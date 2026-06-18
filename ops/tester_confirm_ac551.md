# ---
# file: ops/tester_confirm_ac551.md
# project: solanatrilly
# purpose: AC-55.1 — Tester confirmation that a single GREEN deploy run on 'main' at HEAD
#          carries the FULL P6 dashboard chain (US-48, US-49, US-50, US-51) to the VPS
#          solanatrilly staging stack, with AC-39.2 phase-promoter + AC-12.3
#          retry-with-backoff confirmed preserved.
# story: US-55 AC-55.1
# sprint: sprint-11
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-55.1

## Purpose

AC-55.1 is the P6 dashboard-chain CLOSEOUT deploy step. With the deploy path sound
(US-52/US-53/US-54), this step obtains ONE ACTUAL GREEN deploy run on 'main' at HEAD
that carries the FULL P6 dashboard chain to the VPS solanatrilly staging stack.

The full P6 dashboard chain consists of:

- **US-48** — Dashboard foundation + ONE-tape-feed consumer (React frontend + Django Channels WebSocket)
- **US-49** — tape→candle API + token-detail/research view (`/api/candles/<mint>/`)
- **US-50** — cohort wall (`/api/cohort/`)
- **US-51** — annotation + export (`/api/annotations/<mint>/`)

The green run must confirm:
1. **AC-39.2 phase-promoter preserved** — `promote_sprint_phase.py` gates the deploy
2. **AC-12.3 retry-with-backoff preserved** — `SMOKE_MAX_ATTEMPTS` + `SMOKE_RETRY_DELAY` present

This record serves as the durable evidence artifact for AC-55.1.

---

## Background: P6 Dashboard Chain

The P6 dashboard chain was blocked by the J1 deploy defect (invalid WebSocket key) in sprint-10.
US-52/US-53/US-54 restored the deploy path:

| Fix | AC | Failure mode addressed |
|---|---|---|
| `push: branches: [main]` restored | AC-52.1 | Per-merge deploys re-enabled |
| `down --remove-orphans` before up | AC-52.3 | Orphaned-container naming conflicts |
| RFC-6455-valid WS key | AC-52.1 | J1 WS-key defect |
| `docker image prune -f` before pull | AC-53.2 | J2/J4 disk exhaustion (ENOSPC) |
| celery-worker container verified Up | AC-53.3 | Scorer container not confirmed |
| Regression guard (9 invariants pinned) | AC-54.2 | Silent regression between runs |
| Reproducibility confirmed (deliberate + per-merge) | AC-54.3 | J4 regression class |

With the deploy path sound, this run carries all four P6 stories in a single HEAD deploy.

---

## P6 Dashboard Chain APIs (available after green deploy)

| Story | Endpoint | Description |
|---|---|---|
| US-48 | `/dashboard/` | React dashboard entry point (HTTP 200) |
| US-48 | `/ws/tape/<mint>/` | WebSocket tape feed (HTTP 101 upgrade) |
| US-49 | `/api/candles/<mint>/` | tape→candle OHLCV API |
| US-50 | `/api/cohort/` | Cohort wall pattern-mining API |
| US-51 | `/api/annotations/<mint>/` | Annotation list/create + export |

---

## AC-39.2 Phase-Promoter Preserved

The `Phase-promoter pre-deploy gate (AC-39.2)` step runs `python3 tools/promote_sprint_phase.py`
before the VPS deploy step in deploy.yml. This gates every deploy on sprint-phase integrity.

| Field | Value |
|---|---|
| Step name in deploy.yml | `Phase-promoter pre-deploy gate (AC-39.2)` |
| Script | `tools/promote_sprint_phase.py` |
| Ordering | Confirmed before `Deploy to VPS staging stack` step |
| Status | PENDING — confirm from green run |

---

## AC-12.3 Retry-with-Backoff Preserved

The `Smoke-test staging stack (AC-6.4 / AC-12.3)` step uses `SMOKE_MAX_ATTEMPTS=12` and
`SMOKE_RETRY_DELAY=5` to retry HTTP 200 on `/health/` with backoff.

| Field | Value |
|---|---|
| SMOKE_MAX_ATTEMPTS | 12 (12 attempts) |
| SMOKE_RETRY_DELAY | 5 (5 seconds between attempts) |
| Max wait | 60 seconds |
| Status | PENDING — confirm from green run |

---

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `push` to `branches: [main]` OR `workflow_dispatch` on main |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |
| AC-39.2 phase-promoter confirmed present | PENDING |
| AC-12.3 retry-with-backoff confirmed present | PENDING |
| Full P6 dashboard chain (US-48/US-49/US-50/US-51) carried | PENDING |

---

## VPS Verification Conditions — AC-55.1

All conditions must be confirmed from the actual green deploy run:

| # | Condition | Enforced by deploy.yml step | Status |
|---|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff (`SMOKE_MAX_ATTEMPTS=12`, `SMOKE_RETRY_DELAY=5`) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` | PENDING |
| 2 | Dashboard route HTTP 200 (`/dashboard/`) | `Smoke-test dashboard route HTTP 200 (AC-48.3)` | PENDING |
| 3 | WS endpoint HTTP 101 upgrade (`/ws/tape/smoke_test/`) | `Smoke-test WS endpoint HTTP 101 upgrade (AC-48.3)` | PENDING |
| 4 | `web` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 5 | `frontend` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 6 | `listener` container Up in the solanatrilly stack | `Verify listener container Up (AC-33.3)` | PENDING |
| 7 | `celery-worker` container Up in the solanatrilly stack | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 8 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |

---

## Structural Guards in deploy.yml

The following deploy.yml steps enforce all VPS conditions structurally,
so a green deploy run constitutes direct Tester confirmation of the full P6 chain:

| Condition | deploy.yml step |
|---|---|
| HTTP 200 on 8002 with retry-backoff (AC-12.3) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` |
| Dashboard route HTTP 200 (US-48) | `Smoke-test dashboard route HTTP 200 (AC-48.3)` |
| WS endpoint HTTP 101 (US-48) | `Smoke-test WS endpoint HTTP 101 upgrade (AC-48.3)` |
| web + frontend containers Up (US-48 P6 foundation) | `Verify frontend and web containers Up (AC-48.3)` |
| listener container Up | `Verify listener container Up (AC-33.3)` |
| celery-worker container Up | `Verify celery-worker container Up (AC-53.3)` |
| solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` |
| AC-39.2 phase-promoter gates every deploy | `Phase-promoter pre-deploy gate (AC-39.2)` |

The regression guard (AC-54.2, `core/tests/test_deploy_regression_guard_ac542.py`) pins
all nine load-bearing deploy invariants so a structural test fails loudly if any is removed.

---

## Verdict

**PENDING — awaiting actual green deploy run on main.**

Once the green run ID/URL is filled in above and all VPS conditions are confirmed,
this record will be updated by the Tester to `status: confirmed` and all conditions
marked PASS.

The green run must carry the FULL P6 dashboard chain (US-48, US-49, US-50, US-51)
with AC-39.2 phase-promoter and AC-12.3 retry-with-backoff both preserved.
