# EPIC — Copy-trade curve-stage ride-to-grad integration (cohort `copy_2026-06-22_curvestage`)

**Status:** foundation validated + seed built; engine integration in progress. **Observe/paper only — no capital.**
**Owner:** solanatrilly live-run operator (integration owned end-to-end; no lab handoff needed — all artifacts readable in `solanatrills`).
**Source of truth:** `solanatrills/models/copy_2026-06-22_curvestage/{HANDOFF.md,formula.json,watchlist.csv}` + `analysis/whale_graph/{entry_select_build.py,etg_room_classifier.py}`.

## The deployable rule (all inputs live-observable, leak-safe)
For each **pool-wallet ≥$250 first-buy** of a pump.fun token, copy **only if ALL hold**, book **observe $25**:
1. **on_curve** — still on the bonding curve (not migrated to pump_amm). *(already in `copytrade/buy_trigger.py`)*
2. **curve_room** — `curve_frac ≤ 0.60`.
3. **pgrad_rank** — `P(graduate) ≥ frozen_threshold` (≈ top-25% among gated candidates).
**Exit:** sell at graduation/migration. **Size $25.**

## Foundation — VALIDATED (reproduce-before-belief)
- Reproduced the lab folds EXACTLY via `etg_room_classifier.py` (fresh-pool basis, NOT the retired frozen-80):
  room gate ≤0.6 → **A +$15.02 / C +$9.62 / B +$5.88 /tr** (avg +$10.17; HANDOFF +$15.0/+$9.6/+$5.9), sel-grad 60–69% vs ~39% breakeven; beats no-gate 3/3.
- **Seed classifier built** (`models/copy_2026-06-22_curvestage/pgrad_lgbm.txt` + `pgrad_meta.json`): LGBM
  (n_estimators=300, lr=0.03, num_leaves=31, min_child=40, sub/col=0.8) trained on **on_curve=(~grad)|pre_grad**
  across all 5 lab weeks (12,228 rows); **frozen P(grad) threshold 0.1529** = 75th pct among gated train candidates.
  Held-out validation on oos_jun13 (frozen threshold, never-seen week): **sel-grad 70%, +$7.35/tr, win 67%** — generalizes.

## Decisions (lab-approved 2026-06-22)
1. **Seed from lab parquets → weekly retrain at ≥1wk lake.** The solanatrilly firehose lake has only ~3 days
   (`dt=06-20/21/22`), so the recorder can't self-train yet. Ship the lab-trained seed; the weekly retrain job
   replaces it once the lake has ≥1 week of on-curve entries.
2. **Blocklist `Gygj9QQby4j2…`** (mega-rugger ×222; validated out-of-time rug signal [[rug-signal-corpus]]) AND keep
   the classifier's per-token down-weighting (belt + suspenders).

## Parity guardrails (lab-flagged — this is where ports in this project die)
**G1 — curve_frac definition parity.** The lab's `curve_frac = pre_sol_in/85` is a **tape net-buy-SOL proxy**, NOT the
on-chain reserve. θ=0.6 is calibrated to the PROXY. **Decision:** compute `curve_frac` live as `pre_sol_in/85` from the
recorder's tape (entry_features already yields `pre_sol_in`) — parity-true by construction, no recalibration. Do NOT use
the on-chain `vsol` reserve for the gate (different quantity → would cut a different population). True-reserve definition
is a future deliberate recalibration step if ever wanted.

**G2 — train/serve data-source parity.** Seed trains on lab **Birdeye** tapes (1s, USD `basePrice×quotePrice`); live
scores on the **recorder** (Helius slot-level Borsh `vsol/vtok` SOL-ratio price + Birdeye backfill). **Risk:**
`price_at_entry`/`fdv_proxy` are the only basis-sensitive features — Helius `price` is a SOL/token ratio, the lab's is
USD/token. The port MUST compute `price_at_entry` in **USD/token** (e.g. `vol_usd/token_qty` or `price_sol×sol_usd_spot`).
The other 18 features (counts, SOL amounts, ratios, breadth) are basis-invariant. **Gate the soak on a feature-distribution
parity table:** live-computed entry_features vs the lab parquets (per-feature median/quantiles). [[live-parity-fresh-tape]].

**G3 — reproducing offline ≠ proving the edge.** Parity confirms the PORT is correct; it does NOT confirm the edge
survives live. The lab's "honest second-buyer entry" is the optimistic next-print fill — the real second-buyer cost (#17)
and live slip are NOT in the +$10/tr. **Soak judgment:** (a) judge on **firehose-reconstructed REAL fills**
(`copytrade/fill_repricing.py` + `curve_price.py` honest fill), NEVER the dashboard PnL (historically inflated);
(b) **selected grad-rate vs ~39% breakeven is the FAST leading health metric** — converges faster than PnL; **KILL the
soak if live grad-rate < ~40%** regardless of dashboard; (c) net $/tr after honest fill staying positive over enough
trades. Capital only after the soak holds. See [[ct-oracle-vs-live-honest-fills]], [[copy-vs-model-revenue-path]].

## Build slices (ordered; hotfix/epic-PR, observe-only)
1. ✅ **Seed classifier artifact** — built + held-out validated (above).
2. **Feature port** → `copytrade/entry_features.py`: pure port of `entry_select_build.py::entry_features` over the
   recorder's tape; `gts=None` at the live buy instant; **USD-basis `price_at_entry`** (G2); yields the 20 FEATS incl
   `curve_frac=pre_sol_in/85` (G1). Byte-faithful to the lab feature math.
3. **G2 parity harness** — compute live entry_features on recorder tokens, diff distributions vs lab parquets; pre-soak gate.
4. **Classifier scorer** → `copytrade/pgrad_classifier.py`: load LGBM, score feature dict, compare to frozen threshold.
5. **Gate + observe wiring** — in `buy_trigger.py`/`trigger_pipeline.py`: watched ≥$250 first-buy on pump token →
   on_curve (exists) + curve_frac≤0.6 + pgrad≥thr → book observe `CopytradePosition` $25 → exit at graduation.
6. **Watchlist load** — the 85 wallets (`watchlist.csv`) + blocklist `Gygj9Q…`.
7. **Weekly retrain job** — celery-beat task: rebuild training set from the lake's prior-week on-curve entries
   (pool-wallet ≥$250 first-buys, entry_features, grad label), retrain LGBM, refresh frozen threshold; takes over the seed at ≥1wk.
8. **Soak monitoring** — selected grad-rate (leading, kill<40%) + honest-fill net $/tr; report like the model soak.

## Provenance
Lab journal `solanatrills/docs/progress_copytrade.md` #32 (dead recipe) → #33 (redirect: token not wallet) → #34 (this).
OOS AUC 0.89–0.97 (train-size dependent). offline ≠ live; the soak is the arbiter.
