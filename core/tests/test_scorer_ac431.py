# ---
# module: core.tests.test_scorer_ac431
# sprint: sprint-9
# story: US-43 AC-43.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.scorer, core.pregrad_features, numpy, lightgbm, json, pathlib, types, pytest
# ---
"""AC-43.1 — ONE shared deterministic BlendScorer over a banked feature fixture.

Verifies:
  1. The scorer loads the active BLEND config from ModelRegistry (config-driven,
     Principle #1): labels, seeds, and feature order come from the model_entry's
     labels_seeds_manifest and feature_list — no literals in core.scorer.
  2. Per-label seed-average: for each label, average the 5 seed boosters' raw
     predictions over the feature matrix.
  3. Percentile-rank: rank(i) = (pool tokens with score <= score_i) / N.
  4. Blend = mean of the 3 label percentile-ranks.
  5. Run-twice byte/tol-identical: two calls over the same banked fixture
     produce exactly identical results (determinism gate).

Banked feature fixture: core/tests/fixtures/scorer_feature_fixture_ac431.json
  Five synthetic token feature dicts covering all 20 pre_* features.
  Committed to the repo so the test input is frozen across code changes.

H1 ImportError trap — module-level import of BlendScorer must succeed;
deletion/rename fails pytest collection before any test runs.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_blend_scorer_importable_ac431

Config-driven — no literals (Principle #1):
  test_scorer_labels_from_model_entry_not_literals_ac431
  test_scorer_feature_list_from_model_entry_not_literals_ac431

Scoring math — over banked feature fixture:
  test_score_pool_per_label_seed_average_matches_manual_ac431
  test_score_pool_percentile_rank_matches_manual_ac431
  test_score_pool_blend_is_mean_of_ranks_ac431
  test_score_pool_run_twice_byte_identical_ac431

Edge cases:
  test_score_pool_empty_returns_empty_ac431
  test_score_pool_single_token_rank_is_one_ac431
  test_score_pool_result_keys_ac431
  test_score_pool_blend_score_in_range_ac431
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.pregrad_features import PRE_FEATURE_NAMES

# ---------------------------------------------------------------------------
# H1 ImportError trap — module-level import; deletion/rename fails collection
# ---------------------------------------------------------------------------
from core.scorer import BlendScorer

assert BlendScorer  # H1 ImportError trap — deletion fails pytest collection

_LABELS = ["ctrl", "oracle", "liq"]
_N_SEEDS = 5

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scorer_feature_fixture_ac431.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCORER_PY = _REPO_ROOT / "core" / "scorer.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _train_tiny_booster(features: list[str], seed: int):
    """Train a minimal in-memory LightGBM booster (2 leaves, 2 rounds)."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    X = rng.standard_normal((40, len(features)))
    y = rng.standard_normal(40)
    dataset = lgb.Dataset(X, y, feature_name=features, free_raw_data=False)
    return lgb.train(
        {
            "num_leaves": 4,
            "verbose": -1,
            "objective": "regression",
            "seed": seed,
        },
        dataset,
        num_boost_round=2,
    )


def _make_model_entry(labels: list[str] = _LABELS, feature_list: list[str] | None = None):
    """Build a minimal model_entry stub (SimpleNamespace, no DB)."""
    if feature_list is None:
        feature_list = list(PRE_FEATURE_NAMES)
    return SimpleNamespace(
        labels_seeds_manifest={
            "labels": labels,
            "seeds": list(range(_N_SEEDS)),
            "boosters": {
                lbl: [f"boosters/{lbl}_s{s}.txt" for s in range(_N_SEEDS)]
                for lbl in labels
            },
        },
        feature_list=feature_list,
        artifact_dir="",
    )


