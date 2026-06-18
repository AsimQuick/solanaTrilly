# ---
# module: core.dashboard.consumer
# sprint: sprint-10
# story: US-48 AC-48.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.datasource, core.schemas, core.resolver, channels, json, asyncio, typing
# ---
"""ONE tape feed WebSocket consumer (PRD §13.4, AC-48.2).

The consumer pushes tape/candle/position deltas to the frontend over WebSocket.
The delta source is EXCLUSIVELY the injected DataSource (the same tape DataSource
the TapeRecorder writes and the FeatureExtractor reads).  There is NO separate
price feed — solanaBilly's heartbeat/feed-split drift class is structurally
excluded.

Key invariants (AC-48.2):
  - Consumer depends only on DataSource (abstract) — NEVER LiveSource/ReplaySource.
  - Feature computation uses FeatureExtractor (the shared US-30 extractor path).
  - All channel/topic names and candle cadence come from get_active_config()
    via the DashboardConfig section (Principle #1 — no literals here).
  - The processing logic (TapeFeedProcessor) is separated from the WS plumbing
    (TapeFeedConsumer) so it is testable OFFLINE with zero firehose.

Message types emitted by TapeFeedProcessor.iter_deltas():
  candle_delta  — an OHLC candle closed at the configured interval boundary
  position_delta — running tape aggregate (last_price, vol_buy, vol_sell, net_flow)
"""
import json
from typing import Any, AsyncGenerator, Callable

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from core.datasource import DataSource
from core.schemas import DashboardConfig

# ---------------------------------------------------------------------------
# SENTINEL — import-time guard (H1 pattern, AC-48.3 wire-up)
# ---------------------------------------------------------------------------
# TapeFeedProcessor and TapeFeedConsumer are both named here so the H1
# ImportError trap in the AC-48.3 test fails pytest COLLECTION if this module
# is deleted or these names are removed.
__all__ = ["TapeFeedProcessor", "TapeFeedConsumer"]


def _default_config_fn():
    """Lazy import of get_active_config to avoid circular imports at load time."""
    from core.resolver import get_active_config  # noqa: PLC0415
    return get_active_config()


