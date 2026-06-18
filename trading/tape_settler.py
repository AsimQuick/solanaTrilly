# ---
# module: trading.tape_settler
# sprint: sprint-13
# story: US-66 AC-66.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.schemas
# ---
"""Tape settler: simulate_tape_exit — the SOLE paper/observe settler (Principle #5).

Ports solanatrills/analysis/wallet_strategy/tape_resettle.py resettle() VERBATIM
(PRD §10.2 / AC-66.2).

Honest-fill conventions (from tape_resettle.py / birdeye_tape_label_conf.py):
    entry quote  = last swap in [entry−30, entry]
    fill         = first swap at entry+2s
    slip-cap     = 15% (fill/quote−1 > 0.15 → not enterable, as live 6002 miss)
    exit trigger → sell fill at first swap >= trigger_t+2s
    impact cost  = 2·size_usd/(size_usd+flow_usd)  (market-impact model)
    fee          = 1%

Un-enterable rows (no quote, no fill, or slip > 15%) are EXCLUDED — NEVER booked
as 0% or −100%.  This is the SOLE settler for paper/observe positions (Principle #5;
no second exit engine exists for paper).

Trigger priority (§10.2 — tape settler walk order):
    1  TAKE_PROFIT_PCT   — absolute percentage gain
    2  DISASTER_CAP      — deep drop guard
    3  STOP_LOSS         — ordinary loss gate
    4  RUG_PULL          — armed only after peak > fill * 1.05 (+5%)
    5  AUTO_SELL_TIMER   — maximum hold-time (hard deadline)

NOTE: This priority order differs from evaluate_exit_rules (§10.1) which fires
RUG_PULL first.  The tape settler's walk checks TP first because each swap is
examined sequentially at its exact price — TP has priority when both conditions
are met on the same swap.

Public API
----------
simulate_tape_exit(trades, entry_ts, config, size_sol, sol_usd) -> dict
    Returns a result dict matching the oracle resettle() output format.
    Unentered: {'enterable': False, 'reason': str}
    Entered:   {'enterable': True, 'pnl': float, 'trigger': str,
                'held': float, 'peak': float, 'flow': float}
"""

from __future__ import annotations

from typing import Sequence, Tuple

from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# Constants (verbatim from tape_resettle.py)
# ---------------------------------------------------------------------------

_GAP: int = 30    # seconds before entry for pre-trade window (quote)
_LAT: int = 2     # seconds latency added to entry for fill / exit fill
_CAP: float = 0.15  # 15% slip cap (mirrors live 6002 rejection)
_FEE: float = 0.01  # 1% round-trip fee

# Type alias: (block_time_seconds, price, usd_volume)
TradeTuple = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# simulate_tape_exit — public entry point
# ---------------------------------------------------------------------------


def simulate_tape_exit(
    trades: Sequence[TradeTuple],
    entry_ts: float,
    config: TradingConfig,
    size_sol: float,
    sol_usd: float = 140.0,
) -> dict:
    """Settle one paper/observe position against its swap tape (PRD §10.2).

    Ports tape_resettle.py resettle() VERBATIM.  All exit-rule logic, fill
    conventions, and impact formula are identical to the trills oracle.

    Parameters
    ----------
    trades:
        Pre-sorted list of (block_time_seconds, price, usd_volume) tuples for
        the position's mint.  Sort order: (block_time, slot, signature) —
        deterministic, matching load_tape() in tape_resettle.py.
    entry_ts:
        Position entry timestamp as a Unix epoch float (seconds).
    config:
        TradingConfig instance providing all exit-rule thresholds.
    size_sol:
        Position size in SOL.  Converted to USD via sol_usd for the impact
        formula (matching the oracle's USD-denominated ``size`` argument).
    sol_usd:
        SOL/USD rate used to convert SOL amounts to USD for impact.
        Default 140.0 matches the oracle's ``--sol-usd`` default.

    Returns
    -------
    dict
        Unentered: {'enterable': False, 'reason': 'no-tape'|'dead'|'slip-miss'}
        Entered:   {'enterable': True, 'pnl': float, 'trigger': str,
                    'held': float, 'peak': float, 'flow': float}
        pnl is a percentage (e.g. 50.0 = +50%).
        held is seconds from entry to trigger.
        peak is the peak return percentage from fill.
        flow is the total USD volume in the [entry−30, entry+30] window.
    """
    size_usd = size_sol * sol_usd
    pol = _config_to_pol(config)
    return _resettle(list(trades), entry_ts, pol, size_usd)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _config_to_pol(config: TradingConfig) -> dict:
    """Convert TradingConfig (percentage-based) to oracle pol dict (fractional).

    The oracle resettle() uses fractional thresholds (0.0–1.0) while
    TradingConfig stores percentages (0.0–100.0).  The conversion is
    field-by-field division by 100.
    """
    return {
        "timer": config.auto_sell_timer_s,
        "tp": config.take_profit_pct / 100.0,
        "sl": config.stop_loss_pct / 100.0,
        "disaster": config.disaster_cap_pct / 100.0,
        "rugcut": config.rug_pull_drop_pct / 100.0,
    }


