<!--
file: scrum-master.md
purpose: Current sprint status board + controlled vocabulary for solanaTrilly
owner: product-owner
last-updated: 2026-06-15
-->

# solanaTrilly — Scrum Master Board

## Current Sprint: sprint-5 — Open P3 (the Tape Recorder) + prove detection live (D4)
- **Phase:** planning
- **Sprint plan (source of truth):** [`sprint5.json`](sprint5.json)
- **PRD:** [`PRD.md`](PRD.md) — delivers **P3 (tape recorder, §6.2)**, "the heart" of the pipeline
- **Previous sprint:** [`sprint4.json`](sprint4.json) — **closed** (review complete; **P0 EXITED**, P2 detection delivered; project advanced to P3 — see Sprint-4 Review below + [`retrospective.md`](retrospective.md) action items D1–D5)
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P0/P1/P2 are closed — the VPS staging stack is live on 8002, the config core is the single source of truth, and P2 detection lands graduated `tokens` rows carrying `graduated_block_time` as the integer rel-anchor the recorder needs. Retrospective **D1** directs P3 (the PumpSwap swap-tape recorder — capture every swap from t0, Birdeye tape as the single source of truth for all signal) as the **primary deliverable**, and **D4** carries the live wiring (drive the `listener` with a concrete Birdeye source + bank the first firehose fixture). Process carries: **D2** (promote `phase`/`dev_status` *before* sprint-end deploys so the US-13 guard isn't tripped by our own staleness), **D3** (normalize story-level `dev_status` to `done` at closeout instead of exempting it), **D5** (standing rule — run the on-box check our root SSH allows before declaring a blocker "human/operator required").

### Sprint Goal
**Open P3 — the tape recorder.** Build the PumpSwap swap-tape recorder behind the `DataSource` seam + injected
clock (Principle #7): **(1)** the `swaps` table (§8) + the one `NormalizedSwap` schema (§7.1) with `rel`
anchored to the DB `Token.graduated_block_time`/`graduated_at` (never the first swap) and constrained
source/phase/side vocabularies (US-17); **(2)** the recorder **core** — port `tape_recorder.py`'s
queue/writer/window/coverage scaffolding source-agnostically, emit one `NormalizedSwap` per **landed** swap with
`owner`=the tx signer and all three units (D1), drop failed swaps, enforce the **stable** `(block_time, slot,
signature)` ordering (#403), keep the zero/degenerate-swap guard (#405) (US-18); **(3)** the append-only
daily-partitioned `jsonl.gz` lake + the queryable `swaps` writer + a truncated-tail-tolerant reader (US-19);
**(4)** recorder resilience — `seek_by_time` gap reconciliation via the **identical** call/path the backfill uses
(one code path → parity by construction) + idle-kill TTL deactivate-but-**re-attach**, config-driven (US-20);
**(5)** the **P3 offline gate** (§16) — deterministic `ReplaySource` swap-stream replay → expected swaps,
live↔backfill byte-parity on golden tokens, ordering/truncated-tail/zero-guard regression green (US-21). **Then**
**(D4)** wire a concrete Birdeye `SUBSCRIBE_TXS` source into `run_listener` on the VPS, spend the **first** of the
10 Birdeye firehose activations (logged, §15.7), prove a real graduation's swaps flow end-to-end, and **bank** the
capture as the golden-token fixture US-21 runs against offline forever (US-22).

## Stories (sprint-5 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-17 | P3 — `swaps` table + the one `NormalizedSwap` schema: tape persistence target (§8, §7.1) | high | US-14, US-5 | 4 | ready | not-started | not-started |
| US-18 | P3 — tape recorder core behind the `DataSource` seam: emission, stable ordering, owner=signer, drop-failed, zero-guard (§6.2) | high | US-17, US-2, US-15 | 4 | ready | not-started | not-started |
| US-19 | P3 — append-only `jsonl.gz` lake + queryable `swaps` writer + truncated-tail-tolerant reader (§6.2, §6.4) | high | US-18 | 3 | ready | not-started | not-started |
| US-20 | P3 — recorder resilience: `seek_by_time` gap reconcile (one code path) + idle-kill TTL re-attach, config-driven (§6.2 D4) | high | US-18, US-11 | 3 | ready | not-started | not-started |
| US-21 | P3 — the offline gate: deterministic replay + live↔backfill byte-parity on golden tokens + regression suite (§16) | high | US-18, US-19, US-20 | 3 | ready | not-started | not-started |
| US-22 | P3/D4 — wire a live Birdeye `SUBSCRIBE_TXS` source into the `listener` + spend & bank the first firehose activation (§6.2, §15.7) | high | US-16, US-18, US-21 | 3 | ready | not-started | not-started |

> **Scope:** sprint-5 commits **6 stories / 20 ACs** — the full P3 tape-recorder core (US-17…US-21) plus the
> D4 live-wiring + firehose-fixture story (US-22). Source of truth: [`sprint5.json`](sprint5.json). On P3 exit,
> the recorder captures every PumpSwap swap from t0 into the lake/`swaps` table with live↔backfill parity, and
> the project advances to **P4 (score-time snapshot + units locked, §6.3 / D1)**.

**Build order:** **US-17 first** — the persistence target everything writes to. Then the core chain is sequential:
**US-18** (recorder core) → **US-19** (lake) → **US-20** (resilience). **US-21** (offline gate) needs US-18/19/20.
**US-22** (live D4) is **last** and depends on the `listener` (US-16) + the built recorder — and the offline P3
gate (US-21) **does NOT depend on the live activation**, so an aborted/short firehose window never blocks P3 exit.

**GitHub Issues:** created at sprint-5 kickoff (2026-06-15), one per story, mirroring the prior convention —
[US-17 #77](https://github.com/AsimQuick/solanaTrilly/issues/77) ·
[US-18 #78](https://github.com/AsimQuick/solanaTrilly/issues/78) ·
[US-19 #79](https://github.com/AsimQuick/solanaTrilly/issues/79) ·
[US-20 #80](https://github.com/AsimQuick/solanaTrilly/issues/80) ·
[US-21 #81](https://github.com/AsimQuick/solanaTrilly/issues/81) ·
[US-22 #82](https://github.com/AsimQuick/solanaTrilly/issues/82).
Source of truth remains [`sprint5.json`](sprint5.json).

---

## Sprint-4 — Exit P0 (for real), Open P2 (Detection) — CLOSED (review complete)
- **Phase:** review (closed)
- **Sprint plan (source of truth):** [`sprint4.json`](sprint4.json)
- **PRD:** [`PRD.md`](PRD.md) — finally exits **PRD §16 P0** (the GREEN VPS deploy) and delivers **P2 (detection, §6.1)**
- **Previous sprint:** [`sprint3.json`](sprint3.json) — **closed** (review complete; P1 config core delivered in code, US-8 deploy failed; see Sprint-3 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P1 (config core) is done; P0 is **still not exited** for a third consecutive sprint — the Sprint-3 deploy smoke-test failed with curl exit 7 on all 13 runs. The Sprint-3 review's caveat is the key lever: **the "firewall" diagnosis was never verified on the box, and CLAUDE.md grants agents root SSH** — so the on-box diagnosis *and* the fix (firewall `ufw allow 8002/tcp` **or** a compose port-bind correction) are agent tasks, not an operator blocker. Sprint-4 actions retrospective **C1** (diagnose on the VPS), **C2** (fix + green deploy + exit P0), **C3** (runtime smoke-test retry), **C4** (programmatic status-integrity CI guard), and **C6** (pull P2 detection in alongside the P0 closeout).

### Sprint Goal
Two halves, one milestone — **exit P0, open P2.** **(1) Close P0 (4th attempt):** SSH to the VPS and
diagnose port-8002 *on the box* (`curl localhost:8002/health`, `docker compose -p solanatrilly ps`,
compose port-bind, `ufw`/`iptables`) to separate a firewall block from a port-publish/bind bug; apply the
fix as root; make the CD smoke-test retry with backoff **at runtime**; run the deploy **green on `main`**
and verify **HTTP 200 on 8002 with solanaBilly untouched on 8001** — retroactively closing US-8
AC-8.3/8.4/8.5, US-6, and US-1's DoD, and finally **exiting P0** (US-12). Add a **programmatic
status-integrity CI guard** on `sprintN.json` (US-13). **(2) Open P2 (detection, PRD §6.1):** the `tokens`
model (US-14); a Birdeye `SUBSCRIBE_MEME` detection consumer **behind the `DataSource` seam + injected
clock** that creates `tokens` rows from the active config's detection filter, dedupes within
`dedupe_window_s`, and pre-stages near-graduation mints — **offline-gated by replaying a MEME stream →
expected token rows** (US-15); and detection resilience — the Helius `migrate` reconciler backstop (D4) +
a Birdeye REST sweep + a dedicated `listener` container (US-16).

## Stories (sprint-4 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-12 | P0 closeout (4th attempt) — diagnose+fix VPS port-8002 on the box, GREEN smoke-test, exit P0 (closes US-8 8.3/8.4/8.5 + US-6 + US-1 DoD) | high | US-8 | 5 | done | done | **approved** |
| US-13 | Process guard — programmatic status-integrity check on `sprintN.json` in CI (C4/A5/B4) | high | US-1 | 3 | done | done | **approved** |
| US-14 | P2 — the `tokens` model: graduated-token persistence target (§8) | high | US-1, US-5 | 3 | done | done | **approved** |
| US-15 | P2 — Birdeye `SUBSCRIBE_MEME` detection consumer behind the `DataSource` seam → `tokens` rows (offline gate) | high | US-14, US-11, US-2 | 4 | done | done | **approved** |
| US-16 | P2 — detection resilience: Helius `migrate` reconciler + Birdeye REST sweep + dedicated `listener` container | high | US-14, US-15 | 3 | done | done | **approved** |

> **Scope:** sprint-4 commits **5 stories / 18 ACs** — the P0 deploy closeout (US-12) + a process guard
> (US-13) + the P2 detection core (US-14…US-16). Source of truth: [`sprint4.json`](sprint4.json). **Review
> outcome: all 5 stories Tester-approved (18/18 ACs); P0 is EXITED** — the staging stack answers 200 on 8002,
> solanaBilly untouched on 8001 — and US-1's deploy-gated DoD closes retroactively; **P2 (detection) is
> delivered** and the project advances to **P3 (tape recorder)**. See the Sprint-4 Review below +
> [`retrospective.md`](retrospective.md) (action items D1–D5).

> **Status integrity note (the recurring A5/B4/C4 artifact):** `sprint4.json` still records story-level
> `dev_status: not-started` on all five completed stories while every AC is `dev_status: done` +
> `tester_status: approved`. US-13's CI guard exempts accepted stories (`--skip-complete` / story-done
> carve-out) so this no longer fails CI, but the field should be **promoted to `done` at closeout**, not
> exempted — carried as **D3**. The board above reflects the normalized (`done`/`approved`) state.

**Build order:** **US-12 first** — the gating P0 closeout (retro C2/B5); it is independent of the P2 chain
and may run in parallel. **US-13** (process guard) is independent. The P2 chain is sequential: **US-14**
(`tokens` model) → **US-15** (detection consumer + offline gate, needs the model + resolver + DataSource
seam) → **US-16** (reconciler + sweep + listener container, needs the consumer).

**GitHub Issues:** created at sprint-4 kickoff (2026-06-15), one per story, mirroring the prior convention —
[US-12 #54](https://github.com/AsimQuick/solanaTrilly/issues/54) ·
[US-13 #55](https://github.com/AsimQuick/solanaTrilly/issues/55) ·
[US-14 #56](https://github.com/AsimQuick/solanaTrilly/issues/56) ·
[US-15 #57](https://github.com/AsimQuick/solanaTrilly/issues/57) ·
[US-16 #58](https://github.com/AsimQuick/solanaTrilly/issues/58).
Source of truth remains [`sprint4.json`](sprint4.json).

---

## Sprint-3 — Exit P0, Open P1 (Config Core) — CLOSED (review complete)
- **Phase:** review (closed)
- **Sprint plan (source of truth):** [`sprint3.json`](sprint3.json)
- **PRD:** [`PRD.md`](PRD.md) — closes the last of **PRD §16 P0** (the CD deploy) and delivers **P1 (config core, §5)**
- **Previous sprint:** [`sprint2.json`](sprint2.json) — **closed** (review complete; 5/6 stories DoD-done, US-6 failed; see Sprint-2 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`
- **Review outcome:** P1 config core (US-9/10/11) **delivered in code, CI-green**; **P0 not exited** — US-8's VPS smoke-test fails (curl exit 7 on all 13 Deploy runs), so nothing is verified-live on 8002. Sprint **blocked** at the VPS DoD gate. See Sprint-3 Review below + [`retrospective.md`](retrospective.md) (action items C1–C6) → carried into sprint-4.

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
| US-8 | P0 closeout — CD deploy lands on the isolated VPS staging stack (closes US-6 6.3/6.4/6.5 + US-1 DoD) | high | US-6 | 5 | in-review | done | **fail** (AC-8.3/8.4/8.5) |
| US-9 | P1 — `PipelineConfig` model: versioned, audited, admin-editable + `pipeline_state` singleton | high | US-1 | 4 | in-review | done | partial (code pass; deploy gate) |
| US-10 | P1 — typed Pydantic v2 schema enforcing the save-time invariants (§5.2) | high | US-9 | 4 | in-review | done | partial (code pass; deploy gate) |
| US-11 | P1 — the config resolver: single cached `get_active_config()` + atomic activation/rollback | high | US-9, US-10 | 3 | in-review | done | partial (code pass; deploy gate) |

> **Status integrity note (retrospective B4 → C4):** `sprint3.json` still records `US-8 status: done` while `tester_status: fail`, and `dev_status: not-started` on completed stories — the same "done ≠ failed/blocked" inconsistency flagged in sprint-1 (A5) and sprint-2 (B4). The board above reflects the *normalized* state (US-8 `in-review`/`fail`, US-9/10/11 `in-review`/`partial`); the `sprintN.json` normalization is owned by PO / Project Lead and is carried as **C4**.

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

## Sprint-4 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-12 + US-13 + US-14/US-15/US-16 (5 stories, 18 ACs) | **Goal:** **fully met — P0 EXITED, P2 delivered**
**Full retrospective + action items (D1–D5):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 5 stories are fully Tester-approved — 18/18 ACs green across 18 merged PRs.** After three sprints of P0-deploy drag,
**P0 IS EXITED**: the isolated staging stack answers **HTTP 200 on port 8002** with solanaBilly untouched on **8001**, confirmed by the Tester
from an actual green deploy run (run 27555701056). **US-1's deploy-gated DoD (retro A1, open since sprint-1), US-6, and US-8 AC-8.3/8.4/8.5
all close retroactively.** **P2 (detection) is delivered** and the project advances to **P3 (tape recorder)**.

**US-12 — P0 closeout, the on-box diagnosis cracked it (C1/C2/C3).** SSH'ing to the VPS (root, per CLAUDE.md) **falsified the three-sprint
"firewall" story**: no ufw, iptables INPUT ACCEPT-all, `0.0.0.0:8002->8000/tcp` correctly published, HTTP 200 both locally and externally. The
real root cause was a **smoke-test timing race** — the curl fired immediately after `docker compose up -d`, hitting the container mid-startup
(migrations before daphne binds). The fix is runtime retry-with-backoff (AC-12.3, `SMOKE_MAX_ATTEMPTS`/`SMOKE_RETRY_DELAY`), with the structural
test upgraded to assert *runtime* loop/sleep/max-attempts behavior in the parsed YAML — closing the sprint-2/3 B3 "green test ≠ live runtime" gap.
The "human escalation required / agents lack firewall access" claim that gated three sprints was simply **wrong**.

**US-13 — the status-integrity guard proved itself live (C4).** `tools/sprint_integrity_check.py` runs in CI on every PR over all `sprint*.json`,
forbidding `done`+`failed/blocked` and flagging stale `dev_status`/`phase`. It **immediately caught a real violation**: the orchestrator's
sprint-end re-deploys (runs 27555736146, 27555822147) failed at the integrity step because `sprint4.json` still read `phase:'planning'` while
every story was done. The "done ≠ failed/blocked" class logged-and-unactioned in A5/B4/C4 is now a permanent, self-enforcing gate.

**US-14/15/16 — P2 detection, behind the live/replay seam, budget preserved.** US-14 ships the `tokens` model (PK `mint`, `graduated_at` t0,
`graduated_block_time` integer rel-anchor for the P3 recorder, constrained status vocabulary, `JsonSafeEncoder` raw lake, admin surface). US-15's
consumer reads MEME events from a `DataSource` + injected clock (US-2 static-analysis guard still green), writes `tokens` rows from the
`get_active_config()` detection filter (Principle #1 — no constant/`os.getenv`), dedupes within `dedupe_window_s`, and pre-stages near-graduation
mints — with a **deterministic offline replay gate** on a synthetic MEME stream (run twice → identical rows). US-16 adds three-belt resilience:
the Helius `migrate` reconciler (recovers a mint a dropped MEME event missed, exactly once via `get_or_create`), a registered Birdeye REST
graduation sweep (Celery-beat, H2 manifest-guarded), and a dedicated `listener` container in both compose files, up on the VPS (#289 lesson).
**No firehose activation was spent** — the offline gate is synthetic, so 10 Birdeye + 10 Helius remain banked.

**Defects:** only 3 trivial lint slips (ruff E741 AC-12.5 ambiguous `l`; F541 AC-15.2 empty f-string; E501 AC-15.4 long header) — each caught by
Tester diagnosis and fixed same-day in one iteration. None reached pytest or production. Coverage ≥80% enforced throughout.

**Process note (the recurring artifact, now caught by the guard).** Story-level `dev_status` still reads `not-started` on all five done stories;
US-13's `--skip-complete`/story-done carve-out exempts accepted stories from CI rather than normalizing the field — promote to `done` at closeout
(D3). The sprint-end deploy failures were a *process* slip (orchestrator didn't update `phase` before triggering) that the new guard correctly
caught — fix at the source (D2). Normalized in this review pass (`phase` → `review`).

**Carry into sprint-5 (priority order):** **D1** open **P3 (tape recorder)** — the `graduated_block_time` rel-anchor is in place · **D2** update
`phase`/`dev_status` *before* sprint-end deploys so the US-13 guard isn't tripped by our own staleness · **D3** normalize story-level `dev_status`
at closeout instead of exempting it · **D4** wire a live Birdeye/Helius source to the `listener` and bank a firehose fixture (prove detection live,
not just offline) · **D5** bank the "verify on the box before escalating to human-required" rule. See `retrospective.md` D1–D5.

**Metrics:** 5 stories committed · **5 fully DoD-done** · **18/18 ACs Tester-approved** · 18 PRs merged, all CI-green · post-merge deploy **GREEN**
(run 27555701056) — **first verified-live VPS presence** (200 on 8002, isolation on 8001) · two later sprint-end re-deploys failed at the US-13
integrity step on stale `phase` (process slip, not a code regression) · 3 trivial lint defects caught and fixed same-day · **0 firehose activations
spent** (10 Birdeye + 10 Helius banked) · ~430+ tests passing · coverage ≥80%. Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-3 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-8 + US-9/US-10/US-11 (4 stories, 16 ACs) | **Goal:** half met — P1 opened, P0 not closed
**Full retrospective + action items (C1–C6):** [`retrospective.md`](retrospective.md)

**Outcome:** All **16 ACs are implemented and CI-green** across 16 merged PRs (#38–#53), and the **P1 config core is fully delivered in code**:
**US-9** (`PipelineConfig` model + django-simple-history audit + Django admin + `pipeline_state` singleton), **US-10** (typed Pydantic v2
schema enforcing every §5.2 invariant — leak guard, D4, `capture_buffer_s ≥ 3`, id22 `adaptive_topk`-only gate, D2 feature-contract subset
— as **write-path** rejection gates), and **US-11** (single cached `get_active_config()` resolver + atomic activation/rollback + an AST
no-silent-auto-start guard). ~290 tests, coverage ≥80%. This is the config-driven source of truth (Principle #1) that P2–P8 read from.

**US-8 — FAILED (P0 not exited).** AC-8.1 (`mkdir -p /root/solanatrilly` before SCP) and AC-8.2 (`workflow_dispatch`, resolving the HTTP 422)
are **PASS** — sprint-2's SCP blocker is closed and **the deploy now actually deploys**: SSH connects, the GHCR image pulls, `docker compose
-p solanatrilly … pull && up -d` runs, and the web container **starts on the VPS**. But **AC-8.3/8.4/8.5 FAIL**: the CD smoke-test (a curl
from the GitHub runner to `http://VPS:8002/health/`) returns **curl exit code 7** on all **13** Deploy runs, so 200-on-8002 is never proven,
the isolation step (gated behind the smoke-test) never runs, and the Tester cannot confirm P0 exit. US-9/10/11 are therefore rated **partial**
(code correct, CI-green, containers deploy — only the sprint-boundary deploy DoD remains).

**DoD status — deploy clause NOT met (third sprint).** Per A2 the deploy clause is gated at the sprint boundary; because the smoke-test never
returns 200, **nothing is verified-live on port 8002** and **US-1's deploy-gated DoD (A1) stays open** — the same "no VPS presence" gap as
sprints 1 and 2, but one layer deeper each time (no pipeline → SCP fails → container starts but port unreachable).

**Diagnosis caveat (B3 not truly in the loop).** curl exit 7 was attributed to a closed port-8002 firewall, but **no one SSH'd to the VPS to
`curl localhost:8002` and rule out a port-publish/bind bug** in `docker-compose.staging.yml`. Firewall (operator action) vs. port-mapping
(code fix) have different remedies; agents have VPS SSH per CLAUDE.md, so the on-box check is owed before escalating. Also: AC-8.5's structural
test asserts a smoke-test retry loop, yet the most recent run shows curl failing immediately with **no retry** — a green test masking a
divergent runtime, the exact sprint-2 B3 lesson recurring.

**Process note (B4 not enforced — third time).** `sprint3.json` reads `US-8 status: done` while `tester_status: fail`, `dev_status:
not-started` on completed stories, and `phase: planning`-era staleness — the "done ≠ failed/blocked" inconsistency A5/B4 flagged twice.
Also, "No open human dependencies for sprint-3" (asserted at kickoff) proved false: a probable operator firewall action emerged at review.

**Carry into sprint-4 (priority order):** **C1** diagnose port-8002 *on the VPS* (curl localhost, `docker compose -p solanatrilly ps`, check
the compose port publish/bind, inspect `ufw`/`iptables`) to separate operator-fix from code-fix → **C2** apply it, re-run the deploy, confirm
200 on 8002 + isolation on 8001 (closes US-8/US-6 and retroactively US-1's DoD; exits P0 — fourth attempt) · **C3** make the smoke-test
actually retry with backoff and have the structural test verify runtime, not file text · **C4** enforce status integrity programmatically
(forbid `done` + `failed`/`blocked`; normalize fields; JSON-lint in CI) · **C5** reopen the human-dependency line in `po-requests.md` if C1
confirms a firewall · **C6** don't let the deploy drag stall throughput — pull **P2 (detection)** into sprint-4 alongside the P0 closeout.
See `retrospective.md` C1–C6.

**Metrics:** 4 stories committed · 0 fully DoD-done · 3 code-complete/CI-green but Tester-`partial` (US-9/10/11) · 1 failed (US-8 deploy ACs) ·
16/16 ACs CI-green / 13 Tester-pass / 3 fail (US-8 AC-8.3/8.4/8.5) · 16 PRs merged (#38–#53), all CI-green · 13 Deploy runs, **0 passed
smoke-test** (curl exit 7) · 2 lint defects (US-8 AC-8.3 F841; US-9 AC-9.3 I001) caught by CI and fixed pre-merge · ~290 tests passing ·
**0 verified-deployed to VPS** (third sprint). Token/cost spend: see `../project-state.json` (Project Lead).

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
- **Product Owner:** owns the active sprint plan (`sprint4.json`), this board, user stories, change control. Does NOT write code.
- **Dev Team:** implements ACs in Docker on `feature/US-X-AC-Y` branches; updates only `dev_status`/`dev_notes`.
- **Tester:** flips `checked`/`tester_status`, enforces DoD, interprets CI, owns `retrospective.md` each sprint. Does NOT execute tests or edit source.
- **Project Lead:** external script; sole owner of `project-state.json`.

## Open items / human dependencies
See [`po-requests.md`](po-requests.md) — **all four sprint-1/P0 operator blockers remain RESOLVED (2026-06-14):**
the GitHub remote (`AsimQuick/solanaTrilly`), the default branch, and the CD secrets (`VPS_SSH_KEY` / `VPS_HOST` /
`VPS_USER`; GHCR via the built-in `GITHUB_TOKEN`) are all provisioned. **No open human dependencies for sprint-5** —
P3 (the tape recorder) is agent-buildable end-to-end, and US-22/D4's single Birdeye `SUBSCRIBE_TXS` firehose
activation is an agent task (keys in `.env`, logged in `ops/firehose_activation_log.md` per §15.7), not an operator
blocker.
The sprint-3 "operator firewall" suspicion was **falsified** by US-12's on-box diagnosis (C1): port 8002 was always
reachable; the deploy failure was a code-side smoke-test timing race that agents fixed (no operator action). The
three operator-only Cutover levers (trading-wallet secret, start firehose, enable real-capital trading) remain out
of scope until Cutover — though **D4** (wire a live source to the `listener` + bank a firehose fixture) will spend
the first of the 10 Birdeye / 10 Helius activations, logged in `ops/firehose_activation_log.md` per §15.7.

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
