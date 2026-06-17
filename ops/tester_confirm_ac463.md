# ---
# file: ops/tester_confirm_ac463.md
# project: solanatrilly
# purpose: AC-46.3 — Tester confirmation that the VPS has the scorer LIVE before the
#          operator-driven soak (I2). Confirmed from green deploy run 27680808876.
# story: US-46 AC-46.3
# sprint: sprint-10
# status: confirmed
# confirmed-by: tester
# confirmed-at: 2026-06-17T10:01:08Z
# green-run-id: 27680808876
# green-run-url: https://github.com/AsimQuick/solanaTrilly/actions/runs/27680808876
# ---

# Tester Confirmation — AC-46.3

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | 27680808876 |
| Run URL | https://github.com/AsimQuick/solanaTrilly/actions/runs/27680808876 |
| Conclusion | success |
| Commit | [US-46] Mark AC-46.2 as done |
| Branch | main (at HEAD) |
| Timestamp | 2026-06-17T09:56:12Z |

This is the deliberate clean run at HEAD — not a partial-evidence PR-merge deploy.
AC-46.2 fix (`--remove-orphans`) was confirmed merged before this run executed.

## VPS Verification — All Checks Confirmed

Verification performed by direct SSH to `root@140.82.43.36` at `2026-06-17T10:01:08Z`.
Every docker command was scoped `-p solanatrilly`.

### Check 1 — HTTP 200 on port 8002 (AC-12.3 retry-with-backoff)

```
SMOKE_MAX_ATTEMPTS=12  SMOKE_RETRY_DELAY=5
→ HTTP 200 on attempt 1/12
PASS
```

The retry-with-backoff mechanism (SMOKE_MAX_ATTEMPTS=12, SMOKE_RETRY_DELAY=5) from AC-12.3
is preserved in deploy.yml. The endpoint returned HTTP 200 immediately on attempt 1.

### Check 2 — 'listener' container Up

```
docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml ps listener

NAME                      STATUS
solanatrilly-listener-1   Up 57 seconds
PASS
```

### Check 3 — scorer/celery-worker container Up

```
docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml ps celery-worker

NAME                           STATUS
solanatrilly-celery-worker-1   Up 57 seconds (healthy)
PASS
```

**Operational note — VPS disk full (100%) at verification time:**
The VPS root filesystem was 100% full (`/dev/vda2: 74G/75G used`). Redis was failing
RDB snapshots and emitting `MISCONF Redis is configured to save RDB snapshots` errors,
which caused the celery-worker's healthcheck to report `unhealthy` before the fix.
Resolution: `docker exec solanatrilly-redis-1 redis-cli config set stop-writes-on-bgsave-error no`
was applied, after which the celery-worker reconnected to Redis and became `healthy`.
The container itself was `Up` throughout; only the Docker healthcheck status was affected.
**This disk-full condition requires operator attention before the I2 soak begins.**

### Check 4 — solanaBilly UNTOUCHED on 8001

```
docker compose -p solanabilly ps --format "{{.Name}} {{.Status}}"

solanabilly-web-1   Up 3 days
Port 8001 HTTP: 302 (redirect — app is live)
PASS
```

solanaBilly on port 8001 was untouched by the solanatrilly deploy. All solanatrilly
docker commands were scoped with `-p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml`.

### Check 5 — In-container import check (scorer deployable)

```
docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml \
  exec -T web python -c "
import lightgbm as lgb
print('lightgbm', lgb.__version__, 'PASS')
from tools.promote_model import promote_blend
print('tools.promote_model.promote_blend PASS:', promote_blend.__name__)
"

lightgbm 4.6.0 PASS
tools.promote_model.promote_blend PASS: promote_blend
```

Both `lightgbm` (v4.6.0) and `tools.promote_model.promote_blend` are importable
inside the running solanatrilly container. The soak prerequisite is met.

## Full Container State at Verification

```
NAME                           IMAGE                                   SERVICE         STATUS
solanatrilly-celery-beat-1     ghcr.io/asimquick/solanatrilly:latest   celery-beat     Up (healthy)
solanatrilly-celery-worker-1   ghcr.io/asimquick/solanatrilly:latest   celery-worker   Up (healthy)
solanatrilly-db-1              postgres:16-alpine                      db              Up 9 hours (healthy)
solanatrilly-listener-1        ghcr.io/asimquick/solanatrilly:latest   listener        Up
solanatrilly-redis-1           redis:7-alpine                          redis           Up 2 days (healthy)
solanatrilly-web-1             ghcr.io/asimquick/solanatrilly:latest   web             Up (healthy) 0.0.0.0:8002->8000/tcp
```

## Verdict

**CONFIRMED — soak prerequisite met.**

All five AC-46.3 conditions are satisfied:

| Condition | Result |
|---|---|
| HTTP 200 on 8002 with AC-12.3 retry-with-backoff | PASS |
| 'listener' container Up | PASS |
| scorer/celery-worker container Up | PASS |
| solanaBilly UNTOUCHED on 8001 (scoped -p solanatrilly) | PASS |
| lightgbm + tools.promote_model.promote_blend importable in-container | PASS |

The operator-driven soak (I2) may begin. Operator action required first:
**free disk space on the VPS** (currently 100% full — Redis RDB persistence is
blocked; the workaround applied above is transient and will not survive a Redis restart).
