# ---
# module: core.dashboard.cohort_wall
# sprint: sprint-10
# story: US-50 AC-50.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.cohort_api, core.dashboard.cohort_grouping, core.schemas
# ---
"""Cohort wall assembler: combines sparklines + grouping/sorting into one payload (AC-50.3).

Principle #2 — ONE price basis:
  Delegates sparkline derivation to build_cohort_sparklines() (AC-50.1), which reuses
  build_candles() — the identical tape→candle path as US-49.  There is NO cohort-wall-
  local price basis.

Principle #1 — config-driven:
  All grouping bucket thresholds come from CohortGroupingConfig via the cfg parameter.
  No numeric literals for thresholds appear in this module.

The named function build_cohort_wall is the H1 anchor: importing it at module level
in the test file causes pytest COLLECTION to fail when the function is deleted/renamed.

Wall response shape:
  {
    "interval_s": int,
    "count":      int,
    "group_by":   str | None,
    "sort_by":    str | None,
    "groups": [
      {
        "key":     str | None,
        "count":   int,
        "entries": [
          {"mint": str, "candles": [...], "meta": {"t0", "depth", "outcome", "score", "exit_trigger"}},
          ...
        ]
      },
      ...
    ]
  }
"""
from __future__ import annotations

from typing import Optional

from core.dashboard.cohort_api import build_cohort_sparklines
from core.dashboard.cohort_grouping import apply_grouping, derive_meta_from_rows
from core.schemas import CohortGroupingConfig

__all__ = ["build_cohort_wall"]


def build_cohort_wall(
    rows: list[dict],
    mints: list[str],
    interval_s: int,
    group_by: Optional[str] = None,
    sort_by: Optional[str] = None,
    cfg: Optional[CohortGroupingConfig] = None,
) -> dict:
    """Assemble the full cohort wall payload: sparklines + metadata + grouping.

    Combines build_cohort_sparklines() (AC-50.1) and apply_grouping() (AC-50.2)
    into one deterministic function that the cohort_api() view delegates to.

    Principle #2 — sparklines are derived from the shared tape→candle path via
    build_cohort_sparklines(); no cohort-wall-local candle basis.

    Principle #1 — all bucket boundaries come from cfg (CohortGroupingConfig);
    no numeric literals for thresholds appear here.

    Args:
        rows:      Raw lake rows as returned by LakeReader.iter_rows().
        mints:     Ordered list of token mint addresses for the cohort.
                   Mints absent from rows receive empty candles + None meta (H4).
        interval_s: Candle interval in seconds (config-driven; must be > 0).
        group_by:  Optional grouping dimension (one of VALID_GROUP_KEYS).
        sort_by:   Optional sort dimension (one of VALID_GROUP_KEYS).
        cfg:       CohortGroupingConfig; defaults to schema defaults when None.

    Returns:
        Merged dict with keys: interval_s, count, group_by, sort_by, groups.

    Raises:
        ValueError: if interval_s <= 0 (propagated from build_cohort_sparklines).
        ValueError: if group_by/sort_by are not in VALID_GROUP_KEYS (from apply_grouping).
    """
    # Step 1 — build sparklines via the shared US-49 tape→candle path (Principle #2).
    sparklines_result = build_cohort_sparklines(rows, mints, interval_s)

    # Step 2 — attach derived metadata to each sparkline entry.
    entries: list[dict] = []
    for sparkline in sparklines_result["sparklines"]:
        mint = sparkline["mint"]
        meta = derive_meta_from_rows(mint, rows)
        entries.append(
            {
                "mint": mint,
                "candles": sparkline["candles"],
                "meta": meta,
            }
        )

    # Step 3 — group and sort entries by config-driven dimensions (Principle #1).
    grouped_result = apply_grouping(entries, group_by, sort_by, cfg)

    # Step 4 — merge into a single payload.
    return {
        "interval_s": sparklines_result["interval_s"],
        "count": sparklines_result["count"],
        **grouped_result,
    }
