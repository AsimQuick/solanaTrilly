# ---
# module: core.tests.test_lake_writer_ac191
# sprint: sprint-5
# story: US-19 AC-19.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.lake_writer, core.tape.recorder, core.normalized_swap,
#               core.clock, core.replay_source, gzip, pathlib, asyncio, pytest
# ---
"""AC-19.1 — append-only, daily-partitioned jsonl.gz lake writer tests.

Tests:
  1. test_write_creates_correct_partition_path
       block_time=1_700_000_060 → dt=2023-11-14/part-0.jsonl.gz

  2. test_write_produces_gzip_compressed_file
       Written file opens cleanly with gzip.open (valid gzip magic).

  3. test_write_reads_back_byte_identical_rows
       3 NormalizedSwaps round-trip through the lake — each decoded line equals
       swap.to_json().

  4. test_write_empty_swaps_returns_none
       write([]) returns None and creates no files under base_dir.

  5. test_write_appends_to_existing_part
       Two sequential write() calls accumulate; total lines == total swaps.

  6. test_recorder_with_lake_writer_writes_to_lake
       Full TapeRecorder + ReplaySource + VirtualClock + LakeWriter integration
       — asyncio.run(recorder.run()) → part file exists with correct content.
"""
import asyncio
import gzip
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.normalized_swap import NormalizedSwap

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)
_MINT = "PUMP_MINT_1111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"

# block_time chosen so that datetime.fromtimestamp(1_700_000_060, UTC)
# → 2023-11-14 (verified: 1_700_000_000 is 2023-11-14T22:13:20Z).
_BLOCK_TIME = 1_700_000_060
_EXPECTED_DATE = "2023-11-14"


