# ---
# module: core.tests.test_tape_sink_ac3
# sprint: sprint-14
# story: US-79 AC-3 (durable per-mint tape store); fix/us78-export-from-lake
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, core.firehose.tape_sink, core.tape.lake_reader, core.data_contract
# ---
"""US-79 AC-3 — durable tape sink writes a lake the LakeReader + swaps export read.

The firehose's in-memory tape is now mirrored to lake/tapes; these tests prove the
sink writes the canonical jsonl.gz lake (round-trips via LakeReader), partitions by
block_time UTC date, tags phase, is resilient (never raises), and that the US-78
swaps export reads what the sink wrote.
"""
from __future__ import annotations

from core.firehose.tape_sink import LakeTapeSink
from core.tape.lake_reader import LakeReader


def _swap(**kw):
    s = {
        "mint": "MINTaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "block_time": 1_750_000_000,
        "slot": 10,
        "signature": "sigA",
        "price": 0.5,
        "side": "buy",
        "vol": 2.0,
        "vol_sol": 2.0,
        "vol_usd": 300.0,
        "owner": "WALLET_A",
    }
    s.update(kw)
    return s


def test_flush_writes_lake_partition_readable_by_lakereader(tmp_path):
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    sink.record(_swap(signature="s1"), "pre")
    sink.record(_swap(signature="s2"), "post")
    assert sink.flush() == 2

    part = tmp_path / "dt=2025-06-15" / "part-0.jsonl.gz"  # 1_750_000_000 UTC
    assert part.exists()

    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 2
    sigs = {r["signature"] for r in rows}
    assert sigs == {"s1", "s2"}
    phases = {r["phase"] for r in rows}
    assert phases == {"pre", "post"}


def test_inline_flush_when_batch_full(tmp_path):
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=3)
    for i in range(3):
        sink.record(_swap(signature=f"s{i}"), "pre")
    # The 3rd record triggers an inline flush -> already on disk, buffer drained.
    assert sink.written == 3
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 3


def test_partition_by_block_time_utc(tmp_path):
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    sink.record(_swap(signature="d1", block_time=1_750_000_000), "pre")  # 2025-06-15
    sink.record(_swap(signature="d2", block_time=1_750_200_000), "pre")  # 2025-06-17
    sink.flush()
    parts = sorted(p.name for p in tmp_path.glob("dt=*"))
    assert parts == ["dt=2025-06-15", "dt=2025-06-17"]


def test_record_never_raises_on_bad_input(tmp_path):
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1)
    # A non-dict-ish object would break dict(swap); the sink must swallow it.
    sink.record(object(), "pre")  # must not raise
    # A clean record still works afterward.
    sink.record(_swap(signature="ok"), "pre")
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert any(r.get("signature") == "ok" for r in rows)


