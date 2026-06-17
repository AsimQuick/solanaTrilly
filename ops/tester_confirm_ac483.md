# ---
# file: ops/tester_confirm_ac483.md
# project: solanatrilly
# purpose: AC-48.3 — Tester confirmation that the dashboard foundation is deployed to the VPS
#          solanatrilly staging stack and smoke-tested. Confirmed from an actual green deploy run.
# story: US-48 AC-48.3
# sprint: sprint-10
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-48.3

## Green Deploy Run

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Conclusion | PENDING |
| Commit | PENDING |
| Branch | main (at HEAD) |
| Timestamp | PENDING |

## Verification Conditions (AC-48.3)

All five conditions must be confirmed from the actual green deploy run:

| Condition | Result |
|---|---|
| HTTP 200 on port 8002 (`/health/`) | PENDING |
| Dashboard route `/dashboard/` returns HTTP 200 | PENDING |
| WS endpoint `ws/tape/<mint>/` accepts HTTP 101 upgrade | PENDING |
| `solanatrilly-web-1` container Up | PENDING |
| `solanatrilly-frontend-1` container Up | PENDING |
| solanaBilly untouched on port 8001 | PENDING |
| All docker commands scoped `-p solanatrilly` | PENDING |

## Notes

PENDING — to be completed by the Tester/orchestrator after the first actual green deploy
run that includes the AC-48.3 changes (dashboard URL, frontend image build, WS check).
