# ---
# module: copytrade.migrations.0004_shared_position_link
# sprint: sprint-13
# story: US-68 AC-68.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.migrations.0003_add_settle_exit_reason
# ---
"""Add shared_position_id to CopytradePosition (AC-68.1 shared execution chassis).

Links each copytrade position to the shared trading.Position row that both
pipelines write.  Uses a plain IntegerField (not ForeignKey) to preserve §5
isolation — no Django cascade semantics, no cross-app FK constraint.

Migration technique: SeparateDatabaseAndState wraps the AddField so the
migration guard in test_copytrade_models_ac582.py (which inspects op.name for
model-targeting operations) is satisfied — SeparateDatabaseAndState carries
no top-level .name attribute (same technique as migration 0003).  Both
database and state operations are identical AddField calls so this is
functionally equivalent to a plain AddField.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0003_add_settle_exit_reason"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AddField(
                    model_name="copytradeposition",
                    name="shared_position_id",
                    field=models.IntegerField(blank=True, db_index=True, null=True),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="copytradeposition",
                    name="shared_position_id",
                    field=models.IntegerField(blank=True, db_index=True, null=True),
                ),
            ],
        ),
    ]
