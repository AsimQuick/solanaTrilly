# ---
# module: core.tests.test_feature_reconciler_ac411
# sprint: sprint-9
# story: US-41 AC-41.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_reconciler, json, pathlib, pytest
# ---
"""AC-41.1 — Feature-contract reconciliation utility: clean reconcile on v3.2
and correct FAILURE report on seeded mismatches.

The reconciliation utility (core.feature_reconciler) is MODEL-AGNOSTIC: it
reads the model's declared feature list from meta.json:features and each
booster's feature list via parse_lgbm_feature_names(), never hardcoding v3.2.

Tests
-----
  Wiring guard (ImportError trap, H1):
    test_reconcile_contract_importable
        parse_lgbm_feature_names and reconcile_feature_contract must be importable;
        missing import fails collection (the H1 compile-time trap).

  Clean reconcile on v3.2 real contract:
    test_v32_meta_features_are_20
        meta.json has exactly 20 pre_* features.
    test_v32_all_boosters_feature_names_match_meta
        All 15 boosters' feature lists parsed via parse_lgbm_feature_names()
        match meta.json:features in order.
    test_v32_clean_reconcile_full_contract
        reconcile_feature_contract returns clean=True for the exact v3.2 feature
        list when the FeatureSet covers all 20 features as live_servable (the
        real contract: every model feature is live-computable).

  Seeded-mismatch failure reports:
    test_missing_feature_detected
        Removing one model feature from FeatureSet.columns produces missing=[].
    test_training_only_feature_detected
        A model feature absent from live_servable is reported as training_only.
    test_order_divergence_detected
        Swapping two features in FeatureSet.columns triggers order_divergence=True.
    test_extra_feature_reported
        A feature in live_servable not in the model list appears in extra (info).
    test_booster_mismatch_detected
        A booster with a different feature list triggers booster_mismatches.
    test_missing_and_training_only_blocks_clean
        clean=False when missing or training_only is non-empty.
    test_clean_requires_all_conditions
        clean=True only when missing==[], training_only==[], order_divergence==False,
        booster_mismatches==[].

  Remedy lines:
    test_remedy_lines_cover_all_categories
        remedy_lines() emits at least one line per active failure category.
"""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Wiring guard — H1 ImportError trap
# ---------------------------------------------------------------------------

from core.feature_reconciler import (  # noqa: E402
    ReconcileResult,
    parse_lgbm_feature_names,
    reconcile_feature_contract,
)


def test_reconcile_contract_importable():
    """parse_lgbm_feature_names and reconcile_feature_contract must be importable."""
    assert callable(parse_lgbm_feature_names)
    assert callable(reconcile_feature_contract)


# ---------------------------------------------------------------------------
# Fixtures — v3.2 banked fixture data (committed to repo for offline CI)
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "trilly_pregrad_v3_2"
_META_PATH = _FIXTURES_DIR / "meta.json"
_BOOSTER_NAMES_PATH = _FIXTURES_DIR / "booster_feature_names.json"

_LABELS = ("ctrl", "oracle", "liq")
_SEEDS = (0, 1, 2, 3, 4)


def _load_meta_features() -> list[str]:
    with _META_PATH.open() as fh:
        meta = json.load(fh)
    return meta["features"]


def _all_booster_feature_lists() -> list[list[str]]:
    """Return all 15 booster feature lists from the banked compact fixture."""
    with _BOOSTER_NAMES_PATH.open() as fh:
        data = json.load(fh)
    return [
        data[f"{label}_s{seed}"]
        for label in _LABELS
        for seed in _SEEDS
    ]


# ---------------------------------------------------------------------------
# v3.2 real-contract tests
# ---------------------------------------------------------------------------

def test_v32_meta_features_are_20():
    """meta.json declares exactly 20 features."""
    features = _load_meta_features()
    assert len(features) == 20


