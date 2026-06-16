# ---
# module: core.tests.test_feature_builder_ac311
# sprint: sprint-7
# story: US-31 AC-31.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tasks, core.feature_builder, core.models, core.tape.lake_reader,
#               gzip, json, csv, pathlib, pytest, yaml
# ---
"""AC-31.1 — Feature Builder Celery task: lake fixture → CSV export.

Tests
-----
  test_build_features_task_in_manifest
      core.tasks.build_features appears in core/task_manifest.json (H2 gate).

  test_build_features_task_registered_in_celery
      core.tasks.build_features is present in the Celery registry after import.

  test_celery_worker_defined_in_dev_compose
      celery-worker service is defined in docker-compose.yml (AC-31.1 container constraint).

  test_celery_worker_defined_in_staging_compose
      celery-worker service is defined in docker-compose.staging.yml (AC-31.1 container constraint).

  test_build_features_over_lake_fixture_produces_csv_with_correct_contents
      Runs the task synchronously (task.apply()) over a two-mint lake fixture and
      asserts: row_count==2, CSV has correct mints, tape_n_trades matches swap counts.

  test_build_features_skips_mint_with_no_lake_rows
      A mint with no lake rows is absent from the CSV output (None -> no row).

  test_build_features_csv_columns_deterministic
      Two calls over the same fixture produce CSVs with identical column headers (determinism).
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest
import yaml

from core.tasks import build_features

MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
MINT_C = "MintCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


def _make_swap_row(mint: str, rel: float, idx: int) -> dict:
    """Build a minimal NormalizedSwap dict for the lake fixture."""
    return {
        "mint": mint,
        "rel": rel,
        "price": 0.001 + idx * 0.0001,
        "side": "buy" if idx % 2 == 0 else "sell",
        "vol_sol": 1.0 + idx * 0.1,
        "owner": f"owner_{mint[-4:]}_{idx}",
        "block_time": 1700000000 + idx * 10,
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


def _make_feature_set(extra_notes=""):
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
        notes=extra_notes,
    )


# ---------------------------------------------------------------------------
# H2 manifest gate
# ---------------------------------------------------------------------------


def test_build_features_task_in_manifest():
    """core.tasks.build_features must appear in core/task_manifest.json (H2 gate)."""
    manifest_path = Path(__file__).resolve().parents[2] / "core" / "task_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "core.tasks.build_features" in manifest["tasks"], (
        "core.tasks.build_features is missing from task_manifest.json — update the manifest"
    )


# ---------------------------------------------------------------------------
# Celery registry
# ---------------------------------------------------------------------------


def test_build_features_task_registered_in_celery():
    """core.tasks.build_features must be present in the Celery app registry."""
    from config.celery import app as celery_app

    # Force autodiscovery
    celery_app.autodiscover_tasks(["core"])
    assert "core.tasks.build_features" in celery_app.tasks, (
        "core.tasks.build_features not found in celery_app.tasks — check @shared_task name="
    )


# ---------------------------------------------------------------------------
# Compose container constraints (AC-31.1)
# ---------------------------------------------------------------------------


def test_celery_worker_defined_in_dev_compose():
    """celery-worker must be defined in docker-compose.yml (never web/gunicorn)."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert "celery-worker" in data.get("services", {}), (
        "celery-worker service is missing from docker-compose.yml"
    )


