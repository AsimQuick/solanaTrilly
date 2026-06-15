# ---
# module: core.tape.gap_reconciler
# sprint: sprint-5
# story: US-20 AC-20.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, core.clock, core.tape.recorder,
#               core.tape.swap_writer, core.normalized_swap, typing
# ---
"""GapReconciler — seek_by_time reconciliation for WS gap recovery (PRD §6.2 D4).

Merges swaps missed during a WebSocket gap into the tape, idempotently on
(mint, signature).  Uses the IDENTICAL normalization path as the offline
backfill (one code path, AC-20.3 / Principle #7): internally drives a
TapeRecorder with swap_source="birdeye_backfill" so live gap-fill and offline
backfill produce byte-identical NormalizedSwaps by construction.

The DataSource seam is preserved: GapReconciler never imports a concrete
source (LiveSource, ReplaySource).  The caller injects a seek-by-time
DataSource that yields swaps over the gap window.
"""
from typing import Any

from core.clock import Clock
from core.datasource import DataSource
from core.normalized_swap import NormalizedSwap
from core.tape.recorder import TapeRecorder
from core.tape.swap_writer import SwapWriter


class GapReconciler:
    """Reconciles WS gaps by fetching missed swaps from a seek_by_time DataSource.

    Idempotency is guaranteed by SwapWriter.write(), which upserts on
    (mint, signature) — re-merging the same swap never duplicates a row.

    One code path (AC-20.3): internally wraps TapeRecorder with
    swap_source="birdeye_backfill".  A standalone backfill uses the same
    TapeRecorder path, so live-gap and offline-backfill results are
    byte-identical by construction (Principle #7 / PRD §3.3).

    Args:
        seek_source: DataSource yielding swaps over the gap window.
                     In production: a Birdeye seek_by_time endpoint adapter.
                     In tests: a ReplaySource with the missed swaps.
        clock:       Injected clock — forwarded to the inner TapeRecorder.
        token_store: Dict mapping mint → token object with graduated_block_time.
        swap_writer: SwapWriter for idempotent DB persistence.
        swap_phase:  NormalizedSwap phase vocabulary value. Defaults to "pre".
    """

    def __init__(
        self,
        seek_source: DataSource,
        clock: Clock,
        token_store: dict[str, Any],
        swap_writer: SwapWriter,
        *,
        swap_phase: str = "pre",
    ) -> None:
        self._recorder = TapeRecorder(
            source=seek_source,
            clock=clock,
            token_store=token_store,
            swap_source="birdeye_backfill",
            swap_phase=swap_phase,
            swap_writer=swap_writer,
        )

    async def run(self) -> list[NormalizedSwap]:
        """Fetch swaps from the seek_by_time source and merge into the tape.

        Returns:
            List of NormalizedSwaps in canonical (block_time, slot, signature) order.

        The merge is idempotent: swaps already present (by (mint, signature))
        are updated in-place, not duplicated.
        """
        await self._recorder.run()
        return self._recorder.normalized_swaps
