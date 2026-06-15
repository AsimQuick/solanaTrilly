# Sprint 5

**Phase:** planning
**Progress:** 0/6 stories | 1/20 ACs
**Last Updated:** 2026-06-15T16:01:40+00:00

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
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-17.1:** A Swap Django model (table 'swaps', PRD §8) holds one row per recorded swap with columns: mint (CharField, FK-or-index to tokens), block_time (INT), slot (INT), signature (CharField), side, price (FLOAT), vol_sol (FLOAT), vol_usd (FLOAT), sol_usd (FLOAT), owner (CharField, nullable — the signer; None excluded from counts), base_reserve (BigIntegerField, nullable), quote_reserve (BigIntegerField, nullable), rel (FLOAT). 'docker compose run --rm web python manage.py makemigrations' + 'migrate' apply cleanly against real Postgres; verified by a pytest test that creates a swap row and reads every column back.
  - Dev: done
- [ ] **AC-17.2:** A composite index on (mint, block_time, slot, signature) — the canonical-ordering key (§8) — is declared in the model's Meta.indexes, and 'side' uses a constrained vocabulary {buy, sell} on a width-bounded column (the §8 VARCHAR-width discipline; choices in the field's choices= parameter, not DB-only). Verified by a pytest test asserting the index over exactly (mint, block_time, slot, signature) is present in the model/migration, that side.choices is non-empty, and that each choice value fits within the declared max_length.
- [ ] **AC-17.3:** A single NormalizedSwap schema (§7.1; a typed dataclass/TypedDict in core, the ONE schema the vendored feature math will eat later) defines fields {rel, price, side, vol_sol, vol_usd, sol_usd, owner, block_time, slot, signature, base_reserve, quote_reserve, quote_mint, source, phase}, with source constrained to {birdeye_live, birdeye_backfill, helius_verify} and phase to {pre, post}. 'rel' is defined as block_time minus the token's graduated_block_time/graduated_at — ANCHORED to the DB Token row on BOTH sides, NEVER the first swap's time. Verified by a pytest test: building a NormalizedSwap from a raw swap + a Token anchors rel to graduated_block_time, and out-of-vocabulary source/phase/side values are rejected. Any JSON persisted alongside uses encoder=JsonSafeEncoder so the H3/US-5 guard stays green.
- [ ] **AC-17.4:** Swap is registered in the Django admin (changelist + detail), read-only on the immutable raw fields. Verified by pytest tests that request the admin changelist and a change-detail page for Swap as an authenticated staff user, asserting HTTP 200 on both. New files carry metadata front matter.

**Dependencies:** US-14, US-5

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-17.1 fixed: **Fix:** Removed one extra blank line in `core/tests/test_swap_model_ac171.py` (line 21 in the original) — ruff's I001 flagged a double blank line between the last import and the first comment as a formatting violation. `ruff check --fix` applied the single-line deletion; CI lint gate should now pass.

