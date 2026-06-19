# ---
# module: core.tests.test_scoring_correctness_us76
# sprint: sprint-14
# story: US-76 (SCORING-CORRECTNESS slice: P1.1, P1.2, P2.4, P2.5)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, core.firehose.spine, core.scorer,
#               core.management.commands.run_firehose, core.pregrad_features,
#               lightgbm, numpy, pandas, pathlib, json, unittest.mock
# ---
"""US-76 SCORING-CORRECTNESS test suite.

Verifies all four parity/gate fixes:

  P1.1 — reference distribution exists + CI guard against pool-of-1 silent-pass.
  P1.2 — 3600s window cap in assemble_pregrad_features.
  P2.4 — >=20 pre-grad swaps secondary gate.
  P2.5 — threshold from meta.json depth_menu, not hardcoded.

Also includes:
  - Pool-of-1 regression test (low-feature token must NOT pass with real ref_dist).
  - Golden-parity test (>60min curve-life fixture at 1e-3 tolerance).
  - Tier-1 replay: assemble a sample of pregrad_enrich.csv mints live-style through
    assemble_pregrad_features (WITH the 3600 cap) and assert crashed == 0.

All tests are offline/deterministic (no network, no firehose).  The ConfigurationError
guard test requires a minimal Django DB (pytest-django) only for PipelineState.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.firehose.spine import assemble_pregrad_features
from core.pregrad_features import PRE_FEATURE_NAMES, compute_pregrad_features

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODEL_DIR = _REPO_ROOT / "models" / "trilly_pregrad_v3_2"
_REF_DIST_PATH = _MODEL_DIR / "reference_dist.json"
_META_PATH = _MODEL_DIR / "meta.json"

# solanatrills paths (read-only reference data — local dev only)
_SOLANATRILLS = Path("/Users/asim/NoIcloud/solanatrills")
_PREGRAD_ENRICH_CSV = _SOLANATRILLS / "analysis" / "graduated" / "pregrad_enrich.csv"
_ARTIFACT_DIR = _SOLANATRILLS / "models" / "trilly_pregrad_v3_2"

_LABELS = ["ctrl", "oracle", "liq"]
_N_SEEDS = 5


# ===========================================================================
# P1.1 — Reference distribution committed + CI guard
# ===========================================================================


def test_reference_dist_json_committed() -> None:
    """reference_dist.json is committed alongside the model artifact (US-76 P1.1).

    The pool-of-1 silent-pass bug requires ref_dist=None to trigger.  Committing
    reference_dist.json and wiring ScoringConfig.reference_dist_path to it blocks
    the None fallback permanently.
    """
    assert _REF_DIST_PATH.is_file(), (
        f"reference_dist.json not found at {_REF_DIST_PATH}.\n"
        "US-76 P1.1: generate and commit models/trilly_pregrad_v3_2/reference_dist.json."
    )


def test_reference_dist_json_loads() -> None:
    """reference_dist.json is valid JSON with the expected label keys."""
    assert _REF_DIST_PATH.is_file(), "reference_dist.json missing (see P1.1)"
    with _REF_DIST_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    label_keys = [k for k in data if not k.startswith("_")]
    assert set(label_keys) == {"ctrl", "oracle", "liq"}, (
        f"reference_dist.json has unexpected label keys: {label_keys}"
    )
    for label in label_keys:
        assert isinstance(data[label], list), f"data[{label!r}] must be a list of floats"
        assert len(data[label]) > 1000, (
            f"data[{label!r}] has only {len(data[label])} entries — expected full corpus"
        )


def test_reference_dist_loads_via_scorer() -> None:
    """ReferenceDistribution.from_file() loads reference_dist.json correctly."""
    from core.scorer import ReferenceDistribution

    ref = ReferenceDistribution.from_file(_REF_DIST_PATH)
    assert set(ref.labels) == {"ctrl", "oracle", "liq"}
    for label in ref.labels:
        assert ref.n_ref[label] > 1000, (
            f"Expected >1000 reference samples for {label!r}, got {ref.n_ref[label]}"
        )


def test_reference_dist_median_rank_near_half() -> None:
    """The median reference score ranks at ~0.5 (sanity check on distribution shape)."""
    from core.scorer import ReferenceDistribution

    ref = ReferenceDistribution.from_file(_REF_DIST_PATH)
    for label in ref.labels:
        median_score = float(np.median(ref._scores[label]))
        rank = ref.percentile_rank(label, median_score)
        assert abs(rank - 0.5) < 0.01, (
            f"Median score for {label!r} ranks at {rank:.4f}, expected near 0.5"
        )


# ---------------------------------------------------------------------------
# ConfigurationError guard (P1.1 — scoring_enabled=True without ref_dist -> fail loudly)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_scoring_guard_raises_when_ref_dist_path_null() -> None:
    """ConfigurationError raised when scoring_enabled=True and reference_dist_path is null.

    US-76 P1.1: the pool-of-1 silent-pass path (ref_dist=None) is blocked at the
    configuration layer.  If scoring_enabled=True and no reference_dist_path is
    wired, the daemon refuses to build the scoring context.
    """
    from core.management.commands.run_firehose import ConfigurationError
    from core.models import PipelineState

    state = PipelineState.get()
    state.scoring_enabled = True
    state.save()

    # Patch get_active_config to return a config with null reference_dist_path.
    mock_config = SimpleNamespace(
        scoring=SimpleNamespace(
            reference_dist_path=None,
            gate="adaptive_topk",
            score_at_elapsed_s=120,
        ),
        trading=SimpleNamespace(paper_size_usd=None),
    )
    mock_model_entry = SimpleNamespace(
        labels_seeds_manifest={"labels": _LABELS, "seeds": list(range(_N_SEEDS)), "boosters": {}},
        feature_list=list(PRE_FEATURE_NAMES),
        artifact_dir="",
    )

    from core.management.commands.run_firehose import FirehoseDaemon

    daemon = FirehoseDaemon.__new__(FirehoseDaemon)

    with (
        patch("core.resolver.get_active_config", return_value=mock_config),
        patch("core.resolver.get_active_model", return_value=mock_model_entry),
        patch("core.scorer.BlendScorer.from_registry", return_value=MagicMock()),
        pytest.raises(ConfigurationError, match="reference_dist_path is null"),
    ):
        daemon._build_scoring_context_sync()


@pytest.mark.django_db
def test_scoring_guard_raises_when_ref_dist_file_missing() -> None:
    """ConfigurationError raised when reference_dist_path is set but file does not exist.

    US-76 P1.1: a missing file is as bad as a null path — the guard must catch both.
    """
    from core.management.commands.run_firehose import ConfigurationError
    from core.models import PipelineState

    state = PipelineState.get()
    state.scoring_enabled = True
    state.save()

    mock_config = SimpleNamespace(
        scoring=SimpleNamespace(
            reference_dist_path="/nonexistent/path/reference_dist.json",
            gate="adaptive_topk",
            score_at_elapsed_s=120,
        ),
        trading=SimpleNamespace(paper_size_usd=None),
    )
    mock_model_entry = SimpleNamespace(
        labels_seeds_manifest={"labels": _LABELS, "seeds": list(range(_N_SEEDS)), "boosters": {}},
        feature_list=list(PRE_FEATURE_NAMES),
        artifact_dir="",
    )

    from core.management.commands.run_firehose import FirehoseDaemon

    daemon = FirehoseDaemon.__new__(FirehoseDaemon)

    with (
        patch("core.resolver.get_active_config", return_value=mock_config),
        patch("core.resolver.get_active_model", return_value=mock_model_entry),
        patch("core.scorer.BlendScorer.from_registry", return_value=MagicMock()),
        pytest.raises(ConfigurationError, match="reference_dist.json is missing"),
    ):
        daemon._build_scoring_context_sync()


# ---------------------------------------------------------------------------
# P1.1 Pool-of-1 regression: low-feature token must NOT pass with real ref_dist
# ---------------------------------------------------------------------------


def test_pool_of_1_regression_low_token_fails() -> None:
    """A low-feature token does NOT pass the 30/day threshold with the real reference.

    US-76 P1.1 pool-of-1 regression: before the fix, score_pool([features])[0]
    returned rank=1.0 for any single-token pool, so every graduation passed the
    hardcoded 0.8 threshold.  With the real reference distribution, a token with
    all-zero features ranks well below 0.7916 (30/day rank_cut).

    This test confirms the regression is fixed by loading the actual reference
    distribution and scoring a zero-feature token.
    """
    pytest.importorskip("lightgbm", reason="lightgbm required for pool-of-1 regression test")
    if not _ARTIFACT_DIR.is_dir():
        pytest.skip(f"solanatrills artifact dir not found at {_ARTIFACT_DIR}")

    import lightgbm as lgb

    from core.scorer import BlendScorer, ReferenceDistribution

    # Build a minimal model entry pointing at the real boosters.
    model_entry = SimpleNamespace(
        labels_seeds_manifest={
            "labels": _LABELS,
            "seeds": list(range(_N_SEEDS)),
            "boosters": {
                lbl: [f"boosters/{lbl}_s{s}.txt" for s in range(_N_SEEDS)]
                for lbl in _LABELS
            },
        },
        feature_list=list(PRE_FEATURE_NAMES),
        artifact_dir=str(_ARTIFACT_DIR),
    )
    boosters: dict[str, list] = {}
    for label in _LABELS:
        bsts = []
        for seed in range(_N_SEEDS):
            bst_path = _ARTIFACT_DIR / "boosters" / f"{label}_s{seed}.txt"
            assert bst_path.is_file(), f"Booster missing: {bst_path}"
            bsts.append(lgb.Booster(model_file=str(bst_path)))
        boosters[label] = bsts
    scorer = BlendScorer(model_entry, boosters)

    # Load the real reference distribution.
    ref = ReferenceDistribution.from_file(_REF_DIST_PATH)

    # Score an all-zero feature token (the weakest possible token).
    zero_features = {feat: 0.0 for feat in PRE_FEATURE_NAMES}
    result = scorer.score_single(zero_features, ref)

    blend = result["blend_score"]
    threshold_30d = 0.7916  # meta.json depth_menu 30/day rank_cut

    # REGRESSION CHECK: an all-zero token must NOT pass the 30/day threshold.
    # Before the fix: blend would be 1.0 (pool-of-1 rank).
    # After the fix: blend is the percentile against 24k+ real tokens.
    assert blend < threshold_30d, (
        f"Pool-of-1 regression NOT fixed: all-zero token scored blend={blend:.4f} >= "
        f"threshold={threshold_30d}.  With the real reference distribution, a zero-feature "
        "token should rank well below 0.7916 (30/day rank_cut).  If blend >= threshold, "
        "the pool-of-1 silent-pass bug is still present."
    )


def test_pool_of_1_single_equals_pool_ref() -> None:
    """score_single(token, ref) == score_pool([token])[0].label_ranks when ref = pool.

    US-76 P1.1 parity invariant: scoring a token alone vs in a pool yields the
    SAME rank against the frozen reference.  Tests that the two paths are equivalent
    when the reference is built from the pool itself.
    """
    pytest.importorskip("lightgbm", reason="lightgbm required for parity test")
    if not _ARTIFACT_DIR.is_dir():
        pytest.skip(f"solanatrills artifact dir not found at {_ARTIFACT_DIR}")

    import lightgbm as lgb

    from core.scorer import BlendScorer, ReferenceDistribution

    model_entry = SimpleNamespace(
        labels_seeds_manifest={
            "labels": _LABELS,
            "seeds": list(range(_N_SEEDS)),
            "boosters": {
                lbl: [f"boosters/{lbl}_s{s}.txt" for s in range(_N_SEEDS)]
                for lbl in _LABELS
            },
        },
        feature_list=list(PRE_FEATURE_NAMES),
        artifact_dir=str(_ARTIFACT_DIR),
    )
    boosters: dict[str, list] = {}
    for label in _LABELS:
        bsts = []
        for seed in range(_N_SEEDS):
            bst_path = _ARTIFACT_DIR / "boosters" / f"{label}_s{seed}.txt"
            bsts.append(lgb.Booster(model_file=str(bst_path)))
        boosters[label] = bsts
    scorer = BlendScorer(model_entry, boosters)

    # Two tokens with distinct non-trivial feature vectors.
    tokens = [
        {feat: float(i % 3) * 0.2 for i, feat in enumerate(PRE_FEATURE_NAMES)},
        {feat: float((i + 1) % 5) * 0.15 for i, feat in enumerate(PRE_FEATURE_NAMES)},
    ]
    pool_results = scorer.score_pool(tokens)
    ref = ReferenceDistribution.from_pool_results(pool_results, scorer.labels)

    for i, tok in enumerate(tokens):
        single = scorer.score_single(tok, ref)
        pool = pool_results[i]
        np.testing.assert_allclose(
            single["blend_score"],
            pool["blend_score"],
            rtol=1e-12,
            err_msg=(
                f"token {i}: score_single vs score_pool blend diverges — "
                f"single={single['blend_score']:.8f} pool={pool['blend_score']:.8f}. "
                "P1.1 parity invariant violated."
            ),
        )


# ===========================================================================
# P1.2 — 3600s window cap in assemble_pregrad_features
# ===========================================================================


def test_3600s_window_cap_excludes_earlier_swaps() -> None:
    """assemble_pregrad_features only uses swaps with rel >= -3600 (P1.2).

    Creates a tape with swaps at rel=-4000 (outside cap), rel=-2000 (inside),
    and rel=-100 (inside).  With the cap, the outside swap is excluded; without
    it, the cohort features differ.  The cap-consistent result must match the
    manual offline-style computation over only the windowed swaps.

    This is the live-vs-offline parity test for the 3600s cap.
    """
    grad_bt = 10000
    # Swaps: some outside the 3600s window (rel < -3600), some inside.
    # We need >= 20 swaps in the window to pass the secondary gate.
    swaps_outside = [
        {
            "block_time": grad_bt - 5000 + i,
            "slot": i,
            "signature": f"out_{i}",
            "side": "buy",
            "owner": f"outside_owner_{i}",
            "vol_sol": 1.0,
            "price": 0.001,
        }
        for i in range(15)  # 15 swaps at rel ~ -5000 to -4985 (outside)
    ]
    swaps_inside = [
        {
            "block_time": grad_bt - 2000 + i * 50,
            "slot": 100 + i,
            "signature": f"in_{i}",
            "side": "buy" if i % 3 != 0 else "sell",
            "owner": f"inside_owner_{i % 8}",
            "vol_sol": 0.5,
            "price": 0.0015,
        }
        for i in range(25)  # 25 swaps at rel ~ -2000 to -800 (inside)
    ]
    all_swaps = swaps_outside + swaps_inside

    # Live path: assemble_pregrad_features WITH 3600s cap.
    live_feats = assemble_pregrad_features(all_swaps, grad_bt)
    assert live_feats is not None, "Expected features from windowed swaps"

    # Offline reference: compute directly over only inside swaps (rel in [-3600, 0)).
    from core.firehose.spine import to_pregrad_swaps
    normalized = to_pregrad_swaps(swaps_inside, grad_bt)
    offline_feats = compute_pregrad_features(normalized)
    assert offline_feats is not None

    # Must match exactly (same code path, same input after cap).
    for feat in PRE_FEATURE_NAMES:
        lv = live_feats.get(feat)
        of = offline_feats.get(feat)
        if lv is None and of is None:
            continue
        if lv is None or of is None:
            # One is NaN — both should be (or neither)
            assert lv is None and of is None, (
                f"Feature {feat!r}: live={lv} offline={of} (one is None)"
            )
            continue
        # Handle NaN comparison
        if isinstance(lv, float) and isinstance(of, float):
            if np.isnan(lv) and np.isnan(of):
                continue
        np.testing.assert_allclose(
            float(lv), float(of), rtol=1e-12,
            err_msg=(
                f"Feature {feat!r} diverges after 3600s cap:\n"
                f"  live={lv!r}  offline={of!r}\n"
                "P1.2: live must match offline after applying the same 3600s cap."
            ),
        )


def test_3600s_cap_swaps_outside_window_ignored() -> None:
    """Swaps at rel < -3600 are excluded from features (direct rel check)."""
    grad_bt = 5000
    # A swap just outside the 3600s cap.
    old_swap = {
        "block_time": grad_bt - 3601,
        "slot": 1,
        "signature": "old",
        "side": "buy",
        "owner": "W0",
        "vol_sol": 100.0,  # very large volume — would dominate if included
        "price": 0.001,
    }
    # 20 swaps just inside the cap.
    fresh_swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": 10 + i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"W{i + 1}",
            "vol_sol": 0.1,
            "price": 0.001,
        }
        for i in range(20)
    ]

    feats_with_old = assemble_pregrad_features([old_swap] + fresh_swaps, grad_bt)
    feats_without_old = assemble_pregrad_features(fresh_swaps, grad_bt)

    # old_swap is W0 with massive buy volume; if included it would change
    # pre_top5_buyer_share, pre_buy_hhi, etc.
    assert feats_with_old is not None
    assert feats_without_old is not None

    # With the 3600s cap, old_swap (rel=-3601) must be excluded.
    np.testing.assert_allclose(
        float(feats_with_old.get("pre_buy_hhi", 0)),
        float(feats_without_old.get("pre_buy_hhi", 0)),
        rtol=1e-12,
        err_msg=(
            "pre_buy_hhi differs between with/without old_swap (rel=-3601) — "
            "the 3600s cap is not excluding the out-of-window swap (P1.2 regression)."
        ),
    )


def test_3600s_cap_at_boundary_included() -> None:
    """A swap at exactly rel=-3600 is INCLUDED (boundary is inclusive at -3600)."""
    grad_bt = 3700
    boundary_swap = {
        "block_time": grad_bt - 3600,  # rel = -3600 exactly
        "slot": 1,
        "signature": "boundary",
        "side": "buy",
        "owner": "B0",
        "vol_sol": 1.0,
        "price": 0.001,
    }
    # 19 more swaps inside the window to reach the 20-swap gate.
    inner_swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": 10 + i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"B{i + 1}",
            "vol_sol": 0.5,
            "price": 0.001,
        }
        for i in range(19)
    ]
    all_swaps = [boundary_swap] + inner_swaps
    feats = assemble_pregrad_features(all_swaps, grad_bt)
    # boundary_swap IS within [-3600, 0) so we have exactly 20 swaps — must produce features.
    assert feats is not None, (
        "assemble_pregrad_features returned None with 20 swaps including boundary rel=-3600. "
        "The window should be [-3600, 0) (inclusive at -3600)."
    )


def test_beyond_3600_excluded_boundary() -> None:
    """A swap at rel=-3601 is EXCLUDED (just outside the 3600s cap)."""
    grad_bt = 3702
    just_outside = {
        "block_time": grad_bt - 3601,  # rel = -3601
        "slot": 1,
        "signature": "outside",
        "side": "buy",
        "owner": "X0",
        "vol_sol": 1.0,
        "price": 0.001,
    }
    # Only 19 swaps inside — so the secondary gate (>=20) should trigger.
    inner_swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": 10 + i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"X{i + 1}",
            "vol_sol": 0.5,
            "price": 0.001,
        }
        for i in range(19)
    ]
    all_swaps = [just_outside] + inner_swaps  # 20 total, but 1 outside the cap
    feats = assemble_pregrad_features(all_swaps, grad_bt)
    # just_outside is excluded by cap -> only 19 in window -> secondary gate triggers -> None
    assert feats is None, (
        "Expected None (secondary gate): just_outside (rel=-3601) must be excluded by the "
        "3600s cap, leaving only 19 windowed swaps which fails the >=20 secondary gate (P1.2)."
    )


# ===========================================================================
# P2.4 — >=20 pre-grad swaps secondary gate
# ===========================================================================


def _make_swaps(n: int, grad_bt: int = 1000) -> list[dict]:
    """Make n pre-grad swap dicts at rel ~ -100 (inside window)."""
    return [
        {
            "block_time": grad_bt - 100 - i,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy" if i % 3 != 0 else "sell",
            "owner": f"W{i % 10}",
            "vol_sol": 1.0,
            "price": 0.001,
        }
        for i in range(n)
    ]


def test_secondary_gate_10_swaps_not_scored() -> None:
    """10 pre-grad swaps -> not scored (secondary gate, P2.4)."""
    swaps = _make_swaps(10)
    feats = assemble_pregrad_features(swaps, 1000)
    assert feats is None, (
        "Expected None (secondary gate skip): 10 pre-grad swaps < min=20 must not score."
    )


def test_secondary_gate_19_swaps_not_scored() -> None:
    """19 pre-grad swaps -> not scored (one below threshold)."""
    swaps = _make_swaps(19)
    feats = assemble_pregrad_features(swaps, 1000)
    assert feats is None, "Expected None: 19 swaps < min=20 must not score (P2.4)."


def test_secondary_gate_20_swaps_scored() -> None:
    """20 pre-grad swaps -> scored (exactly at threshold, P2.4)."""
    swaps = _make_swaps(20)
    feats = assemble_pregrad_features(swaps, 1000)
    assert feats is not None, (
        "Expected features: 20 pre-grad swaps == min=20 must produce features (P2.4)."
    )
    assert len(feats) == len(PRE_FEATURE_NAMES)


def test_secondary_gate_25_swaps_scored() -> None:
    """25 pre-grad swaps -> scored (above threshold, P2.4)."""
    swaps = _make_swaps(25)
    feats = assemble_pregrad_features(swaps, 1000)
    assert feats is not None, "Expected features: 25 swaps > min=20 must score (P2.4)."


def test_secondary_gate_custom_min() -> None:
    """Custom min_pregrad_swaps parameter respected."""
    swaps = _make_swaps(5)
    feats = assemble_pregrad_features(swaps, 1000, min_pregrad_swaps=5)
    assert feats is not None, "Expected features with min_pregrad_swaps=5 and 5 swaps."
    feats2 = assemble_pregrad_features(swaps, 1000, min_pregrad_swaps=6)
    assert feats2 is None, "Expected None with min_pregrad_swaps=6 and 5 swaps."


# ===========================================================================
# P2.5 — threshold from artifact (not hardcoded)
# ===========================================================================


def test_threshold_from_model_30d_returns_0_7916() -> None:
    """_threshold_from_model returns 0.7916 for per_day=30 from v3.2 meta.json (P2.5)."""
    from core.management.commands.run_firehose import _threshold_from_model

    model_entry = SimpleNamespace(artifact_dir=str(_MODEL_DIR))
    threshold = _threshold_from_model(model_entry, per_day=30)
    assert abs(threshold - 0.7916) < 1e-6, (
        f"Expected 0.7916 (30/day rank_cut from meta.json), got {threshold:.6f} (P2.5)."
    )


def test_threshold_from_model_10d_returns_0_9137() -> None:
    """_threshold_from_model returns 0.9137 for per_day=10 from v3.2 meta.json."""
    from core.management.commands.run_firehose import _threshold_from_model

    model_entry = SimpleNamespace(artifact_dir=str(_MODEL_DIR))
    threshold = _threshold_from_model(model_entry, per_day=10)
    assert abs(threshold - 0.9137) < 1e-6, (
        f"Expected 0.9137 (10/day rank_cut), got {threshold:.6f} (P2.5)."
    )


def test_threshold_from_model_not_hardcoded_0_8() -> None:
    """The threshold is NOT 0.8 (the old hardcoded value) for 30/day (P2.5).

    meta.json depth_menu 30/day = 0.7916, not 0.8.  This guards against
    re-introducing the hardcoded magic constant.
    """
    from core.management.commands.run_firehose import _threshold_from_model

    model_entry = SimpleNamespace(artifact_dir=str(_MODEL_DIR))
    threshold = _threshold_from_model(model_entry, per_day=30)
    assert threshold != 0.8, (
        f"threshold={threshold} is the old hardcoded value 0.8 — "
        "P2.5 requires it to come from meta.json depth_menu rank_cut."
    )


def test_threshold_from_model_missing_artifact_dir_returns_fallback() -> None:
    """_threshold_from_model falls back to 0.7916 when artifact_dir is empty."""
    from core.management.commands.run_firehose import _threshold_from_model

    model_entry = SimpleNamespace(artifact_dir="")
    threshold = _threshold_from_model(model_entry, per_day=30)
    assert abs(threshold - 0.7916) < 1e-6, (
        f"Expected fallback 0.7916, got {threshold:.6f}"
    )


def test_threshold_from_model_missing_meta_json_returns_fallback() -> None:
    """_threshold_from_model falls back to 0.7916 when meta.json is not found."""
    from core.management.commands.run_firehose import _threshold_from_model

    model_entry = SimpleNamespace(artifact_dir="/nonexistent/dir/trilly_pregrad_v3_2")
    threshold = _threshold_from_model(model_entry, per_day=30)
    assert abs(threshold - 0.7916) < 1e-6


# ===========================================================================
# Golden parity — >60min curve-life token (P1.2 parity proof)
# ===========================================================================


def test_golden_parity_60min_token_live_vs_offline() -> None:
    """Live assemble_pregrad_features WITH 3600s cap matches offline at 1e-3 tolerance.

    Builds a synthetic >60min-old token (curve life = 4500s, first swap at grad-4500)
    and verifies that:
      1. Live path (assemble_pregrad_features + 3600s cap) excludes swaps at rel < -3600.
      2. The resulting features match the offline reference (direct compute_pregrad_features
         over only the windowed swaps) at float tolerance 1e-3.

    This is the golden-parity check for the >60min cohort (~30% of graduates).
    """
    grad_bt = 10000
    # Token born 4500s before graduation (>60 min old => cap kicks in).
    # Swaps spanning from creation to graduation:
    #   - 8 swaps at rel ~ -4500 to -3700 (outside the 3600s cap -> excluded)
    #   - 25 swaps at rel ~ -3500 to -50 (inside the 3600s cap -> included)
    outside_swaps = [
        {
            "block_time": grad_bt - 4500 + i * 100,
            "slot": i,
            "signature": f"out_{i}",
            "side": "buy",
            "owner": f"EarlyWhale{i}",
            "vol_sol": 5.0,
            "price": 0.0005,
        }
        for i in range(8)  # rel from -4500 to -3700
    ]
    inside_swaps = [
        {
            "block_time": grad_bt - 3500 + i * 130,
            "slot": 100 + i,
            "signature": f"in_{i}",
            "side": "buy" if i % 4 != 0 else "sell",
            "owner": f"InWindow{i % 12}",
            "vol_sol": 0.8,
            "price": 0.001,
        }
        for i in range(25)  # rel from -3500 down to ~ -300
    ]

    all_swaps = outside_swaps + inside_swaps

    # Live path: assemble with 3600s cap.
    live_feats = assemble_pregrad_features(all_swaps, grad_bt)
    assert live_feats is not None, "Expected features from the windowed portion of the tape"

    # Offline reference: compute directly over only the inside swaps.
    from core.firehose.spine import to_pregrad_swaps
    normalized_inside = to_pregrad_swaps(inside_swaps, grad_bt)
    offline_feats = compute_pregrad_features(normalized_inside)
    assert offline_feats is not None

    # Assert near-parity at 1e-3 tolerance (per binding-contract §Q5 / directives §8 Q2).
    mismatches = []
    for feat in PRE_FEATURE_NAMES:
        lv = live_feats.get(feat)
        of = offline_feats.get(feat)
        if lv is None and of is None:
            continue
        if lv is None or of is None:
            mismatches.append(f"{feat}: live={lv} offline={of}")
            continue
        lv_f = float(lv)
        of_f = float(of)
        if np.isnan(lv_f) and np.isnan(of_f):
            continue
        if not np.isclose(lv_f, of_f, atol=1e-3, rtol=1e-3):
            mismatches.append(
                f"{feat}: live={lv_f:.6f} offline={of_f:.6f} diff={abs(lv_f - of_f):.2e}"
            )

    assert not mismatches, (
        "Golden parity FAILED for >60min token (P1.2):\n"
        + "\n".join(f"  {m}" for m in mismatches)
        + "\nLive assemble_pregrad_features (with 3600s cap) must match offline "
        "compute_pregrad_features at tolerance 1e-3."
    )


# ===========================================================================
# Tier-1 replay: assemble a sample of pregrad_enrich.csv mints live-style
# ===========================================================================


def _build_synthetic_tape_from_features(row: dict, grad_bt: int = 10000) -> list[dict]:
    """Build a minimal synthetic tape that can produce approximate features.

    This is a simplified tape builder for crash-testing only — it does NOT
    reproduce exact feature values.  It exists solely to verify that
    assemble_pregrad_features does not crash on realistic feature-range inputs.

    It creates one buy per distinct buyer implied by pre_buyers_first_half +
    pre_buyers_second_half, spread over the 3600s window.
    """
    n_buyers = int(row.get("pre_buyers_first_half") or 5) + int(row.get("pre_buyers_second_half") or 5)
    n_buyers = max(n_buyers, 20)  # ensure we get past the secondary gate
    swaps = []
    for i in range(n_buyers):
        rel = -3500 + int(i * 3000 / n_buyers)
        swaps.append({
            "block_time": grad_bt + rel,
            "slot": i,
            "signature": f"tier1_{i}",
            "side": "buy" if i % 5 != 0 else "sell",
            "owner": f"owner_{i % max(n_buyers // 4, 1)}",
            "vol_sol": 0.5,
            "price": 0.001,
        })
    return swaps


def test_tier1_replay_pregrad_enrich_sample_no_crash() -> None:
    """Tier-1 replay: assemble a sample of pregrad_enrich.csv mints via the live path.

    Builds synthetic tapes through assemble_pregrad_features WITH the 3600s cap
    for a sample of rows from pregrad_enrich.csv and asserts crashed==0.

    Per directives §4: 'crashed > 0 => do not ship'.  Validate the harness itself
    on a known-good case first.

    Skipped when pregrad_enrich.csv is not available (CI without local research data).
    """
    if not _PREGRAD_ENRICH_CSV.is_file():
        pytest.skip(f"pregrad_enrich.csv not found at {_PREGRAD_ENRICH_CSV} — skipping Tier-1 replay")

    import pandas as pd

    df = pd.read_csv(_PREGRAD_ENRICH_CSV).dropna(subset=PRE_FEATURE_NAMES[:5])  # at least 5 features not NaN
    sample = df.head(100)  # first 100 clean rows

    crashed = 0
    skipped = 0
    success = 0

    for _, row in sample.iterrows():
        tape = _build_synthetic_tape_from_features(row.to_dict())
        try:
            feats = assemble_pregrad_features(tape, 10000)
            if feats is None:
                skipped += 1
            else:
                assert len(feats) == len(PRE_FEATURE_NAMES), (
                    f"Feature count mismatch: expected {len(PRE_FEATURE_NAMES)}, got {len(feats)}"
                )
                success += 1
        except Exception as exc:  # noqa: BLE001
            crashed += 1
            print(f"CRASH on row {row.get('mint', '?')}: {exc}")

    assert crashed == 0, (
        f"Tier-1 replay crashed on {crashed}/100 mints. "
        "crashed > 0 means do not ship (directives §4). "
        f"({success} success, {skipped} secondary-gate skips)"
    )


# ===========================================================================
# Meta.json committed alongside reference_dist.json
# ===========================================================================


def test_meta_json_committed() -> None:
    """meta.json is committed alongside reference_dist.json in models/trilly_pregrad_v3_2/."""
    assert _META_PATH.is_file(), (
        f"meta.json not found at {_META_PATH}.\n"
        "Commit meta.json alongside reference_dist.json in models/trilly_pregrad_v3_2/."
    )


def test_meta_json_has_depth_menu_with_30d_rank_cut() -> None:
    """meta.json depth_menu has a 30/day entry with rank_cut=0.7916 (P2.5)."""
    assert _META_PATH.is_file(), "meta.json missing"
    with _META_PATH.open(encoding="utf-8") as fh:
        meta = json.load(fh)
    depth_menu = meta.get("depth_menu", [])
    assert depth_menu, "meta.json depth_menu is empty"
    entry_30d = next((e for e in depth_menu if e.get("per_day") == 30), None)
    assert entry_30d is not None, "No per_day=30 entry in depth_menu"
    assert abs(entry_30d["rank_cut"] - 0.7916) < 1e-6, (
        f"Expected rank_cut=0.7916 for per_day=30, got {entry_30d['rank_cut']}"
    )
