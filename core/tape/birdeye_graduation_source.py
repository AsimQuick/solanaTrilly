# ---
# module: core.tape.birdeye_graduation_source
# sprint: sprint-14
# story: live-firehose-spine
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: core.datasource, core.clock, datetime, json, logging, typing, websockets (lazy)
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
        "poolAddress": None,             # Birdeye new-listing frames carry NO pool
        "blockTime":   <epoch int>,      # parsed from liquidityAddedAt (ISO-8601, UTC)
        "source":      "pump_dot_fun",   # config-driven; must equal detection.filter.source
        "dex":         "pump_amm",       # the RAW DEX/AMM the token listed on (audit)
        "raw":         <the raw Birdeye payload>,   # verbatim, preserves "source":"pump_amm"
    }

DetectionConsumer (core/detection/consumer.py) matches a graduation when
``event["type"] == "MEME_DATA"`` and ``event["graduated"] == filter.graduated``
and ``event["source"] == filter.source``.  We therefore stamp ``source`` with
the value the active ``detection.filter.source`` expects (default
"pump_dot_fun") so graduated pump.fun tokens pass the existing filter WITHOUT a
consumer change.  The original DEX ("pump_amm") is preserved verbatim under
``raw`` and surfaced on a dedicated ``dex`` field for audit.

REAL BIRDEYE FRAMES (the format this module is built against)
=============================================================
- Welcome:   {"type":"WELCOME","data":null}
- pump.fun graduation (WANT):
      {"type":"TOKEN_NEW_LISTING_DATA","data":{
          "address":"6Y5P...okn","decimals":9,"name":"dream",
          "source":"pump_amm","symbol":"dream",
          "liquidity":18640.85,"liquidityAddedAt":"2026-06-19T07:17:33"}}
- NON-pump.fun (DROP): same shape but "source":"pancakeswap_v3" / "meteora_damm_v2".

KEY FACTS about the frame's ``data`` object:
  - ``address``          = the token MINT.
  - ``source``           = the DEX/AMM the token listed on.  pump.fun graduations
                           list on PumpSwap, i.e. ``source == "pump_amm"``.
  - ``liquidityAddedAt`` = ISO-8601 string (e.g. "2026-06-19T07:17:33"), UTC.
  - There is NO pool/pair address field.
  - There is NO epoch/blockTime field.

