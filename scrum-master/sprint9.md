# Sprint 9

**Phase:** planning
**Progress:** 0/6 stories | 1/18 ACs
**Last Updated:** 2026-06-17T05:15:12+00:00

## Sprint Goal
Open P7 — the scorer/serving path: the LAST phase before the whole-project endgame (the operator can PROMOTE a model — current best trilly_pregrad_v3_2 — and START THE FIREHOSE to make live predictions). P0–P6 are closed: the VPS staging stack is live on 8002 (P0), the config core is the single source of truth (P1), detection lands graduated 'tokens' (P2), the tape recorder captures every PumpSwap swap from t0 into the immutable jsonl.gz lake + 'swaps' mirror with live↔backfill byte-parity (P3), the score-time snapshot + three-unit lock (P4), the integrity core — one vendored feature library (tape_microstructure.py), one shared deterministic extractor serving live+offline+replay, and the T0/G1/G2 golden-parity hard merge gate making live==offline BY CONSTRUCTION (P5) — and, as of sprint-8, the Helius program-wide BIRTH-TAPE source (helius_live) that captures every token's COMPLETE pre-graduation tape at the graduation instant (P6), the prerequisite that makes v3.2's 20 pre_* features live-computable AT ALL. What remains between here and the endgame is the SERVING PATH: solanaTrilly currently has NO trading model committed (the only models/ artifact is DevRAG's gitignored model.onnx sentence-embedder, used by NO project code — NOT a trading artifact) and NO LightGBM dependency. v3.2 (solanatrills/models/trilly_pregrad_v3_2/) is a SEED-BAGGED 3-LABEL RANK-AVERAGE BLEND of 15 LightGBM boosters: 3 labels (ctrl=clip(tr30_t1800_25), oracle=clip(oracle_25), liq=log1p(cumbv_peak)) x 5 seeds (s0–s4), files boosters/<label>_s<seed>.txt, over the 20 enrich20_buyer_cohort pre_* features in meta.json. The blend recipe (meta.json selection_recipe): for EACH label, average the 5 seed boosters' raw predictions; percentile-rank that label-score across the candidate POOL; blend = mean of the 3 percentile-ranks; pick the top-K/day by blend. THE CUTOVER RISK (oracle §4.2, 'the gap most likely to surprise'): the percentile-rank is POOL-RELATIVE, but live serving scores ONE token at its graduation instant with no same-day pool yet — the serving path MUST define a deterministic reference distribution / pool handling so the live blend score is reproducible and parity-checkable against the offline blend. Deliver, IN ORDER: (US-40 / H1) FIX THE SPRINT-8 DEPLOY GAP AT ITS ROOT — the deliberate sprint-8-boundary deploy failed 3x at deploy.yml's inline 'Run tests with coverage' (exit 1) while the standalone canonical ci.yml passed the SAME tests on the SAME commit (the H1 'single canonical test job — no second workflow' principle violated in the deploy path); unify the deploy gate so the deploy DEPENDS ON the already-green canonical ci.yml 'test' job rather than re-running a divergent inline copy, then re-run the HEAD deploy and Tester-confirm 200-on-8002 + listener Up (driving helius_live) + solanaBilly untouched on 8001 from a GREEN run — closing the only outstanding sprint-8 DoD item; (US-41 / P7-1) FEATURE-CONTRACT RECONCILIATION (oracle §4.1, PRD §7.4): the P5 FeatureSet (US-29/US-30) must reconcile COLUMN-FOR-COLUMN and IN BOOSTER ORDER against v3.2's actual feature list — the 20 pre_* features in solanatrills/models/trilly_pregrad_v3_2/meta.json (and each booster's feature_name()); PRD §7.4 makes model.feature_list == booster.feature_name() the BINDING order. The P5 live_servable[] set must cover ALL 20 pre_* features with a LIVE computation (the birth-tape source US-34 is what makes the pre_* window live-computable); diff live_servable[] against meta.json:features as an EXPLICIT acceptance check, and the promoter REFUSES on any mismatch (missing feature, order divergence, or a feature that is training_only). solanaTrilly stays MODEL-AGNOSTIC — the contract is enforced against whatever model is loaded, not a hardcode of v3.2; (US-42 / P7-2a) THE model_registry + BLEND WRITE CONTRACT (oracle §4.2, PRD §7.4): a model_registry persistence target + a promote_model.py that ACCEPTS A lightgbm_regression BLEND ARTIFACT (15 boosters + the rank-average blend transform + the labels/seeds manifest + the bound feature_list), NOT just a single model — with the feature_list==feature_name() binding-order gate from US-41 enforced at write time (refuse on mismatch). Add LightGBM to the scorer container requirements (Docker Rules — in-container, never host); (US-43 / P7-2b) THE 15-BOOSTER RANK-AVERAGE BLEND SERVING PATH: ONE shared deterministic scorer that loads the 15 boosters, computes per-label seed-average then the cross-pool percentile-rank then mean-of-3-ranks blend EXACTLY per meta.json:selection_recipe, all config-driven (Principle #1, get_active_config()/core/schemas.py — no literals, no scattered os.getenv). RESOLVE THE CUTOVER RISK: define and implement the deterministic reference-pool/percentile handling for live one-at-a-time scoring (a frozen reference distribution banked from training and/or a rolling daily pool), documented and config-driven, so a live score is reproducible; (US-44 / P7 OFFLINE GATE — SCORER PARITY, the integrity clause): live==offline scoring BY CONSTRUCTION — bank v3.2's golden score vectors (per CLAUDE.md every promoted model ships its own golden_scores.parquet; repro via solanatrills v32_build.py) as the permanent offline oracle, and prove the serving path reproduces them within the documented tolerance GIVEN the same feature inputs AND the same reference pool; FOLD into the EXISTING combined T0/G1/G2 parity suite via the ImportError trap in the single canonical ci.yml 'test' job (H1 — no parallel gate), making 'scores in sync' a STANDING Definition-of-Done line alongside 'sources in sync'; (US-45 / H2 + process) CONFIRM the G3 unforgeable ruff hook actually stops the seven-sprint I001/E501/F401 recurrence WITH EVIDENCE on sprint-9's first commits (the auto-install landed mid-sprint-8, so the proof is owed — the same 'prove the mechanism fires' rigor G4 demanded of the promoter), re-confirm the US-13 status-integrity guard + the F2 phase-promoter fire mechanically in the deploy sequence, and own the sprint-9 retrospective (A3). FIREHOSE: P7 is OFFLINE BY CONSTRUCTION — the scorer/serving path, the feature-contract reconciliation, the BLEND write contract, and the scorer-parity gate all run against banked fixtures + v3.2's frozen artifacts and require ZERO firehose activation (8 Birdeye + 8 Helius remain banked; the HARD RULE 'every activation banks a durable fixture' is untouched this sprint). DEFERRED (forward_plan, NOT committed here to avoid the over-commitment the retrospectives repeatedly warn against on heavy phases): P7-3 the head-to-head SOAK (v3.2 vs incumbent, ~30/day per the MODEL_HANDOFF depth-fragility caveat) — operator-driven, and THE ENDGAME act itself (promote + start firehose); and the P6 DASHBOARD (PRD §13, retrospective H4) — a PRD pillar the PO may schedule as a parallel/follow-on track once the promotion path is unblocked. Build order: US-40 FIRST (closes the outstanding sprint-8 DoD deploy gap; independent of the P7 chain and may run in parallel) and US-45 (process) is independent; then the P7 chain is sequential — US-41 (feature contract) -> US-42 (registry + BLEND write contract) -> US-43 (blend serving path) -> US-44 (scorer-parity gate). The offline scorer-parity gate (US-44) does NOT depend on any live read, so the firehose budget stays fully banked.

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/oracle-direction.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/sprint8.json`
- `CLAUDE.md`
- `ops/firehose_activation_log.md`
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v3_2/meta.json`
- `/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v3_2/MODEL_HANDOFF.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter (project convention)
- [ ] All services run in Docker (no host installs — Docker Rules). LightGBM and any scorer dependency are added to the in-container requirements and run inside the solanatrilly containers (NEVER installed on the host); nothing about modeling/scoring runs on the web/gunicorn process inappropriately (#289).
- [ ] CD pipeline LIVE and GREEN: every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there ('works locally' is NOT done; the deploy clause is gated at the SPRINT boundary per retrospective A2). The smoke-test retains the runtime retry-with-backoff from US-12 (AC-12.3). Per H1: the sprint-boundary deploy is gated by the SAME canonical ci.yml 'test' job that already went green — NOT a divergent inline copy in deploy.yml.
- [ ] VPS verification is IN THE LOOP and gates 'done' (retrospective B3/C1/H1): the Tester confirms from an ACTUAL green deploy run that the stack answers HTTP 200 on 8002, the 'listener' container is Up (driving the helius_live birth-tape source), and solanaBilly is untouched on 8001.
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on 8001 untouched; NO unscoped down / up --force-recreate / prune / volume-removal anywhere).
- [ ] Status integrity enforced PROGRAMMATICALLY (US-13 guard): no story/AC reads status:done while its tester_status is failed/blocked; the guard is GREEN on sprint9.json at review. Per F2/G4, phase-promotion is MECHANICAL and PROVEN to fire in the orchestrator's actual deploy sequence (tools/promote_sprint_phase.py auto-promotes or blocks with REMEDY) BEFORE any sprint-end deploy; per D3, story-level dev_status is promoted to 'done' at closeout (not exempted via --skip-complete).
- [ ] Parity by construction (Principle #2): the scorer reads features from the SAME single shared extractor (US-30) that serves live+offline+replay — no live-only or offline-only feature assembly anywhere; the vendored feature math (tape_microstructure.py) is NEVER hand-edited (re-vendor to update). A model's LIVE score equals its OFFLINE score by construction.
- [ ] Scores in sync — a NEW STANDING Definition-of-Done line (extends 'sources in sync', oracle §3/§4): no serving path ships without (a) passing the scorer-parity gate against the model's banked golden_scores vectors within the documented tolerance and (b) the feature-contract reconciliation (live_servable[] covers ALL of the model's features, column-for-column and in booster order). The scorer-parity check is wired into the single canonical ci.yml 'test' job (H1 — no second workflow) via a compile-time ImportError trap on the named functions (mirroring AC-21.3/AC-32.3/AC-36.3) so a deleted/renamed parity test fails pytest collection.
- [ ] Binding feature order (PRD §7.4): model.feature_list == booster.feature_name(), enforced as a gate in promote_model.py — the promoter REFUSES a model whose feature_list does not match the booster order or whose features are not all live_servable.
- [ ] BLEND artifact supported (oracle §4.2): the model_registry / promote_model.py accept a lightgbm_regression BLEND (15 boosters = 3 labels x 5 seeds + the rank-average blend transform + the labels/seeds manifest), not only a single model. solanaTrilly stays MODEL-AGNOSTIC — v3.2 is the FIRST model it must serve, never a hardcode.
- [ ] Config-driven (Principle #1): the scorer, the blend recipe parameters, the reference-pool/percentile handling, and the model selection are all read from get_active_config() / core/schemas.py — never literals in code, never scattered os.getenv.
- [ ] Raw = immutable truth (§6.4.1): scoring NEVER mutates the raw lake; features are always re-derived via the shared extractor from raw; golden score vectors are banked once and immutable.
- [ ] Firehose budget honored (§15.7): P7 is OFFLINE BY CONSTRUCTION — the serving path, feature-contract reconciliation, BLEND write contract, and scorer-parity gate require NO firehose WS activation; 8 Birdeye + 8 Helius remain banked. The HARD RULE 'every activation banks a durable fixture' holds (no activation this sprint).
- [ ] Process gates mechanized AND PROVEN (retrospective G3/G4/H2): the ruff gate (I001/E501/F401) is unforgeable AND confirmed-with-evidence to fire on sprint-9's first commits without a manual step; the F2 phase-promoter is proven to execute in the actual deploy sequence.
- [ ] retrospective.md updated for sprint-9 (named owner: Tester / scrum facilitator — retrospective A3)

## User Stories

### US-40: H1 — fix the sprint-8 deploy gap at its root: unify the deploy gate on the canonical ci.yml 'test' job + Tester-confirm a green HEAD deploy (retrospective H1)
**Status:** in-progress | **Priority:** high

#### Acceptance Criteria
- [x] **AC-40.1:** Diagnose WHY deploy.yml's inline 'Run tests with coverage' step fails (exit 1) while the standalone canonical ci.yml passes the SAME tests on the SAME commit (run 27665516309 green; sprint-8 boundary deploy runs 27665520087 / 27665535622 / 27665583551 all failed). Identify the concrete divergence (most likely a missing fixture / DB init / service / env-var difference in the Deploy workflow context vs the canonical CI context). The diagnosis is recorded (root cause, not a symptom) and verified by reproducing the divergence and the fix in CI.
  - Dev: done
- [ ] **AC-40.2:** Structural fix per the H1 principle 'single canonical test job — no second workflow': the deploy is gated by DEPENDING ON the already-green canonical ci.yml 'test' job (e.g. workflow_run/needs on the canonical job, or a reusable-workflow call to the SAME job) rather than re-running a DIVERGENT inline copy embedded in deploy.yml. There is exactly ONE test job definition that both PR-CI and the deploy gate consume. Verified by a structural test (YAML parse) asserting deploy.yml does not define a second/divergent test job and that the deploy step depends on the canonical 'test' job; the AC-39.2 phase-promoter pre-deploy gate and the AC-12.3 runtime smoke-test retry-with-backoff are preserved.
- [ ] **AC-40.3:** Re-run the deploy on 'main' at HEAD and Tester-CONFIRM from an ACTUAL GREEN deploy run: HTTP 200 on 8002 (with the AC-12.3 retry-with-backoff), the 'listener' container Up (driving the helius_live birth-tape source), and solanaBilly untouched on 8001 — closing the only outstanding sprint-8 DoD item (the deliberate sprint-boundary deploy). A partial-evidence PR-merge deploy is NOT sufficient — the deliberate boundary run at HEAD must be green. New/changed files carry metadata front matter.

**Dependencies:** US-39

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-40.1 fixed: Removed `import ast` from line 65 of `core/tests/test_deploy_gap_diagnosis_ac401.py`. The module was never used — the final test implementation used `yaml.safe_load` and string inspection instead. Committed and pushed to `feature/US-40-AC-40.1`.

**Tester Status:** approved
**Tester Notes:**
  AC-40.1 diagnosis: **Diagnosis: Code Bug — unused import**
  
  **Root cause:** `core/tests/test_deploy_gap_diagnosis_ac401.py:65` imports `ast` but never uses it. Ruff raises `F401 [*] 'ast' imported but unused`, exiting with code 1 and failing the Lint step.
  
  **Severity:** Low — a trivial one-line fix; no logic is broken, the test itself is correct.
  
  **Recommended fix:** Remove `import ast` from line 65 of `core/tests/test_deploy_gap_diagnosis_ac401.py`. The `ast` module was presumably planned for the structural test (test 4 or 5) but the final implementation used YAML parsing (`yaml.safe_load`) and string inspection instead, making `ast` dead.
  
  The fix is:
  ```python
  # Remove line 65:
  import ast
  ```
  
  No other changes needed. The remaining imports (`json`, `re`, `pathlib.Path`, `yaml`) are all used.

---

### US-41: P7-1 — feature-contract reconciliation: live_servable[] covers ALL of v3.2's 20 pre_* features, column-for-column and in booster order; promoter refuses on mismatch (oracle §4.1, PRD §7.4)
**Status:** not-started | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-41.1:** A reconciliation utility compares the P5 FeatureSet (US-29 hashed/versioned ordered columns + the US-30 live_servable/training_only split) against a model's bound feature list — for v3.2, the 20 pre_* features in solanatrills/models/trilly_pregrad_v3_2/meta.json:features AND each booster's feature_name(). PRD §7.4 makes model.feature_list == booster.feature_name() the BINDING order. The utility is MODEL-AGNOSTIC (reads the model's declared feature list; does not hardcode v3.2's columns) and reports: missing features, extra features, order divergence, and any feature present but flagged training_only. Verified by a pytest test over the v3.2 meta.json + booster feature_name() lists asserting a clean reconcile on the real 20-feature contract and a correct FAILURE report on a seeded mismatch.
- [ ] **AC-41.2:** live_servable[] coverage: ALL 20 of v3.2's pre_* features resolve to a LIVE computation through the single shared extractor (US-30) reading the birth-tape pre-graduation window (US-34) — i.e. each pre_* feature is in live_servable[], not training_only. The diff of live_servable[] against meta.json:features is an EXPLICIT acceptance check (the promotion-blocking clause: if any of the 20 is not live-computable, v3.2 is unservable). Verified by a pytest test asserting the 20 pre_* features are a subset of live_servable[] and are computed by the shared extractor over the banked birth-tape golden fixture (lake/golden/helius_birth_tape/), deterministically.
- [ ] **AC-41.3:** The reconciliation is wired as a hard gate consumed by the promoter (US-42): promote_model.py REFUSES a model whose feature_list mismatches booster order or whose features are not all live_servable, with a printed REMEDY (which feature, what kind of mismatch). The reconciliation test is wired into the single canonical ci.yml 'test' job via the ImportError trap (H1) so it cannot silently vanish. New files carry metadata front matter.

**Dependencies:** US-29, US-30, US-34

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. All 3 ACs are testable and verifiable. AC-41.1: the seeded-mismatch pattern is the established gold standard for gate tests in this project (mirrors AC-29.1, AC-32.x). AC-41.2: asserting 20 pre_* features as a subset of live_servable[] over the already-banked helius_birth_tape golden fixture is a clean offline test — no firehose required. AC-41.3: ImportError trap pattern is the established wiring convention (H1). The MODEL-AGNOSTIC requirement is correctly scoped — the utility reads from the promoted model's manifest, not a hardcoded v3.2 column list. Dependencies on US-29/US-30/US-34 are correct.

---

### US-42: P7-2a — model_registry + promote_model.py BLEND write contract: accept a lightgbm_regression BLEND (15 boosters + rank-blend transform), feature-order gate enforced (oracle §4.2, PRD §7.4)
**Status:** not-started | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-42.1:** A model_registry persistence target (table/schema + admin/audit per the P1 conventions) records a promoted model artifact: kind (e.g. lightgbm_regression_blend), the bound ordered feature_list, the labels/seeds manifest (3 labels x 5 seeds), the blend-transform descriptor (rank-average recipe params), the artifact content hash(es), and the model/feature-set version. solanaTrilly stays MODEL-AGNOSTIC — the registry stores whatever model is promoted; v3.2 is the first, never a hardcode. Verified by a pytest test that a BLEND artifact is recorded with all required fields and an at-most-one-active-model discipline (mirroring the config-activation pattern).
- [ ] **AC-42.2:** promote_model.py ACCEPTS A BLEND: it loads the 15 LightGBM boosters (boosters/<label>_s<seed>.txt for label in {ctrl,oracle,liq}, seed in 0..4) + the rank-average blend transform descriptor, NOT just a single model. LightGBM is added to the in-container scorer requirements (Docker Rules — never host). The promoter enforces the US-41 feature-order gate at write time (model.feature_list == booster.feature_name(), all live_servable) and REFUSES on mismatch with a printed REMEDY. Verified by a pytest test that promotes the real v3.2 booster set (or a faithful fixture of it) end-to-end and that a seeded feature-order/booster-count mismatch is REJECTED.
- [ ] **AC-42.3:** No silent auto-promote / no silent trading auto-start (§5.3/§15.6): promotion is an explicit, audited, idempotent action; scoring_enabled/trading_enabled remain defaulted False and no promote/registry/resolver path flips them True (the US-11 AST no-auto-start guard stays green and is extended to the promote path). Verified by a pytest/AST test that promotion does not enable scoring or trading and that re-running the promoter is idempotent. New files carry metadata front matter.

**Dependencies:** US-41, US-9

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. All 3 ACs are testable and verifiable. AC-42.1: at-most-one-active-model discipline mirrors the config-activation pattern established in US-9/US-11 — well-established precedent. AC-42.2: the 'or a faithful fixture' clause is intentional and acceptable — the real v3.2 boosters live outside the repo; a fixture covering the 3-labels x 5-seeds structure fully exercises the gate logic. The seeded mismatch rejection test is the decisive verification. AC-42.3: the AST no-auto-start guard extension is the correct mechanical check (established in US-11, extended in US-33); idempotency test is clean. LightGBM in-container requirement correctly flagged here (Docker Rules compliance).

---

### US-43: P7-2b — the 15-booster rank-average blend serving path: one shared deterministic scorer + the live reference-pool/percentile cutover resolved, config-driven (oracle §4.2)
**Status:** not-started | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-43.1:** ONE shared deterministic scorer loads the active BLEND from the registry (US-42) and computes the score EXACTLY per meta.json:selection_recipe: for each of the 3 labels, average the 5 seed boosters' raw predictions; percentile-rank that label-score; blend = mean of the 3 percentile-ranks. It reads features ONLY through the single shared US-30 extractor (Principle #2 — no scorer-local feature assembly) and is fully config-driven (Principle #1 — blend params/labels/seeds from get_active_config()/core/schemas.py, no literals). Verified by a pytest test that, over a banked feature fixture, the scorer reproduces the expected per-label seed-average and the mean-of-ranks blend deterministically (run-twice byte/tol-identical).
- [ ] **AC-43.2:** RESOLVE THE CUTOVER RISK (oracle §4.2 'the gap most likely to surprise'): the percentile-rank is POOL-RELATIVE, but live serving scores ONE token at its graduation instant with no same-day pool. Define and implement a DETERMINISTIC reference-pool/percentile handling for live one-at-a-time scoring — a frozen reference distribution banked from training and/or a rolling daily pool — documented and CONFIG-DRIVEN, so a live single-token blend score is reproducible and parity-checkable. Verified by a pytest test that the live (single-token, against the reference distribution) percentile-rank path is deterministic and matches the pool-based computation for the same inputs + reference.
- [ ] **AC-43.3:** The scorer runs behind the seam in the appropriate container (NOT the web/gunicorn process — #289) and is wired so scoring is orchestrated at score time WITHOUT enabling trading (scoring_enabled gates scoring; trading_enabled stays separate and False). The serving path is replay-testable (deterministic ReplaySource path) and the scorer tests are wired into the single canonical ci.yml 'test' job via the ImportError trap (H1). Verified by the structural wiring test + the deterministic serving test passing on the branch and at merge. New files carry metadata front matter.

**Dependencies:** US-42, US-30

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. All 3 ACs are testable and verifiable. AC-43.1: run-twice byte/tol-identical over a banked feature fixture is the established parity-test pattern. AC-43.2: the reference-distribution approach is appropriately bounded — the AC requires CONFIG-DRIVEN, documented, and a pytest proving determinism + pool-vs-single-token parity for the same inputs+reference. The dev team selects the mechanism (frozen distribution or rolling pool); the Tester verifies the contract. This is the correct division. AC-43.3: the #289 container-placement constraint (not web/gunicorn) is structural and verifiable via compose file inspection; the ImportError trap is established convention.

---

### US-44: P7 offline gate — scorer parity: live==offline scoring BY CONSTRUCTION against v3.2's banked golden score vectors, folded into the canonical parity gate (PRD §7.6/§16; 'scores in sync')
**Status:** not-started | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-44.1:** Bank v3.2's golden score vectors as the permanent offline oracle (per CLAUDE.md 'every promoted model ships its own golden_scores.parquet'; reproduce via solanatrills analysis/graduated/v32_build.py over a frozen feature input set, or bank the existing artifact's golden_scores.parquet). The fixture is committed/durable, carries a MANIFEST (dataset id, model/version, content hash; LC_ALL=C sort; SHA-256 of decompressed bytes per US-36), and is raw=immutable. Verified by a pytest test asserting the golden fixture is present, loads, and matches its MANIFEST content hash.
- [ ] **AC-44.2:** The US-43 serving path reproduces v3.2's golden score vectors within the DOCUMENTED tolerance, GIVEN the same feature inputs AND the same reference pool/distribution (the live==offline-by-construction proof for the scorer, the scorer analogue of the T0/G1/G2 swap/feature parity). Offline + deterministic over the banked golden fixture, run-twice byte/tol-identical. Verified by a pytest test asserting per-token blend-score parity to the golden vectors within tolerance and run-twice determinism. The tolerance is explicit and justified (floating-point/library determinism), not open-ended.
- [ ] **AC-44.3:** 'Scores in sync' is a HARD MERGE GATE and a STANDING DoD line (extends 'sources in sync'): the scorer-parity check FOLDS into the EXISTING combined T0/G1/G2 parity suite in the single canonical ci.yml 'test' job (H1 — no parallel gate) and is wired via a compile-time ImportError trap on the named functions (mirroring AC-32.3/AC-36.3) so a deleted/renamed scorer-parity test fails pytest collection. Verified by the combined suite (now including scorer parity) passing on the branch and at merge. New files carry metadata front matter.

**Dependencies:** US-43, US-32

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. All 3 ACs are testable and verifiable. AC-44.1: MANIFEST content-hash check mirrors the established US-36 pattern; the 'present + loads + matches hash' triple is the correct fixture integrity gate. AC-44.2: 'explicit and justified tolerance' requirement is critical and correctly stated — the Tester will verify the tolerance claim is documented inline with the test, not just a magic number. The 'same reference pool/distribution' constraint correctly ties scorer parity to the US-43 reference-distribution resolution. AC-44.3: folding into the existing combined T0/G1/G2 suite (not a second CI job) is the H1 principle; ImportError trap is established. Dependency on US-43 is correct — the serving path must exist before parity can be proven.

---

### US-45: Process — H2: confirm the G3 unforgeable ruff hook stops the recurrence WITH EVIDENCE + re-confirm the status-integrity guard / phase-promoter fire in the deploy sequence (retrospective H2/D3/G4)
**Status:** not-started | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-45.1:** H2 — CONFIRM the G3 unforgeable ruff hook actually stops the seven-sprint I001/E501/F401 recurrence, with EVIDENCE on sprint-9's first commits (the auto-install via .devcontainer postCreate + the CI install-hooks.sh step landed mid-sprint-8, so the proof is owed). Verified by (a) a pytest test asserting the hook is auto-present without any manual 'make install-hooks' step and fires locally on a seeded I001/E501/F401 then passes once fixed (re-confirming AC-39.1), and (b) a recorded observation that sprint-9's commits did NOT incur a ruff CI defect attributable to a missing local hook — the same 'prove the mechanism fires' rigor G4 demanded of the promoter (assumption replaced by evidence).
- [ ] **AC-45.2:** Re-confirm the F2 phase-promoter (tools/promote_sprint_phase.py) fires as a MECHANICAL pre-deploy step in the actual deploy sequence (auto-promote a stale phase or block with REMEDY) on the sprint-9 deploy — closing the loop end-to-end again now that US-40 unifies the deploy gate — and the US-13 status-integrity guard is GREEN on sprint9.json (no status:done while tester_status is failed/blocked; story-level dev_status promoted to 'done' at closeout per D3, NOT exempted via --skip-complete). Verified by the structural test (promoter wired before deploy) + the US-13 guard green on sprint9.json + the sprint-9 deploy run showing the promoter fired.
- [ ] **AC-45.3:** retrospective.md updated for sprint-9 (named owner: Tester / scrum facilitator — retrospective A3): what went well / what didn't / action items, including the H2 evidence verdict (did the unforgeable hook stop the recurrence?) and the P7 outcome (is the serving path complete and the operator unblocked to soak + promote?). After updating any /scrum-master/ doc, re-index via mcp__devrag__reindex_document (project convention). New files carry metadata front matter.

**Dependencies:** US-39

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved. All 3 ACs are testable and verifiable. AC-45.1: part (a) is a mechanical pytest (established in AC-39.1); part (b) is a retrospective observation verifiable at sprint-end from CI run history — a time-bound evidence check, not a subjective judgment. AC-45.2: the triple verification (structural test + US-13 guard green + deploy run evidence) is the same 'proven in the sequence' standard G4 established; correct. AC-45.3: retrospective update is verifiable from file contents; the re-index requirement is the project convention for scrum-master docs. No scope issues — this story is correctly scoped as process confirmation, not new implementation.

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
All 6 stories / 18 ACs approved for sprint-9 planning. Requirements are testable and verifiable throughout. Key observations: (1) All ACs follow established project patterns — seeded-mismatch rejection tests, ImportError traps, run-twice byte/tol-identical checks, MANIFEST content-hash verification. No novel verification approaches that introduce uncertainty. (2) The cutover risk resolution (AC-43.2) is appropriately scoped: the mechanism is left to the dev team (frozen distribution vs rolling pool) while the CONTRACT is pinned (config-driven, documented, determinism + pool-vs-single-token parity pytest). (3) Firehose budget constraint is enforceable — all gates run against already-banked fixtures (helius_birth_tape golden fixture from US-34; v3.2 golden_scores.parquet banked in US-44). (4) No stories are requirements-defects. (5) The 'Scores in sync' standing DoD line is correctly scoped as an extension of 'Sources in sync' — same mechanism, same wiring, new domain.

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint9.json` — do not edit directly._
