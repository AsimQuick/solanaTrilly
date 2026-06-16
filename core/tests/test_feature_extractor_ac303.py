# ---
# module: core.tests.test_feature_extractor_ac303
# sprint: sprint-7
# story: US-30 AC-30.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, core.models,
#               core.tests.test_feature_extractor_ac301,
#               core.tests.test_feature_extractor_ac302,
#               pathlib, yaml, pytest
# ---
"""AC-30.3 — Deterministic + versioned: byte-identical run-twice, FeatureSet stamps.

Same raw + same FeatureSet → byte-identical output on every run. The output
is keyed to the FeatureSet hash and math_version. Verified by a run-twice
pytest test. The AC-30.1 and AC-30.2 extractor tests are pinned by name at
module-import time (H1 / ImportError trap), wiring them into the single
canonical ci.yml 'test' job.

Tests
-----
  test_run_twice_byte_identical_static
      Pure unit (no DB): FeatureExtractor._compute called twice with the same
      swaps → byte-identical result dicts.

  test_run_twice_byte_identical_with_feature_set
      Django DB: creates FeatureSet + Swap rows, calls extract_from_db twice,
      asserts result dicts are byte-identical including version stamps.

  test_output_carries_feature_set_hash
      Django DB: result["_feature_set_hash"] == fs.hash.

  test_output_carries_math_version
      Django DB: result["_math_version"] == fs.math_version.

  test_different_feature_sets_produce_different_stamps
      Django DB: two FeatureSets with different columns → different
      _feature_set_hash stamps.

  test_extractor_determinism_and_cutoff_tests_wired_in_ci
      Pure unit: ci.yml defines a 'test' job and pytest does not --ignore
      core/tests (same pattern as AC-28.3's CI wire test).

  test_pinned_functions_are_callable
      Pure unit: all 4 ImportError-trapped functions are callable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# ImportError trap — AC-30.1 and AC-30.2 gate functions cannot silently vanish
#
# These four module-level imports pin the determinism + cutoff test functions
# by EXACT name.  Deleting, renaming, or moving any of them raises ImportError
# during pytest's collection phase and CI fails immediately — before any test
# runs.
# ---------------------------------------------------------------------------
from core.tests.test_feature_extractor_ac301 import (
    test_feature_extractor_db_vs_lake_byte_identical as _trap_db_vs_lake,
)
from core.tests.test_feature_extractor_ac301 import (
    test_feature_extractor_stable_sort_key_determinism as _trap_determinism,
)
from core.tests.test_feature_extractor_ac302 import (
    test_cutoff_excludes_swap_at_rel_equals_window_s as _trap_cutoff_at,
)
from core.tests.test_feature_extractor_ac302 import (
    test_feature_value_equals_windowed_subset as _trap_value_eq,
)

# ---------------------------------------------------------------------------
# Paths for CI wiring assertion
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

_MINT = "TestMintAC303xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_WINDOW_S = 60


def _swap(rel: float, price: float = 1.5, side: str = "buy", vol: float = 1.0) -> dict:
    """Build a minimal §7.1 microstructure swap dict."""
    return {
        "block_time": 1_700_000_000 + int(rel),
        "slot": 1000 + int(rel),
        "signature": f"Sig{rel:.10f}".replace(".", "x"),
        "rel": rel,
        "price": price,
        "side": side,
        "vol": vol,
        "owner": "OwnerX",
    }


_SWAPS = [_swap(10.0), _swap(20.0), _swap(30.0, side="sell"), _swap(40.0)]


def _make_feature_set(db: bool = True, columns: list | None = None, math_version: str = "solanabilly3:sprint-7"):
    """Return a FeatureSet object (creates a DB row when db=True)."""
    from core.models import FeatureSet

    cols = columns or [
        "tape_n_trades",
        "tape_n_unique_traders",
        "tape_ret_total",
        "tape_logprice_slope_per_s",
    ]
    live = cols[:2]
    content_hash = FeatureSet.compute_hash(cols, math_version)
    if db:
        return FeatureSet.objects.create(
            version="v1",
            math_version=math_version,
            columns=cols,
            live_servable=live,
            hash=content_hash,
        )
    obj = FeatureSet(
        version="v1",
        math_version=math_version,
        columns=cols,
        live_servable=live,
        hash=content_hash,
    )
    return obj


def _write_db_swaps() -> None:
    """Insert _SWAPS into the DB Swap table under _MINT."""
    from core.models import Swap

    for s in _SWAPS:
        Swap.objects.create(
            mint=_MINT,
            block_time=s["block_time"],
            slot=s["slot"],
            signature=s["signature"],
            rel=s["rel"],
            price=s["price"],
            side=s["side"],
            vol_sol=s["vol"],
            vol_usd=s["vol"] * 150.0,
            sol_usd=150.0,
            owner=s["owner"],
        )


# ---------------------------------------------------------------------------
# Run-twice byte-identical — static (no DB)
# ---------------------------------------------------------------------------


def test_run_twice_byte_identical_static() -> None:
    """Pure unit: _compute called twice with identical swaps → byte-identical dicts.

    No DB, no FeatureSet — exercises the core compute path determinism directly.
    """
    from core.feature_extractor import FeatureExtractor

    first = FeatureExtractor._compute(_SWAPS, window_s=_WINDOW_S, bucket_s=15)
    second = FeatureExtractor._compute(_SWAPS, window_s=_WINDOW_S, bucket_s=15)

    assert first is not None, "_compute returned None on first call"
    assert second is not None, "_compute returned None on second call"
    assert first == second, (
        "Run-twice determinism failed for _compute:\n"
        f"  first:  {first}\n"
        f"  second: {second}"
    )


# ---------------------------------------------------------------------------
# Run-twice byte-identical — with FeatureSet stamps (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_run_twice_byte_identical_with_feature_set() -> None:
    """Django DB: extract_from_db called twice → byte-identical output including stamps.

    Verifies that _feature_set_hash and _math_version stamps are identical on
    both calls, and that every tape_* feature value is byte-identical.
    """
    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    _write_db_swaps()
    extractor = FeatureExtractor(feature_set=fs)

    first = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)
    second = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)

    assert first is not None, "extract_from_db returned None on first call"
    assert second is not None, "extract_from_db returned None on second call"
    assert first == second, (
        "Run-twice determinism failed for extract_from_db (including stamps):\n"
        f"  first:  {first}\n"
        f"  second: {second}"
    )


# ---------------------------------------------------------------------------
# FeatureSet version stamps
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_output_carries_feature_set_hash() -> None:
    """Result dict carries _feature_set_hash equal to fs.hash (§6.4.5)."""
    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    _write_db_swaps()
    extractor = FeatureExtractor(feature_set=fs)

    result = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)

    assert result is not None
    assert "_feature_set_hash" in result, (
        "Result dict missing '_feature_set_hash' stamp — AC-30.3 requires it."
    )
    assert result["_feature_set_hash"] == fs.hash, (
        f"_feature_set_hash mismatch: got {result['_feature_set_hash']!r}, "
        f"expected {fs.hash!r}"
    )


@pytest.mark.django_db
def test_output_carries_math_version() -> None:
    """Result dict carries _math_version equal to fs.math_version (§6.4.5)."""
    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    _write_db_swaps()
    extractor = FeatureExtractor(feature_set=fs)

    result = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)

    assert result is not None
    assert "_math_version" in result, (
        "Result dict missing '_math_version' stamp — AC-30.3 requires it."
    )
    assert result["_math_version"] == fs.math_version, (
        f"_math_version mismatch: got {result['_math_version']!r}, "
        f"expected {fs.math_version!r}"
    )


@pytest.mark.django_db
def test_different_feature_sets_produce_different_stamps() -> None:
    """Two FeatureSets with different columns → different _feature_set_hash stamps.

    Confirms that the hash stamps are derived from the FeatureSet content, not
    a constant — different FeatureSets produce distinct hashes.
    """
    from core.feature_extractor import FeatureExtractor

    cols_a = ["tape_n_trades", "tape_n_unique_traders", "tape_ret_total", "tape_logprice_slope_per_s"]
    cols_b = ["tape_n_trades", "tape_max_drawdown"]

    fs_a = _make_feature_set(db=True, columns=cols_a, math_version="solanabilly3:sprint-7")
    fs_b = _make_feature_set(db=True, columns=cols_b, math_version="solanabilly3:sprint-7")

    _write_db_swaps()

    result_a = FeatureExtractor(feature_set=fs_a).extract_from_db(_MINT, window_s=_WINDOW_S)
    result_b = FeatureExtractor(feature_set=fs_b).extract_from_db(_MINT, window_s=_WINDOW_S)

    assert result_a is not None, "extract_from_db with fs_a returned None"
    assert result_b is not None, "extract_from_db with fs_b returned None"

    assert result_a["_feature_set_hash"] != result_b["_feature_set_hash"], (
        "Different FeatureSets produced the same _feature_set_hash — "
        f"fs_a.hash={fs_a.hash!r}, fs_b.hash={fs_b.hash!r}"
    )


# ---------------------------------------------------------------------------
# CI wiring — extractor determinism + cutoff tests exercised in single job
# ---------------------------------------------------------------------------


def test_extractor_determinism_and_cutoff_tests_wired_in_ci() -> None:
    """The AC-30.1/30.2/30.3 determinism+cutoff tests run in the single ci.yml 'test' job.

    Asserts:
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT --ignore core/tests, so the
          ImportError trap functions pinned at the top of this file are always
          exercised by CI (H1 — no second workflow).
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-30.3 requires the determinism+cutoff gate to run in the single "
        "canonical ci.yml 'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the determinism+cutoff gate to run in the single canonical "
        "'test' job — no second workflow file must be introduced."
    )

    test_job = jobs["test"]
    pytest_cmds = [
        step.get("run", "")
        for step in (test_job.get("steps") or [])
        if isinstance(step, dict) and "pytest" in step.get("run", "")
    ]
    assert pytest_cmds, (
        "The 'test' job in ci.yml contains no pytest step.\n"
        "AC-30.3 requires the determinism+cutoff gate to be exercised by the "
        "pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The AC-30.3 wire (this file) lives in core/tests/ and must NOT be "
            "excluded. Remove the --ignore flag to preserve the hard merge gate."
        )


# ---------------------------------------------------------------------------
# ImportError trap callability confirmation
# ---------------------------------------------------------------------------


def test_pinned_functions_are_callable() -> None:
    """All 4 ImportError-trapped AC-30.1/30.2 functions are callable.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case the import resolves to a non-callable.
    """
    pinned: dict[str, object] = {
        "test_feature_extractor_db_vs_lake_byte_identical": _trap_db_vs_lake,
        "test_feature_extractor_stable_sort_key_determinism": _trap_determinism,
        "test_cutoff_excludes_swap_at_rel_equals_window_s": _trap_cutoff_at,
        "test_feature_value_equals_windowed_subset": _trap_value_eq,
    }
    not_callable = [name for name, fn in pinned.items() if not callable(fn)]
    assert not not_callable, (
        "AC-30.3 ImportError trap is broken — these names are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
    )