PUMP.FUN FILTER (config-driven, Principle #1)
=============================================
Only frames whose ``data.source`` is in the allow-list are emitted; everything
else (pancakeswap_v3, meteora_damm_v2, ...) is dropped with a DEBUG log.  The
allow-list is read from ``detection.graduation_dex_allowlist`` with a sane
default of ``["pump_amm"]``.

CONFIG-DRIVEN WIRING (Principle #1)
===================================
Read defensively from the active config's raw ``detection`` section dict (with
defaults baked in so the source also works standalone):

    detection.graduation_subscribe_type   (default "SUBSCRIBE_TOKEN_NEW_LISTING")
    detection.graduation_data_type        (default "TOKEN_NEW_LISTING_DATA")
    detection.graduation_subscribe_data   (default {} — extra fields merged into
                                           the subscribe payload's "data" object)
    detection.graduation_dex_allowlist    (default ["pump_amm"] — raw DEX sources
                                           that count as a pump.fun graduation)
    detection.event_source                (default: detection.filter.source if
                                           present, else "pump_dot_fun" — the
                                           stamped graduation event ``source``)

These are NOT in the pydantic DetectionConfig schema (which is closed); they are
read defensively from the raw detection dict so they can be tuned live without a
migration or schema change.  ``event_source`` defaults to ``filter.source`` so
the stamped source automatically matches whatever the active filter expects.

CLOCK INJECTION (Principle #7 / AC-2.2)
=======================================
The codebase forbids ``datetime.now()`` / ``time.time()`` in ``core/`` (AST
guard).  When ``liquidityAddedAt`` is missing or unparseable, the event-arrival
time is needed as a fallback.  We therefore inject a Clock (the same pattern
DetectionConsumer / ScoreOrchestrator use); ``events()`` reads ``clock.now()``
and passes a fallback epoch into the pure mapper, keeping the mapper pure and
tests deterministic.

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
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from core.clock import Clock, WallClock
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
#: pump.fun is the only meme platform we trade, so this is the sane default.
DEFAULT_EVENT_SOURCE: str = "pump_dot_fun"

#: Default raw-DEX allow-list — pump.fun graduations list on PumpSwap ("pump_amm").
#: Everything else (pancakeswap_v3, meteora_damm_v2, ...) is dropped.
DEFAULT_DEX_ALLOWLIST: tuple[str, ...] = ("pump_amm",)

# Config keys read defensively from the raw detection dict (NOT in the closed
# pydantic schema, so they can be tuned live without a migration).
_CFG_SUBSCRIBE_TYPE = "graduation_subscribe_type"
_CFG_DATA_TYPE = "graduation_data_type"
_CFG_SUBSCRIBE_DATA = "graduation_subscribe_data"
_CFG_EVENT_SOURCE = "event_source"
_CFG_DEX_ALLOWLIST = "graduation_dex_allowlist"


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
    """Return the stamped graduation event ``source`` value (config-driven).

    Resolution order:
      1. detection.event_source                (explicit override)
      2. detection.filter.source               (so the stamp matches the filter)
      3. DEFAULT_EVENT_SOURCE ("pump_dot_fun")
    """
    cfg = config or {}
    explicit = cfg.get(_CFG_EVENT_SOURCE)
    if explicit:
        return explicit
    filt = cfg.get("filter")
    if isinstance(filt, dict) and filt.get("source"):
        return filt["source"]
    return DEFAULT_EVENT_SOURCE


def _dex_allowlist(config: dict | None) -> frozenset[str]:
    """Return the set of raw DEX sources that count as a pump.fun graduation.

    Read from ``detection.graduation_dex_allowlist`` (a list of strings) with a
    default of ``["pump_amm"]``.  Pure function — no I/O.
    """
    cfg = config or {}
    raw = cfg.get(_CFG_DEX_ALLOWLIST)
    if isinstance(raw, (list, tuple)) and raw:
        return frozenset(str(x) for x in raw)
    return frozenset(DEFAULT_DEX_ALLOWLIST)


# ---------------------------------------------------------------------------
# Pure timestamp parser — Birdeye liquidityAddedAt (ISO-8601, UTC) -> epoch int
# ---------------------------------------------------------------------------


def parse_liquidity_added_at(value: Any) -> int | None:
    """Map a Birdeye ``liquidityAddedAt`` value to integer epoch SECONDS (UTC).

    Robust to the several shapes Birdeye (and any upstream tweak) may send:

      - ISO-8601 string WITHOUT offset  ("2026-06-19T07:17:33")  -> treated as UTC.
      - ISO-8601 string WITH trailing Z ("2026-06-19T07:17:33Z") -> UTC.
      - ISO-8601 string WITH an explicit offset ("...+00:00", "...-05:00").
      - A numeric epoch (int/float, or a numeric string)         -> passed through.

    Returns None when the value is missing or cannot be parsed (the caller then
    falls back to the injected clock's event-arrival time and logs a warning —
    it must NEVER crash the persistence chain).

    Pure function — no I/O, no network, no clock.

    Args:
        value: The raw ``liquidityAddedAt`` (or any timestamp) value.

    Returns:
        Integer epoch seconds (UTC), or None if unparseable.
    """
    if value is None:
        return None

    # Numeric epoch passthrough (int/float, or numeric string).
    if isinstance(value, bool):  # guard: bool is an int subclass — reject it.
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string passthrough ("1750000000" / "1750000000.0").
        try:
            return int(float(s))
        except ValueError:
            pass
        # ISO-8601 parse.  Python's fromisoformat handles offsets; normalise a
        # trailing 'Z' to '+00:00' (fromisoformat rejects 'Z' before 3.11).
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
        # Naive timestamps (no offset) are UTC per the Birdeye contract.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    return None


# ---------------------------------------------------------------------------
# Pure frame mapper — raw Birdeye new-listing payload -> MEME_DATA graduation
# ---------------------------------------------------------------------------


def map_new_listing_frame(
    parsed: dict,
    *,
    data_type: str,
    event_source: str,
    dex_allowlist: frozenset[str],
    fallback_epoch: int,
) -> dict | None:
    """Map a raw Birdeye WebSocket frame to a MEME_DATA graduation event, or None.

    Returns None for any frame that is not a *pump.fun* graduation new-listing
    frame: WELCOME/ack/error frames, wrong type, missing mint, OR a non-pump.fun
    DEX source (the latter is debug-logged as a drop).  The returned dict is
    shaped EXACTLY as DetectionConsumer._persist_graduation_sync expects.

    Field mapping (real Birdeye TOKEN_NEW_LISTING_DATA frame -> event):
      data.address          -> address      (the mint; required)
      (no pool field)        -> poolAddress  (always None — Birdeye sends none)
      data.liquidityAddedAt -> blockTime     (ISO-8601 UTC -> epoch int)
      <stamped>             -> source        (config-driven; e.g. "pump_dot_fun")
      data.source           -> dex           (raw DEX, e.g. "pump_amm"; audit)
      data (verbatim)        -> raw           (preserves the raw "source")

    pump.fun filter: ``data.source`` must be in ``dex_allowlist`` (default
    {"pump_amm"}); other DEXes are dropped (debug-logged).

    Timestamp robustness: ``liquidityAddedAt`` (with/without Z/offset) and a
    numeric epoch are both accepted via parse_liquidity_added_at.  When it is
    missing/unparseable, ``blockTime`` falls back to ``fallback_epoch`` (the
    caller's injected-clock event-arrival time) and a WARNING is logged — the
    event is STILL emitted (never dropped, never a crash).

    Pure function — no I/O, no network, no clock (``fallback_epoch`` is injected).

    Args:
        parsed:         A JSON-parsed inbound frame dict.
        data_type:      The frame ``type`` string that marks a graduation payload.
        event_source:   The ``source`` value to stamp on the emitted event.
        dex_allowlist:  Raw ``data.source`` values that count as a pump.fun grad.
        fallback_epoch: Epoch seconds to use when liquidityAddedAt is unparseable.

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

    # --- BUG 1 fix: pump.fun-only filter on the RAW DEX source -------------
    raw_dex = payload.get("source")
    if raw_dex not in dex_allowlist:
        logger.debug(
            "[FIREHOSE] graduation drop: mint=%s dex_source=%r not in allowlist=%s",
            mint, raw_dex, sorted(dex_allowlist),
        )
        return None

    # --- BUG 4: Birdeye new-listing frames carry NO pool address ----------
    # Tolerate the several legacy spellings for safety, but default to None so
    # downstream knows there is genuinely no pool (not an empty placeholder).
    pool = (
        payload.get("poolAddress")
        or payload.get("pairAddress")
        or payload.get("pool")
        or None
    )

    # --- BUG 3 fix: map liquidityAddedAt (ISO-8601, UTC) -> epoch int ------
    raw_ts = (
        payload.get("liquidityAddedAt")
        or payload.get("blockUnixTime")
        or payload.get("blockTime")
        or payload.get("liquidityAddedTime")
    )
    block_time = parse_liquidity_added_at(raw_ts)
    if block_time is None:
        logger.warning(
            "[FIREHOSE] graduation: unparseable/missing timestamp for mint=%s "
            "(raw=%r) — falling back to event-arrival time %d",
            mint, raw_ts, fallback_epoch,
        )
        block_time = fallback_epoch

    # --- BUG 2 fix: stamp the source the DetectionConsumer filter expects --
    # while preserving the RAW dex under `dex` and verbatim under `raw`.
    event: dict[str, Any] = {
        "type": "MEME_DATA",
        "graduated": True,
        "address": mint,
        "poolAddress": pool,
        "blockTime": block_time,
        "source": event_source,
        "dex": raw_dex,
        "raw": payload,
    }
    return event


# ---------------------------------------------------------------------------
# Concrete DataSource — Birdeye new-listing (graduation) stream
# ---------------------------------------------------------------------------


class BirdeyeGraduationSource(DataSource):
    """Live graduation-event source backed by the Birdeye new-listing WebSocket.

    Subscribes to Birdeye's meme-platform / pump.fun new-listing stream and
    yields MEME_DATA graduation events shaped for DetectionConsumer.  The
    subscribe message type, the inbound data-frame type, the raw-DEX allow-list,
    and the stamped event source are all config-driven (Principle #1) with sane
    defaults.

    Args:
        api_key: Birdeye API key (x-api-key query param).
        config:  The active config's raw ``detection`` section dict (or None).
                 Read defensively for the graduation_* tuning keys (see module
                 docstring).  Passing None uses the baked-in defaults.
        clock:   Injected Clock (Principle #7 / AC-2.2).  Used ONLY to derive the
                 event-arrival fallback epoch when a frame's timestamp is
                 missing/unparseable — keeps the module free of datetime.now()/
                 time.time() and keeps tests deterministic.  Defaults to
                 WallClock() for production wiring convenience.

    Note:
        ``websockets``/``ssl``/``certifi`` are imported lazily inside connect()
        to keep import-time free of side effects (US-2 static-analysis guard /
        no network at import).
    """

    def __init__(
        self,
        api_key: str,
        config: dict | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._api_key: str = api_key
        self._config: dict = config or {}
        self._clock: Clock = clock or WallClock()
        self._ws: Any = None
        self._data_type: str = _expected_data_type(self._config)
        self._event_source: str = _event_source(self._config)
        self._dex_allowlist: frozenset[str] = _dex_allowlist(self._config)

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
            "[FIREHOSE] graduation-source subscribed type=%s expecting_frame=%s "
            "source=%s dex_allowlist=%s",
            build_subscribe_message(self._config)["type"],
            self._data_type,
            self._event_source,
            sorted(self._dex_allowlist),
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts from the Birdeye new-listing stream.

        Each raw WebSocket message is JSON-parsed and logged at DEBUG level (so
        the exact live frame format stays confirmable), then mapped via
        map_new_listing_frame.  Non-pump.fun frames (WELCOME/ack/error, wrong
        type, missing mint, non-allowlisted DEX) yield nothing.

        The event-arrival fallback epoch is read from the injected clock here
        (NOT inside the pure mapper) so the mapper stays pure/deterministic.

        Yields nothing if the connection was never opened (``self._ws is None``).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            # Debug-log the RAW frame so the live format is confirmable.
            logger.debug("[FIREHOSE] graduation raw-frame: %s", raw_message)
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            fallback_epoch = int(self._clock.now().timestamp())
            event = map_new_listing_frame(
                parsed,
                data_type=self._data_type,
                event_source=self._event_source,
                dex_allowlist=self._dex_allowlist,
                fallback_epoch=fallback_epoch,
            )
            if event is not None:
                yield event
