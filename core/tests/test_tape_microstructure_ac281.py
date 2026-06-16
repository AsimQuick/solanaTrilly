# ---
# module: core.tests.test_tape_microstructure_ac281
# sprint: sprint-7
# story: US-28 AC-28.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape_microstructure
# ---
"""AC-28.1 — vendored tape_microstructure smoke test.

Tests:
  1. test_compute_features_returns_nonempty_dict
       Calls compute_features over a small in-memory swap list and asserts the
       result is a non-None dict containing all expected tape_* keys.

  2. test_compute_features_signature_preserved
       Verifies the canonical signature: compute_features(swaps, window_s=120,
       bucket_s=15) -> dict — calls with explicit keyword args to confirm no
       rename drift.

  3. test_compute_features_tape_close_buckets_present
       Asserts the tape_close_b{0..n} family is present for a 120s/15s window
       (8 buckets → tape_close_b0 … tape_close_b7).

  4. test_compute_features_known_values
       Feeds a deterministic swap list and spot-checks a handful of tape_*
       values to confirm the math body is unaltered.
"""
from core.tape_microstructure import compute_features

# ---------------------------------------------------------------------------
# Minimal swap fixture — 5 swaps spread across the 120 s window
# ---------------------------------------------------------------------------

_SWAPS = [
    {"rel": 5.0,  "price": 0.001,  "side": "buy",  "vol": 100.0, "owner": "W1"},
    {"rel": 20.0, "price": 0.0012, "side": "buy",  "vol": 200.0, "owner": "W2"},
    {"rel": 35.0, "price": 0.0015, "side": "buy",  "vol": 150.0, "owner": "W1"},
    {"rel": 60.0, "price": 0.0013, "side": "sell", "vol": 80.0,  "owner": "W3"},
    {"rel": 90.0, "price": 0.0014, "side": "buy",  "vol": 120.0, "owner": "W4"},
]

_EXPECTED_KEYS = {
    "tape_n_trades",
    "tape_n_buy",
    "tape_n_sell",
    "tape_n_unique_traders",
    "tape_vol_total",
    "tape_buy_vol",
    "tape_sell_vol",
    "tape_buy_sell_vol_ratio",
    "tape_all_buy_window",
    "tape_sell_frac_cnt",
    "tape_ret_total",
    "tape_max_runup",
    "tape_max_drawdown",
    "tape_high_over_open",
    "tape_low_over_open",
    "tape_logprice_slope_per_s",
    "tape_logprice_accel",
    "tape_time_to_peak_s",
    "tape_time_to_first_sell_s",
    "tape_late_bucket_ret",
    "tape_n_buckets_active",
    "tape_first_swap_rel_s",
}


def test_compute_features_returns_nonempty_dict():
    result = compute_features(_SWAPS)
    assert result is not None, "compute_features returned None for non-empty swaps"
    assert isinstance(result, dict), "compute_features did not return a dict"
    missing = _EXPECTED_KEYS - result.keys()
    assert not missing, f"Missing tape_* keys: {missing}"


def test_compute_features_signature_preserved():
    result = compute_features(_SWAPS, window_s=120, bucket_s=15)
    assert result is not None
    assert isinstance(result, dict)


def test_compute_features_tape_close_buckets_present():
    result = compute_features(_SWAPS, window_s=120, bucket_s=15)
    assert result is not None
    n_buckets = 120 // 15  # = 8
    for b in range(n_buckets):
        key = f"tape_close_b{b}"
        assert key in result, f"Missing bucket close key: {key}"


def test_compute_features_known_values():
    result = compute_features(_SWAPS, window_s=120, bucket_s=15)
    assert result is not None

    assert result["tape_n_trades"] == 5
    assert result["tape_n_buy"] == 4
    assert result["tape_n_sell"] == 1
    assert result["tape_n_unique_traders"] == 4

    open_p = 0.001
    close_p = 0.0014
    expected_ret = close_p / open_p - 1.0
    assert abs(result["tape_ret_total"] - expected_ret) < 1e-9

    assert result["tape_first_swap_rel_s"] == 5.0
    assert result["tape_time_to_first_sell_s"] == 60.0
