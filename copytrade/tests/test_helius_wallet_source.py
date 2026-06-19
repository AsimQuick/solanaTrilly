# ---
# module: copytrade.tests.test_helius_wallet_source
# sprint: cutover (copy-trade live)
# story: live-wallet-source
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: pytest
# ---
"""Offline tests for the copy-trade Helius wallet source + cohort-2.0 trigger.

Synthesises real pump.fun TradeEvent frames (same packing the core birth-tape
tests use) and asserts: the wallet-tx decode reshapes them correctly, the source
class is a clean DataSource, and the 2.0 USD trigger gates on buy/pump.fun/>=$min.
"""
import base64
import struct

import pytest

from copytrade.buy_trigger import should_copy_buy_v2, trigger_usd
from copytrade.helius_wallet_source import HeliusWalletTxSource, decode_wallet_tx
from copytrade.wallet_consumer import WalletTxEvent
from core.tape.helius_birth_tape_source import TRADE_EVENT_DISCRIMINATOR, _b58_from_bytes


def _pack_trade_event(*, mint_byte=7, user_byte=9, sol_amount, token_amount,
                      is_buy, timestamp=1_700_000_000, vsol=30_000_000_000, vtok=1_000_000_000_000):
    mint = bytes([mint_byte]) * 32
    user = bytes([user_byte]) * 32
    return (
        TRADE_EVENT_DISCRIMINATOR
        + mint
        + struct.pack("<QQ", sol_amount, token_amount)
        + bytes([1 if is_buy else 0])
        + user
        + struct.pack("<qQQ", timestamp, vsol, vtok)
    ), _b58_from_bytes(mint), _b58_from_bytes(user)


def _make_frame(packed: bytes, *, signature="sig123", slot=42):
    trade_b64 = base64.b64encode(packed).decode()
    return {
        "method": "transactionNotification",
        "params": {
            "result": {
                "signature": signature,
                "slot": slot,
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            "Program log: Instruction: Buy",
                            f"Program data: {trade_b64}",
                        ],
                    },
                },
            },
        },
    }


def test_decode_wallet_tx_buy_reshapes_to_contract():
    packed, mint_b58, user_b58 = _pack_trade_event(
        sol_amount=2_000_000_000, token_amount=1_000, is_buy=True,  # 2 SOL
    )
    out = decode_wallet_tx(_make_frame(packed, signature="abc"))
    assert out is not None
    assert out["wallet"] == user_b58          # the TRADER
    assert out["mint"] == mint_b58
    assert out["type"] == "buy"
    assert out["signature"] == "abc"
    assert out["sol_amount"] == pytest.approx(2.0)  # lamports -> SOL
    assert out["program"]                            # pump.fun marker set
    assert out["price"] > 0


def test_decode_wallet_tx_sell_side():
    packed, _, _ = _pack_trade_event(sol_amount=500_000_000, token_amount=1, is_buy=False)
    out = decode_wallet_tx(_make_frame(packed))
    assert out["type"] == "sell"


def test_decode_wallet_tx_skips_non_trade_frames():
    assert decode_wallet_tx({"method": "subscriptionAck", "result": 1}) is None
    assert decode_wallet_tx({"method": "transactionNotification", "params": {}}) is None


def test_source_is_a_datasource():
    from core.datasource import DataSource

    src = HeliusWalletTxSource("key", ["walletA", "walletB"])
    assert isinstance(src, DataSource)
    # No connection opened => events() yields nothing (never raises).
    assert src._wallets == ["walletA", "walletB"]


# --- cohort-2.0 USD trigger -------------------------------------------------


def _evt(*, tx_type="buy", mint="AbcPump", sol_amount=2.0, program="prog"):
    return WalletTxEvent(
        wallet="W", mint=mint, tx_signature="s", tx_type=tx_type,
        sol_amount=sol_amount, token_amount=0.0, timestamp=None,
        raw={"program": program},
    )


def test_trigger_usd_values_buy_in_usd():
    # 2 SOL * 150 USD/SOL = 300 USD
    assert trigger_usd(_evt(sol_amount=2.0), 150.0) == pytest.approx(300.0)


def test_v2_trigger_passes_big_pumpfun_buy():
    # 2 SOL * 150 = $300 >= $250 -> copy
    evt = _evt(mint="SomeTokenpump", sol_amount=2.0)
    assert should_copy_buy_v2(evt, min_trigger_buy_usd=250.0, sol_usd=150.0) is True


def test_v2_trigger_rejects_small_buy_below_usd_gate():
    # 1 SOL * 150 = $150 < $250 -> skip (conviction filter)
    evt = _evt(mint="SomeTokenpump", sol_amount=1.0)
    assert should_copy_buy_v2(evt, min_trigger_buy_usd=250.0, sol_usd=150.0) is False


def test_v2_trigger_rejects_sells():
    evt = _evt(tx_type="sell", mint="SomeTokenpump", sol_amount=5.0)
    assert should_copy_buy_v2(evt, min_trigger_buy_usd=250.0, sol_usd=150.0) is False


def test_v2_trigger_rejects_non_pumpfun_token():
    # mint not ending 'pump' and no pump.fun program in raw -> not pump.fun
    evt = _evt(mint="SomeRandomMint", sol_amount=10.0, program="other")
    assert should_copy_buy_v2(evt, min_trigger_buy_usd=250.0, sol_usd=150.0) is False


def test_v2_trigger_does_not_require_pre_graduation():
    # Even with a PumpSwap program tag (post-grad), a >= $250 pump.fun buy triggers
    # (cohort-2.0 copies the first >= $250 buy "whenever it happens").
    evt = _evt(mint="SomeTokenpump", sol_amount=2.0, program="pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
    assert should_copy_buy_v2(evt, min_trigger_buy_usd=250.0, sol_usd=150.0) is True
