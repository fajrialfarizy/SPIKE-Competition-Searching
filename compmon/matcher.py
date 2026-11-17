"""
compmon.matcher
Normalizes competition attributes and matches posts against Competition Report records.
Handles admin rows with missing Competition IDs safely (Instruction 17).
"""

from typing import List, Dict, Tuple, Optional
from compmon.models import CompetitionRecord, AIExtractionResult
from compmon.normalization import normalize_for_matching, normalize_competition_type


def are_attributes_equal(val1: Optional[str], val2: Optional[str]) -> Tuple[bool, bool]:
    """Compares two string attributes after normalization."""
    norm1 = normalize_for_matching(val1 or "")
    norm2 = normalize_for_matching(val2 or "")

    if not norm1 and not norm2:
        return True, True
    if norm1 == norm2:
        return True, False
    return False, False


def are_types_equal(type1: Optional[str], type2: Optional[str]) -> Tuple[bool, bool]:
    """Compares competition types mapped to canonical forms."""
    c1 = normalize_competition_type(type1 or "")
    c2 = normalize_competition_type(type2 or "")
    if not c1 and not c2:
        return True, True
    if c1.lower() == c2.lower():
        return True, False
    return False, False


def are_dates_equal(date1: Optional[str], date2: Optional[str]) -> Tuple[bool, bool]:
    """Compares dates formatted as YYYY-MM-DD."""
    d1 = (date1 or "").strip()
    d2 = (date2 or "").strip()
    if not d1 and not d2:
        return True, True
    if d1 == d2:
        return True, False
    return False, False


def match_competition_record(
    extracted: AIExtractionResult,
    existing_records: List[CompetitionRecord]
) -> Tuple[Optional[str], bool, str, Optional[int]]:
    """
    Matches extracted competition data against existing rows in Competition Report.
    Returns:
    - matched_competition_id: ID if exact match found, None if no match.
    - is_both_empty_match: True if match happened while one or more compared attributes were empty on both sides (D3).
    - note: Informational note (e.g. D2: similar to CMP-xxx but different deadline).
    - matched_row_without_id_idx: Index (0-based) of matched existing record if it has NO Competition ID (Instruction 17).
    """
    target_name = extracted.nama_lomba or ""
    target_org = extracted.penyelenggara or ""
    target_deadline = extracted.deadline_pendaftaran or ""
    target_type = extracted.competition_type or ""

    similar_candidate_id: Optional[str] = None

    for idx, rec in enumerate(existing_records):
        name_match, name_empty = are_attributes_equal(target_name, rec.nama_lomba)
        org_match, org_empty = are_attributes_equal(target_org, rec.penyelenggara)
        type_match, type_empty = are_types_equal(target_type, rec.jenis_lomba)
        deadline_match, deadline_empty = are_dates_equal(target_deadline, rec.deadline_pendaftaran)

        if name_match and org_match and type_match and deadline_match:
            both_empty_used = name_empty or org_empty or type_empty or deadline_empty
            note = ""
            ref_label = rec.competition_id or f"baris_manual_{rec.row_idx or (idx + 2)}"
            if both_empty_used:
                empty_fields = []
                if name_empty: empty_fields.append("Nama Lomba")
                if org_empty: empty_fields.append("Penyelenggara")
                if type_empty: empty_fields.append("Jenis Lomba")
                if deadline_empty: empty_fields.append("Deadline")
                note = f"Cocok dengan {ref_label} (atribut kosong kedua sisi: {', '.join(empty_fields)})"

            matched_id = rec.competition_id if rec.competition_id else None
            unid_idx = idx if not rec.competition_id else None
            return matched_id, both_empty_used, note, unid_idx

        if name_match and org_match and type_match and not deadline_match:
            if not similar_candidate_id and rec.competition_id:
                similar_candidate_id = rec.competition_id

    if similar_candidate_id:
        return None, False, f"mirip ID {similar_candidate_id}, deadline beda", None
    return None, False, "", None


def format_competition_id(highest_existing_num: int) -> str:
    """
    Generates the next sequential Competition ID from the highest existing number (D5).

    `format_competition_id(0)` is the FIRST id ever used: `CMP-000001`.
    Callers must pass the highest number currently present in the sheet, never a
    pre-incremented value (that off-by-one produced `CMP-000002` for the first row).
    """
    return f"CMP-{(highest_existing_num + 1):06d}"
