"""
compmon.gemini_extractor
Extracts structured competition metadata and initial eligibility from captions using Gemini API.
"""

import json
import logging
import time
import random
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from compmon.models import AIExtractionResult

logger = logging.getLogger(__name__)

GEMINI_EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "is_competition_post": {
            "type": "BOOLEAN",
            "description": "True if caption is announcing/promoting an eligible student competition."
        },
        "competition_type": {
            "type": "STRING",
            "enum": [
                "Business Plan",
                "Business Model Canvas",
                "Business Case",
                "Essay",
                "Karya Tulis Ilmiah",
                "null"
            ],
            "description": "Exact category of the competition from allowed list, or null."
        },
        "nama_lomba": {
            "type": "STRING",
            "description": "Official title of the competition, or null."
        },
        "universitas": {
            "type": "STRING",
            "description": "University organizing or affiliated with the event (if explicitly mentioned), or null."
        },
        "penyelenggara": {
            "type": "STRING",
            "description": "Specific committee/institution organizing the event (e.g. BEM FEB UI), or null."
        },
        "skala": {
            "type": "STRING",
            "enum": ["Nasional", "Internasional", "null"],
            "description": "Scale of event, or null."
        },
        "pelaksanaan_final": {
            "type": "STRING",
            "enum": ["Online", "Offline", "Hybrid", "null"],
            "description": "Mode of final stage, or null."
        },
        "tempat_final": {
            "type": "STRING",
            "description": "Physical location/city of final if offline/hybrid, or null."
        },
        "tanggal_mulai_registrasi": {
            "type": "STRING",
            "description": "Start of registration formatted as YYYY-MM-DD, or null."
        },
        "deadline_pendaftaran": {
            "type": "STRING",
            "description": "Registration deadline formatted as YYYY-MM-DD (latest extension if stated), or null."
        },
        "registration_closed_explicit": {
            "type": "BOOLEAN",
            "description": "True if the caption explicitly states registration is officially closed."
        },
        "link_registrasi": {
            "type": "STRING",
            "description": "Exact registration URL that literally appears in the caption text, or null."
        },
        "link_guidebook": {
            "type": "STRING",
            "description": "Exact guidebook/rulebook URL that literally appears in the caption text, or null."
        },
        "eligibility": {
            "type": "STRING",
            "enum": ["Eligible", "Ineligible", "Unknown"],
            "description": "Eligibility for Telkom University students based on rules provided."
        }
    },
    "required": [
        "is_competition_post",
        "competition_type",
        "nama_lomba",
        "penyelenggara",
        "skala",
        "pelaksanaan_final",
        "deadline_pendaftaran",
        "registration_closed_explicit",
        "eligibility"
    ]
}


def build_system_instruction(eligibility_rules: List[Any]) -> str:
    """Builds system prompt formatting each rule with Category and Rule Text (Instruction 9)."""
    rules_formatted = []
    for r in eligibility_rules:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            cat, text = r[0], r[1]
            rules_formatted.append(f"[{cat}] {text}")
        elif isinstance(r, str) and r.strip():
            rules_formatted.append(r.strip())

    rules_text = "\n".join(f"- {rf}" for rf in rules_formatted)
    return f"""You are an expert AI data extraction assistant for an Indonesian university competition monitoring system.
Your mission is to analyze Instagram captions and extract accurate competition metadata.

Eligibility Rules by Category:
{rules_text}

Critical Instructions:
1. Process ONLY the provided caption. Do NOT hallucinate, guess, or invent details not present in the text.
2. If an attribute is missing or uncertain, return null.
3. Date format: Dates MUST strictly follow YYYY-MM-DD format (e.g., 2026-11-20). If date is not verifiable or invalid, return null.
4. Anti-hallucination URL rule: 'link_registrasi' and 'link_guidebook' MUST be URLs that literally appear in the caption text. If not present in caption, return null.
5. Year deduction: If the caption mentions a date without a year (e.g., '15 Oktober'), deduce the year based on the post publication date context provided.
6. Extensions: If an official registration extension is announced, use the latest extended deadline.
7. University inference: Infer university only if affiliation is unambiguous (e.g. 'BEM Universitas Indonesia').
"""


def _url_in_caption(url: str, caption: str) -> bool:
    if not url or not caption:
        return False
    u = url.lower().strip()
    c = caption.lower()
    if u in c:
        return True
    core_url = re.sub(r"^https?://", "", u).rstrip("/")
    if core_url in c:
        return True
    return False


