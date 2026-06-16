# ---
# module: core.tests.test_p4_regression_wire_ac273
# sprint: sprint-6
# story: US-27 AC-27.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: pathlib, yaml, core.tests.test_snapshot_future_window_clamp_ac243,
#               core.tests.test_snapshot_idempotency_ac232,
#               core.tests.test_score_orchestration_ac252,
#               core.tests.test_three_unit_lock_ac261,
#               core.tests.test_unit_parity_ac262
# ---
"""AC-27.3 — P4 regression suite wired into the single canonical ci.yml 'test' job.

Wiring mechanism (mirrors AC-26.3 / AC-21.3)
---------------------------------------------
The module-level imports below create a hard compile-time dependency on the
specific test functions from each prior AC's test file.  If any wired function
is deleted or renamed, Python raises ImportError during pytest's collection
phase, failing the CI 'test' job BEFORE any test runs.

H1 note: no second workflow file is introduced.  All assertions run inside the
single canonical ci.yml 'test' job.

Wired tests
-----------
  AC-24.3 #380 clamp:
    test_future_window_is_clamped_to_clock_now

  AC-23.2 at-most-one:
    test_re_snapshot_same_mint_yields_one_row

  AC-25.2 idempotency:
    test_same_instance_retry_second_call_returns_none
    test_fresh_instance_restart_no_second_datasource_call

  AC-26.1 three-unit lock:
    test_all_three_unit_fields_present_and_nonzero
    test_three_unit_relationship_holds_for_recorded_swaps

  AC-26.2 unit parity:
    test_buy_fraction_is_byte_identical_in_sol_and_usd
    test_flow_ratio_is_byte_identical_in_sol_and_usd

Tests
-----
  test_p4_regression_test_files_exist
      Assert all 5 source files exist at their canonical paths.

  test_p4_regression_test_functions_are_callable
      Assert each wired import is callable.

  test_p4_regression_suite_in_single_canonical_test_job
      Read ci.yml; assert (a) job 'test' exists, (b) pytest does NOT
      --ignore core/tests.
"""
from pathlib import Path

import yaml

from core.tests.test_score_orchestration_ac252 import (
    test_fresh_instance_restart_no_second_datasource_call as _restart_ac252,
)
from core.tests.test_score_orchestration_ac252 import (
    test_same_instance_retry_second_call_returns_none as _retry_ac252,
)
from core.tests.test_snapshot_future_window_clamp_ac243 import (
    test_future_window_is_clamped_to_clock_now as _clamp_ac243,
)
from core.tests.test_snapshot_idempotency_ac232 import (
    test_re_snapshot_same_mint_yields_one_row as _idempotency_ac232,
)
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

# ImportError trap — compile-time wire for AC-24.3, AC-23.2, AC-25.2, AC-26.1, AC-26.2.
# Deleting or renaming any wired function raises ImportError at pytest collection time.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "core" / "tests"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_REQUIRED_FILES: dict[str, Path] = {
    "AC-24.3 clamp (test_snapshot_future_window_clamp_ac243.py)": (
        TESTS_DIR / "test_snapshot_future_window_clamp_ac243.py"
    ),
    "AC-23.2 idempotency (test_snapshot_idempotency_ac232.py)": (
        TESTS_DIR / "test_snapshot_idempotency_ac232.py"
    ),
    "AC-25.2 orchestration (test_score_orchestration_ac252.py)": (
        TESTS_DIR / "test_score_orchestration_ac252.py"
    ),
    "AC-26.1 three-unit lock (test_three_unit_lock_ac261.py)": (
        TESTS_DIR / "test_three_unit_lock_ac261.py"
    ),
    "AC-26.2 unit parity (test_unit_parity_ac262.py)": (
        TESTS_DIR / "test_unit_parity_ac262.py"
    ),
}

_REQUIRED_FUNCTIONS: dict[str, object] = {
    "AC-24.3 test_future_window_is_clamped_to_clock_now": _clamp_ac243,
    "AC-23.2 test_re_snapshot_same_mint_yields_one_row": _idempotency_ac232,
    "AC-25.2 test_same_instance_retry_second_call_returns_none": _retry_ac252,
    "AC-25.2 test_fresh_instance_restart_no_second_datasource_call": _restart_ac252,
    "AC-26.1 test_all_three_unit_fields_present_and_nonzero": _three_unit_presence,
    "AC-26.1 test_three_unit_relationship_holds_for_recorded_swaps": _three_unit_rel,
    "AC-26.2 test_buy_fraction_is_byte_identical_in_sol_and_usd": _buy_fraction_parity,
    "AC-26.2 test_flow_ratio_is_byte_identical_in_sol_and_usd": _flow_ratio_parity,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_p4_regression_test_files_exist() -> None:
    """Assert all 5 P4 regression source files exist at their canonical repo paths."""
    missing: list[str] = []
    for label, path in _REQUIRED_FILES.items():
        if not path.is_file():
            missing.append(f"  {label}\n    expected at: {path}")

    assert not missing, (
        "P4 regression test files are MISSING from the repository.\n"
        "These files are the CI wire for AC-27.3 and MUST NOT be deleted "
        "or moved without updating this suite.\n\n"
        "Missing:\n" + "\n".join(missing)
    )


def test_p4_regression_test_functions_are_callable() -> None:
    """Assert each wired P4 regression function is callable."""
    not_callable: list[str] = []
    for label, fn in _REQUIRED_FUNCTIONS.items():
        if not callable(fn):
            not_callable.append(f"  {label}: {fn!r}")

    assert not not_callable, (
        "One or more P4 regression test functions are not callable.\n"
        "AC-27.3 requires these specific callables to exist:\n\n"
        + "\n".join(not_callable)
    )


def test_p4_regression_suite_in_single_canonical_test_job() -> None:
    """Assert the P4 regression suite runs inside the single canonical ci.yml 'test' job.

    Checks two H1 sub-invariants (AC-27.3 / PRD §12 H1):
      (a) A job named 'test' exists in ci.yml.
      (b) The pytest invocation does NOT --ignore core/tests.
    """
    assert CI_YML.is_file(), f"ci.yml not found at {CI_YML} — cannot verify H1 wiring"

    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "AC-27.3 requires the P4 regression suite to run in the single canonical "
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
        "AC-27.3 requires the P4 regression suite to be exercised by the pytest "
        "invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "AC-27.3 requires the P4 regression suite (in core/tests/) to run in "
            "every CI invocation.  Remove the --ignore flag."
        )
