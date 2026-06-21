# ---
# module: core.detection.consumer
# sprint: sprint-4
# story: US-15 AC-15.1, US-15 AC-15.2, US-15 AC-15.3, hotfix-graduation-null-pool,
#        EPIC-graduation-migrate-detection
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
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
        # AC-15.3: dedupe tracking — first graduation timestamp per mint (within dedupe_window_s)
        self._graduation_seen: dict[str, datetime] = {}
        # AC-15.3: warm-path pre-staging — mints near graduation (not yet graduated)
        self._prestaged: set[str] = set()

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    @property
    def prestaged(self) -> set[str]:
        """Return the set of mints currently on the pre-stage warm path (AC-15.3)."""
        return set(self._prestaged)

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

    def _is_prestage_event(self, event: dict[str, Any], config: PipelineConfigSchema) -> bool:
        """Return True for near-graduation events that should enter the warm path (AC-15.3).

        Criteria:
          - event type is MEME_DATA
          - event source matches the active filter source
          - event is NOT yet graduated (graduated=False)
          - progress_percent >= config.detection.prestage_progress_pct
        """
        filt = config.detection.filter
        return (
            event.get("type") == "MEME_DATA"
            and not event.get("graduated", False)
            and event.get("source") == filt.source
            and event.get("progress_percent", 0.0) >= config.detection.prestage_progress_pct
        )

    def _is_within_dedupe_window(
        self,
        mint: str,
        timestamp: datetime,
        dedupe_window_s: int,
    ) -> bool:
        """Return True if mint was already graduated within dedupe_window_s seconds (AC-15.3).

        Used to suppress duplicate graduation events without hitting the database again.
        Time comparison uses the injected clock's timestamp — never wall time.
        """
        if mint not in self._graduation_seen:
            return False
        first_seen = self._graduation_seen[mint]
        return (timestamp - first_seen).total_seconds() <= dedupe_window_s

    def _persist_graduation_sync(
        self,
        event: dict[str, Any],
        timestamp: datetime,
    ) -> None:
        """Synchronous ORM call: create or update the Token row for this event.

        Imported lazily inside this method to avoid circular imports at module
        load time (core.models imports core.schemas, which is already in scope
        at the package level; the lazy import sidesteps any load-order issues).

        Field mapping (MEME_DATA event → Token row):
          address      → mint (primary key)
          poolAddress  → pool_address
          blockTime    → graduated_block_time + graduated_at (UTC epoch conversion)
          dex_source   → dex_source  (if present; falls back to source)
          source       → dex_source  (fallback when dex_source key is absent)
          <full event> → raw_graduation (verbatim JSONB)

        The ``dex_source`` key is preferred over ``source`` so that migrate-path
        events (dex_source="helius_migrate", source="pump_dot_fun") store the
        correct provenance.  Birdeye events carry only ``source``; the fallback
        preserves their existing behavior.

        If blockTime is absent, graduated_at falls back to the injected clock's
        timestamp (the stamp_events timestamp passed into this call).
        """
        from core.models import Token  # lazy import — avoids circular at load time

        mint: str = event["address"]
        # Birdeye new-listing (graduation) frames carry NO pool address, so the
        # graduation source emits poolAddress=None.  pool_address is a non-nullable
        # CharField — coerce a null/absent pool to "" so a null pool persists
        # cleanly instead of crashing the chain (downstream scores on pre-grad
        # Helius features keyed by mint and tolerates an empty pool).
        pool_address: str = event.get("poolAddress") or ""
        # dex_source: prefer the explicit "dex_source" key (set by HeliusMigrateSource
        # to "helius_migrate") over "source" (set to "pump_dot_fun" for filter matching).
        # Birdeye events carry only "source"; the fallback preserves their behavior.
        dex_source: str = event.get("dex_source") or event.get("source", "")

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
                if config is not None:
                    mint = event.get("address", "")
                    if self._is_graduation_event(event, config):
                        dedupe_window_s = config.detection.dedupe_window_s
                        if not self._is_within_dedupe_window(mint, timestamp, dedupe_window_s):
                            await _persist_async(event, timestamp)
                            if mint not in self._graduation_seen:
                                self._graduation_seen[mint] = timestamp
                    elif self._is_prestage_event(event, config):
                        self._prestaged.add(mint)
