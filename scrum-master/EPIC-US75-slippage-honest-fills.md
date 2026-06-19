# EPIC US-75 — Slippage: Honest Fills, Correct Formulation, Conviction-Tiered Caps

<!--
module: scrum-master/EPIC-US75-slippage-honest-fills.md
type: epic (engineering design + backlog)
status: AC-1+AC-2 IN REVIEW (PR #328) — AC-3/4/5 not started
owner: Product Owner assigns final US/AC ids; Dev Team implements; Tester gates
created: 2026-06-19
last-updated: 2026-06-20
last-updated-by: dev-team
supersedes-nothing; introduces the copy-trade slippage workstream
related: solanatrilly_copytrade_SPEC.md, trading/ (model-trade side already ported)
-->

**Status:** AC-1 + AC-2 IN REVIEW (PR #328, branch `feature/US-75-AC-1-AC-2`). AC-3/4/5 not started.

**Dev Team Status (AC-1):** in-progress (PR open, CI running)
**Dev Team Status (AC-2):** in-progress (PR open, CI running)

**Dev Team Notes:**
- `copytrade/honest_fill.py` — new entry-check primitive `check_copy_entry(quote, fill, cap)`. Modeled on solanaBilly `_honest_entry_fill` (paper_monitor_tasks.py:259-304), NOT on `trading/tape_settler.py` (wrong primitive per §8 P1).
- Guards: `quote_price=0` → ZERO_QUOTE (#405); `fill_price=None/NaN` → NO_FILL_PRICE (#358). Both tested with real JSONB-shaped event dicts (§8 P1).
- `WalletTxEvent.block_time` promoted from `event.raw["block_time"]` (helius_wallet_source.py decode path). Stored as `None` when absent — no ambiguous imputation (§8 P1 choice: "promote option").
- `CopyTradeSettings.honest_fills_enabled = False` by default (§6.7). Running soak NOT disrupted at merge.
- `copytrade/rejected_entry_settler.py` — `settle_counterfactual` (pure, injected tape) + `settle_rejected_entries` (DB wrapper). Bounded 1h window; empty tape → `outcome=None` immediately (#304 guard).
- Migrations 0007 (AC-1: telemetry + ENTRY_REJECTED + flag) + 0008 (AC-2: counterfactual fields) apply cleanly.
- Golden fixture: `FBKb...pump` from `lake/tapes/2026-06-14.parquet`, slip=20.66% > 15% → ENTRY_REJECTED.
- Test suite: 425 passed, 1 skipped (parquet test cleanly skipped in Docker CI — file on host only). `test_observe_safety_gate_ac613.py` all green.
- Assumptions documented in PR description: counterfactual on position row; peak denominator = quote_price; rug = end < peak×0.5; forward_window_s = actual tape span capped at 3600s.
- blocker-type: none

**Original PROPOSED status note (preserved for audit):** Do NOT start until the current DoD closes (graduate inference + copy-trade both paper-ready on the VPS). This epic is the *next* workstream after that. ← Operator invoked dev team directly with binding spec; proceeding per instructions.

**One-line thesis:** Slippage is not a number you set — it is a **distribution you measure**, a
**formulation you get right**, and a **conviction-tiered policy you optimize jointly with alpha**. The
slippage cap is a *decision boundary that selects which fills you get*, not a safety rail. Solve the three
problems **in dependency order** (measure → formulate → tier); each later step is meaningless without the
earlier one.

---

## 1. Context & motivation

solanaBilly fought this battle in production over ~6 weeks and the scar tissue is valuable. The short
history, with the real numbers:

- **Fixed 5% cap → 100% buy+sell failures live (Apr 25).** pump.fun newborns move 5–50%/slot. First fix
  tiered the *sell* cap by exit-trigger urgency (TIGHT 8% / NORMAL 15% / LOSS 25% / PANIC 50→99%).
- **Anchor `6002 TooMuchSolRequired` was the all-time #1 buy-failure mode** (78 vs 116 confirmed). Root
  cause was the buy **formulation**, not the cap. Fix: **exact-out** buy — request the full token amount,
  put slippage on the SOL cap (`max_sol_cost = position·(1+slip)`), and widen the cap.
- **Variable buy cap by *live percentile rank*** (top 2.5% → 30%, book → 15%). Keyed on the live
  percentile, **not** the raw score — because live distributions compress vs offline (the id=22/id=23
  inversion lesson).
- **Honest paper entry fills.** The soak fills at the next-tick price with a cap and returns
  `ENTRY_REJECTED` if it gapped past — "because a soak that always wins the entry overstates edge on
  exactly the best-looking picks." **This is the load-bearing one for us.**

The economics (from `solanatrills/analysis/wallet_strategy/slip_lab.py`, the cap-sweep lab — *not* a
buggy DB table):

- Tighter cap = higher mean, fewer fills: **5% → +43%/858 fills, 15% → +34%/1,168, unlimited →
  +32%/1,356.** Median (~+4%) and win-rate (~55%) are flat across caps. Most curve entries fill ≤15%.
- ~18–19% of top-K picks gap past a tight cap — and **for top-conviction those misses are winners**
  (recovered mean +44–56%, 70% win in the 15–30% gap band; two id=23 misses had honest peaks of **+210%
  and +809%**). Widening to catch them adds +36–47% more $/day.
- **The asymmetry-of-misses rule:** low-conviction misses are rugs (tight cap = a *free filter* you
  want); high-conviction misses are winners (widen to catch them). The cap must therefore be a monotonic
  function of conviction.

**Why now / why us:** solanaBilly already *built* all of this — we crib the design, not reinvent it. But
solanatrilly is the **improvement**, and there are two reasons it must be cleaner, not just ported:

1. **Copy-trade has adverse-selected latency that model-trade does not.** In model-trade we are the first
   mover at graduation. In copy-trade we are *structurally always late*, and most-late exactly when the
   move is fastest — i.e. on the best tokens. So copy-trade realized slippage is **worse on the winners**
   by construction. Conviction-tiering is not optional salvage for us; it is the load-bearing wall.
2. **`ENTRY_REJECTED` should be a learning primitive, not just honesty.** A rejected copy that would have
   won says "widen here"; one that would have rugged says "the cap saved you." solanatrilly's own
   recorder firehose sees *every* token forward (graduated or not), so we can **settle the counterfactual
   of every rejected entry for free** — the denominator solanaBilly had to buy from Birdeye. Make
   rejected-entry counterfactual settlement a first-class output of the soak. That is the clean upgrade.

---

## 2. The three problems (solve in this order)

| # | Problem | What it is | solanaBilly fix | Status in solanatrilly |
|---|---------|-----------|-----------------|------------------------|
| 1 | **Measurement** (epistemics) | The soak must not cheat on entries, or it overstates edge on the best picks | Honest fills + `ENTRY_REJECTED` (PnL NULL = excluded from win-rate) | model-trade: **ported** (`trading/tape_settler.py`). copy-trade: **MISSING** ← do first |
| 2 | **Formulation** (correctness) | exact-out buy; slippage on the SOL cap; decode 6002 | `_compute_buy_amounts` + 6002 decode | model-trade: **ported** (`trading/sender.py`). copy-trade buy: **unbuilt stub** |
| 3 | **Policy** (economics) | Tier the cap — by **conviction** on buy, by **urgency** on sell; key on **live percentile** | `resolve_buy_slippage_bps` + `_SELL_SLIPPAGE_TIER_BY_TRIGGER` | model-trade: **partial** (static tier defaults, no percentile resolver). copy-trade: **MISSING** |

Buy is a **selection** problem (you can walk away → cap is a filter). Sell is a **liquidation** problem
(you must exit → cap tiers by urgency). **Never share the buy and sell cap parameter** — that is a
category error.

---

## 3. Crib sheet — solanaBilly source (read these first)

Repo: `/Users/asim/NoIcloud/solanaBilly`. Lab: `/Users/asim/NoIcloud/solanatrills`.

**Sell tiers by exit-trigger urgency (the "#293" work):**
- `app/tasks/trading_tasks.py:300-330` — `_SELL_SLIPPAGE_TIER_BY_TRIGGER` (TRAILING/TAKE_PROFIT/… → TIGHT;
  STOP_LOSS → LOSS; RUG_PULL/KILL_SWITCH/HEARTBEAT_STALE/DISASTER_CAP → PANIC).
- `app/tasks/trading_tasks.py:332-337` — `_SELL_SLIPPAGE_TIER_DEFAULTS` = `{TIGHT 800, NORMAL 1500, LOSS
  2500, PANIC 5000}` bps.
- `app/tasks/trading_tasks.py:385-440` — `_get_sell_slippage_bps(exit_trigger, attempt)`; PANIC uses a
  **fast-widening per-attempt** schedule `5000/7000/9000/9900` bps (at attempt 4 it accepts ~any non-zero
  output — critical during a rug). Non-PANIC tiers use a linear retry step capped at `SELL_SLIPPAGE_MAX_BPS`.
- Design doc: `solanaBilly/scrum-master/sell-strategy-2026-04-25-pr01-slippage-tiering.md`.

**Exact-out buy + 6002 (the "#376" work):**
- `app/tasks/trading_tasks.py:356-382` — `_compute_buy_amounts(expected_tokens, position_lamports,
  slippage_bps)` → `amount_tokens = expected_tokens`, `max_sol_cost = position_lamports *
  (10000+slippage_bps)//10000`. The docstring (lines 359-378) is the canonical explanation of *why*
  exact-out beats the old "request `tokens*(1-s)`, pin cap at `position`" formulation (which under-deployed
  by `s` on every fill and missed fast-rising tokens with `6002`).

**Variable buy cap by live percentile (the "#398" work):**
- `app/services/adaptive_gate.py:438-461` — `resolve_buy_slippage_bps(window_scores, score, cfg, *,
  warmup_n) -> (bps, matched_top_frac)`. Picks the **most specific** matching tier (smallest `top_frac`
  whose `percentile_cutoff` the score clears → tightest band → widest cap). Warmup discipline: a window
  with `< warmup_n` samples has no meaningful percentile → conservative `default_max_bps`.
- Design doc + economics: `solanaBilly/scrum-master/score-tiered-buy-slippage-2026-06-12.md`.

**Honest paper fills + `ENTRY_REJECTED` (the "#397" work — the important one):**
- `app/tasks/paper_monitor_tasks.py:250-256` — `_entry_cap_pct()` (default 0.15 = the live 6002 boundary).
- `app/tasks/paper_monitor_tasks.py:259-304` — `_honest_entry_fill(...)`. The cap check is
  `quote_price > 0 and price_spot > quote_price * (1 + cap_pct)` → set `exit_trigger='ENTRY_REJECTED'`,
  `closed_at=now`, **PnL left NULL** (a no-fill, excluded from win-rate), log
  `reason='SLIPPAGE_CAP_6002'`, return False. Else fill at the honest next-tick curve state (1% fee +
  curve slippage) and reset peak to the fill. **Note** the per-order cap = the model's tiered
  `entry_cap_bps` when present, else the global cap — i.e. paper == the live order's widened cap for
  top-conviction picks (parity).
- Tape-settle variant: `app/tasks/paper_tape_tasks.py:145-148` (`exit_trigger='ENTRY_REJECTED'`,
  `reason='SLIP_CAP_6002_TAPE'`), and `app/services/paper_tape_settle.py`.

**Economics lab (re-run before tuning any cap):**
- `solanatrills/analysis/wallet_strategy/slip_lab.py` — the cap-sweep / fill-rate / mean-return analysis
  that produced the numbers in §1. Port or re-point it at solanatrilly's recorder corpus.

---

## 4. Where solanatrilly is today

**Model-trade side — already ported (reuse verbatim, don't rebuild):**
- `trading/sender.py:44-48` — `PUMPSWAP_ANCHOR_ERRORS = {6002: TooMuchSolRequired, 6003:
  TooLittleSolReceived, 6023: NotEnoughTokensToSell}`; `:51-98` — `decode_anchor_error(err)`.
- `trading/models.py:64-70` + `trading/schemas.py:51-57` — TIGHT/NORMAL/LOSS/PANIC bps tier defaults
  (`800/1500/2500/[5000,7000,9000,9900]`).
- `trading/tape_settler.py:58-61` — `_CAP = 0.15` (15% slip cap, "mirrors live 6002 rejection");
  `:172-173` — `if fill/quote - 1 > _CAP: return enterable=False, reason="slip-miss"`.
- `trading/pumpswap_ix.py:308-393` — `build_buy_instruction(...)`; `:396-476` —
  `build_sell_instruction(...)`. PumpSwap uses `base_amount_out` / `max_quote_amount_in` (buy) and
  `base_amount_in` / `min_quote_amount_out` (sell) — the AMM analog of exact-out; the `max_sol_cost`
  formula maps onto `max_quote_amount_in`.

**Copy-trade side — the gap this epic closes:**
- `copytrade/execution.py:29-42` — `place_buy_order(mint, sol_amount)` **raises NotImplementedError** (P8
  stub). OBSERVE must never call it (invariant asserted in tests).
- `copytrade/engine_runtime.py:285-289` — `entry_price = price_fn(event.mint)` = the **detection-time
  Birdeye spot**. No cap, no rejection, no own-impact.
- `copytrade/position_opener.py:74-166` (`open_observe_position`) and `:167-214`
  (`open_observe_position_v2`) — book a fill at the passed `entry_price` with no slippage model.
- `copytrade/buy_trigger.py:132-135` — USD valuation of the watched wallet's buy (SOL × cached spot).
- `copytrade/exits.py` — exit evaluators already exist (TP/SL/TRAIL/TIMER + mirror); sell-cap tiering by
  exit reason plugs in here.

**Net:** copy-trade OBSERVE currently books fills at the detection-time spot — it captures *some*
copy-latency drift but **overstates edge on the fast-gapping winners**, the exact mistake the honest-fill
work fixed on the model side. The soak cannot yet measure copy-slippage honestly.

---

## 5. Stories (priority order)

> PO assigns final ids; provisional below. Each rides the standard branch→PR→CI-green→squash flow and the
> **DoD is paper-proven on the VPS staging stack, not "works locally."** Keep the safety floor: OBSERVE
> stays observe; no real capital; §5 isolation intact.

### US-75 / AC-1 — Honest fills in copy-trade OBSERVE *(do first; pre-capital; zero-risk)*
Port the `_honest_entry_fill` discipline into the copy-trade observe path. Quote = the watched wallet's
fill price; **our fill = the curve/AMM state at `wallet_buy_ts + copy_latency`**; apply a cap →
`ENTRY_REJECTED` (PnL NULL, excluded from win-rate) if it gapped past.
- Touch: `copytrade/engine_runtime.py` (replace the bare `price_fn(event.mint)` entry with a
  quote/our-fill/cap model), `copytrade/position_opener.py` (book honest fill or reject),
  `copytrade/exits.py` (unchanged). Reuse `trading/tape_settler.py` logic as the reference.
- Cleaner-than-billy: model **our own-impact** at copy size, not just latency drift (billy's paper fill
  modelled latency only). At paper sizes it's ~0, but wire it now so it scales.
- **AC:** the soak emits `ENTRY_REJECTED` rows with `quote_price`, `fill_price`, `cap_pct`,
  `copy_latency_s`; rejected entries carry NULL PnL; a fast-gapping winner that we'd miss live is rejected
  in the soak. Tests assert OBSERVE never calls `place_buy_order`.

### US-75 / AC-2 — Per-copy slip telemetry + rejected-entry counterfactual settlement
Log `quote / our_fill / realized_slip / cap / copy_latency / 6002` per copy-buy. For every
`ENTRY_REJECTED`, **settle the forward counterfactual** off the recorder firehose (what did the token do
after we passed?) so the cap can be tuned from data.
- Touch: `copytrade/` telemetry + a settler that reads the recorder tape forward from the rejection ts.
- **AC:** a query yields, per rejected copy, the realized peak/return had we filled — the input to cap
  tuning. This is the solanatrilly improvement billy never had (it bought the denominator from Birdeye).

### US-75 / AC-3 — Conviction-tiered buy cap (wallet-rank) + urgency-tiered sell cap
Buy: tier the cap by the **watched wallet's v2-rank** (the #398 analog — top wallets → 30%, low →
tight/skip), plus a "don't chase >cap above the wallet's entry" gate. Sell: tier by **exit-trigger
urgency** (trailing/TP TIGHT → … → mirror/rug PANIC fast-widening), reusing the
`_SELL_SLIPPAGE_TIER_BY_TRIGGER` shape.
- Touch: new `copytrade/slippage.py` (config + resolver, port of `resolve_buy_slippage_bps`),
  `copytrade/exits.py` (sell-cap by reason), cohort-2.0 config block. Key on **live percentile**, not raw
  rank/score.
- Cleaner-than-billy: config-driven from cohort.json (typed Pydantic), **not** env-vars
  (`BUY_SLIPPAGE_BPS` etc.); parity-by-construction (one resolver shared by observe-fill and live-order).
- **AC:** resolver unit-tested against a golden window; top-wallet copies get the wide cap, book wallets
  the tight cap; sell cap widens with urgency; absent config ⇒ safe default (current behavior).

### US-75 / AC-4 — Exact-out copy buy (P8 execution) + 6002 decode
Implement `place_buy_order` over PumpSwap reusing `trading/pumpswap_ix.build_buy_instruction` and
`trading/sender.decode_anchor_error`. `max_quote_amount_in = position·(1+cap)` (exact-out); decode/log
6002/6003/6023. **Stays gated behind `mode != observe` — operator-flip only.**
- **AC:** dry-run/devnet buy builds with the correct cap; 6002 path decodes and logs; OBSERVE still never
  reaches it.

### US-75 / AC-5 — The empirical deploy gate (decision rule)
Run the honest-fills soak; deploy copy **capital only if measured realized copy-slip stays under the
threshold where v2's edge clears the placebo (~10–15%)**. Let the soak — not a-priori reasoning — set the
threshold. If it runs hotter, AC-3 wallet-rank tiering becomes mandatory to salvage the top-wallet subset.
- **AC:** a soak report with the realized-slip distribution + rejected-entry counterfactuals + the
  go/no-go call. This is the arbiter; do not flip capital without it.

---

## 6. Definition of Done (epic)
- AC-1 + AC-2 merged & running in the VPS OBSERVE soak; honest fills + counterfactuals visible in export.
- AC-3 resolver tested & config-driven from cohort.json; AC-4 buy path built and gated behind
  operator-flip; AC-5 soak report produced.
- Safety floor never crossed during the epic: OBSERVE stays observe, §5 isolation intact, solanaBilly
  untouched.

**DoD additions mandated by the Tester gate (§8) — binding:**
1. **Migration ships with AC-1:** `quote_price`, `fill_price`, `cap_pct`, `copy_latency_s` added to the
   copytrade position/rejection store; `ENTRY_REJECTED` added as a valid `exit_reason`/`exit_trigger`
   choice; `python manage.py migrate` passes in Docker CI.
2. **Golden fixture before AC-1 merges:** a known-gapping token fixture that goes **red without the fix,
   green with it** (`fill_price > quote_price·(1+cap)` ⇒ `ENTRY_REJECTED`, NULL PnL).
3. **Tier-1 replay gate passes:** honest-fill run over the real `slip_lab` corpus, `crashed > 0 ⇒ do not ship`.
4. **Zero-div (#405) + None/NaN (#358) guards explicitly unit-tested:** `quote_price=0` and
   `fill_price=None|NaN` must not crash and must return a defined result.
5. **`copy_latency_s` derivation pinned** (see §8 P1) — either `block_time` promoted onto `WalletTxEvent`
   or the field documented as clock-arrival-approximate; tested either way.
6. **OBSERVE isolation guard (`test_observe_safety_gate_ac613.py`) stays green** on every AC PR — no path
   reaches `place_buy_order`.
7. **`honest_fills_enabled` flag in `CopyTradeSettings`, default False** — the running v1/v2 soak is not
   disrupted at merge; AC-5 soak runs with it deliberately flipped on.
8. **VPS staging smoke:** OBSERVE engine runs one session with the flag on without crashing; export yields
   an `ENTRY_REJECTED` row or an explicit "no rejections this session" log.

## 7. Sequencing
**AC-1 → AC-2 first** (measurement + the learning loop; both pre-capital, zero-risk, highest leverage).
AC-3 next (policy). AC-4 only when P8 capital is on the table. AC-5 is the gate that authorizes the flip.

---

*Motivation for the implementer:* solanaBilly already paid for this knowledge in failed live trades — the
crib sheet in §3 is a gift, use it. But do not transliterate it. solanatrilly's edge is **config-driven
parity-by-construction** (one resolver, typed config, observe == live by construction) and a **forward
recorder that makes the rejected-entry counterfactual free**. Billy tuned caps from a bought Birdeye
denominator and env-vars; we tune from our own forward tape and cohort.json. Build it so the soak is an
honest measuring instrument first, and the cap falls out of the data — cleaner, typed, and parity-true.*

---

## 8. Tester review — binding revisions before dev handoff (2026-06-20)

**Verdict: NEEDS REVISION.** Sequencing, economics, and the solanaBilly crib are sound; the gaps are
engineering-spec, not strategy. The DoD additions in §6 (items 1–8) and the revisions below are **binding** —
the dev team implements against this section. Cross-referenced `buildout_directives.md` §3 (trouble-PRs) and
§4 (testing methodology). File:line anchors are from the live tree.

**Priority 1 — blocks AC-1 test design:**
- **Pin `copy_latency_s`.** `WalletTxEvent` (`copytrade/wallet_consumer.py:42-59`) carries only the
  clock-injected `timestamp`; the on-chain `block_time` is in `event.raw` (`helius_wallet_source.py:77`)
  but never surfaced. Either promote `block_time: Optional[float]` onto `WalletTxEvent` and populate it
  (`helius_wallet_source.py:68-78`), or define `copy_latency_s=None` when block_time is absent and document
  it. No ambiguous telemetry.
- **#405/#358 in AC-1's text:** `quote_price=0` or `fill_price=None/NaN` must not crash; guard the division
  (`quote_price>0` before `fill/quote-1`, per `trading/tape_settler.py:172` + solanaBilly
  `paper_monitor_tasks.py:312`) and coerce `None→nan` at the boundary. Test with a **real JSONB dict**, not
  a hand-built `np.nan` dict.
- **Honest fill is a new `copytrade/honest_fill.py`** modeled on solanaBilly `_honest_entry_fill`
  (`paper_monitor_tasks.py:259-304`) — the entry-check primitive (quote→fill→cap), **not**
  `trading/tape_settler.py`'s full `simulate_tape_exit` (wrong primitive; risks misuse).

**Priority 2 — blocks AC-1 merge gate:** migration (§6.1), `honest_fills_enabled` flag (§6.7), golden
fixture red→green (§6.2). All three are merge preconditions for AC-1.

**Priority 3 — clarify before AC-3 starts:**
- **AC-3 "live percentile" = the watched wallet's rank within the active cohort** (sorted by
  `precision`/v2-rank), **not** a model-score percentile — copy-trade has no per-token score. The resolver
  receives a rank fraction `[0,1]`. (solanaBilly `adaptive_gate.py:438-461` is the shape; the input differs.)
- **AC-3 "absent config → safe default"** must be numeric: **1500 bps (15%)**, asserted in test.
- **AC-2 "a query yields…"** → concrete: a `copytrade_rejected_entries` table (or fields on the position
  row) carrying `peak_return_pct`, `rug_outcome`, `forward_window_s`; a unit test asserts the settler
  populates them for a synthetic rejection (real parquet fixture, #358 guard; bounded forward window so an
  empty tape writes `outcome=None`, never a perpetual skip — #304).

**Per-AC test tiers (from §4 methodology):**
- **AC-1:** Tier-3 unit (`_honest_copy_fill` cap/boundary/zero/None cases) → golden fixture (known gapper,
  red→green) → Tier-1 replay over the `slip_lab` corpus (`crashed>0 ⇒ no ship`) → extend
  `test_observe_safety_gate_ac613.py` (cap breach ⇒ no live position row).
- **AC-2:** Tier-3 unit on a **real parquet** fixture; bounded forward window.
- **AC-3:** Tier-3 against a **captured live percentile/rank window**; warmup→default; sell-tier param table
  incl. PANIC 5000/7000/9000/9900.
- **AC-4:** Tier-2 `simulateTransaction` (devnet, 0 SOL) + assert `decode_anchor_error` on 6002; verify
  `max_quote_amount_in = position·(1+cap)` (not the token-side formulation, #376); reuse `trading/pumpswap_ix`
  fee-recipient/program resolution (#290/#288); invoke `verify_ghost_buy` (#365/#366).
- **AC-5:** no CI gate; output = a written report (slip distribution + rejected-entry counterfactual table +
  go/no-go).
