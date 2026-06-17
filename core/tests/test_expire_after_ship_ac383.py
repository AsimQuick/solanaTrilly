# ---
# module: core.tests.test_expire_after_ship_ac383
# sprint: sprint-8
# story: US-38 AC-38.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.lake_ship, core.tape.manifest, gzip, json, shutil, pathlib, pytest
# ---
"""AC-38.3 — Expire-after-ship: no data loss gate.

A partition is expired on the VPS ONLY after a verified successful ship: the
shipped copy exists locally AND its content hash matches the local MANIFEST
(written by ship_partition at ship time, recording the VPS content hash).  An
unshipped or hash-mismatched partition is NEVER expired.

Test coverage:
  A  is_partition_verified_shipped → False when local dir absent
  B  is_partition_verified_shipped → False when MANIFEST absent
  C  is_partition_verified_shipped → False on content-hash mismatch
  D  is_partition_verified_shipped → True when everything matches
  E  sweep_expired_partitions_safe: verified-shipped old partition is expired
  F  sweep_expired_partitions_safe: unshipped old partition is retained (NOT deleted)
  G  sweep_expired_partitions_safe: hash-mismatch old partition is retained
  H  sweep_expired_partitions_safe: recent partition retained regardless of ship status
  I  sweep_expired_partitions_safe: idempotent (run-twice → same result)
  J  sweep_expired_partitions_safe: empty VPS lake returns empty lists
  K  sweep_expired_partitions_safe: mixed scenario — shipped expires, unshipped retained
  L  sweep_vps_lake_partitions task: unshipped partition retained when local_base provided

All tests are offline and deterministic — no DB, no network, no wall-clock.
"""
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

from core.tape.lake_ship import (
    is_partition_verified_shipped,
    ship_partition,
    sweep_expired_partitions_safe,
)
from core.tape.manifest import build_manifest, write_manifest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SAMPLE_ROWS = [
    {
        "mint": "MintAAA",
        "rel": -10.0,
        "price": 0.001,
        "side": "buy",
        "vol": 100.0,
        "owner": "OwnerA",
        "block_time": 1_750_100_000,
        "slot": 12345,
        "signature": "sig1",
    },
    {
        "mint": "MintBBB",
        "rel": 5.0,
        "price": 0.002,
        "side": "sell",
        "vol": 50.0,
        "owner": "OwnerB",
        "block_time": 1_750_100_010,
        "slot": 12346,
        "signature": "sig2",
    },
]


def _write_part(partition_dir: Path, rows: list[dict]) -> Path:
    """Write rows as part-0.jsonl.gz into *partition_dir*."""
    partition_dir.mkdir(parents=True, exist_ok=True)
    part = partition_dir / "part-0.jsonl.gz"
    with gzip.open(part, "wb") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
    return part


def _make_vps_partition(base: Path, date_str: str) -> Path:
    """Create a minimal VPS partition at *base/dt={date_str}/part-0.jsonl.gz*."""
    part_dir = base / f"dt={date_str}"
    _write_part(part_dir, _SAMPLE_ROWS)
    return part_dir


# ---------------------------------------------------------------------------
# A — is_partition_verified_shipped → False when local dir absent
# ---------------------------------------------------------------------------


def test_verified_shipped_false_when_local_absent(tmp_path):
    """is_partition_verified_shipped returns False when the local copy does not exist."""
    vps_dir = tmp_path / "vps" / "dt=2026-06-01"
    _write_part(vps_dir, _SAMPLE_ROWS)
    local_dir = tmp_path / "local" / "dt=2026-06-01"  # does not exist

    assert not is_partition_verified_shipped(vps_dir, local_dir)


# ---------------------------------------------------------------------------
# B — is_partition_verified_shipped → False when MANIFEST absent
# ---------------------------------------------------------------------------


def test_verified_shipped_false_when_manifest_absent(tmp_path):
    """is_partition_verified_shipped returns False when MANIFEST.json is missing."""
    vps_dir = tmp_path / "vps" / "dt=2026-06-01"
    _write_part(vps_dir, _SAMPLE_ROWS)

    local_dir = tmp_path / "local" / "dt=2026-06-01"
    local_dir.mkdir(parents=True)
    # Copy the part file but do NOT write MANIFEST
    shutil.copy2(str(vps_dir / "part-0.jsonl.gz"), str(local_dir / "part-0.jsonl.gz"))

    assert not is_partition_verified_shipped(vps_dir, local_dir)


