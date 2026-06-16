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

## Sprint-3 — Exit P0, Open P1 (Config Core)

**Reviewed:** 2026-06-15 | **Phase at review:** review
**Committed scope (source of truth `sprint3.json`):** 4 stories — US-8 (P0 deploy closeout) + US-9/US-10/US-11 (P1 config core), **16 ACs**
**Outcome:** All 16 ACs **implemented and CI-green** across 16 merged PRs (#38–#53). The **P1 config core is fully delivered in code** (US-9/10/11). But **P0 is still not exited** for a third consecutive sprint: every Deploy run fails the VPS smoke-test, so nothing is verified-live on port 8002. **US-8 FAILED** (AC-8.3/8.4/8.5); US-9/10/11 rated **partial** (code complete, sprint-boundary deploy DoD unmet). Sprint goal **half met** — P1 opened, P0 not closed.

### Sprint goal vs. delivered
The goal had two halves on one milestone (PRD §16 / §5): **(1)** close the last P0 blocker — fix the CD deploy that never reached the VPS (`mkdir -p` the SCP target, add `workflow_dispatch`), run it green on `main`, and verify the isolated stack answers **200 on 8002** with solanaBilly untouched on **8001**, retroactively closing US-1's DoD (US-8; retro B1/B2/B3/B5); **(2)** deliver the P1 config core — versioned/audited/admin-editable `PipelineConfig` + `pipeline_state` singleton (US-9), a typed Pydantic v2 schema that **rejects an invalid config at save time** (US-10), and the single cached `get_active_config()` resolver with atomic activation + rollback and no silent auto-start (US-11).

**Delivered:** **the entire P1 config core, in code, behind a green gate** — the model, django-simple-history audit, admin surface, and the `pipeline_state` singleton (US-9); the full §5.2 invariant set as Pydantic rejection gates **on the write path** (US-10); and the cached resolver with atomic flip/rollback + an AST no-auto-start guard (US-11). **The deploy now actually deploys** — sprint-2's SCP blocker is gone: SSH connects, the GHCR image pulls, `docker compose -p solanatrilly … pull && up -d` runs, and the web container **starts on the VPS**. **What is NOT delivered:** a *verified* live stack. The CD smoke-test (a curl from the GitHub runner to `http://VPS:8002/health/`) fails with **curl exit code 7** on all 13 Deploy runs, so AC-8.3 (200 on 8002), AC-8.4 (isolation step, gated behind the smoke-test), and AC-8.5 (Tester confirmation → P0 exit) never pass. **"VPS presence from P0" remains unmet — three sprints running.**

### Tester feedback (verbatim source — `sprint3.json` `tester_sprint_notes`, 2026-06-15)
> SUMMARY: All 16 ACs across 4 stories are fully implemented and verified by CI. All 16 PRs (38-53) passed the full CI suite (lint + test). Code quality is high. The sprint is blocked at the VPS DoD gate by a single infrastructure issue: VPS port 8002 is not open to external connections, causing every Deploy workflow smoke-test to fail with curl exit code 7.
>
> CI RESULTS (16/16 PRs passing): PRs 38-42 (US-8) all pass · 43-46 (US-9) all pass · 47-50 (US-10) all pass · 51-53 (US-11) all pass.
>
> DEPLOY RESULTS (0/13 Deploy runs passing smoke test): Every Deploy run since US-8 AC-8.3 merge fails at the smoke-test step. The deploy steps before it (SSH, docker compose pull+up) all succeed. curl exit code 7 = 'Failed to connect to host' = port not reachable externally from the GitHub Actions runner.
>
> INFRASTRUCTURE BLOCKER: Port 8002 on VPS 140.82.43.36 must be opened to inbound TCP connections. This is a single operator action (e.g., `ufw allow 8002/tcp` or equivalent firewall rule). No code changes are required. Human escalation is required because agents do not have direct VPS firewall access.
>
> DOD GAPS (sprint-level): (1) VPS smoke-test gate not cleared — BLOCKED on port-8002 firewall; (2) VPS isolation check (solanaBilly on 8001) never ran — BLOCKED on same firewall (isolation step gated after smoke-test); (3) retrospective.md not yet updated for sprint-3 — pending closeout; (4) Status integrity: story status/dev_status show 'not-started' for completed stories — normalize at closeout.
>
> STORIES THAT WOULD PASS DOD ONCE FIREWALL IS FIXED: US-9, US-10, US-11 (all code correct, all CI green, all containers deploy — only the external smoke-test gate remains). STORY REQUIRING ADDITIONAL VERIFICATION AFTER FIREWALL FIX: US-8 AC-8.4 (isolation step must pass from an actual deploy run) and US-8 AC-8.5 (Tester must confirm 200 on 8002 + solanaBilly untouched on 8001 from a green deploy run before P0 is declared exited).

### What went well 👍
- **The P1 config core is done — the single biggest deliverable of the project so far.** US-9 (model + django-simple-history audit + admin + `pipeline_state` singleton), US-10 (full Pydantic v2 schema enforcing every §5.2 invariant with explicit rejection tests), and US-11 (cached `get_active_config()` resolver + atomic activation/rollback + no-silent-auto-start) all landed CI-green (~290 tests). This is the config-driven source of truth (Principle #1) every later phase (P2–P8) reads from — built early, before the regime churn (#314/#316/#402) the PRD warns against.
- **Save-time invariant enforcement is real and on the WRITE path (AC-10.4), not just the UI.** The leak guard, D4 label-truncation guard, `capture_buffer_s ≥ 3`, the id22 `adaptive_topk`-only gate, and the D2 feature-contract subset check are now permanent rejection gates — an invalid config *cannot be persisted*.
- **No silent auto-start is enforced with an AST guard (AC-11.3).** §5.3/§15.6 honored from the start: `firehose_active`/`scoring_enabled`/`trading_enabled` default False and no boot/ready/resolver/WS-drop path flips them True.
- **The deploy genuinely deploys now.** Sprint-2's keystone blocker is closed: AC-8.1 (`mkdir -p /root/solanatrilly` before SCP) and AC-8.2 (`workflow_dispatch`) both landed and are verified — the HTTP 422 is gone, SSH connects, the image pulls from GHCR, and the web container starts on the VPS. **P0 has never been this close.**
- **AC-as-PR cadence and the defect loop held at scale.** 16 ACs → 16 PRs, all CI-green; two lint defects (US-8 AC-8.3 F841 dead assignment; US-9 AC-9.3 I001) were caught by CI and fixed pre-merge in one iteration each.

### What didn't go well 👎
- **P0 still not exited — third consecutive sprint with no verified VPS presence.** Sprint-1 had no pipeline; sprint-2 built it but the SCP failed on a missing directory; sprint-3 fixed that and the container now *starts*, but the external smoke-test can't reach port 8002. The blocker moved one layer deeper each sprint, yet the CLAUDE.md "hello-world deployed day one" mandate and US-1's DoD (retro A1, now re-re-issued) remain open.
- **The "firewall" diagnosis is unverified on the box.** curl exit code 7 was attributed to a closed port-8002 firewall — plausible, but **no one SSH'd to the VPS to `curl localhost:8002/health` and distinguish a firewall block from a port-publish/bind bug** in `docker-compose.staging.yml` (e.g. the container binding `127.0.0.1` or the host port not published). These have different fixes: one is an operator firewall action, the other is a code change. Per CLAUDE.md agents *have* SSH to the VPS — B3 ("VPS verification in the loop") was logged twice and the diagnosis still stops at the GitHub runner's external curl.
- **The smoke-test retry loop didn't actually run.** AC-8.5's structural test asserts the smoke-test uses a retry loop, yet the most recent run (27540260960) shows the curl failing **immediately, with no retry**, right after the container started. A green structural test masked a runtime that doesn't behave as asserted — the exact sprint-2 B3 lesson ("passing CI ≠ a deployed, smoke-tested service"), recurring.
- **"No open human dependencies for sprint-3" was wrong.** The board asserted it at kickoff; a probable operator-only action (open 8002 inbound) emerged at review. The pre-sprint dependency scan missed external port reachability — the one thing that ultimately gated the sprint.
- **Status integrity broke for the third sprint.** `sprint3.json` carries **`US-8 status: done` while `tester_status: fail`** — the precise "done ≠ failed/blocked" violation flagged as A5 (sprint-1) and B4 (sprint-2); `dev_status: not-started` still sits on completed stories; and the board phase still read `planning` at review. B4 was logged and again not enforced.

### Action items → sprint-4
| # | Action | Owner | Rationale |
|---|--------|-------|-----------|
| C1 | **Diagnose port-8002 ON the VPS before concluding "firewall."** SSH in and run `curl -v localhost:8002/health`, `docker compose -p solanatrilly ps`, inspect the port-publish/bind in `docker-compose.staging.yml`, and check `ufw`/`iptables`. Distinguish an operator firewall fix from a code-level port-publish/bind bug. | Dev / Tester | curl exit 7 has ≥2 root causes with different fixes; the "firewall" call is unverified. Finally puts B3 verification *in the loop* (agents have VPS SSH). |
| C2 | **Apply whatever C1 finds, re-run the deploy on `main`, and confirm 200 on 8002 + solanaBilly isolation on 8001.** Closes US-8 AC-8.3/8.4/8.5 and US-6, retroactively closes US-1's DoD, and **exits P0**. This is the fourth attempt at the same milestone. | Dev / operator | The keystone P0 deliverable; "tested, **deployed**" is the only P0-exit clause still open. |
| C3 | **Make the smoke-test actually retry with backoff at runtime** (the loop AC-8.5 claims) and upgrade the structural test to verify *runtime* retry behavior, not just file text — so a slow container start isn't a false negative and a green test reflects reality. | Dev | The retry loop is asserted in tests but not executed in the run; closes the B3 "green test ≠ live stack" gap. |
| C4 | **Enforce status integrity programmatically (third issuance of A5/B4).** Forbid `status: done` while `tester_status` is `failed`/`blocked`; normalize `dev_status` + board `phase` at review; add a JSON-lint/CI check on `sprintN.json` so it cannot recur. | PO / Project Lead | Logged in sprint-1 and sprint-2, actioned in neither. `US-8 done + fail` is live right now. |
| C5 | **Reopen the human-dependency line.** If C1 confirms a firewall: record the open-8002 operator action in `po-requests.md`; the board must stop asserting "no open human dependencies" until VPS external reachability is proven. | PO | "No open human dependencies" was asserted and proven false at review. |
| C6 | **Don't let the deploy blocker stall code throughput.** P1 config core is delivered; with deploy gated at the sprint boundary (A2), pull **P2 (detection)** into sprint-4 in parallel with the P0 closeout (C1/C2). | PO | Three sprints of P0-deploy drag should not also stall P1→P2 progress now that the config core is built. |

### Metrics
- **Stories:** 4 committed / **0 fully DoD-done** (VPS deploy gate unmet) / 3 code-complete & CI-green but Tester-`partial` (US-9/10/11) / 1 failed (US-8 deploy ACs) | **0 verified-deployed to VPS** (third sprint)
- **ACs:** 16 committed / **13 Tester-pass** (code) / 3 fail (US-8 AC-8.3/8.4/8.5, all on the external smoke-test gate)
- **PRs merged:** 16 (PR#38–#53), all CI-green (`test` job pass)
- **Deploy runs:** 13 triggered, **0 passed smoke-test** (curl exit code 7 on every run)
- **CI defects:** 2 lint (US-8 AC-8.3 F841 dead assignment; US-9 AC-9.3 I001), both caught by CI and fixed pre-merge in 1 iteration
- **Tests:** ~290+ passing | **Coverage:** ≥80% gate met (enforced in CI)
- **Token spend / cost:** see `../project-state.json` (owned by Project Lead — not reproduced here)
- **Retrospective carry-over:** **B1** partially actioned (SCP blocker fixed; a deeper port-reachability gate surfaced → C1/C2) · **B2 actioned** (`workflow_dispatch` added, 422 resolved) · **B3 not truly actioned** (verification still stops at the GitHub runner) → C1/C3 · **B4 not actioned** (third occurrence) → C4 · **B5 actioned** (US-8 carried in and built; deploy still blocked) · **A1 still open** → re-issued as C2

---

## Sprint-4 — Exit P0 (for real, 4th attempt), Open P2 (Detection)

**Reviewed:** 2026-06-15 | **Phase at review:** review
**Committed scope (source of truth `sprint4.json`):** 5 stories — US-12 (P0 closeout) + US-13 (process guard) + US-14/US-15/US-16 (P2 detection), **18 ACs**
**Outcome:** **All 5 stories fully Tester-approved; 18/18 ACs approved across 18 merged PRs.** **P0 IS FINALLY EXITED** — the VPS staging stack answers **HTTP 200 on 8002** with solanaBilly untouched on **8001**, from an actual green deploy run. **P2 (detection) is delivered** — the `tokens` model, the DataSource-seam detection consumer with a green offline replay gate, and detection resilience (Helius reconciler + Birdeye REST sweep + dedicated `listener` container). Sprint goal **fully met.** Four sprints of P0-deploy drag are over.

### Sprint goal vs. delivered
Two halves on one milestone (PRD §16 / §6.1): **(1)** close the last P0 blocker — diagnose port-8002 *on the VPS*, fix it as root, make the smoke-test retry at runtime, run the deploy green on `main`, verify 200-on-8002 + isolation-on-8001, and exit P0 (US-12), plus a programmatic status-integrity CI guard (US-13); **(2)** open P2 detection — the `tokens` persistence target (US-14), a Birdeye `SUBSCRIBE_MEME` consumer behind the DataSource seam + injected clock that writes `tokens` rows from the active config's detection filter with dedupe + pre-stage, offline-gated by a MEME-stream replay (US-15), and detection resilience: the Helius `migrate` reconciler backstop, a Birdeye REST graduation sweep, and a dedicated `listener` container (US-16).

**Delivered: everything, behind a green gate.** US-12 exited P0; US-13 shipped the guard and it *proved itself live* (see below); US-14/15/16 delivered the full P2 detection core — the consumer reads from a `DataSource` + injected clock (US-2 static-analysis guard still green), the offline replay gate is deterministic, the reconciler recovers a mint a dropped MEME event missed (exactly once, no duplicate), and the `listener` container is up on the VPS in both compose files. **No live Birdeye/Helius activation was spent** — US-15's offline gate runs on a schema-faithful synthetic MEME stream, so the firehose budget is untouched (still 10/10 each).

### Tester feedback (verbatim source — `sprint4.json` `tester_sprint_notes`, 2026-06-15)
> FINAL QUALITY REVIEW — 2026-06-15. All 5 stories (18 ACs) reviewed and approved. DoD: (1) All 18 ACs CI-green — 18 PRs merged, CI 'test' job green on every feature branch and at merge. (2) No critical defects — 3 trivial lint violations (ruff E741 AC-12.5, F541 AC-15.2, E501 AC-15.4) caught by Tester diagnosis, fixed same-day, re-merged green. (3) Coverage ≥80% enforced throughout. (4) Metadata front matter on all new files. (5) Docker/listener — US-16 AC-16.3 added the listener service to both compose files; VPS deploy confirmed it running. (6) CD pipeline — post-PR-merge deploy green (run 27555701056); orchestrator sprint-end re-deploy failures were caused by stale `phase:'planning'` triggering the US-13 validator, not a code regression. (7) VPS verification — US-12 confirmed HTTP 200 on 8002 and solanaBilly untouched on 8001. (8) Status integrity — US-13 `sprint_integrity_check.py` running in CI and proved itself by catching the stale phase. (9) Offline detection gate — US-15 AC-15.4 ReplaySource replay test green; no live Birdeye activation spent. REMAINING DOD GAP: `retrospective.md` not yet updated for sprint-4 (named owner: Tester/scrum facilitator) — sprint cannot be 'complete' until written. [This entry closes that gap.]

### What went well 👍
- **P0 is EXITED — the project's longest-running blocker is closed.** Four sprints (no pipeline → SCP fails → container starts but unreachable → finally green) end here. The isolated staging stack answers **200 on 8002** and solanaBilly is untouched on **8001**, confirmed by the Tester from an actual green deploy run — not from green pytest alone. US-1's deploy-gated DoD (retro A1, open since sprint-1), US-6, and US-8 AC-8.3/8.4/8.5 all close retroactively.
- **The 3-sprint "firewall" misdiagnosis was finally falsified by going to the box (C1).** SSH'ing in (root, per CLAUDE.md) showed: no ufw, iptables INPUT ACCEPT-all, `0.0.0.0:8002->8000/tcp` correctly published, and **HTTP 200 both locally and externally**. The real root cause was a **smoke-test timing race** — the curl fired immediately after `docker compose up -d`, hitting the container mid-startup (migrations running before daphne binds). Neither (a) firewall nor (b) port-bind bug applied. The "human escalation required / agents lack firewall access" claim that gated three sprints was **wrong** — and only the on-box check (C1) surfaced it.
- **The runtime-retry fix is real and self-verifying (C3).** AC-12.3 gives the smoke-test bounded retry-with-backoff (`SMOKE_MAX_ATTEMPTS`/`SMOKE_RETRY_DELAY`), and the structural test parses the workflow YAML to assert *runtime* loop/sleep/max-attempts behavior — not keyword presence. The exact "green test masked a no-retry runtime" gap from sprint-2/3 B3 is closed.
- **The status-integrity guard proved itself in production (C4).** US-13's `sprint_integrity_check.py` runs in CI on every PR over all `sprint*.json`, forbidding `done`+`failed/blocked` and flagging stale `dev_status`/`phase`. It immediately **caught a real violation**: the orchestrator's sprint-end re-deploys (runs 27555736146, 27555822147) failed at the integrity step because `sprint4.json` still read `phase:'planning'` while every story was done. The "done ≠ failed/blocked" class flagged in A5 (sprint-1), B4 (sprint-2), and C4 (sprint-3) — logged-and-unactioned three times — is now a permanent, self-enforcing gate.
- **P2 detection landed behind the live/replay seam (Principle #7), with the budget preserved.** The consumer reads MEME events from a `DataSource` and time from an injected clock; the US-2 static-analysis guard (no concrete-source import, no `time.time()`/`datetime.now()` on the core path) still holds. The detection filter is read from `get_active_config()` (US-11 resolver), not a hardcoded constant or `os.getenv` (Principle #1). The offline replay gate is deterministic (run twice → identical rows) on a **synthetic** MEME stream — **0 firehose activations spent** (10 Birdeye + 10 Helius still banked).
- **Three-belt detection resilience.** Helius `migrate` reconciler (D4) recovers a mint a dropped Birdeye MEME event missed — exactly once, `get_or_create` for no-overwrite idempotency; a Birdeye REST graduation sweep runs as a registered Celery-beat task (H2 manifest test guards its removal); and a dedicated `listener` container (#289 lesson — detection never shares the web/gunicorn process) is in both compose files and up on the VPS.
- **The defect loop held at scale, again.** 18 ACs → 18 PRs, all CI-green. The only defects were 3 trivial lint slips (E741 ambiguous `l`, F541 empty f-string, E501 long header) — each caught by Tester diagnosis and fixed same-day in one iteration. None reached pytest or production.

### What didn't go well 👎
- **Status integrity recurred at the orchestrator layer — but this time the guard caught it.** The new US-13 validator did its job, yet the underlying habit it targets still slipped: the orchestrator triggered two sprint-end deploys with `phase:'planning'` standing while all stories were done, and both deploys failed at the integrity step. A win for the guard, but the process gap (update `phase` *before* the sprint-end deploy) is real and should be fixed at the source so the guard isn't routinely tripped by our own staleness.
- **Story-level `dev_status` is still `not-started` on all 5 done stories.** The artifact A5/B4/C4 keeps flagging persists at the story level: every US-12…US-16 record reads `dev_status:'not-started'` while all ACs are `dev_status:'done'` and `tester_status:'approved'`. US-13 had to add a `--skip-complete` flag and a "story already done → skip stale check" carve-out so archived/accepted stories don't trip CI — which papers over the un-promoted field rather than normalizing it. The field should be promoted to `done` at closeout, not exempted.
- **Three sprints of P0 drag were partly self-inflicted.** The on-box diagnosis that finally cracked it (C1) was available from sprint-1 — CLAUDE.md granted root SSH the whole time. Attributing curl exit 7 to an operator-only firewall for three sprints, without once running `curl localhost:8002` on the box, cost real schedule. The lesson (verify on the box before escalating to "human required") is now banked the hard way.
- **The `listener` container runs but isn't yet driven by a live source.** `run_listener` starts the async loop and logs "ready to accept live DataSource configuration," but no concrete Birdeye/Helius source is wired to it on the VPS yet — detection is proven offline (replay gate) but not yet live end-to-end. That's correct for P2's offline-gated scope, but the live wiring + a banked firehose fixture is the obvious next step (carried as D4).

### Action items → sprint-5
| # | Action | Owner | Rationale |
|---|--------|-------|-----------|
| D1 | **Open P3 (tape recorder).** P0 is exited and P2 detection is delivered; the `tokens` model already carries `graduated_block_time` as the integer rel-anchor the recorder needs. Pull the PumpSwap swap-tape recorder (capture every swap from t0; Birdeye tape as single source of truth) into sprint-5 as the primary deliverable. | PO | The next phase on the critical path now that detection lands graduated tokens. |
| D2 | **Update `phase` before triggering sprint-end deploys.** The US-13 guard caught two orchestrator deploys with `phase:'planning'` still standing. Promote `phase` (and story `dev_status`) at the *start* of the review/deploy step, not after, so the integrity gate isn't tripped by our own staleness. | Project Lead / PO | A self-inflicted CI failure the guard now catches — fix at the source. |
| D3 | **Normalize story-level `dev_status` at closeout instead of exempting it.** Promote `dev_status:'done'` on accepted stories rather than relying on US-13's `--skip-complete`/story-done carve-out; the carve-out hides an un-promoted field that has now drifted in four consecutive sprints. | PO / Project Lead | Closes the A5/B4/C4 artifact for real, not by exemption. |
| D4 | **Wire a live source to the `listener` and bank a firehose fixture.** The consumer + reconciler are built behind the seam but driven only by replay. Wire a concrete Birdeye/Helius source into `run_listener` on the VPS, run a single logged firehose activation (`ops/firehose_activation_log.md`, §15.7), and confirm a real graduation flows to a `tokens` row end-to-end — banking the durable capture as a fixture. | Dev | Proves detection live, not just offline, and starts spending the firehose budget deliberately. |
| D5 | **Bank the on-box-diagnosis lesson as a standing rule.** "Before declaring a blocker 'human/operator required,' run the on-box check the access we already have allows." curl exit 7 was misattributed to a firewall for three sprints; the rule prevents the next three-sprint drag. | Tester / facilitator | The single highest-cost process miss of P0, now generalized. |

### Metrics
- **Stories:** 5 committed / **5 fully DoD-done** (P0 exited; retrospective written) | **1 verified-deployed-and-live on the VPS** (first time — staging stack answers 200 on 8002)
- **ACs:** 18 committed / **18 Tester-approved** / 0 failed
- **PRs merged:** 18, all CI-green (`test` job pass)
- **Deploy runs:** post-merge deploy **GREEN** (run 27555701056; US-15 green at 27553071900); two later sprint-end re-deploys failed at the US-13 integrity step on stale `phase:'planning'` (a process slip the guard caught, not a code regression) — fixed in this review pass (`phase` → `review`)
- **CI defects:** 3 trivial lint (E741 AC-12.5, F541 AC-15.2, E501 AC-15.4), all caught by Tester diagnosis and fixed same-day in 1 iteration each
- **Firehose budget:** **0 activations spent** — US-15 offline gate runs on a synthetic MEME stream (10 Birdeye + 10 Helius still banked)
- **Coverage:** ≥80% gate met (enforced in CI); dev-reported 100% on the new ACs
- **Token spend / cost:** see `../project-state.json` (owned by Project Lead — not reproduced here)
- **Retrospective carry-over:** **C1 ACTIONED** (on-box diagnosis; firewall falsified, timing race found) · **C2 ACTIONED** (green deploy, P0 EXITED — A1 closes retroactively) · **C3 ACTIONED** (runtime retry + structural test verifies runtime) · **C4 ACTIONED** (US-13 guard live in CI, proved itself) · **C5 N/A** (no firewall → no operator action; the "human required" claim was falsified) · **C6 ACTIONED** (P2 detection delivered alongside the P0 closeout)

---

## Sprint-5 — Open P3 (the Tape Recorder) + prove detection live (D4)

**Reviewed:** 2026-06-16 | **Phase at review:** review
**Committed scope (source of truth `sprint5.json`):** 6 stories — US-17…US-22 (20 ACs)
**Outcome:** **All 6 stories implemented, merged, and CI-green; the full P3 tape recorder is live on the VPS.** US-17, US-18, US-19, US-20, US-21 are fully Tester-**PASS** (17/17 ACs). **US-22 is CONDITIONAL_PASS** — all 3 ACs are functionally DoD-complete and the live end-to-end proof is confirmed on the box, but the story was held conditional on two **process** items at review: (A) this retrospective section (DoD item, owner: Tester/scrum facilitator), and (B) a clean final deploy after the stale-`phase` slip (D2) tripped the US-13 guard. Sprint goal **met** — P3 ("the heart of the pipeline") is delivered, detection→recorder is proven **live** end-to-end, and the first firehose spends bank real PumpSwap reality into the replay corpus.

### Sprint goal vs. delivered
The goal (PRD §6.2 / retrospective D1+D4) was to **open P3 — the PumpSwap swap-tape recorder** behind the `DataSource` seam + injected clock (Principle #7), in build order: the `swaps` table + the one `NormalizedSwap` schema with `rel` anchored to `Token.graduated_block_time` (US-17); the recorder **core** — one `NormalizedSwap` per **landed** swap, `owner`=signer, all three units, drop-failed, **stable** `(block_time, slot, signature)` ordering (#403), zero/degenerate guard (#405) (US-18); the append-only daily-partitioned `jsonl.gz` lake + idempotent `swaps` writer + truncated-tail-tolerant reader (US-19); resilience — `seek_by_time` gap reconciliation via the **identical** backfill code path + config-driven idle-kill TTL re-attach (US-20); the **P3 offline gate** — deterministic `ReplaySource` replay + live↔backfill byte-parity on golden tokens + the regression suite wired so it can't vanish (US-21); and **finally (D4)** wire a concrete Birdeye `SUBSCRIBE_TXS` source into `run_listener`, spend the **first** firehose activation (logged §15.7), prove a real graduation's swaps flow end-to-end on the VPS, and **bank** the capture as the golden fixture US-21 runs against offline forever (US-22).

**Delivered: everything, behind a green gate.** The persistence target, the source-agnostic recorder core (US-2 static-analysis guard held throughout — no concrete-source import, no `time.time()`/`datetime.now()` on the core path), the immutable `jsonl.gz` lake + idempotent mirror + recovery-grade reader, `seek_by_time` reconciliation that is **literally the same `TapeRecorder` call path** as backfill (AST-guarded, byte-identical parity), config-resolved idle-kill, and a deterministic (run-twice-byte-identical) offline gate with an `ImportError`-trap-wired regression suite. **D4 was over-delivered and went live:** two firehose activations were spent (see below), and **8 real PumpSwap swaps for `H9L9apxE8RREZZgTaNLmGeUfCYJQfHBwQxuXzvPNpump` flowed Birdeye `SUBSCRIBE_TXS` → recorder → `swaps` rows + `jsonl.gz` on the VPS `listener` container** — Tester-confirmed on the box via `psql … count(*) = 8`.

### Tester feedback (verbatim source — `sprint5.json` `tester_sprint_notes`, 2026-06-16)
> SPRINT-5 FINAL QUALITY REVIEW — 2026-06-16.
>
> STORY VERDICTS: US-17 PASS, US-18 PASS, US-19 PASS, US-20 PASS, US-21 PASS, US-22 CONDITIONAL_PASS. All 6 stories / 20 ACs implemented, merged to main, and CI-green. The full P3 tape recorder is live on VPS.
>
> DOD ITEMS MET: (1) All 20 ACs verified by CI — every PR for every AC had 'test' job PASSED at merge. (2) No critical defects — all dev-tester loop defects were lint/style (low severity) and resolved within 2 iterations each. (3) Coverage >= 80% enforced by ci.yml --cov-fail-under=80, threshold met on all PRs. (4) Code file headers include metadata front matter — confirmed in dev implementation notes for all new files. (5) All services in Docker (listener, web, db, redis all running in solanatrilly compose project on VPS). (6) VPS smoke-test PASSED: listener Up, web Up/healthy on 8002, /health/ returns 200, db/redis healthy. solanaBilly on 8001 fully isolated. (7) Principle #7 DataSource seam intact (US-2 static-analysis guard held throughout). (8) Raw = immutable truth — jsonl.gz lake is append-only, daily-partitioned. (9) Firehose budget honored — 2 activations (Birdeye 10->9->8), both deliberate, time-boxed, logged in ops/firehose_activation_log.md with durable fixtures banked.
>
> DOD ITEMS PENDING (2 items blocking full PASS): (A) retrospective.md sprint-5 section not yet written — DoD requires 'retrospective.md updated for sprint-5 (named owner: Tester / scrum facilitator — retrospective A3)'. Scrum facilitator must author this section before sprint is closed as DONE. (B) The final deploy at HEAD (ddb203b) failed due to stale 'phase=planning' field tripping US-13 guard — this is now corrected to 'review' in this Tester update; a clean re-deploy should be triggered to confirm the pipeline is green at true HEAD.
>
> RECURRING PATTERN FOR RETROSPECTIVE: Dev team consistently pushed test files without running 'ruff check --fix' inside the container — the same I001/E501 lint pattern surfaced in AC-17.1, AC-17.2, AC-19.3, AC-22.2. Recommend adding 'ruff check --fix inside container' as a pre-push checklist item for sprint-6.
>
> D2 PROCESS GAP: The sprint 'phase' was never updated from 'planning' to 'development'/'review' during the sprint, causing the US-13 guard to fail on the final deploy. Per retrospective D2, this should be done before any sprint-end deploy. Corrected by Tester in this update.

### What went well 👍
- **P3 is delivered — "the heart of the pipeline" is built and live.** Six stories, 20 ACs, all merged behind a green `test` job. The recorder captures every PumpSwap swap from t0 into an immutable `jsonl.gz` lake + a queryable `swaps` mirror, with `rel` anchored to the DB `Token.graduated_block_time` on both sides (never the first swap). This is the project's primary dataset and the single source of truth for all downstream signal.
- **Principle #7 held end-to-end — the seam paid off again.** The recorder core reads swaps from a `DataSource` + injected clock with **no** concrete-source import and **no** wall-clock call; the US-2 static-analysis guard stayed green across all six stories. The concrete `BirdeyeSwapSource` + `WallClock` live **only** in the `run_listener` adapter wiring (US-22 AC-22.1), never on the core path — exactly the live/replay separation the PRD mandates.
- **Parity by construction is real, not aspirational.** `GapReconciler` *is* the `TapeRecorder` backfill path — an AST guard proves `gap_reconciler.py` never calls `from_raw_swap()` directly (one code path), and the live-gap-reconcile vs. pure-backfill outputs are **byte-identical** (AC-20.3). The offline gate (US-21) extends this to live↔backfill byte-parity on golden tokens and run-twice determinism (rows **and** `jsonl.gz` content identical).
- **The regression suite cannot silently vanish.** AC-21.3 wires the #403 stable-ordering test, the #405 zero/degenerate guard test, and the truncated-tail recovery test into the canonical `ci.yml` `test` job via a compile-time `ImportError` trap on 4 named functions across 3 files — delete or rename any and CI fails at collection. The hard-won scars (#403, #405, the recovered-161k-rows tail reader) are now permanent gates.
- **Detection→recorder is proven LIVE, not just offline.** The first deliberate firehose spends drove a real graduated token's swaps all the way to `swaps` rows + `jsonl.gz` on the VPS `listener` container, Tester-confirmed on the box (`psql … count = 8` for `H9L9…pump`). After four sprints of "proven offline only," the live path is exercised end-to-end.
- **D5 actioned — verify on the box before escalating.** The AC-22.3 live proof was confirmed by SSH'ing to the VPS and running `psql` against `solanatrilly-db-1` (scoped `-p solanatrilly`), not by inferring from green CI. The sprint-4 lesson ("run the on-box check our root SSH allows") was applied directly.
- **D3 actioned — the recurring status artifact is finally normalized, not exempted.** For the first time in five sprints, story-level `dev_status` is promoted to `done` at closeout in `sprint5.json` rather than left `not-started` and papered over by US-13's `--skip-complete` carve-out. The A5/B4/C4/D3 artifact is closed at the source.
- **Firehose discipline held under real spend.** Both activations were deliberate, time-boxed (3.7 min and ≤150 s — well under the 30-min cap), logged in `ops/firehose_activation_log.md` with role/WS/purpose/duration/remaining-count, and **banked durable fixtures**: a 40-swap golden capture (`E6ifp2…pump`, dt=2026-06-15) and the 8-swap live-proof capture (`H9L9…pump`). The HARD-RULE "every activation banks a fixture" was honored both times.

### What didn't go well 👎
- **D2 recurred — the sprint-end deploy failed on our own stale `phase`, for the second consecutive sprint.** The final commit (`ddb203b`, documentation-only) triggered deploy run 27593136555, which **failed at the US-13 integrity step** because `sprint5.json` still read `phase: planning` while every story was `done`. This is exactly the self-inflicted failure D2 told us to prevent by promoting `phase` *before* the sprint-end deploy. The guard did its job (again); the process habit it targets slipped (again). Corrected to `review` by the Tester at this review. The prior deploy at `d8a9e18` (run 27592748809) was fully green, so the VPS *is* running the AC-22.3 code — only the doc-only re-deploy went red.
- **US-22 was the messy story, and the offline gate masked a missing live layer.** US-21's gate passed on **synthetic** schema-faithful fixtures, which read as "the recorder works end-to-end" — but AC-22.3 revealed that the actual live ingestion layer **did not exist**: a `birdeye_swap_mapper` (real Birdeye envelope → internal swap event), mapped/bounded sources, a `BirdeyeSwapSource` WS-handshake fix, settings plumbing for `BIRDEYE_API_KEY`, and a recorder None-reserve guard fix all had to be built during AC-22.3, with several red CI iterations (`2f906c5`, `c24a002` failed; green at `d8a9e18`). Synthetic-fixture parity is not the same as a working live adapter — the live path needs to be exercised against a real capture sooner.
- **Two firehose activations were spent where D4 budgeted one.** D4 said "the first of the 10." AC-22.2 spent #1 to bank the 40-swap golden fixture; AC-22.3 then needed a **second** (#2) for the end-to-end live proof because the live ingestion layer wasn't complete until that story. Justified, and 8 Birdeye remain — but it is a 2× overspend against plan, driven directly by the US-22 scope discovery above.
- **The same lint pattern recurred a fourth time.** ruff `I001`/`E501` slips appeared in AC-17.1, AC-17.2, AC-19.3, and AC-22.2 — dev pushing test files without running `ruff check --fix` inside the container first. Every one was caught by CI and fixed in ≤2 iterations (none reached pytest or production), but it is avoidable churn flagged since sprint-1.
- **Auto-generated `sprint5.md` drifted from the source of truth.** `sprint5.md` still renders `Phase: planning` and `Dev Team Status: not-started` on every story while `sprint5.json` carries the normalized `phase: review` / `dev_status: done` state — the same stale-mirror confusion noted in earlier sprints. The `.json` is authoritative; the `.md` regeneration lagged the closeout edits.

### Action items → sprint-6
| # | Action | Owner | Rationale |
|---|--------|-------|-----------|
| E1 | **Fix D2 at the source — promote `phase` (and confirm story `dev_status`) BEFORE triggering any sprint-end deploy.** The US-13 guard has now caught a stale-`phase` deploy failure two sprints running (sprint-4 runs 27555736146/27555822147; sprint-5 run 27593136555). Make "set `phase`→`in-progress`/`review` first" a hard step in the orchestrator's deploy sequence so the guard isn't routinely tripped by our own staleness. | Project Lead / PO | A self-inflicted, guard-caught CI failure repeating across sprints; the guard works — the habit doesn't. |
| E2 | **Add "run `ruff check --fix` inside the container" as a pre-push dev checklist item.** The `I001`/`E501` pattern has surfaced in every sprint (AC-17.1/17.2/19.3/22.2 this sprint). A one-line pre-commit/pre-push gate inside the web image kills it before CI. | Dev | Same low-severity lint churn flagged since sprint-1; cheap to eliminate. |
| E3 | **Exercise the live adapter against a banked real capture EARLY, not at the end of the story.** US-21's synthetic-fixture gate passed while the live ingestion layer (`birdeye_swap_mapper`, mapped/bounded sources, WS handshake) didn't exist — discovered only in AC-22.3, costing a second firehose spend. In future live-wiring stories, bank one real capture first, then build/verify the adapter against it offline before spending another live window. | Dev / PO | Synthetic parity ≠ a working live adapter; this is the B3/"green test ≠ live runtime" lesson in a new costume, and it burned firehose budget. |
| E4 | **Open P4 — score-time snapshot + units locked (§6.3 / D1).** P3 is delivered: the recorder captures every swap from t0 into the lake/`swaps` table with live↔backfill parity. The next phase on the critical path is the score-time feature snapshot with the three-unit lock, feeding the promoted-model scorer. | PO | P3 exit advances the pipeline; the units (vol_sol/vol_usd/sol_usd) and `rel` anchor are already in place for the snapshot. |
| E5 | **Trigger and confirm a clean final deploy at true HEAD (closes US-22 condition B).** With `phase` corrected to `review`, re-run the deploy on `main` and confirm 200-on-8002 + listener Up + solanaBilly untouched on 8001 from a green run, then flip US-22 CONDITIONAL_PASS → PASS. | Dev / Tester | The only remaining US-22 closeout item besides this retrospective (condition A, now satisfied). |

### Metrics
- **Stories:** 6 committed / **5 fully Tester-PASS** (US-17…US-21) / **1 CONDITIONAL_PASS** (US-22 — functionally DoD-complete, held on 2 process items: this retrospective + a clean re-deploy) | **P3 delivered and live on the VPS**
- **ACs:** 20 committed / **20 implemented, merged, CI-green** / 17 fully approved + the 3 US-22 ACs conditional
- **PRs merged:** #83–#102 (AC-as-PR) + the AC-22.3 commit series to `main`; CI `test` job green at merge (AC-22.3 had red push iterations at `2f906c5`/`c24a002`, green at `d8a9e18`)
- **Deploy runs:** `d8a9e18` **GREEN** (run 27592748809 — VPS running AC-22.3 code, 200 on 8002, listener Up); final doc-only commit `ddb203b` deploy **FAILED** (run 27593136555) at the US-13 integrity step on stale `phase: planning` — a D2 process slip the guard caught, corrected to `review` in this review pass
- **Firehose budget:** **2 activations spent** (Birdeye **10→9→8**; Helius **10** untouched) — both deliberate, time-boxed (3.7 min; ≤150 s), logged in `ops/firehose_activation_log.md`; fixtures banked: 40-swap golden `E6ifp2…pump` (dt=2026-06-15) + 8-swap live proof `H9L9…pump`
- **VPS verification:** Tester-confirmed from the box — `solanatrilly-listener-1` Up, `solanatrilly-web-1` Up/healthy on 8002 (`/health/` 200, `/admin/` 302), db/redis healthy; AC-22.3 `psql … count(*) = 8` for the logged mint; solanaBilly's 6 containers Up/untouched on 8001
- **CI defects:** lint-only (`I001`/`E501` in AC-17.1/17.2/19.3/22.2), each caught by CI and fixed in ≤2 iterations; none reached pytest or production
- **Coverage:** ≥80% gate met (enforced in CI)
- **Token spend / cost:** see `../project-state.json` (owned by Project Lead — not reproduced here)
- **Retrospective carry-over:** **D1 ACTIONED** (P3 tape recorder delivered) · **D2 NOT actioned** (stale `phase` tripped the deploy again → re-issued as **E1**) · **D3 ACTIONED** (story-level `dev_status` normalized to `done` at closeout, not exempted) · **D4 ACTIONED** (live Birdeye source wired, detection→recorder proven live, 2 firehose fixtures banked) · **D5 ACTIONED** (on-box `psql` verification before claiming the live proof)

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
