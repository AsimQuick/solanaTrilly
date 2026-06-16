# Sprint 6

**Phase:** planning
**Progress:** 0/5 stories | 2/17 ACs
**Last Updated:** 2026-06-16T04:54:49+00:00

## Sprint Goal
Open P4 — the score-time snapshot + units locked (PRD §6.3 / §8 / D1; retrospective E4). P0–P3 are closed: the VPS staging stack is live on 8002, the config core (P1) is the single source of truth, P2 detection lands graduated 'tokens', and P3's tape recorder captures every PumpSwap swap from t0 into the immutable jsonl.gz lake + the queryable 'swaps' mirror with live↔backfill byte-parity. Sprint-6 adds the ONLY non-tape live read in the whole pipeline (Principle #3): a single on-demand Birdeye REST snapshot per token at score time, for the things the tape cannot give — holder distribution, mint/freeze authority, the LP-burned flag, and a liquidity/TVL/depth read (a first-class field per §1.1, never an assumption) — and locks the three-unit (vol_sol/vol_usd/sol_usd) parity backbone (D1). Deliver, IN ORDER: (1) the persistence target — the 'snapshots' table (§8: mint, taken_at, elapsed_s, raw JSONB) with the AT-MOST-ONE-ROW-PER-TOKEN discipline (§6.3) + a typed score-time snapshot schema, raw written via JsonSafeEncoder so the H3/US-5 guard stays green (US-23); (2) the score-time snapshot FETCHER behind the DataSource seam + injected clock (US-2 / Principle #7) — AT MOST ONE on-demand Birdeye REST read per token (the scheduled per-token poll regime is RETIRED — that cost cut funds retiring solanaBilly), capturing holders/authority/LP-burned/liquidity raw, with the #380 future-window clamp (any to=/window clamped to the injected now — Birdeye 400s on future windows) and the Redis token-bucket limiter KEPT while the scheduler is DROPPED (US-24); (3) score-time ORCHESTRATION — on graduation, schedule EXACTLY ONE snapshot at scoring.score_at_elapsed_s read from get_active_config() (US-11 resolver; Principle #1 — never a constant/os.getenv), honoring capture_buffer_s so the tape tail has landed, idempotent (at-most-once even on retry/restart), behind the seam + virtual clock so it is replay-testable, with NO scheduled-polling task registered (H2 manifest) (US-25); (4) UNITS LOCKED (D1) — every recorded swap carries all three units with the relationship locked (vol_usd ≈ vol_sol·sol_usd; sol_usd = the per-block reference) and unit-invariant features (shares/ratios/counts) are byte-identical regardless of unit basis (the parity backbone), wired into the canonical ci.yml so it cannot silently vanish (US-26); (5) the P4 OFFLINE GATE (§16) — replay a captured-or-synthetic Birdeye score-time snapshot through ReplaySource + virtual clock → EXACTLY the expected raw 'snapshots' row, deterministically (run twice → byte-identical raw JSONB), raw=immutable-truth re-derivability, and the #380-clamp / at-most-once / unit-parity regression suite green in CI (US-27). The P4 gate is OFFLINE and synthetic — the score-time read is a single on-demand REST snapshot (NOT a firehose WS activation), so NO Birdeye/Helius firehose activation is required this sprint (8 Birdeye / 10 Helius remain banked); per E3, if a real Birdeye REST snapshot can be banked cheaply it is used as the gate fixture, but the gate never depends on a live read. Build order: US-23 FIRST (the persistence target everything writes to); then US-24 (fetcher) → US-25 (orchestration) are sequential; US-26 (units lock) is independent of the snapshot chain and may run in parallel; US-27 (offline gate) needs US-23/24/25/26. Process carries: E1 — promote the sprint 'phase' (and story dev_status) to their real values BEFORE any sprint-end deploy so the US-13 integrity guard is not tripped by our own staleness (this has now caught a stale-phase deploy two sprints running); E2 — run 'ruff check --fix' inside the web container as a pre-push checklist item to kill the recurring I001/E501 churn before CI; E3 — exercise the live adapter against a banked real capture EARLY rather than discovering a missing live layer at story-end; E5 — the first sprint-6 deploy at true HEAD (phase now 'review'-corrected on sprint-5) is a clean green run, closing the US-22 condition-B (clean-final-deploy) carry and confirming 200-on-8002 + listener Up + solanaBilly untouched on 8001.

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/sprint5.json`
- `CLAUDE.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter (project convention)
- [ ] All services run in Docker (no host installs — Docker Rules); the score-time snapshot fetcher runs off the dedicated 'listener'/'celery' containers (NEVER the web/gunicorn process — the #289 lesson), defined in docker-compose.yml AND docker-compose.staging.yml and brought up on the VPS.
- [ ] CD pipeline LIVE and GREEN: every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there ('works locally' is NOT done; the deploy clause is gated at the SPRINT boundary per retrospective A2). The smoke-test retains the runtime retry-with-backoff from US-12 (AC-12.3).
- [ ] VPS verification is IN THE LOOP and gates 'done' (retrospective B3/C1): the Tester confirms from an ACTUAL green deploy run that the stack answers HTTP 200 on 8002, the 'listener' container is up, and solanaBilly is untouched on 8001.
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on 8001 untouched; NO unscoped down / up --force-recreate / prune / volume-removal anywhere).
- [ ] Status integrity enforced PROGRAMMATICALLY (US-13 guard): no story/AC reads status:done while its tester_status is failed/blocked; the guard is GREEN on sprint6.json at review. Per retrospective E1/D2, the sprint 'phase' (and story dev_status) are promoted to their real values BEFORE any sprint-end deploy so the guard is not tripped by our own staleness; per D3, story-level dev_status is promoted to 'done' at closeout (not exempted via --skip-complete).
- [ ] Snapshot fetcher is replay-testable OFFLINE (Principle #7): the fetcher core reads the snapshot from a DataSource + an injected clock (no concrete-source import on the core path, US-2 static-analysis guard holds), and the P4 offline gate (§16 — ReplaySource snapshot replay → expected raw 'snapshots' row, deterministic; raw-immutable re-derivability; #380-clamp + at-most-once + unit-parity tests) is green.
- [ ] Raw = immutable truth (§6.4.1): the score-time snapshot is stored verbatim as append-only raw JSONB and never mutated/re-pulled; the captured fields (holders/authority/lp_burned/liquidity) are re-derivable from raw.
- [ ] Score-time discipline honored (§6.3): AT MOST ONE on-demand snapshot per token (no scheduled per-token polling regime); the Redis token-bucket limiter is kept; the #380 future-window clamp is enforced; the snapshot fires at scoring.score_at_elapsed_s resolved from get_active_config() (Principle #1).
- [ ] Units locked (D1): every recorded swap carries vol_sol + vol_usd + sol_usd with the locked relationship; unit-invariant features are byte-identical across unit bases; the unit-parity test is wired into the canonical ci.yml 'test' job (H1) so it cannot vanish.
- [ ] Firehose budget honored (§15.7): the P4 offline gate does NOT require a firehose WS activation; the score-time snapshot is a single on-demand REST read, not a firehose. If any live read is taken it is deliberate, logged in ops/firehose_activation_log.md, and banks a durable fixture; the budget (8 Birdeye / 10 Helius remaining) is otherwise untouched.
- [ ] retrospective.md updated for sprint-6 (named owner: Tester / scrum facilitator — retrospective A3)

## User Stories

### US-23: P4 — the 'snapshots' table + the score-time snapshot schema: snapshot persistence target (§8, §6.3)
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-23.1:** A Snapshot Django model (table 'snapshots', PRD §8) holds one row per token's score-time read with columns: mint (CharField, FK-or-index to tokens), taken_at (TIMESTAMPTZ — when the snapshot was captured), elapsed_s (INT — seconds since the token's graduated_at, i.e. the score_at_elapsed_s the snapshot was taken at), raw (JSONField, the verbatim holders/authority/liquidity payload). 'docker compose run --rm web python manage.py makemigrations' + 'migrate' apply cleanly against real Postgres; verified by a pytest test that creates a snapshot row and reads every column back.
  - Dev: done
