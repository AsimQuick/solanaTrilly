# ---
# module: core.tests.test_birdeye_graduation_source_new_pair
# sprint: sprint-14
# story: hotfix-new-pair-graduation-detection
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, asyncio, ast, json, datetime, pathlib,
#               core.tape.birdeye_graduation_source, core.clock
# ---
"""Tests for the NEW SUBSCRIBE_NEW_PAIR / NEW_PAIR_DATA graduation detection path.

This module replaces the old SUBSCRIBE_MEME detection (which produced ~0 frames/4min
live) with SUBSCRIBE_NEW_PAIR (proven: 6 pump_amm graduations in ~3min).

Verifies:
  - map_new_pair_frame: base=SOL case, quote=SOL case, decimals=9 case,
    ISO blockTime parse, non-pump source filtered, missing mint returns None,
    both-SOL / neither-SOL edge returns None, min_liquidity floor.
  - Dedupe by MINT address (not name): same mint → 1 emit; same NAME different mint → 2 emits.
  - Reconnect safety: connect()/disconnect()/events() interface intact.
  - build_subscribe_message: NEW_PAIR type, no data payload at min_liq=0, min_liq>0 added.
  - BirdeyeGraduationSource.events(): drops below-min-liq, dedupes by mint, yields correct event.
  - Architectural guards: no top-level network imports, no wall-clock calls.
  - DEFAULT_SUBSCRIBE_TYPE == "SUBSCRIBE_NEW_PAIR".
  - DEFAULT_DATA_TYPE == "NEW_PAIR_DATA".

Fixture: core/tests/fixtures/new_pair_data_frames.json (6 real + synthetic variants).

Integration: emitted event dict drives DetectionConsumer to persist a Token with the
correct mint + graduated_block_time (see test_new_pair_integration_persists_token).
"""
import ast
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.clock import VirtualClock
from core.tape.birdeye_graduation_source import (
    DEFAULT_DATA_TYPE,
    DEFAULT_EVENT_SOURCE,
    DEFAULT_MIN_LIQUIDITY,
    DEFAULT_SUBSCRIBE_TYPE,
    BirdeyeGraduationSource,
    build_subscribe_message,
    map_new_pair_frame,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "core" / "tape" / "birdeye_graduation_source.py"
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "new_pair_data_frames.json"

# ---------------------------------------------------------------------------
# Load fixtures
# ---------------------------------------------------------------------------


def _load() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_FIXTURES = _load()
_REAL = _FIXTURES["real_frames"]
_SYN = _FIXTURES["synthetic"]

# Convenience references
_OILPEPE_FRAME = _REAL[0]   # base=SOL, quote=OILPEPE (decimals=6)
_SOLCOIN_FRAME = _REAL[1]   # base=SOLCOIN, quote=SOL (decimals=6)
_BASS_FRAME = _REAL[2]      # base=SOL, quote=BASS (decimals=9)
_WAGMI_FRAME = _REAL[3]
_MOON_FRAME = _REAL[4]      # blockTime ends in Z
_PEPE2_FRAME = _REAL[5]     # base=PEPE2, quote=SOL

_METEORA_FRAME = _SYN["meteora_frame"]
_BOTH_SOL_FRAME = _SYN["both_sol_frame"]
_NEITHER_SOL_FRAME = _SYN["neither_sol_frame"]
_LOW_LIQ_FRAME = _SYN["low_liquidity_frame"]
_DUPLICATE_FRAME = _SYN["duplicate_mint_frame"]        # same mint as OILPEPE
_SAME_NAME_DIFF_MINT = _SYN["same_name_different_mint_frame"]
_MISSING_BT_FRAME = _SYN["missing_block_time_frame"]

_T0 = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
_FALLBACK_EPOCH = int(_T0.timestamp())

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Expected parsed epochs from ISO strings in fixtures.
_OILPEPE_EPOCH = int(datetime(2026, 6, 21, 9, 3, 6, tzinfo=timezone.utc).timestamp())
_BASS_EPOCH = int(datetime(2026, 6, 21, 9, 5, 30, tzinfo=timezone.utc).timestamp())
_MOON_EPOCH = int(datetime(2026, 6, 21, 9, 7, 0, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map(frame: dict, *, min_liquidity: int = 0) -> dict | None:
    return map_new_pair_frame(
        frame,
        event_source=DEFAULT_EVENT_SOURCE,
        min_liquidity=min_liquidity,
        fallback_epoch=_FALLBACK_EPOCH,
    )


class _FakeWS:
    """Simulate an async WebSocket over a list of raw frames."""

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


def _make_source(frames: list[dict], config: dict | None = None) -> BirdeyeGraduationSource:
    src = BirdeyeGraduationSource(api_key="testkey", config=config, clock=VirtualClock(_T0))
    src._ws = _FakeWS(frames)
    return src


async def _collect(src: BirdeyeGraduationSource) -> list[dict]:
    return [e async for e in src.events()]


# ---------------------------------------------------------------------------
# DEFAULT CONSTANTS
# ---------------------------------------------------------------------------


def test_default_subscribe_type_is_new_pair():
    assert DEFAULT_SUBSCRIBE_TYPE == "SUBSCRIBE_NEW_PAIR"


def test_default_data_type_is_new_pair_data():
    assert DEFAULT_DATA_TYPE == "NEW_PAIR_DATA"


def test_default_event_source_is_pump_dot_fun():
    assert DEFAULT_EVENT_SOURCE == "pump_dot_fun"


def test_default_min_liquidity_is_zero():
    assert DEFAULT_MIN_LIQUIDITY == 0


# ---------------------------------------------------------------------------
# build_subscribe_message
# ---------------------------------------------------------------------------


def test_subscribe_message_type_is_new_pair():
    msg = build_subscribe_message(None)
    assert msg["type"] == "SUBSCRIBE_NEW_PAIR"


def test_subscribe_message_data_is_empty_at_default_min_liq():
    """With min_liquidity=0 (default), data payload has no keys (no server filter)."""
    msg = build_subscribe_message(None)
    assert msg["data"] == {}


def test_subscribe_message_includes_min_liquidity_when_set():
    cfg = {"graduation_min_liquidity": 5000}
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_NEW_PAIR"
    assert msg["data"]["min_liquidity"] == 5000


def test_subscribe_message_config_override_replaces_payload():
    cfg = {
        "graduation_subscribe_type": "SUBSCRIBE_CUSTOM",
        "graduation_subscribe_data": {"key": "val"},
    }
    msg = build_subscribe_message(cfg)
    assert msg["type"] == "SUBSCRIBE_CUSTOM"
    assert msg["data"] == {"key": "val"}


# ---------------------------------------------------------------------------
# map_new_pair_frame: wrong type / missing data
# ---------------------------------------------------------------------------


def test_welcome_frame_returns_none():
    assert _map({"type": "WELCOME", "data": None}) is None


def test_meme_data_frame_returns_none():
    """MEME_DATA frames are not handled by the new mapper."""
    frame = {"type": "MEME_DATA", "data": {"address": "x", "meme_info": {}}}
    assert _map(frame) is None


def test_missing_data_key_returns_none():
    assert _map({"type": "NEW_PAIR_DATA"}) is None


def test_non_dict_data_returns_none():
    assert _map({"type": "NEW_PAIR_DATA", "data": "invalid"}) is None


# ---------------------------------------------------------------------------
# map_new_pair_frame: source filter
# ---------------------------------------------------------------------------


def test_meteora_source_returns_none():
    """meteora_damm_v2 is not pump_amm — must be dropped."""
    assert _map(_METEORA_FRAME) is None


def test_pump_amm_source_returns_event():
    """pump_amm source passes the filter."""
    assert _map(_OILPEPE_FRAME) is not None


# ---------------------------------------------------------------------------
# map_new_pair_frame: base=SOL case (quote is the graduated mint)
# ---------------------------------------------------------------------------


def test_base_sol_quote_is_mint():
    """When base=SOL, the graduated mint is extracted from quote.address."""
    event = _map(_OILPEPE_FRAME)
    assert event is not None
    expected_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    assert event["address"] == expected_mint
    assert event["address"] != SOL_MINT


def test_base_sol_decimals_from_quote():
    """When base=SOL, decimals come from quote.decimals."""
    event = _map(_OILPEPE_FRAME)
    assert event["decimals"] == _OILPEPE_FRAME["data"]["quote"]["decimals"]
    assert event["decimals"] == 6


# ---------------------------------------------------------------------------
# map_new_pair_frame: quote=SOL case (base is the graduated mint)
# ---------------------------------------------------------------------------


def test_quote_sol_base_is_mint():
    """When quote=SOL, the graduated mint is extracted from base.address."""
    event = _map(_SOLCOIN_FRAME)
    assert event is not None
    expected_mint = _SOLCOIN_FRAME["data"]["base"]["address"]
    assert event["address"] == expected_mint
    assert event["address"] != SOL_MINT


def test_quote_sol_decimals_from_base():
    """When quote=SOL, decimals come from base.decimals."""
    event = _map(_SOLCOIN_FRAME)
    assert event["decimals"] == _SOLCOIN_FRAME["data"]["base"]["decimals"]
    assert event["decimals"] == 6


# ---------------------------------------------------------------------------
# map_new_pair_frame: decimals=9 case (BASS)
# ---------------------------------------------------------------------------


def test_bass_decimals_are_9():
    """BASS token has decimals=9 — not 6; verify variable-decimals handling."""
    event = _map(_BASS_FRAME)
    assert event is not None
    assert event["decimals"] == 9
    assert event["address"] == _BASS_FRAME["data"]["quote"]["address"]


# ---------------------------------------------------------------------------
# map_new_pair_frame: ISO blockTime parsing
# ---------------------------------------------------------------------------


def test_parse_naive_iso_string_is_treated_as_utc():
    """REQUIRED (capital review): a NAIVE ISO blockTime (no tz suffix) MUST parse
    as UTC, not host-local time. If it were ever read as local, graduated_block_time
    would be off by the host tz offset (hours), silently corrupting the pre-grad
    tape `rel` anchor and the score-join. This pins the UTC contract as code,
    independent of any frame fixture / the test host's timezone.
    """
    from core.tape.birdeye_graduation_source import parse_liquidity_added_at

    # 2026-06-21T09:03:06 UTC == epoch 1782032586
    assert parse_liquidity_added_at("2026-06-21T09:03:06") == 1782032586
    assert parse_liquidity_added_at("2026-06-21T09:03:06") == int(
        datetime(2026, 6, 21, 9, 3, 6, tzinfo=timezone.utc).timestamp()
    )


def test_block_time_is_integer_epoch():
    event = _map(_OILPEPE_FRAME)
    assert isinstance(event["blockTime"], int)
    assert event["blockTime"] == _OILPEPE_EPOCH


def test_graduated_block_time_equals_block_time():
    event = _map(_OILPEPE_FRAME)
    assert event["graduated_block_time"] == event["blockTime"]


def test_block_time_with_z_suffix():
    """ISO blockTime with trailing Z is parsed correctly (MOON frame)."""
    event = _map(_MOON_FRAME)
    assert event is not None
    assert event["blockTime"] == _MOON_EPOCH


def test_block_time_bass_frame():
    event = _map(_BASS_FRAME)
    assert event["blockTime"] == _BASS_EPOCH


def test_missing_block_time_falls_back_and_warns():
    """Missing blockTime falls back to fallback_epoch and emits a WARNING."""
    records: list[logging.LogRecord] = []

    import core.tape.birdeye_graduation_source as mod

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    mod.logger.addHandler(handler)
    prev_level = mod.logger.level
    mod.logger.setLevel(logging.WARNING)
    try:
        event = _map(_MISSING_BT_FRAME)
    finally:
        mod.logger.removeHandler(handler)
        mod.logger.setLevel(prev_level)

    assert event is not None
    assert event["blockTime"] == _FALLBACK_EPOCH
    assert any("falling back" in r.getMessage().lower() for r in records)


# ---------------------------------------------------------------------------
# map_new_pair_frame: pool address
# ---------------------------------------------------------------------------


def test_pool_address_is_data_address():
    """poolAddress = data.address (the PumpSwap pair, NOT the mint)."""
    event = _map(_OILPEPE_FRAME)
    assert event["poolAddress"] == _OILPEPE_FRAME["data"]["address"]
    assert event["poolAddress"] != event["address"]  # pool != mint


# ---------------------------------------------------------------------------
# map_new_pair_frame: emitted event shape (DetectionConsumer contract)
# ---------------------------------------------------------------------------


def test_emitted_event_type_is_meme_data():
    """type is kept as MEME_DATA so DetectionConsumer._is_graduation_event matches."""
    event = _map(_OILPEPE_FRAME)
    assert event["type"] == "MEME_DATA"


def test_emitted_event_graduated_is_true():
    event = _map(_OILPEPE_FRAME)
    assert event["graduated"] is True


def test_emitted_event_source_is_pump_dot_fun():
    """source is stamped pump_dot_fun — NOT pump_amm (the split is deliberate)."""
    event = _map(_OILPEPE_FRAME)
    assert event["source"] == "pump_dot_fun"


def test_emitted_event_creation_time_is_zero():
    """creation_time=0 because NEW_PAIR_DATA has no creation_time field."""
    event = _map(_OILPEPE_FRAME)
    assert event["creation_time"] == 0


def test_emitted_event_progress_percent_is_zero():
    """progress_percent=0.0 because NEW_PAIR_DATA has no progress_percent field."""
    event = _map(_OILPEPE_FRAME)
    assert event["progress_percent"] == 0.0


def test_emitted_event_raw_is_data_payload():
    """raw is the verbatim data dict for audit."""
    event = _map(_OILPEPE_FRAME)
    assert event["raw"] == _OILPEPE_FRAME["data"]


def test_all_required_downstream_fields_present():
    """All fields DetectionConsumer and downstream readers expect are present."""
    event = _map(_OILPEPE_FRAME)
    required = {
        "type", "graduated", "address", "source",
        "poolAddress", "blockTime", "graduated_block_time",
        "creation_time", "progress_percent", "decimals", "raw",
    }
    missing = required - set(event.keys())
    assert not missing, f"Missing required fields: {missing}"


# ---------------------------------------------------------------------------
# map_new_pair_frame: edge cases — both-SOL / neither-SOL
# ---------------------------------------------------------------------------


def test_both_sol_returns_none():
    """Both base and quote are SOL — no graduated mint can be identified."""
    assert _map(_BOTH_SOL_FRAME) is None


def test_neither_sol_returns_none():
    """Neither base nor quote is SOL/USDC — cannot identify a unique graduated mint."""
    assert _map(_NEITHER_SOL_FRAME) is None


def test_usdc_on_base_is_treated_as_skip():
    """USDC on base side (like SOL) means quote is the graduated mint."""
    frame = {
        "type": "NEW_PAIR_DATA",
        "data": {
            "address": "UsdcSolPoOl11111111111111111111111111111111",
            "name": "USDC-MYTOKEN",
            "source": "pump_amm",
            "base": {"address": USDC_MINT, "symbol": "USDC", "decimals": 6},
            "quote": {"address": "MyToKeN11111111111111111111111111111111111", "symbol": "MYTOKEN", "decimals": 6},
            "liquidity": 10000.0,
            "blockTime": "2026-06-21T09:15:00",
        },
    }
    event = _map(frame)
    assert event is not None
    assert event["address"] == "MyToKeN11111111111111111111111111111111111"


def test_missing_mint_address_in_non_sol_side_returns_none():
    """If the non-SOL side has no address, return None."""
    frame = {
        "type": "NEW_PAIR_DATA",
        "data": {
            "address": "SomePoOl11111111111111111111111111111111111",
            "name": "SOL-EMPTY",
            "source": "pump_amm",
            "base": {"address": SOL_MINT, "symbol": "SOL", "decimals": 9},
            "quote": {"address": "", "symbol": "EMPTY", "decimals": 6},
            "liquidity": 5000.0,
            "blockTime": "2026-06-21T09:20:00",
        },
    }
    assert _map(frame) is None


# ---------------------------------------------------------------------------
# map_new_pair_frame: min_liquidity client-side floor
# ---------------------------------------------------------------------------


def test_low_liquidity_frame_passes_with_zero_floor():
    """With min_liquidity=0 (default), low-liquidity frames are NOT dropped."""
    event = _map(_LOW_LIQ_FRAME, min_liquidity=0)
    assert event is not None


def test_low_liquidity_frame_dropped_with_floor():
    """With min_liquidity=1000, a frame with liquidity=50 is dropped."""
    event = _map(_LOW_LIQ_FRAME, min_liquidity=1000)
    assert event is None


def test_high_liquidity_frame_passes_with_floor():
    """OILPEPE with liquidity=7300 passes a floor of 5000."""
    event = _map(_OILPEPE_FRAME, min_liquidity=5000)
    assert event is not None


# ---------------------------------------------------------------------------
# All 6 real frames produce valid events
# ---------------------------------------------------------------------------


def test_all_six_real_frames_produce_events():
    """Each of the 6 real captured frames yields a non-None event."""
    for i, frame in enumerate(_REAL):
        result = _map(frame)
        assert result is not None, f"Real frame {i} ({frame['data'].get('name')}) returned None"


def test_all_six_real_events_have_correct_mint():
    """Each real event's address is the non-SOL side of the pair."""
    for frame in _REAL:
        event = _map(frame)
        base = frame["data"]["base"]["address"]
        quote = frame["data"]["quote"]["address"]
        # The mint should be whichever of base/quote is NOT SOL.
        if base == SOL_MINT:
            assert event["address"] == quote
        else:
            assert event["address"] == base
        assert event["address"] != SOL_MINT


def test_all_six_real_events_stamp_pump_dot_fun():
    for frame in _REAL:
        event = _map(frame)
        assert event["source"] == "pump_dot_fun"


def test_all_six_real_events_pool_address_is_data_address():
    for frame in _REAL:
        event = _map(frame)
        assert event["poolAddress"] == frame["data"]["address"]


# ---------------------------------------------------------------------------
# Deduplication: by MINT address, NOT by name
# ---------------------------------------------------------------------------


def test_events_deduplicates_same_mint():
    """Two frames with the same mint address yield exactly 1 event."""
    # OILPEPE_FRAME and DUPLICATE_FRAME have the same quote.address (OILPEPE mint).
    oilpepe_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    dup_mint = _DUPLICATE_FRAME["data"]["quote"]["address"]
    assert oilpepe_mint == dup_mint, "Fixture integrity: duplicate_mint_frame must have same mint"

    src = _make_source([_OILPEPE_FRAME, _DUPLICATE_FRAME])
    events = asyncio.run(_collect(src))
    assert len(events) == 1
    assert events[0]["address"] == oilpepe_mint


def test_events_same_name_different_mint_yields_two_events():
    """Two frames with the same token NAME but different mint addresses yield 2 events."""
    oilpepe_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    diff_mint = _SAME_NAME_DIFF_MINT["data"]["quote"]["address"]
    assert oilpepe_mint != diff_mint, "Fixture integrity: same_name_different_mint must differ"

    src = _make_source([_OILPEPE_FRAME, _SAME_NAME_DIFF_MINT])
    events = asyncio.run(_collect(src))
    assert len(events) == 2
    mints = {e["address"] for e in events}
    assert oilpepe_mint in mints
    assert diff_mint in mints


def test_seen_mints_populated_after_emit():
    """After yielding, the mint is in _seen_mints."""
    src = _make_source([_OILPEPE_FRAME])
    asyncio.run(_collect(src))
    expected_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    assert expected_mint in src._seen_mints


# ---------------------------------------------------------------------------
# below_min_liquidity counter
# ---------------------------------------------------------------------------


def test_below_min_liquidity_incremented_for_low_liq_frame():
    """Frames dropped by the min_liquidity floor increment below_min_liquidity."""
    src = _make_source([_LOW_LIQ_FRAME], config={"graduation_min_liquidity": 1000})
    events = asyncio.run(_collect(src))
    assert events == []
    assert src.below_min_liquidity == 1


def test_below_min_liquidity_zero_at_start():
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    assert src.below_min_liquidity == 0


def test_instant_skipped_is_alias_for_below_min_liquidity():
    """instant_skipped is a deprecated alias — reads and writes the same counter."""
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))
    src.below_min_liquidity = 3
    assert src.instant_skipped == 3
    src.instant_skipped = 5
    assert src.below_min_liquidity == 5


# ---------------------------------------------------------------------------
# BirdeyeGraduationSource.events() — end-to-end
# ---------------------------------------------------------------------------


def test_events_no_ws_yields_nothing():
    src = BirdeyeGraduationSource(api_key="k", config=None, clock=VirtualClock(_T0))

    async def _run() -> list:
        return [e async for e in src.events()]

    assert asyncio.run(_run()) == []


def test_events_meteora_frame_dropped():
    src = _make_source([_METEORA_FRAME])
    events = asyncio.run(_collect(src))
    assert events == []


def test_events_six_real_frames_yield_six_events():
    """All 6 distinct-mint real frames yield 6 events via events()."""
    src = _make_source(_REAL)
    events = asyncio.run(_collect(src))
    assert len(events) == 6


def test_events_mixed_frames_correct_count():
    """Mix of valid, meteora, both-SOL, duplicate yields 3 unique mint events."""
    frames = [
        _OILPEPE_FRAME,   # valid → 1
        _METEORA_FRAME,   # dropped (wrong source)
        _BOTH_SOL_FRAME,  # dropped (no mint)
        _DUPLICATE_FRAME, # dropped (same mint as OILPEPE)
        _BASS_FRAME,      # valid → 2
        _PEPE2_FRAME,     # valid → 3
    ]
    src = _make_source(frames)
    events = asyncio.run(_collect(src))
    assert len(events) == 3


def test_events_emitted_source_is_pump_dot_fun():
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    assert events[0]["source"] == "pump_dot_fun"


def test_events_emitted_type_is_meme_data():
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    assert events[0]["type"] == "MEME_DATA"


def test_events_emitted_graduated_is_true():
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    assert events[0]["graduated"] is True


def test_events_pool_address_is_data_address():
    """poolAddress in emitted event = data.address (the PumpSwap pair), reliably present."""
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    assert events[0]["poolAddress"] == _OILPEPE_FRAME["data"]["address"]


def test_events_address_is_spl_mint_not_pool():
    """address in emitted event = the SPL mint (Token.mint key for score-join)."""
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    expected_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    pool_addr = _OILPEPE_FRAME["data"]["address"]
    assert events[0]["address"] == expected_mint
    assert events[0]["address"] != pool_addr


def test_events_graduated_block_time_is_parsed_epoch():
    src = _make_source([_OILPEPE_FRAME])
    events = asyncio.run(_collect(src))
    assert events[0]["graduated_block_time"] == _OILPEPE_EPOCH


def test_events_below_min_liq_frames_dropped_and_counted():
    """Low-liquidity frames are dropped and below_min_liquidity is incremented."""
    src = _make_source([_LOW_LIQ_FRAME], config={"graduation_min_liquidity": 1000})
    events = asyncio.run(_collect(src))
    assert events == []
    assert src.below_min_liquidity == 1


def test_events_deterministic():
    """Same frames + same clock → same events, run twice."""
    def _run() -> list:
        s = _make_source(_REAL)
        return asyncio.run(_collect(s))

    assert _run() == _run()


# ---------------------------------------------------------------------------
# Integration: emitted event persists Token via DetectionConsumer
# ---------------------------------------------------------------------------


VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}


