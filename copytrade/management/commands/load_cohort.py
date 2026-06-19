# ---
# module: copytrade.management.commands.load_cohort
# sprint: cutover (copy-trade live)
# story: cohort-2.0-load
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: django, copytrade.cohort_lifecycle
# ---
"""load_cohort — load a cohort.json (schema copytrade-2.0) into the engine.

Ops entry point for the SPEC §6 fresh-start: reads a cohort JSON file, runs the
full validate -> stop -> settle -> purge -> persist -> subscribe replacement, and
points the engine at the new cohort.  The engine stays OFF after load (operator
flips it on).  Reproducible/idempotent: re-running with the same file wipes and
reloads the same cohort.

    python manage.py load_cohort /path/to/cohort.json
    python manage.py load_cohort /path/to/cohort.json --settlement-price 0.0

Paper/observe only — no capital path is touched.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from copytrade.cohort_lifecycle import replace_cohort_v2


class Command(BaseCommand):
    help = "Load a cohort.json (copytrade-2.0) into the copy-trade engine (SPEC §6 fresh-start)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the cohort.json file (schema copytrade-2.0).")
        parser.add_argument(
            "--settlement-price",
            type=float,
            default=0.0,
            help="Price used to mark-close any currently-open positions during the wipe (default 0.0).",
        )

    def handle(self, *args, **options):
        path = options["path"]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CommandError(f"cannot read cohort JSON {path!r}: {exc}") from exc

        # The management command is the composition root — it injects the clock.
        settlement_ts = timezone.now()
        try:
            cohort = replace_cohort_v2(
                data,
                settlement_price=options["settlement_price"],
                settlement_ts=settlement_ts,
            )
        except Exception as exc:  # surfaced as a clean command error
            raise CommandError(f"cohort load failed: {exc}") from exc

        # Report what landed.
        from copytrade.models import CopyTradeSettings, CopytradeWallet

        settings = CopyTradeSettings.get()
        n_wallets = CopytradeWallet.objects.filter(cohort_id=cohort.cohort_id).count()
        heads = {}
        for w in CopytradeWallet.objects.filter(cohort_id=cohort.cohort_id):
            heads[w.strategy_id] = heads.get(w.strategy_id, 0) + 1

        self.stdout.write(self.style.SUCCESS(
            f"Loaded cohort '{cohort.cohort_id}' — mode={settings.mode} "
            f"usd_size=${settings.usd_size_per_trade} min_trigger=${settings.min_trigger_buy_usd} "
            f"wallets={n_wallets} heads={heads} engine_on={settings.engine_on}"
        ))
        self.stdout.write(
            "Engine remains OFF after load — flip it on with the dashboard/engine_control "
            "(observe/paper only; never set mode=live without operator sign-off)."
        )
