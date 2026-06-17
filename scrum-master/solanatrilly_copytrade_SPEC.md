# solanatrilly — Copy-Trade Dashboard (v1) — Build Spec & Consumption Contract

*For the agent building solanatrilly. Written 2026-06-17 by the research side (solanatrills).*
*This adds ONE new, self-contained feature: a copy-trade engine + dashboard tab that consumes a JSON list
of wallets we hand you and trades alongside them. It reuses apparatus you already have.*

---

## 0. Why this fits trilly perfectly (what you already have vs what's new)
You already have: a Helius-websocket firehose recording all token tapes at launch; a Birdeye feed of
graduated tokens; a click-to-download export for training data; and working buy/sell execution; all
config-driven. **This feature reuses all of that.** The ONLY genuinely new pieces are:
1. Subscribe (Helius) to a **list of WALLETS** we give you (not tokens).
2. **Filter precisely** for when one of those wallets BUYS a pump.fun token *on the bonding curve*.
3. Copy-buy, then **exit on our configurable TP/SL/curve rules** (we do NOT mirror their sells).
4. A **dedicated dashboard tab** with JSON upload, on/off, SOL size, and **per-wallet PnL**.

**Hard requirement: this must NOT clash with the existing firehose / model pipeline.** Run it as a
separate service/worker with its own Helius subscription, its own config namespace, and its own DB
tables. Its on/off switch affects only this engine.

### 0.1 Parity with the prediction pipeline (copy-trade is a peer, not a bolt-on)
Copy-trade is a **first-class trading pipeline, a sibling to the existing prediction (model) pipeline** —
it must have **all the same trading capabilities the prediction pipeline already has**, just driven by a
wallet list instead of a model:
- **Same execution path** — reuse the exact buy/sell apparatus the prediction pipeline uses (orders,
  slippage handling, fills).
- **Paper trading must be an available option (same as the prediction pipeline).** Copy-trade has the same
  **Paper (observe) ↔ Live** toggle the prediction pipeline has, exposed the same way in the UI/config.
  Paper mode is the default and runs the full logic (detect → "buy" → manage → "sell" → record PnL)
  placing **no real orders** (books fills at the live price). We must be able to run the whole copy-trade
  cohort in paper first, exactly like we can paper-trade the model pipeline.
