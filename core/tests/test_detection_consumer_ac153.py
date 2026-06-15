# ---
# module: core.tests.test_detection_consumer_ac153
# sprint: sprint-4
# story: US-15 AC-15.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.consumer, core.models, core.resolver, core.clock, core.replay_source, asyncio, pytest
# ---
"""AC-15.3 — Dedupe window + pre-stage warm path in DetectionConsumer.

Tests:
  1. test_within_window_duplicate_creates_exactly_one_row
       Two graduation events for the same mint within dedupe_window_s -> exactly one Token row.

  2. test_prestage_event_does_not_create_graduated_token_row
       A pre-stage event (graduated=False, progress_percent >= prestage_progress_pct)
       does NOT create a Token row.

  3. test_prestage_event_tracked_on_warm_path
       A pre-stage event is tracked in consumer.prestaged but still produces no Token row.

  4. test_prestage_then_graduation_creates_exactly_one_token_row
       Pre-stage followed by graduation creates exactly one Token row with data from the
       graduation event.

  5. test_event_below_prestage_threshold_not_prestaged
       An event with progress_percent below prestage_progress_pct is not pre-staged and
       creates no Token row.

  6. test_dedupe_is_per_mint_different_mints_both_persisted
       Two graduation events for different mints both produce Token rows (dedupe is per-mint).
"""
import asyncio
from datetime import datetime, timezone

import pytest

from core.models import PipelineConfig, Token
from core.resolver import get_active_config, invalidate_active_config_cache

# ---------------------------------------------------------------------------
# Shared valid section fixtures (all §5.2 invariants satisfied — same as
# test_detection_consumer_ac152.py):
#   scoring.window_s (180) > score_at_elapsed_s (120)       ✓  leak guard
#   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)  ✓  D4
#   scoring.capture_buffer_s (4) >= 3                       ✓  tape tail
#   gate == "adaptive_topk"                                 ✓
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
# Helpers
# ---------------------------------------------------------------------------


def _make_active_config(
    source: str = "pump_dot_fun",
    prestage_progress_pct: float = 95.0,
    dedupe_window_s: int = 60,
) -> PipelineConfig:
    """Create and save a PipelineConfig row with is_active=True.

    The detection dict includes prestage_progress_pct and dedupe_window_s at
    the top level of the detection section (not inside filter), as required by
    DetectionConfig schema (AC-15.3).
    """
    return PipelineConfig.objects.create(
        version=1,
        label=f"test-config-ac153-{source}-{dedupe_window_s}",
        is_active=True,
        detection={
            "filter": {"source": source, "graduated": True},
            "prestage_progress_pct": prestage_progress_pct,
            "dedupe_window_s": dedupe_window_s,
        },
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )


async def _run_consumer(
    events: list[dict],
    config_fn=None,
    clock_t0: datetime | None = None,
):
    """Drive DetectionConsumer with a ReplaySource and VirtualClock.

    Returns the consumer instance so callers can inspect .prestaged and .processed.
    """
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    if clock_t0 is None:
        clock_t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    source = ReplaySource(event_log=events)
    clock = VirtualClock(clock_t0)
    consumer = DetectionConsumer(source, clock, config_fn=config_fn)
    await consumer.run()
    return consumer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_within_window_duplicate_creates_exactly_one_row() -> None:
    """Two graduation events for the same mint within dedupe_window_s create exactly one Token row.

    Both events arrive at the same virtual clock timestamp (t0), so the delta is 0 seconds,
    which is <= dedupe_window_s=60. The second event must be suppressed.
    """
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun", dedupe_window_s=60)

    mint = "MINT_DEDUP_A"
    event = {
        "type": "MEME_DATA",
        "address": mint,
        "poolAddress": "POOL_DEDUP_A",
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }
    # Two identical graduation events for the same mint
    events = [event, dict(event)]  # shallow copy so they're separate dicts

    asyncio.run(_run_consumer(events, config_fn=get_active_config))

    count = Token.objects.filter(mint=mint).count()
    assert count == 1, (
        f"Expected exactly 1 Token row for {mint} after duplicate graduation events "
        f"within dedupe_window_s=60, got {count}"
    )


@pytest.mark.django_db(transaction=True)
def test_prestage_event_does_not_create_graduated_token_row() -> None:
    """A pre-stage event (graduated=False, progress_percent >= prestage_progress_pct)
    must NOT create a Token row — the mint is not yet graduated.
    """
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun", prestage_progress_pct=95.0)

    mint = "MINT_PRESTAGE_A"
    prestage_event = {
        "type": "MEME_DATA",
        "address": mint,
        "source": "pump_dot_fun",
        "graduated": False,
        "progress_percent": 97.0,  # >= 95.0 prestage threshold
    }

    asyncio.run(_run_consumer([prestage_event], config_fn=get_active_config))

    assert not Token.objects.filter(mint=mint).exists(), (
        f"Token row for {mint} should NOT exist after a pre-stage event (graduated=False)"
    )