- [x] **AC-23.2:** At-most-one-row-per-token is enforced (§6.3 'one row/token'): a uniqueness constraint on mint (or update_or_create idempotency on mint) means re-taking a snapshot for the same token does NOT create a duplicate row, and the raw JSONField uses encoder=JsonSafeEncoder so the H3/US-5 guard stays green (non-finite → null, Decimal → float, datetime → iso). Verified by a pytest test: writing a snapshot whose raw payload contains NaN/Inf/Decimal/datetime persists without error and reads back json-safe; re-writing the same mint yields exactly one row.
  - Dev: done
- [ ] **AC-23.3:** A single typed score-time snapshot schema (a dataclass/TypedDict in core — the ONE shape the fetcher emits and the feature assembly will later eat) defines the captured fields {holder_distribution, mint_authority, freeze_authority, lp_burned (bool), liquidity, tvl, depth}, with the liquidity/TVL/depth read carried as a first-class field (§1.1, never assumed). Verified by a pytest test that builds the schema from a raw Birdeye snapshot payload and asserts every field maps, and that any JSON persisted alongside uses JsonSafeEncoder so the H3/US-5 guard stays green. New files carry metadata front matter.
- [ ] **AC-23.4:** Snapshot is registered in the Django admin (changelist + detail), read-only on the immutable raw fields. Verified by pytest tests that request the admin changelist and a change-detail page for Snapshot as an authenticated staff user, asserting HTTP 200 on both. New files carry metadata front matter.

