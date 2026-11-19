"""
templates.formula_locale
Keeps every generated formula in canonical US syntax and translates only the argument
separator for the spreadsheet's own locale (Fix 9).

Google Sheets parses formulas with the separator of the spreadsheet locale: an Indonesian
(`id_ID`) sheet expects `;` and shows `#ERROR! Error mengurai formula` for a comma formula.
Changing the locale is not allowed, so the formula is localized at write time instead.
Everything in this module is pure: it never touches the Sheets API.
"""

from typing import Optional

# Locales whose decimal separator is a comma: Google Sheets then uses ";" between
# arguments. `id_ID` (the owner's spreadsheet) is the reason this module exists.
DECIMAL_COMMA_LOCALES = frozenset("""
id de fr es it pt nl tr ru uk pl cs sk hu ro bg hr sr sl bs mk sq
da fi nb nn sv is el lt lv et ca gl eu
""".split())

# Values a written formula can show when it did not parse (or lost its reference).
FORMULA_ERROR_PREFIXES = ("#ERROR!", "#NAME?", "#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NUM!")

FORMULA_ERROR_HINT = (
    "#ERROR! usually means the argument separator does not match the spreadsheet locale"
)


class FormulaLocaleError(RuntimeError):
    """A formula could not be entered in the spreadsheet, with any separator."""


def separator_for_locale(locale: Optional[str]) -> str:
    """
    Argument separator for a spreadsheet locale.

    `id_ID`, `de-DE`, `fr_FR` ... -> ";"; unknown/empty or a dot-decimal locale -> ",".
    Only the primary language subtag matters.
    """
    primary = str(locale or "").strip().replace("-", "_").split("_")[0].lower()
    return ";" if primary in DECIMAL_COMMA_LOCALES else ","


def other_separator(separator: str) -> str:
    """The separator to try after `separator`."""
    return "," if separator == ";" else ";"


def _rewrite_separators(formula: str, target: str) -> str:
    """
    Replaces argument separators ("," or ";") outside double-quoted strings by `target`.

    Inside a string literal a double quote is escaped by doubling it (""), so `""` is
    copied verbatim and does not close the literal.
    """
    text = str(formula or "")
    out = []
    in_string = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == '"':
                if i + 1 < length and text[i + 1] == '"':
                    out.append('"')
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch in (",", ";"):
            out.append(target)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def localize_formula(formula: str, separator: str) -> str:
    """
    Returns `formula` written for `separator`.

    Only argument-separator commas are replaced, and only OUTSIDE double-quoted strings, so
    `TEXTJOIN(",", ...)`, `SPLIT(flat, ",")` and every other literal survive untouched.
    With `separator=","` the text is returned unchanged. No other character changes.
    """
    if separator == ",":
        return str(formula or "")
    return _rewrite_separators(formula, separator)


def canonicalize_formula(formula: str) -> str:
    """
    Canonical (comma) form of a stored formula, so a localized generated formula compares
    equal to its canonical source. Separators inside string literals are preserved.
    """
    return _rewrite_separators(formula, ",")


def is_formula_error(value: object) -> bool:
    """True when a cell value is one of the Sheets error values (e.g. `#ERROR!`)."""
    text = str(value or "").strip()
    return any(text.startswith(prefix) for prefix in FORMULA_ERROR_PREFIXES)
