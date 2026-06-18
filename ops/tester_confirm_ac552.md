# ---
# file: ops/tester_confirm_ac552.md
# project: solanatrilly
# purpose: AC-55.2 — Tester VPS-CONFIRMATION of the full P6 dashboard chain from the green
#          deploy run obtained in AC-55.1: HTTP 200 on 8002, dashboard route, WS upgrade
#          (HTTP 101), US-49 candle API, US-50 cohort wall API, US-51 annotation list API,
#          all four containers Up, solanaBilly untouched on 8001. US-49/US-50 promoted to
#          PASSED; US-51 receives its final VPS-gate sign-off.
# story: US-55 AC-55.2
# sprint: sprint-11
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-55.2

## Purpose

AC-55.2 is the Tester VPS-CONFIRMATION step for the full P6 dashboard chain, using the
actual green deploy run obtained in AC-55.1 (US-52/US-53/US-54 deploy path repairs).

This record confirms:

1. **All P6 dashboard chain VPS conditions** — HTTP 200 on 8002, dashboard route 200, WS
   upgrade HTTP 101, US-49/US-50/US-51 API endpoints respond, all four containers Up,
   solanaBilly untouched on 8001.
2. **US-49 (tape→candle API + token-detail/research view) promoted to PASSED** — CI-verified
   code-complete in sprint-10; blocked only by the J1 deploy defect (WS-key); no
   application-code change required for promotion.
3. **US-50 (cohort pattern-mining wall) promoted to PASSED** — same: CI-verified
   code-complete in sprint-10; blocked only by the J1 deploy defect; no application-code
   change required.
4. **US-51 (annotation + export) final VPS-gate sign-off** — US-51's requirements were
   approved in sprint-10; this record is the final VPS-gate sign-off confirming the
   annotation list/create + export endpoints are live on the VPS.

This record serves as the durable evidence artifact for AC-55.2.

---

## Background: P6 Dashboard Chain VPS Confirmation

The P6 dashboard chain was blocked by the J1 deploy defect in sprint-10.
US-52/US-53/US-54 restored the deploy path, and AC-55.1 obtained the green deploy run.
AC-55.2 is the Tester-VPS-CONFIRMS step that closes out the P6 offline gate.

### US-49/US-50 Status Context

Both stories were CI-green in sprint-10 but blocked from VPS-confirmation by the J1
WebSocket key defect (run 27681451880). No application-code changes are needed:

| Story | Status in sprint-10 | Blocker | AC-55.2 promotion |
|---|---|---|---|
| US-49 (candle API + token-detail view) | CI-green, VPS-unconfirmed | J1 WS-key defect | → PASSED |
| US-50 (cohort wall) | CI-green, VPS-unconfirmed | J1 WS-key defect | → PASSED |
| US-51 (annotation + export) | Requirements-approved, CI-green | J1 WS-key defect | Final VPS sign-off |

### P6 Dashboard Chain APIs

| Story | Endpoint | Smoke-test in deploy.yml |
|---|---|---|
| US-48 | `/dashboard/` | `Smoke-test dashboard route HTTP 200 (AC-48.3)` |
| US-48 | `/ws/tape/smoke_test/` | `Smoke-test WS endpoint HTTP 101 upgrade (AC-48.3)` |
| US-49 | `/api/candles/smoke_test/?interval_s=60` | `Smoke-test US-49 candle API (AC-55.2)` |
| US-50 | `/api/cohort/?interval_s=60` | `Smoke-test US-50 cohort wall API (AC-55.2)` |
| US-51 | `/api/annotations/smoke_test/` | `Smoke-test US-51 annotation list API (AC-55.2)` |

---

## Green Deploy Run (from AC-55.1)

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `push` to `branches: [main]` OR `workflow_dispatch` on main |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |

---

## VPS Verification Conditions — AC-55.2

All conditions must be confirmed from the actual green deploy run obtained in AC-55.1:

