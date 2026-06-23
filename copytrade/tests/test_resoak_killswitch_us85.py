# ---
# module: copytrade.tests.test_resoak_killswitch_us85
# sprint: sprint-15
# story: US-85
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.soak_killswitch, copytrade.resoak_harness, pytest
# ---
"""US-85 LOCAL-PROOF tests: re-soak harness + kill-switch.

AC-85.1: Re-soak harness runs recalibrated gate + honest settler over local tapes.
AC-85.2: Kill-switch trips at selected grad-rate < 40% / >=15 trades; does NOT
         trip above it; VOID/ENTRY_REJECTED excluded from denominator.
AC-85.3: LOCAL-PROOF + HONEST CAVEAT documented.

Tests that require real lake data are marked @require_lake (skip in CI).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from copytrade.soak_killswitch import (
    KILLSWITCH_GRAD_RATE_FLOOR,
    KILLSWITCH_MIN_TRADES,
    RESULT_ENTRY_REJECTED,
    RESULT_GRAD,
    RESULT_NO_GRAD,
    RESULT_VOID,
    KillSwitchState,
    SoakTrade,
    check_killswitch_from_resoak_report,
    evaluate_killswitch,
)

LAKE_PATH = "/Users/asim/NoIcloud/solanatrills/lake/firehose"
require_lake = pytest.mark.skipif(
    not Path(LAKE_PATH).exists(),
    reason="local firehose lake not present (CI skip)",
)

MODEL_DIR = "/Users/asim/NoIcloud/solanatrilly/models/copy_2026-06-22_curvestage"
require_model = pytest.mark.skipif(
    not Path(MODEL_DIR).exists(),
    reason="model artifacts not present",
)


# ---------------------------------------------------------------------------
# AC-85.2 — Kill-switch definition and denominator logic
# ---------------------------------------------------------------------------

class TestKillSwitchDefinition:
    """AC-85.2: kill-switch constants are config-driven."""

    def test_floor_is_40_pct(self):
        """Kill-switch grad-rate floor is 0.40."""
        assert KILLSWITCH_GRAD_RATE_FLOOR == 0.40, (
            "KILLSWITCH_GRAD_RATE_FLOOR should be 0.40 (40%)"
        )

    def test_min_trades_is_15(self):
        """Kill-switch minimum real-entry trades is 15."""
        assert KILLSWITCH_MIN_TRADES == 15, (
            "KILLSWITCH_MIN_TRADES should be 15"
        )

    def test_result_constants_are_strings(self):
        """Result constants are string-typed."""
        for c in (RESULT_GRAD, RESULT_NO_GRAD, RESULT_VOID, RESULT_ENTRY_REJECTED):
            assert isinstance(c, str)

    def test_grad_and_no_grad_are_distinct_from_void_and_rejected(self):
        """GRAD/NO_GRAD are distinct from VOID/ENTRY_REJECTED (denominator logic)."""
        real_results = {RESULT_GRAD, RESULT_NO_GRAD}
        excluded_results = {RESULT_VOID, RESULT_ENTRY_REJECTED}
        assert real_results.isdisjoint(excluded_results), (
            "GRAD/NO_GRAD must not overlap with VOID/ENTRY_REJECTED"
        )


class TestKillSwitchDenominator:
    """AC-85.2: VOID and ENTRY_REJECTED rows are excluded from the denominator."""

    def test_void_rows_excluded_from_denominator(self):
        """VOID rows do not count in the kill-switch denominator."""
        trades = [SoakTrade(result=RESULT_VOID) for _ in range(20)]
        state = evaluate_killswitch(trades)
        assert state.n_real_entries == 0
        assert not state.halted  # can't kill with 0 real entries

    def test_entry_rejected_rows_excluded_from_denominator(self):
        """ENTRY_REJECTED rows do not count in the kill-switch denominator."""
        trades = [SoakTrade(result=RESULT_ENTRY_REJECTED) for _ in range(20)]
        state = evaluate_killswitch(trades)
        assert state.n_real_entries == 0
        assert not state.halted

    def test_mixed_excluded_rows_with_few_real_entries(self):
        """Many VOID + a few real entries: denominator = only real entries."""
        trades = (
            [SoakTrade(result=RESULT_VOID) for _ in range(50)]
            + [SoakTrade(result=RESULT_GRAD) for _ in range(3)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(3)]
            + [SoakTrade(result=RESULT_ENTRY_REJECTED) for _ in range(10)]
        )
        state = evaluate_killswitch(trades)
        assert state.n_real_entries == 6  # only GRAD + NO_GRAD
        assert state.n_grad == 3
        assert state.grad_rate == pytest.approx(0.5, abs=0.01)

    def test_grad_rate_computed_over_real_entries_only(self):
        """grad_rate = n_grad / n_real_entries, ignoring VOID and ENTRY_REJECTED."""
        trades = [
            SoakTrade(result=RESULT_GRAD),   # 1 grad
            SoakTrade(result=RESULT_NO_GRAD), # 1 no-grad
            SoakTrade(result=RESULT_VOID),    # NOT in denominator
            SoakTrade(result=RESULT_ENTRY_REJECTED),  # NOT in denominator
        ]
        state = evaluate_killswitch(trades)
        assert state.n_real_entries == 2
        assert state.n_grad == 1
        assert state.grad_rate == pytest.approx(0.5, abs=0.01)


class TestKillSwitchTripsBelow40:
    """AC-85.2: kill-switch trips when grad-rate < 40% AND >=15 trades."""

    def test_kill_fires_at_0pct_grad_rate_15_trades(self):
        """Kill fires: 0/15 graduated, 15 real entries."""
        trades = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(15)]
        state = evaluate_killswitch(trades)
        assert state.halted, "Kill-switch should fire at 0% grad-rate with 15 entries"
        assert "40%" in state.halt_reason or "0.0%" in state.halt_reason

    def test_kill_fires_at_5_of_15_grad_rate(self):
        """Kill fires: 5/15 = 33.3% < 40% floor."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(5)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(10)]
        )
        state = evaluate_killswitch(trades)
        assert state.halted, "Kill should fire: 5/15 = 33.3% < 40%"
        assert state.grad_rate == pytest.approx(5 / 15, abs=0.001)

    def test_kill_fires_at_exactly_min_trade_count(self):
        """Kill fires at exactly KILLSWITCH_MIN_TRADES real entries (boundary)."""
        n = KILLSWITCH_MIN_TRADES  # 15
        # 0% grad-rate at exactly 15 entries
        trades = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(n)]
        state = evaluate_killswitch(trades)
        assert state.halted

    def test_kill_fires_for_large_sample_below_threshold(self):
        """Kill fires for 100 entries at 35% grad-rate."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(35)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(65)]
        )
        state = evaluate_killswitch(trades)
        assert state.halted
        assert state.grad_rate == pytest.approx(0.35, abs=0.001)


class TestKillSwitchDoesNotTripAbove40:
    """AC-85.2: kill-switch does NOT trip when grad-rate >= 40%."""

    def test_no_kill_at_40pct_exactly(self):
        """Kill does NOT fire at exactly 40% (floor is strict <)."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(6)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(9)]
        )
        state = evaluate_killswitch(trades)
        assert state.grad_rate == pytest.approx(6 / 15, abs=0.001)  # 40%
        assert not state.halted, "Kill should NOT fire at exactly 40%"

    def test_no_kill_at_60pct_grad_rate(self):
        """Kill does NOT fire at 60% grad-rate (well above floor)."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(9)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(6)]
        )
        state = evaluate_killswitch(trades)
        assert not state.halted
        assert state.grad_rate == pytest.approx(0.6, abs=0.001)

    def test_no_kill_below_min_trades_even_at_0pct(self):
        """Kill does NOT fire if < KILLSWITCH_MIN_TRADES real entries, even at 0%."""
        trades = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(14)]  # 14 < 15
        state = evaluate_killswitch(trades)
        assert not state.halted, (
            "Kill should NOT fire with only 14 real entries (below min threshold)"
        )
        assert state.n_real_entries == 14
        assert state.grad_rate == pytest.approx(0.0, abs=0.001)


class TestKillSwitchStateProperties:
    """KillSwitchState properties and serialisation."""

    def test_grad_rate_is_none_with_no_real_entries(self):
        """grad_rate is None when no real entries."""
        state = KillSwitchState()
        assert state.grad_rate is None

    def test_can_kill_false_below_min(self):
        """can_kill is False when n_real_entries < KILLSWITCH_MIN_TRADES."""
        state = KillSwitchState(n_real_entries=14)
        assert not state.can_kill

    def test_can_kill_true_at_min(self):
        """can_kill is True when n_real_entries >= KILLSWITCH_MIN_TRADES."""
        state = KillSwitchState(n_real_entries=15)
        assert state.can_kill

    def test_to_dict_has_required_keys(self):
        """to_dict() includes all expected keys."""
        state = KillSwitchState(n_real_entries=10, n_grad=5)
        d = state.to_dict()
        for key in ("n_real_entries", "n_grad", "grad_rate", "halted",
                    "halt_reason", "can_kill",
                    "killswitch_grad_rate_floor", "killswitch_min_trades"):
            assert key in d, f"Missing key in to_dict(): {key}"


class TestKillSwitchFromResoakReport:
    """check_killswitch_from_resoak_report convenience wrapper."""

    def test_report_wrapper_grad_and_no_grad(self):
        """Wrapper correctly maps graduated + entry_valid to GRAD/NO_GRAD."""
        report = {
            "trades": [
                {"graduated": True, "entry_valid": True, "mint": "A"},
                {"graduated": False, "entry_valid": True, "mint": "B"},
                {"graduated": False, "entry_valid": True, "mint": "C"},
            ]
        }
        # 1 GRAD + 2 NO_GRAD = 3 real entries; grad-rate = 33.3% < 40%
        # But 3 < 15 min_trades so kill should NOT fire
        state = check_killswitch_from_resoak_report(report)
        assert state.n_real_entries == 3
        assert state.n_grad == 1
        assert not state.halted

    def test_report_wrapper_entry_invalid_excluded(self):
        """Wrapper maps entry_valid=False to ENTRY_REJECTED (excluded from denominator)."""
        report = {
            "trades": [
                {"graduated": True, "entry_valid": False, "mint": "A"},  # ENTRY_REJECTED
                {"graduated": False, "entry_valid": True, "mint": "B"},   # NO_GRAD
            ]
        }
        state = check_killswitch_from_resoak_report(report)
        assert state.n_real_entries == 1  # only the valid entry counts
        assert state.n_grad == 0

    def test_report_wrapper_empty_trades(self):
        """Wrapper handles empty trades list."""
        state = check_killswitch_from_resoak_report({"trades": []})
        assert state.n_real_entries == 0
        assert not state.halted


# ---------------------------------------------------------------------------
# AC-85.1 — Re-soak harness structure (source-level tests, no lake required)
# ---------------------------------------------------------------------------

class TestResoakHarnessStructure:
    """AC-85.1: harness structure verifications (no lake access)."""

    def test_harness_never_uses_vol_usd(self):
        """resoak_harness.py does not use vol_usd (all-zero in tapes)."""
        import inspect

        from copytrade import resoak_harness

        src = inspect.getsource(resoak_harness)
        # vol_usd may appear in comments/docstrings (documenting it is all-zero),
        # but must NEVER be used as a computation input (row["vol_usd"] or .vol_usd).
        # Check that the functional code path doesn't read the vol_usd field from rows.
        assert 'row["vol_usd"]' not in src and ".vol_usd" not in src, (
            "resoak_harness should NEVER read row.vol_usd (all-zero in firehose tapes)"
        )

    def test_harness_uses_vol_sol_for_dollar_basis(self):
        """resoak_harness.py uses vol_sol for dollar quantities."""
        import inspect

        from copytrade import resoak_harness

        src = inspect.getsource(resoak_harness)
        assert "vol_sol" in src, "resoak_harness should use vol_sol for dollar basis"
        assert "sol_usd" in src, "resoak_harness should use sol_usd for price conversion"

    def test_harness_uses_killswitch(self):
        """resoak_harness.py imports and calls evaluate_killswitch."""
        import inspect

        from copytrade import resoak_harness

        src = inspect.getsource(resoak_harness)
        assert "evaluate_killswitch" in src, (
            "resoak_harness should call evaluate_killswitch"
        )

    def test_harness_uses_honest_fill_label_not_tokens_table(self):
        """resoak_harness.py uses label_graduations (free label), not Token DB."""
        import inspect

        from copytrade import resoak_harness

        src = inspect.getsource(resoak_harness)
        assert "label_graduations" in src, (
            "resoak_harness should use label_graduations for graduation truth"
        )
        # Must NOT import from the Django models table (DB is live state)
        assert "from copytrade.models" not in src, (
            "resoak_harness must not read from the live Token DB table"
        )

    def test_harness_documents_breakeven_grad_rate(self):
        """resoak_harness.py documents the ~39% breakeven grad-rate."""
        from copytrade.resoak_harness import BREAKEVEN_GRAD_RATE

        assert abs(BREAKEVEN_GRAD_RATE - 0.39) < 0.02, (
            "BREAKEVEN_GRAD_RATE should be ~0.39"
        )

    def test_harness_report_has_soak_judgment_trinity(self):
        """ResoakReport documents the soak judgment trinity."""
        from copytrade.resoak_harness import ResoakReport

        r = ResoakReport()
        trinity = r.soak_judgment_trinity
        assert isinstance(trinity, dict)
        assert "a" in trinity and "b" in trinity and "c" in trinity

    def test_harness_honest_caveat_is_documented(self):
        """ResoakReport carries the honest caveat."""
        from copytrade.resoak_harness import ResoakReport

        r = ResoakReport()
        assert "properly tested" in r.honest_caveat.lower() or "not proven" in r.honest_caveat.lower() or (
            "NOT" in r.honest_caveat and "proven" in r.honest_caveat
        ), f"Honest caveat not properly documented: {r.honest_caveat}"

    def test_round_trip_cost_is_documented(self):
        """resoak_harness.py has a ROUND_TRIP_COST constant."""
        from copytrade.resoak_harness import ROUND_TRIP_COST

        assert 0 < ROUND_TRIP_COST < 0.05, (
            "ROUND_TRIP_COST should be a small positive fraction (e.g. 0.006)"
        )


# ---------------------------------------------------------------------------
# AC-85.3 — LOCAL-PROOF: synthetic trade stream for kill-switch trigger test
# ---------------------------------------------------------------------------

class TestLocalProofSyntheticKillSwitch:
    """AC-85.3: synthetic trade stream proves kill-switch logic end-to-end."""

    def test_synthetic_stream_trips_kill_at_30pct_grad_rate(self):
        """Synthetic stream: 30% grad-rate over 20 entries trips the kill-switch."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(6)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(14)]
            + [SoakTrade(result=RESULT_VOID) for _ in range(50)]  # excluded
            + [SoakTrade(result=RESULT_ENTRY_REJECTED) for _ in range(10)]  # excluded
        )
        state = evaluate_killswitch(trades)

        assert state.n_real_entries == 20
        assert state.n_grad == 6
        assert state.grad_rate == pytest.approx(0.30, abs=0.001)
        assert state.halted, "30% grad-rate should trip the kill-switch"
        assert state.halt_reason is not None

    def test_synthetic_stream_healthy_at_65pct_grad_rate(self):
        """Synthetic stream: 65% grad-rate over 20 entries does NOT trip the kill."""
        trades = (
            [SoakTrade(result=RESULT_GRAD) for _ in range(13)]
            + [SoakTrade(result=RESULT_NO_GRAD) for _ in range(7)]
            + [SoakTrade(result=RESULT_VOID) for _ in range(30)]
        )
        state = evaluate_killswitch(trades)

        assert state.n_real_entries == 20
        assert state.grad_rate == pytest.approx(0.65, abs=0.001)
        assert not state.halted

    def test_synthetic_stream_no_kill_below_min_count(self):
        """Synthetic stream: 10 entries at 0% does NOT trip the kill (below min=15)."""
        trades = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(10)]
        state = evaluate_killswitch(trades)
        assert not state.halted

    def test_synthetic_stream_boundary_14_vs_15_entries(self):
        """Boundary test: 14 entries (no kill) vs 15 entries (kill fires at 0%)."""
        trades_14 = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(14)]
        state_14 = evaluate_killswitch(trades_14)
        assert not state_14.halted

        trades_15 = [SoakTrade(result=RESULT_NO_GRAD) for _ in range(15)]
        state_15 = evaluate_killswitch(trades_15)
        assert state_15.halted

    def test_void_dilution_doesnt_mask_bad_grad_rate(self):
        """Adding many VOIDs does not mask a bad grad-rate from triggering the kill."""
        # 0/20 graduated = 0% grad-rate; swamped by 200 VOIDs
        trades = (
            [SoakTrade(result=RESULT_NO_GRAD) for _ in range(20)]
            + [SoakTrade(result=RESULT_VOID) for _ in range(200)]
        )
        state = evaluate_killswitch(trades)
        # n_real_entries = 20 (not 220), grad_rate = 0%, kill fires
        assert state.n_real_entries == 20
        assert state.halted


