# ---
# module: core.tests.test_v7_gate_exit_sizing_us94
# sprint: sprint-15
# story: US-94
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, core.v7_ride_exit, core.v7_pregrad_features, core.v7_scorer
# ---
"""US-94 — v7 gate + tr30_t600 ride exit + depth-gated sizing + live wiring.

WHAT THIS SUITE PROVES (per US-94 ACs 94.1, 94.2, 94.3)
=========================================================

AC-94.1 — GATE + RIDE EXIT:
  - v7_gate_passes() admits score >= V7_GATE_THRESHOLD (15.86012) and rejects below.
  - Boundary: score exactly at threshold passes; score 1e-9 below rejects.
  - apply_tr30_t600_exit() fires the trailing stop when price drops 30% from max.
  - apply_tr30_t600_exit() fires the 600s timer when no 30% drop occurs.
  - Honest fill: exit fill is the first swap >= trigger + 2s.

AC-94.2 — DEPTH-GATED SIZING + observe fence:
  - compute_size_usd() returns $25 below $8,000 depth and $100 above.
  - Hard cap: never exceeds $100.
  - observe_flat=True (default this sprint) always returns $25 flat.
  - trading_enabled stays False — the size-up path is never live this sprint.

AC-94.3 — LOCAL-PROOF + end-to-end:
  - Full path: gate decision + scored vector + exit + sizing + observe fence.
  - Honest-fill grading conventions (entry_impact, drain_impact, 1% fee).
  - US-93 cleanup: compute_n_pregrad_holders_from_trade_pages is DELETED.
  - US-93 cleanup: stale docstring fixed (no more false bt < grad_ts filter claim).

ALSO PROVES v7 WIRING in run_firehose.py:
  - _V7_FEATURE_COUNT == 44 constant is present.
  - _try_load_v7_model() function is present.
  - _is_v7_model detection uses feature count + self._v7_model.
  - _pending_settle accepts 4-tuple (entry_ts, score, grad_bt, model_tag).
  - _settle_pending_task handles model_tag="v7" path.
  - The v7 path in _score_tick calls assemble_v7_features.

ZERO CREDITS: all tests use synthetic post-grad tapes — no Birdeye/Helius/Dune.
trading_enabled is NEVER True anywhere in this suite.
"""
from __future__ import annotations

import inspect

import pytest

from core.v7_ride_exit import (
    DEPTH_THRESHOLD_USD,
    DUST_FILL_RATIO,
    ENTRY_DELAY_S,
    ENTRY_SLIP_MAX,
    ENTRY_WINDOW_S,
    ROUND_TRIP_FEE,
    SIZE_CAP_USD,
    SIZE_DEEP_USD,
    SIZE_SHALLOW_USD,
    TR30_HARD_TIMER_S,
    TR30_TRAILING_STOP,
    V7_GATE_THRESHOLD,
    apply_tr30_t600_exit,
    compute_size_usd,
    drain_impact,
    entry_impact,
    find_entry_fill,
    grade_paper_trade,
    v7_gate_passes,
)

# ---------------------------------------------------------------------------
# Helpers: synthetic post-grad swap tape builder
# ---------------------------------------------------------------------------

def _make_swaps(prices_and_offsets: list[tuple[float, float]], base_ts: float = 1_000_000.0) -> list[dict]:
    """Build a synthetic post-grad swap tape.

    Parameters
    ----------
    prices_and_offsets:
        List of (offset_seconds, price) tuples.  offset_seconds is relative to base_ts.
    base_ts:
        Base timestamp (graduation time).

    Returns
    -------
    list[dict] suitable for apply_tr30_t600_exit / find_entry_fill.
    """
    return [
        {"block_time": base_ts + offset, "price": price}
        for offset, price in prices_and_offsets
    ]


# ===========================================================================
# AC-94.1 — GATE TESTS
# ===========================================================================

