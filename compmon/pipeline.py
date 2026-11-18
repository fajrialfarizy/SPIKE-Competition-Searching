"""
compmon.pipeline
Core pipeline orchestrator for Initial, Routine, and Evaluation workflows.

Key guarantees:
- Task 23: Mandatory pagination. Account scraping raises `resultsLimit` in multiples of
  `max_results_per_input` up to `max_results_ceiling_per_source` and stops only when a run
  returns fewer rows than requested. Keyword scraping narrows `dateFrom`/`dateTo` windows
  (the documented keyword actor input) and only escalates `maxPostsPerKeyword` on a
  single-day window. A source is "completed" only when its whole range was covered.
- Task 24: the per-run cap counts only Post IDs that are NOT already in System Log, and
  counts a Post ID once even when two sources find it. Scraping stops at the cap, the
  unfinished source is deferred through `pending_ranges`, and pending sources run first.
- Task 25: `pending_ranges` is read at start, completed entries are removed, duplicates are
  never added, and the JSON is kept small (entry cap + reason truncation).
- Task 28/29: the pipeline never writes to a worksheet. Competition IDs are assigned by
  `sheets_repo` from the real sheet row (`row_idx`), refreshing the highest number first.
- Fix 3: a source is "exhausted" only when the RAW actor item count is below the requested
  limit, never from the post-date-filtered count (pinned old posts are not progress).
- D17: a post may fail extraction at most `max_failed_run_attempts` runs; after that it is
  dead (not processed, no cap budget, no `failed_posts`, no `Partial`).
- D18: `global_checkpoint` records scraping coverage (run start time, UTC), so extraction
  failures and cap-deferred posts no longer force a re-scrape of the whole window.
"""

import logging
import time
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional, Set, Tuple

from compmon.config import AppConfig, validate_and_parse_config, validate_keyword_string
from compmon.models import (
    RawPost, ProcessingStatus, EligibilityStatus,
    CompetitionRecord, ExecutionLogRecord,
    PendingRange, SystemLogRecord
)
from compmon.sheets_repo import SheetsRepository
from compmon.apify_client import ApifyScraperClient
from compmon.gemini_extractor import GeminiExtractor, GeminiPermanentError
from compmon.validator import parse_iso_timestamp
from compmon.dedup import deduplicate_post_batch, partition_new_and_existing
from compmon.matcher import match_competition_record
from compmon.eligibility import evaluate_eligibility_and_deadline
from compmon.checkpoint import (
    parse_pending_ranges, serialize_pending_ranges, checkpoint_should_advance,
    merge_pending_ranges, group_pending_by_source, _pending_key
)
from compmon.retry_policy import parse_attempts, next_attempt, set_attempts, is_dead
from compmon.exec_log import generate_execution_id, format_iso_wib, determine_execution_status
from compmon.quota import ExecutionSafetyTracker, check_apify_quota_available

logger = logging.getLogger(__name__)

# Upper bound on the number of Apify runs spent on a single keyword (safety valve for the
# date-window recursion). Hitting it leaves the source unfinished instead of looping forever.
MAX_KEYWORD_RUNS_PER_SOURCE = 24


