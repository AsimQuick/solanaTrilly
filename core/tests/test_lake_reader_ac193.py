# ---
# module: core.tests.test_lake_reader_ac193
# sprint: sprint-5
# story: US-19 AC-19.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.lake_reader, gzip, io, json, pathlib, pytest
# ---
"""AC-19.3 — truncated-tail-tolerant lake reader (LakeReader) tests.

Tests:
  1. test_reads_complete_rows_from_valid_part
       A valid gzip jsonl with 3 rows returns all 3 dicts.

  2. test_truncated_tail_returns_complete_rows_no_exception
       **THE KEY TEST**: write a gzip file with 3 complete JSON lines + a 4th
       line deliberately truncated mid-record (``{"rel": 60.0, "sig`` — no
       closing brace, no newline).  Assert: no exception raised, exactly 3
       complete dicts are returned, the truncated partial line is NOT in results.

  3. test_truncated_gzip_eof_tolerates_silently
       Simulate a truly truncated gzip (valid header + some content then abrupt
       end).  EOFError from gzip is caught; no exception propagates; whatever
       complete rows decoded before truncation are returned.

  4. test_empty_part_returns_no_rows
       An empty gzip file yields zero rows.

  5. test_reads_multiple_part_files
       Two part files in the same partition; rows from both are yielded.

  6. test_nonexistent_date_returns_no_rows
       A date_str with no matching partition yields nothing and raises no error.
"""
import gzip
import io
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_valid_part(part_path: Path, rows: list[dict]) -> None:
    """Write *rows* as newline-delimited JSON inside a valid gzip file."""
    content = "\n".join(json.dumps(r) for r in rows) + "\n"
    with gzip.open(part_path, "wb") as gz:
        gz.write(content.encode("utf-8"))


def _make_partition_dir(base: Path, date_str: str, part_name: str = "part-0.jsonl.gz") -> Path:
    """Create the dt= partition directory under *base* and return the part-file path."""
    partition = base / f"dt={date_str}"
    partition.mkdir(parents=True, exist_ok=True)
    return partition / part_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reads_complete_rows_from_valid_part(tmp_path: Path) -> None:
    """A valid gzip jsonl with 3 complete rows must return all 3 dicts."""
    from core.tape.lake_reader import LakeReader

    date_str = "2024-01-15"
    part_path = _make_partition_dir(tmp_path, date_str)
    rows = [{"rel": 10.0, "a": 1}, {"rel": 20.0, "a": 2}, {"rel": 30.0, "a": 3}]
    _write_valid_part(part_path, rows)

    reader = LakeReader(base_dir=tmp_path)
    result = list(reader.iter_rows(date_str=date_str))

    assert len(result) == 3, f"Expected 3 rows, got {len(result)}"
    assert result == rows, f"Rows mismatch: {result}"


def test_truncated_tail_returns_complete_rows_no_exception(tmp_path: Path) -> None:
    """**KEY TEST**: partial JSON on the final line is skipped; no exception raised.

    Write a gzip file with 3 complete JSON lines + a 4th line that is
    deliberately truncated mid-record (``{\"rel\": 60.0, \"sig``).
    The reader must:
    - Not raise any exception.
    - Return exactly the 3 complete dicts.
    - NOT include any fragment of the truncated 4th line.
    """
    from core.tape.lake_reader import LakeReader

    date_str = "2024-02-20"
    part_path = _make_partition_dir(tmp_path, date_str)

    # Write 3 complete lines + one truncated partial (valid gzip, malformed last line)
    content = b'{"rel": 10.0, "a": 1}\n{"rel": 20.0, "a": 2}\n{"rel": 30.0, "a": 3}\n{"rel": 60.0, "sig'
    with gzip.open(part_path, "wb") as gz:
        gz.write(content)

    reader = LakeReader(base_dir=tmp_path)

    # Must not raise
    result = list(reader.iter_rows(date_str=date_str))

    assert len(result) == 3, (
        f"Expected exactly 3 complete rows, got {len(result)}. Results: {result}"
    )
    assert result[0] == {"rel": 10.0, "a": 1}
    assert result[1] == {"rel": 20.0, "a": 2}
    assert result[2] == {"rel": 30.0, "a": 3}
    # The partial 4th line must NOT appear in results
    for row in result:
        assert "sig" not in str(row) or row.get("rel") != 60.0, (
            f"Partial truncated row must not appear in results: {row}"
        )


