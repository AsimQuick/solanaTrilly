# ---
# module: core.tests.test_swap_writer_ac192
# sprint: sprint-5
# story: US-19 AC-19.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.swap_writer, core.tape.recorder, core.normalized_swap,
#               core.models, core.clock, core.replay_source, asyncio, pytest, types
# ---
"""AC-19.2 — SwapWriter mirrors NormalizedSwaps to the 'swaps' DB table, idempotently.

Tests:
  1. test_swap_writer_creates_swap_row
       write() with one (mint, NormalizedSwap) pair creates exactly one Swap row.

  2. test_swap_writer_row_fields_match_normalized_swap
       All Swap row fields match the NormalizedSwap source values.

  3. test_swap_writer_idempotent_same_mint_signature
       Calling write() twice with the same (mint, signature) does NOT create a
       second row — Swap.objects.count() remains 1.

  4. test_swap_writer_multiple_swaps_all_written
       write() with N (mint, NormalizedSwap) pairs creates exactly N Swap rows.

  5. test_swap_writer_rerecord_batch_no_duplicates
       Re-calling write() with the same batch of swaps yields no new rows —
       count stays at N.

  6. test_swap_writer_nullable_fields_round_trip
       owner=None, base_reserve=None, quote_reserve=None survive the DB round-trip.

  7. test_recorder_with_swap_writer_writes_to_db
       Full TapeRecorder + ReplaySource + VirtualClock + SwapWriter integration:
       asyncio.run(recorder.run()) produces Swap rows with correct field values.

  8. test_recorder_with_swap_writer_idempotent_on_rerun
       Running recorder.run() twice over the same events via two separate recorder
       instances with a shared SwapWriter does not produce duplicate Swap rows.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.models import Swap
from core.normalized_swap import NormalizedSwap

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)
_MINT = "PUMP_MINT_1111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"
_BLOCK_TIME = 1_700_000_060


def _make_normalized_swap(n: int = 1, *, side: str = "buy") -> NormalizedSwap:
    """Return a deterministic NormalizedSwap with index *n*."""
    sig = f"SIG_{n:04d}" + "A" * 84
    return NormalizedSwap(
        rel=float(_BLOCK_TIME - _GRADUATED_BLOCK_TIME),
        block_time=_BLOCK_TIME + n,
        slot=200 + n,
        signature=sig[:128],
        price=0.00025 + n * 0.00001,
        side=side,
        vol_sol=2.5 + n * 0.1,
        vol_usd=375.0 + n * 10.0,
        sol_usd=150.0,
        owner="SIGNER_WALLET_" + f"{n:04d}" + "X" * 46,
        base_reserve=5_000_000 + n,
        quote_reserve=1_250_000 + n,
        quote_mint=_QUOTE_MINT,
        source="birdeye_live",
        phase="pre",
    )


def _make_raw_swap_event(n: int = 1, *, failed: bool = False) -> dict:
    """Return a raw swap event that TapeRecorder can normalize."""
    sig = f"SIG_{n:04d}" + "A" * 84
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _BLOCK_TIME + n,
        "slot": 200 + n,
        "signature": sig[:128],
        "side": "buy",
        "price": 0.00025 + n * 0.00001,
        "vol_sol": 2.5 + n * 0.1,
        "vol_usd": 375.0 + n * 10.0,
        "sol_usd": 150.0,
        "owner": "SIGNER_WALLET_" + f"{n:04d}" + "X" * 46,
        "base_reserve": 5_000_000 + n,
        "quote_reserve": 1_250_000 + n,
        "quote_mint": _QUOTE_MINT,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Unit tests — SwapWriter in isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_swap_writer_creates_swap_row() -> None:
    """write() with one (mint, NormalizedSwap) pair must create exactly one Swap row."""
    from core.tape.swap_writer import SwapWriter

    swap = _make_normalized_swap(1)
    writer = SwapWriter()
    count_before = Swap.objects.count()

    writer.write([(_MINT, swap)])

    assert Swap.objects.count() == count_before + 1


@pytest.mark.django_db
def test_swap_writer_row_fields_match_normalized_swap() -> None:
    """All Swap row fields must match the source NormalizedSwap values."""
    from core.tape.swap_writer import SwapWriter

    swap = _make_normalized_swap(2)
    writer = SwapWriter()
    writer.write([(_MINT, swap)])

    row = Swap.objects.get(mint=_MINT, signature=swap.signature)

    assert row.mint == _MINT
    assert row.block_time == swap.block_time
    assert row.slot == swap.slot
    assert row.signature == swap.signature
    assert row.side == swap.side
    assert row.price == pytest.approx(swap.price)
    assert row.vol_sol == pytest.approx(swap.vol_sol)
    assert row.vol_usd == pytest.approx(swap.vol_usd)
    assert row.sol_usd == pytest.approx(swap.sol_usd)
    assert row.owner == swap.owner
    assert row.base_reserve == swap.base_reserve
    assert row.quote_reserve == swap.quote_reserve
    assert row.rel == pytest.approx(swap.rel)


@pytest.mark.django_db
def test_swap_writer_idempotent_same_mint_signature() -> None:
    """Re-writing the same (mint, signature) must NOT create a second Swap row."""
    from core.tape.swap_writer import SwapWriter

    swap = _make_normalized_swap(3)
    writer = SwapWriter()

    writer.write([(_MINT, swap)])
    count_after_first = Swap.objects.count()

    writer.write([(_MINT, swap)])
    count_after_second = Swap.objects.count()

    assert count_after_second == count_after_first, (
        f"Re-recording the same swap created a duplicate row. "
        f"Count before={count_after_first}, after={count_after_second}"
    )


@pytest.mark.django_db
def test_swap_writer_multiple_swaps_all_written() -> None:
    """write() with N pairs must create exactly N distinct Swap rows."""
    from core.tape.swap_writer import SwapWriter

    swaps = [(_MINT, _make_normalized_swap(i)) for i in range(10, 15)]
    writer = SwapWriter()
    count_before = Swap.objects.count()

    writer.write(swaps)

    assert Swap.objects.count() == count_before + 5


@pytest.mark.django_db
def test_swap_writer_rerecord_batch_no_duplicates() -> None:
    """Re-calling write() with the same batch must yield no new rows (idempotency)."""
    from core.tape.swap_writer import SwapWriter

    batch = [(_MINT, _make_normalized_swap(i)) for i in range(20, 25)]
    writer = SwapWriter()

    writer.write(batch)
    count_after_first = Swap.objects.count()

    writer.write(batch)
    count_after_second = Swap.objects.count()

    assert count_after_second == count_after_first, (
        f"Re-recording the same batch created duplicates. "
        f"Count went from {count_after_first} to {count_after_second}"
    )


@pytest.mark.django_db
def test_swap_writer_nullable_fields_round_trip() -> None:
    """owner=None, base_reserve=None, quote_reserve=None survive the DB round-trip."""
    from core.tape.swap_writer import SwapWriter

    sig = "NULLSIG" + "B" * 85
    swap = NormalizedSwap(
        rel=60.0,
        block_time=_BLOCK_TIME + 99,
        slot=299,
        signature=sig[:128],
        price=0.0003,
        side="sell",
        vol_sol=1.0,
        vol_usd=150.0,
        sol_usd=150.0,
        owner=None,
        base_reserve=None,
        quote_reserve=None,
        quote_mint=_QUOTE_MINT,
        source="birdeye_live",
        phase="pre",
    )

    writer = SwapWriter()
    writer.write([(_MINT, swap)])

    row = Swap.objects.get(mint=_MINT, signature=sig[:128])
    assert row.owner is None
    assert row.base_reserve is None
    assert row.quote_reserve is None


# ---------------------------------------------------------------------------
# Integration tests — TapeRecorder + SwapWriter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recorder_with_swap_writer_writes_to_db() -> None:
    """TapeRecorder + SwapWriter integration: recorder.run() produces Swap rows.

    asyncio.run(recorder.run()) must create Swap rows whose field values match
    the recorder's normalized_swaps list.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder
    from core.tape.swap_writer import SwapWriter

    events = [_make_raw_swap_event(i) for i in range(30, 33)]
    token_store = {_MINT: _TOKEN}
    t0 = datetime(2023, 11, 14, 0, 0, 0, tzinfo=timezone.utc)

    swap_writer = SwapWriter()
    source = ReplaySource(event_log=events)
    clock = VirtualClock(t0)
    recorder = TapeRecorder(source, clock, token_store=token_store, swap_writer=swap_writer)

    count_before = Swap.objects.count()
    asyncio.run(recorder.run())

    normalized = recorder.normalized_swaps
    assert len(normalized) == 3, f"Expected 3 normalized swaps, got {len(normalized)}"
    assert Swap.objects.count() == count_before + 3

    for ns in normalized:
        row = Swap.objects.get(mint=_MINT, signature=ns.signature)
        assert row.block_time == ns.block_time
        assert row.slot == ns.slot
        assert row.side == ns.side
        assert row.price == pytest.approx(ns.price)
        assert row.vol_sol == pytest.approx(ns.vol_sol)
        assert row.vol_usd == pytest.approx(ns.vol_usd)
        assert row.sol_usd == pytest.approx(ns.sol_usd)
        assert row.owner == ns.owner
        assert row.base_reserve == ns.base_reserve
        assert row.quote_reserve == ns.quote_reserve
        assert row.rel == pytest.approx(ns.rel)


