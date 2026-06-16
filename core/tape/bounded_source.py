# ---
# module: core.tape.bounded_source
# sprint: sprint-5
# story: US-22 AC-22.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, asyncio, typing
# ---
"""BoundedSource — a DataSource that stops after a max event count or time-box.

A deliberate firehose activation (PRD §15.7) is time-boxed (<= 30 min) and/or
capped at N events.  The TapeRecorder only persists to the lake + 'swaps' table
AFTER its source iterator is exhausted (it batches on completion), so a live
stream — which never ends on its own — must be bounded for the recording to
land.  BoundedSource wraps any inner DataSource and ends the iteration cleanly
(so the recorder's post-loop writes run) once either bound is hit:

  * max_events  — stop after this many events have been yielded.
  * max_seconds — stop this many seconds after the first event-wait begins.

The deadline is enforced even during WebSocket silence: each pull from the inner
generator is awaited with a timeout, so a quiet stream still terminates at the
box.  Time is read via asyncio's monotonic loop clock (loop.time()) — never
datetime.now()/time.time(), so the core/ clock guard (AC-2.2) stays green and
this remains independent of the injected data Clock.
"""
import asyncio
from typing import Any, AsyncGenerator, Optional

from core.datasource import DataSource


class BoundedSource(DataSource):
    """DataSource decorator that ends iteration after max_events or max_seconds.

    Args:
        inner:       The wrapped DataSource.
        max_events:  Stop after yielding this many events (None = no count cap).
        max_seconds: Stop this many seconds after iteration begins (None = no
                     time cap).  At least one bound should be set for a live
                     source, or it will run until the inner source ends.
    """

    def __init__(
        self,
        inner: DataSource,
        *,
        max_events: Optional[int] = None,
        max_seconds: Optional[float] = None,
    ) -> None:
        self._inner: DataSource = inner
        self._max_events: Optional[int] = max_events
        self._max_seconds: Optional[float] = max_seconds
        self._emitted: int = 0

    @property
    def emitted(self) -> int:
        """Number of events yielded so far."""
        return self._emitted

    async def connect(self) -> None:
        await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        loop = asyncio.get_event_loop()
        deadline = (
            loop.time() + self._max_seconds if self._max_seconds is not None else None
        )
        inner_gen = self._inner.events().__aiter__()

        while True:
            if self._max_events is not None and self._emitted >= self._max_events:
                return

            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                try:
                    event = await asyncio.wait_for(inner_gen.__anext__(), timeout=remaining)
                except asyncio.TimeoutError:
                    return  # time-box reached during silence — end cleanly so writes run
                except StopAsyncIteration:
                    return
            else:
                try:
                    event = await inner_gen.__anext__()
                except StopAsyncIteration:
                    return

            self._emitted += 1
            yield event