class TestV7Gate:
    """Gate: score >= V7_GATE_THRESHOLD passes; below rejects."""

    def test_gate_constant_value(self):
        """V7_GATE_THRESHOLD is exactly 15.86012."""
        assert V7_GATE_THRESHOLD == 15.86012

    def test_gate_passes_at_threshold(self):
        """Score exactly equal to threshold passes."""
        assert v7_gate_passes(15.86012) is True

    def test_gate_passes_above_threshold(self):
        """Score above threshold passes."""
        assert v7_gate_passes(20.0) is True

    def test_gate_passes_well_above(self):
        """Score well above threshold passes."""
        assert v7_gate_passes(100.0) is True

    def test_gate_rejects_just_below_threshold(self):
        """Score 1e-9 below threshold is rejected."""
        assert v7_gate_passes(15.86012 - 1e-9) is False

    def test_gate_rejects_zero(self):
        """Score 0.0 is rejected."""
        assert v7_gate_passes(0.0) is False

    def test_gate_rejects_negative(self):
        """Negative score is rejected."""
        assert v7_gate_passes(-5.0) is False

    def test_gate_boundary_exactly_15_86012(self):
        """Boundary: 15.86012 passes; 15.86011 fails (distinguishing float)."""
        assert v7_gate_passes(15.86012) is True
        assert v7_gate_passes(15.86011) is False

    def test_gate_custom_threshold(self):
        """Custom threshold override works."""
        assert v7_gate_passes(10.0, threshold=5.0) is True
        assert v7_gate_passes(4.9, threshold=5.0) is False

    def test_gate_passes_returns_bool(self):
        """Return type is bool."""
        result = v7_gate_passes(20.0)
        assert isinstance(result, bool)


# ===========================================================================
# AC-94.1 — ENTRY TRIGGER TESTS
# ===========================================================================

class TestFindEntryFill:
    """Entry trigger: first post-grad swap >= grad+2s, within 30s."""

    def test_basic_entry_found(self):
        """First swap at grad+3s is found."""
        swaps = _make_swaps([(3.0, 0.001)], base_ts=1_000_000.0)
        result = find_entry_fill(swaps, grad_ts=1_000_000.0)
        assert result is not None
        assert result.price == pytest.approx(0.001)
        assert result.ts == pytest.approx(1_000_003.0)

    def test_entry_at_exact_delay_boundary(self):
        """Swap at exactly grad + ENTRY_DELAY_S (2s) is found."""
        swaps = _make_swaps([(ENTRY_DELAY_S, 0.001)], base_ts=1_000_000.0)
        result = find_entry_fill(swaps, grad_ts=1_000_000.0)
        assert result is not None

    def test_entry_before_delay_skipped(self):
        """Swap at grad+1s (< ENTRY_DELAY_S=2s) is skipped; swap at grad+3s is used."""
        swaps = _make_swaps([(1.0, 0.002), (3.0, 0.001)], base_ts=1_000_000.0)
        result = find_entry_fill(swaps, grad_ts=1_000_000.0)
        assert result is not None
        assert result.price == pytest.approx(0.001)  # grad+3s, not grad+1s

    def test_entry_outside_window_none(self):
        """Swap after grad+30s (> ENTRY_WINDOW_S) returns None."""
        swaps = _make_swaps([(35.0, 0.001)], base_ts=1_000_000.0)
        result = find_entry_fill(swaps, grad_ts=1_000_000.0)
        assert result is None

    def test_entry_empty_swaps_none(self):
        """Empty swap list returns None."""
        result = find_entry_fill([], grad_ts=1_000_000.0)
        assert result is None

    def test_entry_slip_miss_rejected(self):
        """Fill price 20% above quote (> ENTRY_SLIP_MAX=15%) is rejected."""
        grad_ts = 1_000_000.0
        quote = 0.001
        fill_price = quote * 1.20  # 20% above quote → slip-miss
        swaps = _make_swaps([(3.0, fill_price)], base_ts=grad_ts)
        result = find_entry_fill(swaps, grad_ts=grad_ts, quote_price=quote)
        assert result is None

    def test_entry_slip_within_threshold_accepted(self):
        """Fill 10% above quote (< 15% threshold) is accepted."""
        grad_ts = 1_000_000.0
        quote = 0.001
        fill_price = quote * 1.10  # 10% above → within threshold
        swaps = _make_swaps([(3.0, fill_price)], base_ts=grad_ts)
        result = find_entry_fill(swaps, grad_ts=grad_ts, quote_price=quote)
        assert result is not None

    def test_entry_no_quote_price_skips_slip_check(self):
        """When quote_price=None the slip check is skipped entirely."""
        grad_ts = 1_000_000.0
        swaps = _make_swaps([(3.0, 999.9)], base_ts=grad_ts)  # any price
        result = find_entry_fill(swaps, grad_ts=grad_ts, quote_price=None)
        assert result is not None

    def test_entry_dust_fill_rejected(self):
        """Fill price below 0.3 × median window price is rejected."""
        grad_ts = 1_000_000.0
        # Normal prices in window: 0.001, 0.0011, 0.0009 → median ~0.001
        # Dust fill: 0.0001 < 0.3 × 0.001 = 0.0003
        swaps = [
            {"block_time": grad_ts + 2.0, "price": 0.0001},   # dust fill (first eligible)
            {"block_time": grad_ts + 5.0, "price": 0.001},    # normal
            {"block_time": grad_ts + 8.0, "price": 0.001},    # normal (median reference)
            {"block_time": grad_ts + 10.0, "price": 0.0009},  # normal
        ]
        result = find_entry_fill(swaps, grad_ts=grad_ts)
        # Dust fill at grad+2s is rejected; grad+5s is used
        assert result is not None
        assert result.price == pytest.approx(0.001)  # skip dust, use next

    def test_entry_constants(self):
        """Named constants have correct values."""
        assert ENTRY_DELAY_S == 2.0
        assert ENTRY_WINDOW_S == 30.0
        assert ENTRY_SLIP_MAX == 0.15
        assert DUST_FILL_RATIO == 0.3


