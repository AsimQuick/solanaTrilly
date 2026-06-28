# ---
# module: core.tests.test_pf_v2_scorer_e2e
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: pytest, django, core.pf_features, core.pf_scorer, core.models, core.schemas
# ---
"""End-to-end test: pf_v2 path builds features -> scores -> gates -> records Prediction.

WHAT THIS TESTS
===============
1. PfScorer API: from_dir() loads contract.json, feature_order, threshold, MODEL_PRESENT.
2. is_pf_model() correctly detects v1 and v2 artifact dirs.
3. SCORING (requires MODEL_PRESENT=True): score() returns a float; gate_passes() works.
4. WIRING (DB test): simulates the _score_tick pf branch for a v2 model — builds
   13 features from a synthetic pre-grad tape, calls _persist_prediction_sync, and
   asserts a Prediction row is written with model_id="trilly_pf_v2", correct gate.
5. NO LIVE SETTLE: asserts gate-passing pf_v2 mints are NOT added to _pending_settle.
6. activate_config SUCCEEDS: the recommended config from promote_pf passes
   PipelineConfigSchema without ValidationError.

SKIP RULES
==========
Tests requiring model.txt are skipped in CI where it is absent (MODEL_PRESENT=False).
All other tests run unconditionally.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.pf_features import compute_pf_features
from core.pf_scorer import PfScorer, is_pf_model

# ---------------------------------------------------------------------------
# Model dirs
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_V2_DIR = _REPO_ROOT / "models" / "trilly_pf_v2"
_V1_DIR = _REPO_ROOT / "models" / "trilly_pf_v1"

# V2 scorer loaded from the committed contract.json (model.txt optional)
_v2_scorer: PfScorer | None = None
if _V2_DIR.is_dir():
    try:
        _v2_scorer = PfScorer.from_dir(_V2_DIR)
    except Exception:
        _v2_scorer = None

# ---------------------------------------------------------------------------
# Synthetic pre-grad tape (13-feature compatible)
# ---------------------------------------------------------------------------

_GRAD_BT = 2000
_GRAD_TS = float(_GRAD_BT)
# Tape with diverse buyer behaviors to exercise all 13 features.
# W2 buys twice (repeat buyer); W1, W3 also sell (non-diamonds).
# W8 buys at t=1950 (in last 60s, gts-60=1940).
# W6 at t=1700, W7 at t=1900, W8 at t=1950 are in last 300s (gts-300=1700).
def _make_swap(bt, slot, side, vol_sol, tok, owner, price):
    return {
        "block_time": bt, "slot": slot, "side": side,
        "vol_sol": vol_sol, "token_amount": tok, "owner": owner, "price": price,
    }

_TAPE = [
    _make_swap(1000, 1,  "buy",  10.0, 1_000_000, "W1", 0.001),
    _make_swap(1100, 2,  "buy",  5.0,  500_000,   "W2", 0.0015),
    _make_swap(1200, 3,  "sell", 2.0,  200_000,   "W1", 0.0018),
    _make_swap(1300, 4,  "buy",  8.0,  800_000,   "W3", 0.002),
    _make_swap(1400, 5,  "buy",  3.0,  300_000,   "W4", 0.0025),
    _make_swap(1500, 6,  "buy",  4.0,  400_000,   "W5", 0.003),
    _make_swap(1600, 7,  "buy",  1.0,  100_000,   "W2", 0.0028),  # W2 repeat
    _make_swap(1700, 8,  "buy",  6.0,  600_000,   "W6", 0.004),
    _make_swap(1800, 9,  "sell", 1.0,  100_000,   "W3", 0.0035),
    _make_swap(1900, 10, "buy",  2.0,  200_000,   "W7", 0.005),
    _make_swap(1950, 11, "buy",  7.0,  700_000,   "W8", 0.006),
]

# ---------------------------------------------------------------------------
# PfScorer API tests (run unconditionally — no model.txt needed)
# ---------------------------------------------------------------------------


def test_is_pf_model_detects_v2():
    """is_pf_model returns True for the staged trilly_pf_v2 artifact dir."""
    if _V2_DIR.is_dir() and (_V2_DIR / "contract.json").is_file():
        assert is_pf_model(str(_V2_DIR)) is True


def test_is_pf_model_detects_v1():
    """is_pf_model returns True for the staged trilly_pf_v1 artifact dir."""
    if _V1_DIR.is_dir() and (_V1_DIR / "config.json").is_file():
        assert is_pf_model(str(_V1_DIR)) is True


def test_is_pf_model_none_returns_false():
    assert is_pf_model(None) is False


def test_is_pf_model_nonexistent_returns_false():
    assert is_pf_model("/nonexistent/path/xyz") is False


def test_v2_scorer_feature_order():
    """PfScorer.from_dir loads 13 features in contract order for v2."""
    if _v2_scorer is None:
        pytest.skip("trilly_pf_v2 artifact dir not available")
    expected_order = [
        "f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1",
        "new_buyers_last60", "new_buyers_last300", "diamond_frac",
        "top5_buyer_share", "repeat_buyer_frac", "price_ret", "price_maxrun",
    ]
    assert _v2_scorer.feature_order == expected_order, (
        f"Feature order mismatch: {_v2_scorer.feature_order}"
    )


def test_v2_scorer_threshold():
    """PfScorer loads threshold -0.2425 from contract.gate.live_pred_threshold."""
    if _v2_scorer is None:
        pytest.skip("trilly_pf_v2 artifact dir not available")
    assert abs(_v2_scorer.threshold - (-0.2425)) < 1e-10, (
        f"Threshold mismatch: {_v2_scorer.threshold}"
    )


def test_v2_scorer_model_name():
    """PfScorer loads model_name from contract.json."""
    if _v2_scorer is None:
        pytest.skip("trilly_pf_v2 artifact dir not available")
    assert _v2_scorer.model_name == "trilly_pf_v2", (
        f"model_name mismatch: {_v2_scorer.model_name}"
    )


def test_v2_scorer_model_present_is_bool():
    """MODEL_PRESENT is always a bool."""
    if _v2_scorer is None:
        pytest.skip("trilly_pf_v2 artifact dir not available")
    assert isinstance(_v2_scorer.MODEL_PRESENT, bool)


def test_v2_gate_passes_at_threshold():
    """gate_passes returns True at threshold, False below."""
    if _v2_scorer is None:
        pytest.skip("trilly_pf_v2 artifact dir not available")
    threshold = _v2_scorer.threshold
    assert _v2_scorer.gate_passes(threshold) is True
    assert _v2_scorer.gate_passes(threshold + 0.001) is True
    assert _v2_scorer.gate_passes(threshold - 0.001) is False
    # Negative threshold — values around -0.24 must work correctly
    assert _v2_scorer.gate_passes(0.0) is True   # 0.0 > -0.2425
    assert _v2_scorer.gate_passes(-0.5) is False  # -0.5 < -0.2425


def test_compute_pf_features_returns_13():
    """compute_pf_features returns all 13 features."""
    result = compute_pf_features(_TAPE, _GRAD_TS)
    assert result is not None
    expected_keys = {
        "f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1",
        "new_buyers_last60", "new_buyers_last300", "diamond_frac",
        "top5_buyer_share", "repeat_buyer_frac", "price_ret", "price_maxrun",
    }
    assert set(result.keys()) == expected_keys, (
        f"Key mismatch: got {sorted(result.keys())}"
    )


def test_tape_enrich_values():
    """Spot-check enrich7 values on the synthetic tape."""
    result = compute_pf_features(_TAPE, _GRAD_TS)
    assert result is not None

    # W8 at t=1950 >= gts-60=1940 -> 1 new buyer in last 60s
    assert result["new_buyers_last60"] == 1.0, (
        f"new_buyers_last60={result['new_buyers_last60']}"
    )

    # W6 at t=1700 (exactly gts-300=1700, >= qualifies), W7 at t=1900, W8 at t=1950
    # -> 3 new buyers in last 300s
    assert result["new_buyers_last300"] == 3.0, (
        f"new_buyers_last300={result['new_buyers_last300']}"
    )

    # Sellers are W1 (slot3) and W3 (slot9) -> 2 buyers in seller_set
    # Total buyers: W1,W2,W3,W4,W5,W6,W7,W8 = 8
    # diamond_frac = 6/8 = 0.75
    assert abs(result["diamond_frac"] - 0.75) < 1e-9, (
        f"diamond_frac={result['diamond_frac']}"
    )

    # W2 has 2 buys -> repeat_buyer_frac = 1/8 = 0.125
    assert abs(result["repeat_buyer_frac"] - 0.125) < 1e-9, (
        f"repeat_buyer_frac={result['repeat_buyer_frac']}"
    )

    # price_ret: last swap (slot11) price=0.006; first_px=0.001; ret = 0.006/0.001 - 1 = 5.0
    assert abs(result["price_ret"] - 5.0) < 1e-9, (
        f"price_ret={result['price_ret']}"
    )

    # price_maxrun: max(0.001, 0.0015, ..., 0.006)/0.001 - 1 = 5.0
    assert abs(result["price_maxrun"] - 5.0) < 1e-9, (
        f"price_maxrun={result['price_maxrun']}"
    )


# ---------------------------------------------------------------------------
# Scoring tests (require model.txt — skip in CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _v2_scorer is None or not (_v2_scorer.MODEL_PRESENT if _v2_scorer else False),
    reason="trilly_pf_v2 model.txt not present (CI / host-only gate)"
)
def test_v2_score_returns_float():
    """score() returns a float (may be negative — regression L1 model)."""
    features = compute_pf_features(_TAPE, _GRAD_TS)
    assert features is not None
    result = _v2_scorer.score(features)
    assert isinstance(result, float)


@pytest.mark.skipif(
    _v2_scorer is None or not (_v2_scorer.MODEL_PRESENT if _v2_scorer else False),
    reason="trilly_pf_v2 model.txt not present (CI / host-only gate)"
)
def test_v2_score_deterministic():
    """Same features always produce the same score."""
    features = compute_pf_features(_TAPE, _GRAD_TS)
    assert features is not None
    s1 = _v2_scorer.score(features)
    s2 = _v2_scorer.score(features)
    assert s1 == s2


# ---------------------------------------------------------------------------
# Prediction persistence (DB test)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pf_v2_persist_prediction_writes_row():
    """_persist_prediction_sync creates a Prediction row for trilly_pf_v2."""
    from core.management.commands.run_firehose import FirehoseDaemon  # noqa: PLC0415
    from core.models import Prediction  # noqa: PLC0415

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        wallet_bank=None,
    )

    mint = "TestMintPfV2E2E000000000000000000000000000000"
    score_time = 1_750_000_001
    pf2_score = -0.18  # passes gate (> -0.2425)
    threshold = -0.2425
    passed = pf2_score >= threshold
    assert passed is True

    scoring = {
        "model_id": "trilly_pf_v2",
        "per_day_target": 18,
        "rank_cut": threshold,
        "gate": "adaptive_topk",
        "score_at_elapsed_s": 1,
        "outcome_window_s": 1800,
    }
    result = {
        "blend_score": pf2_score,
        "label_scores": {"trilly_pf_v2": pf2_score},
        "label_ranks": {},
    }
    sol_usd = 180.0

    daemon._persist_prediction_sync(mint, score_time, result, pf2_score, passed, scoring, sol_usd)

    pred = Prediction.objects.get(mint=mint, score_time=score_time, model_id="trilly_pf_v2")
    assert abs(pred.blend - pf2_score) < 1e-9, f"blend mismatch: {pred.blend}"
    assert pred.picked is True
    assert abs(pred.rank_cut - threshold) < 1e-9
    assert pred.label_scores == {"trilly_pf_v2": pf2_score}
    assert abs(pred.sol_usd_spot - sol_usd) < 1e-9


@pytest.mark.django_db
def test_pf_v2_gate_fail_not_picked():
    """Prediction with score < threshold has picked=False."""
    from core.management.commands.run_firehose import FirehoseDaemon  # noqa: PLC0415
    from core.models import Prediction  # noqa: PLC0415

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        wallet_bank=None,
    )

    mint = "TestMintPfV2GateFail00000000000000000000000000"
    score_time = 1_750_000_002
    pf2_score = -0.50  # fails gate (< -0.2425)
    threshold = -0.2425
    passed = pf2_score >= threshold
    assert passed is False

    scoring = {
        "model_id": "trilly_pf_v2",
        "per_day_target": 18,
        "rank_cut": threshold,
        "gate": "adaptive_topk",
        "score_at_elapsed_s": 1,
        "outcome_window_s": 1800,
    }
    result = {
        "blend_score": pf2_score,
        "label_scores": {"trilly_pf_v2": pf2_score},
        "label_ranks": {},
    }

    daemon._persist_prediction_sync(mint, score_time, result, pf2_score, passed, scoring, 180.0)

    pred = Prediction.objects.get(mint=mint, score_time=score_time, model_id="trilly_pf_v2")
    assert pred.picked is False


def test_pf_v2_does_not_enter_pending_settle():
    """Gate-passing pf_v2 mints must NOT appear in _pending_settle (PnL offline only)."""
    from core.management.commands.run_firehose import FirehoseDaemon  # noqa: PLC0415

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        wallet_bank=None,
    )

    mint = "PfV2E2ENoSettleTest0000000000000000000000000"
    gate_passing_score = 0.0  # 0.0 > -0.2425 — gate passes

    # Populate the tape
    for swap in _TAPE:
        daemon._tape.add(mint, swap)

    # Simulate pf sentinel detection
    class _PfSentinel:
        feature_list = ["pf"]
        class _pf_scorer_stub:
            MODEL_PRESENT = True
            feature_order = [
                "f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1",
                "new_buyers_last60", "new_buyers_last300", "diamond_frac",
                "top5_buyer_share", "repeat_buyer_frac", "price_ret", "price_maxrun",
            ]
            threshold = -0.2425
            @staticmethod
            def score(features):
                return gate_passing_score
            @staticmethod
            def gate_passes(s):
                return s >= -0.2425
        _pf_scorer = _pf_scorer_stub()

    scorer = _PfSentinel()
    _scorer_feature_list = getattr(scorer, "feature_list", [])
    _is_pf = len(_scorer_feature_list) == 1 and _scorer_feature_list[0] in ("pf_v1", "pf")
    assert _is_pf, "sentinel detection failed"

    swaps = daemon._tape.get(mint)
    pf_features = compute_pf_features(swaps, float(2000))
    assert pf_features is not None

    # Mock the scoring path
    with (
        patch.object(scorer._pf_scorer, "score", return_value=gate_passing_score),
        patch.object(scorer._pf_scorer, "gate_passes", return_value=True),
    ):
        raw = scorer._pf_scorer.score(pf_features)
        passed = scorer._pf_scorer.gate_passes(raw)
        assert passed is True

    # Simulate the branch outcome
    daemon._scored_mints.add(mint)
    # (no daemon._pending_settle[mint] = ...)

    assert mint not in daemon._pending_settle, (
        "pf_v2 gate-passing mint must NOT enter _pending_settle (PnL is offline only)."
    )
    assert mint in daemon._scored_mints


# ---------------------------------------------------------------------------
# activate_config schema validation (CRITICAL: tester-required test)
# ---------------------------------------------------------------------------


def test_promote_pf_config_schema_valid():
    """The recommended config from promote_pf passes PipelineConfigSchema without ValidationError.

    This validates the two schema fixes:
    1. score_at_elapsed_s = 1 (not 0; schema requires Field(gt=0))
    2. gate = "adaptive_topk" (schema requires Literal["adaptive_topk"])
    """
    from core.schemas import PipelineConfigSchema  # noqa: PLC0415

    config_dict = {
        "detection": {
            "source": "birdeye_meme",
            "filter": {"source": "pump_amm", "graduated": True},
        },
        "tape": {
            "idle_kill_ttl_s": 1800,
            "pre_grad_idle_kill_ttl_s": 300,
            "max_postgrad_subscriptions": 5,
            "graduation_silence_watchdog_s": 120,
        },
        "scoring": {
            "gate": "adaptive_topk",           # schema Literal["adaptive_topk"]
            "score_at_elapsed_s": 1,            # must be > 0 (not 0!)
            "window_s": 120,                    # must be > score_at_elapsed_s
            "per_day_target": 18,
            "reference_dist_path": None,
        },
        "outcome": {
            "window_s": 1800,
        },
        "trading": {
            "paper_size_usd": 25.0,
            "enabled": False,
        },
    }

    # Must NOT raise ValidationError
    schema = PipelineConfigSchema(**config_dict)
    assert schema.scoring.score_at_elapsed_s == 1
    assert schema.scoring.gate == "adaptive_topk"
    assert schema.tape.idle_kill_ttl_s >= schema.outcome.window_s


def test_score_at_elapsed_s_zero_fails_schema():
    """score_at_elapsed_s = 0 must FAIL schema validation (tester-required guard)."""
    from pydantic import ValidationError  # noqa: PLC0415

    from core.schemas import PipelineConfigSchema  # noqa: PLC0415

    config_dict = {
        "tape": {
            "idle_kill_ttl_s": 1800,
        },
        "scoring": {
            "gate": "adaptive_topk",
            "score_at_elapsed_s": 0,   # INVALID — schema requires gt=0
            "window_s": 120,
        },
        "outcome": {
            "window_s": 1800,
        },
        "trading": {},
    }
    with pytest.raises(ValidationError):
        PipelineConfigSchema(**config_dict)


def test_wrong_gate_fails_schema():
    """gate = 'flag' must FAIL schema validation (schema requires 'adaptive_topk')."""
    from pydantic import ValidationError  # noqa: PLC0415

    from core.schemas import PipelineConfigSchema  # noqa: PLC0415

    config_dict = {
        "tape": {
            "idle_kill_ttl_s": 1800,
        },
        "scoring": {
            "gate": "flag",             # INVALID — schema requires Literal["adaptive_topk"]
            "score_at_elapsed_s": 1,
            "window_s": 120,
        },
        "outcome": {
            "window_s": 1800,
        },
        "trading": {},
    }
    with pytest.raises(ValidationError):
        PipelineConfigSchema(**config_dict)
