<!--
file: AGENT_HANDOFF.md
purpose: Handoff to the next agent continuing the solanatrilly live (paper) cutover —
         both the prediction (inference) side and the copy-trade side.
owner: operator (Claude)
last-updated: 2026-06-19
-->

# solanatrilly — Agent Handoff (live cutover, paper)

Read this top to bottom before touching anything. It tells you what we're building, what's
already done and validated, the bugs already fixed (and the patterns behind them), exactly where
the inference side stands, and the copy-trade side you must also bring up.

---

## 0. The one rule that overrides everything
**This is OBSERVE / PAPER only. Never enable real capital.** `PipelineState.trading_enabled`
and `copytrade global.mode=live` stay OFF. The operator flips those, not you, and only after a
soak holds. Also: **never touch solanaBilly** (it is LIVE on the same VPS, project `solanabilly`,
port 8001) — every docker command must be scoped `-p solanatrilly`. Don't alter the VPS
`~/.ssh/authorized_keys`. Secrets are env-var-referenced only; never commit `.env`.

---

## 1. What we are building
solanatrilly is a **model-agnostic Solana pump.fun trading pipeline** with TWO sibling trading
heads that share one execution apparatus:

1. **Prediction pipeline** — at a token's *graduation* (pump.fun bonding curve → PumpSwap AMM),
   score it with a promoted model using **pre-graduation buyer-cohort features**, and if it passes
   the gate, take a position and manage it to an exit. Current model: `trilly_pregrad_v3.2`
   (15-booster LightGBM rank-blend, 20 `pre_*` features, entry at graduation, $25).
2. **Copy-trade pipeline** — a first-class SIBLING driven by a **JSON list of wallets** instead of
   a model: watch a cohort of proven early-buyer wallets; when one makes a qualifying first-buy of a
   pre-grad pump.fun token, copy it (paper), and exit per the strategy head's rule.

Both consume the SAME live data plane (Helius birth-tape collection + Birdeye graduation websocket)
and the SAME paper/observe execution path (`trading/` P8 apparatus). Everything is config-driven.

**Data architecture (the intended, correct design — confirmed with the operator):**
- **Helius** (`transactionSubscribe`, program-wide pump.fun) → birth-tapes for EVERY launched token,
  keyed by mint. These accumulate the model's pre-grad features (and copy-trade's early-buy signals).
- **Birdeye graduation websocket** (`SUBSCRIBE_TOKEN_NEW_LISTING`, filtered to `pump_amm`) → the
  graduation trigger. We do NOT poll for graduations and do NOT wait for Helius to infer them.
- Join by **mint** (contract address). Dead/idle tokens are evicted (two-tier idle-kill).
- Honor the anti-drift contract: live features must equal offline features (FeatureSet hash +
  `math_version`). See `/Users/asim/NoIcloud/solanatrills/docs/archive/t2_era/data-lake.md`.

---

## 2. Definition of Done
You are done when, **in paper/observe mode, end to end, observed live**:
1. **Prediction:** a pump.fun token is collected pre-grad → graduates → is scored by `trilly_pregrad_v3.2`
   → (if it passes the gate) a PAPER position opens, is managed, and closes with a recorded PnL —
   and you've watched this full chain fire on real live data and confirmed it's sane.
2. **Copy-trade:** the `copy_2026-06-19_v1` cohort is consumed; a cohort wallet's qualifying first-buy
   triggers a PAPER copy-position that opens/manages/closes per its strategy head — observed live.
3. **Both run concurrently**, isolated (own ON/OFF, positions, PnL), no shared mutable state, with
   the dashboard showing their state (Live Positions + Copy Trade tab).
4. Bugs found along the way are fixed via grouped hotfix PRs (CI green, deployed), with the firehose
   turned OFF after every observation window and **no real capital ever used**.
5. A clean handoff/report at the end. Operator-gated lines (production firehose at scale, real-capital
   flips, the live Cutover §16) are surfaced for the operator, NOT crossed by you.

The firehose costs money. Use it in deliberate, time-boxed windows; turn it OFF after each; bank
durable fixtures so you don't re-burn budget. The operator grants windows — ask if you need more.

---

## 3. Current state (what's already done)
- **BUILD complete:** sprints 1–14 shipped (v14.0.0). All components + offline tests exist. The live
  runtime ("cutover") was always deferred to the operator — that's the work in progress now.
