# ---
# module: tools.promote_v4
# sprint: hotfix
# story: v4-deploy
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: django, core.models, core.pregrad_features, core.v4_rep_builder,
#               core.resolver, tools.promote_model
# ---
"""Idempotent promoter: seed the v4 FeatureSet + register trilly_pregrad_v4 in OBSERVE mode.

WHAT THIS DOES (idempotent — safe to run twice)
================================================
  1. Seed the live FeatureSet (53 features: enrich20 + REP24 + recurrence9)
     matching the v4 meta.json feature order.
  2. Promote models/trilly_pregrad_v4 via tools.promote_model.promote_blend(),
     passing the seeded FeatureSet columns + live_servable.
     The feature-contract gate runs at write time.
  3. Activate that ModelRegistry row (at-most-one-active).
  4. Set PipelineState to OBSERVE mode: scoring_enabled=True,
     firehose_active=False, trading_enabled=False.

IMPORTANT: does NOT flip trading_enabled. v4 runs in OBSERVE only.

RUN (inside the web container)
==============================
    docker compose exec web python manage.py shell < tools/promote_v4.py
  or:
    docker compose -p solanatrilly exec web python manage.py shell < tools/promote_v4.py
"""
from __future__ import annotations

import os
import sys


def _ensure_django() -> None:
    import django
    from django.apps import apps
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Absolute path to the v4 model artifact dir in this repo.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT_DIR = os.environ.get(
    "V4_ARTIFACT_DIR",
    os.path.join(_REPO_ROOT, "models", "trilly_pregrad_v4"),
)

FEATURE_SET_VERSION = "enrich20_REP24_recurrence9_v4"
FEATURE_SET_MATH_VERSION = "v4-deploy:2026-06-20"


def main() -> int:
    _ensure_django()

    import json
    from pathlib import Path

    from core.models import FeatureSet, ModelRegistry, PipelineState
    from core.pregrad_features import PRE_FEATURE_NAMES
    from core.resolver import activate_model, get_active_model
    from core.v4_rep_builder import RECURRENCE_FEATURE_NAMES, REP_FEATURE_NAMES
    from tools.promote_model import FeatureContractError, promote_blend

    # ----------------------------------------------------------------------
    # (1) Load meta.json to get the binding feature order.
    #     The 53 features are: PRE_FEATURE_NAMES (20) + REP24 + recurrence9
    #     in EXACTLY the meta.json order.
    # ----------------------------------------------------------------------
    artifact_path = Path(ARTIFACT_DIR)
    meta_path = artifact_path / "meta.json"
    with meta_path.open() as fh:
        meta = json.load(fh)

    all_features: list[str] = meta["features"]
    assert len(all_features) == 53, f"Expected 53 features, got {len(all_features)}"
    assert all_features[:20] == list(PRE_FEATURE_NAMES), "enrich20 order mismatch"
    assert all_features[20:44] == REP_FEATURE_NAMES, "REP24 order mismatch"
    assert all_features[44:53] == RECURRENCE_FEATURE_NAMES, "recurrence9 order mismatch"

    # All 53 features are live-computable (enrich20 via compute_pregrad_features,
    # REP24+recurrence9 via compute_rep_features/compute_recurrence_features from
    # the v4_wallet_bank). columns == live_servable = all 53.
    columns = list(all_features)
    live_servable = list(all_features)

    fs_hash = FeatureSet.compute_hash(columns, FEATURE_SET_MATH_VERSION)

    feature_set, fs_created = FeatureSet.objects.get_or_create(
        hash=fs_hash,
        defaults={
            "version": FEATURE_SET_VERSION,
            "math_version": FEATURE_SET_MATH_VERSION,
            "columns": columns,
            "live_servable": live_servable,
            "notes": (
                "v4 pre-grad feature contract: 53 features = enrich20 + REP24 "
                "(outcome-weighted wallet reputation) + recurrence9 (pre_whale_*). "
                "Seeded by tools/promote_v4.py for the v4 observe soak."
            ),
        },
    )
    print(f"FeatureSet: id={feature_set.pk} ({'created' if fs_created else 'existing'}), "
          f"version={feature_set.version}, {len(columns)} features")

    # ----------------------------------------------------------------------
    # (2) Promote the v4 blend (idempotent: reuse an existing matching row).
    # ----------------------------------------------------------------------
    model_version = meta.get("name", "trilly_pregrad_v4")
    resolved_artifact_dir = str(artifact_path.resolve())

    existing = ModelRegistry.objects.filter(
        model_version=model_version,
        feature_set_version=FEATURE_SET_VERSION,
        artifact_dir=resolved_artifact_dir,
    ).order_by("-created_at").first()

    if existing is not None:
        registry_entry = existing
        promoted_now = False
        print(f"ModelRegistry: reusing existing id={registry_entry.pk}")
    else:
        try:
            registry_entry = promote_blend(
                artifact_dir=ARTIFACT_DIR,
                feature_set_columns=feature_set.columns,
                feature_set_live_servable=feature_set.live_servable,
                notes=(
                    f"v4 observe-soak promotion. FeatureSet hash={feature_set.hash}. "
                    "Promoted by tools/promote_v4.py. "
                    "53 features = enrich20 + REP24 + recurrence9. "
                    "OBSERVE only (trading_enabled=False)."
                ),
            )
        except FeatureContractError as exc:
            print("PROMOTE ABORTED — feature-contract gate refused v4:")
            print(str(exc))
            return 1
        promoted_now = True
        print(f"ModelRegistry: promoted id={registry_entry.pk}")

    # ----------------------------------------------------------------------
    # (3) Activate the ModelRegistry row (at-most-one-active).
    # ----------------------------------------------------------------------
    activate_model(registry_entry.pk)
    registry_entry.refresh_from_db()
    active_model = get_active_model()

    # ----------------------------------------------------------------------
    # (4) Set PipelineState to OBSERVE.
    # ----------------------------------------------------------------------
    state = PipelineState.get()
    # HARD CONSTRAINT: do NOT flip trading_enabled (OBSERVE only, BANK_SPEC.md)
    state.scoring_enabled = True
    state.firehose_active = False
    state.trading_enabled = False
    state.save()

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    print("=" * 72)
    print("v4 PROMOTE COMPLETE — OBSERVE mode (no capital, firehose OFF)")
    print("=" * 72)
    print(f"FeatureSet        : id={feature_set.pk}, {len(columns)} features")
    print(f"ModelRegistry     : id={registry_entry.pk} "
          f"({'promoted now' if promoted_now else 'reused'})")
    print(f"  model_version   : {registry_entry.model_version}")
    print(f"  artifact_dir    : {registry_entry.artifact_dir}")
    print(f"  is_active       : {registry_entry.is_active}")
    print(f"Active model pk   : {active_model.pk if active_model else None}")
    print(f"PipelineState     : scoring_enabled={state.scoring_enabled}, "
          f"firehose_active={state.firehose_active}, "
          f"trading_enabled={state.trading_enabled}")
    print("=" * 72)
    return 0


_rc = main()
if __name__ == "__main__":
    sys.exit(_rc)
