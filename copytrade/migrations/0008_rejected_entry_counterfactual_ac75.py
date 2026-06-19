# ---
# module: copytrade.migrations.0008_rejected_entry_counterfactual_ac75
# sprint: US-75
# story: US-75 AC-2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: copytrade.migrations.0007_honest_fills_ac75
# ---
"""US-75 AC-2: rejected-entry counterfactual settlement fields.

Adds three columns to copytrade_positions (on ENTRY_REJECTED rows) that hold
the forward counterfactual: peak_return_pct, rug_outcome, forward_window_s.

Schema choice: fields on the existing position row rather than a separate
copytrade_rejected_entries table.  Both satisfy §8 P3; using the existing row
avoids a join and keeps all position-level data co-located.

Schema changes (copytrade_positions)
--------------------------------------
  peak_return_pct    FLOAT NULL  — best (peak/quote_price - 1) from rejection ts forward
  rug_outcome        BOOLEAN NULL — True if token dropped ≥50% from peak in the window
  forward_window_s   INTEGER NULL — actual seconds of tape consumed (bounded)

NULL on any of these means the settler has not run yet, or the tape was empty in
the forward window (outcome=None, per §8 P3 #304 guard — never a perpetual skip).

DoD §6.1: migrate passes in Docker CI.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0007_honest_fills_ac75"),
    ]

    operations = [
        migrations.AddField(
            model_name="copytradeposition",
            name="peak_return_pct",
            field=models.FloatField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-2: for ENTRY_REJECTED rows, best return (as fraction, "
                    "e.g. 0.5 = +50%) from rejection ts forward in the recorder tape. "
                    "NULL if settler has not run or tape was empty."
                ),
            ),
        ),
        migrations.AddField(
            model_name="copytradeposition",
            name="rug_outcome",
            field=models.BooleanField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-2: for ENTRY_REJECTED rows, True if token dropped "
                    ">=50% from peak in the forward window (rug signal). "
                    "NULL if settler has not run or tape was empty."
                ),
            ),
        ),
        migrations.AddField(
            model_name="copytradeposition",
            name="forward_window_s",
            field=models.IntegerField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-2: for ENTRY_REJECTED rows, actual seconds of tape "
                    "consumed by the counterfactual settler (bounded by "
                    "COUNTERFACTUAL_WINDOW_S). NULL if settler has not run."
                ),
            ),
        ),
    ]
