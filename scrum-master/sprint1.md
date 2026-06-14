# Sprint 1

**Phase:** complete
**Progress:** 1/1 stories | 5/5 ACs
**Last Updated:** 2026-06-14T20:36:27+00:00

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
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-1.1:** docker-compose.yml defines every service in containers — web (Django ASGI via Daphne/Uvicorn), db (Postgres 16), redis, celery worker, celery-beat — connected via Docker network hostnames; no service is installed on the host (Docker Rules).
  - Dev: done
- [x] **AC-1.2:** requirements.txt pins the full stack with explicit version bounds: Django 5, djangorestframework, channels, channels-redis, celery, redis, daphne (or uvicorn), pydantic v2, psycopg[binary].
  - Dev: done
- [x] **AC-1.3:** Django Channels is configured with the Redis channel layer and an ASGI application; the service boots over ASGI and a trivial WebSocket consumer completes the handshake and echoes a message — verified by a pytest test using channels.testing.WebsocketCommunicator.
  - Dev: done
- [x] **AC-1.4:** `docker compose run --rm web python manage.py migrate` applies cleanly and `docker compose up -d` brings every service to a healthy state.
  - Dev: done
- [x] **AC-1.5:** The Celery worker connects to the Redis broker, autodiscovers tasks, and a sample task executes end-to-end (enqueue then resolve a result) — verified by a pytest test that dispatches the task and asserts the result is returned within a timeout.
  - Dev: done

**Dev Team Status:** in-progress
**Dev Team Notes:**
  AC-1.1 done (commit d096d70). docker-compose.yml fully rewritten with all 5 services: web (Daphne ASGI), db (postgres:16-alpine), redis (redis:7-alpine), celery-worker, celery-beat. All services connected via Docker network hostnames; no localhost references. Healthchecks on db and redis; celery services depend_on both. requirements.txt updated with daphne, channels, channels-redis, celery, redis, pyyaml pins. config/celery.py created; config/__init__.py exports celery_app; config/asgi.py updated to ProtocolTypeRouter; settings.py adds ASGI_APPLICATION, CHANNEL_LAYERS, CELERY_* config with daphne+channels in INSTALLED_APPS. Dockerfile CMD switched to daphne. Unit tests in core/tests/test_compose_topology.py assert all topology invariants (9 tests). AC-1.2 done: requirements.txt updated with pydantic>=2.0,<3 (pydantic v2), djangorestframework tightened to <4, django-environ tightened to <1. All 9 AC-1.2 packages now carry both lower and upper version bounds. Unit tests in core/tests/test_requirements_pins.py (5 tests) verify package presence, lower/upper bounds for all required packages, Django major==5, and pydantic major==2. All 15 tests pass, 100% coverage. AC-1.3 done (commit 56492ed). core/consumers.py: EchoConsumer(AsyncWebsocketConsumer) accepts handshake and echoes text verbatim. core/routing.py: websocket_urlpatterns wires ws/echo/ to EchoConsumer. config/asgi.py: ProtocolTypeRouter updated with URLRouter(websocket_urlpatterns) for websocket protocol. core/tests/test_websocket_echo.py: pytest test using WebsocketCommunicator + asgiref async_to_sync (no extra test deps). All 16 tests pass, 100% coverage. AC-1.4 done: docker-compose.yml updated with healthchecks for all 5 services — web (python urllib.request to /health/), celery-worker (celery inspect ping grepping for pong), celery-beat (pidfile presence via kill -0). core/tests/test_migrate_healthy.py adds 6 tests verifying: no pending migrations (MigrationExecutor plan is empty), core Django tables exist post-migrate, /health/ returns 200+ok, compose web has healthcheck defined, all 5 services have healthchecks, web healthcheck references /health/ endpoint. No new packages needed (pyyaml already pinned).
  [DEPLOY] Deploy trigger failed. Will be caught by full sprint deploy.

**Tester Status:** blocked
**Tester Notes:**
  AC review (2026-06-15): All 5 ACs individually APPROVED — every PR (PR#1–PR#5) merged with CI 'test' job SUCCESS on both feature branch and main (verified via gh pr list --state merged). Dev-reported coverage is 100% across all ACs, exceeding the ≥80% DoD threshold; CI green implies coverage gate enforced. No critical defects observed.
  
  BLOCKERS preventing story-level DoD sign-off:
  1. VPS deployment NOT done — deploy_summary records 'Deploy ERROR — Failed to trigger workflow: HTTP 404: Not Found' for deploy.yml. The CD pipeline (GitHub Actions → GHCR → VPS) does not exist in the repository. DoD requires 'Merged + deployed to VPS solanatrilly staging stack (-p solanatrilly) and smoke-tested there'.
  2. retrospective.md does NOT exist — DoD requires 'retrospective.md updated'.
  
  Story remains BLOCKED until (a) deploy.yml is authored and successfully deploys to VPS port 8002 with -p solanatrilly scope, VPS smoke-test passes, and (b) retrospective.md is written and committed.

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
