# ---
# module: core.replay_source
# sprint: sprint-2
# story: US-2 AC-2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource
# ---
"""ReplaySource — concrete DataSource that replays events from an in-memory sequence.

Used in offline testing and the replay harness.  Accepts any iterable of event
dicts at construction time and yields them in order through events().

Consumers MUST NOT import this class directly — inject DataSource instead.
"""
from typing import Any, AsyncGenerator, Iterable

from core.datasource import DataSource


class ReplaySource(DataSource):
    """Concrete DataSource that replays a pre-loaded event log in order."""

    def __init__(self, event_log: Iterable[dict[str, Any]] = ()) -> None:
        self._event_log: list[dict[str, Any]] = list(event_log)

    async def connect(self) -> None:
        """No-op: replay needs no network connection."""

    async def disconnect(self) -> None:
        """No-op: nothing to close."""

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield each event from the pre-loaded log in insertion order."""
        for event in self._event_log:
            yield event
