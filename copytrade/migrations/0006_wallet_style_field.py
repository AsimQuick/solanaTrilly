# ---
# module: copytrade.migrations.0006_wallet_style_field
# sprint: copytrade-2.1-loader
# story: copytrade-v2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: copytrade.migrations.0005_cohort_v2_fields
# ---
"""Add CopytradeWallet.style field for copytrade-2.1 per-wallet exit dispatch."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("copytrade", "0005_cohort_v2_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="copytradewallet",
            name="style",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=32,
                help_text=(
                    "Copytrade-2.1 per-wallet exit style: 'ride' (our_trailing) or "
                    "'scalp' (mirror_wallet_sell). Empty for pre-2.1 cohorts."
                ),
            ),
        ),
    ]
