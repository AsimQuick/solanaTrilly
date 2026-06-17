# ---
# module: core.tests.test_lake_ship_ac381
# sprint: sprint-8
# story: US-38 AC-38.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.lake_ship, core.tape.manifest, core.tasks, core.schemas,
#               gzip, json, pathlib, pytest
# ---
"""AC-38.1 — Daily VPS→local lake ship: Celery-beat task, config-driven window, MANIFEST.

Test coverage:
  (A) H2 task-manifest guard — ship_lake_partitions is in the committed manifest
      and registered in the live Celery registry (guards against removal).
  (B) ship_partition: ships files from src to dst, MANIFEST written and valid.
  (C) ship_partition: returns None for absent source partition (no-op).
  (D) ship_partition: row_count and mint_cohort are correct in MANIFEST.
  (E) ship_lake: multi-date fan-out, skips absent dates, returns only found results.
  (F) ship_lake_partitions task: config-driven window — reads lake_ship_window_days.
  (G) beat schedule — ship-lake-partitions entry registered in settings.
  (H) Both compose files define the celery-beat service (task runs off celery container).
  (I) content_hash in MANIFEST is non-empty and reproducible (run-twice idempotent).
  (J) Shipped partition directory and part file exist at dst after ship_partition.
  (K) lake_ship_window_days schema field — default=1, accepted values, le=7 enforced.

All tests are offline and deterministic — no DB, no network, no wall-clock.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from core.tape.lake_ship import ShipResult, ship_lake, ship_partition
from core.tape.manifest import MANIFEST_REQUIRED_KEYS, read_manifest

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_NAME = "core.tasks.ship_lake_partitions"


def _write_part(partition_dir: Path, rows: list[dict]) -> Path:
    """Write rows as part-0.jsonl.gz into *partition_dir*."""
    partition_dir.mkdir(parents=True, exist_ok=True)
    part = partition_dir / "part-0.jsonl.gz"
    with gzip.open(part, "wb") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
    return part


def _make_lake(base: Path, date_str: str, rows: list[dict]) -> Path:
    """Create a minimal lake partition at *base/dt={date_str}/part-0.jsonl.gz*."""
    partition_dir = base / f"dt={date_str}"
    _write_part(partition_dir, rows)
    return partition_dir


_SAMPLE_ROWS = [
    {"mint": "MintAAA", "rel": -10.0, "price": 0.001, "side": "buy", "vol": 100.0,
     "owner": "OwnerA", "block_time": 1_750_100_000, "slot": 12345, "signature": "sig1"},
    {"mint": "MintBBB", "rel": 5.0, "price": 0.002, "side": "sell", "vol": 50.0,
     "owner": "OwnerB", "block_time": 1_750_100_010, "slot": 12346, "signature": "sig2"},
    {"mint": "MintAAA", "rel": 20.0, "price": 0.003, "side": "buy", "vol": 75.0,
     "owner": "OwnerC", "block_time": 1_750_100_020, "slot": 12347, "signature": "sig3"},
]

# ---------------------------------------------------------------------------
# (A) H2 task-manifest guard
# ---------------------------------------------------------------------------

MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"


def test_ship_task_in_committed_manifest():
    """ship_lake_partitions must appear in core/task_manifest.json (H2 removal guard)."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert _TASK_NAME in manifest["tasks"], (
        f"'{_TASK_NAME}' is absent from core/task_manifest.json. "
        "Add it — the H2 guard fires if a registered task is missing from the manifest."
    )


def test_ship_task_registered_in_celery():
    """ship_lake_partitions must be discoverable in the live Celery registry."""
    import core.tasks  # noqa: F401 — side-effect: registers @shared_task decorators
    from config import celery_app

    assert _TASK_NAME in celery_app.tasks, (
        f"'{_TASK_NAME}' is absent from the Celery task registry. "
        "Ensure the @shared_task decorator uses the correct name= argument."
    )


# ---------------------------------------------------------------------------
# (B) ship_partition: happy path — files copied, MANIFEST written
# ---------------------------------------------------------------------------


def test_ship_partition_copies_files_and_writes_manifest(tmp_path):
    """ship_partition copies part-0.jsonl.gz from src to dst and writes MANIFEST."""
    src = tmp_path / "src_lake"
    dst = tmp_path / "dst_lake"
    date_str = "2026-06-16"
    _make_lake(src, date_str, _SAMPLE_ROWS)

    result = ship_partition(src, dst, date_str)

    assert result is not None
    assert isinstance(result, ShipResult)
    assert result.date_str == date_str
    assert "part-0.jsonl.gz" in result.part_files_copied

    dst_part = dst / f"dt={date_str}" / "part-0.jsonl.gz"
    assert dst_part.exists(), "Copied part file must exist in dst partition"

    manifest_file = dst / f"dt={date_str}" / "MANIFEST.json"
    assert manifest_file.exists(), "MANIFEST.json must be written in dst partition"


