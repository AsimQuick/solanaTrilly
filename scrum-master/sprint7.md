# Sprint 7

**Phase:** planning
**Progress:** 3/6 stories | 10/18 ACs
**Last Updated:** 2026-06-17T00:00:00+00:00

## Sprint Goal
Open P5 — the lake + extraction contract + vendored feature math + the Feature Builder + the T0/G1/G2 golden-parity merge gate (PRD §6.4 / §7.2 / §7.6 / §16; retrospective F1). P0–P4 are closed: the VPS staging stack is live on 8002, the config core (P1) is the single source of truth, P2 detection lands graduated 'tokens', P3's tape recorder captures every PumpSwap swap from t0 into the immutable jsonl.gz lake + the queryable 'swaps' mirror with live↔backfill byte-parity, and P4 takes exactly one on-demand score-time snapshot per token with the three-unit (vol_sol/vol_usd/sol_usd) lock. P5 is THE integrity core (§6.4): it makes a model's live score EQUAL its offline score by construction — one vendored feature library, one deterministic extractor serving live + offline + replay, and a golden-parity gate that fails any PR that breaks byte-identity. This is the fix for the evidenced solanaBilly disaster where the t2 go-live got 23% live precision vs 78% offline purely from feature-assembly drift (#358/#359/#367). Deliver, IN ORDER: (1) VENDOR THE FEATURE MATH (§7.2) — copy solanabilly3/src/tape_microstructure.py VERBATIM into the repo (the ONE sanctioned edit is rels.ptp() → np.ptp(rels) for numpy 2.x), preserving compute_features(swaps, window_s=120, bucket_s=15) -> dict|None and the full tape_* family; never hand-edit (re-vendor to update) — and prove it with the G1 function-parity gate (§7.6): the frozen ≈15-token golden fixture (solanabilly3/data/microstructure/golden_fixture_t0t2_15s.json) runs through the vendored math and every feature matches within 1e-9 (US-28); (2) the EXTRACTION CONTRACT — the 'feature_sets' table (§8, §6.4.5): a FeatureSet is a hashed config (ordered columns + vendored-math version) with the live_servable vs training_only split (D2/D3) so same raw + same FeatureSet → byte-identical output (US-29); (3) the SHARED DETERMINISTIC EXTRACTOR (§6.4.4/§6.4.5) — read raw normalized swaps from the lake/'swaps' mirror, feed the §7.1 subset {rel, price, side, vol, owner} to the vendored compute_features, ONE code path for live + offline + replay (Principle #2), leak-free by the causal cutoff (hard-reject any swap with rel ≥ window_s; labels start strictly after), deterministic and versioned (US-30); (4) the FEATURE BUILDER (§6.5) — a one-click Celery export: pick a FeatureSet + cohort + label def → run the SAME vendored extractor over the lake → CSV/parquet + a manifest (FeatureSet version+hash, source(s), date range, mint cohort, row count, content hash, label def) that reproduces the export exactly; runs off the dedicated celery container, NEVER web/gunicorn (#289) (US-31); (5) the T0/G2 SOURCE-PARITY GATE (§7.6, §16) — the #1 merge gate (§6.4.3): Birdeye live↔backfill byte-identical normalized swaps + features (the seam that actually serves; offline, no firehose) PLUS a Helius raw-truth cross-check that confirms Birdeye coverage/side/amount — wired as a hard merge gate in the single canonical ci.yml 'test' job via the ImportError trap (US-32). FIREHOSE: P5's offline gates (G1, G2(a) live↔backfill) require NO activation — G2(a) uses the banked sprint-5 captures (E6ifp2…pump / H9L9…pump) + an on-demand Birdeye seek_by_time REST backfill (a REST read within the professional allowance, NOT a firehose WS activation); G2(b) raw-truth is the project's ONE budgeted G2 activation (CLAUDE.md — 'G2 needs one activation'): a single deliberate, time-boxed, ledgered Helius activation banks the durable raw-truth fixture (Helius 10→9 at most), after which the CI cross-check runs OFFLINE against the banked fixture forever and the P5 gate never depends on a live read (8 Birdeye remain untouched). PROCESS HARDENING (US-33) — the carries that a checklist has demonstrably failed to fix: F2 MECHANIZE phase-promotion (the stale-'phase' deploy failure has tripped the US-13 integrity guard THREE consecutive sprints D2→E1→F2 — make it a mechanical pre-deploy step / CI pre-check, not a step a human remembers); F4 MECHANIZE the ruff gate (a real pre-commit hook or container-entrypoint/Makefile ruff step — the I001/E501/F401 churn has recurred FIVE sprints); F3 close the carried sprint-6 DoD VPS clause — the first sprint-7 deploy at true HEAD is a clean green run, Tester-confirmed 200-on-8002 + listener Up + solanaBilly untouched on 8001 (re-issue of E5 / US-22 condition B). DEFERRED: F5 (build the live Birdeye REST SnapshotDataSource adapter) is consciously deferred to sprint-8, scheduled near where the scorer (P7) consumes it — it is a single on-demand REST read within the Birdeye allowance (NOT a firehose activation) and is off the P5 critical path; keeping sprint-7 focused on the integrity core avoids the over-commitment the retrospectives repeatedly warn against. Build order: US-28 FIRST (the vendored math everything downstream eats) — US-29 (FeatureSet table) and US-33 (process) are independent and may run in parallel; then US-30 (extractor) needs US-28 + US-29 (+ the US-19 lake reader); then US-31 (Feature Builder) needs US-30 and US-32 (T0/G2 parity) needs US-28 + US-30.

## Reference Documents
- `scrum-master/PRD.md`
- `scrum-master/retrospective.md`
- `scrum-master/scrum-master.md`
- `scrum-master/sprint6.json`
- `CLAUDE.md`

## Definition of Done
- [ ] All ACs verified by CI / Tester
- [ ] No critical defects
- [ ] Coverage threshold met (>=80%)
- [ ] Code file headers include metadata front matter (project convention)
- [ ] All services run in Docker (no host installs — Docker Rules); the Feature Builder export runs off the dedicated 'celery' container (NEVER the web/gunicorn process — the #289 lesson), defined in docker-compose.yml AND docker-compose.staging.yml and brought up on the VPS.
- [ ] CD pipeline LIVE and GREEN: every story is merged + deployed to the VPS solanatrilly staging stack (-p solanatrilly, port 8002) and smoke-tested there ('works locally' is NOT done; the deploy clause is gated at the SPRINT boundary per retrospective A2). The smoke-test retains the runtime retry-with-backoff from US-12 (AC-12.3).
- [ ] VPS verification is IN THE LOOP and gates 'done' (retrospective B3/C1): the Tester confirms from an ACTUAL green deploy run that the stack answers HTTP 200 on 8002, the 'listener' container is up, and solanaBilly is untouched on 8001. This closes the carried sprint-6 DoD VPS clause (F3 / US-33 AC-33.3).
- [ ] Hard isolation from live solanaBilly preserved (every docker command scoped with -p solanatrilly; solanaBilly on 8001 untouched; NO unscoped down / up --force-recreate / prune / volume-removal anywhere).
- [ ] Status integrity enforced PROGRAMMATICALLY (US-13 guard): no story/AC reads status:done while its tester_status is failed/blocked; the guard is GREEN on sprint7.json at review. Per F2/US-33, phase-promotion is now MECHANICAL — the sprint 'phase' (and story dev_status) are promoted to their real values automatically (a pre-deploy step / CI pre-check) BEFORE any sprint-end deploy so the guard is never tripped by our own staleness (the failure that recurred D2→E1→F2); per D3, story-level dev_status is promoted to 'done' at closeout (not exempted via --skip-complete).
- [ ] Parity by construction (Principle #2): the feature math is VENDORED VERBATIM from solanabilly3/src/tape_microstructure.py (the only sanctioned edit is rels.ptp() → np.ptp(rels)) and is NEVER hand-edited (re-vendor to update); the single shared extractor (US-30) is the one code path imported by the live scorer, the offline CSV builder, and the replay harness — no live-only or offline-only feature assembly anywhere.
- [ ] Golden parity is a HARD MERGE GATE (§6.4.3 — 'the #1 gate'): the combined T0/G1/G2 parity suite (G1 function parity within 1e-9; G2(a) Birdeye live↔backfill byte-identity; G2(b) Helius raw-truth cross-check over a banked fixture) passes in the single canonical ci.yml 'test' job (H1 — no second workflow) and is wired via a compile-time ImportError trap on the named functions (mirroring AC-21.3) so a deleted/renamed parity test fails pytest collection.
- [ ] Raw = immutable truth (§6.4.1): features are ALWAYS re-derivable from the raw lake — change a feature definition and rebuild, never re-collect; the extractor reads raw verbatim and never mutates it.
- [ ] Leak-free by the causal cutoff (§6.4.4): every feature is stamped with its [0, window_s] window; the extractor hard-rejects any swap with rel >= window_s; labels start strictly after window_s. Enforced in code and tested.
- [ ] Deterministic, versioned extraction (§6.4.5): a FeatureSet is a hashed config (ordered columns + vendored-math version); same raw + same FeatureSet → byte-identical output (run-twice byte-identical); every export carries a manifest that reproduces it exactly.
- [ ] Firehose budget honored (§15.7): P5's offline gates (G1 + G2(a) live↔backfill) require NO firehose WS activation — G2(a) uses banked sprint-5 captures + an on-demand Birdeye seek_by_time REST backfill (a REST read within the professional allowance, not a firehose activation). G2(b) raw-truth is at most ONE deliberate, time-boxed, ledgered Helius activation (ops/firehose_activation_log.md) that banks a durable raw-truth fixture (Helius 10→9 at most); the CI gate then runs offline against the banked fixture and NEVER depends on a live read. 8 Birdeye remain untouched. The HARD RULE 'every activation banks a durable fixture' holds.
- [ ] Process gates mechanized (retrospective F2/F4): phase-promotion is a mechanical pre-deploy/CI step (not a checklist); the ruff gate (I001/E501/F401) is a real pre-commit hook or container-entrypoint/Makefile step caught locally before push.
- [ ] retrospective.md updated for sprint-7 (named owner: Tester / scrum facilitator — retrospective A3)

## User Stories

### US-28: P5 — vendor tape_microstructure.py VERBATIM (§7.2) + the G1 function-parity golden gate (§7.6, §16)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-28.1:** The feature math is vendored VERBATIM from solanabilly3/src/tape_microstructure.py into the repo (e.g. core/tape_microstructure.py), applying ONLY the one sanctioned edit rels.ptp() → np.ptp(rels) (numpy 2.x), preserving the compute_features(swaps, window_s=120, bucket_s=15) -> dict|None signature and the full tape_* feature family (tape_n_trades, tape_n_unique_traders, tape_ret_total, tape_max_drawdown, tape_logprice_slope_per_s, tape_close_b{0..n}, …). A metadata front-matter header is added as a top-of-file comment WITHOUT altering the math body. Verified by a pytest test that imports compute_features and calls it over a small in-memory swap list, asserting it returns a non-None dict with the expected tape_* keys. A re-vendor note in the header documents that updates re-copy from solanabilly3/src/ and the file is NEVER hand-edited.
  - Dev: done
- [x] **AC-28.2:** G1 function parity (§7.6): the frozen ≈15-token golden fixture (solanabilly3/data/microstructure/golden_fixture_t0t2_15s.json, banked VERBATIM into the repo golden set with its provenance — regenerator solanabilly3/scripts/make_microstructure_golden.py — recorded) is run through the vendored compute_features and EVERY feature matches the fixture's expected value within 1e-9 absolute. Verified by a pytest test asserting abs(actual − expected) <= 1e-9 per feature across all ~15 tokens.
  - Dev: done
- [x] **AC-28.3:** The None-on-no-usable-swap contract holds and the G1 gate cannot silently vanish: compute_features returns None (recorded as no-feature, NEVER a zero row, §7.2) for an empty/degenerate swap list — verified by a test. The G1 parity test is wired into the single canonical ci.yml 'test' job (H1 — no second workflow) via a compile-time ImportError trap on the named test function (mirroring AC-21.3) so deleting/renaming it fails pytest collection. New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-17

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-28.1 done: Branch pushed. Here is the implementation summary:
  
  ---
  
  **AC-28.1 Implementation Summary**
  
  **Files changed:**
  
  1. `core/tape_microstructure.py` *(new)* — Verbatim copy of `solanabilly3/src/tape_microstructure.py` with:
     - Metadata front-matter header (module, sprint, story, vendored-from, sanctioned-edit, re-vendor-note)
     - **One sanctioned edit**: `rels.ptp()` → `np.ptp(rels)` (numpy 2.x compatibility, line 105)
     - Math body entirely unaltered; `compute_features(swaps, window_s=120, bucket_s=15) -> dict | None` signature preserved; full `tape_*` feature family intact including `tape_close_b{0..7}` buckets
  
  2. `core/tests/test_tape_microstructure_ac281.py` *(new)* — 4 pytest tests:
     - `test_compute_features_returns_nonempty_dict` — asserts non-None dict with all 22 expected `tape_*` keys
     - `test_compute_features_signature_preserved` — calls with explicit `window_s=120, bucket_s=15` kwargs
     - `test_compute_features_tape_close_buckets_present` — asserts `tape_close_b0`…`tape_close_b7` all present
     - `test_compute_features_known_values` — deterministic spot-check (n_trades, ret_total, first_swap_rel_s, time_to_first_sell_s)
  
  3. `requirements.txt` *(modified)* — Added …
  AC-28.2 fixed: **Fix summary:** Removed the dead `import pytest` from `core/tests/test_tape_microstructure_ac282.py:45`. The import was never used — no `@pytest.mark`, `pytest.raises`, or fixtures anywhere in the file. One-line deletion, pushed to `feature/US-28-AC-28.2`.
  AC-28.3 done: Pushed. 
  
  ---
  
  **Implementation Summary**
  
  **Files changed:**
  
  1. `core/tests/test_tape_microstructure_ac283.py` *(new)* — 8 tests across two deliverables:
  
     **None-on-no-usable-swap contract (6 tests):**
     - `test_compute_features_returns_none_for_empty_list` — `compute_features([])` → `None`
     - `test_compute_features_returns_none_not_empty_dict` — verifies sentinel is `None`, not `{}`
     - `test_compute_features_returns_none_for_all_out_of_window_swaps` — all `rel >= window_s` → `None`
     - `test_compute_features_returns_none_for_swaps_at_exact_window_boundary` — `rel == window_s` excluded (strict `<`) → `None`
     - `test_compute_features_returns_none_for_zero_price_swaps` — `price == 0` filtered → `None`
     - `test_compute_features_returns_none_for_missing_price_swaps` — missing `price` key → `None`
  
     **G1 gate CI wire (2 tests + module-level ImportError trap):**
     - Module-level imports of all 4 G1 parity functions by exact name from `test_tape_microstructure_ac282.py` — deleting/renaming any raises `ImportError` at collection time
     - `test_g1_gate_functions_are_callable` — human-readable companion confirming callables
     - `test_g1_gate_wired_in_single_canonical_test …
  [DEPLOY] VPS deploy PASSED (run 27631810933)

**Tester Status:** approved
**Tester Notes:**
  AC-28.2 diagnosis: Found it. The failure is clear.
  
  ---
  
  **Diagnosis: Code Bug — F401 Unused Import**
  
  **Type:** Code bug (not a requirements issue)
  
  **Severity:** Low — single-line, auto-fixable
  
  **File:** `core/tests/test_tape_microstructure_ac282.py:45`
  
  **Error:**
  ```
  F401 `pytest` imported but unused
  ```
  
  `import pytest` was added to the test file but never used — the tests only call `compute_features` directly and use bare `assert` statements. There are no `@pytest.mark` decorators, `pytest.raises`, `pytest.fixture`, or any other `pytest` symbols in the file. The import is dead weight.
  
  **Recommended fix:** Remove line 45 (`import pytest`) from `core/tests/test_tape_microstructure_ac282.py`. That's the entire change needed. `ruff check --fix` would apply it automatically.
  
  No requirements changes, no fixture changes, no logic changes needed. The golden fixture and all four parity tests are correct — only the stray import needs to go.

---

### US-29: P5 — the 'feature_sets' table + the deterministic hashed/versioned FeatureSet + live_servable/training_only split (§6.4.5, §8, D2/D3)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-29.1:** A FeatureSet Django model (table 'feature_sets', PRD §8) holds the extraction contract with columns: id, version, math_version (the vendored tape_microstructure version from US-28), columns[] (the ordered feature column list), live_servable[] (the subset servable at live score time, D2), hash, notes. 'docker compose run --rm web python manage.py makemigrations' + 'migrate' apply cleanly against real Postgres; verified by a pytest test that creates a feature_sets row and reads every column back. New files carry metadata front matter.
  - Dev: done
- [x] **AC-29.2:** Deterministic content hash (§6.4.5 — 'a FeatureSet is a hashed config'): the hash is a pure function of the ordered columns[] + math_version. Identical inputs → identical hash; reordering columns OR bumping math_version changes the hash. Verified by a pytest test (same config → same hash; reorder → different hash; math_version bump → different hash). This is the 'same raw + same FeatureSet → byte-identical output' anchor that US-30/US-31 build on.
  - Dev: done
- [x] **AC-29.3:** The live_servable vs training_only split is recorded as data on the row (D2/D3): live_servable[] is a subset of columns[], and any feature lacking a live computation is training_only (present in columns[], absent from live_servable[]). Verified by a pytest test asserting live_servable ⊆ columns and that a designated training-only column is excluded from live_servable. A read-only Django admin (changelist + detail) returns HTTP 200 for an authenticated staff user. (The P1/§5.2 config validator that REJECTS an active model whose contract ⊄ live_servable already exists; this story establishes the column-set it checks against.) New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-5, US-9

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-29.1 fixed: **Fix:** Removed a spurious extra blank line after the import block in `core/tests/test_feature_set_ac291.py` (lines 15–18). Ruff's I001 rule requires exactly one blank line separating import sections from the rest of the file — two blank lines was the violation. Applied via `ruff check --fix`, committed, and pushed to `feature/US-29-AC-29.1`.
  AC-29.2 done: Done. Here is the implementation summary:
  
  ---
  
  **AC-29.2 Implementation Summary**
  
  **Files changed:**
  
  1. `core/models.py` *(modified)* — Added `import hashlib` and `import json` at module level; added `FeatureSet.compute_hash(columns, math_version) -> str` static method. The method JSON-serializes `{"columns": columns, "math_version": math_version}` with compact separators and returns the SHA-256 hex digest (64 chars). Column order is encoded in the JSON array, so reordering changes the hash. Story header updated to include `US-29 AC-29.2`.
  
  2. `core/tests/test_feature_set_ac292.py` *(new)* — 10 pure-unit pytest tests (no DB required) covering:
     - `test_same_inputs_produce_same_hash` — identical inputs → identical hash
     - `test_hash_is_64_hex_chars` — output is a valid 64-char hex string
     - `test_reorder_columns_changes_hash` — swapping two columns → different hash
     - `test_reverse_columns_changes_hash` — reversing column list → different hash
     - `test_math_version_bump_changes_hash` — bumping math_version → different hash
     - `test_different_math_versions_all_differ` — 4 distinct versions → 4 distinct hashes
     - `test_single_column_reorder_changes_hash` — minimal reo …
  AC-29.3 done: Pushed successfully.
  
  ---
  
  **Implementation Summary**
  
  **Files changed:**
  
  1. `core/admin.py` *(modified)* — Added `FeatureSetAdmin` class registered with `@admin.register(FeatureSet)`. Fully read-only: all fields listed in `readonly_fields`, `has_add_permission` returns `False`, `has_delete_permission` returns `False`. Changelist displays `id`, `version`, `math_version`, `hash`. Updated metadata header to include US-29 AC-29.3. Added `FeatureSet` to the model imports.
  
  2. `core/models.py` *(modified)* — Metadata header updated to include `US-29 AC-29.3` in the story list (no logic changes; the model already had `live_servable` from AC-29.1).
  
  3. `core/tests/test_feature_set_ac293.py` *(new)* — 10 pytest tests with metadata front matter:
     - `test_live_servable_is_subset_of_columns` — `live_servable ⊆ columns` (pure logic)
     - `test_live_servable_is_strict_subset` — confirms training-only features exist
     - `test_training_only_column_in_columns_not_in_live_servable` — designated training-only column (`tape_max_drawdown`) present in `columns`, absent from `live_servable`
     - `test_all_live_servable_columns_are_in_columns` — per-element subset check
     - `test_training_only_featu …
  [DEPLOY] VPS deploy PASSED (run 27647472152)

**Tester Status:** approved
**Tester Notes:**
  AC-29.1 diagnosis: Got the exact error. Here is my diagnosis:
  
  ---
  
  ## Diagnosis: Code Bug — I001 Import-Sorting Violation
  
  **Type:** Code bug (not a requirements issue)
  
  **Severity:** Low — single auto-fixable file, no logic changes needed
  
  **File:** `core/tests/test_feature_set_ac291.py:15`
  
  **Error:**
  ```
  I001 [*] Import block is un-sorted or un-formatted
    core/tests/test_feature_set_ac291.py:15:1
    import pytest          ← line 15
                           ← line 16 (blank)
    from core.models import FeatureSet   ← line 17
  ```
  
  **Root cause:** ruff's isort (rule I001) considers the two-line import block (lines 15–17, including the blank separator) to be malformatted. The ruff config has `known-first-party = ["config", "core", "tools"]`, so `pytest` (third-party) and `from core.models` (first-party) should be in separate isort sections — but the specific blank-line placement or section boundary is not what ruff's formatter produces, causing it to flag the entire block.
  
  **Note:** Migrations are excluded from linting (`extend-exclude = ["*/migrations/*"]`), so `0009_featureset.py` is not a factor. `models.py` is clean. The test file is the only source of the failure.
  
  **Recommended fix:** Run `ruff …

---

### US-30: P5 — the shared deterministic extractor: raw lake → vendored compute_features → feature values, ONE code path (Principle #2), leak-free causal cutoff (§6.4.4/§6.4.5)
**Status:** done | **Priority:** high

#### Acceptance Criteria
- [x] **AC-30.1:** ONE extractor, all callers (Principle #2). A FeatureExtractor reads normalized swaps for a mint from the lake / 'swaps' mirror, orders them by the stable (block_time, slot, signature) key, and feeds the §7.1 subset {rel, price, side, vol, owner} to the vendored compute_features for a given FeatureSet → feature values. The SAME extractor code path serves live and offline — no live-only or offline-only assembly. Verified by a pytest test that the extractor over DB 'swaps' rows and over the jsonl.gz lake for the SAME token yields byte-identical feature values (closing the assembly-drift class #358/#359/#367 by construction).
  - Dev: done
- [x] **AC-30.2:** Leak-free by the causal cutoff (§6.4.4): the extractor hard-rejects (EXCLUDES) any swap with rel >= window_s before computing features; each feature is stamped with its [0, window_s] window; labels start strictly after window_s. Verified by a pytest test: a swap at rel == window_s and one at rel > window_s are both excluded, a swap at rel == window_s − epsilon is included, and the feature value equals the value computed over the windowed subset.
  - Dev: done
- [x] **AC-30.3:** Deterministic + versioned (§6.4.5): same raw + same FeatureSet → byte-identical output (run twice → identical), and the output is keyed to the FeatureSet hash/math_version. Verified by a run-twice byte-identical pytest test; the extractor determinism + leak-cutoff tests are wired into the single canonical ci.yml 'test' job via the ImportError trap (H1). New files carry metadata front matter.
  - Dev: done

**Dependencies:** US-28, US-29, US-19

**Dev Team Status:** not-started
**Dev Team Notes:**
  AC-30.1 done: Pushed successfully. Here is the implementation summary for the orchestrator:
  
  ---
  
  **AC-30.1 Implementation Summary**
  
  **Files changed:**
  
  1. `core/feature_extractor.py` *(new)* — `FeatureExtractor` class implementing the ONE shared deterministic extractor (Principle #2, PRD §6.4.4/§6.4.5). Two source adapters (`extract_from_db`, `extract_from_lake`) both normalize to the §7.1 dict format (`{rel, price, side, vol, owner, block_time, slot, signature}`) and delegate to the single `_compute()` staticmethod which calls the vendored `compute_features`. `vol_sol` is used as the `vol` field on both paths (PumpSwap quote unit). Stable sort by `(block_time, slot, signature)` applied before compute; equal-rel tiebreaking is preserved by Python's stable sort inside `compute_features`. Mint filtering for the lake path requires `mint` key in lake rows.
  
  2. `core/tests/test_feature_extractor_ac301.py` *(new)* — 8 pytest tests with metadata front matter:
     - `test_feature_extractor_db_vs_lake_byte_identical` — **THE KEY TEST**: writes identical 6-swap dataset to DB and jsonl.gz lake, asserts `dict == dict` (byte-identical) for the same mint from both sources
     - `test_feature_extractor_db_only …
  AC-30.2 done: **Implementation Summary**
  
  **Files changed:**
  
  1. `core/feature_extractor.py` *(modified)* — Updated `_compute()` to explicitly pre-filter swaps with `rel >= window_s` before calling `compute_features` (making the causal cutoff auditable at the extractor layer, not just inside vendored math). After getting the feature dict, adds `_window_s` key to stamp every result with its `[0, window_s)` extraction window. Story header updated to include `AC-30.2`.
  
  2. `core/tests/test_feature_extractor_ac302.py` *(new)* — 7 pure-unit pytest tests (no DB required):
     - `test_cutoff_excludes_swap_at_rel_equals_window_s` — swap at `rel == window_s` is excluded; `n_trades == 1`
     - `test_cutoff_excludes_swap_beyond_window_s` — swap at `rel > window_s` is excluded; `n_trades == 1`
     - `test_cutoff_includes_swap_just_before_window_s` — swap at `rel == window_s − 1e-9` is included; `n_trades == 2`
     - `test_feature_value_equals_windowed_subset` — extractor features are byte-identical to `compute_features` called directly over in-window swaps (value equality, not just count)
     - `test_feature_stamped_with_window_s` — returned dict carries `_window_s == window_s`
     - `test_feature_stamped_with_no …
  AC-30.3 done: The commit is on the correct branch. The only unstaged change is `scrum-master/sprint7.md` which is owned by the orchestrator — correctly left alone.
  
  ---
  
  **Implementation summary for the orchestrator:**
  
  **Files changed:**
  
  1. `core/feature_extractor.py` *(modified)* — Updated story header to include AC-30.3. Modified `extract_from_db` and `extract_from_lake` to stamp the output dict with `_feature_set_hash` and `_math_version` from the FeatureSet (keyed to the extraction contract per §6.4.5). The `_compute` static method is unchanged — it remains the single compute path, still stamps `_window_s`. Backward-compatible with all AC-30.1 tests (same FeatureSet → same stamps; dict equality still holds).
  
  2. `core/tests/test_feature_extractor_ac303.py` *(new)* — Metadata front-matter header. Module-level ImportError trap pinning 4 key functions from AC-30.1 and AC-30.2 by exact name (`test_feature_extractor_db_vs_lake_byte_identical`, `test_feature_extractor_stable_sort_key_determinism`, `test_cutoff_excludes_swap_at_rel_equals_window_s`, `test_feature_value_equals_windowed_subset`). 7 tests:
     - `test_run_twice_byte_identical_static` — pure unit, no DB: `_compute` twice → byte-identi …

**Tester Status:** approved
**Tester Notes:**
  Requirements approved — all 3 ACs are testable. AC-30.1: byte-identity between DB swaps and jsonl.gz lake for the SAME token is a concrete and automatable assertion — this is the Principle #2 proof point that closes the solanaBilly drift class. The (block_time, slot, signature) ordering key is consistent with AC-18 (US-18, approved). AC-30.2: three explicit cutoff boundary cases (==window_s excluded, >window_s excluded, ==window_s−epsilon included) with a value assertion — thorough boundary coverage. AC-30.3: run-twice byte-identity is concrete; ImportError trap mirrors the AC-21.3 / AC-28.3 pattern already in use.

---

### US-31: P5 — the Feature Builder: one-click CSV/parquet + manifest over the lake, off the celery container (§6.5)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-31.1:** A Celery task (the Feature Builder) takes a FeatureSet + a mint cohort + a label def and runs the SHARED vendored extractor (US-30) over the lake to produce a CSV/parquet export. The task is registered (the H2 task-manifest test confirms it) and runs off the dedicated 'celery' container (NEVER web/gunicorn — the #289 lesson), defined in BOTH docker-compose.yml and docker-compose.staging.yml. Verified by a pytest test that runs the task over a small lake fixture and asserts the CSV/parquet contents.
- [ ] **AC-31.2:** Every export carries a manifest (§6.4.5): FeatureSet version+hash, source(s), date range, mint cohort, row count, content hash, and label def. Re-running the build over the same raw + same FeatureSet reproduces the export AND the manifest content-hash EXACTLY (deterministic). Verified by a pytest test asserting the manifest fields are populated and that a run-twice produces a byte-identical content hash.
- [ ] **AC-31.3:** Leak-free labels (§6.4.4): the label is computed strictly after window_s (the outcome window, §17) and never overlaps the feature window; a build whose label def would draw from within [0, window_s] is rejected at task submission time with a clear error. The export is the labs' input only (nothing about modeling runs on the VPS). Verified by a pytest test that a label def drawing from within the feature window is rejected. New files carry metadata front matter.

**Dependencies:** US-30

**Dev Team Status:** in-progress
**Dev Team Notes:**
  AC-31.1 done: Implemented the Feature Builder Celery task and all supporting infrastructure.
  - Created `core/feature_builder.py` — `build_features_core()` function that runs the shared US-30 FeatureExtractor over the lake and writes a deterministic CSV (columns: mint, sorted tape_* keys, sorted stamp keys).
  - Added `build_features` task to `core/tasks.py` (registered as `core.tasks.build_features`); runs FeatureExtractor.extract_from_lake — the SAME code path as live scoring (Principle #2, zero assembly drift).
  - Updated `core/task_manifest.json` to include `core.tasks.build_features` (H2 gate passes).
  - Updated `docker-compose.staging.yml` to add `celery-worker` and `celery-beat` services (both use GHCR image, same network + depends_on pattern as dev compose, restart: unless-stopped with healthchecks). Both compose files now define the celery containers — #289 lesson enforced.
  - Created `core/tests/test_feature_builder_ac311.py` — 7 tests covering: manifest gate (H2), Celery registry, celery-worker in both compose files, two-mint lake fixture → CSV contents + tape_n_trades count, skip-mint-with-no-rows, and column header determinism. All 7 pass.
  - Full suite: 834 passed, 0 failures. Ruff clean.
  - blocker-type: none

**Tester Status:** approved
**Tester Notes:**
  Requirements approved — all 3 ACs are testable. AC-31.1: H2 task-manifest test (US-4 pattern) gates registration; lake fixture + content assertion gates output correctness; docker-compose presence in both files is inspectable. AC-31.2: seven manifest fields are enumerated — all verifiable by assertion; run-twice content-hash identity is concrete. AC-31.3 wording tightened (added 'at task submission time with a clear error' to pin when/how the rejection fires — minor fix applied directly). The #289 constraint (never web/gunicorn) is explicit and verifiable via the H2 manifest test.

---

### US-32: P5 — the T0/G2 source-parity gate (§7.6, §16): Birdeye live↔backfill byte-parity + Helius raw-truth cross-check, wired as a hard merge gate
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-32.1:** G2(a) live↔backfill parity (the seam that actually serves). For the banked sprint-5 golden token(s) (E6ifp2…pump / H9L9…pump), the Birdeye live-stream tape and a Birdeye seek_by_time REST backfill of the SAME token (the identical reconcile call path, US-20) produce byte-identical normalized swaps AND byte-identical compute_features output (via the US-30 extractor). The seek_by_time backfill is an on-demand REST read within the Birdeye professional allowance — NOT a firehose WS activation. Offline + deterministic. Verified by a pytest test asserting swap-level and feature-level byte-identity.
- [ ] **AC-32.2:** G2(b) raw-truth spot-check (§7.6): a Helius PumpSwap decode confirms the Birdeye tape's coverage/side/amount on the golden token(s) (verified 100% on 3 mints previously; widened here). This is the project's ONE budgeted G2 firehose activation (CLAUDE.md — 'G2 needs one activation'): a single deliberate, time-boxed, ledgered (ops/firehose_activation_log.md) Helius activation banks the durable raw-truth fixture (Helius 10→9); the CI cross-check then runs OFFLINE against the banked fixture forever and the P5 gate NEVER depends on a live read. Verified by the banked fixture + an offline parity test over it. (If the Helius window is not spent this sprint, the offline G1 + G2(a) gate still gates the merge and G2(b) carries to the next sprint — the gate is offline by construction, so an aborted/short window never blocks P5 exit.)
- [ ] **AC-32.3:** Golden parity is a HARD MERGE GATE (§6.4.3 — 'the #1 gate'). The combined T0/G1/G2 parity suite (G1 from US-28; G2(a)/G2(b) here) passes in the single canonical ci.yml 'test' job (H1 — no second workflow) and is wired via a compile-time ImportError trap on the named functions (mirroring AC-21.3) so a deleted/renamed parity test fails pytest collection. Verified by the tests passing on the feature branch and at merge. New files carry metadata front matter.

**Dependencies:** US-28, US-30, US-19, US-20

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved — all 3 ACs are testable. AC-32.1: two named golden tokens anchor the offline test; swap-level and feature-level byte-identity are separate assertions, ensuring the seam holds end-to-end. AC-32.2: the carry clause (G2(b) carries if Helius window not spent; G1+G2(a) still gates) was implicit — wording tightened to say 'carries to the next sprint' for clarity (minor fix applied directly). The ops/firehose_activation_log.md ledger requirement is a DoD constraint verifiable by the Tester. AC-32.3: ImportError trap mirrors the AC-21.3 / AC-28.3 pattern; 'passing on the feature branch and at merge' is concrete CI evidence.

---

### US-33: Process hardening — mechanize phase-promotion (F2) + the ruff gate (F4) + close the carried sprint-6 DoD VPS clause (F3)
**Status:** ready | **Priority:** high

#### Acceptance Criteria
- [ ] **AC-33.1:** F2 — phase-promotion is MECHANICAL, not a checklist. Add a pre-deploy step (in the deploy sequence) or a CI pre-check that promotes a stale sprint 'phase' to its real value (or blocks the deploy with a clear remedy message) so the US-13 integrity guard is never tripped by our own staleness. Verified by a test / dry-run: a sprintN.json reading phase:'planning' while all stories are 'done' is auto-promoted (or the deploy is deterministically blocked with the remedy printed) — reproducing and closing the exact failure mode that tripped the sprint-end deploy three consecutive sprints (D2→E1→F2; runs 27555736146/27555822147, 27593136555, 27627161427).
- [ ] **AC-33.2:** F4 — the ruff gate is MECHANIZED. A real pre-commit hook (or a 'ruff check' step in the web-container entrypoint / a Makefile dev target run inside the container) catches I001/E501/F401 locally before push. Verified by the hook/target existing and failing on a seeded lint violation, then passing once fixed — killing the I001/E501/F401 churn that has recurred for five sprints (AC-17.1/17.2/19.3/22.2, AC-23.3). New files carry metadata front matter.
- [ ] **AC-33.3:** F3 — the clean final deploy at true HEAD. The first sprint-7 sprint-boundary deploy runs GREEN on 'main' and the Tester confirms from an ACTUAL green deploy run: HTTP 200 on 8002, the 'listener' container Up, and solanaBilly untouched on 8001 — closing the carried sprint-6 DoD VPS clause (re-issue of E5 / US-22 condition B). The smoke-test retains the US-12 runtime retry-with-backoff (AC-12.3).

**Dependencies:** US-13

**Dev Team Status:** not-started

**Tester Status:** approved
**Tester Notes:**
  Requirements approved — all 3 ACs are testable. AC-33.1 wording tightened (added 'message' after 'remedy' for clarity — minor fix applied directly); the three CI run IDs are concrete regression anchors. AC-33.2: seeded-failure test is the standard pattern; the five-sprint recurrence history (AC-17.1/17.2/19.3/22.2, AC-23.3) grounds the urgency. AC-33.3: explicit three-condition Tester confirmation (200 on 8002, listener Up, solanaBilly untouched on 8001) with retry-with-backoff retention — fully verifiable from the actual deploy run. US-33 can begin in parallel with US-29 (both independent of US-28's vendored math).

---

---

## Sprint Review

### Dev Team Sprint Notes
_Pending_

### Tester Sprint Notes
Sprint-7 requirements review COMPLETE — all 6 stories / 18 ACs approved for implementation. No requirements defects; 4 minor wording fixes applied directly to ACs (AC-28.1: added assertion detail to smoke test; AC-31.3: pinned rejection to 'task submission time with a clear error'; AC-32.2: made G2(b) carry clause explicit ('carries to the next sprint'); AC-33.1: added 'message' after 'remedy'). All ACs carry explicit pytest verification criteria with concrete assertions — no vague 'it works' clauses. The P5 integrity core (US-28→30→31→32) is properly sequenced: US-28 (vendored math) is the foundation; US-29 and US-33 are independent and may run in parallel with US-28; US-30 (extractor) needs US-28+US-29+US-19; US-31 (Feature Builder) needs US-30; US-32 (T0/G2 gate) needs US-28+US-30. The ImportError CI trap pattern (AC-21.3 / AC-28.3 / AC-30.3 / AC-32.3) is correctly carried through all gate stories. Firehose budget is sound: G1 + G2(a) need NO activation; G2(b) is the ONE budgeted Helius activation (Helius 10→9) with an explicit carry-to-next-sprint fallback that never blocks P5 exit. DEFERRED: F5 (live Birdeye REST SnapshotDataSource adapter) correctly deferred to sprint-8 near the scorer (P7). Ready for Dev Team kickoff.

### PO Sprint Review Notes
_Pending_

---
_Auto-generated from `sprint7.json` — do not edit directly._
