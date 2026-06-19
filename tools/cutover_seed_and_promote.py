# ---
# module: tools.cutover_seed_and_promote
# sprint: sprint-12 (cutover / endgame)
# story: v3.2 observe-soak cutover — seed contract + observe/paper config + promote
# status: implemented
# created-by: claude
# last-updated: 2026-06-19
# dependencies: django, core.models, core.pregrad_features, core.resolver,
#               tools.promote_model
# ---
"""Idempotent cutover: seed the live FeatureSet + an OBSERVE/PAPER PipelineConfig,
promote trilly_pregrad_v3_2, and activate it — all in OBSERVE mode (no trading,
no firehose).

WHAT THIS DOES (idempotent — safe to run twice)
================================================
  1. Seed the live FeatureSet (anti-drift extraction contract) matching what the
     LIVE shared extractor actually computes for v3.2: the 20 pre_* buyer-cohort
     features in binding booster order (core.pregrad_features.PRE_FEATURE_NAMES).
     columns == live_servable == PRE_FEATURE_NAMES (justified below).
  2. Seed exactly ONE active PipelineConfig in OBSERVE/PAPER mode:
       trading.enabled = False, scoring.gate = adaptive_topk, sensible score timing.
     (firehose_active / scoring_enabled / trading_enabled are pipeline_state flags,
      NOT config fields — see step 5.)
  3. Promote /Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v3_2 via
     tools.promote_model.promote_blend(...), passing the seeded FeatureSet's
     columns + live_servable. The feature-contract gate runs at write time.
  4. Activate that ModelRegistry row as the live model (at-most-one-active).
  5. Seed PipelineState (singleton id=1) in OBSERVE/PAPER mode:
       scoring_enabled = True   (so score_token will run and emit observe scores)
       firehose_active = False  (operator turns the firehose on separately)
       trading_enabled = False  (NO capital — observe/paper only)
  6. Print a clear summary.

IDEMPOTENCY DISCIPLINE
======================
  - FeatureSet:    get_or_create keyed on the natural key (hash) — the content
                   hash is a pure function of (columns, math_version), so the
                   SAME contract always maps to the SAME row.
  - PipelineConfig: get_or_create keyed on (version, label); re-run never makes a
                   second row. activate_config() enforces at-most-one-active.
  - ModelRegistry: keyed on (model_version, feature_set_version, artifact_dir) —
                   if a matching promoted row already exists we reuse it instead
                   of calling promote_blend() again (avoids duplicate rows).
  - PipelineState: get()/update_or_create on the pk=1 singleton.

RUN (inside the web container)
==============================
    docker compose exec web python manage.py shell < tools/cutover_seed_and_promote.py

  (or, on the staging compose file:)
    docker compose -f docker-compose.staging.yml exec web \
        python manage.py shell < tools/cutover_seed_and_promote.py

The script is self-contained: it calls django.setup() defensively so it also runs
under `python manage.py shell <` (which already configures Django) AND under a
bare `python tools/cutover_seed_and_promote.py` if DJANGO_SETTINGS_MODULE is set.
"""
from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# Django bootstrap (defensive — `manage.py shell <` already calls setup()).
# ---------------------------------------------------------------------------
def _ensure_django() -> None:
    import django
    from django.apps import apps

    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()


# ===========================================================================
# Tunables — the EXACT values chosen for the v3.2 observe-soak cutover.
# ===========================================================================

# Absolute path to the v3.2 model artifact dir (meta.json + boosters/<label>_s<seed>.txt).
# NOTE: this is the research repo "solanatrills" (NOT the Django app "solanatrilly").
# On the live VPS this directory MUST exist at this path (or override ARTIFACT_DIR
# below) so promote_blend() can SHA-256 the 15 boosters + meta.json.
ARTIFACT_DIR = os.environ.get(
    "CUTOVER_ARTIFACT_DIR",
    "/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v3_2",
)

