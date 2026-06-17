# ---
# module: core.tests.test_scorer_ac432
# sprint: sprint-9
# story: US-43 AC-43.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.scorer, core.schemas, core.pregrad_features, numpy, lightgbm, json, pathlib, types, pytest
# ---
"""AC-43.2 — Deterministic live single-token scoring against a frozen reference distribution.

Resolves the cutover risk (oracle §4.2 'the gap most likely to surprise'):
  - Live serving scores ONE token at its graduation instant with no same-day pool.
  - ReferenceDistribution provides frozen per-label score arrays (banked from
    training) so live percentile ranks are reproducible and config-driven.
  - ScoringConfig.reference_dist_path (Principle #1) points to the banked file.

Two properties verified:

  1. DETERMINISM: score_single(token, ref_dist) called twice over the same inputs
     produces byte-identical results.

  2. PARITY: if the reference distribution is built from the same pool that
     score_pool() ranked against (ReferenceDistribution.from_pool_results(pool_results)),
     then score_single(pool[i], ref_dist)["label_ranks"] == score_pool(pool)[i]["label_ranks"]
     for every token i.  The pool-based formula and the reference formula are the
     same count-based operation; they yield identical values when ref == pool scores.

H1 ImportError trap — module-level imports of ReferenceDistribution and
score_single anchor both symbols; deletion/rename fails pytest collection.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_reference_distribution_importable_ac432
  test_score_single_method_exists_ac432

Config-driven (Principle #1 — ScoringConfig.reference_dist_path):
  test_scoring_config_has_reference_dist_path_field_ac432
  test_scoring_config_reference_dist_path_defaults_none_ac432
  test_scoring_config_accepts_reference_dist_path_ac432

ReferenceDistribution construction and serialization:
  test_reference_distribution_from_dict_ac432
  test_reference_distribution_to_dict_round_trip_ac432
  test_reference_distribution_from_pool_results_ac432
  test_reference_distribution_from_file_ac432
  test_reference_distribution_labels_property_ac432
  test_reference_distribution_n_ref_property_ac432

Percentile-rank math:
  test_reference_distribution_percentile_rank_midpoint_ac432
  test_reference_distribution_percentile_rank_max_is_one_ac432
  test_reference_distribution_percentile_rank_min_above_zero_ac432
  test_reference_distribution_percentile_rank_empty_returns_zero_ac432

score_single results:
  test_score_single_result_keys_ac432
  test_score_single_label_keys_ac432
  test_score_single_blend_in_range_ac432

DETERMINISM gate:
  test_score_single_run_twice_byte_identical_ac432

PARITY gate (single-token vs pool-based for same inputs + reference):
  test_score_single_parity_with_score_pool_label_ranks_ac432
  test_score_single_parity_with_score_pool_blend_score_ac432
  test_score_single_parity_with_score_pool_label_scores_ac432
  test_score_single_parity_all_tokens_in_pool_ac432
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.pregrad_features import PRE_FEATURE_NAMES
from core.schemas import ScoringConfig

# ---------------------------------------------------------------------------
# H1 ImportError trap — deletion/rename fails pytest collection
# ---------------------------------------------------------------------------
from core.scorer import BlendScorer, ReferenceDistribution

assert ReferenceDistribution  # H1: deleting ReferenceDistribution fails collection
assert hasattr(BlendScorer, "score_single")  # H1: deleting score_single fails collection

_LABELS = ["ctrl", "oracle", "liq"]
_N_SEEDS = 5

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scorer_feature_fixture_ac431.json"
_REF_DIST_FIXTURE = Path(__file__).parent / "fixtures" / "reference_dist_ac432.json"


# ---------------------------------------------------------------------------
# Helpers (mirrors AC-43.1 helpers — session-scope, no DB)
# ---------------------------------------------------------------------------


def _train_tiny_booster(features: list[str], seed: int):
    """Train a minimal in-memory LightGBM booster (4 leaves, 2 rounds)."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    X = rng.standard_normal((40, len(features)))
    y = rng.standard_normal(40)
    dataset = lgb.Dataset(X, y, feature_name=features, free_raw_data=False)
    return lgb.train(
        {"num_leaves": 4, "verbose": -1, "objective": "regression", "seed": seed},
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
# Session-scoped fixtures
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
    """Session-scoped BlendScorer (no DB)."""
    model_entry = _make_model_entry()
    return BlendScorer(model_entry, tiny_boosters)


@pytest.fixture(scope="session")
def feature_fixture():
    """Load the AC-43.1 banked feature fixture (5 token feature dicts)."""
    with _FIXTURE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def pool_results(scorer, feature_fixture):
    """score_pool() results over the banked fixture (cached for reuse)."""
    return scorer.score_pool(feature_fixture)


@pytest.fixture(scope="session")
def ref_dist_from_pool(scorer, pool_results):
    """ReferenceDistribution built from the pool's own label scores (parity test)."""
    return ReferenceDistribution.from_pool_results(pool_results, scorer.labels)


# ---------------------------------------------------------------------------
# H1 ImportError trap tests
# ---------------------------------------------------------------------------


def test_reference_distribution_importable_ac432():
    """ReferenceDistribution is importable from core.scorer (H1 wiring guard)."""
    from core.scorer import ReferenceDistribution as _RD

    assert callable(_RD)


def test_score_single_method_exists_ac432():
    """BlendScorer.score_single exists and is callable (H1 wiring guard)."""
    assert callable(getattr(BlendScorer, "score_single", None))


# ---------------------------------------------------------------------------
# Config-driven (Principle #1 — ScoringConfig.reference_dist_path)
# ---------------------------------------------------------------------------


def test_scoring_config_has_reference_dist_path_field_ac432():
    """ScoringConfig has a reference_dist_path field (config-driven, Principle #1)."""
    fields = ScoringConfig.model_fields
    assert "reference_dist_path" in fields, (
        "ScoringConfig must have reference_dist_path field (AC-43.2 config-driven contract)"
    )


def test_scoring_config_reference_dist_path_defaults_none_ac432():
    """ScoringConfig.reference_dist_path defaults to None (optional, safe default)."""
    cfg = ScoringConfig(score_at_elapsed_s=120, window_s=1800)
    assert cfg.reference_dist_path is None


def test_scoring_config_accepts_reference_dist_path_ac432():
    """ScoringConfig accepts a non-None reference_dist_path string."""
    cfg = ScoringConfig(
        score_at_elapsed_s=120,
        window_s=1800,
        reference_dist_path="/fixtures/reference_dist_ac432.json",
    )
    assert cfg.reference_dist_path == "/fixtures/reference_dist_ac432.json"


# ---------------------------------------------------------------------------
# ReferenceDistribution construction and serialization
# ---------------------------------------------------------------------------


def test_reference_distribution_from_dict_ac432():
    """ReferenceDistribution.from_dict() constructs from a plain dict."""
    data = {"ctrl": [0.1, 0.5, 0.9], "oracle": [0.2, 0.6, 1.0], "liq": [0.0, 0.4, 0.8]}
    ref = ReferenceDistribution.from_dict(data)
    assert ref.labels == ["ctrl", "oracle", "liq"]
    for label in ref.labels:
        assert ref.n_ref[label] == 3


def test_reference_distribution_to_dict_round_trip_ac432():
    """to_dict() / from_dict() are inverse operations (round-trip fidelity)."""
    data = {"ctrl": [0.1, 0.3, 0.7], "oracle": [0.2, 0.5, 0.8], "liq": [0.05, 0.4, 0.9]}
    ref = ReferenceDistribution.from_dict(data)
    serialized = ref.to_dict()
    restored = ReferenceDistribution.from_dict(serialized)
    for label in ref.labels:
        np.testing.assert_array_equal(
            np.sort(np.array(data[label])),
            np.sort(np.array(serialized[label])),
        )
        np.testing.assert_array_equal(
            ref._scores[label],
            restored._scores[label],
        )


def test_reference_distribution_from_pool_results_ac432(pool_results, scorer):
    """from_pool_results() builds one array per label from score_pool() output."""
    ref = ReferenceDistribution.from_pool_results(pool_results, scorer.labels)
    assert set(ref.labels) == set(scorer.labels)
    for label in scorer.labels:
        assert ref.n_ref[label] == len(pool_results)


def test_reference_distribution_from_file_ac432():
    """from_file() loads the banked reference fixture (AC-43.2 canonical fixture)."""
    ref = ReferenceDistribution.from_file(_REF_DIST_FIXTURE)
    assert set(ref.labels) == {"ctrl", "oracle", "liq"}
    for label in ref.labels:
        assert ref.n_ref[label] == 30, (
            f"Expected 30 reference samples for {label!r}, got {ref.n_ref[label]}"
        )


def test_reference_distribution_labels_property_ac432():
    """labels property returns the expected label names."""
    ref = ReferenceDistribution.from_dict({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert set(ref.labels) == {"a", "b"}


def test_reference_distribution_n_ref_property_ac432():
    """n_ref property returns the number of samples per label."""
    ref = ReferenceDistribution.from_dict({"ctrl": list(range(10)), "oracle": list(range(15))})
    assert ref.n_ref["ctrl"] == 10
    assert ref.n_ref["oracle"] == 15


# ---------------------------------------------------------------------------
# Percentile-rank math
# ---------------------------------------------------------------------------


def test_reference_distribution_percentile_rank_midpoint_ac432():
    """percentile_rank() is correct for a score that splits the reference in half."""
    # 10 scores: 0,1,...,9; rank of score=5 = (0,1,2,3,4,5 all <=5) / 10 = 6/10
    ref = ReferenceDistribution.from_dict({"ctrl": list(range(10))})
    rank = ref.percentile_rank("ctrl", 5.0)
    assert rank == pytest.approx(6 / 10)


def test_reference_distribution_percentile_rank_max_is_one_ac432():
    """percentile_rank() returns 1.0 for the maximum value in the reference."""
    ref = ReferenceDistribution.from_dict({"ctrl": [1.0, 2.0, 3.0, 4.0, 5.0]})
    assert ref.percentile_rank("ctrl", 5.0) == pytest.approx(1.0)


def test_reference_distribution_percentile_rank_min_above_zero_ac432():
    """percentile_rank() returns > 0 for the minimum value in the reference."""
    ref = ReferenceDistribution.from_dict({"ctrl": [1.0, 2.0, 3.0]})
    rank = ref.percentile_rank("ctrl", 1.0)
    assert rank == pytest.approx(1 / 3)


def test_reference_distribution_percentile_rank_empty_returns_zero_ac432():
    """percentile_rank() returns 0.0 when the reference array is empty."""
    ref = ReferenceDistribution({"ctrl": np.array([], dtype=np.float64)})
    assert ref.percentile_rank("ctrl", 99.0) == 0.0


# ---------------------------------------------------------------------------
# score_single result structure
# ---------------------------------------------------------------------------


def test_score_single_result_keys_ac432(scorer, feature_fixture, ref_dist_from_pool):
    """score_single() returns a dict with exactly the three expected top-level keys."""
    result = scorer.score_single(feature_fixture[0], ref_dist_from_pool)
    assert set(result.keys()) == {"label_scores", "label_ranks", "blend_score"}


def test_score_single_label_keys_ac432(scorer, feature_fixture, ref_dist_from_pool):
    """score_single() label_scores and label_ranks have exactly the label keys."""
    result = scorer.score_single(feature_fixture[0], ref_dist_from_pool)
    expected = set(scorer.labels)
    assert set(result["label_scores"].keys()) == expected
    assert set(result["label_ranks"].keys()) == expected


def test_score_single_blend_in_range_ac432(scorer, feature_fixture, ref_dist_from_pool):
    """score_single() blend_score is in (0, 1] for every token in the fixture."""
    for feat in feature_fixture:
        result = scorer.score_single(feat, ref_dist_from_pool)
        assert 0.0 < result["blend_score"] <= 1.0, (
            f"blend_score {result['blend_score']} is outside (0, 1]"
        )


# ---------------------------------------------------------------------------
# DETERMINISM gate (AC-43.2 requirement: reproducible)
# ---------------------------------------------------------------------------


def test_score_single_run_twice_byte_identical_ac432(scorer, feature_fixture, ref_dist_from_pool):
    """Two calls to score_single() over the same inputs produce byte-identical results.

    Satisfies the AC-43.2 determinism requirement: 'reproducible and parity-checkable'.
    """
    token = feature_fixture[0]
    result1 = scorer.score_single(token, ref_dist_from_pool)
    result2 = scorer.score_single(token, ref_dist_from_pool)
    assert result1 == result2, (
        "score_single() is not deterministic: two calls over the same inputs "
        "produced different results (AC-43.2 determinism gate)"
    )


# ---------------------------------------------------------------------------
# PARITY gate (AC-43.2 requirement: matches pool-based computation)
# ---------------------------------------------------------------------------


def test_score_single_parity_with_score_pool_label_scores_ac432(
    scorer, feature_fixture, pool_results, ref_dist_from_pool
):
    """score_single() label_scores match score_pool() label_scores for the same token.

    Per-label seed-average is identical regardless of pool vs single-token path,
    since both compute predictions over the same feature matrix.
    """
    for i, feat in enumerate(feature_fixture):
        single = scorer.score_single(feat, ref_dist_from_pool)
        for label in scorer.labels:
            np.testing.assert_allclose(
                single["label_scores"][label],
                pool_results[i]["label_scores"][label],
                rtol=1e-12,
                err_msg=(
                    f"label_scores[{label!r}] diverges between score_single and "
                    f"score_pool for token index {i} (AC-43.2 parity gate)"
                ),
            )


def test_score_single_parity_with_score_pool_label_ranks_ac432(
    scorer, feature_fixture, pool_results, ref_dist_from_pool
):
    """score_single() label_ranks match score_pool() label_ranks for the same inputs+reference.

    This is the core parity invariant (AC-43.2): when ref_dist is built from
    from_pool_results(pool_results), the reference contains the same label-score
    values as the pool, so the count-based percentile-rank formula yields identical
    values whether applied to the pool or the reference distribution.
    """
    for i, feat in enumerate(feature_fixture):
        single = scorer.score_single(feat, ref_dist_from_pool)
        for label in scorer.labels:
            np.testing.assert_allclose(
                single["label_ranks"][label],
                pool_results[i]["label_ranks"][label],
                rtol=1e-12,
                err_msg=(
                    f"label_ranks[{label!r}] diverges between score_single (ref) and "
                    f"score_pool (pool) for token index {i} — "
                    f"expected {pool_results[i]['label_ranks'][label]:.6f}, "
                    f"got {single['label_ranks'][label]:.6f} (AC-43.2 parity gate)"
                ),
            )


def test_score_single_parity_with_score_pool_blend_score_ac432(
    scorer, feature_fixture, pool_results, ref_dist_from_pool
):
    """score_single() blend_score matches score_pool() blend_score (AC-43.2 parity gate)."""
    for i, feat in enumerate(feature_fixture):
        single = scorer.score_single(feat, ref_dist_from_pool)
        np.testing.assert_allclose(
            single["blend_score"],
            pool_results[i]["blend_score"],
            rtol=1e-12,
            err_msg=(
                f"blend_score diverges between score_single (ref) and score_pool (pool) "
                f"for token index {i} — "
                f"expected {pool_results[i]['blend_score']:.6f}, "
                f"got {single['blend_score']:.6f} (AC-43.2 parity gate)"
            ),
        )


def test_score_single_parity_all_tokens_in_pool_ac432(
    scorer, feature_fixture, pool_results, ref_dist_from_pool
):
    """Parity holds for ALL tokens in the pool, not just token 0 (AC-43.2).

    This is the primary parity test: every token in the banked fixture, when
    scored single-token against a reference built from the pool, produces the
    same blend_score as the pool-based computation.
    """
    mismatches = []
    for i, feat in enumerate(feature_fixture):
        single = scorer.score_single(feat, ref_dist_from_pool)
        pool = pool_results[i]
        if not np.isclose(single["blend_score"], pool["blend_score"], rtol=1e-12):
            mismatches.append(
                f"token {i}: single={single['blend_score']:.8f} pool={pool['blend_score']:.8f}"
            )
    assert not mismatches, (
        "score_single parity failed for tokens:\n"
        + "\n".join(mismatches)
        + "\n(AC-43.2 parity gate: same inputs + reference must yield same blend_score)"
    )
