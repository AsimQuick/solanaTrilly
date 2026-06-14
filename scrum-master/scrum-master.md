<!--
file: scrum-master.md
purpose: Current sprint status board + controlled vocabulary for solanaTrilly
owner: product-owner
last-updated: 2026-06-14
-->

# solanaTrilly — Scrum Master Board

## Current Sprint: sprint-1 — P0 Scaffold & Foundation
- **Phase:** planning
- **Sprint plan (source of truth):** [`sprint1.json`](sprint1.json)
- **PRD:** [`PRD.md`](PRD.md) — maps to **PRD §16 Phase P0**
- **Project state:** owned by Project Lead — `../project-state.json`

### Sprint Goal
Land the P0 foundation: a fully containerized Django 5 + DRF + Channels + Celery stack with
the `DataSource`/virtual-clock testing seam, hardened CI (H1 pinned actions, H2 task-manifest
test, H3 `json_safe` encoder), and an automated CD pipeline that deploys a hello-world
solanaTrilly to the **isolated** VPS staging stack (`-p solanatrilly`, port **8002**) — proving
the local → GitHub → GHCR → VPS path end-to-end on day one, with the firehose activation
ledger seeded.

### Why P0 now
The scaffold today is minimal (basic Django + DRF, a `web`+`db` compose with redis/celery
commented out, an unpinned `ci.yml`, no CD, no `DataSource`/clock seam, no `json_safe`, no task
manifest, no firehose ledger). Sprint-1 brings the scaffold to full P0 compliance so every later
phase (P1–P8) builds on a tested, deployed, drift-resistant base. Retrofitting the clock seam
(Principle #7) or the CD pipeline late is the expensive path the PRD explicitly warns against.

## Stories
| ID | Title | Priority | Deps | ACs | Dev | Tester |
|----|-------|----------|------|-----|-----|--------|
| US-1 | Containerized full-stack service topology (Django 5 + DRF + Channels + Celery + Redis + Postgres) | high | — | 5 | not-started | not-started |
| US-2 | `DataSource` interface + injectable virtual clock (the live/replay seam) | high | US-1 | 4 | not-started | not-started |
| US-3 | H1 — hardened, SHA-pinned CI as a hard merge gate | high | US-1 | 4 | not-started | not-started |
| US-4 | H2 — Celery task-manifest registration test | high | US-1 | 3 | not-started | not-started |
| US-5 | H3 — single `json_safe` JSONField encoder at every write site | medium | US-1 | 3 | not-started | not-started |
| US-6 | CD pipeline (GitHub Actions → GHCR → VPS staging) + hello-world live under hard isolation | high | US-1, US-3 | 5 | not-started | not-started |
| US-7 | Firehose activation ledger seeded | medium | — | 3 | not-started | not-started |

**Suggested build order:** US-1 → (US-2, US-3, US-4, US-5 in parallel) → US-6 (needs US-1 + US-3).
US-7 is doc-only and can land any time.

## Definition of Done (sprint-1)
A story is Done only when ALL of the following hold:
- All ACs verified by CI / Tester
- No critical defects
- Coverage threshold met (≥80%)
- Code file headers include metadata front matter (project convention)
- All services run in Docker (no host installs — Docker Rules)
- **Merged + deployed to the VPS solanatrilly staging stack (`-p solanatrilly`) and smoke-tested there** ("works locally" is NOT done)
- Hard isolation from live solanaBilly preserved (every docker command scoped with `-p solanatrilly`)
- `retrospective.md` updated

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
- **Product Owner:** owns `sprint1.json`, this board, user stories, change control. Does NOT write code.
- **Dev Team:** implements ACs in Docker on `feature/US-X-AC-Y` branches; updates only `dev_status`/`dev_notes`.
- **Tester:** flips `checked`/`tester_status`, enforces DoD, interprets CI. Does NOT execute tests or edit source.
- **Project Lead:** external script; sole owner of `project-state.json`.

## Open items / human dependencies
See [`po-requests.md`](po-requests.md) — **US-6 (CD → VPS) is blocked** until the GitHub remote
exists and the CD secrets (GHCR + VPS SSH) are provisioned as repo secrets.

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
