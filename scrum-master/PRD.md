<!--
file: solanaTrilly_PRD.md
purpose: Definitive PRD for solanaTrilly — the graduated-pump.fun-token prediction & trading pipeline
status: v1.0 — consolidated, buildable. Supersedes all prior drafts (v0.1–v0.4).
owner: operator (asimf.musa@gmail.com)
authored-by: Claude (Opus 4.8), from a full audit of solanaBilly (code + 360 merged PRs), solanabilly3, solanatrills, and live API research
last-updated: 2026-06-14
-->

# solanaTrilly — Product Requirements Document (v1.0, definitive)

> **This is a single, self-contained spec.** It folds in every earlier decision and the research that
> resolved the open unknowns. There are no "superseded, reflow later" layers — if it's here, it's the
> decision. It is written to be **built autonomously, overnight**, by an agent with read access to
> `solanaBilly`, `solanabilly3`, and `solanatrills`. Concrete program IDs, discriminators, endpoints,
> schemas, and a file-by-file port manifest are included so the agent does not have to guess.
>
> **[TO TUNE]** marks operator tuning knobs (defaults + reasoning given). **[VERIFY-LIVE]** marks the
> few facts to confirm against a live API key before relying on them. Everything else is direction.
>
> ⚠️ **DJANGO IS NEW TO THIS MACHINE AND THE VPS.** This is the first Django project on both the dev
> machine and the VPS — solanaBilly is Flask. **When something breaks, suspect the environment before
> the code.** A missing/old Django, DRF, Channels, ASGI server (Daphne/Uvicorn), `channels-redis`,
> `psycopg[binary]`, or Node/Vite for the frontend will throw errors that *look* like bugs but are
> just absent dependencies. Before head-banging on a "code issue": confirm the dependency is installed
> **in the right place** (it must be in the Docker image, not the host — Docker rules still apply), that
> the image was rebuilt after a `requirements.txt`/`package.json` change, and that the VPS pulled the
> new image. Pin all Django-stack versions in `requirements.txt` and treat ImportError / "no module" /
> ASGI-handshake / channel-layer-connection failures as environment-first, code-second. This will
> recur throughout the project — make it a first check, not a last resort.

---

## 1. Vision & scope

**solanaTrilly detects pump.fun tokens at graduation** (the moment they complete the bonding curve and
migrate to the PumpSwap AMM), records their complete swap tape, scores them against a promoted model,
and trades the winners — with a real dashboard the operator can actually see.

- **It REPLACES solanaBilly.** Once the crown jewels are ported (§14), the curve-token pipeline is
  decommissioned. solanaTrilly is the one live pipeline going forward.
- **Same PR ceremony:** small revertible PRs against `main`, lowercase prefixes
  (`detection:`/`tape:`/`features:`/`scoring:`/`trading:`/`dashboard:`/`ops:`), one squash each,
  structured file front-matter, tests in Docker, green required-CI as a hard merge gate.

### 1.1 Why graduated tokens
Only ~1% of pump.fun tokens graduate; graduation is itself a brutal filter that removes most of the
rug/dead-on-arrival population solanaBilly burned months dodging. A graduated token has a clean,
observable t0 (the migrate event), a real AMM with locked (LP-burned) liquidity, and a complete free
behavioral record (every PumpSwap swap is captured from Birdeye — parity-verified 100%-faithful to raw chain, Helius firehose as cross-check). **The edge shifts
from "detect the rug before the curve drains" to "rank which survivors pump."**

> **Honest caveat (do not oversell):** graduated PumpSwap pools are still thin and highly variable
> (constant-product `x*y=k`, 0.25% fee). Per-token liquidity/depth is a **first-class collected field**
> that gates fill, sizing, and honest settlement — never an assumption.

### 1.2 The three-repo reality
| Repo | Role | Owns |
|---|---|---|
| **solanaTrilly** *(this, new)* | Live pipeline: detect → record tape → collect → score → trade → visualize | The serving plane + the lake |
| **solanabilly3** (local) | Offline training lab | The canonical feature **math** (`src/tape_microstructure.py`), the **feature contract**, and `promote_model.py` |
| **solanatrills** (local) | Strategy/labels lab | The model **artifacts**, the **labels**, and the **`tape_resettle.py` settler oracle** |

The labs stay local with no PR ceremony. **Their needs are inputs; this PRD has veto.** The live
pipeline must be robust and replay-testable; the labs work with what we can serve safely, not the
reverse. solanaTrilly's job: land clean, leak-free, drift-free, parity-tested data, and serve a
promoted model honestly.

---

## 2. Core principles (non-negotiable — each fixes an evidenced solanaBilly failure)

