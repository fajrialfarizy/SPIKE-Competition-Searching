"""
compmon.env_check
Colab environment check and minimal dependency installer.

Why this exists: Colab preinstalls google-genai, google-auth, pydantic and
websockets. A blanket `pip install -r requirements.txt` with `==` pins downgraded
them and broke `import apify_client`. The notebooks therefore call

    run_environment_check()   # report versions, imports and API signatures
    ensure_environment()      # install ONLY missing/too-old packages, then stop

`ensure_environment` never uses `-r`, never force-reinstalls, and never downgrades
a preinstalled package: it installs a single package per pip call with the
specifier from requirements.txt and adds `--upgrade` only when the installed
version is below the minimum.

All checks are offline: no Apify, Gemini or Sheets call is made.
"""

from __future__ import annotations

import importlib.metadata as metadata
import inspect
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from packaging.specifiers import InvalidSpecifier, SpecifierSet

# Printed by the environment check cell, in this order.
TRACKED_DISTRIBUTIONS: Tuple[str, ...] = (
    "apify-client",
    "apify-shared",
    "gspread",
    "google-genai",
    "google-auth",
    "pydantic",
)

REQUIREMENTS_FILE = "requirements.txt"

RESTART_MESSAGE = (
    "Paket baru dipasang. Jalankan Runtime > Restart session, lalu jalankan ulang "
    "notebook dari atas."
)

INSTALL_HINT = (
    "Jalankan sel 'Smart Install' tepat di bawah sel ini, lalu Runtime > Restart "
    "session, lalu jalankan ulang notebook dari atas (mulai dari sel Environment Check)."
)


class EnvironmentCheckError(RuntimeError):
    """The runtime is missing a package or an API the notebook depends on."""


class PackageInstallError(RuntimeError):
    """pip failed for one specific package. Never swallowed."""


@dataclass(frozen=True)
class Requirement:
    """One line of requirements.txt: a distribution name and its specifier."""

    name: str
    specifier: str

    def spec(self) -> str:
        return f"{self.name}{self.specifier}"


# ---------------------------------------------------------------- requirements


