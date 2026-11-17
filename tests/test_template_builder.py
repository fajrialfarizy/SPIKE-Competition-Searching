"""
tests/test_template_builder.py
Checks the generated template: 11 sheets, safe initialisation, the generic Grade
ARRAYFORMULA and idempotent / full-sheet protected ranges.
"""

import re
import unittest

from templates.build_template import (
    INITIAL_SHEETS_DATA, GRADE_ARRAYFORMULA_D2, SHEET_ROWS, PROTECTED_RANGE_DESCRIPTIONS,
    build_grade_arrayformula, populate_template,
)
from tests.fakes import FakeClient


class TestTemplateData(unittest.TestCase):

    def test_all_11_sheets_present(self):
        expected = [
            "Competition Report", "System Log", "Execution Log", "Config", "Run State",
            "Source Accounts", "Master Top University", "Master Top Organizer",
            "Grade Rules", "Eligibility Rules", "Search Keywords",
        ]
        self.assertEqual(len(INITIAL_SHEETS_DATA), 11)
        for name in expected:
            self.assertIn(name, INITIAL_SHEETS_DATA)

    def test_competition_report_headers(self):
        headers = INITIAL_SHEETS_DATA["Competition Report"]["headers"]
        self.assertEqual(len(headers), 15)
        self.assertEqual(headers[0], "Competition ID")
        self.assertEqual(headers[3], "Grade Lomba")
        self.assertEqual(headers[14], "Status Pemeriksaan")

    def test_system_log_headers(self):
        headers = INITIAL_SHEETS_DATA["System Log"]["headers"]
        self.assertEqual(len(headers), 13)
        self.assertEqual(headers[0], "Post ID")
        self.assertEqual(headers[8], "Processing Status")

    def test_config_contains_pagination_ceiling(self):
        config_rows = {row[0]: row[1] for row in INITIAL_SHEETS_DATA["Config"]["rows"]}
        self.assertEqual(config_rows["max_results_ceiling_per_source"], "200")
        self.assertEqual(config_rows["max_results_per_input"], "20")
        # The model ID must not be changed by this work.
        self.assertEqual(config_rows["gemini_model"], "gemini-3.5-flash-lite")


class TestGradeFormula(unittest.TestCase):

    def test_formula_shape(self):
        formula = GRADE_ARRAYFORMULA_D2
        self.assertTrue(formula.startswith("=ARRAYFORMULA(IFS("))
        self.assertIn("'Master Top University'!", formula)
        self.assertIn("'Master Top Organizer'!", formula)
        self.assertIn("'Grade Rules'!", formula)

    def test_empty_aliases_are_filtered_out(self):
        """The old bug joined empty cells into `\\b\\b`, which matched every row."""
        formula = GRADE_ARRAYFORMULA_D2
        self.assertNotIn("\\b", formula.replace("\\s", ""))
        self.assertIn(
            "FILTER('Master Top University'!A$2:B,LEN('Master Top University'!A$2:B)>0)", formula
        )
        self.assertIn(
            "FILTER('Master Top Organizer'!A$2:B,LEN('Master Top Organizer'!A$2:B)>0)", formula
        )
        # An empty master list must match nothing.
        self.assertIn("[^\\s\\S]", formula)

    def test_no_fixed_rule_rows(self):
        """Grades must be looked up by Priority, never from B$4-style fixed rows."""
        references = re.findall(r"'Grade Rules'!B\$?(\d+)", GRADE_ARRAYFORMULA_D2)
        self.assertTrue(references)
        self.assertTrue(all(int(ref) <= 2 for ref in references), references)
        self.assertNotIn("'Grade Rules'!B$4", GRADE_ARRAYFORMULA_D2)
        self.assertIn("TO_TEXT('Grade Rules'!A$2:A)", GRADE_ARRAYFORMULA_D2)

    def test_required_inputs_and_blank_row_branches(self):
        formula = GRADE_ARRAYFORMULA_D2
        self.assertIn('"Unknown"', formula)
        self.assertIn('""', formula)
        # blank row test looks at the competition columns only, never at column D
        self.assertIn("LEN(B2:B)=0", formula)
        self.assertNotIn("LOWER(D2:D)", formula)
        self.assertNotIn("(D2:D", formula)

    def test_no_or_in_array_context(self):
        # `IFERROR(` contains the substring `OR(`; only a standalone OR() is forbidden.
        self.assertIsNone(re.search(r"(?<![A-Z])OR\(", GRADE_ARRAYFORMULA_D2))

    def test_hybrid_counts_as_offline(self):
        self.assertIn('" hybrid "', GRADE_ARRAYFORMULA_D2)
        self.assertIn('" offline "', GRADE_ARRAYFORMULA_D2)

    def test_top_university_matches_universitas_and_penyelenggara(self):
        formula = GRADE_ARRAYFORMULA_D2
        # E = Universitas, F = Penyelenggara, both matched against the university pattern
        self.assertIn("LOWER(E2:E)", formula)
        self.assertIn("LOWER(F2:F)", formula)

    def test_rules_are_ordered_by_priority_from_the_table(self):
        rules = [
            ["9", "Last", "Any", "Any", "Any"],
            ["1", "First", "Top University", "Internasional", "Any"],
        ]
        formula = build_grade_arrayformula(rules)
        self.assertLess(
            formula.index('TO_TEXT("1")'),
            formula.index('TO_TEXT("9")'),
        )


