# ---
# module: core.tape.reconciler
# sprint: sprint-5
# story: US-20 AC-20.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, core.clock, core.normalized_swap,
#               core.tape.recorder, typing
# ---
"""GapReconciler — seek_by_time reconciliation path for WS gap fill (PRD §6.2 / AC-20.1).

When the live WebSocket feed has a gap (network interruption, reconnect delay,
etc.), the recorder calls GapReconciler.reconcile() to pull the missed swaps from
a seek_by_time DataSource and merge them with the already-recorded live set.

Design principle (AC-20.3 / §3.3 parity by construction):
    GapReconciler internally delegates to TapeRecorder — the SAME normalization
    path used by the live recorder.  The ONLY difference is swap_source, which
    defaults to "birdeye_backfill" (vs "birdeye_live" on the hot path).  This
    guarantees that a live-gap reconcile and a pure offline backfill over the same
    window produce byte-identical NormalizedSwaps.

Idempotency (AC-20.1):
    Deduplication is on (mint, signature).  Because Solana transaction signatures
    are globally unique, the signature alone is sufficient as a dedup key within
    a single-token tape.  The optional SwapWriter write path is also idempotent on
    (mint, signature) via update_or_create (AC-19.2).
"""
from typing import TYPE_CHECKING, Any

from core.clock import Clock
from core.datasource import DataSource
from core.normalized_swap import NormalizedSwap
from core.tape.recorder import TapeRecorder

if TYPE_CHECKING:
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter


class GapReconciler:
    """Fills WS gaps by pulling swaps from a seek_by_time DataSource.

    Usage::

        # Typically called after a live TapeRecorder run has finished with
        # some swaps missing (the gap was detected by the caller).
        reconciler = GapReconciler(
            source=backfill_source,   # DataSource covering the gap window
            clock=clock,
            token_store=token_store,
        )
        all_swaps = await reconciler.reconcile(existing_swaps=live_swaps)

    Args:
        source:      DataSource that yields swaps for the gap window.  The
                     caller (listener wiring) is responsible for constructing
                     this source with the correct time range — the reconciler
                     is source-agnostic (Principle #7).
        clock:       Injected Clock — same interface as used by TapeRecorder.
        token_store: Dict mapping mint → token-like object with graduated_block_time.
        swap_source: NormalizedSwap source vocabulary value.  Defaults to
                     "birdeye_backfill" (the reconcile/backfill vocabulary value,
                     AC-20.3 parity: one code path for both gap-fill and backfill).
        swap_phase:  NormalizedSwap phase vocabulary value. Defaults to "pre".
        swap_writer: Optional SwapWriter for idempotent DB mirror writes (AC-19.2).
                     When provided, NEW (not duplicate) swaps are written to the
                     'swaps' table after reconcile() completes.
        lake_writer: Optional LakeWriter (AC-19.1).  When provided, NEW swaps are
                     appended to the daily-partitioned jsonl.gz lake.
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        token_store: dict[str, Any],
        *,
        swap_source: str = "birdeye_backfill",
        swap_phase: str = "pre",
        swap_writer: "SwapWriter | None" = None,
        lake_writer: "LakeWriter | None" = None,
    ) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._token_store: dict[str, Any] = token_store
        self._swap_source: str = swap_source
        self._swap_phase: str = swap_phase
        self._swap_writer: "SwapWriter | None" = swap_writer
        self._lake_writer: "LakeWriter | None" = lake_writer
        self._merged_swaps: list[NormalizedSwap] = []

    @property
    def merged_swaps(self) -> list[NormalizedSwap]:
        """Return the merged swap set from the last reconcile() call."""
        return list(self._merged_swaps)

    async def reconcile(
        self,
        existing_swaps: "list[NormalizedSwap] | None" = None,
    ) -> list[NormalizedSwap]:
        """Pull gap swaps from source and merge with existing, deduplicating on signature.

        This is the SINGLE seek_by_time code path (AC-20.3 parity by construction):
        both the live pipeline's gap-fill pass and the offline backfill use this
        same method, so a reconcile and a backfill over the same window produce
        byte-identical NormalizedSwap lists.

        Steps:
          1. Run a TapeRecorder over the gap DataSource (swap_source="birdeye_backfill").
          2. Collect its normalized_swaps (already canonical-sorted by TapeRecorder).
          3. Deduplicate: drop any gap swap whose signature already appears in
             existing_swaps — preserving the existing entry over the gap entry.
          4. Merge and re-sort to canonical order (block_time, slot, signature).
          5. If swap_writer is set, write the NEW (non-duplicate) swaps idempotently
             to the 'swaps' DB table via TapeRecorder's own writer path.
          6. If lake_writer is set, append NEW swaps to the jsonl.gz lake.

        Args:
            existing_swaps: Swaps already recorded in the live pass.  When None
                or empty, all gap swaps are treated as new.

        Returns:
            Merged list of NormalizedSwaps sorted by (block_time, slot, signature)
            with no duplicate signatures.
        """
        existing: list[NormalizedSwap] = list(existing_swaps or [])
        existing_sigs: set[str] = {ns.signature for ns in existing}

        # Delegate to TapeRecorder — the SINGLE normalization code path.
        # swap_writer / lake_writer are passed through so the recorder handles
        # idempotent DB writes and lake appends for the NEW swaps only.
        # We pass None here and handle writes below (so we can filter duplicates
        # before writing — avoids unnecessary DB round-trips on already-known sigs).
        recorder = TapeRecorder(
            self._source,
            self._clock,
            token_store=self._token_store,
            swap_source=self._swap_source,
            swap_phase=self._swap_phase,
        )
        await recorder.run()
        gap_swaps: list[NormalizedSwap] = recorder.normalized_swaps

        # Deduplicate: gap swaps whose signature is already in the existing set
        # are silently dropped.  The existing entry is kept.
        new_swaps: list[NormalizedSwap] = [
            ns for ns in gap_swaps if ns.signature not in existing_sigs
        ]

        # Idempotent DB mirror: write only the truly new swaps.
        if self._swap_writer is not None and new_swaps:
            # Reconstruct (mint, NormalizedSwap) pairs.  The mint is recoverable
            # from the token_store by matching each swap's block_time anchor — but
            # there is no direct mint on NormalizedSwap.  We derive it from the
            # recorder's internal _normalized_swaps_with_mints list, filtered to
            # new signatures only.
            new_sigs: set[str] = {ns.signature for ns in new_swaps}
            pairs_to_write = [
                (mint, ns)
                for mint, ns in recorder._normalized_swaps_with_mints  # noqa: SLF001
                if ns.signature in new_sigs
            ]
            if pairs_to_write:
                import asyncio

                await asyncio.to_thread(self._swap_writer.write, pairs_to_write)

        # Idempotent lake append: only new swaps.
        if self._lake_writer is not None and new_swaps:
            self._lake_writer.write(new_swaps)

        # Merge and re-sort to canonical order.
        merged = sorted(
            existing + new_swaps,
            key=lambda s: (s.block_time, s.slot, s.signature),
        )
        self._merged_swaps = merged
        return merged
