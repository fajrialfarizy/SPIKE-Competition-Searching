"""
templates/test_grade_parity.py
Parity check between the Grade Lomba ARRAYFORMULA in a TEST spreadsheet and the Python
oracle (`compmon.grading_support.calculate_grade`).

Status: `Not tested (needs real services)` unless run manually against a separate TEST
spreadsheet with `COMPMON_TEST_SPREADSHEET_ID` set (never the production spreadsheet).
The local part (oracle vs. the PRD rule table) runs offline:

    python -m unittest templates.test_grade_parity -v
"""

import os
import time
import unittest
from typing import Dict, List, Optional, Tuple

from compmon.grading_support import calculate_grade

# The master sheets may hold several comma-separated aliases per row (`UI, BEM UI`), an
# alias with punctuation, and an empty alias cell. The sheet formula must split on "," and
# ignore empty aliases exactly like the oracle.
TOP_UNIVERSITIES = [
    ("Universitas Indonesia", ["UI", "BEM UI", ""]),
    ("Universitas Gadjah Mada", ["UGM"]),
    ("Universitas Airlangga", ["UNAIR"]),
    ("Institut Teknologi Bandung", ["ITB"]),
    ("Universitas Padjadjaran", ["UNPAD", "Unpad (Jatinangor)"]),
]
TOP_ORGANIZERS = [
    ("Unilever", []), ("Gojek", []), ("Huawei", []), ("Pertamina", []), ("Tokopedia", []),
]

ENTITIES = {
    "Top University": ("BEM Universitas Indonesia", "Universitas Indonesia"),
    "Top University via second alias": ("BEM UI", "Fakultas Ekonomi"),
    "Top University via punctuation alias": ("Panitia Unpad (Jatinangor)", ""),
    "Top Organizer": ("PT Pertamina", ""),
    "none": ("Himpunan Kampus Daerah", "Universitas Lokal"),
}
SKALAS = ["Nasional", "Internasional", "Regional"]
MODES = ["Online", "Offline", "Hybrid", "TBD"]
MISSING = [None, "penyelenggara", "skala", "mode"]


def expected_grade(entity: str, skala: str, mode: str, missing: Optional[str]) -> str:
    """
    The PRD / Grade Rules table written out independently of `calculate_grade`
    (so the parity test is not comparing the oracle with itself).
    """
    if missing:
        return "Unknown"
    # The sheet formula now agrees with the oracle: values outside the PRD sets are Unknown.
    if skala not in ("Nasional", "Internasional"):
        return "Unknown"
    if mode not in ("Online", "Offline", "Hybrid"):
        return "Unknown"
    is_intl = skala == "Internasional"
    effective_mode = "Offline" if mode in ("Offline", "Hybrid") else "Online"

    is_top_university = entity in (
        "Top University",
        # The same university reached through its 2nd alias and through an alias with
        # punctuation: the sheet formula must split the alias cell and match phrases.
        "Top University via second alias",
        "Top University via punctuation alias",
    )
    is_top_organizer = entity == "Top Organizer"

    if is_top_university and (is_intl or effective_mode == "Offline"):
        return "Advance"
    if is_top_organizer and (effective_mode == "Offline" or is_intl):
        return "Advance"
    if (is_top_university or is_top_organizer) and effective_mode == "Online":
        return "Professional"
    if is_intl:
        return "Intermediate"
    return "Beginner"


def build_matrix() -> List[Dict[str, str]]:
    """All combinations: entity x skala x mode x missing-input."""
    matrix = []
    for entity, (peny, univ) in ENTITIES.items():
        for skala in SKALAS:
            for mode in MODES:
                for missing in MISSING:
                    row = {
                        "entity": entity,
                        "penyelenggara": peny,
                        "universitas": univ,
                        "skala": skala,
                        "pelaksanaan_final": mode,
                        "missing": missing or "",
                    }
                    if missing == "penyelenggara":
                        row["penyelenggara"] = ""
                    elif missing == "skala":
                        row["skala"] = ""
                    elif missing == "mode":
                        row["pelaksanaan_final"] = ""
                    row["expected"] = expected_grade(entity, skala, mode, missing)
                    matrix.append(row)
    return matrix


