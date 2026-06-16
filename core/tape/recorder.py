# ---
# module: core.tape.recorder
# sprint: sprint-5
# story: US-18 AC-18.1, US-18 AC-18.2, US-18 AC-18.3, US-18 AC-18.4,
#        US-19 AC-19.1, US-19 AC-19.2, US-20 AC-20.2, US-20 AC-20.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, core.clock, core.normalized_swap,
#               core.tape.idle_kill, core.tape.lake_writer, core.tape.swap_writer,
#               datetime, typing
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

AC-18.4 / S8 / #405 — degenerate-swap guard:
    POLICY = "skip"
    Swaps with zero/None reserves, zero volume, or a degenerate price field are
    SKIPPED before NormalizedSwap emission.  They are never emitted as a silent
    0-value row and never raise ZeroDivisionError.  Skipped swaps are tracked in
    TapeRecorder.skipped_degenerate for audit.
"""
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from core.clock import Clock, stamp_events
from core.datasource import DataSource
from core.normalized_swap import NormalizedSwap

if TYPE_CHECKING:
    from core.tape.idle_kill import IdleKillMonitor
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

# ---------------------------------------------------------------------------
# Degenerate-swap guard (S8 / #405 / AC-18.4)
# ---------------------------------------------------------------------------

#: Explicit policy (AC-18.4): degenerate swaps are SKIPPED — never a silent
#: 0-value row in the tape, never a crash.  Skipped swaps land in
#: TapeRecorder.skipped_degenerate so callers can audit them.
DEGENERATE_SWAP_POLICY: str = "skip"


def _is_degenerate_swap(event: dict) -> bool:
    """Return True if *event* is degenerate and must be skipped (S8 / #405).

    Degenerate conditions (AC-18.4):
    - price is None or zero          → ZeroDivisionError / degenerate price field
    - vol_sol is None or zero        → zero/missing volume
    - base_reserve present but == 0  → reserve-derived price ZeroDivisionError (#405)
    - quote_reserve present but == 0 → reserve-derived price ZeroDivisionError (#405)

    NOTE (US-22 AC-22.3, PRD §3.3/§7.1): a *None* reserve is NOT degenerate.  The
    Birdeye live path carries no reserves (price = Birdeye tokenPrice, not
    reserve-derived) — reserves are the helius_verify path only.  Treating
    None reserves as degenerate (the original AC-18.4 wording, written against
    synthetic fixtures that always had reserves) would skip every real Birdeye
    swap.  Only a *present* zero reserve — which would divide-by-zero on the
    reserve-derived price path — is degenerate.
    """
    price = event.get("price")
    vol_sol = event.get("vol_sol")
    base_reserve = event.get("base_reserve")
    quote_reserve = event.get("quote_reserve")

    # Degenerate price: None or zero (or un-castable)
    if price is None:
        return True
    try:
        if float(price) == 0.0:
            return True
    except (TypeError, ValueError):
        return True

    # Zero/missing volume
    if vol_sol is None:
        return True
    try:
        if float(vol_sol) == 0.0:
            return True
    except (TypeError, ValueError):
        return True

    # Reserves: present-but-zero is degenerate (#405 reserve-derived price would
    # ZeroDivisionError).  None is VALID — the Birdeye live path carries no
    # reserves (§3.3/§7.1); skipping None would drop every real Birdeye swap.
    if base_reserve is not None and base_reserve == 0:
        return True
    if quote_reserve is not None and quote_reserve == 0:
        return True

    return False


class TapeRecorder:
    """Reads swap events from a DataSource, timestamps them via an injected Clock.

    AC-18.1: DataSource/Clock seam — live/replay parity (Principle #7).
    AC-18.2: Emits one NormalizedSwap per landed swap; drops failed swaps (§6.2).
    AC-19.1: Optional LakeWriter — when provided, run() persists normalized_swaps
             to the daily-partitioned jsonl.gz lake after all events are consumed.
    AC-19.2: Optional SwapWriter — when provided, run() mirrors normalized_swaps
             to the 'swaps' DB table (idempotent on (mint, signature)) after all
             events are consumed.  (mint, NormalizedSwap) pairs are tracked
             internally so the SwapWriter has the base-token mint for the upsert.
    AC-20.2: Optional IdleKillMonitor — when provided, run() calls check_idle()
             on EVERY event arrival and record_swap() on each landed tracked swap.
             Deactivated mints re-attach on the next swap (never goes blind).

    Args:
        source:       Any DataSource implementation (live or replay).
        clock:        Any Clock implementation (wall or virtual).
        token_store:  Dict mapping mint → token-like object with graduated_block_time.
                      Required for NormalizedSwap emission (AC-18.2).
                      Defaults to {} — no normalization when empty.
        swap_source:  NormalizedSwap source vocabulary value. Defaults to "birdeye_live".
        swap_phase:   NormalizedSwap phase vocabulary value. Defaults to "pre".
        lake_writer:  Optional LakeWriter (AC-19.1).  When provided, all normalized
                      swaps are written to the lake after run() finishes.
                      Defaults to None — existing callers are unaffected.
        swap_writer:  Optional SwapWriter (AC-19.2).  When provided, all normalized
                      swaps are mirrored to the 'swaps' DB table after run() finishes.
                      Defaults to None — existing callers are unaffected.
        idle_monitor: Optional IdleKillMonitor (AC-20.2).  When provided, run()
                      invokes check_idle(timestamp) on every event and record_swap()
                      on each landed swap for a tracked mint, enabling TTL-based
                      deactivation and re-attachment driven by get_active_config().
                      Defaults to None — existing callers are unaffected.
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        token_store: dict[str, Any] | None = None,
        *,
        swap_source: str = "birdeye_live",
        swap_phase: str = "pre",
        lake_writer: "LakeWriter | None" = None,
        swap_writer: "SwapWriter | None" = None,
        idle_monitor: "IdleKillMonitor | None" = None,
    ) -> None:
        self._source: DataSource = source
        self._clock: Clock = clock
        self._token_store: dict[str, Any] = token_store if token_store is not None else {}
        self._swap_source: str = swap_source
        self._swap_phase: str = swap_phase
        self._lake_writer: "LakeWriter | None" = lake_writer
        self._swap_writer: "SwapWriter | None" = swap_writer
        self._idle_monitor: "IdleKillMonitor | None" = idle_monitor
        self._processed: list[tuple[dict[str, Any], datetime]] = []
        # AC-19.2: track (mint, NormalizedSwap) pairs so SwapWriter has the base-token mint.
        self._normalized_swaps_with_mints: list[tuple[str, NormalizedSwap]] = []
        self._skipped_degenerate: list[dict[str, Any]] = []

    @property
    def processed(self) -> list[tuple[dict[str, Any], datetime]]:
        """Return a copy of all (event, timestamp) pairs processed so far."""
        return list(self._processed)

    @property
    def skipped_degenerate(self) -> list[dict[str, Any]]:
        """Return a copy of all degenerate landed swaps skipped per AC-18.4 policy.

        DEGENERATE_SWAP_POLICY == "skip": these events were received (they appear
        in processed) but were not emitted as NormalizedSwaps.  Callers can
        inspect this list to audit what was dropped and why.
        """
        return list(self._skipped_degenerate)

    @property
    def normalized_swaps(self) -> list[NormalizedSwap]:
        """Return all NormalizedSwaps sorted by canonical key (block_time, slot, signature).

        Python's sorted() is a stable sort — swaps whose (block_time, slot, signature)
        triple is identical preserve their original insertion order (AC-18.3, §8, #403).
        """
        return sorted(
            [ns for _, ns in self._normalized_swaps_with_mints],
            key=lambda s: (s.block_time, s.slot, s.signature),
        )

    async def run(self) -> None:
        """Consume all events from the source, stamping each with the injected clock.

        For each event yielded by stamp_events():
          1. The (event, timestamp) pair is appended to self._processed.
          2. If failed=True the event is DROPPED — no NormalizedSwap emitted (§6.2).
          3. For landed swaps whose mint is in token_store, exactly one NormalizedSwap
             is emitted with owner=signer, rel anchored to graduated_block_time.

        AC-19.1: After all events are consumed, if a lake_writer was injected and
        there are normalized swaps, they are persisted to the daily-partitioned lake.
        AC-19.2: If a swap_writer was injected, normalized swaps are also mirrored
        to the 'swaps' DB table (idempotent on (mint, signature)).
        AC-20.2: If an idle_monitor was injected, check_idle(timestamp) is called
        on every event and record_swap(mint, timestamp) on every landed tracked swap.
        """
        async for event, timestamp in stamp_events(self._source, self._clock):
            self._processed.append((event, timestamp))

            # AC-20.2: idle-kill TTL check on every event (any type) so that
            # non-swap events (heartbeats, ticks) also advance the deactivation timer.
            # asyncio.to_thread: check_idle calls config_resolver (Django cache/ORM),
            # which is a SynchronousOnlyOperation in async context — same pattern as
            # SwapWriter.write().
            if self._idle_monitor is not None:
                await asyncio.to_thread(self._idle_monitor.check_idle, timestamp)

            # Drop failed swaps — landed-only per §6.2 / AC-18.2
            if event.get("failed", False):
                continue

            # Degenerate-swap guard (S8 / #405 / AC-18.4)
            # Policy: SKIP — track in skipped_degenerate, never emit, never crash.
            if _is_degenerate_swap(event):
                self._skipped_degenerate.append(event)
                continue

            mint = event.get("mint")
            if mint and mint in self._token_store:
                token = self._token_store[mint]

                # AC-20.2: record swap — re-attaches mint if previously deactivated.
                if self._idle_monitor is not None:
                    self._idle_monitor.record_swap(mint, timestamp)

                normalized = NormalizedSwap.from_raw_swap(
                    event,
                    token,
                    source=self._swap_source,
                    phase=self._swap_phase,
                )
                # AC-19.2: track (mint, NormalizedSwap) so SwapWriter can upsert by mint.
                self._normalized_swaps_with_mints.append((mint, normalized))

        # AC-19.1: persist to lake after all events consumed
        if self._lake_writer is not None and self._normalized_swaps_with_mints:
            self._lake_writer.write(self.normalized_swaps)

        # AC-19.2: mirror to 'swaps' DB table after all events consumed.
        # asyncio.to_thread() runs the sync Django ORM write in a thread-pool
        # executor so it doesn't block the event loop or trip Django's async guard.
        if self._swap_writer is not None and self._normalized_swaps_with_mints:
            await asyncio.to_thread(
                self._swap_writer.write, self._normalized_swaps_with_mints
            )
