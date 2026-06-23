# Sprint 15 — CLEAN RESET

**Phase:** planning
**Progress:** 0/13 stories | 0/39 ACs
**Last Updated:** 2026-06-23

## Sprint Goal
Replace the failed **hotfix-PR style** (half-applied fixes, untraceable landings, fake/inflated dashboard PnL) with **structured user stories + a Tester quality gate + a Dev-Team loop**, then **fix-validate-LOCALLY-then-PURGE** the VPS and deploy **ONLY two artifacts** — model `models/trilly_pregrad_v6`, copy formula `models/copy_2026-06-22_curvestage` — **observe-only, $25, `trading_enabled=FALSE` (never flipped)**.

**LOCKED SEQUENCE = fix-validate-then-purge:**
- **Phase A** — salvage the VPS-only judge/diagnostic probes BEFORE any purge + stand up a LOCAL validation harness over the Jun 20–23 firehose tapes.
- **Phase B** — develop + **PROVE every COPY and MODEL fix LOCALLY** against those tapes FIRST.
- **Phase C** — ONE clean VPS purge of all old models/formulas/positions/half-applied fixes, then deploy the two artifacts with fixes **already baked in**, with a **FRESH HONEST dashboard**.
- **Phase D** — observe soak, judged **EXCLUSIVELY by firehose-reconstructed real fills via `fill_repricing.py`** — NEVER the dashboard number.

**Hard constraints (LOCKED — do not re-litigate):**
- **NO CREDITS** — Helius/Birdeye/Dune OFF for the entire sprint. All testing uses the LOCAL Jun 20–23 tapes only (already pulled, byte-exact vs VPS, at `/Users/asim/NoIcloud/solanatrills/lake/firehose/dt=2026-06-2{0,1,2,3}/part-0.jsonl.gz`). The firehose budget (6 Birdeye + 6 Helius) is UNTOUCHED. Dune is 402/out-of-credits on both keys. REAFFIRMED across ALL stories: any AC implying a credit spend is redesigned around the local Jun 20–23 tapes + the local fixtures already on hand (`lake/golden/*`, `/Users/asim/NoIcloud/solanabilly3/data/graduated/seed_recent.csv`, recorder logs/frames), or explicitly DEFERRED. Vendor escalation requires explicit operator re-authorization.
- **FREE GRADUATION LABELS** — the firehose captures BOTH bonding-curve (`phase:pre`) AND post-grad (`phase:post`) swaps (Jun-22 alone has ~38k post rows, `rel`=seconds-since-grad 0–2918s; earlier "post not captured / only ~13 grads" claims were WRONG). The offline graduation LABEL = cumulative buy `vol_sol` ≥ ~85 SOL computed from the PRE rows. Zero-credit, full-population, for the whole window.
- **DOLLAR BASIS (per-phase)** — `vol_usd` is ALL ZERO on **PRE rows only** (the bug is pre-only); PRE dollars (curve_frac, the $250 filter) derive from `vol_sol × SOL_price` (~$84/SOL), NEVER `vol_usd`. **POST rows already carry a populated `vol_usd`.**
- **CRITICAL GAP (US-92)** — POST rows carry **NO `mint`/pool/pair** (keys: `block_time, slot, signature, rel, price, side, vol, vol_usd, owner, phase`), so post-grad fills are **UNATTRIBUTABLE to a token offline**. The recorder must emit `mint` on every post row, else the live recorder can NEVER self-validate model PnL or the copy ride-to-grad exit via `fill_repricing.py`. **US-92 gates US-89 (deploy) + US-91 (soak).**
- **BAD LINES** — ~2–10%/day are genuinely unparseable partial writes → skip-and-count-and-flag, never fatal. NOTE: `phase:post` rows are **VALID** (different schema, no `mint`/`vol_sol`), NOT corruption — count them as `post`, never as bad lines.
- **MODEL TRACK = OBSERVE + PARITY-ONLY** — ship v6 observe-only; validate ONLY feature/scoring PARITY locally (live `entry_features` vs lab parquets). v6 post-grad PnL is OUT OF SCOPE (a separate Birdeye backtest agent owns it, already done Jun 20–21; the live recorder confirms later).
- **SAFETY GATE** — `trading_enabled` DEFAULT False, NEVER flipped; ZERO real orders / ZERO capital; the live RPC/Sender send boundary present but NEVER invoked (AST-guarded); §5 copy-trade isolation PRESERVED; solanaBilly UNTOUCHED on 8001; every docker op scoped `-p solanatrilly`.
- **ANTI-HOTFIX MECHANISM** — every Phase-A/B story's DoD includes a **reproducible local validation** (against the Jun 20–23 tapes or a golden/regression fixture) that **PROVES the fix landed**. No story is "done" on "works locally" hand-waving. The local-proof gate is what **unblocks the Phase-C purge**.

