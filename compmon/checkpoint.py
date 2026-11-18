"""
compmon.checkpoint
Manages global_checkpoint and pending_ranges in Run State sheet (PRD 6.2, 6.13, D13, D14).
Instruction 25: pending_ranges dedup, remove completed entries, enforce 50k JSON size limit.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Set, Tuple
from compmon.models import PendingRange

logger = logging.getLogger(__name__)

# Google Sheets single-cell limit: 50,000 characters.
# Keep a wide safety margin so admin edits cannot push the cell over the hard limit.
PENDING_RANGES_MAX_CHARS = 30000
# Hard cap on the number of unfinished entries (prevents unbounded growth, Task 25).
MAX_PENDING_ENTRIES = 50
# Long Apify/Gemini error messages are truncated before being persisted (Task 25).
MAX_REASON_CHARS = 240


def _clip_reason(reason: str) -> str:
    """Truncates a pending-range reason to keep the Run State cell small."""
    reason = " ".join(str(reason or "").split())
    if len(reason) > MAX_REASON_CHARS:
        return reason[: MAX_REASON_CHARS - 3] + "..."
    return reason


def parse_pending_ranges(raw_json: Optional[str]) -> List[PendingRange]:
    """Parses JSON string from Run State into a list of PendingRange objects."""
    if not raw_json or not str(raw_json).strip():
        return []
    s = str(raw_json).strip()
    if s in ("[]", ""):
        return []
    try:
        data = json.loads(s)
        if not isinstance(data, list):
            return []
        ranges = []
        for item in data:
            if isinstance(item, dict):
                ranges.append(
                    PendingRange(
                        source_type=str(item.get("source_type", "")),
                        source=str(item.get("source", "")),
                        from_date=str(item.get("from", item.get("from_date", ""))),
                        to_date=str(item.get("to", item.get("to_date", ""))),
                        reason=str(item.get("reason", "")),
                    )
                )
        return ranges
    except Exception:
        return []


def _pending_key(pr: PendingRange) -> Tuple[str, str, str, str]:
    """Returns a deduplication key for a PendingRange (ignores reason)."""
    return (pr.source_type, pr.source, pr.from_date, pr.to_date)


# Fix 8: a ceiling-hit reason records the ceiling that was reached, e.g. "ceiling=300".
# The pattern does not match "max_results_ceiling_per_source=300" on purpose: only the
# explicit marker written by the pipeline identifies a ceiling block.
CEILING_MARKER_RE = re.compile(r"ceiling\s*=\s*(\d+)")


def ceiling_from_reason(reason: Optional[str]) -> Optional[int]:
    """Returns the ceiling recorded in a pending reason ("ceiling=300" -> 300), else None."""
    match = CEILING_MARKER_RE.search(str(reason or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex guarantees digits
        return None


def recorded_ceiling(ranges: List[PendingRange]) -> Optional[int]:
    """Highest ceiling recorded among a source's pending entries (None when none)."""
    values = [v for v in (ceiling_from_reason(pr.reason) for pr in ranges) if v is not None]
    return max(values) if values else None


@dataclass
class PendingMergeResult:
    """
    Result of merging pending ranges.

    `dropped_entries` counts entries that had to be discarded because of the size/count
    cap. It is never allowed to be silently ignored: the pipeline forces the execution
    status to `Partial` when it is non-zero, because unfinished sources were lost.
    """
    ranges: List[PendingRange]
    dropped_entries: int = 0

    @property
    def dropped(self) -> bool:
        return self.dropped_entries > 0


def _earliest(first: str, second: str) -> str:
    """Earliest of two ISO date strings, ignoring empty values."""
    first, second = (first or "").strip(), (second or "").strip()
    if not first:
        return second
    if not second:
        return first
    return min(first, second)


def _latest(first: str, second: str) -> str:
    """Latest of two ISO date strings, ignoring empty values."""
    first, second = (first or "").strip(), (second or "").strip()
    if not first:
        return second
    if not second:
        return first
    return max(first, second)


