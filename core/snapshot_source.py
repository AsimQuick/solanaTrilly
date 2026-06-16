# ---
# module: core.snapshot_source
# sprint: sprint-6
# story: US-24 AC-24.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: abc, datetime
# ---
"""SnapshotDataSource — abstract seam for score-time snapshot fetching.

Callers (SnapshotFetcher) depend only on this interface — never on a concrete
REST client (BirdeyeSnapshotSource) or test double (InMemorySnapshotSource).
This is the DataSource seam for snapshot fetching, mirroring Principle #7 / US-2.

Live implementation: BirdeyeSnapshotSource (US-24, AC-24.2 onwards).
Test implementation: InMemorySnapshotSource (in test files only).
"""
from abc import ABC, abstractmethod
from datetime import datetime


class SnapshotDataSource(ABC):
    """Abstract interface for fetching a score-time snapshot payload for a token.

    Callers depend only on this interface.  The concrete Birdeye REST adapter
    is injected at startup and is never imported by the fetcher core.
    """

    @abstractmethod
    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        """Return the raw snapshot payload for mint, as of the given time.

        as_of is the injected-clock now() at fetch time.  Callers must ensure
        it does not exceed the clock's current value so Birdeye never receives
        a future-window request (the #380 clamp lives in the fetcher, not here).

        Returns a dict containing at minimum:
            holder_distribution, mint_authority, freeze_authority,
            lp_burned, liquidity, tvl, depth
        """
