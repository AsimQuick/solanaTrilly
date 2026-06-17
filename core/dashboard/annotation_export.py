# ---
# module: core.dashboard.annotation_export
# sprint: sprint-10
# story: US-51 AC-51.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.annotation_api, core.tape.manifest, csv, hashlib, json, pathlib
# ---
"""Annotation export — labeled dataset (annotations joined to corpus) for the labs (PRD §13.3, US-51 AC-51.2).

Mirrors the §6.5 Feature Builder export pattern:
  CSV output      — one row per mint in the corpus (mint, tags, notes, annotation_count)
  MANIFEST.json   — dataset_id, content_hash (SHA-256), mint_cohort (LC_ALL=C sorted),
                    row_count, source, date_range  (per US-36 / core.tape.manifest)

Runs as a Celery task (core.tasks.export_annotations) — NEVER on web/gunicorn (#289).
The raw lake is NEVER touched (raw=immutable, §6.4.1).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def build_annotation_export(
    mint_corpus: list,
    output_path: str,
    *,
    dataset_id: str = "annotation_export",
) -> dict:
    """Join annotations to the token corpus and write a labeled CSV + MANIFEST.

    For each mint in *mint_corpus* (in LC_ALL=C sort order) the function collects
    all annotations from the DB via get_annotations() and produces a single
    labeled row:

      mint              — the Solana mint address
      tags              — JSON array of unique tags (first-occurrence order across all
                          annotations for this mint, ordered by created_at ascending)
      notes             — JSON array of non-empty note strings across all annotations
      annotation_count  — number of Annotation rows for this mint (0 = unannotated)

    The MANIFEST follows the US-36 pattern (core.tape.manifest.build_manifest /
    write_manifest): dataset_id, content_hash (SHA-256 of the raw CSV bytes),
    mint_cohort (LC_ALL=C sorted), row_count, source="annotations", date_range
    (min/max annotation created_at, or empty strings when no annotations exist).

    The output is deterministic: same DB state + same corpus → byte-identical CSV
    and identical content_hash across runs.

    Args:
        mint_corpus: List of mint addresses to include in the export.
        output_path: Destination CSV file path.
        dataset_id:  Unique identifier for this dataset (MANIFEST dataset_id field).

    Returns:
        {"path": str, "manifest": dict, "manifest_path": str, "row_count": int}
    """
    from core.dashboard.annotation_api import get_annotations
    from core.tape.manifest import build_manifest, compute_content_hash, lc_all_c_sort, write_manifest

    sorted_corpus = lc_all_c_sort(mint_corpus)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    all_created_datetimes = []

    for mint in sorted_corpus:
        annotations = list(get_annotations(mint))
        all_tags: list = []
        seen_tags: set = set()
        notes: list = []
        for ann in annotations:
            for tag in (ann.tags or []):
                if tag not in seen_tags:
                    seen_tags.add(tag)
                    all_tags.append(tag)
            if ann.note:
                notes.append(ann.note)
            all_created_datetimes.append(ann.created_at)
        rows.append({
            "mint": mint,
            "tags": json.dumps(all_tags),
            "notes": json.dumps(notes),
            "annotation_count": len(annotations),
        })

    fieldnames = ["mint", "tags", "notes", "annotation_count"]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    content_hash = compute_content_hash(Path(output_path))

    if all_created_datetimes:
        sorted_dts = sorted(all_created_datetimes)
        date_start = sorted_dts[0].strftime("%Y-%m-%d")
        date_end = sorted_dts[-1].strftime("%Y-%m-%d")
    else:
        date_start = ""
        date_end = ""

    manifest = build_manifest(
        dataset_id=dataset_id,
        source="annotations",
        date_start=date_start,
        date_end=date_end,
        mint_cohort=list(sorted_corpus),
        row_count=len(rows),
        content_hash=content_hash,
    )

    manifest_path = write_manifest(Path(output_path).parent, manifest)

    return {
        "path": str(output_path),
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "row_count": len(rows),
    }
