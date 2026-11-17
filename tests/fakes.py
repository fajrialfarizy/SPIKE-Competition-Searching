"""
tests/fakes.py
In-memory Google Sheets doubles that mirror the pinned gspread (6.1.4) call signatures.

They are deliberately strict:
- `Worksheet.update(self, values, range_name=None, value_input_option=...)` matches gspread,
- every write records its `value_input_option`, and data writes without "RAW" raise,
- writes outside the grid raise unless the code called `add_rows` first, which is exactly
  the failure mode that made explicit-range writes fail on 100-row sheets.
"""

import re
from typing import Any, Dict, List, Optional

from templates.build_template import INITIAL_SHEETS_DATA, SHEET_COLS, SHEET_ROWS

A1_RE = re.compile(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def col_to_index(col: str) -> int:
    index = 0
    for ch in col:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def index_to_col(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


class GridLimitError(ValueError):
    pass


class FakeWorksheet:
    def __init__(self, title: str, data: Optional[List[List[str]]] = None,
                 rows: int = SHEET_ROWS, cols: int = SHEET_COLS, sheet_id: int = 1):
        self.title = title
        self.data = [list(r) for r in (data or [])]
        self._rows = rows
        self._cols = cols
        self.id = sheet_id
        # write audit trail: (range_name | "append", value_input_option)
        self.writes: List[Dict[str, Any]] = []
        self.last_value_input_option: Optional[str] = None
        self.value_input_options = set()
        self.added_rows = 0
        self.fail_times = 0
        self.fail_message = "503 Service Unavailable backend error"

    # -- gspread geometry ---------------------------------------------------
    @property
    def row_count(self) -> int:
        return self._rows

    @property
    def col_count(self) -> int:
        return self._cols

    def add_rows(self, rows: int):
        self._rows += rows
        self.added_rows += rows

    def add_cols(self, cols: int):
        self._cols += cols

    # -- reads --------------------------------------------------------------
    def get_all_values(self) -> List[List[str]]:
        return [list(r) for r in self.data]

    def col_values(self, col: int) -> List[str]:
        return [row[col - 1] if len(row) >= col else "" for row in self.data]

    # -- write plumbing -----------------------------------------------------
    def _record(self, target: str, value_input_option: Optional[str], allow_user_entered: bool):
        vio = value_input_option or "RAW"
        self.value_input_options.add(vio)
        self.last_value_input_option = vio
        self.writes.append({"target": target, "value_input_option": vio})
        if vio != "RAW" and not allow_user_entered:
            raise AssertionError(
                f"Write to {self.title}!{target} used value_input_option={vio!r}, expected 'RAW'"
            )

    def _maybe_fail(self):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise Exception(self.fail_message)

    def _ensure_cell(self, row: int, col: int):
        if row > self._rows:
            raise GridLimitError(
                f"Write outside the grid: {self.title} has {self._rows} rows, needed {row}. "
                f"Call add_rows() first."
            )
        while len(self.data) < row:
            self.data.append([""] * self._cols)
        while len(self.data[row - 1]) <= col:
            self.data[row - 1].append("")

    def update(self, values=None, range_name: Optional[str] = None,
               value_input_option: Optional[str] = None, **kwargs):
        # gspread 6.1.4 signature: update(values, range_name=None, ...)
        if range_name is None:
            range_name = kwargs.get("range_name")
        if values is None:
            values = kwargs.get("values")
        self._maybe_fail()
        # A real Sheets write of a formula needs USER_ENTERED; data writes must stay RAW.
        has_formula = any(
            isinstance(cell, str) and cell.startswith("=")
            for row in (values or []) for cell in row
        )
        self._record(range_name or "?", value_input_option, allow_user_entered=has_formula)
        if not range_name or values is None:
            raise ValueError("range_name and values are required")
        match = A1_RE.match(range_name)
        if not match:
            raise ValueError(f"Unsupported range: {range_name}")
        start_col = col_to_index(match.group(1))
        start_row = int(match.group(2))
        for r_off, row_values in enumerate(values):
            for c_off, cell in enumerate(row_values):
                self._ensure_cell(start_row + r_off, start_col + c_off)
                self.data[start_row + r_off - 1][start_col + c_off] = str(cell)
        return {}

    def batch_update(self, data, value_input_option: Optional[str] = None, **kwargs):
        self._record("batch", value_input_option, allow_user_entered=False)
        for item in data:
            self.update(
                values=item["values"],
                range_name=item["range"],
                value_input_option=value_input_option,
            )
        return {}

    def append_row(self, values, value_input_option: Optional[str] = None, **kwargs):
        self._append([values], value_input_option)

    def append_rows(self, values, value_input_option: Optional[str] = None, **kwargs):
        self._append(values, value_input_option)

    def _append(self, rows, value_input_option):
        self._maybe_fail()
        self._record("append", value_input_option, allow_user_entered=False)
        for values in rows:
            row_idx = len(self.data) + 1
            self._ensure_cell(row_idx, max(len(values) - 1, 0))
            for c_off, cell in enumerate(values):
                self.data[row_idx - 1][c_off] = str(cell)

    def batch_clear(self, ranges):
        self._record("clear", "RAW", allow_user_entered=True)
        for rng in ranges:
            match = A1_RE.match(rng)
            if not match:
                continue
            start_col, start_row = col_to_index(match.group(1)), int(match.group(2))
            end_col = col_to_index(match.group(3)) if match.group(3) else start_col
            end_row = int(match.group(4)) if match.group(4) else start_row
            for r in range(start_row, min(end_row, len(self.data)) + 1):
                for c in range(start_col, end_col + 1):
                    if len(self.data[r - 1]) > c:
                        self.data[r - 1][c] = ""


class FakeSpreadsheet:
    def __init__(self, worksheet_rows: Optional[Dict[str, List[List[str]]]] = None,
                 row_count: int = SHEET_ROWS, empty: bool = False):
        self._worksheets: Dict[str, FakeWorksheet] = {}
        self.requests: List[Dict[str, Any]] = []
        self.protected: Dict[int, List[Dict[str, Any]]] = {}
        self._next_protected_id = 1000

        if empty:
            return

        for name, content in INITIAL_SHEETS_DATA.items():
            rows = worksheet_rows.get(name) if worksheet_rows else None
            if rows is None:
                rows = [list(content["headers"])]
                if name not in ("Competition Report", "System Log", "Execution Log"):
                    rows += [list(r) for r in content["rows"]]
            self._worksheets[name] = FakeWorksheet(
                name, rows, rows=row_count, sheet_id=len(self._worksheets) + 1
            )

        # Custom sheets used by tests that are not part of the template.
        for name, rows in (worksheet_rows or {}).items():
            if name not in self._worksheets:
                self._worksheets[name] = FakeWorksheet(
                    name, rows, rows=row_count, sheet_id=len(self._worksheets) + 1
                )

    def worksheets(self):
        return list(self._worksheets.values())

    def worksheet(self, title: str):
        return self._worksheets[title]

    def add_worksheet(self, title: str, rows: int, cols: int, index: Optional[int] = None):
        ws = FakeWorksheet(title, [], rows=rows, cols=cols, sheet_id=len(self._worksheets) + 1)
        self._worksheets[title] = ws
        return ws

    def list_protected_ranges(self, sheetid: int):
        return list(self.protected.get(sheetid, []))

    def batch_update(self, body: Dict[str, Any]):
        self.requests.append(body)
        for request in body.get("requests", []):
            if "addProtectedRange" in request:
                pr = request["addProtectedRange"]["protectedRange"]
                sheet_id = pr["range"]["sheetId"]
                pr = dict(pr)
                pr["protectedRangeId"] = self._next_protected_id
                self._next_protected_id += 1
                self.protected.setdefault(sheet_id, []).append(pr)
            elif "deleteProtectedRange" in request:
                rid = request["deleteProtectedRange"]["protectedRangeId"]
                for sheet_id, items in self.protected.items():
                    self.protected[sheet_id] = [i for i in items if i["protectedRangeId"] != rid]
        return {}


class FakeClient:
    """Stand-in for `gspread.authorize(...)`; always returns the same workbook."""

    def __init__(self, worksheet_rows: Optional[Dict[str, List[List[str]]]] = None,
                 row_count: int = SHEET_ROWS, empty: bool = False):
        self.spreadsheet = FakeSpreadsheet(worksheet_rows, row_count=row_count, empty=empty)

    def open_by_key(self, key: str):
        return self.spreadsheet


def build_system_log_row(
    post_id: str,
    caption: str = "caption",
    account: str = "",
    keyword: str = "",
    source_type: str = "Account",
    status: str = "Success",
    eligibility: str = "Eligible",
    competition_id: str = "",
    posted_at: str = "2026-11-10T07:00:00+07:00",
) -> List[str]:
    return [
        post_id,
        f"https://www.instagram.com/p/{post_id}/",
        account,
        keyword,
        source_type,
        posted_at,
        caption,
        posted_at,
        status,
        "",
        eligibility,
        competition_id,
        "",
    ]
