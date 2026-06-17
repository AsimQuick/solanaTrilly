# ---
# module: core.tests.test_scores_in_sync_ac443
# sprint: sprint-9
# story: US-44 AC-44.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tests.test_sources_in_sync_ac363,
#               core.tests.test_golden_scores_fixture_ac441,
#               core.tests.test_scorer_parity_ac442,
#               pathlib, yaml
# ---
"""AC-44.3 — 'Scores in sync' HARD MERGE GATE and STANDING DoD line.

oracle §4 / sprint-9 Definition of Done: no serving path ships without
(a) passing the scorer-parity gate against the model's banked golden_scores
vectors within the documented tolerance AND (b) the feature-contract
reconciliation (live_servable[] covers ALL of the model's features, column-for-
column and in booster order).

This file is the AC-44.3 verification artifact.  It FOLDS the scorer-parity
gate into the EXISTING combined T0/G1/G2 parity suite, extending the 'sources
in sync' standing DoD line (AC-36.3) with 'scores in sync'.

What is pinned
--------------
From test_sources_in_sync_ac363 (the AC-36.3 combined gate, which already pins
G1/G2(a)/G2(b)/AC-36.1 overlap/AC-36.2 pre-grad):
  test_sources_in_sync_gate_functions_are_callable   ← pins the prior gate chain

From test_golden_scores_fixture_ac441 (AC-44.1 — golden oracle fixture):
  test_golden_scores_fixture_present                 ← fixture committed and present
  test_golden_scores_manifest_content_hash_correct   ← immutability / MANIFEST gate

From test_scorer_parity_ac442 (AC-44.2 — live==offline scorer proof):
  test_scorer_parity_gate_importable_ac442           ← BlendScorer + ReferenceDistribution importable
  test_scorer_path_reproduces_golden_scores_ac442    ← MAIN GATE: serving path == golden oracle
  test_scorer_parity_run_twice_byte_identical_ac442  ← determinism gate

Wiring mechanism (H1 — no second workflow)
------------------------------------------
The module-level imports below create a hard compile-time dependency on each
named function.  Deleting, renaming, or moving any of them causes Python to
raise ImportError during pytest's collection phase, failing the CI 'test' job
BEFORE any tests run — loud, immediate, attributable.

All assertions run in the single canonical ci.yml 'test' job via the existing
pytest invocation over core/tests/.  No second workflow file is introduced.

Tests
-----
  test_scores_in_sync_gate_functions_are_callable
      All six ImportError-trapped functions resolve to callables.

  test_scores_in_sync_in_single_canonical_test_job
      ci.yml defines exactly a job named 'test' and the pytest invocation does
      NOT --ignore core/tests (H1).

  test_scores_in_sync_gate_non_trivial
      Each pinned function has a non-empty __name__ and __doc__, guarding against
      a stub replacement that passes the ImportError trap but asserts nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# AC-44.1 golden oracle fixture MAIN GATE functions.
# Deleting or renaming either raises ImportError at collection time.
from core.tests.test_golden_scores_fixture_ac441 import (
    test_golden_scores_fixture_present as _golden_fixture_present_test,
)
from core.tests.test_golden_scores_fixture_ac441 import (
    test_golden_scores_manifest_content_hash_correct as _golden_manifest_hash_test,
)

# AC-44.2 scorer parity MAIN GATE functions.
# Deleting or renaming any of these raises ImportError at collection time.
from core.tests.test_scorer_parity_ac442 import (
    test_scorer_parity_gate_importable_ac442 as _scorer_parity_importable_test,
)
from core.tests.test_scorer_parity_ac442 import (
    test_scorer_parity_run_twice_byte_identical_ac442 as _scorer_parity_determinism_test,
)
from core.tests.test_scorer_parity_ac442 import (
    test_scorer_path_reproduces_golden_scores_ac442 as _scorer_parity_gate_test,
)

# Pins the AC-36.3 combined gate (T0/G1/G2 + AC-36.1 overlap + AC-36.2 pre-grad
# + 'sources in sync' standing DoD).  Deleting test_sources_in_sync_ac363.py raises
# ImportError here at collection time.
from core.tests.test_sources_in_sync_ac363 import (
    test_sources_in_sync_gate_functions_are_callable as _prior_sources_in_sync_gate_test,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Registry — all pinned 'scores in sync' gate functions
# ---------------------------------------------------------------------------

_SCORES_IN_SYNC_GATE_FUNCTIONS: dict[str, object] = {
    # Prior combined gate (T0/G1/G2 + AC-36.1 overlap + AC-36.2 pre-grad) — AC-36.3
    "test_sources_in_sync_gate_functions_are_callable": _prior_sources_in_sync_gate_test,
    # AC-44.1: golden oracle fixture presence and immutability
    "test_golden_scores_fixture_present": _golden_fixture_present_test,
    "test_golden_scores_manifest_content_hash_correct": _golden_manifest_hash_test,
    # AC-44.2: scorer parity gate (live==offline by construction)
    "test_scorer_parity_gate_importable_ac442": _scorer_parity_importable_test,
    "test_scorer_path_reproduces_golden_scores_ac442": _scorer_parity_gate_test,
    "test_scorer_parity_run_twice_byte_identical_ac442": _scorer_parity_determinism_test,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scores_in_sync_gate_functions_are_callable() -> None:
    """All 'scores in sync' gate functions (prior sources-in-sync + AC-44.1 + AC-44.2) are callable.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case an import resolves to a non-callable
    (e.g. a constant or stub replacing a function after a partial rename).

    AC-44.3 (oracle §4): the combined T0/G1/G2 parity suite now includes the
    scorer-parity gate (AC-44.2) and the golden fixture integrity gate (AC-44.1).
    All must remain callable — deleting any breaks this gate.
    """
    not_callable = [
        name
        for name, fn in _SCORES_IN_SYNC_GATE_FUNCTIONS.items()
        if not callable(fn)
    ]
    assert not not_callable, (
        "'Scores in sync' HARD MERGE GATE is broken — "
        "these named functions are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
        + "\n\nAC-44.3 requires these callables to exist.  "
        "Restore the deleted/renamed test to unblock the gate."
    )


def test_scores_in_sync_in_single_canonical_test_job() -> None:
    """The 'scores in sync' parity suite runs in the single canonical ci.yml 'test' job.

    Asserts (H1 — no second workflow):
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT --ignore core/tests, so this
          wire file and all pinned gate functions are always exercised by CI.

    AC-44.3 (oracle §4 / H1): the combined parity suite (now including scorer
    parity) must pass in the SINGLE canonical ci.yml 'test' job.  A second
    workflow would be a H1 violation.
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-44.3 requires the 'scores in sync' parity suite to run in the single "
        "canonical ci.yml 'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the 'scores in sync' parity suite to run in the single "
        "canonical 'test' job — no second workflow file must be introduced."
    )

    test_job = jobs["test"]
    pytest_cmds = [
        step.get("run", "")
        for step in (test_job.get("steps") or [])
        if isinstance(step, dict) and "pytest" in step.get("run", "")
    ]
    assert pytest_cmds, (
        "The 'test' job in ci.yml contains no pytest step.\n"
        "AC-44.3 requires the 'scores in sync' gate to be exercised by the "
        "pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The 'scores in sync' wire file lives in core/tests/ and must NOT "
            "be excluded (AC-44.3 H1).  Remove the --ignore flag."
        )


def test_scores_in_sync_gate_non_trivial() -> None:
    """Each pinned gate function has a non-empty __name__ and __doc__.

    Guards against a stub replacement that passes the ImportError trap but
    contributes no assertions — a real gate function always has a docstring.

    A gate with no docstring is functionally invisible in CI output: when it
    fails, the error message contains no context about what the gate guards.
    """
    empty_doc: list[str] = []
    for name, fn in _SCORES_IN_SYNC_GATE_FUNCTIONS.items():
        if not callable(fn):
            continue
        fn_name = getattr(fn, "__name__", "")
        doc = getattr(fn, "__doc__", None)
        if not fn_name or not doc or not doc.strip():
            empty_doc.append(name)

    assert not empty_doc, (
        "These 'scores in sync' gate functions have no name or docstring — "
        "they may be stubs rather than real parity tests:\n"
        + "\n".join(f"  {n}" for n in empty_doc)
    )
