"""Add the VOID exit_reason choice (orphan reconcile).

Choices are not enforced at the DB level, so this is a state-only AlterField —
no column change.  Kept minimal on purpose: the pre-existing help_text drift on
other US-75 fields is intentionally NOT bundled here.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copytrade', '0008_rejected_entry_counterfactual_ac75'),
    ]

    operations = [
        migrations.AlterField(
            model_name='copytradeposition',
            name='exit_reason',
            field=models.CharField(
                blank=True,
                choices=[
                    ('TP', 'Take Profit'),
                    ('SL', 'Stop Loss'),
                    ('CURVE', 'Curve Completion'),
                    ('TIMER', 'Max Hold Timer'),
                    ('SETTLE', 'Cohort Settlement'),
                    ('TRAIL', 'Trailing Giveback'),
                    ('MIRROR', 'Mirror Wallet Sell'),
                    ('ENTRY_REJECTED', 'Entry Rejected (Slippage Cap)'),
                    ('VOID', 'Voided (orphaned open, superseded)'),
                ],
                max_length=15,
                null=True,
            ),
        ),
    ]