def test_truncated_gzip_eof_tolerates_silently(tmp_path: Path) -> None:
    """A truly truncated gzip (abrupt mid-stream end) must not raise.

    Build a valid small gzip of two JSON lines, then write only the first
    half of the raw bytes to disk.  The reader encounters an EOFError /
    BadGzipFile during readline, must catch it, and must not propagate any
    exception to the caller.
    """
    from core.tape.lake_reader import LakeReader

    date_str = "2024-03-10"
    part_path = _make_partition_dir(tmp_path, date_str)

    # Build a valid gzip buffer in-memory, then truncate it
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(b'{"rel": 1.0}\n{"rel": 2.0}\n')
    full_bytes = buf.getvalue()

    # Write only the first half — guaranteed to be a truncated gzip
    with open(part_path, "wb") as f:
        f.write(full_bytes[: len(full_bytes) // 2])

    reader = LakeReader(base_dir=tmp_path)

    # Must NOT raise — even on truly truncated gzip bytes
    try:
        result = list(reader.iter_rows(date_str=date_str))
    except Exception as exc:
        pytest.fail(f"LakeReader raised an unexpected exception on truncated gzip: {exc!r}")

    # Result may be 0 or 1 rows depending on where the truncation falls —
    # the contract is just that no exception is raised.
    assert isinstance(result, list), "iter_rows must return an iterable of dicts"
    for row in result:
        assert isinstance(row, dict), f"Each yielded item must be a dict, got: {type(row)}"


def test_empty_part_returns_no_rows(tmp_path: Path) -> None:
    """An empty gzip file (no content) yields zero rows."""
    from core.tape.lake_reader import LakeReader

    date_str = "2024-04-01"
    part_path = _make_partition_dir(tmp_path, date_str)

    # Write a valid empty gzip
    with gzip.open(part_path, "wb") as gz:
        gz.write(b"")

    reader = LakeReader(base_dir=tmp_path)
    result = list(reader.iter_rows(date_str=date_str))

    assert result == [], f"Expected empty list from empty part file, got: {result}"


def test_reads_multiple_part_files(tmp_path: Path) -> None:
    """Two part files in the same partition; all rows from both are yielded."""
    from core.tape.lake_reader import LakeReader

    date_str = "2024-05-15"
    part0 = _make_partition_dir(tmp_path, date_str, "part-0.jsonl.gz")
    part1 = _make_partition_dir(tmp_path, date_str, "part-1.jsonl.gz")

    rows_0 = [{"rel": 1.0, "part": 0}, {"rel": 2.0, "part": 0}]
    rows_1 = [{"rel": 3.0, "part": 1}, {"rel": 4.0, "part": 1}]
    _write_valid_part(part0, rows_0)
    _write_valid_part(part1, rows_1)

    reader = LakeReader(base_dir=tmp_path)
    result = list(reader.iter_rows(date_str=date_str))

    assert len(result) == 4, f"Expected 4 rows from 2 part files, got {len(result)}"
    # part-0 is sorted before part-1, so rows_0 comes first
    assert result[:2] == rows_0, f"First 2 rows should come from part-0: {result[:2]}"
    assert result[2:] == rows_1, f"Last 2 rows should come from part-1: {result[2:]}"


def test_nonexistent_date_returns_no_rows(tmp_path: Path) -> None:
    """A date_str with no matching partition yields nothing and raises no error."""
    from core.tape.lake_reader import LakeReader

    reader = LakeReader(base_dir=tmp_path)
    # No partitions created at all
    result = list(reader.iter_rows(date_str="2099-12-31"))

    assert result == [], f"Expected empty list for nonexistent date, got: {result}"


def test_iter_rows_no_date_reads_all_partitions(tmp_path: Path) -> None:
    """When date_str=None, iter_rows reads all partitions in ascending date order."""
    from core.tape.lake_reader import LakeReader

    dates = ["2024-01-01", "2024-01-03", "2024-01-02"]  # intentionally unsorted
    all_expected: list[dict] = []
    for date_str in sorted(dates):  # sorted order is what reader must produce
        part_path = _make_partition_dir(tmp_path, date_str)
        rows = [{"rel": 1.0, "date": date_str}]
        _write_valid_part(part_path, rows)
        all_expected.extend(rows)

    reader = LakeReader(base_dir=tmp_path)
    result = list(reader.iter_rows())  # no date_str → all partitions

    assert len(result) == len(all_expected), (
        f"Expected {len(all_expected)} total rows across all partitions, got {len(result)}"
    )
    assert result == all_expected, f"Rows not in ascending date order: {result}"
