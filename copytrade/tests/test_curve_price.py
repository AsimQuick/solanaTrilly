# ---
# module: copytrade.tests.test_curve_price
# sprint: live hotfix (copy-trade real-SOL readiness, US-75 curve honest-fill)
# story: copytrade-curve-honest-fill
# status: implemented
# created-by: operator
# last-updated: 2026-06-20
# dependencies: pytest, base64, struct, copytrade.curve_price
# ---
"""Tests for copytrade.curve_price (bonding-curve read + honest-fill sim).

Offline only — the RPC read uses an injected fetcher (no network).  Covers the
account deserialization layout, PDA determinism, the constant-product + 1%-fee
buy/sell math (incl. size-impact monotonicity and the round-trip spread), and
the fail-safe None paths.
"""
from __future__ import annotations

import base64
import struct

import pytest

from copytrade.curve_price import (
    CurveState,
    derive_bonding_curve_pda,
    deserialize_bonding_curve,
    read_curve_state,
    simulate_buy,
    simulate_sell,
)

# A real pump.fun mint (used only for deterministic PDA derivation, no network).
_MINT = "EzaC4D6kqh6T6N4dT9b1uZ4Pf7Q9rPbqkVwM6n8Pump"

# Representative early-curve reserves: ~30 SOL vs ~1e15 token base units.
_VSOL = 30_000_000_000          # 30 SOL in lamports
_VTOK = 1_000_000_000_000_000   # 1e15 token base units


def _account_b64(vtok: int, vsol: int, *, complete: bool = False) -> str:
    """Build a synthetic BondingCurve account: 8-byte disc + struct + complete + creator."""
    body = struct.pack(
        "<QQQQQ",
        vtok,            # virtualTokenReserves
        vsol,            # virtualSolReserves
        500_000_000_000, # realTokenReserves
        5_000_000_000,   # realSolReserves
        1_000_000_000_000_000,  # tokenTotalSupply
    )
    raw = (
        b"\x00" * 8                       # Anchor discriminator
        + body
        + bytes([1 if complete else 0])   # complete
        + b"\x11" * 32                    # creator pubkey
        + b"\x00\x00"                     # is_mayhem_mode, is_cashback_coin
    )
    return base64.b64encode(raw).decode()


# --- deserialize -----------------------------------------------------------


def test_deserialize_reads_reserves_and_complete():
    state = deserialize_bonding_curve(_account_b64(_VTOK, _VSOL))
    assert state.virtual_token_reserves == _VTOK
    assert state.virtual_sol_reserves == _VSOL
    assert state.complete is False


def test_deserialize_complete_flag():
    state = deserialize_bonding_curve(_account_b64(_VTOK, _VSOL, complete=True))
    assert state.complete is True


def test_deserialize_too_short_raises():
    with pytest.raises(ValueError):
        deserialize_bonding_curve(base64.b64encode(b"\x00" * 16).decode())


def test_spot_price_is_vsol_over_vtok():
    state = deserialize_bonding_curve(_account_b64(_VTOK, _VSOL))
    assert state.spot_price() == pytest.approx(_VSOL / _VTOK)


# --- PDA -------------------------------------------------------------------


def test_pda_is_deterministic_base58():
    a = derive_bonding_curve_pda(_MINT)
    b = derive_bonding_curve_pda(_MINT)
    assert a == b
    assert 32 <= len(a) <= 44  # base58 pubkey length


# --- buy / sell sim --------------------------------------------------------


def test_simulate_buy_basic():
    state = CurveState(_VSOL, _VTOK, complete=False)
    fill = simulate_buy(state, 100_000_000)  # 0.1 SOL
    assert fill is not None
    assert fill.tokens > 0
    assert fill.sol_lamports == 100_000_000
    assert fill.price == pytest.approx(100_000_000 / fill.tokens)


def test_buy_size_impact_monotonic():
    """A bigger buy gets a WORSE (higher) effective price — size impact."""
    state = CurveState(_VSOL, _VTOK, complete=False)
    small = simulate_buy(state, 1_000_000)     # 0.001 SOL
    big = simulate_buy(state, 5_000_000_000)   # 5 SOL
    assert small is not None and big is not None
    assert big.price > small.price


def test_sell_size_impact_monotonic():
    """A bigger sell gets a WORSE (lower) effective price — size impact."""
    state = CurveState(_VSOL, _VTOK, complete=False)
    small = simulate_sell(state, 1_000_000_000)
    big = simulate_sell(state, 500_000_000_000)
    assert small is not None and big is not None
    assert big.price < small.price


def test_round_trip_loses_to_fees_and_spread():
    """Instant buy then sell the received tokens returns less SOL (fee + spread)."""
    state = CurveState(_VSOL, _VTOK, complete=False)
    buy = simulate_buy(state, 100_000_000)
    assert buy is not None
    sell = simulate_sell(state, buy.tokens)
    assert sell is not None
    assert sell.sol_lamports < buy.sol_lamports  # 2x 1% fee + curve spread


def test_buy_on_complete_curve_returns_none():
    state = CurveState(_VSOL, _VTOK, complete=True)
    assert simulate_buy(state, 100_000_000) is None
    assert simulate_sell(state, 1_000_000_000) is None


# --- RPC read (injected fetcher) -------------------------------------------


def test_read_curve_state_success():
    captured = {}

    def fake_fetch(rpc_url, pubkey, timeout_s):
        captured["pubkey"] = pubkey
        return {"result": {"value": {"data": [_account_b64(_VTOK, _VSOL), "base64"]}}}

    state = read_curve_state(_MINT, rpc_url="http://rpc", fetcher=fake_fetch)
    assert state is not None
    assert state.virtual_sol_reserves == _VSOL
    # The fetched pubkey is the derived bonding-curve PDA, not the mint.
    assert captured["pubkey"] == derive_bonding_curve_pda(_MINT)


def test_read_curve_state_account_not_found_returns_none():
    def fake_fetch(rpc_url, pubkey, timeout_s):
        return {"result": {"value": None}}  # graduated / closed account

    assert read_curve_state(_MINT, rpc_url="http://rpc", fetcher=fake_fetch) is None


def test_read_curve_state_rpc_error_returns_none():
    def fake_fetch(rpc_url, pubkey, timeout_s):
        return None  # network/parse failure

    assert read_curve_state(_MINT, rpc_url="http://rpc", fetcher=fake_fetch) is None


def test_read_curve_state_empty_mint_returns_none():
    assert read_curve_state("", rpc_url="http://rpc", fetcher=lambda *a: {}) is None
