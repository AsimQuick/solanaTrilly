# ---
# module: copytrade.position_opener
# sprint: sprint-12, sprint-13
# story: US-61 AC-61.1, US-68 AC-68.1
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models, copytrade.schemas, copytrade.trigger_pipeline,
#   trading.models
# ---
"""AC-61.1 / AC-68.1: Observe-mode position opener — shared execution chassis.

On a valid US-60 trigger in OBSERVE (paper) mode, opens a copy-trade position
by booking a fill at the injected entry price — NO real order is placed.

AC-68.1 refactor: open_observe_position NOW ALSO writes a shared
trading.Position row (source='copytrade', mode='observe', status='PAPER') so
that BOTH pipelines flow through the SHARED US-64 Position model (SPEC §0.1
'same execution path' parity).  The CopytradePosition row continues to be
written exactly as before — all existing AC-61.1 tests pass unchanged.

§5 ISOLATION PRESERVED: copytrade keeps its own engine/config/ON-OFF; only the
execution+settlement CHASSIS is shared.  The trading.Position row is written
read-only from copytrade's perspective — copytrade does NOT touch
trading_enabled or PipelineState (AST-guarded in test_observe_safety_gate_ac613).

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
from trading.models import Position as SharedPosition


def open_observe_position(
    record: OpenedPositionRecord,
    entry_price: float,
    config: CopyTradeConfig,
) -> CopytradePosition:
    """Open a paper position in OBSERVE mode (SPEC §10 — NO real order).

    Books a fill at *entry_price* without placing any real Solana transaction.

    Writes exactly one CopytradePosition row (as before, AC-61.1) AND one
    shared trading.Position row (AC-68.1).  The two rows are linked via
    CopytradePosition.shared_position_id.

    CopytradePosition fields:
        cohort_id      = record.cohort_id
        mint           = record.mint
        trigger_wallet = record.trigger_wallet
        status         = 'open'
        mode           = 'observe'
        entry_ts       = record.entry_ts
        entry_price    = entry_price  (the booked fill price)
        sol_in         = config.sol_size_per_trade
        shared_position_id = <pk of the shared trading.Position row>

    Shared trading.Position fields (AC-68.1):
        mint       = record.mint
        source     = 'copytrade'
        mode       = 'observe'
        status     = 'PAPER'
        entry_ts   = record.entry_ts
        entry_price = entry_price
        size_sol   = config.sol_size_per_trade

    Parameters
    ----------
    record:
        An OpenedPositionRecord produced by run_trigger_pipeline (US-60 AC-60.3).
    entry_price:
        The current curve price at which the paper fill is booked.
    config:
        Active CopyTradeConfig.  mode MUST be 'observe'; ValueError is raised
        otherwise (live execution is P8-gated).

    Returns
    -------
    The saved CopytradePosition instance (status='open', mode='observe').

    Raises
    ------
    ValueError
        If config.mode != 'observe'.
    """
    if config.mode != CopytradePosition.MODE_OBSERVE:
        raise ValueError(
            f"open_observe_position called with mode={config.mode!r}; "
            "LIVE execution is P8-gated and not implemented this sprint. "
            "The engine must remain in OBSERVE mode. "
            "See copytrade.execution and PRD §10."
        )

    # --- Write shared trading.Position row (AC-68.1 shared chassis) ---
    shared_pos = SharedPosition(
        mint=record.mint,
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_OBSERVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        size_sol=config.sol_size_per_trade,
    )
    shared_pos.save()

    # --- Write copytrade-specific position row (AC-61.1, unchanged) ---
    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        sol_in=config.sol_size_per_trade,
        shared_position_id=shared_pos.pk,
    )
    position.save()
    return position