- **Model promoted:** `trilly_pregrad_v3.2` is ACTIVE in `ModelRegistry` (feature-contract gate
  passed). Seeded `FeatureSet` (`enrich20_buyer_cohort`, `math_version=solanabilly3:sprint-9`, 20
  `pre_*` features) + an active observe/paper `PipelineConfig` (score@120s, two-tier idle-kill
  1800/300, $25 paper). Boosters live on a **deploy-surviving host volume** `/root/solanatrilly/models`
  mounted read-only into `web`+`celery-worker`+`listener`+`copytrade_engine` (in the committed
  `docker-compose.staging.yml`). Re-runnable via `tools/cutover_seed_and_promote.py`.
- **Live spine built + deployed** (PRs #310, #313–#316): `core/management/commands/run_firehose.py`
  (the gated daemon), `core/tape/birdeye_graduation_source.py`, `core/firehose/live_pregrad_buffer.py`,
  `core/firehose/spine.py`, `tools/firehose_state.py` + `manage.py firehose_state` (on/off toggle).
- **`PipelineState`:** `scoring_enabled=True`, **`firehose_active=False`, `trading_enabled=False`.**

### Validated LIVE already (paper)
Helius collection streaming (~13k swaps/window), pre-grad buffer (~360 mints), two-tier idle-kill
(700+ evicts/window), Birdeye graduation detection (50+ pump.fun tokens/window), bounded post-grad
subscription, scoring scheduler running (correctly defers when a mint has no tape), paper-trade wiring.
Zero errors. solanaBilly untouched throughout.

---

## 4. Inference side — common issues already FIXED (learn these patterns)
All offline tests passed but the LIVE runtime had integration bugs offline tests can't catch. The six
already fixed (each a deployed PR) — and the lesson behind each:

1. **#311 — No logging config.** `config/settings.py` had no `LOGGING`; Python's default swallowed
   everything below WARNING, so the daemon ran SILENT. Added a stdout console handler for
   `core`/`copytrade`/`trading` (env `DJANGO_LOG_LEVEL`; set `DEBUG` to see raw WS frames). *Lesson:
   you can't debug what you can't see — confirm `[FIREHOSE]` logs appear first.*
2. **#312 — Graduation layer.** Birdeye `TOKEN_NEW_LISTING_DATA` streams ALL DEXes; filter to
   pump.fun (`data.source == "pump_amm"`). Stamp the emitted event `source="pump_dot_fun"` so it
   passes the `DetectionConsumer` filter. Map `liquidityAddedAt` (ISO-8601) → epoch. Frames have NO
   pool address (null-tolerant). *Lesson: build to the REAL frame, confirmed via DEBUG raw-frame logs.*
3. **#313 — TapeStore populated only after the recorder finished** (a continuous live source never
   "finishes") → stream each swap into the in-memory store as it's recorded (`on_swap` tap).
   *Lesson: batch-after-loop patterns silently no-op against a continuous live stream.*
4. **#314 — Paper-trade had no post-grad price data** (it fed pre-grad swaps with a post-grad entry
   ts → settler returns `enterable:False`). Added a **bounded** post-grad `BirdeyeSwapSource`
   collection per graduated mint (config `tape.max_postgrad_subscriptions`, default 5; TTL =
   score_at + outcome window). *Lesson: post-grad price tracking is a separate subscription; bound it
   for spend.*
5. **#315 — Pre-grad collection only recorded ALREADY-graduated mints.** `build_birth_tape_recorder`
   needs the graduation anchor up front (to compute `rel`), so pre-grad swaps were never captured
   (chicken-and-egg). New `core/firehose/live_pregrad_buffer.py` buffers ALL bonding swaps by mint
   with absolute `block_time`, **anchors retroactively at graduation**, and **idle-kills dead mints**
   (`tape.pre_grad_idle_kill_ttl_s`, default 300). *Lesson: the offline lab knows winners post-hoc; live
   must buffer everything and anchor late.*
6. **#316 — THE keystone: Helius endpoint.** The source used `wss://atlas-mainnet.helius-rpc.com`
   (Atlas/Geyser) which **streamed ZERO frames** under this account's plan → buffer never filled →
   nothing ever scored. The working reference (solanaBilly `app/services/helius_listener.py`) uses the
   STANDARD `wss://mainnet.helius-rpc.com/?api-key=…` for the same `transactionSubscribe`. Matched it.
   *Lesson: when a live source is silent, diff against solanaBilly — it's the proven Helius reference.*

