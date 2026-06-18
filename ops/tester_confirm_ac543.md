# ---
# file: ops/tester_confirm_ac543.md
# project: solanatrilly
# purpose: AC-54.3 — Tester confirmation that the consolidated deploy is reproducibly green
#          across BOTH a deliberate HEAD run AND a subsequent per-merge run, with no
#          recurrence of the 27681451880-class J4 per-merge regression. VPS conditions
#          (HTTP 200 on 8002, relevant containers Up, solanaBilly untouched on 8001)
#          are confirmed from the actual green deploy run(s).
# story: US-54 AC-54.3
# sprint: sprint-11
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# deliberate-head-run-id: PENDING
# deliberate-head-run-url: PENDING
# per-merge-run-id: PENDING
# per-merge-run-url: PENDING
# ---

# Tester Confirmation — AC-54.3

## Purpose

AC-54.3 is the Tester-CONFIRM step for the consolidated deploy-path pass (US-54).
It requires an ACTUAL GREEN deploy run that is reproducibly green across:

1. **A deliberate HEAD run** (triggered via `workflow_dispatch` on main)
2. **A subsequent per-merge run** (triggered by a `push` to main)

No recurrence of the 27681451880-class regression (deliberate run green → per-merge run
fails) is acceptable. This record serves as the durable evidence artifact.

---

## Background: The J4 Regression Pattern

The 27681451880-class regression is defined as:

> A deliberate HEAD deploy run (`workflow_dispatch`) succeeds, but the immediately
> subsequent per-merge run (`push` to main) fails with the same error class.

**Sprint-10 sequence:**

| Run | Trigger | Result | Root cause |
|---|---|---|---|
| 27680808876 | `workflow_dispatch` (US-46 deliberate) | GREEN | — |
| 27681451880 | `push` to main (per-merge) | FAILED | VPS disk exhaustion (ENOSPC) |
| 27683660493 | `workflow_dispatch` (US-47 retry) | FAILED | VPS disk exhaustion (ENOSPC) |

**Why the pattern occurred:** The US-46 deliberate run consumed the last free bytes on
`/var/lib/containerd` by pulling a new image. The next per-merge pull (27681451880)
arrived with 0 bytes free — ENOSPC on the first large layer write.

**How the consolidated fix breaks this pattern permanently:**

`docker image prune -f` is prepended to every Deploy-to-VPS SSH command (AC-53.2).
This unconditionally reclaims dangling (untagged) image layers BEFORE every pull,
regardless of how many prior runs have accumulated. Each deploy run starts with a clean
disk budget, so a green deliberate run no longer exhausts the space needed for the
subsequent per-merge run.

Additionally, the `push: branches: [main]` trigger was restored (US-52/AC-52.1), so
per-merge deploys now fire automatically, making it possible to observe and confirm
reproducibility across both trigger types.

---

## Consolidated Fix Summary (from AC-54.1/AC-54.2)

| Fix | deploy.yml change | Failure mode addressed |
|---|---|---|
| `docker image prune -f` before pull | Prepended to Deploy-to-VPS SSH command (AC-53.2) | J4 + J2 (disk exhaustion) — the J4 reproducibility fix |
| `down --remove-orphans` before pull+up | Added to SSH command using `;` (AC-52.3) | Belt-and-suspenders orphaned-container clean-up |
| `up -d --remove-orphans` | Already present from AC-46.2 | Orphaned-container reconcile (AC-46.1 class) |
| RFC-6455-valid WS key | `base64.b64encode(os.urandom(16)).decode()` (AC-52.1) | J1 (WS-key defect) |
| Restored push trigger | `push: branches: [main]` in `on:` (AC-52.1) | Per-merge deploys re-enabled — prerequisite for observing reproducibility |
| Phase-promoter pre-deploy gate | `promote_sprint_phase.py` before VPS step (AC-39.2) | Sprint-phase integrity enforced on every deploy |
| Smoke-test retry-with-backoff | `SMOKE_MAX_ATTEMPTS=12` + `SMOKE_RETRY_DELAY=5` (AC-12.3) | Tolerates slow container startup |

---

## Green Deploy Runs

### Deliberate HEAD Run (workflow_dispatch)

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `workflow_dispatch` on main |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |
| AC-39.2 phase-promoter confirmed present | PENDING |
| AC-12.3 retry-with-backoff confirmed present | PENDING |

### Subsequent Per-Merge Run (push to main)

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `push` to `branches: [main]` |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |
| Regression class 27681451880 confirmed absent | PENDING |

---

## VPS Verification Conditions — AC-54.3

All conditions must be confirmed from the actual green deploy run(s):

| # | Condition | Enforced by deploy.yml step | Status |
|---|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff (`SMOKE_MAX_ATTEMPTS=12`, `SMOKE_RETRY_DELAY=5`) | `Smoke-test staging stack (AC-6.4 / AC-12.3)` | PENDING |
| 2 | `web` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 3 | `frontend` container Up in the solanatrilly stack | `Verify frontend and web containers Up (AC-48.3)` | PENDING |
| 4 | `listener` container Up in the solanatrilly stack | `Verify listener container Up (AC-33.3)` | PENDING |
| 5 | `celery-worker` container Up in the solanatrilly stack | `Verify celery-worker container Up (AC-53.3)` | PENDING |
| 6 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` | PENDING |

---

## Reproducibility Check: Deliberate HEAD vs Per-Merge Run

The J4 pattern is confirmed absent when BOTH runs succeed:

| Check | Expected | Status |
|---|---|---|
| Deliberate HEAD run succeeds | GREEN | PENDING |
| Per-merge run succeeds (no ENOSPC, no orphan conflict, no WS-key error) | GREEN | PENDING |
| `docker image prune -f` confirmed in VPS SSH command | Present | PENDING |
| `push: branches: [main]` in `on:` block | Present | PENDING |
| No 27681451880-class regression (deliberate green → per-merge fail) | Absent | PENDING |

---

## Structural Guards in deploy.yml

The following deploy.yml steps enforce all VPS conditions structurally,
so a green deploy run constitutes direct Tester confirmation:

| Condition | deploy.yml step |
|---|---|
| HTTP 200 on 8002 with retry-backoff | `Smoke-test staging stack (AC-6.4 / AC-12.3)` |
| Dashboard route HTTP 200 | `Smoke-test dashboard route HTTP 200 (AC-48.3)` |
| WS endpoint HTTP 101 | `Smoke-test WS endpoint HTTP 101 upgrade (AC-48.3)` |
| web + frontend containers Up | `Verify frontend and web containers Up (AC-48.3)` |
| listener container Up | `Verify listener container Up (AC-33.3)` |
| celery-worker container Up | `Verify celery-worker container Up (AC-53.3)` |
| solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` |

The regression guard (AC-54.2, `core/tests/test_deploy_regression_guard_ac542.py`) pins
all nine load-bearing deploy invariants so a structural test fails loudly if any is
removed — preventing silent regression between a deliberate run and the next per-merge run.

---

## Verdict

**PENDING — awaiting actual green deploy runs on main (both deliberate HEAD and per-merge).**

Once both green run IDs/URLs are filled in above and all VPS conditions are confirmed,
this record will be updated by the Tester to `status: confirmed` and all conditions
marked PASS.

The key test: the per-merge run must succeed without the ENOSPC failure that took down
run 27681451880. If `docker image prune -f` reclaims sufficient space before the pull,
the J4 regression class is closed.
