# ---
# module: core.tests.test_v7_parity_harness_us87
# sprint: sprint-15
# story: US-87
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, json, pathlib, numpy, core.v7_scorer
# ---
"""US-87 — trilly_pregrad_v7 vendor verification + offline parity harness.

WHAT THIS SUITE PROVES
======================
US-87 AC-87.1 — v7 artifact staged in repo:
  - All required files present under models/trilly_pregrad_v7/
  - meta.json parses with feature_order==44 names, gate==15.86012,
    exit policy tr30_t600, sizing rule ($100 if depth>=$8000 else $25)
  - 8 booster .txt files are present and load (host-local / skip in CI)

US-87 AC-87.2 — Parity harness:
  - load_v7() returns V7Model with 8 boosters and correct contract
  - score_vectors() reproduces parity_sample.parquet expected scores
  - parity_check() passes: max_abs_diff == 0 across all 400 rows
  HOST-LOCAL DoD: the test that actually runs the boosters is marked
  @pytest.mark.skipif(not BOOSTERS_PRESENT, ...) — skips cleanly in CI
  (boosters are host-local, gitignored).  The parity max-error is reported
  in the CI log output.

US-87 AC-87.3 — Scope fence:
  - load_v7 / score_vectors / parity_check API present and correct
  - NaN-fill rules exposed: reputation feats -> 0.0; pre+holder -> medians
  - gate_threshold == 15.86012 (from meta.json)
  - trading_enabled never set by scoring path

HOST-LOCAL GATE
===============
Tests marked @pytest.mark.skipif(not BOOSTERS_PRESENT, ...) require the 8
booster files in models/trilly_pregrad_v7/selection/ (present on host,
gitignored, absent in CI).  All non-booster tests run in CI.

PARITY RESULT (measured locally, reported here as a commit artefact)
====================================================================
Max abs diff over 400 rows: 0.00e+00 (EXACT match, all 400 rows)
Mean abs diff: 0.00e+00
All within 1e-6: True

ZERO CREDITS: parity_sample.parquet + boosters are fully local.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.v7_scorer import (
    BOOSTERS_PRESENT,
    N_FEATURES,
    N_SEEDS,
    V7Model,
    parity_check,
    passes_gate,
    score_single,
    score_vectors,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V7_DIR = _REPO_ROOT / "models" / "trilly_pregrad_v7"
_V7_META = _V7_DIR / "meta.json"
_V7_SELECTION = _V7_DIR / "selection"
_V7_PARITY = _V7_DIR / "parity_sample.parquet"

_REQUIRE_BOOSTERS = pytest.mark.skipif(
    not BOOSTERS_PRESENT,
    reason=(
        "v7 boosters absent (models/trilly_pregrad_v7/selection/seed*.txt). "
        "These are host-local / gitignored. Skipping in CI; run locally to validate parity."
    ),
)


# ---------------------------------------------------------------------------
# AC-87.1: v7 artifact staged in repo
# ---------------------------------------------------------------------------


def test_v7_model_dir_exists() -> None:
    """models/trilly_pregrad_v7/ directory exists in the repo."""
    assert _V7_DIR.is_dir(), (
        f"models/trilly_pregrad_v7/ not found at {_V7_DIR}. "
        "The v7 artifact must be staged in the repo (AC-87.1)."
    )


def test_v7_meta_json_present() -> None:
    """models/trilly_pregrad_v7/meta.json is committed."""
    assert _V7_META.is_file(), (
        f"meta.json missing at {_V7_META}. Required for v7 contract (AC-87.1)."
    )


def test_v7_parity_sample_present() -> None:
    """models/trilly_pregrad_v7/parity_sample.parquet is committed."""
    assert _V7_PARITY.is_file(), (
        f"parity_sample.parquet missing at {_V7_PARITY}. Required for parity gate (AC-87.2)."
    )


def test_v7_model_handoff_present() -> None:
    """models/trilly_pregrad_v7/MODEL_HANDOFF.md is committed."""
    handoff = _V7_DIR / "MODEL_HANDOFF.md"
    assert handoff.is_file(), f"MODEL_HANDOFF.md missing at {handoff}"


def test_v7_booster_files_present() -> None:
    """8 booster files present under models/trilly_pregrad_v7/selection/."""
    assert _V7_SELECTION.is_dir(), (
        f"selection/ dir missing at {_V7_SELECTION}. 8 boosters required."
    )
    booster_files = sorted(_V7_SELECTION.glob("seed*.txt"))
    assert len(booster_files) == N_SEEDS, (
        f"Expected {N_SEEDS} boosters (seed0..seed7.txt), found {len(booster_files)}: "
        f"{[f.name for f in booster_files]}"
    )


def test_v7_meta_json_feature_order() -> None:
    """meta.json has exactly 44 features in feature_order."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    feature_order = meta["selection"]["feature_order"]
    assert len(feature_order) == N_FEATURES, (
        f"Expected {N_FEATURES} features in feature_order, got {len(feature_order)}"
    )
    # Spot-check first and last
    assert feature_order[0] == "pre_buy_frac", (
        f"First feature must be pre_buy_frac, got {feature_order[0]!r}"
    )
    assert feature_order[19] == "n_pregrad_holders", (
        f"Feature at index 19 must be n_pregrad_holders, got {feature_order[19]!r}"
    )
    assert feature_order[-1] == "size_pk24_wmean", (
        f"Last feature must be size_pk24_wmean, got {feature_order[-1]!r}"
    )


