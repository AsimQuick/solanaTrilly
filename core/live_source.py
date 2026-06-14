# ---
# module: core.live_source
# sprint: sprint-2
# story: US-2 AC-2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource
# ---
"""LiveSource — concrete DataSource for the live Birdeye WebSocket feed.

This is a thin P0 stub.  P1 will open the Birdeye WebSocket, authenticate,
subscribe to PumpSwap swap events, and yield them through events().

Consumers MUST NOT import this class directly — inject DataSource instead.
"""
from typing import Any, AsyncGenerator

from core.datasource import DataSource


class LiveSource(DataSource):
    """Concrete DataSource backed by the live Birdeye WebSocket feed (stub for P0)."""

    async def connect(self) -> None:
        """Open the Birdeye WebSocket connection (P1 implementation)."""

    async def disconnect(self) -> None:
        """Close the Birdeye WebSocket connection (P1 implementation)."""

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield live swap events from Birdeye (P1 implementation — no-op stub)."""
        return
        yield  # pragma: no cover — makes this an async generator
