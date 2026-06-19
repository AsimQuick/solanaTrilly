<!--
file: AGENT_HANDOFF_2.md
purpose: Session-2 handoff — continues AGENT_HANDOFF.md. Covers the copy-trade
         cohort-2.0 live build (PRs #317–#321) and the inference live-window findings.
owner: operator (Claude)
last-updated: 2026-06-19
-->

# solanatrilly — Agent Handoff #2 (copy-trade 2.0 build + inference window)

Read `AGENT_HANDOFF.md` first (it still holds). This documents what session-2 did
and the precise state to continue from. **Everything stayed OBSERVE/PAPER; no real
capital; `trading_enabled=False`, copytrade `engine_on=False`/`mode=observe`;
solanaBilly untouched.**

---

## 1. What shipped this session (all merged to main, CI-green, dispatch-deploy ready)

The copy-trade engine was effectively a SKELETON (offline pure-functions + a
placeholder `ReplaySource`, schema 1.0, no live source, no exits, SOL sizing). The
real cohort (`copy_2026-06-19_v1`) is schema **`copytrade-2.0`** (two heads, USD
sizing, per-head exits incl. mirror-sell — which the old engine *forbade*). Built it
out properly in sequence, each a tested PR:

| PR | What | Tests |
|----|------|-------|
| **#317** | `core/pricing/sol_usd.py` — shared cached SOL/USD spot; replaces the `sol_usd=140.0` hardcode in `run_firehose` (a latent staleness bug) and feeds the copy-trade USD trigger | 6 |
| **#318** | cohort-2.0 schema (`CohortV2`, `GlobalConfig`, discriminated-union exits), validator, models+migration 0005 (`strategy_id`/`size_usd`/`high_water_price`/USD-global fields/`TRAIL`+`MIRROR` reasons), `replace_cohort_v2` fresh-start, `load_cohort` cmd; real cohort banked as a fixture. Fixed the AC-58.2 migration guard's buggy model-name heuristic | +10 (321 total) |
| **#319** | `copytrade/helius_wallet_source.py` — copy-trade's OWN Helius wallet-address subscription (§5-isolated, reuses the proven stateless TradeEvent decoder) + `should_copy_buy_v2` (USD ≥$250 trigger, not pre-grad-only) | +10 (331) |
| **#320** | `copytrade/exits.py` — deterministic per-head exit evaluators: moonshot `our_trailing` (SL/trailing-from-high-water/TP) + scalp `mirror_wallet_sell` | +15 |
| **#321** | `copytrade/engine_runtime.py` + `price_source.py` + `open_observe_position_v2` + rewired `run_copytrade_engine` (dual asyncio: wallet-consumer + periodic exit-manager); **observe-only guard (mode=live ⇒ idle, never `open_live_position`)** | +29 (**375 total, all green**) |

Net: copy-trade now consumes cohort-2.0 end-to-end, both heads, USD sizing/trigger,
own §5-isolated Helius wallet sub, per-head exits — observe/paper, engine OFF by default.

### Inference change shipped this session
- **Raised `tape.pre_grad_idle_kill_ttl_s` 300 → 1800** on the active VPS config
  (live DB edit + cache invalidation). Rationale: the 300 s aggressive kill could
  evict slow-bonders before graduation (suspected contributor to the never-firing
  score join). At 1800 s, a token quiet up to 30 min survives to graduation; memory
  stays bounded. **NOTE: this is a live-config (DB) edit — NOT yet in the seed tool
  default (`tools/cutover_seed_and_promote.py` still sets 300). Codify it there if you
  want it to survive a reseed.**

---

## 2. Inference live window — findings (the open DoD #1)

Ran one clean **90-min window** (`st_fh` on `listener`, DEBUG, `--max-runtime-seconds 5400`).
Everything UP TO the score-join is healthy and proven live:
- Helius streaming (buffer climbed to ~1350 mints / ~52k swaps), heartbeat steady.
- Birdeye graduation detection working (correctly drops non-`pump_amm`, accepts `pump_amm`).
- Idle-kill working at the new TTL (evictions begin ~30 min in, steady-state ~430 evicts).
- Scoring scheduler running, correctly deferring mints with no buffer. Zero errors.

**The score-join (N>0 swaps → paper-buy → paper-sell) was NOT observed firing.**
Diagnosis (evidence-based, NOT a code bug):
- In-window graduations DO happen (37+), but their pre-grad buffers are empty because
  **they launched before the window** (pump.fun launch→graduation routinely takes
  30 min–hours; a 90-min window only catches the *fast* tail, and the startup ~15 min
  eats into it).
- The Helius-buffered mints are ~95% `pump`-suffix (genuine bonding-curve pump.fun
  tokens); the Birdeye graduation set is only ~15% `pump`-suffix and includes many
  **decimals-9 non-bonding-curve tokens** (a different population the pump.fun-program
  subscription correctly never buffers). The genuine pump.fun graduates (e.g.
  `HcGJ…pump`, decimals-6) simply bonded pre-window.
- The "0 intersection between evicted-Helius and graduated-Birdeye mints" is EXPECTED
  (evicted = dead mints; graduated mints are eviction-protected) — **not** evidence of
  a key mismatch. The mint-join logic (`Token.mint == buffered mint`) is sound for
  matching pump.fun tokens.

### THE ROOT CAUSE (found + FIXED — supersedes the "just timing" theory above)
Deeper diagnosis of the window log found the real reason the join never fires: **the
live collection task SILENTLY DIES mid-window.** At `11:22:59` (~49 min into the
90-min window) the log shows `collection: buffer finished (60703 swaps across 1355
mints)` — the Helius WS closed gracefully and the OLD `_collection_loop` ran the
source **exactly once, with no reconnect**, so the collection task ended. For the
remaining ~40 min NOTHING was buffered, the pre-grad tape went stale, and every token
graduating afterward scored 0. **This is almost certainly why the previous agent's
windows never scored either** — collection was dying partway through every run.

**Fixed in PR #323** (`hotfix/firehose-collection-reconnect`): wrapped collection AND
graduation in reconnect loops that rebuild the source and restart, **retaining the
accumulated buffer**, until the firehose flips inactive / the daemon stops. Also
throttled a 50k-line `postgrad: at-capacity skip` log flood (log first over-capacity
mint, then break). Deployed to the VPS.

**With the fix, a long window keeps collecting across WS drops**, so a token's full
bond→graduate lifecycle can finally be captured. There IS still a lifecycle-overlap
requirement (a token must bond AND graduate within the window), so use a genuinely long
window. When it fires, confirm: `[FIREHOSE] score: mint=X score=… gate=…` (N>0) →
`paper-buy` → `paper-sell` → a `trading.Position` (CLOSED, observe) on Live Positions.

### Current live state (session-2 close)
- **Deployed main to the VPS** (reconnect fix + the full copy-trade engine).
- **A 2 h combined window is RUNNING** (`st_fh` on `listener`, reconnect code,
  `idle_ttl=1800s`) **concurrently with the copy-trade engine** (DoD #3): copy-trade is
  ON in observe/paper, cohort `copy_2026-06-19_v1` loaded (5 wallets: scalp 2 / moonshot
  3), `[copytrade] engine starting … mode=observe`, wallet consumer subscribed. **This
  is activation #2.**
- **TURN THE FIREHOSE OFF after the window** (`firehose_state off` + `docker rm -f
  st_fh`) — the daemon auto-stops at its `--max-runtime-seconds` but the `firehose_active`
  flag stays on. The copy-trade engine (`engine_on=True`) keeps its own Helius wallet
  sub running for ongoing observation; turn it off (`engine_on=False`) when done to stop
  that spend.

---

## 3. Copy-trade — how to bring it up live (paper) next

Engine code is on main but **NOT yet deployed/observed live**. To observe:
1. Deploy main: `gh workflow run deploy.yml --repo AsimQuick/solanaTrilly --ref main`
   (dispatch-only; never re-add a push trigger).
2. Load the cohort (copy the file to the VPS or read from the repo fixture):
   `docker compose -p solanatrilly -f docker-compose.staging.yml exec -T copytrade_engine python manage.py load_cohort copytrade/tests/fixtures/cohort_2026-06-19_v1.json`
   (engine stays OFF after load).
3. Turn the engine ON (observe): set `CopyTradeSettings.engine_on=True` (mode stays
   `observe`) via shell or the dashboard engine toggle, then restart/observe the
   `copytrade_engine` service (it reads engine_on at start; the rewired command runs
   the live loop when engine_on + active cohort + mode==observe).
4. Watch `docker logs` for `[copytrade] copy-buy: head=… mint=… usd=… entry=…` then
   `[copytrade] copy-sell: head=… reason=… pnl=…%`. This is a SEPARATE Helius wallet
   subscription (§5) — independent of the firehose; can run concurrently with it (DoD #3).

### Known risks / follow-ups for the copy-trade soak
- **Entry-price unit consistency (validate first):** entry prefers `price_fn` (Birdeye
  USD/token); on a price-miss it falls back to the event's on-chain *curve* price
  (SOL/token) — different unit, would skew exit ratios. Rare, but **recommend hardening
  to skip the open on price-miss** rather than unit-fallback. (Flagged in PR #321.)
- **Post-grad buy detection:** the wallet source decodes pump.fun *bonding-curve*
  TradeEvents, so a watched wallet's POST-graduation buy (on PumpSwap) isn't detected
  yet. These cohort wallets are EARLY (first-10) buyers, so qualifying first-buys are
  overwhelmingly on-curve — but post-grad PumpSwap-buy decode is a documented follow-up.
- **Scalp mirror-sell wiring:** the mirror EXIT logic + the source-wallet-sell SIGNAL
  path (`handle_event` sell branch sets `sold_signals`) are built and unit-tested, but
  observe live that the source wallet's *sell* is actually detected end-to-end on the
  same wallet subscription.
- **Dashboard Copy-Trade tab** (per-wallet PnL, both heads) — APIs exist; the tab
  rendering both heads is still a follow-up (not built this session).

---

## 4. LIVE RESULTS (session-2 close — observe/paper)

### Copy-trade FIRED end-to-end on real data ✅ (DoD #2 — detection + open proven live)
Within ~5 min of turning the engine on, a cohort scalp wallet (`57stAMFv`) made a
qualifying ≥ $250 pump.fun buy and the engine copied it (paper):
```
[copytrade] copy-buy: head=consistent_scalp wallet=57stAMFv mint=AeFdnEYr usd=25.00 entry=5.15e-05
[copytrade] sell-signal: wallet=57stAMFv mint=AeFdnEYr (mirror trigger queued)   # source wallet sold ~6s later
```
A `CopytradePosition` (head=consistent_scalp, $25, open) + a shared `trading.Position`
(source=copytrade) were written; the mirror SELL-signal was correctly detected.

**Bug found live + FIXED (#325):** the mirror close could not BOOK — Birdeye REST has
no price for a fresh pre-grad mint, so `manage_positions` silently skipped and the
position sat open. Fix: book the mirror exit at the **source wallet's sell price**
(curve price from its sell event), unit-consistent with the curve-price entry and
independent of Birdeye. Deployed. (One orphan paper position predates the fix — harmless;
re-upload the cohort to wipe it.)

**Copy-trade still open:** observe a full scalp OPEN→CLOSE live *with the #325 fix*
(needs another trigger+sell); the **moonshot `our_trailing`** head needs a continuous
pre-grad price feed (the wallet sub only sees cohort-wallet swaps, not all swaps on the
held mint; Birdeye REST has no pre-grad price) — a documented price-source follow-up;
Dashboard Copy-Trade tab.

### Inference — root cause FIXED (#323); clean validation window running
The score-join never fired because the collection task DIED on the first Helius WS close
(~49 min in, no reconnect — §2). Fixed + deployed; reconnect code confirmed in the live
image. **A clean 3 h window is running** (the FINAL activation) to validate the reconnect
live (watch for `collection: started … attempt=2` at ~49 min) and catch a
`score (N>0) → paper-buy → paper-sell` in the back half.
**IMPORTANT LESSON:** a mid-window `deploy.yml` run KILLS `st_fh` (the deploy's compose
recreates services / removes orphans). **Do NOT deploy while a firehose window is
running.** (This cut the earlier combined window short.)

## 5. Budget & safety
- Firehose activations: **3 of 3 used** (window 1 = 90 min diagnosis; window 2 = brief
  combined run, killed early by a mid-window deploy; window 3 = clean 3 h validation,
  running). **0 remain — ask the operator before another.** TURN OFF after the window
  (`firehose_state off` + `docker rm -f st_fh`); the daemon auto-stops at max-runtime but
  the `firehose_active` flag persists. Copy-trade `engine_on=True` keeps its own Helius
  wallet sub running for ongoing observation — `engine_on=False` to stop that spend.
- Real capital NEVER used; `trading_enabled=False`; copytrade `mode=observe`.
  solanaBilly untouched.

## 6. PRs this session (all merged)
`#317` sol_usd spot · `#318` cohort-2.0 schema/models/lifecycle · `#319` live Helius
wallet source + USD trigger · `#320` per-head exit evaluators · `#321` engine runtime ·
`#322` handoff + seed TTL · `#323` **firehose collection/graduation reconnect** (inference
root-cause fix) · `#324` handoff correction · `#325` **scalp mirror-exit price**
(live-found fix). Copy-trade suite **376 green**; firehose reconnect + 131 firehose tests
green.