# ---------------------------------------------------------------------------
# C — is_partition_verified_shipped → False on content-hash mismatch
# ---------------------------------------------------------------------------


def test_verified_shipped_false_on_hash_mismatch(tmp_path):
    """is_partition_verified_shipped returns False when local MANIFEST hash does not match VPS."""
    vps_dir = tmp_path / "vps" / "dt=2026-06-01"
    _write_part(vps_dir, _SAMPLE_ROWS)

    local_dir = tmp_path / "local" / "dt=2026-06-01"
    # Write a MANIFEST with a deliberately wrong hash (64 hex chars, all zeros)
    bad_manifest = build_manifest(
        dataset_id="test",
        source="lake_ship",
        date_start="2026-06-01",
        date_end="2026-06-01",
        mint_cohort=["MintAAA"],
        row_count=2,
        content_hash="0" * 64,
    )
    write_manifest(local_dir, bad_manifest)

    assert not is_partition_verified_shipped(vps_dir, local_dir)


# ---------------------------------------------------------------------------
# D — is_partition_verified_shipped → True when everything matches
# ---------------------------------------------------------------------------


def test_verified_shipped_true_on_matching_hash(tmp_path):
    """is_partition_verified_shipped returns True when local MANIFEST hash matches VPS."""
    vps_dir = tmp_path / "vps" / "dt=2026-06-01"
    _write_part(vps_dir, _SAMPLE_ROWS)

    local_dir = tmp_path / "local" / "dt=2026-06-01"
    # Use ship_partition to create a correctly-hashed local copy + MANIFEST
    ship_partition(tmp_path / "vps", tmp_path / "local", "2026-06-01")

    assert is_partition_verified_shipped(vps_dir, local_dir)


# ---------------------------------------------------------------------------
# E — sweep_expired_partitions_safe: verified-shipped old partition is expired
# ---------------------------------------------------------------------------


def test_safe_sweep_expires_verified_shipped_old_partition(tmp_path):
    """A verified-shipped partition older than retention_days is expired."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"
    date_str = "2026-06-01"  # 16 days before reference — old

    _make_vps_partition(vps_lake, date_str)
    ship_partition(vps_lake, local_lake, date_str)  # creates local MANIFEST w/ matching hash

    vps_dir = vps_lake / f"dt={date_str}"
    assert vps_dir.exists()  # exists before sweep

    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert date_str in result.expired
    assert date_str not in result.retained
    assert not vps_dir.exists(), "VPS partition must be deleted after verified ship"


# ---------------------------------------------------------------------------
# F — sweep_expired_partitions_safe: unshipped old partition is retained
# ---------------------------------------------------------------------------


def test_safe_sweep_retains_unshipped_old_partition(tmp_path):
    """An old partition that has NOT been shipped is NOT expired — it stays on VPS."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"
    date_str = "2026-06-01"  # old — within expiry window

    _make_vps_partition(vps_lake, date_str)
    # Intentionally do NOT call ship_partition — no local copy

    vps_dir = vps_lake / f"dt={date_str}"
    assert vps_dir.exists()

    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert date_str not in result.expired
    assert date_str in result.retained
    assert vps_dir.exists(), "Unshipped VPS partition must NOT be deleted"


# ---------------------------------------------------------------------------
# G — sweep_expired_partitions_safe: hash-mismatch old partition is retained
# ---------------------------------------------------------------------------


