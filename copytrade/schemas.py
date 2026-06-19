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

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


# ===========================================================================
# COHORT 2.0 SCHEMA — the real consumption contract (cohort.json `copytrade-2.0`)
# ===========================================================================
#
# The committed engine (above) was built for the never-shipped schema "1.0":
# one flat trade_config, ONE exit model (TP/SL/CURVE/TIMER), SOL sizing, and a
# hard invariant FORBIDDING mirror-sell.  The real cohort the operator hands us
# (`copy_2026-06-19_v1/cohort.json`, schema "copytrade-2.0") is fundamentally
# different and AUTHORITATIVE:
#
#   - a `global` block (mode / usd_size_per_trade / a `trigger` sub-block with
#     the >= $250 `min_trigger_buy_usd` conviction gate / max_concurrent / cadence)
#   - a `strategies[]` array of TWO independent heads, each with its OWN wallet
#     list and its OWN deterministic exit:
#       * consistent_scalp -> exit type "mirror_wallet_sell" (REQUIRES the
#         mirror-sell the 1.0 schema forbade — its validated edge IS the wallet's
#         exit timing)
#       * moonshot         -> exit type "our_trailing" (SL / trailing-giveback / TP)
#   - USD sizing/triggering (not SOL), and NO pre-graduation-only restriction
#     ("copy the first >= $250 buy whenever it happens", still pump.fun-scoped).
#
# These classes are ADDITIVE — the legacy CopyTradeConfig above is retained
# (unused by the 2.0 path) until a later cleanup PR removes the dead 1.0 path.
# The 2.0 path is the one the live engine consumes.


class TriggerConfig(BaseModel):
    """The cohort-2.0 `global.trigger` block — when to copy a watched wallet.

    `min_trigger_buy_usd` is the conviction filter the formula was validated on:
    skip the signal unless the wallet's buy is >= this many USD.  `pump_fun_only`
    keeps us in the validated universe (pump.fun tokens); note the validated rule
    does NOT restrict to pre-graduation buys.
    """

    model_config = ConfigDict(extra="ignore")  # underscore "_notes" siblings ignored

    event: str = "watched_wallet_buy"
    copy_first_buy_only: bool = True
    min_trigger_buy_usd: float = Field(default=250.0, ge=0)
    pump_fun_only: bool = True
    dedupe_token_across_wallets: bool = True


class GlobalConfig(BaseModel):
    """The cohort-2.0 `global` block — engine-wide trigger/size/mode knobs."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["observe", "live"] = "observe"
    usd_size_per_trade: float = Field(default=25.0, gt=0)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    max_concurrent_positions: int = Field(default=30, gt=0)
    reselect_cadence_days: int = Field(default=14, gt=0)


class MirrorWalletSellExit(BaseModel):
    """`consistent_scalp` exit — mirror the SOURCE wallet's sell (no imposed TP/SL).

    This head's edge IS the wallet's exit timing, so we sell our copy as the
    source wallet sells; `max_hold_seconds` is a fallback market-sell only.
    `hard_stop_loss_pct` is intentionally optional (None = no stop; imposing one
    was tested and LOSES for these low-precision scalp wallets).
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["mirror_wallet_sell"]
    max_hold_seconds: int = Field(default=86400, gt=0)
    hard_stop_loss_pct: Optional[float] = Field(default=None, gt=0, le=100)


class OurTrailingExit(BaseModel):
    """`moonshot` exit — OUR own trailing ride (we do NOT mirror their scalp sell).

    Hard SL, a trailing giveback from the high-water mark once profitable, and a
    hard TP cap.  `max_hold_seconds` optional (None = no time cap; the trailing
    stop ends the ride).
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["our_trailing"]
    stop_loss_pct: float = Field(gt=0, le=100)
    trailing_giveback_pct: float = Field(gt=0, le=100)
    take_profit_pct: float = Field(gt=0)
    max_hold_seconds: Optional[int] = Field(default=None, gt=0)
    mirror_wallet_sell: bool = False


#: Discriminated union on the `type` tag — pydantic selects the right exit model.
StrategyExit = Annotated[
    Union[MirrorWalletSellExit, OurTrailingExit],
    Field(discriminator="type"),
]


class StrategyWallet(BaseModel):
    """One wallet in a strategy head.  Only `address` is needed by the engine;
    the head-specific metrics (is_precision, is_capped_ride, …) are informational
    and tolerated via extra="allow"."""

    model_config = ConfigDict(extra="allow")

    address: str
    rank: Optional[int] = None

    @model_validator(mode="after")
    def _check_address(self) -> "StrategyWallet":
        if not self.address or not self.address.strip():
            raise ValueError("wallet address must be a non-empty string")
        return self


class StrategyConfig(BaseModel):
    """One strategy head: its id, enabled flag, deterministic exit, and wallets."""

    model_config = ConfigDict(extra="ignore")

    id: str
    enabled: bool = True
    description: str = ""
    exit: StrategyExit
    wallets: list[StrategyWallet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_id(self) -> "StrategyConfig":
        if not self.id or not self.id.strip():
            raise ValueError("strategy id must be a non-empty string")
        return self


class CohortV2(BaseModel):
    """Validated cohort.json (schema `copytrade-2.0`) — the consumption contract.

    `global` is a Python keyword, so it is bound to `global_` with an alias;
    `populate_by_name=True` lets callers use either name.  Unknown top-level keys
    (e.g. `_schema_notes`, `provenance` notes) are ignored.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    schema_version: Literal["copytrade-2.0"]
    cohort_id: str
    created_at: str
    description: str = ""
    global_: GlobalConfig = Field(alias="global")
    strategies: list[StrategyConfig] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "CohortV2":
        if not self.cohort_id or not self.cohort_id.strip():
            raise ValueError("cohort_id must be a non-empty string")
        if not self.created_at or not self.created_at.strip():
            raise ValueError("created_at must be a non-empty string")
        ids = [s.id for s in self.strategies]
        if len(ids) != len(set(ids)):
            raise ValueError(f"strategy ids must be unique; got {ids}")
        if not any(s.enabled for s in self.strategies):
            raise ValueError("at least one strategy must be enabled")
        return self

    def enabled_strategies(self) -> list[StrategyConfig]:
        """The enabled heads, in declaration order."""
        return [s for s in self.strategies if s.enabled]

    def wallet_to_strategy(self) -> dict[str, str]:
        """Map each wallet address -> its strategy id (enabled heads only).

        If the same address appears under more than one enabled head, the FIRST
        head (declaration order) owns it — matching the cohort's cross-head
        dedupe rule ("the FIRST trigger's head owns the exit").
        """
        mapping: dict[str, str] = {}
        for strat in self.enabled_strategies():
            for w in strat.wallets:
                mapping.setdefault(w.address, strat.id)
        return mapping
