# ---
# module: core.tape.birdeye_graduation_source
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: core.datasource, json, logging, typing, websockets (lazy)
# ---
"""BirdeyeGraduationSource — concrete DataSource for Birdeye's new-listing stream.

Mirrors BirdeyeSwapSource's connect/auth/disconnect/events lifecycle but
subscribes to Birdeye's newly-graduated / new-listing stream instead of the
per-mint SUBSCRIBE_TXS swap feed.  Each yielded event is shaped EXACTLY as
DetectionConsumer expects for a graduation:

    {
        "type":        "MEME_DATA",
        "graduated":   True,
        "address":     <mint>,
        "poolAddress": <pool>,
        "blockTime":   <epoch int>,
        "source":      "birdeye",        # config-driven (detection.filter.source-aware)
        "raw":         <the raw Birdeye payload>,
    }

DetectionConsumer (core/detection/consumer.py) matches a graduation when
``event["type"] == "MEME_DATA"`` and ``event["graduated"] == filter.graduated``
and ``event["source"] == filter.source``.  The ``source`` we stamp is therefore
config-driven (defaults below) so the active config's detection.filter.source
can match it without a code change.

CONFIG-DRIVEN WIRING (Principle #1)
===================================
The subscribe message ``type`` and the inbound frame ``type`` string are read
from the active config's ``detection`` section when a ``config`` dict is
injected, with sane defaults baked in so the source also works standalone:

    detection.graduation_subscribe_type   (default "SUBSCRIBE_TOKEN_NEW_LISTING")
    detection.graduation_data_type        (default "TOKEN_NEW_LISTING_DATA")
    detection.graduation_subscribe_data   (default {} — extra fields merged into
                                           the subscribe payload's "data" object)
    detection.event_source                (default "birdeye" — the stamped
                                           graduation event ``source`` value)

These are NOT in the pydantic DetectionConfig schema (which is closed); they are
read defensively from the raw detection dict so they can be tuned live without a
migration or schema change.

ARCHITECTURAL CONSTRAINTS (enforced by test_tape_recorder_ac181.py / test_clock.py)
==================================================================================
- No import of LiveSource / ReplaySource / core.live_source / core.replay_source.
- No top-level ``import websockets`` — lazy import inside connect() only.
- No ``datetime.now()`` / ``time.time()`` calls anywhere in this file.
- NO network at import time.

Lifecycle:
    source = BirdeyeGraduationSource(api_key=..., config=<detection dict>)
    await source.connect()           # opens WS, sends the subscribe message
    async for event in source.events():
        ...                          # yields MEME_DATA graduation dicts
    await source.disconnect()        # closes WS
"""
import json
import logging
from typing import Any, AsyncGenerator

from core.datasource import DataSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — Birdeye WebSocket handshake (identical to BirdeyeSwapSource)
# ---------------------------------------------------------------------------

BIRDEYE_WS_URL: str = "wss://public-api.birdeye.so/socket/solana"
BIRDEYE_WS_ORIGIN: str = "ws://public-api.birdeye.so"
BIRDEYE_WS_SUBPROTOCOL: str = "echo-protocol"

# ---------------------------------------------------------------------------
# Config-driven defaults (Principle #1) — overridable via the detection config.
# ---------------------------------------------------------------------------

#: Subscription type for the meme-platform / pump.fun new-listing (graduation) stream.
DEFAULT_SUBSCRIBE_TYPE: str = "SUBSCRIBE_TOKEN_NEW_LISTING"

#: Inbound frame type carrying a new-listing (graduation) payload.
DEFAULT_DATA_TYPE: str = "TOKEN_NEW_LISTING_DATA"

#: Default stamped graduation event ``source`` — must equal the active config's
#: detection.filter.source for DetectionConsumer to persist the Token row.
DEFAULT_EVENT_SOURCE: str = "birdeye"

# Config keys read defensively from the raw detection dict (NOT in the closed
# pydantic schema, so they can be tuned live without a migration).
_CFG_SUBSCRIBE_TYPE = "graduation_subscribe_type"
_CFG_DATA_TYPE = "graduation_data_type"
_CFG_SUBSCRIBE_DATA = "graduation_subscribe_data"
_CFG_EVENT_SOURCE = "event_source"


def build_subscribe_message(config: dict | None) -> dict:
    """Build the typed SUBSCRIBE message dict (config-driven, with defaults).

    The message ``type`` is read from ``config[graduation_subscribe_type]`` when
    present, else DEFAULT_SUBSCRIBE_TYPE.  Any ``config[graduation_subscribe_data]``
    dict is merged into the payload's ``data`` object so extra subscription
    parameters (chains, platforms, etc.) can be added live without a code change.

    Pure function — no I/O, no network, no clock.

    Args:
        config: The active config's raw detection section dict, or None.

    Returns:
        A JSON-serializable subscribe message dict.
    """
    cfg = config or {}
    sub_type = cfg.get(_CFG_SUBSCRIBE_TYPE) or DEFAULT_SUBSCRIBE_TYPE
    extra_data = cfg.get(_CFG_SUBSCRIBE_DATA)
    data: dict[str, Any] = {}
    if isinstance(extra_data, dict):
        data.update(extra_data)
    return {"type": sub_type, "data": data}


def _expected_data_type(config: dict | None) -> str:
    """Return the inbound frame ``type`` string that carries a graduation."""
    cfg = config or {}
    return cfg.get(_CFG_DATA_TYPE) or DEFAULT_DATA_TYPE


def _event_source(config: dict | None) -> str:
    """Return the stamped graduation event ``source`` value (config-driven)."""
    cfg = config or {}
    return cfg.get(_CFG_EVENT_SOURCE) or DEFAULT_EVENT_SOURCE


