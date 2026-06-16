# ---
# module: core.replay_snapshot_source
# sprint: sprint-6
# story: US-25 AC-25.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_source, datetime
# ---
"""ReplaySnapshotSource — concrete SnapshotDataSource that replays pre-loaded fixtures.

Used in offline testing (P4 offline gate, §16) and the replay harness.  Accepts a
mapping of mint address → raw snapshot payload at construction time and returns the
pre-loaded payload for the requested mint without any network I/O.

Callers MUST NOT import this class directly — inject SnapshotDataSource instead.
This module is imported ONLY by test files and offline-gate scripts; the core
pipeline path (SnapshotFetcher, ScoreTimeOrchestrator) never references it.
"""
from datetime import datetime

from core.snapshot_source import SnapshotDataSource


class ReplaySnapshotSource(SnapshotDataSource):
    """Concrete SnapshotDataSource that replays pre-loaded snapshot payloads.

    Accepts a dict mapping mint address → raw snapshot payload dict.  Returns the
    pre-loaded payload for the given mint without any network I/O.

    The as_of timestamp is accepted (satisfying the interface contract) and is
    recorded in self.calls for assertion in tests, but it is not used to filter
    or modify the returned payload — callers own the time-clamping logic
    (#380 clamp lives in SnapshotFetcher, not here).

    Attributes:
        call_count: number of times get_snapshot() has been called.
        calls: list of (mint, as_of) tuples recording every call in order.
    """

    def __init__(self, fixtures: dict[str, dict]) -> None:
        self._fixtures = fixtures
        self.call_count: int = 0
        self.calls: list[tuple[str, datetime]] = []

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        """Return the pre-loaded payload for *mint*; record the call for assertions."""
        self.calls.append((mint, as_of))
        self.call_count += 1
        return self._fixtures[mint]