def _resettle(
    trades: list,
    entry: float,
    pol: dict,
    size: float,
) -> dict:
    """Verbatim port of tape_resettle.py resettle().

    Argument names and variable names are kept identical to the oracle for
    line-by-line auditability.  Only the policy source changes: the caller
    passes a pre-converted pol dict (fractional thresholds) via _config_to_pol.

    Parameters
    ----------
    trades:
        List of (t, p, usd) tuples — identical to the oracle's input.
    entry:
        Position entry Unix epoch seconds.
    pol:
        Policy dict: {'timer': int, 'tp': float|None, 'sl': float|None,
                      'disaster': float|None, 'rugcut': float|None}
        All threshold values are fractional (0.0–1.0), not percentages.
    size:
        Position size in USD — matches oracle's ``size`` parameter.
    """
    # ---- verbatim from tape_resettle.py resettle() -------------------------
    if not trades:
        return dict(enterable=False, reason="no-tape")
    timer = pol["timer"]
    pre = [p for t, p, _ in trades if entry - _GAP <= t <= entry]
    post = [(t, p) for t, p, _ in trades if entry + _LAT <= t <= entry + timer + _GAP]
    if not pre or not post:
        return dict(enterable=False, reason="dead")
    quote, fill = pre[-1], post[0][1]
    if fill / quote - 1 > _CAP:
        return dict(enterable=False, reason="slip-miss")
    peak = fill
    trig_t = trig = None
    for t, p in post:
        peak = max(peak, p)
        ret, held = p / fill - 1, t - (entry + _LAT)
        if pol["tp"] is not None and ret >= pol["tp"]:
            trig_t, trig = t, "TAKE_PROFIT_PCT"
            break
        if pol["disaster"] is not None and ret <= -pol["disaster"]:
            trig_t, trig = t, "DISASTER_CAP"
            break
        if pol["sl"] is not None and ret <= -pol["sl"]:
            trig_t, trig = t, "STOP_LOSS"
            break
        if pol["rugcut"] is not None and peak / fill > 1.05 and p <= peak * (1 - pol["rugcut"]):
            trig_t, trig = t, "RUG_PULL"
            break
        if held >= timer:
            trig_t, trig = t, "AUTO_SELL_TIMER"
            break
    if trig_t is None:
        trig_t, trig = post[-1][0], "AUTO_SELL_TIMER"
    after = [p for t, p in post if t >= trig_t + _LAT]
    exitp = after[0] if after else post[-1][1]
    flow = sum(u for t, _, u in trades if entry - _GAP <= t <= entry + _GAP)
    cost = 2 * size / (size + flow) if flow > 0 else 0.20
    pnl = ((exitp / fill) * (1 - _FEE) * (1 - cost) - 1) * 100
    return dict(
        enterable=True,
        pnl=pnl,
        trigger=trig,
        held=trig_t - entry,
        peak=(peak / fill - 1) * 100,
        flow=flow,
    )
    # ---- end verbatim -------------------------------------------------------
