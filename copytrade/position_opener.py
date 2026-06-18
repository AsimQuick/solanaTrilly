# ---
# module: copytrade.position_opener
# sprint: sprint-12
# story: US-61 AC-61.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models, copytrade.schemas, copytrade.trigger_pipeline
# ---
"""AC-61.1: Observe-mode position opener (SPEC §3 exit / §4 / §10).

On a valid US-60 trigger in OBSERVE (paper) mode, opens a copy-trade position
by booking a fill at the injected entry price — NO real order is placed.

The execution apparatus (copytrade.execution.place_buy_order) is NEVER called
on the observe path; observe mode is paper-only by construction (SPEC §10).

LIVE (real-order) execution is OUT OF SCOPE this sprint (P8-gated).  The
open_observe_position function raises ValueError if called with mode != 'observe'
so the P8 gate cannot be bypassed accidentally.
"""
from __future__ import annotations

from copytrade.models import CopytradePosition
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord


def open_observe_position(
    record: OpenedPositionRecord,
    entry_price: float,
    config: CopyTradeConfig,
) -> CopytradePosition:
    """Open a paper position in OBSERVE mode (SPEC §10 — NO real order).

    Books a fill at *entry_price* without placing any real Solana transaction.
    Writes exactly one CopytradePosition row with:

        cohort_id      = record.cohort_id
        mint           = record.mint
        trigger_wallet = record.trigger_wallet
        status         = 'open'
        mode           = 'observe'
        entry_ts       = record.entry_ts
        entry_price    = entry_price  (the booked fill price)
        sol_in         = config.sol_size_per_trade

    The P8 execution apparatus (place_buy_order) is never invoked — this
    function is the paper-only path.

    Parameters
    ----------
    record:
        An OpenedPositionRecord produced by run_trigger_pipeline (US-60 AC-60.3).
        Carries cohort_id, mint, trigger_wallet, and the Clock-stamped entry_ts.
    entry_price:
        The current curve price at which the paper fill is booked.  Injected
        by the caller from live or replay price data; no Solana transaction
        is executed.
    config:
        Active CopyTradeConfig.  sol_size_per_trade is read here.
        mode MUST be 'observe'; ValueError is raised otherwise (live
        execution is P8-gated and not implemented this sprint).

    Returns
    -------
    The saved CopytradePosition instance (status='open', mode='observe').

    Raises
    ------
    ValueError
        If config.mode != 'observe'.  Live execution is P8-gated; the caller
        must not pass mode='live' until P8 ships.
    """
    if config.mode != CopytradePosition.MODE_OBSERVE:
        raise ValueError(
            f"open_observe_position called with mode={config.mode!r}; "
            "LIVE execution is P8-gated and not implemented this sprint. "
            "The engine must remain in OBSERVE mode. "
            "See copytrade.execution and PRD §10."
        )

    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        sol_in=config.sol_size_per_trade,
    )
    position.save()
    return position