@pytest.mark.django_db(transaction=True)
def test_prestage_event_tracked_on_warm_path() -> None:
    """A pre-stage event is tracked in consumer.prestaged but produces no Token row."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun", prestage_progress_pct=95.0)

    mint = "MINT_PRESTAGE_B"
    prestage_event = {
        "type": "MEME_DATA",
        "address": mint,
        "source": "pump_dot_fun",
        "graduated": False,
        "progress_percent": 98.0,  # >= 95.0 prestage threshold
    }

    consumer = asyncio.run(_run_consumer([prestage_event], config_fn=get_active_config))

    assert mint in consumer.prestaged, (
        f"Expected {mint} to be in consumer.prestaged after a pre-stage event, "
        f"got prestaged={consumer.prestaged}"
    )
    assert not Token.objects.filter(mint=mint).exists(), (
        f"Token row for {mint} should NOT exist after a pre-stage event (graduated=False)"
    )


@pytest.mark.django_db(transaction=True)
def test_prestage_then_graduation_creates_exactly_one_token_row() -> None:
    """Pre-stage event followed by graduation event creates exactly one Token row.

    The Token row must be populated from the graduation event (pool_address from graduation).
    """
    invalidate_active_config_cache()
    _make_active_config(
        source="pump_dot_fun",
        prestage_progress_pct=95.0,
        dedupe_window_s=60,
    )

    mint = "MINT_PRESTAGE_GRAD"
    prestage_event = {
        "type": "MEME_DATA",
        "address": mint,
        "source": "pump_dot_fun",
        "graduated": False,
        "progress_percent": 97.0,
    }
    graduation_event = {
        "type": "MEME_DATA",
        "address": mint,
        "poolAddress": "POOL_PG",
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }

    asyncio.run(
        _run_consumer([prestage_event, graduation_event], config_fn=get_active_config)
    )

    count = Token.objects.filter(mint=mint).count()
    assert count == 1, (
        f"Expected exactly 1 Token row for {mint} after pre-stage + graduation, got {count}"
    )
    token = Token.objects.get(mint=mint)
    assert token.pool_address == "POOL_PG", (
        f"pool_address should be 'POOL_PG' (from graduation event), got '{token.pool_address}'"
    )


@pytest.mark.django_db(transaction=True)
def test_event_below_prestage_threshold_not_prestaged() -> None:
    """An event with progress_percent below prestage_progress_pct is not pre-staged
    and creates no Token row.
    """
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun", prestage_progress_pct=95.0)

    mint = "MINT_LOW_PROGRESS"
    low_event = {
        "type": "MEME_DATA",
        "address": mint,
        "source": "pump_dot_fun",
        "graduated": False,
        "progress_percent": 80.0,  # < 95.0 prestage threshold
    }

    consumer = asyncio.run(_run_consumer([low_event], config_fn=get_active_config))

    assert mint not in consumer.prestaged, (
        f"Expected {mint} NOT to be in consumer.prestaged (progress_percent=80.0 < 95.0), "
        f"got prestaged={consumer.prestaged}"
    )
    assert not Token.objects.filter(mint=mint).exists(), (
        f"Token row for {mint} should NOT exist after a below-threshold event"
    )


@pytest.mark.django_db(transaction=True)
def test_dedupe_is_per_mint_different_mints_both_persisted() -> None:
    """Two graduation events for DIFFERENT mints must both produce Token rows.

    Dedupe is per-mint — two distinct mints receiving their first graduation event
    within the same window are NOT deduped against each other.
    """
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun", dedupe_window_s=60)

    mint_x = "MINT_DEDUP_X"
    mint_y = "MINT_DEDUP_Y"

    event_x = {
        "type": "MEME_DATA",
        "address": mint_x,
        "poolAddress": "POOL_DEDUP_X",
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }
    event_y = {
        "type": "MEME_DATA",
        "address": mint_y,
        "poolAddress": "POOL_DEDUP_Y",
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }

    asyncio.run(_run_consumer([event_x, event_y], config_fn=get_active_config))

    assert Token.objects.filter(mint=mint_x).exists(), (
        f"Token row for {mint_x} should exist (first graduation event for this mint)"
    )
    assert Token.objects.filter(mint=mint_y).exists(), (
        f"Token row for {mint_y} should exist (first graduation event for this mint)"
    )
