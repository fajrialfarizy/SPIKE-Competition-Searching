"""Unit tests for compmon.env_check: install only what is missing or too old."""

import tempfile
import unittest
from pathlib import Path

from compmon import env_check

REQUIREMENTS = """\
google-genai>=1.2
apify-client>=1.12,<2
gspread==6.1.4
pydantic>=2.10
"""


class EnvCheckTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.requirements_path = Path(self._tmpdir.name) / "requirements.txt"
        self.requirements_path.write_text(REQUIREMENTS, encoding="utf-8")
        self.printed = []
        self.commands = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def make_version_getter(self, versions):
        return lambda name: versions.get(name)

    def run_ensure(self, versions, returncode=0, checks=None):
        def fake_pip(command):
            self.commands.append(list(command))
            return returncode, "" if returncode == 0 else "ERROR: No matching distribution found"

        return env_check.ensure_environment(
            requirements_path=self.requirements_path,
            printer=self.printed.append,
            version_getter=self.make_version_getter(versions),
            run_pip=fake_pip,
            checks=checks or (lambda: []),
        )

    def installed_specs(self):
        return [
            argument
            for command in self.commands
            for argument in command
            if argument.startswith(("apify-client", "google-genai", "gspread", "pydantic"))
        ]


class TestEnsureEnvironment(EnvCheckTestBase):
    def test_missing_package_installs_only_that_one(self):
        result = self.run_ensure(
            {
                "google-genai": "1.2.0",
                "gspread": "6.1.4",
                "pydantic": "2.10.6",
            }
        )

        self.assertEqual(result, "restart_required")
        self.assertEqual(self.installed_specs(), ["apify-client>=1.12,<2"])
        self.assertEqual(len(self.commands), 1)
        self.assertNotIn("--upgrade", self.commands[0])
        self.assertIn(env_check.RESTART_MESSAGE, self.printed)

    def test_satisfied_packages_install_nothing(self):
        result = self.run_ensure(
            {
                "google-genai": "1.2.0",
                "apify-client": "1.12.2",
                "gspread": "6.1.4",
                "pydantic": "2.10.6",
            }
        )

        self.assertEqual(result, "ok")
        self.assertEqual(self.commands, [])
        self.assertNotIn(env_check.RESTART_MESSAGE, self.printed)

    def test_only_outdated_package_is_upgraded(self):
        result = self.run_ensure(
            {
                "google-genai": "1.2.0",
                "apify-client": "1.9.0",
                "gspread": "6.1.4",
                "pydantic": "2.10.6",
            }
        )

        self.assertEqual(result, "restart_required")
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.installed_specs(), ["apify-client>=1.12,<2"])
        self.assertIn("--upgrade", self.commands[0])

    def test_pip_failure_raises_with_package_name_and_output(self):
        with self.assertRaises(env_check.PackageInstallError) as ctx:
            self.run_ensure({"google-genai": "1.2.0", "gspread": "6.1.4", "pydantic": "2.10.6"}, returncode=1)

        message = str(ctx.exception)
        self.assertIn("apify-client", message)
        self.assertIn("No matching distribution found", message)
        self.assertTrue(any("pip gagal memasang paket 'apify-client'" in line for line in self.printed))

    def test_never_issues_a_blanket_requirements_install(self):
        self.run_ensure({})

        self.assertTrue(self.commands)
        for command in self.commands:
            joined = " ".join(command)
            self.assertNotIn("-r", command)
            self.assertNotIn("requirements.txt", joined)
            self.assertNotIn("--force-reinstall", command)
            # Exact pins stay only for packages that are not preinstalled (gspread).
            exact = [argument for argument in command if "==" in argument]
            self.assertTrue(all(argument.startswith("gspread==") for argument in exact), exact)

    def test_incompatible_but_installed_environment_raises_without_installing(self):
        with self.assertRaises(env_check.EnvironmentCheckError):
            self.run_ensure(
                {
                    "google-genai": "1.2.0",
                    "apify-client": "1.12.2",
                    "gspread": "6.1.4",
                    "pydantic": "2.10.6",
                },
                checks=lambda: ["import apify_client gagal: ImportError"],
            )

        self.assertEqual(self.commands, [])
        self.assertTrue(any("import apify_client gagal" in line for line in self.printed))


class TestRunEnvironmentCheck(EnvCheckTestBase):
    def test_problems_are_reported_and_stop_the_cell(self):
        printed = []
        with self.assertRaises(env_check.EnvironmentCheckError) as ctx:
            env_check.run_environment_check(
                requirements_path=self.requirements_path,
                printer=printed.append,
                checks=lambda: ["import apify_client gagal: ImportError('ignore_docs')"],
            )

        report = "\n".join(printed)
        self.assertIn("[GAGAL] Lingkungan notebook belum siap", report)
        self.assertIn("import apify_client gagal", report)
        self.assertIn(env_check.INSTALL_HINT, str(ctx.exception))

    def test_report_lists_python_and_tracked_versions(self):
        printed = []
        try:
            env_check.run_environment_check(requirements_path=self.requirements_path, printer=printed.append)
        except env_check.EnvironmentCheckError:
            pass

        report = "\n".join(printed)
        self.assertIn("Python:", report)
        for distribution in env_check.TRACKED_DISTRIBUTIONS:
            self.assertIn(distribution, report)

    def test_parse_requirements_ignores_comments_and_blank_lines(self):
        path = Path(self._tmpdir.name) / "with_comments.txt"
        path.write_text("# comment\n\napify-client>=1.12,<2  # inline\n\x20\x20\n", encoding="utf-8")

        parsed = env_check.parse_requirements(path)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].spec(), "apify-client>=1.12,<2")

    def test_unsatisfied_requirements_reports_missing_and_too_old(self):
        requirements = env_check.parse_requirements(self.requirements_path)
        pending = env_check.unsatisfied_requirements(
            requirements,
            lambda name: {"gspread": "6.0.0", "pydantic": "2.10.6"}.get(name),
        )

        self.assertEqual(
            sorted(r.name for r in pending),
            ["apify-client", "google-genai", "gspread"],
        )


if __name__ == "__main__":
    unittest.main()