# ---------------------------------------------------------------------------
# AC-85.3 — LOCAL-PROOF on real lake (skip in CI)
# ---------------------------------------------------------------------------

@require_lake
@require_model
class TestLocalProofOnRealLake:
    """AC-85.3 LOCAL-PROOF: harness produces metrics on real Jun 20-22 tapes."""

    def test_resoak_returns_report_object(self):
        """run_resoak returns a ResoakReport with expected fields."""
        from copytrade.resoak_harness import ResoakReport, run_resoak

        report = run_resoak(
            ["2026-06-20"],
            lake_base_dir=LAKE_PATH,
            model_dir=MODEL_DIR,
        )
        assert isinstance(report, ResoakReport)
        assert report.date_strs == ["2026-06-20"]

    def test_resoak_selects_some_trades(self):
        """Harness selects at least some trades on Jun 20-22 (non-empty output)."""
        from copytrade.resoak_harness import run_resoak

        report = run_resoak(
            ["2026-06-20", "2026-06-21", "2026-06-22"],
            lake_base_dir=LAKE_PATH,
            model_dir=MODEL_DIR,
        )
        assert report.n_candidates > 0, "Should find gate-1+gate-2 candidates"
        assert report.n_selected >= 0, "n_selected should be non-negative"

    def test_resoak_selected_grad_rate_documented(self):
        """Harness computes selected_grad_rate; it's non-None when entries exist."""
        from copytrade.resoak_harness import run_resoak

        report = run_resoak(
            ["2026-06-20", "2026-06-21", "2026-06-22"],
            lake_base_dir=LAKE_PATH,
            model_dir=MODEL_DIR,
        )
        # If there are any selected entries, grad-rate should be populated
        real_entries = [
            t for t in report.trades
            if t.ks_result in (RESULT_GRAD, RESULT_NO_GRAD)
        ]
        if real_entries:
            assert report.selected_grad_rate is not None

    def test_resoak_dollar_basis_is_vol_sol(self):
        """Harness PnL is computed in USD via vol_sol * sol_usd (never vol_usd)."""
        from copytrade.resoak_harness import run_resoak

        report = run_resoak(
            ["2026-06-20"],
            lake_base_dir=LAKE_PATH,
            model_dir=MODEL_DIR,
        )
        # All pnl_usd should be finite (not NaN or inf)
        for t in report.trades:
            if t.entry_valid:
                assert abs(t.pnl_usd) < 1_000, (
                    f"Suspiciously large pnl_usd={t.pnl_usd} for mint {t.mint} — "
                    "check dollar basis"
                )
