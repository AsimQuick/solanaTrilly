# ---
# file: ops/tester_confirm_ac733.md
# project: solanatrilly
# purpose: AC-73.3 — Tester confirmation that the Replay viewer overlay API
#          and route return HTTP 200 on port 8002 in an actual green deploy run;
#          containers Up; solanaBilly UNTOUCHED on 8001; deploy regression guard GREEN.
# story: US-73 AC-73.3
# sprint: sprint-14
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-73.3

## Purpose

AC-73.3 is the US-73 VPS deploy closeout step. After AC-73.1 built the Replay viewer
overlay DRF API (`GET /api/trading/replay/overlay/?run_id=<id>`) and AC-73.2 wired the
`ReplayViewer.jsx` React view into `App.jsx` (route `view === 'replay'`), this step
obtains confirmation from ONE ACTUAL GREEN deploy run that the API and route are live and
returning HTTP 200 on port 8002.

This record is the durable evidence artifact for AC-73.3. It documents the green deploy run ID,
the replay overlay API smoke-test step outcome, all container Up conditions, the solanaBilly
isolation check, and the deploy regression guard status.

Zero firehose — the replay overlay API reads existing Position rows for a given run_id and
returns empty positions for unknown run IDs; no live Birdeye call.

---

## New Smoke-Test Step (AC-73.3)

The deploy pipeline now includes:

```
Smoke-test US-73 Replay viewer overlay API (AC-73.3)
  → GET http://<VPS_HOST>:8002/api/trading/replay/overlay/?run_id=smoke-test-ac733
  → Expects HTTP 200 (up to 6 attempts, 5 s delay)
```

This step runs in the `deploy` job of `deploy.yml` after the US-72 Calibration & PnL
analytics API smoke test and before the solanaBilly isolation check. It is pinned by
INVARIANT-14 in the extended deploy regression guard
(`core/tests/test_deploy_regression_guard_ac712.py`).

---

## API Background (AC-73.1)

The Replay viewer overlay API returns position open/close events for a given pipeline run,
enabling the ReplayViewer chart overlay to display entry/exit markers.

| Behaviour | Description |
|---|---|
| Known `run_id` | Returns list of position overlay events (open/close) |
| Unknown `run_id` | Returns HTTP 200 with empty positions list |
| Missing `run_id` | Returns HTTP 400 |

- Reads Position rows filtered by `run_id`
- No live Birdeye call
- HTTP 200 for any provided run_id (even unknown ones)

---

## React View Background (AC-73.2)

`ReplayViewer.jsx` renders position open/close overlay markers on the TradingView
Lightweight Chart canvas, wired into `App.jsx` with route `view === 'replay'`. The
dashboard `/dashboard/` route returns HTTP 200 (already pinned by AC-48.3 smoke test),
and the `ReplayViewer` component is rendered when `?view=replay` is selected.

---

## Extended Regression Guard (INVARIANT-14)

The extended deploy regression guard (`core/tests/test_deploy_regression_guard_ac712.py`)
now pins five invariants:

| Invariant | Pins |
|---|---|
| INVARIANT-10 | `Pre-deploy built-image import smoke (AC-71.1)` step in `ci.yml` |
| INVARIANT-11 | AC-68.3 step in `deploy.yml` with `django.setup()` prefix (PR #296) |
| INVARIANT-12 | Both AC-69.3 Live Positions open/closed smoke steps in `deploy.yml` |
| INVARIANT-13 | `Smoke-test US-72 Calibration & PnL analytics API (AC-72.3)` step in `deploy.yml` |
| INVARIANT-14 | `Smoke-test US-73 Replay viewer overlay API (AC-73.3)` step in `deploy.yml` |

A green CI run on this branch confirms all five invariants are in force.

---

## Deploy Pipeline — AC-73.3 Wiring

```
deploy.yml (workflow_dispatch)
  ├── ci (job)
  │     uses: ./.github/workflows/ci.yml
  │     (includes pre-deploy import smoke, lint, tests)
  │
  ├── build-and-push (job)  [needs: ci]
  │     Pushes web + frontend images to GHCR
  │
  └── deploy (job)  [needs: build-and-push]
        1. Deploy to VPS staging stack
        2. Smoke-test staging stack (AC-6.4 / AC-12.3)  → /health/ HTTP 200
        3. Smoke-test dashboard route (AC-48.3)           → /dashboard/ HTTP 200
        ...
        N-2. Smoke-test US-72 Calibration & PnL analytics API (AC-72.3)
        N-1. Smoke-test US-73 Replay viewer overlay API (AC-73.3)  ← NEW
        N.   Verify solanaBilly isolation (AC-6.5 / AC-12.4)
```

---

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `workflow_dispatch` on main (HEAD carries US-73) |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |

---

## VPS Verification Conditions — AC-73.3

All conditions must be confirmed from the actual green deploy run log:

| # | Condition | Step / evidence | Status |
|---|---|---|---|
| 1 | `Smoke-test US-73 Replay viewer overlay API (AC-73.3)` step PRESENT in deploy job | Step name visible in run log | PENDING |
| 2 | Replay viewer overlay API returns HTTP 200 at port 8002 | Step conclusion: success | PENDING |
| 3 | `Verify shared apparatus imports in-container (AC-68.3)` step GREEN | Step conclusion: success | PENDING |
| 4 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff | `Smoke-test staging stack` step | PENDING |
| 5 | `copytrade_engine` container Up | `Verify copytrade_engine container Up (AC-59.3)` | PENDING |
| 6 | `listener` container Up | `Verify listener container Up (AC-33.3)` | PENDING |
| 7 | `web` container Up | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 8 | `celery-worker` container Up | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 9 | `frontend` container Up | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 10 | solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |
| 11 | Extended regression guard INVARIANT-14 GREEN in CI | CI `test` job — `test_deploy_regression_guard_ac712.py` | PENDING |
| 12 | Deploy regression guard (all invariants 10–14) GREEN | CI `test` job | PENDING |

---

## Tester Notes

*To be completed by the Tester after the actual green deploy run:*

- The `Smoke-test US-73 Replay viewer overlay API (AC-73.3)` step should appear as a
  GREEN step inside the `deploy` job of the deploy run. Its step log should end with
  `Replay viewer overlay API PASS on attempt N: GET /api/trading/replay/overlay/?run_id=smoke-test-ac733 returned HTTP 200`.

- Confirm solanaBilly is still running on port 8001 (solanaBilly isolation step GREEN).

- Confirm all container Up conditions from the run log.

- Update the PENDING fields above with the actual run ID, URL, and timestamp.
