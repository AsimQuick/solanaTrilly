# ---
# module: copytrade.helius_wallet_source
# sprint: cutover (copy-trade live)
# story: live-wallet-source
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: core.datasource, core.tape.helius_birth_tape_source (pure decode)
# ---
"""HeliusWalletTxSource — copy-trade's OWN Helius wallet-address subscription (§5).

The prediction firehose subscribes program-wide (accountInclude=[pump.fun
program]).  Copy-trade is a SIBLING with its OWN, SEPARATE Helius connection that
subscribes to the COHORT WALLETS (accountInclude=[wallet addresses]) — so Helius
only streams transactions those wallets are party to, and a problem in one
subscription cannot stall the other (SPEC §5).

Decode reuse, NOT state reuse
=============================
The pump.fun Anchor ``TradeEvent`` layout is protocol fact, decoded by the pure,
stateless ``decode_helius_notification`` (already bit-exact-verified against real
trades, fix #316 endpoint).  Reusing that ONE decoder here prevents drift between
the two heads and shares NO mutable firehose state — the actual §5 concern.  The
WebSocket CONNECTION is copy-trade-owned (this class); the run_copytrade_engine
command imports only ``copytrade.*`` so the AC-59.1 isolation guard stays green.

Output contract
===============
Wrapped in ``MappedSwapSource(HeliusWalletTxSource(...), decode_wallet_tx)`` it
yields the wallet-tx dicts ``WalletSubscriptionConsumer._normalize`` expects:
``{wallet, mint, signature, type, sol_amount, token_amount, price, program}``
where ``wallet`` is the TRADER (TradeEvent.user) and ``sol_amount`` is in SOL.

Note (documented scope): the TradeEvent decode captures pump.fun BONDING-CURVE
swaps.  A watched wallet's POST-graduation buy (on PumpSwap) does not emit a
pump.fun TradeEvent and is not yet detected here — a documented follow-up.  These
cohort wallets are selected as EARLY (first-10) buyers, so their qualifying
first-buys are overwhelmingly on-curve, which this source captures.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from core.datasource import DataSource
from core.tape.helius_birth_tape_source import (
    HELIUS_WS_URL,
    PUMP_FUN_PROGRAM,
    decode_helius_notification,
)


def decode_wallet_tx(frame: dict) -> dict | None:
    """Map a raw Helius transactionNotification to a copy-trade wallet-tx event.

    Reuses ``decode_helius_notification`` (pump.fun TradeEvent decode) to extract
    the trader (owner), mint, side, on-chain SOL size, and curve price, then
    reshapes to the ``{wallet, mint, signature, type, sol_amount, token_amount,
    price, program}`` contract.  Returns None for non-trade / undecodable frames
    or events with no trader.
    """
    swap = decode_helius_notification(frame)
    if swap is None:
        return None
    owner = swap.get("owner")
    if not owner:
        return None
    return {
        "wallet": owner,                                  # the TRADER (TradeEvent.user)
        "mint": swap.get("mint", ""),
        "signature": swap.get("signature", ""),
        "type": swap.get("side", "unknown"),              # "buy" | "sell"
        "sol_amount": float(swap.get("vol_sol", 0.0)),    # SOL (lamports already / 1e9)
        "token_amount": 0.0,
        "price": float(swap.get("price", 0.0) or 0.0),    # curve price vsol/vtok
        "program": PUMP_FUN_PROGRAM,                       # marks pump.fun for the trigger
        "block_time": swap.get("block_time"),
    }


class HeliusWalletTxSource(DataSource):
    """Live Helius ``transactionSubscribe`` scoped to the cohort wallet addresses.

    A SEPARATE connection from the firehose (SPEC §5).  Yields raw
    transactionNotification frames; pair with
    ``MappedSwapSource(..., decode_wallet_tx)`` to get wallet-tx event dicts.

    Args:
        api_key:           Helius API key (query param in the WS URL).
        wallet_addresses:  The cohort wallet addresses to subscribe to.
        endpoint:          Helius mainnet WS base (default HELIUS_WS_URL — the
                           working endpoint from fix #316, NOT atlas-mainnet).
    """

    def __init__(
        self,
        api_key: str,
        wallet_addresses: list[str],
        endpoint: str = HELIUS_WS_URL,
    ) -> None:
        self._api_key = api_key
        self._wallets = list(wallet_addresses)
        self._endpoint = endpoint
        self._ws: Any = None
        self._sub_id: int | None = None

    async def connect(self) -> None:
        """Open the Helius WS and subscribe to the cohort wallets' transactions."""
        import websockets  # lazy import — keeps import-time network-free

        url = f"{self._endpoint}/?api-key={self._api_key}"
        self._ws = await websockets.connect(
            url,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=10,
        )
        subscribe_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        # Subscribe to the WALLETS (not the program) — SPEC §5.
                        "accountInclude": self._wallets,
                    },
                    {
                        "commitment": "confirmed",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }
        )
        await self._ws.send(subscribe_msg)
        try:
            ack = json.loads(await self._ws.recv())
            self._sub_id = ack.get("result")
        except Exception:
            pass  # non-fatal: events() still yields

    async def disconnect(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield raw transactionNotification frames from the wallet subscription."""
        if self._ws is None:
            return
        async for raw_message in self._ws:
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("method") == "transactionNotification":
                yield parsed


__all__ = ["HeliusWalletTxSource", "decode_wallet_tx"]
