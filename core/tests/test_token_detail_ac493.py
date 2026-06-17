# ---
# module: core.tests.test_token_detail_ac493
# sprint: sprint-10
# story: US-49 AC-49.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.token_detail, core.scorer, core.pregrad_features,
#               lightgbm, numpy, pandas, json, pathlib, types, pytest
# ---
"""AC-49.3 — feature vector + score panel tests.

Verifies that the token-detail view surfaces the EXACT feature vector the model
saw + its score from the US-43 BlendScorer / US-44 banked golden vectors —
the parity-checked score, never a re-implemented one ('scores in sync').

H1 ImportError trap:
  Module-level import of build_score_panel — deleting it fails pytest collection.

Structural tests (AST):
  - token_detail.py imports FeatureExtractor from core.feature_extractor
    (Principle #2: feature extraction uses the shared US-30 extractor path)
  - token_detail.py imports compute_pregrad_features from core.pregrad_features
    (the US-30 extractor path calls compute_pregrad_features internally)
  - No network client imports (zero firehose, OFFLINE)

Parity tests (using the US-44 banked golden vectors):
  - build_score_panel produces blend_score matching golden_scores.parquet (atol=0.0)
  - build_score_panel produces label_scores matching golden oracle (atol=0.0)
  - build_score_panel produces label_ranks matching golden oracle (atol=0.0)
  - run-twice identical over the banked fixture

Integration tests:
  - build_token_detail with a scorer + ref_dist includes score_panel in output
  - score_panel contains feature_vector and score keys

Tests:
  test_import_trap_build_score_panel_ac493
  test_token_detail_imports_feature_extractor_ac493
  test_token_detail_imports_compute_pregrad_features_ac493
  test_build_score_panel_no_network_imports_ac493
  test_build_score_panel_matches_golden_blend_score_ac493
  test_build_score_panel_matches_golden_label_scores_ac493
  test_build_score_panel_matches_golden_label_ranks_ac493
  test_build_score_panel_run_twice_identical_ac493
  test_build_token_detail_includes_score_panel_ac493
  test_build_token_detail_score_panel_none_without_scorer_ac493
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — module-level import; deletion/rename fails collection
# ---------------------------------------------------------------------------
from core.dashboard.token_detail import build_score_panel, build_token_detail  # noqa: E402

assert build_score_panel  # H1: deleting build_score_panel fails pytest collection

# ---------------------------------------------------------------------------
# Paths — all committed artefacts
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DIR = _REPO_ROOT / "lake" / "golden" / "golden_scores_v3_2"
_GOLDEN_PARQUET = _GOLDEN_DIR / "golden_scores.parquet"
_BOOSTER_DIR = _GOLDEN_DIR / "boosters"
_FIXTURE_PATH = _REPO_ROOT / "core" / "tests" / "fixtures" / "scorer_feature_fixture_ac431.json"
_TOKEN_DETAIL_PY = _REPO_ROOT / "core" / "dashboard" / "token_detail.py"

_LABELS = ["ctrl", "oracle", "liq"]
_N_SEEDS = 5
_ATOL = 0.0
_RTOL = 0.0

# ---------------------------------------------------------------------------
# Banked lake fixture for integration tests (re-used from AC-49.2 pattern)
# ---------------------------------------------------------------------------

_MINT = "AC493_TOKEN_DETAIL_SCORE_PANEL_MINT"
_SCORE_AT = 30

_BANKED_ROWS: list[dict[str, Any]] = [
    {
        "mint": _MINT, "block_time": 0, "slot": 1, "signature": "s1",
        "price": 0.001000, "vol_sol": 100.0, "side": "buy", "rel": 0.0, "owner": None,
    },
    {
        "mint": _MINT, "block_time": 2, "slot": 2, "signature": "s2",
        "price": 0.001200, "vol_sol": 50.0, "side": "sell", "rel": 2.0, "owner": None,
    },
    {
        "mint": _MINT, "block_time": 10, "slot": 3, "signature": "s3",
        "price": 0.001500, "vol_sol": 80.0, "side": "buy", "rel": 10.0, "owner": None,
    },
]


# ---------------------------------------------------------------------------
# Stub scorer / ref_dist for integration tests (no lightgbm required)
# ---------------------------------------------------------------------------

class _StubScorer:
    def score_single(self, features, ref_dist):
        return {
            "label_scores": {"ctrl": 0.5, "oracle": 0.5, "liq": 0.5},
            "label_ranks": {"ctrl": 0.5, "oracle": 0.5, "liq": 0.5},
            "blend_score": 0.5,
        }


class _StubRefDist:
    pass


# ---------------------------------------------------------------------------
# Session-scoped fixtures (parity tests — require lightgbm)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def feature_fixture():
    """Banked feature inputs (5 synthetic tokens, frozen fixture)."""
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def v3_2_boosters():
    """Load the 15 committed v3.2 boosters (ctrl/oracle/liq × seeds 0–4)."""
    lgb = pytest.importorskip("lightgbm")
    boosters: dict[str, list] = {}
    for label in _LABELS:
        label_bsts = []
        for seed in range(_N_SEEDS):
            bst_path = _BOOSTER_DIR / f"{label}_s{seed}.txt"
            assert bst_path.is_file(), (
                f"AC-49.3: committed booster not found: {bst_path}\n"
                "The v3.2 boosters must be committed under lake/golden/golden_scores_v3_2/boosters/."
            )
            bst = lgb.Booster(model_file=str(bst_path))
            label_bsts.append(bst)
        boosters[label] = label_bsts
    return boosters


@pytest.fixture(scope="session")
def v3_2_model_entry():
    """Minimal model_entry stub matching v3.2's blend manifest (no DB required)."""
    from core.pregrad_features import PRE_FEATURE_NAMES  # noqa: PLC0415

    return SimpleNamespace(
        labels_seeds_manifest={
            "labels": _LABELS,
            "seeds": list(range(_N_SEEDS)),
            "boosters": {
                lbl: [f"boosters/{lbl}_s{s}.txt" for s in range(_N_SEEDS)]
                for lbl in _LABELS
            },
        },
        feature_list=list(PRE_FEATURE_NAMES),
        artifact_dir=str(_BOOSTER_DIR.parent),
    )