## Reference Documents
- `scrum-master/prd.md`
- `scrum-master/solanatrilly_copytrade_SPEC.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/EPIC-copy-curvestage-integration.md`
- `scrum-master/EPIC-copy-paper-fill-repricing.md`
- `scrum-master/EPIC-copy-capital-path-wiring.md`
- `scrum-master/EPIC-graduation-migrate-detection.md`
- `scrum-master/EPIC-graduation-mint-extraction-fix.md`
- `scrum-master/EPIC-model-track-deploy.md`
- `scrum-master/EPIC-model-paper-pnl-truncation-floor.md`
- `scrum-master/EPIC-tape-sourcing-escalation.md`
- `CLAUDE.md`
- `ops/firehose_activation_log.md`
- `/Users/asim/NoIcloud/solanatrills/lake/firehose/MANIFEST.md`
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v6/MODEL_HANDOFF.md`
- `/Users/asim/NoIcloud/solanatrills/models/copy_2026-06-22_curvestage/HANDOFF.md`

## Definition of Done (sprint-level — see `sprint15.json` for the full list)
- [ ] **ANTI-HOTFIX LOCAL-PROOF GATE:** every Phase-A/B story ships a reproducible, committed local validation (pytest test / pinned-output script / golden fixture) over the Jun 20–23 tapes that PROVES the fix landed. Every Phase-B local-proof GREEN BEFORE the Phase-C purge is authorized.
- [ ] All ACs verified by CI / Tester; no critical defects; coverage ≥80%; metadata front matter on all new files.
- [ ] **NO CREDITS:** Helius/Birdeye/Dune OFF; firehose budget untouched; no new activation-log entries. Any "needs Birdeye/Dune" task re-designed to local tapes + free grad label, or DEFERRED.
- [ ] **DOLLAR-BASIS DISCIPLINE:** all dollars via `vol_sol × SOL_price` (pinned, config-driven); `vol_usd` never read for a dollar value.
- [ ] **FREE-LABEL DISCIPLINE:** graduation ground truth = cum buy `vol_sol ≥ ~85 SOL` (single named constant).
- [ ] **BAD-LINE DISCIPLINE:** parser skips-counts-flags malformed lines; never fatal, never silently dropped.
- [ ] All services in Docker; both compose files; `-p solanatrilly`-scoped; Vite inside the frontend container.
- [ ] CD pipeline carries the Phase-C/D deploy (GitHub Actions → GHCR → VPS; VPS never hand-edited); smoke-tested on staging (web 8002 / dashboard 8003).
- [ ] VPS verification IN THE LOOP for Phase C/D: green deploy run shows ONLY v6 + curvestage, HTTP 200 on 8002, dashboard on 8003, engines Up, solanaBilly UNTOUCHED on 8001.
- [ ] **OBSERVE-ONLY SAFETY GATE:** `trading_enabled` DEFAULT False, NEVER flipped; $25 observe; ZERO orders/capital; live send boundary never invoked (AST guard GREEN).
- [ ] §5 copy-trade isolation PRESERVED; solanaBilly isolation preserved (purge uses ONLY `-p solanatrilly` scope — no unscoped down/force-recreate/prune/volume-removal).
- [ ] **JUDGE BY `fill_repricing.py`, NEVER THE DASHBOARD:** all PnL/edge judgment grounded in firehose-reconstructed real fills; the fresh dashboard PnL cell wired to `fill_repricing.py` (a prettier version of the old lie is a sprint FAILURE).
- [ ] Config-driven knobs (SOL-price basis, grad threshold, recalibrated classifier threshold, kill-switch bounds).
- [ ] Parity by construction (classifier + v6 score the same live pipeline the lab parquets validate; dashboard reads existing rows, no new math).
- [ ] US-13 status-integrity guard GREEN on `sprint15.json`; mechanical phase-promotion before sprint-end deploy.
- [ ] `retrospective.md` updated for sprint-15 incl. a Change Requests entry recording the clean-reset decision; re-index via `mcp__devrag__reindex_document`.

## Stories (sprint-15 — committed scope)

| ID | Phase | Title | Priority | Deps | ACs | Status |
|----|-------|-------|----------|------|-----|--------|
| US-80 | A | SALVAGE VPS-only probes (`gate_fidelity.py`, `resettle.py`) into the repo BEFORE purge + confirm `fill_repricing.py` canonical | critical | — | 3 | draft |
| US-81 | A | LOCAL validation harness over Jun 20–23 tapes: skip-and-count parser + FREE grad labeler (cum vol_sol ≥ ~85) + `vol_sol × SOL_price` dollar basis | critical | — | 3 | draft |
| US-82 | B-copy | Classifier calibration/parity: G2 parity table (live `entry_features` vs lab parquets), find P(grad) inflation, recalibrate gate-3 to LIVE top-25% (prove selective) | critical | US-81 | 3 | draft |
| US-83 | B-copy | Settler honesty: graduation from GROUND TRUTH (live = corrected on-chain detector US-86; offline = cum-vol_sol≥85) — stop false TIMER/−100% on grads | high | US-81, US-86 | 3 | draft |
| US-84 | B-copy | Wire the weekly retrain on the recorder's OWN on-curve tapes (date-gated no-op until ≥7 days; don't block) | medium | US-81, US-82 | 3 | draft |
| US-85 | B-copy | Re-soak validation harness + kill-switch (selected grad-rate < 40% over ≥15 trades); judge by honest-fill re-settle, NEVER the dashboard | high | US-82, US-83 | 3 | draft |
| US-86 | B-model | FOUNDATIONAL: decide the graduation detector by MATCH-RATE vs completeevent ground truth (`seed_recent.csv`, FREE/local) — score BOTH candidates (substring vs MigrateV2+CreatePool) offline over local fixtures, ship the higher precision/recall; kills the near-zero post-grad tagging | critical | — | 3 | draft |
| US-87 | B-model | Confirm v6 stack wired (v4 SELECTION × v5 EXIT × depth-gated SIZING @ $25 flat) + banks loaded; PROVE feature/scoring PARITY locally (NOT post-grad PnL) | high | US-81 | 3 | draft |
| US-88 | B-model | Carry-forward verification: prove #386 (truncation+floor) and #381/#382 (post-grad entry-tape recovery) survive INTACT into the clean deploy | high | US-87 | 3 | draft |
| US-92 | B-recorder | CRITICAL: post-grad firehose rows must carry the token MINT — recorder writes post rows with NO mint, so post-grad fills are unattributable offline and the live recorder can NEVER self-validate model PnL / copy ride-to-grad exit | critical | US-81 | 3 | draft |
| US-89 | C | ONE clean VPS purge (all old models/formulas/positions/half-applied fixes) then deploy ONLY v6 + curvestage via CD, observe-only/$25/`trading_enabled=FALSE` | critical | US-80…US-88, US-92 | 3 | draft |
| US-90 | C | Fresh HONEST dashboard: fix click-to-copy + pagination (required) + WIRE PnL to `fill_repricing.py` (required); UTC→Dubai time OPTIONAL/nice-to-have | high | US-80, US-89 | 3 | draft |
| US-91 | D | Observe soak both tracks ($25, `trading_enabled=FALSE`), judged EXCLUSIVELY by `fill_repricing.py`; define soak exit criteria + kill-switches | high | US-89, US-90, US-92 | 3 | draft |

> **Scope:** sprint-15 commits **13 stories / 39 ACs** — a deliberately larger "clean reset" sprint, but strictly **sequenced A→B→C→D** with a hard local-proof gate before the purge. Source of truth: [`sprint15.json`](sprint15.json). **NO CREDITS — the firehose budget (6 Birdeye + 6 Helius) is UNTOUCHED; all testing is offline against the local Jun 20–23 tapes + the free cum-vol_sol≥85 graduation label.**

**Build order:**
1. **Phase A (US-80, US-81)** runs FIRST and is the foundation. US-80 (salvage the VPS-`/tmp`-only probes — UNRECOVERABLE once the purge runs) and US-81 (the local harness: parser + free grad labeler + dollar basis) are independent and may run in parallel; every other story imports US-81.
2. **Phase B** runs after A. **COPY:** US-82 (the EDGE — classifier recalibration) → US-85 (re-soak + kill-switch); US-83 (settler honesty, depends on US-86) and US-84 (weekly retrain) in parallel where deps allow. **MODEL:** US-86 (FOUNDATIONAL graduation-detector fix — also feeds US-83) is independent and high-priority; US-87 (v6 wired + parity) → US-88 (carry-forward verification). **RECORDER:** US-92 (CRITICAL — post rows must carry `mint`) is independent and **gates US-89 + US-91** (a mint-less post tape can never be settled by `fill_repricing.py`). Phase-B stories that touch the same shared modules (`run_firehose.py`, `tape_sink.py`, `curvestage_engine`) run **serially through the shared tree**.
3. **Phase C (US-89, US-90)** is GATED on ALL Phase-A/B local-proofs GREEN — **including US-92** (post rows attributable to a token; without it the deploy ships a self-validation that can never run). US-89 (purge + clean deploy of ONLY v6 + curvestage) then US-90 (fresh honest dashboard wired to `fill_repricing.py`).
4. **Phase D (US-91)** is the observe soak on the clean deploy, judged exclusively by `fill_repricing.py`, with the kill-switches armed. Capital / live-flip / the model size-ramp remain OPERATOR-DRIVEN and OUT OF SCOPE.

## Reconciliation with existing untracked EPIC drafts
- **`EPIC-copy-capital-path-wiring.md`** — SUPERSEDED for this sprint. It wires the copy LIVE real-SOL send path (0.04 SOL budget). Sprint-15 is **observe-only, `trading_enabled=FALSE`, NEVER flipped**; the live-capital path is explicitly OUT OF SCOPE. The execution primitives stay gated/inert. Do NOT action it in sprint-15.
- **`EPIC-tape-sourcing-escalation.md`** — PARTIALLY FOLDED. Tier 2 (lake fallback, FREE/local) is reconciled into US-88 AC-88.3 as "confirm present." Tier 3 (Birdeye trade-history REST) is **credit-gated → DEFERRED** this no-credit sprint.
- **`EPIC-graduation-migrate-detection.md` + `EPIC-graduation-mint-extraction-fix.md`** — RECONCILED into US-86, which decides the detector by **match-rate against the completeevent-derived ground truth** (`seed_recent.csv`, FREE/local), NOT by doc precedence: both candidates (the `Instruction: Migrate` substring matcher vs precise `MigrateV2` + PumpSwap `CreatePool`) are scored offline over local fixtures and the higher precision/recall ships. Engineering lean is MigrateV2+CreatePool (substring false-positives + silently breaks on the protocol-version rename the 'V2' implies). Supersedes the partial hotfixes #389–#392. Fresh evidence: Jun 20–23 firehose has `phase=post` on only 13 swaps across 4 full days → the deployed detector misses ~all graduations, blocking both the copy ride-to-grad exit and the model's post-grad outcomes.
- **`EPIC-copy-curvestage-integration.md` / `EPIC-copy-paper-fill-repricing.md` / `EPIC-model-paper-pnl-truncation-floor.md` / `EPIC-model-track-deploy.md`** — these are the source specs for US-82/83/84/85 (copy) and US-87/88 (model). Their integration details are honored; sprint-15 enforces them through the structured story/Tester/Dev loop and the local-proof gate rather than further hotfix PRs.

## Deferred / flagged (NOT in sprint-15)
- **Copy LIVE real-SOL execution** (the capital-path EPIC) — observe-only this sprint; operator-driven.
- **Tier 3 Birdeye trade-history backfill** — credit-gated; deferred (no credits).
- **v6 post-grad PnL validation** — owned by a separate Birdeye backtest agent (already done Jun 20–21); the live recorder confirms later. Out of scope here (parity-only).
- **The depth-gated $100 size ramp** — soak-gated; observe at $25 flat only.
- **Backlog re-resolution of the ~147 mislabeled `Token` rows** (mint-extraction #366 regression) — fix-forward only; a separate authorized task.
- **Live exercise of the #382 Birdeye-REST entry-window recovery** — credit-gated; logic verified offline, live run deferred to the operator-driven post-soak window.

## User Stories
_Full AC text + local-proof DoD per story live in [`sprint15.json`](sprint15.json) (source of truth). The Dev Team fills `dev_status`/`dev_notes`; the Tester fills `tester_status`/`tester_notes`._

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
_Pending_

### PO Sprint Review Notes
_Pending_

---
_Source of truth: `sprint15.json`. After editing any `/scrum-master/` doc, re-index via `mcp__devrag__reindex_document`._
