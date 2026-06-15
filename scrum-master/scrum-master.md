<!--
file: scrum-master.md
purpose: Current sprint status board + controlled vocabulary for solanaTrilly
owner: product-owner
last-updated: 2026-06-15
-->

# solanaTrilly — Scrum Master Board

## Current Sprint: sprint-3 — Exit P0, Open P1 (Config Core)
- **Phase:** planning
- **Sprint plan (source of truth):** [`sprint3.json`](sprint3.json)
- **PRD:** [`PRD.md`](PRD.md) — closes the last of **PRD §16 P0** (the CD deploy) and delivers **P1 (config core, §5)**
- **Previous sprint:** [`sprint2.json`](sprint2.json) — **closed** (review complete; 5/6 stories DoD-done, US-6 failed; see Sprint-2 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`

### Sprint Goal
Two halves, one milestone — **exit P0, open P1.** **(1) Close the last P0 blocker:** fix the CD deploy
that never reached the VPS — add `ssh … mkdir -p /root/solanatrilly` before the SCP (the target dir
doesn't exist on the box, so the SCP errors "No such file or directory") and a `workflow_dispatch`
trigger (fixes the orchestrator's HTTP 422) — then run it green on `main` and **verify the isolated
staging stack answers HTTP 200 on port 8002 with solanaBilly untouched on 8001**, retroactively closing
US-1's deploy-gated DoD (US-8; retrospective B1/B2/B3/B5). **(2) Deliver the P1 config core (PRD §5):**
the versioned, audited, admin-editable `PipelineConfig` model + `pipeline_state` singleton (US-9); a typed
Pydantic v2 schema that **rejects an invalid config at save time**, enforcing every §5.2 invariant (US-10);
and the single cached `get_active_config()` resolver with atomic activation + instant rollback and no
silent firehose/trading auto-start (US-11).

### Why this sprint now
P0 is *substantially* complete but **not exited**: sprint-2 built the CD pipeline yet a trivial
precondition (the `/root/solanatrilly/` directory is missing on the VPS) broke the deploy at the first
SCP, so **nothing is live on port 8002 for a second consecutive sprint** and US-1's DoD (retro A1) stays
open. The retrospective is explicit (B5): **carry US-6's failed ACs (6.3–6.5) into sprint-3 as the
top-priority closeout before any P1 work.** With P0 finally exited, the project advances to **P1 — the
config core**, the single versioned `PipelineConfig` source of truth every later phase (P2–P8) reads from.
Building the config-driven core early (Principle #1) keeps every tunable out of code constants and scattered
`os.getenv` — the regime churn (#314/#316/#402) the PRD warns against. This sprint also actions the carried
retrospective items: B1/B2 (deploy fix + `workflow_dispatch`), **B3 (VPS verification in the loop gates
"done")**, and **B4 (forbid `status: done` while a gate is failed/blocked; normalize stale fields)**.

## Stories (sprint-3 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-8 | P0 closeout — CD deploy lands on the isolated VPS staging stack (closes US-6 6.3/6.4/6.5 + US-1 DoD) | high | US-6 | 5 | ready | not-started | not-started |
| US-9 | P1 — `PipelineConfig` model: versioned, audited, admin-editable + `pipeline_state` singleton | high | US-1 | 4 | ready | not-started | not-started |
| US-10 | P1 — typed Pydantic v2 schema enforcing the save-time invariants (§5.2) | high | US-9 | 4 | ready | not-started | not-started |
| US-11 | P1 — the config resolver: single cached `get_active_config()` + atomic activation/rollback | high | US-9, US-10 | 3 | ready | not-started | not-started |

> **Scope:** sprint-3 commits **4 stories / 16 ACs** — the P0 deploy closeout (US-8) + the P1 config core
> (US-9…US-11). Source of truth: [`sprint3.json`](sprint3.json). After US-8 deploys green and is VPS-verified,
> P0 is **exited** and US-1's deploy-gated DoD closes retroactively; after US-9…US-11, **P1 (config core) is
> delivered** and the project advances to **P2 (detection)**.

**Build order:** **US-8 first** — the top-priority P0 closeout (retro B5); it is independent of the P1 chain
and may run in parallel, but P0 exit is the sprint's gating milestone. The P1 chain is sequential:
**US-9** (model + audit + admin + `pipeline_state`) → **US-10** (Pydantic schema, needs the model) →
**US-11** (resolver + activation, needs the model + schema).

**GitHub Issues:** created at sprint kickoff (2026-06-15), one per story, mirroring the sprint-2 convention —
[US-8 #34](https://github.com/AsimQuick/solanaTrilly/issues/34) ·
[US-9 #35](https://github.com/AsimQuick/solanaTrilly/issues/35) ·
[US-10 #36](https://github.com/AsimQuick/solanaTrilly/issues/36) ·
[US-11 #37](https://github.com/AsimQuick/solanaTrilly/issues/37).
Source of truth remains [`sprint3.json`](sprint3.json).

## Definition of Done (sprint-3)
A story is Done only when ALL of the following hold:
- All ACs verified by CI / Tester
- No critical defects
- Coverage threshold met (≥80%)
- Code file headers include metadata front matter (project convention)
- All services run in Docker (no host installs — Docker Rules); the web image is **rebuilt** after the
  `requirements.txt` change (`django-simple-history`) and the **VPS pulls the new image**
- **CD pipeline is now LIVE (US-8 closes the US-6 gap): every story is merged + deployed to the VPS solanatrilly staging stack (`-p solanatrilly`, port 8002) and smoke-tested there** ("works locally" is NOT done). **Per retrospective A2, the deploy clause is gated at the _sprint_ boundary.**
- **VPS verification is in the loop and gates "done" (retrospective B3):** a green structural pytest is **not** a deployed stack — the Tester confirms the running stack answers 200 on 8002 and solanaBilly is untouched on 8001 from the actual deploy run.
- Hard isolation from live solanaBilly preserved (every docker command scoped with `-p solanatrilly`; solanaBilly on port 8001 untouched)
- **Status integrity enforced (retrospective B4):** no story/AC reads `status: done` while its `tester_status` is `failed`/`blocked`; stale `phase`/`dev_status` fields are normalized at review.
- `retrospective.md` updated for sprint-3 — **named owner: Tester / scrum facilitator** (retrospective A3)

## Sprint-2 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-2…US-7 (6 stories, 22 ACs) | **Goal:** substantially met — one blocker
**Full retrospective + action items (B1–B5):** [`retrospective.md`](retrospective.md)

**Outcome:** 5 of 6 stories are fully Tester-**approved** and AC-complete — **US-2** (`DataSource`/clock seam),
**US-3** (H1 SHA-pinned CI), **US-4** (H2 task-manifest test), **US-5** (H3 `json_safe` encoder), **US-7**
(firehose ledger). **19 of 22 ACs approved**; every merged PR was CI-green (`test` job pass). The P0 hardening
trio (H1/H2/H3) and the live/replay seam now ship with self-tests that turn the sprint-1-era scars (S5 Node-24
flail, S6/#404 manifest drift, #331/#332/#388 JSONB crashes) into permanent regression gates.

**US-6 — FAILED (the one blocker).** AC-6.1 (deploy.yml, SHA-pinned, GHCR push) and AC-6.2
(`docker-compose.staging.yml`) are approved, but **AC-6.3, AC-6.4, AC-6.5 failed**. Root cause (Tester, run
27531582183): the deploy job SCPs `docker-compose.staging.yml` to `/root/solanatrilly/` **which does not exist
on the VPS** → SCP errors "No such file or directory" → `docker compose pull/up` never run → the smoke-test
(8002) and solanaBilly-isolation (8001) steps are never reached. The structural pytest suite is green
(deploy.yml *logic* is correct), but the **end-to-end local → GitHub → GHCR → VPS path is unverified**. A
secondary fault: deploy.yml has only a `push: main` trigger (no `workflow_dispatch`), so the orchestrator's
full-sprint deploy could not be fired — `deploy_summary` records HTTP 422 "Workflow does not have
'workflow_dispatch' trigger."

**DoD status — deploy clause NOT met.** Per retrospective A2 the deploy clause is gated at the **sprint
boundary**; because US-6 never completes a deploy, **nothing is live on port 8002** and **US-1's deploy-gated
DoD (retro A1) stays open** — the same "no VPS presence" gap as sprint-1, now two sprints running, despite the
pipeline itself existing this time.

**Goal gap:** P0 is *substantially* complete — every code/CI/seam/ledger story landed — but P0 is **not exited**
because "tested, **deployed**" is unmet. The fix is small (a `mkdir -p /root/solanatrilly` before the SCP, plus
`workflow_dispatch:`), but until a deploy actually succeeds and the smoke test returns 200, "VPS presence from
P0" remains unachieved.

**Process note (A5 not fully enforced):** stale status fields persist — `sprint2.json` reads `phase: planning`
at review; several stories carry `dev_status: not-started`/`in-progress` while `status: done` and all ACs are
dev-done; and **US-6 reads `status: done` while `tester_status: failed`** — exactly the "done ≠ failed/blocked"
inconsistency A5 flagged in sprint-1.

**Carry into sprint-3 (priority order):** **B1** fix the VPS deploy (`mkdir -p` the target dir; re-run on main;
confirm 200 + isolation) → closes US-6 AC-6.3/6.4/6.5 and retroactively US-1's DoD · **B2** add
`workflow_dispatch:` to deploy.yml · **B3** put VPS verification in the loop (a green structural test is not a
deployed stack) · **B4** enforce A5 (forbid `status: done` with a failed/blocked gate; normalize phase/dev_status
at review) · **B5** carry US-6's failed ACs as the top sprint-3 closeout before any P1 work. See
`retrospective.md` B1–B5.

**Metrics:** 6 stories committed · 5 fully DoD-done · 1 failed (US-6) · 19/22 ACs approved · all merged PRs
CI-green · 2 lint defects (US-2 AC-2.2, US-5 AC-5.2) caught by CI and fixed pre-merge · **0 deployed to VPS**.
Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-1 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-1 (1 story) | **Goal:** partially met
**Full retrospective + action items:** [`retrospective.md`](retrospective.md)

**Outcome:** US-1's 5 ACs were all implemented, merged behind green CI (PR#1–#5), and **Tester-approved**
at the AC level with 100% reported coverage (≥80% gate enforced in `ci.yml`). No critical defects. The
containerized 5-service topology (web/Daphne · db/postgres:16 · redis · celery-worker · celery-beat) is
complete and healthchecked, under Docker Rules (no host installs).

**Story-level DoD: BLOCKED** on two items (Tester, `sprint1.json`):
1. **No VPS deployment** — `deploy.yml` does not exist; the deploy trigger 404'd. The Sprint DoD requires
   "deployed to VPS staging (`-p solanatrilly`, port 8002) + smoke-tested," but the CD pipeline is **US-6**,
   which was never pulled into the sprint. US-1 was therefore structurally un-completable.
2. **`retrospective.md` did not exist** — a DoD item with no owner. Created during this review.

**Goal gap:** The P0 goal (clock seam, CI hardening H1/H2/H3, CD + hello-world live day one, firehose ledger)
spanned US-1…US-7; only **US-1** ran. "VPS presence from P0" was **not** achieved — there is no staging stack
on port 8002 yet. CI still uses `actions/checkout@v4` (tag, not SHA-pinned) → H1 outstanding.

**Metrics:** 9 cycles · 5 PRs merged · 2 CI failures (both AC-1.5, fixed in 2 iterations) · 179,050 tokens ≈
**$9.78** (PO $2.70 / Dev $5.40 / Tester $1.68 / Lead $0).

**Carry into sprint-2 (priority order):** US-6 (CD → VPS, unblocks US-1 DoD) → US-2 (DataSource/clock seam) →
US-3/4/5 (H1/H2/H3 hardening) → US-7 (firehose ledger). Decouple the per-story DoD deploy clause from
topology-only stories, and give `retrospective.md` a named owner each sprint. See `retrospective.md` A1–A6.

## Controlled Vocabulary
- **Sprint `phase`:** `planning` → `in-progress` → `review` → `done`
- **Story `status`:** `draft` → `ready` → `in-progress` → `in-review` → `done`
- **`dev_status` / `tester_status` (story & AC):** `not-started` → `in-progress` → `blocked` → `done`
- **AC `checked`:** `false` until the Tester verifies the AC against CI/DoD, then `true`
- **`priority`:** `high` | `medium` | `low`
- **Commit format:** `[US-X] Description of change`
- **Branch format:** `feature/US-X-AC-Y`
- **PR prefixes (PRD §1):** `detection:` / `tape:` / `features:` / `scoring:` / `trading:` / `dashboard:` / `ops:`

## Ownership boundaries
- **Product Owner:** owns the active sprint plan (`sprint3.json`), this board, user stories, change control. Does NOT write code.
- **Dev Team:** implements ACs in Docker on `feature/US-X-AC-Y` branches; updates only `dev_status`/`dev_notes`.
- **Tester:** flips `checked`/`tester_status`, enforces DoD, interprets CI, owns `retrospective.md` each sprint. Does NOT execute tests or edit source.
- **Project Lead:** external script; sole owner of `project-state.json`.

## Open items / human dependencies
See [`po-requests.md`](po-requests.md) — **all four sprint-1/P0 operator blockers remain RESOLVED (2026-06-14):**
the GitHub remote (`AsimQuick/solanaTrilly`), the default branch, and the CD secrets (`VPS_SSH_KEY` / `VPS_HOST` /
`VPS_USER`; GHCR via the built-in `GITHUB_TOKEN`) are all provisioned. **No open human dependencies for sprint-3.**
US-8's deploy fix (`mkdir -p` + `workflow_dispatch`) is an agent task — the VPS shell access and secrets are
already in place, so no operator action is required to land it. The three operator-only Cutover levers
(trading-wallet secret, start firehose, enable real-capital trading) remain out of scope until Cutover.

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
