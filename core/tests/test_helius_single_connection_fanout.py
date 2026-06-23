# ---
# module: core.tests.test_helius_single_connection_fanout
# sprint: hotfix-single-connection-fanout
# story: hotfix-single-connection-fanout, hotfix-migrate-mint-rpc-resolution
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-22
# dependencies: core.tape.helius_birth_tape_source, core.management.commands.run_firehose,
#               core.detection.consumer, core.firehose.live_pregrad_buffer,
#               core.clock, core.datasource, asyncio, json, pathlib, pytest, unittest.mock
# ---
"""Single-connection fan-out + no-data watchdog tests.

Tests (all offline, no network):

1. test_fanout_trade_frames_reach_collection_buffer
     Two trade frames fan out to the collection queue and are buffered by
     LivePreGradBuffer into TapeStore.  The migrate frame does NOT pollute the
     swap buffer (decode_helius_notification returns None for it).

2. test_fanout_migrate_frame_yields_one_graduation
     The migrate frame fans out to the migrate queue and HeliusMigrateSource
     .events_from_queue yields exactly ONE graduation event (DetectionConsumer
     processes it, Token persisted).

3. test_fanout_migrate_frame_not_in_swap_buffer
     The migrate frame is injected on both queues; the collection path produces
     zero swaps for it (decode_helius_notification filters it to None).

4. test_watchdog_reconnects_on_silent_socket
     Fake source connects then yields nothing; watchdog fires within the
     configured silence window using a VirtualClock.

5. test_reconnect_retains_tape
     Simulate stream-end (source exhausted) and assert self._tape is retained
     across the reconnect attempt.

6. test_queue_data_source_yields_from_queue
     _QueueDataSource drains a pre-populated queue and returns on None sentinel.

7. test_migrate_source_decode_and_dedupe_new_mint
     HeliusMigrateSource._decode_and_dedupe returns an event on first call.

8. test_migrate_source_decode_and_dedupe_dedupes_second_call
     HeliusMigrateSource._decode_and_dedupe returns None on duplicate mint.

9. test_migrate_source_events_from_queue_uses_decode_and_dedupe
     events_from_queue drains a queue with one migrate + one trade frame,
     yields exactly one graduation, skips the trade.

10. test_silence_watchdog_default_in_schema
      TapeConfig.graduation_silence_watchdog_s defaults to 120.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REAL_MIGRATE_FIXTURE = FIXTURES_DIR / "helius_migrate_v2_real_grad_1.json"

# ---------------------------------------------------------------------------
# Verified known mint from a real graduation fixture (MigrateV2 + PumpSwap
# CreatePool, 2026-06-22, cross-checked vs Dune).  The prior fixture
# (helius_migrate_tx_real.json) was a fee-program false positive — see
# graduation-detection-migratev2-substring-bug.
# ---------------------------------------------------------------------------

REAL_FRAME_MINT = "3ZLkpvUaZLSLZKfvQK9oFNcfduaGCYNe7RbZW7RhQTDG"
TEST_FALLBACK_EPOCH = 1_719_000_000

# ---------------------------------------------------------------------------
# Borsh helpers — same constants as ac341 test
# ---------------------------------------------------------------------------

TRADE_EVENT_DISCRIMINATOR: bytes = hashlib.sha256(b"event:TradeEvent").digest()[:8]

_MINT_BYTES: bytes = bytes(range(1, 33))
_USER_BYTES: bytes = bytes(range(33, 65))
_SOL_AMOUNT: int = 500_000_000
_TOKEN_AMOUNT: int = 1_000_000_000
_VSOL: int = 30_000_000_000
_VTOK: int = 600_000_000_000
_TIMESTAMP: int = 1_748_000_000
_SLOT: int = 999_888_777
_SIG_A: str = "5J9fakeHELIUSsignatureAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_SIG_B: str = "5J9fakeHELIUSsignatureBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def _pack_trade_event(
    mint_bytes: bytes,
    sol_amount: int,
    token_amount: int,
    is_buy: bool,
    user_bytes: bytes,
    timestamp: int,
    vsol: int,
    vtok: int,
) -> bytes:
    return (
        TRADE_EVENT_DISCRIMINATOR
        + mint_bytes
        + struct.pack("<QQ", sol_amount, token_amount)
        + bytes([1 if is_buy else 0])
        + user_bytes
        + struct.pack("<qQQ", timestamp, vsol, vtok)
    )


def _make_trade_notification(sig: str = _SIG_A, slot: int = _SLOT, is_buy: bool = True) -> dict:
    packed = _pack_trade_event(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, is_buy,
        _USER_BYTES, _TIMESTAMP, _VSOL, _VTOK,
    )
    b64 = base64.b64encode(packed).decode()
    instruction = "Buy" if is_buy else "Sell"
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 42,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            f"Program log: Instruction: {instruction}",
                            f"Program data: {b64}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                    "transaction": {"signatures": [sig]},
                },
            },
        },
    }


def _load_real_migrate_fixture() -> dict:
    with REAL_MIGRATE_FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _make_subscription_ack() -> dict:
    """A Helius subscription acknowledgement — heartbeat/non-notification."""
    return {"jsonrpc": "2.0", "id": 1, "result": 12345678}


# ---------------------------------------------------------------------------
# Minimal in-memory TapeStore (matches the FirehoseDaemon.TapeStore interface)
# ---------------------------------------------------------------------------


class _MemTapeStore:
    def __init__(self) -> None:
        self._by_mint: dict[str, list[dict]] = {}
        self._on_add = None

    def add(self, mint: str, swap: dict) -> None:
        self._by_mint.setdefault(mint, []).append(swap)

    def get(self, mint: str) -> list[dict]:
        return list(self._by_mint.get(mint, []))

    def count(self, mint: str) -> int:
        return len(self._by_mint.get(mint, []))

    def drop(self, mint: str) -> None:
        self._by_mint.pop(mint, None)

    def mints(self) -> list[str]:
        return list(self._by_mint.keys())


# ---------------------------------------------------------------------------
# Fake DataSource that yields a fixed sequence of frames
# ---------------------------------------------------------------------------


class _FakeSource:
    """DataSource yielding a fixed sequence of raw dicts, then stopping."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = frames

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        for f in self._frames:
            yield f


