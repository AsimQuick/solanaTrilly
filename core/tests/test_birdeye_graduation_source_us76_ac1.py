# ---
# module: core.tests.test_birdeye_graduation_source_us76_ac1
# sprint: sprint-14
# story: US-76 AC-1, hotfix-new-pair-graduation-detection
# status: updated
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, asyncio, ast, json, pathlib, core.tape.birdeye_graduation_source, core.clock
# ---
"""US-76 AC-1 — Detection fix: updated for SUBSCRIBE_NEW_PAIR path.

HISTORY
=======
Originally written for the SUBSCRIBE_MEME / MEME_DATA path (US-76 AC-1).
Updated for the SUBSCRIBE_NEW_PAIR / NEW_PAIR_DATA path (hotfix-new-pair-graduation-detection).

This file retains:
  - Tests of the LEGACY ``map_meme_data_frame`` PURE FUNCTION (still exported for
    backward compat — the function itself is unchanged and correct).
  - Tests of ``BirdeyeGraduationSource.events()`` updated to use NEW_PAIR_DATA frames
    (because events() now calls map_new_pair_frame, not map_meme_data_frame).

The full NEW_PAIR_DATA path is tested in:
  ``test_birdeye_graduation_source_new_pair.py``

Fixture: core/tests/fixtures/meme_data_frames.json (unchanged — real non-graduated
MEME_DATA frames + synthetic MEME_DATA graduated fixtures used for the legacy mapper).

Updated assertions:
  - DEFAULT_SUBSCRIBE_TYPE  → "SUBSCRIBE_NEW_PAIR"  (changed from "SUBSCRIBE_MEME")
  - DEFAULT_DATA_TYPE       → "NEW_PAIR_DATA"        (changed from "MEME_DATA")
  - DEFAULT_SUBSCRIBE_DATA  → {"graduated": True, "source": "pump_dot_fun"} (kept, legacy)
  - build_subscribe_message → type="SUBSCRIBE_NEW_PAIR", empty data (no graduated filter)
  - events() loop now uses NEW_PAIR_DATA frames; instant_skipped = below_min_liquidity alias
  - CURVE_LIFE_MIN_S still exported as 60 (legacy compat) but events() no longer applies it
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
# Load committed fixtures (unchanged — real MEME_DATA frames)
# ---------------------------------------------------------------------------


def _load_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_FIXTURES = _load_fixtures()
_NON_GRADUATED = _FIXTURES["non_graduated"]      # 6 real non-graduated MEME_DATA frames
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
    """Call map_meme_data_frame with the default data_type / event_source.

    NOTE: Uses the hardcoded "MEME_DATA" data_type because map_meme_data_frame is
    the LEGACY mapper; DEFAULT_DATA_TYPE is now "NEW_PAIR_DATA" for the live path.
    """
    return map_meme_data_frame(
        frame,
        data_type="MEME_DATA",        # explicit — not DEFAULT_DATA_TYPE (which changed)
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
# Default constant assertions — UPDATED for the NEW_PAIR path
# ---------------------------------------------------------------------------


def test_default_subscribe_type_is_subscribe_new_pair():
    """DEFAULT_SUBSCRIBE_TYPE changed from SUBSCRIBE_MEME to SUBSCRIBE_NEW_PAIR."""
    assert DEFAULT_SUBSCRIBE_TYPE == "SUBSCRIBE_NEW_PAIR"


def test_default_data_type_is_new_pair_data():
    """DEFAULT_DATA_TYPE changed from MEME_DATA to NEW_PAIR_DATA."""
    assert DEFAULT_DATA_TYPE == "NEW_PAIR_DATA"


def test_default_subscribe_data_preserved_for_compat():
    """DEFAULT_SUBSCRIBE_DATA is preserved for legacy compat (not used by live path)."""
    assert DEFAULT_SUBSCRIBE_DATA == {"graduated": True, "source": "pump_dot_fun"}


def test_default_event_source_is_pump_dot_fun():
    assert DEFAULT_EVENT_SOURCE == "pump_dot_fun"


def test_curve_life_min_s_is_60_for_legacy_compat():
    """CURVE_LIFE_MIN_S is preserved as a legacy export (events() no longer uses it)."""
    assert CURVE_LIFE_MIN_S == 60


# ---------------------------------------------------------------------------
# Subscription message — UPDATED
# ---------------------------------------------------------------------------


def test_subscribe_message_is_subscribe_new_pair():
    """build_subscribe_message now produces SUBSCRIBE_NEW_PAIR, not SUBSCRIBE_MEME."""
    msg = build_subscribe_message(None)
    assert msg["type"] == "SUBSCRIBE_NEW_PAIR"


def test_subscribe_message_default_data_is_empty():
    """Default subscribe has no server-side filter (empty data dict, min_liquidity=0)."""
    msg = build_subscribe_message(None)
    assert msg["data"] == {}


def test_subscribe_message_config_override():
    cfg = {
        "graduation_subscribe_type": "SUBSCRIBE_CUSTOM",
        "graduation_subscribe_data": {"key": "val"},
    }
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_CUSTOM"
    assert msg["data"] == {"key": "val"}


# ---------------------------------------------------------------------------
# map_meme_data_frame (LEGACY pure function) — these still test the function
# directly; all assertions about its behaviour are unchanged.
# ---------------------------------------------------------------------------


def test_non_graduated_frames_emit_nothing():
    """All 6 real non-graduated MEME_DATA frames produce no event from the legacy mapper."""
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
    """meme_info.source='pump_amm' is NOT accepted by the MEME_DATA mapper."""
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
    """A graduated pump.fun MEME_DATA frame produces a non-None event."""
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
    """The synthetic fixture has a 400s curve life."""
    event = _map(_GRADUATED_FRAME)
    curve_life = event["graduated_block_time"] - event["creation_time"]
    assert curve_life == 400
    assert curve_life >= CURVE_LIFE_MIN_S


# ---------------------------------------------------------------------------
# Instant graduation (pure mapper only — events() no longer applies curve-life gate)
# ---------------------------------------------------------------------------


def test_instant_frame_curve_life_is_30s():
    """Confirm the instant fixture has 30s curve life."""
    mi = _INSTANT_FRAME["data"]["meme_info"]
    assert mi["graduated_time"] - mi["creation_time"] == 30


def test_instant_frame_mapper_itself_returns_event():
    """The pure mapper does NOT apply the curve-life gate."""
    event = _map(_INSTANT_FRAME)
    assert event is not None
    assert event["graduated_block_time"] - event["creation_time"] == 30


# ---------------------------------------------------------------------------
# events() loop — UPDATED: events() now uses NEW_PAIR_DATA frames.
# MEME_DATA frames fed to events() are SILENTLY DROPPED (wrong type for new mapper).
# ---------------------------------------------------------------------------


def test_events_meme_data_frames_yield_nothing():
    """MEME_DATA frames are silently dropped by the new events() loop.

    The live BirdeyeGraduationSource.events() now calls map_new_pair_frame,
    which only accepts NEW_PAIR_DATA frames.  Feeding MEME_DATA frames (old
    SUBSCRIBE_MEME shape) to events() yields zero events — confirming that the
    new subscription is the only active path.
    """
    frames = _NON_GRADUATED + [_GRADUATED_FRAME, _INSTANT_FRAME]
    src = _make_source(frames)
    events = asyncio.run(_collect(src))
    assert events == [], (
        "All MEME_DATA frames must be silently dropped by events() "
        "(events() now uses map_new_pair_frame, not map_meme_data_frame)"
    )


def test_events_no_ws_yields_nothing():
    """If the WS was never opened, events() yields nothing."""
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))

    async def _run():
        return [e async for e in src.events()]

    assert asyncio.run(_run()) == []


def test_events_initial_instant_skipped_is_zero():
    """instant_skipped (alias for below_min_liquidity) starts at 0."""
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    assert src.instant_skipped == 0


def test_events_below_min_liquidity_starts_at_zero():
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    assert src.below_min_liquidity == 0


# ---------------------------------------------------------------------------
# Deduplication (events() with NEW_PAIR frames)
# ---------------------------------------------------------------------------


def test_events_deduplicates_same_mint_new_pair():
    """Two NEW_PAIR_DATA frames for the same mint → exactly 1 event."""
    SOL = "So11111111111111111111111111111111111111112"
    frame1 = {
        "type": "NEW_PAIR_DATA",
        "data": {
            "address": "Pool1111111111111111111111111111111111111111",
            "name": "SOL-DUP",
            "source": "pump_amm",
            "base": {"address": SOL, "symbol": "SOL", "decimals": 9},
            "quote": {"address": "DupMint11111111111111111111111111111111111", "symbol": "DUP", "decimals": 6},
            "liquidity": 8000.0,
            "blockTime": "2026-06-21T10:00:00",
        },
    }
    frame2 = dict(frame1)  # same mint, different pool
    frame2 = {
        "type": "NEW_PAIR_DATA",
        "data": {
            "address": "Pool2222222222222222222222222222222222222222",
            "name": "SOL-DUP",
            "source": "pump_amm",
            "base": {"address": SOL, "symbol": "SOL", "decimals": 9},
            "quote": {"address": "DupMint11111111111111111111111111111111111", "symbol": "DUP", "decimals": 6},
            "liquidity": 8000.0,
            "blockTime": "2026-06-21T10:01:00",
        },
    }
    src = _make_source([frame1, frame2])
    events = asyncio.run(_collect(src))
    assert len(events) == 1
    assert events[0]["address"] == "DupMint11111111111111111111111111111111111"


def test_events_seen_mints_populated_after_graduation():
    """After emitting a NEW_PAIR event, the mint is in _seen_mints."""
    SOL = "So11111111111111111111111111111111111111112"
    frame = {
        "type": "NEW_PAIR_DATA",
        "data": {
            "address": "SomePool11111111111111111111111111111111111",
            "name": "SOL-SEENTEST",
            "source": "pump_amm",
            "base": {"address": SOL, "symbol": "SOL", "decimals": 9},
            "quote": {"address": "SeenMint11111111111111111111111111111111111", "symbol": "SEENTEST", "decimals": 6},
            "liquidity": 8000.0,
            "blockTime": "2026-06-21T10:00:00",
        },
    }
    src = _make_source([frame])
    asyncio.run(_collect(src))
    assert "SeenMint11111111111111111111111111111111111" in src._seen_mints


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
