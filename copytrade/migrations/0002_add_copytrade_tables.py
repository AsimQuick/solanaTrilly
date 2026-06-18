# ---
# module: copytrade.migrations.0002_add_copytrade_tables
# sprint: sprint-12
# story: US-58 AC-58.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, copytrade.migrations.0001_initial
# ---
"""Add four copytrade_-prefixed domain tables (SPEC §9).

Tables created:
  copytrade_cohort        — one row per uploaded cohort
  copytrade_wallets       — one row per wallet in the cohort
  copytrade_positions     — one row per copy-trade position opened by the engine
  copytrade_pnl_by_wallet — per-wallet PnL rollup (application-level)

§5 ISOLATION: no FK into RawEvent / tokens / PipelineConfig / PipelineState.
"""

from django.db import migrations, models

import core.encoders


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CopytradeCohort",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cohort_id", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField()),
                ("description", models.TextField(blank=True, default="")),
                ("trade_config", models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("active", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "copytrade_cohort",
            },
        ),
        migrations.CreateModel(
            name="CopytradeWallet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cohort_id", models.CharField(db_index=True, max_length=255)),
                ("address", models.CharField(max_length=64)),
                ("rank", models.IntegerField(blank=True, null=True)),
                ("precision", models.FloatField(blank=True, null=True)),
                ("median_lead_min", models.FloatField(blank=True, null=True)),
            ],
            options={
                "db_table": "copytrade_wallets",
            },
        ),
        migrations.CreateModel(
            name="CopytradePosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cohort_id", models.CharField(db_index=True, max_length=255)),
                ("mint", models.CharField(db_index=True, max_length=64)),
                ("trigger_wallet", models.CharField(max_length=64)),
                ("status", models.CharField(
                    choices=[("open", "Open"), ("closed", "Closed")],
                    default="open",
                    max_length=10,
                )),
                ("mode", models.CharField(
                    choices=[("observe", "Observe"), ("live", "Live")],
                    default="observe",
                    max_length=10,
                )),
                ("entry_ts", models.DateTimeField(blank=True, null=True)),
                ("entry_price", models.FloatField(blank=True, null=True)),
                ("sol_in", models.FloatField(blank=True, null=True)),
                ("exit_ts", models.DateTimeField(blank=True, null=True)),
                ("exit_price", models.FloatField(blank=True, null=True)),
                ("sol_out", models.FloatField(blank=True, null=True)),
                ("exit_reason", models.CharField(
                    blank=True,
                    choices=[
                        ("TP", "Take Profit"),
                        ("SL", "Stop Loss"),
                        ("CURVE", "Curve Completion"),
                        ("TIMER", "Max Hold Timer"),
                    ],
                    max_length=10,
                    null=True,
                )),
                ("realized_pnl_sol", models.FloatField(blank=True, null=True)),
                ("realized_pnl_pct", models.FloatField(blank=True, null=True)),
            ],
            options={
                "db_table": "copytrade_positions",
            },
        ),
        migrations.CreateModel(
            name="CopytradePnlByWallet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cohort_id", models.CharField(db_index=True, max_length=255)),
                ("address", models.CharField(max_length=64)),
                ("n_trades", models.IntegerField(default=0)),
                ("win_rate", models.FloatField(default=0.0)),
                ("total_pnl_sol", models.FloatField(default=0.0)),
                ("avg_hold_s", models.FloatField(default=0.0)),
            ],
            options={
                "db_table": "copytrade_pnl_by_wallet",
            },
        ),
        migrations.AddIndex(
            model_name="copytradeposition",
            index=models.Index(fields=["cohort_id", "status"], name="ct_pos_cohort_status_idx"),
        ),
        migrations.AddIndex(
            model_name="copytradepnlbywallet",
            index=models.Index(fields=["cohort_id", "address"], name="ct_pnl_cohort_addr_idx"),
        ),
        migrations.AddIndex(
            model_name="copytradewallet",
            index=models.Index(fields=["cohort_id", "address"], name="ct_wallet_cohort_addr_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="copytradepnlbywallet",
            unique_together={("cohort_id", "address")},
        ),
    ]
