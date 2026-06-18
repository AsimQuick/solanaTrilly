# ---
# module: copytrade.models
# sprint: sprint-12
# story: US-58 AC-58.1, US-58 AC-58.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, copytrade.schemas, pydantic
# ---
"""Django models for the copytrade §5-isolated namespace (SPEC §2, §5, §7, §9).

CopyTradeSettings is a singleton config row.  The four copytrade_-prefixed
tables (cohort / wallets / positions / pnl_by_wallet) hold all copy-trade
domain data.

§5 ISOLATION: NONE of these models declare a ForeignKey into the raw lake
(RawEvent, Token) or the firehose control plane (PipelineConfig, PipelineState).
All inter-table references within the copytrade namespace use CharField cohort_id
(string key) to avoid Django cascade semantics complicating the US-62 fresh-start
purge.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError

from copytrade.schemas import CopyTradeConfig
from core.encoders import JsonSafeEncoder


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


# ---------------------------------------------------------------------------
# AC-58.2 — The four copytrade_-prefixed domain tables (SPEC §9)
# ---------------------------------------------------------------------------


class CopytradeCohort(models.Model):
    """One row per uploaded cohort (SPEC §2 / §9).

    cohort_id is the user-supplied string identifier from leaderboard.json (e.g.
    'whale-ct-2026-06-17-v1').  Exactly one row may have active=True at a time;
    the US-62 fresh-start lifecycle manages this invariant.

    trade_config stores the raw SPEC §2 trade_config dict as JSONB so the cohort
    JSON is reproduced faithfully.  The canonical typed version lives in
    CopyTradeSettings; this is the audit record.
    """

    cohort_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField()
    description = models.TextField(blank=True, default="")
    trade_config = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=False)

    class Meta:
        app_label = "copytrade"
        db_table = "copytrade_cohort"

    def __str__(self) -> str:
        return f"CopytradeCohort({self.cohort_id}, active={self.active})"


class CopytradeWallet(models.Model):
    """One row per wallet in the active cohort (SPEC §2 / §9).

    cohort_id is a VARCHAR reference to CopytradeCohort.cohort_id — no FK so
    that the US-62 purge can DELETE ... WHERE cohort_id=X without Django cascade
    semantics interfering.  rank / precision / median_lead_min are informational
    fields from the leaderboard JSON.
    """

    cohort_id = models.CharField(max_length=255, db_index=True)
    address = models.CharField(max_length=64)
    rank = models.IntegerField(null=True, blank=True)
    precision = models.FloatField(null=True, blank=True)
    median_lead_min = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "copytrade"
        db_table = "copytrade_wallets"
        indexes = [
            models.Index(fields=["cohort_id", "address"], name="ct_wallet_cohort_addr_idx"),
        ]

    def __str__(self) -> str:
        return f"CopytradeWallet({self.address[:8]}… cohort={self.cohort_id})"


class CopytradePosition(models.Model):
    """One row per copy-trade position opened by the engine (SPEC §4 / §9).

    status is open|closed.  mode mirrors CopyTradeSettings.mode at entry time so
    position history is self-contained.  exit fields are null until the position
    closes.  cohort_id is a VARCHAR reference (no FK) for the same reason as
    CopytradeWallet.
    """

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    ]

    MODE_OBSERVE = "observe"
    MODE_LIVE = "live"
    MODE_CHOICES = [
        (MODE_OBSERVE, "Observe"),
        (MODE_LIVE, "Live"),
    ]

    EXIT_TP = "TP"
    EXIT_SL = "SL"
    EXIT_CURVE = "CURVE"
    EXIT_TIMER = "TIMER"
    EXIT_REASON_CHOICES = [
        (EXIT_TP, "Take Profit"),
        (EXIT_SL, "Stop Loss"),
        (EXIT_CURVE, "Curve Completion"),
        (EXIT_TIMER, "Max Hold Timer"),
    ]

    cohort_id = models.CharField(max_length=255, db_index=True)
    mint = models.CharField(max_length=64, db_index=True)
    trigger_wallet = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_OBSERVE)

    entry_ts = models.DateTimeField(null=True, blank=True)
    entry_price = models.FloatField(null=True, blank=True)
    sol_in = models.FloatField(null=True, blank=True)

    exit_ts = models.DateTimeField(null=True, blank=True)
    exit_price = models.FloatField(null=True, blank=True)
    sol_out = models.FloatField(null=True, blank=True)
    exit_reason = models.CharField(max_length=10, choices=EXIT_REASON_CHOICES, null=True, blank=True)

    realized_pnl_sol = models.FloatField(null=True, blank=True)
    realized_pnl_pct = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "copytrade"
        db_table = "copytrade_positions"
        indexes = [
            models.Index(fields=["cohort_id", "status"], name="ct_pos_cohort_status_idx"),
        ]

    def __str__(self) -> str:
        return f"CopytradePosition({self.mint[:8]}… {self.status} cohort={self.cohort_id})"


class CopytradePnlByWallet(models.Model):
    """Per-wallet PnL rollup for the active cohort (SPEC §9, application-level rollup).

    This is a regular Django-managed table populated by the engine (US-61) after
    each position closes.  One row per (cohort_id, address) pair; upserted on
    every position close.

    Implementation choice (AC-58.2): application-level rollup rather than a DB
    view, so migrations stay simple and the rollup can be tested without DDL
    view creation.
    """

    cohort_id = models.CharField(max_length=255, db_index=True)
    address = models.CharField(max_length=64)
    n_trades = models.IntegerField(default=0)
    win_rate = models.FloatField(default=0.0)
    total_pnl_sol = models.FloatField(default=0.0)
    avg_hold_s = models.FloatField(default=0.0)

    class Meta:
        app_label = "copytrade"
        db_table = "copytrade_pnl_by_wallet"
        unique_together = [("cohort_id", "address")]
        indexes = [
            models.Index(fields=["cohort_id", "address"], name="ct_pnl_cohort_addr_idx"),
        ]

    def __str__(self) -> str:
        return f"CopytradePnlByWallet({self.address[:8]}… cohort={self.cohort_id})"
