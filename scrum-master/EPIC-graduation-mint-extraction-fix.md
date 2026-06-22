---
# ---
# file: scrum-master/EPIC-graduation-mint-extraction-fix.md
# sprint: hotfix
# story: hotfix-migrate-mint-rpc-resolution
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-22
# ---
title: "EPIC: Graduated SPL Mint Extraction Fix (PR #366 Regression)"
phase: retrospective
last-updated: 2026-06-22
last-updated-by: dev-team
---

## Summary

PR #366 (EPIC-graduation-migrate-detection) introduced a migrate-instruction-based graduation
detector that extracted the SPL mint from `innerInstructions`. This heuristic was wrong ~98% of
the time, silently degrading the model track since #366 shipped.

---

## The Bug

**File:** `core/tape/helius_birth_tape_source.py` — function `_extract_migrate_mint()`

**Root cause:** The function walked `innerInstructions` of the migrate transaction, found the 6EF8
CPI with the most accounts, and returned `accounts[2]`. This heuristic returned bonding-curve and
fee-program PDAs (owned by `6EF8rrecthR5…` / `pfeeUxB6…`), NOT the SPL mint.

**Hard evidence:**
- `getAccountInfo` on 12 graduated `Token.mint` values → 0 are real SPL mints.
- All were owned by `6EF8rrecthR5…` or `pfeeUxB6…` — PDA programs, not the SPL Token program.
- Because `TapeStore` keys swaps by SPL mint (from the Borsh `TradeEvent`), the score-join
  `self._tape.get(mint)` looked up a PDA → 0 swaps → only ~2 of 129 graduations ever scored.

**Silent failure mode:** The detector emitted graduation events with wrong mints, DetectionConsumer
persisted `Token` rows with those wrong mints, but the pre-grad tape had NO swaps keyed to those
PDAs → scoring effectively never fired for Helius-detected graduations.

---

## Why the "pump" Suffix Is NOT a Fix

A natural workaround would have been to filter `accounts[2]` by checking if it ends in `"pump"`.
This is NOT viable:

- `solanatrills/tokens_complete.csv` analysis: **75.4% of pump.fun mints end in "pump"**, but
  **24.6% do NOT**.
- Live validated example: real mint `F6zBdu9MRQ7N…` has no "pump" suffix — a suffix filter would
  silently skip 1 in 4 real graduated tokens.

---

## The Fix

**Replace the innerInstructions heuristic with a `getMultipleAccounts` RPC call per graduation.**

### Algorithm (validated 6/6 failing frames, including non-"pump"-suffix mints):

1. `extract_migrate_account_candidates(data)` — pure function, extracts all `message.accountKeys`
   pubkeys from the migrate transaction (dict-form or string-form, handles both).

2. `resolve_spl_mint(account_keys, api_key)` — sync resolver via `urllib.request` (NOT `requests`,
   which is absent from the prod image). Calls `getMultipleAccounts` with `encoding=jsonParsed`.
   Picks the unique account where:
   - `value.owner` ∈ {`TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA` (SPL Token),
     `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb` (Token-2022)}
   - `value.data.parsed.type == "mint"`
   - NOT WSOL (`So111…112`) and NOT USDC (`EPjFWdd5…Dt1v`)
   - Returns `None` on zero matches (log warning + skip) or multiple matches (ambiguous, log + skip).

3. `decode_helius_migrate_event()` is now a **PURE DETECTOR**: returns `address=""` (unresolved)
   + `candidate_accounts=[...]`. It no longer calls `_extract_migrate_mint`.

4. `HeliusMigrateSource._decode_and_dedupe()` is now **async**: runs the pure detect then calls
   `await asyncio.to_thread(resolve_spl_mint, ...)` so the blocking HTTPS call never stalls the
   event loop. Sets `event["address"] = mint` after resolution.

5. `run_firehose.py` `_helius_loop`: `HeliusMigrateSource` is now constructed with the real
   `settings.HELIUS_API_KEY` (previously passed `api_key="fanout"` — unused when no WS was opened,
   but now required for the RPC resolver).

**Cost:** One `getMultipleAccounts` RPC call per graduation (~tens per hour). No credit burn beyond
normal Helius plan usage.

**Validated:** 6/6 failing frames resolved to exactly one real SPL mint, including
`BGuETm3jup…` → `F6zBdu9MRQ7N` (a real mint NOT ending in "pump").

---

## Scope: Fix-Forward Only

The ~147 already-mislabeled `Token` rows (mints set to PDAs, graduated before this fix) are past
their 120s scoring window and NOT re-resolved. Fix-forward: all new graduations detected after
this PR is deployed will resolve correctly.

**Backlog re-resolution:** Out of scope for this hotfix. If the operator wants to re-score the
backlog, a separate task would need to:
1. Query `Token` rows where `dex_source="helius_migrate"` and `graduated_at > PR #366 deploy time`.
2. For each, resolve the real SPL mint from the raw transaction (stored in `Token.raw`).
3. Re-key the `TapeSwap` rows and re-trigger scoring.

This is a Product Owner decision and requires explicit authorization.

---

## Files Changed

- `core/tape/helius_birth_tape_source.py` — added `HELIUS_RPC_URL`, `_SPL_TOKEN_PROGRAM`,
  `_SPL_TOKEN_2022_PROGRAM`, `_USDC_MINT` constants; replaced `_extract_migrate_mint()` with
  `extract_migrate_account_candidates()` + `resolve_spl_mint()`; changed
  `decode_helius_migrate_event()` to pure detector (address="", candidate_accounts);
  made `_decode_and_dedupe()` async with `asyncio.to_thread`; updated `events()` and
  `events_from_queue()` to await `_decode_and_dedupe`.

- `core/management/commands/run_firehose.py` — `_helius_loop` now passes real
  `settings.HELIUS_API_KEY` to `HeliusMigrateSource` (previously hardcoded `"fanout"`).

- `core/tests/test_helius_migrate_source.py` — updated 14 tests; added 9 new tests for
  `extract_migrate_account_candidates`, `resolve_spl_mint` (mocked), and async
  `_decode_and_dedupe` (mocked).

- `core/tests/test_helius_single_connection_fanout.py` — updated tests 7, 8, 9 and the
  fanout graduation test to mock `resolve_spl_mint` (tests are now fully offline).

---

## Test Results

- 35 new/updated tests: all pass.
- Full suite: 3301 passed, 1 pre-existing failure (`test_sprint14_guard_passes` — sprint phase
  stale, unrelated to this fix), 11 skipped, 1 xfailed.
- Zero new regressions.
- Golden parity tests (`test_golden_parity_us76_ac5.py`, `test_golden_parity_v4.py`) green.
- AST constraints (no top-level `websockets`, no `datetime.now()`/`time.time()`, no banned
  imports) confirmed green.
