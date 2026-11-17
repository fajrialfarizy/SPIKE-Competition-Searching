"""
compmon.eligibility
Evaluates competition eligibility and deadline rules according to PRD 1.4, 7.4, 7.5 and D4.
"""

from datetime import datetime, date
from typing import Optional, Tuple
from compmon.models import AIExtractionResult, EligibilityStatus
from compmon.normalization import CANONICAL_COMPETITION_TYPES, normalize_competition_type
import dateutil.parser


def parse_date_only(date_str: Optional[str]) -> Optional[date]:
    """Parses a YYYY-MM-DD or standard date string into a date object."""
    if not date_str or not str(date_str).strip():
        return None
    try:
        return dateutil.parser.parse(str(date_str).strip()).date()
    except Exception:
        return None


def evaluate_eligibility_and_deadline(
    extracted: AIExtractionResult,
    today_wib: date
) -> Tuple[EligibilityStatus, str]:
    """
    Applies the decision hierarchy for eligibility (PRD 1.4, 7.4, 7.5, D4):
    1. If not a competition post or competition type outside scope -> INELIGIBLE
    2. If explicit evidence Telkom Univ students cannot join (or only for high school / pros) -> INELIGIBLE
    3. If registration is explicitly closed -> INELIGIBLE
    4. If deadline < today_wib -> INELIGIBLE (D4: passed deadline is excluded)
    5. If deadline == today_wib -> Continue (Eligible/Unknown unless explicitly closed)
    6. If criteria unclear / missing / ambiguous -> UNKNOWN
    7. If open for college students / public -> ELIGIBLE
    
    Returns (EligibilityStatus, explanation_note)
    """
    # 1. Non-competition or out of scope
    if not extracted.is_competition_post:
        return EligibilityStatus.INELIGIBLE, "Bukan informasi kompetisi"

    norm_type = normalize_competition_type(extracted.competition_type or "")
    if norm_type not in CANONICAL_COMPETITION_TYPES:
        return EligibilityStatus.INELIGIBLE, f"Jenis lomba di luar lingkup: {extracted.competition_type}"

    # 2. Explicit Ineligibility based on AI finding
    ai_status = (extracted.eligibility or "").strip().capitalize()
    if ai_status == "Ineligible":
        return EligibilityStatus.INELIGIBLE, "Kriteria peserta tidak memenuhi syarat (khusus non-mahasiswa / terlarang)"

    # 3. Explicitly closed registration
    if extracted.registration_closed_explicit:
        return EligibilityStatus.INELIGIBLE, "Pendaftaran sudah ditutup secara eksplisit"

    # 4. Deadline evaluation (D4)
    deadline_date = parse_date_only(extracted.deadline_pendaftaran)
    if deadline_date:
        if deadline_date < today_wib:
            return EligibilityStatus.INELIGIBLE, f"Deadline sudah lewat ({deadline_date} < {today_wib})"
        # If deadline >= today_wib, continue
    else:
        # Absence of deadline is allowed to enter for manual review (PRD 7.5)
        pass

    # 5. Check start vs deadline consistency if both exist
    start_date = parse_date_only(extracted.tanggal_mulai_registrasi)
    if start_date and deadline_date and start_date > deadline_date:
        # Inconsistency flagged, treat as Unknown for manual check
        return EligibilityStatus.UNKNOWN, f"Tanggal mulai ({start_date}) lebih besar dari deadline ({deadline_date})"

    # 6. Eligible vs Unknown
    if ai_status == "Eligible":
        return EligibilityStatus.ELIGIBLE, "Terbuka untuk mahasiswa"
    
    # Default to Unknown if ambiguous or missing requirements (PRD: absence of evidence is not exclusion)
    return EligibilityStatus.UNKNOWN, "Persyaratan peserta belum pasti / perlu konfirmasi manual"

