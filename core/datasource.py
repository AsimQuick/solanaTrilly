# ---
# module: core.datasource
# sprint: sprint-2
# story: US-2 AC-2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: abc, typing
# ---
"""DataSource abstract interface — the single seam between the live pipeline and replay.

Consumers (detection, feature-assembly, scoring, exit, settlement) depend ONLY
on this interface.  No consumer may import LiveSource or ReplaySource directly
(Principle #7 / PRD §4).
"""
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator


class DataSource(ABC):
    """Abstract event-stream interface.

    Lifecycle: call connect() before iterating events(); call disconnect() when done.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection to the underlying data source."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the connection."""

    @abstractmethod
    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that yields raw event dicts from the source.

        Each event is a plain dict; the schema is defined by the emitting source
        (Birdeye swap events for LiveSource; fixture records for ReplaySource).
        Consumers must not inspect the concrete type — only call this method.
        """
