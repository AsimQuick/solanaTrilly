# ---
# module: trading.migrations.0008_data_null_sl_disaster_singleton
# sprint: hotfix
# story: fix/model-exit-faithful-tp12tr15
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: django, trading.migrations.0007_nullable_sl_disaster_faithful_tp12tr15
# ---
"""Data migration: null out stop_loss_pct and disaster_cap_pct on the singleton.

Root cause of fix/model-exit-faithful-tp12tr15:
  PR #355 (migration 0006) SET stop_loss_pct=8.0 and disaster_cap_pct=12.0 on
  the live singleton because the Pydantic invariant at the time REQUIRED all
  three of sl/disaster/rug to be non-None.

  The VALIDATED tp12tr15_t1200 policy has NO stop-loss and NO disaster cap:
    Source of truth: v31_winner_check.py line 15
    dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)

  With sl=8.0 active, every losing trade was cut at −8% instead of riding to
  the trailing stop (tr15) or the 1200s timer, which corrupted the model's
  observe measurement (the STOP_LOSS trigger fired that the model was never
  validated with).

Resolution:
  1. trading/schemas.py: stop_loss_pct / disaster_cap_pct are now Optional[float]
     = None; Pydantic invariant 2 only enforces ordering among NON-None values.
  2. trading/models.py: both columns are null=True, blank=True.
  3. This migration nulls the live singleton so the three active exits are:
       TAKE_PROFIT_PCT  (+12%)
       RUG_PULL trailing  (15% giveback from peak, armed at +5%)
       AUTO_SELL_TIMER  (1200s hard deadline)

Policy values preserved from 0006 (unchanged by this migration):
    take_profit_pct   = 12.0
    rug_pull_drop_pct = 15.0
    auto_sell_timer_s = 1200
"""

from django.db import migrations


def null_sl_disaster(apps, schema_editor):
    """Set stop_loss_pct=None and disaster_cap_pct=None on the singleton (pk=1).

    Idempotent: update() on a non-existent row is a no-op; if the singleton
    does not yet exist, the column defaults (null) already apply at create time.
    """
    TradingSettings = apps.get_model("trading", "TradingSettings")
    TradingSettings.objects.filter(pk=1).update(
        stop_loss_pct=None,
        disaster_cap_pct=None,
    )


def restore_sl_disaster(apps, schema_editor):
    """Reverse: restore the sl=8.0, disaster=12.0 values that 0006 set.

    This exactly reverts to the (incorrect) 0006 data state — correct for
    a migration rollback even though those values were wrong for the policy.
    """
    TradingSettings = apps.get_model("trading", "TradingSettings")
    TradingSettings.objects.filter(pk=1).update(
        stop_loss_pct=8.0,
        disaster_cap_pct=12.0,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("trading", "0007_nullable_sl_disaster_faithful_tp12tr15"),
    ]

    operations = [
        migrations.RunPython(
            null_sl_disaster,
            reverse_code=restore_sl_disaster,
        ),
    ]
