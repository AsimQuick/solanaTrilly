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
| Birdeye   |    10 |    2 |         8 |
| Helius    |    10 |    1 |         9 |
| **Total** |**20** | **3**|    **17** |

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

### Activation detail — 2026-06-15 Birdeye SUBSCRIBE_TXS

- **What WS:** Birdeye `SUBSCRIBE_TXS` (`queryType: simple`) on `wss://public-api.birdeye.so/socket/solana`.
- **Token:** `E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump` (symbol SPCX) — a graduated pump.fun token (mint ends `pump`) actively trading on the PumpSwap AMM (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`). Selected via Birdeye REST trade-source check (84 % of recent swaps on `pump_amm`) — no firehose spent on discovery.
- **Filter:** only swaps tagged `source = pump_amm` for the subscribed mint were banked (the graduation destination, CLAUDE.md). Multi-leg routes de-duplicated on `txHash`.
- **Fixture schema:** the exact raw Birdeye SUBSCRIBE_TXS payload (inner `data` of each `TXS_DATA` envelope), one JSON object per gzipped line — Birdeye-native fields (`blockUnixTime`, `txHash`, `tokenAddress`, `tokenPrice`, `volumeUSD`, `owner`, `side`, `from`/`to` legs, `poolId`, `blockNumber` …). Raw = immutable truth; normalized views are re-derivable.
- **Tooling:** `tools/firehose_activate.py` (deliberate, time-boxed, NO synthetic fallback — banks reality or nothing).
- **Budget after this row:** **9 Birdeye / 10 Helius**.
