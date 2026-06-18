# ---
# module: copytrade.tests.test_position_manager_ac612
# sprint: sprint-12
# story: US-61 AC-61.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, copytrade.position_manager, copytrade.models,
#   copytrade.schemas, copytrade.trigger_pipeline, copytrade.position_opener
# ---
"""AC-61.2: Position exit-condition engine tests.

Verifies that the engine exits on the FIRST of the four SPEC §3 triggers and
records the correct exit_reason + realized PnL.  Each test drives exactly one
trigger to fire in isolation; the others are held below threshold so the
correct reason wins.

Tests:
    1.  TP trigger fires first — price >= entry*(1+take_profit_pct/100)
    2.  SL trigger fires first — price <= entry*(1-stop_loss_pct/100)
    3.  CURVE trigger fires first — curve_completion >= curve_completion_exit_pct
    4.  TIMER trigger fires first — held_seconds >= max_hold_seconds
    5.  No exit when all conditions are below threshold
    6.  CURVE is suppressed when exit_before_graduation=False
    7.  TP realized_pnl_sol formula is correct
    8.  SL realized_pnl_pct formula is correct
    9.  CURVE records zero PnL at flat price
    10. TIMER records correct PnL when price moved
    11. copytrade_pnl_by_wallet rollup is created on first position close
    12. copytrade_pnl_by_wallet rollup accumulates correctly over multiple closes
    13. check_and_close_position returns None when no exit fires
    14. check_and_close_position returns closed position when exit fires

All tests are deterministic over replay (injected timestamps + prices, no
firehose, no datetime.now()).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from copytrade.models import CopytradePnlByWallet, CopytradePosition
from copytrade.position_manager import (
    check_and_close_position,
    check_exit_condition,
    close_position,
)
from copytrade.schemas import CopyTradeConfig

# ---------------------------------------------------------------------------
# Constants for deterministic replay
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
_COHORT_ID = "cohort-ac612-test"
_MINT = "MintAC612PumpXXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_WALLET = "Wa11etAC612AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_ENTRY_PRICE = 1.0
_SOL_IN = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(**overrides) -> CopytradePosition:
    """Create and save an open CopytradePosition with default AC-61.2 values."""
    defaults = dict(
        cohort_id=_COHORT_ID,
        mint=_MINT,
        trigger_wallet=_WALLET,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=_T0,
        entry_price=_ENTRY_PRICE,
        sol_in=_SOL_IN,
    )
    defaults.update(overrides)
    pos = CopytradePosition(**defaults)
    pos.save()
    return pos


def _config(**overrides) -> CopyTradeConfig:
    """Return a CopyTradeConfig with safe non-triggering defaults."""
    defaults = dict(
        mode="observe",
        sol_size_per_trade=_SOL_IN,
        take_profit_pct=50.0,          # TP at 1.5 × entry
        stop_loss_pct=40.0,            # SL at 0.6 × entry
        exit_before_graduation=True,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=3600,
        max_concurrent_positions=20,
        mirror_wallet_sells=False,
    )
    defaults.update(overrides)
    return CopyTradeConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. TP trigger fires first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tp_fires_first():
    """price >= entry*(1+tp/100) triggers TP exit before SL / CURVE / TIMER."""
    pos = _make_position()
    cfg = _config(take_profit_pct=50.0)

    # Price at 1.51 — above TP threshold (1.5); SL at 0.6; not hit
    exit_price = 1.51
    ts = _T0 + timedelta(seconds=10)  # well below max_hold_seconds

    result = check_exit_condition(pos, exit_price, ts, cfg, curve_completion_pct=50.0)

    assert result is not None
    reason, price_out = result
    assert reason == CopytradePosition.EXIT_TP
    assert price_out == pytest.approx(exit_price)


# ---------------------------------------------------------------------------
# 2. SL trigger fires first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sl_fires_first():
    """price <= entry*(1-sl/100) triggers SL exit before CURVE / TIMER."""
    pos = _make_position()
    cfg = _config(stop_loss_pct=40.0)

    # Price at 0.59 — below SL threshold (0.6); not at TP (1.5); not expired
    exit_price = 0.59
    ts = _T0 + timedelta(seconds=10)

    result = check_exit_condition(pos, exit_price, ts, cfg, curve_completion_pct=50.0)

    assert result is not None
    reason, price_out = result
    assert reason == CopytradePosition.EXIT_SL
    assert price_out == pytest.approx(exit_price)


# ---------------------------------------------------------------------------
# 3. CURVE trigger fires first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_curve_fires_first():
    """curve_completion >= curve_completion_exit_pct triggers CURVE exit."""
    pos = _make_position()
    cfg = _config(curve_completion_exit_pct=90.0, exit_before_graduation=True)

    # Price at 1.0 — no TP (threshold 1.5), no SL (threshold 0.6)
    # Curve at 91% — above 90% threshold
    exit_price = 1.0
    ts = _T0 + timedelta(seconds=10)

    result = check_exit_condition(pos, exit_price, ts, cfg, curve_completion_pct=91.0)

    assert result is not None
    reason, price_out = result
    assert reason == CopytradePosition.EXIT_CURVE
    assert price_out == pytest.approx(exit_price)


# ---------------------------------------------------------------------------
# 4. TIMER trigger fires first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_timer_fires_first():
    """held_seconds >= max_hold_seconds triggers TIMER exit."""
    pos = _make_position()
    cfg = _config(max_hold_seconds=60)

    # Price at 1.0 — no TP, no SL; curve at 50% (below threshold)
    exit_price = 1.0
    ts = _T0 + timedelta(seconds=61)  # 61 s >= 60 s

    result = check_exit_condition(pos, exit_price, ts, cfg, curve_completion_pct=50.0)

    assert result is not None
    reason, price_out = result
    assert reason == CopytradePosition.EXIT_TIMER
    assert price_out == pytest.approx(exit_price)


# ---------------------------------------------------------------------------
# 5. No exit when all conditions are below threshold
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_exit_when_all_below_threshold():
    """Returns None when price is between TP/SL, curve is low, hold is short."""
    pos = _make_position()
    cfg = _config(
        take_profit_pct=50.0,
        stop_loss_pct=40.0,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=3600,
    )

    # price=1.2 — between SL (0.6) and TP (1.5); curve=50%; 10 s elapsed
    result = check_exit_condition(
        pos, 1.2, _T0 + timedelta(seconds=10), cfg, curve_completion_pct=50.0
    )

    assert result is None


# ---------------------------------------------------------------------------
# 6. CURVE suppressed when exit_before_graduation=False
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_curve_suppressed_when_exit_before_graduation_false():
    """CURVE exit must not fire when exit_before_graduation is False."""
    pos = _make_position()
    cfg = _config(exit_before_graduation=False, curve_completion_exit_pct=90.0)

    # Curve at 99% — would normally trigger CURVE, but flag is False
    result = check_exit_condition(
        pos, 1.0, _T0 + timedelta(seconds=10), cfg, curve_completion_pct=99.0
    )

    assert result is None


# ---------------------------------------------------------------------------
# 7. TP realized_pnl_sol is correct
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tp_realized_pnl_sol_formula():
    """close_position records correct realized_pnl_sol on TP exit.

    sol_out = sol_in * (exit_price / entry_price)
    realized_pnl_sol = sol_out - sol_in
    """
    pos = _make_position()
    exit_price = 1.51
    exit_ts = _T0 + timedelta(seconds=10)

    closed = close_position(pos, CopytradePosition.EXIT_TP, exit_price, exit_ts)

    expected_sol_out = _SOL_IN * (exit_price / _ENTRY_PRICE)
    expected_pnl_sol = expected_sol_out - _SOL_IN
    expected_pnl_pct = (expected_pnl_sol / _SOL_IN) * 100.0

    assert closed.exit_reason == CopytradePosition.EXIT_TP
    assert closed.status == CopytradePosition.STATUS_CLOSED
    assert closed.sol_out == pytest.approx(expected_sol_out)
    assert closed.realized_pnl_sol == pytest.approx(expected_pnl_sol)
    assert closed.realized_pnl_pct == pytest.approx(expected_pnl_pct)


# ---------------------------------------------------------------------------
# 8. SL realized_pnl_pct formula is correct
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sl_realized_pnl_pct_formula():
    """close_position records correct realized_pnl_pct on SL exit."""
    pos = _make_position()
    exit_price = 0.59
    exit_ts = _T0 + timedelta(seconds=10)

    closed = close_position(pos, CopytradePosition.EXIT_SL, exit_price, exit_ts)

    expected_pnl_pct = ((exit_price / _ENTRY_PRICE) - 1.0) * 100.0

    assert closed.exit_reason == CopytradePosition.EXIT_SL
    assert closed.realized_pnl_pct == pytest.approx(expected_pnl_pct, rel=1e-5)
    assert closed.realized_pnl_sol < 0  # SL must be a loss


# ---------------------------------------------------------------------------
# 9. CURVE records zero PnL at flat price
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_curve_exit_at_flat_price_zero_pnl():
    """CURVE exit at unchanged price records zero realized PnL."""
    pos = _make_position()
    exit_ts = _T0 + timedelta(seconds=30)

    closed = close_position(pos, CopytradePosition.EXIT_CURVE, _ENTRY_PRICE, exit_ts)

    assert closed.exit_reason == CopytradePosition.EXIT_CURVE
    assert closed.sol_out == pytest.approx(_SOL_IN)
    assert closed.realized_pnl_sol == pytest.approx(0.0, abs=1e-10)
    assert closed.realized_pnl_pct == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# 10. TIMER records correct PnL when price moved
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_timer_exit_records_pnl_when_price_moved():
    """TIMER exit records PnL proportional to price change."""
    pos = _make_position()
    exit_price = 1.20
    exit_ts = _T0 + timedelta(seconds=61)

    closed = close_position(pos, CopytradePosition.EXIT_TIMER, exit_price, exit_ts)

    expected_pnl_sol = _SOL_IN * ((exit_price / _ENTRY_PRICE) - 1.0)
    assert closed.exit_reason == CopytradePosition.EXIT_TIMER
    assert closed.realized_pnl_sol == pytest.approx(expected_pnl_sol, rel=1e-5)


# ---------------------------------------------------------------------------
# 11. copytrade_pnl_by_wallet rollup is created on first close
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pnl_by_wallet_created_on_first_close():
    """Closing the first position must create one CopytradePnlByWallet row."""
    assert CopytradePnlByWallet.objects.count() == 0

    pos = _make_position()
    close_position(pos, CopytradePosition.EXIT_TP, 1.51, _T0 + timedelta(seconds=10))

    assert CopytradePnlByWallet.objects.count() == 1
    rollup = CopytradePnlByWallet.objects.get(cohort_id=_COHORT_ID, address=_WALLET)
    assert rollup.n_trades == 1
    assert rollup.total_pnl_sol == pytest.approx(pos.realized_pnl_sol)
    assert rollup.win_rate == pytest.approx(1.0)  # one win


# ---------------------------------------------------------------------------
# 12. copytrade_pnl_by_wallet rollup accumulates over multiple closes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pnl_by_wallet_accumulates_over_multiple_closes():
    """After two position closes, the rollup reflects both trades."""
    # Trade 1: TP — a win
    pos1 = _make_position(mint="MintA" + "X" * 39)
    close_position(pos1, CopytradePosition.EXIT_TP, 1.51, _T0 + timedelta(seconds=10))

    # Trade 2: SL — a loss
    pos2 = _make_position(mint="MintB" + "X" * 39)
    close_position(pos2, CopytradePosition.EXIT_SL, 0.59, _T0 + timedelta(seconds=20))

    rollup = CopytradePnlByWallet.objects.get(cohort_id=_COHORT_ID, address=_WALLET)
    assert rollup.n_trades == 2
    assert rollup.win_rate == pytest.approx(0.5)  # 1 win out of 2
    # total_pnl_sol = pnl1 + pnl2 (one positive, one negative)
    expected_total = (
        _SOL_IN * (1.51 / _ENTRY_PRICE - 1.0)
        + _SOL_IN * (0.59 / _ENTRY_PRICE - 1.0)
    )
    assert rollup.total_pnl_sol == pytest.approx(expected_total, rel=1e-5)
    assert rollup.avg_hold_s > 0.0


# ---------------------------------------------------------------------------
# 13. check_and_close_position returns None when no exit fires
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_and_close_returns_none_when_no_exit():
    """check_and_close_position returns None when position remains open."""
    pos = _make_position()
    cfg = _config()

    result = check_and_close_position(
        pos, 1.2, _T0 + timedelta(seconds=10), cfg, curve_completion_pct=50.0
    )

    assert result is None
    # Position is still open in the DB
    pos.refresh_from_db()
    assert pos.status == CopytradePosition.STATUS_OPEN


# ---------------------------------------------------------------------------
# 14. check_and_close_position returns closed position when exit fires
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_and_close_returns_closed_position_when_exit_fires():
    """check_and_close_position closes the position and returns it on TP."""
    pos = _make_position()
    cfg = _config(take_profit_pct=50.0)

    closed = check_and_close_position(
        pos, 1.51, _T0 + timedelta(seconds=10), cfg, curve_completion_pct=0.0
    )

    assert closed is not None
    assert closed.status == CopytradePosition.STATUS_CLOSED
    assert closed.exit_reason == CopytradePosition.EXIT_TP
    # DB row is also updated
    pos.refresh_from_db()
    assert pos.status == CopytradePosition.STATUS_CLOSED