def merge_pending_ranges(
    existing: List[PendingRange],
    new_ranges: List[PendingRange],
    completed_keys: Optional[Set[Tuple[str, str, str, str]]] = None
) -> PendingMergeResult:
    """
    Instruction 25, refined:

    - Entries belonging to a completed source are removed (a completed `completed_keys`
      entry marks the whole `(source_type, source)` pair as finished).
    - Remaining entries are collapsed to ONE entry per `(source_type, source)`: earliest
      `from`, latest `to`, newest reason. This keeps the cell small without losing a range.
    - The count and JSON-size caps then drop the oldest entries as a last resort; every
      dropped entry is counted in `dropped_entries` so callers can force `Partial` instead
      of pretending everything was scheduled.
    """
    completed_keys = completed_keys or set()
    completed_sources: Set[Tuple[str, str]] = {(k[0], k[1]) for k in completed_keys}

    merged: List[PendingRange] = []
    by_source: Dict[Tuple[str, str], PendingRange] = {}

    for pr in list(existing) + list(new_ranges):
        source_key = (pr.source_type, pr.source)
        if source_key in completed_sources:
            logger.info(f"Removing completed pending range: {pr.source_type}:{pr.source}")
            continue

        current = by_source.get(source_key)
        if current is None:
            collapsed = PendingRange(
                source_type=pr.source_type,
                source=pr.source,
                from_date=(pr.from_date or "").strip(),
                to_date=(pr.to_date or "").strip(),
                reason=_clip_reason(pr.reason),
            )
            by_source[source_key] = collapsed
            merged.append(collapsed)
            continue

        current.from_date = _earliest(current.from_date, pr.from_date)
        current.to_date = _latest(current.to_date, pr.to_date)
        if (pr.reason or "").strip():
            current.reason = _clip_reason(pr.reason)

    dropped_entries = 0

    # Enforce the entry cap: the oldest (least recently reported) entries go first.
    while len(merged) > MAX_PENDING_ENTRIES:
        dropped = merged.pop(0)
        dropped_entries += 1
        logger.warning(
            f"pending_ranges has more than {MAX_PENDING_ENTRIES} entries, "
            f"dropped oldest entry: {dropped.source_type}:{dropped.source}"
        )

    # Enforce the JSON size limit as a last resort.
    while merged:
        serialized = serialize_pending_ranges(merged)
        if len(serialized) <= PENDING_RANGES_MAX_CHARS:
            break
        dropped = merged.pop(0)
        dropped_entries += 1
        logger.warning(
            f"pending_ranges JSON too large (>{PENDING_RANGES_MAX_CHARS} chars), "
            f"dropped oldest entry: {dropped.source_type}:{dropped.source}"
        )

    if dropped_entries:
        logger.warning(
            f"pending_ranges dropped {dropped_entries} unfinished entr(ies); "
            f"the execution must be reported as Partial."
        )

    return PendingMergeResult(ranges=merged, dropped_entries=dropped_entries)


def serialize_pending_ranges(ranges: List[PendingRange]) -> str:
    """Serializes a list of PendingRange objects into a clean JSON string."""
    return json.dumps([r.to_dict() for r in ranges], indent=2, ensure_ascii=False)


def group_pending_by_source(
    ranges: List[PendingRange]
) -> Dict[Tuple[str, str], List[PendingRange]]:
    """
    Groups pending entries by (source_type, source) so each unfinished source is scraped once
    per run, using the earliest recorded `from` date (Task 25).
    """
    grouped: Dict[Tuple[str, str], List[PendingRange]] = {}
    for pr in ranges:
        grouped.setdefault((pr.source_type, pr.source), []).append(pr)
    return grouped


def checkpoint_should_advance(
    attempted_sources: List[Tuple[str, str]],
    completed_source_keys: Set[Tuple[str, str]],
    pending_ranges: List[PendingRange],
    dropped_entries: int = 0,
    new_posts_stored: bool = True,
    total_sources: int = 0,
    completed_sources: int = 0,
) -> bool:
    """
    Decision D18 (owner-approved override of PRD 6.2 / 6.13):

    The checkpoint describes SCRAPING coverage, not extraction success. Raw posts are
    stored before AI processing and extraction retries read from System Log, so a failed
    Gemini extraction never justifies re-scraping (and re-paying Apify for) the window.

    The checkpoint advances when ALL of these hold:

    (a) every source attempted in this run was either fully scraped or recorded in
        `pending_ranges` with its own `from` date (nothing is silently lost);
    (b) the `pending_ranges` merge dropped nothing (`dropped_entries == 0`);
    (c) every newly scraped post was written to System Log;
    (d) it was not an outage: if `total_sources > 0` then `completed_sources >= 1`.
        A run in which no source finished (every source errored, hit the pagination
        ceiling, or was deferred by the per-run cap) keeps the old checkpoint, even when
        some posts were stored.

    Things that deliberately do NOT block it: Gemini extraction failures, dead posts
    (D17), a `GeminiPermanentError` halt, posts left `Pending` by the processing cap, and a
    source that was not scraped at all because its ceiling entry is waiting for the owner to
    raise `max_results_ceiling_per_source` (Fix 8: such a source is not attempted, so it is
    neither counted in `total_sources` nor in `attempted_sources`).
    """
    # (b) A dropped pending entry means an unfinished source was lost.
    if dropped_entries > 0:
        return False

    # (c)
    if not new_posts_stored:
        return False

    # (d) Literal: no completed source means the run is treated as an outage.
    if total_sources > 0 and completed_sources < 1:
        return False

    # (a)
    pending_from: Dict[Tuple[str, str], str] = {
        (pr.source_type, pr.source): (pr.from_date or "").strip() for pr in pending_ranges
    }
    for source_key in attempted_sources:
        if source_key in completed_source_keys:
            continue
        if not pending_from.get(source_key):
            logger.warning(
                "Checkpoint not advanced: unfinished source "
                f"{source_key[0]}:{source_key[1]} is missing from pending_ranges"
            )
            return False

    return True

