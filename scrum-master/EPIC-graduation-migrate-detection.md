# EPIC — Graduation Detection on the pump.fun `migrate` Instruction

**Status:** APPROVED (tester GO, 2026-06-21) — dev-team build dispatched
**Workflow:** live hotfix/epic-PR (NOT sprint ceremony)
**Owner:** main orchestrator (operator-directed)

---

## Problem (measured live on VPS, 2026-06-21)

Live graduation detection uses `BirdeyeGraduationSource` — `SUBSCRIBE_NEW_PAIR` filtered to
`data.source=="pump_amm"` (PumpSwap pool creation). PumpSwap is a **permissionless** AMM, so this
captures **every** new PumpSwap pool, not just pump.fun graduations.

- Clean 3h window: **306 "graduations", only 27 (9%) real pump.fun tokens; 279 (91%) arbitrary
  non-pump.fun pools** (e.g. a generic "FIFAWorldCupCoin" pool).
- Real pump.fun grads score ~33% (climbing); the 91% noise scores 0.4% and pollutes `tokens`
  + wastes a score-tick deferring them every 5s.
- Birdeye NEW_PAIR `source` is the **AMM/DEX**, not the launchpad — there is **no** pump.fun
  filter in NEW_PAIR (verified vs docs + 319 stored frames + the captured fixture).
- Mint-suffix (`…pump`) is unreliable: only **75.4%** of 119,083 graduated pump.fun tokens in the
  training set (`solanatrills/tokens_complete.csv`) end in `pump`. Rejected.

## Fix

