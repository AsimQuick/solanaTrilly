---
file: ops/firehose_activation_log.md
purpose: Project-wide live-firehose activation ledger — budget tracking for Birdeye and Helius WS activations (PRD §15.7)
sprint: sprint-5 / sprint-7
story: US-7 AC-7.1 AC-7.2 AC-7.3, US-22 AC-22.2, US-32 AC-32.2
status: active
created-by: dev-team
last-updated: 2026-06-17
---

# Firehose Activation Ledger

> **API keys are in `.env` — never committed to this ledger or any tracked file.**
> Keys are referenced by their env-var names (`BIRDEYE_API_KEY`, `HELIUS_API_KEY`) only.
> Confirmed present in `.env` by the operator (po-requests.md item 3). See PRD §15.7.

## Project-Wide Budget

| Source    | Total | Used | Remaining |
|-----------|------:|-----:|----------:|
| Birdeye   |    10 |    4 |         6 |
| Helius    |    10 |    3 |         7 |
| **Total** |**20** | **7**|    **13** |

Both Birdeye and Helius API keys are provisioned in `.env` (gitignored + untracked).
They are **never committed** — not here, not in any source file, not in any workflow file.
GitHub Actions uses `VPS_SSH_KEY`, `VPS_HOST`, and `VPS_USER` repo secrets for CD;
firehose keys remain in `.env` only.

## Per-Activation Protocol

Every activation is **deliberate, time-boxed (≤ 30 min default; adjustable by explicit PO
decision), and logged here as a PR-reviewed entry** so the remaining count is always visible
in git history.

### Activation table schema

| Column | Description |
|--------|-------------|
| `date` | ISO-8601 date of activation |
| `role/agent` | Who opened the firehose (dev-team / product-owner / tester) |
| `ws` | Which WebSocket: `Birdeye` or `Helius` |
| `purpose` | Why this activation was necessary (1-sentence) |
| `duration` | Actual elapsed time (≤ 30 min unless PO-approved extension) |
| `count_remaining` | Updated remaining budget after this row (Birdeye and Helius separately) |
| `fixtures_banked` | Path(s) of durable fixtures committed as part of this activation's PR |

## HARD RULE — Every Activation MUST Bank Durable Fixtures

> **Every activation MUST bank durable fixtures** (a tape sample, a detection sample, or golden
> vectors) into the lake / golden set. The spend compounds into the replay corpus (PRD §11) — after
> which all further dev replays offline and never needs to go live again for that scenario.
>
> An activation with no committed fixture is a budget burn with no return. It will be flagged by the
> Tester and blocked from merge until a fixture is added.

The firehose is the **source of fixtures, not the dev loop**. Spend a live window to *bank reality*,
then build against it forever.

## Activation Log

