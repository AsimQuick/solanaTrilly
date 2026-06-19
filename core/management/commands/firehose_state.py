# ---
# module: core.management.commands.firehose_state
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: django, tools.firehose_state
# ---
"""firehose_state — management-command wrapper for the PipelineState firehose toggle.

Thin wrapper around tools.firehose_state so the operator can run:

    docker compose run --rm web python manage.py firehose_state on
    docker compose run --rm web python manage.py firehose_state off
    docker compose run --rm web python manage.py firehose_state status

ONLY mutates PipelineState.firehose_active — NEVER scoring_enabled or
trading_enabled (capital safety).  Idempotent.
"""
from django.core.management.base import BaseCommand

from tools.firehose_state import apply_state, resolve_desired_state


class Command(BaseCommand):
    help = (
        "Toggle PipelineState.firehose_active (on|off|status). "
        "Never touches scoring_enabled or trading_enabled. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "state",
            nargs="?",
            default="status",
            help="on | off | status (default: status — print only).",
        )

    def handle(self, *args, **options):
        desired = resolve_desired_state([options.get("state") or "status"])
        result = apply_state(desired)
        verb = "status" if desired is None else desired
        self.stdout.write(
            self.style.SUCCESS(
                f"firehose_state {verb}: firehose_active={result}"
            )
        )