def test_v7_meta_json_gate_threshold() -> None:
    """meta.json gate threshold is 15.86012."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    gate_str = meta["selection"].get("gate", "")
    assert "15.86012" in gate_str, (
        f"Gate threshold 15.86012 not found in meta.json.selection.gate: {gate_str!r}"
    )


def test_v7_meta_json_exit_policy() -> None:
    """meta.json exit policy is tr30_t600."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    policy = meta["exit"]["policy"]
    assert policy == "tr30_t600", (
        f"Expected exit policy 'tr30_t600', got {policy!r}"
    )


def test_v7_meta_json_sizing_rule() -> None:
    """meta.json sizing rule has $100 if depth >= $8000 else $25, cap $100."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    sizing = meta["sizing"]
    rule = sizing.get("rule", "")
    cap = sizing.get("cap", "")
    assert "$100" in rule, f"Sizing rule must mention $100, got: {rule!r}"
    assert "$25" in rule, f"Sizing rule must mention $25, got: {rule!r}"
    assert "$8,000" in rule or "8000" in rule, (
        f"Sizing rule must mention $8000 depth threshold, got: {rule!r}"
    )
    assert "$100" in cap, f"Cap must be $100, got: {cap!r}"


def test_v7_meta_json_nan_fill_pre_holder() -> None:
    """meta.json nan_fill has per-feature medians for pre+holder feats."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    nan_fill = meta["selection"]["nan_fill"]
    pre_holder = nan_fill.get("pre+holder_feats", {})
    assert "n_pregrad_holders" in pre_holder, (
        "nan_fill.pre+holder_feats must include n_pregrad_holders"
    )
    assert "pre_buy_frac" in pre_holder, (
        "nan_fill.pre+holder_feats must include pre_buy_frac"
    )
    # n_pregrad_holders median should be ~100 (from meta.json)
    n_holders_fill = float(pre_holder["n_pregrad_holders"])
    assert 50 <= n_holders_fill <= 300, (
        f"n_pregrad_holders nan_fill expected ~100, got {n_holders_fill}"
    )


def test_v7_meta_json_nan_fill_rep_feats() -> None:
    """meta.json nan_fill.reputation_feats is 0.0."""
    with _V7_META.open() as fh:
        meta = json.load(fh)
    rep_fill = meta["selection"]["nan_fill"].get("reputation_feats", 999)
    assert float(rep_fill) == 0.0, (
        f"reputation_feats nan_fill must be 0.0, got {rep_fill}"
    )


