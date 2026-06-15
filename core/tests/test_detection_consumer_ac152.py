# ---
# module: core.tests.test_detection_consumer_ac152
# sprint: sprint-4
# story: US-15 AC-15.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.consumer, core.models, core.resolver, core.clock, core.replay_source, asyncio, pytest
# ---
"""AC-15.2 — Config-driven graduation filter + Token row persistence.

Tests:
  1. test_graduation_event_creates_token_row_with_correct_fields
       A pump_dot_fun graduation event with all Birdeye fields creates a Token row
       with mint, pool_address, graduated_at, graduated_block_time, dex_source,
       and raw_graduation set correctly from the event.

  2. test_raw_graduation_stored_verbatim
       The raw_graduation JSONB field stores the exact event dict unchanged.

  3. test_non_graduation_event_not_persisted
       An event with graduated=False does NOT create a Token row.

  4. test_mismatched_source_event_not_persisted
       Config filter source=pump_dot_fun but event source=raydium → no Token row.

  5. test_changing_active_config_changes_consumer_behavior
       Run 1 with config A (source=pump_dot_fun) → Token created.
       Change active config to B (source=raydium).
       Run 2 with same pump_dot_fun event for a different mint → no Token.
       Proves filter is read from config, not hardcoded.

  6. test_no_config_fn_yields_no_token_rows
       Consumer with config_fn=None processes events but creates no Token rows
       (backward compatibility with AC-15.1).

  7. test_event_without_block_time_uses_clock_timestamp
       Event without blockTime: graduated_at is set to the injected clock's timestamp.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from core.models import PipelineConfig, Token
from core.resolver import get_active_config, invalidate_active_config_cache

# ---------------------------------------------------------------------------
# Shared valid section fixtures (all §5.2 invariants satisfied — same as
# test_resolver_ac111.py):
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
# Shared test constants
# ---------------------------------------------------------------------------

MINT_TEST = "MINT_AC152_TEST_1"
POOL_TEST = "POOL_AC152_TEST_1"
BLOCK_TIME = 1_700_000_000  # Unix epoch integer — maps to 2023-11-14T22:13:20Z

_GRADUATION_EVENT = {
    "type": "MEME_DATA",
    "address": MINT_TEST,
    "poolAddress": POOL_TEST,
    "blockTime": BLOCK_TIME,
    "source": "pump_dot_fun",
    "graduated": True,
    "progress_percent": 100.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_config(source: str = "pump_dot_fun") -> PipelineConfig:
    """Create and save a PipelineConfig row with is_active=True."""
    return PipelineConfig.objects.create(
        version=1,
        label=f"test-config-{source}",
        is_active=True,
        detection={"filter": {"source": source, "graduated": True}},
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )


def _run_consumer_with_events(events: list[dict], config_fn=None, clock_t0: datetime | None = None) -> None:
    """Drive DetectionConsumer with a ReplaySource synchronously (asyncio.run)."""
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    if clock_t0 is None:
        clock_t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run():
        source = ReplaySource(event_log=events)
        clock = VirtualClock(clock_t0)
        consumer = DetectionConsumer(source, clock, config_fn=config_fn)
        await consumer.run()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_graduation_event_creates_token_row_with_correct_fields() -> None:
    """A graduation event creates a Token row with all fields mapped correctly."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    _run_consumer_with_events([_GRADUATION_EVENT], config_fn=get_active_config)

    assert Token.objects.filter(mint=MINT_TEST).exists(), (
        f"Expected Token row with mint={MINT_TEST} to exist after graduation event"
    )
    token = Token.objects.get(mint=MINT_TEST)

    assert token.pool_address == POOL_TEST, (
        f"pool_address mismatch: expected {POOL_TEST}, got {token.pool_address}"
    )
    expected_graduated_at = datetime.fromtimestamp(BLOCK_TIME, tz=timezone.utc)
    assert token.graduated_at == expected_graduated_at, (
        f"graduated_at mismatch: expected {expected_graduated_at}, got {token.graduated_at}"
    )
    assert token.graduated_block_time == BLOCK_TIME, (
        f"graduated_block_time mismatch: expected {BLOCK_TIME}, got {token.graduated_block_time}"
    )
    assert token.dex_source == "pump_dot_fun", (
        f"dex_source mismatch: expected 'pump_dot_fun', got {token.dex_source}"
    )
    assert token.status == Token.STATUS_DETECTED, (
        f"status mismatch: expected DETECTED, got {token.status}"
    )


@pytest.mark.django_db(transaction=True)
def test_raw_graduation_stored_verbatim() -> None:
    """raw_graduation JSONB stores the exact event dict unchanged."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    _run_consumer_with_events([_GRADUATION_EVENT], config_fn=get_active_config)

    token = Token.objects.get(mint=MINT_TEST)
    assert token.raw_graduation == _GRADUATION_EVENT, (
        f"raw_graduation stored value differs from original event.\n"
        f"Expected: {_GRADUATION_EVENT}\n"
        f"Got:      {token.raw_graduation}"
    )


@pytest.mark.django_db(transaction=True)
def test_non_graduation_event_not_persisted() -> None:
    """An event with graduated=False does NOT create a Token row."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    non_grad_event = {
        "type": "MEME_DATA",
        "address": "MINT_NON_GRAD",
        "poolAddress": "POOL_NON_GRAD",
        "blockTime": BLOCK_TIME,
        "source": "pump_dot_fun",
        "graduated": False,
        "progress_percent": 50.0,
    }

    _run_consumer_with_events([non_grad_event], config_fn=get_active_config)

    assert not Token.objects.filter(mint="MINT_NON_GRAD").exists(), (
        "Token row should NOT exist for an event with graduated=False"
    )