| Date | Role/Agent | WS | Purpose | Duration | Count Remaining (Birdeye / Helius) | Fixtures Banked |
|------|-----------|-----|---------|----------|-------------------------------------|-----------------|
| 2026-06-15 | dev-team | Birdeye SUBSCRIBE_TXS | First firehose spend (US-22 AC-22.2, retrospective D4): bank a real graduated pump.fun token's PumpSwap swap tape as the durable golden fixture the offline replay/parity suite runs against forever. | 3.7 min (≤ 30 min box) | 9 Birdeye / 10 Helius | `lake/golden/birdeye_subscribe_txs/dt=2026-06-15/E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump_pumpswap_golden.jsonl.gz` — 40 real PumpSwap (`source=pump_amm`) swaps for mint `E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump` (SPCX, a graduated pump.fun token), captured live, banked raw (immutable truth, §6.4.1). 23 buy / 17 sell, 40 distinct signatures, ~186 s of tape. |
| 2026-06-16 | dev-team (operator) | Birdeye SUBSCRIBE_TXS | US-22 AC-22.3 end-to-end live proof: real PumpSwap swaps flow Birdeye SUBSCRIBE_TXS → BirdeyeSwapSource → map_birdeye_swap → TapeRecorder → `swaps` rows + jsonl.gz on the VPS `listener` container (`-p solanatrilly`). Proves the live recorder path that the offline US-21 replay/parity suite mirrors. | ≤ 150 s box | 8 Birdeye / 10 Helius | `swaps` table on VPS: 8 real `source=pump_amm` swaps for mint `H9L9apxE8RREZZgTaNLmGeUfCYJQfHBwQxuXzvPNpump` (graduated pump.fun token), recorded live (signer=owner, base/quote reserves NULL per §3.3/§7.1) + the AC-22.2 golden fixture remains the durable offline replay anchor. |
| 2026-06-17 | dev-team | Helius Enhanced TX | US-32 AC-32.2 G2(b) raw-truth spot-check (§7.6): the project's ONE budgeted Helius activation. Fetched Helius Enhanced Transactions for the 40 Birdeye golden signatures (mint `E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump`), confirmed 40/40 coverage and 100% side-parity via pool-flow detection (pool account `DZxWcyPpTyr2NTfmEN2xAUSCb77t1ZLpkg63PbpbKmbC`). Banked as slim raw fixture. CI cross-check (`test_g2b_raw_truth_ac322.py`) runs OFFLINE forever — P5 gate never depends on a live Helius read. | 2s (≤ 30 min box) | 8 Birdeye / 9 Helius | `lake/golden/helius_decoded_txs/dt=2026-06-16/E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump_helius_g2b_raw.jsonl.gz` — 40 Helius-decoded PumpSwap txs (23 buy / 17 sell), token-transfer + native-transfer + account-change fields, raw = immutable truth (§6.4.1). |
| 2026-06-17 | dev-team | Helius transactionSubscribe (program-wide) — INFRASTRUCTURE DELIVERY; live window PENDING | US-34 AC-34.3 birth-tape first live bring-up infrastructure: banked deterministic synthetic golden fixture and wired the offline CI gate. Live program-wide WS window budgeted for a 20-min box when opened; carries to next sprint per AC if aborted. Budget NOT consumed until the live window opens. | N/A — offline infrastructure delivery | 8 Birdeye / 9 Helius (unchanged — no live activation yet) | `lake/golden/helius_birth_tape/dt=2026-06-17/helius_birth_tape_pregrad_golden.jsonl.gz` — 6 deterministic synthetic Helius transactionNotification rows (3 pre-grad trades + 1 migrate + 2 post-grad trades) for mint `6SdsCkVYLUUz9gbrpFxRJE2QCjHmvAMWK8Nsh9MZLh2b` (sha256-derived, AC-34.3 golden anchor). CI gate (`test_birth_tape_live_fixture_ac343.py`) runs OFFLINE forever against this fixture — never needs a live read. |
| 2026-06-20 | operator (Claude, live-run) | Birdeye `SUBSCRIBE_MEME` (graduation) + Helius birth-tape (collection) | **US-76 DoD window-1** — prove the live graduate-inference chain after the parity resolution (PR #334: single-spot USD + unified sort + lab serving bundle): detect → assemble pre-grad tape → score against the frozen reference grid → gate → paper, observe/paper only (`trading_enabled=False`, copy `mode=observe`). 90-min box. **Result:** chain proven THROUGH THE GATE — collection healthy (~1.2k mints / 81k swaps), 5 real graduations scored with N>0 tape (parity-correct serving), curve-life instant-skip fired; all 5 gate-failed below the 30/day cut (0.7916) → no paper trade (a no-edge FINDING, §8). | ~90 min box | 7 Birdeye / 8 Helius | `ops/firehose_window_evidence_2026-06-20_us76_dod.md` — distilled score-join evidence (5 scores + gate outcomes + chain health). Pre-grad tapes were in-memory (AC-3 durable store deferred), so no tape golden bankable. |
| 2026-06-20 | operator (Claude, live-run) | Birdeye `SUBSCRIBE_MEME` (graduation) + Helius birth-tape (collection) | **US-76 DoD window-2** — observe the PAPER LEG after the paper-leg fixes (PR #335: post-grad newest-first/recency + `per_day_target` config knob), calibrated to **50/day** (rank_cut 0.6976, a published depth_menu operating point — lab-sanctioned live calibration). 2-h box; monitor auto-stopped on first settled paper position. **Result:** ✅ **FULL DoD chain observed** — mint `5NgDxD1en3YXvS9amAtgb15nc4oRfuGxomcAfu4wpump` score=**0.7179 gate=PASS** → paper-buy ($25 observe @ 0.0000399664) → paper-sell (settled, pnl −2.25%, AUTO_SELL_TIMER, held 2s) → `trading_positions` CLOSED/observe. Safety floor held (`trading_enabled=False` throughout). | ~12 min to first settle (2-h box) | 6 Birdeye / 7 Helius | `ops/firehose_window_evidence_2026-06-20_us76_dod.md` (window-2 section) — the settled paper position + gate-pass score-join + paper-buy/sell lines. Note: `held=2s/peak=0.00%` ⇒ thin post-grad tape at entry (sub had just opened) — valid settle, refinement noted. |

### Activation detail — 2026-06-17 Helius transactionSubscribe infrastructure (AC-34.3)

- **Status:** Offline infrastructure delivered; live WS window PENDING (carries per AC).
- **What was delivered:**
  - `tools/helius_birth_tape_activate.py` — deliberate, time-boxed Helius transactionSubscribe
    activation tool with `bank_notifications()` + `load_birth_tape_fixture()` pure helpers.
  - `lake/golden/helius_birth_tape/dt=2026-06-17/helius_birth_tape_pregrad_golden.jsonl.gz` —
    deterministic synthetic golden fixture: 6 Helius transactionNotification rows (3 pre-grad
    TradeEvent buys/sells + 1 Migrate + 2 post-grad TradeEvent buys/sells) for
    mint `6SdsCkVYLUUz9gbrpFxRJE2QCjHmvAMWK8Nsh9MZLh2b` (sha256-derived from seed
    `helius_birth_tape_golden_mint_ac343`), graduation anchor `GRADUATED_BT=1_750_100_100`.
  - `core/tests/test_birth_tape_live_fixture_ac343.py` — 11 offline tests that run against the
    golden fixture; CI gate is OFFLINE by construction and never blocks on a live read.
- **Budget:** No Helius activation consumed.  Budget remains **8 Birdeye / 9 Helius**.
  The `Helius 9→8` decrement occurs when `connect_and_capture()` is invoked for the first time.
- **Live window plan:** 20-min time-box, program-wide `transactionSubscribe` on
  `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.  Requires a graduating token within the
  window.  Per AC-34.3: if the window is aborted/short the offline gates still gate the merge.

### Activation detail — 2026-06-17 Helius Enhanced TX (G2(b) AC-32.2)

- **What API:** Helius Enhanced Transactions REST API (`POST https://api.helius.xyz/v0/transactions`).
  This is a REST read, NOT a WebSocket firehose subscription, but is counted as the project's ONE
  budgeted Helius activation for G2(b) (CLAUDE.md — 'G2 needs one activation').
- **Token:** `E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump` — the AC-22.2 banked golden token.
  No new token discovery; we used the existing Birdeye fixture's 40 signatures.
- **Method:** Single batch POST of all 40 Birdeye golden signatures → Helius returned 40 decoded txs.
- **Coverage:** 40/40 (100%) — every Birdeye signature confirmed in Helius decode.
- **Side parity:** 40/40 (100%) using pool-flow detection: the PumpSwap pool's base-token account
  `DZxWcyPpTyr2NTfmEN2xAUSCb77t1ZLpkg63PbpbKmbC` appears in ALL 40 MINT token-transfers.
  Side is determined by whether the pool SENDS (→ buy) or RECEIVES (→ sell) the MINT token.
  Result: 23 buy / 17 sell = exact match with Birdeye fixture (23 buy / 17 sell).
- **Side note on routing txs:** 10/40 transactions are routed through aggregators (OKX DEX Router,
  etc.). The pool-flow approach correctly classifies all 10 without a feePayer heuristic.
- **Fixture schema:** slim raw format — token_transfers, native_transfers, account_token_changes
  (filtered to MINT + wSOL mints only); instruction data omitted to keep fixture compact.
- **Tooling:** `tools/helius_g2b_activate.py` (deliberate, time-boxed, ledgered).
- **Budget after this row:** **8 Birdeye / 9 Helius**.

---

## PLANNED: Helius wallet-subscription — Copy-trade LIVE capital activation (AC-68.2)

**Status:** NOT YET ACTIVATED — planned for operator-driven Cutover (PRD §16).
This entry is logged here per AC-68.2 BEFORE any spend so the activation protocol
is defined and the remaining budget count is visible in git history.

**What WS:** Helius `accountSubscribe` on the copy-trade trading-wallet address.
**Purpose:** Real-time confirmation of the copy-trade engine's trading-wallet balance
as the LIVE Helius wallet-subscription for capital gating (copy-trade LIVE toggle, SPEC §10).
**When activated:** At Cutover (PRD §16) — when the operator provisions the
trading-wallet secret and explicitly flips `trading_enabled=True` via the capital
activation protocol. NOT this sprint (sprint-13 is OFFLINE/PAPER by construction).
**Time-box:** 20-minute subscription window (default budget rule; PO may extend).
**Budget decrement:** Helius 9→8 (1 activation). Occurs when the wallet-subscription
is first opened. Budget is NOT consumed until that moment.
**Current budget:** 8 Birdeye / 9 Helius (unchanged — not yet activated).
**Fixtures to bank:** Trading-wallet balance snapshot at the moment of activation
(durable fixture per the HARD RULE — every activation banks a durable fixture).

### Activation detail — 2026-06-15 Birdeye SUBSCRIBE_TXS

- **What WS:** Birdeye `SUBSCRIBE_TXS` (`queryType: simple`) on `wss://public-api.birdeye.so/socket/solana`.
- **Token:** `E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump` (symbol SPCX) — a graduated pump.fun token (mint ends `pump`) actively trading on the PumpSwap AMM (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`). Selected via Birdeye REST trade-source check (84 % of recent swaps on `pump_amm`) — no firehose spent on discovery.
- **Filter:** only swaps tagged `source = pump_amm` for the subscribed mint were banked (the graduation destination, CLAUDE.md). Multi-leg routes de-duplicated on `txHash`.
- **Fixture schema:** the exact raw Birdeye SUBSCRIBE_TXS payload (inner `data` of each `TXS_DATA` envelope), one JSON object per gzipped line — Birdeye-native fields (`blockUnixTime`, `txHash`, `tokenAddress`, `tokenPrice`, `volumeUSD`, `owner`, `side`, `from`/`to` legs, `poolId`, `blockNumber` …). Raw = immutable truth; normalized views are re-derivable.
- **Tooling:** `tools/firehose_activate.py` (deliberate, time-boxed, NO synthetic fallback — banks reality or nothing).
- **Budget after this row:** **9 Birdeye / 10 Helius**.

---

## Live Firehose Runtime Spine (observe/paper) — feat/live-firehose-spine

The live runtime that ties **collection → graduation → score → paper-trade** is wired
in this branch. It is **OBSERVE/PAPER ONLY** and gated by
`PipelineState.firehose_active`. It **never places real orders** and **never mutates**
`firehose_active`, `scoring_enabled`, or `trading_enabled`.

### Components
- `core/tape/birdeye_graduation_source.py` — `BirdeyeGraduationSource(DataSource)`:
  Birdeye new-listing/graduation WS (`SUBSCRIBE_TOKEN_NEW_LISTING` → `TOKEN_NEW_LISTING_DATA`,
  both config-driven). Emits `MEME_DATA` graduation events for `DetectionConsumer`.
- `core/management/commands/run_firehose.py` — the gated daemon (collection via
  Helius birth-tape recorder; graduation via Birdeye new-listing → DetectionConsumer;
  scoring scheduler over the 20 `PRE_FEATURE_NAMES` via the active `BlendScorer`;
  paper-trade via the P8 apparatus).
- `core/firehose/spine.py` — deterministic scoring + paper-trade helpers
  (`assemble_pregrad_features`, `score_pregrad`, `gate_passes`, `settle_paper_trade`)
  with the `RealCapitalGuardError` paper-only invariant.
- `tools/firehose_state.py` + `core/management/commands/firehose_state.py` — the
  operator ON/OFF toggle for `PipelineState.firehose_active` (only that flag).

### Operate the firehose
```bash
# Turn the firehose ON (operator-gated; only touches firehose_active):
docker compose run --rm web python manage.py firehose_state on
#   or via shell-redirect with an env var:
docker compose run --rm -e FIREHOSE_STATE=on web python manage.py shell < tools/firehose_state.py

# Run the gated daemon (OBSERVE/PAPER; bounded example):
docker compose run --rm web python manage.py run_firehose --max-runtime-seconds 1800

# Turn the firehose OFF:
docker compose run --rm web python manage.py firehose_state off
```

### Safety guarantees
- `trading_enabled` is **False** (default) — paper fills are booked at the observed
  tape price; the real RPC/Sender send path is **structurally unreachable**
  (`core/firehose/spine.py` imports neither `trading.sender` nor `trading.execution_core`;
  `assert_paper_only()` refuses to run if `trading_enabled` is True).
- No firehose budget is spent by this runtime spine itself; the live WS connections
  are only opened while `firehose_active=True` (operator-gated).

### Interface assumptions to confirm during the live window
- **Birdeye graduation frame format** — the exact subscribe `type`
  (`SUBSCRIBE_TOKEN_NEW_LISTING`) and inbound frame `type` (`TOKEN_NEW_LISTING_DATA`),
  plus the mint/pool/blockTime key spellings, are **assumed** and made **config-driven**
  (`detection.graduation_subscribe_type` / `graduation_data_type` /
  `graduation_subscribe_data` / `event_source`). Raw frames are debug-logged
  (`[FIREHOSE] graduation raw-frame: …`) so the live shape can be confirmed and the
  config adjusted **without a code change**.

---

## 2026-06-20 — US-79 paper soak (24h target, operator-directed)

**Activation:** Birdeye `SUBSCRIBE_MEME` (graduation) + Helius birth-tape (collection),
driven by the new persistent **`inference_engine`** service (gated on
`PipelineState.firehose_active`). Started from the dashboard Inference Start button
(`POST /api/control/inference/ {on:true}`) — no shell. Copy engine on (observe, cohort
`copy_2026-06-20_jun_deploy`, 20 wallets) concurrently.

**Purpose:** the operator-directed 24-hour PAPER soak of BOTH engines (pregrad inference
+ copy-trade), `trading_enabled=False` throughout. AC-3 durable tape banks the full
collection tape to `lake/tapes` so the soak yields a replayable corpus + populates the
US-78 swaps surface.

**Pre-soak verification (this session):** flip-on → engine ACTIVE, Helius collection
streaming (buffer 1→2,362 swaps in ~90s), graduation source subscribed, AC-3 banking
(`dt=2026-06-20/part-0.jsonl.gz`). flip-off → idled cleanly, no spend. Swaps export over
the banked tape: **5,185 rows** (was 0 — AC-3 → swaps surface proven live).

**Teardown (per HARD RULE):** stop via dashboard Inference Stop (or
`POST /api/control/inference/ {on:false}`) + copy engine off after ~24h. The persistent
service then idles (no spend) — no container to `docker rm`. **Do NOT deploy during the
soak unless necessary** (a deploy recreates `inference_engine`; it self-resumes since
`firehose_active` persists, but causes a ~30s gap).
