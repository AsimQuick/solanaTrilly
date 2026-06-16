<!--
file: oracle-direction.md
purpose: Forward strategic direction to the Product Owner for sprint-8+ (the endgame: a promotable
         model + a started firehose making predictions). Written by the operator's oracle, grounded in
         the PRD, the anti-drift data-lake contract, the trilly_pregrad_v3_2 model handoff, and the
         solanaBilly Helius firehose architecture. READ THIS BEFORE PLANNING sprint-8.
owner: oracle (operator delegate)
audience: product-owner (planning), project-lead (sequencing)
last-updated: 2026-06-17
status: ACTIVE — directs the backlog AFTER sprint-7 (P5) closes
-->

# Oracle Direction — the road from P5 to "promote a model + start the firehose"

> **Definition of done for the whole project** (operator's words): solanaTrilly is finished when the
> operator can **promote a model** (current best: `trilly_pregrad_v3_2`) and **start the firehose to make
> live predictions**. Everything below sequences the backlog toward exactly that, and no further.
>
> solanaTrilly stays **model-agnostic** — v3.2 is the *first* model it must be able to serve, not a
> hardcode. Every recommendation here is **config-driven** (PRD §5): new behavior arrives as a config
> knob validated by the Pydantic core, never as a literal in code.

---

## 0. Where we are (so the PO plans the right next thing)

- **Sprint-7 (P5)** delivers the integrity core: vendored feature math (US-28), the hashed/versioned
  `feature_sets` contract (US-29), the one shared deterministic extractor (US-30), the Feature Builder
  (US-31), and the **T0/G1/G2 source-parity gate** (US-32). On exit, live==offline **by construction**
  for the **Birdeye** tape.
- **What P5 does NOT yet give us:** a *source* that captures **every token from birth (pre-graduation)**,
  and a *serving path* that can run the model we actually want to promote. Those two gaps are the whole
  of the remaining road. They are the subject of this document.

---

## 1. THE ARCHITECTURE GAP THAT BLOCKS PROMOTION (read first)

**The model we want to promote scores PRE-GRADUATION behavior.** `trilly_pregrad_v3_2`'s entry is the
**graduation instant**, and all **20 features are `pre_*`** (pre-graduation buyer-cohort):
`pre_eb10_sold_frac`, `pre_eb20_netpos_frac`, `pre_buyers_first_half`, `pre_buyers_second_half`, … (see
`/Users/asim/NoIcloud/solanatrills/models/trilly_pregrad_v3_2/meta.json`). To produce these **live**, we
must have each token's **complete pre-graduation tape** at the moment it graduates.

**The current tape source cannot capture that.** PRD §3.3 makes **Birdeye** the primary operational tape
(per-mint `SUBSCRIBE_TXS`, chosen because Birdeye allowance ≫ Helius and it carries both swap legs).
But a **per-mint subscription cannot see a token before you know the mint exists** — and a pre-grad token
is unknown until it has already been trading. **You cannot capture birth-to-graduation behavior with a
per-mint feed.** This is not a tuning problem; it is a topology problem.

**The resolution is exactly the operator's directive:** capture every token from birth with the **Helius
program-wide `transactionSubscribe`** on the pump.fun program — the same single firehose
`solanaBilly` already runs 24/7 (`app/services/tape_recorder.py`: one
`transactionSubscribe(accountInclude=[pump.fun])` hears **every** create/buy/sell/migration for **every**
token, then a launch-window filter decides what to record). One connection, full population, zero
selection bias — and it is the **only** way to feed v3.2's `pre_*` features live.

> **DIRECTION TO PO — Epic "P6a: Pre-graduation birth-tape ingestion (Helius program-wide firehose)".**
> Plan a sprint-8 epic that adds a **second DataSource**: a Helius `transactionSubscribe` on the pump.fun
> program (adapt solanaBilly's `tape_recorder.py` decode — Anchor `TradeEvent` discriminator, raw
> lamports/base-units, program `user` as trader) feeding the **same normalized-swap schema** (PRD §6.4)
> and the **same** lake + extractor built in P5. This is `source:"helius_live"` alongside the existing
> `birdeye_live`/`birdeye_backfill`/`helius_verify`. It is **additive** — it does not replace Birdeye; it
> covers the pre-grad window Birdeye structurally cannot.
>
> Note the PRD already reserves the seam: §6.4 mandates "**one normalized-swap schema across all
> sources**", and `core/tape/` already injects `DataSource` + clock (Principle #7). The birth-tape source
> drops in behind that seam — no new code path through the extractor.

---

## 2. Remove the wallet-count gate; keep (and split) the idle-kill — config-driven

The operator's intent: **track every token** (drop solanaBilly's "<4–6 wallets after 2 min" poll-stop
filter — that was a *trading/scoring* gate, `noise_filter.py` / `trading_tasks.py`, never a tape gate),
**but** unsubscribe **clearly dead** tokens (no trades for ~5 min) to control spend.

