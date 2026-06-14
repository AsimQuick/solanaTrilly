# ---
# module: core.clock
# sprint: sprint-2
# story: US-2 AC-2.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: abc, datetime, typing, core.datasource
# ---
"""Clock abstractions for the solanaTrilly core pipeline.

Provides an injectable Clock interface so that ALL core logic reads 'now'
from the injected clock rather than calling datetime.now() or time.time()
directly.  This enables deterministic replay and offline testing.

Classes:
    Clock        — abstract base; only one method: now() -> datetime
    WallClock    — production clock; delegates to datetime.now(UTC)
    VirtualClock — replay/test clock; time is advanced explicitly

Functions:
    stamp_events — async generator: wraps a DataSource + Clock and yields
                   (event, timestamp) pairs using the injected clock only.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Any

from core.datasource import DataSource


class Clock(ABC):
    """Abstract clock interface.  All core code must read 'now' via this."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time as a UTC-aware datetime."""


class WallClock(Clock):
    """Production clock backed by real wall time (UTC)."""

    def now(self) -> datetime:
        """Return the current UTC wall-clock time."""
        return datetime.now(tz=timezone.utc)


class VirtualClock(Clock):
    """Deterministic clock for replay and testing.

    Time does not advance on its own; callers drive it forward via
    advance() or set_time().  This is the only correct way to control
    'now' in offline/replay code paths.
    """

    def __init__(self, initial_time: datetime) -> None:
        self._current: datetime = initial_time

    def now(self) -> datetime:
        """Return the current virtual time."""
        return self._current

    def advance(self, delta: timedelta) -> None:
        """Advance virtual time forward by *delta*."""
        self._current += delta

    def set_time(self, t: datetime) -> None:
        """Set virtual time to an explicit value *t*."""
        self._current = t


async def stamp_events(
    source: DataSource,
    clock: Clock,
) -> AsyncGenerator[tuple[dict[str, Any], datetime], None]:
    """Async generator: pair each event from *source* with a timestamp from *clock*.

    Lifecycle:
        1. Calls source.connect().
        2. Iterates source.events(); for each event yields (event, clock.now()).
        3. Calls source.disconnect() when the source is exhausted.

    The injected *clock* is the ONLY source of 'now' — no datetime.now() or
    time.time() calls appear in this function (enforced by the static-analysis
    test in test_clock.py).
    """
    await source.connect()
    try:
        async for event in source.events():
            yield event, clock.now()
    finally:
        await source.disconnect()
