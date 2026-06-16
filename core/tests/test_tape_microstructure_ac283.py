# ---
# module: core.tests.test_tape_microstructure_ac283
# sprint: sprint-7
# story: US-28 AC-28.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape_microstructure, core.tests.test_tape_microstructure_ac282, pathlib, yaml
# ---
"""AC-28.3 — None-on-no-usable-swap contract + G1 gate CI wire.

Two deliverables in one AC:

1. None-on-no-usable-swap contract (§7.2)
   compute_features MUST return None (not an empty dict, not a zero row) when
   presented with any degenerate swap list:
     • empty list
     • all swaps outside the feature window (rel >= window_s)
     • all swaps with zero or missing price (filtered out by build_grid)
   The caller must treat None as "no-feature" and never write a zero row.

2. G1 gate cannot silently vanish (H1 / §16)
   The four G1 parity functions in test_tape_microstructure_ac282 are pinned by
   name at module-import time.  Deleting or renaming any of them raises
   ImportError during pytest collection, failing CI BEFORE any tests run.
   A secondary test asserts the ci.yml 'test' job (H1) runs pytest over core/tests
   without --ignore, so this wire is always exercised.

Tests
-----
  test_compute_features_returns_none_for_empty_list
      compute_features([]) → None
  test_compute_features_returns_none_not_empty_dict
      Result is literally None, not an empty dict {} (the zero-row trap)
  test_compute_features_returns_none_for_all_out_of_window_swaps
      Swaps all have rel >= window_s — filtered to zero usable → None
  test_compute_features_returns_none_for_swaps_at_exact_window_boundary
      Swap at rel == window_s is excluded (strict less-than) → None
  test_compute_features_returns_none_for_zero_price_swaps
      All swaps have price == 0 — invalid and filtered → None
  test_compute_features_returns_none_for_missing_price_swaps
      Swaps missing the 'price' key entirely → None
  test_g1_gate_functions_are_callable
      The four imported G1 parity callables are confirmed callable (readable
      companion to the module-level ImportError trap).
  test_g1_gate_wired_in_single_canonical_test_job
      ci.yml has exactly one 'test' job and its pytest invocation does NOT
      --ignore core/tests, so this wire file is always exercised (H1).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.tape_microstructure import compute_features

# ---------------------------------------------------------------------------
# ImportError trap — G1 gate cannot silently vanish
#
# These four module-level imports pin the G1 parity test functions by EXACT
# name.  If any function is deleted, renamed, or moved, Python raises
# ImportError during pytest's collection phase and CI fails immediately —
# loud, immediate, and attributable (mirroring AC-21.3).
# ---------------------------------------------------------------------------
from core.tests.test_tape_microstructure_ac282 import (
    test_g1_fixture_coverage_all_buy_case_present as _g1_all_buy_test,
)
from core.tests.test_tape_microstructure_ac282 import (
    test_g1_fixture_coverage_sell_case_present as _g1_sell_case_test,
)
from core.tests.test_tape_microstructure_ac282 import (
    test_g1_fixture_has_expected_token_count as _g1_count_test,
)
from core.tests.test_tape_microstructure_ac282 import (
    test_g1_golden_fixture_all_tokens_all_features as _g1_all_tokens_test,
)

# ---------------------------------------------------------------------------
# Paths for CI wiring assertions
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# None-on-no-usable-swap contract tests
# ---------------------------------------------------------------------------


def test_compute_features_returns_none_for_empty_list() -> None:
    """compute_features([]) must return None, not a dict."""
    result = compute_features([])
    assert result is None, (
        f"compute_features([]) returned {result!r} — expected None.\n"
        "The §7.2 contract says: no usable swaps → None (never a zero row)."
    )


def test_compute_features_returns_none_not_empty_dict() -> None:
    """The None return must be the sentinel None, not an empty dict {}.

    An empty dict {} would be a zero row and violates §7.2 — the caller
    interprets None as 'no-feature' and skips the mint entirely.
    """
    result = compute_features([])
    assert result is None and not isinstance(result, dict), (
        f"compute_features([]) returned {result!r} — must be None, not an empty dict.\n"
        "A zero dict would silently appear as a zero-feature row in the training set."
    )


def test_compute_features_returns_none_for_all_out_of_window_swaps() -> None:
    """All swaps outside [0, window_s) are filtered; result must be None."""
    out_of_window = [
        {"rel": 120.0, "price": 0.001, "side": "buy", "vol": 100.0, "owner": "W1"},
        {"rel": 150.0, "price": 0.002, "side": "buy", "vol": 200.0, "owner": "W2"},
        {"rel": 300.0, "price": 0.003, "side": "sell", "vol": 50.0,  "owner": "W3"},
    ]
    result = compute_features(out_of_window, window_s=120)
    assert result is None, (
        f"compute_features with all swaps at rel >= 120 returned {result!r}.\n"
        "Expected None: no swap is usable when all fall outside [0, window_s)."
    )


def test_compute_features_returns_none_for_swaps_at_exact_window_boundary() -> None:
    """A single swap at rel == window_s is excluded (strict < window_s) → None."""
    boundary_swap = [
        {"rel": 120.0, "price": 0.001, "side": "buy", "vol": 100.0, "owner": "W1"},
    ]
    result = compute_features(boundary_swap, window_s=120)
    assert result is None, (
        f"compute_features with swap at rel == window_s returned {result!r}.\n"
        "Boundary swap must be EXCLUDED (rel < window_s is the contract, not <=)."
    )


def test_compute_features_returns_none_for_zero_price_swaps() -> None:
    """Swaps with price == 0 are treated as invalid by build_grid → None."""
    zero_price = [
        {"rel": 5.0,  "price": 0.0, "side": "buy", "vol": 100.0, "owner": "W1"},
        {"rel": 10.0, "price": 0.0, "side": "buy", "vol": 200.0, "owner": "W2"},
    ]
    result = compute_features(zero_price, window_s=120)
    assert result is None, (
        f"compute_features with price=0 swaps returned {result!r}.\n"
        "Zero-price swaps are invalid (build_grid filters price <= 0) → None."
    )


def test_compute_features_returns_none_for_missing_price_swaps() -> None:
    """Swaps that lack the 'price' key entirely are filtered → None."""
    no_price = [
        {"rel": 5.0, "side": "buy", "vol": 100.0, "owner": "W1"},
        {"rel": 10.0, "side": "buy", "vol": 200.0, "owner": "W2"},
    ]
    result = compute_features(no_price, window_s=120)
    assert result is None, (
        f"compute_features with missing 'price' swaps returned {result!r}.\n"
        "Swaps without 'price' are filtered by build_grid → None."
    )


# ---------------------------------------------------------------------------
# G1 gate wire tests
# ---------------------------------------------------------------------------


def test_g1_gate_functions_are_callable() -> None:
    """The four G1 parity callables must be callable.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case the import resolves to a non-callable
    (e.g. a constant replacing a function).
    """
    pinned: dict[str, object] = {
        "test_g1_golden_fixture_all_tokens_all_features": _g1_all_tokens_test,
        "test_g1_fixture_has_expected_token_count": _g1_count_test,
        "test_g1_fixture_coverage_all_buy_case_present": _g1_all_buy_test,
        "test_g1_fixture_coverage_sell_case_present": _g1_sell_case_test,
    }
    not_callable = [name for name, fn in pinned.items() if not callable(fn)]
    assert not not_callable, (
        "G1 gate ImportError trap is broken — these names are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
    )


def test_g1_gate_wired_in_single_canonical_test_job() -> None:
    """The G1 gate runs inside the single canonical ci.yml 'test' job (H1).

    Asserts:
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT ignore core/tests, so this
          wire file (and the pinned G1 functions) are always exercised by CI.
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-28.3 requires the G1 gate to run in the single canonical ci.yml "
        "'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the G1 parity gate to run in the single canonical 'test' "
        "job — no second workflow file must be introduced."
    )

    test_job = jobs["test"]
    pytest_cmds = [
        step.get("run", "")
        for step in (test_job.get("steps") or [])
        if isinstance(step, dict) and "pytest" in step.get("run", "")
    ]
    assert pytest_cmds, (
        "The 'test' job in ci.yml contains no pytest step.\n"
        "AC-28.3 requires the G1 gate to be exercised by the pytest invocation "
        "in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The G1 gate wire (this file) lives in core/tests/ and must NOT be "
            "excluded. Remove the --ignore flag to preserve the hard merge gate."
        )
