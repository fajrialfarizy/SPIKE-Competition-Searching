"""
compmon.sheets_repo
The ONLY module responsible for reading and writing Google Sheets.

Guarantees:
- every write uses value_input_option="RAW";
- Competition Report rows are appended only (never overwritten); column D is never written;
- Competition IDs are assigned here from the real sheet row (`row_idx`), refreshing the
  highest existing number immediately before each write (Tasks 28-30);
- worksheets are grown with `add_rows` before any explicit-range write that would fall
  outside the grid (Task 27).
"""

import logging
import time
import random
from typing import List, Dict, Any, Optional, Tuple
from compmon.models import CompetitionRecord, SystemLogRecord, ExecutionLogRecord
from compmon.validator import sanitize_caption
from compmon.matcher import format_competition_id

logger = logging.getLogger(__name__)

COMPETITION_ID_PATTERN = "CMP-"

# Google Sheets allows ~60 reads and ~60 writes per minute per user. Quota errors are
# waited out with a much longer ladder than ordinary transient failures; at ~10 Gemini
# posts per minute a 15 s first retry already keeps the run under the limit.
QUOTA_BACKOFF_SECONDS = (15.0, 30.0, 60.0)


def _is_quota_error(err_str: str) -> bool:
    """429 / RESOURCE_EXHAUSTED / 'Quota exceeded' from the Sheets API."""
    lowered = err_str.lower()
    return (
        "429" in err_str
        or "resource_exhausted" in lowered
        or "quota exceeded" in lowered
        or "rate limit" in lowered
    )