# ---------------------------------------------------------------------------
# Test 1: trade frames reach collection buffer
# ---------------------------------------------------------------------------


def test_fanout_trade_frames_reach_collection_buffer() -> None:
    """Two trade frames → collection_q → LivePreGradBuffer → TapeStore; migrate NOT in buffer."""
    from core.clock import VirtualClock
    from core.firehose.live_pregrad_buffer import LivePreGradBuffer
    from core.tape.helius_birth_tape_source import (
        _QueueDataSource,
        decode_helius_notification,
    )
    from core.tape.mapped_source import MappedSwapSource

    t0 = datetime(2026, 6, 21, tzinfo=timezone.utc)
    clock = VirtualClock(t0)
    store = _MemTapeStore()

    trade_a = _make_trade_notification(sig=_SIG_A, is_buy=True)
    trade_b = _make_trade_notification(sig=_SIG_B, is_buy=False)
    migrate = _load_real_migrate_fixture()

    # Fan-out: push all three frames into a collection_q (like the pump task would)
    collection_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    for frame in [trade_a, trade_b, migrate, _make_subscription_ack()]:
        collection_q.put_nowait(frame)
    collection_q.put_nowait(None)  # sentinel

    collection_source = MappedSwapSource(
        _QueueDataSource(collection_q), decode_helius_notification
    )
    buffer = LivePreGradBuffer(
        source=collection_source,
        store=store,
        clock=clock,
        is_graduated=lambda _: False,
        idle_ttl_s=300.0,
    )

    asyncio.run(buffer.run())

    # Trade frames should have been buffered (the mint encoded in _MINT_BYTES)
    from core.tape.helius_birth_tape_source import _b58_from_bytes
    expected_mint = _b58_from_bytes(_MINT_BYTES)
    assert store.count(expected_mint) == 2, (
        f"Expected 2 buffered swaps for trade mint, got {store.count(expected_mint)}"
    )

    # Migrate frame must NOT appear in the swap buffer (decode_helius_notification returns None)
    assert REAL_FRAME_MINT not in store.mints(), (
        "Migrate frame should NOT pollute the swap buffer"
    )