**Good news: the dead-token unsubscribe is already built and config-driven.** `core/tape/idle_kill.py`
deactivates a mint after `tape.idle_kill_ttl_s` of no swaps and **re-attaches the instant a new swap
arrives** (`reattach:true`) — and because the program-wide firehose still physically hears the mint,
"unsubscribe" is just *dropping it from the record set*: **there is no per-token WS to close, so the cost
saving is real and the reattach is free.** This is strictly cheaper than the per-mint model the operator
had in mind.

**The one correction the operator needs:** a flat **5-minute** kill **violates a label-integrity
invariant.** `core/schemas.py` enforces **`tape.idle_kill_ttl_s ≥ outcome.window_s`** (PRD §5.2 / D4 —
"never truncate a label"). v3.2's label horizon is **1800 s** (`tr30_t1800`), so a 300 s kill would cut a
token off **before its training/outcome window closes**, corrupting every label and the live/offline
parity. 5 minutes is right for *cost*, wrong for *correctness*, under a single global TTL.

> **DIRECTION TO PO — Epic "P6b: Two-tier idle policy (the real spend lever)".** Reconcile cost and
> correctness with **two** config knobs, not one:
> - `tape.pre_grad_idle_kill_ttl_s` (**new, ~300 s**): aggressive kill for tokens that have **not
>   graduated** and show no life. The overwhelming majority of pump.fun mints die in minutes and never
>   graduate — they never need a label window, so killing them at ~5 min is safe **and** is where ~all the
>   firehose write-cost goes. This is the operator's "5-minute dead-token" rule, applied to the only
>   population it is safe for.
> - `tape.idle_kill_ttl_s` (**existing, ≥ outcome.window_s**): once a token **graduates** it enters the
>   labeled population; its TTL must stay ≥ the label window (the D4 invariant — keep the schema guard).
> Keep `reattach:true` for both. Extend the Pydantic validator so the new pre-grad TTL is exempt from the
> `≥ window_s` floor *only while the token is pre-graduation*, and snaps to the protected TTL at the
> graduation instant. **Everything stays in `core/schemas.py` — no literals in the recorder.**

---

## 3. The anti-drift contract is non-negotiable — extend the parity gate to the new source