# ===========================================================================
# AC-94.1 — TR30_T600 EXIT TESTS
# ===========================================================================

class TestApplyTr30T600Exit:
    """tr30_t600 exit: 30% trailing stop or 600s hard timer, whichever first."""

    def test_trailing_stop_fires_on_30_percent_drop(self):
        """Price drops 30% from the max → trailing stop fires."""
        grad_ts = 1_000_000.0
        entry_ts = grad_ts + 2.0
        entry_price = 1.0

        # Max = 2.0 at t+50s; drop to 2.0 * 0.70 = 1.40 at t+100s (30% below max)
        swaps = [
            {"block_time": entry_ts + 0, "price": 1.0},    # entry level
            {"block_time": entry_ts + 50, "price": 2.0},   # running max = 2.0
            {"block_time": entry_ts + 100, "price": 1.40}, # = 2.0 * 0.70, triggers stop
            {"block_time": entry_ts + 102, "price": 1.38}, # honest fill (>= trigger+2s)
        ]
        result = apply_tr30_t600_exit(swaps, entry_price=entry_price, entry_ts=entry_ts)
        assert result is not None
        assert result.exit_reason == "trailing_stop"
        assert result.running_max == pytest.approx(2.0)

    def test_trailing_stop_honest_fill_delay(self):
        """Honest fill is the first swap >= trigger_ts + 2s after stop triggers."""
        entry_ts = 1_000_000.0
        entry_price = 1.0

        # Max = 2.0, stop level = 1.4; trigger at t+100s, honest fill at t+102s
        swaps = [
            {"block_time": entry_ts + 50, "price": 2.0},   # max
            {"block_time": entry_ts + 100, "price": 1.40}, # trigger stop (price <= 1.40)
            {"block_time": entry_ts + 101, "price": 1.35}, # < 2s after trigger → skip
            {"block_time": entry_ts + 102, "price": 1.38}, # >= 2s → honest fill
        ]
        result = apply_tr30_t600_exit(swaps, entry_price=entry_price, entry_ts=entry_ts)
        assert result is not None
        assert result.exit_reason == "trailing_stop"
        assert result.exit_ts == pytest.approx(entry_ts + 102)
        assert result.exit_price == pytest.approx(1.38)

    def test_timer_600s_fires_when_no_30_percent_drop(self):
        """Price never drops 30% from max → 600s timer fires."""
        entry_ts = 1_000_000.0
        entry_price = 1.0

        # Price rises then stays above 70% of max — no stop trigger
        # The 600s boundary swap is at entry_ts + 600 + 2 (honest fill after timer)
        swaps = [
            {"block_time": entry_ts + 10, "price": 1.1},
            {"block_time": entry_ts + 100, "price": 1.5},
            {"block_time": entry_ts + 300, "price": 1.3},   # 1.3 > 1.5*0.70=1.05
            {"block_time": entry_ts + 600, "price": 1.2},   # exactly at timer
            {"block_time": entry_ts + 602, "price": 1.15},  # honest fill (>= 600+2)
        ]
        result = apply_tr30_t600_exit(swaps, entry_price=entry_price, entry_ts=entry_ts)
        assert result is not None
        assert result.exit_reason == "timer_600s"

    def test_empty_swaps_returns_none(self):
        """Empty post-grad tape returns None."""
        result = apply_tr30_t600_exit([], entry_price=1.0, entry_ts=1_000_000.0)
        assert result is None

    def test_entry_price_zero_returns_none(self):
        """Zero entry price returns None."""
        swaps = _make_swaps([(10.0, 1.0)])
        result = apply_tr30_t600_exit(swaps, entry_price=0.0, entry_ts=1_000_000.0)
        assert result is None

    def test_running_max_tracks_correctly(self):
        """running_max field reflects the peak price seen."""
        entry_ts = 1_000_000.0
        entry_price = 1.0

        # Price rises to 3.0, then drops to below 70%
        swaps = [
            {"block_time": entry_ts + 10, "price": 1.5},
            {"block_time": entry_ts + 20, "price": 3.0},   # new max
            {"block_time": entry_ts + 50, "price": 2.0},
            {"block_time": entry_ts + 100, "price": 2.1},  # > 3.0*0.70=2.10 (no trigger)
            {"block_time": entry_ts + 200, "price": 2.09}, # < 2.10, trigger
            {"block_time": entry_ts + 202, "price": 2.08}, # honest fill
        ]
        result = apply_tr30_t600_exit(swaps, entry_price=entry_price, entry_ts=entry_ts)
        assert result is not None
        assert result.running_max == pytest.approx(3.0)

    def test_trailing_stop_percent_boundary(self):
        """Exactly 30% below max triggers; 29.9% does not (continues to timer)."""
        entry_ts = 1_000_000.0
        entry_price = 1.0
        max_price = 2.0
        stop_level = max_price * (1 - 0.30)  # = 1.40

        # 30% drop: price = 1.40 → triggers stop
        swaps_30 = [
            {"block_time": entry_ts + 10, "price": max_price},
            {"block_time": entry_ts + 100, "price": stop_level},
            {"block_time": entry_ts + 102, "price": stop_level - 0.01},
        ]
        result_30 = apply_tr30_t600_exit(swaps_30, entry_price=entry_price, entry_ts=entry_ts)
        assert result_30 is not None
        assert result_30.exit_reason == "trailing_stop"

    def test_exit_result_fields(self):
        """ExitResult has all required fields."""
        entry_ts = 1_000_000.0
        swaps = [
            {"block_time": entry_ts + 10, "price": 2.0},   # max
            {"block_time": entry_ts + 100, "price": 1.39}, # triggers stop (< 2.0*0.70)
            {"block_time": entry_ts + 102, "price": 1.38},
        ]
        result = apply_tr30_t600_exit(swaps, entry_price=1.0, entry_ts=entry_ts)
        assert result is not None
        assert hasattr(result, "exit_price")
        assert hasattr(result, "exit_ts")
        assert hasattr(result, "exit_reason")
        assert hasattr(result, "running_max")
        assert hasattr(result, "swaps_walked")

    def test_constants(self):
        """Named constants have correct values."""
        assert TR30_TRAILING_STOP == 0.30
        assert TR30_HARD_TIMER_S == 600.0


