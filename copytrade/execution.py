# ---
# module: copytrade.execution
# sprint: sprint-12
# story: US-61 AC-61.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: none (P8-gated stub — no real Solana SDK imports)
# ---
"""Execution apparatus stub — the P8-gated real-order path (SPEC §10).

This module defines the interface boundary between the copy-trade engine and
the real Solana order-execution path (PumpSwap buy/sell, PRD §10).  The
actual implementation is deferred to sprint P8.

In OBSERVE (paper) mode the engine MUST NEVER call place_buy_order — it
books paper fills without touching Solana.  Tests assert this invariant by
patching this function and confirming it is never invoked (AC-61.1 execution
guard).

LIVE (real-order) execution is OUT OF SCOPE until the P8 PumpSwap execution
path is built (chainstacklabs manual_buy_pumpswap.py port reference;
pump_amm.json IDL for account order) and the operator explicitly flips the
mode flag after the observe soak (SPEC §10).
"""
from __future__ import annotations


def place_buy_order(mint: str, sol_amount: float) -> None:
    """Place a real buy order on PumpSwap (P8-gated — NOT YET IMPLEMENTED).

    This is the LIVE execution path placeholder.  In OBSERVE mode this
    function MUST NEVER be called.  Calling it always raises
    NotImplementedError until the P8 PumpSwap execution path is implemented.

    Parameters
    ----------
    mint:       Token mint address to buy.
    sol_amount: SOL amount to spend on the buy.
    """
    raise NotImplementedError(
        "place_buy_order: P8 PumpSwap execution path not yet implemented. "
        "The copy-trade engine must remain in OBSERVE mode until P8 ships. "
        "See PRD §10 and the sprint-12 forward_plan for the build plan."
    )
