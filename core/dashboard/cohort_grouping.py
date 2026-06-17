# ---
# module: core.dashboard.cohort_grouping
# sprint: sprint-10
# story: US-50 AC-50.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.schemas
# ---
"""Cohort wall grouping and sorting logic (AC-50.2).

Groups and sorts cohort sparkline entries by five config-driven dimensions:
  outcome       — categorical label (win/loss/neutral) — None offline
  score_band    — model score bucketed by config thresholds — None if no score
  exit_trigger  — trading exit reason — None offline (clean degradation, H4)
  depth_bucket  — total SOL volume bucketed by config thresholds
  time_of_day   — UTC hour period of t0 bucketed by config boundaries

Principle #1: ALL bucket boundaries come from CohortGroupingConfig (core/schemas.py).
No numeric literals for thresholds appear in this module.

Principle H4 (real-missing preserved): When a field is unavailable (e.g. exit_trigger
offline), assign_group_key() returns None — never fabricates, never raises on missing.
"""
from __future__ import annotations

from typing import Optional

from core.schemas import CohortGroupingConfig

__all__ = ["apply_grouping", "assign_group_key", "derive_meta_from_rows", "VALID_GROUP_KEYS"]

VALID_GROUP_KEYS: frozenset[str] = frozenset(
    {"outcome", "score_band", "exit_trigger", "depth_bucket", "time_of_day"}
)


def _bucket_value(value: float, thresholds: list, prefix: str) -> str:
    """Assign value to a named bucket using sorted thresholds.

    Returns f"{prefix}_{i}" where i is the index of the first threshold
    that *value* is strictly less than, or f"{prefix}_{len(thresholds)}"
    when value >= all thresholds. No literals: thresholds come from config.
    """
    for i, threshold in enumerate(sorted(thresholds)):
        if value < threshold:
            return f"{prefix}_{i}"
    return f"{prefix}_{len(thresholds)}"


def _hour_from_t0(t0: int) -> int:
    """Extract UTC hour (0–23) from a Unix timestamp."""
    return (t0 % 86400) // 3600


def assign_group_key(
    entry: dict,
    group_by: str,
    cfg: CohortGroupingConfig,
) -> Optional[str]:
    """Return the group key for *entry* under *group_by* dimension.

    Returns None when the field is unavailable (H4 — real-missing, never
    fabricated). Never raises on missing metadata fields.

    Args:
        entry:    Cohort entry dict. Must contain "meta" sub-dict (may be empty
                  or None). meta keys: outcome, score, exit_trigger, depth, t0.
        group_by: One of VALID_GROUP_KEYS.
        cfg:      CohortGroupingConfig with all bucket boundaries.

    Raises:
        ValueError: if group_by is not in VALID_GROUP_KEYS.
    """
    if group_by not in VALID_GROUP_KEYS:
        raise ValueError(
            f"Unknown group_by: {group_by!r}. Must be one of {sorted(VALID_GROUP_KEYS)}"
        )
    meta: dict = entry.get("meta") or {}

    if group_by == "outcome":
        return meta.get("outcome")

    if group_by == "score_band":
        score = meta.get("score")
        if score is None:
            return None
        return _bucket_value(float(score), cfg.score_band_thresholds, "band")

    if group_by == "exit_trigger":
        return meta.get("exit_trigger")

    if group_by == "depth_bucket":
        depth = meta.get("depth")
        if depth is None:
            return None
        return _bucket_value(float(depth), cfg.depth_bucket_thresholds, "bucket")

    # group_by == "time_of_day"
    t0 = meta.get("t0")
    if t0 is None:
        return None
    hour = _hour_from_t0(int(t0))
    return _bucket_value(float(hour), cfg.time_of_day_hour_boundaries, "period")


def derive_meta_from_rows(mint: str, rows: list[dict]) -> dict:
    """Derive per-token metadata computable from raw lake rows.

    Computable offline:
      t0    — first block_time for this mint (Unix seconds)
      depth — total SOL volume for this mint

    Not computable from raw lake (returned as None — real-missing, H4):
      outcome      — requires outcome labeling
      score        — requires BlendScorer
      exit_trigger — requires trading engine
    """
    mint_rows = [r for r in rows if r.get("mint") == mint]
    if not mint_rows:
        return {
            "t0": None,
            "depth": None,
            "outcome": None,
            "score": None,
            "exit_trigger": None,
        }
    t0 = min(int(r["block_time"]) for r in mint_rows)
    depth = sum(float(r.get("vol_sol", 0.0)) for r in mint_rows)
    return {
        "t0": t0,
        "depth": depth,
        "outcome": None,
        "score": None,
        "exit_trigger": None,
    }


def apply_grouping(
    entries: list[dict],
    group_by: Optional[str],
    sort_by: Optional[str],
    cfg: Optional[CohortGroupingConfig] = None,
) -> dict:
    """Group and sort cohort entries by config-driven dimensions.

    Args:
        entries:  List of cohort entry dicts, each with "mint", "candles", and
                  optionally "meta" sub-dict (outcome/score/exit_trigger/depth/t0).
        group_by: Grouping dimension (one of VALID_GROUP_KEYS) or None.
        sort_by:  Sort dimension (one of VALID_GROUP_KEYS) or None.
                  When group_by is also set, sort applies within each group.
        cfg:      CohortGroupingConfig; defaults to schema defaults if None.

    Returns:
        {
          "group_by": str|None,
          "sort_by":  str|None,
          "groups": [
            {
              "key":     str|None,  # group key (None = unavailable or no grouping)
              "count":   int,
              "entries": [...],
            },
            ...
          ]
        }

    Groups are ordered: non-None keys lexicographically first, None last.
    Within each group, entries are sorted by sort_by key when provided
    (None keys appear last within the sorted group).
    """
    if cfg is None:
        cfg = CohortGroupingConfig()

    if group_by is not None and group_by not in VALID_GROUP_KEYS:
        raise ValueError(
            f"Unknown group_by: {group_by!r}. Must be one of {sorted(VALID_GROUP_KEYS)}"
        )
    if sort_by is not None and sort_by not in VALID_GROUP_KEYS:
        raise ValueError(
            f"Unknown sort_by: {sort_by!r}. Must be one of {sorted(VALID_GROUP_KEYS)}"
        )

    # --- partition into groups ---
    if group_by is not None:
        groups_dict: dict = {}
        for entry in entries:
            key = assign_group_key(entry, group_by, cfg)
            if key not in groups_dict:
                groups_dict[key] = []
            groups_dict[key].append(entry)

        def _group_key_order(k):
            return (k is None, k or "")

        groups = [
            {"key": k, "count": len(v), "entries": v}
            for k, v in sorted(groups_dict.items(), key=lambda item: _group_key_order(item[0]))
        ]
    else:
        groups = [{"key": None, "count": len(entries), "entries": list(entries)}]

    # --- sort within groups ---
    if sort_by is not None:
        for group in groups:
            def _entry_sort_key(e, _sb=sort_by, _cfg=cfg):
                k = assign_group_key(e, _sb, _cfg)
                return (k is None, k or "")
            group["entries"].sort(key=_entry_sort_key)
            group["count"] = len(group["entries"])

    return {
        "group_by": group_by,
        "sort_by": sort_by,
        "groups": groups,
    }
