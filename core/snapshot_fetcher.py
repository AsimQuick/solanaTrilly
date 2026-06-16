# ---
# module: core.snapshot_fetcher
# sprint: sprint-6
# story: US-24 AC-24.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.clock, core.snapshot_source
# ---
"""SnapshotFetcher — at-most-one on-demand score-time snapshot per token.

Reads from a SnapshotDataSource seam (never a concrete Birdeye REST client)
and reads time from an injected Clock (never datetime.now() or time.time()).
This is the live/replay seam for snapshot fetching (Principle #7 / US-2).

At-most-one-per-token (§6.3): once a mint has been fetched, subsequent calls
to fetch() return None without calling the DataSource again.  The concrete
Birdeye REST adapter (BirdeyeSnapshotSource) is injected at startup and is
never imported here.
"""
from typing import Optional

from core.clock import Clock
from core.snapshot_source import SnapshotDataSource


class SnapshotFetcher:
    """At-most-one on-demand snapshot fetcher (§6.3, Principle #7).

    Accepts a SnapshotDataSource (the abstract seam) and a Clock (the time seam).
    Guarantees that get_snapshot() is called at most once per mint address.
    """

    def __init__(self, source: SnapshotDataSource, clock: Clock) -> None:
        self._source = source
        self._clock = clock
        self._fetched: set[str] = set()

    def fetch(self, mint: str) -> Optional[dict]:
        """Fetch a score-time snapshot for mint.  At-most-once per token.

        Returns the raw snapshot dict from the DataSource on the first call for
        a given mint, or None if a snapshot for this mint was already taken
        (at-most-one discipline — §6.3).

        The as_of time passed to the DataSource is always clock.now() — the
        injected Clock provides 'now', not the system clock.  This enables
        deterministic replay and proves the fetcher never bypasses the seam.
        """
        if mint in self._fetched:
            return None
        now = self._clock.now()
        raw = self._source.get_snapshot(mint, as_of=now)
        self._fetched.add(mint)
        return raw

    def already_fetched(self, mint: str) -> bool:
        """Return True if a snapshot for this mint has already been taken."""
        return mint in self._fetched