@pytest.fixture(scope="session")
def v3_2_scorer(v3_2_model_entry, v3_2_boosters):
    """BlendScorer instantiated with the real v3.2 boosters (the serving path)."""
    from core.scorer import BlendScorer  # noqa: PLC0415

    return BlendScorer(v3_2_model_entry, v3_2_boosters)


@pytest.fixture(scope="session")
def ref_dist_fixture(v3_2_scorer, feature_fixture):
    """ReferenceDistribution built from the same 5-token pool (parity invariant)."""
    from core.scorer import ReferenceDistribution  # noqa: PLC0415

    pool_results = v3_2_scorer.score_pool(feature_fixture)
    return ReferenceDistribution.from_pool_results(pool_results, _LABELS)


@pytest.fixture(scope="session")
def golden_df():
    """Banked golden score vectors as a DataFrame (immutable offline oracle)."""
    pd = pytest.importorskip("pandas")
    assert _GOLDEN_PARQUET.is_file(), (
        f"Golden scores parquet not found at {_GOLDEN_PARQUET}.\n"
        "AC-44.1 must be satisfied (fixture present and committed) before AC-49.3 can run."
    )
    return pd.read_parquet(_GOLDEN_PARQUET)


# ===========================================================================
# (a) H1 ImportError trap test
# ===========================================================================


def test_import_trap_build_score_panel_ac493() -> None:
    """H1: build_score_panel is importable — fails collection if deleted.

    Module-level import above constitutes the primary trap; this test is the
    human-readable layer confirming the symbol resolves to a callable.
    """
    assert callable(build_score_panel)


# ===========================================================================
# (b) Structural / AST tests
# ===========================================================================