### The ONE remaining inference gap (start here)
**Graduated-token SCORING has not yet been observed firing live.** In every window so far, no single
token was both *buffered* (launched during the window) AND *graduated* (within the same window) —
pump.fun launch→graduation usually takes longer (~30 min to hours) than the windows ran, so the
graduated tokens always launched before collection started (buffer = 0 swaps for them), and the
buffered tokens hadn't graduated yet. The mint-key join (Helius `_b58_from_bytes(pubkey)` ==
Birdeye `address`) is verified in code/tests but **not yet confirmed end-to-end live.**

**Next step:** run ONE long window (**60–90 min**) so tokens launched in the first ~15 min graduate
while their pre-grad buffer exists. Watch for `[FIREHOSE] score: mint=… score=… gate=…` (N>0 swaps),
then `paper-buy` → `paper-sell`. **If a token you can confirm was buffered still scores 0**, suspect
the pump.fun "**bonding-curve PDA vs real SPL mint**" extraction gotcha (solanaBilly documented it in
`update_sprint6.py:49` — resolving the actual SPL mint can need a follow-up Helius call); verify the
mint string Helius buffers equals the `Token.mint` Birdeye persisted for the same token.

---

## 5. Copy-trade side — your new major workstream
The operator built a **wallet cohort** for solanatrilly to consume:
`/Users/asim/NoIcloud/solanatrills/models/copy_2026-06-19_v1/` → **`cohort.json`** (schema
`copytrade-2.0`) + `COPY_HANDOFF.md` (read it — full detail). It is NOT a model; copy-trade is
wallet-driven.

**What the cohort is:** two INDEPENDENT strategy heads (run either/both, concurrently):
- **`consistent_scalp`** (2 wallets): copy the wallet's ≥ $250 first-buy of a pump.fun token; **exit by
  MIRRORING the wallet's sell** (no imposed TP/SL; fallback market-sell at `max_hold=24h`). Profile:
  ~90% win, median ~1.3x.
- **`moonshot`** (3 wallets): same entry; **exit on OUR rule** — hard SL −50%, 40% trailing giveback
  once profitable, TP +900% (10x). Profile: low win, fat tail.
- **Entry (both):** when a cohort wallet makes its **first buy of a pump.fun token with buy ≥ $250**,
  copy-buy **$25** at the same token; **one position per token (dedupe across wallets)**.
- **`global.mode = observe`** (paper) by default. **Do NOT set `live`.**
- **Wallets DECAY** — this is a 2026-06-01..06-14 snapshot with zero cross-fold persistence.
  Regenerate every ~14 days (research-side `copytrade_formula.py`/`moonshot_formula.py`) and upload a
  new `cohort_id`. Treat the cohort as a swappable input, never hard-code wallets.

**What already exists (sprint-12):** the `copytrade/` app — `wallet_consumer.py`, `buy_trigger.py`,
`trigger_pipeline.py`, `position_manager.py`, `position_opener.py`, `execution.py`,
`cohort_lifecycle.py` (fresh-start per upload), `engine_control.py` (ON/OFF), `api.py` (dashboard tab),
`export_builder.py`, `models.py` (`copytrade_`-prefixed tables), `schemas.py`,
`management/commands/run_copytrade_engine.py`. Built observe/paper, §5-isolated. Full scope:
`scrum-master/solanatrilly_copytrade_SPEC.md`.