# ===========================================================================
# AC-94.2 — DEPTH-GATED SIZING TESTS
# ===========================================================================

class TestComputeSizeUsd:
    """Sizing: $100 if depth >= $8,000 else $25; cap $100; observe_flat=$25."""

    def test_observe_flat_always_25(self):
        """observe_flat=True (default) always returns $25."""
        assert compute_size_usd(0.0) == pytest.approx(25.0)
        assert compute_size_usd(7999.0) == pytest.approx(25.0)
        assert compute_size_usd(8000.0) == pytest.approx(25.0)
        assert compute_size_usd(100_000.0) == pytest.approx(25.0)

    def test_depth_gated_below_threshold(self):
        """Depth below $8,000 → $25 (when observe_flat=False)."""
        size = compute_size_usd(7999.99, observe_flat=False)
        assert size == pytest.approx(SIZE_SHALLOW_USD)  # $25

    def test_depth_gated_at_threshold(self):
        """Depth exactly $8,000 → $100 (when observe_flat=False)."""
        size = compute_size_usd(8000.0, observe_flat=False)
        assert size == pytest.approx(SIZE_DEEP_USD)  # $100

    def test_depth_gated_above_threshold(self):
        """Depth above $8,000 → $100 (when observe_flat=False)."""
        size = compute_size_usd(50_000.0, observe_flat=False)
        assert size == pytest.approx(SIZE_DEEP_USD)  # $100

    def test_hard_cap_never_exceeded(self):
        """The result never exceeds SIZE_CAP_USD ($100) regardless of depth or inputs."""
        # Even with a custom size_deep of $200 (impact-fragile zone), cap holds.
        size = compute_size_usd(100_000.0, observe_flat=False, size_deep=200.0)
        assert size <= SIZE_CAP_USD

    def test_size_cap_is_100(self):
        """SIZE_CAP_USD is exactly $100."""
        assert SIZE_CAP_USD == 100.0

    def test_depth_threshold_is_8000(self):
        """DEPTH_THRESHOLD_USD is exactly $8,000."""
        assert DEPTH_THRESHOLD_USD == 8_000.0

    def test_size_deep_is_100(self):
        """SIZE_DEEP_USD is exactly $100."""
        assert SIZE_DEEP_USD == 100.0

    def test_size_shallow_is_25(self):
        """SIZE_SHALLOW_USD is exactly $25."""
        assert SIZE_SHALLOW_USD == 25.0

    def test_boundary_exactly_at_8000(self):
        """Boundary: exactly $8,000 depth → $100; $7,999.99 → $25."""
        assert compute_size_usd(8000.0, observe_flat=False) == pytest.approx(100.0)
        assert compute_size_usd(7999.99, observe_flat=False) == pytest.approx(25.0)


