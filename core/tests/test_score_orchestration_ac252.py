# ---
# module: core.tests.test_score_orchestration_ac252
# sprint: sprint-6
# story: US-25 AC-25.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.score_orchestrator, core.snapshot_fetcher, core.snapshot_source,
#               core.clock, core.schemas, core.models, pytest, datetime
# ---
"""AC-25.2 — At-most-once / idempotent orchestration: a token never receives a
second score-time snapshot even on task retry, listener restart, or duplicate
graduation event.

The one-row-per-token discipline (US-23.2) holds end-to-end:
  - Re-running orchestration for an already-snapshotted mint is a no-op.
  - Still exactly one 'snapshots' row after any number of retries/restarts.
  - No second DataSource (REST) read is attempted once a row exists.

Two idempotency layers protect this invariant (§6.3):
  1. DB-level (cross-restart): fetch_and_persist() checks Snapshot.objects.filter
     before calling the DataSource.  Works even when the in-memory _fetched set
     is fresh (e.g., after a listener restart).
  2. In-memory (same-process): SnapshotFetcher._fetched set gates subsequent
     calls within the same process lifetime.

Verification strategy:
  (a) same-instance task retry — call orchestrate() twice with the same fetcher;
      verify 1 row, DataSource called exactly once, second return is None.
  (b) fresh-instance listener restart — run orchestrate() with fetcher_1, then
      create a brand-new fetcher_2 + orchestrator_2 (empty _fetched) and run for
      the same mint; verify DataSource_2.call_count == 0 and still exactly 1 row.
  (c) duplicate graduation event — call orchestrate() twice with the same orch
      (same scenario as (a), framed as duplicate event).
  (d) multiple retries — call orchestrate() N times; always exactly 1 row.
  (e) fresh-instance restart returns None — orchestrate() via fresh instance
      returns None without touching the DataSource.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.clock import VirtualClock
from core.schemas import PipelineConfigSchema
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SCORE_AT_S = 120

MINT_A = "AC252MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "AC252MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.40, "count": 800},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 3000.0,
    "tvl": 2900.0,
    "depth": {"bid": 80.0, "ask": 80.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(score_at_elapsed_s: int = SCORE_AT_S) -> PipelineConfigSchema:
    """Minimal valid config with the given score_at_elapsed_s."""
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


class CountingSnapshotSource(SnapshotDataSource):
    """Test double that counts every get_snapshot() call and returns a preset payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.call_count: int = 0

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self.call_count += 1
        return self._payload


def _make_orch(source: SnapshotDataSource, clock: VirtualClock):
    """Return a (ScoreTimeOrchestrator, SnapshotFetcher) pair sharing source + clock."""
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
# (a) Same-instance task retry
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_same_instance_retry_second_call_returns_none():
    """Calling orchestrate() twice with the same fetcher: second call returns None.

    Verifies the in-memory _fetched guard prevents a second DataSource call
    within the same process lifetime (task retry scenario).
    """
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch, _ = _make_orch(source, clock)

    first = orch.orchestrate(MINT_A, T0)
    second = orch.orchestrate(MINT_A, T0)

    assert first == SAMPLE_PAYLOAD, "First orchestrate() must return the snapshot payload"
    assert second is None, "Second orchestrate() for same mint must return None"
    assert Snapshot.objects.filter(mint=MINT_A).count() == 1
    assert source.call_count == 1, (
        f"DataSource must be called exactly once; got {source.call_count}"
    )


