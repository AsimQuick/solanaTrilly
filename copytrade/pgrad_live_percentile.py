# ---
# module: copytrade.pgrad_live_percentile
# sprint: sprint-15
# story: US-82
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: redis, copytrade.pgrad_classifier
# ---
"""Live-percentile thresholding for the curvestage P(grad) gate (US-82).

MOTIVATION (measured 2026-06-23):
  Lab gated population (cf<=0.6, n=2522): pgrad median=0.018, p75=0.096.
  Live depth-matched population (pre_sol_in>=10 SOL, n=48): pgrad median=0.097.
  LIVE/LAB score ratio at trigger depth: ~5x (live median sits at lab p75).
  Using the fixed lab-calibrated threshold 0.0956 would admit ~50% of live
  copy-trigger entries instead of the intended 25% — 2x inflation of the pass rate.

  Root cause: the lab parquets represent WATCHED WALLET firstbuys (median
  tok_age_s=58, pre_sol_in=43 SOL). The live depth-matched vps_export tokens that
  qualify (pre_sol_in>=10 SOL in 55 items) are the HIGHEST VELOCITY subset —
  earlier in their lifecycle (tok_age_s~19) with more concentrated buying — which
  the model scores higher. This is a SELECTION EFFECT: we cannot fully separate
  genuine train/serve skew from population composition without a labeled live soak.

SOLUTION — live-percentile gate:
  Record every raw pgrad score in a rolling Redis window (default 24h, min 50
  scores to activate). The gate threshold = p75 of the recent score mass. When the
  window is not yet warm (< min_scores), fall back to the fixed recalibrated or
  frozen threshold from pgrad_meta.json.

  This is NON-TRIVIALLY SELECTIVE regardless of train/serve skew: the gate always
  selects the top-25% of whatever the model actually scores live. It is also
  transparent — the live score log is visible in Redis and can be inspected by the
  operator at any time.

OFFLINE VALIDATION CEILING (documented per coordinator requirement):
  A fully production-faithful, LABELED selectivity proof is INFEASIBLE offline:
    (a) firehose Jun20-23 tapes lack Birdeye basePrice/quotePrice — production
        entry_features() returns empty for firehose items (structural incompatibility).
    (b) vps_export Birdeye data has the right fields but only n=48 depth-matched
        (copy-trigger depth) entries out of 37k tokens — too small for stable stats.
  Therefore the FINAL selectivity proof is the LIVE SOAK.
  Offline we establish: correct wiring + sensible/robust threshold + parity
  characterization. That is the honest bar: "properly tested," not "proven profitable."

THRESHOLD PRIORITY ORDER (in curvestage_engine.py):
  1. live_percentile (this module) — p75 of rolling window — PREFERRED when warm (>=50 scores)
  2. pgrad_threshold_recalibrated from pgrad_meta.json — 0.0956 — fallback
  3. pgrad_threshold_frozen from pgrad_meta.json — 0.153 — last resort
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: Redis key for the pgrad score sorted set (score → timestamp).
REDIS_KEY = "copytrade:pgrad_scores"

#: Rolling window in seconds (24h).
WINDOW_SECONDS: int = 86400

#: Minimum number of scores before live-percentile activates (avoids cold-start
#: noise — fall back to fixed threshold when window is not warm).
MIN_SCORES_FOR_LIVE_PERCENTILE: int = 50

#: The target percentile (top-25% gate = p75 cutoff).
PERCENTILE: int = 75

#: Score log key for operator inspection (LPUSH, keep last 1000).
SCORE_LOG_KEY = "copytrade:pgrad_score_log"
SCORE_LOG_MAXLEN: int = 1000


def record_score(score: float, *, redis_client=None) -> None:
    """Push a raw pgrad score into the rolling window.

    Called by curvestage_engine immediately after scoring (before the gate
    threshold check) so every scored token is logged regardless of pass/fail.
    This gives an unbiased view of the live score distribution.

    ``redis_client`` is the Django cache Redis client (django_redis.get_redis_connection).
    If None, imports it lazily (avoids import cost at module load time).
    """
    if redis_client is None:
        try:
            from django_redis import get_redis_connection  # noqa: PLC0415
            redis_client = get_redis_connection("default")
        except Exception as exc:
            logger.debug("[pgrad_live_pct] redis unavailable — score not recorded (%s)", exc)
            return
    now = time.time()
    try:
        # Sorted set: member=score_str:timestamp, score=timestamp (for expiry)
        member = f"{score:.6f}:{now:.3f}"
        redis_client.zadd(REDIS_KEY, {member: now})
        # Trim old entries outside the window
        redis_client.zremrangebyscore(REDIS_KEY, 0, now - WINDOW_SECONDS)
        # Also push to the human-readable log (newest first)
        redis_client.lpush(SCORE_LOG_KEY, f"{score:.6f}@{now:.0f}")
        redis_client.ltrim(SCORE_LOG_KEY, 0, SCORE_LOG_MAXLEN - 1)
    except Exception as exc:
        logger.debug("[pgrad_live_pct] redis write failed (%s)", exc)


def get_live_percentile_threshold(*, redis_client=None) -> Optional[float]:
    """Return the current live p75 threshold, or None if window not warm.

    Returns None when fewer than MIN_SCORES_FOR_LIVE_PERCENTILE scores are
    in the window (caller should fall back to fixed threshold).
    """
    if redis_client is None:
        try:
            from django_redis import get_redis_connection  # noqa: PLC0415
            redis_client = get_redis_connection("default")
        except Exception as exc:
            logger.debug("[pgrad_live_pct] redis unavailable (%s)", exc)
            return None
    now = time.time()
    try:
        # Trim stale first
        redis_client.zremrangebyscore(REDIS_KEY, 0, now - WINDOW_SECONDS)
        members = redis_client.zrangebyscore(REDIS_KEY, now - WINDOW_SECONDS, "+inf")
        if len(members) < MIN_SCORES_FOR_LIVE_PERCENTILE:
            logger.debug(
                "[pgrad_live_pct] window not warm (%d < %d) — falling back to fixed threshold",
                len(members), MIN_SCORES_FOR_LIVE_PERCENTILE,
            )
            return None
        # Parse scores from member strings "score:timestamp"
        scores = []
        for m in members:
            if isinstance(m, bytes):
                m = m.decode()
            try:
                scores.append(float(m.split(":")[0]))
            except (ValueError, IndexError):
                pass
        if not scores:
            return None
        scores.sort()
        idx = int(len(scores) * PERCENTILE / 100)
        idx = min(idx, len(scores) - 1)
        threshold = scores[idx]
        logger.debug(
            "[pgrad_live_pct] live p75 threshold=%.4f from %d scores in window",
            threshold, len(scores),
        )
        return threshold
    except Exception as exc:
        logger.debug("[pgrad_live_pct] redis read failed (%s)", exc)
        return None


def get_window_stats(*, redis_client=None) -> dict:
    """Return summary stats for operator inspection (n, median, p75, p25).

    Useful for the live score log and dashboard display.
    """
    if redis_client is None:
        try:
            from django_redis import get_redis_connection  # noqa: PLC0415
            redis_client = get_redis_connection("default")
        except Exception as exc:
            return {"error": str(exc), "n": 0}
    now = time.time()
    try:
        redis_client.zremrangebyscore(REDIS_KEY, 0, now - WINDOW_SECONDS)
        members = redis_client.zrangebyscore(REDIS_KEY, now - WINDOW_SECONDS, "+inf")
        scores = []
        for m in members:
            if isinstance(m, bytes):
                m = m.decode()
            try:
                scores.append(float(m.split(":")[0]))
            except (ValueError, IndexError):
                pass
        if not scores:
            return {"n": 0, "warm": False}
        scores.sort()
        n = len(scores)
        p25_idx = min(int(n * 25 / 100), n - 1)
        p50_idx = min(int(n * 50 / 100), n - 1)
        p75_idx = min(int(n * 75 / 100), n - 1)
        return {
            "n": n,
            "warm": n >= MIN_SCORES_FOR_LIVE_PERCENTILE,
            "median": scores[p50_idx],
            "p25": scores[p25_idx],
            "p75": scores[p75_idx],
            "min": scores[0],
            "max": scores[-1],
        }
    except Exception as exc:
        return {"error": str(exc), "n": 0}