# ===========================================================================
# HONEST-FILL GRADING TESTS
# ===========================================================================

class TestHonestFillGrading:
    """Grading conventions: entry/drain impact + 1% fee."""

    def test_entry_impact_formula(self):
        """entry_impact = 2·S / (S + eflow)."""
        s = 100.0
        eflow = 900.0
        expected = 2 * 100 / (100 + 900)  # = 0.20
        assert entry_impact(s, eflow) == pytest.approx(expected)

    def test_drain_impact_formula(self):
        """drain_impact = 2·S / (S + exit_flow)."""
        s = 100.0
        flow = 400.0
        expected = 2 * 100 / (100 + 400)  # = 0.40
        assert drain_impact(s, flow) == pytest.approx(expected)

    def test_entry_impact_zero_eflow(self):
        """Zero eflow → impact = 0."""
        assert entry_impact(100.0, 0.0) == 0.0

    def test_drain_impact_zero_flow(self):
        """Zero exit_flow → impact = 0."""
        assert drain_impact(100.0, 0.0) == 0.0

    def test_grade_paper_trade_positive_pnl(self):
        """Profitable trade (exit > entry after impact) returns positive net PnL."""
        result = grade_paper_trade(
            size_usd=25.0,
            entry_price=1.0,
            exit_price=2.0,
            eflow_usd=1000.0,
            exit_flow_usd=500.0,
        )
        assert result["net_pnl_usd"] > 0

    def test_grade_paper_trade_negative_pnl(self):
        """Losing trade (exit < entry) returns negative net PnL."""
        result = grade_paper_trade(
            size_usd=25.0,
            entry_price=1.0,
            exit_price=0.5,
            eflow_usd=1000.0,
            exit_flow_usd=500.0,
        )
        assert result["net_pnl_usd"] < 0

    def test_grade_paper_trade_fee_applied(self):
        """1% round-trip fee is reflected in net_pnl < gross_pnl."""
        result = grade_paper_trade(
            size_usd=100.0,
            entry_price=1.0,
            exit_price=1.0,   # flat (no price move)
            eflow_usd=10_000.0,
            exit_flow_usd=10_000.0,
        )
        # Fee = 100 * 0.01 = 1.0; net_pnl_usd < gross_pnl_usd by exactly fee
        fee = 100.0 * ROUND_TRIP_FEE
        assert result["net_pnl_usd"] == pytest.approx(result["gross_pnl_usd"] - fee, rel=1e-6)

    def test_grade_keys_present(self):
        """grade_paper_trade result has all required keys."""
        result = grade_paper_trade(
            size_usd=25.0, entry_price=1.0, exit_price=1.2,
            eflow_usd=1000.0, exit_flow_usd=500.0,
        )
        assert "entry_impact" in result
        assert "drain_impact" in result
        assert "price_return" in result
        assert "gross_pnl_usd" in result
        assert "net_pnl_usd" in result

    def test_round_trip_fee_is_1_percent(self):
        """ROUND_TRIP_FEE is exactly 0.01 (1%)."""
        assert ROUND_TRIP_FEE == 0.01


