# ---
# module: core.tests.test_p4_offline_gate_ac271
# sprint: sprint-6
# story: US-27 AC-27.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.score_orchestrator, core.snapshot_fetcher, core.replay_snapshot_source,
#               core.clock, core.schemas, core.models, pytest, datetime, json
# ---
"""AC-27.1 — P4 Offline Gate: replay a synthetic Birdeye snapshot through
ReplaySnapshotSource + VirtualClock and verify exactly the expected raw
'snapshots' row is stored, deterministically.

The offline gate closes the loop between offline feature research and the live
pipeline:
  - The replay path (ReplaySnapshotSource + VirtualClock) produces the same
    stored row as the live path would, with byte-identical raw JSONB.
  - Running the replay twice yields bit-for-bit identical raw payloads
    (determinism guarantee).
  - The DataSource is called at most once per mint (at-most-one, §6.3).
  - A second run for the same mint (fresh source + fresh orchestrator) is a
    no-op at the DB level (at-most-once cross-restart guard).

All six 'snapshots' row fields that the schema requires are present in raw.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.clock import VirtualClock
from core.replay_snapshot_source import ReplaySnapshotSource
from core.schemas import PipelineConfigSchema
from core.score_orchestrator import ScoreTimeOrchestrator
from core.snapshot_fetcher import SnapshotFetcher

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SCORE_AT_S = 120
MINT = "P4GateMint1111111111111111111111111111111111"

FIXTURE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.35, "top25": 0.58, "count": 1200},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 5000.0,
    "tvl": 4850.0,
    "depth": {"bid": 120.0, "ask": 118.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection={},
        tape={"idle_kill_ttl_s": 7200},
        scoring={
            "score_at_elapsed_s": SCORE_AT_S,
            "window_s": SCORE_AT_S + 180,
            "capture_buffer_s": 4,
        },
        outcome={"window_s": 3600},
        trading={},
    )


def _make_orch(source: ReplaySnapshotSource, clock: VirtualClock):
    """Build a (ScoreTimeOrchestrator, SnapshotFetcher) pair sharing source + clock."""
    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=_make_config,
    )
    return orch, fetcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_p4_gate_replay_stores_expected_snapshot_row():
    """Full replay path → exactly one 'snapshots' row with correct fields."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch, _ = _make_orch(source, clock)

    orch.orchestrate(MINT, T0)

    assert Snapshot.objects.filter(mint=MINT).count() == 1
    snap = Snapshot.objects.get(mint=MINT)

    assert snap.mint == MINT
    assert snap.elapsed_s == SCORE_AT_S
    assert snap.taken_at == clock.now()
    assert snap.raw is not None


@pytest.mark.django_db
def test_p4_gate_replay_returns_fixture_payload():
    """orchestrate() return value equals the fixture payload."""
    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch, _ = _make_orch(source, clock)

    result = orch.orchestrate(MINT, T0)

    assert result == FIXTURE_PAYLOAD


@pytest.mark.django_db
def test_p4_gate_determinism_run_twice_byte_identical_raw():
    """Delete snapshot between runs; second run produces byte-identical raw JSONB."""
    from core.models import Snapshot

    clock1 = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source1 = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch1, _ = _make_orch(source1, clock1)
    orch1.orchestrate(MINT, T0)
    snap1_raw = Snapshot.objects.get(mint=MINT).raw

    Snapshot.objects.filter(mint=MINT).delete()

    clock2 = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source2 = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch2, _ = _make_orch(source2, clock2)
    orch2.orchestrate(MINT, T0)
    snap2_raw = Snapshot.objects.get(mint=MINT).raw

    assert json.dumps(snap1_raw, sort_keys=True) == json.dumps(snap2_raw, sort_keys=True), (
        "Raw JSONB must be byte-identical across two independent replay runs "
        "(determinism guarantee, AC-27.1)"
    )


@pytest.mark.django_db
def test_p4_gate_datasource_called_exactly_once():
    """ReplaySnapshotSource.call_count == 1 after one orchestrate() call."""
    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch, _ = _make_orch(source, clock)

    orch.orchestrate(MINT, T0)

    assert source.call_count == 1, (
        f"DataSource must be called exactly once; got {source.call_count}"
    )


@pytest.mark.django_db
def test_p4_gate_raw_contains_all_fixture_fields():
    """Stored snap.raw contains all 7 fixture keys."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch, _ = _make_orch(source, clock)

    orch.orchestrate(MINT, T0)

    snap = Snapshot.objects.get(mint=MINT)
    for key in FIXTURE_PAYLOAD:
        assert key in snap.raw, f"Expected key '{key}' missing from stored snap.raw"


@pytest.mark.django_db
def test_p4_gate_second_run_is_noop():
    """Fresh source + fresh orchestrator for same mint → source.call_count == 0, still 1 row."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source1 = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    orch1, _ = _make_orch(source1, clock)
    orch1.orchestrate(MINT, T0)

    source2 = ReplaySnapshotSource({MINT: FIXTURE_PAYLOAD})
    clock2 = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    orch2, _ = _make_orch(source2, clock2)
    orch2.orchestrate(MINT, T0)

    assert source2.call_count == 0, (
        "DB-level at-most-once guard must prevent a second DataSource call; "
        f"got {source2.call_count}"
    )
    assert Snapshot.objects.filter(mint=MINT).count() == 1
