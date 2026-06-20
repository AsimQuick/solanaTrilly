# ---
# module: core.tests.test_birdeye_graduation_source_firehose
# sprint: sprint-14
# story: live-firehose-spine, hotfix-graduation-pumpfun-mapping
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, asyncio, ast, datetime, pathlib,
#               core.tape.birdeye_graduation_source, core.replay_source,
#               core.detection.consumer, core.clock
# ---
"""Offline tests for BirdeyeGraduationSource legacy TOKEN_NEW_LISTING_DATA mapper.

Tests the LEGACY ``map_new_listing_frame`` function (TOKEN_NEW_LISTING_DATA shape)
which is preserved for backward compatibility.  New graduation detection uses
``map_meme_data_frame`` (SUBSCRIBE_MEME / MEME_DATA) — see
``test_birdeye_graduation_source_us76_ac1.py``.

The frame shape and hotfixes tested here:

  BUG 1  pump.fun-only filter: only data.source in the allow-list (default
         ["pump_amm"]) is emitted; pancakeswap_v3 / meteora_damm_v2 / WELCOME
         yield nothing.
  BUG 2  source stamping: the emitted MEME_DATA event has source="pump_dot_fun"
         (the value detection.filter expects) so it passes DetectionConsumer,
         while the raw DEX "pump_amm" is preserved under raw (and a `dex` field).
  BUG 3  timestamp: liquidityAddedAt (ISO-8601, UTC, with/without Z/offset) maps
         to an INTEGER epoch; numeric passes through; bad/missing -> fallback +
         warning, never a crash.
  BUG 4  null pool: the frame has no pool -> poolAddress is None and the
         DetectionConsumer persists a Token row with an empty pool, no crash.

Plus the standing architectural guards (lazy imports, no wall-clock calls).

US-76 AC-1 note: DEFAULT_SUBSCRIBE_TYPE is now "SUBSCRIBE_MEME" and DEFAULT_DATA_TYPE
is now "MEME_DATA" (the live subscription was switched to SUBSCRIBE_MEME).  This file's
legacy tests use the literal "TOKEN_NEW_LISTING_DATA" data_type when calling
map_new_listing_frame, since that function is for the old frame shape.
"""
import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from core.clock import VirtualClock
from core.tape.birdeye_graduation_source import (
    DEFAULT_DEX_ALLOWLIST,
    DEFAULT_EVENT_SOURCE,
    BirdeyeGraduationSource,
    build_subscribe_message,
    map_new_listing_frame,
    parse_liquidity_added_at,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core" / "tape" / "birdeye_graduation_source.py"
)

# ---------------------------------------------------------------------------
# REAL observed Birdeye TOKEN_NEW_LISTING_DATA frames (legacy shape).
# map_new_listing_frame only accepts type=="TOKEN_NEW_LISTING_DATA".
# ---------------------------------------------------------------------------

_WELCOME_FRAME = {"type": "WELCOME", "data": None}

# The legacy data type string for map_new_listing_frame.
_LEGACY_DATA_TYPE = "TOKEN_NEW_LISTING_DATA"

# pump.fun graduation (WANT) — source == "pump_amm" (PumpSwap).
_PUMP_FRAME = {
    "type": _LEGACY_DATA_TYPE,
    "data": {
        "address": "6Y5PBgk9rVC9ycsVTxYi6nFtSytEBrYTPy3mL39Nwokn",
        "decimals": 9,
        "name": "dream",
        "source": "pump_amm",
        "symbol": "dream",
        "liquidity": 18640.85,
        "liquidityAddedAt": "2026-06-19T07:17:33",
    },
}

# NON-pump.fun graduations (DROP) — same shape, different DEX source.
_PANCAKE_FRAME = {
    "type": _LEGACY_DATA_TYPE,
    "data": {
        "address": "PancakeMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "decimals": 9,
        "name": "cake",
        "source": "pancakeswap_v3",
        "symbol": "cake",
        "liquidity": 1000.0,
        "liquidityAddedAt": "2026-06-19T07:18:00",
    },
}
_METEORA_FRAME = {
    "type": _LEGACY_DATA_TYPE,
    "data": {
        "address": "MeteoraMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "decimals": 9,
        "name": "meteor",
        "source": "meteora_damm_v2",
        "symbol": "meteor",
        "liquidity": 2000.0,
        "liquidityAddedAt": "2026-06-19T07:19:00",
    },
}

# The expected epoch for the pump frame's liquidityAddedAt (UTC).
_PUMP_EPOCH = int(
    datetime(2026, 6, 19, 7, 17, 33, tzinfo=timezone.utc).timestamp()
)

_T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
_FALLBACK_EPOCH = int(_T0.timestamp())
_ALLOW = frozenset(DEFAULT_DEX_ALLOWLIST)


