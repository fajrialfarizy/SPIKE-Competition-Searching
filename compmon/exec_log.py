"""
compmon.exec_log
Execution logging module to generate execution IDs and format Execution Log records.
Requires status to be 'Partial' whenever pending ranges or uncompleted sources exist (Instruction 16).
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Any
from compmon.models import ExecutionLogRecord, ExecutionStatus, PendingRange


def generate_execution_id(notebook_code: str, timezone_str: str = "Asia/Jakarta") -> str:
    """
    Generates Execution ID formatted as: EXE-YYYYMMDD-HHMMSS-<notebook_code> (WIB).
    """
    now = datetime.now(ZoneInfo(timezone_str))
    ts = now.strftime("%Y%m%d-%H%M%S")
    return f"EXE-{ts}-{notebook_code.upper()}"


def format_iso_wib(dt: Optional[datetime], timezone_str: str = "Asia/Jakarta") -> str:
    """Formats datetime as ISO 8601 with WIB (+07:00) offset for Google Sheets."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo(timezone_str)).isoformat()


def determine_execution_status(
    total_sources_count: int,
    completed_sources_count: int,
    total_new_posts: int,
    failed_posts: int,
    hit_limit: bool = False,
    pending_ranges: Optional[List[Any]] = None,
    halted: bool = False
) -> str:
    """
    Determines execution status (Success, Partial, Failed) (Instruction 16, Task 25):
    - 'Failed' ONLY if nothing usable happened: no source completed, no new post and
      nothing left pending (e.g. every source raised an error).
    - 'Partial' when the run was stopped early (`halted`, e.g. invalid API key), when the
      per-run post budget was reached (`hit_limit`), when a source is unfinished
      (`pending_ranges`), when fewer sources completed than were attempted, or when a post
      failed.
    - 'Success' ONLY when every source completed, nothing is pending, nothing was halted
      and no post failed.

    `pending_ranges` must therefore only contain entries that are genuinely unfinished:
    completed entries are removed before this is called, otherwise the run could never
    reach 'Success' again.
    """
    has_pending = bool(pending_ranges and len(pending_ranges) > 0)

    if (
        completed_sources_count == 0
        and total_sources_count > 0
        and total_new_posts == 0
        and not has_pending
        and not halted
        and not hit_limit
    ):
        return ExecutionStatus.FAILED.value

    if (
        hit_limit
        or halted
        or has_pending
        or (completed_sources_count < total_sources_count)
        or (failed_posts > 0)
    ):
        return ExecutionStatus.PARTIAL.value

    return ExecutionStatus.SUCCESS.value
