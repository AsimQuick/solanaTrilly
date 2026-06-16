# ---
# module: core.feature_builder
# sprint: sprint-7
# story: US-31 AC-31.1, AC-31.2, AC-31.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, core.tape.lake_reader, csv, hashlib, json, pathlib
# ---
"""Feature Builder core logic — shared extractor over lake → CSV + manifest export (PRD §6.5, US-31)."""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
from pathlib import Path


def validate_label_def(label_def: dict, window_s: int) -> None:
    """Enforce leak-free label constraint (AC-31.3, PRD §6.4.4).

    If *label_def* contains a ``label_start_s`` key its value must be >= *window_s*
    (i.e. the label observation window must not overlap the feature window [0, window_s)).
    Raises ``ValueError`` with a clear message when the constraint is violated.
    If ``label_start_s`` is absent the call is a no-op (backward compatible).

    Args:
        label_def: Label definition dict (may contain optional ``label_start_s`` key).
        window_s: Feature extraction window in seconds.
    """
    label_start_s = label_def.get("label_start_s")
    if label_start_s is not None and label_start_s < window_s:
        raise ValueError(
            f"Leak-free label violation: label_def 'label_start_s'={label_start_s!r} "
            f"draws from within the feature window [0, {window_s}). "
            f"Labels must start at or after window_s={window_s}."
        )


def _sha256_file(path: str) -> str:
    """Return the SHA-256 hex digest of the file at *path*."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _block_time_to_date(ts: int) -> str:
    """Convert a Unix timestamp to a UTC date string (YYYY-MM-DD)."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def build_features_core(
    feature_set,
    mint_cohort: list,
    label_def: dict,
    lake_base_dir: str,
    output_path: str,
    *,
    window_s: int = 120,
    bucket_s: int = 15,
) -> dict:
    """Extract features for each mint via the shared extractor, write CSV + manifest.

    Uses FeatureExtractor.extract_from_lake (the shared US-30 extractor) — the
    same code path as live scoring and replay.  Output columns are ordered:
    'mint', then the tape_* features in sorted order, then internal stamp keys.

    Every export also produces a JSON manifest (AC-31.2, PRD §6.4.5) containing:
    FeatureSet version+hash, source(s), date range (UTC) from cohort block_times,
    mint cohort, row count, SHA-256 content hash of the CSV, and label def.
    The manifest path is <output_path>.manifest.json (same directory).

    The content hash is deterministic: same raw + same FeatureSet → byte-identical
    CSV → byte-identical content_hash.

    Args:
        feature_set: A FeatureSet model instance.
        mint_cohort: List of mint address strings to process.
        label_def: Label definition dict (for AC-31.2/31.3 labelling contract).
        lake_base_dir: Root of the jsonl.gz lake tree.
        output_path: File path where the CSV will be written.
        window_s: Feature extraction window in seconds (default 120).
        bucket_s: Bucket size for tape_close_b* (default 15).

    Returns:
        {"path": str, "row_count": int, "manifest": dict, "manifest_path": str}
    """
    validate_label_def(label_def, window_s)

    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    extractor = FeatureExtractor(feature_set=feature_set)
    lake_rows = list(LakeReader(base_dir=lake_base_dir).iter_rows())

    # Compute date range from block_times of all cohort rows in the lake.
    mint_set = set(mint_cohort)
    cohort_block_times = [
        int(r["block_time"])
        for r in lake_rows
        if r.get("mint") in mint_set and "block_time" in r
    ]

    results = []
    for mint in mint_cohort:
        features = extractor.extract_from_lake(
            mint, lake_rows, window_s=window_s, bucket_s=bucket_s
        )
        if features is not None:
            results.append({"mint": mint, **features})

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if not results:
        Path(output_path).write_text("mint\n", encoding="utf-8")
    else:
        # Deterministic column order: mint, sorted tape_* keys, sorted stamp keys.
        tape_keys = sorted(k for k in results[0] if k.startswith("tape_"))
        stamp_keys = sorted(k for k in results[0] if k.startswith("_"))
        fieldnames = ["mint"] + tape_keys + stamp_keys

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)

    # Build and write manifest (AC-31.2).
    if cohort_block_times:
        date_range = {
            "start": _block_time_to_date(min(cohort_block_times)),
            "end": _block_time_to_date(max(cohort_block_times)),
        }
    else:
        date_range = {"start": None, "end": None}

    manifest = {
        "content_hash": _sha256_file(output_path),
        "date_range": date_range,
        "feature_set_hash": feature_set.hash,
        "feature_set_version": feature_set.version,
        "label_def": label_def,
        "mint_cohort": list(mint_cohort),
        "row_count": len(results),
        "sources": [f"lake://{lake_base_dir}"],
    }

    manifest_path = str(Path(output_path).with_suffix(".manifest.json"))
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2, sort_keys=True)

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "path": str(output_path),
        "row_count": len(results),
    }