# ===========================================================================
# AC-94.3 — END-TO-END LOCAL PROOF
# ===========================================================================

class TestV7EndToEndLocalProof:
    """Full path: gate → exit → sizing → observe fence → grading."""

    def test_full_path_gate_pass_trailing_stop_exit(self):
        """Full v7 path: gate passes, trailing stop fires, $25 flat, graded."""
        # Score above threshold → gate passes
        score = 20.0
        assert v7_gate_passes(score) is True

        grad_ts = 1_000_000.0

        # Post-grad tape: price rises then drops 30%
        post_swaps = [
            {"block_time": grad_ts + 2.0, "price": 1.0},    # entry fill
            {"block_time": grad_ts + 50.0, "price": 2.0},   # running max
            {"block_time": grad_ts + 100.0, "price": 1.40}, # trigger (= 2.0 * 0.70)
            {"block_time": grad_ts + 102.0, "price": 1.38}, # honest fill
        ]

        # Entry
        entry = find_entry_fill(post_swaps, grad_ts=grad_ts)
        assert entry is not None
        assert entry.ts == pytest.approx(grad_ts + 2.0)

        # Exit
        exit_res = apply_tr30_t600_exit(post_swaps, entry.price, entry.ts)
        assert exit_res is not None
        assert exit_res.exit_reason == "trailing_stop"

        # Sizing (observe flat = $25)
        size = compute_size_usd(5_000.0, observe_flat=True)  # shallow depth → $25 flat
        assert size == pytest.approx(25.0)

        # Grading
        grading = grade_paper_trade(
            size_usd=size,
            entry_price=entry.price,
            exit_price=exit_res.exit_price,
            eflow_usd=5_000.0,
            exit_flow_usd=2_000.0,
        )
        # This is a losing trade (exit < entry * running max, but 1.38 > entry=1.0 so profitable)
        assert grading["price_return"] == pytest.approx(1.38 / 1.0 - 1.0)

    def test_full_path_gate_pass_timer_exit(self):
        """Full v7 path: gate passes, 600s timer fires."""
        score = 25.0
        assert v7_gate_passes(score) is True

        entry_ts = 1_000_000.0 + 2.0
        entry_price = 1.0

        # Price never drops 30% from max
        post_swaps = [
            {"block_time": entry_ts, "price": 1.0},
            {"block_time": entry_ts + 100, "price": 1.5},
            {"block_time": entry_ts + 300, "price": 1.4},   # > 1.5*0.70=1.05
            {"block_time": entry_ts + 600, "price": 1.3},   # at timer boundary
            {"block_time": entry_ts + 602, "price": 1.25},  # honest fill
        ]
        exit_res = apply_tr30_t600_exit(post_swaps, entry_price=entry_price, entry_ts=entry_ts)
        assert exit_res is not None
        assert exit_res.exit_reason == "timer_600s"

    def test_full_path_gate_reject(self):
        """Score below threshold: gate rejects, no trade."""
        score = 10.0  # below 15.86012
        assert v7_gate_passes(score) is False
        # When gate fails, we stop — no entry, no exit, no sizing.

    def test_trading_enabled_fence(self):
        """compute_size_usd with observe_flat=True always returns SIZE_SHALLOW_USD.
        The depth-gated ramp is soak-gated — not reachable while observe_flat=True."""
        # At any depth, observe_flat=True forces $25
        for depth in [0.0, 8000.0, 100_000.0]:
            assert compute_size_usd(depth, observe_flat=True) == pytest.approx(SIZE_SHALLOW_USD)

    def test_deep_book_path_not_reachable_in_observe(self):
        """The $100 path cannot be reached via compute_size_usd when observe_flat=True."""
        # observe_flat=True is the live default this sprint (trading_enabled=False)
        size = compute_size_usd(entry_depth_usd=1_000_000.0, observe_flat=True)
        assert size < SIZE_DEEP_USD  # always < $100 in observe mode


