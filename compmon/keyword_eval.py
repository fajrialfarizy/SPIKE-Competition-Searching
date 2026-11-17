"""
compmon.keyword_eval
Evaluates keyword relevance rates, discovers new candidate phrases (PRD 10, 6.16, D7, D8)
and writes the evaluation back to the `Search Keywords` sheet (Task 31).

Thresholds are never hard-coded: they come from `Config`
(`threshold_high`, `threshold_medium`, `min_reviewed_for_priority`).
"""

import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

from compmon.models import (
    KeywordPriority, KeywordEvaluationStatus, KeywordActiveStatus, ExecutionLogRecord
)
from compmon.normalization import clean_text
from compmon.exec_log import generate_execution_id, format_iso_wib

logger = logging.getLogger(__name__)

STOPWORDS = {
    "dan", "di", "ke", "dari", "yang", "untuk", "pada", "adalah", "ini", "itu",
    "dengan", "atau", "oleh", "dalam", "bisa", "akan", "juga", "bagi", "sampai",
    "the", "and", "in", "to", "for", "of", "with", "at", "by", "from", "up",
    "tentang", "sudah", "kalian", "halo", "hai", "link", "bio", "cek",
    "info", "lomba", "kompetisi", "competition", "tahun", "2024", "2025", "2026",
    "national", "nasional", "internasional", "international", "mahasiswa", "student",
}

DEFAULT_THRESHOLD_HIGH = 70.0
DEFAULT_THRESHOLD_MEDIUM = 40.0
DEFAULT_MIN_REVIEWED = 10
MAX_CANDIDATES = 20


def _empty_metric() -> Dict[str, Any]:
    return {
        "total_found": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "total_reviewed": 0,
        "relevance_rate": 0.0,
        "new_priority": None,
        "evaluation_status": KeywordEvaluationStatus.PENDING.value,
    }


def calculate_keyword_metrics(
    system_log_rows: List[Dict[str, Any]],
    competition_report_rows: Dict[str, str],
    existing_keywords: List[str],
    threshold_high: float = DEFAULT_THRESHOLD_HIGH,
    threshold_medium: float = DEFAULT_THRESHOLD_MEDIUM,
    min_reviewed: int = DEFAULT_MIN_REVIEWED,
) -> Dict[str, Dict[str, Any]]:
    """
    Computes cumulative keyword metrics (PRD 10.2, D8):
    - only posts with Processing Status == 'Success' are evaluated;
    - Ineligible -> Invalid; Eligible/Unknown -> Valid / Tidak Valid from the report,
      'Belum Diperiksa' is excluded;
    - one post found by several keywords counts once for each keyword;
    - Relevance Rate = Valid / (Valid + Invalid) * 100;
    - `new_priority` is None below `min_reviewed`, meaning "keep the existing priority".
    """
    metrics: Dict[str, Dict[str, Any]] = {
        kw.strip(): _empty_metric() for kw in existing_keywords if kw.strip()
    }

    for row in system_log_rows:
        if row.get("processing_status") != "Success":
            continue

        raw_kw_source = row.get("keyword_source", "")
        if not raw_kw_source:
            continue

        # `load_system_log_index` returns a list, older callers pass the raw cell string.
        if isinstance(raw_kw_source, (list, tuple, set)):
            kws_in_post = [str(k).strip() for k in raw_kw_source if str(k).strip()]
        else:
            kws_in_post = [k.strip() for k in str(raw_kw_source).split(",") if k.strip()]
        if not kws_in_post:
            continue

        eligibility = row.get("eligibility_status")
        comp_id = row.get("competition_id")

        is_valid: Optional[bool] = None
        if eligibility == "Ineligible":
            is_valid = False
        elif eligibility in ("Eligible", "Unknown"):
            if comp_id and comp_id in competition_report_rows:
                status = competition_report_rows[comp_id]
                if status == "Valid":
                    is_valid = True
                elif status == "Tidak Valid":
                    is_valid = False
                else:
                    is_valid = None
            else:
                is_valid = None

        for kw in kws_in_post:
            if kw not in metrics:
                metrics[kw] = _empty_metric()
            metrics[kw]["total_found"] += 1

            if is_valid is True:
                metrics[kw]["valid_count"] += 1
            elif is_valid is False:
                metrics[kw]["invalid_count"] += 1

    for data in metrics.values():
        valid = data["valid_count"]
        invalid = data["invalid_count"]
        reviewed = valid + invalid
        data["total_reviewed"] = reviewed
        data["relevance_rate"] = round((valid / reviewed) * 100.0, 1) if reviewed > 0 else 0.0

        if reviewed >= min_reviewed:
            data["evaluation_status"] = KeywordEvaluationStatus.EVALUATED.value
            if data["relevance_rate"] >= threshold_high:
                data["new_priority"] = KeywordPriority.HIGH.value
            elif data["relevance_rate"] >= threshold_medium:
                data["new_priority"] = KeywordPriority.MEDIUM.value
            else:
                data["new_priority"] = KeywordPriority.LOW.value
        else:
            data["evaluation_status"] = KeywordEvaluationStatus.PENDING.value
            data["new_priority"] = None

    return metrics