@pytest.mark.django_db
def test_recorder_with_swap_writer_idempotent_on_rerun() -> None:
    """Running recorder.run() twice over the same events must not produce duplicates.

    Two independent TapeRecorder instances sharing the same SwapWriter write the
    same events; the Swap table row count must equal the distinct swap count, not 2×.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder
    from core.tape.swap_writer import SwapWriter

    events = [_make_raw_swap_event(i) for i in range(40, 43)]
    token_store = {_MINT: _TOKEN}
    t0 = datetime(2023, 11, 14, 0, 0, 0, tzinfo=timezone.utc)
    swap_writer = SwapWriter()

    # First run
    recorder1 = TapeRecorder(
        ReplaySource(event_log=events),
        VirtualClock(t0),
        token_store=token_store,
        swap_writer=swap_writer,
    )
    asyncio.run(recorder1.run())
    count_after_first = Swap.objects.filter(mint=_MINT).count()

    # Second run — same events
    recorder2 = TapeRecorder(
        ReplaySource(event_log=events),
        VirtualClock(t0),
        token_store=token_store,
        swap_writer=swap_writer,
    )
    asyncio.run(recorder2.run())
    count_after_second = Swap.objects.filter(mint=_MINT).count()

    assert count_after_second == count_after_first, (
        f"Re-running the recorder created duplicate Swap rows. "
        f"Count after first={count_after_first}, after second={count_after_second}"
    )
