# ---
# module: core.v7_capital_guard
# sprint: sprint-15
# story: US-95
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: none
# ---
"""US-95 — v7 capital guard and retrain dependency.

CONTEXT
=======
v7 (trilly_pregrad_v7, US-87) was trained on a hand-picked Birdeye GRADUATE
sample.  The t120 lesson (earlier v4 live run) proved that a Birdeye-trained
model does NOT transfer to the live AMM firehose: score inflation, OOD features,
and stale regime all degrade live edge.  The 8 shipped boosters (seed0..7.txt)
are therefore REFERENCE ARCHITECTURE — they may be deployed OBSERVE-ONLY at
flat $25 per US-89/US-91, but MUST be retrained on solanatrilly's own live AMM
feed before any real capital is authorised.

CAPITAL GUARD
=============
V7_RETRAINED_ON_LIVE_FEED (bool, default False) is the single named gate.

  - False (default): v7 is OBSERVE-ONLY.  Any code path that would flip
    `trading_enabled` or send capital via v7 must check this flag first.
    The guard raises V7CapitalGuardError if capital is requested while the
    flag is False.

  - True: set ONLY after a live-feed retrain has been verified (out-of-time
    held-out fold confirms edge, retrain is recent, US-92 mint-on-post-rows
    is live).  No code in solanatrilly sets this to True automatically; it
    requires operator decision and explicit config change.

This mirrors the lightweight discipline of US-84's date-gated retrain:
a named constant + a guard error, no live training pipeline this sprint.

RETRAIN CONTRACT
================
When the retrain IS run (future, post-soak), it must:

  1. Rebuild the v7 training set from solanatrilly's OWN recorded post-grad
     firehose (NOT Birdeye).
  2. Use the US-86 detector graduation label + the US-81 cum-vol_sol>=85 free
     label as ground truth.
  3. Attribute post-grad outcomes via the US-92 mint field (post rows currently
     carry no mint — see US-92 DEPENDENCY below).
  4. Use the same 44 leak-safe features in meta.json.selection.feature_order
     (no new features without PO approval).
  5. Dollar quantities via the per-phase basis (vol_sol x SOL_price — see US-81
     dollar-basis discipline).
  6. Retrain the 8-seed LightGBM bag.
  7. Re-derive the gate threshold on a held-out RECENT fold (the most-recent fold
     is the only valid read — the lane faded Feb→Jun, stale folds overestimate
     edge).
  8. Retrain WEEKLY (decay is fast; each retrain anchors to the last 7 days of
     live tape).

This mirrors the copy-track US-84 weekly-retrain pattern.  The weekly Celery
task is a date-gated no-op until enough live post-grad data (with mint) exists.

US-92 DEPENDENCY
================
The retrain CANNOT run until US-92 is delivered and live.

US-92 wires the recorder to emit `mint` on every post-grad row.  Currently,
post-grad rows carry `rel` (seconds since graduation) but NO `mint` / `pool` /
`pair` field, so post-grad fills are UNATTRIBUTABLE to a specific token offline.
Without mint-on-post-rows:

  - The offline graduation labeler (US-81) cannot associate post-grad outcomes
    with pre-grad entries.
  - The retrain cannot build a labelled (features, outcome) dataset.
  - Live model PnL cannot be self-validated from the recorder's own tapes.

Dependency chain:
  US-92 (mint-on-post-rows LIVE) → weeks of recorder uptime → v7 retrain →
  out-of-time validation → operator authorises V7_RETRAINED_ON_LIVE_FEED = True
  → capital authorised.

PO RISK REGISTER
================
Four risks the PO must acknowledge before any capital is authorised on v7:

  RIGHT_TAIL_CARRIED:
    The backtest median trade loses; ~5% of trades carry 110–190% of total
    profit (extreme positive right-tail).  Expected PnL is positive only because
    of this thin right tail.  A live run with limited capital and high slippage
    will hit the median, not the right tail.  Risk: live return << backtest.

  SURVIVORSHIP_AND_FEED_TRANSFER:
    v7 was trained on Birdeye GRADUATE samples (tokens that SURVIVED to
    graduation and were indexed by Birdeye).  The live AMM feed is all graduated
    tokens — a broader, noisier population.  A Birdeye-trained model has not been
    validated on this population.  Risk: live score distributions are OOD vs the
    lab, degrading gate selectivity (the t120 lesson).  Mitigation: retrain on
    the live feed.

  DECAY:
    The pump.fun/PumpSwap lane faded Feb→Jun 2026 (measured by the lab).
    Edge that existed in the Feb training window may not hold in Jul+.
    Risk: a stale model has inflated backtest edge that does not realise live.
    Mitigation: retrain WEEKLY and re-validate the held-out recent fold.

  FILL_FRAGILITY:
    The backtest assumes fills at the $350 depth-equivalent VWAP.  The live
    $500-notional entry is at a DIFFERENT book depth.  A deep-book fill question:
    is the $500 filled at a price consistent with the lab's $350 assumption?
    Risk: live slippage exceeds the lab assumption, eroding the edge.
    Mitigation: observe-only soak + honest fill repricing before capital.
"""
from __future__ import annotations

