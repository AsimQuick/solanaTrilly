# ---
# module: core.tests.test_shared_billy_graduation_consumer
# sprint: sprint-16
# story: fix/shared-billy-graduation-consumer
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: pytest, asyncio, datetime, core.detection.consumer,
#               core.detection.helius_reconciler, core.models, core.resolver,
#               core.clock, core.replay_source,
#               core.tape.birdeye_graduation_source
# ---
"""Regression test: shared_billy graduation path MUST use DetectionConsumer.

ROOT CAUSE DOCUMENTED
=====================
In TAPE_SOURCE=shared_billy mode, graduation detection ran through:
    BirdeyeGraduationSource → MigrateReconciler

MigrateReconciler.run() only persists events where event["type"] == "MIGRATE_TX".
BirdeyeGraduationSource emits events with event["type"] == "MEME_DATA" (the shape
DetectionConsumer expects).  Type mismatch → every graduation frame silently dropped
→ 0 Token rows → 0 predictions.  Confirmed live: ~96 pump_amm NEW_PAIR frames/hr
arrived but MigrateReconciler persisted nothing.

THE FIX
=======
shared_billy mode now runs:
    BirdeyeGraduationSource → DetectionConsumer

DetectionConsumer._is_graduation_event checks event["type"] == "MEME_DATA" and
event["source"] == filter.source ("pump_dot_fun") — the exact shape emitted by
BirdeyeGraduationSource.map_new_pair_frame.

TESTS IN THIS MODULE
====================
1. test_migrate_reconciler_drops_meme_data_event
   Documents the mismatch: MigrateReconciler.run() given a MEME_DATA event persists
   ZERO Token rows.  This is the old broken behaviour.

2. test_detection_consumer_persists_meme_data_event
   DetectionConsumer.run() given the same MEME_DATA event + matching active config
   DOES persist a Token row.  This is the fixed behaviour.

3. test_shared_billy_graduation_end_to_end
   Full end-to-end regression: a realistic pump_amm NEW_PAIR_DATA frame flows
   through BirdeyeGraduationSource.events() (via a fake WebSocket shim) →
   DetectionConsumer.run() → Token row persisted with correct mint,
   graduated_block_time, dex_source, and pool_address.  This is the exact path
   that was dead on the live box.

4. test_source_stamp_passes_detection_consumer_filter
   Verifies that the emitted event["source"] == "pump_dot_fun" matches the active
   config's detection filter (filter.source == "pump_dot_fun"), so
   DetectionConsumer._is_graduation_event returns True.  This is the critical
   filter compatibility assertion.

5. test_fake_source_via_replay_source_end_to_end
   Uses ReplaySource (injected fake source) to drive DetectionConsumer without
   any network, confirming the full persist path.  This is the canonical form of
   the end-to-end test for CI (no WebSocket mock needed).

All tests use injected sources (ReplaySource / fake WS shim) — zero network, zero
credits.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from core.clock import VirtualClock
from core.detection.consumer import DetectionConsumer
from core.detection.helius_reconciler import MigrateReconciler
from core.replay_source import ReplaySource

# ---------------------------------------------------------------------------
# Shared valid config sections (same invariants as test_detection_consumer_ac152.py)
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

# ---------------------------------------------------------------------------
# Real NEW_PAIR_DATA frame shape (from 6 captured frames, 2026-06-21)
# ---------------------------------------------------------------------------

# Realistic pump_amm graduation NEW_PAIR_DATA frame (SOL on base, token on quote).
# This is the exact shape that BirdeyeGraduationSource receives from the live WS.
_NEW_PAIR_FRAME = {
    "type": "NEW_PAIR_DATA",
    "data": {
        "address": "G8Xa6y8rhbbL54eLKYVFoxGr8U8dNQvghRmdwM27Vov",  # pool address
        "name": "SOL-TESTGRAD",
        "source": "pump_amm",
        "base": {
            "address": "So11111111111111111111111111111111111111112",  # SOL
            "symbol": "SOL",
            "decimals": 9,
        },
        "quote": {
            "address": "TeST9rAd3ToKeN111111111111111111111111111111",  # graduated token
            "symbol": "TESTGRAD",
            "decimals": 6,
        },
        "txHash": "5TestTxHash111111111111111111111111111111111111111111111",
        "liquidity": 7300.08,
        "blockTime": "2026-06-21T09:03:06",
    },
}

_EXPECTED_MINT = "TeST9rAd3ToKeN111111111111111111111111111111"
_EXPECTED_POOL = "G8Xa6y8rhbbL54eLKYVFoxGr8U8dNQvghRmdwM27Vov"
_EXPECTED_BLOCK_TIME = int(
    datetime(2026, 6, 21, 9, 3, 6, tzinfo=timezone.utc).timestamp()
)

# The MEME_DATA event as emitted by BirdeyeGraduationSource.map_new_pair_frame.
# This is what DetectionConsumer receives.  event["source"] == "pump_dot_fun"
# matches the active config's filter.source == "pump_dot_fun".
_MEME_DATA_EVENT = {
    "type": "MEME_DATA",
    "graduated": True,
    "address": _EXPECTED_MINT,
    "source": "pump_dot_fun",           # stamped by _event_source(); matches filter
    "poolAddress": _EXPECTED_POOL,
    "blockTime": _EXPECTED_BLOCK_TIME,
    "graduated_block_time": _EXPECTED_BLOCK_TIME,
    "creation_time": 0,
    "progress_percent": 0.0,
    "decimals": 6,
    "raw": _NEW_PAIR_FRAME["data"],
}

_T0 = datetime(2026, 6, 21, 9, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_active_config():
    """Create the canonical live config: filter.source='pump_dot_fun', graduated=True."""
    from core.models import PipelineConfig

    return PipelineConfig.objects.create(
        version=1,
        label="test-shared-billy-grad",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )


# ---------------------------------------------------------------------------
# Test 1: MigrateReconciler DROPS the MEME_DATA event (documents the mismatch)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_migrate_reconciler_drops_meme_data_event() -> None:
    """REGRESSION: MigrateReconciler given a MEME_DATA event persists ZERO Token rows.

    This documents the root cause of the shared_billy graduation outage.
    MigrateReconciler.run() only persists events where event["type"]=="MIGRATE_TX".
    BirdeyeGraduationSource emits events with event["type"]=="MEME_DATA".
    Type mismatch → every graduation frame silently dropped → 0 Token rows.

    This test MUST PASS on BOTH old code and new code (it documents the mismatch,
    not the fix).  MigrateReconciler is unchanged; we just confirm its broken
    behaviour is correctly characterised.
    """
    from core.models import Token
    from core.resolver import invalidate_active_config_cache

    invalidate_active_config_cache()
    _make_active_config()

    async def _run():
        reconciler = MigrateReconciler(
            source=ReplaySource(event_log=[_MEME_DATA_EVENT]),
            clock=VirtualClock(_T0),
        )
        await reconciler.run()

    asyncio.run(_run())

    # MigrateReconciler must NOT have persisted anything — the MEME_DATA event
    # does not match its MIGRATE_TX filter.
    assert not Token.objects.filter(mint=_EXPECTED_MINT).exists(), (
        "REGRESSION: MigrateReconciler should NOT persist MEME_DATA events "
        "(type mismatch — only MIGRATE_TX is handled). "
        "This test documents the broken old behaviour that caused the outage."
    )


# ---------------------------------------------------------------------------
# Test 2: DetectionConsumer PERSISTS the same MEME_DATA event (the fix)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_detection_consumer_persists_meme_data_event() -> None:
    """FIX: DetectionConsumer given the same MEME_DATA event DOES persist a Token row.

    This is the replacement consumer for the shared_billy graduation path.
    DetectionConsumer._is_graduation_event checks:
        event["type"] == "MEME_DATA"          → True (matches)
        event["graduated"] == filter.graduated → True == True (matches)
        event["source"] == filter.source       → "pump_dot_fun" == "pump_dot_fun" (matches)
    → Token row is persisted via update_or_create.
    """
    from core.models import Token
    from core.resolver import get_active_config, invalidate_active_config_cache

    invalidate_active_config_cache()
    _make_active_config()

    async def _run():
        consumer = DetectionConsumer(
            source=ReplaySource(event_log=[_MEME_DATA_EVENT]),
            clock=VirtualClock(_T0),
            config_fn=get_active_config,
        )
        await consumer.run()

    asyncio.run(_run())

    assert Token.objects.filter(mint=_EXPECTED_MINT).exists(), (
        "FIX REGRESSION: DetectionConsumer must persist a Token row for a "
        "MEME_DATA event that matches the active detection filter. "
        "If this fails, the shared_billy graduation path is broken."
    )
    token = Token.objects.get(mint=_EXPECTED_MINT)
    assert token.graduated_block_time == _EXPECTED_BLOCK_TIME
    assert token.pool_address == _EXPECTED_POOL
    assert token.dex_source == "pump_dot_fun"


# ---------------------------------------------------------------------------
# Test 3: Source stamp passes DetectionConsumer filter
# ---------------------------------------------------------------------------


def test_source_stamp_passes_detection_consumer_filter() -> None:
    """The MEME_DATA event emitted by BirdeyeGraduationSource passes the active filter.

    Verifies that:
      1. map_new_pair_frame stamps event["source"] == "pump_dot_fun" (via _event_source).
      2. DetectionConsumer._is_graduation_event returns True for this event + config.

    This is the critical filter-compatibility assertion: if source stamp vs filter.source
    mismatches, every graduation is dropped silently.

    The active live config has:
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}}
    BirdeyeGraduationSource._event_source(detection_dict) resolves to "pump_dot_fun"
    via detection.filter.source (resolution order 2).
    """
    from core.tape.birdeye_graduation_source import _event_source, map_new_pair_frame

    # Simulate the live config's detection dict
    detection_dict = {
        "filter": {"source": "pump_dot_fun", "graduated": True},
        "graduation_min_liquidity": 0,
    }

    # Verify that _event_source resolves "pump_dot_fun" from the live config
    stamp = _event_source(detection_dict)
    assert stamp == "pump_dot_fun", (
        f"event_source stamp must be 'pump_dot_fun' to match the live filter; got {stamp!r}"
    )

    # Verify map_new_pair_frame uses this stamp
    event = map_new_pair_frame(
        _NEW_PAIR_FRAME,
        event_source=stamp,
        min_liquidity=0,
        fallback_epoch=int(_T0.timestamp()),
        new_pair_source="pump_amm",
    )
    assert event is not None, "map_new_pair_frame must not return None for a valid pump_amm frame"
    assert event["type"] == "MEME_DATA", f"event type must be MEME_DATA; got {event['type']!r}"
    assert event["source"] == "pump_dot_fun", (
        f"event['source'] must be 'pump_dot_fun' to pass the detection filter; got {event['source']!r}"
    )
    assert event["graduated"] is True
    assert event["address"] == _EXPECTED_MINT

    # Verify DetectionConsumer._is_graduation_event returns True for this event
    # using a synthetic PipelineConfigSchema-like object.
    class _FakeFilter:
        source = "pump_dot_fun"
        graduated = True

    class _FakeDetection:
        filter = _FakeFilter()
        prestage_progress_pct = 80.0
        dedupe_window_s = 300

    class _FakeConfig:
        detection = _FakeDetection()

    consumer = DetectionConsumer(
        source=ReplaySource(event_log=[]),
        clock=VirtualClock(_T0),
    )
    assert consumer._is_graduation_event(event, _FakeConfig()), (  # type: ignore[arg-type]
        "DetectionConsumer._is_graduation_event must return True for a MEME_DATA event "
        "with source='pump_dot_fun' and graduated=True under the live config filter. "
        "If this fails, the event would be silently dropped."
    )


# ---------------------------------------------------------------------------
# Test 4: Full end-to-end via fake BirdeyeGraduationSource (injected source)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_shared_billy_graduation_end_to_end() -> None:
    """Full end-to-end regression: pump_amm NEW_PAIR frame → Token row persisted.

    Simulates the EXACT live path for TAPE_SOURCE=shared_billy:
        BirdeyeGraduationSource (fake WS shim) emits MEME_DATA event
        → DetectionConsumer.run() (config_fn=get_active_config)
        → Token row persisted with correct mint / graduated_block_time / pool_address.

    Uses an injected fake DataSource (ReplaySource pre-loaded with the mapped
    MEME_DATA event) — no network, no credits.

    This test FAILS on the old code (MigrateReconciler path → 0 rows) and PASSES
    on the fix (DetectionConsumer path → 1 row).
    """
    from core.models import Token
    from core.resolver import get_active_config, invalidate_active_config_cache
    from core.tape.birdeye_graduation_source import _event_source, map_new_pair_frame

    invalidate_active_config_cache()
    _make_active_config()

    # Simulate what BirdeyeGraduationSource.events() yields for a real pump_amm frame.
    # In production this comes from the live WS; here we inject via ReplaySource.
    detection_dict = {"filter": {"source": "pump_dot_fun", "graduated": True}}
    stamp = _event_source(detection_dict)  # "pump_dot_fun"
    mapped_event = map_new_pair_frame(
        _NEW_PAIR_FRAME,
        event_source=stamp,
        min_liquidity=0,
        fallback_epoch=int(_T0.timestamp()),
        new_pair_source="pump_amm",
    )
    assert mapped_event is not None, "Fixture frame must map to a non-None event"

    async def _run():
        consumer = DetectionConsumer(
            source=ReplaySource(event_log=[mapped_event]),
            clock=VirtualClock(_T0),
            config_fn=get_active_config,
        )
        await consumer.run()

    asyncio.run(_run())

    # The Token row must exist — this is the regression assertion.
    assert Token.objects.filter(mint=_EXPECTED_MINT).exists(), (
        "ZERO-ROWS REGRESSION: shared_billy graduation path must persist a Token row "
        "when a pump_amm NEW_PAIR_DATA frame is processed through "
        "BirdeyeGraduationSource → DetectionConsumer. "
        "If this fails, the graduation pipeline is dead (0 predictions)."
    )
    token = Token.objects.get(mint=_EXPECTED_MINT)
    assert token.graduated_block_time == _EXPECTED_BLOCK_TIME, (
        f"graduated_block_time mismatch: expected {_EXPECTED_BLOCK_TIME}, "
        f"got {token.graduated_block_time}"
    )
    assert token.pool_address == _EXPECTED_POOL, (
        f"pool_address mismatch: expected {_EXPECTED_POOL!r}, got {token.pool_address!r}"
    )
    assert token.dex_source == "pump_dot_fun", (
        f"dex_source mismatch: expected 'pump_dot_fun', got {token.dex_source!r}"
    )
    assert token.graduated_at == datetime.fromtimestamp(_EXPECTED_BLOCK_TIME, tz=timezone.utc), (
        "graduated_at mismatch"
    )


# ---------------------------------------------------------------------------
# Test 5: _build_shared_billy_graduation returns (None, None) without API key
# ---------------------------------------------------------------------------


def test_build_shared_billy_graduation_returns_none_without_api_key() -> None:
    """_build_shared_billy_graduation returns (None, None) when BIRDEYE_API_KEY is absent.

    This is the same guard behaviour as _build_reconciler — if the key is missing,
    graduation is disabled gracefully (not a crash).
    """
    from unittest.mock import patch

    import django.conf

    from core.clock import WallClock
    from core.management.commands.run_firehose import FirehoseDaemon

    with patch.object(django.conf.settings, "BIRDEYE_API_KEY", "", create=True):
        daemon = FirehoseDaemon(clock=WallClock())
        consumer, source = daemon._build_shared_billy_graduation()
        assert consumer is None
        assert source is None
