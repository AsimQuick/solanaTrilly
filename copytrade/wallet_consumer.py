# ---
# module: copytrade.wallet_consumer
# sprint: sprint-12, US-75
# story: US-59 AC-59.2, US-75 AC-1
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: core.datasource, core.clock
# ---
"""WalletSubscriptionConsumer — injected DataSource + Clock wallet-tx consumer.

Principle #7 seam: this module imports ONLY the abstract DataSource + Clock
interfaces — never a concrete Helius/Birdeye/Live source.  The caller (the
management command, or a test harness) instantiates a concrete source and
clock and injects them here.

Wallet addresses are passed in by the caller, which reads them from the DB via
sync_to_async before handing them over.  This consumer therefore makes NO
synchronous ORM call in an async context (K4 async-safety guard — prevents the
SynchronousOnlyOperation bug class identified in US-48).

Channel/topic names are injected as a parameter (Principle #1 — config-driven,
not hardcoded).

Emits WalletTxEvent for each matching raw event; consumed downstream by the
US-60 BUY-trigger.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator

from core.clock import Clock, stamp_events
from core.datasource import DataSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalletTxEvent:
    """Normalized per-wallet transaction event emitted for the US-60 BUY trigger.

    All fields are derived from the raw DataSource event dict.  The timestamp
    comes exclusively from the injected Clock (Principle #7 — no datetime.now()
    or time.time() on this path).

    tx_type values: "buy" | "sell" | "transfer" | "unknown"

    US-75 AC-1: block_time is the on-chain confirmed block timestamp (Unix epoch
    float, from event.raw["block_time"] populated by decode_wallet_tx / helius
    decode).  Used to compute copy_latency_s = clock.now() − block_time.  None
    when the Helius frame does not carry block_time (e.g. non-pump-fun events),
    in which case honest_fill logs copy_latency_s=None (no ambiguous telemetry —
    §8 P1 requirement).
    """

    wallet: str
    mint: str
    tx_signature: str
    tx_type: str
    sol_amount: float
    token_amount: float
    timestamp: datetime
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    # US-75 AC-1: on-chain confirmed block time (Unix epoch float), or None.
    block_time: float | None = field(default=None, compare=False, hash=False)


class WalletSubscriptionConsumer:
    """Subscribe to wallet-address events via an injected DataSource.

    Principle #7: DataSource + Clock are INJECTED — no concrete source/clock
    import appears in this module.  Wallet addresses are passed in by the
    caller (which reads them from the DB using sync_to_async), so this consumer
    makes NO synchronous ORM call in an async context.

    channel_name is a config-driven parameter (Principle #1): the caller reads
    it from the copytrade config store (e.g. derived from cohort_id) and injects
    it here.  The consumer never decides its own subscription channel name.

    Usage::

        consumer = WalletSubscriptionConsumer(
            source=source,            # concrete source injected by caller
            clock=clock,              # concrete clock injected by caller
            wallet_addresses=addrs,   # read from DB by caller via sync_to_async
            channel_name=name,        # read from config by caller
        )
        async for event in consumer.run():
            # handle WalletTxEvent for the US-60 BUY-trigger
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        wallet_addresses: list[str],
        channel_name: str,
    ) -> None:
        self._source = source
        self._clock = clock
        self._wallets: frozenset[str] = frozenset(wallet_addresses)
        self._channel_name = channel_name
        # All raw (event, ts) pairs seen by this consumer, including filtered ones.
        # Populated during run(); exposed for testing and audit.
        self._processed: list[tuple[dict[str, Any], datetime]] = []

    async def run(self) -> AsyncGenerator[WalletTxEvent, None]:
        """Consume events from the DataSource and yield normalized WalletTxEvents.

        Each raw event is stamped with clock.now() via stamp_events (injected
        clock only — no datetime.now() / time.time() on this path).  Events for
        wallets not in the watch list, or with an empty mint address, are
        filtered out and do NOT produce a WalletTxEvent.
        """
        logger.info(
            "WalletSubscriptionConsumer starting: channel=%s wallets=%d",
            self._channel_name,
            len(self._wallets),
        )
        async for raw_event, ts in stamp_events(self._source, self._clock):
            self._processed.append((raw_event, ts))
            event = self._normalize(raw_event, ts)
            if event is not None:
                yield event
        logger.info(
            "WalletSubscriptionConsumer finished: channel=%s processed=%d",
            self._channel_name,
            len(self._processed),
        )

    def _normalize(self, raw: dict[str, Any], ts: datetime) -> WalletTxEvent | None:
        """Normalize a raw wallet-tx event dict to a WalletTxEvent.

        Expected raw dict keys (schema-faithful to Helius logsSubscribe /
        accountSubscribe wallet-tx events):
            wallet       — monitored wallet address
            mint         — token mint address (empty string if not a token tx)
            signature    — Solana transaction signature
            type         — "buy" | "sell" | "transfer" | "unknown"
            sol_amount   — SOL amount involved in this transaction
            token_amount — token amount involved in this transaction

        Returns None for events from un-watched wallets or events with no mint.
        """
        wallet = raw.get("wallet", "")
        if not wallet or wallet not in self._wallets:
            return None
        mint = raw.get("mint", "")
        if not mint:
            return None
        # US-75 AC-1: promote block_time from raw dict (populated by
        # decode_wallet_tx from the Helius TradeEvent decode path).  None when
        # absent — honest_fill logs copy_latency_s=None in that case.
        raw_bt = raw.get("block_time")
        block_time: float | None = None
        if raw_bt is not None:
            try:
                block_time = float(raw_bt)
            except (TypeError, ValueError):
                block_time = None

        return WalletTxEvent(
            wallet=wallet,
            mint=mint,
            tx_signature=raw.get("signature", ""),
            tx_type=raw.get("type", "unknown"),
            sol_amount=float(raw.get("sol_amount", 0.0)),
            token_amount=float(raw.get("token_amount", 0.0)),
            timestamp=ts,
            raw=raw,
            block_time=block_time,
        )
