# Sprint 2

**Phase:** planning
**Progress:** 3/6 stories | 11/22 ACs
**Last Updated:** 2026-06-15T06:32:50+00:00

## Sprint Goal
Complete the P0 foundation by landing the six remaining P0 stories (US-2…US-7): the DataSource/virtual-clock testing seam, hardened CI (H1 pinned actions, H2 task-manifest test, H3 json_safe encoder), an automated CD pipeline that deploys a hello-world solanaTrilly to the isolated VPS staging stack (-p solanatrilly, port 8002) — proving the local → GitHub → GHCR → VPS path end-to-end and retroactively closing US-1's deploy-gated DoD — with the firehose activation ledger seeded. Exit P0 with a tested, deployed, drift-resistant base ready for P1 (config core). Build order: (US-2, US-3, US-4, US-5 in parallel) → US-6 (needs US-1 + US-3) → US-7 any time.

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/retrospective.md`
- `CLAUDE.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter
- [ ] All services run in Docker (no host installs)
- [ ] CD pipeline (US-6) delivered. Once it is live, every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there. Per retrospective A2, the deploy clause is gated at the SPRINT boundary — a non-CD story landing before US-6 is not structurally blocked by the absent pipeline; it deploys once US-6 exists.
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on port 8001 untouched)
- [ ] retrospective.md updated for sprint-2 (named owner: Tester / scrum facilitator — retrospective A3)

## User Stories

