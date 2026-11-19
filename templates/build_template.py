"""
templates/build_template.py
Creates and initializes the Google Sheets template safely (PRD Section 4 & 5).

Rules:
- NEVER clears existing sheets or overwrites admin data. Only missing sheets are created;
  for existing sheets only a missing header row is filled in.
- Competition Report column D holds one ARRAYFORMULA built from the `Grade Rules` table;
  it is only (re)written when the cell is empty or still holds a formula this generator
  produced (an older named-range version included), never an admin's custom formula.
- Fix 10: D2 references the alias-pattern helper cells directly ('Grade Rules'!$G$2 and
  $G$3), never a named range, so a rejected `addNamedRange` cannot leave D2 empty.
- Sheets are created with enough rows for explicit-range writes (Task 27).
- Protected ranges are idempotent, cover the whole sheet and are warning-only so that any
  admin running the notebook can still write a Competition ID in column A (Task 27).
- Fix 9: formulas stay canonical (comma) here and are localized only when written, using
  the spreadsheet's own locale (`;` for decimal-comma locales such as `id_ID`). The locale
  and the time zone are read, never changed; a non-Jakarta time zone only prints a warning.
- Fix 9: D2 / G2:G3 are read with the FORMULA render option, so a computed `""` can never
  look like an empty cell: a generated formula is refreshed only when it really differs and
  an admin's custom formula is never overwritten.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from templates.formula_locale import (
    FORMULA_ERROR_HINT, FormulaLocaleError, canonicalize_formula, is_formula_error,
    localize_formula, other_separator, separator_for_locale,
)

logger = logging.getLogger(__name__)

# Grid size of every created sheet: explicit-range writes such as A1200:O1200 must fit.
SHEET_ROWS = 2000
SHEET_COLS = 20

PROTECTED_RANGE_DESCRIPTIONS = {
    "competition_id": "Protected: Competition ID Primary Key",
    "grade_formula": "Protected: Grade Lomba Formula",
}

INITIAL_SHEETS_DATA: Dict[str, Dict[str, Any]] = {
    "Competition Report": {
        "headers": [
            "Competition ID", "Nama Lomba", "Jenis Lomba", "Grade Lomba", "Universitas",
            "Penyelenggara", "Skala", "Pelaksanaan Final", "Tempat Final",
            "Tanggal Mulai Registrasi", "Deadline Pendaftaran", "Link Postingan",
            "Link Registrasi", "Link Guidebook", "Status Pemeriksaan"
        ],
        "rows": []
    },
    "System Log": {
        "headers": [
            "Post ID", "URL Postingan", "Source Accounts", "Keyword Source", "Source Type",
            "Waktu Posting", "Caption", "First Seen", "Processing Status", "Processed At",
            "Eligibility Status", "Competition ID", "Error Notes"
        ],
        "rows": []
    },
    "Execution Log": {
        "headers": [
            "Execution ID", "Notebook / Process Name", "Start Time", "End Time",
            "Execution Status", "Total Posts Found", "New Posts", "Successfully Processed",
            "Failed Posts", "Latest Successful Posting Timestamp", "Errors / Notes"
        ],
        "rows": []
    },
    "Config": {
        "headers": ["Key", "Value", "Description"],
        "rows": [
            ["apify_actor_accounts", "apify/instagram-scraper", "Apify Actor ID untuk scraping akun profil"],
            ["apify_actor_keyword", "seemuapps/instagram-keyword-posts-scraper", "Apify Actor ID untuk pencarian keyword"],
            ["gemini_model", "gemini-3.5-flash-lite", "Model Gemini yang digunakan (diatur di Config)"],
            ["max_new_posts_per_run", "100", "Batas maksimal post BARU yang diambil per satu run notebook"],
            ["max_results_per_input", "20", "Jumlah hasil per sumber per actor run (juga langkah pagination)"],
            ["max_results_ceiling_per_source", "200", "Batas atas pagination resultsLimit per sumber; bila tercapai sumber dicatat di pending_ranges"],
            ["account_start_limit_initial", "20", "resultsLimit pertama untuk Source Accounts pada Initial Scraping (di-clamp ke max_results_ceiling_per_source)"],
            ["account_start_limit_routine", "20", "resultsLimit pertama untuk Source Accounts pada Routine Scraping (di-clamp ke max_results_ceiling_per_source)"],
            ["gemini_delay_seconds", "6", "Jeda detik antar request sequential ke Gemini API"],
            ["gemini_max_retries", "3", "Maksimal percobaan ulang request Gemini API saat terjadi error/429"],
            ["max_failed_run_attempts", "3", "Maksimal jumlah run di mana satu post boleh gagal ekstraksi sebelum tidak diulang otomatis (D17)"],
            ["batch_size", "10", "Ukuran batch internal pemrosesan dan penulisan status"],
            ["initial_days_accounts", "60", "Rentang hari scraping awal untuk Source Accounts"],
            ["initial_days_core_keywords", "30", "Rentang hari scraping awal untuk Core Keywords"],
            ["initial_days_discovery_keywords", "30", "Rentang hari scraping awal untuk Discovery Keywords"],
            ["min_reviewed_for_priority", "10", "Minimum post direview sebelum prioritas keyword dapat berubah"],
            ["threshold_high", "70", "Ambang batas Relevance Rate (%) untuk prioritas High"],
            ["threshold_medium", "40", "Ambang batas Relevance Rate (%) untuk prioritas Medium"],
            ["timezone", "Asia/Jakarta", "Zona waktu operasional sistem"],
        ]
    },
    "Run State": {
        "headers": ["Key", "Value", "Updated At"],
        "rows": [
            ["global_checkpoint", "", ""],
            ["pending_ranges", "[]", ""],
            ["keyword_run_counter", "0", ""],
            ["last_execution_id", "", ""],
        ]
    },
    "Source Accounts": {
        "headers": ["Instagram Username"],
        "rows": [
            ["lombabisnis"],
            ["lombabisnisplan"],
            ["info_lomba_kti_essay_esai"],
            ["infolomba"],
        ]
    },
    "Master Top University": {
        "headers": ["Canonical Name", "Aliases"],
        "rows": [
            ["Universitas Indonesia", "UI"],
            ["Universitas Gadjah Mada", "UGM"],
            ["Universitas Airlangga", "UNAIR"],
            ["Institut Teknologi Bandung", "ITB"],
        ]
    },
    "Master Top Organizer": {
        "headers": ["Canonical Name", "Aliases"],
        "rows": [
            ["Unilever", ""],
            ["Gojek", ""],
            ["Huawei", ""],
            ["Pertamina", ""],
            ["Tokopedia", ""],
        ]
    },
    "Grade Rules": {
        "headers": ["Priority", "Grade", "Entity", "Skala", "Mode"],
        "rows": [
            ["1", "Advance", "Top University", "Internasional", "Any"],
            ["2", "Advance", "Top University", "Any", "Offline"],
            ["3", "Advance", "Top Organizer", "Any", "Offline"],
            ["4", "Advance", "Top Organizer", "Internasional", "Any"],
            ["5", "Professional", "Top University", "Any", "Online"],
            ["6", "Professional", "Top Organizer", "Any", "Online"],
            ["7", "Intermediate", "Any", "Internasional", "Any"],
            ["8", "Beginner", "Any", "Any", "Any"],
        ]
    },
    "Eligibility Rules": {
        "headers": ["Rule ID", "Category", "Rule Text", "Active"],
        "rows": [
            ["R1", "Eligible", "Terbuka untuk mahasiswa perguruan tinggi secara umum di Indonesia", "True"],
            ["R2", "Eligible", "Terbuka untuk mahasiswa dan masyarakat umum", "True"],
            ["R3", "Eligible", "Terbuka secara khusus untuk mahasiswa Telkom University", "True"],
            ["R4", "Ineligible", "Secara eksplisit hanya terbuka untuk mahasiswa universitas tertentu selain Telkom University", "True"],
            ["R5", "Ineligible", "Khusus siswa SMA/SMK/sederajat dan tidak memiliki kategori mahasiswa", "True"],
            ["R6", "Ineligible", "Khusus dosen, peneliti, atau tenaga profesional tanpa jalur partisipasi mahasiswa", "True"],
            ["R7", "Ineligible", "Secara eksplisit melarang mahasiswa Telkom University", "True"],
            ["R8", "Ineligible", "Bukan informasi kompetisi atau perlombaan", "True"],
            ["R9", "Ineligible", "Pendaftaran kompetisi sudah resmi ditutup atau deadline telah terlewati", "True"],
            ["R10", "Unknown", "Persyaratan peserta tidak ditemukan atau informasi tidak lengkap", "True"],
            ["R11", "Unknown", "Informasi persyaratan ambigu atau saling bertentangan", "True"],
        ]
    },
    "Search Keywords": {
        "headers": [
            "Keyword", "Category", "Priority", "Active Status", "Frequency", "Last Evaluated",
            "Total Found", "Total Reviewed", "Valid Count", "Invalid Count", "Relevance Rate", "Evaluation Status"
        ],
        "rows": [
            ["lomba business plan", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["business plan competition", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["lomba business model canvas", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["business model canvas competition", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["lomba business case", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["business case competition", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["lomba essay mahasiswa", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["essay competition", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["lomba karya tulis ilmiah", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["LKTI mahasiswa", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["student competition", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["national competition mahasiswa", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["international competition mahasiswa", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["call for participants", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["open registration competition", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["registration is now open", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
            ["kompetisi nasional mahasiswa", "Discovery", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"],
        ]
    }
}

# Column layout of Competition Report (1-based)
COL_NAMA = "B"
COL_JENIS = "C"
COL_GRADE = "D"
COL_UNIV = "E"
COL_PENY = "F"
COL_SKALA = "G"
COL_MODE = "H"


# ---------------------------------------------------------------------------
# Grade formula (Task 26)
# ---------------------------------------------------------------------------

def _sheet_norm(expr: str) -> str:
    """
    Sheet expression that lower-cases, replaces punctuation with spaces and collapses
    whitespace: the same token normalization the Python oracle uses.
    """
    return (
        f'REGEXREPLACE(REGEXREPLACE(LOWER({expr}),"[[:punct:]]+"," "),"\\s+"," ")'
    )


def _sheet_padded(expr: str) -> str:
    """Sheet expression for a token-normalized string padded with single spaces."""
    return f'(" "&{_sheet_norm(expr)}&" ")'


# Each compiled alias pattern lives in ONE generated cell of the `Grade Rules` sheet and is
# referenced by D2 through its ABSOLUTE CELL ADDRESS (Fix 10). Inlining the
# SPLIT/FLATTEN/TEXTJOIN/LET expression in every rule branch made `Competition Report!D2`
# several KB long; a branch now only references the cell, which keeps the formula small and
# fast to recalculate. Named ranges are NOT used: a rejected `addNamedRange` must never be
# able to leave D2 empty or full of #NAME?.
GRADE_RULES_SHEET = "Grade Rules"
GRADE_HELPER_LABEL_COL = "F"
GRADE_HELPER_VALUE_COL = "G"
GRADE_HELPER_HEADER = "Auto-generated alias pattern (do not edit)"

ENTITY_PATTERN_HELPERS: Dict[str, Dict[str, Any]] = {
    "TOP_UNIV_PATTERN": {
        "source_range": "'Master Top University'!A$2:B",
        "row": 2,
    },
    "TOP_ORG_PATTERN": {
        "source_range": "'Master Top Organizer'!A$2:B",
        "row": 3,
    },
}


def helper_cell_reference(row: int) -> str:
    """Absolute reference to one generated alias-pattern cell, e.g. 'Grade Rules'!$G$2."""
    return f"'{GRADE_RULES_SHEET}'!${GRADE_HELPER_VALUE_COL}${row}"


def _col_index(letter: str) -> int:
    """0-based column index of an A1 column letter."""
    index = 0
    for char in letter.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _alias_pattern_expression(range_ref: str) -> str:
    """
    Builds a regex alternation from every NON-EMPTY alias of a master sheet.

    Master `Aliases` cells may hold SEVERAL comma-separated aliases (`UI, BEM UI`), exactly
    as `compmon.sheets_repo.load_master_entities` splits them, so the formula must split on
    "," too. The range is joined into one string with `TEXTJOIN` (a Sheets `SPLIT` over an
    array is not reliable), then `SPLIT` -> `FLATTEN` -> `TRIM`, so `FILTER` receives a 1-D
    `LEN()` condition, which is what Google Sheets accepts. Empty cells/aliases are dropped,
    so they can never become a pattern that matches every row.

    There is no `IFERROR`: a genuine formula error stays visible. Only a truly empty master
    range is handled explicitly, with `IF(COUNTA(range)=0, ...)` returning a pattern that
    matches nothing (`[^\\s\\S]`).
    """
    joined = f'TEXTJOIN(",",TRUE,FLATTEN({range_ref}))'
    return (
        f'IF(COUNTA({range_ref})=0,"[^\\s\\S]",'
        f"LET(flat,{joined},"
        'tokens,ARRAYFORMULA(TRIM(FLATTEN(SPLIT(flat,",")))),'
        "names,FILTER(tokens,LEN(tokens)>0),"
        'padded,ARRAYFORMULA(IF(LEN(REGEXREPLACE(LOWER(names),"[[:punct:]]+"," "))>0,'
        '" "&REGEXREPLACE(REGEXREPLACE(LOWER(names),"[[:punct:]]+"," "),"\\s+"," ")&" ","")),'
        'joined,TEXTJOIN("|",TRUE,padded),'
        'IF(joined="","[^\\s\\S]",joined)))'
    )


GRADE_PATTERN_HELPER_FORMULAS: Dict[str, str] = {
    name: "=" + _alias_pattern_expression(spec["source_range"])
    for name, spec in ENTITY_PATTERN_HELPERS.items()
}


def build_grade_arrayformula(grade_rule_rows: List[List[Any]],
                             patterns: Optional[Dict[str, str]] = None) -> str:
    """
    Builds the single D2 ARRAYFORMULA from the `Grade Rules` table.

    `patterns` overrides the pattern expression per entity (used by the tests to measure how
    much the helper cells save). By default a branch references the helper cell directly:
    `'Grade Rules'!$G$2` (Top University) and `'Grade Rules'!$G$3` (Top Organizer).

    - rules are ordered by Priority (first match wins), never by hard-coded row numbers;
    - the grade value is looked up from the Grade Rules sheet by Priority, so editing the
      Grade column is honoured without touching the formula;
    - Entity/Skala/Mode criteria come from each rule row;
    - Hybrid is treated as Offline; required inputs missing -> "Unknown"; blank row -> "";
    - a value outside the recognised Skala/Mode sets -> "Unknown" (same as the oracle).
    """
    rules = sorted(
        grade_rule_rows,
        key=lambda r: int(str(r[0]).strip()) if str(r[0]).strip().isdigit() else 9999,
    )

    # Direct cell references to the helper cells (Fix 10: no named ranges).
    override = patterns or {}
    univ_pattern = override.get("TOP_UNIV_PATTERN") or helper_cell_reference(2)
    org_pattern = override.get("TOP_ORG_PATTERN") or helper_cell_reference(3)

    univ_text = _sheet_padded(f"{COL_UNIV}2:{COL_UNIV}")
    peny_text = _sheet_padded(f"{COL_PENY}2:{COL_PENY}")
    skala_text = _sheet_padded(f"{COL_SKALA}2:{COL_SKALA}")
    mode_text = _sheet_padded(f"{COL_MODE}2:{COL_MODE}")

    top_univ = f"((REGEXMATCH({univ_text},{univ_pattern})+REGEXMATCH({peny_text},{univ_pattern}))>0)"
    # Fix 10: REGEXMATCH returns a BOOLEAN and Google Sheets ranks booleans ABOVE numbers, so
    # `FALSE>0` is TRUE: `REGEXMATCH(...)>0` made EVERY row a Top Organizer. A boolean is used
    # directly as a condition (as `skala_intl` already was); only SUMS of REGEXMATCH results
    # are numbers and may safely be compared with 0.
    top_org = f"(REGEXMATCH({peny_text},{org_pattern}))"
    skala_intl = f'(REGEXMATCH({skala_text}," internasional "))'
    mode_online = f'(REGEXMATCH({mode_text}," online "))'
    mode_offline = (
        f'((REGEXMATCH({mode_text}," offline ")+REGEXMATCH({mode_text}," hybrid "))>0)'
    )
    # Parity with the Python oracle: a Skala that is neither Nasional nor Internasional, or
    # a Pelaksanaan Final that is not Online/Offline/Hybrid, must be "Unknown" in BOTH.
    skala_known = (
        f'(REGEXMATCH({skala_text}," internasional ")+REGEXMATCH({skala_text}," nasional "))'
    )
    mode_known = (
        f'(REGEXMATCH({mode_text}," online ")+REGEXMATCH({mode_text}," offline ")'
        f'+REGEXMATCH({mode_text}," hybrid "))'
    )
    unrecognized_condition = f"(((({skala_known})=0)+(({mode_known})=0))>0)"

    entity_expr = {
        "Top University": top_univ,
        "Top Organizer": top_org,
        "Any": "TRUE",
    }
    skala_expr = {
        "Internasional": skala_intl,
        "Any": "TRUE",
    }
    mode_expr = {
        "Online": mode_online,
        "Offline": mode_offline,
        "Any": "TRUE",
    }

    blank_condition = (
        f"((LEN({COL_NAMA}2:{COL_NAMA})=0)*(LEN({COL_UNIV}2:{COL_UNIV})=0)"
        f"*(LEN({COL_PENY}2:{COL_PENY})=0)*(LEN({COL_SKALA}2:{COL_SKALA})=0)"
        f"*(LEN({COL_MODE}2:{COL_MODE})=0))"
    )
    missing_condition = (
        f"(((LEN({COL_PENY}2:{COL_PENY})=0)+(LEN({COL_SKALA}2:{COL_SKALA})=0)"
        f"+(LEN({COL_MODE}2:{COL_MODE})=0))>0)"
    )

    branches = [
        f"{blank_condition}, \"\"",
        f"{missing_condition}, \"Unknown\"",
        f"{unrecognized_condition}, \"Unknown\"",
    ]

    for row in rules:
        priority = str(row[0]).strip()
        entity = str(row[2]).strip() if len(row) > 2 else "Any"
        skala = str(row[3]).strip() if len(row) > 3 else "Any"
        mode = str(row[4]).strip() if len(row) > 4 else "Any"

        entity_part = entity_expr.get(entity, "FALSE")
        skala_part = skala_expr.get(skala, "FALSE")
        mode_part = mode_expr.get(mode, "FALSE")

        condition = f"(({entity_part})*({skala_part})*({mode_part}))"
        grade_lookup = (
            f"IFERROR(INDEX(FILTER('Grade Rules'!B$2:B,"
            f"TO_TEXT('Grade Rules'!A$2:A)=TO_TEXT(\"{priority}\")),1),\"\")"
        )
        branches.append(f"{condition}, {grade_lookup}")

    # Safe fallback: a filled row that matches no rule gets an empty grade, never an error.
    branches.append('TRUE, ""')

    body = ",\n  ".join(branches)
    return "=ARRAYFORMULA(IFS(\n  " + body + "\n))"


GRADE_ARRAYFORMULA_D2 = build_grade_arrayformula(INITIAL_SHEETS_DATA["Grade Rules"]["rows"])

# Status words of the Indonesian summary printed at the end of `populate_template`.
D2_STATUS_INSTALLED = "terpasang"
D2_STATUS_REFRESHED = "disegarkan"
D2_STATUS_UNCHANGED = "tidak diubah"

# Markers every version of this generator's Grade formula contains. They let `populate_template`
# recognise a formula an EARLIER version wrote (e.g. the named-range version) and refresh it
# after a fix, while an admin's own formula is still never overwritten.
GENERATED_GRADE_FORMULA_MARKERS = (
    "=ARRAYFORMULA(IFS(",
    "'Grade Rules'!A$2:A",
    "TO_TEXT(",
)


def is_generated_grade_formula(formula: str) -> bool:
    """True when `formula` is a Grade ARRAYFORMULA written by this generator (any version)."""
    text = canonicalize_formula(formula).replace(" ", "")
    return text.startswith("=ARRAYFORMULA(IFS(") and all(
        marker.replace(" ", "") in text for marker in GENERATED_GRADE_FORMULA_MARKERS
    )


# ---------------------------------------------------------------------------
# Fix 9: locale-aware formula writes
# ---------------------------------------------------------------------------

def read_spreadsheet_properties(sh: Any) -> Dict[str, Any]:
    """
    Reads the spreadsheet `properties` (at least `locale` and `timeZone`).

    `Spreadsheet.fetch_sheet_metadata()` (gspread 6.1.4) returns the full spreadsheet
    resource, whose `properties` hold the locale. The cached `_properties` from
    `open_by_key` and the `locale` / `timezone` attributes are fallbacks, so a fake or an
    older gspread never blocks template creation. Nothing here writes the properties.
    """
    try:
        metadata = sh.fetch_sheet_metadata()
        properties = metadata.get("properties") if isinstance(metadata, dict) else None
        if isinstance(properties, dict) and properties:
            return properties
    except Exception as e:  # never block on a metadata read
        logger.warning(f"Could not read the spreadsheet metadata ({e}); using cached properties")

    cached = getattr(sh, "_properties", None)
    if isinstance(cached, dict) and cached:
        return cached

    properties: Dict[str, Any] = {}
    for key, attr in (("locale", "locale"), ("timeZone", "timezone")):
        try:
            value = getattr(sh, attr)
        except Exception:
            value = None
        if value:
            properties[key] = value
    return properties


def warn_if_timezone_is_not_jakarta(properties: Dict[str, Any]) -> str:
    """
    Reports a time zone other than Asia/Jakarta in Indonesian. Never changes it.

    The message is printed (visible in Colab even without logging configured) and logged,
    because every date the pipeline writes is computed in `Config.timezone`.
    """
    time_zone = str(properties.get("timeZone") or "").strip()
    if time_zone and time_zone != "Asia/Jakarta":
        message = (
            f"PERINGATAN: zona waktu spreadsheet ini adalah '{time_zone}', bukan 'Asia/Jakarta'. "
            "Sistem TIDAK mengubah zona waktu spreadsheet; tanggal/jam yang ditulis notebook "
            "memakai zona waktu dari sheet Config (Asia/Jakarta). Ubah Time zone secara manual "
            "di File > Settings bila ingin keduanya sama."
        )
        logger.warning(message)
        print(message)
    return time_zone


def _read_formula(ws: Any, cell: str) -> str:
    """Formula text of `cell` (FORMULA render option); text cells come back unchanged."""
    try:
        value = ws.acell(cell, value_render_option="FORMULA")
    except TypeError:  # pragma: no cover - only for a double without the keyword
        value = ws.acell(cell)
    text = getattr(value, "value", value)
    if isinstance(text, list):  # defensive: a ValueRange instead of a single cell
        text = text[0][0] if text and text[0] else ""
    return str(text or "")


def _read_display(ws: Any, cell: str) -> str:
    """Value the sheet currently shows in `cell` (FORMATTED_VALUE, which exposes errors)."""
    try:
        value = ws.acell(cell)
    except TypeError:  # pragma: no cover
        value = ws.acell(cell, value_render_option="FORMATTED_VALUE")
    text = getattr(value, "value", value)
    if isinstance(text, list):
        text = text[0][0] if text and text[0] else ""
    return str(text or "")


def _formula_values(ws: Any) -> List[List[str]]:
    """Whole worksheet read with the FORMULA render option (computed values are useless)."""
    return ws.get_all_values(value_render_option="FORMULA")


def _write_localized_formula(
    ws: Any, cell: str, canonical_formula: str, separator: str, locale: str, what: str
) -> Tuple[str, str]:
    """
    Writes ONE formula for the spreadsheet locale and verifies it.

    The formula is localized with `separator`, written with USER_ENTERED and read back. If
    the cell shows an error value (#ERROR!, #NAME?, …) the other separator is tried once.
    If the cell still shows an error, `FormulaLocaleError` is raised naming the cell, the
    value, the locale and the separators tried - never a warning that leaves a broken sheet.

    Returns (written_formula, separator_that_worked).
    """
    tried: List[str] = []
    last_value = ""
    for candidate in (separator, other_separator(separator)):
        formula = localize_formula(canonical_formula, candidate)
        ws.update(range_name=cell, values=[[formula]], value_input_option="USER_ENTERED")
        last_value = _read_display(ws, cell).strip()
        tried.append(candidate)
        if not is_formula_error(last_value):
            logger.info(f"Wrote {ws.title}!{cell} with separator {candidate!r} (locale {locale!r})")
            return formula, candidate
        logger.warning(
            f"{ws.title}!{cell} shows {last_value!r} with separator {candidate!r}; "
            f"retrying with {other_separator(candidate)!r}"
        )

    separators = ", ".join(repr(s) for s in tried)
    raise FormulaLocaleError(
        f"Gagal menulis formula ke {ws.title}!{cell} ({what}): sel menampilkan "
        f"'{last_value}' untuk separator {separators} (locale spreadsheet: "
        f"'{locale or 'tidak diketahui'}'). {FORMULA_ERROR_HINT}. Formula sumber tetap "
        f"memakai sintaks kanonik (koma). Periksa locale spreadsheet dan named range, "
        f"lalu jalankan ulang sel ini."
    )


# ---------------------------------------------------------------------------
# Sheet initialisation (Task 27)
# ---------------------------------------------------------------------------

def _protected_range_request(
    sheet_id: int, row_count: int, start_col: int, end_col: int, description: str
) -> Dict[str, Any]:
    """Builds one protected-range request covering every row of the sheet."""
    return {
        "addProtectedRange": {
            "protectedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": row_count,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                },
                "description": description,
                # Warning-only: the notebook may run as any admin account, and a strict
                # protection owned by the creator would make ID writes fail with 403.
                "warningOnly": True,
            }
        }
    }


def _covers(existing_range: Dict[str, Any], sheet_id: int, row_count: int,
            start_col: int, end_col: int) -> bool:
    """True when an existing protected range already covers the full column."""
    return (
        existing_range.get("sheetId") == sheet_id
        and existing_range.get("startRowIndex", 0) == 0
        and existing_range.get("endRowIndex", 0) >= row_count
        and existing_range.get("startColumnIndex") == start_col
        and existing_range.get("endColumnIndex") == end_col
    )


def _ensure_protected_ranges(sh, comp_ws):
    """
    Idempotently protects columns A and D over the whole sheet.

    Existing ranges created by an earlier version are replaced when they do not cover the
    full sheet; nothing is duplicated.
    """
    row_count = getattr(comp_ws, "row_count", SHEET_ROWS) or SHEET_ROWS
    existing = []
    try:
        existing = sh.list_protected_ranges(comp_ws.id) or []
    except Exception as e:  # metadata read is optional; never block template creation
        logger.warning(f"Could not list protected ranges, will not duplicate-check: {e}")

    by_description: Dict[str, Dict[str, Any]] = {}
    for item in existing:
        description = item.get("description", "")
        if description in PROTECTED_RANGE_DESCRIPTIONS.values():
            by_description[description] = item

    requests: List[Dict[str, Any]] = []
    for start_col, end_col, description in [
        (0, 1, PROTECTED_RANGE_DESCRIPTIONS["competition_id"]),
        (3, 4, PROTECTED_RANGE_DESCRIPTIONS["grade_formula"]),
    ]:
        current = by_description.get(description)
        if current and _covers(current.get("range", {}), comp_ws.id, row_count, start_col, end_col):
            logger.info(f"Protected range already present and complete: {description}")
            continue
        if current and "protectedRangeId" in current:
            logger.info(f"Replacing outdated protected range: {description}")
            requests.append({"deleteProtectedRange": {"protectedRangeId": current["protectedRangeId"]}})
        requests.append(
            _protected_range_request(comp_ws.id, row_count, start_col, end_col, description)
        )

    if requests:
        sh.batch_update({"requests": requests})


def populate_template(spreadsheet_client: Any, spreadsheet_id: str):
    """
    Sets up the 11 worksheets safely:
    - never clears existing sheets and never overwrites filled cells;
    - creates missing sheets with SHEET_ROWS rows so explicit-range writes never fail;
    - writes the alias-pattern helper cells in `Grade Rules` (never overwriting admin text);
    - installs the Grade ARRAYFORMULA in D2 when it is empty or holds a formula this
      generator produced, referencing the helper CELLS directly (Fix 10: no named ranges);
    - prints an Indonesian status summary (helper cells, D2, locale, separator), so a run
      never finishes silently with D2 empty;
    - installs the Status Pemeriksaan dropdown, the Post ID text format and idempotent,
      full-sheet protected ranges.
    """
    sh = spreadsheet_client.open_by_key(spreadsheet_id)

    # Fix 9: read the locale (never change it) and pick the separator formulas need there.
    properties = read_spreadsheet_properties(sh)
    locale = str(properties.get("locale") or "").strip()
    separator = separator_for_locale(locale)
    warn_if_timezone_is_not_jakarta(properties)
    print(
        f"Locale spreadsheet: '{locale or 'tidak diketahui'}' -> pemisah argumen "
        f"formula '{separator}'. Formula di kode tetap memakai sintaks kanonik (koma)."
    )

    existing_ws = {ws.title: ws for ws in sh.worksheets()}

    for sheet_name, content in INITIAL_SHEETS_DATA.items():
        if sheet_name not in existing_ws:
            ws = sh.add_worksheet(title=sheet_name, rows=SHEET_ROWS, cols=SHEET_COLS)
            all_data = [content["headers"]] + content["rows"]
            ws.update(range_name="A1", values=all_data, value_input_option="RAW")
            existing_ws[sheet_name] = ws
            logger.info(f"Created sheet '{sheet_name}' with {SHEET_ROWS} rows")
        else:
            ws = existing_ws[sheet_name]
            existing_vals = ws.get_all_values()
            if not existing_vals or not existing_vals[0] or not any(c.strip() for c in existing_vals[0]):
                ws.update(range_name="A1", values=[content["headers"]], value_input_option="RAW")
                logger.info(f"Wrote header row for existing sheet '{sheet_name}'")
            else:
                logger.info(f"Sheet '{sheet_name}' already initialised; left untouched")

    grade_ws = existing_ws.get("Grade Rules")
    comp_ws = existing_ws.get("Competition Report")

    helper_status: Dict[str, str] = {
        f"{GRADE_RULES_SHEET}!{GRADE_HELPER_VALUE_COL}{spec['row']}": "tidak diperiksa"
        for spec in ENTITY_PATTERN_HELPERS.values()
    }
    d2_status = f"{D2_STATUS_UNCHANGED} (Competition Report tidak ditemukan)"
    failure: Optional[FormulaLocaleError] = None

    if grade_ws:
        try:
            helper_status = _install_grade_pattern_helpers(grade_ws, separator, locale)
        except FormulaLocaleError as e:
            # Fix 9: a helper cell in error means the Grade formula cannot work, so D2 is not
            # installed. The status summary still reports the broken cells before re-raising.
            helper_status = {
                f"{GRADE_RULES_SHEET}!{GRADE_HELPER_VALUE_COL}{spec['row']}": "error"
                for spec in ENTITY_PATTERN_HELPERS.values()
            }
            d2_status = f"{D2_STATUS_UNCHANGED} (sel bantu G2:G3 error)"
            failure = e

    if comp_ws and failure is None:
        d2_status = _install_grade_formula(comp_ws, separator=separator, locale=locale)
        _install_status_validation(sh, comp_ws)
        _ensure_protected_ranges(sh, comp_ws)

    sys_ws = existing_ws.get("System Log")
    if sys_ws:
        _install_post_id_text_format(sh, sys_ws)

    _print_template_status(locale, separator, helper_status, d2_status)
    if failure is not None:
        raise failure

    print(f"Template populated safely without overwrite for Spreadsheet: {spreadsheet_id}")


def _print_template_status(locale: str, separator: str, helper_status: Dict[str, str],
                           d2_status: str) -> None:
    """Indonesian summary of what a template run did, printed and logged (visible in Colab)."""
    lines = [
        "",
        "STATUS TEMPLATE (Grade Lomba):",
        f"  locale spreadsheet : {locale or 'tidak diketahui'}",
        f"  pemisah formula    : '{separator}'",
    ]
    for cell, status in helper_status.items():
        lines.append(f"  sel bantu {cell}: {status}")
    lines.append(f"  Competition Report!D2: {d2_status}")
    message = "\n".join(lines)
    print(message)
    logger.info(message)


def _install_grade_formula(comp_ws, separator: str = ",", locale: str = "") -> str:
    """
    Writes the D1 header and, when it is safe, the D2 ARRAYFORMULA.

    Fix 9: D2 is read with the FORMULA render option, so the decision is made from the formula
    text (normalized for the locale separator), never from a computed `""`.

    Fix 10: D2 is installed when it is empty or holds a formula this generator produced (an
    older named-range version included, recognised from its text), refreshed when it differs
    from the current formula, and never overwritten when it is an admin's own formula.

    Returns the Indonesian status word used in the summary printed by `populate_template`.
    """
    values = _formula_values(comp_ws)
    d1 = _cell_at(values, 1, COL_GRADE)
    d2 = _cell_at(values, 2, COL_GRADE)

    if not d1:
        comp_ws.update(range_name="D1", values=[["Grade Lomba"]], value_input_option="RAW")
        logger.info("Wrote Grade Lomba header to D1")

    canonical = GRADE_ARRAYFORMULA_D2
    if not d2:
        _write_localized_formula(comp_ws, "D2", canonical, separator, locale,
                                 what="Grade ARRAYFORMULA")
        logger.info("Installed Grade ARRAYFORMULA in D2")
        return f"{D2_STATUS_INSTALLED} (formula sistem ditulis)"

    if not is_generated_grade_formula(d2):
        logger.warning("D2 already contains a non-generated value; left untouched")
        return f"{D2_STATUS_UNCHANGED} (berisi formula buatan admin, bukan formula sistem)"

    if d2.strip() == localize_formula(canonical, separator).strip():
        logger.info("D2 already holds the generated formula for this locale; left untouched")
        return f"{D2_STATUS_UNCHANGED} (sudah berisi formula sistem yang benar)"

    _write_localized_formula(comp_ws, "D2", canonical, separator, locale,
                             what="Grade ARRAYFORMULA")
    logger.info("Refreshed generated Grade ARRAYFORMULA in D2")
    return f"{D2_STATUS_REFRESHED} (pemisah atau versi formula diperbarui)"


def _assert_helper_cells_render(grade_ws, locale: str) -> None:
    """Raises when a helper cell the Grade formula depends on is showing an error."""
    for spec in ENTITY_PATTERN_HELPERS.values():
        cell = f"{GRADE_HELPER_VALUE_COL}{spec['row']}"
        shown = _read_display(grade_ws, cell).strip()
        if is_formula_error(shown):
            raise FormulaLocaleError(
                f"Formula D2 tidak dipasang karena sel bantu {grade_ws.title}!{cell} "
                f"menampilkan '{shown}' (locale spreadsheet: '{locale or 'tidak diketahui'}'). "
                f"{FORMULA_ERROR_HINT}. Kosongkan sel bantu tersebut (G2:G3) lalu jalankan "
                f"ulang sel ini."
            )


def _cell_at(values: List[List[str]], row: int, letter: str) -> str:
    """Sheet value at a 1-based row and A1 column letter ("" when absent)."""
    index = _col_index(letter)
    if row - 1 >= len(values):
        return ""
    line = values[row - 1]
    if index >= len(line):
        return ""
    return (line[index] or "").strip()


def _install_grade_pattern_helpers(grade_ws, separator: str = ",",
                                   locale: str = "") -> Dict[str, str]:
    """
    Puts each compiled alias pattern in ONE cell of `Grade Rules` (columns F-G).

    Returns the status of every helper cell the Grade formula depends on (`{"Grade Rules!G2":
    "ok"}`); a cell still showing a formula error raises before this returns, so the caller
    never installs D2 while a helper is broken.

    Fix 9: the cells are read with the FORMULA render option and written through
    `_write_localized_formula`, so the pattern matches the spreadsheet locale (`;` on an
    Indonesian sheet) and a wrong separator is repaired instead of silently showing #ERROR!.
    A label or pattern cell is never overwritten with foreign content.

    Fix 10: D2 references these cells by absolute address, so no named range is created and a
    rejected `addNamedRange` can no longer keep D2 empty.
    """
    values = _formula_values(grade_ws)

    if not _cell_at(values, 1, GRADE_HELPER_LABEL_COL):
        grade_ws.update(
            range_name=f"{GRADE_HELPER_LABEL_COL}1",
            values=[[GRADE_HELPER_HEADER]],
            value_input_option="RAW",
        )
        logger.info("Wrote the alias-pattern helper header to Grade Rules")

    for name, spec in ENTITY_PATTERN_HELPERS.items():
        row = spec["row"]
        label_cell = f"{GRADE_HELPER_LABEL_COL}{row}"
        value_cell = f"{GRADE_HELPER_VALUE_COL}{row}"
        canonical = GRADE_PATTERN_HELPER_FORMULAS[name]

        if not _cell_at(values, row, GRADE_HELPER_LABEL_COL):
            grade_ws.update(range_name=label_cell, values=[[name]], value_input_option="RAW")

        current = _cell_at(values, row, GRADE_HELPER_VALUE_COL).strip()
        if not current:
            _write_localized_formula(grade_ws, value_cell, canonical, separator, locale,
                                     what=f"pola alias {name}")
            logger.info(f"Wrote alias pattern {name} to Grade Rules!{value_cell}")
        elif canonicalize_formula(current).strip() == canonicalize_formula(canonical).strip():
            if current == localize_formula(canonical, separator).strip():
                logger.info(
                    f"Grade Rules!{value_cell} already holds the generated pattern; left untouched"
                )
            else:
                logger.info(
                    f"Rewriting Grade Rules!{value_cell} with separator {separator!r} "
                    f"for locale {locale!r}"
                )
                _write_localized_formula(grade_ws, value_cell, canonical, separator, locale,
                                         what=f"pola alias {name}")
        else:
            logger.warning(
                f"Grade Rules!{value_cell} already holds a value; left untouched "
                "(clear the cell to regenerate the pattern)"
            )

    _assert_helper_cells_render(grade_ws, locale)
    return {
        f"{GRADE_RULES_SHEET}!{GRADE_HELPER_VALUE_COL}{spec['row']}": "ok"
        for spec in ENTITY_PATTERN_HELPERS.values()
    }


def _install_status_validation(sh, comp_ws):
    """Dropdown validation for Status Pemeriksaan over every row of the sheet."""
    row_count = getattr(comp_ws, "row_count", SHEET_ROWS) or SHEET_ROWS
    try:
        sh.batch_update({
            "requests": [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": comp_ws.id,
                            "startRowIndex": 1,
                            "endRowIndex": row_count,
                            "startColumnIndex": 14,
                            "endColumnIndex": 15,
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [
                                    {"userEnteredValue": "Valid"},
                                    {"userEnteredValue": "Tidak Valid"},
                                    {"userEnteredValue": "Belum Diperiksa"},
                                ],
                            },
                            "inputMessage": "Pilih status pemeriksaan admin",
                            "strict": True,
                            "showCustomUi": True,
                        },
                    }
                }
            ]
        })
    except Exception as e:
        logger.warning(f"Could not install Status Pemeriksaan dropdown: {e}")


def _install_post_id_text_format(sh, sys_ws):
    """Formats System Log column A as plain text so 19-digit Post IDs never become numbers."""
    row_count = getattr(sys_ws, "row_count", SHEET_ROWS) or SHEET_ROWS
    try:
        sh.batch_update({
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sys_ws.id,
                            "startRowIndex": 1,
                            "endRowIndex": row_count,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "TEXT"}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            ]
        })
    except Exception as e:
        logger.warning(f"Could not set System Log Post ID text format: {e}")
