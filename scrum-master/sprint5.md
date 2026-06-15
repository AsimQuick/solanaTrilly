# Sprint 5

**Phase:** planning
**Progress:** 5/6 stories | 18/20 ACs
**Last Updated:** 2026-06-15T21:55:48+00:00

## Sprint Goal
Open P3 — the tape recorder (PRD §6.2, the heart of the pipeline) — and prove detection live (retrospective D4). P0/P1/P2 are closed: the VPS staging stack is live on 8002, the config core (P1) is the single source of truth, and P2 detection lands graduated tokens in the 'tokens' table with graduated_block_time carried as the integer rel-anchor the recorder needs. Sprint-5 builds the PumpSwap swap-tape recorder that captures every swap from t0 and turns it into the project's primary dataset. Deliver, IN ORDER: (1) the persistence target — the 'swaps' table (§8) + the one NormalizedSwap schema (§7.1) with rel ANCHORED to the DB Token.graduated_block_time/graduated_at (never the first swap's time) and the source/phase/side constrained vocabularies (US-17); (2) the recorder CORE behind the DataSource seam + injected clock (US-2 / Principle #7) — port tape_recorder.py's queue/writer/window/coverage scaffolding source-agnostically, emit one NormalizedSwap per LANDED swap with owner=the tx signer and all three units (vol_sol/vol_usd/sol_usd, D1), drop failed swaps, enforce the canonical STABLE sort on (block_time, slot, signature) (never block_time alone — #403), and keep the zero/degenerate-swap guard (#405 ZeroDivisionError) (US-18); (3) the lake + queryable mirror — append-only daily-partitioned jsonl.gz at lake/tapes/dt=YYYY-MM-DD/part-*.jsonl.gz (raw=immutable truth, §6.4.1) + the 'swaps' table writer (idempotent on (mint, signature)) + a truncated-tail-tolerant reader (port _iter_tape_rows — recovered 161k rows) (US-19); (4) recorder resilience — seek_by_time reconciliation that closes WS gaps using the IDENTICAL call/path the offline backfill uses (one code path → backfill parity by construction) + idle-kill TTL deactivate-but-RE-ATTACH on the next swap, with idle_kill_ttl_s read from get_active_config() (Principle #1, the §5.2 TTL≥outcome.window_s invariant already enforced in P1) (US-20); (5) the P3 OFFLINE GATE (§16) — replay a captured-or-synthetic Birdeye swap stream through ReplaySource + virtual clock → EXACTLY the expected NormalizedSwaps/swaps rows, deterministically (run twice → byte-identical), plus live↔backfill byte-parity on golden token(s) and the ordering/truncated-tail/zero-guard regression suite green in CI (US-21). FINALLY (retrospective D4): wire a concrete Birdeye SUBSCRIBE_TXS source into run_listener on the VPS listener container (behind the existing seam — core path untouched, US-2 guard still green), spend the FIRST of the 10 Birdeye firehose activations — deliberate, time-boxed ≤30 min, logged in ops/firehose_activation_log.md (§15.7) — confirm a real graduated token's PumpSwap swaps flow end-to-end to swaps rows + jsonl.gz on the box, and BANK the durable capture as the golden-token fixture US-21's parity/replay test runs against offline forever (US-22). Build order: US-17 FIRST (the persistence target everything writes to); then US-18 (core) → US-19 (lake) → US-20 (resilience) are sequential on the core; US-21 (offline gate) needs US-18/19/20; US-22 (live D4) is LAST and depends on the listener (US-16) + the built recorder — the offline P3 gate (US-21) does NOT depend on the live activation, so a failed/abbreviated firehose window never blocks P3 exit. Process carries: update phase + story dev_status to their real values BEFORE any sprint-end deploy so the US-13 integrity guard isn't tripped by our own staleness (D2); promote story-level dev_status to 'done' at closeout instead of relying on US-13's --skip-complete carve-out (D3); and the standing rule — before declaring any blocker 'human/operator required', run the on-box check the root SSH we already have allows (D5).

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/sprint4.json`
- `CLAUDE.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter (project convention)
- [ ] All services run in Docker (no host installs — Docker Rules); the recorder runs in the dedicated 'listener' container (US-16, the #289 lesson — the recorder NEVER shares the web/gunicorn process), defined in docker-compose.yml AND docker-compose.staging.yml and brought up on the VPS.
- [ ] CD pipeline LIVE and GREEN: every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there ('works locally' is NOT done; the deploy clause is gated at the SPRINT boundary per retrospective A2). The smoke-test retains the runtime retry-with-backoff from US-12 (AC-12.3).
- [ ] VPS verification is IN THE LOOP and gates 'done' (retrospective B3/C1): the Tester confirms from an ACTUAL green deploy run that the stack answers HTTP 200 on 8002, the 'listener' container is up, and solanaBilly is untouched on 8001.
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on 8001 untouched; NO unscoped down / up --force-recreate / prune / volume-removal anywhere).
- [ ] Status integrity enforced PROGRAMMATICALLY (US-13 guard): no story/AC reads status:done while its tester_status is failed/blocked; the guard is GREEN on sprint5.json at review. Per retrospective D2, the sprint 'phase' (and story dev_status) are promoted to their real values BEFORE any sprint-end deploy so the guard is not tripped by our own staleness; per D3, story-level dev_status is promoted to 'done' at closeout (not exempted via --skip-complete).
- [ ] Recorder is replay-testable OFFLINE (Principle #7): the recorder core reads swaps from a DataSource + an injected clock (no concrete-source import on the core path, US-2 static-analysis guard holds), and the P3 offline gate (§16 — ReplaySource swap-stream replay → expected NormalizedSwaps/swaps rows, deterministic; live↔backfill byte-parity on golden tokens; ordering + truncated-tail + zero-guard tests) is green.
- [ ] Raw = immutable truth (§6.4.1): the tape is written append-only to daily-partitioned jsonl.gz and never mutated/re-pulled; the 'swaps' table is a queryable mirror; features are re-derivable from raw.
- [ ] Firehose budget honored (§15.7): any live Birdeye/Helius activation (US-22/D4) is deliberate, time-boxed ≤30 min, logged in ops/firehose_activation_log.md (date · role · WS · purpose · duration · count remaining · what was captured), and BANKS a durable fixture into the lake/golden set; the offline P3 gate does not depend on a live activation succeeding.
- [ ] retrospective.md updated for sprint-5 (named owner: Tester / scrum facilitator — retrospective A3)

## User Stories

### US-17: P3 — the 'swaps' table + the one NormalizedSwap schema: tape persistence target (§8, §7.1)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-17.1:** A Swap Django model (table 'swaps', PRD §8) holds one row per recorded swap with columns: mint (CharField, FK-or-index to tokens), block_time (INT), slot (INT), signature (CharField), side, price (FLOAT), vol_sol (FLOAT), vol_usd (FLOAT), sol_usd (FLOAT), owner (CharField, nullable — the signer; None excluded from counts), base_reserve (BigIntegerField, nullable), quote_reserve (BigIntegerField, nullable), rel (FLOAT). 'docker compose run --rm web python manage.py makemigrations' + 'migrate' apply cleanly against real Postgres; verified by a pytest test that creates a swap row and reads every column back.
  - Dev: done
- [x] **AC-17.2:** A composite index on (mint, block_time, slot, signature) — the canonical-ordering key (§8) — is declared in the model's Meta.indexes, and 'side' uses a constrained vocabulary {buy, sell} on a width-bounded column (the §8 VARCHAR-width discipline; choices in the field's choices= parameter, not DB-only). Verified by a pytest test asserting the index over exactly (mint, block_time, slot, signature) is present in the model/migration, that side.choices is non-empty, and that each choice value fits within the declared max_length.
  - Dev: done
- [x] **AC-17.3:** A single NormalizedSwap schema (§7.1; a typed dataclass/TypedDict in core, the ONE schema the vendored feature math will eat later) defines fields {rel, price, side, vol_sol, vol_usd, sol_usd, owner, block_time, slot, signature, base_reserve, quote_reserve, quote_mint, source, phase}, with source constrained to {birdeye_live, birdeye_backfill, helius_verify} and phase to {pre, post}. 'rel' is defined as block_time minus the token's graduated_block_time/graduated_at — ANCHORED to the DB Token row on BOTH sides, NEVER the first swap's time. Verified by a pytest test: building a NormalizedSwap from a raw swap + a Token anchors rel to graduated_block_time, and out-of-vocabulary source/phase/side values are rejected. Any JSON persisted alongside uses encoder=JsonSafeEncoder so the H3/US-5 guard stays green.
  - Dev: done
- [x] **AC-17.4:** Swap is registered in the Django admin (changelist + detail), read-only on the immutable raw fields. Verified by pytest tests that request the admin changelist and a change-detail page for Swap as an authenticated staff user, asserting HTTP 200 on both. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-14, US-5

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-17.1 fixed: **Fix:** Removed one extra blank line in `core/tests/test_swap_model_ac171.py` (line 21 in the original) — ruff's I001 flagged a double blank line between the last import and the first comment as a formatting violation. `ruff check --fix` applied the single-line deletion; CI lint gate should now pass.
  AC-17.2 fixed: **Fix summary:** Two lint defects resolved in a single commit on `feature/US-17-AC-17.2`:
  
  1. **`core/models.py:4` (E501)** — Split the 134-char `# story:` front-matter comment across two lines so neither exceeds the 120-char limit.
  2. **`core/tests/test_swap_model_ac172.py:19` (I001)** — Ran `ruff check --fix` inside the container to reorder the import block to match isort rules.
  AC-17.3 done: Done. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-17.3 Implementation Summary**
  
  **Files changed (2 new files):**
  
  1. **`core/normalized_swap.py`** — The `NormalizedSwap` dataclass (PRD §7.1). Defines all 15 required fields: `{rel, price, side, vol_sol, vol_usd, sol_usd, owner, block_time, slot, signature, base_reserve, quote_reserve, quote_mint, source, phase}`. Enforces constrained vocabularies via `__post_init__` validation: `VALID_SOURCES = {birdeye_live, birdeye_backfill, helius_verify}`, `VALID_PHASES = {pre, post}`, `VALID_SIDES = {buy, sell}`. Class method `from_raw_swap(raw, token, *, source, phase)` computes `rel = block_time - token.graduated_block_time` (anchored to the DB Token row, never the first swap's time). `to_json()` uses `JsonSafeEncoder` (H3/US-5 guard stays green; NaN/Inf → null).
  
  2. **`core/tests/test_normalized_swap_ac173.py`** — 26 pytest tests covering: field completeness (all 15 §7.1 fields present), vocabulary set assertions, `rel` anchoring to `graduated_block_time` (not swap's own block_time), field mapping round-trip, nullable fields, out-of-vocabulary rejection for source/phase/side (both via `from_raw_swap` and direct const …
  AC-17.4 done: Pushed successfully. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-17.4 Implementation Summary**
  
  **Files changed (1 modified, 1 new):**
  
  1. **`core/admin.py`** — Updated front matter (added `sprint-5`, `US-17 AC-17.4` to `story:` line). Added `Swap` to the import from `core.models`. Added `SwapAdmin(admin.ModelAdmin)` with all 13 raw blockchain fields in `readonly_fields` (`mint`, `block_time`, `slot`, `signature`, `side`, `price`, `vol_sol`, `vol_usd`, `sol_usd`, `owner`, `base_reserve`, `quote_reserve`, `rel`), `list_display = ["id", "mint", "side", "price", "block_time", "owner"]`, and `ordering = ["-block_time", "-slot"]`.
  
  2. **`core/tests/test_swap_admin_ac174.py`** (new) — Metadata front matter carried. Two pytest tests: `test_swap_admin_changelist_returns_200` (staff user GETs `/admin/core/swap/`, asserts 200) and `test_swap_admin_change_detail_returns_200` (staff user GETs change detail for a created Swap row, asserts 200).
  
  **Test count:** 2 new tests; 503 total pass, 0 regressions.
  
  **Coverage:** threshold met (>= 80%).
  
  **Commit:** `e1fe752` — `[US-17] AC-17.4: Swap registered in Django admin, read-only raw fields, admin tests`
  
  **Branch pushed:** …
  [DEPLOY] VPS deploy PASSED (run 27560527559)

**Tester Status:** approved
**Tester Notes:**
  AC-17.2 diagnosis: I now have everything I need. Here is the diagnosis:
  
  ---
  
  ## Diagnosis: Code Bug — Two Lint Defects in the AC-17.2 Implementation
  
  **Type:** Code bugs (not a requirements issue). AC-17.2 is correctly specified and testable; the test logic itself was never reached because the `Lint` CI step blocked the run first.
  
  **Severity:** Low. Tests never executed — pure style violations that ruff caught at the gate.
  
  ---
  
  ### Error 1 — E501 (not auto-fixable) in `core/models.py:4`
  
  ```
  E501 Line too long (134 > 120)
    --> core/models.py:4:121
  ```
  
  The metadata front matter `# story:` comment on line 4 lists every AC from US-5 through US-17 on a single line (134 chars). The project's `pyproject.toml` sets `line-length = 120`. This is not auto-fixable.
  
  **Fix for Dev:** Break line 4 across two comment lines so neither exceeds 120 chars, e.g.:
  ```python
  # story: US-5 AC-5.2, US-9 AC-9.1, US-9 AC-9.2, US-9 AC-9.3, US-10 AC-10.4,
  #        US-14 AC-14.1, US-14 AC-14.2, US-17 AC-17.1, US-17 AC-17.2
  ```
  
  ---
  
  ### Error 2 — I001 (auto-fixable) in `core/tests/test_swap_model_ac172.py:19`
  
  ```
  I001 [*] Import block is un-sorted or un-formatted
    --> core/tests/test_swap_model_ac172.py:19:1
  ```
  
  Same ro …

---

### US-18: P3 — the tape recorder core behind the DataSource seam: NormalizedSwap emission, canonical ordering, owner=signer, drop-failed, zero-guard (§6.2, Principle #7)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-18.1:** A TapeRecorder consumer reads swap events from a DataSource (NOT a concrete Birdeye client) and reads time from an injected clock — the live/replay seam from US-2 / Principle #7, consuming events via the existing stamp_events helper as the detection consumer does. Verified by (a) a pytest test driving the recorder from an in-memory/ReplaySource and (b) the US-2-style static-analysis guard holding: no concrete-source import and no time.time()/datetime.now() on the core recorder path.
  - Dev: done
- [x] **AC-18.2:** For each LANDED swap the recorder emits exactly one NormalizedSwap (US-17 §7.1) with owner=the tx SIGNER (= Birdeye 'owner', canonical both sides per §3.3), rel anchored to the token's graduated_block_time, and ALL THREE units stored (vol_sol, vol_usd, sol_usd — D1). FAILED swaps are DROPPED (landed-only, §6.2). Verified by pytest: a failed swap produces no NormalizedSwap; a landed swap maps every field correctly; owner equals the signer; all three unit fields are populated.
  - Dev: done
- [x] **AC-18.3:** Swaps are ordered by the canonical key (block_time, slot, signature) with a STABLE sort — never block_time alone (the #403 within-second-order disaster). Verified by a pytest test feeding within-second, out-of-order swaps (equal block_time, differing slot/signature, plus a fully-equal-key pair) and asserting the emitted order matches the stable (block_time, slot, signature) ordering with input order preserved on ties.
  - Dev: done
- [x] **AC-18.4:** Zero-/degenerate-swap guard (S8 / #405): a swap with zero/None reserves, zero volume, or a degenerate price field never raises (no ZeroDivisionError) and is handled per an explicit declared policy (skipped or flagged — never a silent 0 row, never a crash). Verified by a pytest test feeding a degenerate swap and asserting no exception is raised and the documented handling occurs. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-17, US-2, US-15

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-18.1 done: Pushed successfully to `feature/US-18-AC-18.1`.
  
  ---
  
  **Implementation Summary**
  
  **Files changed (3 new files):**
  
  1. **`core/tape/__init__.py`** — Package init for the `tape` sub-package. Metadata front matter carried. Documents the Principle #7 constraint for all tape modules (no concrete-source imports, no direct time calls).
  
  2. **`core/tape/recorder.py`** — `TapeRecorder` class. Mirrors the `DetectionConsumer` pattern exactly: accepts `DataSource` + `Clock` in `__init__`, calls `stamp_events(self._source, self._clock)` in `run()`, accumulates `(event, timestamp)` pairs in `self._processed`. No concrete-source imports; no `datetime.now()` or `time.time()` anywhere.
  
  3. **`core/tests/test_tape_recorder_ac181.py`** — 6 pytest tests:
     - `test_recorder_processes_swap_events_from_replay_source` — drives TapeRecorder from ReplaySource, asserts all 3 events stored with correct payloads (criterion a)
     - `test_recorder_timestamps_use_injected_clock` — proves timestamps come from VirtualClock, not wall time (criterion a)
     - `test_recorder_empty_source_processes_zero_events` — empty source → empty processed list
     - `test_recorder_single_event` — single-event source → 1 entry with …
  AC-18.2 done: Pushed successfully.
  
  ---
  
  **Implementation Summary**
  
  **Files changed (2):**
  
  1. **`core/tape/recorder.py`** (modified) — Extended `TapeRecorder` with three new constructor params: `token_store: dict[str, Any] | None` (mint → token object), `swap_source: str` (defaults to `"birdeye_live"`), `swap_phase: str` (defaults to `"pre"`). Added `normalized_swaps` property. In `run()`: failed swaps (`event["failed"] == True`) are dropped; landed swaps whose mint is in `token_store` call `NormalizedSwap.from_raw_swap()` and append to `_normalized_swaps`. The `token_store` defaults to `{}` so all AC-18.1 tests remain unaffected. Front matter updated to include AC-18.2. The static-analysis guard (no concrete source imports, no `datetime.now()`/`time.time()`) continues to hold — `NormalizedSwap` is neither a concrete source class nor a time call.
  
  2. **`core/tests/test_tape_recorder_ac182.py`** (new) — 7 pytest tests covering:
     - `test_failed_swap_produces_no_normalized_swap` — failed swap → empty `normalized_swaps`
     - `test_landed_swap_maps_every_field_correctly` — all 15 §7.1 fields verified
     - `test_owner_equals_signer` — `owner` == raw event's `owner` (tx signer, §3.3)
     - `test_all …
  AC-18.3 done: Pushed successfully. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-18.3 Implementation Summary**
  
  **Files changed (2):**
  
  1. **`core/tape/recorder.py`** (modified) — Updated `story:` front matter to include `US-18 AC-18.3`. Changed the `normalized_swaps` property from a plain `list(self._normalized_swaps)` copy to `sorted(..., key=lambda s: (s.block_time, s.slot, s.signature))`. Python's `sorted()` is a stable sort (guaranteed by language spec), so swaps with an identical canonical triple preserve their original insertion order. No other logic changed; all AC-18.1 and AC-18.2 behavior is unaffected.
  
  2. **`core/tests/test_tape_recorder_ac183.py`** (new) — Metadata front matter carried. 6 pytest tests:
     - `test_within_second_out_of_order_ordered_by_slot` — equal `block_time`, descending slot input → ascending slot output (the #403 scenario)
     - `test_within_second_equal_slot_ordered_by_signature` — equal `(block_time, slot)`, reverse-alpha signature input → lexicographic signature output
     - `test_fully_equal_key_pair_preserves_input_order` — identical `(block_time, slot, signature)` pair → stable sort, first input remains first (distinguishable by `vol_sol …
  AC-18.4 done: Pushed successfully.
  
  ---
  
  **Implementation Summary**
  
  **Files changed (2):**
  
  1. **`core/tape/recorder.py`** (modified) — Updated front matter to include `US-18 AC-18.4`. Added module-level constant `DEGENERATE_SWAP_POLICY: str = "skip"` (the explicit declared policy). Added `_is_degenerate_swap(event)` guard function that returns `True` for: price is None or 0; vol_sol is None or 0 (zero/missing volume); base_reserve is None or 0; quote_reserve is None or 0 (zero/None reserves per AC-18.4 literal). Added `self._skipped_degenerate: list[dict]` to `__init__` and a `skipped_degenerate` property for audit access. In `run()`, after the failed-swap check, calls `_is_degenerate_swap(event)` — on match, appends to `_skipped_degenerate` and `continue`s (never reaches `from_raw_swap`, never crashes, never emits a 0-value row).
  
  2. **`core/tests/test_tape_recorder_ac184.py`** (new) — Metadata front matter carried. 12 pytest tests covering: policy constant assertion, individual degenerate conditions (price=0, price=None, vol_sol=0, vol_sol=None, base_reserve=0, base_reserve=None, quote_reserve=0, quote_reserve=None), degenerate swap still in `processed`, good swap alongside degenerate passes …
  [DEPLOY] VPS deploy PASSED (run 27562517486)

**Tester Status:** approved
**Tester Notes:**
  All 4 ACs testable. AC-18.4 'documented handling occurs' is bounded by the skipped-or-flagged policy stated in the same AC — not ambiguous.

---

### US-19: P3 — append-only jsonl.gz lake + queryable 'swaps' writer + truncated-tail-tolerant reader (§6.2, §6.4)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-19.1:** The recorder appends NormalizedSwaps to an append-only, daily-partitioned jsonl.gz lake at lake/tapes/dt=YYYY-MM-DD/part-*.jsonl.gz (raw = immutable truth, §6.4.1 — never mutated, never re-pulled). Verified by a pytest test: recording a set of swaps writes a gzip-compressed jsonl part under the correct dt= partition path, which reads back to byte-identical rows.
  - Dev: done
- [x] **AC-19.2:** Recorded swaps are also written to the queryable 'swaps' table (US-17) as a mirror of the jsonl.gz lake, idempotently on (mint, signature) — re-recording the same swap does NOT create a duplicate row. Verified by a pytest test asserting each recorded NormalizedSwap appears as a swaps row with matching fields, and that re-recording the same swaps yields the same row count (no duplicates).
  - Dev: done
- [x] **AC-19.3:** A truncated-tail-tolerant lake reader (port _iter_tape_rows — the recovered-161k-rows scaffolding) yields all complete rows and silently tolerates a truncated/partial final line, never raising. Verified by a pytest test that reads a part file whose last line is deliberately truncated mid-record and asserts every complete row is returned with no exception. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-18

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-19.1 fixed: **Fix:** Restored `push: branches: [main]` alongside `workflow_dispatch` in `.github/workflows/deploy.yml`. The ER commit `c0db4c2` had stripped the push trigger entirely (with a now-removed comment block explaining the rationale), which broke the AC-6.1 and AC-8.2 guard tests. No US-19 code was touched — all 6 AC-19.1 tests were already passing.
  AC-19.2 done: Pushed to `feature/US-19-AC-19.2`.
  
  ---
  
  ## Implementation Summary
  
  **Files changed (3):**
  
  1. **`core/tape/swap_writer.py`** (new) — `SwapWriter` class with a `write(swaps: list[tuple[str, NormalizedSwap]]) -> int` method. Uses `Swap.objects.update_or_create(mint=..., signature=..., defaults={...})` for idempotency on `(mint, signature)`. The `mint` is passed alongside each `NormalizedSwap` since the base-token mint is not stored on the swap schema itself (it lives in the recorder's `token_store`).
  
  2. **`core/tape/recorder.py`** (modified) — Three changes:
     - Added `swap_writer: SwapWriter | None = None` constructor parameter.
     - Changed internal storage from `_normalized_swaps: list[NormalizedSwap]` to `_normalized_swaps_with_mints: list[tuple[str, NormalizedSwap]]` so the mint is available at write time. The public `normalized_swaps` property is backward-compatible.
     - In `run()`, after all events consumed, calls `await asyncio.to_thread(self._swap_writer.write, ...)` — the `asyncio.to_thread()` wrapper is required because Django ORM is synchronous and cannot be called directly from async context.
  
  3. **`core/tests/test_swap_writer_ac192.py`** (new) — 8 pytest tests:
     - …
  AC-19.3 fixed: **Fix summary:** Removed a spurious blank line between `import pytest` and the `# Helpers` comment block in `core/tests/test_lake_reader_ac193.py`. This was a ruff I001 import-sort violation — the extra blank line inside the import section boundary caused isort to flag the entire block as un-sorted. Applied via `ruff check --fix`; no logic changes. CI should now pass Lint and proceed to run the AC-19.3 tests.
  [DEPLOY] VPS deploy PASSED (run 27573474431)

**Tester Status:** approved
**Tester Notes:**
  AC-19.3 diagnosis: I have the full picture. Here is my diagnosis:
  
  ---
  
  ## Diagnosis: Code Bug — ruff I001 import-sort violation in the AC-19.3 test file
  
  **Classification:** Code bug. Not a requirements issue.
  
  **Severity:** Low — the Lint step failed before any tests ran, so the AC-19.3 implementation itself (`core/tape/lake_reader.py`) and its tests were never executed. The feature code is untouched.
  
  ---
  
  ### What failed
  
  ```
  I001 [*] Import block is un-sorted or un-formatted
    --> core/tests/test_lake_reader_ac193.py:36:1
    |
    36 | / import gzip
    37 | | import io
    38 | | import json
    39 | | from pathlib import Path
    40 | |
    41 | | import pytest
       | |_____________^
  
  Found 1 error.
  [*] 1 fixable with the `--fix` option.
  ```
  
  ### Root cause
  
  `core/tests/test_lake_reader_ac193.py` has an import block that violates ruff's isort rules (I001). The violation is auto-fixable — ruff itself says `[*] 1 fixable with the --fix option`.
  
  ### Recurring pattern
  
  This is the same class of defect seen in AC-17.1 (double blank line before imports), AC-17.2 (I001 in test file), and now AC-19.3. The dev-team consistently pushes test files without running `ruff check --fix` inside the container before commit …

---

### US-20: P3 — recorder resilience: seek_by_time gap reconciliation (one code path) + idle-kill TTL re-attach, config-driven (§6.2 D4, Principle #1)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-20.1:** A seek_by_time reconciliation path (behind the DataSource seam) merges any swaps missed during a WS gap into the tape, idempotently. Verified by a pytest test: a live swap stream missing N swaps, followed by a seek_by_time reconcile over the gap window, yields the complete swap set with NO duplicate rows for swaps already recorded (idempotent on (mint, signature)).
  - Dev: done
- [x] **AC-20.2:** Idle-kill TTL (D4): the recorder deactivates a mint after idle_kill_ttl_s of no swaps but RE-ATTACHES on the next swap (never goes blind on a quiet-then-pump token), with idle_kill_ttl_s read from get_active_config() (US-11 resolver), NOT a hardcoded constant or os.getenv (Principle #1). Verified by a pytest test (virtual clock): a mint idle past TTL deactivates, a later swap re-attaches and is recorded, and changing the active config's tape.idle_kill_ttl_s changes the deactivation timing. (The §5.2 invariant idle_kill_ttl_s >= outcome.window_s is already enforced by the P1 Pydantic schema; this AC verifies the recorder HONORS the resolved value.)
  - Dev: done
- [x] **AC-20.3:** Parity by construction (§3.3 / Principle #7 — one code path): the gap reconciliation uses the IDENTICAL seek_by_time call/normalization path the offline backfill uses, not a second live-only path. Verified by a pytest test that a live-gap reconcile and a pure backfill over the same window+token produce byte-identical NormalizedSwaps (same fields, same canonical ordering), demonstrating no divergent second code path. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-18, US-11

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-20.1 done: Pushed to `feature/US-20-AC-20.1`. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-20.1 Implementation Summary**
  
  **Files changed (2 new files):**
  
  1. **`core/tape/gap_reconciler.py`** — `GapReconciler` class. Accepts a `DataSource` (the seek_by_time source, behind the seam — no concrete imports), a `Clock`, a `token_store`, and a `SwapWriter`. Internally constructs a `TapeRecorder` with `swap_source="birdeye_backfill"` and delegates all normalization to it. `run()` calls the inner recorder and returns the sorted `NormalizedSwaps`. Idempotency on `(mint, signature)` is provided by `SwapWriter.update_or_create`. This also lays the structural groundwork for AC-20.3 (one code path / backfill parity): a live-gap reconcile and a standalone backfill are literally the same `TapeRecorder` call path, differing only in the injected `DataSource`.
  
  2. **`core/tests/test_gap_reconciler_ac201.py`** — 5 pytest tests:
     - `test_live_stream_records_partial_set` — TapeRecorder over live events (S1, S3, S5) writes exactly 3 rows.
     - `test_reconcile_fills_gap` — GapReconciler over all 5 swaps adds the 2 missing rows → 5 total.
     - `test_reconcile_no_duplicates_for_existing_swap …
  AC-20.2 done: Pushed to `feature/US-20-AC-20.2`. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-20.2 Implementation Summary**
  
  **Files changed (3):**
  
  1. **`core/tape/idle_kill.py`** (new) — `IdleKillMonitor` class. Tracks per-mint last-swap time and deactivated state. `record_swap(mint, now)` updates last-seen time and re-attaches (clears deactivated flag, logs re-attachment event) if mint was deactivated. `check_idle(now)` reads `idle_kill_ttl_s` from `self._config_resolver()` on every call (the injected `get_active_config` — Principle #1), deactivates any mint whose elapsed time >= TTL, and logs the deactivation event. Pure Python except for the resolver call; never imports concrete DataSources; never calls `datetime.now()` (receives `now` as a parameter). Passes the existing tape static-analysis guards.
  
  2. **`core/tape/recorder.py`** (modified) — Added `idle_monitor: IdleKillMonitor | None = None` constructor parameter (defaults to `None` — all existing callers unaffected). In `run()`, after each event is stamped: calls `await asyncio.to_thread(self._idle_monitor.check_idle, timestamp)` (thread needed because `get_active_config()` uses Django cache/ORM, a SynchronousOnl …
  AC-20.3 done: Pushed successfully. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-20.3 Implementation Summary**
  
  **Files changed (1 new, 2 modified):**
  
  1. **`core/tests/test_gap_reconciler_ac203.py`** (new) — 6 pytest tests verifying parity by construction:
     - `test_gap_reconcile_and_backfill_byte_identical` — Main gate: GapReconciler and TapeRecorder(swap_source="birdeye_backfill") over the same 5-swap window+token produce byte-identical NormalizedSwaps (JSON-serialized strict comparison).
     - `test_parity_all_fields_match` — All 15 NormalizedSwap fields match field-by-field between both paths.
     - `test_parity_canonical_ordering_identical` — Both paths produce the same canonical (block_time, slot, signature) ordering even when input is reversed.
     - `test_parity_source_is_birdeye_backfill_on_both_paths` — Both paths tag swaps with `source="birdeye_backfill"`; neither uses a live-only tag.
     - `test_no_direct_normalization_in_gap_reconciler` — AST guard: `gap_reconciler.py` never calls `from_raw_swap()` directly; all normalization is delegated to TapeRecorder (one code path, Principle #7).
     - `test_parity_single_swap` — Parity holds for the minimal single-swap case …
  [DEPLOY] VPS deploy PASSED (run 27576497212)

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs testable. Virtual-clock design in AC-20.2 ensures timing assertions are deterministic in CI. Byte-identical parity in AC-20.3 is a clear pass/fail gate.

---

### US-21: P3 — the offline gate: deterministic ReplaySource swap-stream replay + live↔backfill byte-parity on golden tokens + recorder regression suite (§16, §6.4)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-21.1:** P3 OFFLINE GATE (§16): replaying a captured-or-synthetic Birdeye swap stream through a ReplaySource + virtual clock yields EXACTLY the expected set of NormalizedSwaps / 'swaps' rows, deterministically — run twice and the rows AND the jsonl.gz content are byte-identical. The fixture is a schema-faithful Birdeye SUBSCRIBE_TXS stream (§3.3); if US-22 banks a real capture it is used here. Verified by the replay test asserting the expected normalized swaps and run-twice determinism.
  - Dev: done
- [x] **AC-21.2:** live↔backfill byte-parity on golden token(s) (§16; the P3-scoped slice of the G2(a) live↔backfill seam — full G1/G2 golden parity is P5): the live swap stream and the seek_by_time backfill for the same golden token(s) produce byte-identical NormalizedSwaps (rel, price, side, vol_*, owner, and canonical ordering all match). Verified by a pytest test over the golden token(s) asserting byte-identical normalized swaps from both the live-replay and the backfill paths.
  - Dev: done
- [x] **AC-21.3:** The recorder regression suite is green in CI and wired so it cannot silently vanish: the canonical stable-ordering test (US-18.3), the truncated-tail recovery test (US-19.3), and the zero/degenerate-swap guard test (US-18.4) all pass in the single canonical ci.yml 'test' job (H1 — no second workflow). Verified by the tests passing on the feature branch and at merge. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-18, US-19, US-20

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-21.1 done: Pushed successfully to `feature/US-21-AC-21.1`.
  
  ---
  
  **Implementation summary for AC-21.1:**
  
  **File added (1 new file):**
  
  - **`core/tests/test_replay_offline_gate_ac211.py`** — The P3 OFFLINE GATE test. Contains a synthetic schema-faithful Birdeye SUBSCRIBE_TXS fixture (§3.3) with 6 events (5 landed: 4 buy + 1 sell; 1 failed). Drives `TapeRecorder(ReplaySource, VirtualClock)` and verifies determinism.
  
  **7 tests, all passing:**
  1. `test_fixture_schema_faithful` — All §3.3 fields present, types correct, vocabulary valid
  2. `test_replay_yields_expected_normalized_swaps` — Exactly 5 NormalizedSwaps; correct rel anchoring, source/phase, side distribution, canonical ordering
  3. `test_replay_failed_swap_excluded` — Failed event absent from normalized output
  4. `test_replay_deterministic_rows_run_twice` — JSON-serialized swaps byte-identical between two independent runs
  5. `test_replay_deterministic_lake_run_twice` — Decompressed jsonl.gz content byte-identical between two independent runs
  6. `test_replay_swaps_rows_correct` *(django_db)* — Exactly 5 'swaps' rows; rel, side, vol_sol spot-checked
  7. `test_replay_swaps_rows_idempotent_run_twice` *(django_db)* — Row count stays 5 after tw …
  AC-21.2 done: Pushed successfully to `feature/US-21-AC-21.2`.
  
  ---
  
  **Implementation Summary**
  
  **Files changed (1 new file):**
  
  1. **`core/tests/test_live_backfill_parity_ac212.py`** — AC-21.2 test. Defines a synthetic schema-faithful Birdeye SUBSCRIBE_TXS golden fixture (7 events: 5 buy + 1 sell + 1 failed, for one token). Two path helpers: `_run_live_path()` drives `TapeRecorder(swap_source="birdeye_live")` and `_run_backfill_path()` drives `TapeRecorder(swap_source="birdeye_backfill")`. Both receive identical raw events; two JSON serializers compare the fields: `_ac212_fields_as_json()` (the AC-21.2-listed fields: rel, price, side, vol_sol, vol_usd, sol_usd, owner) and `_all_non_source_fields_as_json()` (all 14 fields except `source`). Metadata front matter carried.
  
  **8 tests, all passing:**
  1. `test_golden_fixture_schema_faithful` — all 7 fixture events have every §3.3 Birdeye field with correct types
  2. `test_live_backfill_same_swap_count` — both paths yield 6 NormalizedSwaps (7 - 1 failed)
  3. `test_live_backfill_data_fields_byte_identical` — **main AC-21.2 gate**: rel, price, side, vol_sol, vol_usd, sol_usd, owner JSON-identical between paths
  4. `test_live_backfill_canonical_ordering_ide …
  AC-21.3 done: Pushed. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-21.3 Implementation Summary**
  
  **Files changed (1 new file):**
  
  1. **`core/tests/test_regression_suite_ac213.py`** — The regression suite wire for AC-21.3. Metadata front matter carried (`story: US-21 AC-21.3`).
  
     **Wiring mechanism:** Module-level imports create hard compile-time dependencies on 4 specific test functions by exact name. If any of the three regression files is deleted or renamed, Python raises `ImportError` during pytest's collection phase — the CI `test` job fails before any tests run.
  
     Imported (with `_` alias so pytest doesn't re-collect them):
     - `test_truncated_tail_returns_complete_rows_no_exception` from `core.tests.test_lake_reader_ac193` (AC-19.3)
     - `test_within_second_out_of_order_ordered_by_slot` from `core.tests.test_tape_recorder_ac183` (AC-18.3)
     - `test_degenerate_policy_is_skip` and `test_zero_price_swap_skipped_no_exception` from `core.tests.test_tape_recorder_ac184` (AC-18.4)
  
     **3 new wire tests:**
     - `test_regression_test_files_exist` — filesystem check: all 3 source files at their canonical paths
     - `test_regression_test_functions_are_callable` — assert …
  [DEPLOY] VPS deploy PASSED (run 27578234819)

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs testable. AC-21.1 synthetic-fixture fallback resolves fixture conditionality without ambiguity. Byte-identical and CI-wiring assertions are clear pass/fail.

---

### US-22: P3/D4 — wire a live Birdeye SUBSCRIBE_TXS source into the listener + spend & bank the first firehose activation (prove the recorder live) (§6.2, §15.7)
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-22.1:** A concrete Birdeye SUBSCRIBE_TXS swap-stream DataSource (a LiveSource adapter) is wired into run_listener (the US-16 dedicated 'listener' container) behind the EXISTING DataSource seam — the clock-injected recorder core is unchanged and the US-2 static-analysis guard still holds (the concrete source + wall clock live ONLY in the listener/adapter wiring, never on the core recorder path). Verified by a structural test that the listener wiring constructs a concrete Birdeye source + wall clock for the recorder and that the core-path static-analysis guard remains green.
  - Dev: done
- [ ] **AC-22.2:** A SINGLE, deliberate, time-boxed (<=30 min) live Birdeye firehose activation is run and LOGGED in ops/firehose_activation_log.md per §15.7 (date · role/agent · which WS [Birdeye SUBSCRIBE_TXS] · purpose · duration · count remaining [9 Birdeye / 10 Helius] · what was captured). The activation BANKS a durable tape fixture into the lake/golden set. Verified by the committed ledger entry (count decremented to 9 Birdeye) and the banked durable fixture committed to the repo/lake.
- [ ] **AC-22.3:** End-to-end live proof: during the activation a real graduated token's PumpSwap swaps flow Birdeye SUBSCRIBE_TXS → recorder → 'swaps' rows + jsonl.gz on the VPS 'listener' container, and the banked capture is committed as the durable golden-token fixture that US-21's parity/replay test runs against OFFLINE forever (so no further activation is needed for that scenario). Verified by two durable evidence items: (1) Tester runs 'docker exec <web_or_db_container> psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT count(*) FROM swaps WHERE mint=''<logged_mint>'';"' on the VPS (scoped with -p solanatrilly) and confirms count >= 1; (2) Tester confirms the banked fixture file is committed at its declared lake/golden repo path. Additionally, the offline replay/parity test (US-21) must pass against the banked fixture in CI. New files carry metadata front matter.

**Dependencies:** US-16, US-18, US-21

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-22.1 done: All guards green. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-22.1 Implementation Summary**
  
  **Files changed (4):**
  
  1. **`requirements.txt`** — Added `websockets>=12.0,<13` under the ASGI/WebSocket section; needed for the live Birdeye WS connection in `BirdeyeSwapSource.connect()`.
  
  2. **`core/tape/birdeye_swap_source.py`** (new) — `BirdeyeSwapSource(DataSource)` concrete adapter. Connects to `wss://public-api.birdeye.so/socket/solana` with `X-API-KEY` auth header, sends `SUBSCRIBE_TXS` subscription for the given mint (`txType: "swap"`), and yields parsed swap-event dicts (unwrapped from Birdeye's `{"type":…,"data":{…}}` envelope). `websockets` is imported lazily inside `connect()` to keep module import-time free of network side effects. Contains no `datetime.now()`, `time.time()`, `LiveSource`, or `ReplaySource` references — all existing US-2 guards remain green.
  
  3. **`core/management/commands/run_listener.py`** (modified) — Added top-level imports (`BirdeyeSwapSource`, `WallClock`, `TapeRecorder`, `settings`) and the module-level function `build_swap_recorder(api_key: str, mint: str) -> TapeRecorder` — the single, declared adapter-wiring point where conc …

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs testable. AC-22.3 amended directly to specify the two durable evidence items (VPS psql count >= 1 + committed fixture path) so the live-proof gate has an unambiguous pass/fail criterion; no PO judgment needed.

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
Requirements validation complete 2026-06-15. All 6 stories / 20 ACs approved. One minor wording fix applied directly to AC-22.3 (added two durable VPS evidence items so the live-proof gate has a binary pass/fail; no scope change). Sprint ready for development to begin in build order: US-17 → US-18 → US-19/US-20 (sequential) → US-21 → US-22.

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint5.json` — do not edit directly._
