# ---
# file: ops/green_deploy_ac532.md
# project: solanatrilly
# purpose: AC-53.2 — Record of the green re-deploy carrying the US-47 ruff-gate change
#          to the VPS after applying the disk-exhaustion root-cause fix
# story: US-53 AC-53.2
# sprint: sprint-11
# status: pending-run
# created-by: dev-team
# last-updated: 2026-06-18
# ---

# Green Deploy Record — AC-53.2

## Root Cause Fixed (from AC-53.1)

**Run that failed:** [27683660493](https://github.com/AsimQuick/solanaTrilly/actions/runs/27683660493)
**Root cause (AC-53.1):** VPS disk space exhaustion in `/var/lib/containerd` overlayfs
storage. The 195 MB Django Python 3.12 site-packages layer (`b338df4614ce`) could
not be extracted during `docker compose pull` — the OS returned ENOSPC
("no space left on device") because accumulated image layers from prior sprint-10
deploy runs had filled the partition.

This failure is **distinct** from:
- The orphaned-container failure (AC-52.3 / run 27673867804) — that fails during `up -d`
- The WS-key defect (US-52) — that would fail only in the smoke-test step, never reached

## Fix Applied

**Change in `.github/workflows/deploy.yml`** — Deploy-to-VPS SSH command:

```diff
  ssh ... "${VPS_USER}@${VPS_HOST}" \
-   'docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml down --remove-orphans; \
+   'docker image prune -f; \
+    docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml down --remove-orphans; \
     docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml pull && \
     docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml up -d --remove-orphans'
```

`docker image prune -f` removes all dangling image layers (unreferenced by any tag)
without interactive confirmation and without `--all` (named tags like `:latest` are
preserved). This reclaims disk space from prior deploy runs before every new pull,
guaranteeing the VPS has space for the layer extraction regardless of deploy history.

## Gate Confirmation

The deploy uses the US-40 unified workflow_call gate:
- `deploy.yml` → `ci` job calls `.github/workflows/ci.yml` via `workflow_call`
- The canonical `test` job (lint + pytest + coverage ≥ 80%) must pass before
  `build-and-push` and `deploy` jobs run
- No inline pytest copy — divergent gate is absent (H1 invariant preserved)

## Steps Confirmed Preserved

| Step | AC Reference | Evidence |
|---|---|---|
| Phase-promoter pre-deploy gate | AC-39.2 | `python3 tools/promote_sprint_phase.py scrum-master/sprint*.json` — present and ordered before Deploy-to-VPS |
| Smoke-test with retry-with-backoff | AC-12.3 | `SMOKE_MAX_ATTEMPTS=12 SMOKE_RETRY_DELAY=5` loop on `/health/` — preserved unchanged |
| Dashboard route HTTP 200 | AC-48.3 | `/dashboard/` curl check with retry — preserved |
| WS endpoint HTTP 101 | AC-48.3 | Python socket WS upgrade check with RFC-6455-valid key (US-52) — preserved |
| frontend + web containers Up | AC-48.3 | `docker compose ps` grep — preserved |
| listener container Up | AC-33.3 | `docker compose ps` grep — preserved |
| solanaBilly isolation check | AC-6.5 | port 8001 curl + solanabilly ps — preserved |

## Green Run Record

GREEN_RUN_ID: PENDING
GREEN_RUN_URL: PENDING
GREEN_RUN_CONCLUSION: PENDING
DEPLOY_TIMESTAMP: PENDING

<!-- The orchestrator fills in the above fields after the actual green deploy run
     on main. The structural fix (docker image prune -f) is committed in this
     branch; the deploy triggers on merge to main via push:[main] in deploy.yml. -->
