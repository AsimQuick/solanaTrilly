# ---
# module: core.tests.test_unit_parity_wire_ac263
# sprint: sprint-6
# story: US-26 AC-26.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: pathlib, yaml, core.tests.test_three_unit_lock_ac261,
#               core.tests.test_unit_parity_ac262
# ---
"""AC-26.3 — Unit-parity test suite wired into the single canonical ci.yml 'test' job.

Wiring mechanism (mirrors AC-21.3)
-----------------------------------
The module-level imports below create a hard compile-time dependency on the
specific test functions in the AC-26.1 and AC-26.2 test files.  Deleting or
renaming any of those functions causes Python to raise ImportError during pytest's
collection phase, failing the CI 'test' job BEFORE any test runs — loud,
immediate, and attributable.

H1 note: no second workflow file is introduced.  All assertions run inside the
single canonical ci.yml 'test' job.

Tests
-----
  test_unit_parity_test_files_exist
      Assert the AC-26.1 and AC-26.2 source files exist at their canonical paths.

  test_unit_parity_test_functions_are_callable
      Assert each wired import resolves to a callable.

  test_unit_parity_wired_in_single_canonical_test_job
      Read ci.yml; assert (a) exactly one job named 'test' exists and
      (b) the pytest invocation does not ignore core/tests.
"""
from pathlib import Path

import yaml

from core.tests.test_three_unit_lock_ac261 import (
    test_all_three_unit_fields_present_and_nonzero as _three_unit_presence,
)
from core.tests.test_three_unit_lock_ac261 import (
    test_three_unit_relationship_holds_for_recorded_swaps as _three_unit_rel,
)
from core.tests.test_unit_parity_ac262 import (
    test_buy_fraction_is_byte_identical_in_sol_and_usd as _buy_fraction_parity,
)
from core.tests.test_unit_parity_ac262 import (
    test_flow_ratio_is_byte_identical_in_sol_and_usd as _flow_ratio_parity,
)

# ImportError trap — compile-time wire for AC-26.1 and AC-26.2.
# If any of the functions above are deleted or renamed, Python raises ImportError
# during pytest collection, killing the CI 'test' job immediately.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR  = REPO_ROOT / "core" / "tests"
CI_YML     = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_REQUIRED_FILES: dict[str, Path] = {
    "AC-26.1 three-unit lock (test_three_unit_lock_ac261.py)": (
        TESTS_DIR / "test_three_unit_lock_ac261.py"
    ),
    "AC-26.2 unit parity (test_unit_parity_ac262.py)": (
        TESTS_DIR / "test_unit_parity_ac262.py"
    ),
}

_REQUIRED_FUNCTIONS: dict[str, object] = {
    "AC-26.1 test_all_three_unit_fields_present_and_nonzero": _three_unit_presence,
    "AC-26.1 test_three_unit_relationship_holds_for_recorded_swaps": _three_unit_rel,
    "AC-26.2 test_buy_fraction_is_byte_identical_in_sol_and_usd": _buy_fraction_parity,
    "AC-26.2 test_flow_ratio_is_byte_identical_in_sol_and_usd": _flow_ratio_parity,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unit_parity_test_files_exist() -> None:
    """Assert the AC-26.1 and AC-26.2 test files exist at their canonical repo paths.

    The module-level ImportError guard is the primary trip-wire; this test
    provides the human-readable backup with an explicit path in the failure.
    """
    missing: list[str] = []
    for label, path in _REQUIRED_FILES.items():
        if not path.is_file():
            missing.append(f"  {label}\n    expected at: {path}")

    assert not missing, (
        "Unit-parity test files are MISSING from the repository.\n"
        "These files are the CI wire for AC-26.3 and MUST NOT be deleted "
        "or moved without updating this suite.\n\n"
        "Missing:\n" + "\n".join(missing)
    )


def test_unit_parity_test_functions_are_callable() -> None:
    """Assert each wired test function is callable.

    The module-level imports guarantee the names exist; this test makes the
    callable contract explicit and catches the pathological case where an import
    name is replaced with a non-callable (e.g. a constant).
    """
    not_callable: list[str] = []
    for label, fn in _REQUIRED_FUNCTIONS.items():
        if not callable(fn):
            not_callable.append(f"  {label}: {fn!r}")

    assert not not_callable, (
        "One or more unit-parity test functions are not callable.\n"
        "AC-26.3 requires these specific callables to exist:\n\n"
        + "\n".join(not_callable)
    )


def test_unit_parity_wired_in_single_canonical_test_job() -> None:
    """Assert the unit-parity suite runs inside the single canonical ci.yml 'test' job.

    Checks two H1 sub-invariants (AC-26.3 / PRD §12 H1):
      (a) Exactly one job named 'test' exists in ci.yml.
      (b) The pytest invocation does NOT --ignore core/tests, so this suite
          and its pinned functions are always exercised.
    """
    assert CI_YML.is_file(), f"ci.yml not found at {CI_YML} — cannot verify H1 wiring"

    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "AC-26.3 requires the unit-parity suite to run in the single canonical "
        "'test' job (H1 — no second workflow)."
    )

    test_job = jobs["test"]
    steps = test_job.get("steps") or []

    pytest_cmds: list[str] = []
    for step in steps:
        if isinstance(step, dict):
            run_cmd = step.get("run", "")
            if isinstance(run_cmd, str) and "pytest" in run_cmd:
                pytest_cmds.append(run_cmd)

    assert pytest_cmds, (
        "The 'test' job in ci.yml contains no pytest step.\n"
        "AC-26.3 requires the unit-parity suite to be exercised by the pytest "
        "invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "AC-26.3 requires the unit-parity suite (in core/tests/) to run in "
            "every CI invocation.  Remove the --ignore flag."
        )
