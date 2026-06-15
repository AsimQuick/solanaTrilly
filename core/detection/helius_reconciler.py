# ---
# module: core.detection.helius_reconciler
# sprint: sprint-4
# story: US-16 AC-16.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource, core.clock, core.models, asgiref, datetime, typing
# ---
"""MigrateReconciler — Helius 'migrate' gap-recovery backstop for the detection pipeline.

Implements the D4 resilience backstop (PRD §3.3, §6.1).  The reconciler subscribes
to the Helius transactionSubscribe stream (filtered to the pump program 'migrate'
instruction) and persists graduated tokens that the Birdeye MEME stream may have
missed (dropped events, WebSocket reconnects, etc.).

Key design decisions:
  - Uses get_or_create (NOT update_or_create): if DetectionConsumer already wrote
    the Token row, the reconciler leaves it untouched.  This is no-overwrite
    idempotency — the MEME stream row is canonical; Helius fills the gap only.
  - Follows the SAME DataSource seam as DetectionConsumer (Principle #7 / US-2):
    the constructor accepts a DataSource and a Clock; no concrete source class is
    imported here.
  - Module-level constants (PUMP_PROGRAM, MIGRATE_INSTRUCTION, MAX_TX_VERSION) are
    exported for the live HeliusSource wiring layer to use; they are NOT used inside
    this module itself.

Module-level constants (for HeliusSource subscription — not used inside this file):
  PUMP_PROGRAM          — the pump.fun program address to filter on
  MIGRATE_INSTRUCTION   — the instruction discriminator to filter on
  MAX_TX_VERSION        — maxSupportedTransactionVersion for the subscription
"""
from datetime import datetime, timezone
from typing import Any

from asgiref.sync import sync_to_async

from core.clock import Clock, stamp_events
from core.datasource import DataSource

# ---------------------------------------------------------------------------
# Subscription constants — exported for the live HeliusSource wiring layer.
# These are NOT used inside this module; they live here as the canonical
# definition so both the live source and any tests import from one place.
# ---------------------------------------------------------------------------

PUMP_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MIGRATE_INSTRUCTION: str = "migrate"
MAX_TX_VERSION: int = 0


class MigrateReconciler:
    """Helius-side gap-recovery reconciler for graduated tokens (D4 backstop).

    Consumes events from any DataSource carrying MIGRATE_TX payloads and calls
    Token.objects.get_or_create for each — filling in tokens the Birdeye MEME
    stream missed without overwriting tokens the MEME consumer already created.

    Args:
        source: Any DataSource implementation (live HeliusSource or ReplaySource
                for offline testing).  The reconciler never imports a concrete class.
        clock:  Any Clock implementation (WallClock for production, VirtualClock
                for deterministic offline tests).
    """

    def __init__(self, source: DataSource, clock: Clock) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._processed: list[tuple[dict[str, Any], datetime]] = []

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_migrate_sync(
        self,
        event: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Synchronous ORM helper: get_or_create a Token row for a MIGRATE_TX event.

        Uses get_or_create (NOT update_or_create) so that an existing Token row
        written by the DetectionConsumer (MEME stream) is never overwritten.
        The Helius migrate row fills in the gap only when the MEME stream missed it.

        Lazy import of core.models avoids circular imports at module load time.

        Field mapping (Helius MIGRATE_TX event → Token row):
          event["mint"]             → mint (primary key)
          event.get("pool_address") → pool_address (defaults to "")
          event.get("block_time")   → graduated_block_time (INT) +
                                      graduated_at (UTC datetime from epoch)
          "helius_migrate"          → dex_source (static label for this path)
          event                     → raw_graduation (verbatim JSONB)

        If block_time is absent, graduated_at falls back to the injected clock's
        timestamp (the stamp_events timestamp passed into this call).
        """
        from core.models import Token  # lazy import — avoids circular at load time

        mint: str = event["mint"]
        pool_address: str = event.get("pool_address", "")

        block_time = event.get("block_time")
        if block_time is not None:
            graduated_at = datetime.fromtimestamp(int(block_time), tz=timezone.utc)
            graduated_block_time = int(block_time)
        else:
            graduated_at = timestamp
            graduated_block_time = int(timestamp.timestamp())

        Token.objects.get_or_create(
            mint=mint,
            defaults={
                "pool_address": pool_address,
                "graduated_at": graduated_at,
                "graduated_block_time": graduated_block_time,
                "dex_source": "helius_migrate",
                "raw_graduation": event,
            },
        )

    async def run(self) -> None:
        """Consume all events from the source, persisting MIGRATE_TX events as Token rows.

        For each event yielded by stamp_events():
          1. The (event, timestamp) pair is appended to self._processed.
          2. If the event type is "MIGRATE_TX", _persist_migrate_sync is called via
             sync_to_async to bridge the async loop to Django's synchronous ORM.

        Returns when the source is exhausted.
        """
        _persist_async = sync_to_async(self._persist_migrate_sync, thread_sensitive=True)

        async for event, timestamp in stamp_events(self._source, self._clock):
            self._processed.append((event, timestamp))

            if event.get("type") == "MIGRATE_TX":
                await _persist_async(event, timestamp)
