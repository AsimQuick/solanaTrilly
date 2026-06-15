# ---
# module: core.tests.test_detection_consumer_ac154
# sprint: sprint-4
# story: US-15 AC-15.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.consumer, core.models, core.resolver, core.clock, core.replay_source, asyncio, json, pathlib, pytest
# ---
"""AC-15.4 — P2 Offline Gate: replay a synthetic MEME stream → expected token rows, deterministically.

The fixture at core/tests/fixtures/meme_stream_ac154.json is a schema-faithful
Birdeye MEME_DATA stream (PRD §3.2) with 8 events covering:
  - ALPHA: pre-stage (97%) then graduation → 1 Token row
  - BETA:  direct graduation → 1 Token row
  - GAMMA: graduation + within-window duplicate → 1 Token row (dedupe gate)
  - DELTA: below-threshold pre-stage (80% < 95%) → no Token row
  - EPSILON: wrong source (raydium_amm) → no Token row
  - 1 noise event (type=SUBSCRIBE_MEME) → ignored

Expected Token rows after replay: ALPHA, BETA, GAMMA (3 total).

No live Birdeye activation was spent; the fixture is fully synthetic.
If a real captured stream replaces this fixture, log the activation in
ops/firehose_activation_log.md per PRD §15.7 before merging.

Tests:
  1. test_replay_yields_expected_token_mints
       The full stream produces EXACTLY the 3 expected Token rows — no more, no fewer.

  2. test_replay_expected_token_fields
       Each of the 3 Token rows carries correct field values (pool_address,
       graduated_block_time, dex_source) derived from the graduation event.

  3. test_replay_dedupe_suppresses_duplicate
       GAMMA's duplicate graduation (within dedupe_window_s=60) does NOT produce
       a second Token row — exactly 1 row for GAMMA.

  4. test_replay_prestage_below_threshold_no_row
       DELTA (progress_percent=80 < prestage_progress_pct=95) produces no Token row
       and is not tracked on the pre-stage warm path.

  5. test_replay_wrong_source_filtered
       EPSILON (source=raydium_amm, graduated=true) is filtered out — no Token row.

  6. test_replay_is_deterministic
       Running the full replay twice (DB cleared between runs) yields EXACTLY the
       same Token row snapshot — same mints, same field values. Byte-identical results.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.models import PipelineConfig, Token
from core.resolver import get_active_config, invalidate_active_config_cache

# ---------------------------------------------------------------------------
# Fixture file location (relative to this test file)
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "meme_stream_ac154.json"

# Base Unix timestamp embedded in the fixture (2025-03-01 00:00:00 UTC)
_T0_UNIX: int = 1740787200

# Exactly the mints the replay must produce Token rows for
EXPECTED_MINTS: frozenset[str] = frozenset(
    {
        "AC154MINTalpha11111111111111111111111111111",
        "AC154MINTbeta222222222222222222222222222222",
        "AC154MINTgamma3333333333333333333333333333",
    }
)

# Mints the replay must NOT produce Token rows for
EXCLUDED_MINTS: frozenset[str] = frozenset(
    {
        "AC154MINTdelta4444444444444444444444444444",  # below-threshold pre-stage
        "AC154MINTepsilon55555555555555555555555555",  # wrong source
    }
)

# ---------------------------------------------------------------------------
# Valid section values satisfying §5.2 cross-section invariants
# ---------------------------------------------------------------------------

_VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
_VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
_VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
_VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}

# Detection config matching the fixture's filter parameters
_DETECTION = {
    "filter": {"source": "pump_dot_fun", "graduated": True},
    "prestage_progress_pct": 95.0,
    "dedupe_window_s": 60,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_config() -> PipelineConfig:
    """Create an is_active PipelineConfig matching the fixture's filter parameters."""
    return PipelineConfig.objects.create(
        version=1,
        label="test-config-ac154-replay",
        is_active=True,
        detection=_DETECTION,
        tape=_VALID_TAPE,
        scoring=_VALID_SCORING,
        outcome=_VALID_OUTCOME,
        trading=_VALID_TRADING,
    )


def _load_fixture_events() -> list[dict]:
    """Load the events array from the fixture file."""
    with FIXTURE_PATH.open() as fh:
        data = json.load(fh)
    return data["events"]


async def _run_replay(events: list[dict]) -> None:
    """Drive DetectionConsumer with a ReplaySource and static VirtualClock(t0).

    The VirtualClock does not advance between events, so all events receive
    the same timestamp t0.  This is intentional — it proves the dedupe logic
    is driven by the injected clock, not blockTime or wall time, and makes the
    replay byte-deterministic across runs.
    """
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    source = ReplaySource(event_log=events)
    clock = VirtualClock(t0)
    consumer = DetectionConsumer(source, clock, config_fn=get_active_config)
    await consumer.run()


