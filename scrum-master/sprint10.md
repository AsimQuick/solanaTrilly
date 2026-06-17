# Sprint 10

**Phase:** planning
**Progress:** 0/6 stories | 0/18 ACs
**Last Updated:** 2026-06-17T00:00:00+00:00

## Sprint Goal
Open P6 — the FOUNDATIONAL research-first dashboard (PRD §13, build phase P6, the last unbuilt PRD pillar). P0–P5 + P7 are closed: the VPS staging stack is live on 8002 (P0), the config core is the single source of truth (P1), detection lands graduated 'tokens' (P2), the tape recorder captures every PumpSwap swap from t0 into the immutable jsonl.gz lake + 'swaps' mirror with live↔backfill byte-parity (P3), the score-time snapshot + three-unit lock (P4), the integrity core — one vendored feature library (tape_microstructure.py), one shared deterministic extractor serving live+offline+replay, and the T0/G1/G2 golden-parity hard merge gate making live==offline BY CONSTRUCTION (P5) — the Helius program-wide birth-tape source making v3.2's 20 pre_* features live-computable (sprint-8), and the P7 scorer/serving path (sprint-9): the model-agnostic feature-contract reconciler (US-41), the ModelRegistry + 15-booster BLEND write contract (US-42), the one shared deterministic BlendScorer with the oracle §4.2 cutover risk resolved (US-43), and the 'scores in sync' scorer-parity gate against v3.2's banked golden score vectors (US-44). What remains of the PRD's THREE pillars is the THIRD — the research-first dashboard (§13), which solanaBilly never had and which the operator explicitly demanded ('the UI is terrible. I can't even see what's happening with the positions'; 'guide the modeling agents with visualizations only I can see and a machine can't'). Sprint-10 OPENS P6 and meets its offline gate ('operator sees real candles for a replayed token'): (US-48) the dashboard FOUNDATION — React + Vite served by DRF + Django Channels realtime (NOT server-rendered tables; solanaBilly's Flask/DataTables UI is IGNORED, built fresh per §14), the 'frontend' dev container (§15.1) added to BOTH compose files (Docker Rules, scoped -p solanatrilly), and a Channels consumer pushing tape/candle/position deltas from the ONE tape source (§13.4 — one source, no separate price feed to drift), deployed + smoke-tested on the VPS (P6 DoD = live on VPS, not green locally); (US-49) the token-detail / research view — a tape→candle API (1s/5s/15s/1m OHLC derived from the lake via the SAME shared US-30 extractor path / raw lake — Principle #2, never a separate price basis) + the view rendering a replayed token's real candle with buy/sell-pressure & net-flow overlay, t0/score markers, AND the exact feature vector the model saw + its score (from the US-43 BlendScorer / US-44 banked golden vectors) — THIS MEETS the P6 offline gate; (US-50) the cohort small-multiples pattern-mining wall — a grid of mini candle sparklines, groupable/sortable by outcome, score band, exit trigger, depth bucket, and time-of-day (the 'winners share this shape / rugs share this pre-entry tape' research instrument); (US-51) human annotation → labeled export (§13.3, the killer feature) — the annotations table (PRD §8: mint, author, tags[], note, created_at) + a tag panel beside the chart (free-text + categorical: 'classic rug shape', 'slow bleed', 'clean ignition', 'fakeout pop', 'organic') storing on mint, and an EXPORT to the labs as a labeled dataset (mirroring the §6.5 Feature Builder export, off the celery container per #289) — turning visual pattern-recognition into a feature/label source. PLUS the two agent-actionable sprint-9 carries: (US-46 / I1) a CLEAN re-deploy of US-43 (the 15-booster blend serving path) to the VPS solanatrilly staging stack from a clean green run, resolving the failed per-story deploy (run 27673867804) so the VPS has the scorer live BEFORE the operator-driven soak (I2); (US-47 / I4) close the AI dev-agent / ruff hook gap — G3 made the hook unforgeable for human/devcontainer paths (H2 CONFIRMED) but 4 sprint-9 lint defects (F401/I001) still occurred in AI-agent commits that bypass local pre-commit hooks; add a structural mechanism so violations are caught before the CI Lint step, making the gate as unforgeable for agents as G3 made it for humans. FIREHOSE: P6 is OFFLINE/REPLAY-DRIVEN BY CONSTRUCTION — ALL dashboard data comes from the EXISTING lake + ReplaySource + the US-43 BlendScorer / US-44 banked golden vectors; ZERO firehose activation this sprint (8 Birdeye + 8 Helius remain banked; the HARD RULE 'every activation banks a durable fixture' is untouched). DEFERRED (forward_plan, NOT committed here to avoid the over-commitment the retrospectives repeatedly warn against on heavy phases): I2 the operator-driven P7-3 SOAK and I3 the ENDGAME (both operator-driven, not sprint stories); the REMAINING dashboard views (Live Positions board §13.2#1, Calibration & PnL analytics §13.2#4, Replay viewer §13.2#5, Config/model control §13.2#6, Feature Builder UI §13.2#7) — Live Positions and Calibration require P8 position/PnL data that does not exist until the trading engine lands; and the P8 trading-execution path (PRD §10), gated behind the soak/endgame. Build order: US-46 and US-47 are independent process/deploy carries (may run first / in parallel). The dashboard chain is sequential: US-48 (stack + one-feed) → US-49 (candles + token detail = the P6 gate) → US-50 (cohort wall) and US-51 (annotation) both depend on US-49 and may run in parallel after it.

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/oracle-direction.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/sprint9.json`
- `CLAUDE.md`
- `ops/firehose_activation_log.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects (and no major defects open at sprint close)
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter (project convention) — on all new backend AND frontend source files
- [ ] All services run in Docker (no host installs — Docker Rules). The new 'frontend' (Vite) dev container is added to BOTH compose files and runs INSIDE the solanatrilly containers (NEVER Node/Vite installed on the host); any package.json/requirements change is followed by an image rebuild; every docker command is scoped with -p solanatrilly.
- [ ] CD pipeline LIVE and GREEN: every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there ('works locally' is NOT done; the P6 phase DoD is explicitly 'live on the VPS', PRD §15.2/§16). The smoke-test retains the runtime retry-with-backoff from US-12 (AC-12.3). Per H1: the deploy is gated by the SAME canonical ci.yml 'test' job that already went green — NOT a divergent inline copy in deploy.yml.
- [ ] VPS verification is IN THE LOOP and gates 'done' (retrospective B3/C1/H1): the Tester confirms from an ACTUAL green deploy run that the stack answers HTTP 200 on 8002, the 'listener' container is Up, and solanaBilly is untouched on 8001.
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on 8001 untouched; NO unscoped down / up --force-recreate / prune / volume-removal anywhere).
- [ ] Status integrity enforced PROGRAMMATICALLY (US-13 guard): no story/AC reads status:done while its tester_status is failed/blocked; the guard is GREEN on sprint10.json at review. Per F2/G4, phase-promotion is MECHANICAL and PROVEN to fire in the orchestrator's actual deploy sequence (tools/promote_sprint_phase.py auto-promotes or blocks with REMEDY) BEFORE any sprint-end deploy; per D3, story-level dev_status is promoted to 'done' at closeout (not exempted via --skip-complete).
- [ ] Config-driven (Principle #1): all dashboard tunables (candle intervals, cohort grouping keys, WS channel/topic names, export destination) are read from get_active_config() / core/schemas.py — never literals in code, never scattered os.getenv.
- [ ] Parity by construction (Principle #2): the dashboard candles AND the displayed feature vector/score are derived from the SAME single shared extractor (US-30) / the same raw lake / the same US-43 BlendScorer that serve live+offline+replay — NO dashboard-local feature assembly, NO dashboard-local price/candle basis. The vendored feature math (tape_microstructure.py) is NEVER hand-edited. What the operator sees on the chart IS what the model saw.
- [ ] Dashboard realtime is ONE tape feed (§13.4): Channels pushes candle/position deltas from the SAME tape the recorder writes / the extractor reads; there is NO separate price feed — solanaBilly's 'Position has NO price feed' drift class (a heartbeat/feed split) is structurally excluded. Verified by a structural test asserting the consumer's delta source is the tape DataSource / extractor path, not an independent price source.
- [ ] The frontend is React + Vite + DRF + Django Channels (§13.1) — NOT server-rendered tables; solanaBilly's Flask/DataTables UI is IGNORED and the dashboard is built fresh (§14 'IGNORE — build §13 fresh'). TradingView Lightweight Charts (MIT) is the candle/marker renderer.
- [ ] Raw = immutable truth (§6.4.1): the dashboard, the candle API, and the annotation/export paths NEVER mutate the raw lake; candles/features are always re-derived via the shared extractor from raw; annotations are a SEPARATE store keyed on mint and never write back into raw.
- [ ] Firehose budget honored (§15.7): P6 is OFFLINE/REPLAY-DRIVEN BY CONSTRUCTION — every dashboard view, the candle API, the cohort wall, and the annotation/export all run against the EXISTING lake + ReplaySource + the US-43/US-44 banked artifacts and require NO firehose WS activation; 8 Birdeye + 8 Helius remain banked. The HARD RULE 'every activation banks a durable fixture' holds (no activation this sprint).
- [ ] Scores in sync (standing DoD, oracle §3/§4): the feature vector + score the token-detail view displays come from the US-43 BlendScorer path that already passes the US-44 scorer-parity gate against v3.2's banked golden_scores vectors — the dashboard surfaces the parity-checked score, never a re-implemented one.
- [ ] Sources in sync (standing DoD): the dashboard reads the same single shared extractor over the same lake; no dashboard-only source assembly is introduced.
- [ ] Process gates mechanized AND PROVEN (retrospective G3/G4/H2 + I4): the ruff gate (I001/E501/F401) is unforgeable AND, per US-47, the AI dev-agent path is structurally covered so a violation is caught before the CI Lint step; the F2 phase-promoter is proven to execute in the actual deploy sequence.
- [ ] retrospective.md updated for sprint-10 (named owner: Tester / scrum facilitator — retrospective A3); after updating any /scrum-master/ doc, re-index via mcp__devrag__reindex_document (project convention).

## User Stories

### US-46: I1 — clean re-deploy of the US-43 15-booster blend serving path to the VPS staging stack from a green run, resolving the failed per-story deploy (run 27673867804); Tester-confirm the scorer is live on VPS before the soak (retrospective I1)
**Status:** to-do | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-46.1:** Diagnose the ROOT CAUSE of the failed US-43 per-story deploy (run 27673867804, conclusion: failure) — the blend serving path was CI-green on the standalone canonical ci.yml but its per-story boundary deploy failed (a deploy-context issue, not a code regression). Identify the concrete divergence (e.g. a deploy-context env/secret/migration/LightGBM-image difference, or a transient runner/registry condition) and record the root cause (not a symptom), verified by reproducing the diagnosis against the deploy run logs / the unified workflow_call gate from US-40.
- [ ] **AC-46.2:** Re-run the deploy on 'main' at HEAD (the unified US-40 workflow_call gate — gated by the already-green canonical ci.yml 'test' job, NOT a divergent inline copy) and obtain an ACTUAL GREEN deploy run that deploys the US-43 blend serving path (BlendScorer + score_token Celery task + the LightGBM in-container requirement + libgomp1) to the VPS solanatrilly staging stack. Verified by the green deploy run id being recorded and the AC-39.2 phase-promoter pre-deploy step + the AC-12.3 smoke-test retry-with-backoff confirmed preserved in that run.
- [ ] **AC-46.3:** Tester-CONFIRM from that ACTUAL GREEN deploy run that the VPS has the scorer LIVE before the operator-driven soak (I2): HTTP 200 on 8002 (with the AC-12.3 retry-with-backoff), the 'listener' container Up, a scorer/celery-worker container Up, and solanaBilly UNTOUCHED on 8001 (every command scoped -p solanatrilly). Additionally confirm the scorer is deployable in-container: lightgbm imports and tools.promote_model.promote_blend is importable inside the solanatrilly container (an in-container import check), proving the soak prerequisite is met. A partial-evidence PR-merge deploy is NOT sufficient — the deliberate clean run at HEAD must be green. New/changed files carry metadata front matter.

**Dependencies:** US-43, US-40

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-46.1 is an investigative AC (diagnosis, not mechanical) — acceptable because the deliverable is a written root-cause record citing the failing step in run 27673867804, verified against the deploy logs; the US-40 workflow_call gate is the known resolution path. AC-46.2 is concrete: green deploy run ID must be recorded, AC-39.2 phase-promoter and AC-12.3 smoke-test retry-with-backoff must be confirmed present in that specific run. AC-46.3 is highly specific with five individually measurable VPS conditions plus an in-container import check — no ambiguity. All 3 ACs are independently verifiable.

---

### US-47: I4 — close the AI dev-agent / ruff hook gap: a structural mechanism that catches I001/E501/F401 before the CI Lint step on the AI-agent commit path, making the gate as unforgeable for agents as G3 made it for humans (retrospective I4/H2)
**Status:** to-do | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-47.1:** Add a STRUCTURAL mechanism that runs `ruff check` on the AI dev-agent commit path WITHOUT a manual step — e.g. a container-entrypoint ruff-check step in the dev-agent container, or a tighter pre-commit guard the dev-agent loop invokes — so a violation is caught BEFORE the CI Lint step (the 4 sprint-9 lint defects — F401 AC-40.1, I001 AC-41.1, F401 AC-42.3, I001 AC-44.2 — occurred in AI-agent commits that bypass the G3 local pre-commit hook). The mechanism fires automatically with no manual invocation. Verified by a pytest test asserting the mechanism is wired (structural — present in the entrypoint/dev-agent-loop/config it lives in) and fires without a manual 'make'/install step, mirroring the AC-39.1/AC-45.1 'no manual step' proof for the human hook.
- [ ] **AC-47.2:** Behavioral proof: the mechanism CATCHES a seeded I001 (import-sort), E501 (line-length), AND F401 (unused import) — the exact recurring lint class — on the AI-agent path and passes once fixed. Verified by a pytest test that seeds each of the three violation classes, asserts the mechanism flags them (non-zero / refusal), and that a clean file passes — the 'prove the mechanism fires' rigor (assumption replaced by evidence) the project demanded of G3/G4 and the promoter.
- [ ] **AC-47.3:** The mechanism cannot SILENTLY VANISH: it is wired so a deleted/renamed/disabled check fails loudly (e.g. a structural/AST test pinning the entrypoint or loop step by name + an ImportError-trap-style guard on the named function/step, mirroring the H1 ImportError-trap pattern used across the canonical test job) — a removed guard fails pytest collection or a structural test, never degrades to a no-op (the ImportError-trap anti-pattern of a swallowed exception is explicitly excluded). New files carry metadata front matter.

**Dependencies:** US-39

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-47.1 requires a structural pytest assertion proving the mechanism exists and fires without a manual step — mirrors the AC-39.1/AC-45.1 'no manual step' proof pattern already validated in prior sprints, so the bar is known and replicable. AC-47.2 requires behavioral evidence across all three recurring lint classes (I001, E501, F401): seeded violation → non-zero exit; clean file → zero exit. This is the exact 'prove the mechanism fires' rigor the project demanded of G3/G4. AC-47.3's silent-vanish guard (structural/AST test + ImportError-trap-style pinning) mirrors the H1 pattern established across the canonical test job — no new verification concept required. All 3 ACs are testable and independently verifiable.

---

### US-48: P6 — the dashboard FOUNDATION: React + Vite + DRF + Django Channels stack, the 'frontend' dev container in BOTH compose files, and the ONE-tape-feed Channels consumer pushing candle/position deltas from the single tape source (PRD §13.1/§13.4/§15.1)
**Status:** to-do | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-48.1:** A React + Vite frontend served by DRF + Django Channels (NOT server-rendered tables; solanaBilly's Flask/DataTables UI is IGNORED and the dashboard is built fresh per §14) is scaffolded: a 'frontend' (Vite) dev container per §15.1 is added to BOTH the local compose AND docker-compose.staging.yml, scoped -p solanatrilly (Docker Rules — Node/Vite run IN the container, NEVER on the host; built static assets are served by 'web' in prod). Verified by a structural test (compose parse) asserting the 'frontend' service exists in both compose files with no host port collision with solanaBilly's 8001 and the project remains isolatable with -p solanatrilly, plus a build/health check that the Vite dev container builds and the React app mounts. New frontend source files carry metadata front-matter header comments.
- [ ] **AC-48.2:** ONE tape feed (§13.4): a Django Channels consumer pushes tape/candle/position deltas to the frontend over WebSocket, sourced from the SAME tape DataSource / shared US-30 extractor path the recorder writes and the extractor reads — there is NO separate price feed (solanaBilly's 'Position has NO price feed' heartbeat/feed-split drift class is structurally excluded). The channel/topic names and any cadence are config-driven (Principle #1, core/schemas.py — no literals). Verified by (a) a structural/AST test asserting the consumer's delta source is the tape DataSource / extractor and NOT an independent price source, and (b) a deterministic test that, driven from a banked ReplaySource tape, the consumer emits the expected candle/position delta sequence (run-twice identical) — OFFLINE, zero firehose.
- [ ] **AC-48.3:** The dashboard foundation is deployed to the VPS solanatrilly staging stack and smoke-tested THERE (P6 DoD = live on the VPS, PRD §15.2/§16 — 'works locally' is NOT done). Verified by the Tester confirming from an ACTUAL GREEN deploy run: HTTP 200 on 8002, the dashboard route returns HTTP 200 and the WS endpoint accepts a WebSocket upgrade (HTTP 101), the 'frontend'/'web' containers Up, and solanaBilly untouched on 8001 (every command scoped -p solanatrilly). The WS consumer tests are wired into the single canonical ci.yml 'test' job via an ImportError trap on the named consumer function (H1) so a deleted/renamed consumer fails pytest collection. New files carry metadata front matter.

**Dependencies:** US-1, US-30

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-48.1: structural compose-parse test covers both compose files with port-collision check; the build/health check ('Vite dev container builds and React app mounts') uses standard React/Vite verification terminology and is unambiguous in the dev-container context. AC-48.2: two distinct verification paths (structural/AST source-assertion + deterministic replay test) together prove both the absence of a separate price feed AND the correct delta emission sequence — the dual-proof pattern is appropriate given the 'one tape feed' invariant is a standing DoD line. AC-48.3: wording tightened in-place — 'WS endpoint reachable' sharpened to HTTP 101 upgrade acceptance for unambiguous smoke-test verification. ImportError trap on named consumer function locks the CI gate against silent deletion. All 3 ACs are testable and independently verifiable.

---

### US-49: P6 OFFLINE GATE — token detail / research view: a tape→candle API (1s/5s/15s/1m OHLC) from the shared US-30 extractor / raw lake + the view rendering a replayed token's real candle with pressure/net-flow overlay, t0/score markers, AND the exact feature vector + score the model saw ('operator sees real candles for a replayed token', PRD §13.2#2)
**Status:** to-do | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-49.1:** A tape→candle API derives 1s/5s/15s/1m OHLC candles for a token from the lake via the SAME shared US-30 extractor path / raw lake — NEVER a separate price basis (Principle #2; PRD §13.2#2; the §14 'one coordinated price-basis pass' warning). The candle intervals are config-driven (Principle #1). Verified by a pytest test that, over a banked lake/replay fixture, the candle endpoint returns deterministic OHLC for each interval (run-twice byte/value-identical) and that the candle basis resolves to the shared extractor / raw lake (a structural assertion there is no candle-local price source).
- [ ] **AC-49.2:** The token-detail / research view renders a REPLAYED token's REAL candle (TradingView Lightweight Charts) with a buy/sell-pressure & net-flow overlay and t0/score markers — MEETING the P6 offline gate: 'operator sees real candles for a replayed token.' Deterministic over a banked lake/replay fixture. Verified by a pytest/component test asserting the view assembles the candle series + overlay + markers from the candle API (AC-49.1) for a banked replay token, deterministically and OFFLINE (zero firehose).
- [ ] **AC-49.3:** The view also surfaces the EXACT feature vector the model saw + its score — sourced from the US-43 BlendScorer / the US-44 banked golden vectors (the parity-checked score, never a re-implemented one; 'scores in sync'). The displayed feature vector is the same one the shared US-30 extractor produced (Principle #2 — what the operator sees IS what the model saw). Verified by a pytest test that the view's displayed feature vector + score match the BlendScorer/golden-vector output for the banked token within the documented tolerance, run-twice identical. The token-detail tests are wired into the canonical ci.yml 'test' job via an ImportError trap on the named candle/detail functions (H1). New files carry metadata front matter.

**Dependencies:** US-48, US-43, US-30

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-49.1: four named candle intervals (1s/5s/15s/1m) with deterministic run-twice OHLC equality over a banked fixture, plus a structural no-separate-price-basis assertion — fully verifiable and consistent with the established parity-by-construction pattern. AC-49.2: 'pytest/component test asserting the view assembles the candle series + overlay + markers' is sufficient specificity for a frontend component test; the test drives from the AC-49.1 candle API output against a banked replay token, which pins the exact data contract. AC-49.3: 'within the documented tolerance' references the US-43/US-44 tolerance already established and banked — not a vague hedge but a pointer to an existing artifact; run-twice byte/tol-identical and ImportError trap complete the gate. All 3 ACs are testable and independently verifiable.

---

### US-50: P6 — the cohort small-multiples pattern-mining wall: a grid of mini candle sparklines groupable/sortable by outcome, score band, exit trigger, depth bucket, and time-of-day ('winners share this shape / rugs share this pre-entry tape', PRD §13.2#3)
**Status:** to-do | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-50.1:** A cohort endpoint returns a grid of mini candle sparklines for a corpus of tokens, each sparkline derived from the SAME tape→candle path as US-49 (Principle #2 — no cohort-local candle basis; reuse the shared extractor / raw lake). Rendered from the EXISTING lake/replay corpus, OFFLINE (zero firehose). Verified by a pytest test that, over a banked corpus fixture, the cohort endpoint returns the expected set of sparkline series deterministically (run-twice identical).
- [ ] **AC-50.2:** The wall is GROUPABLE and SORTABLE by outcome, score band, exit trigger (where available), depth bucket, and time-of-day — the grouping keys/bucket boundaries are config-driven (Principle #1, core/schemas.py — no literals). Where a field (e.g. exit trigger) is not yet available offline it degrades cleanly (omitted/empty, never a crash or a fabricated value — real-missing preserved, never silently imputed, per H4). Verified by a pytest test asserting each grouping/sort key produces the correct partition over the banked corpus and that an unavailable field is handled cleanly.
- [ ] **AC-50.3:** The cohort wall view renders the small-multiples grid (TradingView Lightweight Charts / sparkline render) and applies the active grouping/sort over the banked corpus, deterministically and OFFLINE. The cohort tests are wired into the canonical ci.yml 'test' job via an ImportError trap on the named cohort function (H1) so a deleted/renamed endpoint fails pytest collection. New files carry metadata front matter.

**Dependencies:** US-49

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-50.1: cohort endpoint returning sparkline grid from the same tape→candle path as US-49 is a concrete reuse requirement, verifiable by pytest determinism test over a banked corpus fixture. AC-50.2: all five grouping/sort dimensions are named; the degradation rule ('omitted/empty, never a crash or fabricated value') is precisely stated and testable (pytest asserts non-None/non-crash for missing exit-trigger field); config-driven boundary aligns with Principle #1. AC-50.3: ImportError trap on named cohort function, consistent with the standing H1 gate pattern. The 'run-twice identical' requirement across all three ACs is clear and consistent with established parity conventions. All 3 ACs are testable and independently verifiable.

---

### US-51: P6 — human annotation → labeled export (the killer feature): the annotations table (PRD §8: mint, author, tags[], note, created_at) + a tag panel beside the chart + export to the labs as a labeled dataset (mirroring the §6.5 Feature Builder export, off the celery container per #289) (PRD §13.3)
**Status:** to-do | **Priority:** medium

#### Acceptance Criteria
- [ ] **AC-51.1:** The 'annotations' table (PRD §8: mint, author, tags[], note, created_at) is created as a SEPARATE store keyed on mint that NEVER writes back into the raw lake (raw=immutable, §6.4.1). A tag panel beside the chart records free-text + categorical tags ('classic rug shape', 'slow bleed', 'clean ignition', 'fakeout pop', 'organic'; the categorical set is config-driven, Principle #1). Verified by a pytest test that an annotation persists with all 5 fields keyed on mint, multiple annotations on one mint are retained, and the write path does not mutate the raw lake.
- [ ] **AC-51.2:** An EXPORT produces a labeled dataset (annotations joined to the token corpus on mint) for the labs, mirroring the §6.5 Feature Builder export pattern (CSV/parquet + a MANIFEST: dataset id, content hash, LC_ALL=C sort, SHA-256 of decompressed bytes per US-36) and running OFF the celery container (NEVER web/gunicorn, #289). Deterministic over a banked annotation+corpus fixture. Verified by a pytest test that the export emits the labeled dataset + a content-hash-matching MANIFEST deterministically (run-twice byte-identical) and a structural/AST test that the export task is a Celery task, not a web view.
- [ ] **AC-51.3:** No silent state change: the annotation/export paths NEVER enable scoring or trading (scoring_enabled/trading_enabled stay defaulted False; the US-11 AST no-auto-start guard stays green and covers the annotation/export path) and the export is idempotent. The annotation/export tests are wired into the canonical ci.yml 'test' job via an ImportError trap on the named export function (H1) so a deleted/renamed export fails pytest collection. New files carry metadata front matter.

**Dependencies:** US-49

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. AC-51.1: all 5 schema fields are named (mint, author, tags[], note, created_at); the 'never writes back into raw lake' invariant (§6.4.1) is tested directly; categorical tag set is config-driven (Principle #1); multiple-annotations-per-mint retention is explicitly required and testable. AC-51.2: export format mirrors the US-36/US-31 MANIFEST pattern already validated — dataset id, content hash, LC_ALL=C sort, SHA-256 of decompressed bytes — so the bar is known and replicable; the Celery-task structural assertion (not a web view, per #289) matches the pattern used in US-31/US-38; byte-identical run-twice requirement is concrete. AC-51.3: no-auto-start guard (US-11 AST guard) and idempotency are both independently verifiable; ImportError trap on named export function locks CI against silent deletion. All 3 ACs are testable and independently verifiable.

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
_Pending_

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint10.json` — do not edit directly._
