# ---
# module: core.management.commands.promote_pf_v1
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: django, pathlib, json, core.models, core.resolver
# ---
"""promote_pf_v1 — register and activate trilly_pf_v1 as the active model.

USAGE
=====
  docker compose run --rm web python manage.py promote_pf_v1

or with a custom artifact_dir (default: /app/models/trilly_pf_v1):

  docker compose run --rm web python manage.py promote_pf_v1 \
      --artifact-dir /app/models/trilly_pf_v1

WHAT IT DOES
============
1. Validates that config.json exists in artifact_dir and has name==trilly_pf_v1.
2. Upserts a ModelRegistry row for trilly_pf_v1 (idempotent).
3. Calls activate_model() — atomically sets is_active=True on this row,
   is_active=False on all others.
4. Emits the PipelineConfig JSON the operator must activate (or creates one
   if none exists) with:
     scoring.gate = "flag"
     scoring.score_at_elapsed_s = 0   (score at graduation instant)
     scoring.per_day_target = 30      (informational; pf_v1 uses flag_threshold)
     scoring.reference_dist_path = "" (pf_v1 does NOT need this)
5. Prints the exact steps to activate the PipelineConfig.

PIPELINE CONFIG NOTE
====================
pf_v1's gate comes from config.json selection.flag_threshold (0.7952…), NOT
from the registry's depth_menu.  The PipelineConfig.scoring.gate field is set
to "flag" as a label for human readability; the actual threshold is read from
config.json at scoring time by _build_scoring_context_sync.

REFERENCE_DIST NOTE
===================
pf_v1 does NOT use reference_dist.json.  The US-76 P1.1 guard that normally
requires reference_dist_path when scoring_enabled=True is bypassed for pf_v1
(detected via is_pf_v1_model in _build_scoring_context_sync).  Leave
reference_dist_path blank or null in the PipelineConfig.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

_DEFAULT_ARTIFACT_DIR = "/app/models/trilly_pf_v1"


class Command(BaseCommand):
    help = "Register and activate trilly_pf_v1 as the live scoring model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--artifact-dir",
            default=_DEFAULT_ARTIFACT_DIR,
            help=f"Path to the trilly_pf_v1 artifact dir (default: {_DEFAULT_ARTIFACT_DIR}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Validate only — do not modify the database.",
        )
        parser.add_argument(
            "--score-at-elapsed-s",
            type=int,
            default=0,
            help="Seconds after graduation to score (default: 0 = score at graduation).",
        )

    def handle(self, *args, **options):  # noqa: ARG002
        artifact_dir = Path(options["artifact_dir"])
        dry_run: bool = options["dry_run"]
        score_at: int = options["score_at_elapsed_s"]

        self.stdout.write(f"[promote_pf_v1] artifact_dir = {artifact_dir}")

        # --- 1. Validate config.json ---
        config_path = artifact_dir / "config.json"
        if not config_path.is_file():
            self.stderr.write(
                self.style.ERROR(f"config.json not found at {config_path}. "
                                 "Ensure the artifact is staged.")
            )
            sys.exit(1)

        with config_path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)
        if cfg.get("name") != "trilly_pf_v1":
            self.stderr.write(
                self.style.ERROR(
                    f"config.json name mismatch: expected 'trilly_pf_v1', got {cfg.get('name')!r}."
                )
            )
            sys.exit(1)

        flag_threshold = float(cfg.get("selection", {}).get("flag_threshold", 0.7952236368890475))
        self.stdout.write(f"[promote_pf_v1] config.json valid: flag_threshold={flag_threshold:.8f}")

        model_txt = artifact_dir / "model.txt"
        if model_txt.is_file():
            self.stdout.write(self.style.SUCCESS("[promote_pf_v1] model.txt PRESENT — scoring active."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "[promote_pf_v1] model.txt NOT FOUND — scoring will be skipped (CI gate). "
                    "Place model.txt in the artifact dir to enable live scoring."
                )
            )

        if dry_run:
            self.stdout.write("[promote_pf_v1] --dry-run: no DB changes made.")
            return

        # --- 2. Upsert ModelRegistry row ---
        from core.models import ModelRegistry  # noqa: PLC0415
        from core.resolver import activate_model  # noqa: PLC0415

        model_row, created = ModelRegistry.objects.get_or_create(
            model_version="trilly_pf_v1",
            defaults={
                "artifact_dir": str(artifact_dir),
                "is_active": False,
            },
        )
        if not created:
            # Update artifact_dir in case it changed (re-promote after path move).
            ModelRegistry.objects.filter(pk=model_row.pk).update(artifact_dir=str(artifact_dir))
            model_row.refresh_from_db()
        action = "created" if created else "updated"
        self.stdout.write(
            f"[promote_pf_v1] ModelRegistry row {action}: "
            f"id={model_row.pk} version={model_row.model_version}"
        )

        # --- 3. Activate the model ---
        activate_model(model_row.pk)
        self.stdout.write(self.style.SUCCESS(f"[promote_pf_v1] ModelRegistry id={model_row.pk} is now ACTIVE."))

        # --- 4. Show PipelineConfig instructions ---
        self.stdout.write("")
        self.stdout.write("=" * 72)
        self.stdout.write("NEXT STEP: activate a PipelineConfig for pf_v1")
        self.stdout.write("=" * 72)
        self.stdout.write("")
        self.stdout.write("Create or update a PipelineConfig with scoring.gate='flag' and")
        self.stdout.write("scoring.reference_dist_path='' (pf_v1 uses config.json threshold).")
        self.stdout.write("")
        self.stdout.write("Minimal PipelineConfig JSON (via Django shell or admin):")
        self.stdout.write("")

        example_scoring = {
            "gate": "flag",
            "score_at_elapsed_s": score_at,
            "per_day_target": 30,
            "reference_dist_path": "",
        }
        example_outcome = {"window_s": 1800}
        example_detection = {"filter": {"source": "pump_amm"}}
        example_tape = {
            "graduation_silence_watchdog_s": 120,
            "pre_grad_idle_kill_ttl_s": 300,
            "max_postgrad_subscriptions": 5,
        }
        example_trading = {"paper_size_usd": 25}

        self.stdout.write(f"  scoring   = {json.dumps(example_scoring, indent=None)}")
        self.stdout.write(f"  outcome   = {json.dumps(example_outcome, indent=None)}")
        self.stdout.write(f"  detection = {json.dumps(example_detection, indent=None)}")
        self.stdout.write(f"  tape      = {json.dumps(example_tape, indent=None)}")
        self.stdout.write(f"  trading   = {json.dumps(example_trading, indent=None)}")
        self.stdout.write("")
        self.stdout.write("Then activate the config:")
        self.stdout.write("  from core.resolver import activate_config")
        self.stdout.write("  activate_config(<config_id>)")
        self.stdout.write("")
        self.stdout.write(f"Gate threshold in use: {flag_threshold:.8f} (from config.json)")
        self.stdout.write("No reference_dist.json required for pf_v1.")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("[promote_pf_v1] Done."))