- **Same config-driven control** and **same click-to-download export** (its trades/PnL export like the
  prediction pipeline's data does).
- **Runs side-by-side, concurrently, and fully independently** of the prediction pipeline — both can be ON
  at once; each has its own ON/OFF, its own positions, its own PnL, its own capital/limits. Neither blocks
  or interferes with the other (see §5 isolation). Think of it as a second strategy plugged into the same
  trading + dashboard + export chassis.

---

## 1. v1 scope (and explicit NON-goals — read this first)
**In scope (v1):**
- Consume our JSON cohort (wallets + trade config). Treat **all wallets identically**.
- Subscribe to those wallets; copy their **pump.fun curve buys** at our SOL size.
- Exit each position on configurable **TP / SL / near-curve-completion / max-hold**.
- A dashboard tab: **upload JSON, ON/OFF, SOL size, observe/live mode**, live status, **per-wallet PnL**.
- **Fresh-start on every upload**: a new cohort wipes the old one entirely (see §6).

**NOT in v1 (defer — say no to scope creep here):**
- ❌ No per-wallet manual controls (pause/keep individual wallets). All-or-nothing per cohort.
- ❌ No cross-cohort history / no "previous cohort" records kept. New upload = clean slate.
- ❌ No candle charts / TA. Simple tables + numbers only.
- ❌ No funder logic, no auto-retuning of TP/SL, no per-position manual exits.
- ❌ We will NOT need alerts/triggers to keep one wallet and drop another — if we want changes, **we
  upload a new JSON.** Your only job is to consume whatever JSON is current.

---

## 2. The consumption contract (the JSON we hand you)
File: `leaderboard.json` (we produce it; you provide an upload button). Schema:

```json
{
  "schema_version": "1.0",
  "cohort_id": "whale-ct-2026-06-17-v1",        // unique per upload; the wipe key (see §6)
  "created_at": "2026-06-17",
  "description": "...",
  "trade_config": {
    "mode": "observe",               // "observe" (paper) | "live". DEFAULT observe. See §10.
    "sol_size_per_trade": 0.25,       // SOL per copy-buy. Operator-overridable in the UI.
    "take_profit_pct": 200,           // sell if up >= this % vs our entry
    "stop_loss_pct": 40,              // sell if down >= this % vs our entry. TIGHT on purpose (see §4).
    "exit_before_graduation": true,   // sell as the token nears curve completion (avoid migration)
    "curve_completion_exit_pct": 90,  // when bonding curve is >= this % complete, sell
    "max_hold_seconds": 1800,         // hard time cap per position
    "max_concurrent_positions": 20,   // capital guard; ignore new signals beyond this
    "copy_only_pumpfun_curve_buys": true,  // CRITICAL filter (see §3)
    "copy_first_buy_only": true,      // only the wallet's FIRST buy of a token (entry), not adds
    "dedupe_token_across_wallets": true,   // if 2 watched wallets buy same token, buy ONCE
    "mirror_wallet_sells": false      // we use OUR exits, never copy their sells
  },
  "wallets": [
    { "address": "EohTPNGd...", "rank": 1, "precision": 0.38, "total_pump_buys": 255, "median_lead_min": 1.4 },
    ...   // ~10 wallets
  ]
}
```

- `trade_config` is the source of truth for trade params; the UI may **override** `sol_size_per_trade`,
  `take_profit_pct`, `stop_loss_pct`, and `mode` live (persist overrides to your config store).
- The `wallets[]` metadata (`precision`, `median_lead_min`) is informational for the dashboard; the engine
  only needs `address`.
- **on/off is runtime state**, NOT in the JSON (it's an operational toggle in the UI).

---

## 3. Engine behavior — the correctness-critical path (get this exactly right)
For each wallet in the cohort, subscribe via Helius (e.g. enhanced-websocket / `logsSubscribe` /
`accountSubscribe` on the wallet). On each of that wallet's transactions:

**BUY-COPY trigger — ALL of these must hold, or skip:**
1. The tx is a **SWAP where the watched wallet is the BUYER** (acquiring the token, spending SOL). Not a
   sell, not a transfer, not the wallet being a passive party.
2. The token is a **pump.fun token** (mint ends in `pump` / pump.fun bonding-curve program). 
3. The token is **still on the bonding curve (PRE-graduation)** — NOT already migrated to PumpSwap/Raydium.
   *This is essential:* our edge is curve entry. A buy on an already-graduated token is the wrong signal
   (we found wallets that only buy at/after graduation — those are useless; do not copy them).
4. It is the wallet's **first** buy of this token in this cohort session (`copy_first_buy_only`).
5. `dedupe_token_across_wallets`: if we already hold (or are opening) this token, **do not** open a second
   position — record the additional triggering wallet but buy once. Attribute the position to the **first**
   triggering wallet (for PnL).
6. We are under `max_concurrent_positions`.

**On a valid trigger:** market-buy `sol_size_per_trade` of the token ASAP (your existing buy path), at the
current curve price. Record entry (cohort_id, wallet, mint, ts, price, sol_in, tx).

**Exit (we manage it ourselves — never wait for the wallet to sell):** monitor each open position; exit on
the **first** of:
- price ≥ entry × (1 + `take_profit_pct`/100)  → reason `TP`
- price ≤ entry × (1 − `stop_loss_pct`/100)    → reason `SL`
- bonding curve ≥ `curve_completion_exit_pct`% complete (if `exit_before_graduation`) → reason `CURVE`
- held ≥ `max_hold_seconds` → reason `TIMER`

Use your existing sell path. Record exit (ts, price, sol_out, reason, realized PnL).

**Never** copy the wallet's sells/adds. We only take the entry signal; the exit is ours.

---

## 4. Trade-param guidance (why the defaults are what they are)
- **TIGHT stop-loss is deliberate.** The wallets hit graduation ~25–38% of the time; we lose on the rest.
  At a low hit-rate, a *generous* SL bleeds us on every miss, so a **tight SL lowers the breakeven** — the
  opposite of "give it room." Default 40%; we'll tune it from live PnL. Don't widen it without data.
- **TP is generous** because graduating tokens ride the curve up ~+170% on average — we want to capture
  that, with `exit_before_graduation` as the natural take-profit (sell as it nears completion, before the
  migration mechanics). TP is mostly a backstop.
- **SOL size** small by default; operator sets it in the UI.

---

## 5. Separation from the existing pipeline (non-negotiable)
- **Separate service/worker** (e.g. `copytrade_engine`) — not inside the recorder or the model-inference
  loop.
- **Separate Helius subscription** (subscribes to wallet addresses, not the token firehose). If you must
  share a Helius plan/connection, use a distinct subscription/channel; prefer a separate connection so a
  problem in one can't stall the other.
- **Separate config namespace** (`copytrade.*`) and **separate DB tables** (§9). No shared mutable state
  with the firehose.
- The copy-trade **ON/OFF** switch gates only this engine. The firehose, graduation feed, and model
  pipeline keep running regardless.

---

## 6. Cohort lifecycle & fresh-start (this is the part that prevents mess)
A `cohort_id` identifies the active cohort. **There is exactly one active cohort at a time.**

**Uploading a new JSON = full replacement:**
1. Require the engine to be **OFF** to upload (or auto-stop it on upload).
2. **Close/settle any open copy positions** (market-sell; or in observe mode, mark closed at current
   price).
3. **Purge all copytrade records** for the old cohort (wallets, positions, trades, pnl). v1 keeps **no
   history** across cohorts — clean slate, by design.
4. Validate + load the new JSON; subscribe to the new wallets.
5. Operator flips **ON**.

So: "what happens to the previous cohort when we upload a new one?" → **it's wiped, positions closed, zero
history retained.** Simple and predictable. (History/versioning is a deliberate v2 item.)

---

## 7. Config-driven architecture
Conform to trilly's existing config-driven pattern. The cohort JSON + UI overrides populate a
`copytrade` config block (mode, sol_size, tp, sl, exit params, on/off, active cohort_id). The engine reads
config at runtime; changing config (or uploading a new cohort) reconfigures it without code changes —
exactly like the rest of trilly.

---

## 8. Frontend — the Copy-Trade dashboard tab (v1, keep it simple)
A new tab/page **"Copy Trade"**, reusing your dashboard component library. Sections:
1. **Cohort & controls (top bar):**
   - Upload JSON (drag/drop or file-picker) → schema-validate → load (with the §6 wipe confirm).
   - **ON / OFF** master toggle. **Mode** toggle: Observe (paper) / Live.
   - Editable: **SOL size**, **Take-profit %**, **Stop-loss %** (persist as overrides).
   - Status chips: engine state, mode, # wallets watched, # open positions, cohort_id, created_at.
2. **Per-wallet PnL table (the key view — "did this wallet make us money?"):**
   one row per wallet: address (short, copyable), #trades, win-rate, total realized PnL (SOL & %),
   avg hold, last-trigger time. Sortable by PnL. This is how we judge the cohort live.
3. **Open positions table:** token (mint, link), triggering wallet, entry time/price, current price,
   unrealized PnL %, time held, curve % complete.
4. **Recent trades log:** token, wallet, entry→exit, realized PnL, exit reason (TP/SL/CURVE/TIMER), mode.
5. A small **cohort summary**: total trades, overall win-rate, net PnL (SOL), since cohort start.

No candle charts, no per-row action buttons, no per-wallet toggles in v1.

---

## 9. Data model (suggested tables, all `copytrade_`-prefixed, separate from existing)
- `copytrade_cohort` — cohort_id, created_at, description, trade_config (json), uploaded_at, active(bool).
- `copytrade_wallets` — cohort_id, address, rank, precision, median_lead_min.
- `copytrade_positions` — id, cohort_id, mint, trigger_wallet, status(open/closed), mode,
  entry_ts, entry_price, sol_in, exit_ts, exit_price, sol_out, exit_reason, realized_pnl_sol, realized_pnl_pct.
- `copytrade_pnl_by_wallet` — (view or rollup) cohort_id, address, n_trades, win_rate, total_pnl_sol, avg_hold_s.
Fresh-start (§6) truncates these for the old cohort.

---

## 10. Observe-first & safety (important — don't skip)
- **Default `mode: "observe"` (paper).** In observe mode the engine does everything (detect, "buy",
  manage, "sell", record PnL) but **places no real orders** — it books fills at the live price it would
  have gotten. This is how we validate that (a) we detect the wallet's curve buy fast enough, (b) fills are
  realistic, (c) the live PnL matches the offline edge — **before any capital.**
- **Why this matters:** this strategy is validated *offline only*. In this project, offline edges have
  repeatedly failed live. The observe soak is the arbiter. **Do not enable `live` mode until the operator
  flips it after the observe soak looks good.** Make `live` a deliberate, clearly-labeled switch.
- Per-trade SOL size small; `max_concurrent_positions` caps exposure.

---

## 11. Handoff / how you get the data
- The cohort file: `solanatrills/analysis/whale_graph/out/leaderboard.json` (we hand it over / you provide
  the upload UI; we'll upload through it). Schema = §2. ~10 wallets in v1.
- We (research) regenerate it periodically (the leaderboard decays ~4%/week, so expect refreshes). Each
  refresh is a new `cohort_id` → your §6 fresh-start handles it.
- **What we need back from you for the soak:** the per-wallet PnL + the trades log (so we can compare live
  results to the offline precision). The click-to-download export you're already building is the natural
  channel — include the `copytrade_positions` table in it.

---

## TL;DR for the builder
New **Copy Trade** tab + a **separate** engine that: takes our JSON of ~10 wallets, subscribes to them on
Helius, and **only when one of them buys a pump.fun token still on the bonding curve**, copy-buys our SOL
size and exits on **our** tight-SL / generous-TP / near-completion rules (never mirrors their sells).
Config-driven, ON/OFF, observe-by-default, **fresh-start on every new upload (wipe the old cohort)**, and a
**per-wallet PnL** table so we can see which wallets actually made us money. Keep it isolated from the
firehose. That's v1.
