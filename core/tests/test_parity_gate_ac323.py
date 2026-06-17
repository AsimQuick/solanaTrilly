# ---
# module: core.tests.test_parity_gate_ac323
# sprint: sprint-8
# story: US-32 AC-32.3, US-36 AC-36.1
# status: extended
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tests.test_tape_microstructure_ac283,
#               core.tests.test_g2a_live_backfill_parity_ac321,
#               core.tests.test_g2b_raw_truth_ac322,
#               core.tests.test_post_grad_overlap_parity_ac361,
#               pathlib, yaml
# ---
"""AC-32.3 / AC-36.1 — Combined T0/G1/G2 parity gate wired into the single canonical ci.yml 'test' job.

Golden parity is a HARD MERGE GATE (§6.4.3 — 'the #1 gate').  This file is the
combined wire for the full T0/G1/G2 parity suite:

  G1  — vendored feature math parity (US-28 AC-28.2/28.3):
         pinned via test_tape_microstructure_ac283.test_g1_gate_wired_in_single_canonical_test_job

  G2(a) — Birdeye live↔backfill byte-parity (US-32 AC-32.1):
           MAIN GATE: test_g2a_swap_level_byte_identity_e6ifp2
           MAIN GATE: test_g2a_feature_level_byte_identity_e6ifp2

  G2(b) — Helius raw-truth cross-check (US-32 AC-32.2):
           MAIN GATE: test_g2b_coverage_100pct
           MAIN GATE: test_g2b_side_parity_100pct

  G2/helius — helius_live birth-tape post-graduation OVERLAP byte-parity (US-36 AC-36.1):
              MAIN GATE: test_overlap_swap_level_byte_identity
              MAIN GATE: test_overlap_feature_level_byte_identity

Wiring mechanism (mirroring AC-21.3)
-------------------------------------
The module-level imports below create a hard compile-time dependency on each
gate function by its EXACT name.  Deleting, renaming, or moving any parity
test causes Python to raise ImportError during pytest's collection phase,
failing the CI 'test' job BEFORE any tests run — loud, immediate, attributable.

No second workflow is introduced (H1): all gates run in the single canonical
ci.yml 'test' job via the existing pytest invocation over core/tests/.

Tests
-----
  test_combined_parity_gate_functions_are_callable
      All five ImportError-trapped functions resolve to callables.

  test_combined_parity_gate_in_single_canonical_test_job
      ci.yml defines a job named 'test' and the pytest invocation does NOT
      --ignore core/tests, so this wire file is always exercised (H1).

  test_g2_gate_key_functions_are_non_trivial
      Each gate function has a non-empty __name__ and __doc__, guarding against
      a stub replacement that passes ImportError but asserts nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# G2(a) — live↔backfill byte-parity MAIN GATE functions (AC-32.1)
from core.tests.test_g2a_live_backfill_parity_ac321 import (
    test_g2a_feature_level_byte_identity_e6ifp2 as _g2a_feature_test,
)
from core.tests.test_g2a_live_backfill_parity_ac321 import (
    test_g2a_swap_level_byte_identity_e6ifp2 as _g2a_swap_test,
)

# G2(b) — Helius raw-truth MAIN GATE functions (AC-32.2)
from core.tests.test_g2b_raw_truth_ac322 import (
    test_g2b_coverage_100pct as _g2b_coverage_test,
)
from core.tests.test_g2b_raw_truth_ac322 import (
    test_g2b_side_parity_100pct as _g2b_side_test,
)

# G2/helius — helius_live birth-tape post-graduation OVERLAP byte-parity (AC-36.1)
# Folding the new source into this gate (oracle §3 / anti-drift contract).
# Deleting or renaming either function raises ImportError at collection time.
from core.tests.test_post_grad_overlap_parity_ac361 import (
    test_overlap_feature_level_byte_identity as _g2_helius_feature_test,
)
from core.tests.test_post_grad_overlap_parity_ac361 import (
    test_overlap_swap_level_byte_identity as _g2_helius_swap_test,
)

# ---------------------------------------------------------------------------
# ImportError trap — combined T0/G1/G2 parity gate cannot silently vanish
#
# These module-level imports pin each gate function by EXACT name.
# Deleting, renaming, or moving any of them raises ImportError at pytest
# collection time, failing CI BEFORE any tests run (mirroring AC-21.3).
# ---------------------------------------------------------------------------
# G1 — the AC-28.3 wire (which in turn pins the four AC-28.2 G1 functions)
from core.tests.test_tape_microstructure_ac283 import (
    test_g1_gate_wired_in_single_canonical_test_job as _g1_combined_wire_test,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Registry — all five pinned gate functions
# ---------------------------------------------------------------------------

_PINNED_GATE_FUNCTIONS: dict[str, object] = {
    "test_g1_gate_wired_in_single_canonical_test_job": _g1_combined_wire_test,
    "test_g2a_swap_level_byte_identity_e6ifp2": _g2a_swap_test,
    "test_g2a_feature_level_byte_identity_e6ifp2": _g2a_feature_test,
    "test_g2b_coverage_100pct": _g2b_coverage_test,
    "test_g2b_side_parity_100pct": _g2b_side_test,
    # AC-36.1: helius_live birth-tape post-graduation OVERLAP byte-parity
    "test_overlap_swap_level_byte_identity": _g2_helius_swap_test,
    "test_overlap_feature_level_byte_identity": _g2_helius_feature_test,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_combined_parity_gate_functions_are_callable() -> None:
    """All T0/G1/G2 gate functions (including AC-36.1 helius overlap) resolve to callables.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case an import resolves to a non-callable
    (e.g. a constant or stub replacing a function after a partial rename).
    """
    not_callable = [
        name for name, fn in _PINNED_GATE_FUNCTIONS.items() if not callable(fn)
    ]
    assert not not_callable, (
        "Combined T0/G1/G2 parity gate ImportError trap is broken — "
        "these names are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
    )


def test_combined_parity_gate_in_single_canonical_test_job() -> None:
    """The combined T0/G1/G2 parity gate runs in the single canonical ci.yml 'test' job.

    Asserts (H1 — no second workflow):
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT --ignore core/tests, so this
          wire file and all pinned gate functions are always exercised by CI.
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-32.3 requires the combined T0/G1/G2 gate to run in the single "
        "canonical ci.yml 'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the combined T0/G1/G2 parity gate to run in the single "
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
        "AC-32.3 requires the combined T0/G1/G2 gate to be exercised by the "
        "pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The combined T0/G1/G2 parity gate wire (this file) lives in "
            "core/tests/ and must NOT be excluded. Remove the --ignore flag."
        )


def test_g2_gate_key_functions_are_non_trivial() -> None:
    """Each pinned gate function has a non-empty __name__ and __doc__.

    Guards against a stub replacement that passes the ImportError trap but
    contributes no assertions — a real gate function always has a docstring.
    """
    empty_doc: list[str] = []
    for name, fn in _PINNED_GATE_FUNCTIONS.items():
        if not callable(fn):
            continue
        doc = getattr(fn, "__doc__", None)
        fn_name = getattr(fn, "__name__", "")
        if not fn_name or not doc or not doc.strip():
            empty_doc.append(name)

    assert not empty_doc, (
        "These gate functions have no name or docstring — "
        "they may be stubs rather than real parity tests:\n"
        + "\n".join(f"  {n}" for n in empty_doc)
    )
