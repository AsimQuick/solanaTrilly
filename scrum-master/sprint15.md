# Sprint 15 — CLEAN RESET

**Phase:** planning
**Progress:** 0/16 stories | 0/48 ACs
**Last Updated:** 2026-06-26

## Sprint Goal
Replace the failed **hotfix-PR style** (half-applied fixes, untraceable landings, fake/inflated dashboard PnL) with **structured user stories + a Tester quality gate + a Dev-Team loop**, then **fix-validate-LOCALLY-then-PURGE** the VPS and deploy **ONLY two artifacts** — model `models/trilly_pregrad_v7` (the post-grad lane; **supersedes the retired v6/v6.1** — a from-scratch BUILD, not a verify), copy formula `models/copy_2026-06-22_curvestage` — **observe-only, $25, `trading_enabled=FALSE` (never flipped)**.

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
- **SHARED FIREHOSE (US-96, CRITICAL — added 2026-06-26)** — operator directive: *"we cannot go with 2 firehoses."* solanatrilly must STOP running its own duplicate Helius firehose and instead **read solanaBilly's existing tapes**. Design source (READ IT): `/Users/asim/NoIcloud/solanatrills/docs/solanatrilly_shared_firehose_design.md`. Components: **5a** mount `- /root/tape:/app/lake/billy_tape:ro` on `inference_engine` + `celery-worker` (distinct path, NOT `/app/lake/firehose`); **5b** add `TAPE_SOURCE` (default `shared_billy`) — no Helius sub (`HeliusBirthTapeSource` @`_helius_loop` ~`run_firehose.py:2552`), no self-tape-write (`LakeTapeSink` @`tape_sink.py`), instead TAIL billy's tape -> feed `_scoring_loop` (~`run_firehose.py:1502`); `TAPE_SOURCE=self` = fallback; **5c** port the stdlib `read_rows()` schema-A<->B adapter from `solanatrills/analysis/replay/replay_core.py` into every lake-line parse site (consume loop, `core/tape/lake_reader.py`, US-78 swaps export, curvestage settler) — schema A is RICHER (exact `price=vsol/vtok`, exact `curve_frac` from reserves, real-book depth, **and `token_amount`** -> the clean source for US-93's #1 feature); **5d** the WHOLE coordination = a one-way file-**mtime FRESHNESS** check (no solanaBilly DB read): refuse-at-enable with the VERBATIM warning *"You must turn on solanaBilly's firehose."* + idle-at-startup if stale; **5e** drop `HELIUS_API_KEY` from `inference_engine` ONLY (keep on listener/celery-worker/copytrade_engine). POST-grad swaps are a SEPARATE Birdeye `_postgrad_loop` stream — leave it; **US-92 stays orthogonal**. **US-96 GATES US-89** — the clean deploy must ship the shared-tape architecture, not the duplicate. §8 open questions (billy's on-disk schema, writer mtime cadence, graduation marker) are resolved by the dev ON THE VPS during build.
- **US-93 REOPENED (2026-06-26 — was wrongly closed in PR #412, NOT merged)** — PR #412 implemented the model's #1 feature `n_pregrad_holders` (imp 727.1, ~2x the next) as a **BUY-COUNT proxy** (`count(buys) - count(sells) > 0` per wallet); the lab + `parity_sample.parquet` definition is **BALANCE-BASED** (distinct wallets with net-positive TOKEN balance). The agent's own docstring admits the deviation. AND the only "parity" test (`test_v7_feature_vector_score_matches_parity_sample_spot_check`) reads parity_sample's ALREADY-COMPUTED features and scores them — it validates the SCORER (US-87), **NOT the live builder** — so the deviation passes CI silently. Fix: AC-93.1 now mandates **balance-based** holder count (schema-A `token_amount` via US-96; from schema B, `token ~= vol_sol/price`), and **NEW AC-93.4** adds a real **feature-builder parity test** (recompute features FROM A TAPE, assert they match parity_sample's feature columns). **US-93 now DEPENDS ON US-96** (clean `token_amount`). US-87 (scorer parity, 0.00 error over 400 rows) is CORRECT and stays done; US-94 still depends on US-93.
- **MODEL TRACK = v7 BUILD + PARITY-PROVEN (RESCOPED 2026-06-26)** — trilly_pregrad_v7 **supersedes** the retired v6/v6.1 and is a from-scratch BUILD (44 leak-safe pre-grad + `n_pregrad_holders` + 24 wallet-rep feats → mean of 8 LightGBM boosters → gate ≥ 15.86012 → `tr30_t600` RIDE exit → depth-gated sizing). The build chain is **US-87** (vendor + parity harness) → **US-93** (44-feature live vector incl. the #1 feature `n_pregrad_holders` + the wallet-rep bank) → **US-94** (gate + ride exit + depth-gated sizing). **LOCAL-PROOF = parity vs `models/trilly_pregrad_v7/parity_sample.parquet`** (400 tok × 44 feats + expected 8-seed score; live feature vector + score MUST match before any number is believed — project law). Observe-only @ $25 flat; trading_enabled=FALSE; the depth-gated size-up is SOAK-GATED (US-91). The shipped boosters are **REFERENCE/ARCHITECTURE** — a Birdeye-trained model did NOT transfer to the live firehose (t120) → **MUST retrain on solanatrilly's own live AMM feed before ANY capital** (US-95, depends on US-92, post-soak/future). **4 PO RISKS** (price them in, do NOT scope v7 as guaranteed $500/day): RIGHT-TAIL-CARRIED (median trade loses; top ~5% carry 110–190% of profit — #1 risk), SURVIVORSHIP + FEED-TRANSFER, DECAY (retrain weekly), FILL-FRAGILITY (~$300 stressed–$570 modeled). The old US-87 'verify v6 wired + parity' scope is OBSOLETE.
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
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v7/MODEL_HANDOFF.md` (v7 authoritative spec — PO + Dev sections)
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v7/meta.json` (v7 contract: feature_order, nan_fill, gate, exit, sizing, economics, caveats)
- `/Users/asim/NoIcloud/solanatrills/analysis/graduated/POSTGRAD_FINDINGS.md` (v7 study + honest caveats)
- `models/trilly_pregrad_v7/parity_sample.parquet` (VENDORED — 400 tok × 44 feats + expected 8-seed score; the local-proof oracle)
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v6/MODEL_HANDOFF.md` (v6 — RETIRED; lineage reference only)
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
| US-87 | B-model | **v7 BUILD #1/3** — vendor `trilly_pregrad_v7` into the repo + stand up the offline PARITY HARNESS vs `parity_sample.parquet` (400 tok × 44 feats + expected 8-seed score). **SUPERSEDES the obsolete "verify v6 wired" scope** (v6 retired; v7 is a BUILD) | critical | US-81 | 3 | draft |
| US-88 | B-model | Carry-forward verification: prove #386 (truncation+floor) and #381/#382 (post-grad entry-tape recovery) survive INTACT into the clean deploy. **#381/#382 IS v7's post-grad entry-window recovery** (linked) | high | US-87, US-93 | 3 | draft |
| US-93 | B-model | **v7 BUILD #2/3 — REOPENED 2026-06-26** (PR #412 NOT done): the 44-feature LIVE vector. **AC-93.1 FIX:** `n_pregrad_holders` (#1 feature) must be **BALANCE-BASED** (net-positive TOKEN balance via schema-A `token_amount` from US-96), NOT PR #412's buy-count proxy. **NEW AC-93.4:** real feature-builder parity test (recompute features FROM A TAPE vs `parity_sample.parquet`, not just score golden features). + 19 pre-grad feats + 24 wallet-rep feats | critical | US-81, US-86, US-87, **US-96** | 4 | defect-found |
| US-94 | B-model | **v7 BUILD #3/3** — gate (score ≥ 15.86012) + `tr30_t600` RIDE exit (30% trailing off running max, 600s timer, honest fill) + depth-gated SIZING ($100 if depth ≥ $8k else $25, CAP $100). Observe-only @ $25 flat; size-up soak-gated | high | US-87, US-93 | 3 | draft |
| US-95 | B-model | **v7 RISK/DEPENDENCY** (scope as known dep, not a this-sprint build) — Birdeye-trained ≠ live (t120); shipped boosters are REFERENCE/ARCHITECTURE, MUST retrain on solanatrilly's OWN live AMM feed before capital. **DEPENDS ON US-92** (mint-on-post-row to retrain); retrain itself is post-soak/future | high | US-87, US-92 | 3 | draft |
| US-92 | B-recorder | CRITICAL: post-grad firehose rows must carry the token MINT — recorder writes post rows with NO mint, so post-grad fills are unattributable offline and the live recorder can NEVER self-validate model PnL / copy ride-to-grad exit | critical | US-81 | 3 | draft |
| US-96 | B-firehose | **SHARED FIREHOSE (operator: "we cannot go with 2 firehoses")** — mount solanaBilly's `/root/tape:ro`, add `TAPE_SOURCE=shared_billy` (tail billy's tape, NO Helius sub, NO self-tape-write; `self`=fallback), port the stdlib schema-A↔B `read_rows()` adapter (schema A carries exact reserves + **`token_amount`** → feeds US-93), one-way file-**mtime freshness** precondition (verbatim *"You must turn on solanaBilly's firehose."*), drop `HELIUS_API_KEY` from `inference_engine` only. **GATES US-89** | critical | US-81 | 4 | draft |
| US-89 | C | ONE clean VPS purge (all old models/formulas/positions/half-applied fixes) then deploy ONLY **v7** + curvestage via CD, observe-only/$25/`trading_enabled=FALSE` (boosters are reference pending live-feed retrain — NO capital) | critical | US-80…US-88, US-92…US-96 | 3 | draft |
| US-90 | C | Fresh HONEST dashboard: fix click-to-copy + pagination (required) + WIRE PnL to `fill_repricing.py` (required); UTC→Dubai time OPTIONAL/nice-to-have | high | US-80, US-89 | 3 | draft |
| US-91 | D | Observe soak both tracks ($25, `trading_enabled=FALSE`), judged EXCLUSIVELY by `fill_repricing.py`; adopt **v7's pre-registered soak ACs verbatim** (observe @ $25 → no inversion → deep-book fill ≤2× model → edge holds OOT → only then ramp ≤ $100) + the 4 PO risks; define kill-switches | high | US-89, US-90, US-92 | 3 | draft |

> **Scope:** sprint-15 commits **17 stories / 53 ACs** — a deliberately larger "clean reset" sprint, but strictly **sequenced A→B→C→D** with a hard local-proof gate before the purge. Source of truth: [`sprint15.json`](sprint15.json). **NO CREDITS — the firehose budget (6 Birdeye + 6 Helius) is UNTOUCHED; all testing is offline against the local Jun 20–23 tapes + the free cum-vol_sol≥85 graduation label.**

**Build order:**
1. **Phase A (US-80, US-81)** runs FIRST and is the foundation. US-80 (salvage the VPS-`/tmp`-only probes — UNRECOVERABLE once the purge runs) and US-81 (the local harness: parser + free grad labeler + dollar basis) are independent and may run in parallel; every other story imports US-81.
2. **Phase B** runs after A. **COPY:** US-82 (the EDGE — classifier recalibration) → US-85 (re-soak + kill-switch); US-83 (settler honesty, depends on US-86) and US-84 (weekly retrain) in parallel where deps allow. **MODEL (v7 BUILD):** US-86 (FOUNDATIONAL graduation-detector fix — also feeds US-83 and is v7's entry trigger) is independent and high-priority; then the v7 build chain US-87 (vendor + parity harness) → US-93 (44-feature live vector incl. `n_pregrad_holders` + wallet-rep bank — **REOPENED**, the #1 feature must be BALANCE-based not buy-count, **depends on US-96** for the clean `token_amount`) → US-94 (gate + `tr30_t600` ride exit + depth-gated sizing) → US-88 (carry-forward verification, now directly relevant — #381/#382 IS v7's post-grad entry-window recovery). US-95 (retrain-on-live-feed risk/dependency, **depends on US-92**) is a traceability story, not a this-sprint build. **v7 supersedes the retired v6** — the deploy ships v7. **FIREHOSE:** US-96 (CRITICAL — operator: "we cannot go with 2 firehoses") replaces solanatrilly's duplicate Helius firehose with a read of solanaBilly's shared tape (mount :ro, `TAPE_SOURCE=shared_billy`, schema-A↔B adapter, one-way mtime freshness precondition); it depends on US-81, feeds US-93 the clean `token_amount`, and **gates US-89** (the clean deploy must ship the shared-tape architecture, not the duplicate). **RECORDER:** US-92 (CRITICAL — post rows must carry `mint`) is independent and **gates US-89 + US-91** (a mint-less post tape can never be settled by `fill_repricing.py`). Phase-B stories that touch the same shared modules (`run_firehose.py`, `tape_sink.py`, `curvestage_engine`) run **serially through the shared tree**.
3. **Phase C (US-89, US-90)** is GATED on ALL Phase-A/B local-proofs GREEN — **including US-92** (post rows attributable to a token) **and US-96** (the deploy must ship the shared-tape firehose, not the duplicate Helius one). US-89 (purge + clean deploy of ONLY **v7** + curvestage) then US-90 (fresh honest dashboard wired to `fill_repricing.py`).
4. **Phase D (US-91)** is the observe soak on the clean deploy, judged exclusively by `fill_repricing.py`, with the kill-switches armed. Capital / live-flip / the model size-ramp remain OPERATOR-DRIVEN and OUT OF SCOPE.

## Reconciliation with existing untracked EPIC drafts
- **`EPIC-copy-capital-path-wiring.md`** — SUPERSEDED for this sprint. It wires the copy LIVE real-SOL send path (0.04 SOL budget). Sprint-15 is **observe-only, `trading_enabled=FALSE`, NEVER flipped**; the live-capital path is explicitly OUT OF SCOPE. The execution primitives stay gated/inert. Do NOT action it in sprint-15.
- **`solanatrilly_shared_firehose_design.md`** (lab doc, `/Users/asim/NoIcloud/solanatrills/docs/`) — FOLDED IN FULL as **US-96**. Operator directive "we cannot go with 2 firehoses": mount billy's `/root/tape:ro`, `TAPE_SOURCE=shared_billy`, schema-A↔B adapter, one-way mtime freshness precondition, drop Helius from `inference_engine`. The doc's §7 testing/rollback = US-96's DoD; its §8 open questions (billy on-disk schema, writer mtime cadence, graduation marker) are dev-on-VPS-during-build. Supersedes the standalone duplicate-firehose path; **gates US-89**.
- **`EPIC-tape-sourcing-escalation.md`** — PARTIALLY FOLDED. Tier 2 (lake fallback, FREE/local) is reconciled into US-88 AC-88.3 as "confirm present." Tier 3 (Birdeye trade-history REST) is **credit-gated → DEFERRED** this no-credit sprint.
- **`EPIC-graduation-migrate-detection.md` + `EPIC-graduation-mint-extraction-fix.md`** — RECONCILED into US-86, which decides the detector by **match-rate against the completeevent-derived ground truth** (`seed_recent.csv`, FREE/local), NOT by doc precedence: both candidates (the `Instruction: Migrate` substring matcher vs precise `MigrateV2` + PumpSwap `CreatePool`) are scored offline over local fixtures and the higher precision/recall ships. Engineering lean is MigrateV2+CreatePool (substring false-positives + silently breaks on the protocol-version rename the 'V2' implies). Supersedes the partial hotfixes #389–#392. Fresh evidence: Jun 20–23 firehose has `phase=post` on only 13 swaps across 4 full days → the deployed detector misses ~all graduations, blocking both the copy ride-to-grad exit and the model's post-grad outcomes.
- **`EPIC-copy-curvestage-integration.md` / `EPIC-copy-paper-fill-repricing.md` / `EPIC-model-paper-pnl-truncation-floor.md` / `EPIC-model-track-deploy.md`** — these are the source specs for US-82/83/84/85 (copy) and the model carry-forwards (US-88). Their integration details are honored; sprint-15 enforces them through the structured story/Tester/Dev loop and the local-proof gate rather than further hotfix PRs. **NOTE:** `EPIC-model-track-deploy.md` targeted v6 — **superseded**: trilly_pregrad_v7 replaces v6/v6.1 and the model track is now a v7 BUILD (US-87/US-93/US-94) proven by parity vs `parity_sample.parquet`, not a v6 verify.

## Deferred / flagged (NOT in sprint-15)
- **Copy LIVE real-SOL execution** (the capital-path EPIC) — observe-only this sprint; operator-driven.
- **Tier 3 Birdeye trade-history backfill** — credit-gated; deferred (no credits).
- **v7 post-grad PnL / live capital** — the v7 build (US-87/US-93/US-94) is proven LOCALLY by parity vs `parity_sample.parquet` (live feature vector + score == expected); live $/day is a **soak** question (US-91), graded on the post-grad TAPE + on-chain graduation, NEVER dashboard PnL. **No capital** until a live-feed retrain (US-95) holds out-of-time.
- **v7 live-feed retrain** — Birdeye-trained ≠ live (t120); the shipped 8 boosters are REFERENCE/ARCHITECTURE. The retrain is **post-soak/future** (needs banked live post-grad feed via US-92) — scoped as a known dependency in US-95, not a this-sprint run.
- **`n_pregrad_holders` live-computability — RESOLVED + DEFECT** — the #1 feature (imp 727.1) IS computable live, BUT the definition matters: it must be **BALANCE-based** (distinct wallets with net-positive TOKEN balance: cumulative buy `token_amount` − sell `token_amount` > 0), matching the lab + `parity_sample.parquet`. PR #412 shipped a **buy-count proxy** (count(buys)−count(sells)>0) — OUT OF DISTRIBUTION → US-93 REOPENED. The clean source is schema-A `token_amount` (US-96); from schema B it is derivable (`token ≈ vol_sol/price`). See revised AC-93.1 + the new feature-builder parity test AC-93.4.
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
