# ---
# module: core.tape.birdeye_graduation_source
# sprint: sprint-14
# story: live-firehose-spine, hotfix-graduation-pumpfun-mapping, US-76 AC-1,
#        hotfix-new-pair-graduation-detection
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: core.datasource, core.clock, datetime, json, logging, typing, websockets (lazy)
# ---
"""BirdeyeGraduationSource — concrete DataSource for Birdeye's SUBSCRIBE_NEW_PAIR stream.

Subscribes to Birdeye's ``SUBSCRIBE_NEW_PAIR`` stream and emits one graduation
event per mint on every ``NEW_PAIR_DATA`` frame where ``data.source=="pump_amm"``
(pump.fun graduation = PumpSwap pool creation), shaped EXACTLY as DetectionConsumer
expects:

    {
        "type":                 "MEME_DATA",        # kept for DetectionConsumer compat
        "graduated":            True,
        "address":              <mint>,             # SPL mint (NOT the pool address)
        "source":               "pump_dot_fun",     # stamped; config-driven (event_source)
        "poolAddress":          <pool>,             # data.address (the PumpSwap pair)
        "blockTime":            <epoch int>,        # parsed from ISO data.blockTime
        "graduated_block_time": <epoch int>,        # same as blockTime
        "creation_time":        0,                  # absent in NEW_PAIR; zero-filled
        "progress_percent":     0.0,                # absent in NEW_PAIR; zero-filled
        "decimals":             <int>,              # decimals of the non-SOL/USDC side
        "raw":                  <data dict>,        # verbatim data payload
    }

REAL FRAME SHAPE (from 6 captured SUBSCRIBE_NEW_PAIR frames, 2026-06-21)
=========================================================================
    {"type":"NEW_PAIR_DATA","data":{
        "address":"G8Xa6y8rhbbL54eLKYVFoxGr8U8dNQvghRmdwM27Vov",  // pool address
        "name":"SOL-OILPEPE","source":"pump_amm",
        "base":{"address":"So111...112","symbol":"SOL","decimals":9},
        "quote":{"address":"DKUJ4hP...","symbol":"OILPEPE","decimals":6},
        "txHash":"...","liquidity":7300.08,"blockTime":"2026-06-21T09:03:06"}}

CRITICAL field rules (verified from 6 real frames):
  - SOL can be on EITHER ``base`` or ``quote``.
  - The graduated mint = the base/quote side whose address is NOT SOL and NOT USDC.
    SOL  = So11111111111111111111111111111111111111112
    USDC = EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  - decimals = the decimals of that NON-SOL/USDC token side (e.g. OILPEPE=6, BASS=9).
  - graduated_block_time = parse the ISO ``blockTime`` string to epoch seconds.
  - pool address = ``data.address`` (the PumpSwap pair).
  - There is NO creation_time and NO progress_percent in NEW_PAIR_DATA; zero-filled.

SOURCE FILTER SPLIT (deliberate asymmetry)
==========================================
  - Birdeye frame ``data.source`` == "pump_amm"  → filter condition (NEW_PAIR origin)
  - Emitted event ``source``     == "pump_dot_fun" → stamp value (matches config filter)
These are intentionally different: ``pump_amm`` identifies the DEX in the Birdeye
stream; ``pump_dot_fun`` is what ``DetectionConsumer._is_graduation_event`` matches
against ``config.detection.filter.source``.  The split mirrors the old MEME_DATA mapper.

SUBSCRIBE MESSAGE
=================
    {"type":"SUBSCRIBE_NEW_PAIR"}
Birdeye supports OPTIONAL server-side ``min_liquidity``/``max_liquidity`` int fields
inside the subscribe payload.  A permissive default (min_liquidity=0) is used so
every graduation is captured.  The operator can tighten via config, but should be
aware that raising the floor risks missing low-liquidity legitimate graduations
(the curve can exit with ≈$7k liquidity and still score well).

MIN_LIQUIDITY FLOOR (config-driven, default 0)
===============================================
``min_liquidity`` controls both the server-side subscribe payload AND a client-side
belt-and-suspenders drop.  The server-side parameter reduces traffic; the client-side
check is a safety net in case Birdeye ignores the parameter.  Set to 0 to capture
every graduation; only raise if firehose credit burn becomes a concern.

BELOW_MIN_LIQUIDITY COUNTER (replaces instant_skipped)
=======================================================
``below_min_liquidity`` counts frames dropped by the min_liquidity floor (replaces
the old ``instant_skipped`` curve-life counter, which required creation_time absent
in NEW_PAIR_DATA).  Callers that previously read ``instant_skipped`` should switch to
``below_min_liquidity``; ``instant_skipped`` is preserved as a deprecated alias so
existing callers do not crash.

DEDUPLICATION
=============
We emit at most ONE event per MINT per session.  A ``_seen_mints`` set tracks all
mints for which a graduation event has been emitted.  Real frames show duplicate token
NAMES across different mints, so dedupe is on the MINT ADDRESS, never the name.

CONFIG-DRIVEN WIRING (Principle #1)
===================================
Read defensively from the active config's raw ``detection`` section dict (with defaults
baked in); NOT in the closed pydantic DetectionConfig schema:

    detection.graduation_subscribe_type    (default "SUBSCRIBE_NEW_PAIR")
    detection.graduation_data_type         (default "NEW_PAIR_DATA")
    detection.graduation_new_pair_source   (default "pump_amm")
    detection.event_source                 (default "pump_dot_fun")
    detection.graduation_min_liquidity     (default 0, permissive — capture all)

CLOCK INJECTION (Principle #7 / AC-2.2)
========================================
No ``datetime.now()`` / ``time.time()`` in ``core/``.  The injected Clock is used
only for the event-arrival fallback epoch when ``blockTime`` is missing/unparseable.

ARCHITECTURAL CONSTRAINTS
==========================
- No import of LiveSource / ReplaySource / core.live_source / core.replay_source.
- No top-level ``import websockets`` — lazy import inside connect() only.
- No ``datetime.now()`` / ``time.time()`` calls anywhere in this file.
- NO network at import time.

Legacy compatibility
====================
``map_new_listing_frame``, ``map_meme_data_frame``, ``CURVE_LIFE_MIN_S``,
``DEFAULT_DATA_TYPE``, ``DEFAULT_SUBSCRIBE_DATA``, and ``parse_liquidity_added_at``
are preserved for backward-compatibility with existing tests that exercise the old
MEME_DATA / SUBSCRIBE_MEME path.  The live BirdeyeGraduationSource now uses the
NEW_PAIR mapper exclusively.

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
# Well-known mint addresses — used to identify the non-SOL/USDC side
# ---------------------------------------------------------------------------

_SOL_MINT: str = "So11111111111111111111111111111111111111112"
_USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ---------------------------------------------------------------------------
# NEW_PAIR config-driven defaults (Principle #1)
# ---------------------------------------------------------------------------

#: Subscription type for the NEW_PAIR graduation stream.
DEFAULT_SUBSCRIBE_TYPE: str = "SUBSCRIBE_NEW_PAIR"

#: Inbound frame type carrying a new-pair payload.
DEFAULT_DATA_TYPE: str = "NEW_PAIR_DATA"

#: Default stamped graduation event ``source`` — must equal the active config's
#: detection.filter.source for DetectionConsumer to persist the Token row.
DEFAULT_EVENT_SOURCE: str = "pump_dot_fun"

#: Birdeye ``data.source`` value that identifies pump.fun (PumpSwap) pairs.
#: FILTER condition — NOT the same as the stamped event_source above.
DEFAULT_NEW_PAIR_SOURCE: str = "pump_amm"

#: Default min_liquidity floor (0 = capture every graduation).
#: Raising this risks missing low-liquidity genuine graduations (see module doc).
DEFAULT_MIN_LIQUIDITY: int = 0

# ---------------------------------------------------------------------------
# LEGACY MEME_DATA defaults — preserved for backward compat with existing tests
# ---------------------------------------------------------------------------

#: Legacy subscribe data payload for SUBSCRIBE_MEME (preserved for test compat).
DEFAULT_SUBSCRIBE_DATA: dict[str, Any] = {"graduated": True, "source": "pump_dot_fun"}

#: Minimum curve life (graduated_time - creation_time) for MEME_DATA path (legacy).
#: Preserved so existing tests that import this name do not crash.
CURVE_LIFE_MIN_S: int = 60

# Config keys read defensively from the raw detection dict.
_CFG_SUBSCRIBE_TYPE = "graduation_subscribe_type"
_CFG_DATA_TYPE = "graduation_data_type"
_CFG_SUBSCRIBE_DATA = "graduation_subscribe_data"
_CFG_EVENT_SOURCE = "event_source"
_CFG_NEW_PAIR_SOURCE = "graduation_new_pair_source"
_CFG_MIN_LIQUIDITY = "graduation_min_liquidity"

# Legacy key — kept for backward compat.
_CFG_DEX_ALLOWLIST = "graduation_dex_allowlist"
DEFAULT_DEX_ALLOWLIST: tuple[str, ...] = ("pump_amm",)


def build_subscribe_message(config: dict | None) -> dict:
    """Build the typed SUBSCRIBE_NEW_PAIR message dict (config-driven, with defaults).

    For ``SUBSCRIBE_NEW_PAIR`` the server-side ``min_liquidity`` filter is included
    when non-zero.  A config override of ``graduation_subscribe_data`` replaces the
    entire data payload.

    Pure function — no I/O, no network, no clock.

    Args:
        config: The active config's raw detection section dict, or None.

    Returns:
        A JSON-serializable subscribe message dict.
    """
    cfg = config or {}
    sub_type = cfg.get(_CFG_SUBSCRIBE_TYPE) or DEFAULT_SUBSCRIBE_TYPE

    # If a full override is provided, use it verbatim.
    extra_data = cfg.get(_CFG_SUBSCRIBE_DATA)
    if isinstance(extra_data, dict):
        return {"type": sub_type, "data": dict(extra_data)}

    # Build the NEW_PAIR subscribe payload.
    min_liq = cfg.get(_CFG_MIN_LIQUIDITY, DEFAULT_MIN_LIQUIDITY)
    if isinstance(min_liq, bool):
        min_liq = DEFAULT_MIN_LIQUIDITY
    try:
        min_liq = int(min_liq)
    except (TypeError, ValueError):
        min_liq = DEFAULT_MIN_LIQUIDITY

    if min_liq > 0:
        data: dict[str, Any] = {"min_liquidity": min_liq}
    else:
        data = {}

    return {"type": sub_type, "data": data}


def _expected_data_type(config: dict | None) -> str:
    """Return the inbound frame ``type`` string that carries a new-pair payload."""
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


def _min_liquidity(config: dict | None) -> int:
    """Return the min_liquidity client-side floor from config (default 0)."""
    cfg = config or {}
    raw = cfg.get(_CFG_MIN_LIQUIDITY, DEFAULT_MIN_LIQUIDITY)
    if isinstance(raw, bool):
        return DEFAULT_MIN_LIQUIDITY
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MIN_LIQUIDITY


def _dex_allowlist(config: dict | None) -> frozenset[str]:
    """Return the set of raw DEX sources (legacy compat, used by old mapper)."""
    cfg = config or {}
    raw = cfg.get(_CFG_DEX_ALLOWLIST)
    if isinstance(raw, (list, tuple)) and raw:
        return frozenset(str(x) for x in raw)
    return frozenset(DEFAULT_DEX_ALLOWLIST)


def _new_pair_source(config: dict | None) -> str:
    """Return the Birdeye NEW_PAIR ``data.source`` value to filter graduations on.

    Default "pump_amm" (PumpSwap, the sole pump.fun graduation destination).
    Config-driven so the filter can be retuned live if Birdeye ever relabels the
    PumpSwap DEX, without a code change/redeploy.
    """
    cfg = config or {}
    raw = cfg.get(_CFG_NEW_PAIR_SOURCE)
    if isinstance(raw, str) and raw:
        return raw
    return DEFAULT_NEW_PAIR_SOURCE


# ---------------------------------------------------------------------------
# Pure timestamp parser — Birdeye blockTime (ISO-8601, UTC) -> epoch int
# (Also used by legacy TOKEN_NEW_LISTING_DATA tests as parse_liquidity_added_at)
# ---------------------------------------------------------------------------


def parse_liquidity_added_at(value: Any) -> int | None:
    """Map a Birdeye ``liquidityAddedAt`` or ``blockTime`` value to epoch SECONDS (UTC).

    Robust to several shapes:
      - ISO-8601 string WITHOUT offset  ("2026-06-21T09:03:06")  -> treated as UTC.
      - ISO-8601 string WITH trailing Z ("2026-06-21T09:03:06Z") -> UTC.
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
# NEW pure frame mapper — NEW_PAIR_DATA (SUBSCRIBE_NEW_PAIR shape) -> graduation event
# ---------------------------------------------------------------------------