1. **Config-driven by a single source of truth.** Every tunable (detection filter, scoring time,
   feature set, active model, exit policy) lives in *one versioned `PipelineConfig`*, validated by a
   typed schema, edited in the UI, read by *every* service via one resolver. No knob in a code
   constant or a scattered `os.getenv`. *(Pain: `SCORE_AT_POLL`/`OHLCV_WINDOWS` in code,
   `trading_config_json` POSTed by hand — regime churn across #314/#316/#402.)*
2. **Parity by construction — one feature library, all callers.** Feature math is *vendored verbatim*
   from `solanabilly3/src/tape_microstructure.py` and imported byte-identically by the live scorer,
   the offline CSV builder, and the replay harness. A model's feature contract is *data* on its
   registry row, honored everywhere. *(Pain: live≠offline — the t2 go-live got 23% live precision vs
   78% offline purely from assembly drift; #358/#359/#367.)*
3. **Tape-first, tape-only for signal.** The recorded PumpSwap tape is the primary dataset; candles,
   volume, flows, concentration, holder churn all derive from it. The only non-tape live read is a
   single on-demand snapshot at score time (§6.3). *(Pain: poll-first; tapes bolted on late at #399.)*
4. **One price basis, changed in one coordinated pass.** ONE price basis — the **Birdeye per-swap price**,
   identical in backfill and live (the training lake is Birdeye; a separately-decoded reserve-implied price
   cannot be reproduced offline, so it is NOT used) — flows through the recorder, the settler, the rug
   signals, the microstructure math, and the execution chain. Defined once; every consumer agrees by
   construction — including the trills grader. *(Pain: the #391 price-basis mismatch silently settled
   every row at ~−93% — a second price basis on the live side is exactly that bug.)*
5. **Verify against captured reality, never an approximation.** Settlement, parity, and model checks
   validate against the recorded tape (the trusted oracle), real Postgres replays, and golden vectors.
   *(Pain: the settler was rebuilt 5×; each validated against an approximation of the path it was
   estimating until graded against the real tape — #390/#391/#395/#396/#397/#400/#403/#405.)*
6. **Port, don't rewrite.** The execution edge cases (ghost-buy, slippage tiers, Sender 5xx→RPC +
   circuit breaker, Anchor 6002/6003/6023 decode, the rug ring buffer, the exit engine, the settler)
   are crown jewels — lifted verbatim, adapted only where the AMM basis demands. *(Every one was found
   live: #288/#301/#303/#305/#317/#376/#377/#385.)*
7. **One code path, swappable feed + clock.** Live and replay are the same code behind a `DataSource`
   interface and an injectable clock. This is the foundation of the testing strategy (§11) and falls
   out of §6.4 done right.
8. **Visualization is foundational.** A real dashboard — live positions on candles, a pattern-mining
   wall, calibration analytics, replay viewer, human annotation → labeled export (§13). *(Pain: the
   old UI was spec'd to forbid a frontend framework — static tables, no charts, no realtime.)*

---

## 3. Hard facts — the resolved unknowns (so the agent doesn't guess)

Researched against live docs/IDLs, June 2026. Confidence noted; **[VERIFY-LIVE]** = confirm with a key.

### 3.1 pump.fun graduation
- Graduation is **reserve-based**, not a fixed SOL/USD number: the curve completes when
  `complete == true` / `real_token_reserves → 0`. The "~85 SOL / ~$69k" figure is derived and drifts
  with SOL price — **do not hardcode it.** *(HIGH)*
- Since **2025-03-20, graduated tokens migrate to PumpSwap** (pump.fun's own AMM), **not Raydium**.
  PumpSwap is the default and effectively sole destination. *(HIGH)*
- The on-chain marker is the permissionless, idempotent **`migrate(user, mint)`** instruction on the
  pump program, which CPIs PumpSwap **`create_pool`** (canonical pool index 0) and burns all LP. Trigger
  "graduated → track" on this. *(HIGH)*

| Program | Address |
|---|---|
| pump.fun bonding-curve program | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` |
| **PumpSwap AMM program** | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` |
| Raydium AMM v4 (legacy/secondary only) | `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8` |

### 3.2 Birdeye WebSocket (detection)
- Endpoint `wss://public-api.birdeye.so/socket/solana?x-api-key=KEY` — **key in the URL**, handshake
  headers `Origin: ws://public-api.birdeye.so`, subprotocol `echo-protocol`. *(HIGH)*
- **Detector = `SUBSCRIBE_MEME` with `source=pump_dot_fun`** and the `graduated` field/filter. Payload
  `MEME_DATA` carries `address` (the mint) directly, plus `graduated`, `graduated_time`,
  `progress_percent`, price/liquidity/mcap. `progress_percent` lets us **pre-stage** tokens approaching
  graduation (warm the recorder before t0). *(HIGH)*
- `SUBSCRIBE_TXS` (per-token parsed trades, carries `owner`/price/side/volumeUSD) exists as a managed
  alternative to firehose decoding — optionally used to drive the live chart for open positions only.
- **Tier is contradictory in Birdeye's own docs** (Premium $199 vs Business $499; 500 vs 2000
  connections; 100 tokens/connection). **[VERIFY-LIVE]** against the account dashboard before budgeting.

### 3.3 The swap tape — Birdeye primary; Helius = raw-truth verification
**Source decision (parity-driven, verified 2026-06-14).** The training lake is built from Birdeye
(`seek_by_time`), and a parity probe confirmed Birdeye is **100% faithful to raw chain** (every on-chain
swap present; side + amount exact, matched by tx signature; 3 mints). To honor Principle #2 (one
representation both sides) and the credit reality (**Birdeye allowance ≫ Helius**), **the live tape is
recorded from Birdeye — the same source the model trains on** — so live↔backfill is byte-parity by
construction. There is **no PumpSwap decoder on the live path.**
- **Live tape:** Birdeye `SUBSCRIBE_TXS` (per-token parsed swaps: `owner`, price, side, volumeUSD, `txHash`)
  for real-time, **reconciled at settle by the *identical* `seek_by_time` REST call the backfill uses**
  (closes any WS gap → exact backfill parity).
- **Price basis = Birdeye per-swap price** (`tokenPrice`, quote-implied), identical backfill+live (Principle #4).
  Do **not** add a separately-decoded `quote_reserve/base_reserve` price on the live side — the Birdeye lake
  can't reproduce it (#391 reincarnated).
- **Trader = the tx signer.** VERIFIED: Birdeye `owner` **is** the fee-payer/signer (100% on 3 mints). Use the
  signer as canonical trader **both sides** (the lake already does). In ~8–16% of swaps the signer's own token
  balance didn't move (bundled/proxy/router) — that signer≠beneficiary gap is a *coordination feature*, not an
  error. (This REPLACES the old "use `postTokenBalances.owner`, never `accountKeys[0]`" rule, which would
  attribute a *different* wallet than the trained lake = a parity break.)
- Fee: PumpSwap **0.25%** (0.20% LP + 0.05% protocol). *(HIGH)*

**Below: the Helius PumpSwap decode — retained ONLY as the G2 raw-truth verification oracle (§7.6) + optional
low-latency redundancy, NOT the operational tape:**
- `transactionSubscribe` params: `accountInclude=[pAMMBay6…]`, `failed:false`, `vote:false`, options
  `encoding:"jsonParsed"`, `transactionDetails:"full"`, **`maxSupportedTransactionVersion:0`** (essential —
  AMM swaps use v0/ALT txs; omitting it silently drops most swaps). *(HIGH)*
- PumpSwap is Anchor and **emits program-data event logs** — decode those, no vault-diffing needed:
  - Instruction discriminators: `buy = 66063d1201daebea`, `sell = 33e685a4017f83ad`.
  - Event discriminators: `BuyEvent = [103,244,82,31,44,245,119,119]`,
    `SellEvent = [62,47,55,10,165,3,220,42]`.
  - The event carries `pool_base_token_reserves`, `pool_quote_token_reserves`, `lp_fee`,
    `protocol_fee`, amounts, and **`user` (the trader)**. *(HIGH)*
- **Price** = `quote_reserve / base_reserve` (decimal-scaled); when quote = WSOL (the usual case, 9 dec):
  `price_SOL = (quote_reserve/1e9)/(base_reserve/10^baseDec)`. **Always read `quote_mint` first** — don't
  hardcode WSOL. Trade **size in SOL** = the quote leg; **USD** = `size_SOL × sol_usd` at that block. *(HIGH)*
- **Trader (verification only):** `postTokenBalances[].owner` / event `user` = the *beneficiary*; this DIFFERS
  from the canonical *signer* (Birdeye `owner` = `accountKeys[0]`) in ~8–16% of bundled swaps. The **signer is
  canonical** (parity with the lake); the beneficiary is a verification / coordination cross-check, not the
  trader id used by features. *(HIGH)*

### 3.4 Pre-graduation history (training enrichment only)
- `GET /defi/v3/token/txs-by-volume?source=pump_dot_fun` returns historical bonding-curve swaps with
  per-swap **`owner` wallet**, price, side, time — so wallet-level features are reconstructable offline.
  *(HIGH; `seek_by_time`'s `source` support and earliest-second coverage are **[VERIFY-LIVE]**.)*
- **This is training-lake enrichment only — never a synchronous dependency of a live score** (§9 D2).

### 3.5 Dune — OFFLINE backfill / training only (never a live source)
Dune decoded-SQL is a batch warehouse (no realtime) — a **training-lake** source, NOT a serving dependency.
- **Cohort seed:** `pumpdotfun_solana.pump_evt_completeevent` = the native graduation event (mint + exact ts),
  complete (~250/day) — we generate our own seed (no third-party saved query).
- **Cross-wallet / pedigree aggregates (causal, cutoff < t0):** deployer recidivism via
  `pumpdotfun_solana.pump_evt_createevent` (the EVENT — complete ~30k/day; `pump_call_create` is SPARSE, do
  NOT use); recidivist-dumper blocklist; smart-money PnL snapshots — all from `dex_solana.trades` (`trader_id`).
- **Cost:** scan-dominated (~2.4 credits per cohort-month regardless of cohort size) — the free tier suffices;
  do not buy a paid plan.
- **HARD RULE (parity, D2/D3):** a Dune-derived feature is `training_only` UNLESS it has a byte-equivalent
  **live** computation (e.g. the funding-graph via Helius `funded-by`, §3.6). The save-time validator REJECTS
  any active model whose contract includes a `training_only` feature.

### 3.6 Funding graph / "who's-behind-it" — the live Helius primitive (candidate axis)
The one cross-wallet signal that IS live-computable: Helius **`funded-by`** (`GET /v1/wallet/{addr}/funded-by`)
returns the degree-1 funder + `funderType` (CEX label baked in). Recurse to **depth-2**, STOP at CEX roots, cap
fan-out, MEMOIZE (hubs/CEX recur across tokens). Computable LIVE at score time over deployer + early buyers
(~30–50 calls/token, mostly cache hits; enforce a score-time budget + NaN-on-timeout). This is the live
equivalent that lets *cross-wallet* features be `live_servable` (Dune cannot serve). **Status: CANDIDATE,
UNPROVEN** — a first depth-2 prototype on 120 graduated pilot tokens did NOT separate winners/rugs (AUC
0.40–0.57; graduation likely already filters the blatantly-coordinated launches). Keep the primitive wired;
treat its features as research until an observe soak says otherwise.

---

## 4. Architecture overview

```
                 Birdeye SUBSCRIBE_MEME (graduation)            Birdeye SUBSCRIBE_TXS (swap tape)
                 + Helius migrate reconciler                    + seek_by_time reconcile · Helius=verify/funded-by
                            │                                          │
                            ▼                                          ▼
   ┌─────────── listener container (dedicated, never shares gunicorn) ───────────┐
   │  DETECTION → Token row (mint, pool, graduated_at=t0)   TAPE RECORDER         │
   │                          │                          (Birdeye swaps → NormalizedSwap)
   └──────────────────────────┼──────────────────────────────────┬──────────────┘
                              ▼                                    ▼
                  on-demand score-time snapshot          jsonl.gz lake + swaps table
                  (holders/authority/liquidity, once)    (raw = immutable truth)
                              │                                    │
                              └──────────────┬─────────────────────┘
                                             ▼
                          shared feature library (vendored from solanabilly3)
                              │                         │                    │
                        live scorer            one-click CSV builder    replay harness
                              │                         │                    │
                      model_registry            → labs (train)        sandbox runs → dashboard
                              │
                        trading + exit engine (ported) → settle == tape oracle
```

**The seam that makes it all testable (Principle #7):** every consumer reads events from a
`DataSource` and time from an injected clock. `LiveSource` = Birdeye/Helius + wall-clock; `ReplaySource`
= the lake + a virtual clock. Detection, feature assembly, scoring, exit eval, and settlement never know
which they're on. Build this from P0 (§16) — retrofitting it is the expensive path.

**Stack:** Django 5 + DRF (control plane + API), Django Channels (realtime UI), a React + TradingView
Lightweight Charts frontend (§13), Celery + Redis (tasks + rate-limit token bucket + Channels layer),
PostgreSQL 16 (JSONB raw), Pydantic v2 (config schema), Docker compose. ALL services in containers;
solanaBilly's Docker rules carry over verbatim.

---

## 5. The config-driven core

### 5.1 `PipelineConfig` — one versioned source of truth
A single Django model holds an immutable, versioned snapshot of everything tunable; activation is one
atomic flip with instant rollback (mirrors `model_registry.is_active`).

```
PipelineConfig
├── id, version, label, is_active, created_at, created_by, notes   (django-simple-history audited)
├── detection   { source:"birdeye_meme", filter:{source:"pump_dot_fun", graduated:true},
│                 prestage_progress_pct, dedupe_window_s, reconciler:"helius_migrate" }
├── tape        { amm_programs:["pumpswap"], idle_kill_ttl_s, reattach:true }
├── scoring     { score_at_elapsed_s, window_s, capture_buffer_s, universe_gate }
├── feature_set → FK FeatureSet   (vendored math version + column list)
├── model       → FK ModelRegistry
├── outcome     { window_s, label_def }            # offline labelling only
└── trading     { enabled, gate, sizing, exit_policy, paper_size_usd }   # §10
```

### 5.2 Invariants the Pydantic schema ENFORCES at save time (so they can't drift)
- `scoring.window_s` is closed before `scoring.score_at_elapsed_s` (leak guard).
- `model.feature_contract ⊆ feature_set.columns` **and** ⊆ the **`live_servable`** set — fails in the
  UI, never live with "REFUSING TO SCORE" (D2).
- `tape.idle_kill_ttl_s ≥ outcome.window_s` (D4 — never truncate a label).
- `scoring.capture_buffer_s ≥ 3` (the tape tail must land before scoring; §7).
- `gate` is `adaptive_topk` (never a fixed score threshold for a regression ranker — §9, the id22 lesson).

### 5.3 The resolver — every service reads from here
A single cached `get_active_config()` is the *only* way any service learns a tunable. Changing the
scoring time or feature set = edit + activate in the UI; workers pick it up on the next task, no
redeploy. Operator-only actions (start firehose, enable trading) are permission-gated, confirmed admin
actions. The system **never auto-starts or auto-recovers** the firehose — it is started deliberately
(the operator for production; dev/test within the §15.7 activation budget), so a WS drop never silently
resumes. *(Agents may start it; the point is no silent/automatic start, not a ban.)*

---

## 6. Data plane

### 6.1 Detection
- **Primary:** Birdeye `SUBSCRIBE_MEME` (`source=pump_dot_fun`, `graduated`). On a graduation event →
  create a `tokens` row (`mint`, `pool_address`, `graduated_at`=t0, raw event JSONB), warm the recorder,
  schedule the score-time snapshot + scoring. Use `progress_percent` to **pre-stage** near-graduation
  mints so the recorder is attached before t0.
- **Gap-recovery reconciler (D4):** a Helius `transactionSubscribe` on the pump program filtered to the
  `migrate` instruction runs as a backstop — a dropped Birdeye WS event never loses a token. A periodic
  Birdeye REST sweep of recent graduations is the third belt.
- Dedicated `listener` container (solanaBilly #289 lesson). Dedupe within `dedupe_window_s`.

### 6.2 The tape recorder (the heart)
Port `app/services/tape_recorder.py`'s queue/writer/window/coverage scaffolding verbatim (source-agnostic,
hard-won), adapting the recorder to consume the **Birdeye swap stream** (§3.3). For each swap, emit the
**one normalized-swap schema** (§7.1).

- **Source = Birdeye `SUBSCRIBE_TXS` live + `seek_by_time` reconciliation** (§3.3) — the same source the lake
  is built from. The Helius PumpSwap decode is the G2 raw-truth verification oracle, NOT the operational tape.
- **Price basis = Birdeye per-swap price** (Principle #4) — identical to the lake; carry the raw Birdeye swap
  fields so price is reconstructable. (Pool reserves are carried only when the Helius verification path runs.)
- **Units (D1):** store **`vol_sol`, `vol_usd`, and the `sol_usd` reference** on every swap. Unit-invariant
  features (shares, ratios, counts) are the parity backbone; quote-unit features declare their column.
- **Storage:** append-only daily-partitioned `jsonl.gz` (`lake/tapes/dt=YYYY-MM-DD/part-*.jsonl.gz`) +
  a queryable `swaps` table (parquet promoted nightly). Reader tolerates truncated tails (port
  `_iter_tape_rows` — recovered 161k rows from truncated parts).
- **Canonical ordering `(block_time, slot, signature)` with a STABLE sort** — never `block_time` alone;
  within-second order flips a −50%-disaster vs a timer exit (#403). Port verbatim.
- **Owner = the signer** (= Birdeye `owner`; canonical both sides, §3.3). **Drop failed swaps** (landed-only).
  **Zero-/degenerate-swap guard** (a degenerate swap caused a live `ZeroDivisionError`, #405).
- **Idle-kill TTL (D4):** deactivate-but-re-attach on the next swap (the firehose still sees the mint);
  TTL ≥ outcome window. Never go blind on a quiet-then-pump token.

### 6.3 Score-time snapshot (the only non-tape live read)
**No scheduled polling** (solanaBilly's per-token poll regime is retired — that cost cut funds retiring
solanaBilly). Take **at most one** on-demand Birdeye REST snapshot per token, at `score_at_elapsed_s`,
for the things the tape cannot give: holder distribution, mint/freeze authority, LP-burned flag, and a
liquidity/TVL/depth read (a first-class field per §1.1). Keep the Redis token-bucket limiter; drop the
scheduler. Clamp any `to=` window to `now` (Birdeye 400s on future windows — #380). Store raw JSONB.

### 6.4 The lake & extraction contract (THE integrity core)
1. **Raw = immutable truth.** Every swap and every snapshot stored verbatim, append-only, partitioned.
   Never mutated, never re-pulled. Features are *always* re-derivable from raw — change a definition,
   rebuild; never re-collect. Raw must preserve per-swap wallet + reserves so any future feature is
   buildable later.
2. **One normalized-swap schema across all sources** (live Helius/PumpSwap, on-demand Birdeye pre-grad,
   offline backfill) — §7.1. `[AGENT: VERIFY]` per source that per-swap wallet is present; any source
   lacking it ⇒ wallet features are `training_only`, auto-excluded from `live_servable` (D3).
3. **Parity by golden vectors (the #1 gate).** A frozen token set must produce byte-identical normalized
   swaps **and** feature values across sources. CI merge gate (G1/G2, §7.5).
4. **Leak-free by the causal cutoff.** Every feature is stamped with its `[0, window_s]` window; the
   extractor hard-rejects any swap with `rel ≥ window_s`. Labels start strictly after. Enforced in code.
5. **Deterministic, versioned extraction.** A `FeatureSet` is a hashed config (ordered columns +
   vendored-math version). Same raw + same FeatureSet → byte-identical output. Every export carries a
   **manifest** (FeatureSet version+hash, source(s), date range, mint cohort, row count, content hash,
   label def) — re-running reproduces it exactly.

### 6.5 Feature build → CSV (one-click)
The operator picks a `FeatureSet` + cohort + label def in the UI → a Celery task runs the **shared
vendored extractor** over the lake → CSV/parquet **+ manifest** → download. Same code that serves live
(Principle #2). This is the labs' input; nothing about modeling runs on the VPS.

---

## 7. Cross-repo contracts (byte-identical or parity is a lie)

### 7.1 The normalized-swap schema (vendored math eats exactly this)
```python
NormalizedSwap = {
  "rel":   float,              # block_time − launch_timestamp (seconds since t0). ANCHOR to the DB
                              #   graduated_at/launch_timestamp on BOTH sides — not the first swap's time.
  "price": float,             # > 0 ; Birdeye per-swap price (quote-implied), identical both sides
  "side":  "buy" | "sell",
  "vol_sol": float, "vol_usd": float, "sol_usd": float,   # D1: store all three
  "owner": str | None,        # trader = the tx SIGNER (= Birdeye owner, verified). None excluded from counts
  # ordering/raw carried for the settler + reproducibility:
  "block_time": int, "slot": int, "signature": str,
  "base_reserve": int|None, "quote_reserve": int|None, "quote_mint": str,  # reserves: helius_verify path only; None for Birdeye
  "source": "birdeye_live" | "birdeye_backfill" | "helius_verify", "phase": "pre" | "post",
}
```
The vendored `compute_features` consumes the subset `{rel, price, side, vol, owner}` — feed it `vol_usd`
(or the declared unit). **Ordering key `(block_time, slot, signature)`, stable sort.**

### 7.2 The feature math — vendor, do not fork
Copy `solanabilly3/src/tape_microstructure.py` **verbatim** (the one sanctioned edit is `rels.ptp()` →
`np.ptp(rels)` for numpy 2.x). Signature: `compute_features(swaps, window_s=120, bucket_s=15) -> dict|None`
(returns None for no usable swap — record as no-feature, never a zero row). Emits the `tape_*` family
(`tape_n_trades`, `tape_n_unique_traders`, `tape_ret_total`, `tape_max_drawdown`,
`tape_logprice_slope_per_s`, `tape_close_b{0..n}`, …). Re-vendor to update; never hand-edit.

### 7.3 The feature contract / spec JSON (from the labs)
Models carry a spec (`solanabilly3/experiments/spec_*.json`): `entry_poll` (regime guard, must equal the
live scoring regime), `base_features.keep` (the allowlist), `derived_features` (ratio formulas),
`target` / `exit_policy`, `leak_audit`. **Ratio semantics are a byte-exact hazard:** `a/(b+1)` for
counts/volumes, `a/(b+1e-9)` for prices, **then** ±inf→NaN — *not* a `_safe_div(b!=0)`. The
`transform_script` ships in the artifact metadata and runs **before** the feature gate; the golden test
must validate transform **and** assembly.

### 7.4 `model_registry` write contract (mirror `promote_model.py` exactly)
solanaTrilly's `model_registry` must accept what the lab's promoter writes. For the **lightgbm_regression**
path (the current champion family):
- `model_type = "lightgbm_regression"`; `model_artifact` = **raw LightGBM text booster** (`Booster.save_model()`
  output, starts `b"tree\n"`, **no header, no gzip**); loader does `lgb.Booster(model_str=blob.decode())`.
- `extraction_config` JSONB carries `entry_poll`, `feature_cols` (**exact `booster.feature_name()` order** —
  the loader scores by column order, not names), `derived_features`, `target_type:"regression"`,
  `serving_contract` ("score→raw float; gate raw_score≥cutoff; do NOT predict_proba; preserve NaN, never
  impute"), and `threshold_calibration` (raw-score cutoffs at top-K).
- `model.feature_list == booster.feature_name()` is the binding order — the promoter refuses on mismatch.
- The polymorphic loader (`model_artifact_loader.py`) also handles AutoGluon (magic `b"AUTOGLUON\x01"`)
  and sklearn joblib — port it verbatim (§14). `trading_config_json` is **operator-POSTed**, not written
  by the promoter.

### 7.5 The settler oracle (`solanatrills/.../tape_resettle.py`) — reproduce its arithmetic
solanaTrilly's in-app settler (`paper_tape_settle.simulate_tape_exit`, §14) **must** agree with the lab's
grader by construction. Constants: `GAP=30s, LATENCY=2s, SLIP_CAP=0.15, FEE=0.01` — **for PumpSwap, set
`FEE`/`SLIP_CAP` to the AMM's 0.25% fee tier** and keep them identical cross-repo (coordinate the change).
Arithmetic: entry quote = last swap in `[entry−30, entry]`; fill = first swap at `entry+2`; enterability
guards (no pre/post → DEAD; `fill/quote−1 > 0.15` → slip-miss; **excluded, never booked 0%/−100%**); walk
post-swaps tracking peak with trigger priority **TAKE_PROFIT_PCT → DISASTER_CAP → STOP_LOSS → RUG_PULL
(armed only after +5% peak) → AUTO_SELL_TIMER**; exit fill = first swap `≥ trigger_t + 2`; impact
`cost = 2·size/(size+flow)`, `pnl = ((exit/fill)·(1−FEE)·(1−cost) − 1)·100`. Policy read from the DB
config reads TP/timer from `exit_policy` and SL/disaster/rugcut from `exit_rules` (dual-section).

### 7.6 Golden parity gates (CI, before any money)
- **G1 function parity:** a frozen fixture (≈15 tokens) → vendored `compute_features` → every feature
  within `1e-9`.
- **G2 source parity:** (a) Birdeye live-stream vs Birdeye `seek_by_time` backfill on ≈20 tokens →
  byte-identical normalized swaps + features (the live↔backfill seam that actually serves); (b) raw-truth
  spot-check — Helius PumpSwap decode on ≈20 tokens confirms Birdeye coverage/side/amount (verified 100% on 3
  mints; widen in CI).
- **Full-path scorer parity:** reproduce the lab's `golden_scores.parquet` bit-exact (0.0 abs err)
  through the production load+transform+score path, including the ratio columns.

---

## 8. Postgres schema (concrete)

Lift the basis-agnostic spine from solanaBilly verbatim; rebuild the feature tables for AMM state.

| Table | Key columns | Notes |
|---|---|---|
| `tokens` | `mint PK`, `pool_address`, `graduated_at TIMESTAMPTZ`, `graduated_block_time INT` (rel-anchor), `dex_source`, `raw_graduation JSONB`, `status` | t0 = `graduated_at` |
| `swaps` | `mint`, `block_time`, `slot`, `signature`, `side`, `price`, `vol_sol`, `vol_usd`, `sol_usd`, `owner`, `base_reserve`, `quote_reserve`, `rel` | queryable mirror of jsonl.gz; index `(mint, block_time, slot, signature)` |
| `snapshots` | `mint`, `taken_at`, `elapsed_s`, raw JSONB (holders/authority/liquidity) | one row/token (§6.3) |
| `feature_sets` | `id`, `version`, `math_version`, `columns[]`, `live_servable[]`, `hash`, `notes` | §6.4.5 |
| `model_registry` | mirror `promote_model.py` (§7.4): `id`, `model_type`, `model_artifact BYTEA`, `extraction_config JSONB`, `transform_script TEXT`, `metrics JSONB`, `is_active`, `trading_config_json JSONB`, timing slots | basis-agnostic, lift |
| `predictions` | `mint`, `config_version`, `model_id`, `score FLOAT`, `feature_vector JSONB`, `predicted_at`, `actual_outcome`, `actual_label` | `feature_vector` stored for live↔offline diffing |
| `positions` | mirror solanaBilly `positions`: `status∈(PENDING,OPEN,CLOSING,CLOSED,SELL_FAILED,PAPER)`, entry/exit price/sol/tokens, `exit_trigger VARCHAR(30)`, `peak_price`, `pnl_sol/pnl_pct`, `confidence`, heartbeat | `graduated` flag now always-true context |
| `trade_log` | `position_id?`, `action VARCHAR(30)`, `detail_json JSONB` (via `json_safe`), `tx_signature`, `error_message` | append-only event log |
| `pipeline_config` | §5.1 | source of truth |
| `pipeline_state` | singleton `id=1`: `firehose_active`, `scoring_enabled`, `trading_enabled` | explicit start; no silent auto-recovery |
| `annotations` | `mint`, `author`, `tags[]`, `note`, `created_at` | §13.3 human labels |
| `replay_runs` *(sandbox schema)* | `run_id`, `config_version`, `model_id`, `cohort`, params | §11; isolated `replay` schema |

**Replay test discipline (port):** schema-setup fixtures run `subprocess.run(["python","manage.py",
"migrate"])` against real Postgres — never in-process (Django's analogue of solanaBilly's Alembic rule),
never `create_all`. `VARCHAR(30)`/`(20)` widths constrain new trigger/action/status vocabulary.

---

## 9. Serving / scoring

Port `inference_tasks.py`'s spine (§14): the model cache + warm-load, the polymorphic `load_artifact`
dispatch, the Prediction write, and the isolated trade-chain handoff (a trading failure never affects
the committed Prediction). Keep the three refuse-to-serve guards — they are the best protection we have:
1. **Regime guard:** active model's `entry_poll` must equal the live scoring regime, else refuse.
2. **Feature-contract gate:** any contract column whose **key is absent** from the assembled
   `feature_dict` → refuse (never NaN-fill). Present-but-None passes through as real float NaN (≈half the
   features are sparse by design) — preserve, never impute.
3. **Completeness gate:** refuse if non-None coverage of a declared family < threshold.

**Lead decisions (resolving the labs' asks — this PRD has veto):**
- **D1 units:** store `vol_sol`+`vol_usd`+`sol_usd`; unit-invariant features are the parity backbone.
- **D2 the feature contract is split `live_servable` vs `training_only`.** `live_servable` = the post-grad
  Birdeye tape + the one score-time snapshot (§6.3) + **live-computable funding-graph features** (Helius
  `funded-by`, §3.6, within a score-time budget, NaN-on-timeout). `training_only` = the on-demand pre-grad pull
  **and every Dune cross-wallet aggregate lacking a live `funded-by`/recorder equivalent** (§3.5) — training-lake
  only, **never** a synchronous live-score dependency (the t2 prewarm-race #379 reincarnated). The config
  validator REJECTS any active model whose contract ⊄ `live_servable`.
- **The gate is `adaptive_topk` of a GATED universe, never a fixed score threshold** (the id22 lesson):
  apply a pre-entry activity gate first (e.g. `tape flow over [t0, score] ≥ $X` — a proxy for the lab's
  universe), then admit the top-`pct` of the trailing live score stream (Redis ZSET window, warmup +
  abs-floor guards). Port `adaptive_gate.py`. min_confidence (raw-score cutoff from
  `threshold_calibration.json`) is the fail-back, not the primary gate.
- **Live-edge is unproven until an observe/paper soak says otherwise.** The live firehose is more
  rug-dense than the offline cohort; "live-computable ≠ live-edge." Promotion is replay-gated (§11) and
  every model starts in **observe/paper** (no capital) until the realized edge is measured.

---

## 10. Trading & exit

Port the execution chain and exit engine from `trading_tasks.py` verbatim, adapting only the AMM basis
(§14). This is the densest crown jewel — every piece was paid for in live losses.

### 10.1 The buy/sell execution paths (RESOLVED — do NOT re-derive; this cost solanaBilly days)
Graduated tokens swap on **PumpSwap** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`). The full swap
account lists below are pinned from the official IDL (`pump-fun/pump-public-docs idl/pump_amm.json`),
cross-verified against three working SDKs. Getting the account order wrong is exactly what ate our time.

- **Direct reference — port from these:** `chainstacklabs/pumpfun-bonkfun-bot` →
  `learning-examples/pumpswap/manual_buy_pumpswap.py`, `manual_sell_pumpswap.py`,
  `get_pumpswap_pools.py` (pool derivation). Self-contained (`solders`/`solana`/`spl` only). **Trust the
  IDL order below over the scripts if they differ** — a 2026-04-28 program upgrade changed the layout.
- **Discriminators:** buy `66063d1201daebea`, sell `33e685a4017f83ad`.
- **Args:** buy = `base_amount_out:u64, max_quote_amount_in:u64, track_volume:OptionBool` (the 3rd arg is
  easy to miss — 1 byte); sell = `base_amount_in:u64, min_quote_amount_out:u64`.
- **Find the pool (deterministic, no indexer):** `creator = PDA(["pool-authority", base_mint],
  6EF8…pump)`; `pool = PDA(["pool", 0u16_LE, creator, base_mint, wSOL], pAMMBay…)`.
- **BUY — 23 accounts in order** (✍=writable, 🔑=signer): `1 pool✍ · 2 user✍🔑 · 3 global_config ·
  4 base_mint · 5 quote_mint(wSOL) · 6 user_base_ata✍ · 7 user_quote_ata✍ · 8 pool_base_vault✍ ·
  9 pool_quote_vault✍ · 10 protocol_fee_recipient · 11 protocol_fee_recipient_ata✍ ·
  12 base_token_program · 13 quote_token_program · 14 system_program · 15 associated_token_program ·
  16 event_authority · 17 program(self) · 18 coin_creator_vault_ata✍ · 19 coin_creator_vault_authority ·
  20 global_volume_accumulator · 21 user_volume_accumulator✍ · 22 fee_config · 23 fee_program(pfeeUxB6…)`.
- **SELL — 21 accounts:** identical `1–19`, then `20 fee_config · 21 fee_program`. **Sell OMITS the two
  volume accumulators (buy 20/21) — don't pad them.**
- **Gotchas (verify live, never hardcode):** (a) total fee is **0.25% or 0.30%** depending on the live
  creator-fee config — **read `lp/protocol/coin_creator_fee_basis_points` from GlobalConfig on-chain**;
  the fee accounts are mandatory regardless of the rate. (b) **derive `event_authority` at runtime**
  (`PDA(["__event_authority"], pAMMBay…)`), never hardcode a literal. (c) protocol fee recipients are
  mutable config — read from GlobalConfig. (d) **pre-create the user base-token ATA idempotently** (the
  buy ix does NOT create it → AccountNotInitialized / Anchor 3012); wSOL ATA: create → fund → syncNative
  → `closeAccount` to unwrap after. (e) buy fee is **on top** (`max_quote_in ≈ cp_cost/(1−fee) +
  slippage`); sell fee is **deducted** (`min_quote_out` is the post-fee floor). (f) budget rent for
  first-use program accounts (fee-recipient ATA + creator-vault ATA + user-volume-accumulator ≈ 0.0055
  SOL total) on top of the trade. This replaces the curve buy/sell ix builder in the ported chain (§14).

- **Buy:** `buy_token` → balance check → **AMM quote (PumpSwap constant-product)** → BUY_ATTEMPT →
  simulate → send via Helius Sender → confirm (`meta.err` check + `getSignatureStatuses` fallback) →
  **ghost-buy verify** (poll the ATA, `< 10%` of expected ⇒ no Position) → Position row with the *actual*
  received balance. Adaptive-gate `base_sol` bypass (sizing already decided by the gate, #388).
- **Sell:** 4 attempts; normal gaps `[0,2,8,30]`, **PANIC `[0,2,2,2]`**; per-attempt **Sender 5xx→RPC
  fallback** + **circuit breaker** (5/60s → route direct to RPC for 120s; **buys excluded** — double-submit
  opens two positions). Slippage tiers: TIGHT 800 / NORMAL 1500 / LOSS 2500 / **PANIC `[5000,7000,9000,9900]`
  per-attempt**.
- **Exit engine** (`evaluate_exit_rules`, injectable `now` for replay): priority RUG_PULL → DISASTER_CAP
  → STOP_LOSS → NEXT_POLL_GUARD → TAKE_PROFIT_PCT → AUTO_SELL_TIMER → CEILING/VOLUME_COLLAPSE/
  CONCENTRATION → TRAILING (arms only if peak>entry·1.05, not in 60s grace) → STALE. Two-speed RUG_PULL
  via a Redis price ring buffer.
- **Anchor decode** (`6002 TooMuchSolRequired`/buy, `6003 TooLittleSolReceived`/sell,
  `6023 NotEnoughTokensToSell`) never raises; 6023 storms are a benign confirmation-race (default RPC →
  Helius, #377).
- **AMM adaptation (the coordinated pass, Principle #4):** replace `_deserialize_bonding_curve` /
  `_compute_entry_price` / `_compute_tokens_out` / `_compute_sol_out` with PumpSwap pool-reserve
  constant-product math + the 0.25% fee; the curve PDA derivation → pool-account derivation; the
  graduated/Jupiter branch becomes the default route. **Keep every zero-reserve guard** (Python division
  is silent).
- **Settlement = the tape oracle** (§7.5). Paper/observe positions settle off the recorded tape, agree
  with the trills grader by construction — the sole settler when observe mode is on.

### 10.2 Paper trades & exact-exit settlement (SOLVED here — port it, don't re-struggle)
solanaBilly struggled for weeks to **close a paper trade and record at exactly what exit/price it would
have filled** — the settler was rebuilt 5× (§12 S2). It is **solved**, and the agents should take it as
direct inspiration. Observe-mode positions (`status='PAPER'`) are entry-only and never reach the live
exit monitor, so they settle off the **recorded tape** via the trusted oracle — reproducing the *exact*
exit trigger and fill the money run would have taken, by construction.
- **Port `paper_tape_settle.simulate_tape_exit`** (a verbatim port of `solanatrills/.../tape_resettle.py`
  `resettle()`): entry quote = last swap in `[entry−30, entry]`; fill = first swap at `entry+2`; walk
  post-swaps tracking peak with the exact trigger priority (TP → DISASTER_CAP → STOP_LOSS → RUG_PULL
  (armed only after +5% peak) → AUTO_SELL_TIMER); exit fill = first swap ≥ `trigger_t+2`; impact
  `2·size/(size+flow)`. Un-enterable rows (no quote/fill, or slip > 15%) are **EXCLUDED, never booked as
  0% or −100%**.
- **A settled paper position writes the SAME realized fields as a live CLOSED one** (`exit_price`,
  `exit_trigger`, `pnl_pct`, `peak_price`, `closed_at`) — so the dashboard (§13) and analytics treat
  paper and live identically, and **you SEE exactly where each paper trade exited on the candle**
  (settled == `closed_at IS NOT NULL`).
- Inspiration files (solanaBilly): `app/services/paper_tape_settle.py`, `app/tasks/paper_tape_tasks.py`.
  The tape grader is the oracle — do **not** invent a second exit engine for paper (Principle #5). Replay
  uses this same settler (§11), so paper == replay == live-minus-execution by construction.

---

## 11. Testing & replay — the promotion gate (the answer to the 40-minute loop)

**The loop we're killing:** ship a model → pipeline isn't model-agnostic → hotfix → run live → wait
≥40 min → catch the next bug → repeat. Root cause: the only place the real serving code met real data was
production. The tape is a deterministic recording — replay it through the real code (Principle #7).

### 11.1 The ladder (maps 1:1 to what triggers the loop)
| Tier | Answers | Triggered by | Speed |
|---|---|---|---|
| **T0 cross-source golden parity** | Live path == offline, byte-identical swaps + features? (= §7.6) | new model / FeatureSet | ms, CI gate |
| **T1 "will it serve?" replay** | Drop the model in the real serving path over N historical tokens — crash / refuse / NaN / insane score? | **new model** | seconds |
| **T2 full-pipeline replay ("a day in 30s")** | Replay whole days end-to-end → real Predictions/Positions/PnL via the real exit engine + tape settler | **new model, exit, config** | seconds–min |
| **T3 regression corpus** | Did this PR flip a verdict on a frozen tape-settled cohort? | **any code change** | CI gate |

T0+T1 kill the not-model-agnostic bug (every "accommodation" change is exercised offline before a live
token). T2 is the centerpiece — it writes the identical artifacts a live run would to a **replay sandbox
schema**, so the operator **views a replay run in the same dashboard as a live run** (candles, markers,
PnL). T3 is the hotfix safety net (the file+test-dropped-together failure, #404, can't hide a verdict
flip here).

### 11.2 Promotion is replay-gated
No model goes live until **T0 + T1 + T2** pass, and the PnL the labs quote and the PnL replay produces
**agree** — a gap *is* a parity/leak bug, found offline for free. Then it runs **observe/paper** (no
capital) until realized edge is measured (§9). Two consumers, one harness: the Feature Builder feeds
training; replay feeds validation.

### 11.3 Architecture (from P0) + honest limits
Thin Celery/WS adapters, **pure clock-injected core**, a `DataSource` interface (`Live`/`Replay`/backfill)
+ virtual clock, replay runs in an isolated `replay` schema. **Replay cannot reproduce execution reality**
— RPC/Sender landing, real slippage, sub-slot fills, true concurrency races (the prewarm race was a
race). So a live confirmation soak remains, but its job shrinks from "find all bugs" to "confirm execution
matches expectation." The 40-minute loop becomes a one-time confirmation, not a grind.

---

## 12. Hardening requirements from the 360-PR audit (scars → structural fixes)

| # | Scar | PRs | Fix (hard requirement) |
|---|---|---|---|
| S1 | On-chain execution edge cases — all found live | #288/#301/#303/#305/#317/#376/#377/#385 | Port the execution body verbatim (§14) + keep real-failure replay fixtures as T3 regressions |
| S2 | Settler rebuilt 5× | #390/#391/#395-#397/#400/#403/#405 | §7.5 tape oracle + §11 verify-against-reality; one settler |
| S3 | Live≠offline parity | #358/#359/#367/#379/#380 | §7 one vendored library + §7.6 golden gate + D2 (no hot-path external call) |
| S4 | Poll/regime churn | #314/#316/#402 | §5 single config; regime is one validated field |
| S5 | **CI flail — ~11 PRs on ONE Node-24 fix** | #108–#138 | **H1: pin every GitHub Action to a SHA, freeze the runner, one canonical `ci.yml` from P0.** A CI change is deliberate, never a 10-PR flail |
| S6 | **Celery task registration recurred for months** | #116/#125/#127/#128/#134 → #404 | **H2: Django app autodiscovery + a CI test asserting the live registered-task set vs a committed manifest** (fails when a task vanishes even if its test vanished too); `git show --stat HEAD` before push; never `git stash` between add and commit |
| S7 | JSONB/psycopg crashes | #331/#332/#388 | **H3: one `json_safe()` (non-finite→null, Decimal→float, datetime→iso) as a custom JSONField encoder at every write site** |
| S8 | Zero/null/NaN | #194/#359/#405/#388 | **H4: guard zero before every division; preserve real-missing as NaN, never silently impute; None→NaN policy declared per feature** |

H1–H4 + S1/S2 porting are **P0–P3 acceptance criteria**, not afterthoughts.

---

## 13. Operator dashboard — monitoring + human-in-the-loop research instrument (FOUNDATIONAL)

> **DIRECTIVE — ignore solanaBilly's UI completely.** Do not port, open, or take inspiration from it. It
> was spec'd to forbid a frontend framework (US-8: "no external frontend framework… plain HTML…
> `setInterval` polling") — static DataTables, no charts, no realtime. It is the thing being replaced.
> Build fresh. *(Operator: "the UI is terrible. I can't even see what's happening with the positions.")*

Two jobs — the second is the one solanaBilly never had:
1. **Operational monitoring** — live positions + pipeline health at a glance.
2. **A research instrument** — surface data *visually* so the operator's eyes find patterns a model
   can't, then feed them back to the labs as **human-labeled signal**. *(Operator: "guide the modeling
   agents with visualizations only I can see and a machine can't.")*

### 13.1 Stack
React + Vite served by DRF + **Django Channels** for realtime (not server-rendered tables).
**TradingView Lightweight Charts** (MIT) for every candle/marker. Keep it lean.

### 13.2 Views
1. **Live Positions board** *(the #1 ask)* — one card per open position: live tape-derived candle with
   entry/peak/current markers and **every armed exit threshold drawn as a line + a countdown to whichever
   fires next**; live PnL (gross + after-friction); time-in-trade; opening score; the position's tape
   ticker. Watch a position breathe and know *why* it will exit before it does.
2. **Token detail / research** — full tape candle (1s/5s/15s/1m) + buy/sell-pressure & net-flow overlay,
   top-buyer concentration over time, holder churn, **the exact feature vector the model saw + the
   score**, predicted-vs-actual band, t0/score/entry/exit markers.
3. **Cohort small-multiples — the pattern-mining wall** — a grid of mini candle sparklines, **groupable/
   sortable by outcome, score band, exit trigger, depth bucket, time-of-day**. Where the eye spots "the
   winners share this shape / the rugs share this pre-entry tape." A machine sees rows; the operator sees
   shapes.
4. **Calibration & PnL analytics** — win-rate by score band, realized PnL by exit trigger, score-vs-actual
   scatter, calibration curve. *(The t2 inversion — high confidence = worst outcomes — would have been
   obvious here in seconds instead of after an 11-hour bleed.)*
5. **Replay viewer** — render any replay `run_id` identically to live; scrub a historical day, watch
   replayed positions open/close on candles. Validate a model/exit/config visually before going live.
6. **Config & model control** — the §5 admin surface in an operator skin: view/diff/activate with audit
   history; operator-only actions gated + confirmed.
7. **Feature Builder** — the §6.5 one-click export.

### 13.3 Human annotation → labeled export (the killer feature)
The operator tags any token/position (free-text + categorical: "classic rug shape", "slow bleed", "clean
ignition", "fakeout pop", "organic") on a panel beside the chart. Annotations store on `mint` and **export
to the labs as a labeled dataset** — turning visual pattern-recognition into a feature/label source the
machine cannot self-generate. *That* is "visualizations only I can see and a machine can't," made
actionable: spot a shape → tag it → it's a column in the next export.

### 13.4 One feed (the tape)
Channels pushes tape/candle/position deltas; charts update tick-by-tick from the tape. The same tape the
recorder writes and the settler reads drives the live chart — one source, no separate price feed to drift
(solanaBilly's "Position has NO price feed" came from a heartbeat/feed split). **This is foundational, not
deferred:** Live Positions + candles + the cohort wall land at P6.

---

## 14. Port manifest (exact lift / adapt / rebuild)

The labs/solanaBilly have done the heavy lifting. **Single most important note:** the price basis flows
through `tape_recorder` → `paper_tape_settle` / `rug_tape_signals` / `tape_microstructure` →
`trading_tasks` → the trills grader. Change it to the **PumpSwap reserve basis in ONE coordinated pass**
across all of them, or the agree-by-construction settler guarantee is lost.

| Source (solanaBilly unless noted) | Verdict | What changes for solanaTrilly |
|---|---|---|
| `app/services/tape_recorder.py` | **ADAPT** | Keep queue/writer/window/coverage verbatim. Replace discriminator + Borsh decode with PumpSwap `BuyEvent`/`SellEvent`; reserves → pool reserves; units → store all three (D1) |
| `app/services/helius_listener.py` | **ADAPT** | Keep threading/backoff/dedup/reconciler/cmd-queue. Repurpose for the **Helius `migrate` reconciler** (detection backstop) + the G2 raw-truth verification path; `maxSupportedTransactionVersion:0`. The operational tape is the Birdeye stream (§3.3), NOT this listener. |
| `app/services/paper_tape_settle.py` | **ADAPT (math verbatim)** | `price` = AMM reserve ratio; `FEE`/`SLIP_CAP` → PumpSwap 0.25%; keep ordering + truncation + zero-guard + the trigger-priority arithmetic; stay identical to trills grader |
| `app/services/model_artifact_loader.py` | **LIFT VERBATIM** | None — basis-agnostic (sklearn/AutoGluon/lightgbm_regression) |
| `app/services/tape_microstructure.py` | **LIFT VERBATIM (vendored)** | Re-vendor from `solanabilly3/src/`; do not hand-edit |
| `app/services/tape_features.py`, `rug_tape_signals.py`, `adaptive_gate.py` | **ADAPT/LIFT** | reader basis → AMM; signal/gate math verbatim |
| `app/tasks/trading_tasks.py` (exit engine, buy/sell, ghost-buy, Sender 5xx/breaker, slippage tiers, Anchor decode, `_json_safe`, rug ring buffer) | **ADAPT (heavy)** | Lift all logic; replace the 4 price/curve functions with PumpSwap constant-product + 0.25% fee + pool derivation; keep every zero-guard |
| `app/tasks/inference_tasks.py` (model cache, warm-load, 3 refuse-to-serve guards, None→NaN, Prediction write, isolated trade handoff) | **ADAPT** | Keep guards verbatim; feature assembly → tape/AMM features; regime → solanaTrilly's scoring regime |
| `app/models/*` core (`model_registry`, `predictions`, `positions`, `trade_log`, `pipeline_state`) | **LIFT** | Basis-agnostic spine; rebuild feature tables for AMM |
| `solanabilly3/scripts/promote_model.py` write contract | **MIRROR** | solanaTrilly's `model_registry` accepts it unchanged (§7.4) |
| **solanaBilly's Flask/DataTables UI** | **IGNORE** | Build §13 fresh |

---

## 15. Deployment, VPS ownership & the live-firehose budget

### 15.1 Service topology
| Container | Role |
|---|---|
| `web` | Django ASGI (Daphne/Uvicorn) — DRF API, admin, Channels |
| `listener` | Birdeye `SUBSCRIBE_MEME` detection + Birdeye `SUBSCRIBE_TXS` swap-tape recorder + Helius `migrate` reconciler (dedicated) |
| `celery` | Scoring, feature builds, replay, trading |
| `celery-beat` | tape→parquet promotion, outcome backfill (tape-derived), reconciler sweep |
| `db` / `redis` | Postgres 16 / Celery broker + rate-limit bucket + Channels layer |
| `frontend` (dev) | Vite dev server; built static assets served by `web` in prod |

Prefer config-in-DB (§5) over env-in-compose so deploys don't depend on a hand-edited prod compose
(a solanaBilly drift source).

### 15.2 The agents OWN the VPS end-to-end (Definition of Done = live on the VPS, not green locally)
All Claude instances can SSH to the VPS (`ssh root@140.82.43.36`) and have full GitHub access. **Use it.**
- **DoD for every phase = merged + deployed to the solanaTrilly staging stack on the VPS + smoke-tested
  there.** "Works locally" is NOT done. The operator must never return to find deployment waiting.
- The agents set up everything: the repo/registry pull on the VPS, Docker + compose, the database,
  migrations, env wiring (referencing operator-provisioned secrets, §15.6), the CD automation, and they
  keep repo↔VPS in sync **by construction** (§15.4), never by hand.

### 15.3 CRITICAL — isolate from the LIVE solanaBilly stack (never disturb prod)
solanaBilly runs LIVE on this VPS for the entire build. A new repo + Docker on the same box can collide
with and kill it. **Hard isolation, enforced — this is a safety gate, not a preference:**
- Distinct compose project (`-p solanatrilly`), distinct container names, distinct host ports (e.g.
  `web` on **8002**, not solanaBilly's 8001), distinct Postgres **database + volume**, distinct Redis,
  distinct Docker network.
- **NEVER** run a docker command that can touch solanaBilly's containers (`web`,`celery`,`celery-beat`,
  `listener`,`db`,`redis`) or volumes — no unscoped `down` / `up --force-recreate` / `prune -a` /
  volume removal. **Scope every docker command with `-p solanatrilly`.** solanaBilly is read-only.
- *(Carries the standing rules: the VPS pipeline is live; no ad-hoc prod scripts; an accidental wipe is
  the worst case; prefer scoped, reversible commands.)*

### 15.4 The deploy pipeline — automate local→GH→VPS (don't abandon the flow; fix what hurt)
The flow is fine; solanaBilly's pain was that it was **manual and drift-prone** (hand-maintained prod
compose, manual `pull && up`, env drift). Make GitHub the deploy engine:
- **GitHub Actions CD:** merge to `main` → tests (H1 pinned CI) → build image → push to **GHCR** →
  deploy to the VPS staging stack (SSH deploy step or a tiny puller on the box). The image is the
  artifact; the VPS pulls a *tested* image, never builds on the box.
- **The repo's `docker-compose.staging.yml` is the ONLY compose.** The VPS runs exactly what's in the
  repo — no hand-edits on the box (this kills the #1 solanaBilly drift class).
- **Config-in-DB (§5)** means compose/env carry almost nothing tunable → almost nothing to drift.
- Net: "GitOps-lite" — `main` is the source of truth, CD reconciles the VPS to it.

### 15.5 Phased VPS presence — deploy from P0, not at the end
The biggest VPS lesson from solanaBilly: deferring deployment made sync a late crisis. So **P0 deploys a
hello-world Django to the VPS staging stack through the full CD pipeline** — the VPS/secrets/Docker/sync
headache is solved on day one when it's cheap. Every subsequent phase deploys + smoke-tests on the VPS;
the pipeline grows live.

### 15.6 Can the operator be hands-off? Almost — three retained levers
Agents do ALL infra. The operator retains exactly three operator-only actions (standing rules):
1. **Provision the production trading-wallet secret** once (the real-SOL key) — agents never hold or
   invent it; until then trading runs **observe/paper with no capital**.
2. **Start the firehose** for production.
3. **Enable real-capital trading.**
Everything up to a fully-deployed, observe-mode, paper-trading, dashboard-visible solanaTrilly on the VPS
should be waiting with nothing for the operator to do but watch.

### 15.7 The live-firehose activation budget (dev/test — don't fly blind, but be strategic)
Dev needs to see the real firehoses, but live WS costs credits and casual auto-start is forbidden. So a
**hard budget, shared across all roles (dev-team / product-owner / tester) for the whole project:**
- **10 Birdeye activations + 10 Helius activations, total, project-wide** (test both firehoses). API
  keys are in `.env`.
- Each activation is **deliberate, time-boxed (≤ 30 min [TO TUNE]), and logged in a committed ledger**
  `ops/firehose_activation_log.md` (date · role/agent · which WS · purpose · duration · **count
  remaining** · what was captured). PR-reviewed, so the count is always visible.
- **Every activation MUST bank durable fixtures** to the lake / golden set (a tape sample, a detection
  sample, golden vectors). The spend compounds into the replay corpus (§11) — after which all further
  dev replays offline and never needs to go live again for that scenario.
- This is the practical arm of replay-first: **the firehose is the source of fixtures, not the dev
  loop.** Spend a live window to *bank reality*, then build against it forever.

---

## 16. Build phases (each independently verifiable OFFLINE — the overnight plan)

> **DoD per phase = merged + deployed to the VPS solanatrilly staging stack + smoke-tested there**
> (§15.2), under hard isolation from live solanaBilly (§15.3). The offline gate below is the *logic*
> bar; "deployed + smoke-tested on the VPS" is the *done* bar. VPS presence starts at P0, not the end.

| Phase | Deliverable | Offline gate |
|---|---|---|
| **P0** | Scaffold: Django+DRF+Channels+Celery+Docker; **`DataSource`+virtual clock+thin-adapter ground rule**; **H1 pinned CI + H2 task-manifest test + H3 `json_safe` encoder**; **GitHub Actions CD → GHCR → VPS staging (isolated `-p solanatrilly`); hello-world deployed live (§15.4–15.5)**; `ops/firehose_activation_log.md` seeded (§15.7) | `migrate` runs; task-manifest test fails when a task is removed; a no-op `Replay` source resolves; **CD deploys hello-world to the VPS and the smoke test hits it** |
| **P1** | Config core (§5): `PipelineConfig`+Pydantic schema+resolver+admin+audit; invariants enforced | save/validate/activate; an invalid config is rejected in tests |
| **P2** | Detection (§6.1): Birdeye `SUBSCRIBE_MEME` → `tokens`; Helius `migrate` reconciler; dedupe | replay a captured MEME stream → expected token rows |
| **P3** | Tape recorder (§6.2): Birdeye swap stream + `seek_by_time` reconcile → normalized swaps → jsonl.gz+`swaps`; idle-kill re-attach | live↔backfill byte-parity on golden tokens; ordering + truncated-tail + zero-guard tests green |
| **P4** | Score-time snapshot (§6.3) + units locked (D1) | snapshot stored raw; unit-parity test green |
| **P5** | Lake + extraction contract (§6.4) + vendored math (§7.2) + Feature Builder (§6.5) + **T0/G1/G2 golden parity** | export a CSV+manifest; **golden parity is a merge gate** |
| **P6** | Dashboard (§13): Live Positions + tape candles + cohort wall + token detail | operator sees real candles for a replayed token |
| **P7** | Scoring (§9): loader + 3 guards + adaptive-topk-of-gated-universe + Prediction write | T1 will-it-serve replay green on a promoted model |
| **P8** | Replay harness (§11) + trading/exit (§10) + tape settler, **behind the replay gate**; observe/paper mode | T2 full-day replay → sandbox Positions render in the dashboard; T3 wired to CI; promotion blocked until T0+T1+T2 pass |
| **Cutover** | When solanaTrilly is proven in observe/paper on the VPS: operator provisions the trading-wallet secret, stops solanaBilly, promotes solanatrilly staging→prod, flips firehose + capital (§15.6) | solanaBilly decommissioned; solanaTrilly is the one live pipeline |

---

## 17. Operator tuning knobs (defaults are starting guesses; all are UI edits per §5)

| Knob | Default | Reasoning |
|---|---|---|
| Detection | Birdeye `SUBSCRIBE_MEME` `source=pump_dot_fun` + Helius `migrate` reconciler | Emits mint directly; reconciler closes WS-drop gaps |
| Scheduled polling | **NONE** — one snapshot at score time | Tape carries all high-res signal |
| `score_at_elapsed_s` | 120 s (t+2min post-grad) | Early enough to catch the move; tape shows if buying sustains. **Biggest unknown — tune from data** |
| `window_s` / `capture_buffer_s` | 120 s / 4 s | Vendored math default; buffer so the tail lands before scoring |
| Outcome window | 1800 s (30 min); recorder TTL ≥ this | Pump-time horizon; never truncate labels |
| Universe gate | tape flow over `[t0, score] ≥ $500` | Proxy for the lab's offline universe; rank top-K of *gated* (id22 lesson) |
| AMM | PumpSwap (Raydium legacy only) | Sole graduation destination since 2025-03 |
| Units | store `vol_sol`+`vol_usd`+`sol_usd` | Parity backbone (D1) |
| Trading | **observe/paper, capital OFF** | Live-edge unproven until soak; firehose operator-only |

---

## 18. Changelog
- **v1.3 (2026-06-14)** — Added a top-of-doc **Django-is-new-to-this-environment** warning (suspect deps/
  image/VPS before code; recurs all project). Added **§10.1 resolved PumpSwap buy/sell paths** (full
  23-account buy / 21-account sell IDL order, discriminators, args, pool derivation, fee/ATA/rent gotchas;
  direct reference = `chainstacklabs/pumpfun-bonkfun-bot` `learning-examples/pumpswap/`). Added **§10.2
  paper-trade exact-exit settlement** (the solved tape-oracle settler; port `paper_tape_settle`; paper
  writes the same realized fields as live so the exit is visible on the candle). Removed the confusing
  "firehose = hard rule / never auto-starts" framing (agents may start it; the rule is only *no silent
  auto-start*) in §5.3, §15.6, and the schema table.
- **v1.2 (2026-06-14)** — Rewrote §15 into **Deployment, VPS ownership & the live-firehose budget**:
  agents own the VPS end-to-end (DoD = deployed + smoke-tested on the VPS, not green locally); **hard
  isolation from the LIVE solanaBilly stack** (`-p solanatrilly`, distinct ports/db/volumes, never
  unscoped docker); **automated CD** (GH Actions → GHCR → VPS, repo compose is the only compose,
  config-in-DB kills drift); **VPS presence from P0** (hello-world deployed via CD on day one); the
  operator's **three retained levers** (trading-wallet secret, firehose on, capital on); and the
  **firehose activation budget** (10 Birdeye + 10 Helius project-wide, ledgered, must bank replay
  fixtures). Wired into §16 (P0 + per-phase VPS DoD + a Cutover row).
- **v1.1 (2026-06-14)** — Data-source correction after the solanatrills parity probe + funding-graph prototype.
  **The swap tape is Birdeye on BOTH sides** (live + backfill), not a Helius-firehose reserve-implied decode:
  Birdeye verified 100%-faithful to raw chain, the training lake is Birdeye, and the Birdeye credit allowance ≫
  Helius — so one source = parity by construction (§3.3, §6.2, Principle #4). **Trader = the tx signer**
  (Birdeye `owner` verified = signer; replaces the old `postTokenBalances.owner` / never-`accountKeys[0]` rule,
  which would attribute a different wallet than the trained lake — a parity break; §3.3, §7.1). **Helius
  repositioned** to: the G2 raw-truth verification oracle, the `migrate` detection reconciler, and the live
  `funded-by` funding-graph primitive (§3.6). **Dune added (§3.5) as OFFLINE-only** (native graduation seed +
  causal pedigree/dumper/smart-money aggregates), with the hard rule that any Dune feature needs a live
  equivalent or is `training_only` (§9 D2). Funding-graph features = CANDIDATE/unproven (first depth-2 prototype
  did not separate winners/rugs on graduated tokens).
- **v1.0 (2026-06-14)** — Definitive consolidated rewrite from a full audit (solanaBilly code + 360 PRs,
  solanabilly3, solanatrills, live API research). Resolved all open unknowns: PumpSwap (not Raydium)
  graduation + program IDs/discriminators (§3), Birdeye `SUBSCRIBE_MEME` detection, firehose decode +
  trader extraction, the three-repo contract set (§7), the file-by-file port manifest with the
  price-basis coordinated-pass warning (§14). Folded in v0.2–v0.4 decisions (retire solanaBilly,
  tape-first/no-polling, D1–D4, §11 replay strategy, §12 hardening, §13 dashboard). Superseded the
  patchwork drafts entirely.
- *(v0.1–v0.4 history collapsed into this consolidated spec.)*