def _extra_row(entity: str, penyelenggara: str, universitas: str, skala: str, mode: str,
               expected: str) -> Dict[str, str]:
    return {
        "entity": entity,
        "penyelenggara": penyelenggara,
        "universitas": universitas,
        "skala": skala,
        "pelaksanaan_final": mode,
        "missing": "",
        "expected": expected,
    }


# Sample rows for the multi-alias / punctuation-alias / empty-alias / unrecognised-value rules.
MULTI_ALIAS_ROWS = [
    _extra_row("alias_bem_ui", "BEM UI", "Fakultas Ekonomi", "Nasional", "Online",
               "Professional"),
    _extra_row("alias_punctuation", "Panitia Unpad (Jatinangor)", "", "Internasional",
               "Online", "Advance"),
    _extra_row("empty_alias_never_matches", "Himpunan Kampus Daerah", "Universitas Lokal",
               "Nasional", "Online", "Beginner"),
    _extra_row("unknown_skala", "BEM UI", "", "Regional", "Online", "Unknown"),
    _extra_row("unknown_mode", "BEM UI", "", "Nasional", "TBD", "Unknown"),
]

# Fix 10: the owner's real sheet graded BINUS/BINUS as Professional because the sheet
# formula compared a BOOLEAN REGEXMATCH result with 0 (`FALSE>0` is TRUE in Google Sheets),
# so every row counted as Top Organizer. These rows use names that are in NEITHER master
# list, so no entity rule may match: Nasional must stay Beginner and Internasional
# Intermediate, whatever the mode.
UNRELATED_NAMES = [
    _extra_row(
        f"unrelated_{skala.lower()}_{mode.lower()}", "BINUS", "BINUS", skala, mode,
        "Intermediate" if skala == "Internasional" else "Beginner",
    )
    for skala in ("Nasional", "Internasional")
    for mode in ("Online", "Offline", "Hybrid")
]

TEST_ROWS = build_matrix() + MULTI_ALIAS_ROWS + UNRELATED_NAMES


class TestOracleMatchesPrdTable(unittest.TestCase):
    """Offline: the Python oracle must reproduce the PRD table for every combination."""

    def test_oracle_matches_expected_for_all_combinations(self):
        for row in TEST_ROWS:
            with self.subTest(entity=row["entity"], skala=row["skala"],
                              mode=row["pelaksanaan_final"], missing=row["missing"]):
                self.assertEqual(
                    calculate_grade(
                        penyelenggara=row["penyelenggara"],
                        skala=row["skala"],
                        pelaksanaan_final=row["pelaksanaan_final"],
                        universitas=row["universitas"],
                        top_universities=TOP_UNIVERSITIES,
                        top_organizers=TOP_ORGANIZERS,
                    ),
                    row["expected"],
                )

    def test_matrix_covers_every_combination(self):
        self.assertEqual(
            len(TEST_ROWS),
            len(ENTITIES) * len(SKALAS) * len(MODES) * len(MISSING)
            + len(MULTI_ALIAS_ROWS) + len(UNRELATED_NAMES),
        )

    def test_unrelated_names_are_never_top_organizer_or_top_university(self):
        """Fix 10 regression: BINUS/BINUS is not in any master list (owner-reported bug)."""
        self.assertEqual(len(UNRELATED_NAMES), 6)
        expected_by_skala = {"Nasional": "Beginner", "Internasional": "Intermediate"}
        for row in UNRELATED_NAMES:
            with self.subTest(skala=row["skala"], mode=row["pelaksanaan_final"]):
                self.assertEqual(row["expected"], expected_by_skala[row["skala"]])
                grade = calculate_grade(
                    penyelenggara=row["penyelenggara"],
                    skala=row["skala"],
                    pelaksanaan_final=row["pelaksanaan_final"],
                    universitas=row["universitas"],
                    top_universities=TOP_UNIVERSITIES,
                    top_organizers=TOP_ORGANIZERS,
                )
                self.assertEqual(grade, row["expected"])
                self.assertNotIn(grade, ("Professional", "Advance"))

    def test_multiple_aliases_punctuation_and_empty_alias(self):
        """The oracle splits aliases the same way the master sheets and the formula do."""
        for row in MULTI_ALIAS_ROWS:
            with self.subTest(entity=row["entity"]):
                self.assertEqual(
                    calculate_grade(
                        penyelenggara=row["penyelenggara"],
                        skala=row["skala"],
                        pelaksanaan_final=row["pelaksanaan_final"],
                        universitas=row["universitas"],
                        top_universities=TOP_UNIVERSITIES,
                        top_organizers=TOP_ORGANIZERS,
                    ),
                    row["expected"],
                )