**Your copy-trade tasks (DoD #2):**
1. **Reconcile the cohort format.** The engine was built before this `cohort.json` existed; confirm
   `wallet_consumer`/`cohort_lifecycle` parse schema `copytrade-2.0` (two heads, per-head `exit`,
   `global.mode/usd_size_per_trade/min_buy_usd`). If not, adapt the loader to this contract (it's the
   real consumption format). Upload `copy_2026-06-19_v1/cohort.json` into the engine.
2. **Bring up the live copy-trade daemon** the same way the firehose spine was brought up: it needs
   its OWN Helius **wallet-address** subscription (SPEC §5 — subscribe to WALLETS, not the token
   firehose), the first-buy ≥ $250 detection, dedupe-per-token, and the two exit heads, all paper.
   Gate it on the copytrade engine ON/OFF, isolated from the prediction firehose.
3. **Observe it live** (paper) in a window alongside the prediction pipeline; fix bugs (hotfix PRs).
4. **Expect the SAME bug classes as the inference side** to transfer — they share primitives:
   - The **Helius endpoint** must be `mainnet`, not `atlas` (fix #316) — check the wallet subscription
     uses the working endpoint.
   - **Logging** must surface `[copytrade]`/engine logs (fix #311 added core/copytrade/trading loggers).
   - **Live buffering vs batch-after-finish** (fix #313/#315) — the wallet consumer must process the
     stream incrementally, not assume a finite source.
   - **Mint/address identity** — the same SPL-mint extraction care (fix #312 + the gap in §4).
   - **Spend bounds** on any per-token/per-wallet subscriptions (fix #314 pattern).

---

## 6. How to operate (commands)
All on the VPS, scoped `-p solanatrilly`. SSH: `ssh root@140.82.43.36`.
Compose file: `/root/solanatrilly/docker-compose.staging.yml`.

- **Firehose ON/OFF/status:** `docker compose -p solanatrilly -f docker-compose.staging.yml exec -T web python manage.py firehose_state on|off|status`
- **Run the prediction daemon (a window):** run it on the `listener` service (it has HELIUS+BIRDEYE
  keys + the model mount):
  `docker compose -p solanatrilly -f docker-compose.staging.yml run -d --name st_fh -e DJANGO_LOG_LEVEL=DEBUG listener python manage.py run_firehose --max-runtime-seconds 5400 -v 3`
  then `docker logs st_fh` (grep `[FIREHOSE]`). `--max-runtime-seconds` is a hard auto-stop; STILL run
  `firehose_state off` after, and `docker rm -f st_fh`.
- **Copy-trade engine:** `… run -d --name st_ct listener python manage.py run_copytrade_engine …`
  (verify keys/mount; check the command's flags).
- **Dashboard (live, viewable):** `http://140.82.43.36:8002/dashboard/` (root `/` 404s — served at
  `/dashboard/`; a root redirect is a nice-to-have). Live Positions APIs:
  `/api/trading/positions/{open,closed}/`. Copy Trade tab + APIs under the copytrade app.

### Dev workflow (unchanged, enterprise)
- Branch → hotfix/feat PR → CI green → squash-merge → **deploy is dispatch-only**:
  `gh workflow run deploy.yml --repo AsimQuick/solanaTrilly --ref main` (NEVER re-add a `push:` trigger
  to `deploy.yml` — a guard test `core/tests/test_deploy_dispatch_only_guard.py` blocks it; that
  per-commit-deploy noise was a real problem we fixed).
- Tests run in Docker: `docker compose run --rm web sh -c "ruff check … && pytest -q -k …"`.
- The deploy runs in-container import + smoke tests (incl. AC-68.3 — uses `django.setup()`; don't
  break that), Live Positions HTTP 200, and a **solanaBilly isolation** check.
- The gitops orchestrator that BUILT the project is done (project-complete); you are the operator now.
  You can still drive it for net-new sprints if useful, but the cutover work is hands-on.

---

## 7. Key files & pointers
- Live spine: `core/management/commands/run_firehose.py`, `core/firehose/{live_pregrad_buffer,spine}.py`,
  `core/tape/{birdeye_graduation_source,birdeye_swap_source,helius_birth_tape_source,recorder}.py`,
  `core/detection/consumer.py`, `core/scorer.py`, `core/pregrad_features.py`, `core/score_orchestrator.py`.
- Cutover/seed: `tools/cutover_seed_and_promote.py`, `tools/promote_model.py`, `tools/firehose_state.py`.
- Copy-trade: `copytrade/*` (see §5), `scrum-master/solanatrilly_copytrade_SPEC.md`.
- Model artifacts (host): `/root/solanatrilly/models/trilly_pregrad_v3_2/` (boosters+meta).
  Source on Mac: `/Users/asim/NoIcloud/solanatrills/models/{trilly_pregrad_v3_2,copy_2026-06-19_v1}/`.
- Working Helius reference: `/Users/asim/NoIcloud/solanaBilly/app/services/helius_listener.py`.
- Anti-drift contract: `/Users/asim/NoIcloud/solanatrills/docs/archive/t2_era/data-lake.md`.
- Memory: `solanatrilly-live-cutover-state`, `solanatrilly-endgame-and-oracle-direction`,
  `django-vps-deploy-gotchas` (operator memory dir).

## 8. Operator-gated — STOP, do not cross
Production firehose at scale, ANY real-capital flip (`trading_enabled=True`, copytrade `mode=live`,
real PumpSwap orders / the trading wallet), and the §16 Cutover. Surface these for the operator;
never do them yourself. Paper/observe is your whole sandbox.
