# ---
# module: core.tape.birdeye_graduation_source
# sprint: sprint-14
# story: live-firehose-spine, hotfix-graduation-pumpfun-mapping, US-76 AC-1
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: core.datasource, core.clock, datetime, json, logging, typing, websockets (lazy)
# ---
"""BirdeyeGraduationSource — concrete DataSource for Birdeye's SUBSCRIBE_MEME stream.

Subscribes to Birdeye's ``SUBSCRIBE_MEME`` stream (filtered to ``graduated:true``
+ ``source:pump_dot_fun``) and emits one MEME_DATA graduation event per mint on
the ``graduated`` false→true transition, shaped EXACTLY as DetectionConsumer
expects:

    {
        "type":              "MEME_DATA",
        "graduated":         True,
        "address":           <mint>,                    # = data.address
        "source":            "pump_dot_fun",            # stamped; config-driven
        "poolAddress":       <pool> | None,             # meme_info.pool.address
        "blockTime":         <epoch int>,               # meme_info.graduated_time
        "graduated_block_time": <epoch int>,            # same as blockTime
        "creation_time":     <epoch int>,               # meme_info.creation_time
        "progress_percent":  <float>,                   # meme_info.progress_percent
        "decimals":          <int>,                     # data.decimals (top-level)
        "raw":               <full data dict>,          # verbatim
    }

REAL FRAME SHAPE (from 6 captured SUBSCRIBE_MEME frames, 2026-06-20)
======================================================================
    {"type": "MEME_DATA", "data": {
        "eventType": "meme_stats",
        "address":   <mint>,       # top-level — the mint address
        "decimals":  6,            # top-level — token decimals (pump.fun = 6)
        ...stats...,
        "meme_info": {
            "source":          "pump_dot_fun",   # platform; NOT "pump_amm"
            "creation_time":   <unix s>,
            "creator":         <wallet>,
            "graduated":       false,            # or true on graduation
            "graduated_time":  null,             # or unix s when graduated
            "progress_percent": <0-100 float>,
            "pool": {
                "address": <pool_pubkey>,
                "realSolReserves": "...",
                ...
            },
            ...
        },
    }}

NOTE: unlike the old TOKEN_NEW_LISTING_DATA frame:
  - ``data.address`` is the MINT (not a pool).
  - ``data.decimals`` is at the top level of ``data`` (NOT in meme_info).
  - ``meme_info.source`` is "pump_dot_fun" for pump.fun (NOT "pump_amm").
  - ``meme_info.pool.address`` carries the pool address.
  - ``meme_info.graduated_time`` is a unix epoch int (or null when not yet grad).
  - The stream is CONTINUOUS meme-stats updates; a single mint appears many times
    with graduated=false until it graduates (graduated→true).

SUBSCRIBE_MEME FILTER (source=pump_dot_fun)
==========================================
The subscription payload is:
    {"type":"SUBSCRIBE_MEME","data":{"graduated":true,"source":"pump_dot_fun"}}
Birdeye filters the stream server-side. We also check ``meme_info.source`` on
arrival to be safe (defensive client-side validation).

CURVE-LIFE GATE
===============
After mapping a graduated frame, we require:
    graduated_time - creation_time >= CURVE_LIFE_MIN_S  (60 seconds)
Tokens failing this gate are skipped and the ``instant_skipped`` counter on the
source is incremented.  The counter is the live instant-rate monitor.

DEDUPLICATION
=============
We emit at most ONE event per mint.  A ``_seen_mints`` set tracks all mints for
which a graduation event has been emitted in this session.  Repeat frames for the
same mint (Birdeye sends continuous meme_stats updates) are silently dropped
after the first graduation emit.

CONFIG-DRIVEN WIRING (Principle #1)
===================================
Read defensively from the active config's raw ``detection`` section dict (with
defaults baked in):

    detection.graduation_subscribe_type   (default "SUBSCRIBE_MEME")
    detection.graduation_data_type        (default "MEME_DATA")
    detection.graduation_subscribe_data   (default {"graduated":true,"source":"pump_dot_fun"})
    detection.event_source                (default "pump_dot_fun")

These are NOT in the pydantic DetectionConfig schema (which is closed); they are
read defensively from the raw detection dict so they can be tuned live without a
migration or schema change.

CLOCK INJECTION (Principle #7 / AC-2.2)
========================================
No ``datetime.now()`` / ``time.time()`` in ``core/``.  The injected Clock is used
only for the event-arrival fallback epoch when ``meme_info.graduated_time`` is
missing — keeps the mapper pure and tests deterministic.

ARCHITECTURAL CONSTRAINTS
==========================
- No import of LiveSource / ReplaySource / core.live_source / core.replay_source.
- No top-level ``import websockets`` — lazy import inside connect() only.
- No ``datetime.now()`` / ``time.time()`` calls anywhere in this file.
- NO network at import time.

Legacy compatibility
====================
``map_new_listing_frame`` and ``parse_liquidity_added_at`` are preserved for
backward-compatibility with the existing integration test
(``test_graduation_detection_integration_hotfix.py``).  They still work against
the old TOKEN_NEW_LISTING_DATA frame shape.

Lifecycle:
    source = BirdeyeGraduationSource(api_key=..., config=<detection dict>)
    await source.connect()
    async for event in source.events():
        ...                          # yields MEME_DATA graduation dicts
    await source.disconnect()
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

#: Subscription type for the SUBSCRIBE_MEME graduation stream (US-76 AC-1).
DEFAULT_SUBSCRIBE_TYPE: str = "SUBSCRIBE_MEME"

#: Inbound frame type carrying a MEME_DATA meme-stats payload.
DEFAULT_DATA_TYPE: str = "MEME_DATA"

#: Default stamped graduation event ``source`` — must equal the active config's
#: detection.filter.source for DetectionConsumer to persist the Token row.
DEFAULT_EVENT_SOURCE: str = "pump_dot_fun"

#: Default subscribe data payload for SUBSCRIBE_MEME (filter to graduated pump.fun).
DEFAULT_SUBSCRIBE_DATA: dict[str, Any] = {"graduated": True, "source": "pump_dot_fun"}

#: Minimum curve life (graduated_time - creation_time) in seconds for a token to
#: be considered a true pump.fun graduation.  Tokens below this threshold are
#: skipped (instant/<60s) and the instant_skipped counter is incremented.
CURVE_LIFE_MIN_S: int = 60

# Config keys read defensively from the raw detection dict (NOT in the closed
# pydantic schema, so they can be tuned live without a migration).
_CFG_SUBSCRIBE_TYPE = "graduation_subscribe_type"
_CFG_DATA_TYPE = "graduation_data_type"
_CFG_SUBSCRIBE_DATA = "graduation_subscribe_data"
_CFG_EVENT_SOURCE = "event_source"

# Legacy key — kept for backward compat but not used by the new MEME_DATA mapper.
_CFG_DEX_ALLOWLIST = "graduation_dex_allowlist"
DEFAULT_DEX_ALLOWLIST: tuple[str, ...] = ("pump_amm",)


def build_subscribe_message(config: dict | None) -> dict:
    """Build the typed SUBSCRIBE_MEME message dict (config-driven, with defaults).

    For SUBSCRIBE_MEME the subscribe data MUST include ``{"graduated":true,
    "source":"pump_dot_fun"}`` to filter server-side.  The defaults bake this in;
    a ``graduation_subscribe_data`` override replaces it entirely.

    Pure function — no I/O, no network, no clock.

    Args:
        config: The active config's raw detection section dict, or None.

    Returns:
        A JSON-serializable subscribe message dict.
    """
    cfg = config or {}
    sub_type = cfg.get(_CFG_SUBSCRIBE_TYPE) or DEFAULT_SUBSCRIBE_TYPE
    extra_data = cfg.get(_CFG_SUBSCRIBE_DATA)
    if isinstance(extra_data, dict):
        data: dict[str, Any] = dict(extra_data)
    else:
        data = dict(DEFAULT_SUBSCRIBE_DATA)
    return {"type": sub_type, "data": data}


def _expected_data_type(config: dict | None) -> str:
    """Return the inbound frame ``type`` string that carries a meme-stats payload."""
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
    """Return the set of raw DEX sources (legacy compat, used by old mapper)."""
    cfg = config or {}
    raw = cfg.get(_CFG_DEX_ALLOWLIST)
    if isinstance(raw, (list, tuple)) and raw:
        return frozenset(str(x) for x in raw)
    return frozenset(DEFAULT_DEX_ALLOWLIST)


# ---------------------------------------------------------------------------
# Pure timestamp parser — Birdeye liquidityAddedAt (ISO-8601, UTC) -> epoch int
# (Kept for backward compat with old TOKEN_NEW_LISTING_DATA tests.)
# ---------------------------------------------------------------------------


def parse_liquidity_added_at(value: Any) -> int | None:
    """Map a Birdeye ``liquidityAddedAt`` value to integer epoch SECONDS (UTC).

    Robust to several shapes:
      - ISO-8601 string WITHOUT offset  ("2026-06-19T07:17:33")  -> treated as UTC.
      - ISO-8601 string WITH trailing Z ("2026-06-19T07:17:33Z") -> UTC.
      - ISO-8601 string WITH an explicit offset.
      - A numeric epoch (int/float, or a numeric string).

    Returns None when the value is missing or cannot be parsed.

    Pure function — no I/O, no network, no clock.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            pass
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    return None


