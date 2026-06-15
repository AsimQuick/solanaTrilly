# ---
# module: core.tests.test_replay_offline_gate_ac211
# sprint: sprint-5
# story: US-21 AC-21.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.recorder, core.tape.lake_writer, core.tape.swap_writer,
#               core.replay_source, core.clock, core.normalized_swap, core.models,
#               asyncio, gzip, pathlib, pytest, types
# ---
"""AC-21.1 — P3 OFFLINE GATE (§16): deterministic ReplaySource swap-stream replay.

Replaying a synthetic schema-faithful Birdeye SUBSCRIBE_TXS stream (§3.3)
through ReplaySource + VirtualClock + TapeRecorder yields EXACTLY the expected
NormalizedSwaps / 'swaps' rows, deterministically.

Run twice → the NormalizedSwap rows AND the jsonl.gz decompressed content are
byte-identical (Principle #7 / PRD §16).

The synthetic fixture contains 6 events for one token mint:
  - 5 landed swaps (4 buy, 1 sell) with varied block_time/slot/signature
  - 1 failed swap (must be excluded from normalized output per §6.2 / AC-18.2)

Expected output: 5 NormalizedSwaps in canonical (block_time, slot, signature) order.

Tests:
  1. test_fixture_schema_faithful
       All synthetic fixture events have every §3.3 Birdeye SUBSCRIBE_TXS field.

  2. test_replay_yields_expected_normalized_swaps
       Replay yields exactly 5 NormalizedSwaps; correct rel anchoring, source/phase,
       side distribution, and canonical ordering.

  3. test_replay_failed_swap_excluded
       The failed swap in the fixture is absent from normalized output.

  4. test_replay_deterministic_rows_run_twice
       Run twice (fresh recorder each time); JSON-serialized NormalizedSwaps are
       byte-identical between both runs (core determinism gate).

  5. test_replay_deterministic_lake_run_twice
       Run twice with independent LakeWriters; decompressed jsonl.gz content is
       byte-identical between runs.

  6. test_replay_swaps_rows_correct
       Replay with SwapWriter writes exactly 5 'swaps' rows; spot-check each
       row's rel, side, vol_sol against the NormalizedSwap.

  7. test_replay_swaps_rows_idempotent_run_twice
       Run replay with SwapWriter twice; row count stays 5 (idempotent on
       (mint, signature) per AC-19.2).
"""
import asyncio
import gzip
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Synthetic fixture — schema-faithful Birdeye SUBSCRIBE_TXS stream (PRD §3.3)
# ---------------------------------------------------------------------------

_GRADUATED_BT = 1_750_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BT)

_MINT = "P3GATE_MINT_1111111111111111111111111111111111111111"  # 50 chars
_WSOL = "So11111111111111111111111111111111111111112"

_T0 = datetime(2025, 6, 16, 0, 0, 0, tzinfo=timezone.utc)

REQUIRED_BIRDEYE_FIELDS = frozenset({
    "type",
    "mint",
    "block_time",
    "slot",
    "signature",
    "side",
    "price",
    "vol_sol",
    "vol_usd",
    "sol_usd",
    "owner",
    "base_reserve",
    "quote_reserve",
    "quote_mint",
    "failed",
})


def _make_event(n: int, *, side: str = "buy", failed: bool = False) -> dict[str, Any]:
    """Build a schema-faithful Birdeye SUBSCRIBE_TXS event dict (PRD §3.3)."""
    bt = _GRADUATED_BT + 1000 + n
    sig = f"P3SIG{n:04d}" + "A" * 86
    owner = f"P3OWN{n:04d}" + "B" * 54
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": bt,
        "slot": 30_000_000 + n,
        "signature": sig,
        "side": side,
        "price": 0.0001 + n * 0.00001,
        "vol_sol": 1.0 + n * 0.5,
        "vol_usd": 150.0 + n * 75.0,
        "sol_usd": 150.0,
        "owner": owner,
        "base_reserve": 10_000_000 - n * 100_000,
        "quote_reserve": 5_000_000 + n * 50_000,
        "quote_mint": _WSOL,
        "failed": failed,
    }


_FIXTURE_EVENTS: list[dict[str, Any]] = [
    _make_event(1, side="buy"),
    _make_event(2, side="buy"),
    _make_event(3, side="buy"),
    _make_event(4, side="buy"),
    _make_event(5, side="sell"),
    _make_event(6, side="buy", failed=True),
]

