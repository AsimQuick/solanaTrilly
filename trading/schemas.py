# ---
# module: trading.schemas
# sprint: sprint-13, hotfix, feat/copy-live-exec-curve-ix, fix/model-exit-faithful-tp12tr15
# story: US-64 AC-64.1, US-66 AC-66.1, fix/model-exit-timer-1200, copy-live-exec
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
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
     — ONLY enforced for fields that are NOT None; None means "disabled" (no guard fires).
     The validated tp12tr15_t1200 policy (lab source of truth) has sl=None, disaster=None.
  3. trailing_pct < take_profit_pct (trailing can never equal/exceed TP)

Exit priority ladder (PRD §10.1 / US-66 AC-66.1):
  RUG_PULL → DISASTER_CAP → STOP_LOSS → NEXT_POLL_GUARD → TAKE_PROFIT_PCT
  → AUTO_SELL_TIMER → CEILING/VOLUME_COLLAPSE/CONCENTRATION → TRAILING

Slippage tiers (PRD §10.1):
  TIGHT 800 bps / NORMAL 1500 bps / LOSS 2500 bps / PANIC [5000, 7000, 9000, 9900] bps

Lab policy source of truth (v31_winner_check.py line 15):
  dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)
  — NO stop_loss, NO disaster_cap; only TP, trailing-giveback (rugcut), and timer.
"""

from typing import Optional

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

    # --- Live budget kill-switch (copy-live-exec) ---
    # max_daily_spend_sol:     Hard cap on SOL spent on LIVE buys per calendar day
    #                          (UTC date). ExecutionCore.execute_buy refuses if the
    #                          sum of sol_in for today's live positions would exceed
    #                          this. Default 0.04 SOL — conservative first-send cap.
    # max_open_live_positions: Hard cap on simultaneously open LIVE positions.
    #                          ExecutionCore.execute_buy refuses if there are already
    #                          this many open live CopytradePositions.
    #                          These are the BUDGET KILL-SWITCH guards — never bypassed.
    max_daily_spend_sol: float = Field(default=0.04, gt=0)
    max_open_live_positions: int = Field(default=2, gt=0)

    # --- Slippage tiers (PRD §10.1): TIGHT / NORMAL / LOSS / PANIC ---
    slippage_tight_bps: int = Field(default=800, ge=0)
    slippage_normal_bps: int = Field(default=1500, ge=0)
    slippage_loss_bps: int = Field(default=2500, ge=0)
    slippage_panic_bps: list[int] = Field(
        default_factory=lambda: [5000, 7000, 9000, 9900]
    )

    # --- Exit rule parameters (PRD §10.1 priority ladder) ---
    # Defaults implement the validated tp12tr15_t1200 policy.
    # Lab source of truth (v31_winner_check.py:15):
    #   dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)
    #
    #   take_profit_pct=12     → pol["tp"]      = 0.12   (+12% gain; TP threshold)
    #   rug_pull_drop_pct=15   → pol["rugcut"]  = 0.15   (trailing 15% from peak; RUG_PULL)
    #   auto_sell_timer_s=1200 → pol["timer"]   = 1200   (max hold-time)
    #   stop_loss_pct=None     → pol["sl"]      = None   (disabled — not in validated policy)
    #   disaster_cap_pct=None  → pol["disaster"]= None   (disabled — not in validated policy)
    #
    # None means "guard disabled" — the setter never fires. The tape_settler
    # already checks `if pol["sl"] is not None` / `if pol["disaster"] is not None`
    # before evaluating those branches, so None is fully safe.
    #
    # Ordering invariant (Invariant 2) only applies to the NON-None subset:
    #   if all three of sl/disaster/rug are set: sl < disaster < rug  (enforced)
    #   if sl is None: only disaster < rug is checked (if both set)
    #   if disaster is None: only sl < rug is checked (if sl set)
    #   if both None: no ordering check (only rug_pull is active)
    take_profit_pct: float = Field(default=12.0, gt=0)
    stop_loss_pct: Optional[float] = Field(default=None, gt=0, le=100)
    disaster_cap_pct: Optional[float] = Field(default=None, gt=0, le=100)
    rug_pull_drop_pct: float = Field(default=15.0, gt=0, le=100)
    next_poll_guard_s: int = Field(default=10, ge=0)
    auto_sell_timer_s: int = Field(default=1200, gt=0)
    stale_timeout_s: int = Field(default=1800, gt=0)

    # --- Trailing stop parameters ---
    # trailing_pct is used by the live exit_engine's TRAILING rule (not the settler).
    # Must satisfy: trailing_pct < take_profit_pct (Pydantic invariant 3).
    # With take_profit_pct=12.0, trailing_pct defaults to 10.0 (< 12 ✓).
    trailing_pct: float = Field(default=10.0, gt=0, le=100)
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
        2. Loss priority ordering — only enforced for the NON-None subset:
             if sl and disaster both set:   sl < disaster  (sl fires before disaster)
             if disaster and rug both set:  disaster < rug (disaster fires before rug)
             if sl and rug both set:        sl < rug
           None means the guard is DISABLED; no ordering check against None values.
           The validated tp12tr15_t1200 policy has sl=None, disaster=None — only
           rug_pull_drop_pct is active.
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
        # Only compare NON-None pairs; None means "guard disabled".
        sl = self.stop_loss_pct
        dis = self.disaster_cap_pct
        rug = self.rug_pull_drop_pct
        if sl is not None and dis is not None and not (sl < dis):
            raise ValueError(
                f"Exit priority ordering: stop_loss_pct ({sl}) must be < "
                f"disaster_cap_pct ({dis}). "
                "Lower percentages fire earlier in the exit priority ladder (PRD §10.1)."
            )
        if dis is not None and not (dis < rug):
            raise ValueError(
                f"Exit priority ordering: disaster_cap_pct ({dis}) must be < "
                f"rug_pull_drop_pct ({rug}). "
                "Lower percentages fire earlier in the exit priority ladder (PRD §10.1)."
            )
        if sl is not None and dis is None and not (sl < rug):
            raise ValueError(
                f"Exit priority ordering: stop_loss_pct ({sl}) must be < "
                f"rug_pull_drop_pct ({rug}) when disaster_cap_pct is None. "
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
