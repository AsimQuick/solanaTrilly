# ---
# module: core.tape.mapped_source
# sprint: sprint-5
# story: US-22 AC-22.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, typing
# ---
"""MappedSwapSource — a DataSource that applies a pure mapper to an inner source.

Wraps any DataSource (live BirdeyeSwapSource, or a ReplaySource over the raw
golden capture) and yields the mapped INTERNAL swap dicts the recorder consumes
(§7.1).  Events the mapper returns None for are skipped, so a malformed Birdeye
message never reaches the recorder.

This is the single seam where raw Birdeye-native events become the one internal
schema — used identically on the live path and the offline replay/parity path
(so US-21 replays the *real* banked capture through the same mapper the live
listener uses).  It imports no concrete source and no clock (US-2 / AC-2.2
guards stay green).
"""
from typing import Any, AsyncGenerator, Callable, Optional

from core.datasource import DataSource

Mapper = Callable[[dict[str, Any]], Optional[dict[str, Any]]]


class MappedSwapSource(DataSource):
    """DataSource decorator: yields mapper(raw) for each inner event, skipping None.

    Args:
        inner:  The wrapped DataSource (e.g. BirdeyeSwapSource or ReplaySource).
        mapper: Pure function raw_event -> internal_event_dict | None.
    """

    def __init__(self, inner: DataSource, mapper: Mapper) -> None:
        self._inner: DataSource = inner
        self._mapper: Mapper = mapper

    async def connect(self) -> None:
        await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        async for raw in self._inner.events():
            mapped = self._mapper(raw)
            if mapped is not None:
                yield mapped
