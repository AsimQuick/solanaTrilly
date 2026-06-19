# ---
# module: core.tests.test_birdeye_graduation_source_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, asyncio, ast, pathlib, core.tape.birdeye_graduation_source,
#               core.replay_source, core.detection.consumer, core.clock
# ---
"""Offline tests for BirdeyeGraduationSource (no network, deterministic).

Verifies:
  1. Canned raw frames -> correctly-shaped MEME_DATA graduation events.
  2. The emitted events are EXACTLY what DetectionConsumer matches as a
     graduation (type/graduated/source) and carries address/poolAddress/blockTime.
  3. Config-driven subscribe type + data type (defaults + overrides).
  4. Non-graduation frames (WELCOME/ack, wrong type, missing mint) are skipped.
  5. Lazy imports / NO import-time network: no top-level websockets/ssl import,
     no datetime.now()/time.time() (the US-2 / AC-2.2 guards already scan
     core/tape, but this asserts the contract here too).
"""
import ast
import asyncio
from pathlib import Path

from core.clock import VirtualClock
from core.replay_source import ReplaySource
from core.tape.birdeye_graduation_source import (
    DEFAULT_DATA_TYPE,
    DEFAULT_EVENT_SOURCE,
    DEFAULT_SUBSCRIBE_TYPE,
    BirdeyeGraduationSource,
    build_subscribe_message,
    map_new_listing_frame,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core" / "tape" / "birdeye_graduation_source.py"
)

# A canned raw new-listing frame in the documented default shape.
_RAW_FRAME = {
    "type": DEFAULT_DATA_TYPE,
    "data": {
        "address": "MintGradAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump",
        "poolAddress": "Poo1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "blockUnixTime": 1_750_000_000,
        "symbol": "GRAD",
    },
}

_WELCOME_FRAME = {"type": "WELCOME", "data": {"message": "connected"}}
_WRONG_TYPE_FRAME = {"type": "TXS_DATA", "data": {"address": "x"}}


# ---------------------------------------------------------------------------
# Pure mapper tests
# ---------------------------------------------------------------------------


def test_map_new_listing_frame_shapes_meme_data():
    event = map_new_listing_frame(
        _RAW_FRAME, data_type=DEFAULT_DATA_TYPE, event_source=DEFAULT_EVENT_SOURCE
    )
    assert event is not None
    assert event["type"] == "MEME_DATA"
    assert event["graduated"] is True
    assert event["address"] == _RAW_FRAME["data"]["address"]
    assert event["poolAddress"] == _RAW_FRAME["data"]["poolAddress"]
    assert event["blockTime"] == 1_750_000_000
    assert event["source"] == DEFAULT_EVENT_SOURCE
    assert event["raw"] == _RAW_FRAME["data"]


def test_map_skips_non_graduation_frames():
    assert map_new_listing_frame(_WELCOME_FRAME, data_type=DEFAULT_DATA_TYPE, event_source="birdeye") is None
    assert map_new_listing_frame(_WRONG_TYPE_FRAME, data_type=DEFAULT_DATA_TYPE, event_source="birdeye") is None
    # Missing mint -> None
    no_mint = {"type": DEFAULT_DATA_TYPE, "data": {"poolAddress": "p"}}
    assert map_new_listing_frame(no_mint, data_type=DEFAULT_DATA_TYPE, event_source="birdeye") is None


def test_map_alternate_key_spellings():
    frame = {
        "type": DEFAULT_DATA_TYPE,
        "data": {"tokenAddress": "Mint2", "pairAddress": "Pool2", "blockTime": 42},
    }
    event = map_new_listing_frame(frame, data_type=DEFAULT_DATA_TYPE, event_source="birdeye")
    assert event["address"] == "Mint2"
    assert event["poolAddress"] == "Pool2"
    assert event["blockTime"] == 42


# ---------------------------------------------------------------------------
# Config-driven wiring tests
# ---------------------------------------------------------------------------


def test_subscribe_message_defaults():
    msg = build_subscribe_message(None)
    assert msg["type"] == DEFAULT_SUBSCRIBE_TYPE
    assert msg["data"] == {}


def test_subscribe_message_config_override():
    cfg = {
        "graduation_subscribe_type": "SUBSCRIBE_CUSTOM",
        "graduation_subscribe_data": {"chains": ["solana"]},
    }
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_CUSTOM"
    assert msg["data"] == {"chains": ["solana"]}


def test_source_uses_config_data_type_and_source():
    cfg = {
        "graduation_data_type": "CUSTOM_DATA",
        "event_source": "pump_dot_fun",
    }
    source = BirdeyeGraduationSource(api_key="k", config=cfg)
    # Frame with the custom data type maps; with the default it does not.
    frame = {"type": "CUSTOM_DATA", "data": {"address": "M", "poolAddress": "P", "blockTime": 1}}
    event = map_new_listing_frame(frame, data_type=source._data_type, event_source=source._event_source)
    assert event["source"] == "pump_dot_fun"
    assert event["address"] == "M"


# ---------------------------------------------------------------------------
# End-to-end (offline): the source via events() yields graduation events that
# DetectionConsumer matches and persists.  We drive events() WITHOUT network by
# directly feeding the mapper over a ReplaySource-style stream is not possible
# (the source reads its own ws); instead we verify the mapper+shape feed
# DetectionConsumer's matcher exactly.
# ---------------------------------------------------------------------------


def test_emitted_event_matches_detection_consumer_filter():
    from core.detection.consumer import DetectionConsumer

    event = map_new_listing_frame(
        _RAW_FRAME, data_type=DEFAULT_DATA_TYPE, event_source="birdeye"
    )

    # Build a fake config whose detection.filter expects source="birdeye", graduated=True.
    class _Filter:
        source = "birdeye"
        graduated = True

    class _Detection:
        filter = _Filter()
        prestage_progress_pct = 95.0
        dedupe_window_s = 60

    class _Config:
        detection = _Detection()

    consumer = DetectionConsumer(
        source=ReplaySource(event_log=[]),
        clock=VirtualClock(__import__("datetime").datetime(2026, 6, 19, tzinfo=__import__("datetime").timezone.utc)),
    )
    assert consumer._is_graduation_event(event, _Config()) is True


def test_events_yields_mapped_graduations_via_fake_ws():
    """Drive events() against a fake async-iterable ws (no network)."""

    class _FakeWS:
        def __init__(self, frames):
            self._frames = [__import__("json").dumps(f) for f in frames]

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

    source = BirdeyeGraduationSource(api_key="k", config=None)
    source._ws = _FakeWS([_WELCOME_FRAME, _RAW_FRAME, _WRONG_TYPE_FRAME])

    async def _collect():
        return [e async for e in source.events()]

    events = asyncio.run(_collect())
    assert len(events) == 1
    assert events[0]["type"] == "MEME_DATA"
    assert events[0]["graduated"] is True
    assert events[0]["address"] == _RAW_FRAME["data"]["address"]


# ---------------------------------------------------------------------------
# Lazy-import / no-import-time-network guard (this file's own assertion)
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