The data sources must tell **one story** (the operator's "they need to be in sync"). This is already the
project's central scar (PRD §1: 23% live vs 78% offline precision from assembly drift, #358/#359/#367)
and the anti-drift contract is written down: `/Users/asim/NoIcloud/solanatrills/docs/archive/t2_era/
data-lake.md` — **VPS = capture+serve only; local = the single source of truth for history; flows are
one-way; every dataset gets a MANIFEST; cross-machine compares use `LC_ALL=C sort`.**

Sprint-7's **US-32 (T0/G2 source-parity gate)** is exactly the mechanism: Birdeye live↔backfill
byte-parity + a Helius raw-truth cross-check, wired as a **hard merge gate**.

> **DIRECTION TO PO — fold the new source into the existing gate, don't invent a parallel one.** When the
> Helius **birth-tape** source lands (§1), extend US-32's parity gate to reconcile it against Birdeye in
> the **window where they overlap (post-graduation)**: same mint, same swaps, byte-identical after
> normalization. The pre-grad-only window has no Birdeye counterpart, so its gate is Helius
> **raw-truth self-consistency** (decode determinism + the §6.4 schema invariants) plus the G1 feature
> golden-parity. **No source enters the lake without a parity gate and a MANIFEST.** Make "sources in
> sync" a standing Definition-of-Done line, not a one-off story.

---

## 4. The serving path must run a 15-booster blend — flag this now, it's not the ONNX path

`trilly_pregrad_v3_2` is **not a single model**: it is a **seed-bagged rank-average blend of 15 LightGBM
boosters** (3 labels × 5 seeds — `tr30_t1800_25`, `oracle_25`, `log1p(cumbv_peak)`), scored by
percentile-ranking each label's score across the candidate pool then averaging the three ranks
(`MODEL_HANDOFF.md`). **solanaTrilly currently has NO trading model committed.** The only thing under
`solanatrilly/models/` is `model.onnx` — a BERT/XLM-Roberta **sentence-embedder that belongs to DevRAG**
(its local semantic-search model; gitignored, referenced by `devrag-config.json`, used by NO project code).
It is **not** a trading artifact — do not mistake it for one. The serving path (P7) must be **built** to
load v3.2's 15-booster LightGBM blend; nothing in `models/` today serves it. (Naming footgun: DevRAG put
its embedder in `models/`, the conventional name for trading artifacts — when P7 needs a model dir, keep
the promoted artifact clearly separate from DevRAG's `model.onnx`.)

> **DIRECTION TO PO — two reconciliations before promotion:**
> 1. **Feature contract:** the FeatureSet built in US-29/US-30 must reconcile **column-for-column and in
>    booster order** against v3.2's actual `feature_name()` list (the 20 `pre_*` features). PRD §7.4 makes
>    `model.feature_list == booster.feature_name()` the **binding order** — the promoter refuses on
>    mismatch. The P5 `live_servable[]` set must therefore cover **all 20** of v3.2's features with a
>    **live** computation, or v3.2 is unservable. Direct the dev team to diff `live_servable[]` against
>    `meta.json:features` as an explicit acceptance check.
> 2. **Blend serving + `model_registry` write contract (§7.4):** plan a story that confirms solanaTrilly's
>    serving path can load **multiple boosters**, apply the **rank-average blend**, and that its
>    `model_registry`/`promote_model.py` mirror (§7.4) accepts a `lightgbm_regression` **blend** artifact
>    (15 boosters + the rank-blend transform), not just a single model. This is the gap most likely to
>    surprise us at cutover — surface it in sprint-8 planning, not at promotion time.

---

## 5. Spend discipline (the operator is watching cost)

- **The firehose itself is cheap in connection terms** — one sustained program-wide
  `transactionSubscribe` (solanaBilly proves it runs affordably 24/7). The cost is **write volume**
  (every mint's swaps) and **VPS disk**, both bounded by the **pre-grad idle TTL** of §2. That two-tier
  TTL is the single biggest spend lever — get it right and full-population capture is sustainable.
- **VPS disk discipline:** retention ≤ 7 days on the box (data-lake.md), daily ship to the local lake,
  expire on the VPS. *(Operator note 2026-06-17: VPS was at 87% — pruned to 45%. With full-population
  capture coming, the daily ship+expire job and the ≤7-day retention sweep are now load-bearing, not
  nice-to-have. Put the shipping job on the backlog if it is not already implemented.)*
- **Birdeye/Helius activation budget** (`ops/firehose_activation_log.md`): 8 Birdeye / 10 Helius remain.
  P5's offline gates need none. The birth-tape epic's first live bring-up is a **deliberate, logged,
  fixture-banking** Helius activation like every other (PRD §15.7) — bank a real pre-grad→graduation tape
  as the durable golden fixture the extractor/parity suite runs against forever.

---

## 6. Suggested sprint-8 shape (PO owns the final plan)

1. **P6a — Helius birth-tape DataSource** (program-wide firehose → normalized schema → P5 lake/extractor). §1
2. **P6b — Two-tier idle policy** (`pre_grad_idle_kill_ttl_s` ~300 s + the protected post-grad TTL). §2
3. **Extend US-32 parity** to the birth-tape source + a MANIFEST gate. §3
4. **Feature-contract reconciliation** — `live_servable[]` ⊇ v3.2's 20 `pre_*` features. §4.1
5. **Blend serving + `model_registry` blend write contract** (§7.4). §4.2
6. **Daily VPS→lake ship + ≤7-day retention sweep** (disk safety under full-population). §5
7. **F5 (deferred from sprint-7):** the live Birdeye REST score-time snapshot adapter, now on the
   scorer's critical path.

When 1–5 are green and a soak (head-to-head v3.2 vs the incumbent, ~30/day per the handoff caveats) holds,
the operator can promote `trilly_pregrad_v3_2` and start the firehose. **That is done.**

---
*This is direction, not a spec change. The PRD remains the contract; where this document and the PRD
appear to differ (e.g. §3.3 "Birdeye primary / Helius verify-only"), the reconciliation is in §1: Helius
becomes an **additive birth-tape source** for the pre-grad window Birdeye cannot reach, while Birdeye
stays primary post-graduation. If the PO believes any item here conflicts with the PRD in a way §1 does
not resolve, raise it in `po-requests.md` for the operator rather than guessing.*