def _phrase_tokens(text: str) -> Tuple[str, ...]:
    return tuple(clean_text(text).lower().split())


def is_subphrase(short: str, long: str) -> bool:
    """True when `short` appears as a contiguous token sequence inside `long`."""
    short_tokens = _phrase_tokens(short)
    long_tokens = _phrase_tokens(long)
    if not short_tokens or len(short_tokens) >= len(long_tokens):
        return False
    width = len(short_tokens)
    return any(long_tokens[i:i + width] == short_tokens for i in range(len(long_tokens) - width + 1))


def extract_candidate_keywords(
    valid_captions: List[str],
    existing_keywords: List[str],
    min_frequency: int = 3,
    max_candidates: int = MAX_CANDIDATES,
) -> List[str]:
    """
    Extracts 2-4 word n-gram candidates from Valid captions without AI (PRD 10.1, D7):

    - lower-case, punctuation/extra spaces removed, stopword-only phrases dropped;
    - phrases already covered by an existing keyword are dropped;
    - a phrase that is a sub-phrase of a longer candidate is dropped (the longer, more
      specific phrase is preferred);
    - results are sorted by descending frequency, then alphabetically.
    """
    clean_existing = [clean_text(k).lower() for k in existing_keywords if k and k.strip()]
    ngram_counter: Counter = Counter()

    for caption in valid_captions:
        if not caption:
            continue
        cleaned = re.sub(r"[^\w\s]", " ", caption.lower())
        words = [w for w in cleaned.split() if len(w) > 2 and w not in STOPWORDS]

        for n in range(2, 5):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                ngram_counter[gram] += 1

    frequent = [
        phrase for phrase, count in ngram_counter.items() if count >= min_frequency
    ]

    def is_covered_by_existing(phrase: str) -> bool:
        return any(phrase == ex or is_subphrase(phrase, ex) for ex in clean_existing)

    candidates = [p for p in frequent if not is_covered_by_existing(p)]

    # Drop phrases that are only a fragment of a longer candidate.
    filtered: List[str] = []
    for phrase in sorted(candidates, key=lambda p: (-len(_phrase_tokens(p)), p)):
        if any(is_subphrase(phrase, longer) for longer in filtered):
            continue
        filtered.append(phrase)

    ranked = sorted(
        filtered,
        key=lambda p: (-ngram_counter[p], -len(_phrase_tokens(p)), p),
    )
    return ranked[:max_candidates]


