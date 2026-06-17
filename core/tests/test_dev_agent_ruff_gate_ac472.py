# ---
# module: test_dev_agent_ruff_gate_ac472
# sprint: sprint-10
# story: US-47 AC-47.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: scripts/dev-agent-ruff-gate.sh, pyproject.toml, ruff
# ---
"""AC-47.2 — behavioral proof: the AI dev-agent ruff gate catches seeded violations.

AC-47.2 requires evidence — not assumption — that the mechanism catches the
exact recurring lint classes that caused the sprint-9 defects:

  * I001 — import-sort violation (isort ordering)
  * E501 — line-length violation (> 120 chars per pyproject.toml)
  * F401 — unused import

Each test seeds the violation into a temp Python file, invokes ruff with the
project's pyproject.toml config, and asserts:
  - violation → ruff exits non-zero AND prints the expected rule code
  - clean file → ruff exits 0

This is the "prove the mechanism fires" rigor demanded of G3/G4 and the
phase promoter: assumption replaced by evidence.

Scope: this module proves AC-47.2 only (behavioral seeded-violation proof).
The structural wiring proof is AC-47.1 (test_dev_agent_ruff_gate_ac471.py).
The silent-vanish anti-deletion guard is AC-47.3.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"

# Line-length limit from pyproject.toml — E501 fires above this.
_LINE_LENGTH = 120


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_ruff(file_path: Path) -> subprocess.CompletedProcess:
    """Run ruff check on a single file using the project's pyproject.toml config."""
    return subprocess.run(
        [
            "ruff",
            "check",
            "--config",
            str(PYPROJECT_TOML),
            str(file_path),
        ],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Sanity: pyproject.toml is present and ruff is available
# ---------------------------------------------------------------------------


def test_pyproject_toml_exists() -> None:
    """pyproject.toml must be present so ruff picks up the project config.

    AC-47.2: the mechanism runs ruff with the project config (line-length=120,
    select=E/F/W/I) — that config lives here.
    """
    assert PYPROJECT_TOML.exists(), (
        f"pyproject.toml not found at {PYPROJECT_TOML}.\n"
        "The ruff gate uses this file to determine line-length (120) and "
        "rule selection (E/F/W/I) for I001/E501/F401 detection."
    )


def test_ruff_is_available() -> None:
    """ruff must be available on the PATH inside the web container.

    AC-47.2: the mechanism invokes ruff; if ruff is absent, all behavioral
    tests below would be vacuously passing (exit 0 with no output).
    """
    result = subprocess.run(
        ["ruff", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"'ruff --version' failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}\n"
        "ruff must be installed in the web container (requirements.txt / pip)."
    )
    assert "ruff" in result.stdout.lower(), (
        f"Unexpected 'ruff --version' output: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# I001 — import-sort violation
# ---------------------------------------------------------------------------


def test_ruff_catches_i001_import_sort_violation(tmp_path: Path) -> None:
    """Seeded I001: imports out of alphabetical order — ruff must exit non-zero.

    'import sys' before 'import os' violates isort: alphabetical order
    requires os < sys.  ruff selects I001 via 'I' in pyproject.toml.

    AC-47.2: mechanism flags I001 (the sprint-9 defect class from AC-41.1 /
    AC-44.2) with a non-zero exit code.
    """
    # sys before os violates isort alphabetical ordering (os < sys)
    content = "import sys\nimport os\n\nx = 1\n"
    seeded = tmp_path / "seed_i001.py"
    seeded.write_text(content, encoding="utf-8")

    result = _run_ruff(seeded)

    assert result.returncode != 0, (
        "Expected ruff to exit non-zero for I001 (import-sort violation).\n"
        f"File content:\n{content}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "I001" in result.stdout, (
        "Expected 'I001' in ruff output for import-sort violation.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        "Check that pyproject.toml selects 'I' rules and isort is active."
    )


# ---------------------------------------------------------------------------
# E501 — line-length violation
# ---------------------------------------------------------------------------


def test_ruff_catches_e501_line_length_violation(tmp_path: Path) -> None:
    """Seeded E501: line > 120 chars — ruff must exit non-zero.

    pyproject.toml sets line-length = 120.  A line of 130 chars triggers E501.
    ruff selects E501 via 'E' in pyproject.toml.

    AC-47.2: mechanism flags E501 on the AI-agent path.
    """
    # Build a line that is clearly over 120 chars (130 chars of content).
    padding = "a" * (_LINE_LENGTH + 10)  # 130 'a' chars → total line > 120
    content = f'x = "{padding}"\n'
    assert len(content.rstrip("\n")) > _LINE_LENGTH, (
        f"Test setup error: seeded line is not longer than {_LINE_LENGTH} chars"
    )

    seeded = tmp_path / "seed_e501.py"
    seeded.write_text(content, encoding="utf-8")

    result = _run_ruff(seeded)

    assert result.returncode != 0, (
        "Expected ruff to exit non-zero for E501 (line-length violation).\n"
        f"Line length: {len(content.rstrip())} chars (limit: {_LINE_LENGTH})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "E501" in result.stdout, (
        "Expected 'E501' in ruff output for line-length violation.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        f"Check that pyproject.toml line-length = {_LINE_LENGTH} is in effect "
        "and that 'E' rules are selected."
    )


# ---------------------------------------------------------------------------
# F401 — unused import
# ---------------------------------------------------------------------------


def test_ruff_catches_f401_unused_import(tmp_path: Path) -> None:
    """Seeded F401: import present but never used — ruff must exit non-zero.

    'import os' imported but not referenced triggers F401.  ruff selects
    F401 via 'F' in pyproject.toml.

    AC-47.2: mechanism flags F401 (the sprint-9 defect class from AC-40.1 /
    AC-42.3) with a non-zero exit code.
    """
    content = "import os\n\nx = 1\n"
    seeded = tmp_path / "seed_f401.py"
    seeded.write_text(content, encoding="utf-8")

    result = _run_ruff(seeded)

    assert result.returncode != 0, (
        "Expected ruff to exit non-zero for F401 (unused import).\n"
        f"File content:\n{content}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "F401" in result.stdout, (
        "Expected 'F401' in ruff output for unused import.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        "Check that pyproject.toml selects 'F' rules."
    )


# ---------------------------------------------------------------------------
# Clean file passes
# ---------------------------------------------------------------------------


def test_ruff_passes_clean_file(tmp_path: Path) -> None:
    """A file with no violations must make ruff exit 0.

    Proves the mechanism does NOT false-positive on valid code — the gate
    allows clean commits through.

    AC-47.2: 'passes once fixed' — the clean-file case.
    """
    content = "x = 1\n"
    clean = tmp_path / "seed_clean.py"
    clean.write_text(content, encoding="utf-8")

    result = _run_ruff(clean)

    assert result.returncode == 0, (
        "Expected ruff to exit 0 for a clean file with no violations.\n"
        f"File content:\n{content}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# All three violation classes in one file — gate catches them together
# ---------------------------------------------------------------------------


def test_ruff_catches_all_three_violation_classes_together(tmp_path: Path) -> None:
    """A file seeding I001 + E501 + F401 simultaneously — gate flags all.

    Proves the mechanism handles co-occurring violations (the realistic case
    where an AI agent introduces multiple lint classes in a single commit).

    AC-47.2: all three recurring lint classes caught by a single ruff run.
    """
    padding = "b" * (_LINE_LENGTH + 10)  # E501: line > 120 chars
    content = (
        # I001: sys before os violates isort
        "import sys\n"
        "import os\n"  # F401: os never used; sys never used either
        "\n"
        f'x = "{padding}"\n'  # E501: line too long
    )
    seeded = tmp_path / "seed_all_three.py"
    seeded.write_text(content, encoding="utf-8")

    result = _run_ruff(seeded)

    assert result.returncode != 0, (
        "Expected ruff to exit non-zero for combined I001+E501+F401 violations.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # All three rule codes must appear in the output.
    missing = [code for code in ("I001", "E501", "F401") if code not in result.stdout]
    assert not missing, (
        f"Missing rule code(s) in ruff output: {missing}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        "All three violation classes (I001, E501, F401) must be flagged."
    )


# ---------------------------------------------------------------------------
# Fixed file passes — 'passes once fixed' (AC-47.2 explicit requirement)
# ---------------------------------------------------------------------------


def test_ruff_passes_once_i001_is_fixed(tmp_path: Path) -> None:
    """Fix an I001 violation → ruff exits 0.

    Proves 'passes once fixed' for the import-sort case: reorder os before
    sys and ruff no longer objects.

    AC-47.2: 'passes once fixed' requirement for I001.
    """
    # Fixed: os before sys (alphabetical)
    content = "import os\nimport sys\n\nx = os.getcwd()\ny = sys.version\n"
    fixed = tmp_path / "fixed_i001.py"
    fixed.write_text(content, encoding="utf-8")

    result = _run_ruff(fixed)

    assert result.returncode == 0, (
        "Expected ruff to exit 0 after fixing I001 (os before sys, both used).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_ruff_passes_once_f401_is_fixed(tmp_path: Path) -> None:
    """Fix an F401 violation → ruff exits 0.

    Proves 'passes once fixed' for the unused-import case: actually use the
    imported name so ruff no longer reports F401.

    AC-47.2: 'passes once fixed' requirement for F401.
    """
    # Fixed: os is actually used
    content = "import os\n\nx = os.getcwd()\n"
    fixed = tmp_path / "fixed_f401.py"
    fixed.write_text(content, encoding="utf-8")

    result = _run_ruff(fixed)

    assert result.returncode == 0, (
        "Expected ruff to exit 0 after fixing F401 (import now used).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_ruff_passes_once_e501_is_fixed(tmp_path: Path) -> None:
    """Fix an E501 violation → ruff exits 0.

    Proves 'passes once fixed' for the line-length case: shorten the line
    to <= 120 chars so ruff no longer reports E501.

    AC-47.2: 'passes once fixed' requirement for E501.
    """
    # Fixed: line well within 120 chars
    content = 'x = "short"\n'
    assert len(content.rstrip("\n")) <= _LINE_LENGTH, (
        f"Test setup error: 'fixed' line is still too long ({len(content.rstrip())} chars)"
    )
    fixed = tmp_path / "fixed_e501.py"
    fixed.write_text(content, encoding="utf-8")

    result = _run_ruff(fixed)

    assert result.returncode == 0, (
        "Expected ruff to exit 0 after fixing E501 (line now within 120 chars).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
