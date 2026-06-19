# ---
# module: copytrade.migrations.0007_honest_fills_ac75
# sprint: US-75
# story: US-75 AC-1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: copytrade.migrations.0006_wallet_style_field
# ---
"""US-75 AC-1: honest fills telemetry fields + ENTRY_REJECTED exit reason +
honest_fills_enabled feature flag.

Schema changes
--------------
CopytradePosition (copytrade_positions):
  - quote_price      FLOAT NULL  — watched wallet's confirmed fill price
  - fill_price       FLOAT NULL  — our estimated fill at detection time
  - cap_pct          FLOAT NULL  — slippage cap fraction applied (e.g. 0.15)
  - copy_latency_s   FLOAT NULL  — clock_arrival − on-chain block_time (seconds)
  - ENTRY_REJECTED added to exit_reason choices (choices-only change, no DDL)

CopyTradeSettings (copytrade_config):
  - honest_fills_enabled  BOOLEAN NOT NULL DEFAULT FALSE  (§6.7 feature flag)

DoD §6.1: Django `migrate` passes in Docker CI.
DoD §6.7: Default False — the running v1/v2 soak is not disrupted at merge.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0006_wallet_style_field"),
    ]

    operations = [
        # --- CopytradePosition: new telemetry columns ---
        migrations.AddField(
            model_name="copytradeposition",
            name="quote_price",
            field=models.FloatField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-1: watched wallet's confirmed fill price (the 'quote'). "
                    "Populated when honest_fills_enabled=True."
                ),
            ),
        ),
        migrations.AddField(
            model_name="copytradeposition",
            name="fill_price",
            field=models.FloatField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-1: our estimated fill price at detection time "
                    "(the AMM/curve state at wallet_buy_ts + copy_latency). "
                    "Populated when honest_fills_enabled=True."
                ),
            ),
        ),
        migrations.AddField(
            model_name="copytradeposition",
            name="cap_pct",
            field=models.FloatField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-1: slippage cap fraction applied to this entry "
                    "(e.g. 0.15 for 15%). Populated when honest_fills_enabled=True."
                ),
            ),
        ),
        migrations.AddField(
            model_name="copytradeposition",
            name="copy_latency_s",
            field=models.FloatField(
                null=True,
                blank=True,
                help_text=(
                    "US-75 AC-1: seconds from on-chain block_time to our clock "
                    "arrival. None when block_time was absent from the Helius event. "
                    "Populated when honest_fills_enabled=True."
                ),
            ),
        ),
        # --- CopytradePosition: ENTRY_REJECTED exit reason ---
        # "ENTRY_REJECTED" is 14 chars > varchar(10) — must ALTER the column.
        # Unlike prior SeparateDatabaseAndState migrations (which only added choices
        # without changing length), this one DOES issue DDL to widen to varchar(15).
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
                    ("TRAIL", "Trailing Giveback"),
                    ("MIRROR", "Mirror Wallet Sell"),
                    ("ENTRY_REJECTED", "Entry Rejected (Slippage Cap)"),
                ],
                max_length=15,
                null=True,
            ),
        ),
        # --- CopyTradeSettings: honest_fills_enabled feature flag (§6.7) ---
        migrations.AddField(
            model_name="copytradesettings",
            name="honest_fills_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "US-75 AC-1: when True, apply honest-fill slippage cap to "
                    "OBSERVE copy entries and emit ENTRY_REJECTED rows. "
                    "Default False — the running soak is not disrupted at merge. "
                    "Flip deliberately for the AC-5 soak (§6.7)."
                ),
            ),
        ),
    ]