_EXPECTED_SWAP_COUNT = 5
_TOKEN_STORE: dict[str, Any] = {_MINT: _TOKEN}

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_replay(*, lake_writer=None, swap_writer=None) -> list:
    """Drive TapeRecorder with the full fixture; return normalized_swaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = ReplaySource(event_log=_FIXTURE_EVENTS)
        clock = VirtualClock(_T0)
        recorder = TapeRecorder(
            source,
            clock,
            token_store=_TOKEN_STORE,
            swap_source="birdeye_live",
            swap_phase="pre",
            lake_writer=lake_writer,
            swap_writer=swap_writer,
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fixture_schema_faithful() -> None:
    """All synthetic fixture events carry every §3.3 Birdeye SUBSCRIBE_TXS field."""
    for i, event in enumerate(_FIXTURE_EVENTS):
        missing = REQUIRED_BIRDEYE_FIELDS - set(event.keys())
        assert not missing, (
            f"Event[{i}] missing §3.3 required fields: {missing}. "
            "Fixture must be schema-faithful (AC-21.1 / PRD §3.3)."
        )
        assert event["type"] == "SWAP", (
            f"Event[{i}] type={event['type']!r}; §3.3 SUBSCRIBE_TXS events must be type='SWAP'."
        )
        assert isinstance(event["block_time"], int), (
            f"Event[{i}] block_time must be int, got {type(event['block_time']).__name__}."
        )
        assert isinstance(event["slot"], int), (
            f"Event[{i}] slot must be int, got {type(event['slot']).__name__}."
        )
        if not event.get("failed", False):
            assert event["side"] in {"buy", "sell"}, (
                f"Event[{i}] side={event['side']!r} not in {{buy, sell}}."
            )
            assert float(event["price"]) > 0, (
                f"Event[{i}] price={event['price']!r} must be > 0 for landed swaps."
            )


def test_replay_yields_expected_normalized_swaps() -> None:
    """Replay yields exactly 5 NormalizedSwaps with correct field values."""
    swaps = _run_replay()

    assert len(swaps) == _EXPECTED_SWAP_COUNT, (
        f"Expected {_EXPECTED_SWAP_COUNT} NormalizedSwaps (5 landed, 1 failed excluded), "
        f"got {len(swaps)}."
    )

    keys = [(s.block_time, s.slot, s.signature) for s in swaps]
    assert keys == sorted(keys), (
        f"NormalizedSwaps not in canonical (block_time, slot, signature) order: {keys}"
    )

    for i, swap in enumerate(swaps):
        expected_rel = float(swap.block_time - _GRADUATED_BT)
        assert swap.rel == expected_rel, (
            f"Swap[{i}] rel={swap.rel!r} != block_time({swap.block_time}) "
            f"- graduated_bt({_GRADUATED_BT}) = {expected_rel!r}. "
            "rel must be anchored to token.graduated_block_time."
        )

    for i, swap in enumerate(swaps):
        assert swap.source == "birdeye_live", (
            f"Swap[{i}] source={swap.source!r}, expected 'birdeye_live'."
        )
        assert swap.phase == "pre", (
            f"Swap[{i}] phase={swap.phase!r}, expected 'pre'."
        )

    sides = [s.side for s in swaps]
    assert sides.count("buy") == 4, f"Expected 4 buy swaps, got {sides.count('buy')} in {sides}."
    assert sides.count("sell") == 1, f"Expected 1 sell swap, got {sides.count('sell')} in {sides}."


def test_replay_failed_swap_excluded() -> None:
    """The failed swap (event 6) is absent from normalized output."""
    swaps = _run_replay()
    failed_sig = _make_event(6, side="buy", failed=True)["signature"]
    present_sigs = {s.signature for s in swaps}
    assert failed_sig not in present_sigs, (
        f"Failed swap signature {failed_sig!r} appeared in normalized output. "
        "Failed swaps must be dropped (§6.2 / AC-18.2)."
    )


def test_replay_deterministic_rows_run_twice() -> None:
    """Run replay twice; JSON-serialized NormalizedSwaps are byte-identical.

    Core determinism gate: same fixture + same injected clock → same output.
    """
    run_1 = _run_replay()
    run_2 = _run_replay()

    assert len(run_1) == len(run_2), (
        f"Run 1 yielded {len(run_1)} swaps; Run 2 yielded {len(run_2)}. "
        "Replay must be deterministic."
    )

    for i, (s1, s2) in enumerate(zip(run_1, run_2)):
        json_1 = s1.to_json()
        json_2 = s2.to_json()
        assert json_1 == json_2, (
            f"Swap[{i}] NOT byte-identical between runs:\n"
            f"  Run 1: {json_1}\n"
            f"  Run 2: {json_2}\n"
            "Replay must be deterministic (Principle #7 / PRD §16)."
        )


def test_replay_deterministic_lake_run_twice(tmp_path: Path) -> None:
    """Run replay twice with independent LakeWriters; decompressed content is byte-identical.

    Each run writes to its own directory; decompressed JSON lines are compared.
    """
    from core.tape.lake_writer import LakeWriter

    dir_run_1 = tmp_path / "run1"
    dir_run_2 = tmp_path / "run2"
    dir_run_1.mkdir()
    dir_run_2.mkdir()

    _run_replay(lake_writer=LakeWriter(base_dir=dir_run_1))
    _run_replay(lake_writer=LakeWriter(base_dir=dir_run_2))

    parts_1 = list(dir_run_1.rglob("*.jsonl.gz"))
    parts_2 = list(dir_run_2.rglob("*.jsonl.gz"))

    assert len(parts_1) == 1, f"Expected 1 part file from run 1, found: {parts_1}"
    assert len(parts_2) == 1, f"Expected 1 part file from run 2, found: {parts_2}"

    with gzip.open(parts_1[0], "rb") as gz:
        content_1 = gz.read()
    with gzip.open(parts_2[0], "rb") as gz:
        content_2 = gz.read()

    assert content_1 == content_2, (
        "Decompressed jsonl.gz content differs between run 1 and run 2. "
        "jsonl.gz content must be byte-identical across replay runs (PRD §16 / AC-21.1)."
    )

    lines = content_1.decode("utf-8").splitlines()
    assert len(lines) == _EXPECTED_SWAP_COUNT, (
        f"Expected {_EXPECTED_SWAP_COUNT} JSON lines in the lake, got {len(lines)}."
    )


@pytest.mark.django_db(transaction=True)
def test_replay_swaps_rows_correct() -> None:
    """Replay with SwapWriter writes exactly 5 'swaps' rows with correct field values."""
    from core.models import Swap
    from core.tape.swap_writer import SwapWriter

    swaps = _run_replay(swap_writer=SwapWriter())

    db_count = Swap.objects.filter(mint=_MINT).count()
    assert db_count == _EXPECTED_SWAP_COUNT, (
        f"Expected {_EXPECTED_SWAP_COUNT} rows in 'swaps', got {db_count}."
    )

    for ns in swaps:
        row = Swap.objects.get(mint=_MINT, signature=ns.signature)
        assert row.block_time == ns.block_time, (
            f"Swap {ns.signature[:20]!r}: block_time mismatch DB={row.block_time} NS={ns.block_time}"
        )
        assert row.rel == ns.rel, (
            f"Swap {ns.signature[:20]!r}: rel mismatch DB={row.rel} NS={ns.rel}"
        )
        assert row.side == ns.side, (
            f"Swap {ns.signature[:20]!r}: side mismatch DB={row.side!r} NS={ns.side!r}"
        )
        assert row.vol_sol == ns.vol_sol, (
            f"Swap {ns.signature[:20]!r}: vol_sol mismatch DB={row.vol_sol} NS={ns.vol_sol}"
        )


@pytest.mark.django_db(transaction=True)
def test_replay_swaps_rows_idempotent_run_twice() -> None:
    """Run replay with SwapWriter twice; row count stays at 5 (no duplicates).

    Idempotency on (mint, signature) from AC-19.2 ensures re-recording never
    inserts duplicates — the second run upserts the same rows.
    """
    from core.models import Swap
    from core.tape.swap_writer import SwapWriter

    _run_replay(swap_writer=SwapWriter())
    count_1 = Swap.objects.filter(mint=_MINT).count()

    _run_replay(swap_writer=SwapWriter())
    count_2 = Swap.objects.filter(mint=_MINT).count()

    assert count_1 == _EXPECTED_SWAP_COUNT, (
        f"After run 1: expected {_EXPECTED_SWAP_COUNT} rows, got {count_1}."
    )
    assert count_2 == _EXPECTED_SWAP_COUNT, (
        f"After run 2: expected {_EXPECTED_SWAP_COUNT} rows, got {count_2}. "
        "Re-recording must NOT create duplicates (AC-19.2 idempotency)."
    )