class TestPopulateTemplate(unittest.TestCase):

    def test_creates_all_sheets_with_2000_rows(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")

        ws = client.spreadsheet.worksheets()
        self.assertEqual(len(ws), 11)
        for sheet in ws:
            self.assertGreaterEqual(sheet.row_count, SHEET_ROWS)

    def test_existing_data_is_never_cleared(self):
        comp_rows = [
            INITIAL_SHEETS_DATA["Competition Report"]["headers"],
            ["CMP-000007", "Admin Row", "Essay", "Advance", "UI", "BEM UI", "Nasional",
             "Offline", "Depok", "2026-01-01", "2026-12-01", "", "", "", "Valid"],
        ]
        client = FakeClient({"Competition Report": comp_rows})
        populate_template(client, "sheet_id")

        ws = client.spreadsheet.worksheet("Competition Report")
        self.assertEqual(ws.data[1][0], "CMP-000007")
        self.assertEqual(ws.data[1][3], "Advance")

    def test_custom_d2_formula_is_not_overwritten(self):
        comp_rows = [
            INITIAL_SHEETS_DATA["Competition Report"]["headers"],
            ["CMP-000001", "Row", "Essay", "=MY_OWN_FORMULA()", "", "", "", "", "", "", "", "", "", "", ""],
        ]
        client = FakeClient({"Competition Report": comp_rows})
        populate_template(client, "sheet_id")
        self.assertEqual(client.spreadsheet.worksheet("Competition Report").data[1][3],
                         "=MY_OWN_FORMULA()")

    def test_protected_ranges_are_full_sheet_and_not_duplicated(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")

        first = client.spreadsheet.list_protected_ranges(comp_ws.id)
        self.assertEqual(len(first), 2)
        descriptions = sorted(p["description"] for p in first)
        self.assertEqual(descriptions, sorted(PROTECTED_RANGE_DESCRIPTIONS.values()))
        for item in first:
            self.assertGreaterEqual(item["range"]["endRowIndex"], SHEET_ROWS)
            self.assertTrue(item["warningOnly"])

        populate_template(client, "sheet_id")
        second = client.spreadsheet.list_protected_ranges(comp_ws.id)
        self.assertEqual(len(second), 2, "second run must not add duplicate protected ranges")

    def test_outdated_protected_range_is_replaced(self):
        client = FakeClient(empty=True)
        # Simulate the previous version: only rows 1-1000, strict protection.
        client.spreadsheet.protected[1] = [{
            "protectedRangeId": 55,
            "description": PROTECTED_RANGE_DESCRIPTIONS["competition_id"],
            "warningOnly": False,
            "range": {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1000,
                      "startColumnIndex": 0, "endColumnIndex": 1},
        }]
        populate_template(client, "sheet_id")

        comp_ws = client.spreadsheet.worksheet("Competition Report")
        ranges = [p for p in client.spreadsheet.list_protected_ranges(comp_ws.id)
                  if p["description"] == PROTECTED_RANGE_DESCRIPTIONS["competition_id"]]
        self.assertEqual(len(ranges), 1)
        self.assertGreaterEqual(ranges[0]["range"]["endRowIndex"], SHEET_ROWS)
        self.assertTrue(ranges[0]["warningOnly"])


if __name__ == "__main__":
    unittest.main()
