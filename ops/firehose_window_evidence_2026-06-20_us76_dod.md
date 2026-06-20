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

## Follow-ups identified (fixed in `feature/US-76-paper-leg-fixes`)
1. **Post-grad subscription saturation** — `_postgrad_plan_sync` subscribed all graduated
   rows oldest-first (cap 5), so stale dead tokens held every slot and fresh graduations
   were starved of post-grad price data (a latent paper-leg blocker even for a gate-passer).
   Fixed: recent-only, newest-first.
2. **rank_cut needs live calibration** — `per_day_target` is now a config knob; the 5 live
   scores (mean ~0.40) sit below the 30/day cut. Re-run at a calibrated per_day to exercise
   the paper leg on a real gate-passer.
