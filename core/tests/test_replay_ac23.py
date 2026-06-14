# ---
# module: core.tests.test_replay_ac23
# sprint: sprint-2
# story: US-2 AC-2.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.clock, core.datasource, core.live_source, core.replay_source
# ---
"""AC-2.3 — No-op ReplaySource resolves through the same code path as LiveSource.

Tests:
  1. test_noop_replay_empty_log_resolves
       stamp_events(ReplaySource([]), VirtualClock) completes without error and
       yields zero (event, ts) pairs — the P0 offline gate:
       'a no-op Replay source resolves'.

  2. test_fixture_replay_yields_events_via_stamp_events
       stamp_events(ReplaySource(fixture), VirtualClock) yields all fixture
       events stamped with the injected VirtualClock's time, in order.

  3. test_live_source_through_same_code_path
       stamp_events(LiveSource(), VirtualClock) completes without error through
       the identical stamp_events code path — proving source-type independence.

  4. test_both_sources_polymorphic_via_datasource
       Both ReplaySource and LiveSource are typed as DataSource and passed to
       the same stamp_events call site — confirming the 'same code path'
       invariant: stamp_events dispatches identically for both concrete types.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
DELTA = timedelta(seconds=5)

FIXTURE_EVENTS: list[dict[str, Any]] = [
    {"type": "swap", "mint": "TOKEN_A", "amount": 100},
    {"type": "swap", "mint": "TOKEN_B", "amount": 200},
    {"type": "swap", "mint": "TOKEN_C", "amount": 300},
]


async def _drain(source, clock) -> list[tuple[dict[str, Any], datetime]]:
    """Collect all (event, timestamp) pairs from stamp_events via the injected source."""
    from core.clock import stamp_events

    return [(e, ts) async for e, ts in stamp_events(source, clock)]


# ---------------------------------------------------------------------------
# 1. No-op ReplaySource (empty log) — P0 offline gate
# ---------------------------------------------------------------------------


def test_noop_replay_empty_log_resolves() -> None:
    """Empty ReplaySource resolves through stamp_events with VirtualClock.

    P0 offline gate: 'a no-op Replay source resolves'.  The generator must
    complete without raising and yield exactly zero events.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource

    async def _run() -> list:
        return await _drain(ReplaySource(event_log=[]), VirtualClock(T0))

    results = asyncio.run(_run())
    assert results == []


# ---------------------------------------------------------------------------
# 2. Fixture-based ReplaySource through stamp_events
# ---------------------------------------------------------------------------


def test_fixture_replay_yields_events_via_stamp_events() -> None:
    """ReplaySource over a fixture log yields every event via stamp_events with VirtualClock.

    The VirtualClock is advanced by DELTA after each event is consumed so that
    successive timestamps are deterministic and distinguishable.
    """
    from core.clock import VirtualClock, stamp_events
    from core.replay_source import ReplaySource

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=FIXTURE_EVENTS)
        clock = VirtualClock(T0)
        results: list[tuple[dict[str, Any], datetime]] = []
        async for event, ts in stamp_events(source, clock):
            results.append((event, ts))
            clock.advance(DELTA)
        return results

    results = asyncio.run(_run())

    assert len(results) == len(FIXTURE_EVENTS)
    for i, (event, ts) in enumerate(results):
        assert event == FIXTURE_EVENTS[i], f"Event {i} payload mismatch"
        assert ts == T0 + i * DELTA, f"Event {i}: wrong timestamp"


# ---------------------------------------------------------------------------
# 3. LiveSource through the same stamp_events code path
# ---------------------------------------------------------------------------


def test_live_source_through_same_code_path() -> None:
    """LiveSource stub resolves through stamp_events with VirtualClock.

    The P0 LiveSource stub yields no events, so the generator completes
    immediately — but it MUST flow through the identical stamp_events code
    path that handles ReplaySource.
    """
    from core.clock import VirtualClock
    from core.live_source import LiveSource

    async def _run() -> list:
        return await _drain(LiveSource(), VirtualClock(T0))

    results = asyncio.run(_run())
    assert results == []


# ---------------------------------------------------------------------------
# 4. Polymorphic dispatch — same call site, both concrete types
# ---------------------------------------------------------------------------


def test_both_sources_polymorphic_via_datasource() -> None:
    """Both ReplaySource and LiveSource resolve via the same stamp_events call site.

    Each source is typed as DataSource and passed to _drain() — the same
    function that wraps stamp_events.  This proves the 'same code path'
    invariant at the type level: stamp_events is source-type-agnostic.
    """
    from core.clock import VirtualClock
    from core.datasource import DataSource
    from core.live_source import LiveSource
    from core.replay_source import ReplaySource

    single_event: list[dict[str, Any]] = [{"event": "grad", "mint": "PUMP123"}]

    # Both declared as DataSource — the interface type, not the concrete type
    replay_src: DataSource = ReplaySource(event_log=single_event)
    live_src: DataSource = LiveSource()

    async def _run() -> tuple[list, list]:
        replay_results = await _drain(replay_src, VirtualClock(T0))
        live_results = await _drain(live_src, VirtualClock(T0))
        return replay_results, live_results

    replay_results, live_results = asyncio.run(_run())

    # ReplaySource yields the single fixture event stamped at T0
    assert len(replay_results) == 1
    assert replay_results[0][0] == single_event[0]
    assert replay_results[0][1] == T0

    # LiveSource stub yields nothing — same path, different data
    assert live_results == []
