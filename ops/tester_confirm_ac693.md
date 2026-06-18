# ---
# file: ops/tester_confirm_ac693.md
# project: solanatrilly
# purpose: AC-69.3 — Tester confirmation that a green deploy run on 'main' carries the
#          US-69 Live Positions dashboard board (AC-69.2 DRF API + React view) to the VPS
#          solanatrilly staging stack: Live Positions open+closed API return HTTP 200,
#          containers Up, solanaBilly UNTOUCHED on 8001, nine-invariant deploy guard green.
# story: US-69 AC-69.3
# sprint: sprint-13
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-69.3

## Purpose

AC-69.3 is the US-69 Live Positions dashboard board CLOSEOUT deploy step. After delivering
the project-wide async-safety guard (AC-69.1) and the Live Positions DRF API + React view
(AC-69.2), this step obtains ONE ACTUAL GREEN deploy run on 'main' at HEAD that carries
the full Live Positions feature to the VPS solanatrilly staging stack.

This record is the durable evidence artifact for AC-69.3. It documents the green deploy
run ID, the Live Positions API HTTP 200 confirmation on both endpoints, all container Up
conditions, the solanaBilly isolation check, and the nine-invariant guard (AC-54.2) status.

---

## Live Positions Feature (US-69 AC-69.2)

The following files comprise the Live Positions dashboard board deployed by this AC:

| File | Role |
|---|---|
| `trading/api.py` | DRF views: `positions_open_view` (PAPER+OPEN rows) and `positions_closed_view` (settled/closed rows). Reads shared US-64 Position rows for both source=model and source=copytrade. Zero firehose — no live price source. |
| `trading/urls.py` | URL routing: `/api/trading/positions/open/` and `/api/trading/positions/closed/` |
| `frontend/src/LivePositions.jsx` | React component: Open Positions + Closed Positions tables with source filter. Entry price from Position row — NOT a live Birdeye call. Metadata front matter + H1 ImportError trap. |

AC-69.3 smoke-test conditions (verified by deploy.yml steps):

| Endpoint | Expected | deploy.yml step |
|---|---|---|
| `GET /api/trading/positions/open/` | HTTP 200 | `Smoke-test US-69 Live Positions open API (AC-69.3)` |
| `GET /api/trading/positions/closed/` | HTTP 200 | `Smoke-test US-69 Live Positions closed API (AC-69.3)` |

Both endpoints serve the shared US-64 Position table (source=model and source=copytrade)
and do NOT contact any live price source (zero firehose constraint, AC-69.2).

---

## Nine-Invariant Deploy Guard (AC-54.2)

The nine-invariant deploy guard (`core/tests/test_deploy_regression_guard_ac542.py`, AC-54.2)
pins all nine load-bearing deploy invariants so the deploy path cannot silently regress.
The guard runs in the canonical CI `test` job and must be green before any deploy proceeds.

| Invariant | Description |
|---|---|
| INVARIANT-1 | `--remove-orphans` in `up -d` command |
| INVARIANT-2 | `down --remove-orphans` before `up` |
| INVARIANT-3 | AC-39.2 phase-promoter step exists |
| INVARIANT-4 | Phase-promoter ordered BEFORE VPS deploy |
| INVARIANT-5 | AC-12.3 retry-with-backoff smoke-test |
| INVARIANT-6 | Unified workflow_call gate (no inline pytest) |
| INVARIANT-7 | RFC-6455-valid WS key |
| INVARIANT-8 | VPS disk-exhaustion fix (`docker image prune -f`) |
| INVARIANT-9 | Per-merge push trigger (`push: branches: [main]`) |

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
| Live Positions open API HTTP 200 | PENDING |
| Live Positions closed API HTTP 200 | PENDING |
| Nine-invariant guard (AC-54.2) green in CI | PENDING |

---

## VPS Verification Conditions — AC-69.3

All conditions must be confirmed from the actual green deploy run:

| # | Condition | Enforced by deploy.yml step | Status |
|---|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff | `Smoke-test staging stack (AC-6.4 / AC-12.3)` | PENDING |
| 2 | `GET /api/trading/positions/open/` returns HTTP 200 | `Smoke-test US-69 Live Positions open API (AC-69.3)` | PENDING |
| 3 | `GET /api/trading/positions/closed/` returns HTTP 200 | `Smoke-test US-69 Live Positions closed API (AC-69.3)` | PENDING |
| 4 | `copytrade_engine` container Up in the solanatrilly stack | `Verify copytrade_engine container Up (AC-59.3)` | PENDING |
| 5 | `listener` container Up in the solanatrilly stack | `Verify listener container Up (AC-33.3)` | PENDING |
| 6 | `web` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 7 | `celery-worker` container Up in the solanatrilly stack | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 8 | `frontend` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 9 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |
| 10 | Nine-invariant deploy guard (AC-54.2) green in CI | `test` job in ci.yml → `test_deploy_regression_guard_ac542.py` | PENDING |

---

## Safety Gate Preserved

AC-69.3 is OBSERVE/PAPER-FIRST — the trading apparatus is deployed with `trading_enabled`
DEFAULT False (no real orders, no capital). The Live Positions API surfaces Position rows
already in the database from replay/paper runs:

- Zero firehose: both `/api/trading/positions/open/` and `/api/trading/positions/closed/`
  read from the shared US-64 Position table with NO live Birdeye price calls
- `trading_enabled=False` (default) — live orders never sent this sprint
- Cutover-gated: capital activation is an operator-driven step (PRD §16)
- 8 Birdeye + 8 Helius remain banked

---

## Structural Guards in deploy.yml

| Condition | deploy.yml step |
|---|---|
| HTTP 200 on 8002 with retry-backoff (AC-12.3) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` |
| Live Positions open API HTTP 200 | `Smoke-test US-69 Live Positions open API (AC-69.3)` |
| Live Positions closed API HTTP 200 | `Smoke-test US-69 Live Positions closed API (AC-69.3)` |
| copytrade_engine container Up | `Verify copytrade_engine container Up (AC-59.3)` |
| listener container Up | `Verify listener container Up (AC-33.3)` |
| web + frontend containers Up | `Verify frontend and web containers Up (AC-48.3)` |
| celery-worker container Up | `Verify celery-worker container Up (AC-53.3)` |
| solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` |
| Nine-invariant guard (AC-54.2) gates CI | `core/tests/test_deploy_regression_guard_ac542.py` in CI test job |

---

## Metadata Front Matter Verification

AC-69.3 requires all new backend AND frontend files from AC-69.2 carry metadata front matter.

| File | Front Matter Present |
|---|---|
| `trading/api.py` | Yes — `# ---` header with module, sprint, story, status, created-by |
| `trading/urls.py` | Yes — `# ---` header with module, sprint, story, status, created-by |
| `trading/tests/test_live_positions_api_ac692.py` | Yes — `# ---` header |
| `frontend/src/LivePositions.jsx` | Yes — `// ---` header with file, stack, purpose, created-by, sprint, story |

---

## Verdict

**PENDING — awaiting actual green deploy run on main.**

Once the green run ID/URL is filled in above and all ten VPS conditions are confirmed,
this record will be updated by the Tester to `status: confirmed` and all conditions
marked PASS.

The green run must carry the US-69 Live Positions board (AC-69.2 DRF API + React view)
with:
- Live Positions open API HTTP 200 (condition 2)
- Live Positions closed API HTTP 200 (condition 3)
- all five containers Up (conditions 4-8)
- solanaBilly UNTOUCHED on 8001 (condition 9)
- nine-invariant guard (AC-54.2) green (condition 10)
