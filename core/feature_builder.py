# ---
# module: core.feature_builder
# sprint: sprint-7
# story: US-31 AC-31.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, core.tape.lake_reader, csv, pathlib
# ---
"""Feature Builder core logic — shared extractor over lake → CSV export (PRD §6.5, US-31)."""
from __future__ import annotations

import csv
from pathlib import Path


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
    """Extract features for each mint via the shared extractor, write CSV.

    Uses FeatureExtractor.extract_from_lake (the shared US-30 extractor) — the
    same code path as live scoring and replay.  Output columns are ordered:
    'mint', then the tape_* features in sorted order, then internal stamp keys.

    Args:
        feature_set: A FeatureSet model instance.
        mint_cohort: List of mint address strings to process.
        label_def: Label definition dict (passed through for AC-31.2/31.3 use;
                   not consumed in AC-31.1).
        lake_base_dir: Root of the jsonl.gz lake tree.
        output_path: File path where the CSV will be written.
        window_s: Feature extraction window in seconds (default 120).
        bucket_s: Bucket size for tape_close_b* (default 15).

    Returns:
        {"path": str, "row_count": int}
    """
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    extractor = FeatureExtractor(feature_set=feature_set)
    lake_rows = list(LakeReader(base_dir=lake_base_dir).iter_rows())

    results = []
    for mint in mint_cohort:
        features = extractor.extract_from_lake(
            mint, lake_rows, window_s=window_s, bucket_s=bucket_s
        )
        if features is not None:
            results.append({"mint": mint, **features})

    if not results:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("mint\n", encoding="utf-8")
        return {"path": str(output_path), "row_count": 0}

    # Deterministic column order: mint, sorted tape_* keys, sorted stamp keys.
    tape_keys = sorted(k for k in results[0] if k.startswith("tape_"))
    stamp_keys = sorted(k for k in results[0] if k.startswith("_"))
    fieldnames = ["mint"] + tape_keys + stamp_keys

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    return {"path": str(output_path), "row_count": len(results)}
