# Sprint 1

**Phase:** planning
**Progress:** 0/7 stories | 2/27 ACs
**Last Updated:** 2026-06-14T19:52:22+00:00

## Sprint Goal
Land the P0 foundation (PRD §16): a fully containerized Django 5 + DRF + Channels + Celery stack with the DataSource/virtual-clock testing seam, hardened CI (H1 pinned actions, H2 task-manifest test, H3 json_safe encoder), and an automated CD pipeline that deploys a hello-world solanaTrilly to the isolated VPS staging stack (-p solanatrilly, port 8002) — proving the local→GitHub→GHCR→VPS path end-to-end on day one, with the firehose activation ledger seeded.

## Reference Documents
- `scrum-master/PRD.md`
- `CLAUDE.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter
- [ ] All services run in Docker (no host installs)
- [ ] Merged + deployed to VPS solanatrilly staging stack (-p solanatrilly) and smoke-tested there
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly)
- [ ] retrospective.md updated

## User Stories

### US-1: Containerized full-stack service topology (Django 5 + DRF + Channels + Celery + Redis + Postgres)
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-1.1:** docker-compose.yml defines every service in containers — web (Django ASGI via Daphne/Uvicorn), db (Postgres 16), redis, celery worker, celery-beat — connected via Docker network hostnames; no service is installed on the host (Docker Rules).
  - Dev: done
- [x] **AC-1.2:** requirements.txt pins the full stack with explicit version bounds: Django 5, djangorestframework, channels, channels-redis, celery, redis, daphne (or uvicorn), pydantic v2, psycopg[binary].
  - Dev: done
- [ ] **AC-1.3:** Django Channels is configured with the Redis channel layer and an ASGI application; the service boots over ASGI and a trivial WebSocket consumer completes the handshake and echoes a message — verified by a pytest test using channels.testing.WebsocketCommunicator.
- [ ] **AC-1.4:** `docker compose run --rm web python manage.py migrate` applies cleanly and `docker compose up -d` brings every service to a healthy state.
- [ ] **AC-1.5:** The Celery worker connects to the Redis broker, autodiscovers tasks, and a sample task executes end-to-end (enqueue then resolve a result) — verified by a pytest test that dispatches the task and asserts the result is returned within a timeout.

**Dev Team Status:** in-progress
**Dev Team Notes:**
  AC-1.1 done (commit d096d70). docker-compose.yml fully rewritten with all 5 services: web (Daphne ASGI), db (postgres:16-alpine), redis (redis:7-alpine), celery-worker, celery-beat. All services connected via Docker network hostnames; no localhost references. Healthchecks on db and redis; celery services depend_on both. requirements.txt updated with daphne, channels, channels-redis, celery, redis, pyyaml pins. config/celery.py created; config/__init__.py exports celery_app; config/asgi.py updated to ProtocolTypeRouter; settings.py adds ASGI_APPLICATION, CHANNEL_LAYERS, CELERY_* config with daphne+channels in INSTALLED_APPS. Dockerfile CMD switched to daphne. Unit tests in core/tests/test_compose_topology.py assert all topology invariants (9 tests). AC-1.2 done: requirements.txt updated with pydantic>=2.0,<3 (pydantic v2), djangorestframework tightened to <4, django-environ tightened to <1. All 9 AC-1.2 packages now carry both lower and upper version bounds. Unit tests in core/tests/test_requirements_pins.py (5 tests) verify package presence, lower/upper bounds for all required packages, Django major==5, and pydantic major==2. All 15 tests pass, 100% coverage.

**Tester Status:** approved
**Tester Notes:**
  All 5 ACs are CI-verifiable. Minor clarifications added to AC 1.3 (WebsocketCommunicator) and AC 1.5 (assert result within timeout) to make the automated verification mechanism explicit. AC 1.4 requires both sub-checks (migrate exit code + docker compose ps healthy) to pass in CI.

---

### US-2: DataSource interface + injectable virtual clock — the testable live/replay seam (Principle #7, PRD §11.3)
**Status:** draft | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-2.1:** A `DataSource` abstract interface is defined with `LiveSource` and `ReplaySource` implementations that satisfy the same contract; consumers depend on the interface, never a concrete source.
- [ ] **AC-2.2:** An injectable clock abstraction exists (wall-clock for live, virtual clock for replay); pipeline core reads current time only through the injected clock — enforced by a CI lint step (ruff rule or ast-grep assertion) that fails the build if any direct `datetime.now()` or `time.time()` calls appear in modules under `solanatrilly/core/`.
- [ ] **AC-2.3:** A no-op `ReplaySource` resolves and yields an empty event stream fully offline (no network calls); an automated pytest test instantiates `ReplaySource`, advances the virtual clock, and asserts the event stream is empty — the test must pass without any live network connections.
- [ ] **AC-2.4:** Celery and Channels are thin adapters around a clock-injected pure core; 'core' is defined as the `solanatrilly/core/` package (no Celery or Channels imports permitted there). A CI test imports every module under `solanatrilly/core/` and asserts none of their transitive imports resolve to `celery` or `channels` symbols, failing if any are found.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Two precision fixes applied: AC 2.2 now specifies the CI lint enforcement mechanism for the 'no direct clock calls' constraint (ruff/ast-grep on solanatrilly/core/); AC 2.4 anchors 'core modules' to the `solanatrilly/core/` package path so the import-check test has a stable, non-vacuous target.

---

### US-3: H1 — hardened, SHA-pinned CI as a hard merge gate (PRD §12 S5)
**Status:** draft | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-3.1:** Every GitHub Action in the workflow is pinned to a full 40-character commit SHA (no floating tags such as @v4).
- [ ] **AC-3.2:** The CI runner is pinned to an explicit Ubuntu image version (not `ubuntu-latest`).
- [ ] **AC-3.3:** One canonical ci.yml runs build + ruff lint + pytest with the coverage gate (>=80%), all inside Docker.
- [ ] **AC-3.4:** The `test` status check is configured as a required check on main; verified by a CI step that calls `gh api repos/{owner}/{repo}/branches/main/protection` and asserts `required_status_checks.contexts` contains `test` — the step fails if branch protection is not set. Initial configuration is a one-time human action (repo Settings → Branches) documented in the sprint file.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  ACs 3.1–3.3 are grep/file-inspectable. AC 3.4 fixed: branch protection cannot self-verify via CI alone, so the AC now specifies a `gh api` assertion step that confirms the protection rule is active; the one-time human setup step is explicitly noted as a prerequisite.

---

### US-4: H2 — Celery task-manifest registration test (PRD §12 S6)
**Status:** draft | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-4.1:** Django/Celery app autodiscovery is configured so tasks register automatically across installed apps.
- [ ] **AC-4.2:** A committed manifest file enumerates the expected registered Celery task set.
- [ ] **AC-4.3:** A CI test asserts the live registered-task set equals the committed manifest, failing if any task in the manifest is absent from the registered set OR if any registered task is absent from the manifest. The test is verified non-vacuous by a separate negative-path test case that temporarily removes a task name from the manifest and asserts the comparison function returns a non-empty diff.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  AC 4.3 fixed: the original 'demonstrated by a deliberate removal during review' conflated a one-time review ritual with a persistent CI gate. Replaced with a durable negative-path unit test that asserts the comparison function returns a non-empty diff when a task is missing — this runs on every CI build and is objectively re-verifiable.

---

### US-5: H3 — single json_safe JSONField encoder applied at every write site (PRD §12 S7)
**Status:** draft | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-5.1:** A single `json_safe()` encoder converts non-finite floats (NaN/Inf/-Inf) to null, Decimal to float, and datetime to an ISO-8601 string.
- [ ] **AC-5.2:** `json_safe` is set as the project-wide default encoder for JSONField by subclassing JSONField in a base module (e.g., `solanatrilly/core/fields.py`) or via Django's `FIELD_DEFAULTS`; a CI test asserts no JSONField instantiation in the codebase overrides the encoder to a different value, and a Django system check (`AppConfig.ready`) raises `ImproperlyConfigured` if the encoder is not registered — ensuring coverage at every JSONB write site by construction.
- [ ] **AC-5.3:** Unit tests cover NaN, +Inf, -Inf, Decimal, datetime, and nested container values.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  AC 5.2 fixed: 'applied at every JSONB write site by default' was not verifiable as written (per-field patching could satisfy the letter but not the spirit). Replaced with a concrete enforcement pattern — project-wide base JSONField subclass plus a CI grep for overrides and a Django system check — making universality CI-provable.

---

### US-6: CD pipeline (GitHub Actions → GHCR → VPS staging) with hello-world deployed live under hard isolation (PRD §15.3–15.5)
**Status:** draft | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-6.1:** A GitHub Actions CD workflow builds the image on merge to main (after CI passes) and pushes it to GHCR.
- [ ] **AC-6.2:** docker-compose.staging.yml is the only VPS compose and enforces isolation: compose project -p solanatrilly, web on port 8002, distinct Postgres DB + volume, distinct Redis, distinct Docker network.
- [ ] **AC-6.3:** CD deploys the tested GHCR image to the VPS staging stack by pulling the image — never builds on the box, never hand-edits the compose on the box.
- [ ] **AC-6.4:** A hello-world Django endpoint is reachable on the VPS at port 8002 and an automated post-deploy smoke test hits it and passes.
- [ ] **AC-6.5:** Every docker and docker compose command in the deploy job is scoped with `-p solanatrilly`; a CI lint step (grep or script) inspects the deploy workflow YAML and fails the build if any docker command is present without the `-p solanatrilly` flag or if any of the forbidden unscoped commands (down, up --force-recreate, prune, volume rm) appear. Human code review confirms no out-of-band VPS commands were introduced.

**Dependencies:** US-1, US-3

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  ACs 6.1–6.4 are inspectable (workflow YAML structure, compose file content, smoke-test step). AC 6.5 fixed: 'verified in review' was not a CI assertion. Replaced with a static lint step that greps the deploy YAML for unscoped docker commands — making the isolation guarantee CI-provable on every merge. Human review remains as a secondary backstop. Note: US-6 is blocked on the human dependency documented in po-requests.md (GitHub remote + GHCR/VPS SSH secrets must be provisioned before this story can be implemented).

---

### US-7: Firehose activation ledger seeded (PRD §15.7)
**Status:** draft | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-7.1:** ops/firehose_activation_log.md is created with the ledger columns: date, role/agent, which WS (Birdeye/Helius), purpose, duration, count remaining, what was captured.
- [ ] **AC-7.2:** The starting budget is recorded — 10 Birdeye + 10 Helius activations project-wide, 0 used.
- [ ] **AC-7.3:** The ledger states the activation rules: each activation is deliberate, time-boxed (<=30 min), must bank durable fixtures to the lake/golden set, and is PR-reviewed so the remaining count stays visible.

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  All 3 ACs are verifiable by file-existence check (ops/firehose_activation_log.md present) and content grep (column headers, budget numbers, rule text). Doc-only story; no scope issues.

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
_Auto-generated from `sprint1.json` — do not edit directly._
