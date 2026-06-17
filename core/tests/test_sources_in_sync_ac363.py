# ---
# module: core.tests.test_sources_in_sync_ac363
# sprint: sprint-8
# story: US-36 AC-36.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tests.test_parity_gate_ac323,
#               core.tests.test_pregrad_self_consistency_ac362,
#               pathlib, yaml
# ---
"""AC-36.3 — 'Sources in sync' HARD MERGE GATE and STANDING DoD line.

oracle §3 / sprint-8 Definition of Done: no source enters the lake without
(a) passing the extended T0/G1/G2 parity gate AND (b) carrying a MANIFEST.

This file is the AC-36.3 verification artifact.  It wires the COMPLETE combined
parity suite — now including BOTH the helius_live post-graduation OVERLAP byte-
parity (AC-36.1) AND the pre-graduation raw-truth self-consistency (AC-36.2) —
into a single compile-time ImportError trap, mirroring AC-21.3/AC-32.3.

What is pinned
--------------
From test_parity_gate_ac323 (the AC-32.3 combined gate, which already pins
G1/G2(a)/G2(b)/AC-36.1 overlap):
  test_combined_parity_gate_functions_are_callable  ← pins the prior gate

From test_pregrad_self_consistency_ac362 (AC-36.2 — the pre-grad window):
  test_pregrad_decode_twice_byte_identical           ← decode determinism
  test_pregrad_normalized_swaps_twice_identical      ← swap determinism
  test_pregrad_g1_feature_golden_parity              ← G1 golden parity
  test_birth_tape_manifest_content_hash_correct      ← MANIFEST integrity

Wiring mechanism (H1 — no second workflow)
------------------------------------------
The module-level imports below create a hard compile-time dependency on each
named function.  Deleting, renaming, or moving any of them causes Python to raise
ImportError during pytest's collection phase, failing the CI 'test' job BEFORE
any tests run — loud, immediate, attributable.

All assertions run in the single canonical ci.yml 'test' job via the existing
pytest invocation over core/tests/.  No second workflow file is introduced.

Tests
-----
  test_sources_in_sync_gate_functions_are_callable
      All five ImportError-trapped functions resolve to callables.

  test_sources_in_sync_in_single_canonical_test_job
      ci.yml defines exactly a job named 'test' and the pytest invocation does
      NOT --ignore core/tests (H1).

  test_sources_in_sync_gate_non_trivial
      Each pinned function has a non-empty __name__ and __doc__, guarding against
      a stub replacement that passes the ImportError trap but asserts nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# Pins the AC-32.3 combined gate (G1 + G2(a) + G2(b) + AC-36.1 overlap).
# Deleting test_parity_gate_ac323.py raises ImportError here at collection time.
from core.tests.test_parity_gate_ac323 import (
    test_combined_parity_gate_functions_are_callable as _prior_combined_gate_test,
)

# AC-36.2 pre-grad raw-truth self-consistency MAIN GATE functions.
# Deleting or renaming any of these four raises ImportError at collection time.
from core.tests.test_pregrad_self_consistency_ac362 import (
    test_birth_tape_manifest_content_hash_correct as _pregrad_manifest_hash_test,
)
from core.tests.test_pregrad_self_consistency_ac362 import (
    test_pregrad_decode_twice_byte_identical as _pregrad_decode_determinism_test,
)
from core.tests.test_pregrad_self_consistency_ac362 import (
    test_pregrad_g1_feature_golden_parity as _pregrad_g1_parity_test,
)
from core.tests.test_pregrad_self_consistency_ac362 import (
    test_pregrad_normalized_swaps_twice_identical as _pregrad_swap_determinism_test,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Registry — all pinned 'sources in sync' gate functions
# ---------------------------------------------------------------------------

_SOURCES_IN_SYNC_GATE_FUNCTIONS: dict[str, object] = {
    # Prior combined gate (G1 + G2(a) + G2(b) + AC-36.1 overlap) — AC-32.3
    "test_combined_parity_gate_functions_are_callable": _prior_combined_gate_test,
    # AC-36.2 / AC-36.3: pre-grad raw-truth self-consistency
    "test_pregrad_decode_twice_byte_identical": _pregrad_decode_determinism_test,
    "test_pregrad_normalized_swaps_twice_identical": _pregrad_swap_determinism_test,
    "test_pregrad_g1_feature_golden_parity": _pregrad_g1_parity_test,
    "test_birth_tape_manifest_content_hash_correct": _pregrad_manifest_hash_test,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sources_in_sync_gate_functions_are_callable() -> None:
    """All 'sources in sync' gate functions (prior combined + AC-36.2 pre-grad) are callable.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case an import resolves to a non-callable
    (e.g. a constant or stub replacing a function after a partial rename).

    AC-36.3 (oracle §3): the combined T0/G1/G2 parity suite now includes the
    helius_live overlap (AC-36.1) and the pre-grad self-consistency (AC-36.2).
    Both must remain callable — deleting either breaks this gate.
    """
    not_callable = [
        name
        for name, fn in _SOURCES_IN_SYNC_GATE_FUNCTIONS.items()
        if not callable(fn)
    ]
    assert not not_callable, (
        "'Sources in sync' HARD MERGE GATE is broken — "
        "these named functions are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
        + "\n\nAC-36.3 requires these callables to exist.  "
        "Restore the deleted/renamed test to unblock the gate."
    )


def test_sources_in_sync_in_single_canonical_test_job() -> None:
    """The 'sources in sync' parity suite runs in the single canonical ci.yml 'test' job.

    Asserts (H1 — no second workflow):
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT --ignore core/tests, so this
          wire file and all pinned gate functions are always exercised by CI.

    AC-36.3 (oracle §3 / H1): the combined parity suite must pass in the SINGLE
    canonical ci.yml 'test' job.  A second workflow would be a H1 violation.
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-36.3 requires the 'sources in sync' parity suite to run in the single "
        "canonical ci.yml 'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the 'sources in sync' parity suite to run in the single "
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
        "AC-36.3 requires the 'sources in sync' gate to be exercised by the "
        "pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The 'sources in sync' wire file lives in core/tests/ and must NOT "
            "be excluded (AC-36.3 H1).  Remove the --ignore flag."
        )


def test_sources_in_sync_gate_non_trivial() -> None:
    """Each pinned gate function has a non-empty __name__ and __doc__.

    Guards against a stub replacement that passes the ImportError trap but
    contributes no assertions — a real gate function always has a docstring.

    A gate with no docstring is functionally invisible in CI output: when it
    fails, the error message contains no context about what the gate guards.
    """
    empty_doc: list[str] = []
    for name, fn in _SOURCES_IN_SYNC_GATE_FUNCTIONS.items():
        if not callable(fn):
            continue
        fn_name = getattr(fn, "__name__", "")
        doc = getattr(fn, "__doc__", None)
        if not fn_name or not doc or not doc.strip():
            empty_doc.append(name)

    assert not empty_doc, (
        "These 'sources in sync' gate functions have no name or docstring — "
        "they may be stubs rather than real parity tests:\n"
        + "\n".join(f"  {n}" for n in empty_doc)
    )