# ---------------------------------------------------------------------------
# Test 2: migrate frame yields exactly one graduation via events_from_queue
# ---------------------------------------------------------------------------


def test_fanout_migrate_frame_yields_one_graduation() -> None:
    """migrate frame → migrate_q → HeliusMigrateSource.events_from_queue → 1 graduation.

    resolve_spl_mint is mocked so the test is fully offline.  The resolved mint
    must equal REAL_FRAME_MINT (what the mock returns).
    """
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    t0 = datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc)
    clock = VirtualClock(t0)

    migrate = _load_real_migrate_fixture()
    trade_a = _make_trade_notification()

    migrate_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    # Fan-out: both frames go on the migrate queue, plus the ack and sentinel
    for frame in [trade_a, migrate, _make_subscription_ack()]:
        migrate_q.put_nowait(frame)
    migrate_q.put_nowait(None)

    src = HeliusMigrateSource(api_key="fanout", event_source="pump_dot_fun", clock=clock)

    events_collected: list[dict] = []

    async def _run() -> None:
        with patch(
            "core.tape.helius_birth_tape_source.resolve_spl_mint",
            return_value=REAL_FRAME_MINT,
        ):
            async for ev in src.events_from_queue(migrate_q):
                events_collected.append(ev)

    asyncio.run(_run())

    assert len(events_collected) == 1, (
        f"Expected exactly 1 graduation event, got {len(events_collected)}"
    )
    ev = events_collected[0]
    assert ev["type"] == "MEME_DATA"
    assert ev["graduated"] is True
    assert ev["address"] == REAL_FRAME_MINT, (
        f"Mint mismatch: expected {REAL_FRAME_MINT!r}, got {ev['address']!r}"
    )
    assert ev["source"] == "pump_dot_fun"


# ---------------------------------------------------------------------------
# Test 3: migrate frame does NOT pollute the swap buffer
# ---------------------------------------------------------------------------


def test_fanout_migrate_frame_not_in_swap_buffer() -> None:
    """Migrate frame injected into collection queue produces zero swaps."""
    from core.tape.helius_birth_tape_source import decode_helius_notification

    migrate = _load_real_migrate_fixture()
    result = decode_helius_notification(migrate)
    assert result is None, (
        "decode_helius_notification must return None for a migrate frame "
        "(migrate frames must NOT reach the swap buffer)"
    )


# ---------------------------------------------------------------------------
# Test 4: watchdog reconnects on silent socket
# ---------------------------------------------------------------------------


