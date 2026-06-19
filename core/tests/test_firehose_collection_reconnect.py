# ---
# module: core.tests.test_firehose_collection_reconnect
# sprint: cutover (inference live)
# story: collection-reconnect
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: pytest, asyncio
# ---
"""The live collection loop must RECONNECT when the Helius WS closes.

Regression for the observed live bug: the Helius WS closed gracefully ~49 min
into a 90-min window, ``buffer.run()`` returned ("buffer finished"), and the OLD
single-run collection loop ENDED — so the pre-grad buffer went stale and nothing
graduating afterward could ever score.  The loop must instead rebuild the source
and restart, RETAINING the accumulated buffer, until the firehose flips inactive.

Pure-async (ORM-touching internals are patched) so the reconnect control flow is
exercised deterministically without a DB / cross-thread transaction visibility.
"""
import asyncio
from typing import Any, AsyncGenerator

from core.datasource import DataSource
from core.management.commands.run_firehose import FirehoseDaemon


class _BoundedSource(DataSource):
    """A source that yields ONE swap then ends — mimics a WS that closes."""

    def __init__(self, mint: str) -> None:
        self._mint = mint

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "mint": self._mint,
            "block_time": 1,
            "slot": 1,
            "signature": "sig",
            "side": "buy",
            "price": 1.0,
            "vol_sol": 1.0,
            "owner": "owner",
        }


def test_collection_loop_reconnects_then_stops_on_flip():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _BoundedSource(f"Mint{calls['n']}pump")

    daemon = FirehoseDaemon(collection_factory=factory)
    # Patch the ORM-touching internals so the test is pure-async (no DB).
    daemon._resolve_pre_grad_ttl_sync = lambda: 300.0
    daemon._collection_graduated = lambda mint: False

    # _read_state: active after the 1st run (forces a reconnect), then inactive
    # after the 2nd (forces a clean exit).  The OLD code never called _read_state
    # at all — it ran the source exactly once and returned.
    states = [(True, True, False), (False, True, False)]

    async def fake_read_state():
        return states.pop(0) if states else (False, True, False)

    daemon._read_state = fake_read_state

    asyncio.run(asyncio.wait_for(daemon._collection_loop(), timeout=5.0))

    # Reconnected at least once (the OLD code would call the factory exactly once).
    assert calls["n"] >= 2, f"expected reconnect; factory called {calls['n']}x"
    # The buffer RETAINED swaps across reconnects (same TapeStore reused).
    assert daemon._tape.count("Mint1pump") >= 1
    assert daemon._tape.count("Mint2pump") >= 1