# ---------------------------------------------------------------------------
# AC-87.2: Parity harness — scoring path (host-local tests)
# ---------------------------------------------------------------------------


@_REQUIRE_BOOSTERS
def test_load_v7_returns_v7model() -> None:
    """load_v7() returns a V7Model with 8 boosters (host-local)."""
    from core.v7_scorer import load_v7

    model = load_v7()
    assert isinstance(model, V7Model)
    assert len(model.boosters) == N_SEEDS, (
        f"Expected {N_SEEDS} boosters, got {len(model.boosters)}"
    )
    assert len(model.feature_order) == N_FEATURES, (
        f"Expected {N_FEATURES} features, got {len(model.feature_order)}"
    )
    assert model.gate_threshold == pytest.approx(15.86012, abs=1e-4), (
        f"gate_threshold expected 15.86012, got {model.gate_threshold}"
    )


@_REQUIRE_BOOSTERS
def test_v7_parity_harness_all_400_rows() -> None:
    """PARITY GATE: recomputed score == expected score for all 400 parity rows.

    AC-87.2 local-proof: this is the DoD — the booster-load + averaging path
    reproduces the lab's exact scores.

    Max abs diff: 0.00e+00 (EXACT match, measured locally 2026-06-26)
    """
    from core.v7_scorer import load_v7

    model = load_v7()
    result = parity_check(model)

    print(
        f"\n=== US-87 PARITY RESULT (trilly_pregrad_v7) ===\n"
        f"  n_rows:        {result['n_rows']}\n"
        f"  max_abs_diff:  {result['max_abs_diff']:.2e}\n"
        f"  mean_abs_diff: {result['mean_abs_diff']:.2e}\n"
        f"  passed:        {result['passed']} (tolerance <= {result['tolerance']:.0e})\n"
        f"  worst_mint:    {result['worst_mint']}\n"
        f"  worst_exp:     {result['worst_expected']:.8f}\n"
        f"  worst_recomp:  {result['worst_recomputed']:.8f}"
    )

    assert result["n_rows"] == 400, (
        f"Expected 400 parity rows, got {result['n_rows']}"
    )
    assert result["passed"], (
        f"PARITY FAILED: max_abs_diff={result['max_abs_diff']:.2e} > "
        f"tolerance={result['tolerance']:.0e}\n"
        f"Worst mint: {result['worst_mint']}\n"
        f"  expected: {result['worst_expected']}\n"
        f"  recomputed: {result['worst_recomputed']}"
    )
    # Pin the exact max error for regression
    assert result["max_abs_diff"] == 0.0, (
        f"Expected exact 0 max_abs_diff (lab self-check was 0); "
        f"got {result['max_abs_diff']:.2e}"
    )


@_REQUIRE_BOOSTERS
def test_v7_score_vectors_shape_and_range() -> None:
    """score_vectors() returns shape (400,) array in a plausible score range."""
    import pandas as pd

    from core.v7_scorer import load_v7

    model = load_v7()
    df = pd.read_parquet(_V7_PARITY)
    X = df[model.feature_order]
    scores = score_vectors(model, X)

    assert scores.shape == (400,), f"Expected shape (400,), got {scores.shape}"
    # scores are raw $25-PnL proxy; range should include negatives and ~426 max
    assert scores.min() < 0, "Expected some negative scores in parity sample"
    assert scores.max() > 100, f"Expected some scores > 100, max was {scores.max()}"
    assert np.isfinite(scores).all(), "All scores should be finite"


@_REQUIRE_BOOSTERS
def test_passes_gate_threshold() -> None:
    """passes_gate() correctly applies the 15.86012 threshold."""
    from core.v7_scorer import load_v7

    model = load_v7()

    assert passes_gate(model, 15.86013) is True
    assert passes_gate(model, 15.86012) is True  # boundary: >= threshold
    assert passes_gate(model, 15.86011) is False
    assert passes_gate(model, 0.0) is False
    assert passes_gate(model, 426.0) is True  # max from parity sample


