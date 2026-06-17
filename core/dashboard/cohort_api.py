# ---
# module: core.dashboard.cohort_api
# sprint: sprint-10
# story: US-50 AC-50.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.candle_api, typing
# ---
"""Cohort sparkline API: mini candle sparklines for a corpus of tokens (AC-50.1).

Principle #2 — ONE price basis:
  Each sparkline is derived from the SAME tape→candle path as US-49 — via
  build_candles() from core.dashboard.candle_api, which in turn uses
  _lake_row_to_micro from core.feature_extractor.  There is NO cohort-local
  candle basis, no separate price source, and no per-cohort price derivation.

Principle #1 — config-driven:
  The candle interval is read from DashboardConfig.candle_intervals_s via
  the caller (views.py / get_active_config()); this module accepts interval_s
  as a parameter — no literals here.

Sparkline format (one entry per mint):
  {
    "mint":    str,
    "candles": [{"t", "open", "high", "low", "close", "vol", "interval_s"}, ...]
  }

Cohort response:
  {
    "interval_s": int,
    "count":      int,
    "sparklines": [<sparkline>, ...]   # one per mint, sorted by mint ASC
  }
"""
from __future__ import annotations

from core.dashboard.candle_api import build_candles

# ---------------------------------------------------------------------------
# SENTINEL — H1 import-time guard
# ---------------------------------------------------------------------------
__all__ = ["build_cohort_sparklines"]


def build_cohort_sparklines(
    rows: list[dict],
    mints: list[str],
    interval_s: int,
) -> dict:
    """Build mini candle sparklines for each mint in *mints*.

    Reuses build_candles() (AC-49.1 path) for every token — Principle #2:
    the cohort sparklines share the identical tape→candle derivation as the
    single-token token-detail view; there is no cohort-local price basis.

    Args:
        rows:       Raw lake rows as returned by LakeReader.iter_rows().
        mints:      Ordered list of token mint addresses for the cohort.
                    Mints not present in *rows* receive an empty candle list
                    (no crash, no fabricated values — real-missing preserved).
        interval_s: Candle interval in seconds (config-driven, Principle #1).
                    Must be > 0.

    Returns:
        {
          "interval_s": int,
          "count":      int,       # number of mints in the cohort
          "sparklines": [
            {
              "mint":    str,
              "candles": [{"t", "open", "high", "low", "close", "vol", "interval_s"}, ...]
            },
            ...
          ]  # one entry per mint, same order as input *mints*
        }

    Raises:
        ValueError: if interval_s <= 0.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s}")

    sparklines = [
        {
            "mint": mint,
            "candles": build_candles(rows, mint, interval_s),
        }
        for mint in mints
    ]

    return {
        "interval_s": interval_s,
        "count": len(mints),
        "sparklines": sparklines,
    }
