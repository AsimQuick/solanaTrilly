# ---
# module: core.tests.test_snapshot_rate_limiter_ac244
# sprint: sprint-6
# story: US-24 AC-24.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_fetcher, core.snapshot_source, core.clock, django.conf, pathlib, json
# ---
"""AC-24.4 — Redis token-bucket rate limiter gates the on-demand snapshot read.

Two verification prongs per the AC:

Prong A — Limiter is consulted BEFORE the DataSource read:
  1. test_limiter_consulted_before_datasource_read — call_order == ['limiter', 'source']
  2. test_limiter_called_exactly_once_per_fetch — limiter called exactly once per fetch
  3. test_limiter_not_called_on_second_fetch_same_mint — at-most-one guard fires before
     the limiter (second fetch for same mint returns None, limiter not called again)
  4. test_fetch_works_without_limiter — limiter=None (default) still works (backward compat)
  5. test_limiter_called_once_per_independent_mint — two different mints each trigger
     exactly one limiter call each

Prong B — H2 manifest: no periodic/scheduled snapshot task:
  6. test_no_snapshot_task_in_celery_beat_schedule — CELERY_BEAT_SCHEDULE has no
     key or task name containing 'snapshot'
  7. test_no_snapshot_task_in_task_manifest — core/task_manifest.json has no task
     name containing 'snapshot'
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from core.clock import VirtualClock
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

MINT_A = "AC244TestMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "AC244TestMintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.38, "count": 750},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 6000.0,
    "tvl": 5800.0,
    "depth": {"bid": 120.0, "ask": 115.0},
}

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeLimiter:
    """In-memory fake limiter that records calls and appends to a shared list."""

    def __init__(self, call_order: list) -> None:
        self._call_order = call_order
        self.call_count: int = 0

    def wait_for_token(self) -> None:
        self._call_order.append("limiter")
        self.call_count += 1


class OrderTrackingSource(SnapshotDataSource):
    """In-memory DataSource that records calls and appends to a shared list."""

    def __init__(self, call_order: list) -> None:
        self._call_order = call_order
        self.call_count: int = 0

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self._call_order.append("source")
        self.call_count += 1
        return SAMPLE_PAYLOAD


class SimpleSource(SnapshotDataSource):
    """In-memory DataSource that returns SAMPLE_PAYLOAD without tracking order."""

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        return SAMPLE_PAYLOAD


# ---------------------------------------------------------------------------
# Prong A — Limiter consulted before DataSource read
# ---------------------------------------------------------------------------


def test_limiter_consulted_before_datasource_read():
    """Limiter.wait_for_token() is called BEFORE DataSource.get_snapshot().

    Uses a shared call_order list appended to by both FakeLimiter and
    OrderTrackingSource.  After a single fetch() the order must be
    ['limiter', 'source'].
    """
    from core.snapshot_fetcher import SnapshotFetcher

    call_order: list = []
    limiter = FakeLimiter(call_order)
    source = OrderTrackingSource(call_order)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0), limiter=limiter)

    fetcher.fetch(MINT_A)

    assert call_order == ["limiter", "source"], (
        f"Expected call order ['limiter', 'source'], got {call_order!r}. "
        "The rate limiter must be consulted BEFORE the DataSource read (AC-24.4)."
    )


def test_limiter_called_exactly_once_per_fetch():
    """Limiter.wait_for_token() is called exactly once for a single fetch() call."""
    from core.snapshot_fetcher import SnapshotFetcher

    call_order: list = []
    limiter = FakeLimiter(call_order)
    source = OrderTrackingSource(call_order)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0), limiter=limiter)

    fetcher.fetch(MINT_A)

    assert limiter.call_count == 1, (
        f"Expected limiter.wait_for_token() called exactly once; "
        f"got {limiter.call_count} call(s). AC-24.4: one rate-limit token per snapshot."
    )


def test_limiter_not_called_on_second_fetch_same_mint():
    """Second fetch for the same mint returns None; limiter is NOT called again.

    The at-most-one guard (§6.3) fires first, before the limiter.  The fetcher
    exits early with None — it must not consume a rate-limit token for a no-op.
    """
    from core.snapshot_fetcher import SnapshotFetcher

    call_order: list = []
    limiter = FakeLimiter(call_order)
    source = OrderTrackingSource(call_order)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0), limiter=limiter)

    first = fetcher.fetch(MINT_A)
    second = fetcher.fetch(MINT_A)  # at-most-one guard triggers here

    assert first == SAMPLE_PAYLOAD, "First fetch must return the payload"
    assert second is None, "Second fetch for same mint must return None (at-most-one §6.3)"
    assert limiter.call_count == 1, (
        f"Limiter must only be called once (for the first fetch); "
        f"got {limiter.call_count} call(s). The at-most-one guard must fire "
        "BEFORE the limiter on a repeated mint."
    )


def test_fetch_works_without_limiter():
    """When limiter=None (the default), fetch() still works normally.

    All callers that don't supply a limiter must remain backward-compatible.
    """
    from core.snapshot_fetcher import SnapshotFetcher

    source = SimpleSource()
    # No limiter — should not raise, should return payload
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    result = fetcher.fetch(MINT_A)

    assert result == SAMPLE_PAYLOAD, (
        "fetch() without a limiter must return the payload from the DataSource. "
        "Backward compatibility broken."
    )


def test_limiter_called_once_per_independent_mint():
    """Two independent mints each trigger exactly one limiter call.

    Total limiter calls must be 2 (one per mint, not 0, not 4).
    """
    from core.snapshot_fetcher import SnapshotFetcher

    call_order: list = []
    limiter = FakeLimiter(call_order)
    source = OrderTrackingSource(call_order)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0), limiter=limiter)

    result_a = fetcher.fetch(MINT_A)
    result_b = fetcher.fetch(MINT_B)

    assert result_a == SAMPLE_PAYLOAD, "Fetch for MINT_A must return payload"
    assert result_b == SAMPLE_PAYLOAD, "Fetch for MINT_B must return payload"
    assert limiter.call_count == 2, (
        f"Expected exactly 2 limiter calls (one per mint); "
        f"got {limiter.call_count}. AC-24.4: each on-demand fetch consumes one token."
    )
    assert source.call_count == 2, (
        f"Expected exactly 2 DataSource calls; got {source.call_count}."
    )


# ---------------------------------------------------------------------------
# Prong B — H2 manifest: no periodic/scheduled snapshot task
# ---------------------------------------------------------------------------


def test_no_snapshot_task_in_celery_beat_schedule():
    """CELERY_BEAT_SCHEDULE must contain no key or task name with 'snapshot'.

    The per-token-poll scheduler regime is RETIRED (AC-24.4).  No periodic
    Celery-beat task should poll for snapshots.  The snapshot is on-demand only.
    """
    from django.conf import settings

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})

    for schedule_key, entry in beat_schedule.items():
        assert "snapshot" not in schedule_key.lower(), (
            f"CELERY_BEAT_SCHEDULE key {schedule_key!r} contains 'snapshot'. "
            "The per-token-poll snapshot scheduler must be DROPPED (AC-24.4 §6.3). "
            "Snapshots are on-demand only."
        )
        task_name = entry.get("task", "")
        assert "snapshot" not in task_name.lower(), (
            f"CELERY_BEAT_SCHEDULE entry {schedule_key!r} has task={task_name!r} "
            "which contains 'snapshot'. The per-token-poll snapshot scheduler must "
            "be DROPPED (AC-24.4 §6.3). Snapshots are on-demand only."
        )


def test_no_snapshot_task_in_task_manifest():
    """core/task_manifest.json must contain no task name with 'snapshot'.

    The H2 manifest test confirms the snapshot is on-demand only — no
    periodic/scheduled snapshot task should be registered as a Celery task.
    """
    assert MANIFEST_PATH.exists(), (
        f"task_manifest.json not found at expected path: {MANIFEST_PATH}"
    )

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tasks = manifest.get("tasks", [])

    for task_name in tasks:
        assert "snapshot" not in task_name.lower(), (
            f"task_manifest.json contains task {task_name!r} which includes 'snapshot'. "
            "The per-token-poll snapshot scheduler is RETIRED (AC-24.4 §6.3). "
            "Snapshots are on-demand only — no scheduled/periodic snapshot task "
            "should appear in the manifest."
        )