def test_celery_worker_defined_in_staging_compose():
    """celery-worker must be defined in docker-compose.staging.yml (AC-31.1 DoD)."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.staging.yml"
    content = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert "celery-worker" in data.get("services", {}), (
        "celery-worker service is missing from docker-compose.staging.yml"
    )


# ---------------------------------------------------------------------------
# Core functional test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_features_over_lake_fixture_produces_csv_with_correct_contents(tmp_path):
    """Task runs over a two-mint fixture and writes correct CSV contents."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"

    # MINT_A: 3 swaps at rel 10, 20, 30
    # MINT_B: 4 swaps at rel 10, 20, 30, 40
    rows = []
    for i, rel in enumerate([10.0, 20.0, 30.0]):
        rows.append(_make_swap_row(MINT_A, rel, i))
    for i, rel in enumerate([10.0, 20.0, 30.0, 40.0]):
        rows.append(_make_swap_row(MINT_B, rel, i + 10))

    _write_lake_fixture(lake_dir, rows)

    fs = _make_feature_set()

    result = build_features.apply(
        args=[fs.pk, [MINT_A, MINT_B], {"label": "pump"}],
        kwargs={
            "lake_base_dir": str(lake_dir),
            "output_path": str(output_csv),
        },
    )

    outcome = result.get()
    assert outcome["row_count"] == 2
    assert outcome["path"] == str(output_csv)

    with open(str(output_csv), newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)

    assert len(csv_rows) == 2

    by_mint = {r["mint"]: r for r in csv_rows}
    assert MINT_A in by_mint, f"MINT_A missing from CSV; got mints: {list(by_mint)}"
    assert MINT_B in by_mint, f"MINT_B missing from CSV; got mints: {list(by_mint)}"

    # tape_n_trades counts all swaps within the window (default 120s covers all rel values)
    assert int(float(by_mint[MINT_A]["tape_n_trades"])) == 3, (
        f"Expected tape_n_trades=3 for MINT_A, got {by_mint[MINT_A]['tape_n_trades']}"
    )
    assert int(float(by_mint[MINT_B]["tape_n_trades"])) == 4, (
        f"Expected tape_n_trades=4 for MINT_B, got {by_mint[MINT_B]['tape_n_trades']}"
    )


# ---------------------------------------------------------------------------
# Edge case: mint with no lake rows produces no CSV row
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_features_skips_mint_with_no_lake_rows(tmp_path):
    """A mint absent from the lake produces no CSV data row."""
    lake_dir = tmp_path / "lake"
    output_csv = tmp_path / "output.csv"

    # Only write rows for MINT_A; MINT_C has no rows in the lake
    rows = [_make_swap_row(MINT_A, rel, i) for i, rel in enumerate([10.0, 20.0, 30.0])]
    _write_lake_fixture(lake_dir, rows)

    fs = _make_feature_set()

    result = build_features.apply(
        args=[fs.pk, [MINT_A, MINT_C], {"label": "pump"}],
        kwargs={
            "lake_base_dir": str(lake_dir),
            "output_path": str(output_csv),
        },
    )

    outcome = result.get()
    # Only MINT_A produces features; MINT_C has no rows so it is skipped
    assert outcome["row_count"] == 1

    with open(str(output_csv), newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)

    mints_in_csv = [r["mint"] for r in csv_rows]
    assert MINT_A in mints_in_csv
    assert MINT_C not in mints_in_csv, "MINT_C has no lake rows but appeared in CSV"


# ---------------------------------------------------------------------------
# Determinism: two calls produce identical column headers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_features_csv_columns_deterministic(tmp_path):
    """Two calls over the same fixture produce CSVs with identical column headers."""
    lake_dir = tmp_path / "lake"
    rows = [_make_swap_row(MINT_A, rel, i) for i, rel in enumerate([10.0, 20.0, 30.0])]
    _write_lake_fixture(lake_dir, rows)

    fs = _make_feature_set()

    output1 = tmp_path / "out1.csv"
    output2 = tmp_path / "out2.csv"

    build_features.apply(
        args=[fs.pk, [MINT_A], {"label": "pump"}],
        kwargs={"lake_base_dir": str(lake_dir), "output_path": str(output1)},
    ).get()
    build_features.apply(
        args=[fs.pk, [MINT_A], {"label": "pump"}],
        kwargs={"lake_base_dir": str(lake_dir), "output_path": str(output2)},
    ).get()

    with open(str(output1), newline="", encoding="utf-8") as f1:
        headers1 = next(csv.reader(f1))
    with open(str(output2), newline="", encoding="utf-8") as f2:
        headers2 = next(csv.reader(f2))

    assert headers1 == headers2, (
        f"Column headers differ between runs:\nRun1: {headers1}\nRun2: {headers2}"
    )