@pytest.mark.django_db(transaction=True)
def test_new_pair_integration_persists_token() -> None:
    """A NEW_PAIR_DATA graduation event flows through DetectionConsumer → Token row.

    Proves the end-to-end contract:
      - map_new_pair_frame emits {type:MEME_DATA, source:pump_dot_fun, graduated:True, ...}
      - DetectionConsumer._is_graduation_event matches it
      - Token row persisted with correct mint + graduated_block_time + pool_address
    """
    from core.detection.consumer import DetectionConsumer
    from core.models import PipelineConfig, Token
    from core.replay_source import ReplaySource
    from core.resolver import get_active_config, invalidate_active_config_cache

    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="test-new-pair-integration",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )

    event = _map(_OILPEPE_FRAME)
    assert event is not None
    assert event["type"] == "MEME_DATA"
    assert event["source"] == "pump_dot_fun"
    assert event["graduated"] is True

    async def _run():
        consumer = DetectionConsumer(
            source=ReplaySource(event_log=[event]),
            clock=VirtualClock(_T0),
            config_fn=get_active_config,
        )
        await consumer.run()

    asyncio.run(_run())

    expected_mint = _OILPEPE_FRAME["data"]["quote"]["address"]
    assert Token.objects.filter(mint=expected_mint).exists(), (
        "Token row must be persisted for the NEW_PAIR graduation event"
    )
    token = Token.objects.get(mint=expected_mint)
    assert token.dex_source == "pump_dot_fun"
    assert token.graduated_block_time == _OILPEPE_EPOCH
    assert token.graduated_at == datetime.fromtimestamp(_OILPEPE_EPOCH, tz=timezone.utc)
    # pool_address must be the PumpSwap pair address (NOT the mint, NOT "")
    assert token.pool_address == _OILPEPE_FRAME["data"]["address"]
    # raw_graduation must contain the emitted event fields for downstream decimals dig
    assert token.raw_graduation["decimals"] == 6
    assert token.raw_graduation["address"] == expected_mint


@pytest.mark.django_db(transaction=True)
def test_new_pair_integration_bass_decimals_9() -> None:
    """BASS frame (decimals=9) persists Token with decimals=9 in raw_graduation."""
    from core.detection.consumer import DetectionConsumer
    from core.models import PipelineConfig, Token
    from core.replay_source import ReplaySource
    from core.resolver import get_active_config, invalidate_active_config_cache

    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="test-new-pair-bass",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )

    event = _map(_BASS_FRAME)
    assert event is not None
    assert event["decimals"] == 9

    async def _run():
        consumer = DetectionConsumer(
            source=ReplaySource(event_log=[event]),
            clock=VirtualClock(_T0),
            config_fn=get_active_config,
        )
        await consumer.run()

    asyncio.run(_run())

    expected_mint = _BASS_FRAME["data"]["quote"]["address"]
    token = Token.objects.get(mint=expected_mint)
    assert token.raw_graduation["decimals"] == 9


# ---------------------------------------------------------------------------
# Architectural guards
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
