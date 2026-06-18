# ---
# module: copytrade.models
# sprint: sprint-12
# story: US-58 AC-58.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, copytrade.schemas, pydantic
# ---
"""Django model for the copytrade.* config store (SPEC §2, §5, §7).

CopyTradeSettings is a singleton (pk=1) row that holds the live copy-trade
config. It is ENTIRELY SEPARATE from PipelineConfig / PipelineState — §5
isolation: no shared mutable state, no FK into the raw lake or core tables.

All writes are validated through CopyTradeConfig (Pydantic v2) at save time,
mirroring the US-10 write-path-gate pattern used by PipelineConfig.clean().
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError

from copytrade.schemas import CopyTradeConfig


class CopyTradeSettings(models.Model):
    """Singleton config row for the copy-trade engine (SPEC §2 + runtime state).

    Singleton contract: save() forces pk=1 so there is always at most one row.
    Use CopyTradeSettings.get() to retrieve (or create) the singleton.

    All SPEC §2 trade_config fields are stored as individual columns for clean
    DB-level introspection and Django admin support.  Runtime state fields
    (active_cohort_id, engine_on) share the same row.
    """

    # --- SPEC §2 trade_config fields ---
    mode = models.CharField(max_length=10, default="observe")
    sol_size_per_trade = models.FloatField(default=0.25)
    take_profit_pct = models.FloatField(default=200.0)
    stop_loss_pct = models.FloatField(default=40.0)
    exit_before_graduation = models.BooleanField(default=True)
    curve_completion_exit_pct = models.FloatField(default=90.0)
    max_hold_seconds = models.IntegerField(default=1800)
    max_concurrent_positions = models.IntegerField(default=20)
    copy_only_pumpfun_curve_buys = models.BooleanField(default=True)
    copy_first_buy_only = models.BooleanField(default=True)
    dedupe_token_across_wallets = models.BooleanField(default=True)
    mirror_wallet_sells = models.BooleanField(default=False)

    # --- Runtime state (not in the JSON — operational toggles) ---
    active_cohort_id = models.CharField(max_length=255, null=True, blank=True)
    engine_on = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "copytrade"
        db_table = "copytrade_config"

    def clean(self):
        """Validate the config through the Pydantic schema (AC-58.1 write-path gate).

        Mirrors PipelineConfig.clean() — raises DjangoValidationError on any
        CopyTradeConfig invariant violation so the row CANNOT be persisted.
        """
        try:
            CopyTradeConfig(
                mode=self.mode,
                sol_size_per_trade=self.sol_size_per_trade,
                take_profit_pct=self.take_profit_pct,
                stop_loss_pct=self.stop_loss_pct,
                exit_before_graduation=self.exit_before_graduation,
                curve_completion_exit_pct=self.curve_completion_exit_pct,
                max_hold_seconds=self.max_hold_seconds,
                max_concurrent_positions=self.max_concurrent_positions,
                copy_only_pumpfun_curve_buys=self.copy_only_pumpfun_curve_buys,
                copy_first_buy_only=self.copy_first_buy_only,
                dedupe_token_across_wallets=self.dedupe_token_across_wallets,
                mirror_wallet_sells=self.mirror_wallet_sells,
                active_cohort_id=self.active_cohort_id,
                engine_on=self.engine_on,
            )
        except PydanticValidationError as exc:
            raise DjangoValidationError(str(exc)) from exc

    def save(self, *args, **kwargs):
        """Enforce singleton (pk=1) and validate before persisting (AC-58.1)."""
        self.pk = 1
        kwargs.pop("force_insert", None)
        self.clean()
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> "CopyTradeSettings":
        """Return the singleton row, creating it with safe defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def to_schema(self) -> CopyTradeConfig:
        """Export the current row as a validated CopyTradeConfig instance."""
        return CopyTradeConfig(
            mode=self.mode,
            sol_size_per_trade=self.sol_size_per_trade,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            exit_before_graduation=self.exit_before_graduation,
            curve_completion_exit_pct=self.curve_completion_exit_pct,
            max_hold_seconds=self.max_hold_seconds,
            max_concurrent_positions=self.max_concurrent_positions,
            copy_only_pumpfun_curve_buys=self.copy_only_pumpfun_curve_buys,
            copy_first_buy_only=self.copy_first_buy_only,
            dedupe_token_across_wallets=self.dedupe_token_across_wallets,
            mirror_wallet_sells=self.mirror_wallet_sells,
            active_cohort_id=self.active_cohort_id,
            engine_on=self.engine_on,
        )
