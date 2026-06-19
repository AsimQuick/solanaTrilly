# ---
# module: copytrade.buy_trigger
# sprint: sprint-12
# story: US-60 AC-60.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.wallet_consumer
# ---
"""BUY-COPY trigger predicate (SPEC §3, AC-60.1).

The correctness-critical predicate that decides whether to copy a watched
wallet's buy.  ALL three conditions must hold (this module covers conditions
1–3; multiplicity controls — copy_first_buy_only, dedupe, max_concurrent —
are AC-60.2):

  1. The tx is a SWAP where the watched wallet is the BUYER (tx_type == "buy").
     Sells, transfers, and passive-party events are rejected immediately.

  2. The token is a pump.fun token — identified by the mint suffix "pump" OR
     by the bonding-curve program ID in the raw event.

  3. The token is STILL ON THE BONDING CURVE (PRE-graduation) — NOT already
     migrated to PumpSwap or Raydium.  A buy on a graduated token is the wrong
     signal and must be skipped (SPEC §3: "our edge is curve entry").

This module is a pure-function predicate with no I/O, no ORM access, and no
concrete source imports.  It depends only on WalletTxEvent (a frozen dataclass)
and is therefore testable entirely offline.
"""
from __future__ import annotations

from copytrade.wallet_consumer import WalletTxEvent

# ---------------------------------------------------------------------------
# Program / mint constants
# ---------------------------------------------------------------------------

#: pump.fun bonding-curve program on Solana mainnet.
PUMPFUN_BONDING_CURVE_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: PumpSwap AMM — sole graduation destination since 2025-03 (CLAUDE.md).
PUMPSWAP_AMM: str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

#: Raydium AMM programs — guard against legacy / pre-PumpSwap migrations.
RAYDIUM_AMM_PROGRAMS: frozenset[str] = frozenset({
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uonc",   # Raydium AMM v3
})

#: Mint address suffix used by pump.fun tokens on Solana mainnet.
PUMPFUN_MINT_SUFFIX: str = "pump"

# ---------------------------------------------------------------------------
# Individual condition checks
# ---------------------------------------------------------------------------


def is_buyer(event: WalletTxEvent) -> bool:
    """Condition 1: tx is a SWAP where the watched wallet is the BUYER.

    tx_type must be "buy" — sells, transfers, and unknown events are rejected.
    """
    return event.tx_type == "buy"


def is_pumpfun_token(event: WalletTxEvent) -> bool:
    """Condition 2: the token is a pump.fun token.

    Identified by either:
    - The mint address ends with the pump.fun suffix ("pump"), OR
    - The swap program in the raw event is the pump.fun bonding-curve program.
    """
    if event.mint.lower().endswith(PUMPFUN_MINT_SUFFIX):
        return True
    program = event.raw.get("program", "")
    return program == PUMPFUN_BONDING_CURVE_PROGRAM


def is_on_bonding_curve(event: WalletTxEvent) -> bool:
    """Condition 3: the token is STILL ON THE BONDING CURVE (PRE-graduation).

    Returns False if the swap program is PumpSwap, a Raydium AMM, or the raw
    event carries an explicit graduated=True flag.

    Returns True otherwise (conservative default: absent or unrecognised program
    = not yet migrated).  Callers that require stricter behaviour should ensure
    the raw event carries an explicit program field.
    """
    program = event.raw.get("program", "")

    # Explicit post-graduation signals — reject immediately.
    if program == PUMPSWAP_AMM:
        return False
    if program in RAYDIUM_AMM_PROGRAMS:
        return False
    if event.raw.get("graduated", False):
        return False

    return True


# ---------------------------------------------------------------------------
# Composite predicate (AC-60.1)
# ---------------------------------------------------------------------------


def should_copy_buy(event: WalletTxEvent) -> bool:
    """Return True iff the event satisfies ALL AC-60.1 conditions (SPEC §3 #1–#3).

    Conditions checked:
      1. Watched wallet is the BUYER (tx_type == "buy").
      2. Token is a pump.fun token (mint suffix OR bonding-curve program).
      3. Token is still on the bonding curve (NOT migrated to PumpSwap/Raydium).

    Multiplicity controls (copy_first_buy_only, dedupe_token_across_wallets,
    max_concurrent_positions — SPEC §3 #4–#6) are evaluated by the caller and
    are out of scope for this predicate (AC-60.2).

    NOTE: this is the legacy 1.0 predicate (pre-graduation-only).  The live engine
    uses ``should_copy_buy_v2`` (cohort-2.0) below.
    """
    return is_buyer(event) and is_pumpfun_token(event) and is_on_bonding_curve(event)


# ---------------------------------------------------------------------------
# Cohort-2.0 trigger (the live consumption contract)
# ---------------------------------------------------------------------------


def trigger_usd(event: WalletTxEvent, sol_usd: float) -> float:
    """USD value of the watched wallet's buy = on-chain SOL amount × SOL/USD spot.

    The cohort's ``min_trigger_buy_usd`` conviction gate is in USD; the on-chain
    event carries SOL, so we value it with the shared cached spot (passed in).
    """
    return float(event.sol_amount) * float(sol_usd)


def should_copy_buy_v2(
    event: WalletTxEvent,
    *,
    min_trigger_buy_usd: float,
    sol_usd: float,
) -> bool:
    """Cohort-2.0 BUY-COPY predicate: watched wallet's first ≥ $min pump.fun buy.

    ALL must hold:
      1. The watched wallet is the BUYER (tx_type == "buy").
      2. The token is a pump.fun token (mint suffix OR bonding-curve program).
      3. The buy is >= ``min_trigger_buy_usd`` (the validated conviction filter),
         valued as ``sol_amount × sol_usd``.

    UNLIKE the 1.0 predicate, this does NOT require pre-graduation: the cohort
    copies the first >= $250 buy "whenever it happens" (still pump.fun-scoped).
    Multiplicity controls (first-buy / dedupe / cap) remain the caller's job.
    """
    if not is_buyer(event):
        return False
    if not is_pumpfun_token(event):
        return False
    return trigger_usd(event, sol_usd) >= float(min_trigger_buy_usd)
