# ---
# module: core.tests.test_tape_recorder_ac183
# sprint: sprint-5
# story: US-18 AC-18.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.recorder, core.clock, core.replay_source, asyncio, types
# ---
"""AC-18.3 — Stable canonical sort on (block_time, slot, signature), never block_time alone.

The #403 within-second-order disaster: multiple swaps arriving in the same second must
be ordered deterministically by (block_time, slot, signature) — NOT by arrival order
and NOT by block_time alone.

Tests:
  1. test_within_second_out_of_order_ordered_by_slot
       Swaps with equal block_time fed in DESCENDING slot order → emitted in ASCENDING
       slot order.

  2. test_within_second_equal_slot_ordered_by_signature
       Swaps with equal (block_time, slot) fed in reverse-alphabetical signature order
       → emitted in lexicographic signature order.

  3. test_fully_equal_key_pair_preserves_input_order
       Two swaps sharing an identical (block_time, slot, signature) triple → stable sort
       preserves their original input order (Python sorted() is stable per language spec).

  4. test_different_block_times_ordered_correctly
       Swaps with distinct block_times fed out of order → emitted in block_time-ascending
       order regardless of within-second slot/signature.

  5. test_canonical_sort_combined
       Mixed stream: different block_times AND multiple within-second swaps (same
       block_time, different slots) fed in arbitrary order → emitted in the single
       canonical (block_time, slot, signature) sequence.

  6. test_sort_does_not_affect_failed_swap_drop
       A failed swap among the within-second group is still dropped; the remaining landed
       swaps are still sorted correctly.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)

_MINT = "PUMP_MINT_AC183_11111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"

_TOKEN_STORE: dict[str, Any] = {_MINT: _TOKEN}
_T0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

# Shared block_time — all "within-second" swaps use this value
_SAME_BT = 1_700_000_060


def _make_swap(block_time: int, slot: int, sig: str, failed: bool = False) -> dict[str, Any]:
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": block_time,
        "slot": slot,
        "signature": sig,
        "side": "buy",
        "price": 0.00025,
        "vol_sol": 1.0,
        "vol_usd": 150.0,
        "sol_usd": 150.0,
        "owner": f"OWNER_{sig[-6:]}",
        "base_reserve": 5_000_000,
        "quote_reserve": 1_250_000,
        "quote_mint": _QUOTE_MINT,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def _run_recorder(events: list[dict]) -> list[Any]:
    """Drive TapeRecorder synchronously; return normalized_swaps (sorted)."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = ReplaySource(event_log=events)
        clock = VirtualClock(_T0)
        recorder = TapeRecorder(source, clock, token_store=_TOKEN_STORE)
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_within_second_out_of_order_ordered_by_slot() -> None:
    """Equal block_time fed in descending slot order → emitted in ascending slot order."""
    # Slots fed in REVERSE order: 300, 200, 100
    events = [
        _make_swap(_SAME_BT, 300, "SIG_SLOT300_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        _make_swap(_SAME_BT, 200, "SIG_SLOT200_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"),
        _make_swap(_SAME_BT, 100, "SIG_SLOT100_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"),
    ]
    normalized = _run_recorder(events)

    assert len(normalized) == 3, f"Expected 3 swaps, got {len(normalized)}"

    slots = [ns.slot for ns in normalized]
    assert slots == [100, 200, 300], (
        f"Expected slots [100, 200, 300] after canonical sort, got {slots}. "
        "Canonical sort must order by (block_time, slot, signature) — never block_time alone (#403)."
    )


def test_within_second_equal_slot_ordered_by_signature() -> None:
    """Equal (block_time, slot) fed in reverse-alphabetical signature order → lexicographic output."""
    # Signatures fed in REVERSE lexicographic order: C, B, A
    sig_c = "SIG_C_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    sig_b = "SIG_B_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    sig_a = "SIG_A_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"

    events = [
        _make_swap(_SAME_BT, 200, sig_c),
        _make_swap(_SAME_BT, 200, sig_b),
        _make_swap(_SAME_BT, 200, sig_a),
    ]
    normalized = _run_recorder(events)

    assert len(normalized) == 3, f"Expected 3 swaps, got {len(normalized)}"

    sigs = [ns.signature for ns in normalized]
    assert sigs == sorted(sigs), (
        f"Expected signatures in lexicographic order (equal slot), got {sigs}. "
        "Canonical sort key is (block_time, slot, signature)."
    )
    # Explicit: A < B < C
    assert normalized[0].signature == sig_a
    assert normalized[1].signature == sig_b
    assert normalized[2].signature == sig_c


def test_fully_equal_key_pair_preserves_input_order() -> None:
    """Swaps with identical (block_time, slot, signature) preserve original insertion order.

    Python's sorted() is a stable sort per language spec.  Two swaps that share the
    full canonical key must come out in the order they were received.
    """
    # Both swaps share the exact same canonical triple — differ only in vol_sol
    shared_sig = "SIG_DUP_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"

    swap_first = _make_swap(_SAME_BT, 200, shared_sig)
    swap_first["vol_sol"] = 1.0   # first arrival — distinguisher

    swap_second = _make_swap(_SAME_BT, 200, shared_sig)
    swap_second["vol_sol"] = 2.0  # second arrival — distinguisher

    events = [swap_first, swap_second]
    normalized = _run_recorder(events)

    assert len(normalized) == 2, f"Expected 2 swaps (both identical key, both landed), got {len(normalized)}"

    # Stable sort: first-input swap must remain first in output
    assert normalized[0].vol_sol == 1.0, (
        f"First-input swap must come first when keys are equal (stable sort). "
        f"Got vol_sol={normalized[0].vol_sol!r} first, expected 1.0."
    )
    assert normalized[1].vol_sol == 2.0, (
        f"Second-input swap must come second when keys are equal (stable sort). "
        f"Got vol_sol={normalized[1].vol_sol!r} second, expected 2.0."
    )


def test_different_block_times_ordered_correctly() -> None:
    """Swaps with distinct block_times fed out of order → ascending block_time output."""
    # Feed in DESCENDING block_time order
    events = [
        _make_swap(1_700_000_090, 300, "SIG_BT90_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        _make_swap(1_700_000_060, 200, "SIG_BT60_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"),
        _make_swap(1_700_000_030, 100, "SIG_BT30_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"),
    ]
    normalized = _run_recorder(events)

    assert len(normalized) == 3

    block_times = [ns.block_time for ns in normalized]
    assert block_times == [1_700_000_030, 1_700_000_060, 1_700_000_090], (
        f"Expected ascending block_times, got {block_times}"
    )


def test_canonical_sort_combined() -> None:
    """Mixed stream of different block_times and within-second swaps → canonical order."""
    # Build a deliberately scrambled stream:
    #   BT=060, slot=200  (within-second, arrive 3rd)
    #   BT=030, slot=100  (earlier block, arrive 5th)
    #   BT=060, slot=300  (within-second, arrive 1st)
    #   BT=060, slot=100  (within-second, arrive 4th)
    #   BT=090, slot=200  (later block, arrive 2nd)
    events = [
        _make_swap(1_700_000_060, 200, "SIG_BT60_SL200_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        _make_swap(1_700_000_030, 100, "SIG_BT30_SL100_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"),
        _make_swap(1_700_000_060, 300, "SIG_BT60_SL300_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"),
        _make_swap(1_700_000_060, 100, "SIG_BT60_SL100_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"),
        _make_swap(1_700_000_090, 200, "SIG_BT90_SL200_EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"),
    ]
    normalized = _run_recorder(events)

    assert len(normalized) == 5

    # Expected canonical order: (BT, slot) ascending
    # (030, 100), (060, 100), (060, 200), (060, 300), (090, 200)
    expected_keys = [
        (1_700_000_030, 100),
        (1_700_000_060, 100),
        (1_700_000_060, 200),
        (1_700_000_060, 300),
        (1_700_000_090, 200),
    ]
    actual_keys = [(ns.block_time, ns.slot) for ns in normalized]
    assert actual_keys == expected_keys, (
        f"Canonical (block_time, slot) order mismatch.\n"
        f"  Expected: {expected_keys}\n"
        f"  Got:      {actual_keys}"
    )


def test_sort_does_not_affect_failed_swap_drop() -> None:
    """A failed swap among within-second swaps is still dropped; landed swaps are sorted."""
    events = [
        _make_swap(_SAME_BT, 300, "SIG_SLOT300_LAND_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        _make_swap(_SAME_BT, 200, "SIG_SLOT200_FAIL_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                   failed=True),
        _make_swap(_SAME_BT, 100, "SIG_SLOT100_LAND_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"),
    ]
    normalized = _run_recorder(events)

    # Only 2 landed swaps — failed is dropped
    assert len(normalized) == 2, (
        f"Expected 2 NormalizedSwaps (failed dropped), got {len(normalized)}"
    )

    # Remaining landed swaps must still be in canonical order
    slots = [ns.slot for ns in normalized]
    assert slots == [100, 300], (
        f"After failed-drop, canonical slot order must be [100, 300], got {slots}"
    )
