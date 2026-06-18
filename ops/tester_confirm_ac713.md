# ---
# file: ops/tester_confirm_ac713.md
# project: solanatrilly
# purpose: AC-71.3 — Tester confirmation that the pre-deploy import smoke (AC-71.1)
#          runs and passes in the actual VPS deploy pipeline carrying US-71, and that
#          the post-deploy in-container import step (AC-68.3) stays GREEN.
# story: US-71 AC-71.3
# sprint: sprint-14
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-71.3

## Purpose

AC-71.3 is the M2 recurrence-guard CLOSEOUT verify step. After AC-71.1 added the
pre-deploy built-image import smoke to `ci.yml` and AC-71.2 pinned it in the extended
deploy regression guard, this step obtains confirmation from ONE ACTUAL GREEN deploy
run that carries US-71: the smoke step (`Pre-deploy built-image import smoke (AC-71.1)`)
runs and passes in the live pipeline before any image is pushed to GHCR, and the
post-deploy in-container import step (`Verify shared apparatus imports in-container (AC-68.3)`)
stays GREEN.

This record is the durable evidence artifact for AC-71.3. It documents the green deploy
run ID, the import smoke step outcome, the AC-68.3 in-container step outcome, all
container Up conditions, the solanaBilly isolation check, and the extended regression
guard (AC-71.2) status.

Zero firehose — the import smoke imports symbols inside the built image and does not
contact any live data source.

---

## Pre-Deploy Import Smoke (AC-71.1)

The pre-deploy import smoke is exercised via the deploy pipeline's `ci` job, which calls
`uses: ./.github/workflows/ci.yml`. The `ci.yml` test job includes the smoke step
immediately after `Build containers`, before any push:

```
Build containers
  ↓
Pre-deploy built-image import smoke (AC-71.1)   ← runs python3 tools/run_import_smoke.py
  ↓                                                 against the BUILT container
(lint, tests, coverage)
```

The `build-and-push` job in `deploy.yml` has `needs: ci`, so GHCR push cannot proceed
until the smoke passes. Full chain enforced:

```
ci (smoke inside)  →  build-and-push  →  deploy (VPS)
```

### Smoke command (from tools/run_import_smoke.py)

The script reads `ops/import_smoke_symbols.json` and constructs:

```python
import django; django.setup();
from trading.execution_core import ExecutionCore;
from trading.tape_settler import simulate_tape_exit;
from trading.exit_engine import evaluate_exit_rules;
from trading.position_closer import settle_paper_position;
from copytrade.position_opener import open_live_position;
print('AC-71.1 import smoke OK: ...')
```

`django.setup()` is always first — this prevents the `AppRegistryNotReady` class
(sprint-13 root cause: a module defining a Django model at import time without the
app registry initialised) from passing CI-level tests while failing at VPS time.

### Symbol list (ops/import_smoke_symbols.json)

Matches the AC-68.3 in-container check set (five symbols):

| Symbol | Module |
|---|---|
| `ExecutionCore` | `trading.execution_core` |
| `simulate_tape_exit` | `trading.tape_settler` |
| `evaluate_exit_rules` | `trading.exit_engine` |
| `settle_paper_position` | `trading.position_closer` |
| `open_live_position` | `copytrade.position_opener` |

---

## Post-Deploy In-Container Import Step (AC-68.3)

The `Verify shared apparatus imports in-container (AC-68.3)` step in `deploy.yml`
runs after the VPS containers start, SSHing into the VPS and executing:

```shell
docker compose -p solanatrilly ... exec -T web python3 -c "
  import django; django.setup();
  from trading.execution_core import ExecutionCore;
  ...
  print('AC-68.3: shared apparatus imports OK (...)')
"
```