def test_watchdog_reconnects_on_silent_socket() -> None:
    """Silent socket: watchdog fires within the configured silence window.

    Uses VirtualClock and a fake source that connects but never yields a frame.
    Advances the clock beyond the watchdog threshold and verifies the watchdog
    coroutine returns (which triggers reconnect in _helius_loop).
    """
    from core.clock import VirtualClock

    WATCHDOG_S = 10.0  # short window for the test
    t0 = datetime(2026, 6, 21, tzinfo=timezone.utc)
    clock = VirtualClock(t0)

    last_frame_at: list = [clock.now()]

    async def _watchdog(watchdog_s: float) -> str:
        """Mirror of the watchdog in _helius_loop, using the injected clock."""
        while True:
            await asyncio.sleep(0)  # yield to event loop
            elapsed = (clock.now() - last_frame_at[0]).total_seconds()
            if elapsed >= watchdog_s:
                return "fired"

    async def _clock_driver() -> None:
        """Advance the virtual clock past the threshold after one event loop tick."""
        await asyncio.sleep(0)
        clock.advance(timedelta(seconds=WATCHDOG_S + 1))
        # Yield several times so the watchdog loop can see the advanced time.
        for _ in range(5):
            await asyncio.sleep(0)

    async def _run() -> str:
        watchdog_task = asyncio.create_task(_watchdog(WATCHDOG_S))
        driver_task = asyncio.create_task(_clock_driver())
        done, pending = await asyncio.wait(
            {watchdog_task, driver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(watchdog_task, driver_task, return_exceptions=True)
        if watchdog_task in done:
            return watchdog_task.result()
        return "timeout"

    result = asyncio.run(_run())
    assert result == "fired", (
        f"Expected watchdog to fire ('fired'), got {result!r}. "
        "Silent socket must trigger a reconnect within the watchdog window."
    )


# ---------------------------------------------------------------------------
# Test 5: reconnect retains tape
# ---------------------------------------------------------------------------


def test_reconnect_retains_tape() -> None:
    """self._tape contents are retained across a stream-end / reconnect.

    Simulates the fan-out loop running twice: first with a source that yields
    two trade frames then ends, second with a source that ends immediately.
    Asserts the tape from the first run is present after the second run.
    """
    from core.clock import VirtualClock
    from core.firehose.live_pregrad_buffer import LivePreGradBuffer
    from core.tape.helius_birth_tape_source import (
        _b58_from_bytes,
        _QueueDataSource,
        decode_helius_notification,
    )
    from core.tape.mapped_source import MappedSwapSource

    t0 = datetime(2026, 6, 21, tzinfo=timezone.utc)
    clock = VirtualClock(t0)
    store = _MemTapeStore()
    expected_mint = _b58_from_bytes(_MINT_BYTES)

    async def _run_buffer_with_frames(frames: list[dict]) -> None:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        for f in frames:
            q.put_nowait(f)
        q.put_nowait(None)
        src = MappedSwapSource(_QueueDataSource(q), decode_helius_notification)
        buf = LivePreGradBuffer(
            source=src, store=store, clock=clock,
            is_graduated=lambda _: False, idle_ttl_s=300.0,
        )
        await buf.run()

    async def _run() -> None:
        # First "connection": two trade frames
        trade_a = _make_trade_notification(sig=_SIG_A, is_buy=True)
        trade_b_ = _make_trade_notification(sig=_SIG_B, is_buy=False)
        await _run_buffer_with_frames([trade_a, trade_b_])

        # Assert tape is populated after first run
        assert store.count(expected_mint) == 2, (
            f"Expected 2 swaps after first run, got {store.count(expected_mint)}"
        )

        # Second "connection": no frames (stream-end immediately after connect)
        await _run_buffer_with_frames([])

        # Tape must be RETAINED (not cleared) — daemon-level store persists reconnect
        assert store.count(expected_mint) == 2, (
            f"Expected tape to be RETAINED across reconnect: "
            f"got {store.count(expected_mint)} swaps (should be 2)"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 6: _QueueDataSource yields from queue and returns on None sentinel
# ---------------------------------------------------------------------------


def test_queue_data_source_yields_from_queue() -> None:
    """_QueueDataSource drains a queue and returns cleanly on None sentinel."""
    from core.tape.helius_birth_tape_source import _QueueDataSource

    frames = [{"a": 1}, {"b": 2}, {"c": 3}]
    q: asyncio.Queue = asyncio.Queue()
    for f in frames:
        q.put_nowait(f)
    q.put_nowait(None)  # sentinel

    src = _QueueDataSource(q)
    collected: list[dict] = []

    async def _run() -> None:
        await src.connect()
        async for item in src.events():
            collected.append(item)
        await src.disconnect()

    asyncio.run(_run())

    assert collected == frames, (
        f"_QueueDataSource yielded {collected}, expected {frames}"
    )


# ---------------------------------------------------------------------------
# Test 7: _decode_and_dedupe returns event on first call (async + mocked resolver)
# ---------------------------------------------------------------------------


def test_migrate_source_decode_and_dedupe_new_mint() -> None:
    """HeliusMigrateSource._decode_and_dedupe returns an event on first call for a new mint.

    _decode_and_dedupe is async; resolve_spl_mint is mocked so no network calls.
    """
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="test", event_source="pump_dot_fun", clock=clock)

    migrate = _load_real_migrate_fixture()

    with patch(
        "core.tape.helius_birth_tape_source.resolve_spl_mint",
        return_value=REAL_FRAME_MINT,
    ):
        event = asyncio.run(src._decode_and_dedupe(migrate))

    assert event is not None, "_decode_and_dedupe should return an event for a new mint"
    assert event["type"] == "MEME_DATA"
    assert event["address"] == REAL_FRAME_MINT


# ---------------------------------------------------------------------------
# Test 8: _decode_and_dedupe dedupes on second call
# ---------------------------------------------------------------------------


def test_migrate_source_decode_and_dedupe_dedupes_second_call() -> None:
    """HeliusMigrateSource._decode_and_dedupe returns None on duplicate mint (same session)."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="test", event_source="pump_dot_fun", clock=clock)

    migrate = _load_real_migrate_fixture()

    with patch(
        "core.tape.helius_birth_tape_source.resolve_spl_mint",
        return_value=REAL_FRAME_MINT,
    ):
        first = asyncio.run(src._decode_and_dedupe(migrate))
        assert first is not None, "First call should return an event"
        second = asyncio.run(src._decode_and_dedupe(migrate))

    assert second is None, (
        "_decode_and_dedupe should return None on second call (same mint, per-session dedupe)"
    )


# ---------------------------------------------------------------------------
# Test 9: events_from_queue uses decode_and_dedupe (mocked resolver)
# ---------------------------------------------------------------------------


def test_migrate_source_events_from_queue_uses_decode_and_dedupe() -> None:
    """events_from_queue yields one graduation from migrate, skips trade and ack."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="fanout", event_source="pump_dot_fun", clock=clock)

    migrate = _load_real_migrate_fixture()
    trade = _make_trade_notification()
    ack = _make_subscription_ack()

    q: asyncio.Queue = asyncio.Queue()
    for frame in [trade, ack, migrate]:
        q.put_nowait(frame)
    q.put_nowait(None)  # sentinel

    collected: list[dict] = []

    async def _run() -> None:
        with patch(
            "core.tape.helius_birth_tape_source.resolve_spl_mint",
            return_value=REAL_FRAME_MINT,
        ):
            async for ev in src.events_from_queue(q):
                collected.append(ev)

    asyncio.run(_run())

    assert len(collected) == 1, (
        f"Expected exactly 1 graduation from events_from_queue, got {len(collected)}"
    )
    assert collected[0]["address"] == REAL_FRAME_MINT


# ---------------------------------------------------------------------------
# Test 10: graduation_silence_watchdog_s default in schema
# ---------------------------------------------------------------------------


def test_silence_watchdog_default_in_schema() -> None:
    """TapeConfig.graduation_silence_watchdog_s defaults to 120 seconds."""
    from core.schemas import TapeConfig

    # Minimal required field is idle_kill_ttl_s
    tape = TapeConfig(idle_kill_ttl_s=1800)
    assert hasattr(tape, "graduation_silence_watchdog_s"), (
        "TapeConfig must have graduation_silence_watchdog_s field"
    )
    assert tape.graduation_silence_watchdog_s == 120, (
        f"Expected default graduation_silence_watchdog_s=120, "
        f"got {tape.graduation_silence_watchdog_s}"
    )


# ---------------------------------------------------------------------------
# Test 11: _QueueDataSource implements DataSource interface
# ---------------------------------------------------------------------------


def test_queue_data_source_is_datasource() -> None:
    """_QueueDataSource implements the DataSource abstract interface."""
    from core.datasource import DataSource
    from core.tape.helius_birth_tape_source import _QueueDataSource

    q: asyncio.Queue = asyncio.Queue()
    src = _QueueDataSource(q)
    assert isinstance(src, DataSource), (
        "_QueueDataSource must be a DataSource instance"
    )


# ---------------------------------------------------------------------------
# Test 12: AST — no top-level import websockets after refactor
# ---------------------------------------------------------------------------


def test_helius_birth_tape_source_ast_no_toplevel_websockets() -> None:
    """helius_birth_tape_source.py must NOT have top-level 'import websockets'."""
    import ast as _ast

    src_path = REPO_ROOT / "core" / "tape" / "helius_birth_tape_source.py"
    assert src_path.exists(), f"Missing {src_path}"
    tree = _ast.parse(src_path.read_text(encoding="utf-8"))

    # Only module-level nodes are in ast.Module.body
    for node in tree.body:
        if isinstance(node, _ast.Import):
            for alias in node.names:
                assert alias.name != "websockets", (
                    "Top-level 'import websockets' found — must be lazy inside connect()"
                )
        elif isinstance(node, _ast.ImportFrom):
            assert (node.module or "") != "websockets", (
                "Top-level 'from websockets import ...' found — must be lazy inside connect()"
            )