def sort_keywords(keywords: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sorts keywords according to Instruction 19:
    - Priority: High -> Medium -> Low
    - Category: Core before Discovery
    """
    priority_order = {"High": 1, "Medium": 2, "Low": 3}
    category_order = {"Core": 1, "Discovery": 2}

    def sort_key(k):
        p_val = priority_order.get(k.get("Priority", "Medium"), 2)
        c_val = category_order.get(k.get("Category", "Discovery"), 2)
        return (p_val, c_val, k.get("Keyword", ""))

    return sorted(keywords, key=sort_key)


def _parse_date_str(value: str) -> Optional[date]:
    """Parses a YYYY-MM-DD (or ISO datetime) string into a date."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip().split("T")[0])
    except ValueError:
        return None


class _KeywordRunContext:
    """Mutable accumulator shared by the recursive keyword date-window scraper."""

    def __init__(self, existing_post_ids: Set[str], safety: ExecutionSafetyTracker):
        self.existing_post_ids = existing_post_ids
        self.safety = safety
        self.posts: List[RawPost] = []
        self.seen_ids: Set[str] = set()
        self.runs = 0
        self.budget_blocked = False


class CompetitionPipeline:
    """Orchestrates end-to-end competition monitoring scraping and AI curation."""

    def __init__(
        self,
        sheets_repo: SheetsRepository,
        apify_token: str,
        gemini_api_key: str,
        notebook_name: str = "ROUTINE",
        apify_client: Optional[Any] = None,
        gemini_client: Optional[Any] = None
    ):
        self.repo = sheets_repo
        self.apify_token = apify_token
        self.gemini_key = gemini_api_key
        self.notebook_name = notebook_name

        raw_config = self.repo.load_config()
        self.config: AppConfig = validate_and_parse_config(raw_config)
        self.tz = ZoneInfo(self.config.timezone)

        if apify_client is not None:
            self.apify = apify_client
        else:
            self.apify = ApifyScraperClient(apify_token)

        if gemini_client is not None:
            self.gemini = GeminiExtractor(gemini_api_key, model_name=self.config.gemini_model, client=gemini_client)
        else:
            self.gemini = GeminiExtractor(gemini_api_key, model_name=self.config.gemini_model)

    # =========================================================================
    # Task 23: pagination helpers
    # =========================================================================

    def _budget_reason(self, source_label: str) -> str:
        return (
            f"Batas {self.config.max_new_posts_per_run} post baru per run tercapai saat "
            f"mengambil {source_label}; sisa rentang dijadwalkan pada run berikutnya."
        )

    def _ceiling_reason(self, source_label: str, ceiling: int) -> str:
        return (
            f"Reached max_results_ceiling_per_source={ceiling} for {source_label}; "
            f"the source may still have more posts in this range."
        )

    def _accept_batch(
        self,
        batch: List[RawPost],
        seen_ids: Set[str],
        existing_post_ids: Set[str],
        safety: ExecutionSafetyTracker,
    ) -> Tuple[List[RawPost], bool]:
        """
        Accepts the still-unseen posts of one actor run under the new-post cap (Task 24).
        Returns (accepted_posts, budget_blocked).
        """
        accepted: List[RawPost] = []
        budget_blocked = False
        for post in batch:
            pid = str(post.post_id).strip()
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            if safety.try_accept_new_post(pid, existing_post_ids):
                accepted.append(post)
            else:
                budget_blocked = True
        return accepted, budget_blocked

    def _scrape_account_with_pagination(
        self,
        acc: str,
        cutoff_date: str,
        existing_post_ids: Set[str],
        safety: ExecutionSafetyTracker,
        start_limit: Optional[int] = None,
    ) -> Tuple[List[RawPost], bool, str]:
        """
        Scrapes one account, escalating `resultsLimit` by `max_results_per_input` until the
        actor returns fewer RAW items than requested (source exhausted) or the configured
        ceiling is reached (Task 23). The actor has no upper date bound, so the date cut-off
        is re-applied in Python by the Apify client and duplicates are collapsed by Post ID.

        `start_limit` is the FIRST `resultsLimit` (default `max_results_per_input`, i.e. the
        historical behaviour); it is clamped to the ceiling. Fix 3: completeness is decided
        from the raw item count, never from the post-date-filter count, so pinned old posts
        cannot make a saturated run look exhausted.

        Returns (posts, is_done, reason).
        """
        step = self.config.max_results_per_input
        ceiling = self.config.max_results_ceiling_per_source
        first = int(start_limit) if start_limit else step
        current_limit = max(1, min(first, ceiling))
        all_posts: List[RawPost] = []
        seen_ids: Set[str] = set()

        while True:
            try:
                batch, raw_count = self.apify.scrape_account_posts(
                    actor_id=self.config.apify_actor_accounts,
                    account_username=acc,
                    results_limit=current_limit,
                    only_posts_newer_than=cutoff_date,
                )
            except Exception as e:
                reason = f"Apify error for account '{acc}': {e}"
                logger.error(reason)
                return all_posts, False, reason

            accepted, budget_blocked = self._accept_batch(
                batch, seen_ids, existing_post_ids, safety
            )
            all_posts.extend(accepted)

            if budget_blocked:
                return all_posts, False, self._budget_reason(f"account '{acc}'")

            if raw_count < current_limit:
                return all_posts, True, ""

            next_limit = current_limit + step
            if next_limit > ceiling:
                logger.warning(f"Account '{acc}' reached pagination ceiling {ceiling}")
                return all_posts, False, self._ceiling_reason(f"account '{acc}'", ceiling)

            current_limit = next_limit

    def _query_keyword_window(
        self,
        kw: str,
        d_from: date,
        d_to: date,
        limit: int,
        ctx: _KeywordRunContext,
    ) -> Tuple[int, bool, str]:
        """
        Runs the keyword actor once for one date window.
        Returns (rows_returned, is_done, reason).
        """
        date_from = d_from.isoformat()
        date_to = d_to.isoformat()
        try:
            batch, raw_count = self.apify.scrape_keyword_posts(
                actor_id=self.config.apify_actor_keyword,
                keyword=kw,
                max_posts=limit,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as e:
            return 0, False, f"Apify error for keyword '{kw}' ({date_from}..{date_to}): {e}"

        ctx.runs += 1
        accepted, budget_blocked = self._accept_batch(
            batch, ctx.seen_ids, ctx.existing_post_ids, ctx.safety
        )
        ctx.posts.extend(accepted)

        if budget_blocked:
            ctx.budget_blocked = True
            return raw_count, False, self._budget_reason(f"keyword '{kw}'")

        # Completeness uses the RAW item count, never the date-filtered count (Fix 3).
        return raw_count, raw_count < limit, ""

    def _scrape_keyword_window(
        self,
        kw: str,
        d_from: date,
        d_to: date,
        ctx: _KeywordRunContext,
    ) -> Tuple[bool, str]:
        """
        Covers one keyword/date window completely (Task 23).

        The keyword actor accepts `dateFrom`/`dateTo` and `maxPostsPerKeyword`, so a saturated
        window is split in half and each half is scraped separately. Only a saturated
        single-day window escalates `maxPostsPerKeyword` up to the ceiling; if it still
        saturates there, the source is unfinished and reported in `pending_ranges`.
        """
        step = self.config.max_results_per_input
        ceiling = self.config.max_results_ceiling_per_source

        if ctx.runs >= MAX_KEYWORD_RUNS_PER_SOURCE:
            return False, (
                f"Stopped after {MAX_KEYWORD_RUNS_PER_SOURCE} Apify runs for keyword '{kw}'; "
                f"date window {d_from}..{d_to} is still incomplete."
            )

        rows, exhausted, reason = self._query_keyword_window(kw, d_from, d_to, step, ctx)
        if reason:
            return False, reason
        if ctx.budget_blocked:
            return False, self._budget_reason(f"keyword '{kw}'")
        if exhausted:
            return True, ""

        if d_from == d_to:
            limit = step
            while limit < ceiling:
                limit = min(limit + step, ceiling)
                if ctx.runs >= MAX_KEYWORD_RUNS_PER_SOURCE:
                    return False, (
                        f"Stopped after {MAX_KEYWORD_RUNS_PER_SOURCE} Apify runs for "
                        f"keyword '{kw}' on {d_from}."
                    )
                rows, exhausted, reason = self._query_keyword_window(kw, d_from, d_to, limit, ctx)
                if reason:
                    return False, reason
                if ctx.budget_blocked:
                    return False, self._budget_reason(f"keyword '{kw}'")
                if exhausted:
                    return True, ""
            return False, self._ceiling_reason(f"keyword '{kw}' on {d_from}", ceiling)

        mid = d_from + timedelta(days=(d_to - d_from).days // 2)
        if mid >= d_to:
            mid = d_from
        left_done, left_reason = self._scrape_keyword_window(kw, d_from, mid, ctx)
        if not left_done:
            return False, left_reason
        return self._scrape_keyword_window(kw, mid + timedelta(days=1), d_to, ctx)

    def _scrape_keyword_with_pagination(
        self,
        kw: str,
        date_from: str,
        date_to: str,
        existing_post_ids: Set[str],
        safety: ExecutionSafetyTracker,
    ) -> Tuple[List[RawPost], bool, str]:
        """Scrapes one keyword over [date_from, date_to], returning (posts, is_done, reason)."""
        d_from = _parse_date_str(date_from)
        d_to = _parse_date_str(date_to)
        if d_from is None or d_to is None or d_from > d_to:
            return [], False, f"Invalid keyword date range: {date_from}..{date_to}"

        ctx = _KeywordRunContext(existing_post_ids, safety)
        done, reason = self._scrape_keyword_window(kw, d_from, d_to, ctx)
        return ctx.posts, done, reason

    # =========================================================================
    # Task 24/25: source orchestration
    # =========================================================================

    def _scrape_all_sources(
        self,
        accounts: List[str],
        account_cutoff: str,
        keyword_tasks: List[Tuple[str, str]],
        existing_pending: List[PendingRange],
        existing_post_ids: Set[str],
        account_start_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Scrapes every source needed by this run:
        - sources recorded in `pending_ranges` first (Task 24/25),
        - then the normal account/keyword sources, skipping any source already covered by a
          pending entry in this same run,
        - stops starting new sources once the per-run new-post cap is reached.
        """
        safety = ExecutionSafetyTracker(self.config.max_new_posts_per_run)

        normal_cutoffs: Dict[Tuple[str, str], str] = {("Account", acc): account_cutoff for acc in accounts}
        for kw, cutoff in keyword_tasks:
            normal_cutoffs[("Keyword", kw)] = cutoff

        pending_by_source = group_pending_by_source(existing_pending)

        # (source_type, source, from_date, pending_keys)
        tasks: List[Tuple[str, str, str, List[Tuple[str, str, str, str]]]] = []
        completed_pending_keys: Set[Tuple[str, str, str, str]] = set()
        for (stype, source), prs in pending_by_source.items():
            if stype not in ("Account", "Keyword"):
                # Leftovers from older versions (e.g. "AI"/"System" entries) can never be
                # scraped; drop them instead of forcing every future run to be Partial.
                logger.warning(f"Dropping unsupported pending source type '{stype}' ({source})")
                completed_pending_keys.update(_pending_key(pr) for pr in prs)
                continue
            dates = [pr.from_date for pr in prs if pr.from_date]
            earliest = min(dates) if dates else normal_cutoffs.get((stype, source), "")
            normal_cutoff = normal_cutoffs.get((stype, source))
            if normal_cutoff and (not earliest or normal_cutoff < earliest):
                earliest = normal_cutoff
            tasks.append((stype, source, earliest, [_pending_key(pr) for pr in prs]))

        for acc in accounts:
            if ("Account", acc) not in pending_by_source:
                tasks.append(("Account", acc, account_cutoff, []))
        for kw, cutoff in keyword_tasks:
            if ("Keyword", kw) not in pending_by_source:
                tasks.append(("Keyword", kw, cutoff, []))

        scraped_posts: List[RawPost] = []
        new_pending_ranges: List[PendingRange] = []
        completed_sources = 0
        budget_blocked = False
        today_str = str(datetime.now(self.tz).date())
        # Source accounting for the checkpoint rule (D18).
        attempted_sources: List[Tuple[str, str]] = []
        completed_source_keys: Set[Tuple[str, str]] = set()

        for stype, source, from_date, pending_keys in tasks:
            label = f"{stype.lower()} '{source}'"
            attempted_sources.append((stype, source))
            if not safety.can_accept_more_posts():
                budget_blocked = True
                new_pending_ranges.append(
                    PendingRange(stype, source, from_date or today_str, today_str,
                                 self._budget_reason(label))
                )
                continue

            if stype == "Account":
                posts, done, reason = self._scrape_account_with_pagination(
                    source, from_date or account_cutoff, existing_post_ids, safety,
                    start_limit=account_start_limit,
                )
            elif stype == "Keyword":
                posts, done, reason = self._scrape_keyword_with_pagination(
                    source, from_date or account_cutoff, today_str, existing_post_ids, safety
                )
            else:  # pragma: no cover - guarded when tasks are built
                logger.warning(f"Unsupported pending source type '{stype}'")
                continue

            scraped_posts.extend(posts)

            if done:
                completed_sources += 1
                completed_source_keys.add((stype, source))
                completed_pending_keys.update(pending_keys)
            else:
                if "post baru per run" in reason:
                    budget_blocked = True
                new_pending_ranges.append(
                    PendingRange(stype, source, from_date or today_str, today_str, reason)
                )

        return {
            "scraped_posts": scraped_posts,
            "completed_pending_keys": completed_pending_keys,
            "new_pending_ranges": new_pending_ranges,
            "completed_sources": completed_sources,
            "total_sources": len(tasks),
            "attempted_sources": attempted_sources,
            "completed_source_keys": completed_source_keys,
            "safety": safety,
            "budget_blocked": budget_blocked,
        }

    # =========================================================================
    # run_initial_scraping
    # =========================================================================

    def run_initial_scraping(
        self,
        selected_accounts: Optional[List[str]] = None,
        selected_keywords: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Executes the Initial Scraping workflow (PRD 6.1, 6.14):
        - Loads the System Log index BEFORE scraping (Task 24)
        - Processes pending_ranges sources FIRST (Task 25)
        - Paginates every source (Task 23)
        """
        start_time_utc = datetime.now(timezone.utc)
        start_time_wib = format_iso_wib(start_time_utc, self.config.timezone)
        execution_id = generate_execution_id(self.notebook_name, self.config.timezone)

        logger.info(f"=== Starting Initial Scraping: {execution_id} ===")

        try:
            self.repo.assign_missing_competition_ids()

            run_state = self.repo.load_run_state()
            existing_pending: List[PendingRange] = parse_pending_ranges(run_state.get("pending_ranges"))

            existing_post_ids: Set[str] = set(self.repo.load_system_log_index().keys())

            target_accounts = selected_accounts or self.repo.load_source_accounts()
            all_kw_records = self.repo.load_search_keywords()

            for k in all_kw_records:
                validate_keyword_string(k.get("Keyword", ""))

            active_kws = [
                k for k in all_kw_records
                if k.get("Active Status") == "Aktif" and (selected_keywords is None or k["Keyword"] in selected_keywords)
            ]
            sorted_kws = sort_keywords(active_kws)

            cutoff_acc = (datetime.now(timezone.utc) - timedelta(days=self.config.initial_days_accounts)).strftime("%Y-%m-%d")

            keyword_tasks: List[Tuple[str, str]] = []
            for kw_obj in sorted_kws:
                category = kw_obj.get("Category", "Core")
                days = (
                    self.config.initial_days_core_keywords
                    if category == "Core"
                    else self.config.initial_days_discovery_keywords
                )
                cutoff_kw = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
                keyword_tasks.append((kw_obj["Keyword"], cutoff_kw))

            if not check_apify_quota_available(getattr(self.apify, "client", None)):
                raise RuntimeError("Apify credit quota is exhausted")

            scrape = self._scrape_all_sources(
                accounts=target_accounts,
                account_cutoff=cutoff_acc,
                keyword_tasks=keyword_tasks,
                existing_pending=existing_pending,
                existing_post_ids=existing_post_ids,
                account_start_limit=self.config.account_start_limit_initial,
            )

            pending_merge = merge_pending_ranges(
                existing_pending,
                scrape["new_pending_ranges"],
                scrape["completed_pending_keys"],
            )

            return self._process_collected_posts(
                execution_id=execution_id,
                scraped_posts=scrape["scraped_posts"],
                start_time_wib=start_time_wib,
                start_time_utc=start_time_utc,
                total_sources=scrape["total_sources"],
                completed_sources=scrape["completed_sources"],
                pending_ranges=pending_merge.ranges,
                # Dropped unfinished entries must never look like a clean run.
                hit_limit=scrape["budget_blocked"] or pending_merge.dropped,
                is_routine=False,
                dropped_pending_entries=pending_merge.dropped_entries,
                attempted_sources=scrape["attempted_sources"],
                completed_source_keys=scrape["completed_source_keys"],
            )

        except Exception as fatal_e:
            logger.error(f"Fatal error in Initial Scraping: {fatal_e}")
            end_time_wib = format_iso_wib(datetime.now(timezone.utc), self.config.timezone)
            try:
                self.repo.append_execution_log(
                    ExecutionLogRecord(
                        execution_id=execution_id,
                        notebook_name=self.notebook_name,
                        start_time=start_time_wib,
                        end_time=end_time_wib,
                        execution_status="Failed",
                        total_posts_found=0,
                        new_posts=0,
                        successfully_processed=0,
                        failed_posts=0,
                        latest_successful_posting_timestamp="",
                        errors_notes=str(fatal_e)
                    )
                )
            except Exception as log_e:
                logger.error(f"Failed to write execution log: {log_e}")
            raise

    # =========================================================================
    # run_routine_scraping
    # =========================================================================

    def run_routine_scraping(self) -> Dict[str, Any]:
        """Executes the Routine Scraping workflow (PRD 6.2, 6.15, D13)."""
        start_time_utc = datetime.now(timezone.utc)
        start_time_wib = format_iso_wib(start_time_utc, self.config.timezone)
        execution_id = generate_execution_id("ROUTINE", self.config.timezone)

        try:
            self.repo.assign_missing_competition_ids()

            run_state = self.repo.load_run_state()
            checkpoint_iso = run_state.get("global_checkpoint", "").strip()
            if not checkpoint_iso:
                msg = "global_checkpoint kosong. Silakan jalankan Initial Scraping terlebih dahulu (D13)."
                logger.error(msg)
                self.repo.append_execution_log(
                    ExecutionLogRecord(
                        execution_id=execution_id,
                        notebook_name="ROUTINE",
                        start_time=start_time_wib,
                        end_time=start_time_wib,
                        execution_status="Failed",
                        total_posts_found=0,
                        new_posts=0,
                        successfully_processed=0,
                        failed_posts=0,
                        latest_successful_posting_timestamp="",
                        errors_notes=msg
                    )
                )
                return {"status": "Error", "message": msg}

            run_counter = int(run_state.get("keyword_run_counter", 0))
            existing_pending: List[PendingRange] = parse_pending_ranges(run_state.get("pending_ranges"))

            existing_post_ids: Set[str] = set(self.repo.load_system_log_index().keys())

            target_accounts = self.repo.load_source_accounts()
            all_kws = self.repo.load_search_keywords()

            for k in all_kws:
                validate_keyword_string(k.get("Keyword", ""))

            pending_keyword_sources: Set[str] = {
                pr.source for pr in existing_pending if pr.source_type == "Keyword"
            }

            due_kws = []
            for kw in all_kws:
                if kw.get("Active Status") != "Aktif":
                    continue
                kw_name = kw.get("Keyword", "")
                if kw_name in pending_keyword_sources:
                    # Already served through the pending_ranges path this run.
                    continue
                try:
                    freq = int(kw.get("Frequency", 1))
                except Exception:
                    freq = 1
                if freq <= 0:
                    freq = 1
                if run_counter % freq == 0:
                    due_kws.append(kw)

            sorted_due_kws = sort_keywords(due_kws)
            cutoff_date = checkpoint_iso.split("T")[0]

            if not check_apify_quota_available(getattr(self.apify, "client", None)):
                raise RuntimeError("Apify credit quota is exhausted")

            scrape = self._scrape_all_sources(
                accounts=target_accounts,
                account_cutoff=cutoff_date,
                keyword_tasks=[(kw["Keyword"], cutoff_date) for kw in sorted_due_kws],
                existing_pending=existing_pending,
                existing_post_ids=existing_post_ids,
                account_start_limit=self.config.account_start_limit_routine,
            )

            pending_merge = merge_pending_ranges(
                existing_pending,
                scrape["new_pending_ranges"],
                scrape["completed_pending_keys"],
            )

            return self._process_collected_posts(
                execution_id=execution_id,
                scraped_posts=scrape["scraped_posts"],
                start_time_wib=start_time_wib,
                start_time_utc=start_time_utc,
                total_sources=scrape["total_sources"],
                completed_sources=scrape["completed_sources"],
                pending_ranges=pending_merge.ranges,
                hit_limit=scrape["budget_blocked"] or pending_merge.dropped,
                is_routine=True,
                current_run_counter=run_counter,
                dropped_pending_entries=pending_merge.dropped_entries,
                attempted_sources=scrape["attempted_sources"],
                completed_source_keys=scrape["completed_source_keys"],
            )

        except Exception as fatal_e:
            logger.error(f"Fatal error in Routine Scraping: {fatal_e}")
            end_time_wib = format_iso_wib(datetime.now(timezone.utc), self.config.timezone)
            try:
                self.repo.append_execution_log(
                    ExecutionLogRecord(
                        execution_id=execution_id,
                        notebook_name="ROUTINE",
                        start_time=start_time_wib,
                        end_time=end_time_wib,
                        execution_status="Failed",
                        total_posts_found=0,
                        new_posts=0,
                        successfully_processed=0,
                        failed_posts=0,
                        latest_successful_posting_timestamp="",
                        errors_notes=str(fatal_e)
                    )
                )
            except Exception as log_e:
                logger.error(f"Failed to write execution log: {log_e}")
            raise

    # =========================================================================
    # Decision D17: which System Log rows are still eligible for processing
    # =========================================================================

    def _select_posts_to_process(
        self, sys_index: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], int]:
        """
        Returns (eligible posts, number of dead posts).

        Eligible = `Pending` posts PLUS `Failed` posts whose attempt counter is still below
        `max_failed_run_attempts`. A `Failed` post at or above the limit is dead: it is not
        processed, does not use cap budget, is not counted as a failed post and does not
        force `Partial`. An admin can revive it by setting the status back to `Pending` and
        clearing the `[attempts=N]` marker.
        """
        max_attempts = self.config.max_failed_run_attempts
        eligible: List[Tuple[str, Dict[str, Any]]] = []
        dead = 0
        for pid, info in sys_index.items():
            status = info.get("processing_status", "")
            if status == ProcessingStatus.PENDING.value:
                eligible.append((pid, info))
            elif status == ProcessingStatus.FAILED.value:
                if is_dead(status, info.get("error_notes", ""), max_attempts):
                    dead += 1
                else:
                    eligible.append((pid, info))
        return eligible, dead

    # =========================================================================
    # _process_collected_posts
    # =========================================================================

    def _process_collected_posts(
        self,
        execution_id: str,
        scraped_posts: List[RawPost],
        start_time_wib: str,
        start_time_utc: datetime,
        total_sources: int,
        completed_sources: int,
        pending_ranges: List[PendingRange],
        hit_limit: bool = False,
        is_routine: bool = False,
        current_run_counter: int = 0,
        dropped_pending_entries: int = 0,
        attempted_sources: Optional[List[Tuple[str, str]]] = None,
        completed_source_keys: Optional[Set[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Core data ingestion, persistence, AI extraction, and matching pipeline."""
        deduped = deduplicate_post_batch(scraped_posts)
        sys_index = self.repo.load_system_log_index()
        brand_new, attribution_updates = partition_new_and_existing(deduped, sys_index)

        # Rule 5: raw posts are stored BEFORE AI processing.
        new_sys_records: List[SystemLogRecord] = []
        for p in brand_new:
            rec = SystemLogRecord(
                post_id=p.post_id,
                url=p.url,
                source_accounts=p.get_source_accounts_str(),
                keyword_source=p.get_keyword_source_str(),
                source_type=p.source_type.value,
                waktu_posting=format_iso_wib(p.posted_at, self.config.timezone),
                caption=p.caption,
                first_seen=format_iso_wib(p.first_seen, self.config.timezone),
                processing_status=ProcessingStatus.PENDING.value,
                processed_at="",
                eligibility_status="",
                competition_id="",
                error_notes="",
            )
            new_sys_records.append(rec)

        self.repo.append_raw_posts_to_system_log(new_sys_records)

        for pid, accs, kws, st in attribution_updates:
            row_idx = sys_index[pid]["row_idx"]
            self.repo.update_source_attribution(
                row_idx=row_idx,
                source_accounts=", ".join(accs),
                keyword_source=", ".join(kws),
                source_type=st.value,
            )

        # Reload the index so newly written rows are included.
        sys_index = self.repo.load_system_log_index()

        # (c) of D18: the checkpoint may only move once every new post is really stored.
        new_posts_written = all(str(p.post_id) in sys_index for p in brand_new)

        all_unprocessed, dead_count = self._select_posts_to_process(sys_index)

        limit_cap = self.config.max_new_posts_per_run
        posts_to_process = all_unprocessed[:limit_cap]
        unprocessed_remainder = all_unprocessed[limit_cap:]

        eligibility_rules = self.repo.load_eligibility_rules_with_categories()
        existing_comps, _highest_comp_num, _bottom_row = self.repo.load_all_competition_records()

        today_wib = datetime.now(self.tz).date()
        today_wib_str = str(today_wib)

        success_count = 0
        failed_count = 0
        latest_posting_dt: Optional[datetime] = None
        halted = False

        for idx, (pid, info) in enumerate(posts_to_process):
            caption = info.get("caption", "")
            posted_iso = info.get("waktu_posting", "")
            row_idx = info["row_idx"]

            p_dt = parse_iso_timestamp(posted_iso)
            if p_dt and (latest_posting_dt is None or p_dt > latest_posting_dt):
                latest_posting_dt = p_dt

            # Gemini is sequential: delay BETWEEN calls, not before the first one.
            if idx > 0 and self.config.gemini_delay_seconds > 0:
                time.sleep(self.config.gemini_delay_seconds)

            # Only extraction/validation problems consume an attempt (D17). Sheets calls
            # below are outside this try: their errors are retried by `_execute_with_retry`
            # or propagate as a fatal run error, leaving status/attempts untouched.
            try:
                extracted, date_validation_notes = self.gemini.extract_competition_info(
                    caption=caption,
                    posted_at_iso=posted_iso,
                    today_wib=today_wib_str,
                    eligibility_rules=eligibility_rules,
                    max_retries=self.config.gemini_max_retries,
                    delay_seconds=self.config.gemini_delay_seconds,
                )
                elig_status, elig_note = evaluate_eligibility_and_deadline(extracted, today_wib)
                all_notes_list = [elig_note] + date_validation_notes
                matched_id, _both_empty, match_note, unid_idx = match_competition_record(
                    extracted, existing_comps
                )
            except GeminiPermanentError as perm_e:
                # Invalid API key / daily quota: stop immediately, leave the rest Pending and
                # do NOT consume an attempt for the current post (D17).
                logger.error(f"Permanent Gemini error on post {pid}: {perm_e}")
                halted = True
                break
            except Exception as e:
                logger.error(f"Failed processing post {pid}: {e}")
                failed_count += 1
                attempts = next_attempt(info.get("error_notes", ""))
                self.repo.update_system_log_status(
                    row_idx=row_idx,
                    processing_status=ProcessingStatus.FAILED.value,
                    processed_at_iso=format_iso_wib(datetime.now(timezone.utc), self.config.timezone),
                    eligibility_status="",
                    competition_id="",
                    error_notes=set_attempts(f"Extraction Error: {e}", attempts),
                )
                continue

            processed_at_wib = format_iso_wib(datetime.now(timezone.utc), self.config.timezone)

            if elig_status == EligibilityStatus.INELIGIBLE:
                # D4/PRD 7.4: System Log only, never Competition Report.
                self.repo.update_system_log_status(
                    row_idx=row_idx,
                    processing_status=ProcessingStatus.SUCCESS.value,
                    processed_at_iso=processed_at_wib,
                    eligibility_status=elig_status.value,
                    competition_id="",
                    error_notes="; ".join(all_notes_list),
                )
                success_count += 1
                continue

            final_comp_id = matched_id or ""
            if match_note:
                all_notes_list.append(match_note)
            final_notes = "; ".join(all_notes_list)

            if unid_idx is not None and not matched_id:
                # Task 28: a matched admin row without an ID gets one via sheets_repo only.
                matched_row = existing_comps[unid_idx]
                assigned_id = self.repo.assign_next_competition_id(matched_row.row_idx)
                if assigned_id:
                    matched_row.competition_id = assigned_id
                    final_comp_id = assigned_id
                    logger.info(
                        f"Assigned ID {assigned_id} to matched manual row {matched_row.row_idx}"
                    )
                else:
                    all_notes_list.append("ID baris manual gagal diberikan (sel sudah terisi)")
                    final_notes = "; ".join(all_notes_list)

            elif not matched_id and not final_comp_id:
                new_comp = CompetitionRecord(
                    competition_id="",  # assigned by sheets_repo from the live sheet
                    nama_lomba=extracted.nama_lomba or "",
                    jenis_lomba=extracted.competition_type or "",
                    universitas=extracted.universitas or "",
                    penyelenggara=extracted.penyelenggara or "",
                    skala=extracted.skala or "",
                    pelaksanaan_final=extracted.pelaksanaan_final or "",
                    tempat_final=extracted.tempat_final or "",
                    tanggal_mulai_registrasi=extracted.tanggal_mulai_registrasi or "",
                    deadline_pendaftaran=extracted.deadline_pendaftaran or "",
                    link_postingan=info.get("url", ""),
                    link_registrasi=extracted.link_registrasi or "",
                    link_guidebook=extracted.link_guidebook or "",
                    status_pemeriksaan="Belum Diperiksa",
                )
                self.repo.append_competition_records([new_comp])
                existing_comps.append(new_comp)
                final_comp_id = new_comp.competition_id

            self.repo.update_system_log_status(
                row_idx=row_idx,
                processing_status=ProcessingStatus.SUCCESS.value,
                processed_at_iso=processed_at_wib,
                eligibility_status=elig_status.value,
                competition_id=final_comp_id,
                error_notes=final_notes,
            )
            success_count += 1

        end_time_utc = datetime.now(timezone.utc)
        end_time_wib = format_iso_wib(end_time_utc, self.config.timezone)

        exec_status = determine_execution_status(
            total_sources_count=total_sources,
            completed_sources_count=completed_sources,
            total_new_posts=len(brand_new),
            failed_posts=failed_count,
            hit_limit=hit_limit or bool(unprocessed_remainder),
            pending_ranges=pending_ranges,
            halted=halted,
        )

        latest_posting_iso = format_iso_wib(latest_posting_dt, self.config.timezone) if latest_posting_dt else ""

        # Decision D18: the checkpoint describes SCRAPING coverage, not extraction success.
        # New value = this run's START time (UTC); posts published while the run was scraping
        # are covered by the next run (Post-ID de-duplication makes them free).
        checkpoint_advances = checkpoint_should_advance(
            attempted_sources=attempted_sources or [],
            completed_source_keys=completed_source_keys or set(),
            pending_ranges=pending_ranges,
            dropped_entries=dropped_pending_entries,
            new_posts_stored=new_posts_written,
            total_sources=total_sources,
            completed_sources=completed_sources,
        )

        state_updates = {
            "last_execution_id": execution_id,
            "pending_ranges": serialize_pending_ranges(pending_ranges),
        }
        if checkpoint_advances:
            state_updates["global_checkpoint"] = start_time_utc.astimezone(timezone.utc).isoformat()
            if is_routine:
                state_updates["keyword_run_counter"] = str(current_run_counter + 1)

        self.repo.update_run_state(state_updates, end_time_wib)

        notes: List[str] = []
        if pending_ranges:
            notes.append(f"Pending ranges: {len(pending_ranges)}")
        if halted:
            notes.append("Dihentikan karena error permanen Gemini")
        if dead_count:
            notes.append(
                f"{dead_count} posts permanently failed "
                f"(attempts>={self.config.max_failed_run_attempts}), see System Log"
            )
        errors_notes = "; ".join(notes)

        exec_rec = ExecutionLogRecord(
            execution_id=execution_id,
            notebook_name=self.notebook_name,
            start_time=start_time_wib,
            end_time=end_time_wib,
            execution_status=exec_status,
            total_posts_found=len(scraped_posts),
            new_posts=len(brand_new),
            successfully_processed=success_count,
            failed_posts=failed_count,
            latest_successful_posting_timestamp=latest_posting_iso,
            errors_notes=errors_notes,
        )
        self.repo.append_execution_log(exec_rec)

        summary = {
            "execution_id": execution_id,
            "status": exec_status,
            "total_found": len(scraped_posts),
            "new_posts": len(brand_new),
            "successfully_processed": success_count,
            "failed_posts": failed_count,
            "dead_posts": dead_count,
            "checkpoint_advanced": checkpoint_advances,
            "latest_posting_timestamp": latest_posting_iso,
        }
        logger.info(f"Execution finished: {summary}")
        return summary
