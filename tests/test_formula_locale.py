"""
tests/test_formula_locale.py
Fix 9: formulas stay canonical (commas), are written in the spreadsheet's own locale and
are verified after writing; a wrong separator is repaired, a broken one raises.
"""

import io
import unittest
from contextlib import redirect_stdout

from templates.build_template import GRADE_ARRAYFORMULA_D2, populate_template
from templates.formula_locale import (
    FormulaLocaleError, canonicalize_formula, is_formula_error, localize_formula,
    other_separator, separator_for_locale,
)
from tests.fakes import FakeClient


def commas_outside_strings(formula: str) -> int:
    """Counts argument-separator commas: inside a "..." literal they do not count."""
    count = 0
    in_string = False
    i = 0
    while i < len(formula):
        ch = formula[i]
        if in_string:
            if ch == '"':
                if i + 1 < len(formula) and formula[i + 1] == '"':
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            count += 1
        i += 1
    return count


def cell(ws, row: int, col: int) -> str:
    return ws.data[row - 1][col] if len(ws.data) >= row and len(ws.data[row - 1]) > col else ""


class TestLocalizeFormula(unittest.TestCase):

    def test_commas_inside_strings_are_preserved(self):
        source = '=TEXTJOIN(",",TRUE,FLATTEN(A1:B2))'
        localized = localize_formula(source, ";")
        self.assertEqual(localized, '=TEXTJOIN(",";TRUE;FLATTEN(A1:B2))')
        self.assertIn('TEXTJOIN(","', localized)

    def test_comma_separator_returns_the_formula_unchanged(self):
        source = '=SPLIT(flat,",")&IF(A1="","x","y")'
        self.assertEqual(localize_formula(source, ","), source)

    def test_nested_functions_and_no_bare_comma_in_the_semicolon_output(self):
        source = (
            '=IF(COUNTA(A$2:B)=0,"[^\\s\\S]",LET(flat,TEXTJOIN(",",TRUE,FLATTEN(A$2:B)),'
            'tokens,ARRAYFORMULA(TRIM(FLATTEN(SPLIT(flat,",")))),'
            'joined,TEXTJOIN("|",TRUE,tokens),IF(joined="","[^\\s\\S]",joined)))'
        )
        localized = localize_formula(source, ";")
        self.assertEqual(commas_outside_strings(localized), 0, localized)
        self.assertIn('TEXTJOIN(",";TRUE', localized)
        self.assertIn('SPLIT(flat;",")', localized)
        self.assertIn('IF(joined="";"[^\\s\\S]";joined)', localized)

    def test_doubled_quotes_inside_a_string_are_an_escape(self):
        source = '=IF(A1="kata ""a,b"" lain",1,2)'
        localized = localize_formula(source, ";")
        self.assertEqual(localized, '=IF(A1="kata ""a,b"" lain";1;2)')
        # The doubled quote does not close the literal, so the comma stays and A1=... is one argument.
        self.assertEqual(localized.count('"'), source.count('"'))

    def test_multiline_d2_formula_is_localized_and_round_trips(self):
        localized = localize_formula(GRADE_ARRAYFORMULA_D2, ";")
        self.assertGreater(commas_outside_strings(GRADE_ARRAYFORMULA_D2), 10)
        self.assertEqual(commas_outside_strings(localized), 0)
        self.assertIn("\n", localized)
        self.assertIn('" internasional "', localized)
        # Only separators changed: removing them yields the same text on both sides.
        strip = lambda text: text.replace(";", "").replace(",", "")
        self.assertEqual(strip(localized), strip(GRADE_ARRAYFORMULA_D2))

    def test_canonicalize_restores_the_canonical_form(self):
        localized = localize_formula(GRADE_ARRAYFORMULA_D2, ";")
        self.assertEqual(
            canonicalize_formula(localized).strip(),
            canonicalize_formula(GRADE_ARRAYFORMULA_D2).strip(),
        )

    def test_separator_table(self):
        for locale in ("id_ID", "id", "de-DE", "fr_FR", "es_ES", "it_IT", "pt_BR",
                       "nl_NL", "tr_TR", "ru_RU", "pl_PL"):
            with self.subTest(locale=locale):
                self.assertEqual(separator_for_locale(locale), ";")
        for locale in ("en_US", "en-GB", "", None, "ja_JP", "zh_CN"):
            with self.subTest(locale=locale):
                self.assertEqual(separator_for_locale(locale), ",")
        self.assertEqual(other_separator(";"), ",")
        self.assertEqual(other_separator(","), ";")

    def test_is_formula_error(self):
        for value in ("#ERROR!", "#ERROR! Error mengurai formula", "#NAME?", "#N/A", "#REF!"):
            self.assertTrue(is_formula_error(value), value)
        for value in ("", "Advance", "=ARRAYFORMULA(IFS(", "nilai #VALUE!"):
            self.assertFalse(is_formula_error(value), value)


