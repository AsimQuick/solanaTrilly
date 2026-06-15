<!--
file: retrospective.md
purpose: Sprint retrospectives for solanaTrilly — what went well, what didn't, action items
owner: tester (quality entries) + scrum facilitator (summary entries)
last-updated: 2026-06-15
note: Created during the sprint-1 review. Its prior absence was itself a DoD gap (see Sprint-1).
-->

# solanaTrilly — Sprint Retrospectives

---

## Sprint-1 — P0 Scaffold & Foundation

**Reviewed:** 2026-06-15 | **Phase at review:** review
**Committed scope (source of truth `sprint1.json`):** 1 story — US-1
**Outcome:** US-1 — all 5 ACs implemented, CI-green, and Tester-**approved**; story-level DoD **BLOCKED** (2 items). Sprint goal **partially met**.

### Sprint goal vs. delivered
The stated sprint goal (PRD §16 / P0) was the *full* foundation: containerized Django 5 + DRF + Channels + Celery stack, the `DataSource`/virtual-clock seam, hardened CI (H1 pinned actions, H2 task-manifest test, H3 `json_safe` encoder), an automated CD pipeline deploying a hello-world to the isolated VPS staging stack, and the firehose ledger seeded.

**Delivered:** only the containerized service topology (US-1). The board lists US-2…US-7 as the P0 plan, but **only US-1 was pulled into and executed in sprint-1** — `sprint1.json` and `project-state.json` both record a 1-story sprint. The clock seam (US-2), CI hardening (US-3/4/5), CD pipeline (US-6), and firehose ledger (US-7) were **not started**.