def test_token_detail_imports_feature_extractor_ac493() -> None:
    """AST: token_detail.py imports FeatureExtractor from core.feature_extractor.

    Principle #2: feature extraction uses the shared US-30 extractor path —
    no dashboard-local feature derivation.
    """
    src = _TOKEN_DETAIL_PY.read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "core.feature_extractor"
            and any(alias.name == "FeatureExtractor" for alias in node.names)
        ):
            found = True
            break
    assert found, (
        "token_detail.py must import FeatureExtractor from core.feature_extractor "
        "(AC-49.3 Principle #2 — feature extraction uses the shared US-30 path)"
    )


def test_token_detail_imports_compute_pregrad_features_ac493() -> None:
    """AST: token_detail.py imports compute_pregrad_features from core.pregrad_features.

    Structural proof that pregrad feature extraction uses the canonical US-30
    extractor path, not a reimplementation.
    """
    src = _TOKEN_DETAIL_PY.read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "core.pregrad_features"
            and any(alias.name == "compute_pregrad_features" for alias in node.names)
        ):
            found = True
            break
    assert found, (
        "token_detail.py must import compute_pregrad_features from core.pregrad_features "
        "(AC-49.3 — pregrad feature extraction must use the canonical US-30 path)"
    )


def test_build_score_panel_no_network_imports_ac493() -> None:
    """AST: token_detail.py does not import any network client (zero firehose)."""
    src = _TOKEN_DETAIL_PY.read_text()
    for banned in ("requests", "httpx", "aiohttp", "websockets", "urllib", "birdeye"):
        assert banned not in src, (
            f"token_detail.py must NOT import '{banned}' — OFFLINE, zero firehose (AC-49.3)"
        )


# ===========================================================================
# (c) Parity tests — build_score_panel vs golden vectors (require lightgbm)
# ===========================================================================


def test_build_score_panel_matches_golden_blend_score_ac493(
    feature_fixture, v3_2_scorer, ref_dist_fixture, golden_df
) -> None:
    """build_score_panel blend_score matches the golden oracle (atol=0.0).

    The US-43 BlendScorer + ReferenceDistribution (from pool) must reproduce
    the banked v3.2 golden blend_scores exactly — the 'scores in sync' proof
    for the score_panel (AC-49.3 parity gate).

    Tolerance: atol=0.0 — same boosters, same inputs, same reference → same float64.
    """
    mismatches = []
    for i in range(len(feature_fixture)):
        result = build_score_panel(feature_fixture[i], v3_2_scorer, ref_dist_fixture)
        computed = result["score"]["blend_score"]
        golden = float(golden_df.iloc[i]["blend_score"])
        if not np.isclose(computed, golden, atol=_ATOL, rtol=_RTOL, equal_nan=False):
            mismatches.append(
                f"token {i}: computed={computed:.15f} golden={golden:.15f} "
                f"diff={abs(computed - golden):.2e}"
            )
    assert not mismatches, (
        "AC-49.3 score_panel blend_score parity FAILED (atol=0.0):\n"
        + "\n".join(mismatches)
    )


def test_build_score_panel_matches_golden_label_scores_ac493(
    feature_fixture, v3_2_scorer, ref_dist_fixture, golden_df
) -> None:
    """build_score_panel label_scores match the golden oracle (atol=0.0).

    Per-label seed-averaged predictions must reproduce the banked golden vectors.
    """
    mismatches = []
    for i in range(len(feature_fixture)):
        result = build_score_panel(feature_fixture[i], v3_2_scorer, ref_dist_fixture)
        for label in _LABELS:
            col = f"{label}_score"
            computed = result["score"]["label_scores"][label]
            golden = float(golden_df.iloc[i][col])
            if not np.isclose(computed, golden, atol=_ATOL, rtol=_RTOL, equal_nan=False):
                mismatches.append(
                    f"token {i} {label}_score: "
                    f"computed={computed:.15f} golden={golden:.15f} "
                    f"diff={abs(computed - golden):.2e}"
                )
    assert not mismatches, (
        "AC-49.3: label_score parity failed (atol=0.0):\n"
        + "\n".join(mismatches)
    )