# ---------------------------------------------------------------------------
# (C) ship_partition: absent source → None
# ---------------------------------------------------------------------------


def test_ship_partition_returns_none_for_absent_source(tmp_path):
    """ship_partition returns None when the source partition does not exist."""
    src = tmp_path / "empty_src"
    dst = tmp_path / "dst_lake"
    result = ship_partition(src, dst, "2026-06-16")
    assert result is None


# ---------------------------------------------------------------------------
# (D) MANIFEST contents — row_count and mint_cohort
# ---------------------------------------------------------------------------


def test_ship_partition_manifest_row_count_and_mints(tmp_path):
    """MANIFEST row_count matches rows written; mint_cohort is LC_ALL=C sorted."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    date_str = "2026-06-16"
    _make_lake(src, date_str, _SAMPLE_ROWS)

    result = ship_partition(src, dst, date_str)
    assert result is not None
    assert result.row_count == len(_SAMPLE_ROWS)

    manifest = read_manifest(dst / f"dt={date_str}")
    assert manifest["row_count"] == len(_SAMPLE_ROWS)
    # MintAAA < MintBBB in ASCII byte order
    assert manifest["mint_cohort"] == ["MintAAA", "MintBBB"]


def test_ship_partition_manifest_has_required_keys(tmp_path):
    """MANIFEST must contain all required keys from data-lake.md §1."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    date_str = "2026-06-16"
    _make_lake(src, date_str, _SAMPLE_ROWS)

    ship_partition(src, dst, date_str)
    manifest = read_manifest(dst / f"dt={date_str}")

    missing = MANIFEST_REQUIRED_KEYS - set(manifest.keys())
    assert not missing, f"MANIFEST is missing required keys: {missing}"


# ---------------------------------------------------------------------------
# (E) ship_lake: multi-date, skips absent dates
# ---------------------------------------------------------------------------


def test_ship_lake_ships_multiple_dates(tmp_path):
    """ship_lake ships each date that exists in src; absent dates are skipped."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    present = "2026-06-15"
    absent = "2026-06-14"
    _make_lake(src, present, _SAMPLE_ROWS[:1])

    results = ship_lake(src, dst, [present, absent])

    assert len(results) == 1
    assert results[0].date_str == present
    assert not (dst / f"dt={absent}").exists()


def test_ship_lake_returns_empty_when_all_absent(tmp_path):
    """ship_lake returns an empty list when no source partitions exist."""
    src = tmp_path / "src_empty"
    dst = tmp_path / "dst"
    results = ship_lake(src, dst, ["2026-06-15", "2026-06-16"])
    assert results == []


# ---------------------------------------------------------------------------
# (F) ship_lake_partitions task: config-driven window via window_days kwarg
# ---------------------------------------------------------------------------


def test_ship_task_ships_window_days_back(tmp_path):
    """ship_lake_partitions ships trailing window_days days before reference_date."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    # Reference date is 2026-06-17 — window_days=2 ships 2026-06-16 and 2026-06-15
    _make_lake(src, "2026-06-16", _SAMPLE_ROWS[:2])
    _make_lake(src, "2026-06-15", _SAMPLE_ROWS[:1])

    import core.tasks  # noqa: F401
    from core.tasks import ship_lake_partitions

    shipped = ship_lake_partitions(
        reference_date_str="2026-06-17",
        src_base=str(src),
        dst_base=str(dst),
        window_days=2,
    )

    shipped_dates = {r["date_str"] for r in shipped}
    assert "2026-06-16" in shipped_dates
    assert "2026-06-15" in shipped_dates
    assert len(shipped) == 2


def test_ship_task_window_days_1_ships_previous_day(tmp_path):
    """window_days=1 (default) ships only the day immediately before reference_date."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_lake(src, "2026-06-16", _SAMPLE_ROWS[:1])
    _make_lake(src, "2026-06-15", _SAMPLE_ROWS[:1])

    import core.tasks  # noqa: F401
    from core.tasks import ship_lake_partitions

    shipped = ship_lake_partitions(
        reference_date_str="2026-06-17",
        src_base=str(src),
        dst_base=str(dst),
        window_days=1,
    )

    shipped_dates = {r["date_str"] for r in shipped}
    assert "2026-06-16" in shipped_dates
    assert "2026-06-15" not in shipped_dates


def test_ship_task_returns_manifest_path(tmp_path):
    """ship_lake_partitions result dicts include 'manifest' key pointing to MANIFEST.json."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_lake(src, "2026-06-16", _SAMPLE_ROWS[:1])

    import core.tasks  # noqa: F401
    from core.tasks import ship_lake_partitions

    shipped = ship_lake_partitions(
        reference_date_str="2026-06-17",
        src_base=str(src),
        dst_base=str(dst),
        window_days=1,
    )

    assert len(shipped) == 1
    assert "manifest" in shipped[0]
    manifest_path = Path(shipped[0]["manifest"])
    assert manifest_path.name == "MANIFEST.json"
    assert manifest_path.exists()


