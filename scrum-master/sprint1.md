# Sprint 1

**Phase:** planning
**Progress:** 0/3 stories | 4/12 ACs
**Last Updated:** 2026-06-14T20:11:10+00:00

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
- [x] **AC-1.3:** Django Channels is configured with the Redis channel layer and an ASGI application; the service boots over ASGI and a trivial WebSocket consumer completes the handshake and echoes a message — verified by a pytest test using channels.testing.WebsocketCommunicator.
  - Dev: done
- [x] **AC-1.4:** `docker compose run --rm web python manage.py migrate` applies cleanly and `docker compose up -d` brings every service to a healthy state.
  - Dev: done
- [ ] **AC-1.5:** The Celery worker connects to the Redis broker, autodiscovers tasks, and a sample task executes end-to-end (enqueue then resolve a result) — verified by a pytest test that dispatches the task and asserts the result is returned within a timeout.

**Dev Team Status:** in-progress
**Dev Team Notes:**
  AC-1.1 done (commit d096d70). docker-compose.yml fully rewritten with all 5 services: web (Daphne ASGI), db (postgres:16-alpine), redis (redis:7-alpine), celery-worker, celery-beat. All services connected via Docker network hostnames; no localhost references. Healthchecks on db and redis; celery services depend_on both. requirements.txt updated with daphne, channels, channels-redis, celery, redis, pyyaml pins. config/celery.py created; config/__init__.py exports celery_app; config/asgi.py updated to ProtocolTypeRouter; settings.py adds ASGI_APPLICATION, CHANNEL_LAYERS, CELERY_* config with daphne+channels in INSTALLED_APPS. Dockerfile CMD switched to daphne. Unit tests in core/tests/test_compose_topology.py assert all topology invariants (9 tests). AC-1.2 done: requirements.txt updated with pydantic>=2.0,<3 (pydantic v2), djangorestframework tightened to <4, django-environ tightened to <1. All 9 AC-1.2 packages now carry both lower and upper version bounds. Unit tests in core/tests/test_requirements_pins.py (5 tests) verify package presence, lower/upper bounds for all required packages, Django major==5, and pydantic major==2. All 15 tests pass, 100% coverage. AC-1.3 done (commit 56492ed). core/consumers.py: EchoConsumer(AsyncWebsocketConsumer) accepts handshake and echoes text verbatim. core/routing.py: websocket_urlpatterns wires ws/echo/ to EchoConsumer. config/asgi.py: ProtocolTypeRouter updated with URLRouter(websocket_urlpatterns) for websocket protocol. core/tests/test_websocket_echo.py: pytest test using WebsocketCommunicator + asgiref async_to_sync (no extra test deps). All 16 tests pass, 100% coverage. AC-1.4 done: docker-compose.yml updated with healthchecks for all 5 services — web (python urllib.request to /health/), celery-worker (celery inspect ping grepping for pong), celery-beat (pidfile presence via kill -0). core/tests/test_migrate_healthy.py adds 6 tests verifying: no pending migrations (MigrationExecutor plan is empty), core Django tables exist post-migrate, /health/ returns 200+ok, compose web has healthcheck defined, all 5 services have healthchecks, web healthcheck references /health/ endpoint. No new packages needed (pyyaml already pinned).

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

### US-3: H1—hardened CI: pinned GitHub Actions, task-manifest test, json_safe encoder
**Status:** draft | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-3.1:** All GitHub Actions in `.github/workflows/` are pinned to full SHAs (not floating tags); a CI step audits all action references and fails the build if any floating tags remain.
- [ ] **AC-3.2:** A task-manifest test runs on every PR; it reads `core/tasks.py`, parses all registered Celery tasks, and asserts that every task name matches the expected naming convention (e.g., `app.task.subcomponent` format) and has a docstring.
- [ ] **AC-3.3:** A `json_safe` encoder is registered with Django's JSONEncoder; it handles Decimal, datetime, UUID, and bytes types; a pytest test verifies round-trip encoding/decoding of a fixture containing all handled types.

**Dependencies:** US-1

**Dev Team Status:** not-started

**Tester Status:** not-started

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
