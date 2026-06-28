# ---
# module: core.management.commands.promote_pf
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: django, pathlib, json, core.models, core.resolver, core.pf_scorer, core.schemas
# ---
"""promote_pf — register and activate any trilly_pf_v* model as the active scoring model.

Supersedes promote_pf_v1.  Takes a model dir arg (default: models/trilly_pf_v2),
validates the contract (contract.json or config.json), registers + activates
the ModelRegistry row, and emits a valid PipelineConfig snippet.

USAGE
=====
  docker compose run --rm web python manage.py promote_pf
  docker compose run --rm web python manage.py promote_pf \\
      --artifact-dir /app/models/trilly_pf_v2

SCHEMA CORRECTNESS (tester-required fixes)
==========================================
The PipelineConfig emitted by this command uses:

  scoring.score_at_elapsed_s = 1
    The schema requires gt=0 (strictly positive integer).  pf models score at the
    graduation instant, making 0 the conceptually ideal value; however, 0 is
    rejected by the Pydantic schema validator (Field(gt=0)).  We use 1 second as
    the minimum valid positive value.  In practice, the pipeline waits for any
    pre-grad tape to be present before scoring, and the 1-second delta from the
    exact graduation instant is negligible (the tape is collected concurrently).

  scoring.gate = "adaptive_topk"
    The schema enforces Literal["adaptive_topk"] for ScoringConfig.gate.  The
    actual gate logic for pf models is the contract threshold at runtime
    (pf_scorer.gate_passes()), NOT the BlendScorer adaptive topk; this field is
    metadata for the config schema validator only.

  scoring.window_s = 120 (> score_at_elapsed_s = 1, leak-guard)
    The cross-section invariant requires window_s > score_at_elapsed_s.  We use
    120s as the capture window; the pf model scores at graduation (t=1) and does
    not use the post-grad window (PnL is offline-only), so this field is metadata.

The recommended config PASSES activate_config() without ValidationError — tested
by test_promote_pf_activate_config_succeeds().

REFERENCE_DIST NOTE
===================
pf models do NOT use reference_dist.json.  The US-76 P1.1 guard (requires
reference_dist_path when scoring_enabled=True) is bypassed in _build_scoring_context_sync
for pf models (detected via is_pf_model).  Leave reference_dist_path null.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

_DEFAULT_ARTIFACT_DIR = "/app/models/trilly_pf_v2"

# Minimum valid score_at_elapsed_s — schema requires gt=0; pf conceptually
# scores at graduation (t=0), but 0 is rejected by Pydantic.  1s is the
# smallest valid value; the 1-second delta is negligible in practice.
_SCORE_AT_ELAPSED_S = 1

# Post-grad capture window for config schema validity.  Must be > score_at_elapsed_s.
# pf models do not use this window live (PnL is offline); this is metadata only.
_WINDOW_S = 120

# Outcome window for config schema validity.
_OUTCOME_WINDOW_S = 1800


class Command(BaseCommand):
    help = "Register and activate a trilly_pf_v* model as the live scoring model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--artifact-dir",
            default=_DEFAULT_ARTIFACT_DIR,
            help=f"Path to the trilly_pf_v* artifact dir (default: {_DEFAULT_ARTIFACT_DIR}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Validate only — do not modify the database.",
        )

    def handle(self, *args, **options):  # noqa: ARG002
        artifact_dir = Path(options["artifact_dir"])
        dry_run: bool = options["dry_run"]

        self.stdout.write(f"[promote_pf] artifact_dir = {artifact_dir}")

        # --- 1. Validate contract (contract.json or config.json) ---
        from core.pf_scorer import PfScorer, is_pf_model  # noqa: PLC0415

        contract_path = artifact_dir / "contract.json"
        config_path = artifact_dir / "config.json"

        if not contract_path.is_file() and not config_path.is_file():
            self.stderr.write(
                self.style.ERROR(
                    f"Neither contract.json nor config.json found in {artifact_dir}. "
                    "Ensure the artifact is staged."
                )
            )
            sys.exit(1)

        if not is_pf_model(str(artifact_dir)):
            self.stderr.write(
                self.style.ERROR(
                    f"{artifact_dir} does not appear to be a trilly_pf_v* artifact "
                    "(contract/config name must start with 'trilly_pf')."
                )
            )
            sys.exit(1)

        try:
            scorer = PfScorer.from_dir(artifact_dir)
        except (FileNotFoundError, ValueError) as exc:
            self.stderr.write(self.style.ERROR(f"Failed to load scorer: {exc}"))
            sys.exit(1)

        self.stdout.write(
            f"[promote_pf] Contract valid: name={scorer.model_name} "
            f"n_features={len(scorer.feature_order)} threshold={scorer.threshold:.8f}"
        )
        self.stdout.write(
            f"[promote_pf] Feature order: {scorer.feature_order}"
        )

        if scorer.MODEL_PRESENT:
            self.stdout.write(self.style.SUCCESS("[promote_pf] model.txt PRESENT — scoring active."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "[promote_pf] model.txt NOT FOUND — scoring will be skipped (CI gate). "
                    "Place model.txt in the artifact dir to enable live scoring."
                )
            )

        # --- Validate PipelineConfig schema (pre-check) ---
        self._validate_config_schema()

        if dry_run:
            self.stdout.write("[promote_pf] --dry-run: no DB changes made.")
            return

        # --- 2. Upsert ModelRegistry row ---
        from core.models import ModelRegistry  # noqa: PLC0415
        from core.resolver import activate_model  # noqa: PLC0415

        model_row, created = ModelRegistry.objects.get_or_create(
            model_version=scorer.model_name,
            defaults={
                "artifact_dir": str(artifact_dir),
                "is_active": False,
            },
        )
        if not created:
            ModelRegistry.objects.filter(pk=model_row.pk).update(artifact_dir=str(artifact_dir))
            model_row.refresh_from_db()
        action = "created" if created else "updated"
        self.stdout.write(
            f"[promote_pf] ModelRegistry row {action}: "
            f"id={model_row.pk} version={model_row.model_version}"
        )

        # --- 3. Activate the model ---
        activate_model(model_row.pk)
        self.stdout.write(
            self.style.SUCCESS(f"[promote_pf] ModelRegistry id={model_row.pk} is now ACTIVE.")
        )

        # --- 4. Show PipelineConfig instructions ---
        self._print_pipeline_config_instructions(scorer)
        self.stdout.write(self.style.SUCCESS("[promote_pf] Done."))

    def _validate_config_schema(self) -> None:
        """Validate the recommended PipelineConfig against the Pydantic schema.

        This runs at promote time (even in --dry-run) to prove the recommended
        config is schema-valid before the operator tries to activate it.
        """
        from core.schemas import PipelineConfigSchema  # noqa: PLC0415

        config_dict = self._build_config_dict()
        try:
            PipelineConfigSchema(**config_dict)
            self.stdout.write(
                self.style.SUCCESS(
                    "[promote_pf] PipelineConfigSchema validation: PASS "
                    "(recommended config is schema-valid)."
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(
                self.style.ERROR(f"[promote_pf] PipelineConfigSchema validation FAILED: {exc}")
            )
            sys.exit(1)

    def _build_config_dict(self) -> dict:
        """Return the recommended PipelineConfig dict for activate_config.

        Schema constraints satisfied:
          - scoring.score_at_elapsed_s = 1 (Field(gt=0) — 0 is invalid)
          - scoring.window_s = 120 (> score_at_elapsed_s, leak-guard)
          - scoring.gate = "adaptive_topk" (Literal constraint)
          - tape.idle_kill_ttl_s >= outcome.window_s (D4 label-truncation guard)
          - detection.filter.source = "pump_amm" (pf graduation detection)
        """
        return {
            "detection": {
                "source": "birdeye_meme",
                "filter": {"source": "pump_amm", "graduated": True},
            },
            "tape": {
                "idle_kill_ttl_s": max(1800, _OUTCOME_WINDOW_S),
                "pre_grad_idle_kill_ttl_s": 300,
                "max_postgrad_subscriptions": 5,
                "graduation_silence_watchdog_s": 120,
            },
            "scoring": {
                "gate": "adaptive_topk",          # schema requires Literal["adaptive_topk"]
                "score_at_elapsed_s": _SCORE_AT_ELAPSED_S,  # must be > 0; 1 is minimum valid
                "window_s": _WINDOW_S,             # must be > score_at_elapsed_s
                "per_day_target": 18,              # ~top-10% of ~180/day
                "reference_dist_path": None,       # pf models bypass ref_dist guard
            },
            "outcome": {
                "window_s": _OUTCOME_WINDOW_S,
            },
            "trading": {
                "paper_size_usd": 25.0,
                "enabled": False,
            },
        }

    def _print_pipeline_config_instructions(self, scorer) -> None:
        """Print the exact steps to activate a PipelineConfig for this model."""
        self.stdout.write("")
        self.stdout.write("=" * 72)
        self.stdout.write(f"NEXT STEP: activate a PipelineConfig for {scorer.model_name}")
        self.stdout.write("=" * 72)
        self.stdout.write("")
        self.stdout.write("SCHEMA NOTE:")
        self.stdout.write(f"  scoring.score_at_elapsed_s = {_SCORE_AT_ELAPSED_S}  (min valid; schema requires >0; "
                          "pf conceptually scores at t=0 but 0 is rejected by Pydantic)")
        self.stdout.write(f"  scoring.gate = 'adaptive_topk'  (schema Literal; actual gate = contract "
                          f"threshold {scorer.threshold:.8f} at runtime via pf_scorer.gate_passes)")
        self.stdout.write("")
        self.stdout.write("Create or update a PipelineConfig via Django admin or shell:")
        self.stdout.write("")

        config_dict = self._build_config_dict()
        self.stdout.write("  from core.models import PipelineConfig")
        self.stdout.write("  from core.resolver import activate_config")
        self.stdout.write("  cfg = PipelineConfig.objects.create(")
        self.stdout.write(f"      scoring={json.dumps(config_dict['scoring'])},")
        self.stdout.write(f"      outcome={json.dumps(config_dict['outcome'])},")
        self.stdout.write(f"      detection={json.dumps(config_dict['detection'])},")
        self.stdout.write(f"      tape={json.dumps(config_dict['tape'])},")
        self.stdout.write(f"      trading={json.dumps(config_dict['trading'])},")
        self.stdout.write("  )")
        self.stdout.write("  activate_config(cfg.id)")
        self.stdout.write("")
        self.stdout.write(f"Gate threshold in use: {scorer.threshold:.8f} (from contract, runtime)")
        self.stdout.write(f"Feature order ({len(scorer.feature_order)}): {scorer.feature_order}")
        self.stdout.write("No reference_dist.json required (pf models bypass US-76 ref_dist guard).")
        self.stdout.write("")
