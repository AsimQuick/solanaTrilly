# ---
# module: core.tests.test_score_orchestration_ac253
# sprint: sprint-6
# story: US-25 AC-25.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.score_orchestrator, core.snapshot_fetcher, core.replay_snapshot_source,
#               core.clock, core.schemas, core.models, django.conf, pathlib, json, pytest
# ---
"""AC-25.3 — Orchestration is replay-testable (Principle #7) + H2 manifest guard.

Two verification prongs per the AC:

Prong A — Replay-testability (Principle #7 / DataSource seam):
  Drive the full ScoreTimeOrchestrator + SnapshotFetcher stack from a
  ReplaySnapshotSource (no network I/O) + VirtualClock (no wall-clock reads)
  and assert the expected single snapshot row is produced.

  Tests:
  1. test_replay_orchestration_produces_single_snapshot — full path from
     ReplaySnapshotSource + VirtualClock → exactly one 'snapshots' row.
  2. test_replay_orchestration_row_content — the persisted row carries the
     expected mint, elapsed_s, taken_at, and raw payload from the fixture.
  3. test_replay_source_called_exactly_once — DataSource is called once
     (at-most-one discipline holds even in replay mode, §6.3).
  4. test_replay_second_call_is_noop — re-running orchestration with a fresh
     ReplaySnapshotSource for the same already-snapshotted mint makes no
     DataSource call and leaves exactly one row (idempotency in replay context).

Prong B — H2 manifest guard (no per-token polling task):
  5. test_h2_no_snapshot_in_task_manifest — core/task_manifest.json contains
     no task name with 'snapshot' (the per-token-poll regime is RETIRED, §6.3).
  6. test_h2_no_snapshot_in_celery_beat_schedule — CELERY_BEAT_SCHEDULE has no
     key or task name with 'snapshot'.

Notes:
  - Prong A tests use @pytest.mark.django_db because orchestrate() calls
    fetcher.fetch_and_persist() which writes to the 'snapshots' table.
  - Prong B tests need no DB; they inspect config and the filesystem only.
  - The H2 guard here is intentionally duplicated from AC-24.4: if AC-24.4's
    test file were removed, this test would still prevent a re-introduced poll.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.clock import VirtualClock
from core.replay_snapshot_source import ReplaySnapshotSource
from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SCORE_AT_S = 120

MINT_A = "AC253MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "AC253MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

FIXTURE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.42, "count": 920},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 7500.0,
    "tvl": 7200.0,
    "depth": {"bid": 150.0, "ask": 145.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(score_at_elapsed_s: int = SCORE_AT_S) -> PipelineConfigSchema:
    """Minimal valid PipelineConfigSchema with the given score_at_elapsed_s."""
    return PipelineConfigSchema.from_model_sections(
        detection={},
        tape={"idle_kill_ttl_s": 7200},
        scoring={
            "score_at_elapsed_s": score_at_elapsed_s,
            "window_s": score_at_elapsed_s + 180,
            "capture_buffer_s": 4,
        },
        outcome={"window_s": 3600},
        trading={},
    )


def _make_orch_from_replay(source: ReplaySnapshotSource, clock: VirtualClock):
    """Return a (ScoreTimeOrchestrator, SnapshotFetcher) pair wired to source + clock."""
    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=lambda: _make_config(SCORE_AT_S),
    )
    return orch, fetcher


# ---------------------------------------------------------------------------
# Prong A — Replay-testability (ReplaySnapshotSource + VirtualClock)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_replay_orchestration_produces_single_snapshot():
    """Full path: ReplaySnapshotSource + VirtualClock → exactly one 'snapshots' row.

    The entire orchestration stack (ScoreTimeOrchestrator → SnapshotFetcher →
    ReplaySnapshotSource) runs offline — no network calls, no wall-clock reads.
    Verifies the DataSource seam + injected clock make the orchestration
    replay-testable (Principle #7, AC-25.3).
    """
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT_A: FIXTURE_PAYLOAD})
    orch, _ = _make_orch_from_replay(source, clock)

    result = orch.orchestrate(MINT_A, T0)

    assert result == FIXTURE_PAYLOAD, (
        "orchestrate() via ReplaySnapshotSource must return the fixture payload"
    )
    assert Snapshot.objects.filter(mint=MINT_A).count() == 1, (
        "Replay orchestration must produce exactly one 'snapshots' row"
    )


@pytest.mark.django_db
def test_replay_orchestration_row_content():
    """The persisted snapshot row carries the expected mint, elapsed_s, taken_at, and raw.

    Verifies every column of the 'snapshots' row matches what the replay fixture
    and virtual clock supplied — the raw JSONB is the verbatim fixture payload
    (§6.4.1 immutable truth).
    """
    from core.models import Snapshot

    score_time = T0 + timedelta(seconds=SCORE_AT_S)
    clock = VirtualClock(score_time)
    source = ReplaySnapshotSource({MINT_A: FIXTURE_PAYLOAD})
    orch, _ = _make_orch_from_replay(source, clock)

    orch.orchestrate(MINT_A, T0)

    snap = Snapshot.objects.get(mint=MINT_A)
    assert snap.elapsed_s == SCORE_AT_S, (
        f"elapsed_s in row ({snap.elapsed_s}) must equal "
        f"config.scoring.score_at_elapsed_s ({SCORE_AT_S})"
    )
    assert snap.taken_at == score_time, (
        f"taken_at must equal the virtual clock's time ({score_time}); "
        f"got {snap.taken_at}"
    )
    assert snap.raw == FIXTURE_PAYLOAD, (
        "raw JSONB in the persisted row must be the verbatim fixture payload (§6.4.1)"
    )


@pytest.mark.django_db
def test_replay_source_called_exactly_once():
    """ReplaySnapshotSource.get_snapshot() is called exactly once per mint.

    The at-most-one discipline (§6.3) holds even in replay mode: a single
    orchestrate() call drives exactly one DataSource read.
    """
    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({MINT_A: FIXTURE_PAYLOAD})
    orch, _ = _make_orch_from_replay(source, clock)

    orch.orchestrate(MINT_A, T0)

    assert source.call_count == 1, (
        f"ReplaySnapshotSource must be called exactly once per mint; "
        f"got {source.call_count}"
    )
    assert len(source.calls) == 1, (
        f"source.calls must have exactly one entry; got {source.calls}"
    )
    assert source.calls[0][0] == MINT_A, (
        f"source.calls[0][0] must be MINT_A ({MINT_A!r}); got {source.calls[0][0]!r}"
    )


@pytest.mark.django_db
def test_replay_second_call_is_noop():
    """Re-running orchestration for an already-snapshotted mint is a no-op.

    After a first orchestrate() produces a snapshot row, creating a fresh
    ReplaySnapshotSource + fresh ScoreTimeOrchestrator for the same mint
    must make zero DataSource calls and leave exactly one row.

    This is the replay-context proof of the idempotency requirement (AC-25.2):
    the DB-level guard in fetch_and_persist() survives listener restarts even
    when the source is a ReplaySnapshotSource (no in-memory state carried over).
    """
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))

    # First run: creates the snapshot row
    source_1 = ReplaySnapshotSource({MINT_A: FIXTURE_PAYLOAD})
    orch_1, _ = _make_orch_from_replay(source_1, clock)
    orch_1.orchestrate(MINT_A, T0)
    assert source_1.call_count == 1, "First run must call the DataSource once"

    # Second run: fresh replay source, same mint — must be a no-op
    source_2 = ReplaySnapshotSource({MINT_A: FIXTURE_PAYLOAD})
    orch_2, _ = _make_orch_from_replay(source_2, clock)
    result = orch_2.orchestrate(MINT_A, T0)

    assert result is None, (
        "Second orchestrate() for an already-snapshotted mint must return None "
        "(DB-level at-most-once guard, §6.3)"
    )
    assert source_2.call_count == 0, (
        f"Second run must make zero DataSource calls; got {source_2.call_count}. "
        "The DB-level guard must fire before the ReplaySnapshotSource is called."
    )
    assert Snapshot.objects.filter(mint=MINT_A).count() == 1, (
        "Still exactly one 'snapshots' row after the second run"
    )


# ---------------------------------------------------------------------------
# Prong B — H2 manifest guard (no per-token polling task)
# ---------------------------------------------------------------------------


def test_h2_no_snapshot_in_task_manifest():
    """core/task_manifest.json must contain no task name with 'snapshot'.

    The per-token-poll snapshot scheduler is RETIRED (§6.3).  This guard
    prevents a periodic/scheduled snapshot task from being re-introduced
    without detection.  Intentionally mirrors the guard in AC-24.4 so that
    even if that test file were removed, this guard still holds (AC-25.3).
    """
    assert MANIFEST_PATH.exists(), (
        f"core/task_manifest.json not found at: {MANIFEST_PATH}"
    )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tasks = manifest.get("tasks", [])

    for task_name in tasks:
        assert "snapshot" not in task_name.lower(), (
            f"task_manifest.json contains task {task_name!r} which includes 'snapshot'. "
            "The per-token-poll snapshot scheduler is RETIRED (§6.3, AC-25.3). "
            "Snapshots are on-demand only — no scheduled/periodic snapshot task "
            "should appear in the manifest."
        )


def test_h2_no_snapshot_in_celery_beat_schedule():
    """CELERY_BEAT_SCHEDULE must contain no key or task name with 'snapshot'.

    The per-token-poll scheduler regime is RETIRED (§6.3, AC-24.4, AC-25.3).
    No periodic Celery-beat task should poll for snapshots.  The snapshot is
    on-demand only (scheduled-once-per-token, not a recurring poll).
    """
    from django.conf import settings

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})

    for schedule_key, entry in beat_schedule.items():
        assert "snapshot" not in schedule_key.lower(), (
            f"CELERY_BEAT_SCHEDULE key {schedule_key!r} contains 'snapshot'. "
            "The per-token-poll snapshot scheduler must be DROPPED (§6.3). "
            "Snapshots are on-demand only."
        )
        task_name = entry.get("task", "")
        assert "snapshot" not in task_name.lower(), (
            f"CELERY_BEAT_SCHEDULE entry {schedule_key!r} has task={task_name!r} "
            "which contains 'snapshot'. The per-token-poll snapshot scheduler must "
            "be DROPPED (§6.3). Snapshots are on-demand only."
        )