def _make_swap(n: int = 1) -> NormalizedSwap:
    """Return a deterministic NormalizedSwap with index *n*."""
    return NormalizedSwap(
        rel=float(_BLOCK_TIME - _GRADUATED_BLOCK_TIME),
        block_time=_BLOCK_TIME,
        slot=200 + n,
        signature=f"SIG_{n:04d}" + "1" * 50,
        price=0.00025,
        side="buy",
        vol_sol=2.5,
        vol_usd=375.0,
        sol_usd=150.0,
        owner="SIGNER_WALLET_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        base_reserve=5_000_000,
        quote_reserve=1_250_000,
        quote_mint=_QUOTE_MINT,
        source="birdeye_live",
        phase="pre",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_write_creates_correct_partition_path(tmp_path: Path) -> None:
    """block_time=1_700_000_060 must create dt=2023-11-14/part-0.jsonl.gz."""
    from core.tape.lake_writer import LakeWriter

    writer = LakeWriter(base_dir=tmp_path)
    swaps = [_make_swap(1)]

    result = writer.write(swaps)

    expected = tmp_path / f"dt={_EXPECTED_DATE}" / "part-0.jsonl.gz"
    assert result == expected, f"Expected path {expected}, got {result}"
    assert expected.exists(), f"Part file was not created at {expected}"


def test_write_produces_gzip_compressed_file(tmp_path: Path) -> None:
    """The written file must be valid gzip — opens cleanly with gzip.open."""
    from core.tape.lake_writer import LakeWriter

    writer = LakeWriter(base_dir=tmp_path)
    part_path = writer.write([_make_swap(1)])

    assert part_path is not None
    # Opening and reading must not raise — this verifies gzip magic bytes.
    with gzip.open(part_path, "rb") as gz:
        content = gz.read()

    assert len(content) > 0, "gzip file content must not be empty"


def test_write_reads_back_byte_identical_rows(tmp_path: Path) -> None:
    """3 NormalizedSwaps round-trip through the lake with byte-identical JSON lines."""
    from core.tape.lake_writer import LakeWriter

    writer = LakeWriter(base_dir=tmp_path)
    swaps = [_make_swap(1), _make_swap(2), _make_swap(3)]
    part_path = writer.write(swaps)

    assert part_path is not None

    with gzip.open(part_path, "rb") as gz:
        raw_bytes = gz.read()

    lines = raw_bytes.decode("utf-8").splitlines()
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"

    for i, (line, swap) in enumerate(zip(lines, swaps)):
        expected = swap.to_json()
        assert line == expected, (
            f"Row {i} mismatch.\n  Expected: {expected}\n  Got:      {line}"
        )


def test_write_empty_swaps_returns_none(tmp_path: Path) -> None:
    """write([]) must return None and create no files under base_dir."""
    from core.tape.lake_writer import LakeWriter

    writer = LakeWriter(base_dir=tmp_path)
    result = writer.write([])

    assert result is None, f"Expected None for empty swaps, got {result}"
    # No files should have been created at all
    all_files = list(tmp_path.rglob("*"))
    assert all_files == [], f"Expected no files created, found: {all_files}"


def test_write_appends_to_existing_part(tmp_path: Path) -> None:
    """Two write() calls to the same partition accumulate; total lines == total swaps."""
    from core.tape.lake_writer import LakeWriter

    writer = LakeWriter(base_dir=tmp_path)
    batch_1 = [_make_swap(1), _make_swap(2)]
    batch_2 = [_make_swap(3), _make_swap(4), _make_swap(5)]

    path_1 = writer.write(batch_1)
    path_2 = writer.write(batch_2)

    # Both calls must return the same part file path
    assert path_1 == path_2, f"Both writes should go to the same file: {path_1} vs {path_2}"

    with gzip.open(path_1, "rb") as gz:
        raw_bytes = gz.read()

    lines = raw_bytes.decode("utf-8").splitlines()
    all_swaps = batch_1 + batch_2
    assert len(lines) == len(all_swaps), (
        f"Expected {len(all_swaps)} lines after two writes, got {len(lines)}"
    )

    for i, (line, swap) in enumerate(zip(lines, all_swaps)):
        expected = swap.to_json()
        assert line == expected, (
            f"Row {i} mismatch after append.\n  Expected: {expected}\n  Got:      {line}"
        )


def test_recorder_with_lake_writer_writes_to_lake(tmp_path: Path) -> None:
    """TapeRecorder + ReplaySource + VirtualClock + LakeWriter integration test.

    asyncio.run(recorder.run()) must produce a part file under tmp_path
    with content matching the NormalizedSwaps emitted by the recorder.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.lake_writer import LakeWriter
    from core.tape.recorder import TapeRecorder

    # Build a minimal raw swap event that TapeRecorder can normalize.
    raw_swap: dict = {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _BLOCK_TIME,
        "slot": 201,
        "signature": "SIG_0001" + "1" * 50,
        "side": "buy",
        "price": 0.00025,
        "vol_sol": 2.5,
        "vol_usd": 375.0,
        "sol_usd": 150.0,
        "owner": "SIGNER_WALLET_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "base_reserve": 5_000_000,
        "quote_reserve": 1_250_000,
        "quote_mint": _QUOTE_MINT,
        "failed": False,
    }

    token_store = {_MINT: _TOKEN}
    t0 = datetime(2023, 11, 14, 0, 0, 0, tzinfo=timezone.utc)

    lake_writer = LakeWriter(base_dir=tmp_path)
    source = ReplaySource(event_log=[raw_swap])
    clock = VirtualClock(t0)
    recorder = TapeRecorder(source, clock, token_store=token_store, lake_writer=lake_writer)

    asyncio.run(recorder.run())

    # A part file must exist under the correct partition
    expected_part = tmp_path / f"dt={_EXPECTED_DATE}" / "part-0.jsonl.gz"
    assert expected_part.exists(), (
        f"Expected part file at {expected_part} after recorder.run()"
    )

    # Read back and verify content
    with gzip.open(expected_part, "rb") as gz:
        raw_bytes = gz.read()

    lines = raw_bytes.decode("utf-8").splitlines()
    assert len(lines) == 1, f"Expected 1 line from 1 swap, got {len(lines)}"

    normalized_swaps = recorder.normalized_swaps
    assert len(normalized_swaps) == 1
    assert lines[0] == normalized_swaps[0].to_json(), (
        f"Lake row does not match NormalizedSwap JSON.\n"
        f"  Expected: {normalized_swaps[0].to_json()}\n"
        f"  Got:      {lines[0]}"
    )
