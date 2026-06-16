# ---
# module: core.tests.test_feature_builder_ac312
# sprint: sprint-7
# story: US-31 AC-31.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tasks, core.feature_builder, core.models, core.tape.lake_reader,
#               gzip, hashlib, json, pathlib, pytest
# ---
"""AC-31.2 — Every export carries a deterministic manifest; run-twice byte-identical content hash.

Tests
-----
  test_manifest_fields_all_populated
      Run the Feature Builder over a lake fixture; assert all 7 required manifest
      fields (feature_set_version, feature_set_hash, sources, date_range, mint_cohort,
      row_count, content_hash, label_def) are present and non-None in the written manifest.

  test_manifest_content_hash_matches_csv_sha256
      Compute SHA-256 of the exported CSV independently; assert it equals manifest
      content_hash (verifying the hash covers the actual bytes).

  test_manifest_run_twice_byte_identical_content_hash
      Run the task twice over the same lake fixture + FeatureSet; assert that both
      runs produce the SAME content_hash — proving determinism end-to-end.

  test_manifest_feature_set_fields_match_feature_set_row
      Assert manifest feature_set_version and feature_set_hash equal the values
      on the FeatureSet model row used for the build.

  test_manifest_mint_cohort_matches_input
      Assert manifest mint_cohort equals the cohort passed to the task.

  test_manifest_label_def_matches_input
      Assert manifest label_def equals the label_def passed to the task.

  test_manifest_row_count_matches_csv_data_rows
      Assert manifest row_count equals the number of data rows (non-header) in the CSV.

  test_manifest_date_range_populated_for_cohort_with_rows
      Assert date_range.start and date_range.end are non-None strings for a cohort
      whose mints have lake rows with block_time.

  test_manifest_gate_functions_callable
      Human-readable companion: assert the AC-31.1 pinned gate functions are callable.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# ImportError gate — pins the AC-31.1 task + manifest test functions so that
# renaming/deleting them fails pytest collection (mirroring AC-28.3/AC-30.3).
# ---------------------------------------------------------------------------
from core.tasks import build_features
from core.tests.test_feature_builder_ac311 import (
    test_build_features_over_lake_fixture_produces_csv_with_correct_contents,
    test_build_features_task_in_manifest,
)

MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

_LABEL_DEF = {"outcome": "pump", "horizon_s": 300}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_swap_row(mint: str, rel: float, idx: int, block_time: int = 1700000000) -> dict:
    """Build a minimal NormalizedSwap dict for the lake fixture."""
    return {
        "mint": mint,
        "rel": rel,
        "price": 0.001 + idx * 0.0001,
        "side": "buy" if idx % 2 == 0 else "sell",
        "vol_sol": 1.0 + idx * 0.1,
        "owner": f"owner_{mint[-4:]}_{idx}",
        "block_time": block_time + idx * 10,
        "slot": 200000000 + idx,
        "signature": f"sig_{mint[-4:]}_{idx}",
    }


def _write_lake_fixture(base_dir: Path, rows: list[dict]) -> None:
    """Write rows to a single daily partition part-0.jsonl.gz."""
    partition = base_dir / "dt=2023-11-14"
    partition.mkdir(parents=True, exist_ok=True)
    part_file = partition / "part-0.jsonl.gz"
    with gzip.open(str(part_file), "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _make_feature_set():
    """Create a FeatureSet row in the DB and return it."""
    from core.models import FeatureSet

    cols = ["tape_n_trades", "tape_buy_ratio", "tape_price_range"]
    mv = "solanabilly3:sprint-7"
    h = FeatureSet.compute_hash(cols, mv)
    return FeatureSet.objects.create(
        version="v1",
        math_version=mv,
        columns=cols,
        live_servable=cols,
        hash=h,
        notes="",
    )


def _run_build(fs, lake_dir: Path, output_csv: Path) -> dict:
    """Run the build_features task synchronously and return the outcome."""
    result = build_features.apply(
        args=[fs.pk, [MINT_A, MINT_B], _LABEL_DEF],
        kwargs={
            "lake_base_dir": str(lake_dir),
            "output_path": str(output_csv),
        },
    )
    return result.get()


def _two_mint_rows() -> list[dict]:
    """Return a deterministic two-mint lake fixture."""
    rows = []
    for i, rel in enumerate([10.0, 20.0, 30.0]):
        rows.append(_make_swap_row(MINT_A, rel, i))
    for i, rel in enumerate([10.0, 20.0, 30.0, 40.0]):
        rows.append(_make_swap_row(MINT_B, rel, i + 10))
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_manifest_fields_all_populated(tmp_path):
    """All 7 manifest fields must be present and non-None in the written JSON file."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)

    # The manifest must have been written to disk.
    assert "manifest_path" in outcome, "build result missing manifest_path"
    manifest_path = Path(outcome["manifest_path"])
    assert manifest_path.exists(), f"Manifest file not written: {manifest_path}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_fields = [
        "feature_set_version",
        "feature_set_hash",
        "sources",
        "date_range",
        "mint_cohort",
        "row_count",
        "content_hash",
        "label_def",
    ]
    for field in required_fields:
        assert field in manifest, f"Manifest missing required field: {field!r}"
        assert manifest[field] is not None, f"Manifest field {field!r} is None"

    # sources must be a non-empty list.
    assert isinstance(manifest["sources"], list) and len(manifest["sources"]) > 0

    # date_range must have start + end keys.
    assert "start" in manifest["date_range"], "date_range missing 'start'"
    assert "end" in manifest["date_range"], "date_range missing 'end'"

    # content_hash must be a 64-char hex string (SHA-256).
    assert len(manifest["content_hash"]) == 64, (
        f"content_hash has unexpected length: {len(manifest['content_hash'])}"
    )


