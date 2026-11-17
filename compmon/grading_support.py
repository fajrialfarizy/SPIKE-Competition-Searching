"""
compmon.grading_support
Python reference implementation ("oracle") for Grade Lomba (PRD 8, 6.12).

It mirrors the ARRAYFORMULA written by `templates/build_template.py`:

- matching is done on token sequences (lower-case, punctuation turned into spaces) so an
  alias such as `UI` matches `BEM UI` but never `equity`, `guide` or `quiz`;
- the grade rules are data, not code: the first matching rule by Priority wins, so this
  oracle consumes exactly the same table that lives in the `Grade Rules` sheet;
- `Hybrid` counts as `Offline`;
- Top University is matched against Universitas and Penyelenggara, Top Organizer against
  Penyelenggara only.
"""

import re
from typing import List, Optional, Dict, Any, Tuple



# Default rule table, identical to the rows written into the `Grade Rules` sheet.
DEFAULT_GRADE_RULES: List[Dict[str, Any]] = [
    {"Priority": 1, "Grade": "Advance", "Entity": "Top University", "Skala": "Internasional", "Mode": "Any"},
    {"Priority": 2, "Grade": "Advance", "Entity": "Top University", "Skala": "Any", "Mode": "Offline"},
    {"Priority": 3, "Grade": "Advance", "Entity": "Top Organizer", "Skala": "Any", "Mode": "Offline"},
    {"Priority": 4, "Grade": "Advance", "Entity": "Top Organizer", "Skala": "Internasional", "Mode": "Any"},
    {"Priority": 5, "Grade": "Professional", "Entity": "Top University", "Skala": "Any", "Mode": "Online"},
    {"Priority": 6, "Grade": "Professional", "Entity": "Top Organizer", "Skala": "Any", "Mode": "Online"},
    {"Priority": 7, "Grade": "Intermediate", "Entity": "Any", "Skala": "Internasional", "Mode": "Any"},
    {"Priority": 8, "Grade": "Beginner", "Entity": "Any", "Skala": "Any", "Mode": "Any"},
]


def tokenize(text: Optional[str]) -> str:
    """
    Lower-cases, replaces punctuation with spaces and pads the result with single spaces:
    "BEM FEB UI (Depok)" -> " bem feb ui depok ".

    Padding makes `" ui " in tokenize(...)` equivalent to a regex word-boundary match,
    without any regex escaping problems and without `\\b\\b` style false positives.
    """
    if not text:
        return " "
    lowered = str(text).lower()
    stripped = re.sub(r"[^\w\s]+", " ", lowered, flags=re.UNICODE)
    compact = re.sub(r"\s+", " ", stripped).strip()
    return f" {compact} "


def is_entity_match(target_text: str, canonical_name: str, aliases: List[str]) -> bool:
    """
    True when the canonical name or any non-empty alias appears in `target_text` as a
    whole phrase. Empty aliases are ignored (they used to match everything).
    """
    if not target_text:
        return False
    target = tokenize(target_text)
    for name in [canonical_name] + list(aliases or []):
        phrase = tokenize(name).strip()
        if not phrase:
            continue
        if f" {phrase} " in target:
            return True
    return False


def _matches_any_entity(
    target_texts: List[str],
    entities: List[Tuple[str, List[str]]]
) -> bool:
    for target in target_texts:
        for canon, aliases in entities or []:
            if is_entity_match(target, canon, aliases):
                return True
    return False


def match_grade_rule(
    rule: Dict[str, Any],
    is_top_university: bool,
    is_top_organizer: bool,
    is_internasional: bool,
    mode: str,
) -> bool:
    """Evaluates one `Grade Rules` row against the computed row facts."""
    entity = str(rule.get("Entity", "Any")).strip()
    skala = str(rule.get("Skala", "Any")).strip()
    rule_mode = str(rule.get("Mode", "Any")).strip()

    if entity == "Top University":
        entity_ok = is_top_university
    elif entity == "Top Organizer":
        entity_ok = is_top_organizer
    elif entity == "Any":
        entity_ok = True
    else:
        entity_ok = False

    if skala == "Internasional":
        skala_ok = is_internasional
    elif skala == "Any":
        skala_ok = True
    else:
        skala_ok = False

    if rule_mode == "Any":
        mode_ok = True
    else:
        mode_ok = (rule_mode == mode)

    return entity_ok and skala_ok and mode_ok


def calculate_grade(
    penyelenggara: Optional[str],
    skala: Optional[str],
    pelaksanaan_final: Optional[str],
    universitas: Optional[str],
    top_universities: List[tuple],  # List of (Canonical Name, [Aliases])
    top_organizers: List[tuple],    # List of (Canonical Name, [Aliases])
    grade_rules: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Computes Grade Lomba exactly like column D of Competition Report.

    Required inputs: Penyelenggara, Skala, Pelaksanaan Final. If any is empty the grade is
    "Unknown". The caller decides what a completely blank row should be ("" in the sheet).
    """
    if not penyelenggara or not str(penyelenggara).strip():
        return "Unknown"
    if not skala or not str(skala).strip():
        return "Unknown"
    if not pelaksanaan_final or not str(pelaksanaan_final).strip():
        return "Unknown"

    skala_norm = tokenize(skala)
    if " internasional " in skala_norm:
        is_internasional = True
    elif " nasional " in skala_norm:
        is_internasional = False
    else:
        # PRD only defines Nasional/Internasional; anything else cannot be graded.
        return "Unknown"

    mode_norm = tokenize(pelaksanaan_final)
    if " offline " in mode_norm or " hybrid " in mode_norm:
        mode = "Offline"        # Hybrid counts as Offline (PRD 8)
    elif " online " in mode_norm:
        mode = "Online"
    else:
        return "Unknown"

    is_top_university = _matches_any_entity(
        [universitas or "", penyelenggara], top_universities
    )
    is_top_organizer = _matches_any_entity([penyelenggara], top_organizers)

    rules = sorted(
        grade_rules if grade_rules is not None else DEFAULT_GRADE_RULES,
        key=lambda r: int(r.get("Priority", 9999)),
    )
    for rule in rules:
        if match_grade_rule(
            rule, is_top_university, is_top_organizer, is_internasional, mode
        ):
            return str(rule.get("Grade", "")).strip()

    return ""