### US-2: DataSource interface + injectable virtual clock (the live/replay seam)
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-2.1:** A DataSource abstract interface is defined (e.g. core/datasource.py) expressing the event-stream contract; concrete LiveSource and ReplaySource both implement it; consumers (detection/feature-assembly/scoring/exit/settlement on the core path) depend ONLY on the interface, never on a concrete source — verified by a static-analysis test (or import inspection) asserting no consumer module directly imports a concrete source class (Principle #7 / PRD §4).
  - Dev: done
- [x] **AC-2.2:** An injectable Clock abstraction exists: WallClock (live, wall-time) and VirtualClock (replay, time advanced deterministically/explicitly). Core code reads 'now' only from the injected clock — no direct time.time()/datetime.now() on the core path — verified by a pytest test that drives core logic with a VirtualClock.
  - Dev: done
- [x] **AC-2.3:** A no-op ReplaySource over an empty/fixture event log resolves and yields its events through the SAME code path as LiveSource, with a VirtualClock driving time — verified by a pytest test (satisfies the P0 offline gate: 'a no-op Replay source resolves').
  - Dev: done
- [x] **AC-2.4:** Thin-adapter ground rule enforced: the clock-injected core is pure (imports no Celery/Channels/network modules); Celery tasks and WS consumers are thin adapters around it — verified by a test asserting the core module's import graph is framework/network-free. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-1

**Dev Team Status:** done
**Dev Team Notes:**
  AC-2.1 done. Three files added: core/datasource.py (DataSource ABC with connect/disconnect/events contract), core/live_source.py (LiveSource stub — Birdeye WS impl deferred to P1), core/replay_source.py (ReplaySource iterates an injected event_log). Seven tests in core/tests/test_datasource_interface.py cover: abstract instantiation guard, issubclass checks for both concretes, event ordering, empty-log, stub lifecycle, and the AC-required static-analysis guard (scans core/detection|features|scoring|exit|settlement for direct LiveSource/ReplaySource imports — passes trivially while those packages don't exist, becomes a regression gate as they land). All 34 tests pass; coverage 98%. AC-2.2 done. core/clock.py adds Clock ABC, WallClock (delegates to datetime.now(UTC)), VirtualClock (advance()/set_time() API), and stamp_events() async generator that pairs each DataSource event with clock.now(). core/tests/test_clock.py adds 6 tests: WallClock UTC check, VirtualClock initial/advance/set_time, the key stamp_events test (ReplaySource + VirtualClock, 3 events, 10s advance between each — timestamps verified against expected values), and a static-analysis guard using AST Call-node inspection (not line scanning) to reject any datetime.now()/time.time() call in core source except inside WallClock.now. All 40 tests pass. AC-2.3 done. core/tests/test_replay_ac23.py adds 4 tests: (1) test_noop_replay_empty_log_resolves — empty ReplaySource through stamp_events with VirtualClock yields zero events (the P0 offline gate); (2) test_fixture_replay_yields_events_via_stamp_events — fixture log through stamp_events with advancing VirtualClock, timestamps verified per event; (3) test_live_source_through_same_code_path — LiveSource stub through the identical stamp_events path resolves cleanly; (4) test_both_sources_polymorphic_via_datasource — both typed as DataSource and passed to the same _drain() wrapper, proving stamp_events is source-type-agnostic. No new production code required — stamp_events (AC-2.2) is the common code path. All 44 tests pass; coverage 100%. CI LINT FIX (2026-06-15): Two ruff errors fixed — (1) I001 in core/clock.py: sorted typing imports alphabetically ('Any, AsyncGenerator' replaces 'AsyncGenerator, Any'); (2) F401 in core/tests/test_clock.py: removed unused 'import pytest' (no test function called any pytest API directly). AC-2.4 done. core/tests/test_thin_adapter.py adds 2 tests: (1) test_core_modules_import_no_framework_or_network — AST import-node scan of all 4 pure-core files (datasource.py, clock.py, live_source.py, replay_source.py); asserts each file exists and contains no imports from forbidden namespaces (celery, channels, requests, httpx, aiohttp, websockets, urllib3); (2) test_adapter_modules_exist_and_use_frameworks — verifies tasks.py and consumers.py both exist and DO import their expected frameworks (celery and channels respectively), confirming they are the thin adapters. Top-level namespace extraction handles dotted module names. All 46 tests pass; coverage 100%.

**Tester Status:** failed
**Tester Notes:**
  CI FAILED on branch feature/US-2-AC-2.3 (runs 27512110990 and 27512115019) — Lint step only; pytest was never reached. Diagnosis: CODE BUG in AC-2.2 deliverables, not a requirements issue. Two ruff errors in AC-2.2 files: (1) I001 in core/clock.py:25 — import block unsorted; `from typing import AsyncGenerator, Any` has `Any` after `AsyncGenerator` but isort requires alphabetical order within the from-import name list (fix: `from typing import Any, AsyncGenerator`). (2) F401 in core/tests/test_clock.py:38 — `import pytest` is unused; none of the 6 test functions call any pytest API directly (fix: remove the import line). Severity: LOW — all 44 tests pass locally; the failures are style-only and both are auto-fixable with `ruff --fix`. Recommended fix: dev team applies `ruff --fix core/clock.py core/tests/test_clock.py`, verifies locally with `ruff check .`, and pushes. AC-2.1 (tester: not-started — CI never ran pytest). AC-2.2 (tester: failed — source of lint errors). AC-2.3 (tester: blocked — lint gate prevented pytest execution). AC-2.4 (tester: not-started — not yet implemented).

---

### US-3: H1 — hardened, SHA-pinned CI as a hard merge gate
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-3.1:** Every GitHub Action in ci.yml is pinned to a full 40-char commit SHA (not a tag/branch ref): actions/checkout, setup-python, and any other action — verified by a script/test that scans the workflow and fails on any 'uses:' line not pinned to a SHA (PRD §12 S5/H1).
  - Dev: done
- [x] **AC-3.2:** The CI runner is frozen to a specific OS image (e.g. ubuntu-24.04, never *-latest); ci.yml is the single canonical CI workflow — one source of CI truth, no parallel/duplicate workflows — verified by the CI-config self-test (AC 3.4) which also asserts no *-latest runner string appears in any workflow file and that only one workflow file defines the test job.
  - Dev: done
- [x] **AC-3.3:** CI is a hard merge gate: the 'test' job runs pytest with the >=80% coverage gate (pytest --cov-fail-under=80) and must be green before merge, enforced via the gitops orchestrator (require_ci_pass: true) since GitHub Free branch protection is unavailable on this private repo (per po-requests.md item 1).
  - Dev: done
- [x] **AC-3.4:** A CI-config self-test asserts the H1 invariants (all actions SHA-pinned + runner frozen to a non-*-latest image + single workflow file for the test job) so a future unpinned 'uses:' or a *-latest runner FAILS CI — the 11-PR Node-24 CI flail (S5) cannot recur. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-1

**Dev Team Status:** in-progress
**Dev Team Notes:**
  AC-3.1 done. Two files changed: (1) .github/workflows/ci.yml — actions/checkout@v4 replaced with actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2 (full 40-char commit SHA, verified via gh api); (2) core/tests/test_ci_sha_pins.py (new) — single test test_all_workflow_actions_sha_pinned that discovers all *.yml/*.yaml workflow files under .github/workflows/, parses each with yaml.safe_load, extracts every job-step uses: value, exempts local (./) and Docker (docker://) actions, and asserts the ref after @ matches ^[0-9a-f]{40}$. Fails with per-violation detail if any action is tag/branch-pinned. 47 tests pass; coverage 100%. AC-3.2 done. Two changes: (1) .github/workflows/ci.yml — runs-on changed from ubuntu-latest to ubuntu-24.04, freezing the runner to an explicit versioned OS image; (2) core/tests/test_ci_runner_freeze.py (new) — two tests: test_no_latest_runner_in_any_workflow scans all workflow files and asserts no job runs-on value contains 'latest' (handles string, list, and expression forms); test_single_workflow_defines_test_job asserts exactly one workflow file defines a job with id 'test', enforcing ci.yml as the single canonical CI workflow. Both tests are the AC-3.4 self-test assertions for the runner-frozen and single-workflow invariants. 49 tests pass; coverage 100%.
  AC-3.3 done: Pushed successfully. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-3.3 Implementation Summary**
  
  **Files changed (2 new):**
  
  1. **`gitops.json`** (new) — Machine-readable orchestrator config at repo root. Sets `require_ci_pass: true` and `coverage_threshold: 80`. The project-lead script reads this before merging any PR; since GitHub Free does not support branch protection on private repos, this config is the sole enforcement mechanism preventing a merge while the `test` CI job is red.
  
  2. **`core/tests/test_ci_merge_gate.py`** (new) — 3 tests that assert all AC-3.3 invariants:
     - `test_ci_test_job_has_coverage_gate` — parses `ci.yml`, locates the `test` job, and asserts at least one `run:` step contains `--cov-fail-under=N` with N ≥ 80
     - `test_gitops_config_requires_ci_pass` — reads `gitops.json` and asserts `require_ci_pass` is `true`
     - `test_gitops_coverage_threshold_matches_ci` — cross-checks that `gitops.json`'s `coverage_threshold` matches the `--cov-fail-under` value in `ci.yml`, preventing silent drift between the two sources of truth
  
  **Note:** The coverage flag `--cov-fail-under=80` was already present in `ci.yml` (line 33) from prior work; AC …
  AC-3.4 done: Pushed successfully.
  
  ---
  
  **Implementation summary for the orchestrator:**
  
  **AC-3.4: CI-config self-test for H1 invariants**
  
  **Files changed (1 new):**
  
  1. **`core/tests/test_ci_config_selftest.py`** — The authoritative H1 self-test module. Contains 3 tests that assert all three CI hardening invariants in one place:
     - `test_h1_all_actions_sha_pinned` — scans all `*.yml`/`*.yaml` files under `.github/workflows/`, extracts every step-level `uses:` value, exempts local (`./`) and Docker (`docker://`) actions, and asserts each remaining ref is pinned to exactly 40 lowercase hex characters after `@`. Any mutable tag or branch ref fails CI.
     - `test_h1_runner_frozen_no_latest` — scans all workflow files and asserts no job's `runs-on` value contains the substring `latest` (handles string, list, and expression forms). Any `*-latest` runner fails CI.
     - `test_h1_single_workflow_defines_test_job` — asserts exactly one workflow file defines a job with id `test`, keeping `ci.yml` as the single canonical CI workflow and preventing split-brain CI state.
     
     File carries metadata front matter (`module`, `sprint`, `story`, `status`, `created-by`, `last-updated`, `dependencies`).
  
  **Te …
  [DEPLOY] Deploy trigger failed. Will be caught by full sprint deploy.

**Tester Status:** approved
**Tester Notes:**
  All 4 ACs are testable and verifiable. Minor fix applied to AC 3.2: added explicit verification method referencing the AC 3.4 self-test, and clarified that it also checks for no *-latest runner and a single workflow file (the original text stated the rule without naming how to verify). AC 3.4 was also tightened to explicitly list the three invariants it asserts (SHA-pinned, runner frozen, single workflow file), matching the updated AC 3.2. ACs 3.1 and 3.3 are well-defined with clear verification criteria. Approved for development.

---

### US-4: H2 — Celery task-manifest registration test
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-4.1:** A committed task manifest (e.g. core/task_manifest.json) enumerates every expected registered Celery task; Django app autodiscovery (celery_app.autodiscover_tasks) registers tasks from installed apps (PRD §12 S6/H2).
  - Dev: done
- [x] **AC-4.2:** A pytest test asserts the live registered-task set (celery_app.tasks, excluding celery built-ins) EQUALS the committed manifest: it FAILS when a task is removed/renamed (even if that task's own test was also deleted — the #404 failure mode) AND FAILS when a task is added without updating the manifest.
  - Dev: done
- [x] **AC-4.3:** The manifest test runs inside the CI merge gate; the dev workflow guard is documented in CONTRIBUTING.md (or a dedicated section of the project README) — listing the steps: 'git show --stat HEAD' before push, and never 'git stash' between add and commit (S6 scars). New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-1

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-4.1 done: Branch is up to date with remote. The implementation is complete and pushed.
  
  ---
  
  ## Implementation Summary
  
  **Files changed:**
  
  1. **`core/task_manifest.json`** (new) — Committed JSON manifest enumerating the two expected Celery tasks (`core.tasks.add`, `core.tasks.ping`) with `version` and `description` fields. This is the single source of truth for what tasks the app is expected to register.
  
  2. **`core/tests/test_task_manifest_ac41.py`** (new) — 4 unit tests validating AC-4.1:
     - `test_manifest_file_exists_at_committed_path` — asserts the manifest exists at `core/task_manifest.json`
     - `test_manifest_is_valid_json_with_required_structure` — asserts valid JSON with `"version"` and non-empty `"tasks"` keys
     - `test_all_manifest_tasks_are_registered_via_autodiscovery` — imports `core.tasks`, loads the manifest, and asserts every manifest entry is present in `celery_app.tasks`
     - `test_celery_app_calls_autodiscover_tasks` — static analysis of `config/celery.py` confirming `autodiscover_tasks` is called
  
  **Test count:** 4 new tests (no broker/Redis required — registry-only inspection).
  
  **Coverage:** No new production code branches added; new tests exercise the manifest fil …
  AC-4.2 done: Done.
  
  ---
  
  **Implementation summary for the orchestrator:**
  
  **AC-4.2: Bidirectional manifest-equality test**
  
  **Files changed (1 new):**
  
  1. **`core/tests/test_task_manifest_ac42.py`** — 3 tests implementing the strict bidirectional equality check between the live Celery task registry and `core/task_manifest.json`:
     - `test_registered_tasks_equal_manifest` — computes `manifest_tasks` set and `registered_tasks` set (non-builtin only, filtered by `celery.` prefix), reports both failure modes separately: tasks in manifest but not registered (#404 failure mode — removed/renamed task) and tasks registered but not in manifest (added without manifest update). This single test catches both AC-required failure scenarios by strict set equality without needing to delete or add tasks in test code.
     - `test_registered_set_is_nonempty` — guards against the degenerate case where both registry and manifest are emptied simultaneously (would make equality trivially pass).
     - `test_manifest_tasks_is_nonempty` — paired guard ensuring the manifest itself is non-empty.
  
  **Test count:** 3 new tests (62 total). **Coverage:** 100%. No production code changes required — the manifest and tasks from A …
  AC-4.3 done: Both files look correct. The commit is on the right branch and pushed. Here's the implementation summary for the orchestrator:
  
  ---
  
  ## AC-4.3 Implementation Summary
  
  **Files changed (2 new):**
  
  1. **`CONTRIBUTING.md`** (repo root) — YAML front matter. Documents the Celery task-manifest dev workflow guard with two required steps:
     - Step 1: run `git show --stat HEAD` after every commit and before every push to verify commit content
     - Step 2: never run `git stash` between `git add` and `git commit` (S6 scars — this class of mistake caused a committed manifest to diverge silently from the task registry)
     
     Also includes the S6 incident explanation, safe commit sequencing examples, and a manifest-update how-to guide.
  
  2. **`core/tests/test_contributing_guard_ac43.py`** — Metadata front matter. 4 tests:
     - `test_contributing_md_exists` — asserts CONTRIBUTING.md exists at the repo root
     - `test_contributing_md_contains_git_show_stat_step` — asserts `git show --stat HEAD` is present
     - `test_contributing_md_warns_against_git_stash` — asserts `git stash` appears (as an explicit warning)
     - `test_ci_runs_manifest_test_via_pytest` — loads `ci.yml` via `yaml.safe_load`, find …
  [DEPLOY] Deploy trigger failed. Will be caught by full sprint deploy.

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs are testable and verifiable. Minor fix applied to AC 4.3: the original text said the dev workflow guard 'is documented' without specifying where; updated to require CONTRIBUTING.md or a named README section so Tester can verify its existence. ACs 4.1 and 4.2 are well-defined: 4.1 is verifiable by file inspection and 4.2 specifies both failure modes (removed/renamed task and added-without-manifest-update) with clear test criteria. Approved for development.

---

### US-5: H3 — single json_safe JSONField encoder at every write site
**Status:** ready | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-5.1:** One json_safe() encoder exists (non-finite float NaN/Inf -> null, Decimal -> float, datetime -> ISO-8601 string), implemented as a custom Django JSONField encoder class — a single shared implementation, not duplicated per call site (PRD §12 S7/H3).
- [ ] **AC-5.2:** The encoder is applied at every JSONB write site (every models.JSONField uses encoder=json_safe / all JSONB writes route through it) — verified by a test that round-trips a NaN/Inf/Decimal/datetime payload and asserts the stored value is psycopg-safe, valid JSON.
- [ ] **AC-5.3:** A guard test asserts no model JSONField is declared WITHOUT the json_safe encoder, so a future field cannot silently bypass it and reintroduce the #331/#332/#388 JSONB/psycopg crash class. New files carry metadata front matter.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs are testable and verifiable. No fixes required. AC 5.1 states a single shared implementation (verifiable by code inspection and by the guard test in 5.3 catching any duplication). AC 5.2 specifies a round-trip test with explicit payload types and a psycopg-safe assertion. AC 5.3 closes the regression loop with a structural guard test. The three ACs form a coherent triad: implementation + behavioral test + structural guard. Approved for development.

---

### US-6: CD pipeline (GitHub Actions → GHCR → VPS staging) + hello-world live under hard isolation
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-6.1:** A deploy.yml GitHub Actions workflow exists: on merge to main it runs the hardened CI (H1, US-3), builds the web image, and pushes it to GHCR (ghcr.io/asimquick/solanatrilly) using the built-in GITHUB_TOKEN with permissions: { packages: write } (no extra secret — per po-requests.md item 2). Every action in deploy.yml is SHA-pinned (H1).
- [ ] **AC-6.2:** The repo ships docker-compose.staging.yml as the ONLY VPS compose (PRD §15.4): compose project -p solanatrilly, web on port 8002, distinct Postgres database + volume, distinct Redis, distinct Docker network — the VPS is never hand-edited; it runs exactly what is in the repo.
- [ ] **AC-6.3:** The deploy step SSHes to the VPS (VPS_USER@VPS_HOST via VPS_SSH_KEY repo secrets) and runs 'docker compose -p solanatrilly -f docker-compose.staging.yml pull && up -d' to pull the tested GHCR image. Every docker command in the deploy script is -p solanatrilly-scoped — verified by inspecting deploy.yml for any unscoped down / up --force-recreate / prune / volume removal command; solanaBilly's containers/volumes (port 8001) are never touched (PRD §15.3, hard isolation).
- [ ] **AC-6.4:** A hello-world Django endpoint is live on the VPS staging stack at port 8002, and a CD smoke-test step (run after deploy) hits it and asserts HTTP 200 — proving the local -> GitHub -> GHCR -> VPS path end-to-end (PRD §15.5, 'VPS presence from P0').
- [ ] **AC-6.5:** Deploying US-1's containerized topology through this pipeline retroactively closes US-1's story-level DoD (the sprint-1 deploy blocker recorded in retrospective A1); the deployed stack's hard isolation from live solanaBilly is verified on the VPS by confirming solanaBilly's containers remain running ('docker compose -p solanabilly ps' shows containers up) and port 8001 still responds after the solanatrilly deployment. New files carry metadata front matter.

**Dependencies:** US-1, US-3

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 5 ACs are testable and verifiable. Minor fixes applied: AC 6.3 tightened the isolation claim by adding 'verified by inspecting deploy.yml for any unscoped command', giving the Tester a concrete artifact to check. AC 6.5 replaced the vague 'verified on the box' with a concrete method: 'docker compose -p solanabilly ps shows containers up' and 'port 8001 still responds after the solanatrilly deployment', making isolation falsifiable. ACs 6.1, 6.2, and 6.4 are well-defined with clear artifacts (deploy.yml, docker-compose.staging.yml, HTTP 200 smoke test). Approved for development.

---

### US-7: Firehose activation ledger seeded
**Status:** ready | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-7.1:** ops/firehose_activation_log.md is created and seeded with the project-wide budget: 10 Birdeye + 10 Helius activations, 0 used (remaining 10 / 10). API keys are already in .env (per po-requests.md item 3) — the ledger references them, never commits them (PRD §15.7).
- [ ] **AC-7.2:** The ledger documents the per-activation protocol — deliberate, time-boxed (<=30 min default; adjustable by explicit PO decision), PR-reviewed — with a table schema of columns: date, role/agent, which WS (Birdeye/Helius), purpose, duration, count-remaining, fixtures banked.
- [ ] **AC-7.3:** The ledger states the HARD RULE that every activation MUST bank durable fixtures (a tape/detection sample or golden vectors) into the lake / golden set so the spend compounds into the replay corpus; the doc is re-indexed via mcp__devrag__reindex_document (project convention). New files carry metadata front matter.

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs are testable and verifiable by file inspection. Minor fix applied to AC 7.2: replaced the unresolved placeholder '[TO TUNE]' with '<=30 min default; adjustable by explicit PO decision', making the criterion concrete and auditable. ACs 7.1 and 7.3 are clear: 7.1 requires specific budget numbers and a no-commit-keys rule, both verifiable by inspection; 7.3 requires a stated hard rule and re-indexing confirmation. Approved for development.

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
_Pending_

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint2.json` — do not edit directly._
