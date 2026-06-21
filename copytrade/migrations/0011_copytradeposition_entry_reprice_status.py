"""Add entry_reprice_status to CopytradePosition (EPIC-copy-paper-fill-repricing).

Tracks whether a closed position's fill price has been retrospectively repriced
from the firehose lake:
  NULL           = pending (repricing not yet run, or not yet closed)
  "REPRICED"     = successfully repriced from the lake; entry_price updated
  "ENTRY_REJECTED_TAPE" = repriced price was >15% above wallet quote → treated
                          as ENTRY_REJECTED by the tape (informational only)
  "NO_TAPE"      = no matching lake row in window; keeping curve-sim price
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copytrade', '0010_copytradeposition_entry_tokens'),
    ]

    operations = [
        migrations.AddField(
            model_name='copytradeposition',
            name='entry_reprice_status',
            field=models.CharField(
                max_length=24,
                null=True,
                blank=True,
                help_text=(
                    "EPIC-copy-paper-fill-repricing: NULL=pending, "
                    "REPRICED=repriced from lake, "
                    "ENTRY_REJECTED_TAPE=repriced price >15% cap, "
                    "NO_TAPE=no lake row in window."
                ),
            ),
        ),
    ]