def write_rows_to_test_spreadsheet(gspread_client, test_spreadsheet_id: str) -> int:
    """Writes the matrix into a TEST spreadsheet (rows 2..N) and returns N."""
    sh = gspread_client.open_by_key(test_spreadsheet_id)
    ws = sh.worksheet("Competition Report")

    rows_ac, rows_eo = [], []
    for i, row in enumerate(TEST_ROWS, start=2):
        rows_ac.append([f"PARITY-{i:03d}", f"Parity {row['entity']} {row['skala']} "
                                          f"{row['pelaksanaan_final']} {row['missing']}".strip(),
                        "Business Plan"])
        rows_eo.append([
            row["universitas"], row["penyelenggara"], row["skala"], row["pelaksanaan_final"],
            "Jakarta", "2026-11-01", "2026-12-01", "http://example.com", "", "",
            "Belum Diperiksa",
        ])

    end_row = 1 + len(TEST_ROWS)
    ws.batch_update(
        [
            {"range": f"A2:C{end_row}", "values": rows_ac},
            {"range": f"E2:O{end_row}", "values": rows_eo},
        ],
        value_input_option="RAW",
    )
    return end_row


def run_grade_parity(gspread_client, test_spreadsheet_id: str,
                     settle_seconds: int = 5) -> Tuple[bool, List[Tuple[str, str, str]]]:
    """
    Live parity check: writes sample rows to the TEST spreadsheet, waits for the
    ARRAYFORMULA, then compares column D with the Python oracle.

    Returns (ok, discrepancies) where each discrepancy is (label, expected, actual_from_sheet).
    """
    end_row = write_rows_to_test_spreadsheet(gspread_client, test_spreadsheet_id)
    time.sleep(settle_seconds)

    ws = gspread_client.open_by_key(test_spreadsheet_id).worksheet("Competition Report")
    sheet_grades = ws.col_values(4)[1:end_row]

    discrepancies: List[Tuple[str, str, str]] = []
    for row, actual in zip(TEST_ROWS, sheet_grades):
        if actual.strip() != row["expected"]:
            label = f"{row['entity']} | {row['skala']} | {row['pelaksanaan_final']} | missing={row['missing']}"
            discrepancies.append((label, row["expected"], actual.strip()))

    return (not discrepancies), discrepancies


class TestLiveSheetParity(unittest.TestCase):
    """
    Runs only when a TEST spreadsheet is provided:
        COMPMON_TEST_SPREADSHEET_ID=<id> python -m unittest templates.test_grade_parity
    Otherwise reported as `Not tested (needs real services)`.
    """

    @unittest.skipUnless(
        os.environ.get("COMPMON_TEST_SPREADSHEET_ID"),
        "Not tested (needs real services): set COMPMON_TEST_SPREADSHEET_ID to a TEST sheet",
    )
    def test_sheet_formula_matches_oracle(self):
        import google.auth
        import gspread

        credentials, _ = google.auth.default()
        client = gspread.authorize(credentials)
        ok, discrepancies = run_grade_parity(
            client, os.environ["COMPMON_TEST_SPREADSHEET_ID"]
        )
        self.assertTrue(ok, f"{len(discrepancies)} discrepancies: {discrepancies[:10]}")


if __name__ == "__main__":
    unittest.main()