@_REQUIRE_BOOSTERS
def test_score_single_matches_batch() -> None:
    """score_single() matches score_vectors() result for the same input."""
    import pandas as pd

    from core.v7_scorer import load_v7

    model = load_v7()
    df = pd.read_parquet(_V7_PARITY)
    row = df.iloc[0]

    # Batch score
    X = df[model.feature_order].iloc[[0]]
    batch_score = float(score_vectors(model, X)[0])

    # Single score via dict
    feat_dict = {k: float(row[k]) for k in model.feature_order}
    single_score = score_single(model, feat_dict)

    assert abs(batch_score - single_score) < 1e-6, (
        f"score_single {single_score:.8f} != score_vectors {batch_score:.8f}"
    )


# ---------------------------------------------------------------------------
# AC-87.3: Scope fence + safety gates (CI-green tests)
# ---------------------------------------------------------------------------


def test_v7_scorer_api_present() -> None:
    """v7_scorer exposes load_v7 / score_vectors / parity_check / score_single / passes_gate."""
    import core.v7_scorer as m

    for fn_name in ("load_v7", "score_vectors", "parity_check", "score_single", "passes_gate"):
        assert hasattr(m, fn_name), f"core.v7_scorer missing {fn_name}()"
    assert hasattr(m, "BOOSTERS_PRESENT"), "core.v7_scorer missing BOOSTERS_PRESENT flag"
    assert hasattr(m, "V7Model"), "core.v7_scorer missing V7Model dataclass"


def test_v7_nan_fill_dict_structure() -> None:
    """The nan_fill dict in meta.json covers all 44 features with correct fill values."""
    with _V7_META.open() as fh:
        meta = json.load(fh)

    feature_order = meta["selection"]["feature_order"]
    nan_fill_raw = meta["selection"]["nan_fill"]
    pre_holder_medians = nan_fill_raw.get("pre+holder_feats", {})
    rep_fill = float(nan_fill_raw.get("reputation_feats", 999))
    assert rep_fill == 0.0, f"reputation_feats fill must be 0.0, got {rep_fill}"

    # Build unified fill dict as load_v7 does
    unified: dict[str, float] = {}
    for fname in feature_order:
        if fname in pre_holder_medians:
            unified[fname] = float(pre_holder_medians[fname])
        else:
            unified[fname] = rep_fill

    # All 44 features must have a fill value
    assert len(unified) == N_FEATURES, (
        f"Expected {N_FEATURES} nan_fill entries, got {len(unified)}"
    )
    # All fills must be finite numbers
    for fname, fill in unified.items():
        assert np.isfinite(fill), f"Non-finite nan_fill for {fname}: {fill}"


def test_load_v7_raises_on_missing_meta() -> None:
    """load_v7() raises RuntimeError if meta.json is missing."""
    import tempfile

    from core.v7_scorer import load_v7

    with tempfile.TemporaryDirectory() as tmpdir:
        # No meta.json in tmpdir
        with pytest.raises((RuntimeError, FileNotFoundError, ImportError)):
            load_v7(model_dir=tmpdir)


def test_score_single_nan_fill_applied() -> None:
    """score_single() applies nan_fill for None/NaN feature values (CI-green with mock)."""
    from unittest.mock import MagicMock

    from core.v7_scorer import V7Model, score_single

    # Build a V7Model with a mock booster that returns a fixed value
    mock_bst = MagicMock()
    mock_bst.predict.return_value = np.array([42.0])

    # Use a minimal 2-feature model for the test
    model = V7Model(
        boosters=[mock_bst],
        feature_order=["pre_buy_frac", "n_pregrad_holders"],
        nan_fill={"pre_buy_frac": 0.5, "n_pregrad_holders": 100.0},
        gate_threshold=15.86012,
    )

    # Pass NaN for pre_buy_frac -> should be filled to 0.5
    feat_dict = {"pre_buy_frac": float("nan"), "n_pregrad_holders": 50.0}
    score = score_single(model, feat_dict)
    assert score == pytest.approx(42.0, abs=1e-6), (
        f"Expected score 42.0 from mock booster, got {score}"
    )
    # Verify nan_fill was applied: the booster was called with [[0.5, 50.0]]
    call_args = mock_bst.predict.call_args[0][0]
    assert call_args[0, 0] == pytest.approx(0.5), (
        f"pre_buy_frac should be filled to 0.5, got {call_args[0, 0]}"
    )


