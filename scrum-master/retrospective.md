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
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
