# ---
# module: core.snapshot_fetcher
# sprint: sprint-6
# story: US-24 AC-24.1, US-24 AC-24.2, US-24 AC-24.3, US-24 AC-24.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.clock, core.snapshot_source, core.models, core.rate_limiter, datetime
# ---
"""SnapshotFetcher — at-most-one on-demand score-time snapshot per token.

Reads from a SnapshotDataSource seam (never a concrete Birdeye REST client)
and reads time from an injected Clock (never datetime.now() or time.time()).
This is the live/replay seam for snapshot fetching (Principle #7 / US-2).

At-most-one-per-token (§6.3): once a mint has been fetched, subsequent calls
to fetch() return None without calling the DataSource again.  The concrete
Birdeye REST adapter (BirdeyeSnapshotSource) is injected at startup and is
never imported here.

fetch_and_persist() extends fetch() to store the raw payload verbatim in the
'snapshots' row (§8, §6.4.1) via Snapshot.objects.update_or_create, using
JsonSafeEncoder (via the JSONField on Snapshot.raw) so the H3/US-5 guard
stays green.

#380 future-window clamp: fetch() accepts an optional as_of parameter.  If
provided and it exceeds clock.now(), it is silently clamped to clock.now()
before being forwarded to the DataSource.  Birdeye returns HTTP 400 on any
request whose to= / window= is in the future; this clamp ensures no future
window ever leaves the fetcher regardless of what the caller supplies.
"""
from datetime import datetime
from typing import Optional

from core.clock import Clock
from core.models import Snapshot
from core.snapshot_source import SnapshotDataSource


class SnapshotFetcher:
    """At-most-one on-demand snapshot fetcher (§6.3, Principle #7).

    Accepts a SnapshotDataSource (the abstract seam), a Clock (the time seam),
    and an optional RateLimiter (AC-24.4).  The limiter gates the DataSource
    read so that on-demand REST calls cannot burst beyond the token-bucket
    capacity.  Pass limiter=None (the default) to disable rate limiting — all
    existing callers are backward-compatible.

    Guarantees that get_snapshot() is called at most once per mint address.
    """

    def __init__(self, source: SnapshotDataSource, clock: Clock, limiter=None) -> None:
        self._source = source
        self._clock = clock
        self._limiter = limiter
        self._fetched: set[str] = set()

    def fetch(self, mint: str, as_of: Optional[datetime] = None) -> Optional[dict]:
        """Fetch a score-time snapshot for mint.  At-most-once per token.

        Returns the raw snapshot dict from the DataSource on the first call for
        a given mint, or None if a snapshot for this mint was already taken
        (at-most-one discipline — §6.3).

        as_of is the requested window endpoint forwarded to the DataSource.
        If omitted (None), clock.now() is used.  If provided but beyond
        clock.now(), it is clamped to clock.now() (#380 future-window clamp):
        no future timestamp is ever sent to Birdeye (Birdeye HTTP 400).
        """
        if mint in self._fetched:
            return None
        now = self._clock.now()
        # #380 clamp: never forward a future window to the DataSource
        effective_as_of = min(as_of, now) if as_of is not None else now
        # AC-24.4: gate the DataSource read through the Redis token-bucket limiter
        if self._limiter is not None:
            self._limiter.wait_for_token()
        raw = self._source.get_snapshot(mint, as_of=effective_as_of)
        self._fetched.add(mint)
        return raw

    def already_fetched(self, mint: str) -> bool:
        """Return True if a snapshot for this mint has already been taken."""
        return mint in self._fetched

    def fetch_and_persist(self, mint: str, elapsed_s: int) -> Optional[dict]:
        """Fetch the snapshot and persist it as an immutable 'snapshots' row (§6.4.1).

        Calls fetch() internally — inherits the at-most-one-per-token guard
        (§6.3).  If the mint was already fetched, returns None without
        touching the DB.

        On the first call for a mint:
          • Gets now from the injected Clock (the only source of time — Principle #7).
          • Calls the DataSource to obtain the raw payload.
          • Persists via Snapshot.objects.update_or_create so that a retry or
            restart cannot create a duplicate row (US-23.2 idempotency).
          • raw is stored verbatim; the JSONField's JsonSafeEncoder keeps the
            H3/US-5 guard green (NaN/Inf → null, Decimal → float).

        elapsed_s is seconds since the token's graduated_at — the caller
        (orchestration) supplies this value (score_at_elapsed_s from
        get_active_config(), US-25); the fetcher does not derive it.
        """
        taken_at = self._clock.now()
        raw = self.fetch(mint)
        if raw is None:
            return None
        Snapshot.objects.update_or_create(
            mint=mint,
            defaults={"taken_at": taken_at, "elapsed_s": elapsed_s, "raw": raw},
        )
        return raw