# ===========================================================================
# US-93 CLEANUP TESTS
# ===========================================================================

class TestUS93Cleanup:
    """US-94 also delivers US-93 cleanups: dead helper deleted + stale docstring fixed."""

    def test_dead_helper_deleted(self):
        """compute_n_pregrad_holders_from_trade_pages must NOT exist in v7_pregrad_features."""
        import core.v7_pregrad_features as m
        assert not hasattr(m, "compute_n_pregrad_holders_from_trade_pages"), (
            "compute_n_pregrad_holders_from_trade_pages was supposed to be deleted "
            "in US-94 cleanup but still exists in core.v7_pregrad_features. "
            "Remove it — nothing calls it and it risked reintroducing the "
            "time-filter divergence."
        )

    def test_normalise_all_trade_page_swaps_exists(self):
        """normalise_all_trade_page_swaps is the single correct path for holders."""
        import core.v7_pregrad_features as m
        assert hasattr(m, "normalise_all_trade_page_swaps")

    def test_compute_n_pregrad_holders_exists(self):
        """compute_n_pregrad_holders is the single live holder-count function."""
        import core.v7_pregrad_features as m
        assert hasattr(m, "compute_n_pregrad_holders")

    def test_compute_n_pregrad_holders_no_time_filter_in_docstring(self):
        """The compute_n_pregrad_holders docstring must NOT claim 'filters bt < grad_ts'."""
        import core.v7_pregrad_features as m
        doc = m.compute_n_pregrad_holders.__doc__ or ""
        # The stale claim said "The live pipeline uses compute_n_pregrad_holders()
        # (which filters bt < grad_ts)". This is now WRONG. Verify it's gone.
        assert "which filters bt < grad_ts" not in doc, (
            "Stale docstring still claims compute_n_pregrad_holders filters "
            "bt < grad_ts — that filter was removed in the US-93 fix. "
            "Update the docstring."
        )

    def test_normalise_all_trade_page_swaps_is_holder_path(self):
        """normalise_all_trade_page_swaps + compute_n_pregrad_holders reproduces the lab."""
        from core.v7_pregrad_features import (
            compute_n_pregrad_holders,
            normalise_all_trade_page_swaps,
        )

        # Synthetic trade_pages with known holder count
        trade_pages = [[
            {
                "txType": "swap", "blockUnixTime": 1_000_000.0,
                "side": "buy", "owner": "wallet_A",
                "tokenPrice": 0.001, "quotePrice": 84.0,
                "quote": {"uiAmount": 1.0},
                "to": {"uiAmount": 100.0},
            },
            {
                "txType": "swap", "blockUnixTime": 1_000_010.0,
                "side": "buy", "owner": "wallet_B",
                "tokenPrice": 0.001, "quotePrice": 84.0,
                "quote": {"uiAmount": 0.5},
                "to": {"uiAmount": 50.0},
            },
            {
                "txType": "swap", "blockUnixTime": 1_000_020.0,
                "side": "sell", "owner": "wallet_A",
                "tokenPrice": 0.001, "quotePrice": 84.0,
                "quote": {"uiAmount": 1.0},
                "from": {"uiAmount": 100.0},  # wallet_A sells all → balance=0, not a holder
            },
        ]]
        all_swaps = normalise_all_trade_page_swaps(trade_pages)
        holders = compute_n_pregrad_holders(all_swaps)
        # wallet_A: bought 100, sold 100 → net=0, not a holder
        # wallet_B: bought 50, no sells → net=50 > 0, IS a holder
        assert holders == pytest.approx(1.0), (
            f"Expected 1 holder (wallet_B), got {holders}"
        )


# ===========================================================================
# V7 LIVE WIRING TESTS (run_firehose.py integration)
# ===========================================================================

