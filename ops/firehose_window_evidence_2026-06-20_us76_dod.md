<!--
file: ops/firehose_window_evidence_2026-06-20_us76_dod.md
purpose: Durable evidence banked from the US-76 DoD firehose validation window
         (the activation's banked fixture per the ops/firehose_activation_log.md HARD RULE).
note: the full pre-grad tapes were in-memory only (consumed at score time) — the AC-3
      durable per-mint store is deferred, so no tape golden was bankable; this distilled
      score-join evidence is the durable artifact. Full DEBUG log retained on the VPS
      st_fh container's stdout at window time.
-->

# US-76 DoD firehose window — evidence (2026-06-20, observe/paper)

**Activation:** Birdeye `SUBSCRIBE_MEME` (graduation) + Helius birth-tape (collection); 90-min box.
**Serving:** `trilly_pregrad_v3.2`, single-spot USD vol + unified `(block_time, signature)` sort
(PR #334), lab serving bundle (`reference_dist.json` grid), gate `adaptive_topk` @ 30/day
(rank_cut **0.7916**). `trading_enabled=False`, copy `mode=observe` throughout.

## Chain health (all green)
- Logging alive (`[FIREHOSE]`); **collection streamed healthily** — buffer grew to
  **~1,193 mints / 81,052 swaps**, no stall (#323 absent).
- Graduation source subscribed `SUBSCRIBE_MEME {graduated:True, source:pump_dot_fun}`.
- Curve-life instant-skip fired: `yks7qyAPonTPAkiRXaGsKHinGNcpyQZK12HseDApump curve_life=38s (<60s) instant_skipped=1`.
- 7 new graduations during the window (tokens 284 → 291). **Zero errors/tracebacks.**

## Score-joins (the historically fragile step — FIRED, parity-correct serving)
5 real graduations scored with N>0 buffered pre-grad tape:

| mint | blend score | gate (cut=0.7916) |
|------|------------:|-------------------|
| 6nqJG…pump | 0.5794 | fail |
| 5M8Hq…pump | 0.1802 | fail |
| 43NZ6…diso | 0.3610 | fail |
| 1QoKZ…pump | 0.3420 | fail |
| 6Qt6k…pump | 0.7366 | fail |

## Verdict
**The scoring chain is PROVEN end-to-end through the gate on live data:**
detect → assemble pre-grad tape (N>0, ≥20-swap gate) → normalize (single-spot USD) →
score against the frozen reference grid → gate. All 5 scored **below** the offline
30/day rank_cut, so **no token passed → the paper-buy→paper-sell leg did not fire**.
Per runbook §8/§11 this is a **FINDING (no live edge this window), not a failure**.

## Follow-ups identified (fixed in PR #335 `feature/US-76-paper-leg-fixes`)
1. **Post-grad subscription saturation** — `_postgrad_plan_sync` subscribed all graduated
   rows oldest-first (cap 5), so stale dead tokens held every slot and fresh graduations
   were starved of post-grad price data (a latent paper-leg blocker even for a gate-passer).
   Fixed: recent-only, newest-first.
2. **rank_cut needs live calibration** — `per_day_target` is now a config knob; the 5 live
   scores (mean ~0.40) sit below the 30/day cut. Re-run at a calibrated per_day to exercise
   the paper leg on a real gate-passer.

---

# Window-2 — PAPER LEG OBSERVED ✅ (the full DoD)

**After PR #335** (post-grad newest-first/recency + `per_day_target`), re-run calibrated to
**50/day** (rank_cut **0.6976** — a published depth_menu operating point, lab-sanctioned live
calibration). `trading_enabled=False`, copy `mode=observe` throughout. First settled paper
position arrived **~12 min in**; monitor auto-stopped on success.

## The full chain, observed end-to-end on live data
```
score:     mint=5NgDxD1en3YXvS9amAtgb15nc4oRfuGxomcAfu4wpump score=0.7179 gate=pass
paper-buy: mint=5NgDx… size=$25.00 entry_price=0.0000399664 score=0.7179
paper-sell:mint=5NgDx… pnl=-2.25% trigger=AUTO_SELL_TIMER held=2s peak=0.00%
```
Settled `trading_positions` row: `source=model, mode=observe, status=CLOSED, score=0.7179,
entry=0.0000399664, exit=0.0000390679, realized_pnl_pct=-2.25, exit_trigger=AUTO_SELL_TIMER`.

**This satisfies the US-76 Definition of Done:** a real graduation scored live (N>0 swaps) →
gate → paper-buy → paper-sell observed, curve-life instant-skip logged, single-spot-USD +
unified-sort parity serving, observe/paper, `trading_enabled=False`, §5 isolation, solanaBilly
untouched.

## Quality note (not a blocker)
`held=2s / peak=0.00%` ⇒ the post-grad subscription had only just opened when the token scored
at grad+`score_at_elapsed_s` (120 s), so the post-grad tape was thin at entry and the settler
hit the timer almost immediately. The settle is valid (enterable, booked, closed), but for a
richer/representative paper trade the post-grad subscription wants more lead time before
scoring — a tuning refinement (e.g. open the post-grad sub at graduation and/or delay entry a
few more seconds), not a correctness bug.

---

# Operator dashboard — RESTORED ✅ (folded into the DoD per operator request)

The operator reported the dashboard rendered blank in the browser. Root-caused to the
production frontend never being fully wired for external access (three faults, PR #337):

1. **No host port** on the nginx `frontend` container → unreachable from the host. The
   operator was hitting `:8002` (the Django web/API), whose `/dashboard/` route is only a
   bare smoke-test shell (`<div id='root'></div>`, **no `<script>`** → blank page).
2. **No API proxy** in `frontend/nginx.conf` — only an SPA fallback. The SPA calls relative
   `/api/` + `/ws/`, which without a proxy fall through to `index.html`.
3. **Healthcheck false-negative** — probed `localhost`→`::1`, but the custom `default.conf`
   made the nginx entrypoint skip adding `listen [::]:80` → connection-refused (195 failing
   streaks though nginx served fine).

**Fix (matches the documented production design — nginx serves the built SPA, web handles
API+WS):** `nginx.conf` reverse-proxies `/api/`, `/ws/` (with WS upgrade headers), `/health/`
to `web:8000`, preserving the browser `Host` (in `ALLOWED_HOSTS`). Docker DNS `resolver` +
`$web` variable so `proxy_pass` resolves **per-request** — survives web's IP change on each
deploy without a reload. `docker-compose.staging.yml` maps the frontend to host **8003**
(8001=solanaBilly, 8002=web — both untouched), `depends_on: web`, healthcheck via `127.0.0.1`.

**Operator dashboard URL: `http://140.82.43.36:8003/`** (single origin: SPA + proxied API/WS).

**Verified live post-deploy (2026-06-20):**
```
frontend container : Up (healthy)  0.0.0.0:8003->80/tcp   (failingStreak=0)
GET :8003/                       -> SPA index WITH <script src="/assets/index-*.js">
GET :8003/assets/index-*.js      -> HTTP 200  application/javascript  369,002 bytes
GET :8003/api/copytrade/summary/ -> JSON (proxied to web, not the SPA fallback)
GET :8003/health/                -> {"status": "ok"}
GET :8003/cohort  (client route) -> HTTP 200 (SPA fallback)
```
Safety floor held: `-p solanatrilly` isolation, solanaBilly untouched, deploy dispatch-only
with firehose OFF, no secrets touched.
