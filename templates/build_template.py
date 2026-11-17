"""
templates/build_template.py
Creates and initializes the Google Sheets template safely (PRD Section 4 & 5).

Rules:
- NEVER clears existing sheets or overwrites admin data. Only missing sheets are created;
  for existing sheets only a missing header row is filled in.
- Competition Report column D holds one ARRAYFORMULA built from the `Grade Rules` table;
  it is only (re)written when the cell is empty or still holds an older generated formula.
- Sheets are created with enough rows for explicit-range writes (Task 27).
- Protected ranges are idempotent, cover the whole sheet and are warning-only so that any
  admin running the notebook can still write a Competition ID in column A (Task 27).
"""

import logging
from typing import Any, Dict, List

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
            ["gemini_delay_seconds", "6", "Jeda detik antar request sequential ke Gemini API"],
            ["gemini_max_retries", "3", "Maksimal percobaan ulang request Gemini API saat terjadi error/429"],
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


def _entity_pattern(range_ref: str) -> str:
    """
    Builds a regex alternation from NON-EMPTY master cells only.

    `FILTER(range, LEN(range)>0)` drops empty alias cells, so an empty alias can no longer
    become a bare word-boundary that matches every row (the original bug). Every name is
    token-normalized and padded, so entries are matched as whole phrases; if the master
    sheet is empty the pattern matches nothing (`[^\\s\\S]`).
    """
    filtered = f"FILTER({range_ref},LEN({range_ref})>0)"
    return (
        f'IFERROR(TEXTJOIN("|",TRUE,{_sheet_padded(filtered)}),"[^\\s\\S]")'
    )


def build_grade_arrayformula(grade_rule_rows: List[List[Any]]) -> str:
    """
    Builds the single D2 ARRAYFORMULA from the `Grade Rules` table.

    - rules are ordered by Priority (first match wins), never by hard-coded row numbers;
    - the grade value is looked up from the Grade Rules sheet by Priority, so editing the
      Grade column is honoured without touching the formula;
    - Entity/Skala/Mode criteria come from each rule row;
    - Hybrid is treated as Offline; required inputs missing -> "Unknown"; blank row -> "".
    """
    rules = sorted(
        grade_rule_rows,
        key=lambda r: int(str(r[0]).strip()) if str(r[0]).strip().isdigit() else 9999,
    )

    univ_pattern = _entity_pattern("'Master Top University'!A$2:B")
    org_pattern = _entity_pattern("'Master Top Organizer'!A$2:B")

    univ_text = _sheet_padded(f"{COL_UNIV}2:{COL_UNIV}")
    peny_text = _sheet_padded(f"{COL_PENY}2:{COL_PENY}")
    skala_text = _sheet_padded(f"{COL_SKALA}2:{COL_SKALA}")
    mode_text = _sheet_padded(f"{COL_MODE}2:{COL_MODE}")

    top_univ = f"((REGEXMATCH({univ_text},{univ_pattern})+REGEXMATCH({peny_text},{univ_pattern}))>0)"
    top_org = f"(REGEXMATCH({peny_text},{org_pattern})>0)"
    skala_intl = f'(REGEXMATCH({skala_text}," internasional "))'
    mode_online = f'(REGEXMATCH({mode_text}," online "))'
    mode_offline = (
        f'((REGEXMATCH({mode_text}," offline ")+REGEXMATCH({mode_text}," hybrid "))>0)'
    )

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
    - installs the Grade ARRAYFORMULA in D2 only when D2 is empty or holds an older
      generated formula;
    - installs the Status Pemeriksaan dropdown, the Post ID text format and idempotent,
      full-sheet protected ranges.
    """
    sh = spreadsheet_client.open_by_key(spreadsheet_id)
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

    comp_ws = existing_ws.get("Competition Report")
    if comp_ws:
        _install_grade_formula(comp_ws)
        _install_status_validation(sh, comp_ws)
        _ensure_protected_ranges(sh, comp_ws)

    sys_ws = existing_ws.get("System Log")
    if sys_ws:
        _install_post_id_text_format(sh, sys_ws)

    print(f"Template populated safely without overwrite for Spreadsheet: {spreadsheet_id}")


def _install_grade_formula(comp_ws):
    """Writes D1 header / D2 ARRAYFORMULA only where it is safe to do so."""
    values = comp_ws.get_all_values()
    d1 = values[0][3].strip() if values and len(values[0]) > 3 else ""
    d2 = values[1][3].strip() if len(values) > 1 and len(values[1]) > 3 else ""

    if not d1:
        comp_ws.update(range_name="D1", values=[["Grade Lomba"]], value_input_option="RAW")
        logger.info("Wrote Grade Lomba header to D1")

    if not d2:
        comp_ws.update(range_name="D2", values=[[GRADE_ARRAYFORMULA_D2]], value_input_option="USER_ENTERED")
        logger.info("Installed Grade ARRAYFORMULA in D2")
    elif d2.startswith("=ARRAYFORMULA(IFS("):
        comp_ws.update(range_name="D2", values=[[GRADE_ARRAYFORMULA_D2]], value_input_option="USER_ENTERED")
        logger.info("Refreshed generated Grade ARRAYFORMULA in D2")
    else:
        logger.warning("D2 already contains a non-generated value; left untouched")


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