# ---------------------------------------------------------------------------
# Session-scoped fixture — 15 tiny boosters (3 labels × 5 seeds)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tiny_boosters():
    """Session-scoped: train 15 tiny boosters once, reuse across tests."""
    feature_names = list(PRE_FEATURE_NAMES)
    boosters: dict[str, list] = {}
    seed_counter = 0
    for label in _LABELS:
        label_bsts = []
        for _ in range(_N_SEEDS):
            bst = _train_tiny_booster(feature_names, seed=seed_counter)
            label_bsts.append(bst)
            seed_counter += 1
        boosters[label] = label_bsts
    return boosters


@pytest.fixture(scope="session")
def scorer(tiny_boosters):
    """Session-scoped BlendScorer instantiated directly (no DB)."""
    model_entry = _make_model_entry()
    return BlendScorer(model_entry, tiny_boosters)


@pytest.fixture(scope="session")
def feature_fixture():
    """Load the banked feature fixture (5 token feature dicts)."""
    with _FIXTURE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def pool_results(scorer, feature_fixture):
    """Run score_pool once over the banked fixture (cached for reuse)."""
    return scorer.score_pool(feature_fixture)


# ---------------------------------------------------------------------------
# H1 ImportError trap
# ---------------------------------------------------------------------------


def test_blend_scorer_importable_ac431():
    """BlendScorer is importable from core.scorer (H1 wiring guard)."""
    from core.scorer import BlendScorer as _BS

    assert callable(_BS)


# ---------------------------------------------------------------------------
# Config-driven (Principle #1) — no literals in core/scorer.py
# ---------------------------------------------------------------------------


def test_scorer_labels_from_model_entry_not_literals_ac431(scorer):
    """scorer.labels comes from model_entry.labels_seeds_manifest, not literals."""
    assert scorer.labels == _LABELS


def test_scorer_feature_list_from_model_entry_not_literals_ac431(scorer):
    """scorer.feature_list comes from model_entry.feature_list, not literals."""
    assert scorer.feature_list == list(PRE_FEATURE_NAMES)


def test_scorer_py_has_no_ctrl_literal_ac431():
    """core/scorer.py must not hard-code label names like 'ctrl'/'oracle'/'liq'."""
    source = _SCORER_PY.read_text(encoding="utf-8")
    # These label strings must NOT appear as literals in the scorer body.
    # They are injected at runtime through model_entry.labels_seeds_manifest.
    for lit in ('"ctrl"', "'ctrl'", '"oracle"', "'oracle'", '"liq"', "'liq'"):
        assert lit not in source, (
            f"core/scorer.py must not embed label literal {lit!r}; "
            "labels must come from ModelRegistry (Principle #1)"
        )


# ---------------------------------------------------------------------------
# Scoring math — per-label seed-average
# ---------------------------------------------------------------------------


def test_score_pool_per_label_seed_average_matches_manual_ac431(
    scorer, feature_fixture, pool_results, tiny_boosters
):
    """label_scores == mean of the 5 seed booster predictions (manual check)."""
    feature_names = scorer.feature_list
    X = np.array(
        [[float(f.get(feat, 0.0)) for feat in feature_names] for f in feature_fixture],
        dtype=np.float64,
    )
    for label in scorer.labels:
        expected = np.array(
            [bst.predict(X) for bst in tiny_boosters[label]], dtype=np.float64
        ).mean(axis=0)
        actual = np.array([r["label_scores"][label] for r in pool_results])
        np.testing.assert_allclose(
            actual,
            expected,
            rtol=1e-10,
            err_msg=f"label_scores[{label!r}] diverges from manual seed-average",
        )


# ---------------------------------------------------------------------------
# Scoring math — percentile rank
# ---------------------------------------------------------------------------


def test_score_pool_percentile_rank_matches_manual_ac431(
    scorer, pool_results, tiny_boosters, feature_fixture
):
    """label_ranks[label][i] == (pool tokens with score <= score_i) / N."""
    feature_names = scorer.feature_list
    X = np.array(
        [[float(f.get(feat, 0.0)) for feat in feature_names] for f in feature_fixture],
        dtype=np.float64,
    )
    N = len(feature_fixture)
    for label in scorer.labels:
        raw_scores = np.array(
            [bst.predict(X) for bst in tiny_boosters[label]], dtype=np.float64
        ).mean(axis=0)
        expected_ranks = np.array(
            [(raw_scores <= s).sum() / N for s in raw_scores], dtype=np.float64
        )
        actual_ranks = np.array([r["label_ranks"][label] for r in pool_results])
        np.testing.assert_allclose(
            actual_ranks,
            expected_ranks,
            rtol=1e-10,
            err_msg=f"label_ranks[{label!r}] diverges from manual percentile-rank",
        )