@pytest.mark.django_db
def test_same_instance_retry_row_count_stays_one():
    """Calling orchestrate() twice leaves exactly one 'snapshots' row."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch, _ = _make_orch(source, clock)

    orch.orchestrate(MINT_A, T0)
    orch.orchestrate(MINT_A, T0)

    assert Snapshot.objects.filter(mint=MINT_A).count() == 1


# ---------------------------------------------------------------------------
# (b) Fresh-instance listener restart — DB-level idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fresh_instance_restart_no_second_datasource_call():
    """After a listener restart (new SnapshotFetcher + empty _fetched set),
    orchestrate() for an already-snapshotted mint makes no DataSource call.

    Verifies the DB-level exists() guard in fetch_and_persist() that survives
    listener restarts (AC-25.2 — 'even on... listener restart').
    """
    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))

    # First run: creates the snapshot row
    source_1 = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch_1, _ = _make_orch(source_1, clock)
    orch_1.orchestrate(MINT_A, T0)
    assert source_1.call_count == 1, "First run must call the DataSource once"

    # Simulate listener restart: brand-new fetcher with empty _fetched set
    source_2 = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch_2, _ = _make_orch(source_2, clock)
    result = orch_2.orchestrate(MINT_A, T0)

    assert result is None, (
        "After restart, orchestrate() for an already-snapshotted mint must return None"
    )
    assert source_2.call_count == 0, (
        "After restart, DataSource must NOT be called for an already-snapshotted mint; "
        f"got {source_2.call_count} calls"
    )


@pytest.mark.django_db
def test_fresh_instance_restart_row_count_stays_one():
    """After a listener restart, the 'snapshots' row count for the mint stays at one."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))

    source_1 = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch_1, _ = _make_orch(source_1, clock)
    orch_1.orchestrate(MINT_A, T0)

    # Simulate restart
    source_2 = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch_2, _ = _make_orch(source_2, clock)
    orch_2.orchestrate(MINT_A, T0)

    assert Snapshot.objects.filter(mint=MINT_A).count() == 1


# ---------------------------------------------------------------------------
# (c) Duplicate graduation event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_duplicate_graduation_event_is_noop():
    """A duplicate graduation event for the same mint is a no-op end-to-end.

    Models the scenario where the graduation event fires twice (e.g. from the
    Helius reconciler AND the Birdeye sweep for the same token).  The second
    orchestrate() call must leave exactly one 'snapshots' row and must not
    attempt a second REST read.
    """
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch, _ = _make_orch(source, clock)

    # First graduation event
    orch.orchestrate(MINT_A, T0)
    # Duplicate graduation event (same mint, same graduated_at)
    orch.orchestrate(MINT_A, T0)

    assert Snapshot.objects.filter(mint=MINT_A).count() == 1
    assert source.call_count == 1, (
        f"Duplicate graduation event must not trigger a second REST read; "
        f"DataSource called {source.call_count} times"
    )


# ---------------------------------------------------------------------------
# (d) Multiple retries — row count invariant
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_multiple_retries_always_exactly_one_row():
    """Calling orchestrate() N times always yields exactly one 'snapshots' row."""
    from core.models import Snapshot

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = CountingSnapshotSource(SAMPLE_PAYLOAD)
    orch, _ = _make_orch(source, clock)

    for _ in range(5):
        orch.orchestrate(MINT_A, T0)

    assert Snapshot.objects.filter(mint=MINT_A).count() == 1
    assert source.call_count == 1, (
        f"After 5 orchestrate() calls, DataSource must be called exactly once; "
        f"got {source.call_count}"
    )


# ---------------------------------------------------------------------------
# (e) Independent mints are unaffected
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_idempotency_does_not_block_different_mints():
    """At-most-once guard for MINT_A must not block a legitimate first fetch for MINT_B."""
    from core.models import Snapshot

    class MultiMintSource(SnapshotDataSource):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_snapshot(self, mint: str, as_of: datetime) -> dict:
            self.calls.append(mint)
            return SAMPLE_PAYLOAD

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = MultiMintSource()

    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=lambda: _make_config(SCORE_AT_S),
    )

    orch.orchestrate(MINT_A, T0)
    orch.orchestrate(MINT_A, T0)  # retry — must be no-op
    orch.orchestrate(MINT_B, T0)  # different mint — must fire

    assert Snapshot.objects.filter(mint=MINT_A).count() == 1
    assert Snapshot.objects.filter(mint=MINT_B).count() == 1
    assert source.calls == [MINT_A, MINT_B], (
        f"DataSource must be called once per unique mint; got calls: {source.calls}"
    )
