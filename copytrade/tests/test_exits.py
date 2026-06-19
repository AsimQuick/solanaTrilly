# ---
# module: copytrade.tests.test_exits
# sprint: cutover (copy-trade live)
# story: per-head-exits
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: pytest
# ---
"""Deterministic tests for the per-head exit evaluators (moonshot + scalp)."""
from datetime import datetime, timedelta, timezone

from copytrade.exits import (
    EXIT_MIRROR,
    EXIT_SL,
    EXIT_TIMER,
    EXIT_TP,
    EXIT_TRAIL,
    evaluate_mirror_exit,
    evaluate_trailing_exit,
)
from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit

T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)

# cohort moonshot exit: SL 50%, trailing 40%, TP 900% (10x), no time cap.
MOON = OurTrailingExit(type="our_trailing", stop_loss_pct=50, trailing_giveback_pct=40, take_profit_pct=900)
# cohort scalp exit: mirror, 24h fallback, no hard stop.
SCALP = MirrorWalletSellExit(type="mirror_wallet_sell", max_hold_seconds=86400, hard_stop_loss_pct=None)


def _trail(entry, price, hw, secs=0):
    return evaluate_trailing_exit(
        MOON, entry_price=entry, current_price=price, high_water_price=hw,
        entry_ts=T0, current_ts=T0 + timedelta(seconds=secs),
    )


# --- moonshot our_trailing ---------------------------------------------------


def test_trailing_holds_when_flat():
    d = _trail(1.0, 1.0, 1.0)
    assert not d.should_close
    assert d.high_water_price == 1.0


def test_trailing_take_profit_at_10x():
    d = _trail(1.0, 10.0, 5.0)  # +900%
    assert d.exit_reason == EXIT_TP
    assert d.exit_price == 10.0


def test_trailing_hard_stop_loss_at_minus_50():
    d = _trail(1.0, 0.5, 1.0)  # -50%
    assert d.exit_reason == EXIT_SL


def test_trailing_ratchets_high_water():
    # price 3.0 with prior hw 2.0 -> hw becomes 3.0, no exit (not retraced yet)
    d = _trail(1.0, 3.0, 2.0)
    assert d.high_water_price == 3.0
    assert not d.should_close


def test_trailing_giveback_fires_after_profit():
    # high-water 5x; 40% giveback stop = 3.0. At 2.9 -> TRAIL.
    d = _trail(1.0, 2.9, 5.0)
    assert d.exit_reason == EXIT_TRAIL
    assert d.high_water_price == 5.0


def test_trailing_no_giveback_before_profit():
    # never profitable (hw == entry); a dip that is not -50% must NOT trail.
    d = _trail(1.0, 0.7, 1.0)  # -30%, hw stays 1.0 (== entry) -> no trail, no SL
    assert not d.should_close


def test_trailing_giveback_not_triggered_when_close_to_high():
    # hw 5x, price 4.0 -> giveback stop 3.0, 4.0 > 3.0 -> hold
    d = _trail(1.0, 4.0, 5.0)
    assert not d.should_close
    assert d.high_water_price == 5.0


def test_trailing_no_timer_when_max_hold_none():
    d = _trail(1.0, 1.0, 1.0, secs=10_000_000)  # huge elapsed, but max_hold None
    assert not d.should_close


def test_trailing_timer_when_configured():
    cfg = OurTrailingExit(type="our_trailing", stop_loss_pct=50, trailing_giveback_pct=40,
                          take_profit_pct=900, max_hold_seconds=60)
    d = evaluate_trailing_exit(cfg, entry_price=1.0, current_price=1.0, high_water_price=1.0,
                               entry_ts=T0, current_ts=T0 + timedelta(seconds=120))
    assert d.exit_reason == EXIT_TIMER


# --- scalp mirror_wallet_sell ------------------------------------------------


def _mirror(sold, price=1.0, secs=0, cfg=SCALP):
    return evaluate_mirror_exit(
        cfg, source_wallet_sold=sold, entry_price=1.0, current_price=price,
        entry_ts=T0, current_ts=T0 + timedelta(seconds=secs),
    )


def test_mirror_exits_when_source_wallet_sells():
    d = _mirror(True, price=1.3)
    assert d.exit_reason == EXIT_MIRROR
    assert d.exit_price == 1.3


def test_mirror_holds_while_wallet_holds():
    d = _mirror(False, price=1.5)
    assert not d.should_close


def test_mirror_no_imposed_stop_by_default():
    # hard_stop_loss_pct is None for scalp -> even a big drop does NOT stop us.
    d = _mirror(False, price=0.1)  # -90%
    assert not d.should_close


def test_mirror_fallback_timer_at_max_hold():
    d = _mirror(False, price=1.0, secs=86400)
    assert d.exit_reason == EXIT_TIMER


def test_mirror_optional_hard_stop_when_configured():
    cfg = MirrorWalletSellExit(type="mirror_wallet_sell", max_hold_seconds=86400, hard_stop_loss_pct=60)
    d = evaluate_mirror_exit(cfg, source_wallet_sold=False, entry_price=1.0, current_price=0.3,
                             entry_ts=T0, current_ts=T0 + timedelta(seconds=10))
    assert d.exit_reason == EXIT_SL


def test_mirror_priority_sell_beats_timer():
    # both the sell signal and the timer are true -> MIRROR wins (checked first).
    d = _mirror(True, price=1.2, secs=999999)
    assert d.exit_reason == EXIT_MIRROR
