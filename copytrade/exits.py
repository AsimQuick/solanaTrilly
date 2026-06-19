# ---
# module: copytrade.exits
# sprint: cutover (copy-trade live)
# story: per-head-exits
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: copytrade.schemas (exit configs)
# ---
"""Deterministic per-head exit evaluators for cohort-2.0 (moonshot + scalp).

Each strategy head exits on its OWN rule (cohort.json `strategies[].exit`):

  moonshot  -> ``our_trailing``: hard SL, a trailing giveback from the high-water
               mark once profitable, and a hard TP cap.  We manage it ourselves;
               we do NOT mirror the source wallet's quick scalp sell.

  scalp     -> ``mirror_wallet_sell``: exit our copy WHEN the source wallet sells
               (the head's edge IS the wallet's exit timing).  No imposed TP/SL
               (optional hard stop); a ``max_hold_seconds`` fallback only.

These are PURE functions — no I/O, no clock, no ORM.  The current price, the
high-water mark, the timestamps, and "did the source wallet sell" are all
INJECTED by the engine loop, so the rules are replay-deterministic and exercised
entirely offline.  Exit reason strings match CopytradePosition.EXIT_* constants
("TP" / "SL" / "TRAIL" / "TIMER" / "MIRROR").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit

# Exit reason constants — kept in lockstep with copytrade.models.CopytradePosition.
EXIT_TP = "TP"
EXIT_SL = "SL"
EXIT_TRAIL = "TRAIL"
EXIT_TIMER = "TIMER"
EXIT_MIRROR = "MIRROR"


@dataclass(frozen=True)
class ExitDecision:
    """Outcome of one exit evaluation tick.

    Attributes
    ----------
    high_water_price:
        The updated high-water mark (max of the prior mark and current price).
        The caller PERSISTS this on the position even when no exit fires, so the
        trailing stop ratchets correctly across ticks.
    exit_reason:
        One of the EXIT_* constants when an exit fires this tick, else None.
    exit_price:
        The price to book the exit fill at when exit_reason is set, else None.
    """

    high_water_price: float
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None

    @property
    def should_close(self) -> bool:
        return self.exit_reason is not None


def evaluate_trailing_exit(
    exit_cfg: OurTrailingExit,
    *,
    entry_price: float,
    current_price: float,
    high_water_price: Optional[float],
    entry_ts: datetime,
    current_ts: datetime,
) -> ExitDecision:
    """moonshot ``our_trailing`` exit (first-to-fire wins).

    Order of checks:
      1. TP    — current_price >= entry * (1 + take_profit_pct/100)   [hard cap]
      2. SL    — current_price <= entry * (1 - stop_loss_pct/100)     [hard stop]
      3. TRAIL — once profitable (high-water > entry): current_price <=
                 high_water * (1 - trailing_giveback_pct/100)
      4. TIMER — only if max_hold_seconds is set and elapsed >= it

    The high-water mark is ratcheted to ``max(prior, current_price)`` FIRST so the
    trailing stop reflects this tick's high before being tested.
    """
    hw = max(high_water_price if high_water_price is not None else entry_price, current_price)

    # 1. Take-profit cap.
    if current_price >= entry_price * (1.0 + exit_cfg.take_profit_pct / 100.0):
        return ExitDecision(hw, EXIT_TP, current_price)

    # 2. Hard stop-loss.
    if current_price <= entry_price * (1.0 - exit_cfg.stop_loss_pct / 100.0):
        return ExitDecision(hw, EXIT_SL, current_price)

    # 3. Trailing giveback — only once the position has been profitable.
    if hw > entry_price:
        trail_stop = hw * (1.0 - exit_cfg.trailing_giveback_pct / 100.0)
        if current_price <= trail_stop:
            return ExitDecision(hw, EXIT_TRAIL, current_price)

    # 4. Optional max-hold timer.
    if exit_cfg.max_hold_seconds is not None:
        if (current_ts - entry_ts).total_seconds() >= exit_cfg.max_hold_seconds:
            return ExitDecision(hw, EXIT_TIMER, current_price)

    return ExitDecision(hw, None, None)


def evaluate_mirror_exit(
    exit_cfg: MirrorWalletSellExit,
    *,
    source_wallet_sold: bool,
    entry_price: float,
    current_price: float,
    entry_ts: datetime,
    current_ts: datetime,
) -> ExitDecision:
    """scalp ``mirror_wallet_sell`` exit (first-to-fire wins).

    Order of checks:
      1. MIRROR — the source wallet has sold (this head's edge): exit our copy.
      2. SL     — only if an optional hard_stop_loss_pct is configured (it is
                  intentionally None for the validated scalp wallets).
      3. TIMER  — fallback market-sell at max_hold_seconds if the wallet has not
                  exited by then.

    ``high_water_price`` is carried through (scalp does not use a trailing stop)
    so the same persistence path works for both heads.
    """
    hw = max(high_water_price_default(entry_price, current_price), current_price)

    # 1. Mirror the source wallet's sell.
    if source_wallet_sold:
        return ExitDecision(hw, EXIT_MIRROR, current_price)

    # 2. Optional hard stop (None for the validated scalp wallets).
    if exit_cfg.hard_stop_loss_pct is not None:
        if current_price <= entry_price * (1.0 - exit_cfg.hard_stop_loss_pct / 100.0):
            return ExitDecision(hw, EXIT_SL, current_price)

    # 3. Fallback max-hold timer.
    if (current_ts - entry_ts).total_seconds() >= exit_cfg.max_hold_seconds:
        return ExitDecision(hw, EXIT_TIMER, current_price)

    return ExitDecision(hw, None, None)


def high_water_price_default(entry_price: float, current_price: float) -> float:
    """High-water seed for a head that does not track one (scalp)."""
    return max(entry_price, current_price)