@pytest.mark.django_db
def test_manifest_content_hash_matches_csv_sha256(tmp_path):
    """Independently compute SHA-256 of the CSV; assert it equals manifest content_hash."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)

    manifest = outcome["manifest"]
    expected_hash = hashlib.sha256(output_csv.read_bytes()).hexdigest()

    assert manifest["content_hash"] == expected_hash, (
        f"content_hash mismatch:\n  manifest: {manifest['content_hash']}\n"
        f"  computed: {expected_hash}"
    )


@pytest.mark.django_db
def test_manifest_run_twice_byte_identical_content_hash(tmp_path):
    """Run the build twice over the same fixture; assert content_hash is identical."""
    lake_dir = tmp_path / "lake"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    out1 = tmp_path / "run1.csv"
    out2 = tmp_path / "run2.csv"

    outcome1 = _run_build(fs, lake_dir, out1)
    outcome2 = _run_build(fs, lake_dir, out2)

    hash1 = outcome1["manifest"]["content_hash"]
    hash2 = outcome2["manifest"]["content_hash"]

    assert hash1 == hash2, (
        f"content_hash differs between runs:\n  run1: {hash1}\n  run2: {hash2}"
    )


@pytest.mark.django_db
def test_manifest_feature_set_fields_match_feature_set_row(tmp_path):
    """Manifest feature_set_version and feature_set_hash must equal the FeatureSet row."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)
    manifest = outcome["manifest"]

    assert manifest["feature_set_version"] == fs.version, (
        f"feature_set_version mismatch: {manifest['feature_set_version']!r} != {fs.version!r}"
    )
    assert manifest["feature_set_hash"] == fs.hash, (
        f"feature_set_hash mismatch: {manifest['feature_set_hash']!r} != {fs.hash!r}"
    )


@pytest.mark.django_db
def test_manifest_mint_cohort_matches_input(tmp_path):
    """Manifest mint_cohort must equal the cohort passed to the task."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)
    manifest = outcome["manifest"]

    assert manifest["mint_cohort"] == [MINT_A, MINT_B], (
        f"mint_cohort mismatch: {manifest['mint_cohort']}"
    )


@pytest.mark.django_db
def test_manifest_label_def_matches_input(tmp_path):
    """Manifest label_def must equal the label_def passed to the task."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)
    manifest = outcome["manifest"]

    assert manifest["label_def"] == _LABEL_DEF, (
        f"label_def mismatch: {manifest['label_def']}"
    )


@pytest.mark.django_db
def test_manifest_row_count_matches_csv_data_rows(tmp_path):
    """Manifest row_count must equal the number of data rows in the CSV (excl. header)."""
    import csv

    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)
    manifest = outcome["manifest"]

    with open(str(output_csv), newline="", encoding="utf-8") as f:
        data_rows = list(csv.DictReader(f))

    assert manifest["row_count"] == len(data_rows), (
        f"row_count {manifest['row_count']} != actual CSV data rows {len(data_rows)}"
    )


@pytest.mark.django_db
def test_manifest_date_range_populated_for_cohort_with_rows(tmp_path):
    """date_range.start and .end must be non-None date strings for a cohort with lake rows."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"
    _write_lake_fixture(lake_dir, _two_mint_rows())
    fs = _make_feature_set()

    outcome = _run_build(fs, lake_dir, output_csv)
    dr = outcome["manifest"]["date_range"]

    assert dr["start"] is not None, "date_range.start is None despite cohort having rows"
    assert dr["end"] is not None, "date_range.end is None despite cohort having rows"
    # Simple format check: YYYY-MM-DD (10 chars)
    assert len(dr["start"]) == 10, f"date_range.start unexpected format: {dr['start']!r}"
    assert len(dr["end"]) == 10, f"date_range.end unexpected format: {dr['end']!r}"


def test_manifest_gate_functions_callable():
    """Pinned AC-31.1 gate functions are callable (companion to module-level ImportError trap)."""
    assert callable(test_build_features_task_in_manifest)
    assert callable(test_build_features_over_lake_fixture_produces_csv_with_correct_contents)
