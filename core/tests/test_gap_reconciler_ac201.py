# ---
# module: core.tests.test_gap_reconciler_ac201
# sprint: sprint-5
# story: US-20 AC-20.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.reconciler, core.tape.recorder, core.clock,
#               core.replay_source, core.normalized_swap, asyncio, pytest, types
# ---
"""AC-20.1 — seek_by_time gap reconciliation merges missed swaps idempotently.

The AC states:
  "A seek_by_time reconciliation path (behind the DataSource seam) merges any
  swaps missed during a WS gap into the tape, idempotently.  Verified by a pytest
  test: a live swap stream missing N swaps, followed by a seek_by_time reconcile
  over the gap window, yields the complete swap set with NO duplicate rows for
  swaps already recorded (idempotent on (mint, signature))."

Tests:
  1. test_live_stream_missing_swaps_reconcile_completes_set
       The primary AC test: live recorder delivers 3/5 swaps (missing 2).
       GapReconciler reconcile() delivers all 5.  No duplicates.

  2. test_reconcile_no_duplicates_for_overlapping_swaps
       Reconcile source includes 2 swaps already in the live set.
       Merged result has 0 duplicate signatures.

  3. test_reconcile_fully_overlapping_produces_no_new_swaps
       Reconcile source is 100% overlap with existing.
       merged count == existing count (nothing added).

  4. test_reconcile_with_empty_existing_returns_all_gap_swaps
       existing_swaps=None (first pass, or empty live set).
       All gap swaps returned.

  5. test_reconcile_default_swap_source_is_birdeye_backfill
       GapReconciler swap_source defaults to "birdeye_backfill".
       Emitted NormalizedSwap.source == "birdeye_backfill" (one code path, AC-20.3).

  6. test_reconcile_merged_result_canonical_sort
       Merged swaps are ordered by (block_time, slot, signature), not insertion
       order or gap-first.

  7. test_reconcile_idempotent_on_second_call
       Calling reconcile() twice with the same gap source does not grow the set.
       Idempotent on (mint, signature) in-memory.

  8. test_reconcile_with_swap_writer_idempotent_db
       Full DB integration: live run + reconcile; the 'swaps' table count equals
       total distinct swaps regardless of overlap.  (DB-level idempotency.)
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from core.normalized_swap import NormalizedSwap

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)
_MINT = "PUMP_MINT_RECONCILE_111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"
_TOKEN_STORE: dict[str, Any] = {_MINT: _TOKEN}
_T0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

# Five canonical swap events (all landed, non-degenerate).
# Swap index 1..5 → signatures SWAP_SIG_001..005.
_ALL_SWAP_EVENTS: list[dict[str, Any]] = [
    {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _GRADUATED_BLOCK_TIME + 60 + i,
        "slot": 200 + i,
        "signature": f"SWAP_SIG_{i:03d}" + "A" * 85,
        "side": "buy" if i % 2 == 1 else "sell",
        "price": 0.00025 + i * 0.00001,
        "vol_sol": 1.0 + i * 0.1,
        "vol_usd": 150.0 + i * 10.0,
        "sol_usd": 150.0,
        "owner": f"OWNER_{i:04d}" + "X" * 50,
        "base_reserve": 5_000_000 + i * 1000,
        "quote_reserve": 1_250_000 + i * 500,
        "quote_mint": _QUOTE_MINT,
        "failed": False,
    }
    for i in range(1, 6)
]

# Partition the 5 events into "live" (1,2,5) and "gap" (3,4) plus overlap (2,3,4).
_LIVE_EVENTS = [_ALL_SWAP_EVENTS[0], _ALL_SWAP_EVENTS[1], _ALL_SWAP_EVENTS[4]]  # 1,2,5
_GAP_EVENTS = [_ALL_SWAP_EVENTS[1], _ALL_SWAP_EVENTS[2], _ALL_SWAP_EVENTS[3]]  # 2,3,4 (2 overlaps)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_recorder(events: list[dict]) -> list[NormalizedSwap]:
    """Run a TapeRecorder synchronously; return its normalized_swaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner() -> list[NormalizedSwap]:
        recorder = TapeRecorder(
            ReplaySource(event_log=events),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_source="birdeye_live",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _run_reconciler(
    gap_events: list[dict],
    existing_swaps: list[NormalizedSwap] | None = None,
    swap_source: str = "birdeye_backfill",
) -> list[NormalizedSwap]:
    """Run a GapReconciler synchronously; return the merged NormalizedSwap list."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.reconciler import GapReconciler

    async def _inner() -> list[NormalizedSwap]:
        reconciler = GapReconciler(
            ReplaySource(event_log=gap_events),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_source=swap_source,
        )
        return await reconciler.reconcile(existing_swaps=existing_swaps)

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_live_stream_missing_swaps_reconcile_completes_set() -> None:
    """Primary AC-20.1 test: live misses 2 swaps; reconcile fills them in.

    Scenario:
      Live stream delivers swaps 1, 2, 5 (indices 0, 1, 4 of _ALL_SWAP_EVENTS).
      Gap source delivers swaps 2, 3, 4 (overlap on 2; new 3 and 4).
      Merged result must contain all 5 swaps.
    """
    live_swaps = _run_recorder(_LIVE_EVENTS)
    assert len(live_swaps) == 3, f"Expected 3 live swaps, got {len(live_swaps)}"

    merged = _run_reconciler(_GAP_EVENTS, existing_swaps=live_swaps)

    assert len(merged) == 5, (
        f"Expected 5 merged swaps (all 5 unique), got {len(merged)}. "
        f"Signatures: {[ns.signature[:12] for ns in merged]}"
    )

    all_expected_sigs = {e["signature"] for e in _ALL_SWAP_EVENTS}
    actual_sigs = {ns.signature for ns in merged}
    assert actual_sigs == all_expected_sigs, (
        f"Merged set is missing signatures: {all_expected_sigs - actual_sigs}"
    )


def test_reconcile_no_duplicates_for_overlapping_swaps() -> None:
    """Reconcile with 2 already-recorded swaps in gap source → NO duplicate signatures."""
    live_swaps = _run_recorder(_LIVE_EVENTS)  # swaps 1, 2, 5
    merged = _run_reconciler(_GAP_EVENTS, existing_swaps=live_swaps)  # gap: 2, 3, 4

    sigs = [ns.signature for ns in merged]
    unique_sigs = set(sigs)
    assert len(sigs) == len(unique_sigs), (
        f"Duplicate signatures found in merged result: "
        f"{[s for s in sigs if sigs.count(s) > 1]}"
    )


def test_reconcile_fully_overlapping_produces_no_new_swaps() -> None:
    """100% overlap: reconcile source == existing → merged count unchanged."""
    live_swaps = _run_recorder(_LIVE_EVENTS)  # swaps 1, 2, 5
    # Feed the SAME 3 events back as the gap source — every signature already known.
    merged = _run_reconciler(_LIVE_EVENTS, existing_swaps=live_swaps)

    assert len(merged) == len(live_swaps), (
        f"Fully-overlapping reconcile grew the set: "
        f"existing={len(live_swaps)}, merged={len(merged)}"
    )
    assert {ns.signature for ns in merged} == {ns.signature for ns in live_swaps}


def test_reconcile_with_empty_existing_returns_all_gap_swaps() -> None:
    """existing_swaps=None (or []) → all gap swaps returned (no prior live pass)."""
    merged = _run_reconciler(_GAP_EVENTS, existing_swaps=None)

    # Gap events are indices 1, 2, 3 → 3 distinct swaps (no existing to conflict with)
    assert len(merged) == 3, f"Expected 3 gap swaps, got {len(merged)}"
    expected_sigs = {e["signature"] for e in _GAP_EVENTS}
    assert {ns.signature for ns in merged} == expected_sigs


def test_reconcile_default_swap_source_is_birdeye_backfill() -> None:
    """GapReconciler defaults to swap_source='birdeye_backfill' (AC-20.3 parity path).

    All emitted NormalizedSwaps must have source == 'birdeye_backfill', confirming
    the reconciler uses the backfill vocabulary — the same value an offline backfill
    would produce — not 'birdeye_live'.
    """
    # Use gap_events with no existing so all get emitted
    merged = _run_reconciler(_GAP_EVENTS, existing_swaps=None)

    for ns in merged:
        assert ns.source == "birdeye_backfill", (
            f"Expected source='birdeye_backfill' (AC-20.3 backfill parity), "
            f"got {ns.source!r} for signature {ns.signature[:12]}"
        )


def test_reconcile_merged_result_canonical_sort() -> None:
    """Merged swaps are sorted by canonical key (block_time, slot, signature)."""
    live_swaps = _run_recorder(_LIVE_EVENTS)
    merged = _run_reconciler(_GAP_EVENTS, existing_swaps=live_swaps)

    assert len(merged) == 5
    for i in range(len(merged) - 1):
        a, b = merged[i], merged[i + 1]
        assert (a.block_time, a.slot, a.signature) <= (b.block_time, b.slot, b.signature), (
            f"Canonical sort violated at index {i}: "
            f"({a.block_time},{a.slot},{a.signature[:8]}) > "
            f"({b.block_time},{b.slot},{b.signature[:8]})"
        )


def test_reconcile_idempotent_on_second_call() -> None:
    """Calling reconcile() twice with the same gap source does not grow the merged set.

    This confirms in-memory idempotency on (mint, signature): the second reconcile
    sees all gap signatures as already-existing (they were returned by the first)
    and adds nothing new.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.reconciler import GapReconciler

    live_swaps = _run_recorder(_LIVE_EVENTS)

    async def _inner() -> tuple[list[NormalizedSwap], list[NormalizedSwap]]:
        first = GapReconciler(
            ReplaySource(event_log=_GAP_EVENTS),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
        )
        merged_first = await first.reconcile(existing_swaps=live_swaps)

        # Second reconcile treats the first-merged set as existing.
        second = GapReconciler(
            ReplaySource(event_log=_GAP_EVENTS),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
        )
        merged_second = await second.reconcile(existing_swaps=merged_first)
        return merged_first, merged_second

    merged_first, merged_second = asyncio.run(_inner())

    assert len(merged_second) == len(merged_first), (
        f"Second reconcile grew the set: first={len(merged_first)}, "
        f"second={len(merged_second)} (idempotency violation)"
    )
    assert {ns.signature for ns in merged_second} == {ns.signature for ns in merged_first}


@pytest.mark.django_db
def test_reconcile_with_swap_writer_idempotent_db() -> None:
    """DB-level idempotency: live run + reconcile; Swap table count == distinct swaps.

    Full integration test:
      1. Live TapeRecorder + SwapWriter writes 3 swaps (indices 1, 2, 5).
      2. GapReconciler + SwapWriter reconciles with gap source (swaps 2, 3, 4).
         Swap 2 is already in the table; only swaps 3 and 4 are new.
      3. Swap table count for this mint must be exactly 5 (not 6).
    """
    from core.clock import VirtualClock
    from core.models import Swap
    from core.replay_source import ReplaySource
    from core.tape.reconciler import GapReconciler
    from core.tape.recorder import TapeRecorder
    from core.tape.swap_writer import SwapWriter

    swap_writer = SwapWriter()
    count_before = Swap.objects.filter(mint=_MINT).count()

    async def _inner() -> list[NormalizedSwap]:
        # Step 1: live pass writes 3 swaps
        recorder = TapeRecorder(
            ReplaySource(event_log=_LIVE_EVENTS),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_source="birdeye_live",
            swap_writer=swap_writer,
        )
        await recorder.run()
        live_swaps = recorder.normalized_swaps

        # Step 2: reconcile writes 2 NEW swaps (3 and 4); swap 2 is idempotent
        reconciler = GapReconciler(
            ReplaySource(event_log=_GAP_EVENTS),
            VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_writer=swap_writer,
        )
        return await reconciler.reconcile(existing_swaps=live_swaps)

    merged = asyncio.run(_inner())

    assert len(merged) == 5, f"Expected 5 merged swaps, got {len(merged)}"

    # DB must have exactly 5 distinct rows for this mint (no duplicate for swap 2)
    count_after = Swap.objects.filter(mint=_MINT).count()
    assert count_after == count_before + 5, (
        f"Expected {count_before + 5} Swap rows (5 distinct), got {count_after}. "
        "Reconcile write may have created a duplicate for the overlapping swap."
    )
