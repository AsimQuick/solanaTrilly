# ---
# module: core.detection.consumer
# sprint: sprint-4
# story: US-15 AC-15.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource, core.clock, datetime, typing
# ---
"""DetectionConsumer — reads MEME graduation events from a DataSource seam.

The consumer depends ONLY on the abstract DataSource and Clock interfaces
(Principle #7 / PRD §4).  It never imports a concrete source class (LiveSource,
ReplaySource) and never calls datetime.now() or time.time() directly.

Time is read exclusively via the injected Clock, passed through stamp_events().
This enables deterministic replay: swap the DataSource for a ReplaySource and
the Clock for a VirtualClock to replay any historical tape byte-identically.
"""
from datetime import datetime
from typing import Any

from core.clock import Clock, stamp_events
from core.datasource import DataSource


class DetectionConsumer:
    """Reads events from a DataSource and timestamps them via an injected Clock.

    This is the live/replay seam for the detection path (US-2 Principle #7).
    The consumer never references a concrete source class or the system clock.

    Args:
        source: Any DataSource implementation (live or replay).
        clock:  Any Clock implementation (wall or virtual).
    """

    def __init__(self, source: DataSource, clock: Clock) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._processed: list[tuple[dict[str, Any], datetime]] = []

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    async def run(self) -> None:
        """Consume all events from the source, stamping each with the injected clock.

        For each event yielded by stamp_events(), the (event, timestamp) pair is
        stored in self._processed.  The method returns when the source is exhausted.
        """
        async for event, timestamp in stamp_events(self._source, self._clock):
            self._processed.append((event, timestamp))
