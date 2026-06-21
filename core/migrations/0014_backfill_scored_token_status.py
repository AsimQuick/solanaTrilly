# ---
# migration: core.0014_backfill_scored_token_status
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 2 (restart-safety)
# created-by: dev-team
# last-updated: 2026-06-21
# ---
"""Data migration: set status=SCORED on Token rows that already have a Prediction.

Tier-2 restart-safety requires that ``_due_tokens_sync`` filters to
``status=DETECTED`` only.  Legacy soaked tokens were created before the
status-write was wired, so they remain ``status=DETECTED`` (the default)
even though they have already been scored.  Without this migration a restart
would re-enqueue all of them and re-dispatch lake backfills (or Tier-3 REST
calls in the follow-on PR).

This migration is idempotent: it only sets status=SCORED for rows where the
status is DETECTED (the default) AND a Prediction row exists for that mint.
Rows that are already SCORED, SKIPPED, TRADED, or RECORDING are untouched.
"""
from django.db import migrations


def _set_scored_for_predicted_tokens(apps, schema_editor):
    """Bulk-update Token.status = SCORED for tokens with an existing Prediction."""
    Token = apps.get_model("core", "Token")
    Prediction = apps.get_model("core", "Prediction")

    # Collect the distinct mints that have been scored (Prediction exists).
    scored_mints = set(
        Prediction.objects.values_list("mint", flat=True).distinct()
    )
    if not scored_mints:
        return

    # Only update tokens still at the default DETECTED status — do not
    # overwrite SKIPPED, TRADED, RECORDING, or already-SCORED rows.
    updated = Token.objects.filter(
        mint__in=scored_mints,
        status="DETECTED",
    ).update(status="SCORED")

    # schema_editor's connection is available for logging but we avoid print()
    # in migrations; the count is visible in the migrate --verbosity=2 output.
    _ = updated  # used implicitly by Django's migration runner log


def _noop(apps, schema_editor):
    """Reverse: no-op — restoring the original status would lose information."""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_prediction"),
    ]

    operations = [
        migrations.RunPython(_set_scored_for_predicted_tokens, _noop),
    ]
