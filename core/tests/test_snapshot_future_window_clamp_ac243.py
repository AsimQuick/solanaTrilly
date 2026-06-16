# ---
# module: core.tests.test_snapshot_future_window_clamp_ac243
# sprint: sprint-6
# story: US-24 AC-24.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_fetcher, core.snapshot_source, core.clock, datetime
# ---
"""AC-24.3 — #380 future-window clamp: no future to=/window ever leaves the fetcher.

Birdeye returns HTTP 400 on any REST request whose to= or window= parameter is
in the future.  The fetcher clamps any requested as_of to min(as_of, clock.now())
before forwarding it to the DataSource — the concrete Birdeye adapter never
receives a future timestamp.

Tests:
  1. A future as_of is clamped to clock.now() (the main AC assertion).
  2. A far-future as_of is still clamped to clock.now().
  3. as_of == clock.now() passes through unclamped.
  4. as_of < clock.now() (a past window) passes through unclamped.
  5. as_of=None (default) causes clock.now() to be used.
  6. The DataSource never receives a timestamp exceeding clock.now() even after
     the clock advances between the as_of construction and the fetch call.
  7. The #380 clamp does not bypass the at-most-one-per-token guard (§6.3).
"""
from datetime import datetime, timedelta, timezone

from core.clock import VirtualClock
from core.snapshot_fetcher import SnapshotFetcher
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
ONE_HOUR = timedelta(hours=1)
ONE_DAY = timedelta(days=1)
ONE_MINUTE_AGO = timedelta(minutes=1)

TEST_MINT = "AC243TestMint1111111111111111111111111111111"

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.40, "count": 800},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 3000.0,
    "tvl": 2900.0,
    "depth": {"bid": 80.0, "ask": 75.0},
}

# ---------------------------------------------------------------------------
# In-memory DataSource test double — records the as_of it receives
# ---------------------------------------------------------------------------


class RecordingSnapshotSource(SnapshotDataSource):
    """Test double: records every (mint, as_of) pair received from the fetcher."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.received: list[tuple[str, datetime]] = []

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self.received.append((mint, as_of))
        return self._payload


# ---------------------------------------------------------------------------
# 1. Future as_of is clamped to clock.now()
# ---------------------------------------------------------------------------


def test_future_window_is_clamped_to_clock_now():
    """AC-24.3 main assertion: an as_of beyond clock.now() is clamped to now().

    The DataSource must receive clock.now() (T0), not the future timestamp.
    """
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    future_as_of = T0 + ONE_HOUR
    fetcher.fetch(TEST_MINT, as_of=future_as_of)

    assert len(source.received) == 1, "DataSource must be called exactly once"
    _, received_as_of = source.received[0]
    assert received_as_of == T0, (
        f"#380 clamp failed: DataSource received {received_as_of!r}, "
        f"expected {T0!r} (clock.now()). Future window leaked to DataSource."
    )


# ---------------------------------------------------------------------------
# 2. Far-future as_of is also clamped
# ---------------------------------------------------------------------------


def test_far_future_window_is_clamped_to_clock_now():
    """A far-future as_of (one day ahead) is also clamped to clock.now()."""
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    far_future = T0 + ONE_DAY
    fetcher.fetch(TEST_MINT, as_of=far_future)

    _, received_as_of = source.received[0]
    assert received_as_of == T0, (
        f"#380 clamp failed for far-future: got {received_as_of!r}, expected {T0!r}"
    )


# ---------------------------------------------------------------------------
# 3. as_of == clock.now() passes through unclamped
# ---------------------------------------------------------------------------


def test_present_window_passes_through_unclamped():
    """as_of exactly equal to clock.now() is forwarded unchanged (no clamping needed)."""
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    fetcher.fetch(TEST_MINT, as_of=T0)

    _, received_as_of = source.received[0]
    assert received_as_of == T0, (
        f"Present window should pass through; got {received_as_of!r}"
    )


# ---------------------------------------------------------------------------
# 4. Past as_of passes through unclamped
# ---------------------------------------------------------------------------


def test_past_window_passes_through_unclamped():
    """as_of before clock.now() (a past window) is forwarded unchanged.

    Past windows are valid Birdeye requests; the clamp must not alter them.
    """
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    past_as_of = T0 - ONE_MINUTE_AGO
    fetcher.fetch(TEST_MINT, as_of=past_as_of)

    _, received_as_of = source.received[0]
    assert received_as_of == past_as_of, (
        f"Past window should not be clamped; got {received_as_of!r}, "
        f"expected {past_as_of!r}"
    )


# ---------------------------------------------------------------------------
# 5. as_of=None uses clock.now() (default / backward-compatible behaviour)
# ---------------------------------------------------------------------------


def test_no_as_of_uses_clock_now():
    """When as_of is omitted (None), the DataSource receives clock.now()."""
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    fetcher.fetch(TEST_MINT)  # no as_of

    _, received_as_of = source.received[0]
    assert received_as_of == T0, (
        f"as_of=None must forward clock.now(); got {received_as_of!r}"
    )


# ---------------------------------------------------------------------------
# 6. Clamp holds even when the clock advances between as_of construction and fetch
# ---------------------------------------------------------------------------


def test_clamp_uses_clock_now_at_fetch_time():
    """The clamp reads clock.now() at fetch() call time, not at as_of construction time.

    Scenario: as_of is built before the clock advances; after the advance it is
    in the past, so it should pass through unchanged rather than be clamped.
    This confirms the clamp compares against the clock value AT fetch time.
    """
    clock = VirtualClock(T0)
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=clock)

    # Build an as_of that is currently "now" (T0)
    as_of_at_construction = clock.now()  # == T0

    # Advance the clock so that T0 is now one hour in the past
    clock.advance(ONE_HOUR)
    # clock.now() is now T0 + ONE_HOUR

    # as_of_at_construction (T0) < clock.now() (T0+1h) → should pass through
    fetcher.fetch(TEST_MINT, as_of=as_of_at_construction)

    _, received_as_of = source.received[0]
    assert received_as_of == as_of_at_construction, (
        f"Past as_of should not be clamped; got {received_as_of!r}, "
        f"expected {as_of_at_construction!r}"
    )


# ---------------------------------------------------------------------------
# 7. #380 clamp does not bypass the at-most-one-per-token guard (§6.3)
# ---------------------------------------------------------------------------


def test_clamp_does_not_bypass_at_most_once():
    """A clamped future-window fetch still obeys at-most-one-per-token (§6.3).

    A second fetch for the same mint — even with a different as_of — returns
    None without calling the DataSource again.
    """
    source = RecordingSnapshotSource(SAMPLE_PAYLOAD)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    first = fetcher.fetch(TEST_MINT, as_of=T0 + ONE_HOUR)  # clamped
    second = fetcher.fetch(TEST_MINT, as_of=T0 + ONE_DAY)  # at-most-once guard

    assert first == SAMPLE_PAYLOAD, "First call must return the payload"
    assert second is None, "Second call must return None (at-most-one §6.3)"
    assert len(source.received) == 1, (
        f"DataSource must be called exactly once; got {len(source.received)} calls"
    )