# FeatureSet contract values.
#   version:      meta.json:feature_set ("enrich20_buyer_cohort") — the contract name.
#   math_version: the vendored tape-math version string. The pre_* buyer-cohort
#                 features were vendored in sprint-9 (US-41 AC-41.2); the AC-41.2
#                 coverage test pins math_version="solanabilly3:sprint-9" for exactly
#                 this 20-feature live_servable set (core/tests/
#                 test_live_servable_coverage_ac412.py:149). We mirror that convention.
FEATURE_SET_VERSION = "enrich20_buyer_cohort"
FEATURE_SET_MATH_VERSION = "solanabilly3:sprint-9"

# Observe/paper PipelineConfig identity (natural key for idempotency).
PIPELINE_CONFIG_VERSION = 1
PIPELINE_CONFIG_LABEL = "v3.2-observe-soak"


def main() -> int:
    _ensure_django()

    from core.models import FeatureSet, ModelRegistry, PipelineConfig, PipelineState
    from core.pregrad_features import PRE_FEATURE_NAMES
    from core.resolver import activate_config, activate_model, get_active_model
    from tools.promote_model import FeatureContractError, promote_blend

    # ----------------------------------------------------------------------
    # (1) Seed the live FeatureSet (anti-drift extraction contract).
    #
    # columns == live_servable == PRE_FEATURE_NAMES (the 20 pre_* features in
    # binding booster order). Justification against run_feature_contract_gate
    # (tools/promote_model.py -> core/feature_reconciler.reconcile_feature_contract):
    #   - model_feature_list (meta.json:features) == PRE_FEATURE_NAMES exactly.
    #   - gate requires model_feature_list ⊆ columns AND ⊆ live_servable AND the
    #     relative order of model features within columns == model_feature_list.
    #   - Setting columns = live_servable = PRE_FEATURE_NAMES makes 'missing',
    #     'training_only', and 'order_divergence' all trivially empty/False; the
    #     15 boosters' feature_name() also == PRE_FEATURE_NAMES so booster_
    #     mismatches is empty. => gate is CLEAN. (Statically verified offline.)
    # The model uses exactly these 20 features and no others, so a superset would
    # add only informational 'extra' entries — the minimal correct choice is the
    # exact 20.
    # ----------------------------------------------------------------------
    columns = list(PRE_FEATURE_NAMES)
    live_servable = list(PRE_FEATURE_NAMES)
    fs_hash = FeatureSet.compute_hash(columns, FEATURE_SET_MATH_VERSION)

    feature_set, fs_created = FeatureSet.objects.get_or_create(
        hash=fs_hash,  # natural key — pure fn of (columns, math_version)
        defaults={
            "version": FEATURE_SET_VERSION,
            "math_version": FEATURE_SET_MATH_VERSION,
            "columns": columns,
            "live_servable": live_servable,
            "notes": (
                "v3.2 pre-grad buyer-cohort extraction contract (20 pre_* features, "
                "binding booster order). columns == live_servable == PRE_FEATURE_NAMES. "
                "Seeded by tools/cutover_seed_and_promote.py for the observe soak."
            ),
        },
    )
    # Self-heal: if a row with this hash exists but columns drifted, do NOT mutate
    # (FeatureSet rows are immutable by contract). Re-running with the SAME inputs
    # always resolves to the SAME hash, so the get_or_create above is the guard.

    # ----------------------------------------------------------------------
    # (2) Seed exactly ONE active PipelineConfig in OBSERVE/PAPER mode.
    #
    # Section shape mirrors the canonical valid active config
    # (core/tests/test_resolver_ac113.py VALID_*). §5.2 cross-section invariants
    # (core/schemas.py:206-218):
    #   scoring.window_s (180) > scoring.score_at_elapsed_s (120)  -> leak guard OK
    #   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)     -> D4 OK
    #   scoring.capture_buffer_s (4) >= 3                          -> OK
    # All four cross-section keys are present so PipelineConfig.clean() runs the
    # full Pydantic validation on save (core/models.py:96-118).
    #
    # OBSERVE/PAPER: trading.enabled = False. (The live on/off flags
    # firehose_active / scoring_enabled / trading_enabled are pipeline_state
    # singleton fields, NOT config fields — set in step 5.)
    # ----------------------------------------------------------------------
    detection = {
        "source": "birdeye_meme",
        "filter": {"source": "pump_dot_fun", "graduated": True},
        "prestage_progress_pct": 95.0,
        "dedupe_window_s": 60,
        "reconciler": "helius_migrate",
    }
    tape = {
        "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
        # pre-grad idle-kill: 30 min so slow-bonders survive to graduation
        # (was 300 s, which risked evicting a token before it graduated).
        "pre_grad_idle_kill_ttl_s": 1800,
        "idle_kill_ttl_s": 1800,           # protected post-grad TTL (>= outcome.window_s)
        "reattach": True,
        "birdeye_interval_s": 15,
        "lake_ship_window_days": 1,
        "lake_retention_days": 7,
    }
    scoring = {
        "score_at_elapsed_s": 120,         # canonical score point (codebase default)
        "window_s": 180,                   # > score point (leak guard)
        "capture_buffer_s": 4,
        "gate": "adaptive_topk",
        "reference_dist_path": None,        # pool-based scoring during the soak
    }
    outcome = {
        "window_s": 1800,                   # v3.2 outcome horizon (t1800 labels)
        "label_def": {},
    }
    trading = {
        "gate": "adaptive_topk",
        "enabled": False,                   # OBSERVE/PAPER — no trading
        "position_size_sol": 0.1,
        "max_open_positions": 3,
        "slippage_bps": 50,
        "paper_size_usd": 25.0,             # $25 paper size per meta.json:size_usd
        "exit_policy": None,
    }

    config, cfg_created = PipelineConfig.objects.get_or_create(
        version=PIPELINE_CONFIG_VERSION,
        label=PIPELINE_CONFIG_LABEL,        # natural key (version, label)
        defaults={
            "is_active": False,             # activation is done via activate_config()
            "notes": (
                "v3.2 observe soak: OBSERVE/PAPER. trading.enabled=False. "
                "Seeded by tools/cutover_seed_and_promote.py."
            ),
            "detection": detection,
            "tape": tape,
            "scoring": scoring,
            "outcome": outcome,
            "trading": trading,
            "feature_set_id": feature_set.pk,
        },
    )
    # Ensure the config points at the seeded FeatureSet (idempotent backfill if a
    # prior run created the config before the FeatureSet pk was known).
    if config.feature_set_id != feature_set.pk:
        config.feature_set_id = feature_set.pk
        config.save()

    # Activate via the audited at-most-one-active path (core/resolver.activate_config).
    activate_config(config.pk)

    # ----------------------------------------------------------------------
    # (3) Promote the v3.2 blend (idempotent: reuse an existing matching row).
    #     promote_blend() loads the 15 boosters, runs the feature-contract gate
    #     against the seeded FeatureSet columns + live_servable, and creates a
    #     ModelRegistry row. Signature (tools/promote_model.py:128):
    #       promote_blend(artifact_dir, feature_set_columns,
    #                     feature_set_live_servable, *, notes="") -> ModelRegistry
    # ----------------------------------------------------------------------
    model_version = "trilly_pregrad_v3.2"  # == meta.json:name (promote_blend reads it)
    resolved_artifact_dir = os.path.realpath(ARTIFACT_DIR)

    existing = ModelRegistry.objects.filter(
        model_version=model_version,
        feature_set_version=FEATURE_SET_VERSION,
        artifact_dir=resolved_artifact_dir,
    ).order_by("-created_at").first()

    if existing is not None:
        registry_entry = existing
        promoted_now = False
    else:
        try:
            registry_entry = promote_blend(
                artifact_dir=ARTIFACT_DIR,
                feature_set_columns=feature_set.columns,
                feature_set_live_servable=feature_set.live_servable,
                notes=(
                    "v3.2 observe-soak promotion. FeatureSet hash="
                    f"{feature_set.hash}. Promoted by "
                    "tools/cutover_seed_and_promote.py."
                ),
            )
        except FeatureContractError as exc:
            print("CUTOVER ABORTED — feature-contract gate refused v3.2:")
            print(str(exc))
            return 1
        promoted_now = True

    # ----------------------------------------------------------------------
    # (4) Activate the ModelRegistry row (at-most-one-active, audited path).
    #     core/resolver.activate_model.
    # ----------------------------------------------------------------------
    activate_model(registry_entry.pk)
    registry_entry.refresh_from_db()
    active_model = get_active_model()

    # ----------------------------------------------------------------------
    # (5) Seed PipelineState (singleton id=1) in OBSERVE/PAPER mode.
    #     score_token (core/tasks.py:270-272) gates on scoring_enabled, so the
    #     observe soak needs scoring_enabled=True. Trading + firehose stay OFF.
    # ----------------------------------------------------------------------
    state = PipelineState.get()  # creates pk=1 with safe False defaults if absent
    state.scoring_enabled = True
    state.firehose_active = False
    state.trading_enabled = False
    state.save()  # forces pk=1 (singleton discipline, core/models.py:140-147)

    # ----------------------------------------------------------------------
    # (6) Summary.
    # ----------------------------------------------------------------------
    config.refresh_from_db()
    print("=" * 72)
    print("CUTOVER COMPLETE — v3.2 observe-soak seeded & promoted (OBSERVE/PAPER)")
    print("=" * 72)
    print("FeatureSet:")
    print(f"  id            : {feature_set.pk}  ({'created' if fs_created else 'existing'})")
    print(f"  version       : {feature_set.version}")
    print(f"  math_version  : {feature_set.math_version}")
    print(f"  hash          : {feature_set.hash}")
    print(f"  columns       : {len(feature_set.columns)} features")
    print(f"  live_servable : {len(feature_set.live_servable)} features")
    print()
    print("PipelineConfig:")
    print(f"  id            : {config.pk}  ({'created' if cfg_created else 'existing'})")
    print(f"  version/label : {config.version} / {config.label}")
    print(f"  is_active     : {config.is_active}")
    print(f"  feature_set_id: {config.feature_set_id}")
    print(f"  trading.enabled        : {config.trading.get('enabled')}")
    print(f"  scoring.gate           : {config.scoring.get('gate')}")
    print(f"  scoring.score_at_elapsed_s / window_s : "
          f"{config.scoring.get('score_at_elapsed_s')} / {config.scoring.get('window_s')}")
    print(f"  tape.idle_kill_ttl_s / pre_grad_idle_kill_ttl_s : "
          f"{config.tape.get('idle_kill_ttl_s')} / {config.tape.get('pre_grad_idle_kill_ttl_s')}")
    print(f"  outcome.window_s       : {config.outcome.get('window_s')}")
    print()
    print("ModelRegistry:")
    print(f"  id              : {registry_entry.pk}  "
          f"({'promoted now' if promoted_now else 'reused existing'})")
    print(f"  model_version   : {registry_entry.model_version}")
    print(f"  kind            : {registry_entry.kind}")
    print(f"  feature_set_ver : {registry_entry.feature_set_version}")
    print(f"  is_active       : {registry_entry.is_active}")
    print(f"  artifact_dir    : {registry_entry.artifact_dir}")
    print(f"  active model pk : {active_model.pk if active_model else None}")
    print()
    print("PipelineState (singleton id=1):")
    print(f"  scoring_enabled : {state.scoring_enabled}")
    print(f"  firehose_active : {state.firehose_active}")
    print(f"  trading_enabled : {state.trading_enabled}")
    print("=" * 72)
    print("OBSERVE/PAPER live. No capital, firehose OFF. Turn the firehose on")
    print("separately (tools/firehose_activate.py) when ready to feed the soak.")
    print("=" * 72)
    return 0


# Run on import too, so `python manage.py shell < this_file` executes it.
_rc = main()
# Only hard-exit under a bare `python tools/cutover_seed_and_promote.py` run;
# under `manage.py shell <` a non-zero exit would abort the shell ungracefully,
# but we still surface failures via the printed ABORT message + return code.
if __name__ == "__main__":
    sys.exit(_rc)