def run_keyword_evaluation(
    repo,
    notebook_name: str = "KEYWORD-EVAL",
    min_frequency: int = 3,
) -> Dict[str, Any]:
    """
    Runs the every-4-weeks keyword evaluation and writes it to `Search Keywords` (Task 31).

    - metrics are written to columns F-L; Priority (C) only when reviewed >= minimum;
    - Active Status (D) is never touched and no keyword is ever deleted or activated;
    - new candidates are appended as Category=Discovery, Active Status=Kandidat,
      Priority=Low, Frequency=1;
    - exactly one Execution Log row is appended.
    """
    config = repo.load_config()
    tz_str = config.get("timezone", "Asia/Jakarta")
    threshold_high = float(config.get("threshold_high", DEFAULT_THRESHOLD_HIGH))
    threshold_medium = float(config.get("threshold_medium", DEFAULT_THRESHOLD_MEDIUM))
    min_reviewed = int(config.get("min_reviewed_for_priority", DEFAULT_MIN_REVIEWED))

    start_time_utc = datetime.now(timezone.utc)
    start_time_wib = format_iso_wib(start_time_utc, tz_str)
    execution_id = generate_execution_id(notebook_name, tz_str)

    sys_index = repo.load_system_log_index()
    sys_rows = list(sys_index.values())
    existing_comps, _, _ = repo.load_all_competition_records()
    keyword_rows = repo.load_search_keywords_with_rows()
    existing_keywords = [k.get("Keyword", "") for k in keyword_rows]

    comp_status_map = {c.competition_id: c.status_pemeriksaan for c in existing_comps if c.competition_id}

    metrics = calculate_keyword_metrics(
        sys_rows,
        comp_status_map,
        existing_keywords,
        threshold_high=threshold_high,
        threshold_medium=threshold_medium,
        min_reviewed=min_reviewed,
    )

    valid_comp_ids = {cid for cid, status in comp_status_map.items() if status == "Valid"}
    valid_captions = [
        info["caption"] for info in sys_rows
        if info.get("competition_id") in valid_comp_ids and info.get("caption")
    ]
    candidates = extract_candidate_keywords(
        valid_captions, existing_keywords, min_frequency=min_frequency
    )

    now_wib = format_iso_wib(datetime.now(timezone.utc), tz_str)
    written = 0
    for kw_row in keyword_rows:
        name = kw_row.get("Keyword", "")
        data = metrics.get(name)
        if not data:
            continue
        repo.update_keyword_metrics(
            row_idx=kw_row["_row_idx"],
            last_evaluated=now_wib,
            total_found=data["total_found"],
            total_reviewed=data["total_reviewed"],
            valid_count=data["valid_count"],
            invalid_count=data["invalid_count"],
            relevance_rate=data["relevance_rate"],
            evaluation_status=data["evaluation_status"],
            priority=data["new_priority"],
        )
        written += 1

    known = {k.strip().lower() for k in existing_keywords if k.strip()}
    candidate_rows = []
    for candidate in candidates:
        if candidate.lower() in known:
            continue
        known.add(candidate.lower())
        candidate_rows.append([
            candidate,
            "Discovery",
            KeywordPriority.LOW.value,
            KeywordActiveStatus.CANDIDATE.value,
            "1",
            "",
            "0", "0", "0", "0", "0.0",
            KeywordEvaluationStatus.PENDING.value,
        ])
    if candidate_rows:
        repo.append_keyword_candidates(candidate_rows)

    end_time_wib = format_iso_wib(datetime.now(timezone.utc), tz_str)
    evaluated = sum(1 for d in metrics.values() if d["evaluation_status"] == KeywordEvaluationStatus.EVALUATED.value)
    repo.append_execution_log(ExecutionLogRecord(
        execution_id=execution_id,
        notebook_name=notebook_name,
        start_time=start_time_wib,
        end_time=end_time_wib,
        execution_status="Success",
        total_posts_found=len(valid_captions),
        new_posts=len(candidate_rows),
        successfully_processed=evaluated,
        failed_posts=0,
        latest_successful_posting_timestamp=now_wib,
        errors_notes=(
            f"Evaluated {written} keywords ({evaluated} with >= {min_reviewed} reviewed); "
            f"added {len(candidate_rows)} candidates."
        ),
    ))

    return {
        "execution_id": execution_id,
        "metrics": metrics,
        "keywords_written": written,
        "candidates": [row[0] for row in candidate_rows],
    }
