# ---
# file: core/tests/test_import_smoke_ac711.py
# project: solanatrilly
# purpose: Tests for pre-deploy in-container import smoke (AC-71.1).
#          Validates: symbol data file structure, smoke command generation,
#          subprocess failure on bad imports, Django setup guard, and CI YAML
#          placement of the smoke step.
# story: US-71 AC-71.1
# sprint: sprint-14
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: tools/run_import_smoke.py, ops/import_smoke_symbols.json
# ---

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # PyYAML is available in the Django image

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent
_SYMBOLS_FILE = _REPO_ROOT / "ops" / "import_smoke_symbols.json"
_SMOKE_SCRIPT = _REPO_ROOT / "tools" / "run_import_smoke.py"
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Ground-truth AC-68.3 check set
# ---------------------------------------------------------------------------
AC_683_SYMBOLS: set[tuple[str, str]] = {
    ("trading.execution_core", "ExecutionCore"),
    ("trading.tape_settler", "simulate_tape_exit"),
    ("trading.exit_engine", "evaluate_exit_rules"),
    ("trading.position_closer", "settle_paper_position"),
    ("copytrade.position_opener", "open_live_position"),
}

# ---------------------------------------------------------------------------
# Helper: import the tool module without relying on installed packages
# ---------------------------------------------------------------------------


def _import_tool():
    """Import tools.run_import_smoke, adding repo root to sys.path if needed."""
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import importlib

    return importlib.import_module("tools.run_import_smoke")


# ---------------------------------------------------------------------------
# Structural tests — file existence and JSON validity
# ---------------------------------------------------------------------------


def test_symbols_file_exists():
    assert _SYMBOLS_FILE.exists(), f"Missing: {_SYMBOLS_FILE}"


def test_symbols_file_is_valid_json():
    with _SYMBOLS_FILE.open() as fh:
        data = json.load(fh)
    assert "symbols" in data, "ops/import_smoke_symbols.json must have a 'symbols' key"


def test_each_symbol_has_module_and_attr():
    with _SYMBOLS_FILE.open() as fh:
        data = json.load(fh)
    for sym in data["symbols"]:
        assert "module" in sym, f"Symbol entry missing 'module': {sym}"
        assert "attr" in sym, f"Symbol entry missing 'attr': {sym}"


def test_run_import_smoke_script_exists():
    assert _SMOKE_SCRIPT.exists(), f"Missing: {_SMOKE_SCRIPT}"


# ---------------------------------------------------------------------------
# AC-71.1 (c): symbol list matches AC-68.3 check set
# ---------------------------------------------------------------------------


def test_symbol_list_matches_ac683_check_set():
    with _SYMBOLS_FILE.open() as fh:
        data = json.load(fh)
    actual = {(s["module"], s["attr"]) for s in data["symbols"]}
    assert actual == AC_683_SYMBOLS, (
        f"Symbol list does not match AC-68.3 check set.\n"
        f"  Expected: {sorted(AC_683_SYMBOLS)}\n"
        f"  Got:      {sorted(actual)}"
    )


# ---------------------------------------------------------------------------
# Smoke command tests
# ---------------------------------------------------------------------------


def test_smoke_command_starts_with_django_setup():
    tool = _import_tool()
    # Use a minimal one-entry symbol list for this structural check
    cmd = tool.build_smoke_command([{"module": "os", "attr": "path"}])
    assert cmd.startswith("import django; django.setup()"), (
        "Smoke command must start with 'import django; django.setup()'"
    )


# ---------------------------------------------------------------------------
# AC-71.1 (a): bad symbol → non-zero exit
# ---------------------------------------------------------------------------


def test_unimportable_symbol_fails_smoke():
    tool = _import_tool()
    cmd = tool.build_smoke_command(
        [{"module": "no_such_module_ac711_planted", "attr": "BadSymbol"}]
    )
    result = subprocess.run(
        [sys.executable, "-c", cmd],
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit when importing a non-existent module, got 0"
    )


# ---------------------------------------------------------------------------
# AC-71.1 (b-1): model defined at import time WITHOUT django.setup() → fails
# ---------------------------------------------------------------------------


def test_import_time_model_fails_without_django_setup():
    # Import a real Django model (User is defined at module level using ModelBase
    # metaclass).  Without django.setup() the app registry is not populated, so
    # ModelBase.__new__ raises AppRegistryNotReady when the module is imported.
    # Using an existing model import avoids the compound-statement-after-semicolon
    # SyntaxError that would occur with an inline class definition.
    code = "from django.contrib.auth.models import User"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit when importing a Django model without django.setup() "
        "(AppRegistryNotReady — the sprint-13 failure class), got 0. "
        "Ensure DJANGO_SETTINGS_MODULE is set in the container environment."
    )


# ---------------------------------------------------------------------------
# AC-71.1 (b-2): model defined at import time WITH django.setup() → passes
# ---------------------------------------------------------------------------


def test_import_time_model_passes_with_django_setup():
    # Same import, but now django.setup() is called first.  DJANGO_SETTINGS_MODULE
    # is inherited from the container environment (config.settings), so setup()
    # populates the app registry and the import succeeds.
    code = "import django; django.setup(); from django.contrib.auth.models import User"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
    )
    assert result.returncode == 0, (
        "Expected exit 0 when importing a Django model WITH django.setup(), got "
        f"{result.returncode}\nstderr: {result.stderr.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# CI YAML placement tests
# ---------------------------------------------------------------------------


def _load_ci_steps() -> list[dict]:
    """Return the list of steps from the CI 'test' job."""
    with _CI_YML.open() as fh:
        ci = yaml.safe_load(fh)
    return ci["jobs"]["test"]["steps"]


def test_smoke_step_in_ci_yml():
    steps = _load_ci_steps()
    names = [s.get("name", "") for s in steps]
    assert any("Pre-deploy built-image import smoke (AC-71.1)" in n for n in names), (
        "CI YAML is missing the 'Pre-deploy built-image import smoke (AC-71.1)' step"
    )


def test_smoke_step_runs_run_import_smoke_script():
    steps = _load_ci_steps()
    for step in steps:
        if "Pre-deploy built-image import smoke (AC-71.1)" in step.get("name", ""):
            assert "run_import_smoke.py" in step.get("run", ""), (
                "Smoke step must invoke run_import_smoke.py"
            )
            return
    pytest.fail("Smoke step not found in CI YAML")


def test_smoke_step_after_build_containers():
    steps = _load_ci_steps()
    names = [s.get("name", "") for s in steps]

    build_idx = next(
        (i for i, n in enumerate(names) if "Build containers" in n), None
    )
    smoke_idx = next(
        (i for i, n in enumerate(names) if "Pre-deploy built-image import smoke" in n),
        None,
    )
    assert build_idx is not None, "'Build containers' step not found in CI YAML"
    assert smoke_idx is not None, "Smoke step not found in CI YAML"
    assert smoke_idx > build_idx, (
        f"Smoke step (index {smoke_idx}) must come after 'Build containers' "
        f"(index {build_idx})"
    )
