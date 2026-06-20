# ---
# module: copytrade.curve_price
# sprint: live hotfix (copy-trade real-SOL readiness, US-75 curve honest-fill), feat/copy-live-exec-curve-ix
# story: copytrade-curve-honest-fill, copy-live-exec
# status: refactored
# created-by: operator
# last-updated: 2026-06-21
# dependencies: base64, struct, json, urllib, dataclasses, trading.pumpswap_ix
# ---
"""Bonding-curve price + honest-fill simulation for the copy engine (Option A).

The copy engine prices everything in ONE basis: the pump.fun bonding-curve
``vsol/vtok`` (Helius/on-chain truth), per the operator decision (see memory
``ct-oracle-vs-live-honest-fills``).  This module is the self-contained curve
read + fill-sim primitive:

  - ``read_curve_state(mint)`` reads the bonding-curve account over RPC
    (derive PDA -> getAccountInfo -> deserialize reserves) — billy's proven
    ``_read_curve_price`` path, self-contained in copytrade (NO firehose import,
    §5 clean).  The RESERVES (not a last-swap price) are what make an honest
    size-impact simulation possible, and they are always-current.
  - ``simulate_buy`` / ``simulate_sell`` compute OUR realized fill against those
    reserves via the constant-product curve math + the 1% pump.fun fee.  The
    curve's own size impact (our order moving the thin pool) is INHERENT in the
    formula — this is the honest fill the live bot would receive, NOT an
    idealized spot mark and NOT the watched wallet's price (which would be a
    phantom fill).

Basis note: every price here is the lamports ratio ``virtualSolReserves /
virtualTokenReserves`` (or ``sol_in_lamports / tokens_out`` for a fill), the
SAME basis as the decoded wallet TradeEvent's ``price`` field
(``decode_helius_notification`` returns ``price = vsol / vtok``).  So a quote
(the wallet's curve buy price) and our simulated fill are directly comparable —
``slip = fill_price / quote_price - 1`` is the real execution slippage.

Fail-safe: every read returns ``None`` on any failure (missing key, network
error, malformed payload, graduated/complete curve).  Callers MUST handle None.
Pure except for the RPC call, which takes an injectable ``fetcher`` for offline
tests (no network in tests — same discipline as ``copytrade.price_source``).
"""

from __future__ import annotations

import base64
import json
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from trading.pumpswap_ix import b58decode, b58encode, find_program_address

#: pump.fun bonding-curve program id (protocol fact; hardcoded to avoid importing
#: any firehose module — preserves the copytrade §5 isolation contract).
PUMP_FUN_BONDING_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: 1% pump.fun bonding-curve fee (taken on input for a buy, on output for a sell).
_PUMP_FEE_KEEP: float = 0.99

LAMPORTS_PER_SOL: int = 1_000_000_000

#: Helius mainnet JSON-RPC base (the STANDARD endpoint, fix #316 — NOT atlas).
HELIUS_RPC_BASE: str = "https://mainnet.helius-rpc.com"


@dataclass(frozen=True)
class CurveState:
    """Bonding-curve reserves at a point in time (lamports / token base units).

    Fields added for feat/copy-live-exec-curve-ix:
      creator         — base58 pubkey of the token creator (from bonding-curve
                        account at offset 49, 32 bytes).  None on pre-upgrade
                        accounts that don't carry this field.
      is_mayhem_mode  — pump.fun fee_recipient selector (offset 81).  True ->
                        MAYHEM fee account; False/None -> LEGACY.  Required for
                        building valid bonding-curve buy/sell instructions.
      is_cashback_coin — selects user_volume_accumulator inclusion in the sell ix
                        (offset 82).  True -> append uva to sell accounts; else omit.
                        None for pre-upgrade short accounts.
    """

    virtual_sol_reserves: int
    virtual_token_reserves: int
    complete: bool
    # Extended fields (present after the pump.fun creator-vault upgrade; None on
    # short/pre-upgrade accounts). Gracefully None -> callers must guard.
    creator: Optional[str] = None
    is_mayhem_mode: Optional[bool] = None
    is_cashback_coin: Optional[bool] = None

    def spot_price(self) -> Optional[float]:
        """Instantaneous curve price = vsol / vtok (lamports ratio), or None."""
        if self.virtual_token_reserves <= 0:
            return None
        return self.virtual_sol_reserves / self.virtual_token_reserves