def map_new_pair_frame(
    parsed: dict,
    *,
    event_source: str,
    min_liquidity: int,
    fallback_epoch: int,
    new_pair_source: str = DEFAULT_NEW_PAIR_SOURCE,
) -> dict | None:
    """Map a raw Birdeye NEW_PAIR_DATA frame to a graduation event dict, or None.

    Returns None for any frame that should NOT trigger a graduation event:
      - Wrong ``type`` (WELCOME, ack, MEME_DATA, etc.)
      - ``data`` not a dict
      - ``data.source != "pump_amm"`` (meteora_damm_v2, raydium, etc.)
      - Cannot identify a single non-SOL/USDC mint from base/quote
      - Below client-side min_liquidity floor

    Field mapping (real NEW_PAIR_DATA frame → emitted event):
      data.address            → poolAddress          (the PumpSwap pair/pool)
      base/quote non-SOL side → address              (the SPL mint = Token.mint key)
      base/quote non-SOL side → decimals             (variable: 6 or 9 or other)
      parse(data.blockTime)   → blockTime            (epoch int; compat alias)
      parse(data.blockTime)   → graduated_block_time (canonical field)
      <zero>                  → creation_time        (absent in NEW_PAIR; 0)
      <zero>                  → progress_percent     (absent in NEW_PAIR; 0.0)
      <stamped>               → source               ("pump_dot_fun")
      <always True>           → graduated            (True)
      data (verbatim)         → raw                  (for audit)

    The emitted dict is EXACTLY the same shape DetectionConsumer expects from the
    old MEME_DATA path, so no downstream changes are needed.

    Args:
        parsed:        JSON-parsed inbound frame dict.
        event_source:  ``source`` value to stamp on the emitted event.
        min_liquidity: Client-side liquidity floor (belt-and-suspenders).
                       Frames below this are silently dropped. 0 = no floor.
        fallback_epoch: Epoch seconds to use when blockTime is missing/unparseable.

    Returns:
        A graduation event dict, or None to skip the frame.
    """
    if not isinstance(parsed, dict) or parsed.get("type") != "NEW_PAIR_DATA":
        return None

    payload = parsed.get("data")
    if not isinstance(payload, dict):
        return None

    # Filter to pump.fun (PumpSwap) pairs only (config-driven source, default pump_amm).
    if payload.get("source") != new_pair_source:
        logger.debug(
            "[FIREHOSE] new-pair drop: pool=%s source=%r (not %s)",
            payload.get("address"),
            payload.get("source"),
            new_pair_source,
        )
        return None

    # Client-side liquidity floor (belt-and-suspenders after server-side filter).
    if min_liquidity > 0:
        liq = payload.get("liquidity")
        if isinstance(liq, (int, float)) and not isinstance(liq, bool):
            if liq < min_liquidity:
                logger.debug(
                    "[FIREHOSE] new-pair drop: pool=%s liquidity=%.2f < min_liquidity=%d",
                    payload.get("address"),
                    liq,
                    min_liquidity,
                )
                return None

    # Identify the graduated mint = the base/quote side that is NOT SOL and NOT USDC.
    base = payload.get("base") or {}
    quote = payload.get("quote") or {}
    base_addr = base.get("address", "")
    quote_addr = quote.get("address", "")

    _skip = {_SOL_MINT, _USDC_MINT}
    base_is_skip = base_addr in _skip
    quote_is_skip = quote_addr in _skip

    if base_is_skip and not quote_is_skip:
        # SOL/USDC is on base → graduated mint is quote
        mint = quote_addr
        decimals_raw = quote.get("decimals")
    elif quote_is_skip and not base_is_skip:
        # SOL/USDC is on quote → graduated mint is base
        mint = base_addr
        decimals_raw = base.get("decimals")
    else:
        # Both or neither are SOL/USDC — cannot identify a unique graduated mint.
        logger.debug(
            "[FIREHOSE] new-pair drop: pool=%s cannot identify graduated mint "
            "(base=%s quote=%s)",
            payload.get("address"),
            base_addr,
            quote_addr,
        )
        return None

    if not mint:
        logger.debug(
            "[FIREHOSE] new-pair drop: pool=%s mint is empty",
            payload.get("address"),
        )
        return None

    # Parse decimals (variable: 6 for most pump.fun tokens, 9 for BASS etc.)
    if isinstance(decimals_raw, (int, float)) and not isinstance(decimals_raw, bool):
        decimals: int | None = int(decimals_raw)
    else:
        decimals = None

    # Parse ISO blockTime → epoch seconds.
    raw_block_time = payload.get("blockTime")
    graduated_time = parse_liquidity_added_at(raw_block_time)
    if graduated_time is None:
        logger.warning(
            "[FIREHOSE] new-pair: missing/unparseable blockTime for mint=%s "
            "(raw=%r) — falling back to event-arrival time %d",
            mint,
            raw_block_time,
            fallback_epoch,
        )
        graduated_time = fallback_epoch

    pool_address: str | None = payload.get("address") or None

    event: dict[str, Any] = {
        "type": "MEME_DATA",           # DetectionConsumer checks type=="MEME_DATA"
        "graduated": True,
        "address": mint,               # SPL mint — Token.mint key + score-join key
        "source": event_source,        # stamped "pump_dot_fun" — matches filter
        "poolAddress": pool_address,   # PumpSwap pair address (reliably present now)
        # blockTime kept for DetectionConsumer compat (maps -> graduated_block_time).
        "blockTime": graduated_time,
        "graduated_block_time": graduated_time,
        # Absent in NEW_PAIR; zero-filled so downstream readers don't KeyError.
        "creation_time": 0,
        "progress_percent": 0.0,
        "decimals": decimals,
        "raw": payload,
    }
    return event