**Tester Status:** approved
**Tester Notes:**
  AC-17.1 diagnosis: ---
  
  **Diagnosis: Code Bug — Ruff I001 lint error in the test file**
  
  **What failed:** The CI `Lint` step failed with a single `ruff` error:
  
  ```
  I001 [*] Import block is un-sorted or un-formatted
    --> core/tests/test_swap_model_ac171.py:15:1
  ```
  
  The import block in `core/tests/test_swap_model_ac171.py` (lines 15–19) is flagged by ruff's isort integration as not conforming to the project's import ordering rules. Ruff reports it as auto-fixable with `--fix`.
  
  **Type:** Code bug (not a requirements issue). AC-17.1 is correctly specified; the implementation and test logic are unrelated to this failure. This is purely a style/formatting defect introduced in the test file.
  
  **Severity:** Low. The test suite itself never ran — the lint gate blocked it. No correctness problem, no logic error, no missing functionality.
  
  **Recommended fix (for Dev agent):** In `core/tests/test_swap_model_ac171.py`, fix the import ordering. The current block is:
  
  ```python
  from datetime import datetime, timezone
  
  import pytest
  
  from core.models import Swap, Token
  ```
  
  Run `ruff check --fix core/tests/test_swap_model_ac171.py` inside the container (or adjust the import order manually to match ruff's isort e …

---

### US-18: P3 — the tape recorder core behind the DataSource seam: NormalizedSwap emission, canonical ordering, owner=signer, drop-failed, zero-guard (§6.2, Principle #7)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-18.1:** A TapeRecorder consumer reads swap events from a DataSource (NOT a concrete Birdeye client) and reads time from an injected clock — the live/replay seam from US-2 / Principle #7, consuming events via the existing stamp_events helper as the detection consumer does. Verified by (a) a pytest test driving the recorder from an in-memory/ReplaySource and (b) the US-2-style static-analysis guard holding: no concrete-source import and no time.time()/datetime.now() on the core recorder path.
- [ ] **AC-18.2:** For each LANDED swap the recorder emits exactly one NormalizedSwap (US-17 §7.1) with owner=the tx SIGNER (= Birdeye 'owner', canonical both sides per §3.3), rel anchored to the token's graduated_block_time, and ALL THREE units stored (vol_sol, vol_usd, sol_usd — D1). FAILED swaps are DROPPED (landed-only, §6.2). Verified by pytest: a failed swap produces no NormalizedSwap; a landed swap maps every field correctly; owner equals the signer; all three unit fields are populated.
- [ ] **AC-18.3:** Swaps are ordered by the canonical key (block_time, slot, signature) with a STABLE sort — never block_time alone (the #403 within-second-order disaster). Verified by a pytest test feeding within-second, out-of-order swaps (equal block_time, differing slot/signature, plus a fully-equal-key pair) and asserting the emitted order matches the stable (block_time, slot, signature) ordering with input order preserved on ties.
- [ ] **AC-18.4:** Zero-/degenerate-swap guard (S8 / #405): a swap with zero/None reserves, zero volume, or a degenerate price field never raises (no ZeroDivisionError) and is handled per an explicit declared policy (skipped or flagged — never a silent 0 row, never a crash). Verified by a pytest test feeding a degenerate swap and asserting no exception is raised and the documented handling occurs. New files carry metadata front matter.

**Dependencies:** US-17, US-2, US-15

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 4 ACs testable. AC-18.4 'documented handling occurs' is bounded by the skipped-or-flagged policy stated in the same AC — not ambiguous.

---

### US-19: P3 — append-only jsonl.gz lake + queryable 'swaps' writer + truncated-tail-tolerant reader (§6.2, §6.4)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-19.1:** The recorder appends NormalizedSwaps to an append-only, daily-partitioned jsonl.gz lake at lake/tapes/dt=YYYY-MM-DD/part-*.jsonl.gz (raw = immutable truth, §6.4.1 — never mutated, never re-pulled). Verified by a pytest test: recording a set of swaps writes a gzip-compressed jsonl part under the correct dt= partition path, which reads back to byte-identical rows.
- [ ] **AC-19.2:** Recorded swaps are also written to the queryable 'swaps' table (US-17) as a mirror of the jsonl.gz lake, idempotently on (mint, signature) — re-recording the same swap does NOT create a duplicate row. Verified by a pytest test asserting each recorded NormalizedSwap appears as a swaps row with matching fields, and that re-recording the same swaps yields the same row count (no duplicates).
- [ ] **AC-19.3:** A truncated-tail-tolerant lake reader (port _iter_tape_rows — the recovered-161k-rows scaffolding) yields all complete rows and silently tolerates a truncated/partial final line, never raising. Verified by a pytest test that reads a part file whose last line is deliberately truncated mid-record and asserts every complete row is returned with no exception. New files carry metadata front matter.

**Dependencies:** US-18

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs precisely verifiable. Byte-identical read-back and row-count stability are unambiguous CI gates.

---

### US-20: P3 — recorder resilience: seek_by_time gap reconciliation (one code path) + idle-kill TTL re-attach, config-driven (§6.2 D4, Principle #1)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-20.1:** A seek_by_time reconciliation path (behind the DataSource seam) merges any swaps missed during a WS gap into the tape, idempotently. Verified by a pytest test: a live swap stream missing N swaps, followed by a seek_by_time reconcile over the gap window, yields the complete swap set with NO duplicate rows for swaps already recorded (idempotent on (mint, signature)).
- [ ] **AC-20.2:** Idle-kill TTL (D4): the recorder deactivates a mint after idle_kill_ttl_s of no swaps but RE-ATTACHES on the next swap (never goes blind on a quiet-then-pump token), with idle_kill_ttl_s read from get_active_config() (US-11 resolver), NOT a hardcoded constant or os.getenv (Principle #1). Verified by a pytest test (virtual clock): a mint idle past TTL deactivates, a later swap re-attaches and is recorded, and changing the active config's tape.idle_kill_ttl_s changes the deactivation timing. (The §5.2 invariant idle_kill_ttl_s >= outcome.window_s is already enforced by the P1 Pydantic schema; this AC verifies the recorder HONORS the resolved value.)
- [ ] **AC-20.3:** Parity by construction (§3.3 / Principle #7 — one code path): the gap reconciliation uses the IDENTICAL seek_by_time call/normalization path the offline backfill uses, not a second live-only path. Verified by a pytest test that a live-gap reconcile and a pure backfill over the same window+token produce byte-identical NormalizedSwaps (same fields, same canonical ordering), demonstrating no divergent second code path. New files carry metadata front matter.

**Dependencies:** US-18, US-11

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs testable. Virtual-clock design in AC-20.2 ensures timing assertions are deterministic in CI. Byte-identical parity in AC-20.3 is a clear pass/fail gate.

---

### US-21: P3 — the offline gate: deterministic ReplaySource swap-stream replay + live↔backfill byte-parity on golden tokens + recorder regression suite (§16, §6.4)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-21.1:** P3 OFFLINE GATE (§16): replaying a captured-or-synthetic Birdeye swap stream through a ReplaySource + virtual clock yields EXACTLY the expected set of NormalizedSwaps / 'swaps' rows, deterministically — run twice and the rows AND the jsonl.gz content are byte-identical. The fixture is a schema-faithful Birdeye SUBSCRIBE_TXS stream (§3.3); if US-22 banks a real capture it is used here. Verified by the replay test asserting the expected normalized swaps and run-twice determinism.
- [ ] **AC-21.2:** live↔backfill byte-parity on golden token(s) (§16; the P3-scoped slice of the G2(a) live↔backfill seam — full G1/G2 golden parity is P5): the live swap stream and the seek_by_time backfill for the same golden token(s) produce byte-identical NormalizedSwaps (rel, price, side, vol_*, owner, and canonical ordering all match). Verified by a pytest test over the golden token(s) asserting byte-identical normalized swaps from both the live-replay and the backfill paths.
- [ ] **AC-21.3:** The recorder regression suite is green in CI and wired so it cannot silently vanish: the canonical stable-ordering test (US-18.3), the truncated-tail recovery test (US-19.3), and the zero/degenerate-swap guard test (US-18.4) all pass in the single canonical ci.yml 'test' job (H1 — no second workflow). Verified by the tests passing on the feature branch and at merge. New files carry metadata front matter.

**Dependencies:** US-18, US-19, US-20

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs testable. AC-21.1 synthetic-fixture fallback resolves fixture conditionality without ambiguity. Byte-identical and CI-wiring assertions are clear pass/fail.

---

### US-22: P3/D4 — wire a live Birdeye SUBSCRIBE_TXS source into the listener + spend & bank the first firehose activation (prove the recorder live) (§6.2, §15.7)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-22.1:** A concrete Birdeye SUBSCRIBE_TXS swap-stream DataSource (a LiveSource adapter) is wired into run_listener (the US-16 dedicated 'listener' container) behind the EXISTING DataSource seam — the clock-injected recorder core is unchanged and the US-2 static-analysis guard still holds (the concrete source + wall clock live ONLY in the listener/adapter wiring, never on the core recorder path). Verified by a structural test that the listener wiring constructs a concrete Birdeye source + wall clock for the recorder and that the core-path static-analysis guard remains green.
- [ ] **AC-22.2:** A SINGLE, deliberate, time-boxed (<=30 min) live Birdeye firehose activation is run and LOGGED in ops/firehose_activation_log.md per §15.7 (date · role/agent · which WS [Birdeye SUBSCRIBE_TXS] · purpose · duration · count remaining [9 Birdeye / 10 Helius] · what was captured). The activation BANKS a durable tape fixture into the lake/golden set. Verified by the committed ledger entry (count decremented to 9 Birdeye) and the banked durable fixture committed to the repo/lake.
- [ ] **AC-22.3:** End-to-end live proof: during the activation a real graduated token's PumpSwap swaps flow Birdeye SUBSCRIBE_TXS → recorder → 'swaps' rows + jsonl.gz on the VPS 'listener' container, and the banked capture is committed as the durable golden-token fixture that US-21's parity/replay test runs against OFFLINE forever (so no further activation is needed for that scenario). Verified by two durable evidence items: (1) Tester runs 'docker exec <web_or_db_container> psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT count(*) FROM swaps WHERE mint=''<logged_mint>'';"' on the VPS (scoped with -p solanatrilly) and confirms count >= 1; (2) Tester confirms the banked fixture file is committed at its declared lake/golden repo path. Additionally, the offline replay/parity test (US-21) must pass against the banked fixture in CI. New files carry metadata front matter.

**Dependencies:** US-16, US-18, US-21

**Dev Team Status:** not-started

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