# ---------------------------------------------------------------------------
# NEW pure frame mapper — MEME_DATA (SUBSCRIBE_MEME shape) -> graduation event
# ---------------------------------------------------------------------------


def map_meme_data_frame(
    parsed: dict,
    *,
    data_type: str,
    event_source: str,
    fallback_epoch: int,
) -> dict | None:
    """Map a raw Birdeye MEME_DATA frame to a graduation event dict, or None.

    Returns None for any frame that should NOT trigger a graduation event:
      - Wrong ``type`` (WELCOME, ack, TOKEN_NEW_LISTING_DATA, etc.)
      - ``data`` not a dict
      - Missing ``data.address`` (the mint)
      - ``meme_info`` missing or not a dict
      - ``meme_info.source != "pump_dot_fun"`` (non-pump.fun platform)
      - ``meme_info.graduated != True`` (token still on bonding curve)
      - ``meme_info.graduated_time`` missing / not a positive number

    Does NOT check the curve-life gate or the dedupe seen-set — those are
    applied by the caller (``BirdeyeGraduationSource.events()``) so that the
    ``instant_skipped`` counter and the seen-set remain in the stateful object,
    not this pure function.

    Field mapping (real MEME_DATA frame → emitted event):
      data.address                → address              (the mint)
      data.decimals               → decimals             (top-level on data)
      meme_info.graduated_time    → blockTime            (epoch int; compat alias)
      meme_info.graduated_time    → graduated_block_time (canonical new field)
      meme_info.creation_time     → creation_time        (epoch int)
      meme_info.progress_percent  → progress_percent     (float)
      meme_info.pool.address      → poolAddress          (pool pubkey or None)
      <stamped>                   → source               ("pump_dot_fun")
      <always True>               → graduated            (True)
      data (verbatim)             → raw                  (for audit)

    Args:
        parsed:         JSON-parsed inbound frame dict.
        data_type:      Frame ``type`` string that marks a meme-stats payload.
        event_source:   ``source`` value to stamp on the emitted event.
        fallback_epoch: Epoch seconds to use when graduated_time is missing.

    Returns:
        A graduation event dict, or None to skip the frame.
    """
    if not isinstance(parsed, dict) or parsed.get("type") != data_type:
        return None

    payload = parsed.get("data")
    if not isinstance(payload, dict):
        return None

    mint = payload.get("address")
    if not mint:
        return None

    meme_info = payload.get("meme_info")
    if not isinstance(meme_info, dict):
        return None

    # Require pump_dot_fun source (client-side safety check).
    if meme_info.get("source") != "pump_dot_fun":
        logger.debug(
            "[FIREHOSE] graduation drop: mint=%s meme_info.source=%r (not pump_dot_fun)",
            mint,
            meme_info.get("source"),
        )
        return None

    # Only emit when the token has actually graduated.
    if not meme_info.get("graduated"):
        return None

    # graduated_time is required for the anchor; fall back to injected clock.
    raw_grad_time = meme_info.get("graduated_time")
    if raw_grad_time is not None and isinstance(raw_grad_time, (int, float)) and not isinstance(raw_grad_time, bool):
        graduated_time = int(raw_grad_time)
    else:
        logger.warning(
            "[FIREHOSE] graduation: missing/invalid graduated_time for mint=%s "
            "(raw=%r) — falling back to event-arrival time %d",
            mint,
            raw_grad_time,
            fallback_epoch,
        )
        graduated_time = fallback_epoch

    creation_time_raw = meme_info.get("creation_time")
    if (
        creation_time_raw is not None
        and isinstance(creation_time_raw, (int, float))
        and not isinstance(creation_time_raw, bool)
    ):
        creation_time = int(creation_time_raw)
    else:
        creation_time = 0

    progress_percent = float(meme_info.get("progress_percent") or 0.0)

    # Pool address is inside meme_info.pool.address.
    pool_info = meme_info.get("pool")
    pool_address: str | None = None
    if isinstance(pool_info, dict):
        pool_address = pool_info.get("address") or None

    decimals_raw = payload.get("decimals")
    decimals: int | None = (
        int(decimals_raw)
        if isinstance(decimals_raw, (int, float)) and not isinstance(decimals_raw, bool)
        else None
    )

    event: dict[str, Any] = {
        "type": "MEME_DATA",
        "graduated": True,
        "address": mint,
        "source": event_source,
        "poolAddress": pool_address,
        # blockTime kept for DetectionConsumer compatibility (maps -> graduated_block_time).
        "blockTime": graduated_time,
        # New fields required by AC-1 and the locked contract (§8).
        "graduated_block_time": graduated_time,
        "creation_time": creation_time,
        "progress_percent": progress_percent,
        "decimals": decimals,
        "raw": payload,
    }
    return event