def test_safe_sweep_retains_hash_mismatch_old_partition(tmp_path):
    """A partition with a hash-mismatched local copy is NOT expired."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"
    date_str = "2026-06-01"

    _make_vps_partition(vps_lake, date_str)

    # Create local dir + MANIFEST with wrong hash (simulating a corrupted ship)
    local_dir = local_lake / f"dt={date_str}"
    bad_manifest = build_manifest(
        dataset_id="test",
        source="lake_ship",
        date_start=date_str,
        date_end=date_str,
        mint_cohort=["MintAAA"],
        row_count=2,
        content_hash="0" * 64,  # wrong hash
    )
    write_manifest(local_dir, bad_manifest)

    vps_dir = vps_lake / f"dt={date_str}"
    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert date_str not in result.expired
    assert date_str in result.retained
    assert vps_dir.exists(), "Hash-mismatched VPS partition must NOT be deleted"


# ---------------------------------------------------------------------------
# H — sweep_expired_partitions_safe: recent partition retained regardless
# ---------------------------------------------------------------------------


def test_safe_sweep_retains_recent_partition(tmp_path):
    """A partition within the retention window is retained, even if fully shipped."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"
    date_str = "2026-06-16"  # 1 day before reference — within 7-day window

    _make_vps_partition(vps_lake, date_str)
    ship_partition(vps_lake, local_lake, date_str)  # fully shipped

    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert date_str not in result.expired
    assert date_str in result.retained
    assert (vps_lake / f"dt={date_str}").exists()


# ---------------------------------------------------------------------------
# I — idempotency
# ---------------------------------------------------------------------------


def test_safe_sweep_idempotent(tmp_path):
    """Running sweep_expired_partitions_safe twice yields the same outcome."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"
    date_str = "2026-06-01"

    _make_vps_partition(vps_lake, date_str)
    ship_partition(vps_lake, local_lake, date_str)

    r1 = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )
    r2 = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    # First run expires the verified-shipped partition
    assert date_str in r1.expired
    # Second run: VPS partition already gone — nothing to expire
    assert r2.expired == []


# ---------------------------------------------------------------------------
# J — empty VPS lake
# ---------------------------------------------------------------------------


def test_safe_sweep_empty_vps_lake(tmp_path):
    """sweep_expired_partitions_safe on an empty VPS lake returns empty lists."""
    vps_lake = tmp_path / "vps"
    vps_lake.mkdir()
    local_lake = tmp_path / "local"

    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert result.expired == []
    assert result.retained == []


# ---------------------------------------------------------------------------
# K — mixed scenario
# ---------------------------------------------------------------------------


def test_safe_sweep_mixed_scenario(tmp_path):
    """One shipped old partition expires; one unshipped old partition is retained."""
    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"

    shipped_date = "2026-06-01"    # old + shipped → expires
    unshipped_date = "2026-06-02"  # old + NOT shipped → retained
    recent_date = "2026-06-16"     # within window → retained

    _make_vps_partition(vps_lake, shipped_date)
    _make_vps_partition(vps_lake, unshipped_date)
    _make_vps_partition(vps_lake, recent_date)

    ship_partition(vps_lake, local_lake, shipped_date)
    # unshipped_date intentionally not shipped
    ship_partition(vps_lake, local_lake, recent_date)

    result = sweep_expired_partitions_safe(
        vps_lake, local_lake, "2026-06-17", retention_days=7
    )

    assert result.expired == [shipped_date]
    assert unshipped_date in result.retained
    assert recent_date in result.retained
    assert not (vps_lake / f"dt={shipped_date}").exists()
    assert (vps_lake / f"dt={unshipped_date}").exists()
    assert (vps_lake / f"dt={recent_date}").exists()


# ---------------------------------------------------------------------------
# L — Task integration: unshipped partition retained when local_base provided
# ---------------------------------------------------------------------------


def test_sweep_task_retains_unshipped_when_local_base_provided(tmp_path):
    """sweep_vps_lake_partitions with local_base retains unshipped old partitions."""
    from core.tasks import sweep_vps_lake_partitions

    vps_lake = tmp_path / "vps"
    local_lake = tmp_path / "local"

    _make_vps_partition(vps_lake, "2026-06-01")  # old, not shipped
    _make_vps_partition(vps_lake, "2026-06-16")  # recent, not shipped

    result = sweep_vps_lake_partitions(
        reference_date_str="2026-06-17",
        lake_base=str(vps_lake),
        local_base=str(local_lake),
        retention_days=7,
    )

    assert "2026-06-01" not in result["expired"], (
        "Unshipped partition must NOT be expired when local_base is provided"
    )
    assert "2026-06-01" in result["retained"]
    assert (vps_lake / "dt=2026-06-01").exists(), "Unshipped VPS partition must still exist"
