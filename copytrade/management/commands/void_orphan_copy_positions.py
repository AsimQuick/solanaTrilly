# ---
# module: copytrade.management.commands.void_orphan_copy_positions
# sprint: live hotfix (copy-trade real-SOL readiness)
# story: copytrade-orphan-void
# status: implemented
# created-by: operator
# last-updated: 2026-06-20
# dependencies: django, copytrade.models, copytrade.reconcile
# ---
"""Void orphaned copy positions — abandoned OPEN rows from non-active cohorts.

One-time / on-demand cleanup that mirrors solanaBilly's
``scripts/resettle_paper_outcomes.py`` precedent (operate on already-stamped rows
the idempotent live engine no longer touches).

The live ``run_copytrade_engine`` only seeds/manages OPEN positions for the
*active* cohort, so positions left OPEN under a previous cohort linger forever as
deceiving ``open`` rows (the "30 copies sat open" the lab flagged).  This command
closes them with ``exit_reason=VOID`` and NULL PnL (excluded from win-rate) — it
does NOT fabricate an outcome and does NOT backfill from the offline oracle.

Usage::

    python manage.py void_orphan_copy_positions               # active cohort from CopyTradeSettings
    python manage.py void_orphan_copy_positions --active-cohort copy_2026-06-20_jun_deploy
    python manage.py void_orphan_copy_positions --dry-run      # report only, change nothing
"""
from __future__ import annotations

from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from copytrade.models import CopytradePosition, CopyTradeSettings
from copytrade.reconcile import void_orphan_positions


class Command(BaseCommand):
    help = "Void orphaned OPEN copy positions belonging to non-active cohorts (NULL PnL)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--active-cohort",
            dest="active_cohort",
            default=None,
            help=(
                "The cohort to KEEP (its open positions are not voided).  Defaults to "
                "CopyTradeSettings.active_cohort_id."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report which positions WOULD be voided without modifying anything.",
        )

    def handle(self, *args, **options) -> None:
        active_cohort: str | None = options.get("active_cohort")
        if not active_cohort:
            active_cohort = CopyTradeSettings.get().active_cohort_id or ""

        if not active_cohort:
            self.stderr.write(
                self.style.ERROR(
                    "No active cohort resolved (CopyTradeSettings.active_cohort_id is empty "
                    "and --active-cohort not given).  Refusing to void — pass --active-cohort."
                )
            )
            return

        orphan_qs = CopytradePosition.objects.filter(
            status=CopytradePosition.STATUS_OPEN
        ).exclude(cohort_id=active_cohort)
        n = orphan_qs.count()

        if n == 0:
            self.stdout.write(
                self.style.SUCCESS(f"No orphaned open positions (active cohort={active_cohort}).")
            )
            return

        # Group by cohort for a readable report.
        by_cohort: dict[str, int] = {}
        for cid in orphan_qs.values_list("cohort_id", flat=True):
            by_cohort[cid] = by_cohort.get(cid, 0) + 1
        report = ", ".join(f"{cid}={cnt}" for cid, cnt in sorted(by_cohort.items()))

        if options.get("dry_run"):
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] WOULD void {n} orphaned open position(s): {report} "
                    f"(active cohort kept: {active_cohort})."
                )
            )
            return

        now = datetime.now(timezone.utc)
        voided = void_orphan_positions(active_cohort, now)
        self.stdout.write(
            self.style.SUCCESS(
                f"Voided {len(voided)} orphaned open position(s): {report} "
                f"(active cohort kept: {active_cohort})."
            )
        )