# ---------------------------------------------------------------------------
# LEGACY pure frame mapper — TOKEN_NEW_LISTING_DATA shape (preserved for compat)
# ---------------------------------------------------------------------------


def map_new_listing_frame(
    parsed: dict,
    *,
    data_type: str,
    event_source: str,
    dex_allowlist: frozenset[str],
    fallback_epoch: int,
) -> dict | None:
    """Map a raw Birdeye TOKEN_NEW_LISTING_DATA frame to a MEME_DATA graduation event.

    LEGACY — preserved for backward compatibility with
    ``test_graduation_detection_integration_hotfix.py``.  New code should use
    ``map_meme_data_frame`` against the MEME_DATA / SUBSCRIBE_MEME stream.

    Returns None for any frame that is not a pump.fun graduation new-listing frame.
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

    raw_dex = payload.get("source")
    if raw_dex not in dex_allowlist:
        logger.debug(
            "[FIREHOSE] graduation drop: mint=%s dex_source=%r not in allowlist=%s",
            mint, raw_dex, sorted(dex_allowlist),
        )
        return None

    pool = (
        payload.get("poolAddress")
        or payload.get("pairAddress")
        or payload.get("pool")
        or None
    )

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
# Concrete DataSource — Birdeye SUBSCRIBE_MEME (graduation) stream
# ---------------------------------------------------------------------------


class BirdeyeGraduationSource(DataSource):
    """Live graduation-event source backed by the Birdeye SUBSCRIBE_MEME WebSocket.

    Subscribes to Birdeye's SUBSCRIBE_MEME stream (filtered to graduated pump.fun
    tokens) and yields MEME_DATA graduation events shaped for DetectionConsumer.
    One event is emitted per mint on the graduated false→true transition only
    (deduplicated by ``_seen_mints``).

    Curve-life gate: tokens with ``graduated_time - creation_time < CURVE_LIFE_MIN_S``
    (60 s) are silently skipped and the ``instant_skipped`` counter is incremented.

    Args:
        api_key: Birdeye API key (x-api-key query param).
        config:  The active config's raw ``detection`` section dict (or None).
        clock:   Injected Clock (Principle #7 / AC-2.2).  Defaults to WallClock().

    Attributes:
        instant_skipped: Count of graduation frames skipped because
                         graduated_time - creation_time < CURVE_LIFE_MIN_S.
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
        # Dedupe: tracks mints for which a graduation event has been emitted this session.
        self._seen_mints: set[str] = set()
        # Live instant-rate monitor (the AC-1 curve-life gate counter).
        self.instant_skipped: int = 0

    async def connect(self) -> None:
        """Open the Birdeye WebSocket and send the SUBSCRIBE_MEME message."""
        import ssl

        import websockets

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
        sub = build_subscribe_message(self._config)
        logger.info(
            "[FIREHOSE] graduation-source subscribed type=%s data=%s expecting_frame=%s source=%s",
            sub["type"],
            sub["data"],
            self._data_type,
            self._event_source,
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts from the Birdeye SUBSCRIBE_MEME stream.

        For each inbound frame:
          1. JSON-parse and debug-log.
          2. Map via ``map_meme_data_frame``; skip non-graduation frames.
          3. Skip already-seen mints (dedupe).
          4. Apply curve-life gate: skip + count if graduated_time - creation_time < 60s.
          5. Mark mint as seen and yield the event.

        Yields nothing if the connection was never opened (``self._ws is None``).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            logger.debug("[FIREHOSE] graduation raw-frame: %s", raw_message)
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            fallback_epoch = int(self._clock.now().timestamp())
            event = map_meme_data_frame(
                parsed,
                data_type=self._data_type,
                event_source=self._event_source,
                fallback_epoch=fallback_epoch,
            )
            if event is None:
                continue

            mint = event["address"]

            # Dedupe: emit at most once per mint per session.
            if mint in self._seen_mints:
                logger.debug("[FIREHOSE] graduation dedupe: mint=%s already emitted", mint)
                continue

            # Curve-life gate: skip instant (<60s) graduations.
            curve_life = event["graduated_block_time"] - event["creation_time"]
            if curve_life < CURVE_LIFE_MIN_S:
                self.instant_skipped += 1
                logger.info(
                    "[FIREHOSE] graduation instant-skip: mint=%s curve_life=%ds "
                    "(<%ds) instant_skipped=%d",
                    mint,
                    curve_life,
                    CURVE_LIFE_MIN_S,
                    self.instant_skipped,
                )
                continue

            self._seen_mints.add(mint)
            yield event
