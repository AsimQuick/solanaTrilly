# ---
# file: core/tests/test_ruff_gate_ac332.py
# project: solanatrilly
# purpose: AC-33.2 — verify the mechanized ruff gate (I001/E501/F401)
# story: US-33 AC-33.2
# sprint: sprint-5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: none
# ---
"""AC-33.2 — ruff gate mechanized: pre-commit hook + Makefile lint target.

Verifies the gate exists and correctly catches I001/E501/F401 violations on
a seeded temp file, then passes once the violation is fixed.
"""

import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Artifact existence checks
# ---------------------------------------------------------------------------


def test_makefile_exists():
    assert (REPO_ROOT / "Makefile").exists(), "Makefile missing from repo root"


def test_makefile_has_lint_target():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "lint:" in makefile, "Makefile must define a 'lint:' target"


def test_makefile_lint_target_references_ruff():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "ruff check" in makefile, "Makefile lint target must call 'ruff check'"


def test_pre_commit_hook_script_exists():
    hook = REPO_ROOT / "scripts" / "pre-commit.sh"
    assert hook.exists(), "scripts/pre-commit.sh missing"


def test_pre_commit_hook_script_references_ruff():
    hook = (REPO_ROOT / "scripts" / "pre-commit.sh").read_text()
    assert "ruff check" in hook, "pre-commit.sh must call 'ruff check'"


def test_install_hooks_script_exists():
    installer = REPO_ROOT / "scripts" / "install-hooks.sh"
    assert installer.exists(), "scripts/install-hooks.sh missing"


def test_install_hooks_script_references_pre_commit():
    installer = (REPO_ROOT / "scripts" / "install-hooks.sh").read_text()
    assert "pre-commit.sh" in installer, "install-hooks.sh must copy pre-commit.sh"


# ---------------------------------------------------------------------------
# Seeded-violation tests: ruff fails on violation, passes once fixed
# ---------------------------------------------------------------------------


def _ruff(path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run ruff check on a single file; returns CompletedProcess."""
    cmd = ["ruff", "check"] + (extra_args or []) + [str(path)]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )


def test_i001_violation_caught_and_fixed(tmp_path):
    """I001: unsorted imports → ruff fails; sorted → ruff passes.

    Uses --select I001 to isolate import-ordering check from F401 (unused
    imports) — the fixture files are minimal and don't use the imports.
    """
    bad = tmp_path / "bad_i001.py"
    bad.write_text(
        textwrap.dedent("""\
            import os
            import json
            import abc
        """)
    )
    result = _ruff(bad, extra_args=["--select", "I001"])
    assert result.returncode != 0, "ruff should reject unsorted imports (I001)"
    assert "I001" in result.stdout or "I001" in result.stderr

    good = tmp_path / "good_i001.py"
    good.write_text(
        textwrap.dedent("""\
            import abc
            import json
            import os
        """)
    )
    result = _ruff(good, extra_args=["--select", "I001"])
    assert result.returncode == 0, f"ruff should accept sorted imports; stderr={result.stderr}"


def test_e501_violation_caught_and_fixed(tmp_path):
    """E501: line too long → ruff fails; within limit → ruff passes."""
    bad = tmp_path / "bad_e501.py"
    # 121 chars of content (limit is 120 per pyproject.toml), but ruff check on
    # a standalone file won't load pyproject.toml from REPO_ROOT automatically.
    # We use --config to point at pyproject.toml so the same limit applies.
    long_line = "x = " + "1" * 117  # 4 + 117 = 121 chars, over 120
    bad.write_text(long_line + "\n")

    result = subprocess.run(
        ["ruff", "check", "--config", str(REPO_ROOT / "pyproject.toml"), str(bad)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "ruff should reject lines > 120 chars (E501)"
    assert "E501" in result.stdout or "E501" in result.stderr

    good = tmp_path / "good_e501.py"
    good.write_text("x = 1\n")
    result = subprocess.run(
        ["ruff", "check", "--config", str(REPO_ROOT / "pyproject.toml"), str(good)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ruff should accept short lines; stderr={result.stderr}"


def test_f401_violation_caught_and_fixed(tmp_path):
    """F401: unused import → ruff fails; remove import → ruff passes."""
    bad = tmp_path / "bad_f401.py"
    bad.write_text(
        textwrap.dedent("""\
            import os


            def hello():
                return "hello"
        """)
    )
    result = _ruff(bad)
    assert result.returncode != 0, "ruff should reject unused imports (F401)"
    assert "F401" in result.stdout or "F401" in result.stderr

    good = tmp_path / "good_f401.py"
    good.write_text(
        textwrap.dedent("""\
            def hello():
                return "hello"
        """)
    )
    result = _ruff(good)
    assert result.returncode == 0, f"ruff should accept file without unused imports; stderr={result.stderr}"


def test_clean_file_passes_ruff(tmp_path):
    """A clean file with no violations passes ruff."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        textwrap.dedent("""\
            import abc
            import os


            def greet(name: str) -> str:
                return "hello " + os.path.basename(name) + str(abc.ABC)
        """)
    )
    result = _ruff(clean)
    assert result.returncode == 0, f"Clean file should pass ruff; stderr={result.stderr}"
