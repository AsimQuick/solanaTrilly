# ---
# module: tools.promote_model
# sprint: sprint-9
# story: US-41 AC-41.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_reconciler, sys, argparse
# ---
"""promote_model.py — feature-contract gate for model promotion (PRD §7.4, AC-41.3).

Provides the reconciliation hard gate consumed by the promoter (US-42).
A model is REFUSED if its feature_list mismatches booster order or if any of
its features are not live_servable.  On refusal the gate prints a REMEDY that
names each offending feature and the kind of mismatch.

MODEL-AGNOSTIC: enforces the contract against whatever model is loaded; never
hardcodes v3.2 or any specific column names.

Gate API (consumed by US-42 promote path)::

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
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from core.feature_reconciler import ReconcileResult, reconcile_feature_contract


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
