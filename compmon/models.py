"""
compmon.models
Data models and dataclasses for the competition monitoring system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class SourceType(str, Enum):
    ACCOUNT = "Account"
    KEYWORD = "Keyword"
    BOTH = "Account, Keyword"


class ProcessingStatus(str, Enum):
    PENDING = "Pending"
    SUCCESS = "Success"
    FAILED = "Failed"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "Eligible"
    INELIGIBLE = "Ineligible"
    UNKNOWN = "Unknown"


class ExecutionStatus(str, Enum):
    SUCCESS = "Success"
    PARTIAL = "Partial"
    FAILED = "Failed"


class InspectionStatus(str, Enum):
    VALID = "Valid"
    INVALID = "Tidak Valid"
    UNREVIEWED = "Belum Diperiksa"


class CompetitionScale(str, Enum):
    NASIONAL = "Nasional"
    INTERNASIONAL = "Internasional"


class FinalExecution(str, Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    HYBRID = "Hybrid"


class KeywordPriority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class KeywordActiveStatus(str, Enum):
    ACTIVE = "Aktif"
    INACTIVE = "Nonaktif"
    CANDIDATE = "Kandidat"


class KeywordEvaluationStatus(str, Enum):
    PENDING = "Menunggu Evaluasi"
    EVALUATED = "Sudah Dievaluasi"


@dataclass
class RawPost:
    """Represents a raw scraped Instagram post."""
    post_id: str
    url: str
    source_accounts: List[str] = field(default_factory=list)
    keyword_source: List[str] = field(default_factory=list)
    source_type: SourceType = SourceType.ACCOUNT
    posted_at: Optional[datetime] = None
    caption: str = ""
    first_seen: Optional[datetime] = None

    def get_source_accounts_str(self) -> str:
        return ", ".join(sorted(list(set(self.source_accounts))))

    def get_keyword_source_str(self) -> str:
        return ", ".join(sorted(list(set(self.keyword_source))))


@dataclass
class SystemLogRecord:
    """Represents an entry in the System Log sheet."""
    post_id: str
    url: str
    source_accounts: str
    keyword_source: str
    source_type: str
    waktu_posting: str
    caption: str
    first_seen: str
    processing_status: str
    processed_at: str
    eligibility_status: str
    competition_id: str
    error_notes: str

    def to_row(self) -> List[Any]:
        return [
            str(self.post_id),
            self.url,
            self.source_accounts,
            self.keyword_source,
            self.source_type,
            self.waktu_posting,
            self.caption,
            self.first_seen,
            self.processing_status,
            self.processed_at,
            self.eligibility_status,
            self.competition_id,
            self.error_notes,
        ]


@dataclass
class AIExtractionResult:
    """Structured output received from Gemini API."""
    is_competition_post: bool = False
    competition_type: Optional[str] = None
    nama_lomba: Optional[str] = None
    universitas: Optional[str] = None
    penyelenggara: Optional[str] = None
    skala: Optional[str] = None
    pelaksanaan_final: Optional[str] = None
    tempat_final: Optional[str] = None
    tanggal_mulai_registrasi: Optional[str] = None
    deadline_pendaftaran: Optional[str] = None
    registration_closed_explicit: bool = False
    link_registrasi: Optional[str] = None
    link_guidebook: Optional[str] = None
    eligibility: str = "Unknown"


@dataclass
class CompetitionRecord:
    """
    Represents an entry in Competition Report (columns A-O).
    Includes row_idx (1-based sheet row index) (Instruction 28).
    """
    competition_id: str
    nama_lomba: str
    jenis_lomba: str
    grade_lomba: str = ""  # Handled by ARRAYFORMULA in Column D
    universitas: str = ""
    penyelenggara: str = ""
    skala: str = ""
    pelaksanaan_final: str = ""
    tempat_final: str = ""
    tanggal_mulai_registrasi: str = ""
    deadline_pendaftaran: str = ""
    link_postingan: str = ""
    link_registrasi: str = ""
    link_guidebook: str = ""
    status_pemeriksaan: str = "Belum Diperiksa"
    row_idx: Optional[int] = None

    def to_row_without_grade(self) -> Dict[str, Any]:
        """Returns row parts: (A-C) and (E-O) to preserve ARRAYFORMULA in Column D."""
        part_ac = [self.competition_id, self.nama_lomba, self.jenis_lomba]
        part_eo = [
            self.universitas,
            self.penyelenggara,
            self.skala,
            self.pelaksanaan_final,
            self.tempat_final,
            self.tanggal_mulai_registrasi,
            self.deadline_pendaftaran,
            self.link_postingan,
            self.link_registrasi,
            self.link_guidebook,
            self.status_pemeriksaan,
        ]
        return {"part_ac": part_ac, "part_eo": part_eo}


@dataclass
class ExecutionLogRecord:
    """Represents an entry in Execution Log."""
    execution_id: str
    notebook_name: str
    start_time: str
    end_time: str
    execution_status: str
    total_posts_found: int
    new_posts: int
    successfully_processed: int
    failed_posts: int
    latest_successful_posting_timestamp: str
    errors_notes: str

    def to_row(self) -> List[Any]:
        return [
            self.execution_id,
            self.notebook_name,
            self.start_time,
            self.end_time,
            self.execution_status,
            self.total_posts_found,
            self.new_posts,
            self.successfully_processed,
            self.failed_posts,
            self.latest_successful_posting_timestamp,
            self.errors_notes,
        ]


@dataclass
class PendingRange:
    """Pending range structure stored in Run State."""
    source_type: str
    source: str
    from_date: str
    to_date: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source_type": self.source_type,
            "source": self.source,
            "from": self.from_date,
            "to": self.to_date,
            "reason": self.reason,
        }