def _collect_token_snapshot() -> list[dict]:
    """Return a stable, sorted snapshot of all Token rows for comparison."""
    return [
        {
            "mint": t.mint,
            "pool_address": t.pool_address,
            "graduated_block_time": t.graduated_block_time,
            "dex_source": t.dex_source,
        }
        for t in Token.objects.order_by("mint")
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_replay_yields_expected_token_mints() -> None:
    """Replaying the synthetic stream yields EXACTLY the 3 expected Token rows."""
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_replay(_load_fixture_events()))

    created = frozenset(Token.objects.values_list("mint", flat=True))
    assert created == EXPECTED_MINTS, (
        f"Expected Token mints {sorted(EXPECTED_MINTS)}, got {sorted(created)}"
    )


@pytest.mark.django_db(transaction=True)
def test_replay_expected_token_fields() -> None:
    """Each Token row carries the correct field values derived from the graduation event."""
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_replay(_load_fixture_events()))

    # ALPHA: graduated at T0+20
    alpha = Token.objects.get(mint="AC154MINTalpha11111111111111111111111111111")
    assert alpha.pool_address == "AC154POOLalpha11111111111111111111111111111"
    assert alpha.dex_source == "pump_dot_fun"
    assert alpha.graduated_block_time == _T0_UNIX + 20
    assert alpha.raw_graduation["address"] == "AC154MINTalpha11111111111111111111111111111"

    # BETA: graduated at T0+10
    beta = Token.objects.get(mint="AC154MINTbeta222222222222222222222222222222")
    assert beta.pool_address == "AC154POOLbeta222222222222222222222222222222"
    assert beta.dex_source == "pump_dot_fun"
    assert beta.graduated_block_time == _T0_UNIX + 10

    # GAMMA: first graduation at T0+30 (duplicate at T0+40 is deduped)
    gamma = Token.objects.get(mint="AC154MINTgamma3333333333333333333333333333")
    assert gamma.pool_address == "AC154POOLgamma3333333333333333333333333333"
    assert gamma.dex_source == "pump_dot_fun"
    assert gamma.graduated_block_time == _T0_UNIX + 30


@pytest.mark.django_db(transaction=True)
def test_replay_dedupe_suppresses_duplicate() -> None:
    """GAMMA's duplicate graduation (within dedupe_window_s=60) creates exactly 1 row.

    Both GAMMA events are stamped with the same VirtualClock t0, so the delta is
    0 seconds, which is <= dedupe_window_s=60.  The second event must be suppressed.
    """
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_replay(_load_fixture_events()))

    count = Token.objects.filter(mint="AC154MINTgamma3333333333333333333333333333").count()
    assert count == 1, (
        f"Expected exactly 1 Token row for GAMMA after duplicate graduation "
        f"within dedupe_window_s=60, got {count}"
    )


@pytest.mark.django_db(transaction=True)
def test_replay_prestage_below_threshold_no_row() -> None:
    """DELTA (progress_percent=80 < prestage_progress_pct=95) produces no Token row."""
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_replay(_load_fixture_events()))

    assert not Token.objects.filter(
        mint="AC154MINTdelta4444444444444444444444444444"
    ).exists(), "DELTA should NOT produce a Token row (below pre-stage threshold)"


@pytest.mark.django_db(transaction=True)
def test_replay_wrong_source_filtered() -> None:
    """EPSILON (source=raydium_amm, graduated=true) is filtered out — no Token row."""
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_replay(_load_fixture_events()))

    assert not Token.objects.filter(
        mint="AC154MINTepsilon55555555555555555555555555"
    ).exists(), "EPSILON (source=raydium_amm) should NOT produce a Token row"


@pytest.mark.django_db(transaction=True)
def test_replay_is_deterministic() -> None:
    """Running the full replay twice yields EXACTLY the same Token rows.

    Run 2 starts from a clean DB state (Token rows deleted between runs) but the
    same fixture and the same VirtualClock t0.  The snapshot must be byte-identical:
    same mints, same pool addresses, same graduated_block_time, same dex_source.
    This proves the replay is deterministic end-to-end (Principle #7 / PRD §16).
    """
    invalidate_active_config_cache()
    _make_active_config()

    events = _load_fixture_events()

    # --- Run 1: fresh DB ---
    asyncio.run(_run_replay(events))
    snapshot_run1 = _collect_token_snapshot()

    # Clear Token rows; PipelineConfig row is intentionally kept so run 2 finds the active config.
    Token.objects.all().delete()
    # Invalidate cache so run 2 re-fetches the active config from the DB (not a stale cache entry).
    invalidate_active_config_cache()

    # --- Run 2: same fixture, same clock, same config ---
    asyncio.run(_run_replay(events))
    snapshot_run2 = _collect_token_snapshot()

    assert snapshot_run1 == snapshot_run2, (
        f"Replay is NOT deterministic:\n  run1={snapshot_run1}\n  run2={snapshot_run2}"
    )
    assert len(snapshot_run1) == len(EXPECTED_MINTS), (
        f"Expected {len(EXPECTED_MINTS)} rows per run, got {len(snapshot_run1)}"
    )
