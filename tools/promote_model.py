# ---
# module: tools.promote_model
# sprint: sprint-9
# story: US-41 AC-41.3, US-42 AC-42.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_reconciler, core.models, sys, argparse, hashlib, json, pathlib
# ---
"""promote_model.py — feature-contract gate + BLEND write contract (PRD §7.4, AC-41.3/AC-42.2).

AC-41.3  run_feature_contract_gate()
    Hard gate: refuses a model whose feature_list mismatches booster order or
    contains non-live-servable features.  Prints a REMEDY naming each offending
    feature and mismatch kind.  MODEL-AGNOSTIC.

AC-42.2  promote_blend()
    Loads the 15 LightGBM boosters (boosters/<label>_s<seed>.txt, 3 labels × 5
    seeds) + the rank-average blend transform descriptor, enforces the US-41
    feature-order gate at write time, and records the artifact to ModelRegistry.
    REFUSES with printed REMEDY on feature-order mismatch OR wrong booster count.
    Requires lightgbm (in-container per Docker Rules — never host).

Gate API::

    from tools.promote_model import run_feature_contract_gate, FeatureContractError

    try:
        run_feature_contract_gate(
            model_feature_list=meta["features"],
            feature_set_columns=feature_set.columns,
            feature_set_live_servable=feature_set.live_servable,
            booster_feature_lists=booster_feature_lists,
        )
    except FeatureContractError as exc:
        sys.exit(1)

Blend-promote API::

    from tools.promote_model import promote_blend, FeatureContractError

    try:
        registry_entry = promote_blend(
            artifact_dir="/path/to/trilly_pregrad_v3_2",
            feature_set_columns=feature_set.columns,
            feature_set_live_servable=feature_set.live_servable,
        )
    except FeatureContractError as exc:
        sys.exit(1)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core.feature_reconciler import ReconcileResult, parse_lgbm_feature_names, reconcile_feature_contract

if TYPE_CHECKING:
    from core.models import ModelRegistry


class FeatureContractError(Exception):
    """Raised when a model fails the feature-contract gate (PRD §7.4)."""


def run_feature_contract_gate(
    model_feature_list: list[str],
    feature_set_columns: list[str],
    feature_set_live_servable: list[str],
    booster_feature_lists: Optional[list[list[str]]] = None,
    *,
    print_remedy: bool = True,
) -> ReconcileResult:
    """Run the feature-contract hard gate.

    Compares the model's declared feature list against the P5 FeatureSet
    contract (US-29/US-30) and refuses on any violation.

    Parameters
    ----------
    model_feature_list:
        Ordered features the model requires (meta.json:features / booster.feature_name()).
        This is the BINDING order per PRD §7.4.
    feature_set_columns:
        All features produced by the shared extractor (FeatureSet.columns).
    feature_set_live_servable:
        Features computable at live score time (FeatureSet.live_servable, US-30 D2).
    booster_feature_lists:
        Per-booster feature name lists.  Each must match model_feature_list exactly.
    print_remedy:
        When True (default), prints REMEDY lines to stdout before raising.

    Returns
    -------
    ReconcileResult
        The reconciliation result — only returned when the contract is CLEAN.

    Raises
    ------
    FeatureContractError
        If the model fails the gate: missing features, order divergence,
        training-only features, or booster mismatches.
    """
    result = reconcile_feature_contract(
        model_feature_list=model_feature_list,
        feature_set_columns=feature_set_columns,
        feature_set_live_servable=feature_set_live_servable,
        booster_feature_lists=booster_feature_lists,
    )

    if not result.clean:
        remedy_lines = result.remedy_lines()
        if print_remedy:
            print("PROMOTION REFUSED — feature-contract violations detected:")
            for line in remedy_lines:
                print(f"  REMEDY: {line}")
        raise FeatureContractError(
            "Feature-contract gate failed:\n" + "\n".join(remedy_lines)
        )

    return result


def promote_blend(
    artifact_dir: str | Path,
    feature_set_columns: list[str],
    feature_set_live_servable: list[str],
    *,
    notes: str = "",
) -> "ModelRegistry":
    """Promote a LightGBM BLEND artifact to the ModelRegistry (AC-42.2).

    Loads all 15 LightGBM boosters from ``boosters/<label>_s<seed>.txt``
    (3 labels × 5 seeds), enforces the US-41 feature-order gate against every
    booster's feature_name(), then records the artifact to ModelRegistry on
    success.

    The promoter REFUSES (raises FeatureContractError + prints REMEDY) if:
      - any expected booster file is missing (wrong booster count)
      - any booster's feature list diverges from meta.json:features
      - model features are absent from feature_set_columns
      - model features are not all live_servable
      - relative order of model features within columns diverges

    Parameters
    ----------
    artifact_dir:
        Path to the model directory containing ``meta.json`` and
        ``boosters/<label>_s<seed>.txt``.
    feature_set_columns:
        Ordered list of all features produced by the shared extractor
        (FeatureSet.columns — the P5 contract).
    feature_set_live_servable:
        Subset of columns computable at live score time (FeatureSet.live_servable).
    notes:
        Optional free-text notes stored in the ModelRegistry row.

    Returns
    -------
    ModelRegistry
        The newly created (inactive) ModelRegistry row.  Call
        ``activate_model(entry.pk)`` to make it active.

    Raises
    ------
    FeatureContractError
        On any gate violation: booster count, feature order, or live-servable
        mismatch.  Prints REMEDY lines before raising.
    """
    artifact_dir = Path(artifact_dir)

    # Load meta.json
    meta_path = artifact_dir / "meta.json"
    with meta_path.open() as fh:
        meta = json.load(fh)

    model_feature_list: list[str] = meta["features"]
    labels: list[str] = list(meta["labels"].keys())
    seeds: list[int] = list(range(5))  # convention: seeds 0..4

    # Build expected booster paths and validate count
    boosters_dir = artifact_dir / "boosters"
    expected_paths: list[Path] = [
        boosters_dir / f"{label}_s{seed}.txt"
        for label in labels
        for seed in seeds
    ]
    expected_count = len(expected_paths)

    missing_files = [p for p in expected_paths if not p.exists()]
    if missing_files:
        n_found = expected_count - len(missing_files)
        msg = (
            f"BOOSTER COUNT: expected {expected_count} "
            f"({len(labels)} labels × {len(seeds)} seeds), "
            f"found {n_found}; missing: {[p.name for p in missing_files]}"
        )
        print("PROMOTION REFUSED — booster count mismatch:")
        print(f"  REMEDY: {msg}")
        raise FeatureContractError(msg)

    # Load each booster and extract its feature name list
    booster_feature_lists: list[list[str]] = []
    for path in expected_paths:
        try:
            import lightgbm as lgb  # in-container dependency (Docker Rules, AC-42.2)

            bst = lgb.Booster(model_file=str(path))
            booster_feature_lists.append(bst.feature_name())
        except ImportError:
            # Fallback: text-file parser (lightgbm not installed — tests or CI without scorer)
            booster_feature_lists.append(parse_lgbm_feature_names(path))

    # Enforce US-41 feature-order gate (refuses on any violation)
    run_feature_contract_gate(
        model_feature_list=model_feature_list,
        feature_set_columns=feature_set_columns,
        feature_set_live_servable=feature_set_live_servable,
        booster_feature_lists=booster_feature_lists,
    )

    # Compute SHA-256 content hashes for meta.json + every booster
    def _sha256(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    artifact_content_hashes: dict[str, str] = {"meta.json": _sha256(meta_path)}
    for path in expected_paths:
        artifact_content_hashes[f"boosters/{path.name}"] = _sha256(path)

    # Build the structured manifest and blend descriptor
    labels_seeds_manifest = {
        "labels": labels,
        "seeds": seeds,
        "boosters": {
            lbl: [f"boosters/{lbl}_s{s}.txt" for s in seeds]
            for lbl in labels
        },
    }

    blend_transform_descriptor = {
        "method": "rank_average",
        "labels": labels,
        "per_label": "seed_average_then_percentile_rank",
        "blend": "mean_of_3_percentile_ranks",
        "selection": "top_k_by_blend",
        "selection_recipe": meta.get("selection_recipe", ""),
    }

    model_version = meta.get("name", str(artifact_dir.name))
    feature_set_version = meta.get("feature_set", "unknown")

    # DB write — lazy import so the module is importable without Django setup
    from core.models import ModelRegistry as _ModelRegistry  # noqa: PLC0415

    return _ModelRegistry.objects.create(
        kind="lightgbm_regression_blend",
        feature_list=model_feature_list,
        labels_seeds_manifest=labels_seeds_manifest,
        blend_transform_descriptor=blend_transform_descriptor,
        artifact_content_hashes=artifact_content_hashes,
        model_version=model_version,
        feature_set_version=feature_set_version,
        notes=notes,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Feature-contract gate for model promotion (PRD §7.4, AC-41.3).\n\n"
            "In the full US-42 promoter this is called programmatically via\n"
            "run_feature_contract_gate().  This CLI entry-point is for manual\n"
            "inspection only."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print a summary of the gate API and exit 0 (smoke-test mode).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check:
        print("promote_model gate is importable and ready (AC-41.3)")
        print(f"  run_feature_contract_gate: {run_feature_contract_gate}")
        print(f"  FeatureContractError:      {FeatureContractError}")
        return 0
    print(
        "promote_model.py: use --check for a smoke test, or import "
        "run_feature_contract_gate() from the US-42 promoter."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