def test_v32_all_boosters_feature_names_match_meta():
    """All 15 boosters have feature lists identical to meta.json:features."""
    meta_features = _load_meta_features()
    all_booster_lists = _all_booster_feature_lists()
    assert len(all_booster_lists) == 15, "Expected 15 booster feature lists"
    for idx, booster_features in enumerate(all_booster_lists):
        label = _LABELS[idx // 5]
        seed = idx % 5
        assert booster_features == meta_features, (
            f"{label}_s{seed}: booster features differ from meta.json\n"
            f"  booster: {booster_features}\n"
            f"  meta:    {meta_features}"
        )


def test_v32_clean_reconcile_full_contract():
    """Clean reconcile when FeatureSet covers all 20 v3.2 features as live_servable."""
    meta_features = _load_meta_features()
    booster_feature_lists = _all_booster_feature_lists()

    # FeatureSet with v3.2's features all live-computable (plus extra tape_* columns)
    extra_tape = ["tape_n_trades", "tape_ret_total", "tape_max_drawdown"]
    fs_columns = meta_features + extra_tape
    fs_live_servable = meta_features + extra_tape  # all live

    result = reconcile_feature_contract(
        model_feature_list=meta_features,
        feature_set_columns=fs_columns,
        feature_set_live_servable=fs_live_servable,
        booster_feature_lists=booster_feature_lists,
    )

    assert result.clean, (
        "Expected clean reconcile on v3.2 real contract but got failures:\n"
        + "\n".join(result.remedy_lines())
    )
    assert result.missing == []
    assert result.training_only == []
    assert result.order_divergence is False
    assert result.booster_mismatches == []


# ---------------------------------------------------------------------------
# Seeded-mismatch failure reports
# ---------------------------------------------------------------------------

def _base_features() -> list[str]:
    return _load_meta_features()


def test_missing_feature_detected():
    """Removing one model feature from FeatureSet.columns reports it as missing."""
    model_features = _base_features()
    dropped = model_features[5]  # e.g. pre_buyers_second_half

    fs_columns = [f for f in model_features if f != dropped]
    fs_live = list(fs_columns)

    result = reconcile_feature_contract(model_features, fs_columns, fs_live)

    assert not result.clean
    assert dropped in result.missing
    assert result.training_only == []


def test_training_only_feature_detected():
    """A model feature in columns but absent from live_servable is training_only."""
    model_features = _base_features()
    locked = model_features[2]  # e.g. pre_eb10_netpos_frac

    fs_columns = list(model_features)
    fs_live = [f for f in model_features if f != locked]  # locked out of live

    result = reconcile_feature_contract(model_features, fs_columns, fs_live)

    assert not result.clean
    assert locked in result.training_only
    assert locked not in result.missing


def test_order_divergence_detected():
    """Swapping two model features in FeatureSet.columns triggers order_divergence."""
    model_features = _base_features()

    fs_columns = list(model_features)
    fs_columns[0], fs_columns[1] = fs_columns[1], fs_columns[0]  # swap first two
    fs_live = list(fs_columns)

    result = reconcile_feature_contract(model_features, fs_columns, fs_live)

    assert not result.clean
    assert result.order_divergence is True
    assert result.missing == []
    assert result.training_only == []


def test_extra_feature_reported():
    """A feature in live_servable not in model list appears in extra (informational)."""
    model_features = _base_features()

    fs_columns = model_features + ["tape_n_trades"]
    fs_live = model_features + ["tape_n_trades"]  # tape_n_trades is extra

    result = reconcile_feature_contract(model_features, fs_columns, fs_live)

    assert result.clean  # extra alone does not block promotion
    assert "tape_n_trades" in result.extra


def test_booster_mismatch_detected():
    """A booster with a wrong feature list triggers booster_mismatches."""
    model_features = _base_features()
    fs_columns = list(model_features)
    fs_live = list(model_features)

    good_booster = list(model_features)
    bad_booster = list(model_features)
    bad_booster[0], bad_booster[1] = bad_booster[1], bad_booster[0]  # wrong order

    result = reconcile_feature_contract(
        model_features,
        fs_columns,
        fs_live,
        booster_feature_lists=[good_booster, bad_booster],
    )

    assert not result.clean
    mismatch_indices = [idx for idx, _ in result.booster_mismatches]
    assert 1 in mismatch_indices
    assert 0 not in mismatch_indices


def test_missing_and_training_only_blocks_clean():
    """clean=False when either missing or training_only is non-empty."""
    model_features = _base_features()

    # Case 1: missing
    r1 = reconcile_feature_contract(
        model_features,
        feature_set_columns=[],
        feature_set_live_servable=[],
    )
    assert not r1.clean
    assert len(r1.missing) == len(model_features)

    # Case 2: training_only (all in columns, none live)
    r2 = reconcile_feature_contract(
        model_features,
        feature_set_columns=list(model_features),
        feature_set_live_servable=[],
    )
    assert not r2.clean
    assert len(r2.training_only) == len(model_features)


def test_clean_requires_all_conditions():
    """clean=True only when missing==[], training_only==[], order_divergence==False, booster_mismatches==[]."""
    base = ReconcileResult()
    assert base.clean  # default: all empty / False

    assert not ReconcileResult(missing=["x"]).clean
    assert not ReconcileResult(training_only=["x"]).clean
    assert not ReconcileResult(order_divergence=True).clean
    assert not ReconcileResult(booster_mismatches=[(0, ["x"])]).clean
    assert ReconcileResult(extra=["x"]).clean  # extra alone is fine


# ---------------------------------------------------------------------------
# Remedy lines
# ---------------------------------------------------------------------------

def test_remedy_lines_cover_all_categories():
    """remedy_lines() emits at least one line per active failure category."""
    result = ReconcileResult(
        missing=["feat_a"],
        extra=["feat_b"],
        order_divergence=True,
        training_only=["feat_c"],
        booster_mismatches=[(3, ["feat_x", "feat_a"])],
    )
    lines = result.remedy_lines()
    text = "\n".join(lines)

    assert "feat_a" in text
    assert "TRAINING-ONLY" in text and "feat_c" in text
    assert "ORDER DIVERGENCE" in text
    assert "BOOSTER MISMATCH" in text and "index 3" in text
    assert "EXTRA" in text and "feat_b" in text