# ---------------------------------------------------------------------------
# Pure frame mapper — raw Birdeye new-listing payload -> MEME_DATA graduation
# ---------------------------------------------------------------------------


def map_new_listing_frame(
    parsed: dict,
    *,
    data_type: str,
    event_source: str,
) -> dict | None:
    """Map a raw Birdeye WebSocket frame to a MEME_DATA graduation event, or None.

    Returns None for any frame that is not a graduation new-listing frame
    (WELCOME/ack/error frames, wrong type, missing mint).  The returned dict is
    shaped EXACTLY as DetectionConsumer._persist_graduation_sync expects:
    address (required), poolAddress, blockTime, source, plus type/graduated and
    the verbatim raw payload under "raw".

    Field extraction is tolerant of the several key spellings Birdeye uses across
    its meme streams (``address`` | ``tokenAddress`` | ``mint`` for the mint;
    ``poolAddress`` | ``pairAddress`` | ``pool`` for the pool;
    ``blockUnixTime`` | ``blockTime`` | ``liquidityAddedTime`` for the epoch).
    The exact live spelling is to be confirmed during the live window — raw
    frames are logged at debug level (see events()).

    Pure function — no I/O, no network, no clock.

    Args:
        parsed:       A JSON-parsed inbound frame dict.
        data_type:    The frame ``type`` string that marks a graduation payload.
        event_source: The ``source`` value to stamp on the emitted event.

    Returns:
        A MEME_DATA graduation event dict, or None to skip the frame.
    """
    if not isinstance(parsed, dict) or parsed.get("type") != data_type:
        return None

    payload = parsed.get("data")
    if not isinstance(payload, dict):
        return None

    mint = (
        payload.get("address")
        or payload.get("tokenAddress")
        or payload.get("mint")
    )
    if not mint:
        return None

    pool = (
        payload.get("poolAddress")
        or payload.get("pairAddress")
        or payload.get("pool")
        or ""
    )

    block_time = (
        payload.get("blockUnixTime")
        or payload.get("blockTime")
        or payload.get("liquidityAddedTime")
    )

    event: dict[str, Any] = {
        "type": "MEME_DATA",
        "graduated": True,
        "address": mint,
        "poolAddress": pool,
        "source": event_source,
        "raw": payload,
    }
    if block_time is not None:
        try:
            event["blockTime"] = int(block_time)
        except (TypeError, ValueError):
            # Leave blockTime absent — DetectionConsumer falls back to the
            # injected clock timestamp when blockTime is missing.
            pass
    return event


# ---------------------------------------------------------------------------
# Concrete DataSource — Birdeye new-listing (graduation) stream
# ---------------------------------------------------------------------------


class BirdeyeGraduationSource(DataSource):
    """Live graduation-event source backed by the Birdeye new-listing WebSocket.

    Subscribes to Birdeye's meme-platform / pump.fun new-listing stream and
    yields MEME_DATA graduation events shaped for DetectionConsumer.  The
    subscribe message type, the inbound data-frame type, and the stamped event
    source are all config-driven (Principle #1) with sane defaults.

    Args:
        api_key: Birdeye API key (x-api-key query param).
        config:  The active config's raw ``detection`` section dict (or None).
                 Read defensively for the graduation_* tuning keys (see module
                 docstring).  Passing None uses the baked-in defaults.

    Note:
        ``websockets``/``ssl``/``certifi`` are imported lazily inside connect()
        to keep import-time free of side effects (US-2 static-analysis guard /
        no network at import).
    """

    def __init__(self, api_key: str, config: dict | None = None) -> None:
        self._api_key: str = api_key
        self._config: dict = config or {}
        self._ws: Any = None
        self._data_type: str = _expected_data_type(self._config)
        self._event_source: str = _event_source(self._config)

    async def connect(self) -> None:
        """Open the Birdeye WebSocket and send the new-listing subscribe message.

        Uses the proven Birdeye handshake (matching BirdeyeSwapSource): the API
        key is a query param, the Origin header + 'echo-protocol' subprotocol are
        required, and TLS uses certifi.  The subscribe payload type is
        config-driven (defaults to SUBSCRIBE_TOKEN_NEW_LISTING).
        """
        import ssl  # lazy — keeps import-time free of side effects

        import websockets  # lazy import — keeps import-time free of network side effects

        try:
            import certifi

            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # pragma: no cover - certifi always present in the image
            ssl_ctx = ssl.create_default_context()

        url = f"{BIRDEYE_WS_URL}?x-api-key={self._api_key}"
        self._ws = await websockets.connect(
            url,
            extra_headers={"Origin": BIRDEYE_WS_ORIGIN},
            subprotocols=[BIRDEYE_WS_SUBPROTOCOL],
            ssl=ssl_ctx,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=10,
        )
        subscribe_msg = json.dumps(build_subscribe_message(self._config))
        await self._ws.send(subscribe_msg)
        logger.info(
            "[FIREHOSE] graduation-source subscribed type=%s expecting_frame=%s source=%s",
            build_subscribe_message(self._config)["type"],
            self._data_type,
            self._event_source,
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts from the Birdeye new-listing stream.

        Each raw WebSocket message is JSON-parsed and logged at DEBUG level (so
        the exact live frame format can be confirmed during the live window),
        then mapped via map_new_listing_frame.  Non-graduation frames
        (WELCOME/ack/error, wrong type, missing mint) yield nothing.

        Yields nothing if the connection was never opened (``self._ws is None``).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            # Debug-log the RAW frame so the live format is confirmable (the
            # whole point of the first live window).
            logger.debug("[FIREHOSE] graduation raw-frame: %s", raw_message)
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            event = map_new_listing_frame(
                parsed,
                data_type=self._data_type,
                event_source=self._event_source,
            )
            if event is not None:
                yield event
