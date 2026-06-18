<!--
file: scrum-master.md
purpose: Current sprint status board + controlled vocabulary for solanaTrilly
owner: product-owner
last-updated: 2026-06-18 (sprint-12 PLANNED — Copy-Trade epic opened; issues #243–#248 created)
-->

# solanaTrilly — Scrum Master Board

> 🆕 **MANDATORY NEW FEATURE — PLAN THIS NEXT (after sprint-11): the Copy-Trade Dashboard (v1).**
> Full build spec + consumption contract: [`solanatrilly_copytrade_SPEC.md`](solanatrilly_copytrade_SPEC.md)
> (operator-provided, 2026-06-17). **PO: this is committed scope — do NOT declare the project complete
> until it is built.** Plan it as the next epic once sprint-11 (the dashboard closeout) finishes.
> It is a **first-class trading pipeline, a SIBLING to the prediction (model) pipeline** — same buy/sell
> execution path, same **paper(observe)↔live** toggle (observe is the default; live is a deliberate
> operator switch after the soak), same config-driven control, same click-to-download export — but driven
> by a **JSON list of ~10 wallets** instead of a model.
> Non-negotiables from the spec (read the doc for the full contract): **(1)** a **separate
> `copytrade_engine` service/worker** with its **own Helius subscription** (subscribes to WALLETS, not the
> token firehose), **own `copytrade.*` config namespace**, and **own `copytrade_`-prefixed DB tables** —
> it must NOT clash with or share mutable state with the existing firehose/model pipeline (§5 isolation);
> both pipelines run concurrently, each with its own ON/OFF, positions, PnL, and limits. **(2)** Copy a
> watched wallet ONLY when it **BUYS a pump.fun token still on the bonding curve (PRE-graduation)** —
> first-buy-only, dedupe-token-across-wallets; **never mirror their sells** (we exit on OUR configurable
> **TP / SL / curve-completion / max-hold** rules; tight SL is deliberate). **(3)** A new **"Copy Trade"
> dashboard tab**: JSON upload, ON/OFF, Observe/Live mode, SOL size + TP%/SL% overrides, and the key view —
> a **per-wallet PnL table**. **(4) Fresh-start on every upload**: a new cohort JSON wipes the old cohort
> entirely (close/settle open positions, purge old `copytrade_*` records); exactly one active cohort, no
> cross-cohort history in v1. **(5) Observe(paper) is the DEFAULT and the safety gate** — live trading is
> NOT enabled until the operator flips it after the observe soak (offline edges have repeatedly failed live
> in this project). Honor the explicit v1 NON-goals in §1 (no per-wallet manual controls, no candle charts,
> no history) — do not add scope. Config-driven throughout, like the rest of trilly.

> 🔮 **[`oracle-direction.md`](oracle-direction.md) is the ACTIVE road to the endgame** (promote
> `trilly_pregrad_v3_2` + start the firehose) — **sprint-8 is planned directly from it.** Key points it
> proves: (1) the model we promote scores **pre-graduation** behavior, so we need a **Helius program-wide
> birth-tape source** (per-mint Birdeye structurally cannot capture birth) — **sprint-8 US-34**; (2) the
> dead-token unsubscribe is already built (`core/tape/idle_kill.py`) but needs a **two-tier idle TTL** (a
> flat 5-min kill violates the `idle_kill_ttl_s ≥ outcome.window_s` D4 invariant) — **sprint-8 US-35**;
> (3) v3.2 is a **15-booster rank-blend**, not the single ONNX — the serving path + `model_registry` write
> contract must support it — **now PLANNED as sprint-9 (P7), see [`sprint9.json`](sprint9.json)**. All
> config-driven; the anti-drift parity gate (US-32) extends to the new source (**sprint-8 US-36**) and now
> to the scorer (**sprint-9 US-44 — "scores in sync"**). With **P7 closed** (the scorer/serving path is
> proven `live==offline`), the road to the endgame is now **operator-driven** (the soak + promote acts are
> not sprint stories) — so **sprint-10 OPENS P6, the FOUNDATIONAL research-first dashboard (PRD §13)**, the
> last unbuilt PRD pillar, and closes the two agent-actionable sprint-9 carries (**I1** clean US-43
> redeploy, **I4** the AI-agent ruff gate). See **[`sprint10.json`](sprint10.json)**.

## Sprint-12 — OPEN the Copy-Trade epic (the MANDATORY operator-committed next epic, `solanatrilly_copytrade_SPEC.md`): deliver the Copy-Trade pipeline END-TO-END THROUGH OBSERVE (paper) mode — PLANNING
- **Phase:** planning
- **Sprint plan (source of truth):** [`sprint12.json`](sprint12.json)
- **Why this sprint:** Sprint-11 closed the P6 dashboard VPS deploy gap and delivered **all three PRD pillars DoD-done at the dashboard layer** — but the project is **NOT complete**: the **Copy-Trade Dashboard v1** ([`solanatrilly_copytrade_SPEC.md`](solanatrilly_copytrade_SPEC.md), operator-provided 2026-06-17) is **committed scope and entirely unbuilt** (zero `copytrade` code in the repo). It is a **first-class trading pipeline, a SIBLING to the prediction/model pipeline**, driven by a JSON list of ~10 wallets instead of a model. This sprint OPENS the epic and delivers it **end-to-end through OBSERVE (paper) mode** — the SPEC's default and its validation arbiter.
- **Sprint goal:** Build the SPEC's non-negotiables, **observe-complete**: a **§5-ISOLATED `copytrade_engine`** with its OWN `copytrade.*` config namespace, OWN `copytrade_`-prefixed tables, and OWN Helius **wallet**-subscription (not the token firehose), sharing **no mutable state** with the firehose/model pipeline (**US-58/US-59**); the correctness-critical **copy-BUY trigger** — copy ONLY when a watched wallet BUYS a pump.fun token **still on the bonding curve (PRE-graduation)**, first-buy-only, dedupe-across-wallets, never mirror their sells (**US-60**); the **observe-mode position lifecycle** with OUR TP/SL/curve-completion/max-hold exits (**US-61**); the **cohort fresh-start** lifecycle (new JSON wipes the old cohort entirely; exactly one active cohort, no history) + the engine ON/OFF gating only this engine (**US-62**); and the new **"Copy Trade" tab** (JSON upload, ON/OFF, Observe/Live, SOL size + TP%/SL% overrides, the **key per-wallet PnL table**) + export including `copytrade_positions` (**US-63**). **CRITICAL scoping decision (verified against the codebase, NOT in the SPEC):** the SPEC §0 assumes an existing buy/sell apparatus — **there is none** (no PumpSwap execution path exists; the P8 trading path §10 is still deferred). So **LIVE (real-order) copy-trade execution is OUT OF SCOPE this sprint** — it depends on the P8 execution path and the operator's post-soak Live flip; the Live toggle is surfaced but **inert/guarded**. **Firehose: OFFLINE/replay-driven by construction — zero activation (8 Birdeye + 8 Helius remain banked);** the LIVE Helius wallet-subscription's ledger is planned before LIVE is built. Honor the §1 NON-goals (no per-wallet manual controls, no cross-cohort history, no candle charts/TA, no funder logic/auto-retune/per-position manual exits) — do **not** add scope.

## Stories (sprint-12 — committed scope)
| ID | Title | Priority | Deps | ACs | Status |
|----|-------|----------|------|-----|--------|
| US-58 | Copy-trade FOUNDATION: §5-isolated `copytrade.*` config namespace + the four `copytrade_`-prefixed tables + the cohort-JSON (`leaderboard.json`) validator | high | US-9, US-10 | 3 | todo |
| US-59 | The isolated `copytrade_engine` worker + Helius **wallet**-subscription seam (in both compose files, DataSource-seam, §5-isolated, VPS-deployed) | high | US-58, US-1, US-2 | 3 | todo |
| US-60 | The correctness-critical copy-BUY trigger: PRE-graduation pump.fun curve buy only, first-buy-only, dedupe-across-wallets, never sells — offline replay-gated | high | US-58 | 3 | todo |
| US-61 | Observe-mode position lifecycle + OUR exits (TP/SL/CURVE/TIMER); observe-default safety gate; LIVE real-order execution out of scope (P8-gated) | high | US-60 | 3 | todo |
| US-62 | Cohort FRESH-START lifecycle (new JSON wipes old cohort entirely) + engine ON/OFF gating only this engine | medium | US-58, US-61 | 3 | todo |
| US-63 | The "Copy Trade" dashboard tab (per-wallet PnL table + open positions + trades + summary) + export incl. `copytrade_positions` — VPS-deployed | medium | US-58, US-61, US-62, US-48, US-31 | 3 | todo |

> **Scope:** sprint-12 commits **6 stories / 18 ACs** — the observe-complete foundation of the Copy-Trade epic. Source of truth: [`sprint12.json`](sprint12.json). **Firehose: offline/replay-driven by construction — zero activation (8 Birdeye + 8 Helius remain banked).** Matches the established 6-story / 18-AC cadence (deliberately NOT over-committed — the retrospectives repeatedly warn against over-loading heavy phases). **Deferred to the `forward_plan`:** copy-trade **LIVE execution** (requires building the P8 PumpSwap execution path §10, shared with the prediction pipeline, then wiring Live mode to it + planning the LIVE Helius activation ledger); the operator-driven prediction **soak (I2/K2)** + **endgame (I3/K3)**; the P8-dependent prediction dashboard views (Live Positions, Calibration & PnL, Replay overlay); the async-safety guard (K4); and the explicit SPEC §1 v2 NON-goals.

**Build order:** **US-58** (the §5-isolated config namespace + tables + validator) is the foundation and runs **first**. After it, **US-59** (engine/isolation) and **US-60** (trigger logic) are independent and may run **in parallel**. **US-61** (observe lifecycle/exits) depends on US-60. **US-62** (fresh-start + ON/OFF) depends on US-58/US-61. **US-63** (tab + export) depends on US-58/US-61/US-62 + the US-48 frontend + the US-31 export channel.

> **GitHub Issues:** created at sprint-12 planning (2026-06-18), one per story, mirroring the prior convention —
> [US-58 #243](https://github.com/AsimQuick/solanaTrilly/issues/243) ·
> [US-59 #244](https://github.com/AsimQuick/solanaTrilly/issues/244) ·
> [US-60 #245](https://github.com/AsimQuick/solanaTrilly/issues/245) ·
> [US-61 #246](https://github.com/AsimQuick/solanaTrilly/issues/246) ·
> [US-62 #247](https://github.com/AsimQuick/solanaTrilly/issues/247) ·
> [US-63 #248](https://github.com/AsimQuick/solanaTrilly/issues/248).
> Source of truth remains [`sprint12.json`](sprint12.json).

---

## Sprint-11 — CLOSE the P6 dashboard VPS deploy gap (J1/J2/J4 → the dashboard chain DoD-done, J3) + add the two NON-P8-gated dashboard views (Config/model control skin §13.2#6, Feature Builder UI §13.2#7) — REVIEW COMPLETE (all 6 stories Tester-pass; P6 dashboard chain VPS-CONFIRMED; third PRD pillar DoD DONE)
- **Phase:** done
- **Sprint plan (source of truth):** [`sprint11.json`](sprint11.json)
- **Why this sprint:** Sprint-10 **OPENED P6 at code level** — all 6 stories / 18 ACs landed CI-green and the P6 offline gate is structurally met — **but the VPS deploy DoD ("live on the VPS", PRD §15.2/§16) is UNMET for 5 of 6 stories**, blocked by **deploy-LAYER defects requiring no application-code change**: **J1** a one-line RFC-6455-invalid hardcoded `Sec-WebSocket-Key` in `deploy.yml`'s WS smoke-test (→ Daphne HTTP 400 → fails AC-48.3 → transitively blocks US-49/50/51); **J2** an **independent**, un-root-caused US-47 deploy failure (run 27683660493); **J4** a per-merge deploy regression (run 27681451880) that failed *after* US-46's deliberate HEAD run (27680808876) was green — three distinct deploy failures co-occurred, only one fully diagnosed, so the deploy path needs **one consolidated holistic pass + a regression guard**, not three point-fixes (the multi-sprint P0 deploy-drag lesson). The product is **not** feature-complete: the third PRD pillar (research-first dashboard, §13) is opened in code but its phase DoD ("live on the VPS, not green locally") is unmet.
- **Sprint goal:** **Close the P6 dashboard VPS deploy gap and land the full dashboard chain LIVE on the VPS**, then extend the dashboard with the two views that are **NOT** gated on P8 trading data and only lack a UI surface over **existing** backends. In build order: **(US-52 / J1)** fix the WS smoke-test key + **harden the structural test to validate the key's runtime RFC-6455 validity** (close the B3/C1 "green test ≠ live runtime" recurrence), re-deploy, Tester-confirm AC-48.3; **(US-53 / J2)** root-cause + fix the independent US-47 deploy failure (D5 "go to the box"), re-deploy, confirm VPS; **(US-54 / J4)** one consolidated deploy-path pass reconciling all three failure modes + a regression guard pinning the load-bearing deploy invariants (`--remove-orphans`, the AC-39.2 promoter ordering, the AC-12.3 retry, the US-40 unified `workflow_call` gate, the valid WS key); **(US-55 / J3)** the dashboard-chain **closeout** — one green HEAD deploy carrying US-48/49/50/51 to the VPS + Tester VPS-confirmation, declaring the **P6 offline gate VPS-CONFIRMED** and the **third PRD pillar DoD-done**; **(US-56)** the **Config & model control operator skin** (§13.2#6) over the §5 admin / US-9 PipelineConfig + US-42 ModelRegistry (view/diff/activate, audited, operator-gated, no silent auto-start); **(US-57)** the **Feature Builder UI** (§13.2#7) over the §6.5 one-click labeled export that **already exists from US-31** (only the UI is missing). **Firehose:** OFFLINE/replay-driven by construction — the deploy fixes are CI/VPS infra and both new views surface existing offline backends — **zero** activations (8 Birdeye + 8 Helius remain banked). **Deferred to the `forward_plan`:** the operator-driven **soak (I2, now unblocked)** + **endgame (I3)**; the **P8 trading-execution path** (§10); and the **P8-dependent** dashboard views (**Live Positions** §13.2#1, **Calibration & PnL** §13.2#4, the **Replay viewer** position overlay §13.2#5) — all gated on P8 position/PnL rows that do not exist until the trading engine lands.

## Stories (sprint-11 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-52 | J1 — fix the AC-48.3 WS smoke-test key in `deploy.yml` (valid 16-byte `Sec-WebSocket-Key`) + harden the structural test; re-deploy + confirm AC-48.3 (unblocks US-48/49/50/51) | high | US-48 | 3 | done | done | **pass** |
| US-53 | J2 — root-cause + fix the independent US-47 per-story deploy failure (run 27683660493); re-deploy + confirm VPS | high | US-47 | 3 | done | done | **pass** |
| US-54 | J4 — consolidate the co-occurring deploy-pipeline regressions into ONE holistic pass + a regression guard pinning the load-bearing deploy invariants | high | US-52, US-53 | 3 | done | done | **pass** |
| US-55 | J3 — the P6 dashboard-chain CLOSEOUT: one green HEAD deploy + Tester VPS-confirm of US-48/49/50/51 → P6 offline gate VPS-CONFIRMED, third pillar DoD-done | high | US-52, US-53, US-54 | 3 | done | done | **pass** |
| US-56 | P6 — Config & model control operator skin (§13.2#6): view/diff/activate over US-9 config + US-42 registry (audited, operator-gated, no silent auto-start) | medium | US-48, US-9, US-42 | 3 | done | done | **pass** |
| US-57 | P6 — Feature Builder UI (§13.2#7): a UI surface over the §6.5 one-click labeled export that already exists from US-31 (off the celery container, #289) | medium | US-48, US-31 | 3 | done | done | **pass** |

> **Scope:** sprint-11 commits **6 stories / 18 ACs** — the P6 dashboard VPS deploy-gap closeout (US-52…US-55, retrospective J1–J4) + the two non-P8-gated dashboard views (US-56/US-57). Source of truth: [`sprint11.json`](sprint11.json). **Firehose: offline/replay-driven by construction — zero activation (8 Birdeye + 8 Helius remain banked).** Matches the established 6-story / 18-AC cadence (deliberately NOT over-committed). **On completion, all THREE PRD pillars are delivered AND DoD-done at the dashboard layer;** the remaining whole-project DoD is the operator-driven soak/endgame (I2/I3) + the P8 trading-execution path.

**Build order:** **US-52** (J1) and **US-53** (J2) are independent deploy-layer fixes — run **first / in parallel**. **US-54** (J4) depends on both (it reconciles their root causes + the per-merge regression into one system fix). **US-55** (J3 closeout) depends on US-52/53/54. **US-56** and **US-57** are net-new UI surfaces over existing backends, depend on US-48's foundation, and may run **in parallel** once the deploy path is sound (after US-54).

> **GitHub Issues:** created at sprint-11 planning (2026-06-17), one per story, mirroring the prior convention —
> [US-52 #218](https://github.com/AsimQuick/solanaTrilly/issues/218) ·
> [US-53 #219](https://github.com/AsimQuick/solanaTrilly/issues/219) ·
> [US-54 #220](https://github.com/AsimQuick/solanaTrilly/issues/220) ·
> [US-55 #221](https://github.com/AsimQuick/solanaTrilly/issues/221) ·
> [US-56 #222](https://github.com/AsimQuick/solanaTrilly/issues/222) ·
> [US-57 #223](https://github.com/AsimQuick/solanaTrilly/issues/223).
> Source of truth remains [`sprint11.json`](sprint11.json).

## Sprint-11 Review — Summary (2026-06-18)
**Phase:** done | **Committed scope:** US-52…US-57 (6 stories, 18 ACs) | **Goal:** **fully met — the P6 dashboard VPS deploy gap is CLOSED at root (J1/J2/J4), the full dashboard chain is LIVE and Tester VPS-CONFIRMED, the P6 offline gate is VPS-CONFIRMED, the third PRD pillar (research-first dashboard, §13) phase DoD is DONE, and the two non-P8-gated operator views (Config/model control skin, Feature Builder UI) shipped live**
**Full retrospective + action items (K1–K5):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-PASS** across 19 PRs (#224–#242). The multi-failure deploy gap that blocked 5 of 6 sprint-10 stories is closed **at root, as a system**: **J1** (the RFC-6455-invalid WS smoke-test key) fixed by US-52 + a hardened structural test that validates the key's 16-byte/24-char runtime validity; **J2 + J4** (the independent US-47 deploy failure AND the per-merge regression) both root-caused to a **single shared factor — VPS `/var/lib/containerd` disk exhaustion (ENOSPC) during `docker compose pull`** — and fixed by US-53/US-54 (`docker image prune -f` before pull, scoped `down --remove-orphans`), with a **nine-invariant regression guard** (US-54 AC-54.2) pinning every load-bearing deploy invariant. **Two authoritative GREEN deploy runs on `main` at HEAD — 27745162097 (per-merge) and 27745573971 (deliberate HEAD)** — carried the full dashboard chain to the VPS and the Tester confirmed every condition: HTTP 200 on 8002, `/dashboard/` 200, WS upgrade HTTP 101, the US-49 candle / US-50 cohort / US-51 annotation APIs all 200, `frontend`/`web`/`listener`/`celery-worker` Up, solanaBilly untouched on 8001. **The P6 OFFLINE GATE is VPS-CONFIRMED** ("operator sees real candles for a replayed token" — now live on the VPS, not merely green locally) and **the third PRD pillar's phase DoD is DONE.** On top of the sound deploy path, **US-56** (Config & model control operator skin) and **US-57** (Feature Builder UI) shipped as React+DRF surfaces over the existing US-9/US-42 and US-31 backends — view/diff/activate through the audited paths, export off the celery container, no silent state change. **0 firehose activations — 8 Birdeye + 8 Helius remain banked** (offline/replay-driven by construction).

**J1 — US-52: the WS-key defect fixed AND the green-test-masks-runtime gap closed at the WS layer.** `deploy.yml`'s WS smoke-test now generates a valid 16-byte random key (`os.urandom(16)` → 24-char base64) instead of the hardcoded 22-byte string, and a new pytest guard (`test_deploy_ws_key_ac522.py`) **executes the key assignment and asserts the 16-byte/24-char result** — so reverting to any invalid length fails CI. The B3/C1 "green structural test ≠ live runtime" recurrence that AC-48.3 represented is closed where it lived: a smoke-test that hardcodes its own handshake now validates that handshake.

**J2 + J4 — US-53/US-54: three co-occurring deploy failures reconciled as ONE system fix, not three point-fixes.** The holistic RCA (`ops/rca_consolidated_ac541.md`) mapped each failure to its root cause: **J2** (run 27683660493) and **J4** (run 27681451880) **share** a VPS disk-exhaustion (ENOSPC) root cause — accumulated sprint-10 image layers filled `/var/lib/containerd`, so `docker compose pull` failed before `up -d` was reached; **J1** is genuinely independent (code correctness, smoke-test step). Fix: `docker image prune -f` before each pull (removes dangling layers; tagged solanaBilly images untouched) + scoped `down --remove-orphans`. The **nine-invariant regression guard** (`test_deploy_regression_guard_ac542.py`, 688 lines) pins `--remove-orphans`, the AC-39.2 promoter ordering, the AC-12.3 retry-with-backoff, the US-40 unified `workflow_call` gate, the RFC-6455-valid WS key, the `prune` fix, and the `push: [main]` trigger — fail-loud, never a no-op. Reproducibility proven: BOTH a deliberate HEAD run AND a subsequent per-merge run are green with no ENOSPC recurrence.

**J3 — US-55: the dashboard-chain closeout.** With the deploy path sound, the two green HEAD runs carried US-48 (foundation + one-tape-feed consumer), US-49 (tape→candle token-detail/research view = the offline gate), US-50 (cohort wall), and US-51 (annotation → labeled export) to the VPS. The Tester promoted US-49/US-50 to PASSED (no application-code change — CI-verified code-complete, blocked only by the J1 defect) and signed off US-51's final VPS gate. The dashboard chain is DONE on the VPS.

**US-56 — Config & model control operator skin (§13.2#6).** A read-only DRF API (`core/control_api.py`) surfaces the active `PipelineConfig` (US-9) + `ModelRegistry` (US-42), their django-simple-history audit trails, and a deterministic version diff; operator-GATED activate actions delegate **exclusively** to the audited `activate_config()` / `activate_model()` paths (`IsAdminUser`, at-most-one-active preserved) and an AST guard confirms the view path **never** flips `firehose_active`/`scoring_enabled`/`trading_enabled`. `ConfigControl.jsx` renders it fresh in the US-48 React frontend (no server-rendered tables). H1 ImportError traps wire the surface into the canonical `ci.yml` job. VPS smoke-test confirmed (`/api/control/config/` HTTP 200).

**US-57 — Feature Builder UI (§13.2#7).** A DRF endpoint + `FeatureBuilder.jsx` trigger the **existing** US-31 §6.5 one-click labeled export — no new export math; the view dispatches the existing `build_features` Celery task **off the celery-worker container** (#289), config-driven, and surfaces the produced dataset's US-36-pattern MANIFEST (content hash, LC_ALL=C sort, SHA-256 of decompressed bytes) + a run/status indicator, read back deterministically. AST guards confirm no auto-start flag mutation; H1 ImportError traps lock the surface into CI. VPS smoke-test confirmed (export-result API HTTP 200).

**The deploy story — the hardened smoke-test earned its keep.** The per-merge deploys for the US-55/US-56/US-57 ACs (runs 27742700887, 27743372068, 27743852301, and the US-55/US-56 runs) initially failed at the WS smoke-test with **HTTP 500** — a **latent `SynchronousOnlyOperation` bug in US-48's `TapeFeedConsumer`** (it called `get_active_config()` synchronously inside the async WS-accept path). This bug shipped CI-green in sprint-10 and was **invisible until J1's hardened smoke-test actually completed a real WS handshake** — i.e. fixing the key surfaced a real runtime defect the old (always-400ing) test could never reach. It was **caught by the deploy gate (not silently), diagnosed, and fixed within the sprint** (PR #242, `sync_to_async` wrap, merged 2026-06-18T07:55Z) before the two authoritative green runs. Per the Tester, the per-AC deploy failures do **not** constitute a DoD gap because the gate caught the bug and the sprint closed it.

**Firehose.** **Zero activations** — the deploy fixes are CI/VPS infra and both new views surface existing offline backends (US-9/US-42 config+registry, US-31 export) against the existing lake + ReplaySource. **8 Birdeye + 8 Helius remain banked.** The HARD-RULE "every activation banks a durable fixture" is untouched.

**Defects:** the latent US-48 `TapeFeedConsumer` `SynchronousOnlyOperation` bug (HIGH — WS HTTP 500 at the real handshake; caught by the hardened deploy gate, fixed PR #242 within the sprint) + routine lint slips (E501 AC-57.1, I001 AC-57.3) caught by ruff and fixed in one iteration. No open defects at close. Coverage ≥80% (1826 tests passing at AC-57.3).

**Carry into sprint-12 / forward_plan (priority order):** **K1** the **MANDATORY NEXT EPIC — Copy-Trade Dashboard v1** (`solanatrilly_copytrade_SPEC.md`, operator-committed; a §5-isolated `copytrade_engine` sibling pipeline with its own Helius wallet-subscription, `copytrade.*` config, `copytrade_`-prefixed tables, first-buy-only PRE-graduation copy, observe-default safety gate, per-wallet PnL tab) — plan as its own epic, do NOT over-commit · **K2** the operator-driven **P7-3 soak (I2)** — now fully unblocked (scorer + dashboard both VPS-live) · **K3** the **ENDGAME (I3)** — promote `trilly_pregrad_v3_2` + start the firehose when the soak holds · **K4** an **async-safety guard** so a Channels consumer's sync ORM call in an async context can't ship CI-green again (the US-48 bug class) · **K5** the **P8 trading-execution path** (§10) + the deferred P8-dependent dashboard views (Live Positions §13.2#1, Calibration & PnL §13.2#4, Replay viewer overlay §13.2#5) — follow promotion. See `retrospective.md` K1–K5.

**Metrics:** 6 stories committed · **6 fully Tester-PASS (18/18 ACs)** · **J1/J2/J4 all closed at root; P6 offline gate VPS-CONFIRMED; third PRD pillar DoD DONE; US-56/US-57 shipped live** · 19 PRs merged (#224–#242), CI `test` green at merge on all · deploy runs: two authoritative GREEN HEAD/per-merge runs **27745162097 + 27745573971** (full dashboard chain + all story smoke-tests green); per-AC WS-500 failures (TapeFeedConsumer async bug) caught by the gate and fixed in-sprint by **PR #242** · **0 firehose activations** (8 Birdeye + 8 Helius banked) · coverage ≥80% (1826 tests). Token/cost spend: see `../project-state.json` (Project Lead). **On completion, all THREE PRD pillars are delivered AND DoD-done at the dashboard layer; the remaining whole-project DoD is the operator-driven soak/endgame (I2/I3) + the P8 trading-execution path, with the Copy-Trade Dashboard v1 as the mandatory next epic.**

---

## Sprint-10 — Open P6: the FOUNDATIONAL research-first dashboard (PRD §13, the last unbuilt PRD pillar) + close the two agent-actionable sprint-9 carries (I1 clean US-43 redeploy, I4 the AI-agent ruff gate) — REVIEW COMPLETE (P6 opened at code level; VPS deploy gap → sprint-11)
- **Phase:** review (`deploy-gap`)
- **Sprint plan (source of truth):** [`sprint10.json`](sprint10.json)
- **Review outcome:** **All 6 stories / 18 ACs implemented and CI-green on every feature branch; P6 is OPENED at code level and the P6 offline gate is structurally met** (US-49 — *"operator sees real candles for a replayed token"*: the tape→candle API + token-detail view render a replayed token's real candle + pressure/net-flow overlay + t0/score markers + the **exact feature vector + score** the model saw, all derived through the shared US-30 extractor / US-43 BlendScorer). **The dashboard foundation (US-48), token-detail/research view (US-49), cohort pattern-mining wall (US-50), and human-annotation→labeled-export killer feature (US-51) all landed CI-green.** **US-46 / I1 is the one fully-closed VPS deliverable** — the clean US-43 blend-scorer redeploy ran GREEN (run **27680808876**): HTTP 200 on 8002, `listener` Up, `celery-worker` Up, `lightgbm 4.6.0` + `tools.promote_model.promote_blend` importable in-container, solanaBilly untouched on 8001 — the **soak prerequisite (I2) is confirmed met.** **But the VPS deploy DoD is UNMET for the other 5 stories**, blocked by a single one-line defect in the `deploy.yml` WS smoke-test (a hardcoded, RFC-6455-invalid `Sec-WebSocket-Key` → Daphne HTTP 400 on every attempt) plus an **independent** US-47 deploy failure: **US-46 PASSED · US-47 FAILED** (deploy, independent root cause) **· US-48 FAILED** (AC-48.3 WS-key defect; AC-48.1/48.2 passed) **· US-49 / US-50 BLOCKED** (code-complete, CI-green; blocked only by the US-48.3 WS-key defect — no code changes required) **· US-51 requirements-approved** (code-complete, CI-green; final VPS gate pending the same fix). **Firehose untouched — 8 Birdeye + 8 Helius remain banked** (P6 offline by construction). The fix is one line and the remedy is documented; carried as **J1** (WS-key fix → unblocks US-48/49/50/51) and **J2** (US-47 deploy). See Sprint-10 Review below + [`retrospective.md`](retrospective.md) action items J1–J5.
- **PRD / direction:** [`PRD.md`](PRD.md) §13 (the dashboard) + §14 ("IGNORE solanaBilly's UI — build §13 fresh") + §16 (build phase **P6**, offline gate: *"operator sees real candles for a replayed token"*) + §15.1 (the `frontend` dev container). Planned from the sprint-9 `forward_plan` + retrospective action items **I1–I5** (I5 = the P6 dashboard).
- **Why this sprint:** P0–P5 + the sprint-8 birth-tape source + the **P7 serving path** (sprint-9) are all closed — the operator can promote `trilly_pregrad_v3_2` and the only remaining road to the endgame (**I2** soak + **I3** promote) is **operator-driven, not sprint work**. Of the PRD's **three pillars**, the third — the **research-first dashboard (§13)** — is **entirely unbuilt**, and it is the operator's explicit ask (*"the UI is terrible. I can't even see what's happening with the positions"; "guide the modeling agents with visualizations only I can see and a machine can't"*). solanaBilly never had it; §14 directs us to **IGNORE solanaBilly's Flask/DataTables UI and build §13 fresh**. P6 is **offline/replay-driven by construction** — every dashboard view renders from the **existing lake + ReplaySource + the US-43 BlendScorer / US-44 banked golden vectors**, so **zero firehose activation** (8 Birdeye + 8 Helius stay banked). Two agent-actionable sprint-9 carries ride along: **I1** — a clean US-43 (blend serving path) redeploy so the VPS has the scorer **live before the operator-driven soak** (the per-story deploy run 27673867804 failed; CI-green, deploy-context); and **I4** — close the **AI dev-agent / ruff hook gap** (G3 made the hook unforgeable for humans/devcontainer — H2 CONFIRMED — but 4 sprint-9 lint defects still slipped through AI-agent commits that bypass local pre-commit hooks).
- **Sprint goal:** **Open P6 — the foundational research-first dashboard**, meeting the P6 offline gate (*"operator sees real candles for a replayed token"*). In build order: **(US-46 / I1)** clean re-deploy of the US-43 15-booster blend serving path to the VPS staging stack from a green run, Tester-confirmed scorer-live-on-VPS before the soak; **(US-47 / I4)** a structural mechanism that catches I001/E501/F401 on the **AI dev-agent commit path** before the CI Lint step (as unforgeable for agents as G3 made it for humans); **(US-48)** the dashboard **foundation** — React + Vite + DRF + Django Channels (NOT server-rendered tables), the `frontend` dev container in **both** compose files (Docker Rules, `-p solanatrilly`), and the **ONE-tape-feed** Channels consumer (§13.4 — one source, no separate price feed to drift), deployed + smoke-tested **on the VPS**; **(US-49)** the **token-detail / research view** — a tape→candle API (1s/5s/15s/1m OHLC via the **same** shared US-30 extractor / raw lake, Principle #2) + a replayed token's real candle with pressure/net-flow overlay, t0/score markers, AND the **exact feature vector + score** the model saw (from the US-43 BlendScorer / US-44 golden vectors) — **this MEETS the P6 offline gate**; **(US-50)** the **cohort pattern-mining wall** — a grid of mini candle sparklines, groupable/sortable by outcome, score band, exit trigger, depth bucket, time-of-day; **(US-51)** **human annotation → labeled export** (the killer feature) — the `annotations` table (§8) + a tag panel beside the chart + an export to the labs (mirroring the §6.5 Feature Builder export, off the celery container per #289). **Firehose:** P6 is **offline/replay-driven by construction** — **zero** activations this sprint (8 Birdeye + 8 Helius remain banked). **Deferred to the `forward_plan`:** the operator-driven **soak (I2)** + **endgame (I3)**; the **Live Positions board** (§13.2#1, the operator's #1 ask) and **Calibration & PnL analytics** (§13.2#4) — both **require P8 position/PnL data** that does not exist until the trading engine lands; the **Replay viewer / Config-model control / Feature Builder UI**; and the **P8 trading-execution path** (gated behind the soak/endgame).

## Stories (sprint-10 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-46 | I1 — clean re-deploy of the US-43 15-booster blend serving path to the VPS from a green run; Tester-confirm the scorer is live on VPS before the soak | high | US-43, US-40 | 3 | done | done | **passed** (green run 27680808876; scorer live on VPS; I1 CLOSED) |
| US-47 | I4 — close the AI dev-agent / ruff hook gap: a structural mechanism catching I001/E501/F401 before the CI Lint step on the AI-agent path | high | US-39 | 3 | done | done | **failed** (ACs CI-green; per-story deploy run 27683660493 failed — independent root cause → J2) |
| US-48 | P6 — dashboard FOUNDATION: React + Vite + DRF + Channels, the `frontend` dev container in both compose files, and the ONE-tape-feed consumer (§13.1/§13.4/§15.1) | high | US-1, US-30 | 3 | done | done | **failed** (AC-48.1/48.2 passed; AC-48.3 WS-key smoke-test defect → J1) |
| US-49 | P6 OFFLINE GATE — token detail / research view: tape→candle API (1s/5s/15s/1m) from the shared extractor + real candle + the exact feature vector + score (§13.2#2) | high | US-48, US-43, US-30 | 3 | done | done | **blocked** (3/3 ACs CI-green; P6 offline gate met at code level; VPS gate blocked by US-48.3 → J1) |
| US-50 | P6 — the cohort small-multiples pattern-mining wall: groupable/sortable by outcome, score band, exit trigger, depth bucket, time-of-day (§13.2#3) | medium | US-49 | 3 | done | done | **blocked** (3/3 ACs CI-green; VPS gate blocked by US-48.3 → J1) |
| US-51 | P6 — human annotation → labeled export (the killer feature): the `annotations` table (§8) + tag panel + export to the labs off celery (§13.3) | medium | US-49 | 3 | done | done | **approved (reqs)** (3/3 ACs CI-green, 1637 tests; final VPS gate pending J1) |

> **Scope:** sprint-10 commits **6 stories / 18 ACs** — the P6 dashboard foundation + offline-gate token-detail view + cohort wall + annotation/export (US-48…US-51) + the two agent-actionable sprint-9 carries (US-46 / I1 clean US-43 redeploy, US-47 / I4 the AI-agent ruff gate). Source of truth: [`sprint10.json`](sprint10.json). **P6 is offline/replay-driven by construction — zero firehose activation (8 Birdeye + 8 Helius remain banked).** Matches the established 6-story / 18-AC cadence — deliberately NOT over-committed (the retrospectives repeatedly warn against over-loading heavy phases).

**Build order:** **US-46** (I1 clean US-43 redeploy) and **US-47** (I4 AI-agent ruff gate) are independent process/deploy carries and may run **first / in parallel**. The dashboard chain is **sequential**: **US-48** (stack + the one-tape-feed consumer) → **US-49** (tape→candle API + token-detail view = **the P6 offline gate**). After US-49, **US-50** (cohort wall) and **US-51** (annotation → export) both depend only on US-49 and may run in parallel.

> **GitHub Issues:** created at sprint-10 planning (2026-06-17), one per story, mirroring the prior convention —
> [US-46 #194](https://github.com/AsimQuick/solanaTrilly/issues/194) ·
> [US-47 #195](https://github.com/AsimQuick/solanaTrilly/issues/195) ·
> [US-48 #199](https://github.com/AsimQuick/solanaTrilly/issues/199) ·
> [US-49 #196](https://github.com/AsimQuick/solanaTrilly/issues/196) ·
> [US-50 #197](https://github.com/AsimQuick/solanaTrilly/issues/197) ·
> [US-51 #198](https://github.com/AsimQuick/solanaTrilly/issues/198).
> Source of truth remains [`sprint10.json`](sprint10.json).

## Sprint-10 Review — Summary (2026-06-17)
**Phase:** review (`deploy-gap`) | **Committed scope:** US-46…US-51 (6 stories, 18 ACs) | **Goal:** **met at code level — P6 OPENED and the P6 offline gate structurally satisfied; US-46/I1 closed VPS-live (soak prerequisite met); the VPS deploy DoD for 5 of 6 stories blocked by a single one-line `deploy.yml` WS-smoke-test defect (+ an independent US-47 deploy failure)**
**Full retrospective + action items (J1–J5):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 6 stories / 18 ACs implemented and CI-green on every feature branch.** The third and last unbuilt PRD pillar — the **research-first dashboard (§13)** — is now **opened at code level**: a React + Vite + DRF + Django Channels foundation (US-48), a tape→candle token-detail/research view that **meets the P6 offline gate** (US-49), the cohort pattern-mining wall (US-50), and the human-annotation→labeled-export killer feature (US-51). **US-46 / I1 is fully closed on the VPS** — the clean US-43 blend-scorer redeploy ran GREEN and the scorer is confirmed live (soak prerequisite I2 met). **The remaining DoD gap is a single one-line defect** in `deploy.yml`'s WS smoke-test that fails every deploy at the WebSocket-upgrade step, blocking the VPS confirmation for US-48/49/50/51, plus an **independent** US-47 deploy failure. **No firehose spent — 8 Birdeye + 8 Helius remain banked** (P6 offline by construction).

**US-46 — I1: the clean US-43 redeploy, scorer confirmed live on VPS (the one fully-closed VPS deliverable).** The sprint-9 US-43 per-story deploy had failed (run 27673867804). Root cause diagnosed (`ops/rca_run_27673867804.md`): **orphaned VPS container state** from a prior deploy session — stale image-hash-prefixed containers that `docker compose up -d` could not reconcile without `--remove-orphans`, producing a `No such container` mid-reconcile. Fix: `--remove-orphans` added to the scoped (`-p solanatrilly`) `up -d` in `deploy.yml`. A deliberate clean run at HEAD (run **27680808876**) confirmed all five AC-46.3 conditions: **HTTP 200 on 8002** (with the AC-12.3 retry-with-backoff), **`listener` Up**, **`celery-worker` Up**, **`lightgbm 4.6.0` + `tools.promote_model.promote_blend` importable in-container** (the soak prerequisite), and **solanaBilly untouched on 8001** (HTTP 302, every command scoped `-p solanatrilly`). I1 CLOSED; the operator can begin the I2 soak.

**US-47 — I4: the AI dev-agent ruff gate built + CI-green, but the deploy failed (independent).** The gap: AI agents `git commit` on the **host**, where the G3 devcontainer/CI hook-installer never fires, so 4 sprint-9 lint defects slipped past. Fix: a Claude Code `PreToolUse` Bash hook in the **committed** (`git check-ignore`-verified) `.claude/settings.json` invoking `scripts/dev-agent-ruff-gate.sh`, catching I001/E501/F401 **before** the commit. Behavioral proof (10 tests seeding each violation class + clean-file pass) and a silent-vanish guard (`core/dev_agent_ruff_gate_guard.py` — raises `ImportError` at collection if the gate script / settings hook is deleted, no swallowed-exception anti-pattern). **All 3 ACs CI-green.** But the per-story deploy (run **27683660493**) FAILED — an **independent** root cause (it predates the AC-48.3 WS defect; US-47 last merged 10:49Z, PR #208 merged 11:41Z). VPS DoD unmet; carried as **J2**.

**US-48 — P6 dashboard FOUNDATION: AC-48.1/48.2 passed, AC-48.3 blocked by a WS-key defect.** AC-48.1 (the `frontend` Vite dev container in **both** compose files, port-collision-free, `-p solanatrilly`-isolatable, React/Vite building **in-container** per Docker Rules) and AC-48.2 (the `TapeFeedConsumer` Channels consumer sourced from an **injected `DataSource`** — no `LiveSource`/price-client imports, feed-split drift structurally excluded; `DashboardConfig`-driven channel/topic names; deterministic replay delta sequence) are **CI-verified passed**. **AC-48.3 FAILS at the VPS deploy:** the `deploy.yml` step "Smoke-test WS endpoint HTTP 101 upgrade" sends a **hardcoded** `Sec-WebSocket-Key` of `c29sYW5hdHJpbGx5X2FjNDgzX2tleQ==` (base64 of `solanatrilly_ac483_key` — 22 bytes), but RFC 6455 §4.1 requires exactly **16 random bytes (24-char base64)**; Daphne rejects every attempt with **HTTP 400 'bad Sec-WebSocket-Key (length must be 24 ASCII chars)'**, so all retries fail (deploy runs 27686398326, 27689164827, 27691837229, 27694049191). The VPS state is unconfirmed. Remedy (documented): generate a valid key, e.g. `python3 -c 'import base64,os; print(base64.b64encode(os.urandom(16)).decode())'`. Carried as **J1**.

**US-49 — P6 OFFLINE GATE: code-complete and CI-green; VPS confirmation blocked by US-48.3.** `build_candles` derives 1s/5s/15s/1m OHLC **exclusively** via `_lake_row_to_micro` from `core.feature_extractor` — the same §7.1 path the shared US-30 `FeatureExtractor` uses (Principle #2, no separate price basis; structural assertion confirmed). `token_detail_api` returns the candle series + buy/sell-pressure & net-flow overlay + t0/score markers (AC-49.2), and `build_score_panel` routes the **exact** feature vector through the US-43 `BlendScorer.score_single()` — the parity-checked score, never a re-implementation (AC-49.3; H1 ImportError trap on `build_score_panel`). **All 3 ACs CI-green; the P6 offline gate — *"operator sees real candles for a replayed token"* — is structurally met.** Promotable to passed with **no code changes** once J1 lands a green VPS deploy.

**US-50 — P6 cohort pattern-mining wall: code-complete and CI-green; VPS confirmation blocked by US-48.3.** `build_cohort_sparklines` reuses `build_candles` (Principle #2 — same tape→candle path, no cohort-local basis; AST import assertion confirmed); absent-mint entries return empty lists (real-missing preserved, never fabricated, H4). `CohortGroupingConfig` (score-band / depth-bucket / time-of-day thresholds as Pydantic fields in `DashboardConfig`) drives five-dimension grouping; `assign_group_key` returns `None` cleanly for unavailable fields (e.g. exit_trigger offline). `build_cohort_wall` is the H1 ImportError-trap named function; `CohortWall.jsx` renders TradingView sparklines. **All 3 ACs CI-green.** Blocked only by J1.

**US-51 — P6 human annotation → labeled export (the killer feature): code-complete, CI-green, requirements-approved.** The `annotations` table (PRD §8: mint, author, tags[], note, created_at) is a **separate store keyed on mint with no FK to `RawEvent`** — structurally isolated from the raw lake (§6.4.1); the categorical tag set is config-driven (`AnnotationConfig`, Principle #1). `export_annotations` is a `@shared_task` running **off the celery container** (#289), emitting a labeled CSV + a US-36-pattern MANIFEST (dataset id, content hash, LC_ALL=C sort, SHA-256 of decompressed bytes), deterministic run-twice. The US-11 AST no-auto-start guard is extended to the annotation/export path (scoring/trading flags stay False; idempotent; H1 ImportError trap on the export task). Full suite **1637 passed, 85% coverage**. Requirements-approved; final VPS gate pending J1.

**Firehose.** **Zero activations this sprint** — P6 is offline/replay-driven by construction: every dashboard view, the candle API, the cohort wall, and the annotation/export all ran against the **existing lake + ReplaySource + the US-43 BlendScorer / US-44 banked golden vectors**. **8 Birdeye + 8 Helius remain banked.** The HARD-RULE "every activation banks a durable fixture" is untouched.

**The deploy story — a one-line defect, not an infra wall.** Unlike the four-sprint P0 deploy drag, this gap is a single trivial, fully-diagnosed defect: the WS smoke-test in `deploy.yml` hardcodes an RFC-6455-invalid `Sec-WebSocket-Key`, so Daphne 400s the handshake on every attempt. It blocks US-48/49/50/51's VPS confirmation but requires **no application-code change** — the dashboard code is CI-green. The **structural test asserted the smoke-test step's presence in the workflow file, not the runtime validity of the key it sends** — the recurring "green structural test ≠ working runtime" lesson (B3 sprint-2/3) in a new costume. US-47's deploy failure is a separate, independent root cause. US-46's clean green run (27680808876) proves the deploy pipeline itself is sound; the WS-key and US-47 failures are localized.

**Defects:** the AC-48.3 WS-key defect (HIGH — blocks 4 stories' VPS DoD, one-line fix → J1); the US-47 deploy failure (HIGH — independent root cause → J2). No application-code defects: all 18 ACs CI-green. Coverage ≥85% vs. the ≥80% gate.

**Carry into sprint-11 / forward_plan (priority order):** **J1** fix the `deploy.yml` WS-smoke-test key (valid 16-byte random key) + harden the smoke-test so it can't hardcode an invalid handshake, re-deploy, and Tester-confirm AC-48.3's VPS conditions — **this single fix unblocks US-48/49/50/51 with no code changes** · **J2** diagnose + fix the independent US-47 per-story deploy failure (run 27683660493), re-deploy, confirm VPS · **I2** the operator-driven **P7-3 soak** (now unblocked — US-46 confirmed the scorer live; v3.2 vs incumbent ~30/day, depth-fragile, plan the activation ledger first) · **I3** the **ENDGAME** (promote `trilly_pregrad_v3_2` + start the firehose when the soak holds) · the deferred dashboard views (**Live Positions** §13.2#1, **Calibration & PnL** §13.2#4, **Replay viewer**, **Config/model control**, **Feature Builder UI**) + the **P8 trading-execution path** that unblocks them. See `retrospective.md` J1–J5.

**Metrics:** 6 stories committed · **6 implemented + CI-green (18/18 ACs)** · **1 fully Tester-passed + VPS-confirmed** (US-46) · **2 deploy-failed** (US-47 independent, US-48 AC-48.3 WS-key) · **2 blocked** (US-49/US-50 — code-complete, VPS gate blocked by US-48.3) · **1 requirements-approved** (US-51 — code-complete, final VPS gate pending) · **P6 opened at code level; offline gate structurally met** · deploy runs: US-46 GREEN (27680808876) · US-47 FAILED (27683660493) · US-48/49/50/51 deploys FAILED at the WS-key step (27686398326 / 27689164827 / 27691837229 / 27694049191) · **0 firehose activations** (8 Birdeye + 8 Helius banked) · coverage ≥85%. Token/cost spend: see `../project-state.json` (Project Lead).

---

## Sprint-9 — Open P7: the Scorer/Serving Path (the LAST phase before the endgame) + close the sprint-8 deploy gap (H1) + confirm G3 (H2) — REVIEW COMPLETE
- **Phase:** done
- **Sprint plan (source of truth):** [`sprint9.json`](sprint9.json)
- **PRD / direction:** [`PRD.md`](PRD.md) §7.4 + [`oracle-direction.md`](oracle-direction.md) §4 — opens **P7**, the serving path that lets the operator **promote `trilly_pregrad_v3_2` and start the firehose** (the whole-project endgame). Planned from the sprint-8 `forward_plan` + retrospective action items **H1–H4**.
- **Review outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved.** **H1 (the only outstanding sprint-8 DoD item) is closed** — `deploy.yml` now reuses the canonical `ci.yml` `test` job via `workflow_call` (no divergent inline copy), and a deliberate sprint-boundary deploy confirmed **200-on-8002 + listener Up (driving helius_live) + solanaBilly untouched on 8001** (US-40, run 27667987197). **P7 — the scorer/serving path — is complete:** the model-agnostic feature-contract reconciler (US-41), the `ModelRegistry` + BLEND write contract accepting the 15-booster blend (US-42), the one shared deterministic `BlendScorer` with the oracle §4.2 cutover risk resolved via a frozen `ReferenceDistribution` (US-43), and the **"scores in sync"** scorer-parity hard merge gate against v3.2's banked golden score vectors (US-44, HEAD deploy run 27675645960 GREEN). **H2 verdict: CONFIRMED** — the G3 unforgeable ruff hook stops the seven-sprint recurrence in developer/container paths, with evidence (US-45). **Firehose untouched — 8 Birdeye + 8 Helius remain banked** (P7 offline by construction). The operator is **unblocked to soak + promote** — only the operator-driven **soak + endgame** (forward_plan) remain. Sprint goal **fully met.** See Sprint-9 Review below + [`retrospective.md`](retrospective.md) action items I1–I5.
- **Why this sprint:** P0–P6 are closed — the integrity core (P5) makes live==offline **by construction**, and sprint-8's Helius program-wide **birth-tape** source (P6) made v3.2's 20 `pre_*` features **live-computable for the first time**. What stands between here and the endgame is the **serving path**: solanaTrilly has **no trading model committed** and **no LightGBM dependency**, and `trilly_pregrad_v3_2` is a **15-booster** seed-bagged 3-label rank-average **blend** (3 labels `ctrl`/`oracle`/`liq` × 5 seeds, over the 20 `pre_*` features) — not a single model. The serving path must load the blend, reconcile the feature contract **column-for-column and in booster order** (PRD §7.4), support a **BLEND write contract** in `model_registry`/`promote_model.py`, resolve the **pool-relative percentile-rank cutover risk** for live one-at-a-time scoring (oracle §4.2, "the gap most likely to surprise"), and prove **live==offline scoring by construction** against v3.2's banked golden score vectors. Process carries: **H1** (the only outstanding sprint-8 DoD item — the sprint-boundary deploy failed on a *divergent* inline CI job in `deploy.yml` while the canonical `ci.yml` passed the same commit; unify the gate) and **H2** (confirm with evidence the G3 unforgeable ruff hook stops the seven-sprint recurrence).
- **Sprint goal:** **Open P7 — the scorer/serving path.** In build order: **(US-40 / H1)** fix the deploy gap at its root — gate the deploy on the **same** canonical `ci.yml` `test` job (no second/divergent workflow), then Tester-confirm a green HEAD deploy (200-on-8002 + listener Up driving `helius_live` + solanaBilly untouched on 8001); **(US-41 / P7-1)** feature-contract reconciliation — `live_servable[]` covers **all 20** of v3.2's `pre_*` features, column-for-column and in booster order vs `meta.json`; promoter refuses on mismatch; **(US-42 / P7-2a)** the `model_registry` + `promote_model.py` **BLEND** write contract — accept a `lightgbm_regression` blend (15 boosters + rank-blend transform), feature-order gate enforced, no silent auto-start; **(US-43 / P7-2b)** the **15-booster rank-average blend serving path** — one shared deterministic scorer reading features through the US-30 extractor, with the live reference-pool/percentile cutover resolved, config-driven; **(US-44 / P7 offline gate)** **scorer parity** — bank v3.2's golden score vectors and prove the serving path reproduces them within tolerance, folded into the canonical T0/G1/G2 parity suite ("**scores in sync**" a new standing DoD line); **(US-45 / H2)** confirm the unforgeable ruff hook stops the recurrence with evidence + re-confirm the status-integrity guard / phase-promoter fire in the deploy sequence + own the retrospective. **Firehose:** P7 is **offline by construction** — **zero** activations this sprint (8 Birdeye + 8 Helius remain banked). **Deferred to the `forward_plan`:** P7-3 the head-to-head **soak** (operator-driven, depth-fragile — run at ~30/day, not shallower) and the **endgame** (promote + start firehose); the **P6 dashboard** (PRD §13, H4) as a parallel/follow-on track (candidate sprint-10); and the **trading-execution path** (gated behind `trading_enabled` + the soak).

## Stories (sprint-9 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-40 | H1 — fix the sprint-8 deploy gap at root: unify the deploy gate on the canonical `ci.yml` `test` job + Tester-confirm a green HEAD deploy | high | US-39 | 3 | done | done | **approved** |
| US-41 | P7-1 — feature-contract reconciliation: `live_servable[]` covers all 20 v3.2 `pre_*` features, column-for-column + booster order; promoter refuses on mismatch (oracle §4.1) | high | US-29, US-30, US-34 | 3 | done | done | **approved** |
| US-42 | P7-2a — `model_registry` + `promote_model.py` BLEND write contract: accept a `lightgbm_regression` blend (15 boosters + rank-blend), feature-order gate (oracle §4.2) | high | US-41, US-9 | 3 | done | done | **approved** |
| US-43 | P7-2b — the 15-booster rank-average blend serving path: one shared deterministic scorer + live reference-pool/percentile cutover resolved, config-driven | high | US-42, US-30 | 3 | done | done | **approved** |
| US-44 | P7 offline gate — scorer parity: live==offline by construction vs v3.2's banked golden score vectors, folded into the canonical parity gate ("scores in sync") | high | US-43, US-32 | 3 | done | done | **approved** |
| US-45 | Process — H2: confirm the G3 unforgeable ruff hook stops the recurrence with evidence + re-confirm status-integrity guard / phase-promoter in the deploy sequence | high | US-39 | 3 | done | done | **approved** |

> **Scope:** sprint-9 commits **6 stories / 18 ACs** — the P7 serving-path core (US-41…US-44) + the carried sprint-8 deploy-gap closeout (US-40 / H1) + the process confirmation (US-45 / H2). Source of truth: [`sprint9.json`](sprint9.json). **Review outcome: all 6 stories Tester-approved (18/18 ACs); H1 closed, P7 serving path complete, H2 CONFIRMED.** P7 has exited — the operator can promote `trilly_pregrad_v3_2` (BLEND), the serving path is proven **live==offline by construction** ("scores in sync" now a standing DoD line), and only the operator-driven **soak + endgame** (forward_plan) remain.

**Build order:** **US-40 first** (closes the outstanding sprint-8 DoD; independent of the P7 chain, may run in parallel) and **US-45** (process/H2) is independent. Then the P7 chain is sequential: **US-41** (feature contract) → **US-42** (registry + BLEND write contract) → **US-43** (blend serving path) → **US-44** (scorer-parity gate). The offline scorer-parity gate (US-44) does **not** depend on any live read, so the firehose budget stays fully banked.

> **GitHub Issues:** created at sprint-9 kickoff (2026-06-17), one per story, mirroring the prior convention —
> [US-40 #170](https://github.com/AsimQuick/solanaTrilly/issues/170) ·
> [US-41 #171](https://github.com/AsimQuick/solanaTrilly/issues/171) ·
> [US-42 #172](https://github.com/AsimQuick/solanaTrilly/issues/172) ·
> [US-43 #173](https://github.com/AsimQuick/solanaTrilly/issues/173) ·
> [US-44 #174](https://github.com/AsimQuick/solanaTrilly/issues/174) ·
> [US-45 #175](https://github.com/AsimQuick/solanaTrilly/issues/175).
> Source of truth remains [`sprint9.json`](sprint9.json).

## Sprint-9 Review — Summary (2026-06-17)
**Phase:** done | **Committed scope:** US-40…US-45 (6 stories, 18 ACs) | **Goal:** **fully met — H1 deploy gap closed; P7 scorer/serving path complete; H2 (G3 hook) CONFIRMED with evidence; the operator is unblocked to soak + promote**
**Full retrospective + action items (I1–I5):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved.** The last phase before the whole-project endgame is delivered: solanaTrilly can now load the 15-booster `trilly_pregrad_v3_2` blend, reconcile its feature contract column-for-column in booster order, promote it through an audited model registry, score it deterministically with the live percentile-rank cutover resolved, and prove **live==offline scoring by construction** against banked golden score vectors. The only remaining work is the operator-driven **soak + endgame** (promote + start the firehose) and the parallel **P6 dashboard** track — both in the `forward_plan`.

**US-40 — H1: the sprint-8 deploy gap closed at its root.** The sprint-8 boundary deploy had failed 3× at `Run tests with coverage` (exit 1) in `deploy.yml`'s inline CI job while the standalone canonical `ci.yml` passed identical tests on the identical commit — the H1 "single canonical test job, no second workflow" principle violated in the deploy path. Root cause: the inline copy ran in a different DB/env context. Structural fix: `deploy.yml` now calls `.github/workflows/ci.yml` via `workflow_call` (`deploy` → `build-and-push` → `ci`), so one test-job definition is consumed by both PR-CI and the deploy gate; structural YAML-parse tests lock the invariant and confirm the AC-39.2 phase-promoter and AC-12.3 retry-with-backoff are preserved. A deliberate sprint-boundary run at HEAD (run 27667987197) confirmed all three Tester-confirm conditions: **HTTP 200 on 8002, listener Up (driving helius_live), solanaBilly untouched on 8001** — closing the only outstanding sprint-8 DoD item in one sprint.

**US-41 — P7-1: feature-contract reconciliation.** A model-agnostic `reconcile_feature_contract()` compares the P5 FeatureSet (US-29/US-30 ordered columns + live_servable/training_only split) against a model's bound feature list, reporting missing/extra/order-divergent/training-only features. For v3.2 all **20 `pre_*` features reconcile clean and resolve to a live computation** through the shared US-30 extractor over the banked birth-tape fixture. `run_feature_contract_gate()` (in `tools/promote_model.py`) REFUSES on any mismatch with a printed REMEDY naming the offending feature; wired into the canonical `ci.yml` `test` job via an `ImportError` trap (H1).

**US-42 — P7-2a: the model_registry + BLEND write contract.** A `ModelRegistry` Django model (`db_table=model_registry`, django-simple-history audit, observational-only admin) records a promoted artifact's kind, bound ordered feature_list, labels/seeds manifest (3 labels × 5 seeds), blend-transform descriptor, artifact content hashes, and versions, with at-most-one-active discipline (`activate_model()` mirroring `activate_config()`). `promote_blend()` loads the 15 LightGBM boosters (`boosters/<label>_s<seed>.txt`) + the rank-average transform, enforces the US-41 feature-order gate at write time, and REFUSES on wrong booster count / order mismatch. **LightGBM added to the in-container scorer requirements** (`requirements.txt` + `libgomp1` in the Dockerfile — Docker Rules, never host). The US-11 AST no-auto-start guard is extended to the promote path (AC-42.3): promotion never flips `scoring_enabled`/`trading_enabled` and is idempotent.

**US-43 — P7-2b: the 15-booster rank-average blend serving path + cutover risk resolved.** `BlendScorer` is the one shared deterministic scorer: it loads the active blend from the registry and computes the score EXACTLY per `meta.json:selection_recipe` — per-label seed-average → percentile-rank → mean-of-3-ranks blend — reading features only through the US-30 extractor (Principle #2) and fully config-driven (Principle #1). **The oracle §4.2 cutover risk is resolved:** `ReferenceDistribution` is a frozen per-label score distribution banked from training; `score_single()` ranks a single live token against the frozen reference (same count-based formula as the pool path), so a live one-at-a-time score is reproducible and parity-checkable. Config-driven via `ScoringConfig.reference_dist_path`. A `score_token` Celery task runs the scorer off the **celery-worker container (never web/gunicorn, #289)**, gated by `scoring_enabled`, and replay-testable via `ReplaySource`.

**US-44 — P7 offline gate: "scores in sync."** v3.2's golden score vectors are banked as the permanent offline oracle (`lake/golden/golden_scores_v3_2/golden_scores.parquet` + a MANIFEST with content hash, LC_ALL=C sort, SHA-256 of decompressed bytes per US-36, raw=immutable). The US-43 serving path **reproduces the golden vectors within the documented tolerance, run-twice byte/tol-identical**, given the same feature inputs + reference distribution — the scorer analogue of the T0/G1/G2 swap/feature parity. The check **folds into the existing combined parity suite** in the single canonical `ci.yml` `test` job via `ImportError` traps on the named functions (H1 — no parallel gate), making **"scores in sync" a standing Definition-of-Done line** alongside "sources in sync." A deleted/renamed scorer-parity test fails pytest collection.

**US-45 — H2 + process, both confirmed.** **H2 verdict: CONFIRMED** — the G3 unforgeable ruff hook (auto-installed via `.devcontainer` `postCreateCommand` + CI `install-hooks.sh`, no manual step) catches seeded I001/E501/F401 and passes once fixed; the sprint-9 evidence fixture records verdict `"CONFIRMED"` with zero ruff CI defects attributable to a missing local hook across all sprint-9 implementation commits. **Qualifier:** 4 lint defects (F401 AC-40.1, I001 AC-41.1, F401 AC-42.3, I001 AC-44.2) still occurred — but in the **AI dev-agent commit workflow that bypasses local pre-commit hooks**, not from hook absence; the mechanism itself is proven unforgeable, and the AI-agent gap is carried as I4. The **F2 phase-promoter** is re-confirmed wired before the VPS deploy step and fired in the sprint-9 deploy sequence; the **US-13 status-integrity guard is GREEN on sprint9.json** (no story `status:done` with `tester_status` failed/blocked). The sprint-9 retrospective closes AC-45.3.

**Firehose.** **Zero activations this sprint** — P7 is offline by construction: the serving path, feature-contract reconciliation, BLEND write contract, and scorer-parity gate all ran against already-banked fixtures (helius_birth_tape golden fixture from US-34; v3.2 `golden_scores.parquet` banked in US-44). **8 Birdeye + 8 Helius remain banked** for the deliberate soak. The HARD-RULE "every activation banks a durable fixture" is untouched.

**Defects:** lint-only (F401/I001 class), each caught by CI/ruff and fixed in ≤1 iteration; none reached pytest or production; none attributable to a missing local hook (H2 CONFIRMED). Coverage ≥84% vs. the ≥80% gate.

**The deploy story.** Per-story boundary deploys ran GREEN for US-40 (27667987197), US-41 (27669718100), US-42 (27671498291), and US-44 at HEAD (27675645960 — confirmed 200-on-8002, listener Up, solanaBilly untouched on 8001). **US-43's per-story deploy FAILED** (run 27673867804) — a deploy-context issue, not a code regression (CI-green); all US-43 code is deployed and smoke-tested on VPS via the subsequent US-44 HEAD deploy. A deliberate US-43 re-run is carried as **I1**.

**Carry into sprint-10 / forward_plan (priority order):** **I1** investigate + re-deploy US-43 (blend serving path) from a clean run so the scorer is VPS-confirmed before the soak · **I2** the operator-driven **P7-3 soak** (v3.2 vs incumbent, ~30/day — depth-fragile, inverts at ~10/day; watch for live inversion; plan the activation ledger first) · **I3** the **ENDGAME** — when the soak holds, promote `trilly_pregrad_v3_2` via `promote_blend()` + start the firehose (the whole-project DoD; no capital before the soak holds) · **I4** close the **AI dev-agent / ruff hook gap** (a container-entrypoint or dev-agent-loop `ruff check` so the gate is as unforgeable for agents as G3 made it for humans) · **I5** schedule the **P6 dashboard** (§13, H4) as a parallel track. See `retrospective.md` I1–I5.

**Metrics:** 6 stories committed · **6 fully Tester-approved** (18/18 ACs) · **H1 closed, P7 serving path complete, H2 CONFIRMED** · AC-as-PR cadence, CI `test` green at merge on all · deploy runs: US-40 GREEN (27667987197) · US-41 GREEN (27669718100) · US-42 GREEN (27671498291) · US-43 FAILED (27673867804 — per-story; CI-green; → I1) · US-44 GREEN at HEAD (27675645960) · **0 firehose activations** (8 Birdeye + 8 Helius banked) · 4 lint defects (F401/I001) caught by CI and fixed in ≤1 iteration each, none from a missing hook (H2 CONFIRMED) · coverage ≥84%. Token/cost spend: see `../project-state.json` (Project Lead).

---

## Sprint-8 — Open P6: Pre-graduation Birth-tape Ingestion (the gap that blocks promotion) + Two-tier Idle + Parity Extension (oracle §1–§5) — REVIEW COMPLETE (deploy gap → sprint-9 H1)
- **Phase:** review (`approved-deploy-gap`)
- **Sprint plan (source of truth):** [`sprint8.json`](sprint8.json)
- **PRD / direction:** [`PRD.md`](PRD.md) §6.4 / §13 + [`oracle-direction.md`](oracle-direction.md) — opens **P6** by closing the architecture gap that **blocks model promotion**
- **Review outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved** across PRs #152–#169. The **P6 birth-tape architecture is delivered** — a Helius program-wide `transactionSubscribe` birth-tape `DataSource` (`source:"helius_live"`) captures every token create→buy/sell→migration into the **same** P5 normalized schema + lake + extractor (US-34); the two-tier idle policy makes full-population capture affordable without violating D4 (US-35); the US-32 parity gate is extended to the new source + a MANIFEST gate (US-36); the three-sprint-deferred **F5 Birdeye REST snapshot adapter** is built and proven against a real banked fixture (US-37); daily VPS→lake ship + ≤7-day expire-after-ship retention sweep makes disk safe under full-population capture (US-38); and both process carries closed — **G3** (ruff hook now unforgeable via devcontainer + CI auto-install) + **G4** (F2 promoter proven firing in the actual deploy sequence, run 27665516369) (US-39). One budgeted Helius activation spent (**9→8**), real pre-grad→graduation golden fixture banked; 8 Birdeye untouched. **One DoD gap:** the deliberate sprint-boundary deploy FAILED on a **new** failure mode — the Deploy workflow's inline CI job fails `Run tests with coverage` (exit 1) while the standalone `ci.yml` passes the same tests on the same commit (run 27665516309) — carried to sprint-9 as **H1**. Project advances to **P7 (the scorer/serving path, §7.4)** — the last phase before the operator can promote `trilly_pregrad_v3_2` + start the firehose. See Sprint-8 Review below + [`retrospective.md`](retrospective.md) action items H1–H4.
- **Why this sprint:** P0–P5 are closed — the integrity core (P5) makes live==offline **by construction** for the **Birdeye** tape. But the model the operator wants to promote (`trilly_pregrad_v3_2`) scores **pre-graduation** behavior (all 20 features are `pre_*`), and a **per-mint Birdeye `SUBSCRIBE_TXS` feed structurally cannot capture a token before its mint is known** — a *topology* problem, not a tuning one. Per [`oracle-direction.md`](oracle-direction.md) §1 this is **THE gap blocking promotion**, resolved by a **Helius program-wide `transactionSubscribe`** birth-tape source (the same single firehose solanaBilly runs 24/7), feeding the **same** P5 normalized schema + lake + extractor. Sprint-8 delivers that source (US-34), the **two-tier idle policy** that makes full-population capture affordable without violating the D4 label invariant (US-35), the **anti-drift parity gate extended** to the new source (US-36), the deferred **F5 Birdeye REST snapshot adapter** (US-37 / carry G2), **disk safety** under full-population capture (US-38), and the **process carries G3/G4** (US-39).
- **Sprint goal:** **Open P6 — pre-graduation birth-tape ingestion.** In build order: **(1)** the **Helius program-wide birth-tape `DataSource`** behind the seam (Principle #7) — `source:"helius_live"`, additive alongside Birdeye, decoding create/buy/sell/migration into the one §6.4/§7.1 normalized schema → the **same** P5 lake + shared extractor; first live bring-up is **one** budgeted, ledgered Helius activation (Helius **9→8**) banking a real pre-grad→graduation golden fixture (US-34); **(2)** the **two-tier idle policy** — a new `tape.pre_grad_idle_kill_ttl_s` (~300 s, ungraduated kill) + the **protected** `tape.idle_kill_ttl_s` (≥ `outcome.window_s`, D4 kept), config-driven in `core/schemas.py`, `reattach:true` both (US-35); **(3)** **extend the US-32 parity gate** — post-grad overlap byte-identity vs Birdeye + pre-grad raw-truth self-consistency + a **MANIFEST** gate; "sources in sync" becomes a standing DoD line (US-36); **(4)** the deferred **F5** live Birdeye REST `SnapshotDataSource` adapter, proven offline against a banked real snapshot (US-37); **(5)** the **daily VPS→lake ship + ≤7-day retention sweep** for disk safety (US-38); **(6)** **process hardening** — make the ruff hook **unforgeable** (G3) + **prove** the F2 phase-promoter runs in the deploy sequence (G4) (US-39). **Firehose:** only US-34 spends — one budgeted Helius activation (9→8); all other gates are offline (8 Birdeye banked). **Deferred to sprint-9 (P7):** the feature-contract reconciliation against v3.2's 20 `pre_*` features + the 15-booster blend serving/`model_registry` write contract (surfaced now in `sprint8.json` `forward_plan` per oracle §4).

## Stories (sprint-8 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-34 | P6a — Helius program-wide birth-tape `DataSource`: pre-graduation ingestion behind the seam (oracle §1) | high | US-19, US-30, US-16 | 3 | done | done | **approved** |
| US-35 | P6b — two-tier idle policy: `pre_grad_idle_kill_ttl_s` (~300 s) + the protected post-grad TTL, config-driven (oracle §2, D4) | high | US-34, US-11, US-20 | 3 | done | done | **approved** |
| US-36 | Extend the US-32 T0/G2 parity gate to `helius_live` + a MANIFEST gate (oracle §3) | high | US-32, US-34 | 3 | done | done | **approved** |
| US-37 | F5/G2 — the deferred live Birdeye REST `SnapshotDataSource` adapter, proven offline against a banked real snapshot | high | US-24, US-25 | 3 | done | done | **approved** |
| US-38 | Daily VPS→lake ship + ≤7-day retention sweep: disk safety under full-population capture (oracle §5) | high | US-19, US-34 | 3 | done | done | **approved** |
| US-39 | Process hardening — G3: make the ruff hook unforgeable + G4: prove the F2 phase-promoter runs in the deploy sequence | high | US-33 | 3 | done | done | **approved** |

> **Scope:** sprint-8 commits **6 stories / 18 ACs** — the P6a birth-tape ingestion epic (US-34) + the P6b two-tier idle policy (US-35) + the parity-gate extension (US-36) + the deferred F5 adapter (US-37) + disk safety (US-38) + process carries G3/G4 (US-39). Source of truth: [`sprint8.json`](sprint8.json). **Review outcome: all 6 stories Tester-approved (18/18 ACs); P6 birth-tape ingestion delivered** — solanaTrilly now captures **every token from birth** with `live==offline` parity extended to the new `helius_live` source. The remaining road to promotion is the **P7 serving path** (sprint-9; see `sprint8.json` `forward_plan`). **One DoD gap carried as H1:** the deliberate sprint-boundary deploy failed on a divergent Deploy-workflow CI job (`Run tests with coverage` exit 1) while the standalone `ci.yml` passed the same tests on the same commit.

**Build order:** **US-34 first** — the birth-tape source everything downstream eats (it carries the one budgeted Helius activation). **US-37** (F5 adapter) and **US-39** (process) are independent and may run in parallel. Then **US-35** (two-tier idle) needs US-34; **US-36** (parity extension) needs US-34 + US-32; **US-38** (disk ship/sweep) needs US-34 + US-19. The offline gates (US-36/37/38) do **not** depend on the live activation, so an aborted/short Helius window never blocks P6 exit.

**GitHub Issues:** created at sprint-8 kickoff (2026-06-17), one per story, mirroring the prior convention —
[US-34 #146](https://github.com/AsimQuick/solanaTrilly/issues/146) ·
[US-35 #147](https://github.com/AsimQuick/solanaTrilly/issues/147) ·
[US-36 #148](https://github.com/AsimQuick/solanaTrilly/issues/148) ·
[US-37 #149](https://github.com/AsimQuick/solanaTrilly/issues/149) ·
[US-38 #150](https://github.com/AsimQuick/solanaTrilly/issues/150) ·
[US-39 #151](https://github.com/AsimQuick/solanaTrilly/issues/151).
Source of truth remains [`sprint8.json`](sprint8.json).

## Sprint-8 Review — Summary (2026-06-17)
**Phase:** review (`approved-deploy-gap`) | **Committed scope:** US-34…US-39 (6 stories, 18 ACs) | **Goal:** **met (architecture complete) — P6 pre-graduation birth-tape ingestion delivered; process carries G3/G4 both closed; one DoD deploy gap carried as H1**
**Full retrospective + action items (H1–H4):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved** across PRs #152–#169. The **P6 architecture gap that blocked model promotion is closed** — for seven sprints the pipeline could only see a token *after* its mint was known, structurally blind to the pre-graduation window that `trilly_pregrad_v3_2`'s 20 `pre_*` features depend on. A Helius program-wide `transactionSubscribe` birth-tape source now hears create/buy/sell/migration for **every** token with zero selection bias, feeding the **same** P5 normalized schema + lake + extractor as Birdeye. The project advances to **P7 — the scorer/serving path (§7.4)**, the last phase before the operator can promote `trilly_pregrad_v3_2` and start the firehose.

**US-34 — the Helius program-wide birth-tape `DataSource` (P6a, the source everything downstream eats).** `HeliusBirthTapeSource` runs a single program-wide `transactionSubscribe(accountInclude=[pump.fun program])` and decodes each Anchor `TradeEvent` (adapting solanaBilly's `tape_recorder.py` — raw lamports/base-units, program `user` as trader) into the one `NormalizedSwap` schema, tagged `source="helius_live"` (additive alongside birdeye_live/birdeye_backfill/helius_verify). A `mint` field was added to `NormalizedSwap` so lake rows are self-describing for per-mint extraction. `build_birth_tape_recorder()` in `run_listener` wires it through the **same** `LakeWriter` + `SwapWriter` as the Birdeye path (Principle #2 / one code path), with **no** wallet-count/idle selection gate and pre-grad negative `rel` preserved; the US-2 static-analysis guard held (concrete source only in the adapter). The first live bring-up was the one budgeted Helius activation (**9→8**) banking a real pre-grad→graduation tape as the permanent offline golden fixture (`lake/golden/helius_birth_tape/`), ledgered in `ops/firehose_activation_log.md`.

**US-35 — the two-tier idle policy (P6b, D4-safe by construction).** Two config knobs in `core/schemas.py`: the new `tape.pre_grad_idle_kill_ttl_s` (default 300 s — the aggressive kill for ungraduated tokens, where ~all firehose write-cost lives) and the existing protected `tape.idle_kill_ttl_s` (≥ `outcome.window_s` = 1800 s — the D4 floor kept verbatim, the new knob exempt only while pre-graduation). `core/tape/idle_kill.py` gained `mark_graduated()`, which snaps a mint from the pre-grad tier to the protected tier at the graduation instant; both tiers `reattach:true` (the program-wide firehose still hears the mint, so "unsubscribe" is just dropping it from the record set — the reattach is free). Tested at both boundaries: a graduated mint idle past 300 s but within `window_s` is NOT killed; the same mint is eligible only after `idle_kill_ttl_s` of post-grad idleness.

**US-36 — the parity gate extended to helius_live + a MANIFEST gate (oracle §3).** The new source folds into the *existing* combined T0/G1/G2 suite (now **11 pinned functions** via `ImportError` traps in the single canonical `ci.yml` job — no parallel gate). Post-graduation **overlap** byte-identity vs Birdeye at both swap and feature level; pre-grad-only raw-truth self-consistency (decode run-twice byte-identical + §6.4 schema invariants + G1 golden-parity). Every dataset entering the lake carries a MANIFEST (`core/tape/manifest.py`: dataset id, source, date range, mint cohort, row count, content hash; LC_ALL=C sort; SHA-256 of *decompressed* bytes to avoid gzip variance). "Sources in sync" is now a standing DoD line.

**US-37 — the deferred F5 Birdeye REST `SnapshotDataSource` adapter (three-sprint carry CLOSED).** `BirdeyeSnapshotSource` (three REST calls → the canonical seven-field snapshot) implements the existing P4 snapshot seam, retains the #380 future-window clamp + Redis token-bucket limiter, and lives only in the adapter layer (US-2 guard green). A **real** on-demand snapshot was banked as a durable fixture (within the Birdeye professional allowance — NOT a firehose activation), and the adapter was proven against it **offline, run-twice byte-identical**. This closes the "synthetic parity ≠ working live adapter" gap that recurred across sprint-5 US-22, sprint-6 E3/F5, and sprint-7 — the live client now exists, behind the seam, before the scorer (P7) needs it.

**US-38 — daily VPS→lake ship + ≤7-day retention sweep (disk safety, load-bearing).** With full-population capture coming (operator note 2026-06-17: VPS 87% → pruned to 45%), `core/tape/lake_ship.py` ships the day's lake partitions one-way (VPS=capture+serve, local=single source of truth) as a registered Celery-beat task off the `celery` container (the #289 lesson — never web/gunicorn), in both compose files. A ≤7-day retention sweep then expires **shipped** partitions — and critically, expire is gated on a **verified** ship (local copy exists AND content-hash matches the VPS MANIFEST); unshipped or hash-mismatched partitions are never expired. Scoped to the solanatrilly lake only, no unscoped docker/prune/volume-removal. Idempotent (run-twice → same result). All windows config-driven (`lake_ship_window_days`, `lake_retention_days` in `core/schemas.py`).

**US-39 — process hardening, both carries closed.** **G3:** the ruff gate is now **unforgeable** — `.devcontainer/devcontainer.json` `postCreateCommand` auto-installs the pre-commit hook, and a CI `install-hooks.sh` step runs before the guaranteed `ruff check .` Lint step; AC-39.1 pytest-guards the auto-install (no manual `make install-hooks`) and fails on seeded I001/E501/F401, mirroring the H1/H2/US-2 guards. The six-sprint opt-in weakness is gone. **G4:** the F2 phase-promoter (`tools/promote_sprint_phase.py`) is wired as an explicit pre-deploy gate step in `deploy.yml` (and upstream in `ci.yml`), with a structural test asserting it runs *before* the deploy step — and it **actually fired** in the AC-39.3 PR-merge deploy (run 27665516369 SUCCEEDED), proving "built + tested" → "proven in the sequence." AC-39.3 also re-confirms the US-13 status-integrity guard green on `sprint8.json`.

**Firehose.** **One** budgeted Helius activation spent — US-34 AC-34.3's first live bring-up, the single deliberate, time-boxed, ledgered read banking the durable pre-grad→graduation golden fixture; **Helius 9→8**. Every offline gate (US-36 parity, US-37 F5 REST adapter, US-38 disk job) spent **zero**; US-37's REST read is within the Birdeye professional allowance, not an activation. **8 Birdeye remain banked.** The HARD-RULE "every activation banks a durable fixture" held.

**The deploy gap — a NEW failure mode, not the old self-inflicted one.** For three sprints (D2→E1→F2) the sprint-end deploy died on a stale `phase` tripping the US-13 guard; G4 now mechanically prevents that, and the promoter was proven firing. But the sprint-8 **boundary** deploy still failed — three attempts (runs 27665520087, 27665535622, 27665583551), all at `Run tests with coverage` (exit 1) — while the standalone `ci.yml` **passed the identical tests on the identical commit** (run 27665516309). The deploy is gated by a *second, divergent* CI job embedded in `deploy.yml`, not by the canonical `ci.yml` that already went green — the H1 "single canonical test job, no second workflow" principle violated in the deploy path. The architecture is complete and CI-clean; the AC-39.3 PR-merge deploy gave partial VPS evidence (listener Up, solanaBilly untouched), but the deliberate sprint-boundary run required by the DoD is not green. Carried as **H1**.

**Process note — the ruff irony, a seventh time.** The same pattern (F541 AC-34.1, I001 AC-36.1, I001+F401 AC-38.1) surfaced *during the sprint that made the gate unforgeable* — because the G3 auto-install landed mid-sprint, so the dev agents producing those commits didn't yet have it active; CI ruff caught all three, each fixed in one iteration, none reaching pytest or production. The recurrence should stop next sprint, but confirming it (rather than assuming) is carried as **H2** — the same "prove the mechanism fires" rigor G4 demanded of the promoter.

**Defects:** lint-only (F541/I001/F401), each caught by CI/ruff and fixed in 1 iteration; none reached pytest or production. Coverage ≥80% gate met; standalone CI green on the HEAD commit (run 27665516309).

**Carry into sprint-9 (priority order):** **H1** fix the deploy gap at its root — unify the deploy gate on the canonical `ci.yml` `test` job (rather than `deploy.yml`'s divergent inline copy), then re-run the sprint-8-HEAD deploy and Tester-confirm 200-on-8002 + listener Up (now driving helius_live) + solanaBilly untouched on 8001 · **H2** confirm with evidence that the G3 unforgeable ruff hook stops the recurrence · **H3** open **P7 — the scorer/serving path** (feature-contract reconciliation against v3.2's 20 `pre_*` features + the 15-booster rank-blend serving + `model_registry`/`promote_model.py` BLEND write contract; `sprint8.json` `forward_plan` P7-1/P7-2) · **H4** schedule the P6 dashboard (§13) as a parallel/follow-on track. See `retrospective.md` H1–H4.

**Metrics:** 6 stories committed · **6 fully Tester-approved** (18/18 ACs) · **P6 birth-tape architecture delivered** · PRs #152–#169, CI `test` green at merge (standalone `ci.yml` run 27665516309 green on HEAD) · AC-39.3 PR-merge deploy **SUCCEEDED** (run 27665516369 — listener Up, solanaBilly untouched, G4 promoter proven firing); **sprint-end deploy FAILED** 3 attempts (runs 27665520087 / 27665535622 / 27665583551) at `Run tests with coverage` in the divergent Deploy-workflow CI job — carried as H1 · **1 firehose activation** (Helius **9→8**, US-34 pre-grad→graduation golden fixture banked; 8 Birdeye banked) · 3 lint defects (F541/I001/F401) caught by CI and fixed in 1 iteration each (seventh sprint, during the sprint that made the gate unforgeable). Token/cost spend: see `../project-state.json` (Project Lead).

---

## Sprint-7 — Open P5 (Lake + Extraction Contract + Vendored Math + Feature Builder + T0/G1/G2 Golden Parity, §6.4 / §7.2 / §7.6) — REVIEW COMPLETE
- **Phase:** done
- **Sprint plan (source of truth):** [`sprint7.json`](sprint7.json)
- **PRD:** [`PRD.md`](PRD.md) — delivers **P5 (the integrity core, §6.4)**: parity by construction so a model's live score equals its offline score
- **Review outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved** across PRs #127–#145. The **full P5 integrity core is delivered** — `tape_microstructure.py` vendored verbatim + the G1 function-parity gate (US-28); the hashed/versioned `feature_sets` contract with the live_servable/training_only split (US-29); the **one shared deterministic extractor** serving live+offline+replay, leak-free by the causal cutoff (US-30); the one-click Feature Builder + manifest off the `celery` container (US-31); and the **T0/G1/G2 source-parity hard merge gate** (US-32). **Process hardening landed (US-33): F2 mechanized phase-promotion, F4 mechanized the ruff gate, and F3 closed the carried sprint-6 DoD VPS clause — the clean final HEAD deploy ran GREEN** (run 27654478462: 200 on 8002 at attempt 2/12, listener Up, solanaBilly untouched on 8001). Sprint goal **met.** The recurring stale-`phase` deploy trip (D2→E1→F2, three consecutive sprints) is now mechanically prevented. Project advances to **P6 (dashboard, §13)** — **PO: read [`oracle-direction.md`](oracle-direction.md) before planning sprint-8** (it sets the road P5 → promote `trilly_pregrad_v3_2` + start the firehose). See Sprint-7 Review below + [`retrospective.md`](retrospective.md) action items G1–G4.
- **Previous sprint:** [`sprint6.json`](sprint6.json) — **closed** (review complete; **P4 score-time snapshot + units-lock delivered, Tester-approved 17/17 ACs**; one DoD item — the clean final HEAD deploy — carried as F3, **now closed in sprint-7**; project advanced to P5 — see Sprint-6 Review below + [`retrospective.md`](retrospective.md) action items F1–F5)
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P0–P4 are closed — the VPS staging stack is live on 8002, the config core (P1) is the single source of truth, P2 lands graduated `tokens`, P3's recorder captures every PumpSwap swap from t0 into the immutable `jsonl.gz` lake + the queryable `swaps` mirror with live↔backfill byte-parity, and P4 takes one on-demand score-time snapshot per token with the three-unit lock. Retrospective **F1** directs **P5 (the lake + extraction contract + vendored math + Feature Builder + T0/G1/G2 golden parity, §6.4 / §7.2 / §7.6)** as the **primary deliverable** — THE integrity core that makes live==offline **by construction** (one vendored feature library, one deterministic extractor serving live+offline+replay, a golden-parity merge gate) and fixes the evidenced solanaBilly disaster (23% live vs 78% offline precision purely from assembly drift, #358/#359/#367). Process carries: **F2** — *mechanize* phase-promotion (the stale-`phase` deploy has tripped the US-13 guard **three consecutive sprints**; a checklist has proven not to work); **F4** — *mechanize* the ruff gate (I001/E501/F401 churn, five sprints running); **F3** — confirm a clean final deploy at true HEAD closing the carried sprint-6 VPS clause. **F5** (the live Birdeye REST snapshot adapter) is **consciously deferred to sprint-8**, scheduled near where the scorer (P7) consumes it — a single REST read off the P5 critical path, kept out of sprint-7 to avoid over-commitment on the heaviest phase.

### Sprint Goal
**Open P5 — the lake + extraction contract.** Deliver, in build order: **(1)** vendor `tape_microstructure.py`
**verbatim** (§7.2; only sanctioned edit `rels.ptp()`→`np.ptp(rels)`) + the **G1** function-parity gate (§7.6 —
the ≈15-token golden fixture within `1e-9`) (US-28); **(2)** the `feature_sets` table (§8) — a **hashed,
versioned** FeatureSet (ordered columns + math version) with the `live_servable`/`training_only` split (D2/D3)
(US-29); **(3)** the **shared deterministic extractor** — raw lake → vendored `compute_features`, **one code
path** for live+offline+replay (Principle #2), **leak-free** by the causal cutoff (reject `rel ≥ window_s`),
deterministic+versioned (US-30); **(4)** the **Feature Builder** — one-click CSV/parquet + **manifest** over the
lake, off the dedicated `celery` container (§6.5) (US-31); **(5)** the **T0/G2 source-parity gate** (§7.6, §16) —
Birdeye **live↔backfill** byte-parity (offline, no firehose) + a **Helius raw-truth** cross-check, wired as a
**hard merge gate** in the canonical `ci.yml` (US-32). Plus **process hardening** — mechanize phase-promotion
(F2) + the ruff gate (F4) + the clean HEAD deploy (F3) (US-33). **Firehose:** P5's offline gates (G1, G2(a))
need **no** activation; G2(b) raw-truth is at most **one** budgeted, logged Helius activation banking a durable
fixture (Helius 10→9), the gate offline-by-construction (8 Birdeye remain banked).

## Stories (sprint-7 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-28 | P5 — vendor `tape_microstructure.py` verbatim (§7.2) + G1 function-parity gate (§7.6) | high | US-17 | 3 | done | done | **approved** |
| US-29 | P5 — `feature_sets` table + deterministic hashed/versioned FeatureSet + live_servable/training_only split (§6.4.5, §8) | high | US-5, US-9 | 3 | done | done | **approved** |
| US-30 | P5 — shared deterministic extractor: raw lake → vendored `compute_features`, one code path, leak-free cutoff (§6.4.4) | high | US-28, US-29, US-19 | 3 | done | done | **approved** |
| US-31 | P5 — Feature Builder: one-click CSV/parquet + manifest off the `celery` container (§6.5) | high | US-30 | 3 | done | done | **approved** |
| US-32 | P5 — T0/G2 source-parity gate: Birdeye live↔backfill byte-parity + Helius raw-truth, hard merge gate (§7.6) | high | US-28, US-30, US-19, US-20 | 3 | done | done | **approved** |
| US-33 | Process — mechanize phase-promotion (F2) + ruff gate (F4) + clean HEAD deploy (F3) | high | US-13 | 3 | done | done | **approved** |

> **Scope:** sprint-7 commits **6 stories / 18 ACs** — the full P5 integrity core (US-28…US-32) plus the
> process-hardening carries (US-33: F2/F3/F4). Source of truth: [`sprint7.json`](sprint7.json). **Review
> outcome: all 6 stories Tester-approved (18/18 ACs); P5 is delivered** — the golden-parity gate (T0/G1/G2)
> now gates every PR by byte-identity and the Feature Builder produces the labs' CSV input. The project
> advances to **P6 (dashboard, §13)**. **F5** (the live Birdeye REST snapshot adapter) remains **deferred to
> sprint-8**, scheduled near where the scorer consumes it (carried as G2).

**Build order:** **US-28 first** — the vendored math everything downstream eats. **US-29** (FeatureSet table) and
**US-33** (process) are independent and may run in parallel. Then **US-30** (extractor) needs US-28 + US-29 (+ the
US-19 lake reader); then **US-31** (Feature Builder) needs US-30 and **US-32** (T0/G2 parity) needs US-28 + US-30.
The offline P5 gates (G1, G2(a)) do **not** depend on any live activation, so the firehose budget stays banked
except the single budgeted G2(b) Helius raw-truth fixture spend.

**GitHub Issues:** created at sprint-7 kickoff (2026-06-16), one per story, mirroring the prior convention —
[US-28 #121](https://github.com/AsimQuick/solanaTrilly/issues/121) ·
[US-29 #122](https://github.com/AsimQuick/solanaTrilly/issues/122) ·
[US-30 #123](https://github.com/AsimQuick/solanaTrilly/issues/123) ·
[US-31 #124](https://github.com/AsimQuick/solanaTrilly/issues/124) ·
[US-32 #125](https://github.com/AsimQuick/solanaTrilly/issues/125) ·
[US-33 #126](https://github.com/AsimQuick/solanaTrilly/issues/126).
Source of truth remains [`sprint7.json`](sprint7.json).

---

## Sprint-6 — Open P4 (Score-time Snapshot + units locked, §6.3 / D1) — REVIEW COMPLETE (closeout pending a clean re-deploy)
- **Phase:** done (corrected from a stale `planning` at review — see process note below)
- **Sprint plan (source of truth):** [`sprint6.json`](sprint6.json)
- **PRD:** [`PRD.md`](PRD.md) — delivers **P4 (score-time snapshot, §6.3) + units locked (D1)**
- **Review outcome:** **The full P4 score-time snapshot core is built, merged CI-green, and Tester-approved.** All 5 stories / 17 ACs are approved across PRs #108–#120 — the snapshot fetcher + orchestration behind the `DataSource` seam + injected clock, the three-unit (vol_sol/vol_usd/sol_usd) parity backbone locked (D1), and the deterministic P4 offline gate green. Sprint goal **met.** **One DoD item outstanding:** the clean final VPS deploy at true HEAD never landed — the sprint-end deploy **failed on a stale `phase: planning`** that tripped the US-13 guard (the third consecutive sprint for this self-inflicted slip), corrected to `done` in this review; a clean re-deploy is pending to close the VPS clause. See the Sprint-6 Review below + [`retrospective.md`](retrospective.md) (action items F1–F5). Project advances to **P5 (lake + extraction contract + vendored math + Feature Builder + T0/G1/G2 golden parity, §6.4 / §7.2)**.
- **Previous sprint:** [`sprint5.json`](sprint5.json) — **closed** (review complete; **P3 tape recorder delivered and live on the VPS**, detection→recorder proven live; project advanced to P4 — see Sprint-5 Review below + [`retrospective.md`](retrospective.md) action items E1–E5)
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P0–P3 are closed — the VPS staging stack is live on 8002, the config core (P1) is the single source of truth, P2 lands graduated `tokens`, and P3's recorder captures every PumpSwap swap from t0 into the immutable `jsonl.gz` lake + the queryable `swaps` mirror with live↔backfill parity. Retrospective **E4** directs **P4 (score-time snapshot + units locked, §6.3 / D1)** as the **primary deliverable** — the project's **only** non-tape live read (Principle #3): a single on-demand Birdeye REST snapshot per token at score time (holder distribution, mint/freeze authority, LP-burned flag, liquidity/TVL/depth — a first-class field per §1.1), plus locking the three-unit (vol_sol/vol_usd/sol_usd) parity backbone (D1). Process carries: **E1** (promote `phase`/`dev_status` *before* sprint-end deploys — the US-13 guard has now caught a stale-`phase` deploy two sprints running), **E2** (`ruff check --fix` inside the container as a pre-push checklist item), **E3** (exercise the live adapter against a banked real capture early), **E5** (the first sprint-6 deploy at true HEAD is a clean green run, closing the US-22 condition-B carry).

### Sprint Goal
**Open P4 — the score-time snapshot + units locked.** Build the §6.3 score-time read behind the `DataSource`
seam + injected clock (Principle #7), in build order: **(1)** the `snapshots` table (§8) + a typed score-time
snapshot schema with the **at-most-one-row-per-token** discipline (§6.3) and raw written via `JsonSafeEncoder`
(US-23); **(2)** the snapshot **fetcher** — **at most one** on-demand Birdeye REST read per token (the scheduled
per-token poll regime is **retired**), capturing holders/authority/LP-burned/liquidity raw, with the **#380**
future-window clamp and the Redis token-bucket limiter **kept** while the scheduler is **dropped** (US-24);
**(3)** score-time **orchestration** — on graduation, schedule **exactly one** snapshot at
`scoring.score_at_elapsed_s` read from `get_active_config()` (Principle #1), idempotent (at-most-once),
replay-testable, with **no** scheduled-polling task registered (H2) (US-25); **(4)** **units locked (D1)** — every
swap carries all three units with the relationship locked (`vol_usd ≈ vol_sol·sol_usd`), unit-invariant features
byte-identical across unit bases, CI-wired so it can't vanish (US-26); **(5)** the **P4 offline gate** (§16) —
deterministic `ReplaySource` snapshot replay → exactly the expected raw `snapshots` row (run twice →
byte-identical), raw-immutable re-derivability, and the #380-clamp / at-most-once / unit-parity regression suite
green in CI (US-27). The P4 gate is **offline and synthetic** — the score-time read is a single on-demand **REST**
snapshot (not a firehose WS activation), so **no firehose activation is required** this sprint (8 Birdeye / 10
Helius remain banked).

## Stories (sprint-6 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-23 | P4 — `snapshots` table + score-time snapshot schema: persistence target (§8, §6.3) | high | US-14, US-5 | 4 | done | done | **approved** |
| US-24 | P4 — score-time snapshot fetcher behind the `DataSource` seam: at-most-one on-demand Birdeye REST read + #380 clamp + token-bucket limiter (§6.3) | high | US-23, US-2, US-11 | 4 | done | done | **approved** |
| US-25 | P4 — score-time orchestration: schedule exactly one snapshot at `score_at_elapsed_s` from `get_active_config()`, idempotent, replay-testable (§6.3, Principle #1) | high | US-24, US-11, US-15 | 3 | done | done | **approved** |
| US-26 | P4 — units locked (D1): three-unit (vol_sol/vol_usd/sol_usd) invariant + unit-invariant feature parity backbone, CI-wired (§6.2 D1, §7.1) | high | US-17, US-18 | 3 | done | done | **approved** |
| US-27 | P4 — offline gate: deterministic `ReplaySource` snapshot replay + raw-immutable re-derivability + #380/at-most-once/unit-parity regression suite (§16, §6.4) | high | US-23, US-24, US-25, US-26 | 3 | done | done | **approved** |

> **Scope:** sprint-6 commits **5 stories / 17 ACs** — the full P4 score-time snapshot core (US-23…US-25) plus
> the D1 units-lock (US-26) and the P4 offline gate (US-27). Source of truth: [`sprint6.json`](sprint6.json).
> **Review outcome: all 5 stories Tester-approved (17/17 ACs)** — the pipeline can take one on-demand snapshot per
> token at score time and the three-unit parity backbone is locked. The project advances to **P5 (lake +
> extraction contract + vendored math + Feature Builder + T0/G1/G2 golden parity, §6.4 / §7.2)**.

> **Process note (the recurring stale-`phase` deploy trip — third sprint running):** the sprint-end deploy
> (run 27627161427) **failed at the US-13 integrity step** because `sprint6.json` still read `phase: planning`
> while every story was `done` — the same self-inflicted slip flagged as D2 (sprint-4) and E1 (sprint-5). The
> per-story deploys for US-23/24/25 passed (runs 27595669019 / 27597270397 / 27598463211), so the VPS *runs* the
> snapshot code, but the **clean final deploy at true HEAD is still pending** to close the DoD VPS clause.
> `phase` is corrected to `done` and story-level `dev_status` is normalized to `done` (D3) in this review pass;
> fix-at-source (automate phase-promotion in the deploy sequence) is carried as **F2**, the clean re-deploy as
> **F3**. The auto-generated [`sprint6.md`](sprint6.md) still renders `Dev Team Status: not-started` per story; the
> `.json` is authoritative.

**Build order:** **US-23 first** — the persistence target everything writes to. Then **US-24** (fetcher) →
**US-25** (orchestration) are sequential. **US-26** (units lock) is independent of the snapshot chain and may run
in parallel. **US-27** (offline gate) needs US-23/24/25/26. The offline P4 gate (US-27) does **not** depend on any
live activation, so the firehose budget stays banked.

**GitHub Issues:** created at sprint-6 kickoff (2026-06-16), one per story, mirroring the prior convention —
[US-23 #103](https://github.com/AsimQuick/solanaTrilly/issues/103) ·
[US-24 #104](https://github.com/AsimQuick/solanaTrilly/issues/104) ·
[US-25 #105](https://github.com/AsimQuick/solanaTrilly/issues/105) ·
[US-26 #106](https://github.com/AsimQuick/solanaTrilly/issues/106) ·
[US-27 #107](https://github.com/AsimQuick/solanaTrilly/issues/107).
Source of truth remains [`sprint6.json`](sprint6.json).

---

## Sprint-5 — Open P3 (the Tape Recorder) + prove detection live (D4) — CLOSED (review complete)
- **Phase:** review (closed)
- **Sprint plan (source of truth):** [`sprint5.json`](sprint5.json)
- **PRD:** [`PRD.md`](PRD.md) — delivers **P3 (tape recorder, §6.2)**, "the heart" of the pipeline
- **Review outcome:** **P3 delivered and live on the VPS.** US-17…US-21 fully Tester-**PASS** (17/17 ACs); **US-22 CONDITIONAL_PASS** (functionally DoD-complete; held at review on 2 process items — this retrospective, now written, and a clean re-deploy). Detection→recorder proven **live** end-to-end (8 real PumpSwap swaps on the VPS `listener`); first 2 firehose activations spent + banked. See the Sprint-5 Review below + [`retrospective.md`](retrospective.md) (action items E1–E5). Project advances to **P4 (score-time snapshot + units locked, §6.3 / D1)**.
- **Previous sprint:** [`sprint4.json`](sprint4.json) — **closed** (review complete; **P0 EXITED**, P2 detection delivered; project advanced to P3 — see Sprint-4 Review below + [`retrospective.md`](retrospective.md) action items D1–D5)
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P0/P1/P2 are closed — the VPS staging stack is live on 8002, the config core is the single source of truth, and P2 detection lands graduated `tokens` rows carrying `graduated_block_time` as the integer rel-anchor the recorder needs. Retrospective **D1** directs P3 (the PumpSwap swap-tape recorder — capture every swap from t0, Birdeye tape as the single source of truth for all signal) as the **primary deliverable**, and **D4** carries the live wiring (drive the `listener` with a concrete Birdeye source + bank the first firehose fixture). Process carries: **D2** (promote `phase`/`dev_status` *before* sprint-end deploys so the US-13 guard isn't tripped by our own staleness), **D3** (normalize story-level `dev_status` to `done` at closeout instead of exempting it), **D5** (standing rule — run the on-box check our root SSH allows before declaring a blocker "human/operator required").

### Sprint Goal
**Open P3 — the tape recorder.** Build the PumpSwap swap-tape recorder behind the `DataSource` seam + injected
clock (Principle #7): **(1)** the `swaps` table (§8) + the one `NormalizedSwap` schema (§7.1) with `rel`
anchored to the DB `Token.graduated_block_time`/`graduated_at` (never the first swap) and constrained
source/phase/side vocabularies (US-17); **(2)** the recorder **core** — port `tape_recorder.py`'s
queue/writer/window/coverage scaffolding source-agnostically, emit one `NormalizedSwap` per **landed** swap with
`owner`=the tx signer and all three units (D1), drop failed swaps, enforce the **stable** `(block_time, slot,
signature)` ordering (#403), keep the zero/degenerate-swap guard (#405) (US-18); **(3)** the append-only
daily-partitioned `jsonl.gz` lake + the queryable `swaps` writer + a truncated-tail-tolerant reader (US-19);
**(4)** recorder resilience — `seek_by_time` gap reconciliation via the **identical** call/path the backfill uses
(one code path → parity by construction) + idle-kill TTL deactivate-but-**re-attach**, config-driven (US-20);
**(5)** the **P3 offline gate** (§16) — deterministic `ReplaySource` swap-stream replay → expected swaps,
live↔backfill byte-parity on golden tokens, ordering/truncated-tail/zero-guard regression green (US-21). **Then**
**(D4)** wire a concrete Birdeye `SUBSCRIBE_TXS` source into `run_listener` on the VPS, spend the **first** of the
10 Birdeye firehose activations (logged, §15.7), prove a real graduation's swaps flow end-to-end, and **bank** the
capture as the golden-token fixture US-21 runs against offline forever (US-22).

## Stories (sprint-5 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-17 | P3 — `swaps` table + the one `NormalizedSwap` schema: tape persistence target (§8, §7.1) | high | US-14, US-5 | 4 | done | done | **PASS** |
| US-18 | P3 — tape recorder core behind the `DataSource` seam: emission, stable ordering, owner=signer, drop-failed, zero-guard (§6.2) | high | US-17, US-2, US-15 | 4 | done | done | **PASS** |
| US-19 | P3 — append-only `jsonl.gz` lake + queryable `swaps` writer + truncated-tail-tolerant reader (§6.2, §6.4) | high | US-18 | 3 | done | done | **PASS** |
| US-20 | P3 — recorder resilience: `seek_by_time` gap reconcile (one code path) + idle-kill TTL re-attach, config-driven (§6.2 D4) | high | US-18, US-11 | 3 | done | done | **PASS** |
| US-21 | P3 — the offline gate: deterministic replay + live↔backfill byte-parity on golden tokens + regression suite (§16) | high | US-18, US-19, US-20 | 3 | done | done | **PASS** |
| US-22 | P3/D4 — wire a live Birdeye `SUBSCRIBE_TXS` source into the `listener` + spend & bank the first firehose activation (§6.2, §15.7) | high | US-16, US-18, US-21 | 3 | done | done | **CONDITIONAL_PASS** |

> **Scope:** sprint-5 commits **6 stories / 20 ACs** — the full P3 tape-recorder core (US-17…US-21) plus the
> D4 live-wiring + firehose-fixture story (US-22). Source of truth: [`sprint5.json`](sprint5.json). On P3 exit,
> the recorder captures every PumpSwap swap from t0 into the lake/`swaps` table with live↔backfill parity, and
> the project advances to **P4 (score-time snapshot + units locked, §6.3 / D1)**.

**Build order:** **US-17 first** — the persistence target everything writes to. Then the core chain is sequential:
**US-18** (recorder core) → **US-19** (lake) → **US-20** (resilience). **US-21** (offline gate) needs US-18/19/20.
**US-22** (live D4) is **last** and depends on the `listener` (US-16) + the built recorder — and the offline P3
gate (US-21) **does NOT depend on the live activation**, so an aborted/short firehose window never blocks P3 exit.

**GitHub Issues:** created at sprint-5 kickoff (2026-06-15), one per story, mirroring the prior convention —
[US-17 #77](https://github.com/AsimQuick/solanaTrilly/issues/77) ·
[US-18 #78](https://github.com/AsimQuick/solanaTrilly/issues/78) ·
[US-19 #79](https://github.com/AsimQuick/solanaTrilly/issues/79) ·
[US-20 #80](https://github.com/AsimQuick/solanaTrilly/issues/80) ·
[US-21 #81](https://github.com/AsimQuick/solanaTrilly/issues/81) ·
[US-22 #82](https://github.com/AsimQuick/solanaTrilly/issues/82).
Source of truth remains [`sprint5.json`](sprint5.json).

---

## Sprint-4 — Exit P0 (for real), Open P2 (Detection) — CLOSED (review complete)
- **Phase:** review (closed)
- **Sprint plan (source of truth):** [`sprint4.json`](sprint4.json)
- **PRD:** [`PRD.md`](PRD.md) — finally exits **PRD §16 P0** (the GREEN VPS deploy) and delivers **P2 (detection, §6.1)**
- **Previous sprint:** [`sprint3.json`](sprint3.json) — **closed** (review complete; P1 config core delivered in code, US-8 deploy failed; see Sprint-3 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`
- **Why this sprint:** P1 (config core) is done; P0 is **still not exited** for a third consecutive sprint — the Sprint-3 deploy smoke-test failed with curl exit 7 on all 13 runs. The Sprint-3 review's caveat is the key lever: **the "firewall" diagnosis was never verified on the box, and CLAUDE.md grants agents root SSH** — so the on-box diagnosis *and* the fix (firewall `ufw allow 8002/tcp` **or** a compose port-bind correction) are agent tasks, not an operator blocker. Sprint-4 actions retrospective **C1** (diagnose on the VPS), **C2** (fix + green deploy + exit P0), **C3** (runtime smoke-test retry), **C4** (programmatic status-integrity CI guard), and **C6** (pull P2 detection in alongside the P0 closeout).

### Sprint Goal
Two halves, one milestone — **exit P0, open P2.** **(1) Close P0 (4th attempt):** SSH to the VPS and
diagnose port-8002 *on the box* (`curl localhost:8002/health`, `docker compose -p solanatrilly ps`,
compose port-bind, `ufw`/`iptables`) to separate a firewall block from a port-publish/bind bug; apply the
fix as root; make the CD smoke-test retry with backoff **at runtime**; run the deploy **green on `main`**
and verify **HTTP 200 on 8002 with solanaBilly untouched on 8001** — retroactively closing US-8
AC-8.3/8.4/8.5, US-6, and US-1's DoD, and finally **exiting P0** (US-12). Add a **programmatic
status-integrity CI guard** on `sprintN.json` (US-13). **(2) Open P2 (detection, PRD §6.1):** the `tokens`
model (US-14); a Birdeye `SUBSCRIBE_MEME` detection consumer **behind the `DataSource` seam + injected
clock** that creates `tokens` rows from the active config's detection filter, dedupes within
`dedupe_window_s`, and pre-stages near-graduation mints — **offline-gated by replaying a MEME stream →
expected token rows** (US-15); and detection resilience — the Helius `migrate` reconciler backstop (D4) +
a Birdeye REST sweep + a dedicated `listener` container (US-16).

## Stories (sprint-4 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-12 | P0 closeout (4th attempt) — diagnose+fix VPS port-8002 on the box, GREEN smoke-test, exit P0 (closes US-8 8.3/8.4/8.5 + US-6 + US-1 DoD) | high | US-8 | 5 | done | done | **approved** |
| US-13 | Process guard — programmatic status-integrity check on `sprintN.json` in CI (C4/A5/B4) | high | US-1 | 3 | done | done | **approved** |
| US-14 | P2 — the `tokens` model: graduated-token persistence target (§8) | high | US-1, US-5 | 3 | done | done | **approved** |
| US-15 | P2 — Birdeye `SUBSCRIBE_MEME` detection consumer behind the `DataSource` seam → `tokens` rows (offline gate) | high | US-14, US-11, US-2 | 4 | done | done | **approved** |
| US-16 | P2 — detection resilience: Helius `migrate` reconciler + Birdeye REST sweep + dedicated `listener` container | high | US-14, US-15 | 3 | done | done | **approved** |

> **Scope:** sprint-4 commits **5 stories / 18 ACs** — the P0 deploy closeout (US-12) + a process guard
> (US-13) + the P2 detection core (US-14…US-16). Source of truth: [`sprint4.json`](sprint4.json). **Review
> outcome: all 5 stories Tester-approved (18/18 ACs); P0 is EXITED** — the staging stack answers 200 on 8002,
> solanaBilly untouched on 8001 — and US-1's deploy-gated DoD closes retroactively; **P2 (detection) is
> delivered** and the project advances to **P3 (tape recorder)**. See the Sprint-4 Review below +
> [`retrospective.md`](retrospective.md) (action items D1–D5).

> **Status integrity note (the recurring A5/B4/C4 artifact):** `sprint4.json` still records story-level
> `dev_status: not-started` on all five completed stories while every AC is `dev_status: done` +
> `tester_status: approved`. US-13's CI guard exempts accepted stories (`--skip-complete` / story-done
> carve-out) so this no longer fails CI, but the field should be **promoted to `done` at closeout**, not
> exempted — carried as **D3**. The board above reflects the normalized (`done`/`approved`) state.

**Build order:** **US-12 first** — the gating P0 closeout (retro C2/B5); it is independent of the P2 chain
and may run in parallel. **US-13** (process guard) is independent. The P2 chain is sequential: **US-14**
(`tokens` model) → **US-15** (detection consumer + offline gate, needs the model + resolver + DataSource
seam) → **US-16** (reconciler + sweep + listener container, needs the consumer).

**GitHub Issues:** created at sprint-4 kickoff (2026-06-15), one per story, mirroring the prior convention —
[US-12 #54](https://github.com/AsimQuick/solanaTrilly/issues/54) ·
[US-13 #55](https://github.com/AsimQuick/solanaTrilly/issues/55) ·
[US-14 #56](https://github.com/AsimQuick/solanaTrilly/issues/56) ·
[US-15 #57](https://github.com/AsimQuick/solanaTrilly/issues/57) ·
[US-16 #58](https://github.com/AsimQuick/solanaTrilly/issues/58).
Source of truth remains [`sprint4.json`](sprint4.json).

---

## Sprint-3 — Exit P0, Open P1 (Config Core) — CLOSED (review complete)
- **Phase:** review (closed)
- **Sprint plan (source of truth):** [`sprint3.json`](sprint3.json)
- **PRD:** [`PRD.md`](PRD.md) — closes the last of **PRD §16 P0** (the CD deploy) and delivers **P1 (config core, §5)**
- **Previous sprint:** [`sprint2.json`](sprint2.json) — **closed** (review complete; 5/6 stories DoD-done, US-6 failed; see Sprint-2 Review below + [`retrospective.md`](retrospective.md))
- **Project state:** owned by Project Lead — `../project-state.json`
- **Review outcome:** P1 config core (US-9/10/11) **delivered in code, CI-green**; **P0 not exited** — US-8's VPS smoke-test fails (curl exit 7 on all 13 Deploy runs), so nothing is verified-live on 8002. Sprint **blocked** at the VPS DoD gate. See Sprint-3 Review below + [`retrospective.md`](retrospective.md) (action items C1–C6) → carried into sprint-4.

### Sprint Goal
Two halves, one milestone — **exit P0, open P1.** **(1) Close the last P0 blocker:** fix the CD deploy
that never reached the VPS — add `ssh … mkdir -p /root/solanatrilly` before the SCP (the target dir
doesn't exist on the box, so the SCP errors "No such file or directory") and a `workflow_dispatch`
trigger (fixes the orchestrator's HTTP 422) — then run it green on `main` and **verify the isolated
staging stack answers HTTP 200 on port 8002 with solanaBilly untouched on 8001**, retroactively closing
US-1's deploy-gated DoD (US-8; retrospective B1/B2/B3/B5). **(2) Deliver the P1 config core (PRD §5):**
the versioned, audited, admin-editable `PipelineConfig` model + `pipeline_state` singleton (US-9); a typed
Pydantic v2 schema that **rejects an invalid config at save time**, enforcing every §5.2 invariant (US-10);
and the single cached `get_active_config()` resolver with atomic activation + instant rollback and no
silent firehose/trading auto-start (US-11).

### Why this sprint now
P0 is *substantially* complete but **not exited**: sprint-2 built the CD pipeline yet a trivial
precondition (the `/root/solanatrilly/` directory is missing on the VPS) broke the deploy at the first
SCP, so **nothing is live on port 8002 for a second consecutive sprint** and US-1's DoD (retro A1) stays
open. The retrospective is explicit (B5): **carry US-6's failed ACs (6.3–6.5) into sprint-3 as the
top-priority closeout before any P1 work.** With P0 finally exited, the project advances to **P1 — the
config core**, the single versioned `PipelineConfig` source of truth every later phase (P2–P8) reads from.
Building the config-driven core early (Principle #1) keeps every tunable out of code constants and scattered
`os.getenv` — the regime churn (#314/#316/#402) the PRD warns against. This sprint also actions the carried
retrospective items: B1/B2 (deploy fix + `workflow_dispatch`), **B3 (VPS verification in the loop gates
"done")**, and **B4 (forbid `status: done` while a gate is failed/blocked; normalize stale fields)**.

## Stories (sprint-3 — committed scope)
| ID | Title | Priority | Deps | ACs | Status | Dev | Tester |
|----|-------|----------|------|-----|--------|-----|--------|
| US-8 | P0 closeout — CD deploy lands on the isolated VPS staging stack (closes US-6 6.3/6.4/6.5 + US-1 DoD) | high | US-6 | 5 | in-review | done | **fail** (AC-8.3/8.4/8.5) |
| US-9 | P1 — `PipelineConfig` model: versioned, audited, admin-editable + `pipeline_state` singleton | high | US-1 | 4 | in-review | done | partial (code pass; deploy gate) |
| US-10 | P1 — typed Pydantic v2 schema enforcing the save-time invariants (§5.2) | high | US-9 | 4 | in-review | done | partial (code pass; deploy gate) |
| US-11 | P1 — the config resolver: single cached `get_active_config()` + atomic activation/rollback | high | US-9, US-10 | 3 | in-review | done | partial (code pass; deploy gate) |

> **Status integrity note (retrospective B4 → C4):** `sprint3.json` still records `US-8 status: done` while `tester_status: fail`, and `dev_status: not-started` on completed stories — the same "done ≠ failed/blocked" inconsistency flagged in sprint-1 (A5) and sprint-2 (B4). The board above reflects the *normalized* state (US-8 `in-review`/`fail`, US-9/10/11 `in-review`/`partial`); the `sprintN.json` normalization is owned by PO / Project Lead and is carried as **C4**.

> **Scope:** sprint-3 commits **4 stories / 16 ACs** — the P0 deploy closeout (US-8) + the P1 config core
> (US-9…US-11). Source of truth: [`sprint3.json`](sprint3.json). After US-8 deploys green and is VPS-verified,
> P0 is **exited** and US-1's deploy-gated DoD closes retroactively; after US-9…US-11, **P1 (config core) is
> delivered** and the project advances to **P2 (detection)**.

**Build order:** **US-8 first** — the top-priority P0 closeout (retro B5); it is independent of the P1 chain
and may run in parallel, but P0 exit is the sprint's gating milestone. The P1 chain is sequential:
**US-9** (model + audit + admin + `pipeline_state`) → **US-10** (Pydantic schema, needs the model) →
**US-11** (resolver + activation, needs the model + schema).

**GitHub Issues:** created at sprint kickoff (2026-06-15), one per story, mirroring the sprint-2 convention —
[US-8 #34](https://github.com/AsimQuick/solanaTrilly/issues/34) ·
[US-9 #35](https://github.com/AsimQuick/solanaTrilly/issues/35) ·
[US-10 #36](https://github.com/AsimQuick/solanaTrilly/issues/36) ·
[US-11 #37](https://github.com/AsimQuick/solanaTrilly/issues/37).
Source of truth remains [`sprint3.json`](sprint3.json).

## Definition of Done (sprint-3)
A story is Done only when ALL of the following hold:
- All ACs verified by CI / Tester
- No critical defects
- Coverage threshold met (≥80%)
- Code file headers include metadata front matter (project convention)
- All services run in Docker (no host installs — Docker Rules); the web image is **rebuilt** after the
  `requirements.txt` change (`django-simple-history`) and the **VPS pulls the new image**
- **CD pipeline is now LIVE (US-8 closes the US-6 gap): every story is merged + deployed to the VPS solanatrilly staging stack (`-p solanatrilly`, port 8002) and smoke-tested there** ("works locally" is NOT done). **Per retrospective A2, the deploy clause is gated at the _sprint_ boundary.**
- **VPS verification is in the loop and gates "done" (retrospective B3):** a green structural pytest is **not** a deployed stack — the Tester confirms the running stack answers 200 on 8002 and solanaBilly is untouched on 8001 from the actual deploy run.
- Hard isolation from live solanaBilly preserved (every docker command scoped with `-p solanatrilly`; solanaBilly on port 8001 untouched)
- **Status integrity enforced (retrospective B4):** no story/AC reads `status: done` while its `tester_status` is `failed`/`blocked`; stale `phase`/`dev_status` fields are normalized at review.
- `retrospective.md` updated for sprint-3 — **named owner: Tester / scrum facilitator** (retrospective A3)

## Sprint-7 Review — Summary (2026-06-17)
**Phase:** done | **Committed scope:** US-28…US-33 (6 stories, 18 ACs) | **Goal:** **met — P5 (integrity core) delivered; process carries F2/F3/F4 all closed**
**Full retrospective + action items (G1–G4):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 6 stories / 18 ACs implemented, merged CI-green, and Tester-approved** across PRs #127–#145. The **P5 integrity core (§6.4) is delivered** — the thing that makes a model's live score **equal** its offline score *by construction*, the fix for the evidenced solanaBilly disaster (23% live vs 78% offline precision purely from feature-assembly drift, #358/#359/#367). **And for the first time the three recurring process scars were closed mechanically, not by checklist:** F2 (phase-promotion automated), F4 (ruff gate mechanized), and F3 (the clean final HEAD deploy ran GREEN) — the stale-`phase` deploy trip that failed three consecutive sprints (D2→E1→F2) is now structurally prevented. The project advances to **P6 (dashboard, §13)**.

**US-28 — the vendored math + the G1 function-parity gate.** `solanabilly3/src/tape_microstructure.py` is copied **verbatim** into `core/tape_microstructure.py` with the **one** sanctioned edit (`rels.ptp()`→`np.ptp(rels)` for numpy 2.x), preserving `compute_features(swaps, window_s=120, bucket_s=15) -> dict|None` and the full `tape_*` family; a metadata header documents the re-vendor rule (never hand-edit). The G1 gate runs the frozen ≈15-token golden fixture through the vendored math and asserts every feature within **1e-9** absolute, with the None-on-no-usable-swap contract verified (empty / out-of-window / zero-price / missing-price all → `None`, never a zero row) and the test wired into the canonical `ci.yml` `test` job via a compile-time `ImportError` trap (AC-21.3 pattern) so it cannot silently vanish.

**US-29/30 — the extraction contract + the one shared extractor (Principle #2).** US-29 ships the `feature_sets` table (hashed config = ordered `columns[]` + `math_version`; same inputs → same SHA-256 hash, reorder or bump → different hash) with the `live_servable ⊆ columns` split (D2/D3; `tape_max_drawdown` is the designated training-only column) and a read-only admin. US-30 is the **single `FeatureExtractor`** that reads normalized swaps from the lake *and* the `swaps` mirror, orders by the stable `(block_time, slot, signature)` key, feeds the §7.1 subset to the vendored `compute_features`, and proves **byte-identical** feature dicts from the DB path and the `jsonl.gz` lake path for the same mint — the Principle #2 proof point that closes the assembly-drift class by construction. It is **leak-free by the causal cutoff** (swaps with `rel ≥ window_s` hard-rejected; boundary cases tested at `==`, `>`, `==window_s−ε`), stamps `_window_s` / `_feature_set_hash` / `_math_version`, and is **run-twice byte-identical**.

**US-31/32 — the Feature Builder + the T0/G1/G2 hard merge gate.** US-31's `build_features` Celery task runs the **shared** US-30 extractor over the lake → CSV/parquet + an 8-field manifest (FeatureSet version+hash, sources, date range, cohort, row count, content hash, label def), **run-twice → byte-identical content hash**; it is registered (H2 manifest), runs off the `celery-worker` container (NEVER web/gunicorn — the #289 lesson) defined in **both** compose files, and **rejects at submission time** any label def drawing from within `[0, window_s)` (leak-free labels). US-32 is the **#1 merge gate**: G2(a) Birdeye live↔backfill byte-identity (swap-level **and** feature-level) on the banked sprint-5 golden token via an on-demand `seek_by_time` REST read (no firehose), G2(b) the Helius raw-truth cross-check over a banked fixture (100% signature coverage, 100% side parity, amount parity within tolerance), and the combined T0/G1/G2 suite trapped via `ImportError` on 5 named functions in the single canonical `ci.yml` job (H1). Coverage 88%.

**US-33 — the process scars, mechanized.** **F2:** `tools/promote_sprint_phase.py` auto-promotes a stale `phase` (planning/in-progress + all stories done → `complete`) or blocks the deploy with a printed REMEDY, reproducing and closing the exact D2→E1→F2 failure (23 tests). **F4:** a `Makefile lint` target + `scripts/pre-commit.sh` + `install-hooks.sh` run `ruff check` inside the container and exit 1 on I001/E501/F401 — the churn that recurred five sprints. **F3:** the deploy workflow gained a "Verify listener container Up" step, and the final deploy (run 27654478462) confirmed **200 on 8002 (attempt 2/12 — the AC-12.3 retry-with-backoff working), listener Up, solanaBilly untouched on 8001** — closing the carried sprint-6 DoD VPS clause (re-issue of E5 / US-22 condition B), open since sprint-5.

**Firehose.** **One** budgeted Helius activation spent for G2(b) — the single deliberate, time-boxed, ledgered (`ops/firehose_activation_log.md`) read that banked the durable raw-truth fixture (40 txs, 23 buy / 17 sell); **Helius 10→9**. The CI cross-check now runs **offline** against that fixture forever and never depends on a live read. The offline G1 + G2(a) gates spent **zero** activations. **8 Birdeye remain banked.**

**Process notes — the irony worth recording.** The same ruff pattern (F401 AC-28.2, I001 AC-29.1, E501 AC-31.3) still surfaced *during the very sprint that mechanized the gate* — because the F4 pre-commit hook is developer-**opt-in** (must run `make install-hooks`) and CI ruff is the backstop that caught all three. Each was a one-line fix in ≤1 iteration; none reached pytest or production. The hook is built but not yet unforgeable (carried as G3). Otherwise: F2/F3 worked exactly as designed — the sprint-end deploy was clean for the first time in three sprints.

**Defects:** lint-only (F401, I001, E501), each caught by CI/ruff and fixed in 1 iteration; none reached pytest or production. Coverage 88% vs. the ≥80% gate; 886 tests passing.

**Carry into sprint-8 (priority order):** **G1** open **P6 (dashboard, §13)** — and per the board's standing note, **read `oracle-direction.md` before planning** (the road P5 → promote `trilly_pregrad_v3_2` + start the firehose: a Helius program-wide birth-tape source, a two-tier idle TTL, the 15-booster rank-blend serving path) · **G2** build + prove the deferred **F5** live Birdeye REST `SnapshotDataSource` adapter near where the scorer consumes it · **G3** make the F4 ruff hook **unforgeable** (auto-install or container-entrypoint) so it isn't opt-in · **G4** confirm the F2 promoter is wired into the orchestrator's actual deploy sequence (proven on the next sprint-end deploy), not just a standalone tested tool. See `retrospective.md` G1–G4.

**Metrics:** 6 stories committed · **6 fully Tester-approved** (18/18 ACs) · **P5 delivered** · PRs #127–#145, CI `test` green at merge · final HEAD deploy **GREEN** (run 27654478462; 200 on 8002 at attempt 2/12, listener Up, solanaBilly untouched on 8001) — the carried F3/E5 VPS clause **closed** · **1 firehose activation** (Helius **10→9**, G2(b) raw-truth fixture banked; 8 Birdeye banked) · 3 lint defects (F401/I001/E501) caught by CI and fixed in 1 iteration each · 886 tests · coverage 88% (≥80% gate). Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-6 Review — Summary (2026-06-16)
**Phase:** done (corrected from a stale `planning` at review) | **Committed scope:** US-23…US-27 (5 stories, 17 ACs) | **Goal:** **met — P4 (score-time snapshot + units locked) delivered**
**Full retrospective + action items (F1–F5):** [`retrospective.md`](retrospective.md)

**Outcome:** **The full P4 score-time snapshot core is built, merged CI-green, and Tester-approved.** All 5 stories / 17 ACs are approved across PRs #108–#120. The pipeline can now take **exactly one on-demand Birdeye REST snapshot per token at score time** — the only non-tape live read in the whole pipeline (Principle #3) — for what the tape cannot give (holder distribution, mint/freeze authority, the LP-burned flag, liquidity/TVL/depth as a first-class field, §1.1), and the **three-unit (vol_sol/vol_usd/sol_usd) parity backbone is locked (D1)**. Sprint goal **met.** **One DoD item outstanding:** the clean final VPS deploy at true HEAD never landed — the sprint-end deploy failed on a stale `phase: planning` that tripped the US-13 guard (third consecutive sprint); a clean re-deploy is pending. The project advances to **P5 (lake + extraction contract + vendored math + Feature Builder + T0/G1/G2 golden parity, §6.4 / §7.2)**.

**US-23/24/25 — the score-time snapshot core, behind the live/replay seam (Principle #7).** US-23 ships the `snapshots` table (§8: `mint`/`taken_at`/`elapsed_s`/`raw` JSONB) with at-most-one-row-per-token (`unique=True` + `update_or_create`), the `raw` JSONField written via `JsonSafeEncoder` (H3/US-5 guard green), a single typed `SnapshotSchema` (7 captured fields), and a read-only Django admin. US-24 is the `SnapshotFetcher` reading from a `SnapshotDataSource` + injected `Clock` — **at most one** on-demand read per token, the **#380** future-window clamp (`min(as_of, now)` so no future window leaves the fetcher), and the Redis token-bucket limiter **kept** while the scheduled per-token poll regime is **retired** (H2 manifest confirms no periodic snapshot task); the US-2 static-analysis guard (no concrete-source import, no `time.time()`/`datetime.now()` on the core path) held throughout. US-25's `ScoreTimeOrchestrator` schedules **exactly one** snapshot at `scoring.score_at_elapsed_s` read from `get_active_config()` (Principle #1, not a constant/`os.getenv`), idempotent **end-to-end** — AC-25.2 added a DB-level `Snapshot.objects.filter(mint).exists()` guard so a **listener restart** with a fresh fetcher won't re-hit the DataSource — and replay-testable via `ReplaySnapshotSource`.

**US-26/27 — units locked (D1) + the deterministic offline gate.** US-26 locks the three units with `REL_TOL = 1e-6` as a *named, documented* constant (the Tester's AC-26.1 amendment), all three fields non-null/non-zero, `vol_usd ≈ vol_sol·sol_usd` within tolerance, and unit-invariant aggregates (buy-fraction / flow-ratio / per-trader-share) byte-identical whether derived via SOL or USD — wired into the canonical `ci.yml` `test` job via a compile-time `ImportError` trap (AC-21.3 pattern). US-27 is the P4 offline gate: replaying a schema-faithful synthetic Birdeye snapshot through `ReplaySnapshotSource` + virtual clock stores exactly the expected raw `snapshots` row, **run-twice byte-identical** (`json.dumps(..., sort_keys=True)` after row deletion); raw = verbatim immutable truth, re-derivable without a second fetch; and the #380-clamp / at-most-once / unit-parity regression suite is trapped into the single canonical job on 8 named functions so it cannot silently vanish. **The gate is offline and synthetic — 0 firehose activations spent; 8 Birdeye / 10 Helius remain banked.**

**Forward design paid off.** Several ACs were **test-only** (AC-23.2, AC-26.1/26.2/26.3, AC-27.2/27.3): the `Snapshot` model already carried `unique=True` + `JsonSafeEncoder` (AC-23.1) and `Swap`/`NormalizedSwap` already carried all three units (P3), so P4 was largely proving and trapping invariants the P1/P3 groundwork had anticipated, not retrofitting them.

**Process notes.** **E1/D2 recurred a THIRD consecutive sprint** — the sprint-end deploy (run 27627161427) **failed at the US-13 integrity step** on stale `phase: planning` while all stories were `done`; the per-story deploys for US-23/24/25 passed (runs 27595669019 / 27597270397 / 27598463211), so the VPS runs the snapshot code, but the clean final deploy at HEAD is still pending. The guard works; the habit it keeps catching does not — fix-at-source (automate phase-promotion) is carried as **F2**, the clean re-deploy as **F3**. **D3 actioned again** — story-level `dev_status` normalized to `done` at closeout. **E2 recurred a fifth time** — AC-23.3 shipped an unused `import math` (F401), caught by CI/ruff, fixed in one iteration; mechanize the ruff gate is carried as **F4**. **E3 partial** — the seam + synthetic gate are delivered, but the concrete Birdeye REST `SnapshotDataSource` (the live client) was not built this sprint; build + prove it against a banked real REST snapshot is carried as **F5**. Two minor boundary slips: AC-24.4's second commit (`0f7c8c7`) modified orchestrator-owned `sprint6.md`, and US-26 bundled AC-26.2/26.3 into AC-26.1's commit, leaving empty feature branches that tripped the No-Commits guard (work is real and merged in PR #119).

**Defects:** lint-only (AC-23.3 F401), caught by CI and fixed in 1 iteration; none reached pytest or production. Coverage 86.53% vs. the ≥80% gate; 758 tests passing.

**Carry into sprint-7 (priority order):** **F1** open **P5** (lake + extraction contract + vendored `tape_microstructure.py` + Feature Builder + T0/G1/G2 golden parity) · **F2** automate phase-promotion in the deploy sequence — the stale-`phase` trip is now three sprints running and a checklist has not fixed it · **F3** trigger + confirm the clean final deploy at true HEAD and close the sprint-6 DoD VPS clause (re-issue of E5) · **F4** mechanize the ruff gate (pre-commit/entrypoint hook), not a checklist · **F5** build + prove the live Birdeye REST snapshot adapter against a banked real snapshot before the scorer needs it. See `retrospective.md` F1–F5.

**Metrics:** 5 stories committed · **5 fully Tester-approved** (17/17 ACs) · P4 delivered · PRs #108–#120 (13), CI `test` green at merge · per-story deploys **GREEN** (US-23/24/25) — sprint-end HEAD deploy **FAILED** (run 27627161427) on stale `phase` (D2/E1 third recurrence, corrected to `done`) · **0 firehose activations** (8 Birdeye / 10 Helius banked) · 1 lint defect (AC-23.3 F401) caught and fixed in 1 iteration · 758 tests · coverage 86.53% (≥80% gate). Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-5 Review — Summary (2026-06-16)
**Phase:** review | **Committed scope:** US-17…US-22 (6 stories, 20 ACs) | **Goal:** **met — P3 (tape recorder) delivered; detection→recorder proven LIVE**
**Full retrospective + action items (E1–E5):** [`retrospective.md`](retrospective.md)

**Outcome:** **The full P3 tape recorder is built, merged CI-green, and live on the VPS.** US-17…US-21 are fully Tester-**PASS** (17/17 ACs); **US-22 is CONDITIONAL_PASS** — its 3 ACs are functionally DoD-complete and the live end-to-end proof is confirmed on the box, but the story was held at review on two **process** items: (A) the sprint-5 retrospective (DoD item — now written) and (B) a clean final deploy after a stale-`phase` slip tripped the US-13 guard. The recorder captures every PumpSwap swap from t0 into an immutable `jsonl.gz` lake + a queryable `swaps` mirror, with `rel` anchored to the DB `Token.graduated_block_time`. The project advances to **P4 (score-time snapshot + units locked, §6.3 / D1)**.

**US-17/18/19/20/21 — the P3 core, behind the live/replay seam (Principle #7).** US-17 ships the `swaps` table + the one `NormalizedSwap` schema (constrained source/phase/side vocabularies; `rel` anchored to `graduated_block_time` on both sides). US-18 is the source-agnostic recorder core — one `NormalizedSwap` per **landed** swap, `owner`=signer, all three units (vol_sol/vol_usd/sol_usd), failed swaps dropped, **stable** `(block_time, slot, signature)` ordering (#403), and an explicit `DEGENERATE_SWAP_POLICY="skip"` zero-guard (#405) — with the US-2 static-analysis guard (no concrete-source import, no `time.time()`/`datetime.now()` on the core path) green throughout. US-19 adds the append-only daily-partitioned `jsonl.gz` lake (raw = immutable truth, §6.4.1), the `update_or_create`-idempotent `swaps` writer (no dup on `(mint, signature)`), and a truncated-tail-tolerant reader (the recovered-161k-rows scaffolding). US-20 is **parity by construction**: `GapReconciler` *is* the `TapeRecorder` backfill path (AST-guarded — no direct `from_raw_swap`), so a live-gap reconcile and a pure backfill are byte-identical, plus config-resolved idle-kill TTL re-attach reading `idle_kill_ttl_s` from `get_active_config()` (Principle #1). US-21 is the deterministic offline gate — `ReplaySource` replay yields exactly the expected swaps/`jsonl.gz` (run-twice byte-identical), live↔backfill byte-parity on golden tokens, and the #403/#405/truncated-tail regression suite wired into `ci.yml` via a compile-time `ImportError` trap so it cannot silently vanish.

**US-22 — detection→recorder proven LIVE (D4), first firehose spends banked.** A concrete `BirdeyeSwapSource` (`SUBSCRIBE_TXS`) + `WallClock` are wired into `run_listener` **only** in the adapter layer (core path untouched, US-2 guard green). Two deliberate, time-boxed firehose activations were spent (**Birdeye 10→9→8**, Helius untouched): the first banked a 40-swap golden fixture (`E6ifp2…pump`, dt=2026-06-15); the second drove the end-to-end live proof — **8 real PumpSwap swaps for `H9L9…pump` flowed Birdeye → recorder → `swaps` rows + `jsonl.gz` on the VPS `listener`**, Tester-confirmed on the box via `psql … count(*) = 8` (scoped `-p solanatrilly`). AC-22.3 had to build the previously-missing live ingestion layer (`birdeye_swap_mapper`, mapped/bounded sources, a WS-handshake fix, a recorder None-reserve guard) — the offline gate's synthetic fixtures had masked that the live adapter didn't yet exist (carried as E3).

**Process notes.** **D3 actioned** — story-level `dev_status` is normalized to `done` at closeout in `sprint5.json` (first time in five sprints the A5/B4/C4/D3 artifact is fixed at the source, not exempted via `--skip-complete`). **D2 recurred** — the final doc-only commit (`ddb203b`) deploy (run 27593136555) **failed** at the US-13 integrity step because `phase` still read `planning` while all stories were `done`; the prior deploy at `d8a9e18` (run 27592748809) was fully green, so the VPS runs the AC-22.3 code. `phase` corrected to `review` at this review; fix-at-source carried as **E1**. The recurring `I001`/`E501` lint slips (AC-17.1/17.2/19.3/22.2) are carried as **E2**. The auto-generated `sprint5.md` lags the normalized `.json` state (`.json` is authoritative).

**Defects:** lint-only (`I001`/`E501`), each caught by CI and fixed in ≤2 iterations; none reached pytest or production. Coverage ≥80% enforced throughout.

**Carry into sprint-6 (priority order):** **E1** promote `phase`/`dev_status` *before* the sprint-end deploy (D2, now twice guard-caught) · **E2** `ruff check --fix` inside the container as a pre-push checklist item · **E3** exercise the live adapter against a banked real capture *early* (synthetic parity ≠ a working live adapter; it cost a second firehose spend) · **E4** open **P4 (score-time snapshot + units locked, §6.3 / D1)** · **E5** trigger + confirm a clean final deploy at true HEAD and flip US-22 → PASS. See `retrospective.md` E1–E5.

**Metrics:** 6 stories committed · **5 fully Tester-PASS** (US-17…US-21) · **1 CONDITIONAL_PASS** (US-22 — functionally DoD-complete, held on 2 process items) · 20/20 ACs implemented + merged + CI-green · PRs #83–#102 + the AC-22.3 series, CI `test` green at merge (AC-22.3 red at `2f906c5`/`c24a002`, green at `d8a9e18`) · deploy `d8a9e18` **GREEN** (run 27592748809; 200 on 8002, listener Up, solanaBilly untouched on 8001) — final doc-only `ddb203b` deploy failed on stale `phase` (D2 slip, corrected) · **2 firehose activations spent** (Birdeye 10→9→8; Helius 10 banked), both logged with durable fixtures · coverage ≥80%. Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-4 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-12 + US-13 + US-14/US-15/US-16 (5 stories, 18 ACs) | **Goal:** **fully met — P0 EXITED, P2 delivered**
**Full retrospective + action items (D1–D5):** [`retrospective.md`](retrospective.md)

**Outcome:** **All 5 stories are fully Tester-approved — 18/18 ACs green across 18 merged PRs.** After three sprints of P0-deploy drag,
**P0 IS EXITED**: the isolated staging stack answers **HTTP 200 on port 8002** with solanaBilly untouched on **8001**, confirmed by the Tester
from an actual green deploy run (run 27555701056). **US-1's deploy-gated DoD (retro A1, open since sprint-1), US-6, and US-8 AC-8.3/8.4/8.5
all close retroactively.** **P2 (detection) is delivered** and the project advances to **P3 (tape recorder)**.

**US-12 — P0 closeout, the on-box diagnosis cracked it (C1/C2/C3).** SSH'ing to the VPS (root, per CLAUDE.md) **falsified the three-sprint
"firewall" story**: no ufw, iptables INPUT ACCEPT-all, `0.0.0.0:8002->8000/tcp` correctly published, HTTP 200 both locally and externally. The
real root cause was a **smoke-test timing race** — the curl fired immediately after `docker compose up -d`, hitting the container mid-startup
(migrations before daphne binds). The fix is runtime retry-with-backoff (AC-12.3, `SMOKE_MAX_ATTEMPTS`/`SMOKE_RETRY_DELAY`), with the structural
test upgraded to assert *runtime* loop/sleep/max-attempts behavior in the parsed YAML — closing the sprint-2/3 B3 "green test ≠ live runtime" gap.
The "human escalation required / agents lack firewall access" claim that gated three sprints was simply **wrong**.

**US-13 — the status-integrity guard proved itself live (C4).** `tools/sprint_integrity_check.py` runs in CI on every PR over all `sprint*.json`,
forbidding `done`+`failed/blocked` and flagging stale `dev_status`/`phase`. It **immediately caught a real violation**: the orchestrator's
sprint-end re-deploys (runs 27555736146, 27555822147) failed at the integrity step because `sprint4.json` still read `phase:'planning'` while
every story was done. The "done ≠ failed/blocked" class logged-and-unactioned in A5/B4/C4 is now a permanent, self-enforcing gate.

**US-14/15/16 — P2 detection, behind the live/replay seam, budget preserved.** US-14 ships the `tokens` model (PK `mint`, `graduated_at` t0,
`graduated_block_time` integer rel-anchor for the P3 recorder, constrained status vocabulary, `JsonSafeEncoder` raw lake, admin surface). US-15's
consumer reads MEME events from a `DataSource` + injected clock (US-2 static-analysis guard still green), writes `tokens` rows from the
`get_active_config()` detection filter (Principle #1 — no constant/`os.getenv`), dedupes within `dedupe_window_s`, and pre-stages near-graduation
mints — with a **deterministic offline replay gate** on a synthetic MEME stream (run twice → identical rows). US-16 adds three-belt resilience:
the Helius `migrate` reconciler (recovers a mint a dropped MEME event missed, exactly once via `get_or_create`), a registered Birdeye REST
graduation sweep (Celery-beat, H2 manifest-guarded), and a dedicated `listener` container in both compose files, up on the VPS (#289 lesson).
**No firehose activation was spent** — the offline gate is synthetic, so 10 Birdeye + 10 Helius remain banked.

**Defects:** only 3 trivial lint slips (ruff E741 AC-12.5 ambiguous `l`; F541 AC-15.2 empty f-string; E501 AC-15.4 long header) — each caught by
Tester diagnosis and fixed same-day in one iteration. None reached pytest or production. Coverage ≥80% enforced throughout.

**Process note (the recurring artifact, now caught by the guard).** Story-level `dev_status` still reads `not-started` on all five done stories;
US-13's `--skip-complete`/story-done carve-out exempts accepted stories from CI rather than normalizing the field — promote to `done` at closeout
(D3). The sprint-end deploy failures were a *process* slip (orchestrator didn't update `phase` before triggering) that the new guard correctly
caught — fix at the source (D2). Normalized in this review pass (`phase` → `review`).

**Carry into sprint-5 (priority order):** **D1** open **P3 (tape recorder)** — the `graduated_block_time` rel-anchor is in place · **D2** update
`phase`/`dev_status` *before* sprint-end deploys so the US-13 guard isn't tripped by our own staleness · **D3** normalize story-level `dev_status`
at closeout instead of exempting it · **D4** wire a live Birdeye/Helius source to the `listener` and bank a firehose fixture (prove detection live,
not just offline) · **D5** bank the "verify on the box before escalating to human-required" rule. See `retrospective.md` D1–D5.

**Metrics:** 5 stories committed · **5 fully DoD-done** · **18/18 ACs Tester-approved** · 18 PRs merged, all CI-green · post-merge deploy **GREEN**
(run 27555701056) — **first verified-live VPS presence** (200 on 8002, isolation on 8001) · two later sprint-end re-deploys failed at the US-13
integrity step on stale `phase` (process slip, not a code regression) · 3 trivial lint defects caught and fixed same-day · **0 firehose activations
spent** (10 Birdeye + 10 Helius banked) · ~430+ tests passing · coverage ≥80%. Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-3 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-8 + US-9/US-10/US-11 (4 stories, 16 ACs) | **Goal:** half met — P1 opened, P0 not closed
**Full retrospective + action items (C1–C6):** [`retrospective.md`](retrospective.md)

**Outcome:** All **16 ACs are implemented and CI-green** across 16 merged PRs (#38–#53), and the **P1 config core is fully delivered in code**:
**US-9** (`PipelineConfig` model + django-simple-history audit + Django admin + `pipeline_state` singleton), **US-10** (typed Pydantic v2
schema enforcing every §5.2 invariant — leak guard, D4, `capture_buffer_s ≥ 3`, id22 `adaptive_topk`-only gate, D2 feature-contract subset
— as **write-path** rejection gates), and **US-11** (single cached `get_active_config()` resolver + atomic activation/rollback + an AST
no-silent-auto-start guard). ~290 tests, coverage ≥80%. This is the config-driven source of truth (Principle #1) that P2–P8 read from.

**US-8 — FAILED (P0 not exited).** AC-8.1 (`mkdir -p /root/solanatrilly` before SCP) and AC-8.2 (`workflow_dispatch`, resolving the HTTP 422)
are **PASS** — sprint-2's SCP blocker is closed and **the deploy now actually deploys**: SSH connects, the GHCR image pulls, `docker compose
-p solanatrilly … pull && up -d` runs, and the web container **starts on the VPS**. But **AC-8.3/8.4/8.5 FAIL**: the CD smoke-test (a curl
from the GitHub runner to `http://VPS:8002/health/`) returns **curl exit code 7** on all **13** Deploy runs, so 200-on-8002 is never proven,
the isolation step (gated behind the smoke-test) never runs, and the Tester cannot confirm P0 exit. US-9/10/11 are therefore rated **partial**
(code correct, CI-green, containers deploy — only the sprint-boundary deploy DoD remains).

**DoD status — deploy clause NOT met (third sprint).** Per A2 the deploy clause is gated at the sprint boundary; because the smoke-test never
returns 200, **nothing is verified-live on port 8002** and **US-1's deploy-gated DoD (A1) stays open** — the same "no VPS presence" gap as
sprints 1 and 2, but one layer deeper each time (no pipeline → SCP fails → container starts but port unreachable).

**Diagnosis caveat (B3 not truly in the loop).** curl exit 7 was attributed to a closed port-8002 firewall, but **no one SSH'd to the VPS to
`curl localhost:8002` and rule out a port-publish/bind bug** in `docker-compose.staging.yml`. Firewall (operator action) vs. port-mapping
(code fix) have different remedies; agents have VPS SSH per CLAUDE.md, so the on-box check is owed before escalating. Also: AC-8.5's structural
test asserts a smoke-test retry loop, yet the most recent run shows curl failing immediately with **no retry** — a green test masking a
divergent runtime, the exact sprint-2 B3 lesson recurring.

**Process note (B4 not enforced — third time).** `sprint3.json` reads `US-8 status: done` while `tester_status: fail`, `dev_status:
not-started` on completed stories, and `phase: planning`-era staleness — the "done ≠ failed/blocked" inconsistency A5/B4 flagged twice.
Also, "No open human dependencies for sprint-3" (asserted at kickoff) proved false: a probable operator firewall action emerged at review.

**Carry into sprint-4 (priority order):** **C1** diagnose port-8002 *on the VPS* (curl localhost, `docker compose -p solanatrilly ps`, check
the compose port publish/bind, inspect `ufw`/`iptables`) to separate operator-fix from code-fix → **C2** apply it, re-run the deploy, confirm
200 on 8002 + isolation on 8001 (closes US-8/US-6 and retroactively US-1's DoD; exits P0 — fourth attempt) · **C3** make the smoke-test
actually retry with backoff and have the structural test verify runtime, not file text · **C4** enforce status integrity programmatically
(forbid `done` + `failed`/`blocked`; normalize fields; JSON-lint in CI) · **C5** reopen the human-dependency line in `po-requests.md` if C1
confirms a firewall · **C6** don't let the deploy drag stall throughput — pull **P2 (detection)** into sprint-4 alongside the P0 closeout.
See `retrospective.md` C1–C6.

**Metrics:** 4 stories committed · 0 fully DoD-done · 3 code-complete/CI-green but Tester-`partial` (US-9/10/11) · 1 failed (US-8 deploy ACs) ·
16/16 ACs CI-green / 13 Tester-pass / 3 fail (US-8 AC-8.3/8.4/8.5) · 16 PRs merged (#38–#53), all CI-green · 13 Deploy runs, **0 passed
smoke-test** (curl exit 7) · 2 lint defects (US-8 AC-8.3 F841; US-9 AC-9.3 I001) caught by CI and fixed pre-merge · ~290 tests passing ·
**0 verified-deployed to VPS** (third sprint). Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-2 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-2…US-7 (6 stories, 22 ACs) | **Goal:** substantially met — one blocker
**Full retrospective + action items (B1–B5):** [`retrospective.md`](retrospective.md)

**Outcome:** 5 of 6 stories are fully Tester-**approved** and AC-complete — **US-2** (`DataSource`/clock seam),
**US-3** (H1 SHA-pinned CI), **US-4** (H2 task-manifest test), **US-5** (H3 `json_safe` encoder), **US-7**
(firehose ledger). **19 of 22 ACs approved**; every merged PR was CI-green (`test` job pass). The P0 hardening
trio (H1/H2/H3) and the live/replay seam now ship with self-tests that turn the sprint-1-era scars (S5 Node-24
flail, S6/#404 manifest drift, #331/#332/#388 JSONB crashes) into permanent regression gates.

**US-6 — FAILED (the one blocker).** AC-6.1 (deploy.yml, SHA-pinned, GHCR push) and AC-6.2
(`docker-compose.staging.yml`) are approved, but **AC-6.3, AC-6.4, AC-6.5 failed**. Root cause (Tester, run
27531582183): the deploy job SCPs `docker-compose.staging.yml` to `/root/solanatrilly/` **which does not exist
on the VPS** → SCP errors "No such file or directory" → `docker compose pull/up` never run → the smoke-test
(8002) and solanaBilly-isolation (8001) steps are never reached. The structural pytest suite is green
(deploy.yml *logic* is correct), but the **end-to-end local → GitHub → GHCR → VPS path is unverified**. A
secondary fault: deploy.yml has only a `push: main` trigger (no `workflow_dispatch`), so the orchestrator's
full-sprint deploy could not be fired — `deploy_summary` records HTTP 422 "Workflow does not have
'workflow_dispatch' trigger."

**DoD status — deploy clause NOT met.** Per retrospective A2 the deploy clause is gated at the **sprint
boundary**; because US-6 never completes a deploy, **nothing is live on port 8002** and **US-1's deploy-gated
DoD (retro A1) stays open** — the same "no VPS presence" gap as sprint-1, now two sprints running, despite the
pipeline itself existing this time.

**Goal gap:** P0 is *substantially* complete — every code/CI/seam/ledger story landed — but P0 is **not exited**
because "tested, **deployed**" is unmet. The fix is small (a `mkdir -p /root/solanatrilly` before the SCP, plus
`workflow_dispatch:`), but until a deploy actually succeeds and the smoke test returns 200, "VPS presence from
P0" remains unachieved.

**Process note (A5 not fully enforced):** stale status fields persist — `sprint2.json` reads `phase: planning`
at review; several stories carry `dev_status: not-started`/`in-progress` while `status: done` and all ACs are
dev-done; and **US-6 reads `status: done` while `tester_status: failed`** — exactly the "done ≠ failed/blocked"
inconsistency A5 flagged in sprint-1.

**Carry into sprint-3 (priority order):** **B1** fix the VPS deploy (`mkdir -p` the target dir; re-run on main;
confirm 200 + isolation) → closes US-6 AC-6.3/6.4/6.5 and retroactively US-1's DoD · **B2** add
`workflow_dispatch:` to deploy.yml · **B3** put VPS verification in the loop (a green structural test is not a
deployed stack) · **B4** enforce A5 (forbid `status: done` with a failed/blocked gate; normalize phase/dev_status
at review) · **B5** carry US-6's failed ACs as the top sprint-3 closeout before any P1 work. See
`retrospective.md` B1–B5.

**Metrics:** 6 stories committed · 5 fully DoD-done · 1 failed (US-6) · 19/22 ACs approved · all merged PRs
CI-green · 2 lint defects (US-2 AC-2.2, US-5 AC-5.2) caught by CI and fixed pre-merge · **0 deployed to VPS**.
Token/cost spend: see `../project-state.json` (Project Lead).

## Sprint-1 Review — Summary (2026-06-15)
**Phase:** review | **Committed scope:** US-1 (1 story) | **Goal:** partially met
**Full retrospective + action items:** [`retrospective.md`](retrospective.md)

**Outcome:** US-1's 5 ACs were all implemented, merged behind green CI (PR#1–#5), and **Tester-approved**
at the AC level with 100% reported coverage (≥80% gate enforced in `ci.yml`). No critical defects. The
containerized 5-service topology (web/Daphne · db/postgres:16 · redis · celery-worker · celery-beat) is
complete and healthchecked, under Docker Rules (no host installs).

**Story-level DoD: BLOCKED** on two items (Tester, `sprint1.json`):
1. **No VPS deployment** — `deploy.yml` does not exist; the deploy trigger 404'd. The Sprint DoD requires
   "deployed to VPS staging (`-p solanatrilly`, port 8002) + smoke-tested," but the CD pipeline is **US-6**,
   which was never pulled into the sprint. US-1 was therefore structurally un-completable.
2. **`retrospective.md` did not exist** — a DoD item with no owner. Created during this review.

**Goal gap:** The P0 goal (clock seam, CI hardening H1/H2/H3, CD + hello-world live day one, firehose ledger)
spanned US-1…US-7; only **US-1** ran. "VPS presence from P0" was **not** achieved — there is no staging stack
on port 8002 yet. CI still uses `actions/checkout@v4` (tag, not SHA-pinned) → H1 outstanding.

**Metrics:** 9 cycles · 5 PRs merged · 2 CI failures (both AC-1.5, fixed in 2 iterations) · 179,050 tokens ≈
**$9.78** (PO $2.70 / Dev $5.40 / Tester $1.68 / Lead $0).

**Carry into sprint-2 (priority order):** US-6 (CD → VPS, unblocks US-1 DoD) → US-2 (DataSource/clock seam) →
US-3/4/5 (H1/H2/H3 hardening) → US-7 (firehose ledger). Decouple the per-story DoD deploy clause from
topology-only stories, and give `retrospective.md` a named owner each sprint. See `retrospective.md` A1–A6.

## Controlled Vocabulary
- **Sprint `phase`:** `planning` → `in-progress` → `review` → `done`
- **Story `status`:** `draft` → `ready` → `in-progress` → `in-review` → `done`
- **`dev_status` / `tester_status` (story & AC):** `not-started` → `in-progress` → `blocked` → `done`
- **AC `checked`:** `false` until the Tester verifies the AC against CI/DoD, then `true`
- **`priority`:** `high` | `medium` | `low`
- **Commit format:** `[US-X] Description of change`
- **Branch format:** `feature/US-X-AC-Y`
- **PR prefixes (PRD §1):** `detection:` / `tape:` / `features:` / `scoring:` / `trading:` / `dashboard:` / `ops:`

## Ownership boundaries
- **Product Owner:** owns the active sprint plan (`sprint6.json`), this board, user stories, change control. Does NOT write code.
- **Dev Team:** implements ACs in Docker on `feature/US-X-AC-Y` branches; updates only `dev_status`/`dev_notes`.
- **Tester:** flips `checked`/`tester_status`, enforces DoD, interprets CI, owns `retrospective.md` each sprint. Does NOT execute tests or edit source.
- **Project Lead:** external script; sole owner of `project-state.json`.

## Open items / human dependencies
See [`po-requests.md`](po-requests.md) — **all four sprint-1/P0 operator blockers remain RESOLVED (2026-06-14):**
the GitHub remote (`AsimQuick/solanaTrilly`), the default branch, and the CD secrets (`VPS_SSH_KEY` / `VPS_HOST` /
`VPS_USER`; GHCR via the built-in `GITHUB_TOKEN`) are all provisioned. **No open human dependencies for sprint-7** —
P5 (the lake + extraction contract + vendored math + Feature Builder + T0/G1/G2 golden parity) is agent-buildable
end-to-end. Its core gates are **offline**: G1 function parity and G2(a) Birdeye live↔backfill parity need **no**
firehose activation (G2(a) uses banked sprint-5 captures + an on-demand Birdeye `seek_by_time` REST backfill — a
REST read within the professional allowance, not a firehose WS activation). The **one** budgeted live read — the
G2(b) Helius raw-truth cross-check (CLAUDE.md: "G2 needs one activation") — is an **agent** task within the
firehose budget (Helius 10→9, logged in `ops/firehose_activation_log.md`, banks a durable fixture), **not** an
operator blocker. The three operator-only Cutover levers (trading-wallet secret, start firehose, enable
real-capital trading) remain out of scope until Cutover.
The sprint-3 "operator firewall" suspicion was **falsified** by US-12's on-box diagnosis (C1): port 8002 was always
reachable; the deploy failure was a code-side smoke-test timing race that agents fixed (no operator action). The
three operator-only Cutover levers (trading-wallet secret, start firehose, enable real-capital trading) remain out
of scope until Cutover — though **D4** (wire a live source to the `listener` + bank a firehose fixture) will spend
the first of the 10 Birdeye / 10 Helius activations, logged in `ops/firehose_activation_log.md` per §15.7.

---
*After editing any `/scrum-master/` doc, re-index with `mcp__devrag__reindex_document` (project convention).*
