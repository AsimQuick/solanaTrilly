# ---
# module: core.tests.test_firehose_tape_streaming_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, asyncio, core.clock, core.tape.recorder,
#               core.management.commands.run_firehose, core.firehose.spine,
#               core.normalized_swap, core.pregrad_features
# ---
"""STREAM-AS-RECORDED regression tests for the firehose collection -> TapeStore tap.

THE BUG (pre-fix): _collection_loop did `await recorder.run()` THEN mirrored
recorder._normalized_swaps_with_mints into self._tape.  The Helius birth-tape
source is a CONTINUOUS live stream, so recorder.run() never returns until
shutdown — the TapeStore stayed empty for the whole run and every scoring tick
logged "no pre-grad tape yet (0 swaps) — deferring".  The model never scored.

THE FIX: a per-swap on_swap tap on TapeRecorder tees each recorded swap into the
TapeStore the INSTANT it is normalized, inside run(), before run() returns.

These tests prove the tape is populated INCREMENTALLY (while the source is still
active), not only after completion, and that the scoring task can then assemble
features + produce a score from a mid-run-populated tape.  Offline, deterministic,
no network.

Style note: this project drives coroutines with asyncio.run() inside sync test
functions (see test_tape_recorder_ac181.py) — it does NOT use pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import pytest

from core.clock import VirtualClock
from core.datasource import DataSource
from core.management.commands.run_firehose import TapeStore, _ns_to_swap
from core.tape.recorder import TapeRecorder

_MINT = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_GRAD_BT = 1000


def _raw_swap(block_time: int, slot: int, sig: str, side: str, owner: str, vol: float, price: float) -> dict:
    """A landed, non-degenerate Helius-style internal swap dict for _MINT."""
    return {
        "mint": _MINT,
        "block_time": block_time,
        "slot": slot,
        "signature": sig,
        "side": side,
        "price": price,
        "vol_sol": vol,
        "vol_usd": 0.0,
        "sol_usd": 0.0,
        "owner": owner,
        "base_reserve": 1_000_000,
        "quote_reserve": 5_000,
        "quote_mint": "So11111111111111111111111111111111111111112",
        "failed": False,
    }


# Twenty PRE-grad swaps (block_time < _GRAD_BT => rel < 0) — satisfies US-76 P2.4
# secondary gate (>=20 pre-grad swaps required for feature assembly).
_RAW_SWAPS = [
    _raw_swap(700, 1,  "s1",  "buy",  "A", 2.0,  0.001),
    _raw_swap(710, 2,  "s2",  "buy",  "B", 1.0,  0.0011),
    _raw_swap(720, 3,  "s3",  "buy",  "C", 3.0,  0.0012),
    _raw_swap(730, 4,  "s4",  "sell", "A", 1.5,  0.0013),
    _raw_swap(740, 5,  "s5",  "buy",  "A", 0.5,  0.0014),
    _raw_swap(750, 6,  "s6",  "buy",  "D", 4.0,  0.0015),
    _raw_swap(760, 7,  "s7",  "buy",  "E", 1.2,  0.0016),
    _raw_swap(770, 8,  "s8",  "buy",  "F", 0.8,  0.0017),
    _raw_swap(780, 9,  "s9",  "sell", "B", 0.5,  0.0018),
    _raw_swap(790, 10, "s10", "buy",  "G", 2.1,  0.0019),
    _raw_swap(800, 11, "s11", "buy",  "H", 0.9,  0.0020),
    _raw_swap(810, 12, "s12", "buy",  "I", 1.5,  0.0021),
    _raw_swap(820, 13, "s13", "sell", "C", 2.0,  0.0022),
    _raw_swap(830, 14, "s14", "buy",  "J", 0.7,  0.0023),
    _raw_swap(840, 15, "s15", "buy",  "K", 1.1,  0.0024),
    _raw_swap(850, 16, "s16", "buy",  "L", 0.6,  0.0025),
    _raw_swap(860, 17, "s17", "buy",  "D", 3.0,  0.0026),
    _raw_swap(870, 18, "s18", "sell", "E", 0.4,  0.0027),
    _raw_swap(880, 19, "s19", "buy",  "M", 1.8,  0.0028),
    _raw_swap(890, 20, "s20", "buy",  "N", 0.3,  0.0029),
]


def _token_store() -> dict[str, Any]:
    return {_MINT: type("T", (), {"graduated_block_time": _GRAD_BT})()}


class _ContinuousMockSource(DataSource):
    """A continuous live-style source: yields N swaps, then NEVER ends (until cancel).

    After yielding the last seeded swap it sets *streamed_all* and then awaits an
    Event that the driver never sets — emulating a live stream that keeps the
    connection open.  This is exactly the condition under which the old
    mirror-after-run() code left the TapeStore empty forever.
    """

    def __init__(self, swaps: list[dict]) -> None:
        self._swaps = swaps
        self.streamed_all = asyncio.Event()
        self._hold = asyncio.Event()  # never set -> source stays "live"
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        for s in self._swaps:
            yield s
            await asyncio.sleep(0)  # let a concurrent reader observe the tap mid-stream
        self.streamed_all.set()
        await self._hold.wait()  # stay "live" until cancelled (firehose shutdown)


def test_tape_populated_incrementally_while_source_still_active():
    """The on_swap tap fills the TapeStore mid-stream, BEFORE recorder.run() returns."""
    tape = TapeStore()
    source = _ContinuousMockSource(list(_RAW_SWAPS))
    recorder = TapeRecorder(
        source=source,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
        token_store=_token_store(),
        swap_source="helius_live",
        swap_phase="pre",
        on_swap=lambda mint, ns: tape.add(mint, _ns_to_swap(ns)),
    )

    async def _drive():
        run_task = asyncio.create_task(recorder.run())
        try:
            # Wait until the source has streamed everything but is STILL active
            # (run_task has NOT returned — the source is blocked on its hold event).
            await asyncio.wait_for(source.streamed_all.wait(), timeout=2.0)
            for _ in range(10):  # drain the final queued swap into the tap
                if tape.count(_MINT) >= len(_RAW_SWAPS):
                    break
                await asyncio.sleep(0)
            return run_task.done(), tape.count(_MINT), tape.get(_MINT)
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    run_done, count, stored = asyncio.run(_drive())

    # CORE ASSERTION: the tape is fully populated WHILE the source is still live.
    assert run_done is False, "recorder.run() returned — source was not continuous"
    assert count == len(_RAW_SWAPS), (
        "TapeStore was not populated incrementally during the live stream"
    )
    assert all("block_time" in s and "price" in s and "side" in s for s in stored)


def test_old_mirror_after_finish_would_have_been_empty_midrun():
    """Control: WITHOUT the tap, the tape is empty while the source is still live.

    This pins the exact regression — a recorder with no on_swap tap (the old
    behaviour, where population happened only after run() returned) leaves a
    concurrent reader with an empty tape for the whole continuous stream.
    """
    tape = TapeStore()
    source = _ContinuousMockSource(list(_RAW_SWAPS))
    recorder = TapeRecorder(
        source=source,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
        token_store=_token_store(),
        swap_source="helius_live",
        swap_phase="pre",
        on_swap=None,  # old behaviour: nothing tees into the store mid-run
    )

    async def _drive():
        run_task = asyncio.create_task(recorder.run())
        try:
            await asyncio.wait_for(source.streamed_all.wait(), timeout=2.0)
            await asyncio.sleep(0)
            return run_task.done(), tape.count(_MINT)
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    run_done, count = asyncio.run(_drive())
    assert run_done is False
    assert count == 0, "without the tap the tape must stay empty during a live stream"


def test_clean_shutdown_while_streaming_cancels_run():
    """Cancelling the run task while the source is still live shuts down cleanly."""
    source = _ContinuousMockSource(list(_RAW_SWAPS))
    recorder = TapeRecorder(
        source=source,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
        token_store=_token_store(),
        swap_source="helius_live",
        swap_phase="pre",
        on_swap=lambda mint, ns: None,
    )

    async def _drive():
        run_task = asyncio.create_task(recorder.run())
        await asyncio.wait_for(source.streamed_all.wait(), timeout=2.0)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task
        return source.disconnected

    disconnected = asyncio.run(_drive())
    # stamp_events' finally disconnects the source on cancellation.
    assert disconnected is True


def test_misbehaving_tap_never_breaks_recording():
    """A raising on_swap tap is swallowed — recording/persistence is unaffected."""
    source = _ContinuousMockSource(list(_RAW_SWAPS))

    def _boom(_mint, _ns):
        raise RuntimeError("tap blew up")

    recorder = TapeRecorder(
        source=source,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
        token_store=_token_store(),
        swap_source="helius_live",
        swap_phase="pre",
        on_swap=_boom,
    )

    async def _drive():
        run_task = asyncio.create_task(recorder.run())
        try:
            await asyncio.wait_for(source.streamed_all.wait(), timeout=2.0)
            for _ in range(10):
                if len(recorder.normalized_swaps) >= len(_RAW_SWAPS):
                    break
                await asyncio.sleep(0)
            return len(recorder.normalized_swaps)
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    count = asyncio.run(_drive())
    assert count == len(_RAW_SWAPS), "recording must survive a raising tap"


def test_midrun_tape_assembles_features_and_scores():
    """A TapeStore populated mid-run for a graduated mint yields features + a score."""
    from core.firehose.spine import (
        assemble_pregrad_features,
        gate_passes,
        score_pregrad,
    )
    from core.normalized_swap import NormalizedSwap
    from core.pregrad_features import PRE_FEATURE_NAMES

    # Simulate the tap having teed each recorded NormalizedSwap into the store mid-run.
    tape = TapeStore()
    token = type("T", (), {"graduated_block_time": _GRAD_BT})()
    for raw in _RAW_SWAPS:
        ns = NormalizedSwap.from_raw_swap(raw, token, source="helius_live", phase="pre")
        tape.add(_MINT, _ns_to_swap(ns))

    swaps = tape.get(_MINT)
    assert len(swaps) == len(_RAW_SWAPS)

    features = assemble_pregrad_features(swaps, _GRAD_BT)
    assert features is not None, "mid-run tape must assemble the pre-grad feature row"
    for name in PRE_FEATURE_NAMES:
        assert name in features

    class _StubScorer:
        labels = ["ctrl", "oracle", "liq"]

        def score_pool(self, fl):
            return [
                {
                    "label_scores": {label: 0.9 for label in self.labels},
                    "label_ranks": {label: 0.9 for label in self.labels},
                    "blend_score": 0.9,
                }
                for _ in fl
            ]

        def score_single(self, f, ref):
            return self.score_pool([f])[0]

    result = score_pregrad(features, scorer=_StubScorer())
    assert 0.0 <= result["blend_score"] <= 1.0
    assert gate_passes(result["blend_score"], gate="threshold", threshold=0.8) is True
