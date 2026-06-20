<!--
file: scrum-master/EPIC-model-track-deploy.md
epic: Model track to live-observe (v4 wire + exit fix + backfill)
status: planning — tester pre-flight complete 2026-06-21
owner: live-run orchestrator (Claude)
dev-model: hotfix/epic-PR, post-PO, NOT sprint ceremony
-->

# EPIC — Model track to live-observe

Three independent changes get the model from "scores nothing useful" (332 tokens all
`DETECTED`, 0 `SCORED`; paper exits close in ~0 min) to a meaningful live-observe read.
Sequencing per [[copy-vs-model-revenue-path]]: model is SECOND to copy; build all three
gated + offline-tested; a v4 observe read is only meaningful once the exit + backfill also
work. **Tester pre-flight done 2026-06-21** — verdicts + minimal scope below. Dev-teams run
**serially through the shared tree** (all three touch `run_firehose.py`) or in isolated
worktrees. Every brief states: hotfix/epic-PR, post-PO, not sprint ceremony.

---

## AREA A — Wire v4 REP+recurrence into live scoring — **GO-WITH-CHANGES**

v4 is merged (`core/v4_rep_builder.py`, `models/trilly_pregrad_v4/{meta,reference_dist}.json`,
`tools/promote_v4.py`) but the builder is NOT called live; `score_single` zero-fills the 33
missing features (`core/scorer.py:329`). Builder code + feature order are correct
(`test_v4_meta_feature_order`; enrich20[0:20]+REP24[20:44]+recurrence9[44:53]).

**Showstoppers:**
1. **v4 `meta.json` has no `depth_menu`** → `_threshold_from_model` (`run_firehose.py:169-190`)
   silently falls back to v3.2's `0.7916`. Add a v4 `depth_menu` with correct `rank_cut` per
   depth, or explicitly confirm+document 0.7916 for v4.
2. **Wire at the `_score_tick` call site** (new `assemble_v4_features()` wrapper), NOT inside
   `assemble_pregrad_features` (`spine.py:159-226`, shared with v3.x — must not mutate).
3. **Live buyer-assembly is NOT parity-tested.** Gate (`test_golden_parity_v4.py:113-138`)
   reads buyers from offline `whale_edges.parquet`; live uses
   `extract_buyers_from_swaps()` (`v4_rep_builder.py:430-496`) on the Birdeye tape. Not
   byte-identical by construction. Add `test_v4_buyer_assembly_parity.py` on the golden fixture
   swaps, or document bounded impact.

**Other:** es60 xfail (`pre_whale_nrep3_es`/`pre_whale_totrep_es`, ~2.1%) is correctly
structured (`test_golden_parity_v4.py:465-516`) — no action (per operator: soak the fallback).
Load `WalletBankLookup` once at `FirehoseDaemon.__init__` as `self._wallet_bank` (~44s, ~27MB);
NEVER per-tick. **Mount fix:** move bank to `models/trilly_pregrad_v4/v4_wallet_bank.parquet`
(under the already-mounted `/app/models:ro`) — the `solanatrilly_lake` volume mounts at
`/app/lake/firehose`, not `/app/lake`, so the current `lake/v4_wallet_bank/` path is NOT
visible in the container. Update `_BANK_PATH` in the parity test to match.

---

## AREA B — Fix the broken model exit — **GO-WITH-CHANGES**

**Confirmed root cause:** `TradingSettings.auto_sell_timer_s` live singleton = **300**
(`trading/models.py:78`), but the policy is `tp12tr15_t1200` (timer **1200**). `_resettle`
post window `[entry+_LAT, entry+timer+_GAP]` is only 302s → `AUTO_SELL_TIMER` fires near-zero.
`_resettle` logic itself is correct (verbatim `tape_resettle.py` port). Entry at score-time ≈
graduation (`score_at_elapsed_s` typically 0) — does NOT break the settler.

**Minimal fix (this PR):**
1. **Data migration** to set the existing singleton (`pk=1`) `auto_sell_timer_s=1200` (NOT just
   the `default=300` in `trading/schemas.py:65`/`trading/models.py` — the live row already exists).
2. Also bump the schema default 300→1200 so new singletons start right.
3. Regression test: `TradingSettings.get().auto_sell_timer_s == 1200` post-migration.

**Separate issues — do NOT conflate into this PR:**
- **Quote-window bug:** the settler gets `postgrad_swaps` only; pre-grad swaps in `[grad-30, grad]`
  live on `self._tape`, not `self._postgrad_tape`, so the `[entry-30, entry]` quote window is
  often empty → `enterable=False, reason="dead"`. Timer fix alone won't lower the dead rate.
  Distinct fix (lower `score_at_elapsed_s`, or pass a merged pre+post tape). File separately.
- **Holder force-exit** ("holder cohort sold≥40% AND price≤85% running max") is not implemented
  in `_resettle` (no holder param). Defer entirely.

---

## AREA C — Birdeye trade-history backfill — **NO-GO on stated scope; GO on narrower scope**

`_score_tick` only reads `self._tape`; the ~313 pre-startup graduations defer forever. No
Birdeye historical swaps REST client exists.

**Showstoppers:**
1. `test_live_backfill_parity_ac212.py` covers **normalization only** (ReplaySource→TapeRecorder),
   NOT any HTTP fetch. A new adapter test is required.
2. **`Token.status` is never written in the live path today**; `_due_tokens_sync`
   (`run_firehose.py:933-942`) filters nothing; `SKIPPED_NO_TAPE` isn't in `STATUS_CHOICES`.
   Restart-safety needs: a STATUS migration + `_due_tokens_sync` excluding scored/skipped mints
   from the DB query (not just the in-memory `_scored_mints` set that resets on restart).
3. **asyncio safety:** `_backfill_pending` set accessed from both the ORM thread
   (`_due_tokens_sync`) and the event loop is not thread-safe. Keep backfill state in the async
   context only (or `asyncio.Lock`).
4. **Credit burn:** ~313 tokens × ~10 pages ≈ 3130 REST calls per window. Plan allows 100 rps;
   quantify + document before the activation. Log in `ops/firehose_activation_log.md`.

**Correct narrower scope:**
1. `BirdeyeTradeHistorySource` as a `DataSource` subclass paging `/defi/txs/token/seek_by_time`
   (billy's endpoint; query by SPL mint), so it's replayable via `ReplaySource` in tests.
2. Wire through the existing `GapReconciler` (`core/tape/gap_reconciler.py` — already uses that
   endpoint pattern + dedup + normalization + sink), NOT a parallel path.
3. Run backfill in a **separate `_backfill_loop` coroutine**, NOT inside `_score_tick`; it writes
   `self._tape`, the next scoring tick picks it up.
4. STATUS migration + `_due_tokens_sync` filter (showstopper 2).
5. `ReplaySource`-driven normalization-parity test for the new source.
6. Document the REST budget before activation.

---

## Dispatch order (orchestrator)

1. **B (exit timer)** — smallest, highest-confidence, named revenue blocker; data migration + test.
2. **A (v4 wire)** — largest; needs the 3 showstopper fixes + artifact upload to VPS.
3. **C (backfill)** — redesign to the narrower scope first; most async risk.

Each: tester-validated brief → dev-team branch+PR (CI green) → I review → squash-merge →
dispatch-deploy (outside a firehose window or accept the self-resume gap). Do NOT run
`tools/promote_v4.py` until A is merged + the 33 features confirmed non-zero live.