### Tester feedback (verbatim source — `sprint1.json` `tester_notes`, 2026-06-15)
> All 5 ACs individually **APPROVED** — every PR (PR#1–PR#5) merged with CI 'test' job SUCCESS on both feature branch and main (verified via `gh pr list --state merged`). Dev-reported coverage is 100% across all ACs, exceeding the ≥80% DoD threshold; CI green implies coverage gate enforced. No critical defects observed.
>
> **BLOCKERS preventing story-level DoD sign-off:**
> 1. **VPS deployment NOT done** — `deploy_summary` records *"Deploy ERROR — Failed to trigger workflow: HTTP 404: Not Found"* for `deploy.yml`. The CD pipeline (GitHub Actions → GHCR → VPS) does not exist in the repository. DoD requires *"Merged + deployed to VPS solanatrilly staging stack (-p solanatrilly) and smoke-tested there."*
> 2. **`retrospective.md` does NOT exist** — DoD requires *"retrospective.md updated."*
>
> Story remains BLOCKED until (a) `deploy.yml` is authored and successfully deploys to VPS port 8002 with `-p solanatrilly` scope, VPS smoke-test passes, and (b) `retrospective.md` is written and committed.

### What went well 👍
- **AC-level execution was clean.** All 5 ACs landed as separate PRs (#1–#5), each gated by a green CI `test` job on both the feature branch and `main`. No critical defects.
- **Coverage discipline.** Dev-reported 100% coverage against an ≥80% DoD gate; the gate is real (`ci.yml` runs `pytest --cov-fail-under=80`).
- **Topology is genuinely complete.** All 5 services (web/Daphne ASGI, db/postgres:16, redis, celery-worker, celery-beat) are containerized, wired over Docker network hostnames, healthchecked, and exercised by unit tests (compose topology, requirements pins, websocket echo, migrate/healthy). Docker Rules upheld — no host installs.
- **Defect loop worked.** AC-1.5's two CI failures were caught, analyzed by the Tester, and fixed by Dev within 2 iterations before merge — the quality gate did its job.
- **Pre-work unblocking paid off.** All four `po-requests.md` operator blockers (GitHub remote, default branch, GHCR, VPS SSH secrets) were resolved before development, so US-1 never stalled on inputs.

### What didn't go well 👎
- **DoD requires CD, but CD wasn't in the sprint.** The Sprint DoD mandates "deployed to VPS staging + smoke-tested," yet the pipeline that does this is US-6 — a story that was never pulled in. US-1 was therefore **structurally un-completable**: a topology story is blocked by a DoD clause it has no power to satisfy. `deploy.yml` doesn't exist, so the deploy trigger 404'd.
- **`retrospective.md` was a DoD item that nobody created** until this review. The DoD listed it; no agent owned producing it during the sprint.
- **Scope vs. plan mismatch.** The board advertises a 7-story P0; the sprint executed 1. "VPS presence from P0 / hello-world deployed day one" (a hard CLAUDE.md commitment) was **not achieved** — there is no deployed staging stack on port 8002.
- **Stale status surfaces.** `sprint1.json.phase`, `sprint1.md` ("Phase: planning"), and the board all still read `planning` while `project-state.json` says `review`. Auto-generated `sprint1.md` shows "1/1 stories" with no hint of the 7-story plan — easy to mistake for full P0 completion.
- **Story state is internally inconsistent.** `status: done` while `dev_status: in-progress` and `tester_status: blocked` in the same record. "Done" should not coexist with "blocked."

### Action items → sprint-2
| # | Action | Owner | Rationale |
|---|--------|-------|-----------|
| A1 | Author `deploy.yml` (GitHub Actions → GHCR → VPS) and deploy hello-world to the isolated staging stack (`-p solanatrilly`, port **8002**); smoke-test there. This is **US-6** — pull it into sprint-2 as the top priority. | Dev | Unblocks US-1's DoD; satisfies the "VPS from P0" mandate that P0 missed. |
| A2 | Either (a) deliver CD **inside** every story's sprint, or (b) **decouple** the per-story DoD from the deploy clause and gate deploy at the **sprint** boundary. Don't let a topology story carry a CD blocker. | PO | Removes the structural un-completability that blocked US-1. |
| A3 | Make `retrospective.md` a **named deliverable with an owner** each sprint (not just a DoD checkbox). | Tester / facilitator | The doc existed nowhere until review — DoD items need an owner, not just a line. |
| A4 | Reconcile the board with the source of truth: either commit US-2…US-7 into sprint-2 explicitly, or relabel the board table as "P0 backlog" so "1/1" can't be read as "P0 complete." | PO | Closes the 7-planned-vs-1-executed gap. |
| A5 | Normalize stale phase/status fields (`sprint1.json`, `sprint1.md`, board → `review`/`done`); forbid `status: done` while `tester_status: blocked`. | PO / Project Lead | Prevents misreading sprint state. |
| A6 | Carry forward the un-started P0 stories — US-2 (`DataSource`/virtual-clock seam), US-3/4/5 (H1 SHA-pinned actions, H2 task-manifest test, H3 `json_safe` encoder), US-7 (firehose ledger). CI still uses `actions/checkout@v4` (a tag, not SHA-pinned), so H1 is outstanding. | PO / Dev | These are the rest of the P0 foundation later phases depend on. |

### Metrics (from `project-state.json`)
- **Cycles:** 9 | **PRs merged:** 5 (PR#1–#5) | **CI failures:** 2 (both AC-1.5, fixed in 2 iterations)
- **Token spend:** 179,050 total ≈ **$9.78** — PO 32,503 ($2.70) · Dev 104,285 ($5.40) · Tester 42,262 ($1.68) · Project Lead 0
- **Coverage:** 100% reported vs. ≥80% gate (enforced in CI)
- **Stories:** 1 committed / 1 ACs-complete / **0 fully DoD-Done** (US-1 blocked) | **0 deployed to VPS**

---

## Sprint-2 — Complete the P0 Foundation

**Reviewed:** 2026-06-15 | **Phase at review:** review
**Committed scope (source of truth `sprint2.json`):** 6 stories — US-2…US-7 (22 ACs)
**Outcome:** 5 of 6 stories fully Tester-**approved** and AC-complete (US-2, US-3, US-4, US-5, US-7); **US-6 FAILED** (deploy ACs 6.3–6.5). **19/22 ACs approved**; all merged PRs CI-green. Sprint goal **substantially met** but **P0 not exited** — the CD deploy never completes, so nothing is live on the VPS. Sprint DoD **deploy clause not met** (gated at the sprint boundary per A2).

### Sprint goal vs. delivered
The goal was to finish the P0 foundation: the `DataSource`/virtual-clock seam (US-2), CI hardening H1/H2/H3 (US-3/4/5), the CD pipeline + hello-world live on the **isolated** VPS staging stack (US-6 — which also retroactively closes US-1's deploy-gated DoD), and the firehose ledger (US-7) — exiting P0 with a *tested, deployed, drift-resistant* base.

**Delivered:** everything except the deploy. US-2/3/4/5/7 are done, CI-green, and Tester-approved. US-6's artifacts exist and are structurally correct (deploy.yml SHA-pinned, GHCR push; `docker-compose.staging.yml` with `-p solanatrilly` / port 8002 / distinct volumes + network; smoke-test + isolation steps), but the workflow **fails at the SCP step** because `/root/solanatrilly/` does not exist on the VPS — so the image is never pulled, the stack never starts, and the smoke-test (8002) and isolation check (8001) never run. The end-to-end path is unverified; **"VPS presence from P0" remains unmet for a second consecutive sprint.**

### Tester feedback (verbatim source — `sprint2.json` `tester_sprint_summary`, 2026-06-15)
> Final quality review 2026-06-15. CI gate: all 6 stories pass (test=pass on all 27 merged PRs). DoD status: US-2 APPROVED (lint fixes applied pre-merge, all 4 ACs green), US-3 APPROVED (H1 invariants self-tested), US-4 APPROVED (bidirectional manifest equality verified), US-5 APPROVED (JsonSafeEncoder with guard test), US-7 APPROVED (firehose ledger seeded). US-6 FAILED: deploy workflow fails from AC-6.3 onward — /root/solanatrilly/ directory missing on VPS causes SCP to error; smoke test (AC-6.4) and solanaBilly isolation check (AC-6.5) never execute. Sprint DoD deploy clause not met. Open items: (1) create /root/solanatrilly/ on VPS and re-trigger deploy, or add mkdir -p to deploy script; (2) update retrospective.md for sprint-2 (DoD item, named owner: Tester).

### What went well 👍
- **6× the throughput of sprint-1, all behind a green gate.** 22 ACs across 6 stories, every merged PR CI-green on the hardened `test` job — vs. 1 story in sprint-1. The AC-as-PR cadence held at scale.
- **The P0 hardening trio (H1/H2/H3) is done and self-policing.** CI is SHA-pinned, runner-frozen (`ubuntu-24.04`), and single-workflow, with a self-test (US-3 AC-3.4) that fails on any future unpinned `uses:` or `*-latest` runner; the task-manifest test (US-4) catches both the #404 removed/renamed-task mode and the silent-add mode; the `JsonSafeEncoder` guard (US-5) blocks any JSONField that bypasses the encoder. The S5 / S6-#404 / #331-#332-#388 scars are now permanent regression gates.
- **The live/replay seam exists from P0 (Principle #7).** US-2 ships `DataSource` + injectable clock with static-analysis guards: no consumer imports a concrete source, no `time.time()`/`datetime.now()` on the core path, and the core import graph is framework/network-free. The expensive late-retrofit the PRD warns against was avoided.
- **The defect loop worked again.** Two lint failures (US-2 AC-2.2 I001/F401; US-5 AC-5.2 I001) were caught by CI, diagnosed LOW-severity by the Tester, and `ruff --fix`'d before merge. Style-only, never reached pytest, fixed in one iteration.
- **Retrospective actions A2 and A3 landed.** A3: `retrospective.md` now has a named owner (Tester / scrum facilitator) and is produced as a deliverable. A2: the DoD deploy clause is decoupled from per-story DoD and gated at the sprint boundary, so US-2/3/4/5/7 were not structurally blocked by US-6 — the un-completability that sank US-1 is gone at the per-story level.
- **Firehose ledger seeded (US-7).** 10 Birdeye + 10 Helius / 0 used, per-activation protocol, HARD-RULE fixture-banking mandate, no API keys committed.

### What didn't go well 👎
- **Still nothing on the VPS — two sprints running.** Sprint-1 had no pipeline; sprint-2 *built* the pipeline but a trivial precondition (the `/root/solanatrilly/` directory doesn't exist) breaks the deploy at the first SCP. The CLAUDE.md "hello-world deployed day one" mandate is now far past day one, and US-1's DoD (retro A1) is still open.
- **The blocker was environmental and avoidable.** The deploy SCPs to a directory it never creates; a one-line `ssh … mkdir -p /root/solanatrilly` before the SCP (or creating the dir once on the box) would have closed it. No agent had VPS-shell verification in the loop to catch the missing precondition before the run.
- **The deploy trigger itself is broken.** deploy.yml has only `push: branches: [main]` — no `workflow_dispatch` — so the orchestrator's full-sprint deploy 422'd ("Workflow does not have 'workflow_dispatch' trigger"). Per-story `[DEPLOY]` steps logged "Deploy trigger failed. Will be caught by full sprint deploy," but the full sprint deploy could not be triggered. The pipeline was never exercised by a successful trigger + run on `main` with the directory present.
- **Green structural tests masked an un-deployed stack.** US-6's pytest suite (deploy.yml parsing, port/scope/secret assertions) is all green, which can read as "deploy works." It does not — the tests validate the *file*, not a *running stack*. Passing CI ≠ a deployed, smoke-tested service.
- **A5 was not enforced; the same status inconsistencies recurred.** `sprint2.json` still reads `phase: planning` at review; several stories carry `dev_status: not-started`/`in-progress` while `status: done` and all ACs are dev-done; and **US-6 reads `status: done` while `tester_status: failed`** — the exact "done ≠ blocked/failed" class A5 told us to forbid after sprint-1.

### Action items → sprint-3
| # | Action | Owner | Rationale |
|---|--------|-------|-----------|
| B1 | Fix the deploy precondition: add `ssh … mkdir -p /root/solanatrilly` (or create the dir once on the VPS) **before** the first SCP in `deploy.yml`; re-run the deploy on `main` and confirm the 8002 smoke-test returns **200** and the solanaBilly 8001 isolation check passes. | Dev | Closes US-6 AC-6.3/6.4/6.5 **and** retroactively US-1's DoD (A1, still open) — the keystone P0 deliverable. |
| B2 | Add `workflow_dispatch:` to `deploy.yml` so a deploy can be triggered on demand (fixes the HTTP 422). | Dev | The orchestrator's full-sprint deploy could not fire; an on-demand trigger is needed to verify the path. |
| B3 | Put VPS verification **in the loop** and make it gate "done": after deploy, confirm the stack answers on 8002 and solanaBilly is untouched on 8001. A green structural pytest is **not** a deployed stack. | Tester / Dev | Sprint-2 nearly shipped "deploy works" on the strength of file-parsing tests alone. |
| B4 | Enforce A5 for real: forbid `status: done` while `tester_status` is `failed`/`blocked`; normalize stale `phase`/`dev_status` fields at review. US-6 should read `in-review`/`blocked`, not `done`; `sprint2.json.phase` should be `review`. | PO / Project Lead | A5 was logged in sprint-1 and not actioned; the same inconsistencies recurred — now with `done` + `failed` coexisting. |
| B5 | Carry US-6's failed ACs (6.3–6.5) into sprint-3 as the **top-priority closeout before any P1 work**. P0 is not exited until the VPS stack is live and smoke-tested. | PO | "Tested, **deployed**, drift-resistant base ready for P1" is the P0 exit bar; deploy is the only piece left. |

### Metrics
- **Stories:** 6 committed / 5 fully DoD-done / **1 failed (US-6)** | **0 deployed to VPS**
- **ACs:** 22 committed / **19 approved** / 3 failed (US-6 AC-6.3/6.4/6.5)
- **PRs merged:** all CI-green (`test` job pass); Tester records **27 merged PRs** across the 6 stories
- **CI failures:** 2 lint defects (US-2 AC-2.2 I001/F401; US-5 AC-5.2 I001), both caught by CI and fixed pre-merge in 1 iteration
- **Token spend / cost:** see `../project-state.json` (owned by Project Lead — not reproduced here)
- **Retrospective carry-over:** A1 (VPS deploy) **still open** → re-issued as B1; A2 / A3 **actioned**; A5 **not actioned** → re-issued as B4

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
