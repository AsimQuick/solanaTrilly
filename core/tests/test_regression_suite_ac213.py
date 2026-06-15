# ---
# module: core.tests.test_regression_suite_ac213
# sprint: sprint-5
# story: US-21 AC-21.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: pathlib, yaml, core.tests.test_lake_reader_ac193,
#               core.tests.test_tape_recorder_ac183, core.tests.test_tape_recorder_ac184
# ---
"""AC-21.3 — Recorder regression suite wire.

Wires the three canonical recorder regression tests so they CANNOT silently
vanish from CI:

  • AC-18.3 stable-ordering — test_within_second_out_of_order_ordered_by_slot
    in core/tests/test_tape_recorder_ac183.py

  • AC-18.4 zero/degenerate-swap guard — test_degenerate_policy_is_skip
    in core/tests/test_tape_recorder_ac184.py

  • AC-19.3 truncated-tail recovery — test_truncated_tail_returns_complete_rows_no_exception
    in core/tests/test_lake_reader_ac193.py

Wiring mechanism
----------------
The module-level imports below create a hard compile-time dependency on each
function by its exact name.  Deleting or renaming any of the three regression
tests causes Python to raise ImportError during pytest's collection phase,
failing the CI 'test' job BEFORE any tests run — loud, immediate, attributable.

The filesystem assertions and callable checks below add a second,
human-readable layer: if a file is moved without triggering an ImportError,
the path checks catch it.

H1 note: no second workflow file is introduced.  All assertions run inside the
single canonical ci.yml 'test' job (H1 — no second workflow).

Tests
-----
  test_regression_test_files_exist
      Asserts the three source files are present at their canonical repo paths.

  test_regression_test_functions_are_callable
      Asserts each wired import resolves to a callable (the ImportError guard
      already enforces existence; this adds a human-readable assertion layer).

  test_regression_suite_in_single_canonical_test_job
      Reads ci.yml and asserts: (a) exactly one job named 'test' exists, and
      (b) the pytest invocation does not ignore core/tests so this suite and
      its three pinned tests are always exercised.
"""
from pathlib import Path

import yaml

from core.tests.test_lake_reader_ac193 import (
    test_truncated_tail_returns_complete_rows_no_exception as _truncated_tail_test,
)
from core.tests.test_tape_recorder_ac183 import (
    test_within_second_out_of_order_ordered_by_slot as _stable_ordering_test,
)
from core.tests.test_tape_recorder_ac184 import (
    test_degenerate_policy_is_skip as _zero_guard_policy_test,
)
from core.tests.test_tape_recorder_ac184 import (
    test_zero_price_swap_skipped_no_exception as _zero_guard_runtime_test,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "core" / "tests"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REQUIRED_FILES: dict[str, Path] = {
    "AC-18.3 stable-ordering (test_tape_recorder_ac183.py)": (
        TESTS_DIR / "test_tape_recorder_ac183.py"
    ),
    "AC-18.4 zero-guard (test_tape_recorder_ac184.py)": (
        TESTS_DIR / "test_tape_recorder_ac184.py"
    ),
    "AC-19.3 truncated-tail (test_lake_reader_ac193.py)": (
        TESTS_DIR / "test_lake_reader_ac193.py"
    ),
}

_REQUIRED_FUNCTIONS: dict[str, object] = {
    "AC-18.3 test_within_second_out_of_order_ordered_by_slot": _stable_ordering_test,
    "AC-18.4 test_degenerate_policy_is_skip": _zero_guard_policy_test,
    "AC-18.4 test_zero_price_swap_skipped_no_exception": _zero_guard_runtime_test,
    "AC-19.3 test_truncated_tail_returns_complete_rows_no_exception": _truncated_tail_test,
}

# ---------------------------------------------------------------------------
# Wire tests
# ---------------------------------------------------------------------------


def test_regression_test_files_exist() -> None:
    """Assert the three regression test files exist at their canonical paths.

    The module-level ImportError guard is the primary trip-wire; this test
    provides the human-readable backup with an explicit path in the failure.
    """
    missing: list[str] = []
    for label, path in _REQUIRED_FILES.items():
        if not path.is_file():
            missing.append(f"  {label}\n    expected at: {path}")

    assert not missing, (
        "Recorder regression suite files are MISSING from the repository.\n"
        "These files are the CI wire for AC-21.3 and MUST NOT be deleted "
        "or moved without updating this suite.\n\n"
        "Missing:\n" + "\n".join(missing)
    )


def test_regression_test_functions_are_callable() -> None:
    """Assert each wired regression function is callable.

    The module-level imports guarantee the names exist; this test makes
    the callable contract explicit and catches the pathological case where
    an import name is replaced with a non-callable (e.g. a constant).
    """
    not_callable: list[str] = []
    for label, fn in _REQUIRED_FUNCTIONS.items():
        if not callable(fn):
            not_callable.append(f"  {label}: {fn!r}")

    assert not not_callable, (
        "One or more recorder regression test functions are not callable.\n"
        "AC-21.3 requires these specific callables to exist:\n\n"
        + "\n".join(not_callable)
    )


def test_regression_suite_in_single_canonical_test_job() -> None:
    """Assert the regression suite runs inside the single canonical ci.yml 'test' job.

    Checks two H1 sub-invariants (AC-21.3 / PRD §12 H1):
      (a) Exactly one job named 'test' exists in ci.yml.
      (b) The pytest invocation does NOT --ignore core/tests, so this suite
          and its three pinned tests are always exercised.
    """
    assert CI_YML.is_file(), f"ci.yml not found at {CI_YML} — cannot verify H1 wiring"

    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "AC-21.3 requires the recorder regression suite to run in the single "
        "canonical 'test' job (H1 — no second workflow)."
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
        "AC-21.3 requires the recorder regression suite to be exercised by "
        "the pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "AC-21.3 requires the regression suite (in core/tests/) to run "
            "in every CI invocation.  Remove the --ignore flag."
        )