def validate_strict_date_format(date_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Validates date strictly with regex ^\\d{4}-\\d{2}-\\d{2}$ and checks calendar validity (Instruction 10).
    Returns (valid_date_or_none, error_note_or_none).
    """
    if not date_str:
        return None, None
    d = str(date_str).strip()
    if not re.fullmatch(r"^\d{4}-\d{2}-\d{2}$", d):
        return None, f"Format tanggal tidak valid: '{d}' (harus YYYY-MM-DD)"
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d, None
    except ValueError as e:
        return None, f"Tanggal kalender tidak valid: '{d}' ({e})"


def validate_extracted_links(extracted: AIExtractionResult, raw_caption: str) -> AIExtractionResult:
    """Anti-hallucination validation in Python: checks if links actually appear in caption."""
    caption = raw_caption or ""
    if extracted.link_registrasi:
        if not _url_in_caption(extracted.link_registrasi, caption):
            extracted.link_registrasi = None
    if extracted.link_guidebook:
        if not _url_in_caption(extracted.link_guidebook, caption):
            extracted.link_guidebook = None
    return extracted


class GeminiPermanentError(Exception):
    """Raised when Gemini encounters permanent unrecoverable error (e.g. invalid API key, quota exhausted)."""
    pass


class GeminiExtractor:
    """Handles communication with Gemini API with retries, exponential backoff, and JSON validation."""

    def __init__(self, api_key: str, model_name: str = "gemini-3.5-flash-lite", client: Any = None):
        self.api_key = api_key
        self.model_name = model_name
        if client is not None:
            self.client = client
        else:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except ImportError:
                self.client = None

    def extract_competition_info(
        self,
        caption: str,
        posted_at_iso: str,
        today_wib: str,
        eligibility_rules: List[Any],
        max_retries: int = 3,
        delay_seconds: int = 6
    ) -> Tuple[AIExtractionResult, List[str]]:
        """
        Calls Gemini API with structured JSON output and exponential backoff on 429 / server error.
        Raises GeminiPermanentError on permanent errors (API key invalid, daily quota exhausted).
        Returns (AIExtractionResult, list_of_validation_notes).
        """
        if not self.client:
            raise RuntimeError("google-genai library is not installed or client is None")

        prompt = f"""Context:
- Post Published At: {posted_at_iso}
- Today (WIB): {today_wib}

Caption:
\"\"\"
{caption}
\"\"\"
"""
        system_instruction = build_system_instruction(eligibility_rules)
        validation_notes: List[str] = []

        for attempt in range(1, max_retries + 1):
            try:
                try:
                    from google.genai import types
                    config_obj = types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=GEMINI_EXTRACTION_SCHEMA,
                        temperature=0.0,
                    )
                except Exception:
                    config_obj = {
                        "system_instruction": system_instruction,
                        "response_mime_type": "application/json",
                        "response_schema": GEMINI_EXTRACTION_SCHEMA,
                        "temperature": 0.0,
                    }

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config_obj,
                )

                raw_text = (response.text or "").strip()
                parsed = json.loads(raw_text)

                for k, v in list(parsed.items()):
                    if v == "null":
                        parsed[k] = None

                # Strict date format validation (Instruction 10)
                raw_start = parsed.get("tanggal_mulai_registrasi")
                valid_start, start_err = validate_strict_date_format(raw_start)
                if start_err:
                    validation_notes.append(start_err)

                raw_deadline = parsed.get("deadline_pendaftaran")
                valid_deadline, deadline_err = validate_strict_date_format(raw_deadline)
                if deadline_err:
                    validation_notes.append(deadline_err)

                res = AIExtractionResult(
                    is_competition_post=bool(parsed.get("is_competition_post", False)),
                    competition_type=parsed.get("competition_type"),
                    nama_lomba=parsed.get("nama_lomba"),
                    universitas=parsed.get("universitas"),
                    penyelenggara=parsed.get("penyelenggara"),
                    skala=parsed.get("skala"),
                    pelaksanaan_final=parsed.get("pelaksanaan_final"),
                    tempat_final=parsed.get("tempat_final"),
                    tanggal_mulai_registrasi=valid_start,
                    deadline_pendaftaran=valid_deadline,
                    registration_closed_explicit=bool(parsed.get("registration_closed_explicit", False)),
                    link_registrasi=parsed.get("link_registrasi"),
                    link_guidebook=parsed.get("link_guidebook"),
                    eligibility=parsed.get("eligibility", "Unknown") or "Unknown",
                )

                res = validate_extracted_links(res, caption)
                return res, validation_notes

            except Exception as e:
                err_str = str(e)
                lower_err = err_str.lower()
                # Check for permanent errors using SDK error types/codes (Instruction 6).
                #
                # A daily quota is permanent for this run: retrying cannot help until the
                # quota resets. Google reports it either as "daily quota" or through the
                # quota metric/id, which contains "PerDay" (e.g.
                # `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). Per-minute 429s
                # (e.g. `...PerMinutePerProject...`) must KEEP the retry/backoff path.
                is_permanent = (
                    "API_KEY_INVALID" in err_str or
                    "unregistered" in err_str or
                    "PERMISSION_DENIED" in err_str or
                    "daily quota" in lower_err or
                    "perday" in lower_err or
                    getattr(e, "code", None) in (401, 403)
                )

                if is_permanent:
                    logger.error(f"Permanent Gemini error: {err_str}. Halting execution immediately.")
                    raise GeminiPermanentError(f"Permanent Gemini Error: {err_str}") from e

                logger.warning(f"Gemini API attempt {attempt}/{max_retries} failed: {err_str}")
                if attempt == max_retries:
                    raise RuntimeError(f"Gemini extraction failed after {max_retries} attempts: {err_str}") from e

                sleep_time = (delay_seconds * (2 ** (attempt - 1))) + random.uniform(0.1, 0.5)
                time.sleep(sleep_time)

        raise RuntimeError("Gemini extraction failed unexpectedly")