def find_requirements_file(start: Optional[Path] = None) -> Optional[Path]:
    """Finds requirements.txt in the working directory or its parent (Colab runs from notebooks/)."""
    base = Path(start) if start is not None else Path.cwd()
    candidates = [
        base / REQUIREMENTS_FILE,
        base.parent / REQUIREMENTS_FILE,
        # Repo root next to the compmon package, wherever the notebook runs from.
        Path(__file__).resolve().parent.parent / REQUIREMENTS_FILE,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_requirements(path: Path) -> List[Requirement]:
    """Parses requirements.txt, ignoring comments, blank lines and pip options."""
    parsed: List[Requirement] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line
        specifier = ""
        for i, char in enumerate(line):
            if char in "<>=!~":
                name, specifier = line[:i].strip(), line[i:].strip()
                break
        if not name:
            continue
        try:
            SpecifierSet(specifier)
        except InvalidSpecifier as exc:
            raise ValueError(f"Specifier tidak valid di requirements.txt: '{line}'") from exc
        parsed.append(Requirement(name=name, specifier=specifier))
    return parsed


def installed_version(distribution: str) -> Optional[str]:
    """Installed version of a distribution, or None when it is not installed."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def unsatisfied_requirements(
    requirements: Sequence[Requirement],
    version_getter: Callable[[str], Optional[str]] = installed_version,
) -> List[Requirement]:
    """Requirements that are missing or outside their specifier."""
    pending: List[Requirement] = []
    for requirement in requirements:
        version = version_getter(requirement.name)
        if version is None:
            pending.append(requirement)
            continue
        if not SpecifierSet(requirement.specifier).contains(version, prereleases=True):
            pending.append(requirement)
    return pending


# ---------------------------------------------------------------- compatibility


def _check_signature(
    target: object,
    required: Sequence[str],
    label: str,
    problems: List[str],
) -> None:
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError) as exc:
        problems.append(f"{label}: signature tidak dapat dibaca ({exc})")
        return
    missing = [name for name in required if name not in parameters]
    if missing:
        problems.append(f"{label}: parameter wajib tidak ditemukan {missing}")


def _check_fields(
    model: object,
    required: Sequence[str],
    label: str,
    problems: List[str],
) -> None:
    """Checks a pydantic model's fields; plain constructors are covered by the build test below."""
    fields = getattr(model, "model_fields", None)
    if not fields:
        return
    missing = [name for name in required if name not in fields]
    if missing:
        problems.append(f"{label}: field wajib tidak ditemukan {missing}")


def compatibility_problems() -> List[str]:
    """Offline smoke imports plus signature checks for every third-party call we make."""
    problems: List[str] = []

    try:
        from apify_client import ApifyClient
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        problems.append(f"import apify_client gagal: {exc!r}")
        ApifyClient = None  # type: ignore[assignment]

    if ApifyClient is not None:
        _check_signature(ApifyClient, ["token"], "ApifyClient.__init__", problems)
        _check_signature(ApifyClient.actor, ["actor_id"], "ApifyClient.actor", problems)
        _check_signature(ApifyClient.dataset, ["dataset_id"], "ApifyClient.dataset", problems)
        try:
            client = ApifyClient("signature-check-only")
            _check_signature(
                client.actor("signature-check-only").call,
                ["run_input"],
                "ActorClient.call",
                problems,
            )
            _check_signature(
                client.dataset("signature-check-only").list_items,
                ["offset", "limit"],
                "DatasetClient.list_items",
                problems,
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"apify-client tidak dapat membuat actor/dataset client: {exc!r}")

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # noqa: BLE001
        problems.append(f"import google-genai gagal: {exc!r}")
        genai = None  # type: ignore[assignment]
        types = None  # type: ignore[assignment]

    if genai is not None and types is not None:
        _check_signature(genai.Client, ["api_key"], "genai.Client", problems)
        _check_signature(
            genai.models.Models.generate_content,
            ["model", "contents", "config"],
            "Models.generate_content",
            problems,
        )
        _check_fields(
            types.GenerateContentConfig,
            ["system_instruction", "response_mime_type", "response_schema", "temperature"],
            "types.GenerateContentConfig",
            problems,
        )
        try:
            types.GenerateContentConfig(
                system_instruction="x",
                response_mime_type="application/json",
                response_schema={"type": "OBJECT", "properties": {}},
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(
                "types.GenerateContentConfig menolak response_schema dict dengan tipe "
                f"huruf besar (OBJECT/STRING/BOOLEAN): {exc!r}"
            )

    try:
        import gspread
    except Exception as exc:  # noqa: BLE001
        problems.append(f"import gspread gagal: {exc!r}")
        gspread = None  # type: ignore[assignment]

    if gspread is not None:
        _check_signature(
            gspread.Spreadsheet.list_named_ranges,
            [],
            "gspread.Spreadsheet.list_named_ranges",
            problems,
        )
        _check_signature(gspread.Worksheet.append_row, ["values"], "gspread.Worksheet.append_row", problems)

    return problems


# ---------------------------------------------------------------- reporting


def _print_versions(printer: Callable[[str], None]) -> None:
    printer(f"Python: {platform.python_version()} ({platform.python_implementation()})")
    printer("Versi paket terpasang:")
    for distribution in TRACKED_DISTRIBUTIONS:
        printer(f"  - {distribution}: {installed_version(distribution) or 'TIDAK TERPASANG'}")


def run_environment_check(
    requirements_path: Optional[Path] = None,
    printer: Callable[[str], None] = print,
    checks: Callable[[], List[str]] = compatibility_problems,
) -> None:
    """Prints the environment report and raises EnvironmentCheckError when it is not usable."""
    _print_versions(printer)

    problems = list(checks())
    requirements_file = find_requirements_file() if requirements_path is None else Path(requirements_path)
    if requirements_file is not None and requirements_file.exists():
        pending = unsatisfied_requirements(parse_requirements(requirements_file))
        for requirement in pending:
            problems.append(
                f"{requirement.name} {requirement.specifier} belum terpasang "
                f"(terpasang: {installed_version(requirement.name) or 'tidak ada'})"
            )

    if problems:
        printer("")
        printer("[GAGAL] Lingkungan notebook belum siap:")
        for problem in problems:
            printer(f"  - {problem}")
        printer(INSTALL_HINT)
        raise EnvironmentCheckError(
            "Lingkungan notebook belum siap: " + "; ".join(problems) + ". " + INSTALL_HINT
        )

    printer("")
    printer("Environment siap: semua paket dan signature API sesuai.")


# ---------------------------------------------------------------- install


def _run_pip(command: Sequence[str]) -> Tuple[int, str]:
    completed = subprocess.run(list(command), capture_output=True, text=True)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return completed.returncode, output


def _install_one(
    requirement: Requirement,
    installed: Optional[str],
    run_pip: Callable[[Sequence[str]], Tuple[int, str]],
    printer: Callable[[str], None],
) -> None:
    command = [sys.executable, "-m", "pip", "install", "-q", requirement.spec()]
    if installed is not None:
        # Only a version below the minimum gets --upgrade; a missing package is a plain install.
        command.append("--upgrade")
    printer(f"Memasang {requirement.spec()} ...")
    returncode, output = run_pip(command)
    if returncode != 0:
        printer(f"[GAGAL] pip gagal memasang paket '{requirement.name}'.")
        printer(f"Perintah: {' '.join(command)}")
        if output:
            printer(output)
        raise PackageInstallError(
            f"pip gagal memasang paket '{requirement.name}' "
            f"(exit {returncode}). Perintah: {' '.join(command)}\n{output}"
        )


def ensure_environment(
    requirements_path: Optional[Path] = None,
    printer: Callable[[str], None] = print,
    version_getter: Callable[[str], Optional[str]] = installed_version,
    run_pip: Callable[[Sequence[str]], Tuple[int, str]] = _run_pip,
    checks: Callable[[], List[str]] = compatibility_problems,
) -> str:
    """
    Installs only the packages that are missing or below their minimum version.

    Returns "ok" when nothing had to be installed, "restart_required" after any
    install (the runtime must be restarted before the new versions are imported).
    Raises PackageInstallError when pip fails and EnvironmentCheckError when the
    installed packages still do not match the code.
    """
    requirements_file = find_requirements_file() if requirements_path is None else Path(requirements_path)
    requirements = parse_requirements(requirements_file) if requirements_file else []
    pending = unsatisfied_requirements(requirements, version_getter)

    if not pending:
        problems = list(checks())
        if problems:
            printer("[GAGAL] Dependensi terpasang tetapi tidak kompatibel:")
            for problem in problems:
                printer(f"  - {problem}")
            raise EnvironmentCheckError(
                "Dependensi terpasang tetapi tidak kompatibel: " + "; ".join(problems)
            )
        printer("Semua dependensi sudah terpasang dan kompatibel. Tidak ada yang dipasang.")
        return "ok"

    printer("Paket yang perlu dipasang: " + ", ".join(r.spec() for r in pending))
    for requirement in pending:
        _install_one(requirement, version_getter(requirement.name), run_pip, printer)

    # Packages installed in this interpreter may still be the old imported module,
    # so this check is informational: the restart below is what makes them active.
    problems = list(checks())
    if problems:
        printer("[CATATAN] Pemeriksaan ulang belum lulus di sesi ini:")
        for problem in problems:
            printer(f"  - {problem}")
    printer("")
    printer(RESTART_MESSAGE)
    return "restart_required"
