# ---
# module: core.detection.consumer
# sprint: sprint-4
# story: US-15 AC-15.1, US-15 AC-15.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource, core.clock, core.schemas, asgiref, datetime, typing
# ---
"""DetectionConsumer — reads MEME graduation events from a DataSource seam.

The consumer depends ONLY on the abstract DataSource and Clock interfaces
(Principle #7 / PRD §4).  It never imports a concrete source class (LiveSource,
ReplaySource) and never calls datetime.now() or time.time() directly.

Time is read exclusively via the injected Clock, passed through stamp_events().
This enables deterministic replay: swap the DataSource for a ReplaySource and
the Clock for a VirtualClock to replay any historical tape byte-identically.

AC-15.2 extension:
    When a config_fn is supplied, each MEME_DATA graduation event is matched
    against the detection filter from get_active_config() (the US-11 resolver —
    never a hardcoded constant or os.getenv).  Matching events are persisted as
    Token rows via update_or_create, using sync_to_async to bridge the async
    run() loop to Django's synchronous ORM.
"""
from datetime import datetime, timezone
from typing import Any, Callable

from asgiref.sync import sync_to_async

from core.clock import Clock, stamp_events
from core.datasource import DataSource
from core.schemas import PipelineConfigSchema


class DetectionConsumer:
    """Reads events from a DataSource, timestamps them, and (optionally) persists
    graduation events as Token rows when a config_fn is provided.

    This is the live/replay seam for the detection path (US-2 Principle #7).
    The consumer never references a concrete source class or the system clock.

    Args:
        source:    Any DataSource implementation (live or replay).
        clock:     Any Clock implementation (wall or virtual).
        config_fn: Optional callable returning PipelineConfigSchema | None.
                   When provided, the active detection filter is read on every
                   event — graduation events that pass the filter are persisted
                   as Token rows (AC-15.2).  Must be the US-11 resolver
                   (get_active_config) or a test double; NEVER os.getenv.
                   Defaults to None for backward compatibility with AC-15.1 tests.
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        config_fn: Callable[[], PipelineConfigSchema | None] | None = None,
    ) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._config_fn: Callable[[], PipelineConfigSchema | None] | None = config_fn
        self._processed: list[tuple[dict[str, Any], datetime]] = []

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_graduation_event(self, event: dict[str, Any], config: PipelineConfigSchema) -> bool:
        """Return True when *event* matches the active detection filter.

        Checks:
          - event type is MEME_DATA
          - event['graduated'] matches filter.graduated
          - event['source'] matches filter.source
        """
        filt = config.detection.filter
        return (
            event.get("type") == "MEME_DATA"
            and event.get("graduated") == filt.graduated
            and event.get("source") == filt.source
        )

    def _persist_graduation_sync(
        self,
        event: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Synchronous ORM call: create or update the Token row for this event.

        Imported lazily inside this method to avoid circular imports at module
        load time (core.models imports core.schemas, which is already in scope
        at the package level; the lazy import sidesteps any load-order issues).

        Field mapping (Birdeye MEME_DATA event → Token row):
          address      → mint (primary key)
          poolAddress  → pool_address
          blockTime    → graduated_block_time + graduated_at (UTC epoch conversion)
          source       → dex_source
          <full event> → raw_graduation (verbatim JSONB)

        If blockTime is absent, graduated_at falls back to the injected clock's
        timestamp (the stamp_events timestamp passed into this call).
        """
        from core.models import Token  # lazy import — avoids circular at load time

        mint: str = event["address"]
        pool_address: str = event.get("poolAddress", "")
        dex_source: str = event.get("source", "")

        block_time = event.get("blockTime")
        if block_time is not None:
            graduated_at = datetime.fromtimestamp(int(block_time), tz=timezone.utc)
            graduated_block_time = int(block_time)
        else:
            graduated_at = timestamp
            graduated_block_time = int(timestamp.timestamp())

        Token.objects.update_or_create(
            mint=mint,
            defaults={
                "pool_address": pool_address,
                "graduated_at": graduated_at,
                "graduated_block_time": graduated_block_time,
                "dex_source": dex_source,
                "raw_graduation": event,
            },
        )

    async def run(self) -> None:
        """Consume all events from the source, stamping each with the injected clock.

        For each event yielded by stamp_events():
          1. The (event, timestamp) pair is appended to self._processed.
          2. If config_fn is provided and returns a config, and the event matches
             the active detection filter, the event is persisted as a Token row
             via an async-wrapped synchronous ORM call (sync_to_async).

        Both the config_fn call and the ORM persist call are wrapped with
        sync_to_async because Django's ORM is synchronous and cannot be called
        directly from an async context (Django 5 / asgiref enforcement).

        The method returns when the source is exhausted.
        """
        _config_fn_async = (
            sync_to_async(self._config_fn, thread_sensitive=True)
            if self._config_fn is not None
            else None
        )
        _persist_async = sync_to_async(self._persist_graduation_sync, thread_sensitive=True)

        async for event, timestamp in stamp_events(self._source, self._clock):
            self._processed.append((event, timestamp))

            if _config_fn_async is not None:
                config = await _config_fn_async()
                if config is not None and self._is_graduation_event(event, config):
                    await _persist_async(event, timestamp)
