# ---
# module: core.tape.recorder
# sprint: sprint-5
# story: US-18 AC-18.1, US-18 AC-18.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource, core.clock, core.normalized_swap, datetime, typing
# ---
"""TapeRecorder — reads swap events from a DataSource seam with an injected clock.

The recorder depends ONLY on the abstract DataSource and Clock interfaces
(Principle #7 / PRD §4).  It never imports a concrete source class (LiveSource,
ReplaySource) and never calls datetime.now() or time.time() directly.

Time is read exclusively via the injected Clock, passed through stamp_events().
This enables deterministic replay: swap the DataSource for a ReplaySource and
the Clock for a VirtualClock to replay any historical tape byte-identically.

AC-18.2 extension:
    For each LANDED swap (failed != True), exactly one NormalizedSwap is emitted,
    anchored to the token's graduated_block_time.  Failed swaps are dropped (§6.2).
    owner is always the tx signer (Birdeye 'owner' field, canonical per §3.3).
"""
from datetime import datetime
from typing import Any

from core.clock import Clock, stamp_events
from core.datasource import DataSource
from core.normalized_swap import NormalizedSwap


class TapeRecorder:
    """Reads swap events from a DataSource, timestamps them via an injected Clock.

    AC-18.1: DataSource/Clock seam — live/replay parity (Principle #7).
    AC-18.2: Emits one NormalizedSwap per landed swap; drops failed swaps (§6.2).

    Args:
        source:      Any DataSource implementation (live or replay).
        clock:       Any Clock implementation (wall or virtual).
        token_store: Dict mapping mint → token-like object with graduated_block_time.
                     Required for NormalizedSwap emission (AC-18.2).
                     Defaults to {} — no normalization when empty.
        swap_source: NormalizedSwap source vocabulary value. Defaults to "birdeye_live".
        swap_phase:  NormalizedSwap phase vocabulary value. Defaults to "pre".
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        token_store: dict[str, Any] | None = None,
        *,
        swap_source: str = "birdeye_live",
        swap_phase: str = "pre",
    ) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._token_store: dict[str, Any] = token_store if token_store is not None else {}
        self._swap_source: str = swap_source
        self._swap_phase: str = swap_phase
        self._processed: list[tuple[dict[str, Any], datetime]] = []
        self._normalized_swaps: list[NormalizedSwap] = []

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    @property
    def normalized_swaps(self) -> list[NormalizedSwap]:
        """Return a copy of all NormalizedSwap instances emitted for landed swaps."""
        return list(self._normalized_swaps)

    async def run(self) -> None:
        """Consume all events from the source, stamping each with the injected clock.

        For each event yielded by stamp_events():
          1. The (event, timestamp) pair is appended to self._processed.
          2. If failed=True the event is DROPPED — no NormalizedSwap emitted (§6.2).
          3. For landed swaps whose mint is in token_store, exactly one NormalizedSwap
             is emitted with owner=signer, rel anchored to graduated_block_time.
        """
        async for event, timestamp in stamp_events(self._source, self._clock):
            self._processed.append((event, timestamp))

            # Drop failed swaps — landed-only per §6.2 / AC-18.2
            if event.get("failed", False):
                continue

            mint = event.get("mint")
            if mint and mint in self._token_store:
                token = self._token_store[mint]
                normalized = NormalizedSwap.from_raw_swap(
                    event,
                    token,
                    source=self._swap_source,
                    phase=self._swap_phase,
                )
                self._normalized_swaps.append(normalized)
