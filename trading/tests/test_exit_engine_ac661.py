# ---
# module: trading.tests.test_exit_engine_ac661
# sprint: sprint-13
# story: US-66 AC-66.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.exit_engine, trading.schemas
# ---
"""Deterministic unit tests for evaluate_exit_rules (AC-66.1 §10.1 priority ladder).

Coverage — each test drives EXACTLY ONE rule to fire first:
  T01  RUG_PULL fires first
  T02  DISASTER_CAP fires first
  T03  STOP_LOSS fires first (outside grace)
  T04  STOP_LOSS skipped inside grace → None
  T05  NEXT_POLL_GUARD fires first (inside grace, overrides stopped SL)
  T06  NEXT_POLL_GUARD falls open when next_poll is None
  T07  TAKE_PROFIT_PCT fires first
  T08  AUTO_SELL_TIMER fires first
  T09  CEILING fires first (take_profit raised above ceiling)
  T10  VOLUME_COLLAPSE fires first
  T11  CONCENTRATION fires first
  T12  TRAILING fires first
  T13  TRAILING not armed inside grace → None
  T14  TRAILING not armed when peak below arm multiple → None
  T15  STALE fires first (auto_sell_timer above stale window)
  T16  No rule fires → None
  T17  RUG_PULL fires inside grace (ignores grace)
  T18  DISASTER_CAP fires inside grace (ignores grace)
  T19  Priority — RUG beats DISASTER when both conditions met
  T20  Priority — STOP_LOSS beats NEXT_POLL_GUARD (both fire-eligible)

All tests are offline/deterministic — no Django DB, no Redis, no network.
"""

from datetime import datetime, timedelta, timezone