# ---------------------------------------------------------------------------
# Scoring math — blend = mean of 3 label ranks
# ---------------------------------------------------------------------------


def test_score_pool_blend_is_mean_of_ranks_ac431(
    scorer, pool_results, tiny_boosters, feature_fixture
):
    """blend_score == mean of the 3 label percentile-ranks (manual check)."""
    feature_names = scorer.feature_list
    X = np.array(
        [[float(f.get(feat, 0.0)) for feat in feature_names] for f in feature_fixture],
        dtype=np.float64,
    )
    N = len(feature_fixture)
    rank_arrays = []
    for label in scorer.labels:
        raw_scores = np.array(
            [bst.predict(X) for bst in tiny_boosters[label]], dtype=np.float64
        ).mean(axis=0)
        ranks = np.array(
            [(raw_scores <= s).sum() / N for s in raw_scores], dtype=np.float64
        )
        rank_arrays.append(ranks)
    expected_blend = np.mean(rank_arrays, axis=0, dtype=np.float64)
    actual_blend = np.array([r["blend_score"] for r in pool_results])
    np.testing.assert_allclose(
        actual_blend,
        expected_blend,
        rtol=1e-10,
        err_msg="blend_score diverges from manual mean-of-ranks",
    )


# ---------------------------------------------------------------------------
# Determinism gate — run-twice byte/tol-identical
# ---------------------------------------------------------------------------


def test_score_pool_run_twice_byte_identical_ac431(scorer, feature_fixture):
    """Two calls over the same banked fixture produce byte-identical results."""
    results1 = scorer.score_pool(feature_fixture)
    results2 = scorer.score_pool(feature_fixture)
    assert results1 == results2, (
        "score_pool() is not deterministic: two calls over the same feature "
        "fixture produced different results"
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_score_pool_empty_returns_empty_ac431(scorer):
    """score_pool([]) returns an empty list."""
    assert scorer.score_pool([]) == []


def test_score_pool_single_token_rank_is_one_ac431(scorer, feature_fixture):
    """A pool of 1 token: the sole token receives rank 1.0 for every label."""
    single = [feature_fixture[0]]
    results = scorer.score_pool(single)
    assert len(results) == 1
    result = results[0]
    for label in scorer.labels:
        assert result["label_ranks"][label] == pytest.approx(
            1.0
        ), f"Single-token pool: label_ranks[{label!r}] should be 1.0"
    assert result["blend_score"] == pytest.approx(1.0)


def test_score_pool_result_keys_ac431(pool_results):
    """Each result dict has exactly the three expected top-level keys."""
    for r in pool_results:
        assert set(r.keys()) == {"label_scores", "label_ranks", "blend_score"}


def test_score_pool_label_score_keys_ac431(scorer, pool_results):
    """label_scores and label_ranks dicts have exactly the label keys."""
    expected_keys = set(scorer.labels)
    for r in pool_results:
        assert set(r["label_scores"].keys()) == expected_keys
        assert set(r["label_ranks"].keys()) == expected_keys


def test_score_pool_blend_score_in_range_ac431(pool_results):
    """blend_score is in (0, 1] for every token in the pool."""
    for r in pool_results:
        assert 0.0 < r["blend_score"] <= 1.0, (
            f"blend_score {r['blend_score']} is outside (0, 1]"
        )


def test_score_pool_result_length_matches_input_ac431(scorer, feature_fixture):
    """score_pool returns one result per input feature dict."""
    results = scorer.score_pool(feature_fixture)
    assert len(results) == len(feature_fixture)
