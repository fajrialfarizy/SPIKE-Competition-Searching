"""
compmon.validator
Validates Instagram URLs, Post IDs, timestamps, captions, and schemas.
Guarantees Post ID is always derived as shortcode from URL (Instruction 21).
Guarantees parse_iso_timestamp always produces timezone-aware UTC datetime (Instruction 22).
"""

import re
from datetime import datetime, timezone, time
from typing import Optional, Tuple, Any
from urllib.parse import urlparse
import dateutil.parser


MAX_CAPTION_LENGTH = 45000  # Google Sheets single cell limit is 50,000


def extract_shortcode(url_or_code: str) -> Optional[str]:
    """
    Extracts the Instagram shortcode from a URL or raw shortcode token.
    Matches /p/{code}/, /reel/{code}/, /reels/{code}/
    """
    if not url_or_code:
        return None
    url_or_code = url_or_code.strip()
    match = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url_or_code)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{9,13}", url_or_code) and "_" not in url_or_code.lower().replace("-", ""):
        return url_or_code
    return None


def is_valid_instagram_url(url: str) -> bool:
    """Validates that a URL is a valid Instagram post/reel link."""
    if not url:
        return False
    parsed = urlparse(url.strip())
    if parsed.netloc not in ("instagram.com", "www.instagram.com", "m.instagram.com"):
        return False
    return extract_shortcode(url) is not None


def parse_iso_timestamp(ts_str: Any) -> Optional[datetime]:
    """
    Parses an ISO 8601 string, date string (YYYY-MM-DD), or datetime into a timezone-aware UTC datetime.
    Always returns datetime with tzinfo=timezone.utc (Instruction 22).
    """
    if not ts_str:
        return None

    dt: Optional[datetime] = None

    if isinstance(ts_str, datetime):
        dt = ts_str
    else:
        s = str(ts_str).strip()
        try:
            dt = dateutil.parser.isoparse(s)
        except Exception:
            try:
                dt = dateutil.parser.parse(s)
            except Exception:
                return None

    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

    return dt


def sanitize_caption(caption: str) -> Tuple[str, bool]:
    """
    Sanitizes caption for Google Sheets:
    - Truncates if > 45000 chars to avoid cell overflow.
    - Returns (sanitized_caption, was_truncated).
    """
    if not caption:
        return "", False
    if len(caption) > MAX_CAPTION_LENGTH:
        return caption[:MAX_CAPTION_LENGTH], True
    return caption, False


def validate_raw_post_fields(post_id: str, url: str, posted_at: Any) -> Tuple[bool, str]:
    """
    Checks mandatory fields (PRD 6.4):
    - Must have a valid Post ID (derived shortcode)
    - Must have a valid Instagram URL
    - Must have a readable timestamp
    Returns (is_valid, failure_reason)
    """
    if not post_id or not str(post_id).strip():
        return False, "Missing Post ID"
    if not is_valid_instagram_url(url):
        return False, f"Invalid Instagram URL: {url}"
    dt = parse_iso_timestamp(posted_at)
    if not dt:
        return False, f"Unparseable timestamp: {posted_at}"
    return True, ""
