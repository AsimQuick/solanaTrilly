# ---
# module: trading.schemas
# sprint: sprint-13
# story: US-64 AC-64.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pydantic>=2.0
# ---
"""Pydantic v2 schema for the trading.* config namespace (PRD §10/§17, AC-64.1).

TradingConfig holds ALL §10/§17 trade knobs (slippage tiers TIGHT/NORMAL/LOSS/PANIC,
TP/SL, exit-rule params, sizing, trading_enabled) for the SHARED execution apparatus.

This is the SEPARATE config schema for the trading execution layer.
It is NOT folded into PipelineConfig — §5 'no shared mutable state'.

trading_enabled DEFAULTS False — the observe/paper-first safety gate (AC-64.1 DoD).
No boot/resolver/upload/ON path may flip it True (enforced by the AST guard test).

Save-time invariants enforced at construction:
  1. slippage order: tight < normal < loss (ascending strictness)
  2. priority ordering: stop_loss_pct < disaster_cap_pct < rug_pull_drop_pct
  3. trailing_pct < take_profit_pct (trailing can never equal/exceed TP)

Exit priority ladder (PRD §10.1 / US-66 AC-66.1):
  RUG_PULL → DISASTER_CAP → STOP_LOSS → NEXT_POLL_GUARD → TAKE_PROFIT_PCT
  → AUTO_SELL_TIMER → CEILING/VOLUME_COLLAPSE/CONCENTRATION → TRAILING

Slippage tiers (PRD §10.1):
  TIGHT 800 bps / NORMAL 1500 bps / LOSS 2500 bps / PANIC [5000, 7000, 9000, 9900] bps
"""

from pydantic import BaseModel, Field, model_validator


class TradingConfig(BaseModel):
    """Validated config schema for the shared trading execution apparatus.

    All fields map directly to PRD §10/§17 trade knobs.
    Default trading_enabled=False is the observe/paper-first safety gate.
    """

    # --- Master on/off gate (DEFAULT False — observe/paper safety gate) ---
    trading_enabled: bool = False

    # --- Position sizing ---
    position_size_sol: float = Field(default=0.1, gt=0)
    max_open_positions: int = Field(default=3, gt=0)

    # --- Slippage tiers (PRD §10.1): TIGHT / NORMAL / LOSS / PANIC ---
    slippage_tight_bps: int = Field(default=800, ge=0)
    slippage_normal_bps: int = Field(default=1500, ge=0)
    slippage_loss_bps: int = Field(default=2500, ge=0)
    slippage_panic_bps: list[int] = Field(
        default_factory=lambda: [5000, 7000, 9000, 9900]
    )

    # --- Exit rule parameters (PRD §10.1 priority ladder) ---
    take_profit_pct: float = Field(default=100.0, gt=0)
    stop_loss_pct: float = Field(default=20.0, gt=0, le=100)
    disaster_cap_pct: float = Field(default=40.0, gt=0, le=100)
    rug_pull_drop_pct: float = Field(default=50.0, gt=0, le=100)
    next_poll_guard_s: int = Field(default=10, ge=0)
    auto_sell_timer_s: int = Field(default=300, gt=0)

    # --- Trailing stop parameters ---
    trailing_pct: float = Field(default=20.0, gt=0, le=100)
    trailing_arm_multiple: float = Field(default=1.05, gt=1.0)
    trailing_grace_s: int = Field(default=60, ge=0)

    # --- CEILING / VOLUME_COLLAPSE / CONCENTRATION thresholds ---
    ceiling_pct: float = Field(default=300.0, gt=0)
    volume_collapse_threshold_pct: float = Field(default=10.0, gt=0, le=100)
    concentration_threshold_pct: float = Field(default=40.0, gt=0, le=100)

    @model_validator(mode="after")
    def check_invariants(self) -> "TradingConfig":
        """Enforce save-time invariants (PRD §10.1 / AC-64.1 write-path gate).

        1. Slippage ascending: tight < normal < loss (tighter entries get cheaper fills)
        2. Loss priority ordering: stop_loss < disaster_cap < rug_pull
           (stop_loss fires first; rug_pull is the harshest/last absolute drop guard)
        3. Trailing stop must not equal or exceed take-profit (would never arm correctly)
        """
        # Invariant 1: slippage tiers must be strictly ascending
        if not (self.slippage_tight_bps < self.slippage_normal_bps < self.slippage_loss_bps):
            raise ValueError(
                f"Slippage tiers must be strictly ascending: "
                f"tight ({self.slippage_tight_bps} bps) < "
                f"normal ({self.slippage_normal_bps} bps) < "
                f"loss ({self.slippage_loss_bps} bps). "
                "Got ordering violation — check PRD §10.1."
            )

        # Invariant 2: exit priority ordering (lower % fires first in the ladder)
        if not (self.stop_loss_pct < self.disaster_cap_pct < self.rug_pull_drop_pct):
            raise ValueError(
                f"Exit priority ordering must be: "
                f"stop_loss_pct ({self.stop_loss_pct}) < "
                f"disaster_cap_pct ({self.disaster_cap_pct}) < "
                f"rug_pull_drop_pct ({self.rug_pull_drop_pct}). "
                "Lower percentages fire earlier in the exit priority ladder (PRD §10.1)."
            )

        # Invariant 3: trailing stop must be strictly less than take-profit
        if self.trailing_pct >= self.take_profit_pct:
            raise ValueError(
                f"trailing_pct ({self.trailing_pct}) must be strictly less than "
                f"take_profit_pct ({self.take_profit_pct}). "
                "A trailing stop at or above TP would never arm correctly."
            )

        return self
