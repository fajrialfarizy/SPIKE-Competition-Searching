"""
tests/test_template_builder.py
Checks the generated template: 11 sheets, safe initialisation, the generic Grade
ARRAYFORMULA and idempotent / full-sheet protected ranges.
"""

import re
import unittest

from templates.build_template import (
    INITIAL_SHEETS_DATA, GRADE_ARRAYFORMULA_D2, SHEET_ROWS, PROTECTED_RANGE_DESCRIPTIONS,
    GRADE_PATTERN_HELPER_FORMULAS, build_grade_arrayformula, populate_template,
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

    def test_config_contains_the_new_round_2_keys_with_descriptions(self):
        rows = {row[0]: row for row in INITIAL_SHEETS_DATA["Config"]["rows"]}
        self.assertEqual(rows["max_failed_run_attempts"][1], "3")
        self.assertEqual(rows["account_start_limit_initial"][1], "20")
        self.assertEqual(rows["account_start_limit_routine"][1], "20")
        for key in ("max_failed_run_attempts", "account_start_limit_initial",
                    "account_start_limit_routine"):
            self.assertTrue(rows[key][2].strip(), f"{key} needs a Description")


class TestGradeFormula(unittest.TestCase):

    def test_formula_shape(self):
        formula = GRADE_ARRAYFORMULA_D2
        self.assertTrue(formula.startswith("=ARRAYFORMULA(IFS("))
        # The alias patterns are named ranges, not inlined expressions.
        self.assertIn("TOP_UNIV_PATTERN", formula)
        self.assertIn("TOP_ORG_PATTERN", formula)
        self.assertIn("'Grade Rules'!", formula)
        self.assertNotIn("'Master Top University'!", formula)
        self.assertNotIn("'Master Top Organizer'!", formula)

    def test_d2_references_the_named_ranges_instead_of_inlining_the_pattern(self):
        """One generated cell per pattern keeps D2 much smaller than the inlined version."""
        formula = GRADE_ARRAYFORMULA_D2
        # FILTER( stays in D2 only for the grade lookup (`FILTER('Grade Rules'!B$2:B, ...)`).
        for inline_only in ("TEXTJOIN", "FLATTEN", "SPLIT", "COUNTA(", "FILTER(tokens"):
            self.assertNotIn(inline_only, formula, f"{inline_only} belongs in the helper cell")

        rules = INITIAL_SHEETS_DATA["Grade Rules"]["rows"]
        inlined = build_grade_arrayformula(rules, patterns=GRADE_PATTERN_HELPER_FORMULAS)
        self.assertIn("TEXTJOIN", inlined)
        self.assertLess(len(formula), len(inlined) * 0.7,
                        f"D2 is {len(formula)} chars, inlined {len(inlined)}")

        # Each named range really holds the compiled pattern: three rule branches use the
        # university pattern (twice per branch: Universitas + Penyelenggara), three use the
        # organizer pattern (once each).
        self.assertEqual(formula.count("TOP_UNIV_PATTERN"), 6)
        self.assertEqual(formula.count("TOP_ORG_PATTERN"), 3)
        for name, pattern_formula in GRADE_PATTERN_HELPER_FORMULAS.items():
            self.assertTrue(pattern_formula.startswith("="), name)
            self.assertGreater(len(pattern_formula), 400, name)

    def test_empty_aliases_are_filtered_out(self):
        """The old bug joined empty cells into `\\b\\b`, which matched every row."""
        formula = GRADE_PATTERN_HELPER_FORMULAS["TOP_UNIV_PATTERN"]
        self.assertNotIn("\\b", formula.replace("\\s", ""))
        # Empty cells and empty aliases are dropped from the flattened alias list.
        self.assertIn("names,FILTER(tokens,LEN(tokens)>0)", formula)
        self.assertIn("IF(joined=\"\",\"[^\\s\\S]\",joined)", formula)

    def test_alias_cells_are_comma_split_before_filtering(self):
        """`UI, BEM UI` must behave like the Python side (`load_master_entities`)."""
        univ = GRADE_PATTERN_HELPER_FORMULAS["TOP_UNIV_PATTERN"]
        org = GRADE_PATTERN_HELPER_FORMULAS["TOP_ORG_PATTERN"]
        # One string via TEXTJOIN, then SPLIT on ",": SPLIT over an array is unreliable.
        self.assertIn("TEXTJOIN(\",\",TRUE,FLATTEN('Master Top University'!A$2:B))", univ)
        self.assertIn("TEXTJOIN(\",\",TRUE,FLATTEN('Master Top Organizer'!A$2:B))", org)
        self.assertIn("FLATTEN(SPLIT(flat,\",\"))", univ)
        # FILTER gets a 1-D condition; a 2-column LEN() condition is invalid in Sheets.
        self.assertNotIn("LEN('Master Top University'!A$2:B)", univ)
        self.assertNotIn("LEN('Master Top Organizer'!A$2:B)", org)

    def test_empty_master_range_is_handled_explicitly_without_iferror(self):
        """Only a genuinely empty master list may produce the never-matching pattern."""
        univ = GRADE_PATTERN_HELPER_FORMULAS["TOP_UNIV_PATTERN"]
        org = GRADE_PATTERN_HELPER_FORMULAS["TOP_ORG_PATTERN"]
        self.assertIn("IF(COUNTA('Master Top University'!A$2:B)=0,\"[^\\s\\S]\"", univ)
        self.assertIn("IF(COUNTA('Master Top Organizer'!A$2:B)=0,\"[^\\s\\S]\"", org)
        # No silent IFERROR around the alias pattern: formula errors must stay visible.
        for formula in (univ, org, GRADE_ARRAYFORMULA_D2):
            self.assertNotIn("IFERROR(TEXTJOIN", formula)
            self.assertNotIn("IFERROR(LET", formula)
            self.assertIsNone(re.search(r"IFERROR\(\s*(LET|ARRAYFORMULA|FILTER|SPLIT|TEXTJOIN)",
                                        formula), formula)

    def test_unrecognized_skala_and_mode_become_unknown(self):
        formula = GRADE_ARRAYFORMULA_D2
        self.assertIn('" nasional "', formula)
        self.assertEqual(formula.count('"Unknown"'), 2)
        # Both the missing-input and the unrecognized-value branch come before the rules.
        self.assertLess(formula.rindex('"Unknown"'), formula.index('TO_TEXT("1")'))

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

    def test_grade_pattern_helpers_and_named_ranges_are_installed(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")

        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        self.assertEqual(grade_ws.data[0][5], "Auto-generated alias pattern (do not edit)")
        self.assertEqual(grade_ws.data[1][5], "TOP_UNIV_PATTERN")
        self.assertEqual(grade_ws.data[1][6], GRADE_PATTERN_HELPER_FORMULAS["TOP_UNIV_PATTERN"])
        self.assertEqual(grade_ws.data[2][5], "TOP_ORG_PATTERN")
        self.assertEqual(grade_ws.data[2][6], GRADE_PATTERN_HELPER_FORMULAS["TOP_ORG_PATTERN"])

        names = sorted(client.spreadsheet.named_ranges)
        self.assertEqual(names, ["TOP_ORG_PATTERN", "TOP_UNIV_PATTERN"])
        univ = client.spreadsheet.named_ranges["TOP_UNIV_PATTERN"]["range"]
        self.assertEqual(univ["sheetId"], grade_ws.id)
        self.assertEqual((univ["startRowIndex"], univ["endRowIndex"]), (1, 2))
        self.assertEqual((univ["startColumnIndex"], univ["endColumnIndex"]), (6, 7))
        org = client.spreadsheet.named_ranges["TOP_ORG_PATTERN"]["range"]
        self.assertEqual((org["startRowIndex"], org["endRowIndex"]), (2, 3))

    def test_grade_pattern_helpers_are_idempotent_and_never_overwrite_admin_text(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")
        populate_template(client, "sheet_id")

        self.assertEqual(len(client.spreadsheet.named_ranges), 2, "re-running must not duplicate them")
        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        self.assertEqual(grade_ws.data[1][5], "TOP_UNIV_PATTERN")

        # An admin value in the helper cell is preserved.
        grade_ws.data[1][6] = "=MY_OWN_PATTERN()"
        populate_template(client, "sheet_id")
        self.assertEqual(grade_ws.data[1][6], "=MY_OWN_PATTERN()")

    def test_d2_is_not_written_when_the_named_ranges_cannot_be_created(self):
        """addNamedRange rejected -> keep D2 empty, warn once, never fill the column with #NAME?."""
        client = FakeClient(empty=True)
        client.spreadsheet.named_range_error = (
            "403 PERMISSION_DENIED: The caller does not have permission"
        )

        with self.assertLogs("templates.build_template", level="WARNING") as logs:
            populate_template(client, "sheet_id")

        comp_ws = client.spreadsheet.worksheet("Competition Report")
        values = comp_ws.get_all_values()
        self.assertEqual(values[0][3], "Grade Lomba", "the header may still be written")
        d2 = values[1][3] if len(values) > 1 and len(values[1]) > 3 else ""
        self.assertEqual(d2, "", "D2 must stay empty when its named ranges are missing")
        self.assertNotIn("TOP_UNIV_PATTERN", str(values))

        # Exactly one warning about D2, and it is actionable.
        d2_warnings = [r.getMessage() for r in logs.records
                       if r.levelname == "WARNING" and "D2 was left untouched" in r.getMessage()]
        self.assertEqual(len(d2_warnings), 1, [r.getMessage() for r in logs.records])
        for expected in ("TOP_UNIV_PATTERN", "TOP_ORG_PATTERN", "#NAME?", "re-run this cell"):
            self.assertIn(expected, d2_warnings[0])

    def test_d2_is_installed_on_the_next_run_once_the_named_ranges_exist(self):
        client = FakeClient(empty=True)
        client.spreadsheet.named_range_error = "503 Service Unavailable backend error"
        populate_template(client, "sheet_id")

        comp_ws = client.spreadsheet.worksheet("Competition Report")
        values = comp_ws.get_all_values()
        d2 = values[1][3] if len(values) > 1 and len(values[1]) > 3 else ""
        self.assertEqual(d2, "")

        client.spreadsheet.named_range_error = None
        populate_template(client, "sheet_id")
        values = comp_ws.get_all_values()
        d2 = values[1][3] if len(values) > 1 and len(values[1]) > 3 else ""
        self.assertTrue(d2.startswith("=ARRAYFORMULA(IFS("), d2[:80])
        self.assertIn("TOP_UNIV_PATTERN", d2)

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
