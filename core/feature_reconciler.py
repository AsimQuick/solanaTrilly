# ---
# module: core.feature_reconciler
# sprint: sprint-9
# story: US-41 AC-41.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: dataclasses, pathlib
# ---
"""Feature-contract reconciliation utility (PRD §7.4, oracle §4.1).

Compares a P5 FeatureSet (US-29 ordered columns + US-30 live_servable split)
against a model's bound feature list and reports any divergence.

MODEL-AGNOSTIC: reads the model's declared feature list; never hardcodes v3.2
or any specific set of column names.

Reports four categories of divergence:
  missing       — features the model requires but absent from FeatureSet.columns
  extra         — features in FeatureSet.live_servable the model does not use
                  (informational; not a failure on its own)
  order_divergence — the relative order of model features within FeatureSet.columns
                     does not match model_feature_list (binding-order violation §7.4)
  training_only — features the model requires that are in FeatureSet.columns but
                  NOT in live_servable (i.e. not live-computable; blocks promotion)
  booster_mismatches — list of (booster_index, booster_features) for any booster
                  whose feature list diverges from model_feature_list

A reconcile is CLEAN (ReconcileResult.clean == True) only when:
  missing == [] and training_only == [] and not order_divergence
  and booster_mismatches == []

Extra features in live_servable are reported but do NOT block promotion by
themselves (the model simply ignores features it does not use).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReconcileResult:
    """Result of a feature-contract reconciliation.

    clean is True only when missing, training_only, booster_mismatches are all
    empty and order_divergence is False.  Extra is informational only.
    """

    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    order_divergence: bool = False
    training_only: list[str] = field(default_factory=list)
    booster_mismatches: list[tuple[int, list[str]]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return (
            not self.missing
            and not self.training_only
            and not self.order_divergence
            and not self.booster_mismatches
        )

    def remedy_lines(self) -> list[str]:
        """Human-readable REMEDY lines for logging / promoter refusal messages."""
        lines = []
        for feat in self.missing:
            lines.append(f"MISSING feature '{feat}': present in model list but absent from FeatureSet.columns")
        for feat in self.training_only:
            lines.append(
                f"TRAINING-ONLY feature '{feat}': model requires live computation"
                " but feature is not in live_servable"
            )
        if self.order_divergence:
            lines.append(
                "ORDER DIVERGENCE: model feature list order does not match"
                " FeatureSet.columns order (PRD §7.4 binding-order violation)"
            )
        for idx, booster_features in self.booster_mismatches:
            lines.append(f"BOOSTER MISMATCH at index {idx}: booster feature_name() differs from model feature list")
        for feat in self.extra:
            lines.append(f"EXTRA feature '{feat}': in live_servable but not required by model (informational)")
        return lines


def reconcile_feature_contract(
    model_feature_list: list[str],
    feature_set_columns: list[str],
    feature_set_live_servable: list[str],
    booster_feature_lists: list[list[str]] | None = None,
) -> ReconcileResult:
    """Compare a model's feature list against a P5 FeatureSet contract.

    Parameters
    ----------
    model_feature_list:
        Ordered list of features the model requires (from meta.json:features or
        booster.feature_name()).  This is the BINDING order per PRD §7.4.
    feature_set_columns:
        Ordered list of all features produced by the FeatureSet extractor
        (FeatureSet.columns — the complete extraction contract).
    feature_set_live_servable:
        Subset of feature_set_columns computable at live score time
        (FeatureSet.live_servable — US-30 D2 split).
    booster_feature_lists:
        Optional list of per-booster feature name lists (from booster.feature_name()
        or parse_lgbm_feature_names()).  Each must match model_feature_list exactly.

    Returns
    -------
    ReconcileResult
    """
    columns_set = set(feature_set_columns)
    live_set = set(feature_set_live_servable)

    missing = [f for f in model_feature_list if f not in columns_set]
    extra = [f for f in feature_set_live_servable if f not in set(model_feature_list)]
    training_only = [
        f for f in model_feature_list if f in columns_set and f not in live_set
    ]

    order_divergence = _check_order_divergence(model_feature_list, feature_set_columns)

    booster_mismatches: list[tuple[int, list[str]]] = []
    if booster_feature_lists:
        for idx, booster_features in enumerate(booster_feature_lists):
            if booster_features != model_feature_list:
                booster_mismatches.append((idx, booster_features))

    return ReconcileResult(
        missing=missing,
        extra=extra,
        order_divergence=order_divergence,
        training_only=training_only,
        booster_mismatches=booster_mismatches,
    )


def _check_order_divergence(model_features: list[str], columns: list[str]) -> bool:
    """Return True if model features appear in a different relative order in columns.

    Only features present in both lists are compared for order.  Features absent
    from columns are already captured in 'missing' and do not affect this check.
    """
    present_in_columns = [f for f in columns if f in set(model_features)]
    model_present = [f for f in model_features if f in set(columns)]
    return present_in_columns != model_present


def parse_lgbm_feature_names(booster_path: str | Path) -> list[str]:
    """Extract the feature_names list from a LightGBM text model file.

    LightGBM .txt files contain a line of the form:
        feature_names=feat_a feat_b feat_c ...

    This parser reads that line directly, so lightgbm need not be installed.
    When lightgbm IS installed (US-42+), prefer booster.feature_name() instead.

    Parameters
    ----------
    booster_path:
        Path to a LightGBM text model file (.txt).

    Returns
    -------
    list[str]
        Feature names in the order declared by the model.

    Raises
    ------
    ValueError
        If the file contains no 'feature_names=' line.
    """
    path = Path(booster_path)
    with path.open() as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("feature_names="):
                names_str = line[len("feature_names="):]
                return names_str.split(" ") if names_str else []
    raise ValueError(f"No 'feature_names=' line found in {path}")
