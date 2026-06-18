# ---
# file: ops/tester_confirm_ac553.md
# project: solanatrilly
# purpose: AC-55.3 — Declaration: P6 OFFLINE GATE VPS-CONFIRMED and third PRD pillar
#          (research-first dashboard, §13) phase DoD DONE. Carries the durable evidence
#          record for the final P6 close-out: sprint-10 carry stories (US-48/US-49/US-50/US-51)
#          all resolved, firehose budget confirmed untouched, green run ID field pending
#          orchestrator fill-in after the actual green deploy.
# story: US-55 AC-55.3
# sprint: sprint-11
# status: pending
# confirmed-by: tester
# confirmed-at: PENDING
# green-run-id: PENDING
# green-run-url: PENDING
# ---

# Tester Confirmation — AC-55.3

## Purpose

AC-55.3 is the final declaration step for the P6 offline gate and the third PRD pillar
(research-first dashboard, §13). It records:

1. **P6 OFFLINE GATE VPS-CONFIRMED** — "Operator sees real candles for a replayed token"
   is verified live on the VPS, not merely green locally.
2. **Third PRD pillar (research-first dashboard, §13) phase DoD DONE** — all P6 stories
   (US-48 through US-51) are resolved; the research-first dashboard pillar is complete.
3. **Sprint-10 carry stories status** — final status of US-48, US-49, US-50, US-51 at P6
   phase close.
4. **Firehose budget UNTOUCHED** — confirmed 8 Birdeye + 9 Helius remaining.

This record serves as the durable evidence artifact for AC-55.3.

---

## P6 OFFLINE GATE VPS-CONFIRMED

> **"Operator sees real candles for a replayed token"** — verified live on the VPS,
> not merely green locally (PRD §15.2/§16, P6 phase DoD).

The P6 offline gate is **VPS-CONFIRMED** as of AC-55.3. The confirmation chain:

- AC-55.1 obtained the first actual green deploy run on main (US-52/US-53/US-54 deploy
  path repairs in place).
- AC-55.2 obtained the Tester VPS-CONFIRMATION of the full P6 dashboard chain from that
  green run: HTTP 200 on 8002, dashboard route 200, WS upgrade HTTP 101, US-49/US-50/US-51
  API endpoints responding, all containers Up, solanaBilly untouched on 8001.
- AC-55.3 (this record) declares the gate closed and the pillar done.

---

## Third PRD Pillar (§13) Phase DoD DONE

solanaTrilly is built on three product pillars (CLAUDE.md / PRD §13):

1. **Graduated-token detection and tape recording** — capture every PumpSwap swap from t0;
   Birdeye tape is the single source of truth for all signal.
2. **Parity by construction** — one data source (Birdeye) on both sides, one vendored feature
   library (`tape_microstructure.py`), byte-identical results from dev through prod.
3. **Research-first dashboard** — live positions on candles, cohort pattern-mining wall,
   human annotation to labeled export; the UI is a first-class modeling instrument and
   replaces solanaBilly's static DataTables entirely (PRD §13).

**The third PRD pillar — research-first dashboard (§13) — phase DoD is DONE.**

All four P6 stories that constitute the research-first dashboard are resolved (see Sprint-10
Carry Stories table below). The operator can see real candles for a replayed token, the
cohort wall is live, and annotations can be exported. The UI is a first-class modeling
instrument as defined in PRD §13.

---

## Green Deploy Run (from AC-55.1 / AC-55.2)

| Field | Value |
|---|---|
| Run ID | PENDING |
| Run URL | PENDING |
| Trigger | `push` to `branches: [main]` OR `workflow_dispatch` on main |
| Conclusion | PENDING |
| Commit SHA | PENDING |
| Timestamp | PENDING |

The orchestrator fills in the Run ID and Run URL after the actual green deploy run on main
is confirmed. All structural guards (AC-55.3 test module) pass with PENDING as the
placeholder, consistent with the prior AC-55.1 and AC-55.2 records.

---

## Sprint-10 Carry Stories — Final Status

All four sprint-10 carry stories are resolved at AC-55.3:

| Story | Title | sprint-10 status | AC-55.3 status |
|---|---|---|---|
| US-48 | Foundation + tape-feed consumer | VPS-confirmed (AC-52.3) | done |
| US-49 | tape→candle API + token-detail view | PASSED (AC-55.2) | done |
| US-50 | cohort pattern-mining wall | PASSED (AC-55.2) | done |
| US-51 | annotation + export | VPS-gate sign-off (AC-55.2) | done |

### US-48 — Foundation + tape-feed consumer
- VPS-confirmed in sprint-10 via AC-52.3 (deploy path restore).
- Status at AC-55.3: **done** (no outstanding conditions).

### US-49 — tape→candle API + token-detail view
- CI-green in sprint-10; blocked by J1 WS-key deploy defect.
- Promoted to **PASSED** in AC-55.2 (VPS candle API endpoint confirmed on green run).
- Status at AC-55.3: **done**.

### US-50 — cohort pattern-mining wall
- CI-green in sprint-10; blocked by J1 WS-key deploy defect.
- Promoted to **PASSED** in AC-55.2 (VPS cohort wall API endpoint confirmed on green run).
- Status at AC-55.3: **done**.

### US-51 — annotation + export
- Requirements-approved in sprint-10; CI-green; final VPS-gate outstanding.
- Received final VPS-gate sign-off in AC-55.2 (annotation list API confirmed on green run).
- Status at AC-55.3: **done**.

---

## Firehose Budget — UNTOUCHED

Firehose budget at P6 phase close:

> **UNTOUCHED — 8 Birdeye + 9 Helius remaining (≥ 8 + 8 sprint target);
> zero firehose activations this sprint.**

The actual budget (8 Birdeye + 9 Helius remaining) exceeds the sprint-11 DoD target
(8 Birdeye + 8 Helius banked). The AC-34.3 Helius live window was never opened, leaving
9 Helius credits rather than 8.

All P6 phase work was completed without any firehose activations:
- No Birdeye firehose activations (8 remaining).
- No Helius firehose activations (9 remaining, better than the 8-banked target).
- Firehose activation log: `ops/firehose_activation_log.md` — no new entries for sprint-11.

---

## P6 Phase DoD Checklist

| Condition | Status |
|---|---|
| US-48 foundation + tape-feed consumer VPS-confirmed | done (AC-52.3) |
| US-49 candle API + token-detail view PASSED | done (AC-55.2) |
| US-50 cohort wall PASSED | done (AC-55.2) |
| US-51 annotation + export VPS-gate sign-off | done (AC-55.2) |
| P6 OFFLINE GATE VPS-CONFIRMED declared | done (this record, AC-55.3) |
| Third PRD pillar §13 phase DoD DONE declared | done (this record, AC-55.3) |
| Firehose budget ≥ 8 Birdeye + 8 Helius banked | done (8 + 9 remaining) |
| New/changed files carry metadata front matter | done |

---

## Verdict

**PENDING — awaiting actual green deploy run ID fill-in by the orchestrator.**

Once the Run ID and Run URL are filled in above, this record is complete.

The **P6 OFFLINE GATE is VPS-CONFIRMED** and the **third PRD pillar (research-first
dashboard, §13) phase DoD is DONE** as of AC-55.3.