@pytest.mark.django_db(transaction=True)
def test_mismatched_source_event_not_persisted() -> None:
    """Config filter source=pump_dot_fun but event source=raydium → no Token row."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    raydium_event = {
        "type": "MEME_DATA",
        "address": "MINT_RAYDIUM",
        "poolAddress": "POOL_RAYDIUM",
        "blockTime": BLOCK_TIME,
        "source": "raydium",
        "graduated": True,
        "progress_percent": 100.0,
    }

    _run_consumer_with_events([raydium_event], config_fn=get_active_config)

    assert not Token.objects.filter(mint="MINT_RAYDIUM").exists(), (
        "Token row should NOT exist when event source does not match the config filter source"
    )


@pytest.mark.django_db(transaction=True)
def test_changing_active_config_changes_consumer_behavior() -> None:
    """Changing the active config's detection filter changes consumer behaviour.

    Run 1: config A (source=pump_dot_fun) → pump_dot_fun event for MINT_A → Token created.
    Run 2: config B (source=raydium)      → same pump_dot_fun event for MINT_B → Token NOT created.
    Demonstrates the filter is read from the live config, not hardcoded.
    """
    invalidate_active_config_cache()

    # --- Run 1: config A matches pump_dot_fun ---
    _make_active_config(source="pump_dot_fun")

    event_a = {
        "type": "MEME_DATA",
        "address": "MINT_CONFIG_A",
        "poolAddress": "POOL_CONFIG_A",
        "blockTime": BLOCK_TIME,
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }
    _run_consumer_with_events([event_a], config_fn=get_active_config)

    assert Token.objects.filter(mint="MINT_CONFIG_A").exists(), (
        "Token for MINT_CONFIG_A should exist when config A (source=pump_dot_fun) is active"
    )

    # --- Switch to config B: source=raydium ---
    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    _make_active_config(source="raydium")
    invalidate_active_config_cache()

    # Same event type (pump_dot_fun) for a different mint — should NOT match config B
    event_b = {
        "type": "MEME_DATA",
        "address": "MINT_CONFIG_B",
        "poolAddress": "POOL_CONFIG_B",
        "blockTime": BLOCK_TIME,
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }
    _run_consumer_with_events([event_b], config_fn=get_active_config)

    assert not Token.objects.filter(mint="MINT_CONFIG_B").exists(), (
        "Token for MINT_CONFIG_B should NOT exist when config B (source=raydium) is active "
        "and the event source is pump_dot_fun — proves filter is config-driven, not hardcoded"
    )


@pytest.mark.django_db(transaction=True)
def test_no_config_fn_yields_no_token_rows() -> None:
    """Consumer with config_fn=None processes events but creates no Token rows.

    Backward compatibility guarantee for AC-15.1 callers that pass only
    (source, clock) to DetectionConsumer.
    """
    invalidate_active_config_cache()

    # Even with an active config present, no Token rows should be created
    # because config_fn=None bypasses the persistence path entirely.
    _make_active_config(source="pump_dot_fun")

    event = {
        "type": "MEME_DATA",
        "address": "MINT_NO_CONFIG_FN",
        "poolAddress": "POOL_NO_CONFIG_FN",
        "blockTime": BLOCK_TIME,
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }

    _run_consumer_with_events([event], config_fn=None)

    assert not Token.objects.filter(mint="MINT_NO_CONFIG_FN").exists(), (
        "No Token row should be created when config_fn=None (AC-15.1 backward compat)"
    )


@pytest.mark.django_db(transaction=True)
def test_event_without_block_time_uses_clock_timestamp() -> None:
    """If blockTime is absent, graduated_at is set to the injected clock's timestamp."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    clock_t0 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    event_no_blocktime = {
        "type": "MEME_DATA",
        "address": "MINT_NO_BLOCKTIME",
        "poolAddress": "POOL_NO_BLOCKTIME",
        # Note: blockTime intentionally omitted
        "source": "pump_dot_fun",
        "graduated": True,
        "progress_percent": 100.0,
    }

    _run_consumer_with_events([event_no_blocktime], config_fn=get_active_config, clock_t0=clock_t0)

    assert Token.objects.filter(mint="MINT_NO_BLOCKTIME").exists(), (
        "Token row should still be created even when blockTime is absent"
    )
    token = Token.objects.get(mint="MINT_NO_BLOCKTIME")
    assert token.graduated_at == clock_t0, (
        f"graduated_at should fall back to clock timestamp {clock_t0} when blockTime absent, "
        f"got {token.graduated_at}"
    )
    assert token.graduated_block_time == int(clock_t0.timestamp()), (
        f"graduated_block_time should be int(clock_t0.timestamp()) when blockTime absent"
    )