@dataclass(frozen=True)
class CurveFill:
    """OUR simulated honest fill against the curve (size-impact + 1% fee inherent)."""

    tokens: int  # token base units received (buy) / sold (sell)
    sol_lamports: int  # SOL spent (buy) / received (sell), net of the 1% fee
    price: float  # effective fill price = sol_lamports / tokens (same basis as spot)


# ---------------------------------------------------------------------------
# PDA + account deserialization
# ---------------------------------------------------------------------------


def derive_bonding_curve_pda(mint_b58: str) -> str:
    """Derive the bonding-curve PDA: find_program_address([b'bonding-curve', mint])."""
    program_id = b58decode(PUMP_FUN_BONDING_PROGRAM)
    mint_bytes = b58decode(mint_b58)
    pda_bytes, _bump = find_program_address([b"bonding-curve", mint_bytes], program_id)
    return b58encode(pda_bytes)


def deserialize_bonding_curve(account_data_b64: str) -> CurveState:
    """Deserialize a pump.fun BondingCurve account (after the 8-byte discriminator).

    Layout (chainstacklabs / pump.fun IDL + creator-vault upgrade):
      offset  8: virtualTokenReserves u64   (8 bytes)
      offset 16: virtualSolReserves   u64   (8 bytes)
      offset 24: realTokenReserves    u64   (8 bytes)
      offset 32: realSolReserves      u64   (8 bytes)
      offset 40: tokenTotalSupply     u64   (8 bytes)
      offset 48: complete             bool  (1 byte)
      offset 49: creator              Pubkey (32 bytes) — post-upgrade accounts only
      offset 81: is_mayhem_mode       bool  (1 byte)    — post-upgrade accounts only
      offset 82: is_cashback_coin     bool  (1 byte)    — post-upgrade accounts only

    Pre-upgrade accounts (len < 8 + 41 + 32 = 81) will have creator=None,
    is_mayhem_mode=None, is_cashback_coin=None — callers guard gracefully.

    Raises ValueError if the data is too short to hold the core fields (< 49 bytes).
    """
    raw = base64.b64decode(account_data_b64)
    if len(raw) < 8 + 41:
        raise ValueError(f"bonding curve data too short: {len(raw)} bytes")
    virtual_token, virtual_sol, _real_token, _real_sol, _supply = struct.unpack_from("<QQQQQ", raw, 8)
    complete = bool(raw[8 + 40])

    # Extended fields — present only on post-upgrade accounts (offset 49+).
    # Guard gracefully for any short/pre-upgrade account.
    creator: Optional[str] = None
    is_mayhem_mode: Optional[bool] = None
    is_cashback_coin: Optional[bool] = None

    # creator: 32-byte Pubkey starting at offset 49 (8 discriminator + 41 core fields).
    if len(raw) >= 8 + 41 + 32:  # offset 81
        creator_bytes = raw[49:81]
        creator = b58encode(bytes(creator_bytes))

    # is_mayhem_mode: 1 byte at offset 81.
    if len(raw) >= 8 + 41 + 32 + 1:  # offset 82
        is_mayhem_mode = bool(raw[81])

    # is_cashback_coin: 1 byte at offset 82.
    if len(raw) >= 8 + 41 + 32 + 2:  # offset 83
        is_cashback_coin = bool(raw[82])

    return CurveState(
        virtual_sol_reserves=virtual_sol,
        virtual_token_reserves=virtual_token,
        complete=complete,
        creator=creator,
        is_mayhem_mode=is_mayhem_mode,
        is_cashback_coin=is_cashback_coin,
    )


# ---------------------------------------------------------------------------
# RPC read (injectable fetcher for offline tests)
# ---------------------------------------------------------------------------