class SheetsRepository:
    """Manages all Google Sheets API interactions with batching and append-safe writes."""

    def __init__(self, spreadsheet_client: Any, spreadsheet_id: str):
        self.client = spreadsheet_client
        self.spreadsheet_id = spreadsheet_id
        self._spreadsheet = None
        # Worksheet handles are reused: `spreadsheet.worksheet(title)` is an API call, and
        # per-post lookups would otherwise burn the per-minute read quota.
        self._worksheets: Dict[str, Any] = {}

    @property
    def spreadsheet(self):
        if not self._spreadsheet:
            self._spreadsheet = self.client.open_by_key(self.spreadsheet_id)
        return self._spreadsheet

    def get_worksheet(self, title: str):
        """Returns the cached worksheet handle for `title`, fetching it only once."""
        ws = self._worksheets.get(title)
        if ws is None:
            ws = self.spreadsheet.worksheet(title)
            self._worksheets[title] = ws
        return ws

    def _invalidate_worksheet(self, title: Optional[str] = None):
        """Drops cached handles after `add_rows` or after an API error."""
        if title is None:
            self._worksheets.clear()
        else:
            self._worksheets.pop(title, None)

    def _read_all_values(self, ws) -> List[List[str]]:
        """Reads a whole worksheet through the retry wrapper (reads share the quota too)."""
        return self._execute_with_retry(lambda: ws.get_all_values())

    def _execute_with_retry(self, func, max_retries: int = 3, initial_delay: float = 1.0):
        """
        Executes a Sheets API call with limited exponential backoff on temporary errors.

        Quota errors (429 / RESOURCE_EXHAUSTED / 'Quota exceeded') use a long ladder
        (15 s, 30 s, 60 s plus jitter). 403 / PERMISSION_DENIED / invalid range are
        permanent and raised immediately.
        """
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as e:
                err_str = str(e)
                if "403" in err_str or "PERMISSION_DENIED" in err_str or "invalid range" in err_str.lower():
                    logger.error(f"Permanent Google Sheets error: {err_str}")
                    raise
                # A failed call may mean the handle is stale; re-open it on the next use.
                self._invalidate_worksheet()
                logger.warning(f"Sheets API attempt {attempt}/{max_retries} failed: {err_str}")
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Google Sheets API call failed after {max_retries} retries: {err_str}"
                    ) from e
                if _is_quota_error(err_str):
                    base = QUOTA_BACKOFF_SECONDS[
                        min(attempt - 1, len(QUOTA_BACKOFF_SECONDS) - 1)
                    ]
                    delay = base + random.uniform(0.1, 1.0)
                else:
                    delay = (initial_delay * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                logger.warning(f"Sheets retry in {delay:.1f}s")
                time.sleep(delay)

    # ==========================================
    # Grid helpers (Task 27)
    # ==========================================

    @staticmethod
    def _row_count(ws) -> Optional[int]:
        value = getattr(ws, "row_count", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def ensure_grid_rows(self, ws, needed_rows: int):
        """
        Grows a worksheet so that row `needed_rows` exists before an explicit-range write.
        Google Sheets rejects writes outside the grid; `add_rows` avoids that failure.
        """
        if not needed_rows or needed_rows < 1:
            return
        row_count = self._row_count(ws)
        if row_count is None or needed_rows <= row_count:
            return
        missing = needed_rows - row_count
        logger.info(f"Growing worksheet '{getattr(ws, 'title', '?')}' by {missing} rows")
        self._execute_with_retry(lambda: ws.add_rows(missing))
        # The handle's cached row_count is stale after growing the grid.
        self._invalidate_worksheet(getattr(ws, "title", None))

    # ==========================================
    # Config & Run State Operations
    # ==========================================

    def load_config(self) -> Dict[str, Any]:
        """Loads key-value pairs from Config sheet."""
        ws = self.get_worksheet("Config")
        records = self._read_all_values(ws)
        config_dict = {}
        for row in records[1:]:
            if len(row) >= 2 and row[0].strip():
                config_dict[row[0].strip()] = row[1].strip()
        return config_dict

    def load_run_state(self) -> Dict[str, Any]:
        """Loads state key-values from Run State sheet."""
        ws = self.get_worksheet("Run State")
        records = self._read_all_values(ws)
        state_dict = {}
        for row in records[1:]:
            if len(row) >= 2 and row[0].strip():
                state_dict[row[0].strip()] = row[1].strip()
        return state_dict

    def update_run_state(self, updates: Dict[str, str], updated_at_iso: str):
        """Updates specific keys in Run State sheet."""
        ws = self.get_worksheet("Run State")
        records = self._read_all_values(ws)
        key_to_row = {}
        for idx, row in enumerate(records[1:], start=2):
            if row and row[0].strip():
                key_to_row[row[0].strip()] = idx

        for key, val in updates.items():
            if key in key_to_row:
                row_idx = key_to_row[key]
                self._execute_with_retry(
                    lambda row_idx=row_idx, val=val: ws.update(
                        range_name=f"B{row_idx}:C{row_idx}",
                        values=[[val, updated_at_iso]],
                        value_input_option="RAW",
                    )
                )
            else:
                self.ensure_grid_rows(ws, len(records) + 2)
                self._execute_with_retry(
                    lambda key=key, val=val: ws.append_row(
                        [key, val, updated_at_iso], value_input_option="RAW"
                    )
                )

    def load_source_accounts(self) -> List[str]:
        """Loads list of usernames from Source Accounts sheet."""
        ws = self.get_worksheet("Source Accounts")
        records = self._read_all_values(ws)
        return [row[0].strip().lstrip("@") for row in records[1:] if row and row[0].strip()]

    def load_search_keywords(self) -> List[Dict[str, Any]]:
        """Loads keyword configurations from Search Keywords sheet."""
        return [
            {k: v for k, v in item.items() if k != "_row_idx"}
            for item in self.load_search_keywords_with_rows()
        ]

    def load_search_keywords_with_rows(self) -> List[Dict[str, Any]]:
        """
        Loads keyword rows, including the real 1-based sheet row in `_row_idx`
        (needed because blank rows are skipped and list indexes are not sheet rows).
        """
        ws = self.get_worksheet("Search Keywords")
        records = self._read_all_values(ws)
        if not records or len(records) < 2:
            return []
        headers = [h.strip() for h in records[0]]
        keywords = []
        for idx, row in enumerate(records[1:], start=2):
            if not row or not row[0].strip():
                continue
            item = {headers[i]: (row[i].strip() if i < len(row) else "") for i in range(len(headers))}
            item["_row_idx"] = idx
            keywords.append(item)
        return keywords

    def update_keyword_metrics(
        self,
        row_idx: int,
        last_evaluated: str,
        total_found: int,
        total_reviewed: int,
        valid_count: int,
        invalid_count: int,
        relevance_rate: Any,
        evaluation_status: str,
        priority: Optional[str] = None,
    ):
        """
        Writes evaluation metrics for one keyword row (Task 31).
        Columns F-L are always refreshed; column C (Priority) is written only when
        `priority` is not None, and column D (Active Status) is never touched.
        """
        ws = self.get_worksheet("Search Keywords")
        self.ensure_grid_rows(ws, row_idx)
        values = [[
            last_evaluated,
            str(total_found),
            str(total_reviewed),
            str(valid_count),
            str(invalid_count),
            str(relevance_rate),
            evaluation_status,
        ]]
        self._execute_with_retry(
            lambda: ws.update(
                range_name=f"F{row_idx}:L{row_idx}",
                values=values,
                value_input_option="RAW",
            )
        )
        if priority is not None:
            self._execute_with_retry(
                lambda: ws.update(
                    range_name=f"C{row_idx}",
                    values=[[priority]],
                    value_input_option="RAW",
                )
            )

    def append_keyword_candidates(self, rows: List[List[Any]]):
        """Appends newly discovered candidate keywords (never activates, never deletes)."""
        if not rows:
            return
        ws = self.get_worksheet("Search Keywords")
        current = len(self._read_all_values(ws))
        self.ensure_grid_rows(ws, current + len(rows) + 1)
        self._execute_with_retry(
            lambda: ws.append_rows(rows, value_input_option="RAW")
        )
        logger.info(f"Appended {len(rows)} candidate keywords")

    def load_master_entities(self, sheet_name: str) -> List[Tuple[str, List[str]]]:
        """Loads Canonical Name and Aliases from Master Top University/Organizer sheets."""
        ws = self.get_worksheet(sheet_name)
        records = self._read_all_values(ws)
        entities = []
        for row in records[1:]:
            if not row or not row[0].strip():
                continue
            canon = row[0].strip()
            aliases = [a.strip() for a in row[1].split(",") if a.strip()] if len(row) > 1 else []
            entities.append((canon, aliases))
        return entities

    def load_eligibility_rules_with_categories(self) -> List[Tuple[str, str]]:
        """Loads active rules as list of (Category, Rule Text) from Eligibility Rules sheet."""
        ws = self.get_worksheet("Eligibility Rules")
        records = self._read_all_values(ws)
        rules = []
        for row in records[1:]:
            if len(row) >= 4 and row[3].strip().upper() in ("TRUE", "1", "AKTIF", "YES"):
                category = row[1].strip() if len(row) > 1 else ""
                text = row[2].strip() if len(row) > 2 else ""
                rules.append((category, text))
            elif len(row) >= 3 and (len(row) < 4 or not row[3].strip()):
                category = row[1].strip() if len(row) > 1 else ""
                text = row[2].strip() if len(row) > 2 else ""
                rules.append((category, text))
        return rules

    # ==========================================
    # Competition Report Operations (Append-Safe)
    # ==========================================

    @staticmethod
    def _highest_competition_num_from_values(rows: List[List[str]]) -> int:
        """Highest numeric suffix of any CMP-xxxxxx value in column A."""
        highest = 0
        for row in rows[1:]:
            if not row:
                continue
            comp_id = str(row[0]).strip() if len(row) > 0 else ""
            if comp_id.startswith(COMPETITION_ID_PATTERN):
                try:
                    highest = max(highest, int(comp_id.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return highest

    @staticmethod
    def _bottom_most_filled_row(rows: List[List[str]]) -> int:
        """1-based index of the lowest row with data in columns A-O (excluding D)."""
        bottom_row = 1  # Header is row 1
        for idx, row in enumerate(rows[1:], start=2):
            row_cells_no_d = [c for i, c in enumerate(row) if i != 3]
            if any(bool(c.strip()) for c in row_cells_no_d):
                bottom_row = idx
        return bottom_row

    def load_all_competition_records(
        self, rows: Optional[List[List[str]]] = None
    ) -> Tuple[List[CompetitionRecord], int, int]:
        """
        Reads all existing rows from Competition Report.

        `rows` may be passed when the caller already read the sheet, so one logical
        operation costs one API read (Fix 2).

        Returns:
        - records: CompetitionRecord list; `row_idx` is the real 1-based sheet row.
        - highest_competition_num: integer max of CMP-xxxxxx (0 when none).
        - bottom_most_filled_row: 1-based index of the lowest filled row (A-O, excluding D).
        """
        ws = self.get_worksheet("Competition Report")
        if rows is None:
            rows = self._read_all_values(ws)
        records: List[CompetitionRecord] = []
        highest_num = self._highest_competition_num_from_values(rows)
        bottom_row = self._bottom_most_filled_row(rows)

        for idx, row in enumerate(rows[1:], start=2):
            if not any(bool(c.strip()) for c in row):
                continue

            comp_id = row[0].strip() if len(row) > 0 else ""
            rec = CompetitionRecord(
                competition_id=comp_id,
                nama_lomba=row[1].strip() if len(row) > 1 else "",
                jenis_lomba=row[2].strip() if len(row) > 2 else "",
                grade_lomba=row[3].strip() if len(row) > 3 else "",
                universitas=row[4].strip() if len(row) > 4 else "",
                penyelenggara=row[5].strip() if len(row) > 5 else "",
                skala=row[6].strip() if len(row) > 6 else "",
                pelaksanaan_final=row[7].strip() if len(row) > 7 else "",
                tempat_final=row[8].strip() if len(row) > 8 else "",
                tanggal_mulai_registrasi=row[9].strip() if len(row) > 9 else "",
                deadline_pendaftaran=row[10].strip() if len(row) > 10 else "",
                link_postingan=row[11].strip() if len(row) > 11 else "",
                link_registrasi=row[12].strip() if len(row) > 12 else "",
                link_guidebook=row[13].strip() if len(row) > 13 else "",
                status_pemeriksaan=row[14].strip() if len(row) > 14 else "Belum Diperiksa",
                row_idx=idx,
            )
            records.append(rec)

        return records, highest_num, bottom_row

    @staticmethod
    def _next_free_competition_id(rows: List[List[str]], used_ids: set) -> str:
        """
        Next unused ID: highest existing number + 1 (D5), so the first ID is CMP-000001.
        Always computed from the live sheet, and bumped past anything already present.
        """
        highest = SheetsRepository._highest_competition_num_from_values(rows)
        candidate = format_competition_id(highest)
        while candidate in used_ids:
            highest += 1
            candidate = format_competition_id(highest)
        return candidate

    def assign_next_competition_id(self, row_idx: int) -> Optional[str]:
        """
        Assigns the next Competition ID to a single cell A{row_idx} (Tasks 28/29).

        - refuses to write anything but column A;
        - re-reads the highest ID from the sheet immediately before writing;
        - writes only when the cell is still empty, so an admin value is never overwritten;
        - retries transient failures through `_execute_with_retry`.
        Returns the assigned (or already present CMP-) ID, or None when the cell is occupied
        by a non-CMP value.
        """
        if not row_idx or row_idx < 2:
            raise ValueError(f"Invalid Competition Report row index: {row_idx}")

        ws = self.get_worksheet("Competition Report")
        all_vals = self._read_all_values(ws)
        existing_val = ""
        if len(all_vals) >= row_idx and all_vals[row_idx - 1]:
            existing_val = str(all_vals[row_idx - 1][0]).strip()

        if existing_val:
            if existing_val.startswith(COMPETITION_ID_PATTERN):
                return existing_val
            logger.warning(
                f"Competition Report A{row_idx} already contains '{existing_val}', leaving it untouched."
            )
            return None

        used_ids = {
            str(r[0]).strip() for r in all_vals[1:]
            if r and str(r[0]).strip().startswith(COMPETITION_ID_PATTERN)
        }
        new_id = self._next_free_competition_id(all_vals, used_ids)

        self.ensure_grid_rows(ws, row_idx)
        self._execute_with_retry(
            lambda: ws.update(
                range_name=f"A{row_idx}",
                values=[[new_id]],
                value_input_option="RAW",
            )
        )
        logger.info(f"Assigned Competition ID {new_id} to A{row_idx}")
        return new_id

    def assign_missing_competition_ids(self) -> int:
        """
        Fills Competition IDs for manual admin rows whose column A is truly EMPTY (Task 30).

        A non-empty value that is not `CMP-...` is NEVER overwritten; it is logged instead.
        Returns the number of IDs newly assigned.
        """
        ws = self.get_worksheet("Competition Report")
        rows = self._read_all_values(ws)
        missing_rows: List[int] = []

        for idx, row in enumerate(rows[1:], start=2):
            if not row:
                continue
            comp_id = str(row[0]).strip()
            if comp_id:
                if not comp_id.startswith(COMPETITION_ID_PATTERN):
                    logger.warning(
                        f"Competition Report A{idx} berisi nilai non-CMP '{comp_id}'; dibiarkan apa adanya."
                    )
                continue
            # Only rows that carry real competition data (columns B-O, excluding D).
            has_content = any(bool(c.strip()) for i, c in enumerate(row) if i > 0 and i != 3)
            if has_content:
                missing_rows.append(idx)

        assigned_count = 0
        for r_idx in missing_rows:
            new_id = self.assign_next_competition_id(r_idx)
            if new_id:
                assigned_count += 1
        return assigned_count

    def append_competition_records(
        self,
        new_records: List[CompetitionRecord],
        start_row: Optional[int] = None
    ) -> int:
        """
        Appends new Competition Records safely (append-only, column D untouched).

        Records with an empty `competition_id` get the next ID assigned here, from the live
        highest number, immediately before the write (Tasks 28/29). `row_idx` is set on every
        record so callers never have to guess the sheet row.
        Returns the next free row number.
        """
        if not new_records:
            return start_row or 2

        ws = self.get_worksheet("Competition Report")

        # ONE read, reused for the bottom row, the used IDs and the highest number.
        # Re-read immediately before writing: admins may have added rows during a long run.
        all_vals = self._read_all_values(ws)
        _records, _highest, current_bottom = self.load_all_competition_records(all_vals)
        target_start = max(current_bottom + 1, start_row or 0)

        while len(all_vals) >= target_start:
            target_row_data = all_vals[target_start - 1] if len(all_vals) >= target_start else []
            if not any(bool(c.strip()) for i, c in enumerate(target_row_data) if i != 3):
                break
            logger.warning(f"Target row {target_start} is unexpectedly filled! Moving one row down.")
            target_start += 1

        used_ids = {
            str(r[0]).strip() for r in all_vals[1:]
            if r and str(r[0]).strip().startswith(COMPETITION_ID_PATTERN)
        }
        for rec in new_records:
            if rec.competition_id:
                continue
            rec.competition_id = self._next_free_competition_id(all_vals, used_ids)
            used_ids.add(rec.competition_id)

        rows_ac = []
        rows_eo = []
        for offset, rec in enumerate(new_records):
            rec.row_idx = target_start + offset
            parts = rec.to_row_without_grade()
            rows_ac.append(parts["part_ac"])
            rows_eo.append(parts["part_eo"])

        end_row = target_start + len(new_records) - 1
        self.ensure_grid_rows(ws, end_row)

        data_ac = {"range": f"A{target_start}:C{end_row}", "values": rows_ac}
        data_eo = {"range": f"E{target_start}:O{end_row}", "values": rows_eo}

        self._execute_with_retry(
            lambda: ws.batch_update([data_ac, data_eo], value_input_option="RAW")
        )
        logger.info(f"Successfully appended {len(new_records)} competitions to rows {target_start}-{end_row}")
        return end_row + 1

    # ==========================================
    # System Log Operations
    # ==========================================

    def load_system_log_index(self) -> Dict[str, Dict[str, Any]]:
        """Loads all Post IDs from System Log with their real sheet row."""
        ws = self.get_worksheet("System Log")
        rows = self._read_all_values(ws)
        index: Dict[str, Dict[str, Any]] = {}
        for idx, row in enumerate(rows[1:], start=2):
            if not row or not row[0].strip():
                continue
            post_id = str(row[0]).strip()
            acc_list = [a.strip() for a in row[2].split(",") if a.strip()] if len(row) > 2 else []
            kw_list = [k.strip() for k in row[3].split(",") if k.strip()] if len(row) > 3 else []
            source_type = row[4].strip() if len(row) > 4 else "Account"
            proc_status = row[8].strip() if len(row) > 8 else "Pending"
            elig_status = row[10].strip() if len(row) > 10 else ""
            comp_id = row[11].strip() if len(row) > 11 else ""
            err_notes = row[12].strip() if len(row) > 12 else ""

            index[post_id] = {
                "row_idx": idx,
                "url": row[1] if len(row) > 1 else "",
                "source_accounts": acc_list,
                "keyword_source": kw_list,
                "source_type": source_type,
                "processing_status": proc_status,
                "eligibility_status": elig_status,
                "competition_id": comp_id,
                "caption": row[6] if len(row) > 6 else "",
                "waktu_posting": row[5] if len(row) > 5 else "",
                "error_notes": err_notes,
            }
        return index

    def append_raw_posts_to_system_log(self, records: List[SystemLogRecord]):
        """
        Appends raw post records to System Log BEFORE AI processing (rule 5).
        On retry, Post IDs already present are skipped so one Post ID = one row.
        """
        if not records:
            return

        ws = self.get_worksheet("System Log")

        def write_with_dedup_check():
            current_index = self.load_system_log_index()
            unwritten_records = [r for r in records if r.post_id not in current_index]
            if not unwritten_records:
                return
            self.ensure_grid_rows(ws, len(current_index) + len(unwritten_records) + 2)
            rows = []
            for r in unwritten_records:
                clean_cap, truncated = sanitize_caption(r.caption)
                r.caption = clean_cap
                if truncated:
                    r.error_notes = f"[Truncated] {r.error_notes}".strip()
                rows.append(r.to_row())
            ws.append_rows(rows, value_input_option="RAW")

        self._execute_with_retry(write_with_dedup_check)
        logger.info(f"Appended {len(records)} raw posts to System Log safely")

    def update_system_log_status(
        self,
        row_idx: int,
        processing_status: str,
        processed_at_iso: str,
        eligibility_status: str,
        competition_id: str,
        error_notes: str
    ):
        """Updates processing columns (I-M) of a specific row in System Log."""
        ws = self.get_worksheet("System Log")
        self.ensure_grid_rows(ws, row_idx)
        update_range = f"I{row_idx}:M{row_idx}"
        values = [[
            processing_status,
            processed_at_iso,
            eligibility_status,
            competition_id,
            error_notes,
        ]]
        self._execute_with_retry(
            lambda: ws.update(
                range_name=update_range,
                values=values,
                value_input_option="RAW",
            )
        )

    def update_source_attribution(
        self,
        row_idx: int,
        source_accounts: str,
        keyword_source: str,
        source_type: str
    ):
        """Updates source columns (C-E) when an existing post is rediscovered by another source."""
        ws = self.get_worksheet("System Log")
        self.ensure_grid_rows(ws, row_idx)
        update_range = f"C{row_idx}:E{row_idx}"
        self._execute_with_retry(
            lambda: ws.update(
                range_name=update_range,
                values=[[source_accounts, keyword_source, source_type]],
                value_input_option="RAW",
            )
        )

    # ==========================================
    # Execution Log Operations
    # ==========================================

    def append_execution_log(self, record: ExecutionLogRecord):
        """Appends exactly one row to Execution Log."""
        ws = self.get_worksheet("Execution Log")
        current = len(self._read_all_values(ws))
        self.ensure_grid_rows(ws, current + 2)
        self._execute_with_retry(
            lambda: ws.append_row(record.to_row(), value_input_option="RAW")
        )
        logger.info(f"Appended execution log: {record.execution_id} ({record.execution_status})")