__all__ = [
    "V7_RETRAINED_ON_LIVE_FEED",
    "V7CapitalGuardError",
    "assert_v7_capital_authorised",
    "V7_RETRAIN_DEPENDENCY_US92",
    "V7_RISK_REGISTER",
    "V7_RETRAIN_CONTRACT",
]

# ---------------------------------------------------------------------------
# Capital guard flag
# ---------------------------------------------------------------------------

#: v7 capital gate.
#:
#: Default: False — the shipped boosters are Birdeye-trained (reference/architecture)
#: and have NOT been retrained on solanatrilly's own live AMM feed.  v7 is
#: OBSERVE-ONLY until this is True.
#:
#: Set to True ONLY after ALL of the following are confirmed:
#:   1. US-92 is LIVE (recorder emits mint on every post-grad row).
#:   2. Sufficient live post-grad feed has been recorded (>= 7 days, >= ~1000
#:      attributable post-grad trades).
#:   3. A fresh retrain has been run per the RETRAIN CONTRACT in this module.
#:   4. The held-out RECENT fold shows positive edge (not inflated by stale data).
#:   5. Operator has reviewed the PO risk register (RIGHT_TAIL_CARRIED,
#:      SURVIVORSHIP_AND_FEED_TRANSFER, DECAY, FILL_FRAGILITY) and explicitly
#:      authorised capital.
#:
#: NO code in solanatrilly sets this to True automatically.  This requires an
#: explicit operator decision and a deliberate config/code change.
V7_RETRAINED_ON_LIVE_FEED: bool = False


# ---------------------------------------------------------------------------
# US-92 dependency record
# ---------------------------------------------------------------------------

#: The retrain CANNOT happen until US-92 is delivered and live.
#: US-92 wires the recorder to emit `mint` on every post-grad row.
#: Without it, post-grad fills are UNATTRIBUTABLE offline (no mint -> no
#: labelled training set -> no retrain possible).
V7_RETRAIN_DEPENDENCY_US92: str = (
    "US-92 (mint-on-post-rows) must be LIVE before any v7 retrain can run. "
    "Post-grad rows currently carry no mint/pool/pair field; attribution of "
    "post-grad fills to specific tokens is impossible without this fix.  "
    "Dependency chain: US-92 LIVE -> recorder uptime (>=7 days) -> retrain "
    "-> held-out validation -> operator authorises V7_RETRAINED_ON_LIVE_FEED."
)


# ---------------------------------------------------------------------------
# PO risk register
# ---------------------------------------------------------------------------