def _default_rpc_fetcher(rpc_url: str, pubkey_b58: str, timeout_s: float) -> dict[str, Any] | None:
    """JSON-RPC getAccountInfo (base64) via urllib.  None on any error."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [pubkey_b58, {"encoding": "base64"}],
        }
    ).encode()
    req = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def read_curve_state(
    mint_b58: str,
    *,
    rpc_url: str,
    timeout_s: float = 8.0,
    fetcher: Optional[Callable[[str, str, float], Optional[dict[str, Any]]]] = None,
) -> Optional[CurveState]:
    """Read the live bonding-curve reserves for *mint_b58*, or None.

    Returns the CurveState (incl. ``complete``) on success.  Returns None on any
    failure: missing mint, RPC error, account-not-found (graduated curve account
    closes / migrates), or a malformed/too-short payload.  A ``complete`` curve
    is returned (not None) so callers can distinguish "graduated" from "miss".
    """
    if not mint_b58 or not mint_b58.strip():
        return None
    try:
        pda = derive_bonding_curve_pda(mint_b58)
    except Exception:  # noqa: BLE001 — never crash the caller on a bad mint
        return None

    _fetch = fetcher if fetcher is not None else _default_rpc_fetcher
    try:
        payload = _fetch(rpc_url, pda, timeout_s)
    except Exception:  # noqa: BLE001 — a price miss must never break the caller
        return None
    if payload is None:
        return None

    try:
        value = payload.get("result", {}).get("value")
        if value is None:
            return None  # account not found (e.g. graduated/closed)
        data = value.get("data")
        # base64 encoding -> ["<b64>", "base64"]
        data_b64 = data[0] if isinstance(data, list) else data
        if not data_b64:
            return None
        return deserialize_bonding_curve(data_b64)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Honest fill simulation (constant-product + 1% fee + inherent size impact)
# ---------------------------------------------------------------------------


def simulate_buy(state: CurveState, sol_lamports: int) -> Optional[CurveFill]:
    """Simulate OUR buy of *sol_lamports* against the curve (exact-in).

    tokens_out = (0.99·sol · vtok) // (vsol + 0.99·sol)   [1% fee on input]

    Returns None if the curve is complete (graduated — price the AMM instead) or
    the trade cannot be absorbed.
    """
    if state.complete:
        return None
    if sol_lamports <= 0 or state.virtual_token_reserves <= 0:
        return None
    fee_adjusted = int(sol_lamports * _PUMP_FEE_KEEP)
    vsr = state.virtual_sol_reserves
    vtr = state.virtual_token_reserves
    denom = vsr + fee_adjusted
    if denom <= 0:
        return None
    tokens_out = (fee_adjusted * vtr) // denom
    if tokens_out <= 0:
        return None
    # Effective fill price in the spot basis (lamports per token base unit). The
    # GROSS sol (incl. fee + impact) over tokens is the honest realized price.
    price = sol_lamports / tokens_out
    return CurveFill(tokens=tokens_out, sol_lamports=sol_lamports, price=price)


def simulate_sell(state: CurveState, tokens: int) -> Optional[CurveFill]:
    """Simulate OUR sell of *tokens* against the curve (exact-in on the token side).

    sol_out = 0.99 · (tokens · vsol) // (vtok + tokens)   [1% fee on output]

    Returns None if the curve is complete or the trade cannot be absorbed.
    """
    if state.complete:
        return None
    if tokens <= 0 or state.virtual_token_reserves < 0:
        return None
    vsr = state.virtual_sol_reserves
    vtr = state.virtual_token_reserves
    denom = vtr + tokens
    if denom <= 0:
        return None
    sol_gross = (tokens * vsr) // denom
    sol_out = int(sol_gross * _PUMP_FEE_KEEP)
    if sol_out <= 0:
        return None
    price = sol_out / tokens
    return CurveFill(tokens=tokens, sol_lamports=sol_out, price=price)


__all__ = [
    "PUMP_FUN_BONDING_PROGRAM",
    "LAMPORTS_PER_SOL",
    "HELIUS_RPC_BASE",
    "CurveState",
    "CurveFill",
    "derive_bonding_curve_pda",
    "deserialize_bonding_curve",
    "read_curve_state",
    "simulate_buy",
    "simulate_sell",
]