def comma_formula_engine(formula: str) -> str:
    """A sheet that only parses ';' formulas: a comma formula shows #ERROR!."""
    return "#ERROR!" if commas_outside_strings(formula) else ""


def semicolon_formula_engine(formula: str) -> str:
    """A sheet that only parses ',' formulas: a ';' formula shows #ERROR!."""
    return "#ERROR!" if canonicalize_formula(formula) != formula else ""


def always_error_engine(formula: str) -> str:
    return "#ERROR!"


class PopulateLocaleTestCase(unittest.TestCase):

    def build(self, locale: str = "en_US", engine=None):
        client = FakeClient(empty=True)
        client.spreadsheet.locale = locale
        client.spreadsheet.formula_engine = engine
        return client

    def formulas(self, client):
        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        return (
            grade_ws,
            comp_ws,
            cell(grade_ws, 2, 6),
            cell(grade_ws, 3, 6),
            cell(comp_ws, 2, 3),
        )


class TestPopulateTemplateLocale(PopulateLocaleTestCase):

    def test_indonesian_locale_writes_semicolon_formulas(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        populate_template(client, "sheet_id")

        grade_ws, comp_ws, g2, g3, d2 = self.formulas(client)

        self.assertEqual(grade_ws.data[1][5], "TOP_UNIV_PATTERN")
        self.assertEqual(grade_ws.data[2][5], "TOP_ORG_PATTERN")
        for formula in (g2, g3):
            self.assertTrue(formula.startswith("="), formula[:40])
            self.assertEqual(commas_outside_strings(formula), 0, formula[:120])
            self.assertIn(";", formula)
            self.assertIn('TEXTJOIN(",";TRUE', formula)  # literal comma survived
        self.assertTrue(d2.startswith("=ARRAYFORMULA(IFS("))
        self.assertEqual(commas_outside_strings(d2), 0, d2[:160])
        self.assertIn(";", d2)
        # The locale table chose ';' immediately: one write per cell, no retry needed.
        self.assertEqual([w["target"] for w in grade_ws.writes].count("G2"), 1)
        self.assertEqual([w["target"] for w in comp_ws.writes].count("D2"), 1)

    def test_comma_locale_keeps_comma_formulas(self):
        client = self.build(locale="en_US", engine=semicolon_formula_engine)
        populate_template(client, "sheet_id")

        _grade_ws, _comp_ws, g2, _g3, d2 = self.formulas(client)
        self.assertGreater(commas_outside_strings(g2), 5)
        self.assertGreater(commas_outside_strings(d2), 5)

    def test_wrong_first_guess_is_retried_with_the_other_separator(self):
        """The locale says en_US but the sheet only accepts ';': the helper repairs it."""
        client = self.build(locale="en_US", engine=comma_formula_engine)
        populate_template(client, "sheet_id")

        grade_ws, comp_ws, g2, g3, d2 = self.formulas(client)
        for formula in (g2, g3, d2):
            self.assertEqual(commas_outside_strings(formula), 0, formula[:120])
        self.assertEqual([w["target"] for w in grade_ws.writes].count("G2"), 2)

    def test_error_for_both_separators_raises_and_does_not_install_d2(self):
        client = self.build(locale="id_ID", engine=always_error_engine)

        with self.assertRaises(FormulaLocaleError) as ctx:
            populate_template(client, "sheet_id")

        message = str(ctx.exception)
        self.assertIn("Grade Rules!G2", message)
        self.assertIn("#ERROR!", message)
        self.assertIn("id_ID", message)
        self.assertIn("';'", message)
        self.assertIn("','", message)
        _grade_ws, comp_ws, _g2, _g3, d2 = self.formulas(client)
        self.assertEqual(d2, "", "D2 must not be installed when the helper cells are in error")

    def test_time_zone_is_only_reported_never_changed(self):
        client = self.build(locale="en_US")
        client.spreadsheet.time_zone = "America/New_York"

        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")

        self.assertIn("PERINGATAN", out.getvalue())
        self.assertIn("Asia/Jakarta", out.getvalue())
        self.assertEqual(client.spreadsheet.time_zone, "America/New_York")

    def test_jakarta_time_zone_prints_no_warning(self):
        client = self.build(locale="id_ID")
        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")
        self.assertNotIn("PERINGATAN", out.getvalue())


class TestPopulateTemplateIdempotency(PopulateLocaleTestCase):

    def test_rerun_does_not_rewrite_an_identical_generated_d2(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        first = cell(comp_ws, 2, 3)
        writes_before = len(comp_ws.writes)

        populate_template(client, "sheet_id")

        self.assertEqual(cell(comp_ws, 2, 3), first)
        new_targets = [w["target"] for w in comp_ws.writes[writes_before:]]
        self.assertNotIn("D2", new_targets)

    def test_rerun_does_not_rewrite_identical_helper_cells(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        populate_template(client, "sheet_id")
        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        first = cell(grade_ws, 2, 6)
        writes_before = len(grade_ws.writes)

        populate_template(client, "sheet_id")

        self.assertEqual(cell(grade_ws, 2, 6), first)
        new_targets = [w["target"] for w in grade_ws.writes[writes_before:]]
        self.assertEqual(new_targets, [])

    def test_custom_d2_is_not_overwritten_even_when_it_evaluates_to_empty(self):
        """FORMULA render option: a computed "" must not look like an empty cell (Fix 9)."""
        client = self.build(locale="en_US", engine=lambda formula: "")
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        comp_ws.data[1][3] = "=MY_OWN_FORMULA()"
        writes_before = len(comp_ws.writes)

        populate_template(client, "sheet_id")

        self.assertEqual(cell(comp_ws, 2, 3), "=MY_OWN_FORMULA()")
        new_targets = [w["target"] for w in comp_ws.writes[writes_before:]]
        self.assertNotIn("D2", new_targets)

    def test_custom_helper_cell_is_not_overwritten(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        populate_template(client, "sheet_id")
        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        grade_ws.data[1][6] = "=MY_OWN_PATTERN()"

        populate_template(client, "sheet_id")

        self.assertEqual(cell(grade_ws, 2, 6), "=MY_OWN_PATTERN()")

    def test_legacy_named_range_formula_is_recognised_and_refreshed(self):
        """Fix 10: the previous generator's named-range formula is ours, so it gets repaired."""
        client = self.build(locale="en_US")
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        legacy = (GRADE_ARRAYFORMULA_D2
                  .replace("'Grade Rules'!$G$2", "TOP_UNIV_PATTERN")
                  .replace("'Grade Rules'!$G$3", "TOP_ORG_PATTERN"))
        comp_ws.data[1][3] = legacy
        writes_before = len(comp_ws.writes)

        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")

        refreshed = cell(comp_ws, 2, 3)
        self.assertIn("'Grade Rules'!$G$2", refreshed)
        self.assertNotIn("TOP_UNIV_PATTERN", refreshed)
        self.assertIn("D2", [w["target"] for w in comp_ws.writes[writes_before:]])
        self.assertIn("disegarkan", out.getvalue())

    def test_existing_comma_formula_is_repaired_for_the_new_locale(self):
        """An already broken (comma) helper formula is rewritten with ';', not left broken."""
        client = self.build(locale="en_US", engine=None)
        populate_template(client, "sheet_id")
        grade_ws = client.spreadsheet.worksheet("Grade Rules")
        broken = cell(grade_ws, 2, 6)
        self.assertGreater(commas_outside_strings(broken), 0)

        client.spreadsheet.locale = "id_ID"
        client.spreadsheet.formula_engine = comma_formula_engine
        populate_template(client, "sheet_id")

        repaired = cell(grade_ws, 2, 6)
        self.assertEqual(commas_outside_strings(repaired), 0)
        self.assertIn(";", repaired)


class TestD2InstallationAndStatus(PopulateLocaleTestCase):
    """Fix 10: D2 is installed on a blank sheet and the run always prints a clear status."""

    def test_blank_spreadsheet_gets_the_localized_d2_formula(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")

        comp_ws = client.spreadsheet.worksheet("Competition Report")
        d2 = cell(comp_ws, 2, 3)
        self.assertTrue(d2.startswith("=ARRAYFORMULA(IFS("), d2[:80])
        self.assertEqual(commas_outside_strings(d2), 0, d2[:160])
        self.assertIn("'Grade Rules'!$G$2", d2)
        self.assertIn("'Grade Rules'!$G$3", d2)
        self.assertTrue(d2.endswith(")"), d2[-40:])

        status = out.getvalue()
        self.assertIn("STATUS TEMPLATE", status)
        self.assertIn("Grade Rules!G2: ok", status)
        self.assertIn("Grade Rules!G3: ok", status)
        self.assertIn("Competition Report!D2: terpasang", status)
        self.assertIn("id_ID", status)
        self.assertIn("';'", status)

    def test_rerun_reports_the_generated_d2_as_unchanged(self):
        client = self.build(locale="id_ID", engine=comma_formula_engine)
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        first = cell(comp_ws, 2, 3)

        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")

        self.assertEqual(cell(comp_ws, 2, 3), first)
        status = out.getvalue()
        self.assertIn("Competition Report!D2: tidak diubah (sudah berisi formula sistem", status)
        self.assertNotIn("terpasang", status)

    def test_custom_d2_is_left_untouched_with_a_visible_message(self):
        client = self.build(locale="en_US")
        populate_template(client, "sheet_id")
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        comp_ws.data[1][3] = "=ARRAYFORMULA(IFS(A2:A=\"x\",\"Custom\"))"
        writes_before = len(comp_ws.writes)

        out = io.StringIO()
        with redirect_stdout(out):
            populate_template(client, "sheet_id")

        self.assertEqual(cell(comp_ws, 2, 3), "=ARRAYFORMULA(IFS(A2:A=\"x\",\"Custom\"))")
        self.assertNotIn("D2", [w["target"] for w in comp_ws.writes[writes_before:]])
        status = out.getvalue()
        self.assertIn("Competition Report!D2: tidak diubah", status)
        self.assertIn("buatan admin", status)

    def test_helper_error_still_prints_the_status_table_before_raising(self):
        client = self.build(locale="id_ID", engine=always_error_engine)
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(FormulaLocaleError):
                populate_template(client, "sheet_id")

        status = out.getvalue()
        self.assertIn("Grade Rules!G2: error", status)
        self.assertIn("Grade Rules!G3: error", status)
        self.assertIn("Competition Report!D2: tidak diubah (sel bantu G2:G3 error)", status)
        comp_ws = client.spreadsheet.worksheet("Competition Report")
        self.assertEqual(cell(comp_ws, 2, 3), "")


if __name__ == "__main__":
    unittest.main()