# ---------------------------------------------------------------------------
# (G) Beat schedule — ship-lake-partitions entry in settings.py
# ---------------------------------------------------------------------------


def test_beat_schedule_has_ship_entry():
    """CELERY_BEAT_SCHEDULE in settings.py must include the ship-lake-partitions entry."""
    from django.conf import settings

    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    assert "ship-lake-partitions" in schedule, (
        "CELERY_BEAT_SCHEDULE is missing 'ship-lake-partitions'. "
        "Add it to config/settings.py so the celery-beat container runs the task daily."
    )
    entry = schedule["ship-lake-partitions"]
    assert entry["task"] == _TASK_NAME


# ---------------------------------------------------------------------------
# (H) Both compose files define celery-beat (task runs off dedicated container)
# ---------------------------------------------------------------------------


def test_docker_compose_has_celery_beat():
    """docker-compose.yml must define a celery-beat service (AC-38.1 Docker Rules)."""
    compose_path = REPO_ROOT / "docker-compose.yml"
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")
    assert "celery-beat" in content, (
        "docker-compose.yml must define a 'celery-beat' service for AC-38.1. "
        "The ship task MUST run off the celery container, NEVER web/gunicorn (#289)."
    )


def test_docker_compose_staging_has_celery_beat():
    """docker-compose.staging.yml must define a celery-beat service (AC-38.1 Docker Rules)."""
    compose_path = REPO_ROOT / "docker-compose.staging.yml"
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")
    assert "celery-beat" in content, (
        "docker-compose.staging.yml must define a 'celery-beat' service for AC-38.1. "
        "The ship task MUST run off the celery container, NEVER web/gunicorn (#289)."
    )


# ---------------------------------------------------------------------------
# (I) content_hash is non-empty and reproducible (idempotent run-twice)
# ---------------------------------------------------------------------------


def test_ship_partition_content_hash_is_stable(tmp_path):
    """Shipping the same partition twice produces the same content_hash."""
    src = tmp_path / "src"
    dst1 = tmp_path / "dst1"
    dst2 = tmp_path / "dst2"
    date_str = "2026-06-16"
    _make_lake(src, date_str, _SAMPLE_ROWS)

    r1 = ship_partition(src, dst1, date_str)
    r2 = ship_partition(src, dst2, date_str)

    assert r1 is not None and r2 is not None
    assert r1.content_hash == r2.content_hash
    assert r1.content_hash != "", "content_hash must be non-empty"


# ---------------------------------------------------------------------------
# (J) Shipped partition: dst directory and part file exist
# ---------------------------------------------------------------------------


def test_ship_partition_dst_partition_exists(tmp_path):
    """After ship_partition, the dst partition directory and part file must exist."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    date_str = "2026-06-16"
    _make_lake(src, date_str, _SAMPLE_ROWS)

    result = ship_partition(src, dst, date_str)
    assert result is not None

    dst_dir = dst / f"dt={date_str}"
    assert dst_dir.is_dir()
    assert (dst_dir / "part-0.jsonl.gz").exists()


# ---------------------------------------------------------------------------
# (K) lake_ship_window_days schema field
# ---------------------------------------------------------------------------


def test_schema_lake_ship_window_days_default_is_1():
    """TapeConfig.lake_ship_window_days must default to 1 (ship yesterday's partition)."""
    from core.schemas import TapeConfig

    tape = TapeConfig(idle_kill_ttl_s=1800)
    assert tape.lake_ship_window_days == 1


def test_schema_lake_ship_window_days_accepts_valid_values():
    """lake_ship_window_days accepts values 1..7."""
    from core.schemas import TapeConfig

    for v in [1, 3, 7]:
        tape = TapeConfig(idle_kill_ttl_s=1800, lake_ship_window_days=v)
        assert tape.lake_ship_window_days == v


def test_schema_lake_ship_window_days_rejects_zero_and_above_7():
    """lake_ship_window_days must be rejected for 0 and values > 7."""
    from pydantic import ValidationError

    from core.schemas import TapeConfig

    with pytest.raises(ValidationError):
        TapeConfig(idle_kill_ttl_s=1800, lake_ship_window_days=0)

    with pytest.raises(ValidationError):
        TapeConfig(idle_kill_ttl_s=1800, lake_ship_window_days=8)
