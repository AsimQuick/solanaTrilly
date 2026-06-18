# ---
# file: ops/tester_confirm_ac533.md
# project: solanatrilly
# purpose: AC-53.3 — Tester confirmation that the VPS conditions are met after the AC-53.2
#          disk-exhaustion root-cause fix. Confirmed from an actual green deploy run.
# story: US-53 AC-53.3
# sprint: sprint-11
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-53.3

## Purpose

AC-53.3 is the Tester-CONFIRM step that validates the four VPS conditions are met
after the AC-53.2 disk-exhaustion root-cause fix is deployed to the VPS via an
actual green deploy run. This record serves as the durable evidence artifact.

## Root Cause (AC-53.1)

| Field | Detail |
|---|---|
| Failing run | 27683660493 |
| Root cause | VPS disk exhaustion (ENOSPC) during `docker compose pull` |
| Mechanism | `/var/lib/containerd` overlayfs partition filled by accumulated image layers from prior sprint-10 deploy runs; the 195 MB Django layer could not be extracted |
| Record | `ops/rca_run_27683660493.md` |

This was a ROOT cause failure, not a symptom. The disk ran out of space, which
prevented `docker compose pull` from extracting new image layers. Any symptom patch
(e.g., retrying the pull without clearing disk space) would fail again on the next
sprint with the same accumulated layer history.

## Root Fix Applied (AC-53.2)

`docker image prune -f` was prepended to the Deploy-to-VPS SSH command in
`.github/workflows/deploy.yml`, BEFORE `docker compose down/pull/up`.

This reclaims dangling (untagged) image layers without interactive confirmation
and without `--all` (tagged images like `:latest` are preserved). The prune runs
unconditionally on every deploy, so the VPS disk stays clean regardless of how
many sprint deploy runs have accumulated.

This is a ROOT fix: it addresses the cause (disk fills up with dangling layers)
rather than a symptom (retrying the pull, increasing timeouts, or patching around
the ENOSPC error). Record: `ops/green_deploy_ac532.md`.

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Conclusion | PENDING |
| Commit | PENDING |
| Branch | main (at HEAD) |
| Timestamp | PENDING |

## VPS Verification Conditions — AC-53.3

All four conditions must be confirmed from the actual green deploy run:

| # | Condition | Status |
|---|---|---|
| 1 | HTTP 200 on port 8002 (`/health/`) with AC-12.3 retry-with-backoff (`SMOKE_MAX_ATTEMPTS=12`, `SMOKE_RETRY_DELAY=5`) | PENDING |
| 2 | `listener` container Up in the solanatrilly stack | PENDING |
| 3 | scorer/celery-worker container Up in the solanatrilly stack | PENDING |
| 4 | solanaBilly UNTOUCHED on port 8001 (every command scoped `-p solanatrilly`) | PENDING |

## Structural Guards in deploy.yml

The following deploy.yml steps enforce these conditions on every deploy run:

| Condition | deploy.yml step |
|---|---|
| HTTP 200 on 8002 with retry-backoff | `Smoke-test staging stack (AC-6.4 / AC-12.3)` |
| listener container Up | `Verify listener container Up (AC-33.3)` |
| celery-worker container Up | `Verify celery-worker container Up (AC-53.3)` |
| solanaBilly UNTOUCHED on 8001 | `Verify solanaBilly isolation (AC-6.5 / AC-12.4)` |

The `Verify celery-worker container Up (AC-53.3)` step was added in this AC. It
SSHs to the VPS and runs:

```
docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml ps
```

Then checks that a line matching `^solanatrilly.celery.worker` contains `running|up`.
All docker compose commands carry `-p solanatrilly` scope.

## Distinction from Symptom Patch

A symptom patch would be one or more of:
- Retrying `docker compose pull` on failure without clearing disk space
- Increasing timeouts or adding sleep before the pull
- Catching the ENOSPC error and continuing anyway
- Manually SSHing to the VPS to clear space (not automated, not durable)

The AC-53.2 fix is none of these. It is a structural change to the deploy pipeline
that prevents disk exhaustion from occurring in the first place on every future deploy,
without operator intervention.

## Verdict

**PENDING — awaiting actual green deploy run on main.**

Once the green run completes and run ID/URL are filled in above, this record will be
updated by the Tester to `status: confirmed` and all four conditions marked PASS.