The `django.setup()` prefix (PR #296, commit 35ce277) prevents `AppRegistryNotReady`
on any module that defines a Django model at import time. This step stays GREEN when
the prod image (built and pushed to GHCR in `build-and-push`) carries all five
shared apparatus symbols importable in the live container.

---

## Extended Regression Guard (AC-71.2)

The extended deploy regression guard (`core/tests/test_deploy_regression_guard_ac712.py`)
pins three new invariants that must remain GREEN in CI on every run:

| Invariant | Pins |
|---|---|
| INVARIANT-10 | `Pre-deploy built-image import smoke (AC-71.1)` step in `ci.yml` |
| INVARIANT-11 | AC-68.3 step in `deploy.yml` with `django.setup()` prefix (PR #296) |
| INVARIANT-12 | Both AC-69.3 Live Positions open/closed smoke steps in `deploy.yml` |

The guard fails loudly on any removal (H1 pattern). A green CI run on this branch
confirms all three invariants are in force.

---

## Deploy Pipeline Chain — AC-71.3 Wiring

```
deploy.yml (workflow_dispatch only)
  ├── ci (job)
  │     uses: ./.github/workflows/ci.yml
  │     Steps include:
  │       1. Build containers
  │       2. Pre-deploy built-image import smoke (AC-71.1)  ← SMOKE STEP
  │       3. Lint
  │       4. Run tests with coverage
  │
  ├── build-and-push (job)  [needs: ci]
  │     Pushes web + frontend images to GHCR
  │     Cannot proceed until ci (including smoke) passes
  │
  └── deploy (job)  [needs: build-and-push]
        1. Deploy to VPS staging stack
        2. Smoke-test staging stack (AC-6.4 / AC-12.3)
        3. ...
        N. Verify shared apparatus imports in-container (AC-68.3)  ← AC-68.3 STEP
        N+1. Smoke-test US-69 Live Positions open API (AC-69.3)
        N+2. Smoke-test US-69 Live Positions closed API (AC-69.3)
```

The smoke step at position 2 runs against the **built** container (`docker compose build`
in step 1), not the test venv, not the GHCR image. This surfaces the exact
`AppRegistryNotReady` class pre-VPS rather than after a whole-sprint round-trip.

---

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `workflow_dispatch` on main (HEAD carries US-71) |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |

---

## VPS Verification Conditions — AC-71.3

All conditions must be confirmed from the actual green deploy run log:

| # | Condition | Step / evidence | Status |
|---|---|---|---|
| 1 | `Pre-deploy built-image import smoke (AC-71.1)` step PRESENT in ci job | Step name visible in run log | PENDING |
| 2 | Smoke step exits 0 (all five symbols import with `django.setup()`) | Step conclusion: success | PENDING |
| 3 | `Verify shared apparatus imports in-container (AC-68.3)` step GREEN | Step conclusion: success | PENDING |
| 4 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff | `Smoke-test staging stack` step | PENDING |
| 5 | `copytrade_engine` container Up | `Verify copytrade_engine container Up (AC-59.3)` | PENDING |
| 6 | `listener` container Up | `Verify listener container Up (AC-33.3)` | PENDING |
| 7 | `web` container Up | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 8 | `celery-worker` container Up | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 9 | `frontend` container Up | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 10 | solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |
| 11 | Extended regression guard (AC-71.2) GREEN in CI | CI `test` job — `test_deploy_regression_guard_ac712.py` | PENDING |
| 12 | `GET /api/trading/positions/open/` HTTP 200 | `Smoke-test US-69 Live Positions open API (AC-69.3)` | PENDING |
| 13 | `GET /api/trading/positions/closed/` HTTP 200 | `Smoke-test US-69 Live Positions closed API (AC-69.3)` | PENDING |

---

## Tester Notes

*To be completed by the Tester after the actual green deploy run:*

- The smoke step (`Pre-deploy built-image import smoke (AC-71.1)`) should appear as a
  GREEN step inside the `ci` job of the deploy run. Its step log should end with
  `AC-71.1 import smoke OK: ExecutionCore, simulate_tape_exit, evaluate_exit_rules,
  settle_paper_position, open_live_position`.

- The `Verify shared apparatus imports in-container (AC-68.3)` step should appear as a
  GREEN step inside the `deploy` job. Its log should end with
  `AC-68.3 PASS: shared apparatus imports in-container confirmed`.

- Confirm both conditions from the run log and update the PENDING fields above.
