# ---
# module: copytrade.honest_fill
# sprint: US-75
# story: US-75 AC-1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: math, logging, copytrade.wallet_consumer
# ---
"""Honest copy-fill primitive for the copy-trade OBSERVE path (US-75 AC-1).

Ports the ``_honest_entry_fill`` discipline from solanaBilly
``app/tasks/paper_monitor_tasks.py:259-304`` into the copy-trade observe path.

The check is the ENTRY primitive only — quote→fill→cap — NOT the full
simulate_tape_exit settler from trading/tape_settler.py (wrong primitive for
this use case; the full settler is for model-trade post-hoc settlement).

Design contract
---------------
- quote_price  : the watched wallet's confirmed fill price (from its TradeEvent).
- fill_price   : the AMM/curve state at wallet_buy_ts + copy_latency (our entry
                 price if we tried to copy immediately after detecting the event).
- cap          : default 0.15 (15%).  If fill > quote·(1+cap) the live buy
                 would hit Anchor error 6002 TooMuchSolRequired — REJECT.
- On rejection : return EntryCheckResult(enterable=False, reason="SLIPPAGE_CAP_6002").
  The position row's exit_reason is set to ENTRY_REJECTED, PnL is left NULL
  (excluded from win-rate — the honest-fill discipline).
- On accept    : return EntryCheckResult(enterable=True, fill_price=fill_price,
                 copy_latency_s=<derived>).

Guards (§8 P1 / §3 trouble-PRs #405 #358)
------------------------------------------
- quote_price=0  → guard before dividing; returns enterable=False, reason="ZERO_QUOTE".
- fill_price=None/NaN → coerced to NaN at boundary; returns enterable=False,
  reason="NO_FILL_PRICE".
- Never crash on bad inputs (production telemetry paths may see JSONB None).

copy_latency_s derivation (§8 P1)
----------------------------------
Computed as ``clock_arrival_ts - block_time`` when block_time is available on
the WalletTxEvent (promoted from event.raw["block_time"] by wallet_consumer._normalize).
When block_time is None (non-pump-fun events, legacy sources), copy_latency_s=None
is stored — no ambiguous imputation of zero.

Feature flag
-------------
All callers must check ``CopyTradeSettings.honest_fills_enabled`` (default False)
before calling this module's check function.  When the flag is False the existing
observe path runs unchanged (no slippage cap applied).  This ensures the live
running v1/v2 soak is NOT disrupted at merge (§6.7 requirement).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# Default slippage cap (matches trading/tape_settler.py _CAP and solanaBilly)
# ---------------------------------------------------------------------------

DEFAULT_ENTRY_SLIP_CAP: float = 0.15  # 15% — the live Anchor 6002 boundary


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryCheckResult:
    """Result of an honest copy-fill entry check.

    Attributes
    ----------
    enterable:
        True if the copy-entry is within the slippage cap (proceed with open).
        False if the entry is rejected — do NOT open a position (PnL stays NULL).

    fill_price:
        The fill price used for the check.  Present on both enterable and
        rejected results so the telemetry can persist it.

    quote_price:
        The watched wallet's confirmed fill price.

    cap_pct:
        The slippage cap fraction applied (e.g. 0.15 for 15%).

    copy_latency_s:
        Seconds from on-chain block_time to our clock arrival.  None when
        block_time was absent from the event (no ambiguous imputation).

    realized_slip_pct:
        (fill/quote - 1) as a fraction.  None when the check short-circuited
        due to zero quote or missing fill.

    reason:
        Human-readable rejection reason.  Empty string on accepted entries.
        "SLIPPAGE_CAP_6002" — fill exceeded cap.
        "ZERO_QUOTE"        — quote_price was zero or negative (guard #405).
        "NO_FILL_PRICE"     — fill_price was None or NaN (guard #358).
    """

    enterable: bool
    fill_price: Optional[float]
    quote_price: float
    cap_pct: float
    copy_latency_s: Optional[float]
    realized_slip_pct: Optional[float]
    reason: str


# ---------------------------------------------------------------------------
# Public entry-check function
# ---------------------------------------------------------------------------


def check_copy_entry(
    quote_price: float,
    fill_price: Optional[float],
    *,
    clock_arrival_ts: datetime,
    block_time: Optional[float] = None,
    cap_pct: float = DEFAULT_ENTRY_SLIP_CAP,
) -> EntryCheckResult:
    """Check whether a copy-entry is within the slippage cap.

    This is the ENTRY primitive (quote→fill→cap check), modeled on solanaBilly
    ``_honest_entry_fill`` (paper_monitor_tasks.py:259-304).  It is NOT the full
    tape settler.

    Parameters
    ----------
    quote_price:
        The watched wallet's confirmed fill price at ``wallet_buy_ts``.  This is
        the price we observed on-chain when the signal fired.
    fill_price:
        Our estimated fill price at ``wallet_buy_ts + copy_latency``.  In
        OBSERVE mode this is the curve/AMM state at detection time (the
        price_fn() result from engine_runtime.handle_event — the Birdeye spot
        at the moment our engine detected the event, which includes both the
        copy-latency drift AND any own-impact at our position size).
        May be None when the price feed has no data for the mint yet.
    clock_arrival_ts:
        The wall-clock time our engine received the event (from the injected
        Clock).  Used to compute copy_latency_s when block_time is available.
    block_time:
        On-chain confirmed block timestamp (Unix epoch float), or None.
        Promoted from WalletTxEvent.block_time (§8 P1).
    cap_pct:
        Slippage cap fraction (default 0.15 = 15%).

    Returns
    -------
    EntryCheckResult
        enterable=True  → proceed with position open.
        enterable=False → reject; set exit_reason=ENTRY_REJECTED, leave PnL NULL.

    Guards (§8 P1 / trouble-PRs #405 #358)
    ----------------------------------------
    - quote_price <= 0 → ZERO_QUOTE, enterable=False (no division attempted).
    - fill_price None or NaN → NO_FILL_PRICE, enterable=False.
    """
    # --- copy_latency_s derivation (§8 P1) ---
    copy_latency_s: Optional[float] = None
    if block_time is not None:
        try:
            arrival_epoch = clock_arrival_ts.timestamp()
            copy_latency_s = arrival_epoch - float(block_time)
        except (TypeError, ValueError, OSError):
            copy_latency_s = None

    # --- Guard: zero or negative quote (#405) ---
    if not (quote_price > 0):
        logger.warning(
            "[copytrade] honest_fill: ZERO_QUOTE — quote_price=%r, fill_price=%r",
            quote_price,
            fill_price,
        )
        return EntryCheckResult(
            enterable=False,
            fill_price=fill_price,
            quote_price=float(quote_price),
            cap_pct=cap_pct,
            copy_latency_s=copy_latency_s,
            realized_slip_pct=None,
            reason="ZERO_QUOTE",
        )

    # --- Guard: missing or NaN fill price (#358) ---
    # Coerce None → NaN at the boundary before any numeric op.
    fill_f: float = float("nan") if fill_price is None else float(fill_price)
    if math.isnan(fill_f):
        logger.warning(
            "[copytrade] honest_fill: NO_FILL_PRICE — quote_price=%.8g, fill_price=%r",
            quote_price,
            fill_price,
        )
        return EntryCheckResult(
            enterable=False,
            fill_price=None,
            quote_price=float(quote_price),
            cap_pct=cap_pct,
            copy_latency_s=copy_latency_s,
            realized_slip_pct=None,
            reason="NO_FILL_PRICE",
        )

    # --- Slip check (#405 guard: quote_price > 0 already asserted above) ---
    realized_slip = fill_f / float(quote_price) - 1.0

    if realized_slip > cap_pct:
        logger.info(
            "[copytrade] honest_fill: ENTRY_REJECTED slip=%.4f > cap=%.2f "
            "quote=%.8g fill=%.8g latency=%s",
            realized_slip,
            cap_pct,
            quote_price,
            fill_f,
            f"{copy_latency_s:.1f}s" if copy_latency_s is not None else "None",
        )
        return EntryCheckResult(
            enterable=False,
            fill_price=fill_f,
            quote_price=float(quote_price),
            cap_pct=cap_pct,
            copy_latency_s=copy_latency_s,
            realized_slip_pct=realized_slip,
            reason="SLIPPAGE_CAP_6002",
        )

    # --- Accepted entry ---
    logger.debug(
        "[copytrade] honest_fill: ACCEPTED slip=%.4f cap=%.2f quote=%.8g fill=%.8g",
        realized_slip,
        cap_pct,
        quote_price,
        fill_f,
    )
    return EntryCheckResult(
        enterable=True,
        fill_price=fill_f,
        quote_price=float(quote_price),
        cap_pct=cap_pct,
        copy_latency_s=copy_latency_s,
        realized_slip_pct=realized_slip,
        reason="",
    )


__all__ = [
    "DEFAULT_ENTRY_SLIP_CAP",
    "EntryCheckResult",
    "check_copy_entry",
]
