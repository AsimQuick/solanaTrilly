# EPIC US-76 — Graduate-Inference Foundation (detect → normalize → retain → score v3.2 live)

<!--
module: scrum-master/EPIC-US76-graduate-inference-foundation.md
type: epic (engineering design + backlog)
status: AC-5 in-progress — golden-parity harness + Tier-1 replay on feature/US-76-AC-5.
created: 2026-06-20
last-updated: 2026-06-20
last-updated-by: dev-team
contract: solanatrills/docs/solanatrilly_buildout_directives.md §8 (PRE-GRAD MODEL SERVING CONTRACT) — binding
pairs-with: EPIC-US75 (slippage/copy), future US-77 (v4 wallet bank), US-78 (data contract)
-->

**Goal:** make the promoted **`trilly_pregrad_v3.2`** model fire live on a real graduation —
`detect → assemble pre-grad tape → normalize → 20 features → cross-sectional score → gate → paper-trade` —
parity-true to the offline lab. This is the milestone that satisfies the graduate-inference DoD and is the
v4 fallback. Serve v3.2 only; v4 is a later promotion (US-77) and the live surface is forward-compatible.

**Why this epic exists:** the live system never scored because it detected the **wrong event** (Birdeye
`SUBSCRIBE_TOKEN_NEW_LISTING` → any PumpSwap pool listing, `graduated:True` hardcoded) — contaminated with
instant/direct-to-AMM listings that have no bonding-curve tape. True graduations (Dune `completeevent`) still
have ~6.5-min median curve life and ~76% rich tapes (`docs/graduation_detection_finding.md`). Fix detection,
retain the tape, normalize in one place, score cross-sectionally.

---

## The locked contract (from directives §8 — do not deviate without asking the lab)
- **Model:** `models/trilly_pregrad_v3_2`. **Builder:** vendor `feats_one()` (`solanatrills/analysis/
  graduated/enrich_pregrad.py:48`) **verbatim** → the 20 `enrich20_buyer_cohort` features in `v3_2/meta.json`
  order. Already vendored as `core/pregrad_features.py` (`compute_pregrad_features`) — reuse, do not reimplement.
- **Currency = SOL-space.** Every v3.2 feature is a ratio/share/count/fraction (only cut is `n_whales5` =
  volume-share > 5%, itself a ratio) → SOL/USD cancels. Compute volume from each Helius swap's **SOL leg**;
  **no per-trade USD** for v3.2. (Per-trade SOL/USD@block_time is built later, only for v4 + copy-trade.)
