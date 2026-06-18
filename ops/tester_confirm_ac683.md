# ---
# file: ops/tester_confirm_ac683.md
# project: solanatrilly
# purpose: AC-68.3 — Tester confirmation that a green deploy run on 'main' carries the
#          P8 shared execution apparatus (US-64/US-65/US-66/US-67/US-68) to the VPS
#          solanatrilly staging stack: shared apparatus imports in-container,
#          copytrade_engine + listener/web/celery-worker/frontend Up, solanaBilly UNTOUCHED
#          on 8001, nine-invariant deploy guard (AC-54.2) green.
# story: US-68 AC-68.3
# sprint: sprint-13
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-68.3

## Purpose

AC-68.3 is the P8 shared execution apparatus CLOSEOUT deploy step. After wiring both
pipelines' observe/paper paths through the shared apparatus (AC-68.1) and wiring
copy-trade's Live toggle to the capital-OFF shared execution path (AC-68.2), this step
obtains ONE ACTUAL GREEN deploy run on 'main' at HEAD that carries the full P8 execution
chassis to the VPS solanatrilly staging stack.

This record is the durable evidence artifact for AC-68.3. It documents the green deploy
run ID, the shared apparatus in-container import confirmation, all five container Up
conditions, the solanaBilly isolation check, and the nine-invariant guard (AC-54.2) status.

---

## Shared Apparatus (P8 Execution Chassis)

The following US-64/US-65/US-66/US-67/US-68 modules comprise the shared apparatus
verified by the AC-68.3 in-container import step:

| Symbol | Module | Role |
|---|---|---|
| `ExecutionCore` | `trading.execution_core` | Clock-injected buy/sell boundary (capital-OFF/Cutover-gated) |
| `simulate_tape_exit` | `trading.tape_settler` | Sole paper/observe tape settler (§10.2 verbatim port) |
| `evaluate_exit_rules` | `trading.exit_engine` | §10.1 nine-rule priority ladder (RUG_PULL → STALE) |
| `settle_paper_position` | `trading.position_closer` | Bridges settler result to shared Position model |
| `open_live_position` | `copytrade.position_opener` | Copy-trade Live toggle wired to shared execution path |

All five symbols must import in the live web container with no `ImportError`. A failing
import indicates the shared execution path is broken in-container — not merely in CI.

---

## In-Container Import Check

The `Verify shared apparatus imports in-container (AC-68.3)` deploy step runs the
following import check via `docker compose exec -T web python -c "..."` on the VPS:

```python
from trading.execution_core import ExecutionCore
from trading.tape_settler import simulate_tape_exit
from trading.exit_engine import evaluate_exit_rules
from trading.position_closer import settle_paper_position
from copytrade.position_opener import open_live_position
print("AC-68.3: shared apparatus imports OK (...)")
```

A green deploy run confirms all five symbols import successfully in the live container.

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
| Shared apparatus in-container import check | PENDING |
| Nine-invariant guard (AC-54.2) green in CI | PENDING |

---

## VPS Verification Conditions — AC-68.3

All conditions must be confirmed from the actual green deploy run:

| # | Condition | Enforced by deploy.yml step | Status |
|---|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff | `Smoke-test staging stack (AC-6.4 / AC-12.3)` | PENDING |
| 2 | Shared apparatus imports in-container: `ExecutionCore`, `simulate_tape_exit`, `evaluate_exit_rules`, `settle_paper_position`, `open_live_position` | `Verify shared apparatus imports in-container (AC-68.3)` | PENDING |
| 3 | `copytrade_engine` container Up in the solanatrilly stack | `Verify copytrade_engine container Up (AC-59.3)` | PENDING |
| 4 | `listener` container Up in the solanatrilly stack | `Verify listener container Up (AC-33.3)` | PENDING |
| 5 | `web` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 6 | `celery-worker` container Up in the solanatrilly stack | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 7 | `frontend` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 8 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |
| 9 | Nine-invariant deploy guard (AC-54.2) green in CI | `test` job in ci.yml → `test_deploy_regression_guard_ac542.py` | PENDING |

---

## Safety Gate Preserved

AC-68.3 is OBSERVE/PAPER-FIRST — the shared apparatus is deployed with `trading_enabled`
DEFAULT False (no real orders, no capital). The `open_live_position` path is wired but
capital-gated:

- `trading_enabled=False` (default) — live orders never sent this sprint
- Cutover-gated: capital activation is an operator-driven step (PRD §16)
- HELIUS wallet-subscription activation: PLANNED in `ops/firehose_activation_log.md` (not spent)
- Zero firehose: 8 Birdeye + 8 Helius remain banked

---

## Structural Guards in deploy.yml

| Condition | deploy.yml step |
|---|---|
| HTTP 200 on 8002 with retry-backoff (AC-12.3) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` |
| Shared apparatus imports in-container | `Verify shared apparatus imports in-container (AC-68.3)` |
| copytrade_engine container Up | `Verify copytrade_engine container Up (AC-59.3)` |
| listener container Up | `Verify listener container Up (AC-33.3)` |
| web + frontend containers Up | `Verify frontend and web containers Up (AC-48.3)` |
| celery-worker container Up | `Verify celery-worker container Up (AC-53.3)` |
| solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` |
| Nine-invariant guard (AC-54.2) gates CI | `core/tests/test_deploy_regression_guard_ac542.py` in CI test job |

---

## Verdict

**PENDING — awaiting actual green deploy run on main.**

Once the green run ID/URL is filled in above and all nine VPS conditions are confirmed,
this record will be updated by the Tester to `status: confirmed` and all conditions
marked PASS.

The green run must carry the full P8 shared execution apparatus (US-64 Position model +
trading config, US-65 PumpSwap ix + AMM math, US-66 exit engine + tape settler, US-67
replay harness, US-68 both-pipeline wiring + Live toggle) with:
- shared apparatus imports in-container confirmed (condition 2)
- all five containers Up (conditions 3-7)
- solanaBilly UNTOUCHED on 8001 (condition 8)
- nine-invariant guard (AC-54.2) green (condition 9)