from trading.exit_engine import PositionSnapshot, evaluate_exit_rules
from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTRY_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(**overrides) -> TradingConfig:
    """Build a TradingConfig with safe defaults, applying overrides."""
    defaults = dict(
        trading_enabled=False,
        stop_loss_pct=20.0,
        disaster_cap_pct=40.0,
        rug_pull_drop_pct=50.0,
        next_poll_guard_s=10,
        take_profit_pct=100.0,
        auto_sell_timer_s=300,
        trailing_pct=20.0,
        trailing_arm_multiple=1.05,
        trailing_grace_s=60,
        ceiling_pct=300.0,
        volume_collapse_threshold_pct=10.0,
        concentration_threshold_pct=40.0,
        stale_timeout_s=1800,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def _snap(
    entry: float = 1.0,
    current: float = 1.0,
    peak: float = 1.0,
    seconds_held: int = 30,
    next_poll: float | None = None,
    curr_vol: float | None = None,
    entry_vol: float | None = None,
    curr_conc: float | None = None,
    entry_conc: float | None = None,
) -> tuple[PositionSnapshot, datetime]:
    """Return (PositionSnapshot, now) for a given set of scenario parameters."""
    now = _ENTRY_TS + timedelta(seconds=seconds_held)
    snap = PositionSnapshot(
        entry_price=entry,
        current_price=current,
        peak_price=peak,
        entry_ts=_ENTRY_TS,
        next_poll_snapshot_price=next_poll,
        current_volume=curr_vol,
        entry_volume=entry_vol,
        current_concentration=curr_conc,
        entry_concentration=entry_conc,
    )
    return snap, now


# ---------------------------------------------------------------------------
# T01 — RUG_PULL fires first
# ---------------------------------------------------------------------------

def test_rug_pull_fires_first():
    """current=0.45 ≤ entry*0.50 → RUG_PULL (priority 1)."""
    snap, now = _snap(entry=1.0, current=0.45, seconds_held=30)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "RUG_PULL"


# ---------------------------------------------------------------------------
# T02 — DISASTER_CAP fires first
# ---------------------------------------------------------------------------

def test_disaster_cap_fires_first():
    """current=0.55 > 0.50 (not RUG), ≤ 0.60 (DISASTER at 40%) → DISASTER_CAP."""
    snap, now = _snap(entry=1.0, current=0.55, seconds_held=30)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "DISASTER_CAP"


# ---------------------------------------------------------------------------
# T03 — STOP_LOSS fires first (outside grace)
# ---------------------------------------------------------------------------

def test_stop_loss_fires_first():
    """seconds=90 > grace=60; current=0.79 ≤ entry*0.80 → STOP_LOSS."""
    snap, now = _snap(entry=1.0, current=0.79, seconds_held=90)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "STOP_LOSS"


# ---------------------------------------------------------------------------
# T04 — STOP_LOSS skipped inside grace → None
# ---------------------------------------------------------------------------

def test_stop_loss_skipped_in_grace():
    """seconds=30 < grace=60; STOP_LOSS skipped, no other rule fires → None."""
    snap, now = _snap(entry=1.0, current=0.79, seconds_held=30)
    # 0.79 = 21% drop — qualifies for STOP_LOSS numerically but is inside grace.
    # RUG_PULL threshold: 0.79 > 0.50 → no.
    # DISASTER_CAP threshold: 0.79 > 0.60 → no.
    # next_poll is None → NEXT_POLL_GUARD falls open.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result is None


# ---------------------------------------------------------------------------
# T05 — NEXT_POLL_GUARD fires first (inside grace)
# ---------------------------------------------------------------------------

def test_next_poll_guard_fires_first():
    """next_poll=0.92 < entry=1.0 → NEXT_POLL_GUARD (priority 4, no grace dependency)."""
    snap, now = _snap(entry=1.0, current=0.97, next_poll=0.92, seconds_held=30)
    # In grace (30 < 60): STOP_LOSS skipped.
    # RUG/DISASTER: 0.97 fine.
    # NEXT_POLL_GUARD: 0.92 < 1.0 → fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "NEXT_POLL_GUARD"


# ---------------------------------------------------------------------------
# T06 — NEXT_POLL_GUARD falls open when next_poll is None
# ---------------------------------------------------------------------------

def test_next_poll_guard_falls_open_when_none():
    """next_poll=None → NEXT_POLL_GUARD rule disabled; no other rule fires → None."""
    snap, now = _snap(entry=1.0, current=0.90, next_poll=None, seconds_held=30)
    # 10% drop, inside grace, no poll snapshot → only emergency rules could fire.
    # RUG: 0.90 > 0.50 → no. DISASTER: 0.90 > 0.60 → no.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result is None


# ---------------------------------------------------------------------------
# T07 — TAKE_PROFIT_PCT fires first
# ---------------------------------------------------------------------------

def test_take_profit_pct_fires_first():
    """current=2.01 ≥ entry*2.0 (take_profit_pct=100%) → TAKE_PROFIT_PCT."""
    snap, now = _snap(entry=1.0, current=2.01, seconds_held=30)
    # next_poll=None (falls open). No emergency. TAKE_PROFIT fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "TAKE_PROFIT_PCT"


# ---------------------------------------------------------------------------
# T08 — AUTO_SELL_TIMER fires first
# ---------------------------------------------------------------------------

def test_auto_sell_timer_fires_first():
    """seconds=305 ≥ auto_sell_timer_s=300; peak=1.04 < 1.05 (trailing not armed)."""
    snap, now = _snap(entry=1.0, current=1.05, peak=1.04, seconds_held=305)
    # No emergency. current > entry (NEXT_POLL_GUARD won't fire). TAKE_PROFIT: 1.05 < 2.0.
    # AUTO_SELL_TIMER: 305 ≥ 300 → fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "AUTO_SELL_TIMER"


# ---------------------------------------------------------------------------
# T09 — CEILING fires first
# ---------------------------------------------------------------------------

def test_ceiling_fires_first():
    """take_profit raised to 500% (fires at 6x); ceiling=300% (fires at 4x).
    current=4.01 ≥ 4.0 → CEILING before TAKE_PROFIT."""
    cfg = _cfg(take_profit_pct=500.0)  # fires at entry*6.0
    snap, now = _snap(entry=1.0, current=4.01, seconds_held=30)
    # TAKE_PROFIT: 4.01 < 6.0 → no.
    # AUTO_SELL: 30 < 300 → no.
    # CEILING (300%): 4.01 ≥ 1.0*(1+300/100)=4.0 → fires.
    result = evaluate_exit_rules(snap, cfg, now=now)
    assert result == "CEILING"


# ---------------------------------------------------------------------------
# T10 — VOLUME_COLLAPSE fires first
# ---------------------------------------------------------------------------

def test_volume_collapse_fires_first():
    """curr_vol=700, entry_vol=1000 (30% drop vs 10% threshold) → VOLUME_COLLAPSE."""
    snap, now = _snap(
        entry=1.0, current=1.05, peak=1.05, seconds_held=100,
        curr_vol=700.0, entry_vol=1000.0,
    )
    # TAKE_PROFIT: 1.05 < 2.0 → no. AUTO_SELL: 100 < 300 → no. CEILING: 1.05 < 4.0 → no.
    # VOLUME_COLLAPSE: 700 < 1000*(1-0.10)=900 → fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "VOLUME_COLLAPSE"


# ---------------------------------------------------------------------------
# T11 — CONCENTRATION fires first
# ---------------------------------------------------------------------------

def test_concentration_fires_first():
    """curr_conc=55, entry_conc=10, delta=45 > threshold=40 → CONCENTRATION."""
    snap, now = _snap(
        entry=1.0, current=1.05, peak=1.05, seconds_held=100,
        curr_conc=55.0, entry_conc=10.0,
        # no volume data → VOLUME_COLLAPSE falls open
    )
    # TAKE_PROFIT: 1.05 < 2.0 → no. AUTO_SELL: 100 < 300 → no. CEILING: 1.05 < 4.0 → no.
    # VOLUME_COLLAPSE: falls open (no volume). CONCENTRATION: 45 > 40 → fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "CONCENTRATION"


# ---------------------------------------------------------------------------
# T12 — TRAILING fires first
# ---------------------------------------------------------------------------

def test_trailing_fires_first():
    """peak=1.20 ≥ entry*1.05; not in grace (90>60); current=0.85 ≤ peak*0.80=0.96."""
    snap, now = _snap(entry=1.0, peak=1.20, current=0.85, seconds_held=90)
    # RUG: 0.85 > 0.50 → no. DISASTER: 0.85 > 0.60 → no.
    # STOP_LOSS: not in grace, 0.85 > 0.80 → no (threshold is ≤0.80, 0.85 > 0.80).
    # next_poll=None → NEXT_POLL_GUARD falls open.
    # TAKE_PROFIT: 0.85 < 2.0 → no. AUTO_SELL: 90 < 300 → no. CEILING: 0.85 < 4.0 → no.
    # VOLUME/CONCENTRATION: falls open.
    # TRAILING: peak=1.20 ≥ 1.05, not in grace, 0.85 ≤ 1.20*0.80=0.96 → fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "TRAILING"


# ---------------------------------------------------------------------------
# T13 — TRAILING not armed inside grace → None
# ---------------------------------------------------------------------------

def test_trailing_not_armed_in_grace():
    """seconds=30 < grace=60; TRAILING blocked even with armed peak → None."""
    snap, now = _snap(entry=1.0, peak=1.20, current=0.85, seconds_held=30)
    # RUG: 0.85 > 0.50 → no. DISASTER: 0.85 > 0.60 → no.
    # STOP_LOSS: in grace → skipped.
    # next_poll=None → NEXT_POLL_GUARD falls open.
    # TAKE_PROFIT: 0.85 < 2.0 → no. AUTO_SELL: 30 < 300 → no. CEILING: 0.85 < 4.0 → no.
    # VOLUME/CONCENTRATION: falls open.
    # TRAILING: in_grace=True → not armed → no.
    # STALE: 30 < 1800 → no.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result is None


# ---------------------------------------------------------------------------
# T14 — TRAILING not armed when peak below arm multiple → None
# ---------------------------------------------------------------------------

def test_trailing_not_armed_peak_below_multiple():
    """peak=1.03 < entry*1.05=1.05 → trailing not armed → None."""
    snap, now = _snap(entry=1.0, peak=1.03, current=0.85, seconds_held=90)
    # Not in grace (90 > 60).
    # STOP_LOSS: 0.85 > 0.80 → no (entry*0.80=0.80; 0.85 > 0.80 so NOT triggered).
    # next_poll=None → NEXT_POLL_GUARD falls open.
    # TAKE_PROFIT: 0.85 < 2.0 → no. AUTO_SELL: 90 < 300 → no. CEILING: 0.85 < 4.0 → no.
    # VOLUME/CONCENTRATION: falls open.
    # TRAILING: peak=1.03 < 1.05 → not armed → no.
    # STALE: 90 < 1800 → no.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result is None


# ---------------------------------------------------------------------------
# T15 — STALE fires first
# ---------------------------------------------------------------------------

def test_stale_fires_first():
    """auto_sell_timer_s=2000, stale_timeout_s=1800; seconds=1850 ≥ 1800 → STALE."""
    cfg = _cfg(auto_sell_timer_s=2000, stale_timeout_s=1800)
    snap, now = _snap(entry=1.0, current=1.05, peak=1.04, seconds_held=1850)
    # AUTO_SELL: 1850 < 2000 → no.
    # TRAILING: peak=1.04 < 1.0*1.05=1.05 → not armed → no.
    # STALE: 1850 ≥ 1800 → fires.
    result = evaluate_exit_rules(snap, cfg, now=now)
    assert result == "STALE"


# ---------------------------------------------------------------------------
# T16 — No rule fires → None
# ---------------------------------------------------------------------------

def test_no_rule_fires():
    """Healthy position well within all thresholds → None."""
    snap, now = _snap(entry=1.0, current=1.05, peak=1.04, seconds_held=30)
    # current=1.05, peak=1.04 (<1.05 arm), seconds=30 (<60 grace) → no rule fires.
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result is None


# ---------------------------------------------------------------------------
# T17 — RUG_PULL fires inside grace
# ---------------------------------------------------------------------------

def test_rug_pull_fires_inside_grace():
    """seconds=10 (inside grace); RUG_PULL ignores grace → still fires."""
    snap, now = _snap(entry=1.0, current=0.45, seconds_held=10)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "RUG_PULL"


# ---------------------------------------------------------------------------
# T18 — DISASTER_CAP fires inside grace
# ---------------------------------------------------------------------------

def test_disaster_cap_fires_inside_grace():
    """seconds=10 (inside grace); DISASTER_CAP ignores grace → still fires."""
    snap, now = _snap(entry=1.0, current=0.55, seconds_held=10)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "DISASTER_CAP"


# ---------------------------------------------------------------------------
# T19 — Priority: RUG_PULL beats DISASTER_CAP when both conditions met
# ---------------------------------------------------------------------------

def test_priority_order_rug_beats_disaster():
    """current=0.40 triggers both RUG (≤0.50) and DISASTER (≤0.60) → RUG_PULL wins."""
    snap, now = _snap(entry=1.0, current=0.40, seconds_held=30)
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "RUG_PULL"


# ---------------------------------------------------------------------------
# T20 — Priority: STOP_LOSS beats NEXT_POLL_GUARD when both eligible
# ---------------------------------------------------------------------------

def test_priority_order_stop_loss_beats_next_poll():
    """current=0.75, next_poll=0.80; both STOP_LOSS and NEXT_POLL_GUARD would fire.
    STOP_LOSS (priority 3) fires before NEXT_POLL_GUARD (priority 4)."""
    snap, now = _snap(entry=1.0, current=0.75, next_poll=0.80, seconds_held=90)
    # Not in grace (90 > 60).
    # RUG: 0.75 > 0.50 → no. DISASTER: 0.75 > 0.60 → no.
    # STOP_LOSS: 0.75 ≤ 0.80 → fires (priority 3).
    # NEXT_POLL_GUARD: 0.80 < 1.0 → would also fire (priority 4, but not reached).
    result = evaluate_exit_rules(snap, _cfg(), now=now)
    assert result == "STOP_LOSS"
