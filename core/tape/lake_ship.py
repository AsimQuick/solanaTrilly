# ---
# module: core.tape.lake_ship
# sprint: sprint-8
# story: US-38 AC-38.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.manifest, gzip, json, shutil, pathlib, dataclasses
# ---
"""Lake ship utility — copy daily lake partitions from a source tree to a destination tree.

Ships a set of date partitions (dt=YYYY-MM-DD directories) from *src_base* to
*dst_base*, then writes a MANIFEST for each shipped partition.

Architecture contract (data-lake.md):
  - ONE-WAY: VPS = capture+serve only; local = single source of truth for history.
  - The ship NEVER writes back to the source tree.
  - Every shipped partition carries a MANIFEST on arrival.
  - Raw files are immutable truth — the ship copies verbatim (no rewrite).

No wall-clock calls are made in this module — date selection is the caller's
responsibility, passed as explicit date strings (Clock principle, AC-2.2).
"""
from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.tape.manifest import build_manifest, compute_content_hash, write_manifest


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ShipResult:
    """Outcome of shipping a single daily partition."""

    date_str: str
    src_dir: Path
    dst_dir: Path
    part_files_copied: list[str]
    row_count: int
    mint_cohort: list[str]
    content_hash: str
    manifest_written: Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_rows_and_mints(part_files: list[Path]) -> tuple[int, list[str]]:
    """Return (row_count, sorted_mint_cohort) by scanning part-*.jsonl.gz files."""
    total = 0
    mints: set[str] = set()
    for pf in part_files:
        try:
            with gzip.open(pf, "rt", encoding="utf-8") as gz:
                for raw_line in gz:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        row = json.loads(raw_line)
                        total += 1
                        mint = row.get("mint", "")
                        if mint:
                            mints.add(mint)
                    except json.JSONDecodeError:
                        continue
        except (OSError, EOFError, gzip.BadGzipFile):
            continue
    return total, sorted(mints)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ship_partition(
    src_base: Path,
    dst_base: Path,
    date_str: str,
    *,
    source_tag: str = "lake_ship",
) -> Optional[ShipResult]:
    """Copy a single daily partition from *src_base* to *dst_base* and write MANIFEST.

    Copies every ``part-*.jsonl.gz`` file from
    ``src_base/dt={date_str}/`` to ``dst_base/dt={date_str}/``,
    then writes a MANIFEST.json in the destination partition directory.

    Args:
        src_base:   Root of the source lake tree (e.g. VPS lake path).
        dst_base:   Root of the destination lake tree (e.g. local lake path).
        date_str:   Partition date string ``"YYYY-MM-DD"``.
        source_tag: Value written to MANIFEST ``source`` field.

    Returns:
        ``ShipResult`` if the partition exists and was shipped, ``None`` if the
        source partition directory does not exist or contains no part files.
    """
    src_base = Path(src_base)
    dst_base = Path(dst_base)

    src_dir = src_base / f"dt={date_str}"
    if not src_dir.is_dir():
        return None

    part_files = sorted(src_dir.glob("part-*.jsonl.gz"))
    if not part_files:
        return None

    dst_dir = dst_base / f"dt={date_str}"
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied_names: list[str] = []
    for part_file in part_files:
        dst_file = dst_dir / part_file.name
        shutil.copy2(str(part_file), str(dst_file))
        copied_names.append(part_file.name)

    # Count rows + mints from the DESTINATION copies (verify copy integrity)
    dst_parts = [dst_dir / n for n in copied_names]
    row_count, mint_cohort = _count_rows_and_mints(dst_parts)

    # Content hash over the primary part file (part-0.jsonl.gz if present,
    # otherwise the first copied file — consistent with data-lake.md §1)
    primary = dst_dir / "part-0.jsonl.gz"
    if not primary.exists():
        primary = dst_parts[0] if dst_parts else None
    content_hash = compute_content_hash(primary) if primary and primary.exists() else ""

    manifest = build_manifest(
        dataset_id=f"lake_ship_{date_str}",
        source=source_tag,
        date_start=date_str,
        date_end=date_str,
        mint_cohort=mint_cohort,
        row_count=row_count,
        content_hash=content_hash,
    )
    manifest_path = write_manifest(dst_dir, manifest)

    return ShipResult(
        date_str=date_str,
        src_dir=src_dir,
        dst_dir=dst_dir,
        part_files_copied=copied_names,
        row_count=row_count,
        mint_cohort=mint_cohort,
        content_hash=content_hash,
        manifest_written=manifest_path,
    )


def ship_lake(
    src_base: Path,
    dst_base: Path,
    date_strs: list[str],
    *,
    source_tag: str = "lake_ship",
) -> list[ShipResult]:
    """Ship a list of daily partitions and return results for those that existed.

    Args:
        src_base:   Root of the source lake tree.
        dst_base:   Root of the destination lake tree.
        date_strs:  Ordered list of ``"YYYY-MM-DD"`` strings to attempt.
        source_tag: Written to each MANIFEST ``source`` field.

    Returns:
        List of ``ShipResult`` — one per partition that was found and copied.
        Partitions absent from *src_base* are silently skipped.
    """
    results: list[ShipResult] = []
    for date_str in date_strs:
        result = ship_partition(src_base, dst_base, date_str, source_tag=source_tag)
        if result is not None:
            results.append(result)
    return results