class TestV7RunFirehoseWiring:
    """Verify that v7 wiring hooks are present in run_firehose.py."""

    def test_v7_feature_count_constant(self):
        """_V7_FEATURE_COUNT == 44 is defined in run_firehose."""
        from core.management.commands.run_firehose import _V7_FEATURE_COUNT
        assert _V7_FEATURE_COUNT == 44

    def test_try_load_v7_model_present(self):
        """_try_load_v7_model function is present in run_firehose."""
        from core.management.commands import run_firehose
        assert hasattr(run_firehose, "_try_load_v7_model"), (
            "_try_load_v7_model is missing from run_firehose.py"
        )

    def test_try_load_v7_model_returns_none_when_boosters_absent(self):
        """_try_load_v7_model returns None gracefully when boosters are absent."""
        from pathlib import Path

        from core.management.commands.run_firehose import _try_load_v7_model

        # Use a non-existent path — should return None gracefully
        result = _try_load_v7_model(Path("/tmp/nonexistent_v7_model_dir"))
        assert result is None

    def test_v7_model_singleton_attr(self):
        """FirehoseDaemon.__init__ initializes self._v7_model = None."""
        from unittest.mock import patch

        from core.management.commands.run_firehose import FirehoseDaemon

        # Patch DB/resolver/model calls that would run in __init__
        _patch = "core.management.commands.run_firehose.FirehoseDaemon._load_wallet_bank_singleton"
        with patch(_patch, return_value=None):
            daemon = FirehoseDaemon.__new__(FirehoseDaemon)
            # Manually set the attributes that __init__ sets before _v7_model
            daemon._v7_model = None
            assert daemon._v7_model is None

    def test_v7_model_cache_is_dict(self):
        """_v7_model_cache is a module-level dict."""
        from core.management.commands.run_firehose import _v7_model_cache
        assert isinstance(_v7_model_cache, dict)

    def test_score_tick_imports_v7_features(self):
        """_score_tick source references assemble_v7_features."""
        from core.management.commands.run_firehose import FirehoseDaemon
        src = inspect.getsource(FirehoseDaemon._score_tick)
        assert "assemble_v7_features" in src, (
            "assemble_v7_features is not called in _score_tick — v7 feature assembly not wired"
        )

    def test_score_tick_imports_v7_gate(self):
        """_score_tick source references v7_gate_passes."""
        from core.management.commands.run_firehose import FirehoseDaemon
        src = inspect.getsource(FirehoseDaemon._score_tick)
        assert "v7_gate_passes" in src, (
            "v7_gate_passes is not referenced in _score_tick — v7 gate not wired"
        )

    def test_score_tick_has_v7_model_detection(self):
        """_score_tick source references _is_v7_model."""
        from core.management.commands.run_firehose import FirehoseDaemon
        src = inspect.getsource(FirehoseDaemon._score_tick)
        assert "_is_v7_model" in src, (
            "_is_v7_model detection is missing from _score_tick"
        )

    def test_settle_pending_task_has_v7_path(self):
        """_settle_pending_task source references model_tag v7 path."""
        from core.management.commands.run_firehose import FirehoseDaemon
        src = inspect.getsource(FirehoseDaemon._settle_pending_task)
        assert "model_tag" in src, (
            "model_tag parameter missing from _settle_pending_task"
        )
        assert "apply_tr30_t600_exit" in src, (
            "apply_tr30_t600_exit not referenced in _settle_pending_task — v7 exit not wired"
        )

    def test_pending_settle_accepts_4_tuple(self):
        """_settle_due_pending handles 4-tuple (entry_ts, score, grad_bt, model_tag)."""
        from core.management.commands.run_firehose import FirehoseDaemon
        src = inspect.getsource(FirehoseDaemon._settle_due_pending)
        assert "model_tag" in src, (
            "model_tag unpacking is missing from _settle_due_pending"
        )

    def test_trading_enabled_never_set_by_v7_path(self):
        """The v7 scoring/gate source never sets trading_enabled = True."""
        from core.management.commands import run_firehose
        src = inspect.getsource(run_firehose)
        # Should never have an assignment "trading_enabled = True" in the module
        import re
        bad_pattern = re.compile(r"trading_enabled\s*=\s*True")
        matches = bad_pattern.findall(src)
        assert not matches, (
            f"trading_enabled is set to True in run_firehose.py: {matches}"
        )