def test_build_score_panel_matches_golden_label_ranks_ac493(
    feature_fixture, v3_2_scorer, ref_dist_fixture, golden_df
) -> None:
    """build_score_panel label_ranks match the golden oracle (atol=0.0).

    Per-label percentile ranks must reproduce the banked golden rank vectors.
    """
    mismatches = []
    for i in range(len(feature_fixture)):
        result = build_score_panel(feature_fixture[i], v3_2_scorer, ref_dist_fixture)
        for label in _LABELS:
            col = f"{label}_rank"
            computed = result["score"]["label_ranks"][label]
            golden = float(golden_df.iloc[i][col])
            if not np.isclose(computed, golden, atol=_ATOL, rtol=_RTOL, equal_nan=False):
                mismatches.append(
                    f"token {i} {label}_rank: "
                    f"computed={computed:.15f} golden={golden:.15f} "
                    f"diff={abs(computed - golden):.2e}"
                )
    assert not mismatches, (
        "AC-49.3: label_rank parity failed (atol=0.0):\n"
        + "\n".join(mismatches)
    )


def test_build_score_panel_run_twice_identical_ac493(
    feature_fixture, v3_2_scorer, ref_dist_fixture
) -> None:
    """Two calls to build_score_panel over the same inputs produce identical results.

    AC-49.3 requires run-twice identical: the feature vector and score must be
    byte-for-byte equal across invocations.
    """
    result1 = build_score_panel(feature_fixture[0], v3_2_scorer, ref_dist_fixture)
    result2 = build_score_panel(feature_fixture[0], v3_2_scorer, ref_dist_fixture)
    assert result1 == result2, (
        "AC-49.3: build_score_panel must be run-twice identical — "
        "same inputs must produce byte-for-byte equal feature_vector and score."
    )


# ===========================================================================
# (d) Integration tests — build_token_detail with scorer (no lightgbm needed)
# ===========================================================================


def test_build_token_detail_includes_score_panel_ac493(feature_fixture) -> None:
    """build_token_detail with a scorer + ref_dist includes a non-None score_panel.

    Patches compute_pregrad_features to return a known feature vector so that
    the score_panel is populated regardless of whether _BANKED_ROWS contain
    pre-grad swaps (rel < 0).
    """
    stub_scorer = _StubScorer()
    stub_ref_dist = _StubRefDist()
    rows = copy.deepcopy(_BANKED_ROWS)

    with patch(
        "core.dashboard.token_detail.compute_pregrad_features",
        return_value=copy.deepcopy(feature_fixture[0]),
    ):
        detail = build_token_detail(
            rows, _MINT, 5, _SCORE_AT,
            scorer=stub_scorer,
            ref_dist=stub_ref_dist,
        )

    assert detail["score_panel"] is not None, (
        "score_panel must be non-None when scorer + ref_dist are provided and "
        "compute_pregrad_features returns a valid feature vector"
    )
    assert "feature_vector" in detail["score_panel"], (
        "score_panel must contain 'feature_vector' key (AC-49.3 Principle #2)"
    )
    assert "score" in detail["score_panel"], (
        "score_panel must contain 'score' key (AC-49.3)"
    )
    score = detail["score_panel"]["score"]
    assert "label_scores" in score
    assert "label_ranks" in score
    assert "blend_score" in score


def test_build_token_detail_score_panel_none_without_scorer_ac493() -> None:
    """build_token_detail without scorer returns score_panel=None.

    When no scorer is provided (the default), the score_panel key must exist
    in the returned dict but its value must be None.
    """
    rows = copy.deepcopy(_BANKED_ROWS)
    detail = build_token_detail(rows, _MINT, 5, _SCORE_AT)
    assert "score_panel" in detail, (
        "build_token_detail must always include the 'score_panel' key in its return dict"
    )
    assert detail["score_panel"] is None, (
        "score_panel must be None when no scorer is provided (default behaviour)"
    )
