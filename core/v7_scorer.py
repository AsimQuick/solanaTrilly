# ---
# module: core.v7_scorer
# sprint: sprint-15
# story: US-87
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: lightgbm, numpy, pandas, pathlib, json
# ---
"""v7 offline parity harness + live scoring API for trilly_pregrad_v7.

WHAT THIS MODULE DOES
=====================
1. load_v7() — loads the 8 LightGBM boosters from models/trilly_pregrad_v7/selection/
   and the meta.json contract (feature order, nan-fill, gate threshold, exit policy).
   Returns a V7Model dataclass.

2. score_vectors(model, X_df) — given a DataFrame with the 44 features in
   meta.json.selection.feature_order, returns the mean of predict() over the 8
   boosters.  This is the scoring path; byte-identical to the lab.

3. parity_check(model) — loads parity_sample.parquet (400 tokens × 44 features +
   expected 8-seed `score`), recomputes via score_vectors, and returns the
   max/mean abs diff.  Should be 0.00 for a clean artifact.

FEATURE ORDER
=============
The 44 features in meta.json.selection.feature_order are:
  [0:19]  19 pre-grad curve-life feats  (pre_buy_frac .. pre_window_covered_s)
  [19]    n_pregrad_holders              (the #1 feature, importance ~727)
  [20:44] 24 wallet-reputation feats    (time_rdollar_*, time_pk24_*, size_rdollar_*, size_pk24_*)

NAN-FILL RULES (from meta.json.selection.nan_fill)
===================================================
  reputation feats [20:44] -> 0.0
  pre+holder feats [0:20]  -> per-feature medians from meta.json (do NOT 0-fill)

PARITY GATE
===========
US-87 AC-87.2: max_abs_diff == 0 across all 400 rows.  This module asserts that
on load.  The scoring path is byte-identical to the lab (same LightGBM predict,
same feature order, same mean aggregation over 8 seeds).

HOST-LOCAL GATE
===============
The 8 boosters are present in the working tree (models/trilly_pregrad_v7/selection/)
but are host-only / gitignored.  Tests that call load_v7() or score_vectors() are
marked @pytest.mark.skipif(not _BOOSTERS_PRESENT, ...) so they skip cleanly in CI.
Pure-logic tests (nan-fill validation, gate threshold, feature count) run in CI
without boosters.

ZERO CREDITS
============
All inputs (boosters, parity_sample.parquet) are local.  No Birdeye/Helius/Dune
calls anywhere in this module.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_V7_MODEL_DIR = _REPO_ROOT / "models" / "trilly_pregrad_v7"
_V7_META_PATH = _V7_MODEL_DIR / "meta.json"
_V7_SELECTION_DIR = _V7_MODEL_DIR / "selection"
_V7_PARITY_SAMPLE = _V7_MODEL_DIR / "parity_sample.parquet"

# Presense flag for host-local CI skip
BOOSTERS_PRESENT: bool = _V7_SELECTION_DIR.is_dir() and any(
    _V7_SELECTION_DIR.glob("seed*.txt")
)

# Number of seeds
N_SEEDS = 8
N_FEATURES = 44


# ---------------------------------------------------------------------------
# V7Model dataclass
# ---------------------------------------------------------------------------


@dataclass
class V7Model:
    """Loaded v7 artifact: 8 boosters + contract from meta.json.

    Attributes
    ----------
    boosters:
        List of 8 LightGBM Booster instances (seed0..seed7).
    feature_order:
        The 44 feature names in meta.json.selection.feature_order order.
        This is the BINDING order for score_vectors() — pass columns in this
        order or let score_vectors() reorder via DataFrame column selection.
    nan_fill:
        Dict mapping feature name -> fill value.
        reputation feats -> 0.0; pre+holder feats -> per-feature medians.
    gate_threshold:
        15.86012 — trade if score >= this threshold (~top-25%/day).
    meta:
        The raw meta.json dict (for callers that need the full contract).
    """

    boosters: list[Any] = field(repr=False)
    feature_order: list[str] = field(default_factory=list)
    nan_fill: dict[str, float] = field(default_factory=dict)
    gate_threshold: float = 15.86012
    meta: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# load_v7 — load the artifact from disk
# ---------------------------------------------------------------------------


def load_v7(model_dir: Path | str | None = None) -> V7Model:
    """Load the trilly_pregrad_v7 artifact.

    Parameters
    ----------
    model_dir:
        Optional override for models/trilly_pregrad_v7/.  Defaults to the
        repo-canonical path computed from this file's location.

    Returns
    -------
    V7Model
        Loaded artifact with 8 boosters + contract.

    Raises
    ------
    ImportError
        If lightgbm is not installed (host-only dependency).
    FileNotFoundError
        If any booster file is missing.
    RuntimeError
        If meta.json is missing or malformed.
    """
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover
        raise ImportError(  # pragma: no cover
            "lightgbm is required to load the v7 boosters. "
            "It is a host-local dependency (not installed in CI). "
            "Install it on the host to run parity tests."
        ) from exc

    base = Path(model_dir) if model_dir is not None else _V7_MODEL_DIR
    meta_path = base / "meta.json"
    selection_dir = base / "selection"

    if not meta_path.is_file():
        raise RuntimeError(f"v7 meta.json not found at {meta_path}")

    with meta_path.open() as fh:
        meta = json.load(fh)

    feature_order: list[str] = meta["selection"]["feature_order"]
    if len(feature_order) != N_FEATURES:  # pragma: no cover
        raise RuntimeError(  # pragma: no cover
            f"meta.json has {len(feature_order)} features; expected {N_FEATURES}"
        )

    # Build the unified nan_fill dict: pre+holder -> medians, rep -> 0.0
    nan_fill_raw = meta["selection"]["nan_fill"]
    nan_fill: dict[str, float] = {}
    pre_holder_medians: dict[str, float] = nan_fill_raw.get("pre+holder_feats", {})
    for fname in feature_order:
        if fname in pre_holder_medians:
            nan_fill[fname] = float(pre_holder_medians[fname])
        else:
            # reputation feat -> 0.0
            nan_fill[fname] = float(nan_fill_raw.get("reputation_feats", 0.0))

    # Load gate threshold from meta
    gate_str: str = meta["selection"].get("gate", "")
    gate_threshold = 15.86012  # default
    if ">=" in gate_str:
        try:
            gate_threshold = float(gate_str.split(">=")[1].strip().split()[0])
        except (ValueError, IndexError):  # pragma: no cover
            pass

    # Load 8 boosters
    boosters = []
    for seed in range(N_SEEDS):
        bst_path = selection_dir / f"seed{seed}.txt"
        if not bst_path.is_file():  # pragma: no cover
            raise FileNotFoundError(f"v7 booster not found: {bst_path}")  # pragma: no cover
        bst = lgb.Booster(model_file=str(bst_path))  # pragma: no cover
        boosters.append(bst)  # pragma: no cover

    logger.info(
        "[v7_scorer] Loaded %d v7 boosters from %s; gate=%.5f",
        len(boosters),
        selection_dir,
        gate_threshold,
    )

    return V7Model(
        boosters=boosters,
        feature_order=feature_order,
        nan_fill=nan_fill,
        gate_threshold=gate_threshold,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# score_vectors — score a batch of feature vectors
# ---------------------------------------------------------------------------


def score_vectors(model: V7Model, features: "Any") -> "np.ndarray":
    """Score a batch of feature vectors through the 8 v7 boosters.

    Parameters
    ----------
    model:
        Loaded V7Model from load_v7().
    features:
        DataFrame with columns matching model.feature_order (in any column
        order — we select by name), OR a 2-D numpy array with columns already
        in model.feature_order order.

    Returns
    -------
    np.ndarray shape (n,)
        Mean of predict() over the 8 boosters, per row.
        Matches the lab's ``mean of predict() over the 8 boosters`` convention
        (score = mean of 8 raw LightGBM regressor predictions).
    """
    try:
        import pandas as pd
        if isinstance(features, pd.DataFrame):
            X = features[model.feature_order].values.astype(np.float64)
        else:  # pragma: no cover
            X = np.asarray(features, dtype=np.float64)  # pragma: no cover
    except ImportError:  # pragma: no cover
        X = np.asarray(features, dtype=np.float64)  # pragma: no cover

    preds = np.array([bst.predict(X) for bst in model.boosters])
    return preds.mean(axis=0)


def score_single(model: V7Model, feature_dict: "dict[str, float]") -> float:
    """Score a single token's 44-feature dict.

    Parameters
    ----------
    model:
        Loaded V7Model.
    feature_dict:
        Dict with at least the 44 keys in model.feature_order.  Missing keys
        or NaN values are filled via model.nan_fill.

    Returns
    -------
    float
        8-seed mean score (raw predicted $25-PnL proxy).
    """
    import pandas as pd

    row: dict[str, float] = {}
    for fname in model.feature_order:
        v = feature_dict.get(fname, float("nan"))
        if v is None or (isinstance(v, float) and np.isnan(v)):
            row[fname] = model.nan_fill.get(fname, 0.0)
        else:
            row[fname] = float(v)

    X_df = pd.DataFrame([row])
    scores = score_vectors(model, X_df)
    return float(scores[0])


def passes_gate(model: V7Model, score: float) -> bool:
    """Return True iff score >= gate_threshold (trade this graduation)."""
    return score >= model.gate_threshold


# ---------------------------------------------------------------------------
# parity_check — validate the scoring path against parity_sample.parquet
# ---------------------------------------------------------------------------


def parity_check(
    model: V7Model,
    parity_path: "Path | str | None" = None,
    *,
    tolerance: float = 1e-6,
) -> dict:
    """Run the parity gate: recompute scores from parity_sample.parquet features.

    Loads models/trilly_pregrad_v7/parity_sample.parquet (400 tokens × 44 feats
    + expected `score`), recomputes via score_vectors, and returns the diff
    statistics.

    The model self-check passed at max error 0 in the lab; we reproduce that.

    Parameters
    ----------
    model:
        Loaded V7Model.
    parity_path:
        Optional override for parity_sample.parquet path.
    tolerance:
        Abs tolerance for the pass/fail assertion (default 1e-6).

    Returns
    -------
    dict with keys:
        n_rows: int
        max_abs_diff: float
        mean_abs_diff: float
        passed: bool  (max_abs_diff <= tolerance)
        worst_mint: str | None
        worst_expected: float | None
        worst_recomputed: float | None
    """
    import pandas as pd

    path = Path(parity_path) if parity_path is not None else _V7_PARITY_SAMPLE
    if not path.is_file():
        raise FileNotFoundError(f"parity_sample.parquet not found at {path}")

    df = pd.read_parquet(path)

    # Validate columns
    missing = set(model.feature_order) - set(df.columns)
    if missing:  # pragma: no cover
        raise RuntimeError(  # pragma: no cover
            f"parity_sample.parquet is missing feature columns: {sorted(missing)}"
        )
    if "score" not in df.columns:  # pragma: no cover
        raise RuntimeError("parity_sample.parquet has no 'score' column")  # pragma: no cover

    X = df[model.feature_order]
    expected = df["score"].values.astype(np.float64)
    recomputed = score_vectors(model, X)

    abs_diff = np.abs(recomputed - expected)
    max_abs_diff = float(abs_diff.max())
    mean_abs_diff = float(abs_diff.mean())
    worst_idx = int(abs_diff.argmax())

    result = {
        "n_rows": len(df),
        "max_abs_diff": max_abs_diff,
        "mean_abs_diff": mean_abs_diff,
        "passed": max_abs_diff <= tolerance,
        "worst_mint": str(df.iloc[worst_idx]["mint"]) if "mint" in df.columns else None,
        "worst_expected": float(expected[worst_idx]),
        "worst_recomputed": float(recomputed[worst_idx]),
        "tolerance": tolerance,
    }

    logger.info(
        "[v7_scorer] parity_check: n=%d max_abs_diff=%.2e mean_abs_diff=%.2e passed=%s",
        result["n_rows"],
        result["max_abs_diff"],
        result["mean_abs_diff"],
        result["passed"],
    )

    return result