| # | Condition | Enforced by deploy.yml step | Status |
|---|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff (`SMOKE_MAX_ATTEMPTS=12`, `SMOKE_RETRY_DELAY=5`) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` | PENDING |
| 2 | Dashboard route HTTP 200 (`/dashboard/`) | `Smoke-test dashboard route HTTP 200 (AC-48.3)` | PENDING |
| 3 | WS endpoint HTTP 101 upgrade (`/ws/tape/smoke_test/`) | `Smoke-test WS endpoint HTTP 101 upgrade (AC-48.3)` | PENDING |
| 4 | US-49 candle API HTTP 200 (`/api/candles/smoke_test/?interval_s=60`) | `Smoke-test US-49 candle API (AC-55.2)` | PENDING |
| 5 | US-50 cohort wall API HTTP 200 (`/api/cohort/?interval_s=60`) | `Smoke-test US-50 cohort wall API (AC-55.2)` | PENDING |
| 6 | US-51 annotation list API HTTP 200 (`/api/annotations/smoke_test/`) | `Smoke-test US-51 annotation list API (AC-55.2)` | PENDING |
| 7 | `web` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 8 | `frontend` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 9 | `listener` container Up in the solanatrilly stack | `Verify listener container Up (AC-33.3)` | PENDING |
| 10 | `celery-worker` container Up in the solanatrilly stack | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 11 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |

---

## US-49 Promotion to PASSED

**Story:** US-49 — tape→candle API + token-detail/research view

**AC-55.2 promotion basis:**
- All US-49 ACs were CI-green in sprint-10 (candle API, token-detail view, parity tests).
- The story was blocked from VPS-confirmation by the J1 WS-key defect only.
- US-52/US-53/US-54 restored the deploy path; no application-code change to US-49 required.
- AC-55.2's `Smoke-test US-49 candle API (AC-55.2)` step confirms the endpoint is live on the VPS.

| Field | Value |
|---|---|
| Promotion basis | CI-verified code-complete; blocked only by J1 deploy defect (now fixed) |
| Smoke-test endpoint | `/api/candles/smoke_test/?interval_s=60` → HTTP 200 |
| No application-code change | Confirmed — US-49 code unchanged since sprint-10 CI green |
| Promotion status | PENDING — confirmed on actual green deploy run |

---

## US-50 Promotion to PASSED

**Story:** US-50 — cohort pattern-mining wall

**AC-55.2 promotion basis:**
- All US-50 ACs were CI-green in sprint-10 (cohort sparklines, wall assembly, grouping/sort).
- The story was blocked from VPS-confirmation by the J1 WS-key defect only.
- US-52/US-53/US-54 restored the deploy path; no application-code change to US-50 required.
- AC-55.2's `Smoke-test US-50 cohort wall API (AC-55.2)` step confirms the endpoint is live.

| Field | Value |
|---|---|
| Promotion basis | CI-verified code-complete; blocked only by J1 deploy defect (now fixed) |
| Smoke-test endpoint | `/api/cohort/?interval_s=60` → HTTP 200 |
| No application-code change | Confirmed — US-50 code unchanged since sprint-10 CI green |
| Promotion status | PENDING — confirmed on actual green deploy run |

---

## US-51 VPS-Gate Sign-Off

**Story:** US-51 — annotation + export (human annotation → labeled export)

**AC-55.2 sign-off basis:**
- US-51 requirements were approved in sprint-10 (Tester-approved ACs 51.1/51.2/51.3).
- All US-51 ACs were CI-green in sprint-10.
- The VPS-gate was the final outstanding condition.
- AC-55.2's `Smoke-test US-51 annotation list API (AC-55.2)` step confirms the annotation
  endpoint is live on the VPS.

| Field | Value |
|---|---|
| Requirements approval | Tester-approved in sprint-10 (ACs 51.1/51.2/51.3) |
| Smoke-test endpoint | `/api/annotations/smoke_test/` → HTTP 200 |
| VPS-gate sign-off status | PENDING — confirmed on actual green deploy run |

---

## P6 Offline Gate VPS-CONFIRMED Status

Once AC-55.2 is confirmed, the P6 offline gate is VPS-CONFIRMED:

> **"Operator sees real candles for a replayed token"** — verified live on the VPS,
> not merely green locally (PRD §15.2/§16, P6 phase DoD).

| P6 Story | sprint-10 status | AC-55.2 outcome |
|---|---|---|
| US-48 (foundation + tape-feed consumer) | VPS-confirmed (AC-52.3) | Already PASSED |
| US-49 (candle API + token-detail view) | CI-green, VPS-unconfirmed | → PASSED |
| US-50 (cohort wall) | CI-green, VPS-unconfirmed | → PASSED |
| US-51 (annotation + export) | Requirements-approved, CI-green | Final VPS sign-off |

Firehose budget: **UNTOUCHED** — AC-55.2 is VPS infra confirmation only; zero firehose
activations (8 Birdeye + 8 Helius remain banked, per sprint-11 DoD).

---

## Verdict

**PENDING — awaiting actual green deploy run on main.**

Once the green run ID/URL is filled in above and all VPS conditions are confirmed,
this record will be updated by the Tester to `status: confirmed`, all conditions marked
PASS, and US-49/US-50/US-51 promotions recorded above.

The P6 offline gate is VPS-CONFIRMED when this record is updated to `status: confirmed`.
