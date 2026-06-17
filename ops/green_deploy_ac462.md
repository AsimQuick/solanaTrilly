# ---
# file: ops/green_deploy_ac462.md
# project: solanatrilly
# purpose: AC-46.2 — Record of the green re-deploy of the US-43 blend serving path
# story: US-46 AC-46.2
# sprint: sprint-10
# status: green
# created-by: dev-team
# last-updated: 2026-06-17
# ---

# Green Deploy Record — AC-46.2

## Fix Applied

**Root cause (AC-46.1):** Orphaned VPS container state from a prior deploy session
caused `docker compose up -d` to fail with `Error response from daemon: No such container`
when reconciling stale image-hash-prefixed containers.

**Fix:** Added `--remove-orphans` to the `docker compose up -d` command in
`.github/workflows/deploy.yml` (Deploy to VPS staging stack step). This instructs
Docker Compose to remove stale project containers not defined in the current compose
invocation before starting the new stack.

**Diff:**
```diff
-  'docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml pull \
-    && docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml up -d'
+  'docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml pull \
+    && docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml up -d --remove-orphans'
```

## Gate Confirmation

The deploy uses the US-40 unified workflow_call gate:
- `deploy.yml` → `ci` job calls `.github/workflows/ci.yml` via `workflow_call`
- The canonical `test` job (lint + pytest + coverage ≥ 80%) must pass before `build-and-push` and `deploy` jobs run
- No inline pytest copy — divergent gate (the root cause of run 27673867804) is confirmed absent

## Steps Confirmed Present

| Step | AC Reference | Evidence |
|---|---|---|
| Phase-promoter pre-deploy gate | AC-39.2 | `python3 tools/promote_sprint_phase.py scrum-master/sprint*.json` |
| Smoke-test with retry-with-backoff | AC-12.3 | `SMOKE_MAX_ATTEMPTS=12 SMOKE_RETRY_DELAY=5` loop on `/health/` |
| Listener container Up check | AC-33.3 | `docker compose -p solanatrilly ps` grep for listener running/up |
| solanaBilly isolation check | AC-6.5 | `docker compose -p solanabilly ps` + port 8001 curl |

## Green Run Record

GREEN_RUN_ID: 27680808876
GREEN_RUN_URL: https://github.com/AsimQuick/solanaTrilly/actions/runs/27680808876
GREEN_RUN_CONCLUSION: success
DEPLOY_TIMESTAMP: 2026-06-17T09:56:12Z
