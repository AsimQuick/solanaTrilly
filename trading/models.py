# ---
# module: trading.models
# sprint: sprint-13, sprint-14
# story: US-64 AC-64.1, US-64 AC-64.2, US-66 AC-66.1, US-67 AC-67.1, US-72 AC-72.1
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-19
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
    stale_timeout_s = models.IntegerField(default=1800)

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
                stale_timeout_s=self.stale_timeout_s,
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
            stale_timeout_s=self.stale_timeout_s,
            trailing_pct=self.trailing_pct,
            trailing_arm_multiple=self.trailing_arm_multiple,
            trailing_grace_s=self.trailing_grace_s,
            ceiling_pct=self.ceiling_pct,
            volume_collapse_threshold_pct=self.volume_collapse_threshold_pct,
            concentration_threshold_pct=self.concentration_threshold_pct,
        )


class Position(models.Model):
    """Unified position row — the SHARED record both pipelines write (AC-64.2).

    Both the model-prediction pipeline (source='model') and the copy-trade
    engine (source='copytrade') write rows here.  The exit/settlement fields
    are populated by the tape settler (US-66) when the position closes.

    sentinel: closed_at IS NOT NULL  ↔  position settled/closed (AC-66.3).
    """

    SOURCE_MODEL = "model"
    SOURCE_COPYTRADE = "copytrade"
    SOURCE_CHOICES = [
        (SOURCE_MODEL, "Model prediction pipeline"),
        (SOURCE_COPYTRADE, "Copy-trade engine"),
    ]

    MODE_OBSERVE = "observe"
    MODE_LIVE = "live"
    MODE_CHOICES = [
        (MODE_OBSERVE, "Observe (paper, no capital)"),
        (MODE_LIVE, "Live (capital at risk)"),
    ]

    STATUS_PAPER = "PAPER"
    STATUS_OPEN = "OPEN"
    STATUS_CLOSED = "CLOSED"
    STATUS_CHOICES = [
        (STATUS_PAPER, "Paper / sandbox position"),
        (STATUS_OPEN, "Open live position"),
        (STATUS_CLOSED, "Closed position"),
    ]

    mint = models.CharField(max_length=64, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES)

    # --- Prediction score (null for copytrade positions with no model score) ---
    score = models.FloatField(null=True, blank=True)

    # --- Entry fields (always populated at open time) ---
    entry_ts = models.DateTimeField()
    entry_price = models.FloatField()
    size_sol = models.FloatField()

    # --- Exit / settlement fields (null until position closes) ---
    exit_ts = models.DateTimeField(null=True, blank=True)
    exit_price = models.FloatField(null=True, blank=True)
    exit_trigger = models.CharField(max_length=32, null=True, blank=True)
    realized_pnl_sol = models.FloatField(null=True, blank=True)
    realized_pnl_pct = models.FloatField(null=True, blank=True)
    peak_price = models.FloatField(null=True, blank=True)

    # closed_at IS NOT NULL ↔ settled/closed (AC-66.3 sentinel)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trading"
        db_table = "trading_positions"
        indexes = [
            models.Index(fields=["source", "status"]),
        ]

    def __str__(self):
        return f"Position({self.mint[:8]}… src={self.source} status={self.status})"


class ReplayPosition(models.Model):
    """Replay sandbox position — written ONLY by the T2 replay harness (AC-67.1).

    This model uses db_table='trading_replay_positions', which is NEVER
    'trading_positions' (the live table).  The harness writes here; the live
    pipeline writes to Position.  Isolation is a hard invariant (§11.3).

    replay_run_id: opaque string identifying one harness run (e.g. a UUID).
    score: the prediction score for this mint from the injected predictor.
    enterable: False when simulate_tape_exit returns an un-enterable result —
        the row is still written so the run is fully auditable, but all
        PnL/trigger/held fields are null.
    """

    # --- Replay run identifier ---
    replay_run_id = models.CharField(max_length=64, db_index=True)

    # --- Token identification ---
    mint = models.CharField(max_length=64, db_index=True)
    score = models.FloatField()

    # --- Entry fields (null only when un-enterable) ---
    entry_ts = models.DateTimeField(null=True, blank=True)
    entry_price = models.FloatField(null=True, blank=True)
    size_sol = models.FloatField()

    # --- Enterability ---
    enterable = models.BooleanField(default=False)
    unentered_reason = models.CharField(max_length=32, null=True, blank=True)

    # --- Settlement fields (null when un-enterable) ---
    exit_trigger = models.CharField(max_length=32, null=True, blank=True)
    realized_pnl_pct = models.FloatField(null=True, blank=True)
    peak_pct = models.FloatField(null=True, blank=True)
    held_s = models.FloatField(null=True, blank=True)
    flow_usd = models.FloatField(null=True, blank=True)
    exit_price = models.FloatField(null=True, blank=True)
    peak_price_abs = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "trading"
        db_table = "trading_replay_positions"
        indexes = [
            models.Index(fields=["replay_run_id", "mint"]),
        ]

    def __str__(self):
        return (
            f"ReplayPosition(run={self.replay_run_id[:8]}… "
            f"mint={self.mint[:8]}… enterable={self.enterable})"
        )
