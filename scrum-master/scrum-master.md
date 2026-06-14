<!--
file: scrum-master.md
purpose: Current sprint status board + controlled vocabulary for solanaTrilly
owner: product-owner
last-updated: 2026-06-15
-->

# solanaTrilly — Scrum Master Board

## Current Sprint: sprint-2 — Complete the P0 Foundation
- **Phase:** planning
- **Sprint plan (source of truth):** [`sprint2.json`](sprint2.json)
- **PRD:** [`PRD.md`](PRD.md) — maps to **PRD §16 Phase P0** (the remainder)
- **Previous sprint:** [`sprint1.json`](sprint1.json) — **closed** (review complete; see Sprint-1 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`

### Sprint Goal
Finish the P0 foundation by landing the six remaining P0 stories (US-2…US-7): the
`DataSource`/virtual-clock testing seam, hardened CI (H1 pinned actions, H2 task-manifest
test, H3 `json_safe` encoder), an automated CD pipeline that deploys a hello-world
solanaTrilly to the **isolated** VPS staging stack (`-p solanatrilly`, port **8002**) —
proving the local → GitHub → GHCR → VPS path end-to-end and **retroactively closing US-1's
deploy-gated DoD** — with the firehose activation ledger seeded. Exit P0 with a tested,
deployed, drift-resistant base ready for **P1 (config core)**.

### Why finish P0 now
Sprint-1 delivered only the containerized topology (US-1); the rest of the P0 scaffold is still
missing — no CD pipeline (so nothing is on the VPS), no `DataSource`/clock replay seam, an
unpinned `ci.yml` (H1 outstanding — CI still uses `actions/checkout@v4`, a tag), no task-manifest
test (H2), no `json_safe` encoder (H3), no firehose ledger. Sprint-2 brings the scaffold to full
P0 compliance so every later phase (P1–P8) builds on a tested, deployed, drift-resistant base.
Retrofitting the clock seam (Principle #7) or the CD pipeline late is the expensive path the PRD
explicitly warns against. This sprint also actions the retrospective fixes (A1–A6): CD first to
unblock US-1, deploy gated at the sprint boundary (A2), and `retrospective.md` owned each sprint (A3).

## Stories (sprint-2 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-2 | `DataSource` interface + injectable virtual clock (the live/replay seam) | high | US-1 | 4 | ready | not-started | not-started |
| US-3 | H1 — hardened, SHA-pinned CI as a hard merge gate | high | US-1 | 4 | ready | not-started | not-started |
| US-4 | H2 — Celery task-manifest registration test | high | US-1 | 3 | ready | not-started | not-started |
| US-5 | H3 — single `json_safe` JSONField encoder at every write site | medium | US-1 | 3 | ready | not-started | not-started |
| US-6 | CD pipeline (GitHub Actions → GHCR → VPS staging) + hello-world live under hard isolation | high | US-1, US-3 | 5 | ready | not-started | not-started |
| US-7 | Firehose activation ledger seeded | medium | — | 3 | ready | not-started | not-started |

> **Scope:** sprint-2 commits the **six remaining P0 stories** (US-2…US-7). Source of truth: [`sprint2.json`](sprint2.json).
> US-1 (containerized topology) landed in sprint-1 — its 5 ACs are Tester-approved; its only open item is the
> VPS deploy, which **US-6 closes** in this sprint. After sprint-2, P0 is complete and the project advances to
> **P1 (PRD §16 — config core)**.

**Build order:** (US-2, US-3, US-4, US-5 in parallel — all depend only on US-1) → **US-6** (needs US-1 + US-3;
top priority — it unblocks US-1's DoD and delivers "VPS presence from P0"). **US-7** is doc-only, lands any time.

**GitHub Issues (created 2026-06-15):** [US-2 → #6](https://github.com/AsimQuick/solanaTrilly/issues/6) ·
[US-3 → #7](https://github.com/AsimQuick/solanaTrilly/issues/7) · [US-4 → #8](https://github.com/AsimQuick/solanaTrilly/issues/8) ·
[US-5 → #9](https://github.com/AsimQuick/solanaTrilly/issues/9) · [US-6 → #10](https://github.com/AsimQuick/solanaTrilly/issues/10) ·
[US-7 → #11](https://github.com/AsimQuick/solanaTrilly/issues/11). Source of truth remains [`sprint2.json`](sprint2.json).

## Definition of Done (sprint-2)
A story is Done only when ALL of the following hold:
- All ACs verified by CI / Tester
- No critical defects
- Coverage threshold met (≥80%)
- Code file headers include metadata front matter (project convention)
- All services run in Docker (no host installs — Docker Rules)
- **CD pipeline (US-6) delivered; once live, every story is merged + deployed to the VPS solanatrilly staging stack (`-p solanatrilly`, port 8002) and smoke-tested there** ("works locally" is NOT done). **Per retrospective A2, the deploy clause is gated at the _sprint_ boundary** — a story landing before US-6 is not blocked by the absent pipeline; it deploys once US-6 exists.
- Hard isolation from live solanaBilly preserved (every docker command scoped with `-p solanatrilly`)
- `retrospective.md` updated for sprint-2 — **named owner: Tester / scrum facilitator** (retrospective A3)

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
- **Product Owner:** owns the active sprint plan (`sprint2.json`), this board, user stories, change control. Does NOT write code.
- **Dev Team:** implements ACs in Docker on `feature/US-X-AC-Y` branches; updates only `dev_status`/`dev_notes`.
- **Tester:** flips `checked`/`tester_status`, enforces DoD, interprets CI, owns `retrospective.md` each sprint. Does NOT execute tests or edit source.
- **Project Lead:** external script; sole owner of `project-state.json`.

## Open items / human dependencies
See [`po-requests.md`](po-requests.md) — **all four sprint-1/P0 blockers are RESOLVED (2026-06-14):** the
GitHub remote (`AsimQuick/solanaTrilly`), the default branch, and the CD secrets (`VPS_SSH_KEY` / `VPS_HOST` /
`VPS_USER`; GHCR via the built-in `GITHUB_TOKEN`) are all provisioned. **US-6 (CD → VPS) is unblocked** and is
the top-priority story in sprint-2. No open human dependencies for sprint-2. The three operator-only Cutover
levers (trading-wallet secret, start firehose, enable real-capital trading) remain out of scope until Cutover.

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
