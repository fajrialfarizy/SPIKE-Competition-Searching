"""Guards requirements.txt against pins that break a fresh Google Colab runtime."""

import unittest
from pathlib import Path

REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"

# Colab (Python 3.13) preinstalls these. A `==` pin makes pip downgrade them,
# which is what broke `from apify_client import ApifyClient`.
COLAB_PREINSTALLED = {"google-genai", "google-auth", "pydantic", "websockets"}


def parse_requirements(path: Path):
    """Returns {distribution: specifier} for every non-comment line."""
    parsed = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        name = line
        specifier = ""
        for i, char in enumerate(line):
            if char in "<>=!~":
                name, specifier = line[:i].strip(), line[i:].strip()
                break
        parsed[name.lower()] = specifier
    return parsed


class TestRequirementsFile(unittest.TestCase):
    def setUp(self):
        self.requirements = parse_requirements(REQUIREMENTS_PATH)

    def test_preinstalled_colab_packages_are_not_pinned(self):
        pinned = {
            name: specifier
            for name, specifier in self.requirements.items()
            if name in COLAB_PREINSTALLED and "==" in specifier
        }
        self.assertEqual(
            pinned,
            {},
            "Paket bawaan Colab tidak boleh dipin dengan '==': " + repr(pinned),
        )

    def test_only_gspread_uses_an_exact_pin(self):
        exact = {
            name: specifier
            for name, specifier in self.requirements.items()
            if "==" in specifier
        }
        self.assertEqual(
            set(exact),
            {"gspread"},
            "Hanya gspread yang boleh dipin exact, ditemukan: " + repr(exact),
        )

    def test_apify_client_range_keeps_apify_shared_below_two(self):
        # apify-client 1.8.1 had no upper bound on apify-shared, so pip installed
        # apify-shared 2.x, which no longer exports apify_shared.utils.ignore_docs.
        self.assertIn("apify-client", self.requirements)
        specifier = self.requirements["apify-client"]
        self.assertIn("<2", specifier, f"apify-client harus dibatasi <2, dapat: {specifier}")

    def test_every_runtime_import_is_listed(self):
        for distribution in ("apify-client", "gspread", "google-genai", "google-auth", "pydantic"):
            self.assertIn(distribution, self.requirements)


if __name__ == "__main__":
    unittest.main()
