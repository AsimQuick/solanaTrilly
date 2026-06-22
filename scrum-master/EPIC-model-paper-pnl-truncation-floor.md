# EPIC — Model paper-trade PnL correctness: window-close settle + impact floor

**Status:** ✅ FIXED (PR #386) + DEPLOYED (2026-06-22 ~20:14Z) + live-verified. **Observe/paper only — `trading_enabled=false`.**

## Why this mattered
The operator's #1 priority is *trustworthy realized PnL* — the soak is useless without it. Two
independent bugs corrupted **every** model paper trade. Both proven live by read-only Birdeye
reconstruction (not by mocks — see [[testing-against-reality]]).

## Bug 1 — truncated-tape settle (the big one)
**Symptom:** model paper trades booked as `AUTO_SELL_TIMER` at `held` ~3–14s with near-zero or
misleading PnL, missing the real later TAKE_PROFIT / RUG_PULL.

**Root cause** (`core/management/commands/run_firehose.py`): the paper leg settled at *score time*
(~grad+120s) over whatever post-grad tape had arrived. The #382 REST recovery returned on the **first**
non-empty page (`if swaps: break`), and at grad+120s Birdeye has only indexed swaps up to ~now (a few
seconds past entry). The settler's `AUTO_SELL_TIMER` fallback (`if trig_t is None: trig_t = post[-1][0]`)
then fired at the **last available swap** — "ran out of tape," not a real 20-min hold. The mint was
marked SCORED immediately, so it never re-settled.

**Proof (reconstruction over the full now-indexed `[entry−30, entry+1800]` window, same settler):**
8 of 9 trades mis-settled.

| mint | recorded (truncated) | REAL (full tape) |
|---|---|---|
| DgNo…pump | −8.2% TIMER @6s | **+12.9% TAKE_PROFIT @138s** |
| Ac4a…sonnet | −29.1% TIMER @12s | **+17.5% TAKE_PROFIT @109s** |
| GFzi…pump | −0.8% TIMER @3s | **+4.7% TAKE_PROFIT @8s** |
| 4X7r…pump | −1.6% TIMER @5s | **+16.0% TAKE_PROFIT @18s** |
| EZ1V…pump | −0.4% TIMER @3s | **−18.8% RUG_PULL @19s** |
| 3jKA…pump | −8.0% TIMER @6s | **−13.6% RUG_PULL @107s** |
| 3mwt…2inw | +23.3% TP @2s | +24.8% TP @2s ✓ (control: TP fired before index lag → matches) |

The 3mwt match is the control proving the reconstruction is faithful and the divergence is purely the
truncation.

**Fix:** DEFER the settle until wall-clock passes `entry + outcome_window_s` (grad+120+1800 ≈ grad+32min),
then settle ONCE over a single fresh full-window `BirdeyeBackfiller.run_for_window` fetch (fully indexed
by then). Gate-passers register in `_pending_settle` and short-circuit the scorer each tick (no 30-min
re-score; the #377 starvation lesson); `_settle_inflight` dedups the fetch+settle; the token stays
DETECTED until the settle fires (restart-safe). Retired `_postgrad_rest_fetch_task` + `_postgrad_enterable`.

## Bug 2 — unfloored impact haircut
**Root cause** (`trading/tape_settler.py`): the depth/impact cost `2·size/(size+flow)` can exceed 1 on a
thin tape, driving `(1−cost)` negative → impossible sub-(−100%) PnL with a **negative effective exit
price** (live: ids 620/622 at −120.6% / −108.4%, `exit_price < 0`).

**Fix:** floor the haircut at a total loss — `impact_factor = max(1−cost, 0)`. A long loses at most 100%.
This is the **one deliberate deviation** from the verbatim `tape_resettle.py` port; mirrors the copy
curvestage `settle_grad` floor.

## Model edge — feedback for solanatrills (the lab)
Benchmark = v4 offline @30/day deploy depth (`models/trilly_pregrad_v4/MODEL_HANDOFF.md`): **+$8.41/tr,
+7.4% median, 58% win**, +$252/day, on 23.9k graduates (honest-fill, same `tp12tr15_t1200`, full 1800s
outcome).

Live honest reconstruction (n=9, full-tape, the only trustworthy read pre-fix):
- All 9: mean −8.6%, **win 56%**, median +4.7%.
- 7 genuinely-tradeable (excl. 2 dead-on-arrival): mean **+6.2%**, **win 71%**, median **+12.9%**.

**Live win-rate (56% / 71%) and median (+4.7% / +12.9%) bracket the offline 58% / +7.4% — the selection
is behaving as backtested.** n=9 is far too small for a $/trade verdict; the soak must accumulate under
the fix.

**Actionable lab item — survivorship at the entry gate.** 2 of 9 picks (RLeo, kiKj) passed the ≥20
*pre-grad*-swap gate but had only 2–4 *post*-grad swaps in 30 min — dead on arrival, single-print −60%.
**Question: does v4's "enterable graduate" offline universe drop tokens with no real post-grad fill
(honest-fill `dead`/`slip-miss` exclusion)?** If offline drops them and live books them, live structurally
underperforms offline. Fix = a **post-grad liquidity gate** (require ≥N post-grad swaps before booking)
so the live enterable universe matches offline. Tracked as a separate follow-up (modeling decision; the
truncated soak data can't answer it — re-evaluate after the fix accumulates clean trades).

**Also for the lab:** (a) floor the offline oracle's impact haircut identically (Bug 2) so offline == live
on thin tapes; (b) a copy-style reconstructed-fills oracle for the model would make this benchmark exact
(golden_scores is score/rank parity only, no PnL).

## Scope / disposition of historical rows
Fix-forward. The pre-fix model trades (ids 434–622) keep their corrupted recorded PnL except the 2
impossible ones (620/622, negative exit price) which were **voided** (NULL PnL). The model HC cutover was
bumped to the deploy time so monitored PnL reflects only post-fix trades. Real PnL of the pre-fix trades
is the reconstruction table above.

## Verification
35+ reality-anchored tests (captured Birdeye payload; mock only our control flow): window-close
full-window settle, wall-clock deferral gate, transient-error retry, truncated-vs-full trigger divergence,
thin-tape floor. Golden parity (us76 + v4) + full settler suite green. Live: deployed, inference_engine
healthy (38 grads/15min, 0 errors, WS not silent), gate-passers register `_pending_settle`; first
post-fix window-close settle verified ~grad+32min.