def test_flush_failure_rebuffers(tmp_path, monkeypatch):
    sink = LakeTapeSink(base_dir=tmp_path / "x", flush_every=1000)
    sink.record(_swap(signature="keep"), "pre")

    import gzip as _gz
    monkeypatch.setattr(_gz, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert sink.flush() == 0          # failed
    monkeypatch.undo()
    # The row was re-buffered, so a subsequent flush persists it (no data loss).
    assert sink.flush() == 1
    rows = list(LakeReader(base_dir=tmp_path / "x").iter_rows())
    assert any(r.get("signature") == "keep" for r in rows)


def test_swaps_export_reads_sink_output(tmp_path):
    """End-to-end: sink-written lake -> US-78 swaps surface populates."""
    import pyarrow.parquet as pq

    from core.data_contract import build_swaps_dataset, utc_date_str

    sink = LakeTapeSink(base_dir=tmp_path / "lake", flush_every=1000)
    sink.record(_swap(signature="s1", owner="W1"), "pre")
    sink.record(_swap(signature="s2", owner="W2"), "post")
    sink.flush()

    rows = list(LakeReader(base_dir=tmp_path / "lake").iter_rows())
    res = build_swaps_dataset(rows, out_dir=tmp_path / "export", dataset_id="t")
    assert res["row_count"] == 2

    part = tmp_path / "export" / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet"
    d = pq.ParquetFile(str(part)).read().to_pydict()
    # phase -> source mapping survives the round-trip
    assert set(d["source"]) == {"pump_dot_fun", "pump_amm"}
    assert set(d["signer"]) == {"W1", "W2"}


# ---------------------------------------------------------------------------
# Epoch-zero / bad-timestamp guard (fix/us78-export-from-lake)
# ---------------------------------------------------------------------------


def test_epoch_zero_block_time_is_dropped(tmp_path):
    """Rows with block_time=0 must NOT land in a dt=1970 partition."""
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    sink.record(_swap(signature="bad", block_time=0), "pre")
    flushed = sink.flush()
    # The bad-timestamp row is dropped, not written.
    assert flushed == 0
    assert sink.quarantined == 1
    # No partitions at all should exist under tmp_path.
    partitions = list(tmp_path.glob("dt=*"))
    assert partitions == [], f"unexpected partitions: {partitions}"


def test_none_block_time_is_dropped(tmp_path):
    """Rows with block_time=None must NOT land in any date partition."""
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    bad_swap = _swap(signature="bad_none")
    bad_swap["block_time"] = None
    sink.record(bad_swap, "pre")
    flushed = sink.flush()
    assert flushed == 0
    assert sink.quarantined == 1
    assert list(tmp_path.glob("dt=*")) == []


def test_pre_2020_block_time_is_dropped(tmp_path):
    """Rows with block_time that maps to a pre-2020 date (e.g. 1977) are dropped."""
    # epoch 221_000_000 -> 1977-01-xx (a slot number mistaken for a unix timestamp)
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    sink.record(_swap(signature="bad_1977", block_time=221_000_000), "pre")
    flushed = sink.flush()
    assert flushed == 0
    assert sink.quarantined == 1
    # Confirm no 1977 partition was written
    partitions = [p.name for p in tmp_path.glob("dt=*")]
    assert not any("1977" in p for p in partitions), f"1977 partition found: {partitions}"
    assert not any("1970" in p for p in partitions), f"1970 partition found: {partitions}"


def test_good_row_still_written_alongside_bad(tmp_path):
    """Bad-timestamp rows are dropped; good rows in the same batch are still written."""
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    sink.record(_swap(signature="bad", block_time=0), "pre")
    sink.record(_swap(signature="good", block_time=1_750_000_000), "post")
    flushed = sink.flush()
    assert flushed == 1
    assert sink.quarantined == 1
    # Good row lands in the correct 2025 partition.
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 1
    assert rows[0]["signature"] == "good"
    partitions = [p.name for p in tmp_path.glob("dt=*")]
    assert partitions == ["dt=2025-06-15"]


def test_swaps_export_non_empty_when_lake_has_data(tmp_path):
    """US-78 swaps export returns non-empty results when lake/firehose has data.

    This is the primary regression test for the 'swaps export is empty' issue:
    the export must read from the lake (not the DB) and return rows + a parquet
    file shaped per the contract columns.
    """
    import pyarrow.parquet as pq

    from core.data_contract import SWAPS_COLUMNS, build_swaps_dataset, utc_date_str

    # Write three rows to a fixture lake (simulating lake/firehose on the VPS).
    lake_dir = tmp_path / "lake_firehose"
    sink = LakeTapeSink(base_dir=lake_dir, flush_every=1000)
    for i in range(3):
        sink.record(
            _swap(signature=f"sig{i}", owner=f"W{i}", block_time=1_750_000_000 + i * 60),
            "pre",
        )
    sink.flush()
    assert sink.written == 3

    # Run the export builder (the same code path as export_data_contract task).
    rows = list(LakeReader(base_dir=lake_dir).iter_rows())
    assert len(rows) == 3, "lake must have 3 rows before export"

    out = tmp_path / "export"
    res = build_swaps_dataset(rows, out_dir=out, dataset_id="fixture_test")
    assert res["row_count"] == 3, "export must be non-empty when lake has data"
    assert res["skipped"] == 0

    # Parquet file exists and has the exact contract columns.
    part = out / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet"
    assert part.exists(), f"parquet part not found at {part}"
    table = pq.ParquetFile(str(part)).read()
    assert table.schema.names == SWAPS_COLUMNS, (
        f"schema mismatch: got {table.schema.names}, expected {SWAPS_COLUMNS}"
    )
    d = table.to_pydict()
    assert len(d["mint"]) == 3
