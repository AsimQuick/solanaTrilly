# ---
# module: core.dashboard.token_detail
# sprint: sprint-10
# story: US-49 AC-49.2, US-49 AC-49.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.candle_api, core.feature_extractor, core.pregrad_features, typing
# ---
"""Token-detail / research view assembler (AC-49.2, AC-49.3).

Assembles the three data layers the frontend TokenDetail view renders:

  candles     — OHLC candle series (from build_candles, AC-49.1 — one price basis)
  overlay     — per-candle buy/sell-pressure & net-flow histogram
  markers     — t0 (first swap block_time) and score_time (t0 + score_at_elapsed_s)
  score_panel — EXACT feature vector the model saw + its score from the US-43
                BlendScorer (AC-49.3); None if no scorer/ref_dist supplied.

Principle #2 — ONE price basis:
  All data derives from the SAME raw lake rows, normalized via _lake_row_to_micro
  from core.feature_extractor — the same §7.1 path as the shared US-30 extractor.
  No network calls, no separate price source, no dashboard-local price derivation.

Principle #1 — config-driven:
  interval_s and score_at_elapsed_s come from the caller (DashboardConfig and
  ScoringConfig via get_active_config()); no literals inside this module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.dashboard.candle_api import build_candles
from core.feature_extractor import FeatureExtractor, _lake_row_to_micro
from core.pregrad_features import compute_pregrad_features

if TYPE_CHECKING:
    from core.scorer import BlendScorer, ReferenceDistribution

# ---------------------------------------------------------------------------
# SENTINEL — H1 import-time guard
# ---------------------------------------------------------------------------
__all__ = ["build_token_detail", "build_overlay", "build_score_panel"]

# Fallback when no active config is available (tests that don't need DB).
SCORE_AT_ELAPSED_S_DEFAULT: int = 120


def build_overlay(
    rows: list[dict],
    mint: str,
    interval_s: int,
) -> list[dict]:
    """Derive per-candle buy/sell-pressure & net-flow overlay from raw lake rows.

    Uses the identical mint filter, stable sort key, and _lake_row_to_micro
    normalization as build_candles (Principle #2).  The side field determines
    whether each swap contributes to buy_vol or sell_vol in its candle window.

    Args:
        rows:       Raw lake rows (same list as LakeReader.iter_rows()).
        mint:       Token mint address; rows for other mints are filtered out.
        interval_s: Candle window size in seconds (must be > 0).

    Returns:
        List of overlay dicts sorted by ascending 't':
        [{"t": int, "buy_vol": float, "sell_vol": float, "net_flow": float}, ...]
        Empty list if no rows match the mint.

    Raises:
        ValueError: if interval_s <= 0.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s}")

    filtered = [r for r in rows if r.get("mint") == mint]
    if not filtered:
        return []

    # Stable sort — identical to build_candles and FeatureExtractor.
    filtered.sort(key=lambda r: (int(r["block_time"]), int(r["slot"]), str(r["signature"])))
    micro_rows = [_lake_row_to_micro(r) for r in filtered]

    overlay_map: dict[int, dict] = {}
    for row in micro_rows:
        vol: float = row["vol"]
        side: str = row["side"]
        block_time: int = int(row["block_time"])
        window_start: int = (block_time // interval_s) * interval_s

        if window_start not in overlay_map:
            overlay_map[window_start] = {"buy_vol": 0.0, "sell_vol": 0.0}
        if side == "buy":
            overlay_map[window_start]["buy_vol"] += vol
        else:
            overlay_map[window_start]["sell_vol"] += vol

    return [
        {
            "t": t,
            "buy_vol": v["buy_vol"],
            "sell_vol": v["sell_vol"],
            "net_flow": v["buy_vol"] - v["sell_vol"],
        }
        for t, v in sorted(overlay_map.items())
    ]


def build_score_panel(
    feature_vector: dict,
    scorer: BlendScorer,
    ref_dist: ReferenceDistribution,
) -> dict:
    """Score a pre-extracted feature vector via the US-43 BlendScorer (AC-49.3).

    Args:
        feature_vector: Pre-extracted feature dict from the US-30 extractor path
                       (FeatureExtractor.extract_pregrad_from_lake or equivalent).
        scorer:         US-43 BlendScorer — the parity-checked scorer, never
                       a re-implementation ('scores in sync').
        ref_dist:      Frozen ReferenceDistribution for single-token percentile
                       ranking (AC-43.2 cutover risk resolution).

    Returns:
        {
          "feature_vector": dict,   # the exact features the model saw (Principle #2)
          "score": {
            "label_scores": {label: float},
            "label_ranks":  {label: float},
            "blend_score":  float,
          }
        }
    """
    score_result = scorer.score_single(feature_vector, ref_dist)
    return {
        "feature_vector": feature_vector,
        "score": score_result,
    }


def build_token_detail(
    rows: list[dict],
    mint: str,
    interval_s: int,
    score_at_elapsed_s: int = SCORE_AT_ELAPSED_S_DEFAULT,
    scorer: BlendScorer | None = None,
    ref_dist: ReferenceDistribution | None = None,
) -> dict:
    """Assemble the full token-detail payload for the research view (AC-49.2, AC-49.3).

    Combines the candle series (AC-49.1 — build_candles), the buy/sell-pressure
    & net-flow overlay (build_overlay), t0/score markers, and optionally the
    EXACT feature vector + score from the US-43 BlendScorer (AC-49.3).

    The assembler is the ONLY place that joins these layers; the frontend
    component does not compute or re-derive any data — it renders what this
    function returns.

    Args:
        rows:              Raw lake rows for all mints (LakeReader.iter_rows()).
        mint:              Token mint address to build the detail for.
        interval_s:        Candle interval in seconds (config-driven, Principle #1).
        score_at_elapsed_s: Seconds after t0 at which the model scores the token.
                           Sourced from ScoringConfig.score_at_elapsed_s (Principle #1).
        scorer:            Optional US-43 BlendScorer; when provided together with
                           ref_dist, score_panel is populated (AC-49.3).
        ref_dist:          Optional frozen ReferenceDistribution; required alongside
                           scorer for score_panel (AC-49.3).

    Returns:
        {
          "mint":        str,
          "interval_s":  int,
          "candles":     [{"t", "open", "high", "low", "close", "vol", "interval_s"}, ...],
          "overlay":     [{"t", "buy_vol", "sell_vol", "net_flow"}, ...],
          "markers":     {"t0": int | None, "score_time": int | None},
          "score_panel": {
            "feature_vector": dict,
            "score": {"label_scores": ..., "label_ranks": ..., "blend_score": float},
          } | None,   # None when scorer/ref_dist not supplied or no pre-grad swaps
        }
    """
    # Candle series — AC-49.1 path (one price basis, shared extractor).
    candles = build_candles(rows, mint, interval_s)

    # Per-candle buy/sell-pressure & net-flow overlay.
    overlay = build_overlay(rows, mint, interval_s)

    # t0 = block_time of the first swap for this mint in the lake.
    mint_rows = [r for r in rows if r.get("mint") == mint]
    t0: int | None = min(int(r["block_time"]) for r in mint_rows) if mint_rows else None
    score_time: int | None = (t0 + score_at_elapsed_s) if t0 is not None else None

    # Feature extraction + scoring (AC-49.3).
    score_panel: dict | None = None
    if scorer is not None and ref_dist is not None:
        swaps = FeatureExtractor._load_lake_swaps(mint, rows)
        features = compute_pregrad_features(swaps)
        if features is not None:
            score_panel = build_score_panel(features, scorer, ref_dist)

    return {
        "mint": mint,
        "interval_s": interval_s,
        "candles": candles,
        "overlay": overlay,
        "markers": {"t0": t0, "score_time": score_time},
        "score_panel": score_panel,
    }
