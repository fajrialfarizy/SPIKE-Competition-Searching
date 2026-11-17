"""
compmon.normalization
Text normalization functions and abbreviations dictionary.
"""

import re
import unicodedata
from typing import Dict

# Dictionary of standard competition abbreviations and academic aliases
ABBREVIATIONS: Dict[str, str] = {
    "bmc": "business model canvas",
    "bpc": "business plan competition",
    "bcc": "business case competition",
    "kti": "karya tulis ilmiah",
    "lkti": "lomba karya tulis ilmiah",
    "lktin": "lomba karya tulis ilmiah nasional",
    "lktii": "lomba karya tulis ilmiah internasional",
    "kdim": "karya tulis ilmiah",
    "karya tulis": "karya tulis ilmiah",
    "paper": "essay",
    "esai": "essay",
    "essay competition": "essay",
    "business plan": "business plan",
    "business case": "business case",
    "business model canvas": "business model canvas",
}

# Standardized Competition Types recognized by PRD
CANONICAL_COMPETITION_TYPES = [
    "Business Plan",
    "Business Model Canvas",
    "Business Case",
    "Essay",
    "Karya Tulis Ilmiah",
]


def clean_text(text: str) -> str:
    """Removes extra whitespaces, newlines, and strips the string."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    # Replace newlines, tabs, and multiple spaces with a single space
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_matching(text: str) -> str:
    """
    Normalizes a text string for exact 4-attribute competition matching:
    - Lowercases
    - Strips accents and punctuation
    - Expands recognized abbreviations
    - Compacts whitespace
    """
    if not text:
        return ""

    text = clean_text(text).lower()

    # Expand recognized abbreviations as whole words
    words = re.findall(r"\b\w+\b", text)
    expanded_words = [ABBREVIATIONS.get(w, w) for w in words]
    expanded_text = " ".join(expanded_words)

    # Remove all punctuation, leave only alphanumeric and spaces
    clean = re.sub(r"[^\w\s]", " ", expanded_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def normalize_competition_type(comp_type: str) -> str:
    """Maps varied competition type strings to PRD canonical enum values."""
    if not comp_type:
        return ""
    norm = normalize_for_matching(comp_type)
    if "business model canvas" in norm or "bmc" in norm:
        return "Business Model Canvas"
    if "business plan" in norm or "bpc" in norm:
        return "Business Plan"
    if "business case" in norm or "bcc" in norm:
        return "Business Case"
    if "karya tulis" in norm or "kti" in norm or "lkti" in norm:
        return "Karya Tulis Ilmiah"
    if "essay" in norm or "esai" in norm or "paper" in norm:
        return "Essay"
    return comp_type.strip()