- **Decimals:** resolve **per-mint** (seed from the MEME_DATA `decimals` field, validate once against the
  mint account on chain — solanaBilly #288). pump.fun curve token = 6, SOL = 9; never hardcode.
- **Window/anchor:** assemble `[max(graduated_time − 3600, creation_time), graduated_time)`, strict
  `block_time < graduated_time`, anchored on the MEME_DATA `graduated_time`. For tokens **>60 min old** at
  graduation, **truncate at grad−3600** (do NOT walk back to creation); clamp to creation only when <60 min.
- **Scoring is CROSS-SECTIONAL (parity trap):** 15 boosters (3 labels × 5 seeds) → mean 5 seeds/label →
  **percentile-rank the label score across the candidate pool** → mean of 3 ranks → top-K/day. Never score a
  token in isolation — rank against a **frozen reference distribution** (or rolling cohort window).
- **Curve-life gate:** **≥60s** `graduated_time − creation_time` (primary) AND **≥20 pre-grad swaps**
  (secondary, feature-stability floor; p5=51/p10=100 in a 5k sample, only 0.4% of true graduates < 20).
  Do NOT gate on buyer count. **Log the skipped fraction** (live instant-rate monitor).
- **Golden ground truth:** `solanatrills/analysis/graduated/pregrad_enrich.csv` (20-feat per-mint matrix) at
  **float tolerance ~1e-3**; PLUS a generated `golden_scores.parquet` (run the 15 frozen boosters over those
  rows) for end-to-end score parity.

## Confirmed live `SUBSCRIBE_MEME` frame (probe 2026-06-20)
```
{"type":"MEME_DATA","data":{
   "address": <mint>, "decimals": 6, "price": <num>, "liquidity": <num>, ...stats...,
   "meme_info": { "source":"pump_dot_fun", "creation_time": <unix s>, "graduated": <bool>,
                  "graduated_time": <unix s | null>, "progress_percent": <0-100>,
                  "pool": {"address": <pool>}, "creator": <wallet> } }}
```
It is a continuous `meme_stats` stream filtered by the subscription. Non-graduated tokens carry
`graduated=false, graduated_time=null, progress_percent<100`. True graduations are ~11/hr (rare vs the old
listing flood) — the validation window must run long enough to catch one.

---

## Acceptance criteria

### AC-1 — Detection fix: true graduations via `SUBSCRIBE_MEME`
- Switch the subscription to `{"type":"SUBSCRIBE_MEME","data":{"graduated":true,"source":"pump_dot_fun"}}`
  (the subscribe type is already config-driven, `graduation_subscribe_type`) **and rewrite the frame-mapper**
  in `core/tape/birdeye_graduation_source.py` for the MEME_DATA shape above. Remove the hardcoded
  `graduated:True` (line ~374).
- **Fire a graduation event ONCE per mint** on the `graduated` false→true transition (dedupe by mint). Emit
  `mint=address`, `graduated_block_time=meme_info.graduated_time`, `creation_time`, `progress_percent`,
  `pool_address=meme_info.pool.address`, `decimals`.
- **Curve-life gate** before scheduling a score: require `graduated_time − creation_time ≥ 60s`; **log + skip**
  the rest and increment an `instant_skipped` counter (the live early-warning metric).
- **AC:** against banked real frames (`/tmp/meme_frames.json` + a graduated-frame fixture), the mapper emits
  exactly one event per graduated mint with the right anchor; non-graduated frames emit nothing; an
  instant (<60s) graduation is skipped + counted.

**Dev Team Status:** implemented
**Dev Team Notes:**
- Rewrote frame-mapper as `map_meme_data_frame` in `core/tape/birdeye_graduation_source.py` for MEME_DATA shape.
- Subscription switched to `SUBSCRIBE_MEME` with `{"graduated":true,"source":"pump_dot_fun"}` via `DEFAULT_SUBSCRIBE_DATA`.
- New constants: `DEFAULT_SUBSCRIBE_TYPE="SUBSCRIBE_MEME"`, `DEFAULT_DATA_TYPE="MEME_DATA"`, `CURVE_LIFE_MIN_S=60`.
- Legacy `map_new_listing_frame` preserved for backward-compat with integration test; `events()` uses new mapper only.
- `BirdeyeGraduationSource` gains `instant_skipped: int` counter and `_seen_mints: set[str]` for deduplication.
- Committed fixture: `core/tests/fixtures/meme_data_frames.json` — 6 real non-graduated frames + 4 synthetic variants.
- New test file: `core/tests/test_birdeye_graduation_source_us76_ac1.py` (41 tests); updated legacy test (23 tests); updated integration test.
- Instant-skip gate verified by: `test_events_skips_instant_graduation_and_increments_counter` (30s curve life, instant_skipped==1); `test_events_instant_skipped_counts_multiple` (two instants, counter==2); `test_events_mixed_frames_emit_only_valid_graduation` (mix of 6 real + instant + non-pump + graduated yields exactly 1 event, instant_skipped==1).
- Discrepancy vs epic prose: the real frames show `meme_info.pool` as a rich object (`{"address":...,"realSolReserves":...,...}`), not just `{"address":<pool>}` as in the epic's prose summary. The mapper reads only `pool["address"]` — handles both shapes.
- 64/64 unit tests + 2/2 integration tests pass; `ruff check .` clean.
- blocker-type: none

### AC-2 — Single `NormalizedSwap` layer (SOL-space, per-mint decimals, guarded)
- One module every source passes through (Helius live, Birdeye offline-style, any future) → byte-identical
  fields for the vendored `compute_pregrad_features`. **Reuse the offline feature math; do not reimplement.**
- **Currency:** volume from the SOL leg (SOL-space); no USD for v3.2. **Decimals:** resolve per-mint, convert
  raw→ui once, centrally. **Zero/dust:** reject fill price far below local median; guard every division
  (#405). **Missing:** coerce JSONB `None → np.nan` at the boundary, before any numeric op (#358).
- **AC:** unit tests for zero-price, None/NaN, Token-2022-vs-SPL decimals; a Helius-sourced swap and an
  offline-style swap normalize to the same dict shape.

### AC-3 — Durable per-mint tape store (state-based retention, gap-heal)
- Replace the in-memory idle-kill buffer with a **durable per-mint store** (DB/disk), keyed by mint,
  append-on-arrival, listening continuously from launch. **Retain by state, not silence:** keep a mint until
  it graduates (tape consumed) or is provably dead (old + no curve progress) — burst-traders go quiet then
  graduate. **Gap-heal:** on websocket reconnect, and at graduation if the earliest cached swap ≫ creation,
  backfill the missing window (Birdeye/Helius REST) before scoring.
- **AC:** a token born → quiet 35 min → graduates still has its full tape at score time (the idle-kill
  regression); a simulated reconnect gap is healed.

### AC-4 — Cross-sectional scoring (frozen reference distribution)
- Implement the percentile-rank step against a **frozen reference distribution** shipped with the model (or a
  rolling cohort window) — never single-token. Wire the gate (`adaptive_topk`/threshold) over the blend.
- **AC:** a token scored alone vs in a pool yields the same rank against the frozen reference; matches the
  offline blend recipe.

### AC-5 — Golden-parity test + Tier-1 replay merge gate
- Rebuild a sample of real mints **live-style** (durable store → normalize → `compute_pregrad_features`) and
  assert each feature row equals its `pregrad_enrich.csv` row at **~1e-3**. Generate `golden_scores.parquet`
  (15 frozen boosters over those rows) and assert end-to-end score parity.
- **Tier-1 replay:** run the production scoring path over the active model's real corpus; **`crashed > 0 ⇒ do
  not ship`**. Validate the harness on a known-good case first.
- **AC:** both gates green in CI; the live-vs-offline feature diff is within tolerance.

---

## Definition of Done
- A **real graduation** scored live: `score (N>0 swaps) → gate → paper-buy → paper-sell` observed in a
  firehose validation window (the 2nd granted activation), with the curve-life gate skipping instants and the
  `instant_skipped` metric logged.
- Golden-parity (feature + score) green; Tier-1 replay green; trouble-PR guards (#405/#358/#288) unit-tested.
- Rollout behind a flag; safety floor intact (observe/paper, `trading_enabled=False`, §5 isolation,
  solanaBilly untouched). v3.2 stays the promoted model; v4 deferred to US-77.

## Sequencing
AC-1 (detect) + AC-2 (normalize) are independent and parallel. AC-3 (durable store) depends on neither but is
required for DoD. AC-4 (scoring) + AC-5 (parity) gate the live validation. Re-enable the firehose only after
AC-5 is green and guards/alerts are in place.

## Testing methodology (directives §4 — binding)
Tier-1 replay over the real corpus (`crashed>0 ⇒ no ship`); golden fixtures asserted bit/float-exact;
"green against reality" merge gate (reproduce on real data → fix → prove on real data → flag → observe →
enable). Validate each harness on a known-good case first. Never "unit green → firehose → pray."

---

## Tester review — binding revisions before dev handoff (2026-06-20)

**Verdict: NEEDS REVISION.** The tester caught two BLOCKING parity defects that would make the system
*appear* to work (paper trades fire) while being silently wrong. The items below are **binding**; the dev
team implements against this section. File:line from the live tree.

**Priority 1 — BLOCKING, resolve before dev starts:**
1. **AC-4 — reference distribution does not exist → pool-of-1 silent failure.** `models/trilly_pregrad_v3_2/`
   has no `reference_dist.json`. Live `run_firehose._build_scoring_context_sync:804-807` falls back to
   `ref_dist=None` → `spine.py:185-187` calls `scorer.score_pool([features])[0]` (pool of 1) →
   `scorer._percentile_rank:179-180` returns **1.0 for every token** → blend 1.0 → with hardcoded
   `threshold=0.8` **every graduation passes**. FIX: (a) generate + commit
   `models/trilly_pregrad_v3_2/reference_dist.json` from the **full** `pregrad_enrich.csv` OOT label scores
   via `BlendScorer.score_pool` (per `meta.json` recipe); (b) set `ScoringConfig.reference_dist_path`;
   (c) CI guard: if `scoring_enabled=True` and `reference_dist_path` null/missing → **fail loudly** (block
   the `ref_dist=None` path in prod); (d) test that single-token vs pool gives the same rank against the
   frozen reference.
2. **AC-5 — 3600s window cap not enforced live → parity break for >60-min tokens (~30% of graduates).**
   `to_pregrad_swaps`/`compute_pregrad_features` filter `rel<0` only, not `rel>=-3600`. FIX: in
   `assemble_pregrad_features` add `swaps = [s for s in normalized if s["rel"] >= -3600]` (clamp to
   `creation_time` only when <60 min old). AC-5 golden fixture MUST include a >60-min-curve-life token and
   match `pregrad_enrich.csv` at 1e-3.
3. **AC-3 — name the gap-heal REST source.** Existing `GapReconciler` uses Birdeye REST, but §1 locks
   Birdeye = notifier only. Pick the source explicitly; if Birdeye, AC-2's normalization test MUST cover
   Helius-sourced and Birdeye-sourced swaps converging to the identical dict.

**Priority 2 — before firehose re-enable:**
4. **≥20-swaps secondary gate** — enforce in the scoring loop / `assemble_pregrad_features` (sub-20 → skip +
   count), with an explicit test (10 swaps → not scored; 25 → scored). Currently absent.
5. **Threshold from artifact, not hardcoded.** `run_firehose:830` hardcodes `0.8`; `meta.json` depth_menu =
   0.7916 @30/day. Wire `rank_cut` from `meta.json`/`ModelRegistry` at promotion; no magic constant.
6. **AC-1 fixture committed (not `/tmp`).** Add to `core/tests/fixtures/`: a graduated pump.fun frame
   (≥60s curve life), a non-graduated frame, an instant (<60s) frame, a non-pump.fun frame. Assert the
   mapper emits all six fields incl. `decimals` + `creation_time` (current mapper emits no `decimals`).

**Priority 3 — before DoD:**
7. **AC-3 store medium decided.** Likely reuse the existing `swaps` DB table via `SwapWriter` (idempotent on
   `(mint,signature)`) — but specify the **read path at score time** and **avoid synchronous ORM writes on
   the Helius hot path** (every program-wide swap). Justify any new store vs the existing table.
8. **#287 Borsh offset test** — decode a captured real tx with `decode_helius_trade_event`
   (`helius_birth_tape_source.py:189`, `<QQ` @40) and assert `sol_amount/token_amount/side/owner` vs known
   values. Committed fixture, no network.
9. **Tier-1 replay corpus = `pregrad_enrich.csv` assembled live-style** (through `assemble_pregrad_features`
   WITH the 3600s cap), not via offline `enrich_pregrad.py` directly — the harness must build features the
   exact way the daemon does.

**DoD additions (binding):** `reference_dist.json` committed + CI guard against the `None` fallback; 3600s
cap enforced + tested on a >60-min token; ≥20-swaps gate enforced + tested; threshold sourced from the
artifact; AC-1 fixtures committed; gap-heal source named + normalized.

---

## Dev Team Status

**Dev Team Status:** in-progress (AC-5 golden-parity harness on feature/US-76-AC-5; AC-2 on PR #332; earlier slices: P1.1/P1.2/P2.4/P2.5 on PR #329, AC-1 on PR #331)

**Dev Team Notes:**
- PR #329: `feature/US-76-scoring-correctness` — all four binding Tester revisions implemented
- P1.1 (pool-of-1 bug): generated `reference_dist.json` (24708 samples, ctrl/oracle/liq) from full
  `pregrad_enrich.csv` via all 15 v3.2 LightGBM boosters; committed to `models/trilly_pregrad_v3_2/`;
  `.gitignore` negation + `git add -f` to bypass the `models/` exclusion; `ConfigurationError` guard
  blocks the `ref_dist=None` silent fallback when `scoring_enabled=True`
- P1.2 (3600s cap): `assemble_pregrad_features` now filters `windowed = [s for s in normalized if -3600 <= s["rel"] < 0]`
- P2.4 (>=20 gate): `assemble_pregrad_features` returns `None` if `len(windowed) < 20`; all existing
  6-swap test fixtures expanded to 20 swaps (4 test files updated)
- P2.5 (threshold): `_threshold_from_model()` reads `rank_cut` from `meta.json` `depth_menu`
  (30/day -> 0.7916); fallback 0.7916 if meta absent
- AC-2 (single normalisation layer): `feature/US-76-AC-2` — implemented and tested
  - `core/normalized_swap.py`: added `coerce_jsonb_none()` (#358), `is_dust_price()` (#405),
    `normalize_raw_for_features()` — the single layer every source passes through; produces the
    exact §7.1 dict `compute_pregrad_features` consumes; SOL-space vol (no USD for v3.2)
  - `core/tape/mint_decimals.py`: `MintDecimalsResolver` class — per-mint decimals cache (#288),
    seeded from graduation-event `MEME_DATA.decimals`; `fetch_mint_decimals` async stub; fallback
    documented (6) not hardcoded; singleton `get_default_resolver()` for daemon use
  - `core/firehose/spine.py`: `to_pregrad_swaps()` now delegates to `normalize_raw_for_features()`;
    `assemble_pregrad_features()` threads `base_decimals` parameter; old `or 0.0` None-to-zero
    pattern replaced with proper None→nan coercion; zero-price swaps dropped, not passed through
  - `core/tests/test_normalized_swap_us76_ac2.py`: 48 new tests — all pass
  - `core/tests/fixtures/helius_borsh_offset_ac287.json`: committed Borsh offset regression fixture
    (#287) with known-good sol_amount/token_amount/side/owner values; decoded deterministically
    by `test_borsh_offset_fixture_decode` (no network)
  - SOL-space confirmed: vol = vol_sol (Helius SOL leg), no USD introduced for v3.2
- AC-5 (golden-parity harness + Tier-1 replay): `feature/US-76-AC-5` — implemented with FINDINGS
  - Fixture: `core/tests/fixtures/trilly_pregrad_v3_2/parity_corpus_v76ac5.json.gz` — 20 mints
    (2 with curve_life > 3600s incl. one at 67125s), all >=20 pre-grad trades; 1.2MB gzipped
  - Score oracle: `models/trilly_pregrad_v3_2/golden_scores.parquet` — 20 rows, live-path
    blend/label scores generated from assemble_pregrad_features + BlendScorer + reference_dist
  - Test file: `core/tests/test_golden_parity_us76_ac5.py` (16 tests)
  - Tier-1 replay: crashed=0 over all 20 fixture mints (gate passes)
  - Harness self-validation: first mint validates determinism + no-inf before full run
  - 3600s cap exercised: real >60min token (67125s curve life) in fixture, cap confirmed
  - Score oracle: run-twice-identical confirmed locally with boosters (PASS); in CI (no boosters)
    this test skips with explicit message rather than silently passing
  - **PARITY FINDINGS** (2 documented breaks, not fixes for this PR):
    - BREAK-1 pre_insider_sell_ratio: offline uses USD vol (uiAmount_SOL * quotePrice + 1.0),
      live uses SOL vol (uiAmount_SOL + 1.0). The +1 Laplace smoother is NOT scale-invariant —
      differs by ~87x (SOL/USD). For thin-SOL-volume tokens: delta up to 100x. Documented as
      xfail; 18/20 mints break tolerance. Root fix: retrain with SOL-space volumes in offline lab.
    - BREAK-2 same-block tie-breaking: offline sorts by blockUnixTime only (Python stable sort,
      preserves Birdeye page insertion order). Live sorts by (block_time, slot=0, signature)
      alphabetically on txHash. When multiple swaps land in same block: (a) cohort membership
      changes affecting all EB10/EB20 features; (b) sell-before-buy reorder flips "sold" flag,
      affecting pre_diamond_frac and pre_seller_of_buyers_frac. Max delta: 0.1 (EB features),
      2.54e-3 (diamond/seller). Documented as xfail. Root fix: unify sort keys in both paths.
    - Deployer features (pre_deployer_*): offline has deployer data; live passes deployer=None
      (deployer lookup not yet wired). Expected behaviour, not a parity bug.
  - 10 scale-invariant features (counts, HHI, breadth, top5_share, repeat_buyer) pass 1e-3 tol
  - Max delta on scale-invariant features: 8.34e-5 (pre_buy_hhi) — comfortably within tolerance
  - Test counts: 16 new (AC-5): 13 pass, 1 skip (score oracle in CI), 2 xfail (documented breaks)
  - Full suite: 2952 pass, 1 fail (pre-existing sprint14 stale phase), 5 skip, 2 xfail — clean
- Remaining work: P3 gap-heal source naming (AC-3), AC-4 cross-sectional scoring wire-up
- blocker-type: none

### Parity resolution — BREAK-1 + BREAK-2 closed (2026-06-20, operator-run, lab-decided)

The AC-5 harness's two xfail breaks are resolved per the lab's directives §8/§9 decisions.
The harness is **green: 18 features asserted parity-clean at ~1e-3, 2 eb-netpos as a
permanent blessed xfail.** Full suite: 2953 pass / 1 xfail / 5 skip / 1 pre-existing
unrelated red (`test_sprint14_guard_passes` — sprint14.json phase staleness, untouched here).

- **BREAK-1 (pre_insider_sell_ratio) — FIXED.** Live now serves the feature `vol` in **USD via
  ONE graduation-time SOL/USD spot** (`sol_usd_spot` threaded `run_firehose._score_tick` →
  `assemble_pregrad_features` → `to_pregrad_swaps` → `normalize_raw_for_features`, where
  `vol = vol_sol × spot`). The spot is the shared cached `core.pricing.get_sol_usd()` resolved
  in the scoring context (within minutes of graduation; "roughly right" suffices — it cancels
  in the 19 ratios and only makes the +1.0 smoother negligible at USD scale). ISR max delta vs
  the offline per-trade-USD golden: **~5.5e-4** (< 1.5e-3). Now ASSERTED, not xfail.
- **BREAK-2 (same-block sort) — FIXED** with one blessed residual. `compute_pregrad_features`
  now sorts by **`(block_time, signature)`** (slot dropped) to match the offline golden's
  `(blockUnixTime, txHash)`. sold/diamond/seller now parity-clean. The only residual is
  `pre_eb10/20_netpos_frac` (~1 mint/5%): netpos is a `buy_v > sell_v` comparison and single-spot
  USD structurally can't reproduce offline per-trade-USD on near-tie wallets. **Lab A1: this stays
  a permanent xfail** — eb-netpos is low-gain, rare, picks stable (r=0.998, ~96% overlap); no
  netpos redefinition / retrain.
- **Serving bundle — adopted from the lab verbatim** (built by `analysis/graduated/build_serving_bundle.py`
  on the final unified-sort golden; **no retrain** — frozen boosters soaked on the new golden, lab A2):
  `models/trilly_pregrad_v3_2/reference_dist.json` (per-label 1001-pt score→percentile grid + blend
  recipe + rank_cut) and `golden_scores.parquet` (mint→{ctrl,oracle,liq}_pred/_pct/blend, full corpus).
  `ReferenceDistribution.from_file` reads the grid (back-compat with the legacy format); the live
  `BlendScorer` reproduces the per-label `_pred` **bit-for-bit** (0.0 delta over the fixture), `_pct`/blend
  follow via the grid (≤6.7e-4 vs the exact-population oracle). Fixture `expected_features` re-baked from
  the final `pregrad_enrich.csv`.
- **Deployer features** (`pre_deployer_*`) — live serves 0 (lookup unwired). **DoD-acceptable (lab A3:**
  3 lowest-gain feats, 1.61% total; ablation blend Pearson 0.994, ~6% pick churn). **Wire before capital**
  via the US-78 `tokens` export (deployer = creator wallet). Excluded from the asserted set.

**Files:** `core/pregrad_features.py` (sort), `core/normalized_swap.py` + `core/firehose/spine.py`
(single-spot vol), `core/management/commands/run_firehose.py` (`sol_usd_spot=sol_usd`),
`core/scorer.py` (`from_file` grid branch), `core/tests/test_golden_parity_us76_ac5.py` +
`core/tests/test_scoring_correctness_us76.py` (lab-bundle schema), fixture + bundle artifacts.

### ✅ DoD MET — live firehose validation (2026-06-20, observe/paper)

Two firehose windows on the VPS staging stack (`-p solanatrilly`), observe/paper,
`trading_enabled=False` throughout. Evidence: `ops/firehose_window_evidence_2026-06-20_us76_dod.md`,
ledger: `ops/firehose_activation_log.md`.

- **Window-1 (90 min, 30/day):** chain proven THROUGH THE GATE — collection healthy (~1.2k mints /
  81k swaps, #323 absent), 5 real graduations scored with N>0 pre-grad tape (parity-correct
  single-spot-USD serving + unified sort), curve-life instant-skip fired. All 5 gate-failed below
  the 0.7916 cut → no paper trade (a no-edge FINDING, §8). Surfaced 2 paper-leg blockers → PR #335.
- **PR #335 (paper-leg fixes):** post-grad subscription was oldest-first over ALL graduated rows →
  fresh graduations starved of the bounded post-grad slots (latent paper-leg blocker); fixed to
  recent-only/newest-first. `per_day_target` made a config knob (lab A2/A4 live calibration).
- **Window-2 (50/day = rank_cut 0.6976, a published depth_menu operating point):** ✅ **FULL chain
  observed** — mint `5NgDx…wpump` score=**0.7179 gate=PASS** → paper-buy ($25 observe) → paper-sell
  (settled, pnl −2.25%, AUTO_SELL_TIMER) → `trading_positions` CLOSED/observe. First settle ~12 min in.

**Status: the US-76 graduate-inference DoD is MET.** v3.2 serves live, parity-true, detect→score→
gate→paper-buy→paper-sell observed. Safety floor held; solanaBilly untouched.

**Refinement noted (not a blocker):** the proven paper trade had `held=2s/peak=0.00%` — the post-grad
sub had just opened, so the tape was thin at entry. For representative paper trades, give the
post-grad subscription more lead time before scoring (open at graduation / delay entry a few s).

### Post-DoD operability + US-78 (2026-06-20)

- **Operator dashboard RESTORED (PR #337)** — folded into the DoD per operator request. The
  production frontend was never fully wired for external access (no host port; nginx had no
  `/api`+`/ws` proxy; healthcheck `localhost`→`::1` false-negative). Fixed: nginx serves the SPA
  and reverse-proxies `/api`+`/ws`+`/health` to `web` (Docker DNS resolver + `$web` var, per-request
  resolution); frontend exposed on host **8003**. **Operator URL: `http://140.82.43.36:8003/`**
  (8002 = Django API only — its `/dashboard/` is a bare smoke shell). Verified live: SPA + bundle
  200, API/WS proxied, frontend healthy. Evidence in the firehose-window evidence doc.
- **US-78 data-contract export — increment 1 (PR #338):** `swaps` + `tokens` Parquet surfaces (§10),
  UTC-date partitioned, by-mint/by-wallet(signer) queryable, MANIFEST per surface. Worker-only task
  `core.tasks.export_data_contract` + `POST /api/export/data-contract/trigger/`. `deployer` wired from
  `raw_graduation.raw.meme_info.creator`. **Increment 2 (predictions_positions)** deferred: needs the
  per-label score breakdown (`ctrl/oracle/liq_pred`, percentile, per_day_target) persisted at
  score-time (AC-3-adjacent) — `Position` currently persists only the blend `score`.
