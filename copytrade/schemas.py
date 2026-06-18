# ---
# module: copytrade.schemas
# sprint: sprint-12
# story: US-58 AC-58.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pydantic>=2.0
# ---
"""Pydantic v2 schema for the copytrade.* config namespace (SPEC §2, §5).

CopyTradeConfig is the SEPARATE config schema for the copy-trade engine.
It is NOT folded into PipelineConfig — §5 'no shared mutable state'.

Holds every SPEC §2 trade_config field plus runtime state
(active_cohort_id, engine_on).

Save-time invariants enforced at construction:
  - mirror_wallet_sells must be False (SPEC §3: we never mirror their sells)
  - sol_size_per_trade must be > 0
  - take_profit_pct must be > 0
  - stop_loss_pct must be > 0 and ≤ 100
  - curve_completion_exit_pct must be > 0 and ≤ 100
  - max_hold_seconds must be > 0
  - max_concurrent_positions must be > 0
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CopyTradeConfig(BaseModel):
    """Validated config schema for the copy-trade engine (SPEC §2 trade_config + runtime state).

    All fields correspond directly to the SPEC §2 leaderboard.json trade_config keys
    plus the two runtime-state fields (active_cohort_id, engine_on) that are NOT in
    the JSON but are held in this config store.

    Default mode is 'observe' (paper) per SPEC §10 and the sprint DoD safety gate.
    mirror_wallet_sells is permanently False — a save-time invariant enforced by the
    model_validator below (SPEC §3: we exit on OUR rules, never copy their sells).
    """

    # --- SPEC §2 trade_config fields ---
    mode: Literal["observe", "live"] = "observe"
    sol_size_per_trade: float = Field(default=0.25, gt=0)
    take_profit_pct: float = Field(default=200.0, gt=0)
    stop_loss_pct: float = Field(default=40.0, gt=0, le=100)
    exit_before_graduation: bool = True
    curve_completion_exit_pct: float = Field(default=90.0, gt=0, le=100)
    max_hold_seconds: int = Field(default=1800, gt=0)
    max_concurrent_positions: int = Field(default=20, gt=0)
    copy_only_pumpfun_curve_buys: bool = True
    copy_first_buy_only: bool = True
    dedupe_token_across_wallets: bool = True
    mirror_wallet_sells: bool = False

    # --- Runtime state (not in the JSON — operational toggles) ---
    active_cohort_id: Optional[str] = None
    engine_on: bool = False

    @model_validator(mode="after")
    def check_invariants(self) -> "CopyTradeConfig":
        """Enforce save-time invariants (SPEC §3 / sprint DoD observe-first gate)."""
        if self.mirror_wallet_sells:
            raise ValueError(
                "mirror_wallet_sells must be False — the copy-trade engine exits on "
                "OUR configurable rules (TP/SL/CURVE/TIMER), never by mirroring the "
                "watched wallet's sells (SPEC §3)."
            )
        return self
