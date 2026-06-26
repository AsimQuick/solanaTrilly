# ---
# module: core.v7_ride_exit
# sprint: sprint-15
# story: US-94
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: none
# ---
"""v7 post-grad inference pipeline: entry trigger, tr30_t600 ride exit, depth-gated sizing.

WHAT THIS MODULE DOES
=====================
Implements the three load-bearing mechanics for the v7 post-graduation lane
(US-94 ACs 94.1, 94.2, 94.3):

1. ENTRY TRIGGER  — ``find_entry_fill(swaps, grad_ts)``
   First post-grad AMM swap >= grad_ts + 2s, within 30s.
   Rejects if fill/quote - 1 > 15% (slip-miss) or dust-fill (fill < 0.3 × local
   median price).  Entering later is refuted; this function returns the earliest
   valid fill.

2. EXIT — tr30_t600  — ``apply_tr30_t600_exit(swaps, entry_price, entry_ts)``
   Track the running max from fill; sell when price <= running_max × (1 − 0.30)
   (30% trailing stop) OR at 600s, whichever first.  Honest fill = first swap
   >= trigger + 2s.  Fast scalps are refuted (post-grad open is a dump, win~30%).

3. SIZING  — ``compute_size_usd(entry_depth_usd)``
   size = $100 if entry_depth >= $8,000 else $25.  Cap $100 (never $200).
   Offline depth proxy = eflow ($-flow in [grad, grad+60]).
   Live depth = graduation-instant AMM pool liquidity.
   THIS SPRINT: observe-only at $25 FLAT; the $100 ramp is soak-gated (US-91).

HONEST-FILL GRADING CONVENTIONS (match settle_engine.py in the lab)
====================================================================
  Entry impact: 2·S / (S + eflow)
  Drain impact: 2·S / (S + exit_flow)
  1% round-trip fee
  Grade on the post-grad TAPE + on-chain graduation, NEVER dashboard PnL.

CONSTANTS (all config-driven named constants — never scattered literals)
=========================================================================
  V7_GATE_THRESHOLD      = 15.86012   gate: score >= this → trade
  TR30_TRAILING_STOP     = 0.30       30% trailing stop off running max
  TR30_HARD_TIMER_S      = 600.0      600-second hard timer
  ENTRY_WINDOW_S         = 30.0       entry must arrive within 30s of grad
  ENTRY_DELAY_S          = 2.0        fill = first swap >= grad + 2s
  ENTRY_SLIP_MAX         = 0.15       reject if fill/quote - 1 > 15%
  DUST_FILL_RATIO        = 0.3        reject if fill < 0.3 × local median price
  DEPTH_THRESHOLD_USD    = 8_000.0    depth gate: >= $8k → $100 else $25
  SIZE_DEEP_USD          = 100.0      size for deep-book fills
  SIZE_SHALLOW_USD       = 25.0       size for shallow fills (default / soak flat)
  SIZE_CAP_USD           = 100.0      hard cap — NEVER exceed $100

ZERO CREDITS
============
No Birdeye/Helius/Dune calls.  This module is pure Python (no I/O).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (all config-driven named constants — never scattered literals)
# ---------------------------------------------------------------------------

#: Gate threshold: trade if v7 score >= V7_GATE_THRESHOLD (~top-25%/day ≈ 52/day).
V7_GATE_THRESHOLD: float = 15.86012

#: 30% trailing stop off the running post-grad max.
TR30_TRAILING_STOP: float = 0.30

#: Hard 600-second timer for the tr30_t600 exit.
TR30_HARD_TIMER_S: float = 600.0

#: Entry window: fill must arrive within 30s of graduation.
ENTRY_WINDOW_S: float = 30.0

#: Entry delay: first valid fill is the first swap >= grad_ts + 2s.
ENTRY_DELAY_S: float = 2.0

#: Slip-miss threshold: reject fill if fill/quote - 1 > 15%.
ENTRY_SLIP_MAX: float = 0.15

#: Dust-fill threshold: reject fill if fill < 0.3 × local median price.
DUST_FILL_RATIO: float = 0.3

#: Depth threshold for sizing: entry_depth >= $8,000 → $100 else $25.
DEPTH_THRESHOLD_USD: float = 8_000.0

#: Trade size for deep-book fills.
SIZE_DEEP_USD: float = 100.0

#: Trade size for shallow fills (also the observe-only default this sprint).
SIZE_SHALLOW_USD: float = 25.0

#: Hard cap: NEVER exceed $100/trade ($200 is the impact-fragile zone).
SIZE_CAP_USD: float = 100.0

#: Round-trip fee (1%).
ROUND_TRIP_FEE: float = 0.01

#: Impact model: entry impact = 2·S / (S + eflow).
def entry_impact(size_usd: float, eflow_usd: float) -> float:
    """Entry impact: 2·S / (S + eflow).  Returns 0 if eflow <= 0."""
    if eflow_usd <= 0:
        return 0.0
    return 2.0 * size_usd / (size_usd + eflow_usd)


#: Impact model: drain impact = 2·S / (S + exit_flow).
def drain_impact(size_usd: float, exit_flow_usd: float) -> float:
    """Drain impact: 2·S / (S + exit_flow).  Returns 0 if exit_flow <= 0."""
    if exit_flow_usd <= 0:
        return 0.0
    return 2.0 * size_usd / (size_usd + exit_flow_usd)


# ---------------------------------------------------------------------------
# Entry trigger
# ---------------------------------------------------------------------------


@dataclass
class EntryFill:
    """Result of find_entry_fill."""

    price: float        # fill price
    ts: float           # fill block_time
    swap_idx: int       # index in swaps list


def find_entry_fill(
    swaps: list[dict],
    grad_ts: float,
    *,
    entry_delay_s: float = ENTRY_DELAY_S,
    entry_window_s: float = ENTRY_WINDOW_S,
    slip_max: float = ENTRY_SLIP_MAX,
    dust_ratio: float = DUST_FILL_RATIO,
    quote_price: Optional[float] = None,
) -> Optional[EntryFill]:
    """Find the first valid post-grad entry fill.

    Entry trigger spec (MODEL_HANDOFF.md §1):
      - Fill = first post-grad AMM swap >= grad_ts + entry_delay_s (default 2s).
      - Window: fill must arrive within entry_window_s (default 30s) of grad_ts.
      - Slip-miss: reject if fill/quote - 1 > slip_max (default 15%).
        quote_price is the at-graduation AMM quote; if absent, skip the slip check.
      - Dust-fill: reject if fill < dust_ratio × local median price of the
        first-window swaps.  Protects against a fill on a collapsed/zero price.

    Parameters
    ----------
    swaps:
        Post-grad swap records.  Each must have at minimum:
          - block_time: float (absolute Unix seconds)
          - price: float (token price, same units used throughout)
    grad_ts:
        Graduation Unix timestamp (seconds).
    entry_delay_s:
        Minimum delay after graduation (default 2s).
    entry_window_s:
        Maximum window for the fill from graduation (default 30s).
    slip_max:
        Slip-miss threshold.  If quote_price is given and
        fill_price / quote_price - 1 > slip_max, the fill is rejected.
    dust_ratio:
        Dust-fill protection.  The fill is rejected if it is less than
        dust_ratio × median(prices in window).
    quote_price:
        At-graduation AMM quote price.  Optional; slip check is skipped if None.

    Returns
    -------
    EntryFill if a valid fill is found, None otherwise.
    """
    earliest = grad_ts + entry_delay_s
    deadline = grad_ts + entry_window_s

    # Collect window swaps for median price computation (dust check)
    window_prices: list[float] = []
    for s in swaps:
        bt = float(s.get("block_time") or s.get("block_unix_time") or 0)
        if bt <= 0:
            continue
        if bt >= grad_ts and bt <= deadline:
            p = float(s.get("price") or 0)
            if p > 0:
                window_prices.append(p)

    median_price: Optional[float] = None
    if window_prices:
        sorted_prices = sorted(window_prices)
        mid = len(sorted_prices) // 2
        if len(sorted_prices) % 2 == 1:
            median_price = sorted_prices[mid]
        else:
            median_price = (sorted_prices[mid - 1] + sorted_prices[mid]) / 2.0

    for idx, s in enumerate(swaps):
        bt = float(s.get("block_time") or s.get("block_unix_time") or 0)
        if bt <= 0:
            continue
        if bt < earliest:
            continue
        if bt > deadline:
            # Past window — no more candidates
            break

        price = float(s.get("price") or 0)
        if price <= 0:
            continue

        # Slip-miss check
        if quote_price is not None and quote_price > 0:
            slip = abs(price / quote_price - 1.0)
            if slip > slip_max:
                logger.debug(
                    "[v7_ride_exit] entry slip-miss: fill=%.8f quote=%.8f slip=%.4f > %.4f",
                    price, quote_price, slip, slip_max,
                )
                continue

        # Dust-fill check
        if median_price is not None and median_price > 0:
            if price < dust_ratio * median_price:
                logger.debug(
                    "[v7_ride_exit] entry dust-fill: fill=%.8f median=%.8f ratio=%.4f < %.4f",
                    price, median_price, price / median_price, dust_ratio,
                )
                continue

        return EntryFill(price=price, ts=bt, swap_idx=idx)

    return None


# ---------------------------------------------------------------------------
# Exit: tr30_t600
# ---------------------------------------------------------------------------


@dataclass
class ExitResult:
    """Result of apply_tr30_t600_exit."""

    exit_price: float     # exit fill price
    exit_ts: float        # exit block_time
    exit_reason: str      # "trailing_stop" | "timer_600s"
    running_max: float    # running max price at exit
    swaps_walked: int     # number of post-grad swaps examined


def apply_tr30_t600_exit(
    swaps: list[dict],
    entry_price: float,
    entry_ts: float,
    *,
    trailing_stop: float = TR30_TRAILING_STOP,
    hard_timer_s: float = TR30_HARD_TIMER_S,
    fill_delay_s: float = ENTRY_DELAY_S,
) -> Optional[ExitResult]:
    """Apply the tr30_t600 ride exit policy on the post-grad AMM price.

    EXIT SPEC (MODEL_HANDOFF.md §5):
      - Track the running max from fill (entry_price is the initial max).
      - SELL when price <= running_max × (1 − trailing_stop) (30% trailing stop)
        OR at hard_timer_s (600s), whichever first.
      - Honest fill = first swap >= trigger_ts + fill_delay_s (default 2s).
      - Fast scalps are REFUTED — the post-grad open is a dump (win~30%); ride.

    The function walks swaps in block_time order, updating the running max and
    checking the trailing stop at each step.

    Parameters
    ----------
    swaps:
        Post-grad swap records in chronological order.  Each must have:
          - block_time: float (absolute Unix seconds)
          - price: float
    entry_price:
        Fill price at entry.
    entry_ts:
        Entry fill block_time (absolute Unix seconds).
    trailing_stop:
        Fraction off the running max that triggers the stop (default 0.30 = 30%).
    hard_timer_s:
        Hard timer in seconds from entry_ts (default 600s).
    fill_delay_s:
        Minimum delay for the honest exit fill (first swap >= trigger + delay_s).

    Returns
    -------
    ExitResult if the exit triggers, None if no post-grad swaps or the exit
    cannot be determined (e.g. all swaps are before entry_ts).
    """
    if not swaps or entry_price <= 0:
        return None

    deadline_ts = entry_ts + hard_timer_s

    # Collect all swaps at or after entry_ts (the post-grad window)
    post_swaps = []
    for s in swaps:
        bt = float(s.get("block_time") or s.get("block_unix_time") or 0)
        if bt <= 0:
            continue
        if bt >= entry_ts:
            p = float(s.get("price") or 0)
            if p > 0:
                post_swaps.append((bt, p))

    post_swaps.sort(key=lambda x: x[0])

    if not post_swaps:
        return None

    running_max = entry_price
    stop_level = running_max * (1.0 - trailing_stop)
    n_walked = 0

    # Trailing stop tracker
    triggered_ts: Optional[float] = None
    triggered_price: Optional[float] = None
    triggered_reason: Optional[str] = None

    for bt, price in post_swaps:
        n_walked += 1

        # Hard timer check: if this swap is past the deadline, the exit fires
        # at the timer boundary (first swap >= deadline + fill_delay_s).
        if bt >= deadline_ts:
            # Find the honest fill for the timer exit
            fill_ts_min = deadline_ts + fill_delay_s
            # Use this swap if it's after the delay, else the next valid one
            if bt >= fill_ts_min:
                triggered_ts = bt
                triggered_price = price
                triggered_reason = "timer_600s"
                break
            else:
                # Keep looking for the honest-fill swap after the timer
                continue

        # Update running max
        if price > running_max:
            running_max = price
            stop_level = running_max * (1.0 - trailing_stop)

        # Trailing stop check
        if price <= stop_level:
            # Trailing stop triggered — find the honest fill (first swap >= trigger + delay_s)
            trigger_ts = bt
            fill_ts_min = trigger_ts + fill_delay_s
            # Look for the honest fill in subsequent swaps
            found_fill = False
            for fill_bt, fill_price in post_swaps:
                if fill_bt >= fill_ts_min:
                    triggered_ts = fill_bt
                    triggered_price = fill_price
                    triggered_reason = "trailing_stop"
                    found_fill = True
                    break
            if not found_fill:
                # No fill found after delay — use the trigger price itself
                triggered_ts = trigger_ts
                triggered_price = price
                triggered_reason = "trailing_stop"
            break

    # If the loop completed without a trigger, use the last available price as
    # the 600s timer exit (tape ran out before 600s — use last swap price).
    if triggered_ts is None and post_swaps:
        last_bt, last_price = post_swaps[-1]
        triggered_ts = last_bt
        triggered_price = last_price
        triggered_reason = "timer_600s"

    if triggered_ts is None or triggered_price is None:
        return None

    return ExitResult(
        exit_price=triggered_price,
        exit_ts=triggered_ts,
        exit_reason=triggered_reason or "timer_600s",
        running_max=running_max,
        swaps_walked=n_walked,
    )


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def compute_size_usd(
    entry_depth_usd: float,
    *,
    depth_threshold: float = DEPTH_THRESHOLD_USD,
    size_deep: float = SIZE_DEEP_USD,
    size_shallow: float = SIZE_SHALLOW_USD,
    size_cap: float = SIZE_CAP_USD,
    observe_flat: bool = True,
) -> float:
    """Compute the trade size in USD.

    SIZING SPEC (MODEL_HANDOFF.md §6):
      size = $100 if entry_depth >= $8,000 else $25.
      Hard cap: $100 (NEVER $200).

    THIS SPRINT: observe_flat=True → always returns size_shallow ($25 FLAT).
    The depth-gated ramp toward $100 is soak-gated (US-91); the live
    trading_enabled=FALSE fence means the size-up path is never live this sprint.

    Parameters
    ----------
    entry_depth_usd:
        Entry-instant depth in USD.
        Offline: eflow ($-flow in [grad, grad+60]).
        Live: graduation-instant AMM pool liquidity.
    depth_threshold:
        Threshold in USD (default $8,000).
    size_deep:
        Size for deep-book fills (default $100).
    size_shallow:
        Size for shallow fills (default $25, also the observe-only flat default).
    size_cap:
        Hard cap in USD (default $100 — NEVER exceed).
    observe_flat:
        If True (default this sprint), always return size_shallow ($25 flat)
        regardless of depth.  Soak-gated: flip only after US-91 soak holds.

    Returns
    -------
    float
        Trade size in USD, capped at size_cap.
    """
    if observe_flat:
        # THIS SPRINT: flat $25; depth-gated ramp is soak-gated
        return min(size_shallow, size_cap)

    size = size_deep if entry_depth_usd >= depth_threshold else size_shallow
    return min(size, size_cap)


# ---------------------------------------------------------------------------
# V7 gate check
# ---------------------------------------------------------------------------


def v7_gate_passes(score: float, *, threshold: float = V7_GATE_THRESHOLD) -> bool:
    """Return True iff v7 score >= threshold (trade this graduation).

    Parameters
    ----------
    score:
        8-seed mean score from score_vectors / score_single.
    threshold:
        Gate threshold (default V7_GATE_THRESHOLD = 15.86012).

    Returns
    -------
    bool
        True → trade (gate passes).  False → skip (gate rejects).
    """
    return float(score) >= float(threshold)


# ---------------------------------------------------------------------------
# Paper PnL grading (honest-fill conventions from settle_engine.py)
# ---------------------------------------------------------------------------


def grade_paper_trade(
    *,
    size_usd: float,
    entry_price: float,
    exit_price: float,
    eflow_usd: float,
    exit_flow_usd: float,
    fee: float = ROUND_TRIP_FEE,
) -> dict:
    """Grade a paper trade using honest-fill impact conventions.

    Matches lab ``analysis/graduated/settle_engine.py``:
      entry_impact  = 2·S / (S + eflow)
      drain_impact  = 2·S / (S + exit_flow)
      1% round-trip fee

    Grade on the post-grad TAPE + on-chain graduation, NEVER dashboard PnL.

    Parameters
    ----------
    size_usd:
        Trade size in USD.
    entry_price, exit_price:
        Fill prices (same units — SOL/token or USD/token, consistent).
    eflow_usd:
        $-flow in [grad, grad+60] (eflow for entry impact).
    exit_flow_usd:
        $-flow at exit (for drain impact).
    fee:
        Round-trip fee fraction (default 0.01 = 1%).

    Returns
    -------
    dict with keys:
        entry_impact, drain_impact, price_return, gross_pnl_usd, net_pnl_usd
    """
    ei = entry_impact(size_usd, eflow_usd)
    di = drain_impact(size_usd, exit_flow_usd)

    if entry_price <= 0:
        return {
            "entry_impact": ei,
            "drain_impact": di,
            "price_return": 0.0,
            "gross_pnl_usd": -size_usd,
            "net_pnl_usd": -size_usd,
        }

    # Price return (raw, before impact)
    price_ret = exit_price / entry_price - 1.0

    # Effective entry price (worse than fill by entry impact)
    eff_entry = entry_price * (1.0 + ei)

    # Effective exit price (worse than fill by drain impact)
    eff_exit = exit_price * (1.0 - di)

    # Gross return after impact
    if eff_entry <= 0:
        gross_ret = price_ret
    else:
        gross_ret = eff_exit / eff_entry - 1.0

    gross_pnl = size_usd * gross_ret
    net_pnl = gross_pnl - size_usd * fee  # 1% fee on size

    return {
        "entry_impact": ei,
        "drain_impact": di,
        "price_return": price_ret,
        "gross_pnl_usd": gross_pnl,
        "net_pnl_usd": net_pnl,
    }
