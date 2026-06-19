# ---
# module: core.tests.test_birdeye_graduation_source_us76_ac1
# sprint: sprint-14
# story: US-76 AC-1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, asyncio, ast, json, pathlib, core.tape.birdeye_graduation_source, core.clock
# ---
"""US-76 AC-1 — Detection fix: true graduations via SUBSCRIBE_MEME.

Tests the rewritten frame-mapper (map_meme_data_frame) and the stateful
BirdeyeGraduationSource.events() loop against committed fixtures from
core/tests/fixtures/meme_data_frames.json (real + synthetic MEME_DATA frames).

Assertions:
  - map_meme_data_frame: graduated pump.fun emits exactly ONE event with all 6
    fields correct (graduated_block_time == graduated_time); non-graduated emits
    nothing; non-pump.fun emits nothing.
  - events() loop: same as above PLUS: an instant (<60s) graduation emits nothing
    AND increments instant_skipped; a repeat of the same graduated mint emits
    nothing (dedupe).
  - Subscription message is correctly built for SUBSCRIBE_MEME.
  - New defaults (SUBSCRIBE_MEME / MEME_DATA) are correct.
  - Existing architectural guards (lazy imports, no wall-clock calls) still pass.

Ground truth: /tmp/meme_frames.json — 6 real captured SUBSCRIBE_MEME frames
(non-graduated). Fixture data committed at core/tests/fixtures/meme_data_frames.json.

Discrepancy note vs epic prose:
  The EPIC-US76 section "Confirmed live SUBSCRIBE_MEME frame" shows meme_info.pool
  as {"address": <pool>} only.  The REAL frames in /tmp/meme_frames.json show pool
  as a richer object {"address":..., "realSolReserves":..., ...}.  The mapper reads
  only pool["address"] — handles both shapes correctly.
  The epic's frame summary matches the real data on all material fields
  (source, creation_time, graduated, graduated_time, progress_percent, pool.address,
  decimals top-level on data).
"""
import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from core.clock import VirtualClock
from core.tape.birdeye_graduation_source import (
    CURVE_LIFE_MIN_S,
    DEFAULT_DATA_TYPE,
    DEFAULT_EVENT_SOURCE,
    DEFAULT_SUBSCRIBE_DATA,
    DEFAULT_SUBSCRIBE_TYPE,
    BirdeyeGraduationSource,
    build_subscribe_message,
    map_meme_data_frame,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core" / "tape" / "birdeye_graduation_source.py"
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "meme_data_frames.json"

# ---------------------------------------------------------------------------
# Load committed fixtures
# ---------------------------------------------------------------------------


def _load_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_FIXTURES = _load_fixtures()
_NON_GRADUATED = _FIXTURES["non_graduated"]      # 6 real non-graduated frames
_SYNTH = _FIXTURES["synthetic"]

_GRADUATED_FRAME = _SYNTH["graduated_pump_fun"]
_INSTANT_FRAME = _SYNTH["instant_graduation"]
_NON_PUMP_FRAME = _SYNTH["graduated_non_pump_fun"]
_DUPLICATE_FRAME = _SYNTH["graduated_pump_fun_duplicate"]

# Derived expected values from the graduated fixture.
_GRAD_MINT = _GRADUATED_FRAME["data"]["address"]
_GRAD_CREATION_TIME = _GRADUATED_FRAME["data"]["meme_info"]["creation_time"]  # 1781909530
_GRAD_GRADUATED_TIME = _GRADUATED_FRAME["data"]["meme_info"]["graduated_time"]  # 1781909930
_GRAD_POOL_ADDRESS = _GRADUATED_FRAME["data"]["meme_info"]["pool"]["address"]
_GRAD_DECIMALS = _GRADUATED_FRAME["data"]["decimals"]  # 6
_GRAD_PROGRESS = _GRADUATED_FRAME["data"]["meme_info"]["progress_percent"]  # 100.0

_T0 = datetime(2026, 6, 20, 0, 0, 0, tzinfo=timezone.utc)
_FALLBACK_EPOCH = int(_T0.timestamp())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map(frame: dict) -> dict | None:
    """Call map_meme_data_frame with the default data_type / event_source."""
    return map_meme_data_frame(
        frame,
        data_type=DEFAULT_DATA_TYPE,
        event_source=DEFAULT_EVENT_SOURCE,
        fallback_epoch=_FALLBACK_EPOCH,
    )


class _FakeWS:
    """Synchronous iterator wrapper that simulates an async WebSocket."""

    def __init__(self, frames: list[dict]):
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
        pass


def _make_source(frames: list[dict]) -> BirdeyeGraduationSource:
    src = BirdeyeGraduationSource(api_key="testkey", config=None, clock=VirtualClock(_T0))
    src._ws = _FakeWS(frames)
    return src


async def _collect(src: BirdeyeGraduationSource) -> list[dict]:
    return [e async for e in src.events()]


# ---------------------------------------------------------------------------
# Default constant assertions
# ---------------------------------------------------------------------------


def test_default_subscribe_type_is_subscribe_meme():
    assert DEFAULT_SUBSCRIBE_TYPE == "SUBSCRIBE_MEME"


def test_default_data_type_is_meme_data():
    assert DEFAULT_DATA_TYPE == "MEME_DATA"


def test_default_subscribe_data_has_graduated_and_source():
    assert DEFAULT_SUBSCRIBE_DATA == {"graduated": True, "source": "pump_dot_fun"}


def test_default_event_source_is_pump_dot_fun():
    assert DEFAULT_EVENT_SOURCE == "pump_dot_fun"


def test_curve_life_min_s_is_60():
    assert CURVE_LIFE_MIN_S == 60


# ---------------------------------------------------------------------------
# Subscription message
# ---------------------------------------------------------------------------


def test_subscribe_message_is_subscribe_meme():
    msg = build_subscribe_message(None)
    assert msg["type"] == "SUBSCRIBE_MEME"
    assert msg["data"]["graduated"] is True
    assert msg["data"]["source"] == "pump_dot_fun"


def test_subscribe_message_config_override():
    cfg = {
        "graduation_subscribe_type": "SUBSCRIBE_CUSTOM",
        "graduation_subscribe_data": {"key": "val"},
    }
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_CUSTOM"
    assert msg["data"] == {"key": "val"}


# ---------------------------------------------------------------------------
# map_meme_data_frame: non-graduated frames emit nothing
# ---------------------------------------------------------------------------


def test_non_graduated_frames_emit_nothing():
    """All 6 real non-graduated frames produce no event."""
    for frame in _NON_GRADUATED:
        result = _map(frame)
        assert result is None, (
            f"Non-graduated frame for mint={frame['data']['address']} "
            f"should produce None, got {result}"
        )


def test_wrong_type_frame_emits_nothing():
    """A frame with the wrong 'type' field is dropped."""
    welcome = {"type": "WELCOME", "data": None}
    assert _map(welcome) is None


def test_missing_data_emits_nothing():
    """Frame with no 'data' key is dropped."""
    assert _map({"type": "MEME_DATA"}) is None


def test_missing_mint_emits_nothing():
    """Frame with no data.address is dropped."""
    frame = {
        "type": "MEME_DATA",
        "data": {
            "meme_info": {
                "source": "pump_dot_fun",
                "graduated": True,
                "graduated_time": 100,
                "creation_time": 0,
            }
        },
    }
    assert _map(frame) is None


# ---------------------------------------------------------------------------
# map_meme_data_frame: non-pump.fun graduated frame emits nothing
# ---------------------------------------------------------------------------


def test_non_pump_fun_source_emits_nothing():
    """meme_info.source='raydium_launchlab' is dropped (client-side filter)."""
    assert _map(_NON_PUMP_FRAME) is None


def test_pump_amm_source_emits_nothing():
    """meme_info.source='pump_amm' (old listing frame value) is NOT accepted."""
    frame = {
        "type": "MEME_DATA",
        "data": {
            "address": "SomeMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "decimals": 6,
            "meme_info": {
                "source": "pump_amm",
                "creation_time": 1000,
                "graduated": True,
                "graduated_time": 1500,
                "pool": {"address": "SomePoolAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
                "progress_percent": 100.0,
            },
        },
    }
    assert _map(frame) is None


# ---------------------------------------------------------------------------
# map_meme_data_frame: graduated pump.fun frame emits exactly ONE event
# ---------------------------------------------------------------------------


def test_graduated_pump_fun_emits_event():
    """A graduated pump.fun frame produces a non-None event."""
    event = _map(_GRADUATED_FRAME)
    assert event is not None


def test_graduated_event_type_is_meme_data():
    event = _map(_GRADUATED_FRAME)
    assert event["type"] == "MEME_DATA"


def test_graduated_event_graduated_is_true():
    event = _map(_GRADUATED_FRAME)
    assert event["graduated"] is True


def test_graduated_event_source_is_pump_dot_fun():
    event = _map(_GRADUATED_FRAME)
    assert event["source"] == "pump_dot_fun"


def test_graduated_event_address_is_mint():
    event = _map(_GRADUATED_FRAME)
    assert event["address"] == _GRAD_MINT


def test_graduated_event_graduated_block_time_equals_graduated_time():
    """graduated_block_time must equal meme_info.graduated_time (the anchor)."""
    event = _map(_GRADUATED_FRAME)
    assert event["graduated_block_time"] == _GRAD_GRADUATED_TIME


def test_graduated_event_block_time_equals_graduated_time():
    """blockTime (DetectionConsumer compat alias) equals graduated_time."""
    event = _map(_GRADUATED_FRAME)
    assert event["blockTime"] == _GRAD_GRADUATED_TIME


def test_graduated_event_creation_time():
    event = _map(_GRADUATED_FRAME)
    assert event["creation_time"] == _GRAD_CREATION_TIME


def test_graduated_event_progress_percent():
    event = _map(_GRADUATED_FRAME)
    assert event["progress_percent"] == _GRAD_PROGRESS


def test_graduated_event_pool_address():
    """pool_address comes from meme_info.pool.address."""
    event = _map(_GRADUATED_FRAME)
    assert event["poolAddress"] == _GRAD_POOL_ADDRESS


def test_graduated_event_decimals():
    """decimals is read from top-level data.decimals (NOT from meme_info)."""
    event = _map(_GRADUATED_FRAME)
    assert event["decimals"] == _GRAD_DECIMALS


def test_graduated_event_all_six_required_fields_present():
    """AC-1 requires 6 specific fields on the emitted event."""
    event = _map(_GRADUATED_FRAME)
    required = {
        "graduated_block_time",
        "creation_time",
        "progress_percent",
        "poolAddress",
        "decimals",
        "address",
    }
    missing = required - set(event.keys())
    assert not missing, f"Missing required fields: {missing}"


def test_graduated_event_raw_is_data_payload():
    """raw is the verbatim data dict for audit."""
    event = _map(_GRADUATED_FRAME)
    assert event["raw"] == _GRADUATED_FRAME["data"]


def test_graduated_event_curve_life_is_400s():
    """The synthetic fixture has a 400s curve life — above the 60s gate."""
    event = _map(_GRADUATED_FRAME)
    curve_life = event["graduated_block_time"] - event["creation_time"]
    assert curve_life == 400
    assert curve_life >= CURVE_LIFE_MIN_S


# ---------------------------------------------------------------------------
# Instant graduation gate (pure mapper + events() loop)
# ---------------------------------------------------------------------------


def test_instant_frame_curve_life_is_30s():
    """Confirm the instant fixture has 30s curve life (below the 60s gate)."""
    mi = _INSTANT_FRAME["data"]["meme_info"]
    assert mi["graduated_time"] - mi["creation_time"] == 30


def test_instant_frame_mapper_itself_returns_event():
    """The pure mapper does NOT apply the curve-life gate — that's events() only."""
    event = _map(_INSTANT_FRAME)
    assert event is not None
    assert event["graduated_block_time"] - event["creation_time"] == 30


def test_events_skips_instant_graduation_and_increments_counter():
    """events() must skip an instant graduation AND increment instant_skipped."""
    src = _make_source([_INSTANT_FRAME])
    events = asyncio.run(_collect(src))
    assert events == [], "Instant graduation must not emit any event"
    assert src.instant_skipped == 1


def test_events_instant_skipped_counts_multiple():
    """Each instant graduation increments the counter."""
    # Build a second instant frame with a different mint.
    instant2 = {
        "type": "MEME_DATA",
        "data": {
            "address": "InstantMint2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "decimals": 6,
            "meme_info": {
                "source": "pump_dot_fun",
                "creation_time": 1781909530,
                "graduated": True,
                "graduated_time": 1781909559,  # 29s — also instant
                "pool": {"address": "InstantPool2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
                "progress_percent": 100.0,
            },
        },
    }
    src = _make_source([_INSTANT_FRAME, instant2])
    events = asyncio.run(_collect(src))
    assert events == []
    assert src.instant_skipped == 2


# ---------------------------------------------------------------------------
# Deduplication: same mint emits at most once
# ---------------------------------------------------------------------------


def test_events_deduplicates_same_mint():
    """A second graduated frame for the same mint must not emit a second event."""
    # Both _GRADUATED_FRAME and _DUPLICATE_FRAME have the same address.
    assert _GRADUATED_FRAME["data"]["address"] == _DUPLICATE_FRAME["data"]["address"]

    src = _make_source([_GRADUATED_FRAME, _DUPLICATE_FRAME])
    events = asyncio.run(_collect(src))
    assert len(events) == 1, "Duplicate grad frame must not emit a second event"
    assert events[0]["address"] == _GRAD_MINT


def test_events_seen_mints_populated_after_graduation():
    """After emitting, the mint is in _seen_mints."""
    src = _make_source([_GRADUATED_FRAME])
    asyncio.run(_collect(src))
    assert _GRAD_MINT in src._seen_mints


# ---------------------------------------------------------------------------
# events() loop: graduated pump.fun emits exactly ONE event, non-graduated zero
# ---------------------------------------------------------------------------


def test_events_graduated_emits_one_event():
    """A single graduated pump.fun frame produces exactly 1 event via events()."""
    src = _make_source([_GRADUATED_FRAME])
    events = asyncio.run(_collect(src))
    assert len(events) == 1


def test_events_non_graduated_frames_emit_nothing():
    """All 6 real non-graduated frames produce zero events via events()."""
    src = _make_source(_NON_GRADUATED)
    events = asyncio.run(_collect(src))
    assert events == []


def test_events_non_pump_fun_emits_nothing():
    """A graduated raydium_launchlab frame produces zero events."""
    src = _make_source([_NON_PUMP_FRAME])
    events = asyncio.run(_collect(src))
    assert events == []


def test_events_mixed_frames_emit_only_valid_graduation():
    """Mix of non-graduated + instant + non-pump + graduated yields exactly 1 event."""
    frames = _NON_GRADUATED + [_INSTANT_FRAME, _NON_PUMP_FRAME, _GRADUATED_FRAME]
    src = _make_source(frames)
    events = asyncio.run(_collect(src))
    assert len(events) == 1
    ev = events[0]
    assert ev["address"] == _GRAD_MINT
    assert ev["source"] == "pump_dot_fun"
    assert ev["graduated"] is True
    assert ev["graduated_block_time"] == _GRAD_GRADUATED_TIME
    assert ev["creation_time"] == _GRAD_CREATION_TIME
    assert ev["progress_percent"] == _GRAD_PROGRESS
    assert ev["poolAddress"] == _GRAD_POOL_ADDRESS
    assert ev["decimals"] == _GRAD_DECIMALS
    # instant_skipped must have been incremented for the instant frame.
    assert src.instant_skipped == 1


def test_events_no_ws_yields_nothing():
    """If the WS was never opened, events() yields nothing."""
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))

    async def _run():
        return [e async for e in src.events()]

    assert asyncio.run(_run()) == []


def test_events_initial_instant_skipped_is_zero():
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    assert src.instant_skipped == 0


# ---------------------------------------------------------------------------
# Architectural guards (still valid for the updated module)
# ---------------------------------------------------------------------------


def test_no_top_level_network_imports():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_imports.add(node.module.split(".")[0])
    assert "websockets" not in top_level_imports
    assert "certifi" not in top_level_imports
    assert "ssl" not in top_level_imports


def test_no_wall_clock_calls():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            owner = getattr(node.func.value, "id", None)
            assert not (owner == "datetime" and attr == "now"), "no datetime.now() allowed"
            assert not (owner == "time" and attr == "time"), "no time.time() allowed"
