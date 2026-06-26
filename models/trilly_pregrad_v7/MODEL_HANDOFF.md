# trilly_pregrad_v7 — MODEL HANDOFF (scoping doc for Product Owner + Dev Team)

**What it is:** the post-graduation lane (buy a token at the moment it graduates the pump.fun curve, ride the
post-grad pump, exit on a trailing stop) — **rebuilt on the trusted/honest harness** with a fresh selection model
(pre-grad + holder + wallet-reputation), a ride exit, and depth-gated sizing. It is the successor to
`trilly_pregrad_v6/v6.1`: same lane, but selection/exit/sizing were **re-derived and validated walk-forward on the
most-recent data + a 2× impact stress** (v6's projections predated that rigor).

**Status: PAPER-SOAK CANDIDATE — not capital-cleared.** Offline ≠ live is the project law; every champion is soaked
first. Build it, observe it, let the soak decide.

**Headline:** clears **~$500–760/day on the recent walk-forward folds at the modeled impact** (seed-robust — all 8
seeds > $500 on the held-out June fold), and stays **profitable (~$300–470/day) under a 2× impact stress** (it does
*not* invert, unlike the t30 sizing mirage). Whether it banks $500 or ~$350 live is a **fill-quality question** the
soak must answer.

---

## FOR THE PRODUCT OWNER — scope, value, risk, acceptance

**The bet (one line):** at graduation, ~209 tokens/day are fillable; full-pop they lose money, but a selection model
picks the top ~25% (~52/day), rides them with a 30%-trailing/600s exit, and sizes up the deep-book ones — netting a
positive, seed-robust $/day on recent data.

**Value (offline, cost-honest, walk-forward, recent folds):**
| basis | June (most live-like) | May |
|---|---|---|
| modeled impact, depth-gated $100 | **+$570/day** (CI-lo +$6.40, 8/8 seeds +$503–621) | +$764/day |
| 2× impact stress | +$306/day (CI-lo +$1.20, still PASS) | +$470/day |
| flat $25 anchor (impact-independent) | ~$130–210/day | — |

**Acceptance criteria for the soak (pre-register these):**
1. Observe (paper) at flat $25 first; live per-trade $/tr ≥ ~$2.5 on recent graduates (≈ the offline anchor), no inversion.
2. Deep-book ($100-eligible) fills match the `2S/(S+flow)` impact model within ~2× (logged from reconstructed firehose, not dashboard).
3. The selection edge holds out-of-time (retrain weekly; the lane decays — see risks).
4. Only after 1–3 hold: ramp size depth-gated toward $100. **Never exceed $100/trade.**

**Risks the PO must price in (these are real, not boilerplate):**
- **Right-tail-carried.** The *median* trade LOSES money (−$5 to −$8); the top ~5% of trades produce 110–190% of the
  profit. A minority of moonshots carry the book — if they don't materialise or slippage eats them, the edge thins
  fast. This is the #1 risk and it gets worse at larger size.
- **Survivorship + feed-transfer.** The model was trained on a hand-picked Birdeye graduate sample. The t120 line
  proved a Birdeye-trained model did **not** transfer to the live firehose. **The model MUST be retrained on
  solanatrilly's own live AMM feed before capital** — treat the shipped boosters as a reference/architecture, not the
  final live model.
- **Decay.** The lane structurally faded over the training window (profitable in Feb, all-negative full-pop by June —
  only selection keeps June alive). Plan for **frequent retraining** and continuous monitoring; a stale model goes
  negative.
- **Fill fragility.** The >$500 rests on depth-gated $100 fills. The honest expectation band is **~$300 (stressed) to
  ~$570 (modeled) per day** on the recent fold.

**What to scope:** (1) graduation-instant entry trigger + honest fill; (2) the 44-feature vector incl. the
**wallet-reputation bank** (heaviest dep — reuses v4/v6 wallet-bank infra) and **holder count**; (3) the ride exit +
depth-gated sizing; (4) a retrain pipeline on the live feed; (5) reconstructed-firehose fill logging for the soak.

---

## FOR THE DEV TEAM — technical spec

### Inference pipeline (per graduation event)
1. **Entry trigger.** On the on-chain graduation marker (`CompleteEvent` / `bonding_complete`; same detection as
   `app/services/wallet_graduate_bank.detect_graduation_ts`), enter on the **first post-grad AMM swap ≥ grad_ts + 2s,
   within 30s**; reject if fill/quote − 1 > 15% (slip-miss) or a dust-fill (fill < 0.3× local median price).
   *(Entering later is refuted — do not delay.)*
2. **Build the 44-feature vector** (all leak-safe, available at graduation — `meta.json.selection.feature_order` is
   the exact order):
   - **19 pre-grad curve-life feats** from the bonding-curve tape (the same kind v4 already computes): `pre_buy_frac,
     pre_buy_vol_usd, pre_buys_last60, pre_max_trade_usd, pre_mean_trade_usd, pre_n_buys, pre_n_sells, pre_n_swaps,
     pre_net_flow_usd, pre_price_ret, pre_top1_buyer_share, pre_top3_buyer_share, pre_trades_per_sec, pre_uniq_buyers,
     pre_uniq_traders, pre_vol_last300, pre_vol_last60, pre_vol_usd, pre_window_covered_s`.
   - **`n_pregrad_holders`** — distinct wallets holding a positive balance at graduation (derive from cumulative
     buys−sells per wallet on the pre-grad tape, or a holder snapshot). **This is the #1 feature by importance — confirm
     it's computable live during scoping.**
   - **24 wallet-reputation feats** (`whale_outcome_features.csv` columns) — outcome-weighted reputation of the token's
     early/big buyers' PRIOR graduate picks, walk-forward (only picks resolved before this grad). Families:
     `time_rdollar_*`, `time_pk24_*`, `size_rdollar_*`, `size_pk24_*` (each: `repmean_mean/max`, `repmax_mean`,
     `ngood_sum`, `nhist`, `wmean`). **Built from the same wallet-bank infra v4/v6 already maintain**
     (`analysis/graduated/v4_outcome_rep.py`). NaN/unknown → **0** (in-distribution; many tokens have unknown-rep buyers).
   - NaN fill for pre+holder feats → the medians in `meta.json.selection.nan_fill` (do NOT 0-fill those).
3. **Score** = mean of `predict()` over the 8 boosters in `selection/seed0..7.txt`.
4. **Gate:** trade if `score ≥ 15.86012` (calibrated to ~top-25%/day ≈ 52 trades/day). *(Eval used per-day adaptive
   top-25%; the fixed threshold is the live-equivalent at scale.)*
5. **Exit — RIDE `tr30_t600`** on the post-grad AMM price: track the running max from fill; **sell when price ≤
   running_max × (1 − 0.30)** (30% trailing stop) **or at 600s**, whichever first; honest fill = first swap ≥ trigger+2s.
   *(Fast scalps are refuted — the post-grad open is a dump; you ride.)*
6. **Sizing:** `size = $100 if entry_depth ≥ $8,000 else $25`. **Cap $100** ($200 is the impact-fragile zone). Offline
   depth = `eflow` ($-flow in [grad, grad+60]); **live = graduation-instant AMM pool liquidity** — the soak must
   confirm the live proxy tracks `eflow` before trusting the size-up.

### Honest-fill / grading conventions (match these or the soak lies)
Entry impact `2·S/(S+eflow)`, drain impact `2·S/(S+flow)` on the exit, **1% round-trip fee**; reference engine =
`analysis/graduated/settle_engine.py`. **Grade the soak on the post-grad TAPE + on-chain graduation, NOT dashboard PnL.**

### Validate before trusting (parity)
`parity_sample.parquet` = 400 tokens × the 44 features + the expected 8-seed `score` (+ `eflow`). Diff your live
feature vector and score against it; they must match before any number is believed.

### Files
`meta.json` (full contract: feature order, nan-fill, gate, exit, sizing, economics, caveats, discipline),
`selection/seed0..7.txt` (8 LightGBM boosters), `parity_sample.parquet`. Build/eval scripts:
`analysis/graduated/{replay_grad,replay_grad_entry,select_grad,select_grad2,select_grad_final,rigor_grad,build_v7_artifact}.py`;
full study + caveats in `analysis/graduated/POSTGRAD_FINDINGS.md`.

---

## DEPLOYMENT DISCIPLINE (non-negotiable)
1. **OBSERVE at flat $25 first** (impact-independent ~$130–210/day). Confirm no inversion — offline ≠ live is project law.
2. **Retrain on solanatrilly's own live AMM feed** before trusting live numbers (Birdeye-trained ≠ live, proven on t120).
3. Only after the $25 soak holds: **ramp depth-gated toward $100**, watching reconstructed-firehose deep-book fills vs
   the impact model. **Cap $100; never $200.** Sizing multiplies the edge — if selection inverts live, sizing
   amplifies the loss. **Size is the last lever, never the first.**
4. Retrain weekly; the lane decays. The most-recent fold is the only valid read.