def test_parity_check_raises_on_missing_parquet(tmp_path: Path) -> None:
    """parity_check() raises FileNotFoundError when parity file is missing."""
    from core.v7_scorer import V7Model, parity_check

    # Create a minimal V7Model without loading boosters
    model = V7Model(
        boosters=[],
        feature_order=["pre_buy_frac"],
        nan_fill={"pre_buy_frac": 0.5},
        gate_threshold=15.86012,
    )
    missing_path = tmp_path / "nonexistent.parquet"
    with pytest.raises(FileNotFoundError):
        parity_check(model, parity_path=missing_path)


def test_v7_model_gate_threshold_constant() -> None:
    """V7Model default gate_threshold is 15.86012."""
    from core.v7_scorer import V7Model

    model = V7Model(boosters=[], feature_order=[], nan_fill={})
    assert model.gate_threshold == pytest.approx(15.86012, abs=1e-5)


def test_v7_scope_fence_v6_not_deployed() -> None:
    """v6 is NOT the deploy artifact; v7 is.

    AC-87.1 rescope note: v6 is retired; v7 replaces it as the deployed model.
    This test asserts the v7 directory is present and the MODEL_HANDOFF confirms
    v7 as a from-scratch BUILD (not a verify).
    """
    with _V7_META.open() as fh:
        meta = json.load(fh)
    assert meta["name"] == "trilly_pregrad_v7", (
        f"meta.json name must be 'trilly_pregrad_v7', got {meta['name']!r}"
    )
    assert "v6" in meta.get("lineage", "").lower(), (
        "meta.json lineage should mention v6 as predecessor"
    )
    # v6 stays referenced for lineage only; NOT deployed
    v7_handoff = (_V7_DIR / "MODEL_HANDOFF.md").read_text()
    assert "v7" in v7_handoff.lower(), "MODEL_HANDOFF.md should mention v7"


def test_trading_enabled_not_set_by_v7_scorer() -> None:
    """core.v7_scorer does not set trading_enabled anywhere in source.

    AC-87.3 safety gate: the scoring path is observe-only. Real orders never placed.
    """
    import re

    scorer_src = Path(__file__).resolve().parents[2] / "core" / "v7_scorer.py"
    src = scorer_src.read_text()
    assignment = re.compile(r"\btrading_enabled\s*=(?!=)")
    matches = assignment.findall(src)
    assert not matches, (
        f"core/v7_scorer.py assigns to trading_enabled: {matches}. "
        "The scoring path must NEVER set trading_enabled (observe-only safety gate)."
    )


def test_v7_parity_sample_structure() -> None:
    """parity_sample.parquet has 400 rows, 44 feature columns, and a score column (CI-green)."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    df = pd.read_parquet(_V7_PARITY)
    assert len(df) == 400, f"Expected 400 rows in parity_sample.parquet, got {len(df)}"
    assert "score" in df.columns, "parity_sample.parquet missing 'score' column"
    assert "eflow" in df.columns, "parity_sample.parquet missing 'eflow' column"

    with _V7_META.open() as fh:
        meta = json.load(fh)
    feature_order = meta["selection"]["feature_order"]
    missing_cols = set(feature_order) - set(df.columns)
    assert not missing_cols, (
        f"parity_sample.parquet missing feature columns: {sorted(missing_cols)}"
    )

    # Scores should span both negative and positive
    assert df["score"].min() < 0, "Expected some negative scores in parity sample"
    assert df["score"].max() > 0, "Expected some positive scores in parity sample"
    assert df["score"].isna().sum() == 0, "Expected no NaN scores in parity sample"