#: Four PO risks that must be acknowledged before any capital is authorised.
V7_RISK_REGISTER: dict[str, str] = {
    "RIGHT_TAIL_CARRIED": (
        "Backtest median trade loses; ~5% of trades carry 110-190% of total profit. "
        "Expected PnL is positive only because of a thin right tail.  "
        "A live run at limited capital and real slippage will likely hit the median. "
        "Risk: live return << backtest EV."
    ),
    "SURVIVORSHIP_AND_FEED_TRANSFER": (
        "v7 trained on Birdeye GRADUATE samples (survivorship bias); live AMM feed "
        "is all graduated tokens (broader, noisier population).  Birdeye-trained model "
        "NOT validated on the live population (the t120 lesson: score distributions OOD, "
        "gate selectivity degrades).  Mitigation: retrain on own live feed before capital."
    ),
    "DECAY": (
        "PumpSwap lane faded Feb->Jun 2026 (lab measurement).  Edge that existed in "
        "the Feb training window may not hold in Jul+.  "
        "Risk: stale model has inflated backtest edge that does not realise live.  "
        "Mitigation: retrain WEEKLY; the most-recent held-out fold is the only valid read."
    ),
    "FILL_FRAGILITY": (
        "Backtest assumes fills at ~$350 depth-equivalent VWAP; live entry is $500 "
        "(different book depth).  Slippage at $500 may exceed the lab assumption.  "
        "Risk: live fills erode edge modelled at $350.  "
        "Mitigation: observe-only soak + honest fill repricing before capital."
    ),
}


# ---------------------------------------------------------------------------
# Retrain contract
# ---------------------------------------------------------------------------

#: Specification for the future live-feed retrain (deferred until US-92 + feed matures).
#: Committed here so the future retrain agent has an unambiguous contract.
V7_RETRAIN_CONTRACT: dict[str, object] = {
    "data_source": "solanatrilly OWN recorded post-grad firehose (NOT Birdeye)",
    "graduation_label": (
        "US-86 on-chain detector (MigrateV2 + PumpSwap CreatePool) "
        "+ US-81 free cum-vol_sol>=85 label"
    ),
    "outcome_attribution": "US-92 mint field on post-grad rows (gating precondition)",
    "feature_order": "meta.json.selection.feature_order (44 features, unchanged)",
    "dollar_basis": "vol_sol x SOL_price per US-81 discipline (never vol_usd)",
    "model": "8-seed LightGBM bag (same architecture as current v7)",
    "threshold_derivation": "held-out MOST-RECENT fold (not pooled — decay is fast)",
    "retrain_cadence": "WEEKLY (mirrors US-84 copy-track retrain pattern)",
    "minimum_data": ">=7 days of live post-grad feed with mint (>=~1000 attributable trades)",
    "us84_mirror": (
        "Like US-84's date-gated retrain, the weekly job is a no-op until the lake "
        "matures; the shipped boosters carry until then."
    ),
    "no_op_until": "US-92 LIVE + recorder uptime >= 7 days + operator authorises capital",
}


# ---------------------------------------------------------------------------
# Guard function
# ---------------------------------------------------------------------------


class V7CapitalGuardError(RuntimeError):
    """Raised when v7 capital is requested but the model has not been retrained
    on the live feed.

    The shipped boosters are Birdeye-trained reference/architecture; they are
    OBSERVE-ONLY until V7_RETRAINED_ON_LIVE_FEED is True.
    """


def assert_v7_capital_authorised() -> None:
    """Raise V7CapitalGuardError if v7 capital is not yet authorised.

    Call this guard at any code path that would send real capital via v7
    (e.g. before flipping trading_enabled=True for the v7 track).

    Raises
    ------
    V7CapitalGuardError
        When V7_RETRAINED_ON_LIVE_FEED is False (the default).

    Does nothing when V7_RETRAINED_ON_LIVE_FEED is True (capital authorised).
    """
    if not V7_RETRAINED_ON_LIVE_FEED:
        raise V7CapitalGuardError(
            "v7 capital is NOT authorised.  "
            "V7_RETRAINED_ON_LIVE_FEED=False (default): the shipped boosters are "
            "Birdeye-trained reference/architecture and have NOT been retrained on "
            "solanatrilly's own live AMM feed.  "
            f"Dependency: {V7_RETRAIN_DEPENDENCY_US92}  "
            "Set V7_RETRAINED_ON_LIVE_FEED=True ONLY after the retrain contract in "
            "core.v7_capital_guard has been executed and the held-out recent fold "
            "confirms positive edge."
        )
