"""
compmon.retry_policy
Machine-readable extraction-attempt counter (decision D17).

A post may fail extraction at most `Config.max_failed_run_attempts` runs. The counter
lives INSIDE the existing System Log `Error Notes` cell as a trailing marker:

    Extraction Error: Gemini extraction failed after 3 attempts: ... [attempts=2]

No column and no sheet is added for it. All parsing/formatting happens here so the
pipeline never hand-builds or hand-parses the marker.
"""

import re
from typing import Optional

from compmon.models import ProcessingStatus

# Marker must be at the END of the cell: an `[attempts=N]` mentioned in the middle of an
# error message (e.g. quoted text) is never treated as the counter.
_ATTEMPTS_MARKER_RE = re.compile(r"\s*\[attempts=(\d+)\]\s*$")


def parse_attempts(error_notes: Optional[str]) -> int:
    """Number of finished failed runs for this post (no marker -> 0)."""
    if not error_notes:
        return 0
    match = _ATTEMPTS_MARKER_RE.search(str(error_notes))
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except (TypeError, ValueError):
        return 0


def set_attempts(error_notes: Optional[str], attempts: int) -> str:
    """
    Writes the attempt counter at the end of `error_notes`.

    Any previous trailing marker is replaced (repeated updates never stack markers) and
    the rest of the message - however long - is preserved byte for byte.
    """
    base = _ATTEMPTS_MARKER_RE.sub("", str(error_notes or "")).strip()
    marker = f"[attempts={max(0, int(attempts))}]"
    return f"{base} {marker}".strip() if base else marker


def next_attempt(error_notes: Optional[str]) -> int:
    """Attempt number for the failure happening now (1 for a post that never failed)."""
    return parse_attempts(error_notes) + 1


def is_dead(status: str, error_notes: Optional[str], max_attempts: int) -> bool:
    """
    True for a `Failed` post that already used up its automatic retries (decision D17).

    Dead posts are not processed, do not count against the per-run cap, do not count as
    `failed_posts` and do not force `Partial`.
    """
    if status != ProcessingStatus.FAILED.value:
        return False
    return parse_attempts(error_notes) >= max(1, int(max_attempts))
