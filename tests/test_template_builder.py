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


# A comparison of something with 0 or 1 (`>0`, `=0`, `>=1`, `<>0`, ...).
COMPARISON_WITH_A_NUMBER = re.compile(r"[=<>!]=?\s*[01]\b")


def _matching_close(text: str, open_index: int):
    """Index of the `)` matching the `(` at `open_index` (None when unbalanced)."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _matching_open(text: str, close_index: int):
    """Index of the `(` matching the `)` at `close_index` (None when unbalanced)."""
    depth = 0
    for index in range(close_index, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _has_top_level_arithmetic(expression: str) -> bool:
    """True when `expression` uses +, -, *, /, ^ or & outside its own parentheses."""
    depth = 0
    in_string = False
    for index, char in enumerate(expression):
        if in_string:
            if char == '"' and not (index + 1 < len(expression) and expression[index + 1] == '"'):
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char in "+-*/^&":
            return True
    return False


def _unwrap_parens(expression: str) -> str:
    """Strips parentheses that wrap the whole expression, e.g. `((F(x)))` -> `F(x)`."""
    text = expression.strip()
    while (text.startswith("(") and text.endswith(")")
           and _matching_close(text, 0) == len(text) - 1):
        text = text[1:-1].strip()
    return text


def regexmatch_compared_with_a_number(formula: str):
    """
    Every comparison of a bare `REGEXMATCH(...)` result with a number (`>0`, `=0`, ...).

    Fix 10: REGEXMATCH returns a BOOLEAN and Google Sheets ranks booleans ABOVE numbers, so
    `FALSE>0` is TRUE and such a comparison is always true. Only SUMS of REGEXMATCH results
    are numbers and may be compared, so a sum form is accepted while a bare boolean - with or
    without redundant parentheses around it - is reported.
    """
    found = []
    for match in COMPARISON_WITH_A_NUMBER.finditer(formula):
        # The left operand of the comparison must be a parenthesised group.
        index = match.start() - 1
        while index >= 0 and formula[index].isspace():
            index -= 1
        if index < 0 or formula[index] != ")":
            continue
        open_index = _matching_open(formula, index)
        if open_index is None:
            continue
        # `REGEXMATCH(...)>0`: the operand is the call itself, so the matched `(` is its
        # ARGUMENT list and `inner` below would be just `A1,B1`. Use the call when the text
        # before the `(` is the REGEXMATCH function name.
        before = re.search(r"[A-Za-z_][A-Za-z0-9_.]*$", formula[:open_index])
        if before and before.group().upper() == "REGEXMATCH":
            found.append(formula[open_index - len(before.group()):match.end()])
            continue
        inner = _unwrap_parens(formula[open_index:index + 1])
        # Only a whole REGEXMATCH call is a boolean; `LEN(...)`/`COUNTA(...)` are numbers and
        # `REGEXMATCH(a)+REGEXMATCH(b)` is a numeric sum. Anything else is not our concern.
        if not inner.startswith("REGEXMATCH("):
            continue
        if _has_top_level_arithmetic(inner):
            continue
        found.append(formula[open_index:match.end()])
    return found


def ifs_conditions(formula: str):
    """The condition of every IFS branch (text before the first top-level comma)."""
    body = formula[formula.index("IFS(") + len("IFS("):]
    conditions = []
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith(")"):
            continue
        depth = 0
        in_string = False
        for index, char in enumerate(text):
            if in_string:
                if char == '"' and not (index + 1 < len(text) and text[index + 1] == '"'):
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                conditions.append(text[:index].strip())
                break
    return conditions


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
        # Fix 10: the alias patterns are the helper CELLS, never a named range.
        self.assertIn("'Grade Rules'!$G$2", formula)
        self.assertIn("'Grade Rules'!$G$3", formula)
        self.assertNotIn("TOP_UNIV_PATTERN", formula)
        self.assertNotIn("TOP_ORG_PATTERN", formula)
        self.assertIn("'Grade Rules'!", formula)
        self.assertNotIn("'Master Top University'!", formula)
        self.assertNotIn("'Master Top Organizer'!", formula)

    def test_d2_references_the_helper_cells_instead_of_inlining_the_pattern(self):
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

        # Each helper cell really holds the compiled pattern: three rule branches use the
        # university pattern (twice per branch: Universitas + Penyelenggara), three use the
        # organizer pattern (once each).
        self.assertEqual(formula.count("'Grade Rules'!$G$2"), 6)
        self.assertEqual(formula.count("'Grade Rules'!$G$3"), 3)
        for name, pattern_formula in GRADE_PATTERN_HELPER_FORMULAS.items():
            self.assertTrue(pattern_formula.startswith("="), name)
            self.assertGreater(len(pattern_formula), 400, name)

    def test_detector_flags_known_bad_boolean_comparisons(self):
        """The structural guard must be proven on bad input, not only on generated formulas."""
        bad = [
            '=ARRAYFORMULA(IFS((REGEXMATCH(A2:A,B2)>0),"x"))',
            '=ARRAYFORMULA(IFS((REGEXMATCH(A2:A,B2))=0,"x"))',
            '=IF(REGEXMATCH(A1,B1)=0,"no","yes")',
            '=IF((REGEXMATCH(A1,B1)>=1),"yes","no")',
        ]
        for formula in bad:
            with self.subTest(formula=formula):
                self.assertEqual(len(regexmatch_compared_with_a_number(formula)), 1, formula)

    def test_detector_accepts_numeric_sums_and_bare_booleans(self):
        good = [
            # A sum of booleans is a number: comparing it with 0 is correct.
            '=ARRAYFORMULA(IFS(((REGEXMATCH(A2:A,B2)+REGEXMATCH(C2:C,B2))>0),"x"))',
            # A bare boolean used as a condition (the Fix 10 shape) compares with nothing.
            '=ARRAYFORMULA(IFS((REGEXMATCH(A2:A,B2)),"x"))',
            # Numeric functions compared with 0 stay allowed.
            '=IF((LEN(A1)=0)*(LEN(B1)=0),"blank","filled")',
        ]
        for formula in good:
            with self.subTest(formula=formula):
                self.assertEqual(regexmatch_compared_with_a_number(formula), [], formula)

    def test_no_regexmatch_result_is_compared_with_a_number(self):
        """Fix 10: `REGEXMATCH(...)>0` is always TRUE in Sheets, so it must never be generated."""
        for label, formula in [("D2", GRADE_ARRAYFORMULA_D2),
                               *GRADE_PATTERN_HELPER_FORMULAS.items()]:
            with self.subTest(formula=label):
                self.assertEqual(regexmatch_compared_with_a_number(formula), [], label)

    def test_top_organizer_branch_uses_the_boolean_directly(self):
        """The exact owner-reported bug: BINUS/BINUS must not count as Top Organizer."""
        formula = GRADE_ARRAYFORMULA_D2
        self.assertIn("'Grade Rules'!$G$3))", formula)
        self.assertNotIn("'Grade Rules'!$G$3)>0", formula)
        # The university branch is a SUM of two REGEXMATCH results, which is numeric.
        self.assertIn("((REGEXMATCH((\" \"&", formula)

    def test_ifs_conditions_are_numeric_not_bare_booleans(self):
        """A bare boolean mixed with numbers in IFS is the class of bug Fix 10 removes."""
        conditions = ifs_conditions(GRADE_ARRAYFORMULA_D2)
        self.assertGreater(len(conditions), len(INITIAL_SHEETS_DATA["Grade Rules"]["rows"]))
        for condition in conditions:
            with self.subTest(condition=condition[:60]):
                self.assertTrue(
                    condition == "TRUE" or "*" in condition or ">0" in condition
                    or "=0" in condition,
                    f"IFS condition is a bare boolean/number: {condition[:120]}",
                )

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

    def test_grade_pattern_helpers_are_installed_and_referenced_by_d2(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")

        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        self.assertEqual(grade_ws.data[0][5], "Auto-generated alias pattern (do not edit)")
        self.assertEqual(grade_ws.data[1][5], "TOP_UNIV_PATTERN")
        self.assertEqual(grade_ws.data[1][6], GRADE_PATTERN_HELPER_FORMULAS["TOP_UNIV_PATTERN"])
        self.assertEqual(grade_ws.data[2][5], "TOP_ORG_PATTERN")
        self.assertEqual(grade_ws.data[2][6], GRADE_PATTERN_HELPER_FORMULAS["TOP_ORG_PATTERN"])

        # Fix 10: no named range is created; D2 points straight at the helper cells.
        self.assertEqual(client.spreadsheet.named_ranges, {})
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        d2 = comp_ws.data[1][3]
        self.assertIn("'Grade Rules'!$G$2", d2)
        self.assertIn("'Grade Rules'!$G$3", d2)

    def test_grade_pattern_helpers_are_idempotent_and_never_overwrite_admin_text(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")
        populate_template(client, "sheet_id")

        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        self.assertEqual(grade_ws.data[1][5], "TOP_UNIV_PATTERN")

        # An admin value in the helper cell is preserved.
        grade_ws.data[1][6] = "=MY_OWN_PATTERN()"
        populate_template(client, "sheet_id")
        self.assertEqual(grade_ws.data[1][6], "=MY_OWN_PATTERN()")

    def test_d2_is_installed_even_when_named_ranges_cannot_be_created(self):
        """Fix 10: D2 no longer depends on named ranges, so a rejected addNamedRange is harmless."""
        client = FakeClient(empty=True)
        client.spreadsheet.named_range_error = (
            "403 PERMISSION_DENIED: The caller does not have permission"
        )

        populate_template(client, "sheet_id")

        comp_ws = client.spreadsheet.worksheet("Competition Report")
        self.assertEqual(comp_ws.data[0][3], "Grade Lomba")
        d2 = comp_ws.data[1][3]
        self.assertTrue(d2.startswith("=ARRAYFORMULA(IFS("), d2[:80])
        self.assertIn("'Grade Rules'!$G$2", d2)
        self.assertEqual(client.spreadsheet.named_ranges, {})

    def test_d2_is_written_once_and_then_left_alone(self):
        client = FakeClient(empty=True)
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        first = comp_ws.data[1][3]

        populate_template(client, "sheet_id")

        self.assertEqual(comp_ws.data[1][3], first)

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
