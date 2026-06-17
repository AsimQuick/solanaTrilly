# ---
# file: core/tests/test_ruff_gate_unforgeable_ac391.py
# project: solanatrilly
# purpose: AC-39.1 — verify the unforgeable ruff gate (auto-install, no manual step)
# story: US-39 AC-39.1
# sprint: sprint-8
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: none
# ---
"""AC-39.1 — unforgeable ruff gate: devcontainer auto-install + CI hook step.

Verifies:
  - Structural: ci.yml has the auto-install hook step (AC-39.1) and the Lint step
  - Structural: .devcontainer/devcontainer.json exists with postCreateCommand
  - Behavior: ruff correctly catches I001/E501/F401 violations on seeded files,
    then passes once fixed (mirrors AC-33.2 but confirms the unforgeable path)
"""

import json
import subprocess
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Structural tests — CI auto-install step
# ---------------------------------------------------------------------------


def test_ci_has_auto_install_hook_step():
    """ci.yml must have a step with '39.1' in the name."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml missing from .github/workflows/"

    with ci_path.open() as f:
        ci = yaml.safe_load(f)

    steps = ci["jobs"]["test"]["steps"]
    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    matching = [n for n in step_names if "39.1" in n]
    assert matching, (
        f"ci.yml must have a step containing '39.1' in its name; found steps: {step_names}"
    )


def test_ci_auto_install_step_runs_install_script():
    """The AC-39.1 step in ci.yml must run install-hooks.sh."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    with ci_path.open() as f:
        ci = yaml.safe_load(f)

    steps = ci["jobs"]["test"]["steps"]
    matching = [s for s in steps if isinstance(s, dict) and "39.1" in s.get("name", "")]
    assert matching, "No step with '39.1' in name found in ci.yml"

    step = matching[0]
    run_cmd = step.get("run", "")
    assert "install-hooks.sh" in run_cmd, (
        f"AC-39.1 step must run install-hooks.sh; got: {run_cmd!r}"
    )


def test_ci_has_guaranteed_ruff_check_step():
    """ci.yml must have the existing Lint step running 'ruff check .'."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    with ci_path.open() as f:
        ci = yaml.safe_load(f)

    steps = ci["jobs"]["test"]["steps"]
    lint_steps = [
        s for s in steps
        if isinstance(s, dict) and "ruff check" in s.get("run", "")
    ]
    assert lint_steps, "ci.yml must have a step that runs 'ruff check'"

    run_cmd = lint_steps[0].get("run", "")
    assert "ruff check ." in run_cmd, (
        f"Lint step must run 'ruff check .'; got: {run_cmd!r}"
    )


# ---------------------------------------------------------------------------
# Structural tests — devcontainer config
# ---------------------------------------------------------------------------


def test_devcontainer_config_exists():
    """.devcontainer/devcontainer.json must exist."""
    devcontainer = REPO_ROOT / ".devcontainer" / "devcontainer.json"
    assert devcontainer.exists(), (
        ".devcontainer/devcontainer.json missing — AC-39.1 requires it for auto-install"
    )


def test_devcontainer_has_post_create_command():
    """devcontainer.json must have postCreateCommand containing install-hooks.sh."""
    devcontainer_path = REPO_ROOT / ".devcontainer" / "devcontainer.json"
    assert devcontainer_path.exists(), ".devcontainer/devcontainer.json missing"

    # devcontainer.json is JSONC — strip comment lines before parsing
    raw_lines = devcontainer_path.read_text().splitlines()
    json_lines = [ln for ln in raw_lines if not ln.strip().startswith("//")]
    content = json.loads("\n".join(json_lines))

    post_create = content.get("postCreateCommand", "")
    assert post_create, "devcontainer.json must define 'postCreateCommand'"
    assert "install-hooks.sh" in post_create, (
        f"postCreateCommand must reference install-hooks.sh; got: {post_create!r}"
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ruff(path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run ruff check on a single file; returns CompletedProcess."""
    cmd = ["ruff", "check"] + (extra_args or []) + [str(path)]
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Behavior tests — seeded violations (mirrors AC-33.2, confirms unforgeable path)
# ---------------------------------------------------------------------------


def test_i001_violation_caught_and_fixed(tmp_path):
    """I001: unsorted imports → ruff fails; sorted → ruff passes."""
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
    """E501: line > 120 chars → ruff fails; within limit → ruff passes."""
    bad = tmp_path / "bad_e501.py"
    long_line = "x = " + "1" * 117  # 4 + 117 = 121 chars, over the 120-char limit
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