# ---------------------------------------------------------------------------
# LEGACY pure frame mapper — MEME_DATA (SUBSCRIBE_MEME shape) -> graduation event
# (preserved for backward compat with test_birdeye_graduation_source_us76_ac1.py)
# ---------------------------------------------------------------------------


def map_meme_data_frame(
    parsed: dict,
    *,
    data_type: str,
    event_source: str,
    fallback_epoch: int,
) -> dict | None:
    """Map a raw Birdeye MEME_DATA frame to a graduation event dict, or None.

    LEGACY — preserved for backward compatibility with existing tests.
    The live BirdeyeGraduationSource now uses ``map_new_pair_frame`` against
    the SUBSCRIBE_NEW_PAIR / NEW_PAIR_DATA stream.

    Returns None for any frame that should NOT trigger a graduation event:
      - Wrong ``type``
      - ``data`` not a dict
      - Missing ``data.address`` (the mint)
      - ``meme_info`` missing or not a dict
      - ``meme_info.source != "pump_dot_fun"``
      - ``meme_info.graduated != True``
      - ``meme_info.graduated_time`` missing / not a positive number
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

    if meme_info.get("source") != "pump_dot_fun":
        logger.debug(
            "[FIREHOSE] graduation drop: mint=%s meme_info.source=%r (not pump_dot_fun)",
            mint,
            meme_info.get("source"),
        )
        return None

    if not meme_info.get("graduated"):
        return None

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
        "blockTime": graduated_time,
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
    ``map_new_pair_frame`` against the NEW_PAIR_DATA / SUBSCRIBE_NEW_PAIR stream.

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
# Concrete DataSource — Birdeye SUBSCRIBE_NEW_PAIR (graduation) stream
# ---------------------------------------------------------------------------


class BirdeyeGraduationSource(DataSource):
    """Live graduation-event source backed by the Birdeye SUBSCRIBE_NEW_PAIR WebSocket.

    Subscribes to Birdeye's SUBSCRIBE_NEW_PAIR stream and yields MEME_DATA graduation
    events shaped for DetectionConsumer.  One event is emitted per MINT on the
    NEW_PAIR_DATA frame where data.source=="pump_amm".

    Deduplication is by MINT ADDRESS (not token name — real frames show duplicate
    names across different mints).  The ``_seen_mints`` set tracks all mints for which
    a graduation event has been emitted in this session.

    Min-liquidity gate: frames below the configured ``min_liquidity`` floor are
    silently dropped and the ``below_min_liquidity`` counter is incremented.  The
    curve-life gate (which required creation_time, absent in NEW_PAIR) is REMOVED.

    Args:
        api_key: Birdeye API key (x-api-key query param).
        config:  The active config's raw ``detection`` section dict (or None).
        clock:   Injected Clock (Principle #7 / AC-2.2).  Defaults to WallClock().

    Attributes:
        below_min_liquidity: Count of frames dropped because data.liquidity was
                             below the configured min_liquidity floor.
        instant_skipped:     Deprecated alias for below_min_liquidity (kept so
                             existing callers do not crash).
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
        self._min_liquidity: int = _min_liquidity(self._config)
        self._new_pair_source: str = _new_pair_source(self._config)
        # Dedupe: tracks mints for which a graduation event has been emitted this session.
        self._seen_mints: set[str] = set()
        # Below-min-liquidity counter (replaces the old curve-life instant_skipped).
        self.below_min_liquidity: int = 0

    @property
    def instant_skipped(self) -> int:
        """Deprecated alias for below_min_liquidity.  Use below_min_liquidity instead."""
        return self.below_min_liquidity

    @instant_skipped.setter
    def instant_skipped(self, value: int) -> None:
        """Allow legacy test code to set instant_skipped (maps to below_min_liquidity)."""
        self.below_min_liquidity = value

    async def connect(self) -> None:
        """Open the Birdeye WebSocket and send the SUBSCRIBE_NEW_PAIR message."""
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
            "[FIREHOSE] graduation-source subscribed type=%s data=%s "
            "expecting_frame=%s source_stamp=%s min_liquidity=%d",
            sub["type"],
            sub["data"],
            self._data_type,
            self._event_source,
            self._min_liquidity,
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts from the Birdeye SUBSCRIBE_NEW_PAIR stream.

        For each inbound frame:
          1. JSON-parse and debug-log.
          2. Map via ``map_new_pair_frame`` (with min_liquidity=0 so we can count
             liq-drops separately below); skip non-graduation / non-pump frames.
          3. Apply client-side min_liquidity floor: drop + increment below_min_liquidity
             counter if the frame's liquidity is below the configured floor.
          4. Skip already-seen mints (dedupe by MINT ADDRESS, not by name).
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
            # Pass min_liquidity=0 here so the mapper never drops for liq reasons;
            # we apply the floor below so we can count drops accurately.
            event = map_new_pair_frame(
                parsed,
                event_source=self._event_source,
                min_liquidity=0,
                fallback_epoch=fallback_epoch,
                new_pair_source=self._new_pair_source,
            )
            if event is None:
                continue

            # Client-side min_liquidity floor (belt-and-suspenders): count drops.
            if self._min_liquidity > 0:
                payload = parsed.get("data") or {}
                liq = payload.get("liquidity")
                if isinstance(liq, (int, float)) and not isinstance(liq, bool):
                    if liq < self._min_liquidity:
                        self.below_min_liquidity += 1
                        logger.info(
                            "[FIREHOSE] graduation below-min-liq: mint=%s "
                            "liquidity=%.2f < min_liquidity=%d below_min_liquidity=%d",
                            event["address"],
                            liq,
                            self._min_liquidity,
                            self.below_min_liquidity,
                        )
                        continue

            mint = event["address"]

            # Dedupe: emit at most once per mint per session (by ADDRESS, not name).
            if mint in self._seen_mints:
                logger.debug("[FIREHOSE] graduation dedupe: mint=%s already emitted", mint)
                continue

            self._seen_mints.add(mint)
            yield event
