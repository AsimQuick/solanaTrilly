# ---
# module: trading.models
# sprint: sprint-13
# story: US-64 AC-64.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, trading.schemas, pydantic, core.encoders
# ---
"""Django models for the trading.* config namespace (PRD §10/§17, AC-64.1).

TradingSettings is a singleton config row holding all §10/§17 trade knobs.

§5 ISOLATION: This model does NOT declare any ForeignKey into the raw lake
(RawEvent, Token) or the copy-trade namespace (copytrade_*).

Singleton contract: save() forces pk=1 so there is always at most one row.
Write-path invariant gate: clean() validates through TradingConfig Pydantic
schema — invalid configs raise DjangoValidationError and CANNOT be persisted
(US-10 write-path pattern / AC-64.1).

trading_enabled DEFAULTS False — the observe/paper-first safety gate.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError

from core.encoders import JsonSafeEncoder
from trading.schemas import TradingConfig


def _default_panic_bps() -> list:
    """Module-level callable default for slippage_panic_bps JSONField.

    Returns the PRD §10.1 PANIC tier: [5000, 7000, 9000, 9900] bps.
    Must be a module-level function (not a lambda) so Django migrations can
    reference it by dotted path.
    """
    return [5000, 7000, 9000, 9900]


class TradingSettings(models.Model):
    """Singleton config row for the shared trading execution apparatus (PRD §10/§17).

    Singleton contract: save() forces pk=1 so there is always at most one row.
    Use TradingSettings.get() to retrieve (or create) the singleton.

    All PRD §10/§17 trade knobs are stored as individual columns for clean
    DB-level introspection and Django admin support.

    trading_enabled DEFAULTS False — the observe/paper-first safety gate.
    NO boot/resolver/upload/ON path may flip this True (AST-guarded in tests).
    """

    # --- Master on/off gate (DEFAULT False — observe/paper safety gate) ---
    trading_enabled = models.BooleanField(default=False)

    # --- Position sizing ---
    position_size_sol = models.FloatField(default=0.1)
    max_open_positions = models.IntegerField(default=3)

    # --- Slippage tiers (PRD §10.1): TIGHT / NORMAL / LOSS / PANIC ---
    slippage_tight_bps = models.IntegerField(default=800)
    slippage_normal_bps = models.IntegerField(default=1500)
    slippage_loss_bps = models.IntegerField(default=2500)
    slippage_panic_bps = models.JSONField(
        default=_default_panic_bps,
        encoder=JsonSafeEncoder,
    )

    # --- Exit rule parameters (PRD §10.1 priority ladder) ---
    take_profit_pct = models.FloatField(default=100.0)
    stop_loss_pct = models.FloatField(default=20.0)
    disaster_cap_pct = models.FloatField(default=40.0)
    rug_pull_drop_pct = models.FloatField(default=50.0)
    next_poll_guard_s = models.IntegerField(default=10)
    auto_sell_timer_s = models.IntegerField(default=300)

    # --- Trailing stop parameters ---
    trailing_pct = models.FloatField(default=20.0)
    trailing_arm_multiple = models.FloatField(default=1.05)
    trailing_grace_s = models.IntegerField(default=60)

    # --- CEILING / VOLUME_COLLAPSE / CONCENTRATION thresholds ---
    ceiling_pct = models.FloatField(default=300.0)
    volume_collapse_threshold_pct = models.FloatField(default=10.0)
    concentration_threshold_pct = models.FloatField(default=40.0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trading"
        db_table = "trading_config"

    def clean(self):
        """Validate the config through the Pydantic schema (AC-64.1 write-path gate).

        Mirrors CopyTradeSettings.clean() / PipelineConfig.clean() — raises
        DjangoValidationError on any TradingConfig invariant violation so the
        row CANNOT be persisted with invalid data.
        """
        try:
            TradingConfig(
                trading_enabled=self.trading_enabled,
                position_size_sol=self.position_size_sol,
                max_open_positions=self.max_open_positions,
                slippage_tight_bps=self.slippage_tight_bps,
                slippage_normal_bps=self.slippage_normal_bps,
                slippage_loss_bps=self.slippage_loss_bps,
                slippage_panic_bps=self.slippage_panic_bps,
                take_profit_pct=self.take_profit_pct,
                stop_loss_pct=self.stop_loss_pct,
                disaster_cap_pct=self.disaster_cap_pct,
                rug_pull_drop_pct=self.rug_pull_drop_pct,
                next_poll_guard_s=self.next_poll_guard_s,
                auto_sell_timer_s=self.auto_sell_timer_s,
                trailing_pct=self.trailing_pct,
                trailing_arm_multiple=self.trailing_arm_multiple,
                trailing_grace_s=self.trailing_grace_s,
                ceiling_pct=self.ceiling_pct,
                volume_collapse_threshold_pct=self.volume_collapse_threshold_pct,
                concentration_threshold_pct=self.concentration_threshold_pct,
            )
        except PydanticValidationError as exc:
            raise DjangoValidationError(str(exc)) from exc

    def save(self, *args, **kwargs):
        """Enforce singleton (pk=1) and validate before persisting (AC-64.1)."""
        self.pk = 1
        kwargs.pop("force_insert", None)
        self.clean()
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> "TradingSettings":
        """Return the singleton row, creating it with safe defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def to_schema(self) -> TradingConfig:
        """Export the current row as a validated TradingConfig instance."""
        return TradingConfig(
            trading_enabled=self.trading_enabled,
            position_size_sol=self.position_size_sol,
            max_open_positions=self.max_open_positions,
            slippage_tight_bps=self.slippage_tight_bps,
            slippage_normal_bps=self.slippage_normal_bps,
            slippage_loss_bps=self.slippage_loss_bps,
            slippage_panic_bps=self.slippage_panic_bps,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            disaster_cap_pct=self.disaster_cap_pct,
            rug_pull_drop_pct=self.rug_pull_drop_pct,
            next_poll_guard_s=self.next_poll_guard_s,
            auto_sell_timer_s=self.auto_sell_timer_s,
            trailing_pct=self.trailing_pct,
            trailing_arm_multiple=self.trailing_arm_multiple,
            trailing_grace_s=self.trailing_grace_s,
            ceiling_pct=self.ceiling_pct,
            volume_collapse_threshold_pct=self.volume_collapse_threshold_pct,
            concentration_threshold_pct=self.concentration_threshold_pct,
        )