def _map(frame, *, event_source=DEFAULT_EVENT_SOURCE, allow=_ALLOW, fallback=_FALLBACK_EPOCH):
    return map_new_listing_frame(
        frame,
        data_type=_LEGACY_DATA_TYPE,
        event_source=event_source,
        dex_allowlist=allow,
        fallback_epoch=fallback,
    )


# ---------------------------------------------------------------------------
# BUG 1 — pump.fun-only filter
# ---------------------------------------------------------------------------


def test_only_pump_amm_frame_is_emitted():
    """pump_amm yields a graduation; pancakeswap_v3 / meteora_damm_v2 / WELCOME do not."""
    assert _map(_PUMP_FRAME) is not None
    assert _map(_PANCAKE_FRAME) is None
    assert _map(_METEORA_FRAME) is None
    assert _map(_WELCOME_FRAME) is None


def test_dex_allowlist_is_config_driven():
    """A custom allow-list admits a different DEX and rejects pump_amm."""
    only_meteora = frozenset({"meteora_damm_v2"})
    assert _map(_METEORA_FRAME, allow=only_meteora) is not None
    assert _map(_PUMP_FRAME, allow=only_meteora) is None


def test_missing_mint_dropped():
    no_mint = {"type": _LEGACY_DATA_TYPE, "data": {"source": "pump_amm"}}
    assert _map(no_mint) is None


# ---------------------------------------------------------------------------
# BUG 2 — source stamping (passes DetectionConsumer) + raw DEX preserved
# ---------------------------------------------------------------------------


def test_emitted_event_stamps_pump_dot_fun_and_preserves_raw_dex():
    event = _map(_PUMP_FRAME, event_source="pump_dot_fun")
    assert event["type"] == "MEME_DATA"
    assert event["graduated"] is True
    assert event["source"] == "pump_dot_fun"          # passes detection.filter
    assert event["address"] == _PUMP_FRAME["data"]["address"]
    assert event["dex"] == "pump_amm"                  # raw DEX surfaced for audit
    assert event["raw"]["source"] == "pump_amm"        # raw preserves "pump_amm"
    assert event["raw"] == _PUMP_FRAME["data"]


def test_default_event_source_is_pump_dot_fun():
    assert DEFAULT_EVENT_SOURCE == "pump_dot_fun"
    assert _map(_PUMP_FRAME)["source"] == "pump_dot_fun"


# ---------------------------------------------------------------------------
# BUG 3 — timestamp mapping (liquidityAddedAt ISO-8601 -> integer epoch)
# ---------------------------------------------------------------------------


def test_block_time_is_integer_epoch_from_liquidity_added_at():
    event = _map(_PUMP_FRAME)
    assert isinstance(event["blockTime"], int)
    assert event["blockTime"] == _PUMP_EPOCH


def test_parse_iso_with_and_without_z():
    naive = parse_liquidity_added_at("2026-06-19T07:17:33")
    withz = parse_liquidity_added_at("2026-06-19T07:17:33Z")
    withoffset = parse_liquidity_added_at("2026-06-19T07:17:33+00:00")
    assert naive == withz == withoffset == _PUMP_EPOCH


def test_parse_iso_with_nonzero_offset():
    # 07:17:33-05:00 == 12:17:33Z
    epoch = parse_liquidity_added_at("2026-06-19T07:17:33-05:00")
    assert epoch == int(datetime(2026, 6, 19, 12, 17, 33, tzinfo=timezone.utc).timestamp())


def test_parse_numeric_passthrough():
    assert parse_liquidity_added_at(1_750_000_000) == 1_750_000_000
    assert parse_liquidity_added_at(1_750_000_000.0) == 1_750_000_000
    assert parse_liquidity_added_at("1750000000") == 1_750_000_000


def test_parse_bad_value_returns_none():
    assert parse_liquidity_added_at("not-a-date") is None
    assert parse_liquidity_added_at(None) is None
    assert parse_liquidity_added_at("") is None
    assert parse_liquidity_added_at(True) is None  # bool guarded out


def test_bad_timestamp_falls_back_and_warns():
    """Bad/missing timestamp -> fallback epoch + a WARNING, never a crash/drop."""
    import logging

    from core.tape import birdeye_graduation_source as mod

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    mod.logger.addHandler(handler)
    prev_level = mod.logger.level
    mod.logger.setLevel(logging.WARNING)
    try:
        bad = {
            "type": _LEGACY_DATA_TYPE,
            "data": {"address": "M", "source": "pump_amm", "liquidityAddedAt": "garbage"},
        }
        event = _map(bad)
    finally:
        mod.logger.removeHandler(handler)
        mod.logger.setLevel(prev_level)

    assert event is not None                      # NOT dropped, no crash
    assert event["blockTime"] == _FALLBACK_EPOCH  # fell back to arrival time
    assert any("unparseable" in r.getMessage() for r in records)  # WARNING logged


