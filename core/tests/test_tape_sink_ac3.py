# ---
# module: core.tests.test_tape_sink_ac3
# sprint: sprint-14
# story: US-79 AC-3 (durable per-mint tape store)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
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
