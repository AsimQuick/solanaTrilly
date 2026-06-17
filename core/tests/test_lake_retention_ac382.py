# ---
# module: core.tests.test_lake_retention_ac382
# sprint: sprint-8
# story: US-38 AC-38.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.lake_ship, core.schemas, core.tasks, config.settings
# ---
"""Tests for AC-38.2: ≤7-day VPS lake retention sweep.

All tests are offline and deterministic — no DB, no network, no wall-clock.

Test coverage:
  A  Old partition expired
  B  New (recent) partition retained
  C  Mixed old+new
  D  Idempotency (run-twice → same result)
  E  Empty lake (no partitions)
  F  Schema validation for lake_retention_days
  G  Task manifest contains sweep_vps_lake_partitions
  H  Task registered in Celery (importable + named correctly)
  I  Beat schedule entry present
  J  Config-driven: task honours retention_days kwarg
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_partition(base: Path, date_str: str) -> Path:
    """Create a minimal dt=YYYY-MM-DD partition directory under *base*."""
    part_dir = base / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / "placeholder.txt").write_text("data")
    return part_dir


# ---------------------------------------------------------------------------
# A — Old partition expired
# ---------------------------------------------------------------------------


def test_old_partition_expired(tmp_path):
    """A partition older than retention_days is deleted and listed as expired."""
    from core.tape.lake_ship import sweep_expired_partitions

    lake = tmp_path / "lake"
    lake.mkdir()
    old_dir = _make_partition(lake, "2026-06-01")  # clearly old

    result = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)

    assert "2026-06-01" in result.expired
    assert "2026-06-01" not in result.retained
    assert not old_dir.exists(), "expired partition directory should be deleted"


# ---------------------------------------------------------------------------
# B — New partition retained
# ---------------------------------------------------------------------------


def test_new_partition_retained(tmp_path):
    """A partition within retention_days is kept and listed as retained."""
    from core.tape.lake_ship import sweep_expired_partitions

    lake = tmp_path / "lake"
    lake.mkdir()
    new_dir = _make_partition(lake, "2026-06-16")  # 1 day ago — within 7-day window

    result = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)

    assert "2026-06-16" in result.retained
    assert "2026-06-16" not in result.expired
    assert new_dir.exists(), "retained partition directory should still exist"


# ---------------------------------------------------------------------------
# C — Mixed old + new
# ---------------------------------------------------------------------------


def test_mixed_old_and_new(tmp_path):
    """Old partitions are expired; new ones are retained; both lists are sorted."""
    from core.tape.lake_ship import sweep_expired_partitions

    lake = tmp_path / "lake"
    lake.mkdir()
    _make_partition(lake, "2026-06-01")  # 16 days ago — expired
    _make_partition(lake, "2026-06-05")  # 12 days ago — expired
    _make_partition(lake, "2026-06-14")  # 3 days ago — retained
    _make_partition(lake, "2026-06-16")  # 1 day ago — retained

    result = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)

    assert result.expired == ["2026-06-01", "2026-06-05"]
    assert result.retained == ["2026-06-14", "2026-06-16"]
    assert not (lake / "dt=2026-06-01").exists()
    assert not (lake / "dt=2026-06-05").exists()
    assert (lake / "dt=2026-06-14").exists()
    assert (lake / "dt=2026-06-16").exists()


# ---------------------------------------------------------------------------
# D — Idempotency
# ---------------------------------------------------------------------------


def test_idempotent(tmp_path):
    """Running the sweep twice yields the same result; second run has no expired dirs."""
    from core.tape.lake_ship import sweep_expired_partitions

    lake = tmp_path / "lake"
    lake.mkdir()
    _make_partition(lake, "2026-06-01")
    _make_partition(lake, "2026-06-16")

    result1 = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)
    result2 = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)

    assert result1.expired == ["2026-06-01"]
    assert result1.retained == ["2026-06-16"]

    # Second run: the expired dir is already gone — nothing to expire
    assert result2.expired == []
    assert result2.retained == ["2026-06-16"]


# ---------------------------------------------------------------------------
# E — Empty lake
# ---------------------------------------------------------------------------


def test_empty_lake(tmp_path):
    """Sweeping a lake with no partitions returns empty lists without error."""
    from core.tape.lake_ship import sweep_expired_partitions

    lake = tmp_path / "empty_lake"
    lake.mkdir()

    result = sweep_expired_partitions(lake, "2026-06-17", retention_days=7)

    assert result.expired == []
    assert result.retained == []


def test_nonexistent_lake(tmp_path):
    """Sweeping a non-existent lake_base returns empty lists without error."""
    from core.tape.lake_ship import sweep_expired_partitions

    result = sweep_expired_partitions(tmp_path / "no_such_dir", "2026-06-17", retention_days=7)

    assert result.expired == []
    assert result.retained == []


# ---------------------------------------------------------------------------
# F — Schema validation for lake_retention_days
# ---------------------------------------------------------------------------


def test_schema_default_is_7():
    """lake_retention_days defaults to 7."""
    from core.schemas import TapeConfig

    tape = TapeConfig(idle_kill_ttl_s=300)
    assert tape.lake_retention_days == 7


@pytest.mark.parametrize("days", [1, 3, 7])
def test_schema_valid_values(days):
    """lake_retention_days accepts values 1–7."""
    from core.schemas import TapeConfig

    tape = TapeConfig(idle_kill_ttl_s=300, lake_retention_days=days)
    assert tape.lake_retention_days == days


@pytest.mark.parametrize("days", [0, 8, -1])
def test_schema_invalid_values(days):
    """lake_retention_days rejects values outside 1–7."""
    from pydantic import ValidationError

    from core.schemas import TapeConfig

    with pytest.raises(ValidationError):
        TapeConfig(idle_kill_ttl_s=300, lake_retention_days=days)


# ---------------------------------------------------------------------------
# G — Task manifest
# ---------------------------------------------------------------------------


def test_task_manifest_contains_sweep():
    """core.tasks.sweep_vps_lake_partitions is listed in task_manifest.json."""
    manifest_path = Path(__file__).resolve().parent.parent / "task_manifest.json"
    data = json.loads(manifest_path.read_text())
    assert "core.tasks.sweep_vps_lake_partitions" in data["tasks"]


# ---------------------------------------------------------------------------
# H — Task registered in Celery
# ---------------------------------------------------------------------------


def test_task_importable():
    """sweep_vps_lake_partitions can be imported and has the correct Celery name."""
    from core.tasks import sweep_vps_lake_partitions

    assert sweep_vps_lake_partitions.name == "core.tasks.sweep_vps_lake_partitions"


# ---------------------------------------------------------------------------
# I — Beat schedule
# ---------------------------------------------------------------------------


def test_beat_schedule_entry():
    """sweep-vps-lake-partitions is present in CELERY_BEAT_SCHEDULE."""
    from django.conf import settings

    assert "sweep-vps-lake-partitions" in settings.CELERY_BEAT_SCHEDULE
    entry = settings.CELERY_BEAT_SCHEDULE["sweep-vps-lake-partitions"]
    assert entry["task"] == "core.tasks.sweep_vps_lake_partitions"


# ---------------------------------------------------------------------------
# J — Config-driven: task honours retention_days kwarg
# ---------------------------------------------------------------------------


def test_task_config_driven(tmp_path):
    """Task uses the retention_days kwarg and returns correct expired/retained lists."""
    from core.tasks import sweep_vps_lake_partitions

    lake = tmp_path / "lake"
    lake.mkdir()
    _make_partition(lake, "2026-06-01")   # old — should be expired
    _make_partition(lake, "2026-06-16")   # recent — should be retained

    result = sweep_vps_lake_partitions(
        reference_date_str="2026-06-17",
        lake_base=str(lake),
        retention_days=7,
    )

    assert result["expired"] == ["2026-06-01"]
    assert result["retained"] == ["2026-06-16"]