Detect graduation from the pump.fun program's own **`migrate` instruction** (program
`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) via the **already-open** Helius `transactionSubscribe`
collection connection. 100% pump.fun, event-driven, zero AMM noise.

## Foundation already in repo

- `core/detection/helius_reconciler.py` — `MigrateReconciler` + `PUMP_PROGRAM`,
  `MIGRATE_INSTRUCTION="migrate"`. Built but DORMANT (no live source feeds it). Stays as the
  gap-recovery backstop.
- `core/tape/helius_birth_tape_source.py` — live Helius `transactionSubscribe` on `6EF8` with
  `decode_helius_notification`. **This connection already receives migrate frames** and drops them
  silently (`_is_trade_log()` → False). This is where the migrate decoder is added.
- `core/management/commands/run_firehose.py` `_build_graduation()` (~1372) — wiring point.
- `core/detection/consumer.py` — `DetectionConsumer` persists `MEME_DATA` events unchanged.

## solanaBilly reference (`/Users/asim/NoIcloud/solanaBilly`)

- `app/tasks/trading_tasks.py` — billy's graduation signal = reading the bonding-curve `complete`
  flag: `_read_curve_price()` → `(price, graduated, curve_state)`; `PUMP_FUN_PROGRAM_ID`:111;
  AC-33.5 graduation-aware retry (~3885-3913), `GRADUATION_DETECTED`. This is the POLL approach —
  reference only; we use push (migrate ix), not poll.
- `app/services/helius_derivation.py:163` — `is_complete`.
- `app/services/helius_listener.py` — proven Helius WS reference.

## Validated design (tester, GO)

### Primary mechanism: migrate-ix push (NOT curve-state poll)
The migrate ix fires once per graduation from the pump.fun program — zero false positives, push not
poll, data already flowing through the collection connection. Billy's curve poll is a sell-path guard
(`getAccountInfo` per token), does not scale, and adds an RPC dep not in the trilly stack.

### SHOWSTOPPER-1 (must close first): capture a REAL migrate frame
The only migrate fixture (`helius_migrate_ac161.json`) is **synthetic** (already-decoded events).
No real `transactionNotification` migrate frame exists. The decoder is unwritten + unverified against
live data. **Step 0:** capture ≥1 real migrate frame (10-min standard-endpoint capture, no firehose
activation) and bank it before/with building the decoder.

### SHOWSTOPPER-2: extract mint + pool from accounts, NOT logs
A migrate ix emits only `"Program log: Instruction: Migrate"` — no Borsh event struct to decode.
- Graduated SPL mint ← `params.result.transaction.transaction.message.accountKeys`
  (pump_amm `create_pool` IDL: `base_mint` is CPI account index 3; confirm against the real frame).
- New pool address ← walk `meta.innerInstructions` for the CPI to
  `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`; pool PDA is its first account.
- `block_time` ← slot anchor.
Account indices are ESTIMATES until verified on the real frame — flag in PR.

### SHOWSTOPPER-3: reuse the existing connection
The collection `HeliusBirthTapeSource` already streams all `6EF8` txs incl. migrate. **Prefer a
fan-out/second decoder on the same stream** over a second WebSocket (saves a subscription slot).
A dedicated `HeliusMigrateSource` on its own connection (same endpoint/params) is acceptable if
fan-out is too complex.

### Routing
Emit migrate events in the **exact `map_new_pair_frame` shape**: `type="MEME_DATA"`,
`graduated=True`, `address=<SPL mint>`, `source` matching `config.detection.filter.source`
(`pump_dot_fun`), `poolAddress`, `blockTime`, `graduated_block_time`, `decimals`, `raw`; stamp
provenance `dex_source="helius_migrate"`. Route through `DetectionConsumer` (primary).

### Birdeye = secondary, not dropped
Keep `BirdeyeGraduationSource` wired as the gap-recovery secondary (feeding `MigrateReconciler` or a
lower-priority DetectionConsumer). Add a `min_liquidity` floor (~5000 USDC) to cut obvious non-pump
pools (real grads exit with ≥$7k liquidity). Dropping Birdeye = single point of failure on a Helius
reconnect gap.

### Score-join safety
The collection buffer is keyed by the SPL mint from the decoded TradeEvent; the migrate ix references
the same SPL mint. `self._tape.get(mint)` will hit. The bonding-curve-PDA gotcha does NOT apply
(PDA ≠ mint). Verify on the first real frame.

## Build scope

**Modify:**
- `core/tape/helius_birth_tape_source.py` — add `decode_helius_migrate_event(data) -> dict|None`
  (pure; detect `"Instruction: Migrate"` in logMessages; extract mint + pool; return MEME_DATA shape).
  Add the migrate source/fan-out.
- `core/management/commands/run_firehose.py` — `_build_graduation()`: migrate source primary +
  Birdeye secondary wired to `MigrateReconciler`; add the secondary reconciler task.
- `core/tests/fixtures/` — bank the real migrate-tx frame.
- new `core/tests/test_helius_migrate_source.py`.

**No change:** `helius_reconciler.py`, `consumer.py`, `birdeye_graduation_source.py` (demoted),
`HeliusBirthTapeSource.connect()` subscription params.

## DoD
1. Real migrate `transactionNotification` frame banked as a fixture.
2. `decode_helius_migrate_event` passes unit tests vs real + synthetic frames.
3. Migrate events accepted by `DetectionConsumer` unchanged.
4. `_build_graduation()` builds migrate primary + Birdeye secondary (gap-fill, retained).
5. Full test suite green (no regression to collection/scoring/post-grad/reconciler).
6. Deployed to VPS staging; operator sees ~0 `pump_amm` noise tokens over 1h; real grads appear with
   `dex_source="helius_migrate"`.
7. 3h window: noise ratio 91% → ~0%; catch rate cross-checked vs Birdeye secondary.

## Test plan
Unit (offline): decode returns MEME_DATA for real migrate frame; returns None for trade/create
frames; DetectionConsumer accepts migrate event; no-banned-import AST scan; **mint-key-equality**
(migrate mint == pre-grad TradeEvent mint for same token); birdeye-fallback-still-wired.
Integration (one-time live capture): 10-min standard-endpoint capture → bank first migrate frame →
verify decoder output.
Staging smoke: 1h run → `SELECT dex_source, count(*) FROM tokens WHERE graduated_at > now()-'1h'
GROUP BY dex_source`; expect helius_migrate = real pump.fun, noise absent.

## Risk register
| Risk | Likelihood | Mitigation |
|---|---|---|
| SPL-mint account index differs from estimate | Med (synthetic-only) | bank real frame; assert vs known token |
| Pool extraction from innerInstructions fragile | Med | fall back to "" pool (consumer coerces null→"") |
| Helius WS drop misses a graduation | Low | Birdeye secondary + MigrateReconciler backstop |
| 2nd Helius subscription slot | Low | fan-out the existing connection |