# ---------------------------------------------------------------------------
# BUG 4 — null pool
# ---------------------------------------------------------------------------


def test_pool_address_is_none_when_frame_has_no_pool():
    event = _map(_PUMP_FRAME)
    assert event["poolAddress"] is None


# ---------------------------------------------------------------------------
# Config-driven wiring (subscribe / data type / event source / allowlist)
# ---------------------------------------------------------------------------


def test_subscribe_message_is_now_subscribe_meme():
    """US-76 AC-1: default subscription is SUBSCRIBE_MEME (not SUBSCRIBE_TOKEN_NEW_LISTING)."""
    msg = build_subscribe_message(None)
    assert msg["type"] == "SUBSCRIBE_MEME"
    assert msg["data"]["graduated"] is True
    assert msg["data"]["source"] == "pump_dot_fun"


def test_subscribe_message_config_override():
    cfg = {
        "graduation_subscribe_type": "SUBSCRIBE_CUSTOM",
        "graduation_subscribe_data": {"chains": ["solana"]},
    }
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_CUSTOM"
    assert msg["data"] == {"chains": ["solana"]}


def test_event_source_derives_from_filter_source():
    """With no explicit event_source, the stamp defaults to detection.filter.source."""
    cfg = {"filter": {"source": "pump_dot_fun", "graduated": True}}
    src = BirdeyeGraduationSource(api_key="k", config=cfg)
    assert src._event_source == "pump_dot_fun"


def test_explicit_event_source_overrides_filter():
    cfg = {"filter": {"source": "ignored"}, "event_source": "explicit_src"}
    src = BirdeyeGraduationSource(api_key="k", config=cfg)
    assert src._event_source == "explicit_src"


def test_source_reads_allowlist_from_config():
    cfg = {"graduation_dex_allowlist": ["pump_amm", "another_amm"]}
    src = BirdeyeGraduationSource(api_key="k", config=cfg)
    assert src._dex_allowlist == frozenset({"pump_amm", "another_amm"})


def test_default_allowlist_is_pump_amm():
    src = BirdeyeGraduationSource(api_key="k", config=None)
    assert src._dex_allowlist == frozenset({"pump_amm"})


# ---------------------------------------------------------------------------
# End-to-end (offline): events() over a fake ws yields nothing for legacy frames
# (legacy TOKEN_NEW_LISTING_DATA frames are not recognized by the MEME_DATA mapper)
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self, frames):
        self._frames = [json.dumps(f) for f in frames]

    def __aiter__(self):
        self._it = iter(self._frames)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self):
        self._closed = True


def test_events_legacy_frames_yield_nothing_via_meme_data_mapper():
    """TOKEN_NEW_LISTING_DATA frames are NOT recognized by the new MEME_DATA mapper.

    The events() loop now uses map_meme_data_frame which expects type=="MEME_DATA".
    Legacy frames (type=="TOKEN_NEW_LISTING_DATA") are silently dropped.
    This confirms the old subscription is dead — only SUBSCRIBE_MEME frames fire.
    """
    source = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    source._ws = _FakeWS([_WELCOME_FRAME, _PUMP_FRAME, _PANCAKE_FRAME, _METEORA_FRAME])

    async def _collect():
        return [e async for e in source.events()]

    events = asyncio.run(_collect())
    # Legacy frames are dropped by the new mapper — zero events.
    assert len(events) == 0


def test_events_run_twice_identical():
    """Determinism: the same canned frames + injected clock yield identical events."""
    def _run():
        src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
        src._ws = _FakeWS([_WELCOME_FRAME, _PUMP_FRAME, _PANCAKE_FRAME])

        async def _collect():
            return [e async for e in src.events()]

        return asyncio.run(_collect())

    assert _run() == _run()


def test_events_no_ws_yields_nothing():
    source = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))

    async def _collect():
        return [e async for e in source.events()]

    assert asyncio.run(_collect()) == []


# ---------------------------------------------------------------------------
# Architectural guards (lazy imports / no wall-clock calls in the module)
# ---------------------------------------------------------------------------


def test_no_top_level_network_imports():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top_level_imports: set[str] = set()
    for node in tree.body:  # module-body only (top level)
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_imports.add(node.module.split(".")[0])
    assert "websockets" not in top_level_imports, "websockets must be imported lazily inside connect()"
    assert "certifi" not in top_level_imports, "certifi must be imported lazily inside connect()"
    assert "ssl" not in top_level_imports, "ssl must be imported lazily inside connect()"


def test_no_wall_clock_calls():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            owner = getattr(node.func.value, "id", None)
            assert not (owner == "datetime" and attr == "now"), "no datetime.now() in tape source"
            assert not (owner == "time" and attr == "time"), "no time.time() in tape source"