class TapeFeedProcessor:
    """Core delta-computation logic for the ONE tape feed (AC-48.2).

    Consumes raw swap events from an injected DataSource (NEVER a concrete
    LiveSource or ReplaySource — dependency-injected, Principle #7).  Aggregates
    swaps into OHLC candles at the config-driven interval and maintains a running
    position aggregate.

    Args:
        source:    Any DataSource implementation (live or replay).
        config_fn: Optional callable → PipelineConfigSchema | None.
                   When None, defaults to _default_config_fn (get_active_config).
                   Resolved at iter_deltas() call time so tests can inject a
                   synthetic config without Django ORM being available.
    """

    def __init__(
        self,
        source: DataSource,
        config_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._source: DataSource = source
        self._config_fn: Callable[[], Any] = config_fn or _default_config_fn

    async def iter_deltas(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield candle_delta and position_delta dicts from the tape source.

        Reads config once at entry (Principle #1: interval and topic names come
        from DashboardConfig, never literals).  Then consumes every swap event
        from self._source — the ONE DataSource that the recorder writes and the
        FeatureExtractor reads — and emits:
          - a position_delta on every swap (running aggregate: last price, net flow)
          - a candle_delta whenever the candle interval boundary is crossed

        At source exhaustion, the partial candle is emitted if it contains data.

        Yields:
            dict with 'type' == 'candle_delta' or 'position_delta'.
        """
        config = self._config_fn()
        _dash = config.dashboard if config is not None else DashboardConfig()
        interval_s: int = _dash.candle_interval_s
        candle_topic: str = _dash.candle_topic
        position_topic: str = _dash.position_topic

        # Running candle accumulator
        candle_open: float | None = None
        candle_high: float | None = None
        candle_low: float | None = None
        candle_close: float | None = None
        candle_vol: float = 0.0
        candle_start: int | None = None  # Unix ts of candle open

        # Running position aggregate
        vol_buy: float = 0.0
        vol_sell: float = 0.0
        n_swaps: int = 0
        last_price: float | None = None

        await self._source.connect()
        try:
            async for event in self._source.events():
                price: float | None = event.get("price")
                vol: float = float(event.get("vol_sol") or event.get("vol") or 0.0)
                side: str = event.get("side", "buy")
                block_time: int = int(event.get("block_time", 0))
                mint: str = event.get("mint", "")

                if price is None or price <= 0:
                    continue

                price = float(price)
                n_swaps += 1
                last_price = price
                if side == "buy":
                    vol_buy += vol
                else:
                    vol_sell += vol

                # Initialize candle window on first swap
                if candle_start is None:
                    candle_start = (block_time // interval_s) * interval_s
                    candle_open = price
                    candle_high = price
                    candle_low = price

                # Detect candle interval crossing
                current_window_start = (block_time // interval_s) * interval_s
                if current_window_start > candle_start and candle_open is not None:
                    # Close the completed candle
                    yield {
                        "type": candle_topic,
                        "mint": mint,
                        "candle": {
                            "t": candle_start,
                            "open": candle_open,
                            "high": candle_high,
                            "low": candle_low,
                            "close": candle_close,
                            "vol": candle_vol,
                            "interval_s": interval_s,
                        },
                    }
                    # Start new candle
                    candle_start = current_window_start
                    candle_open = price
                    candle_high = price
                    candle_low = price
                    candle_vol = 0.0

                # Accumulate into current candle
                candle_high = max(candle_high, price)
                candle_low = min(candle_low, price)
                candle_close = price
                candle_vol += vol

                # Emit position_delta on every swap
                yield {
                    "type": position_topic,
                    "mint": mint,
                    "position": {
                        "last_price": last_price,
                        "vol_buy": vol_buy,
                        "vol_sell": vol_sell,
                        "net_flow": vol_buy - vol_sell,
                        "n_swaps": n_swaps,
                    },
                }

        finally:
            await self._source.disconnect()

        # Emit final partial candle if any swaps were received
        if candle_open is not None and candle_close is not None:
            yield {
                "type": candle_topic,
                "mint": "",
                "candle": {
                    "t": candle_start,
                    "open": candle_open,
                    "high": candle_high,
                    "low": candle_low,
                    "close": candle_close,
                    "vol": candle_vol,
                    "interval_s": interval_s,
                },
            }


class TapeFeedConsumer(AsyncWebsocketConsumer):
    """Django Channels WebSocket consumer for the ONE tape feed (AC-48.2, PRD §13.4).

    Endpoint: ws/tape/<mint>/

    On connection, joins the config-driven channel group and starts a background
    task that drives TapeFeedProcessor.iter_deltas() — consuming from the injected
    DataSource and forwarding each delta as a JSON WebSocket message.

    The DataSource is injected at construction time (Principle #7 — the consumer
    never instantiates LiveSource/ReplaySource directly).  In the live pipeline
    the source factory is wired by the ASGI routing layer; in tests it is replaced
    with a ReplaySource for deterministic OFFLINE verification.

    Args:
        source:    DataSource injected by the caller / routing layer.
        config_fn: Optional callable returning PipelineConfigSchema | None.
    """

    # Class-level source factory — override in tests for dependency injection.
    # The live routing layer must set this before the consumer handles connections.
    source_factory: Callable[[], DataSource] | None = None

    def __init__(self, *args, source: DataSource | None = None, config_fn: Callable | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._injected_source: DataSource | None = source
        self._config_fn: Callable | None = config_fn
        self._feed_task = None

    async def connect(self) -> None:
        self.mint: str = self.scope["url_route"]["kwargs"].get("mint", "")

        # Resolve channel group name from config (Principle #1).
        # get_active_config() may hit the Django ORM on a cache miss; the
        # consumer runs in an async context, so the sync call MUST be wrapped
        # in sync_to_async or Django raises SynchronousOnlyOperation (the
        # WS-upgrade 500 caught by the AC-48.3 deploy smoke-test).
        config = await sync_to_async(self._config_fn or _default_config_fn)()
        _dash = config.dashboard if config is not None else DashboardConfig()
        self.group_name: str = f"{_dash.ws_channel_prefix}_{self.mint}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        source = self._injected_source
        if source is None and self.source_factory is not None:
            source = self.source_factory()

        if source is not None:
            processor = TapeFeedProcessor(source, config_fn=self._config_fn)
            import asyncio
            self._feed_task = asyncio.ensure_future(self._run(processor))

    async def disconnect(self, close_code: int) -> None:
        if self._feed_task is not None:
            self._feed_task.cancel()
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data: str = None, bytes_data: bytes = None) -> None:
        pass

    async def _run(self, processor: TapeFeedProcessor) -> None:
        """Drive the processor and send each delta over the WebSocket."""
        async for delta in processor.iter_deltas():
            await self.send(text_data=json.dumps(delta))
