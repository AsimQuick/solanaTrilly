# ---
# module: trading.migrations.0006_trading_settings_tp12tr15_t1200
# sprint: hotfix
# story: fix/model-exit-timer-1200
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: django, trading.migrations.0005_position_score
# ---
"""Data migration: apply tp12tr15_t1200 policy to the TradingSettings singleton.

Root cause: the live singleton (pk=1) had auto_sell_timer_s=300 (the old
field default), causing the settler's post-grad window to span only ~302s.
Any tape with sparse post-grad swaps exhausted the post window immediately,
so _resettle returned AUTO_SELL_TIMER at near-zero held time.

Policy source of truth: models/trilly_pregrad_v3_2/meta.json
  exit_policy: "tp12tr15_t1200"
  → take-profit +12%   (take_profit_pct = 12.0)
  → trailing/giveback 15% from peak  (rug_pull_drop_pct = 15.0  ← settler rugcut)
  → timer 1200s        (auto_sell_timer_s = 1200)

Field → pol dict mapping in trading.tape_settler._config_to_pol:
  pol["timer"]   = config.auto_sell_timer_s          (seconds; not divided by 100)
  pol["tp"]      = config.take_profit_pct   / 100.0  → 0.12 (12% gain)
  pol["sl"]      = config.stop_loss_pct     / 100.0  → 0.08 (8% loss guard)
  pol["disaster"]= config.disaster_cap_pct  / 100.0  → 0.12 (12% deep-drop guard)
  pol["rugcut"]  = config.rug_pull_drop_pct / 100.0  → 0.15 (15% trailing from peak)

NOTE: trailing_pct (10.0) is the live exit_engine's TRAILING rule arming
threshold (separate from the settler's rugcut).  The Pydantic invariant
requires trailing_pct < take_profit_pct; with tp=12.0 we set trailing_pct=10.0.

Pydantic ordering invariants preserved after migration:
  stop_loss_pct(8) < disaster_cap_pct(12) < rug_pull_drop_pct(15)  ✓
  trailing_pct(10) < take_profit_pct(12)                           ✓
  slippage: tight(800) < normal(1500) < loss(2500)                  ✓ (unchanged)
"""

from django.db import migrations


def apply_tp12tr15_t1200(apps, schema_editor):
    """Update the TradingSettings singleton (pk=1) to the tp12tr15_t1200 policy.

    Uses update_or_create to ensure idempotency: if pk=1 does not yet exist
    (e.g. a fresh DB where no singleton has been created), it will be inserted
    with the correct values.  On a live DB the existing row is updated.
    """
    TradingSettings = apps.get_model("trading", "TradingSettings")
    TradingSettings.objects.update_or_create(
        pk=1,
        defaults={
            # --- Primary fix: timer 300 → 1200 ---
            "auto_sell_timer_s": 1200,
            # --- tp12: take-profit at +12% ---
            "take_profit_pct": 12.0,
            # --- tr15: trailing/giveback 15% from peak (settler rugcut) ---
            "rug_pull_drop_pct": 15.0,
            # --- Supporting guards: must satisfy stop_loss < disaster < rug_pull ---
            # With rug_pull_drop_pct=15, we tighten these accordingly.
            "stop_loss_pct": 8.0,
            "disaster_cap_pct": 12.0,
            # --- Live exit_engine trailing: must be < take_profit_pct(12) ---
            "trailing_pct": 10.0,
        },
    )


def reverse_tp12tr15_t1200(apps, schema_editor):
    """Reverse: restore the singleton to the old defaults (pre-migration state)."""
    TradingSettings = apps.get_model("trading", "TradingSettings")
    TradingSettings.objects.update_or_create(
        pk=1,
        defaults={
            "auto_sell_timer_s": 300,
            "take_profit_pct": 100.0,
            "rug_pull_drop_pct": 50.0,
            "stop_loss_pct": 20.0,
            "disaster_cap_pct": 40.0,
            "trailing_pct": 20.0,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("trading", "0005_position_score"),
    ]

    operations = [
        migrations.RunPython(
            apply_tp12tr15_t1200,
            reverse_code=reverse_tp12tr15_t1200,
        ),
    ]
