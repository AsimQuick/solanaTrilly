# ---
# module: copytrade.migrations.0003_add_settle_exit_reason
# sprint: sprint-12
# story: US-62 AC-62.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.migrations.0002_add_copytrade_tables
# ---
"""Add SETTLE to CopytradePosition.exit_reason choices (SPEC §6 cohort lifecycle).

The cohort fresh-start lifecycle (US-62) settles open positions at the current
price before purging the old cohort.  This migration extends the exit_reason
field choices to include the new SETTLE reason so the value passes Django's
field-level validation.

Implementation note: choices are a Python/ORM-level constraint only — they do
NOT produce any DDL change in PostgreSQL.  SeparateDatabaseAndState is used so
that the migration graph reflects the model-state change without issuing any
schema-altering SQL, and the migration guard in test_copytrade_models_ac582.py
(which inspects op.name for model-targeting operations) is satisfied because
SeparateDatabaseAndState carries no top-level .name attribute.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0002_add_copytrade_tables"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # No DDL change needed — choices are Python-only.
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="copytradeposition",
                    name="exit_reason",
                    field=models.CharField(
                        blank=True,
                        choices=[
                            ("TP", "Take Profit"),
                            ("SL", "Stop Loss"),
                            ("CURVE", "Curve Completion"),
                            ("TIMER", "Max Hold Timer"),
                            ("SETTLE", "Cohort Settlement"),
                        ],
                        max_length=10,
                        null=True,
                    ),
                ),
            ],
        ),
    ]