**Dependencies:** US-14, US-5

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-23.1 fixed: **Fix summary:** Updated `test_ledger_budget_decremented_to_9_birdeye` → `test_ledger_budget_decremented_to_8_birdeye` in `core/tests/test_firehose_activation_ac222.py`. The regex and message now assert `| Birdeye | 10 | 2 | 8 |` to match the ledger state after the AC-22.3 second activation (commit `ddb203b`). No source code or ledger changes needed — the test was simply stale.
  AC-23.2 done: Done. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-23.2 Implementation Summary**
  
  **Files changed:** 1 new file
  
  - `core/tests/test_snapshot_idempotency_ac232.py` — 9 pytest tests covering both AC-23.2 invariants:
    - **JsonSafeEncoder guard (H3/US-5):** Four targeted tests (`test_raw_with_nan_*`, `test_raw_with_inf_*`, `test_raw_with_decimal_*`, `test_raw_with_datetime_*`) plus one mixed payload test confirm NaN→null, ±Inf→null, Decimal→float, datetime→ISO string — all persist without error and read back json-safe from Postgres.
    - **At-most-one-row-per-token (§6.3):** Three tests verify `update_or_create` idempotency (first write creates one row, re-write leaves exactly one row, re-write updates the existing row). One additional test confirms the `unique=True` DB constraint rejects a direct duplicate `objects.create` with `IntegrityError`.
  
  **No source code changes were needed** — the `Snapshot` model already carried `unique=True` on `mint` and `encoder=JsonSafeEncoder` on `raw` (committed in AC-23.1). AC-23.2 is purely a test-coverage AC.
  
  **Test count:** 9 new tests  
  **Full suite:** 654 passed (was 645 before AC-23.1 fix + these 9)  
  **Coverage:** 88% ( …

**Tester Status:** approved
**Tester Notes:**
  AC-23.1 diagnosis: I have everything I need. The failure is fully diagnosed.
  
  ---
  
  **Diagnosis: CODE BUG — severity LOW, single test assertion stale**
  
  **What failed:**
  `core/tests/test_firehose_activation_ac222.py::test_ledger_budget_decremented_to_9_birdeye` (line 247)
  
  **Root cause:**
  The test asserts the budget summary table in `ops/firehose_activation_log.md` shows `| Birdeye | 10 | 1 | 9 |` (used=1, remaining=9). That was correct after the AC-22.2 first activation. But commit `ddb203b` ("[US-22] AC-22.3 done: live firehose proof + ledger 9->8") added a second Birdeye activation row and updated the summary table to `| Birdeye | 10 | 2 | 8 |`. The test was never updated to match — so the feature/US-23 branch inherited the correct ledger but the stale test.
  
  **Not a requirements issue.** AC-22.2's requirement (budget is decremented when a live activation is taken) is still met — the table is accurate. The test is simply asserting the wrong current row values.
  
  **All other tests pass.** 644/645 green, coverage 88%. No migration failures, no model issues, no new US-23 test failures — `test_snapshot_model_ac231.py` passes cleanly.
  
  **Recommended fix (Dev Team):**
  In `core/tests/test_firehose_activati …

---

### US-24: P4 — the score-time snapshot fetcher behind the DataSource seam: at-most-one on-demand Birdeye REST read + #380 future-window clamp + token-bucket limiter (§6.3, Principle #7)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-24.1:** A SnapshotFetcher reads the score-time snapshot from a DataSource (NOT a concrete Birdeye REST client) and reads time from an injected clock — the live/replay seam from US-2 / Principle #7. It takes AT MOST ONE on-demand snapshot per token (never the retired scheduled-poll regime). Verified by (a) a pytest test driving the fetcher from an in-memory/ReplaySource and (b) the US-2-style static-analysis guard holding: no concrete-source import and no time.time()/datetime.now() on the core fetcher path.
- [ ] **AC-24.2:** The fetcher captures the score-time fields the tape cannot give — holder distribution, mint/freeze authority, the LP-burned flag, and a liquidity/TVL/depth read (§6.3, §1.1) — and stores them RAW as immutable JSONB (§6.4.1) via the §8 'snapshots' row (US-23) using JsonSafeEncoder. Verified by a pytest test: a snapshot fetch persists exactly one row whose raw payload is intact and from which the typed schema (US-23.3) re-derives.
- [ ] **AC-24.3:** Future-window clamp (#380): any to=/window parameter the fetcher would send to the Birdeye REST read is clamped to the injected-clock now (Birdeye returns HTTP 400 on a future window). Verified by a pytest test: a snapshot requested with a to-window beyond the injected now is clamped to now — no future window ever leaves the fetcher.
- [ ] **AC-24.4:** The Redis token-bucket rate limiter (ported from solanaBilly, kept per §6.3) gates the on-demand snapshot read, while the per-token-poll SCHEDULER is dropped (no scheduled polling). Verified by a pytest test that the limiter is consulted before the read AND that no periodic/scheduled snapshot task is registered (the H2 task-manifest test confirms the snapshot is on-demand only, not a celery-beat poll). New files carry metadata front matter.

**Dependencies:** US-23, US-2, US-11

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements review PASS. All 4 ACs are testable. AC-24.1: two-pronged gate (ReplaySource drive-test + static-analysis guard) mirrors the proven US-2 pattern; 'no time.time()/datetime.now()' is a grep-checkable assertion — note the guard must also cover datetime.utcnow() consistent with US-2 (this is implied by 'US-2-style' but the dev team should confirm scope matches the existing guard). AC-24.2: persist-and-re-derive test chains to US-23.3 schema, making the end-to-end contract verifiable in one pytest. AC-24.3: clamp assertion is precise and negative (no future window escapes the fetcher), testable with an injected virtual clock set to a known now and a to-window set beyond it. AC-24.4: two verifiable conditions — limiter is consulted (can be asserted via mock or call-count) and H2 manifest has no periodic snapshot task (an existing guard pattern). Dependencies US-23, US-2, US-11 are correctly declared; this story must follow US-23. No scope issues.

---

### US-25: P4 — score-time orchestration: schedule EXACTLY ONE snapshot at score_at_elapsed_s from get_active_config(), idempotent, replay-testable (§6.3, Principle #1)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-25.1:** On a token's graduation the orchestration schedules EXACTLY ONE score-time snapshot at scoring.score_at_elapsed_s seconds after the token's graduated_at, with the elapsed time read from get_active_config() (US-11 resolver), NOT a hardcoded constant or os.getenv (Principle #1). Verified by a pytest test (virtual clock): a token graduated at t0 triggers one snapshot at t0+score_at_elapsed_s, and changing the active config's scoring.score_at_elapsed_s changes the trigger time accordingly. The snapshot honors the resolved scoring.capture_buffer_s so the tape tail has landed before score time (§7; the §5.2 capture_buffer_s >= 3 invariant is already enforced by the P1 Pydantic schema).
- [ ] **AC-25.2:** At-most-once / idempotent: a token never receives a second score-time snapshot even on task retry, listener restart, or duplicate graduation event — the one-row-per-token discipline (US-23.2) holds end-to-end. Verified by a pytest test that re-running the orchestration for an already-snapshotted mint is a no-op (still exactly one 'snapshots' row, no second REST read attempted).
- [ ] **AC-25.3:** The orchestration drives the fetcher behind the DataSource seam + injected clock so it is replay-testable (Principle #7), and NO scheduled-polling task is registered (the snapshot is on-demand, scheduled-once-per-token; the H2 task-manifest test guards against a re-introduced periodic poll). Verified by a pytest test running the orchestration from a ReplaySource + virtual clock to the expected single snapshot, plus the H2 manifest assertion that no per-token polling task exists. New files carry metadata front matter.

**Dependencies:** US-24, US-11, US-15

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements review PASS. All 3 ACs are testable. AC-25.1: the virtual-clock test is split into two concrete assertions — (a) snapshot fires at t0+score_at_elapsed_s and (b) changing the active config value shifts the trigger time — together they verify Principle #1 compliance without relying on code inspection alone. The capture_buffer_s note is correctly tagged as 'already enforced by P1 Pydantic schema' and does not require a new test here. AC-25.2: no-op re-run test verifies both the row count (exactly one) and the absence of a second fetch attempt (can be asserted via mock or call-count on the DataSource); covers retry/restart/duplicate-event scenarios. AC-25.3: combines ReplaySource end-to-end replay with the H2 manifest guard in a single story — the two together verify replay-testability and the absence of a re-introduced poll. Dependencies US-24 → US-25 ordering is correct and explicitly declared. No scope issues.

---

### US-26: P4 — units locked (D1): the three-unit (vol_sol/vol_usd/sol_usd) invariant + unit-invariant feature parity backbone, CI-wired (§6.2 D1, §7.1)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-26.1:** Every recorded swap carries all three units (vol_sol, vol_usd, sol_usd) with the relationship LOCKED: vol_usd ≈ vol_sol · sol_usd within a tolerance declared as a named constant in the test (e.g. REL_TOL = 1e-6 for exact fixed-point products, or a wider bound if floating-point intermediates are used — the implementation must document the chosen tolerance), and sol_usd is the per-block SOL/USD reference (D1) — none of the three is silently dropped or zeroed. Verified by a pytest test over recorded swaps asserting the three-unit relationship holds within the declared tolerance and all three fields are non-null and non-zero.
- [ ] **AC-26.2:** Unit-invariant features (shares, ratios, counts) are computed byte-identically regardless of which unit basis is chosen — the parity backbone (D1). Verified by a unit-parity pytest test: a unit-invariant aggregate (e.g. a per-trader volume share, or a buy/sell flow ratio) over the same swaps is byte-identical whether derived via vol_sol or vol_usd; any quote-unit (non-invariant) feature explicitly declares its unit column.
- [ ] **AC-26.3:** The unit-parity test (26.1 + 26.2) is wired into the single canonical ci.yml 'test' job (H1 — no second workflow) so it cannot silently vanish (a compile-time ImportError trap on the named test function(s), mirroring AC-21.3). Verified by the tests passing on the feature branch and at merge, and by the trap failing collection if the test file is deleted/renamed. New files carry metadata front matter.

**Dependencies:** US-17, US-18

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements review PASS with one direct fix applied. AC-26.1 originally said 'within a declared tolerance' without specifying how that tolerance must be declared — amended to require it as a named constant in the test and to note that the implementation must document the chosen value; this prevents the tolerance from being buried as a magic number. All 3 ACs are testable: AC-26.1 verifies three-unit presence (non-null, non-zero) plus the arithmetic invariant within the named tolerance; AC-26.2 uses byte-identical comparison of a unit-invariant aggregate derived both ways — this is a deterministic, repeatable assertion given the same swap records; AC-26.3 uses the established ImportError-trap pattern from AC-21.3 to make the wiring unforgeable. US-26 is correctly declared as independent of the snapshot chain (depends US-17/18 only) and may proceed in parallel with US-24/US-25. No scope issues.

---

### US-27: P4 — the offline gate: deterministic ReplaySource snapshot replay → expected raw 'snapshots' row + raw-immutable re-derivability + the #380/at-most-once/unit-parity regression suite (§16, §6.4)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-27.1:** P4 OFFLINE GATE (§16): replaying a captured-or-synthetic Birdeye score-time snapshot through a ReplaySource + virtual clock stores EXACTLY the expected raw 'snapshots' row, deterministically — run twice and the stored raw JSONB is byte-identical. The fixture is a schema-faithful Birdeye snapshot payload (§6.3); if a real Birdeye REST snapshot is banked (per E3) it is used here. Verified by the replay test asserting the expected stored snapshot and run-twice determinism.
- [ ] **AC-27.2:** Raw = immutable truth (§6.4.1): the stored snapshot is the verbatim raw payload — never mutated, never re-pulled — and the typed captured fields (holders/authority/lp_burned/liquidity/tvl/depth) are re-derivable from it. Verified by a pytest test asserting the persisted raw JSONB equals the source payload (json-safe-normalized) and that the US-23.3 schema re-derives from the stored raw without a second fetch.
- [ ] **AC-27.3:** The P4 regression suite is green in CI and wired so it cannot silently vanish: the #380 future-window-clamp test (US-24.3), the at-most-one-snapshot / idempotency test (US-23.2 / US-25.2), and the unit-parity test (US-26) all pass in the single canonical ci.yml 'test' job (H1 — no second workflow), wired via a compile-time ImportError trap on the named test functions (mirroring AC-21.3). Verified by the tests passing on the feature branch and at merge. New files carry metadata front matter.

**Dependencies:** US-23, US-24, US-25, US-26

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements review PASS. All 3 ACs are testable. AC-27.1: run-twice byte-identical determinism is the same gate that proved out the P3 offline gate (US-21) — an established, executable pattern; the fixture can be synthetic, keeping the gate live-activation-free as required. AC-27.2: two-part assertion (persisted JSONB == json-safe-normalized source, then re-derive typed schema from stored raw without a second fetch) is concrete and verifiable; 'without a second fetch' can be asserted by confirming the DataSource is not called during re-derivation. AC-27.3 is the regression harness that collects three previously-tested ACs (AC-24.3 / AC-23.2+25.2 / AC-26) under ImportError traps in a single H1 job — this mirrors AC-21.3 exactly and prevents silent removal. All upstream dependencies (US-23/24/25/26) are correctly declared; US-27 must be last. No scope issues.

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
Sprint-6 requirements review complete (2026-06-16). All 5 stories and 17 ACs pass the testability gate. Every AC names a specific, runnable pytest verification; no AC relies on manual inspection or vague output matching. The established guard patterns (H1 ImportError trap, H2 manifest, US-2 static-analysis guard, JsonSafeEncoder H3 check) are correctly extended into the new stories — no new guard patterns are introduced without a known precedent. One direct fix applied: AC-26.1 'declared tolerance' was amended to require a named constant in the test and implementation documentation (prevents a magic number obscuring the invariant). Build order (US-23 → US-24 → US-25; US-26 parallel; US-27 last) is reflected correctly in dependency declarations. No firehose activation is required for any AC in this sprint — the P4 offline gate is synthetic and the score-time read is a single on-demand REST snapshot. No scope defects or PO-judgment items identified. Dev may begin with US-23.

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint6.json` — do not edit directly._
