# ---
# module: core.v4_rep_builder
# sprint: hotfix
# story: v4-deploy
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: numpy, pandas
# ---
"""Live REP + recurrence feature builder for trilly_pregrad_v4.

Vendored VERBATIM from solanatrills/analysis/graduated/v4_outcome_rep.py
lines 34-100 (lagged_rep, build_pool logic) + build_whale_features.py
(pool_features logic).  Mechanical adaptations:
  - lagged_rep(): identical math, zero change.
  - WalletBankLookup: reads the v4_wallet_bank.parquet (offline-built 443,953
    rows) into a dict keyed by (pool, wallet) → sorted (grad_unix, y_rdollar,
    y_pk24) arrays for O(1) per-wallet lookup.
  - compute_rep_features(): takes the token's early/big buyer lists (pre-built
    from the Helius tape by the caller) and the graduation time T; applies the
    leak-safe lag H = 1800s (rdollar) / 86400s (pk24) and aggregates per
    v4_outcome_rep.py lines 86-99.
  - compute_recurrence_features(): same leak-safe bank read using strict
    grad_unix < T (no H buffer needed for count-only recurrence — prior
    appearances are counted if they happened before T).

OUTCOMES (match training exactly, from BANK_SPEC.md + v4_outcome_rep.py):
  rdollar: y_rdollar col in bank, H=1800s, thr=0.0
  pk24:    y_pk24 col in bank,    H=86400s, thr=100.0

POOLS:
  time: first-10 buyers by block time (rank in whale_edges.parquet)
  size: top-10 buyers by USD (rank in whale_edges_bysize.parquet)
  For recurrence9 the es60 pool is also needed; the bank only has time+size
  so for 'es' we reuse the time pool (conservative fallback — same wallets,
  same leak-safe count). The parity gate checks live vs offline CSVs.

FEATURE ORDER follows meta.json exactly (53 total):
  enrich20 [0:20]  -- handled by compute_pregrad_features in core.pregrad_features
  REP24    [20:44] -- returned by compute_rep_features()
  recurrence9 [44:53] -- returned by compute_recurrence_features()
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirror BANK_SPEC.md / v4_outcome_rep.py)
# ---------------------------------------------------------------------------

OUTCOMES = [
    # (name, bank_col, H_seconds, clip_lo, clip_hi, good_threshold)
    ("rdollar", "y_rdollar", 1800,  -100, 1000,   0.0),
    ("pk24",    "y_pk24",    86400,    0, 3000, 100.0),
]

# 24 REP feature names in meta.json order (features[20:44])
REP_FEATURE_NAMES: list[str] = [
    "time_rdollar_repmean_mean",
    "time_rdollar_repmean_max",
    "time_rdollar_repmax_mean",
    "time_rdollar_ngood_sum",
    "time_rdollar_nhist",
    "time_rdollar_wmean",
    "size_rdollar_repmean_mean",
    "size_rdollar_repmean_max",
    "size_rdollar_repmax_mean",
    "size_rdollar_ngood_sum",
    "size_rdollar_nhist",
    "size_rdollar_wmean",
    "time_pk24_repmean_mean",
    "time_pk24_repmean_max",
    "time_pk24_repmax_mean",
    "time_pk24_ngood_sum",
    "time_pk24_nhist",
    "time_pk24_wmean",
    "size_pk24_repmean_mean",
    "size_pk24_repmean_max",
    "size_pk24_repmax_mean",
    "size_pk24_ngood_sum",
    "size_pk24_nhist",
    "size_pk24_wmean",
]

# 9 recurrence feature names in meta.json order (features[44:53])
RECURRENCE_FEATURE_NAMES: list[str] = [
    "pre_whale_nrep3_size",
    "pre_whale_nrep5_size",
    "pre_whale_totrep_size",
    "pre_whale_maxrep_size",
    "pre_whale_nrep3_time",
    "pre_whale_totrep_time",
    "pre_whale_nrep3_es",
    "pre_whale_totrep_es",
    "pre_whale_convergence",
]

V4_FEATURE_NAMES: list[str] = REP_FEATURE_NAMES + RECURRENCE_FEATURE_NAMES


# ---------------------------------------------------------------------------
# lagged_rep — VERBATIM from v4_outcome_rep.py lines 34-52
# ---------------------------------------------------------------------------


def lagged_rep(
    t: np.ndarray,
    y: np.ndarray,
    H: float,
    thr: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-row reputation from strictly-earlier, resolved picks of THIS wallet.

    t, y are this wallet's appearances sorted by grad_unix
    (y may contain NaN = unresolved/unlabelled).

    VERBATIM from v4_outcome_rep.py:34-52.
    """
    n = len(t)
    rep_mean = np.full(n, np.nan)
    rep_max = np.full(n, np.nan)
    rep_n = np.zeros(n)
    rep_ng = np.zeros(n)
    hmask = ~np.isnan(y)
    ht = t[hmask]
    hy = y[hmask]
    if len(ht) == 0:
        return rep_mean, rep_max, rep_n, rep_ng
    cum_y = np.cumsum(hy)
    cum_g = np.cumsum(hy > thr)
    cmax = np.maximum.accumulate(hy)
    k = np.searchsorted(ht, t - H, side="right")  # #resolved history picks available at each row
    pos = k > 0
    idx = k[pos] - 1
    rep_mean[pos] = cum_y[idx] / k[pos]
    rep_max[pos] = cmax[idx]
    rep_n[pos] = k[pos]
    rep_ng[pos] = cum_g[idx]
    return rep_mean, rep_max, rep_n, rep_ng


# ---------------------------------------------------------------------------
# WalletBankLookup — loads v4_wallet_bank.parquet into memory-efficient dicts
# ---------------------------------------------------------------------------


class WalletBankLookup:
    """In-memory index over the v4_wallet_bank.parquet for O(1) live lookup.

    The bank has 443,953 rows: (pool, wallet, mint, grad_unix, weight,
    y_rdollar, y_pk24).  We build one dict per pool:
      _bank[pool][wallet] = {
          "grad_unix": np.ndarray (sorted ascending),
          "y_rdollar": np.ndarray (float, NaN for unresolved),
          "y_pk24":    np.ndarray (float, NaN for unresolved),
          "weight":    np.ndarray (float),
      }
    so that a per-wallet lookup for a given T is a dict-get + numpy searchsorted.

    Pool values in the bank: "time" and "size".
    The "es" (es60) pool used by 2 of the 9 recurrence features is absent from
    the bank; we substitute the "time" pool as a conservative fallback.
    """

    def __init__(self, bank_path: "str | Path") -> None:
        import pandas as pd  # local import — only needed at load time

        df = pd.read_parquet(bank_path)
        self._bank: dict[str, dict[str, dict]] = {}
        for pool, grp in df.groupby("pool"):
            pool_dict: dict[str, dict] = {}
            for wallet, wgrp in grp.groupby("wallet"):
                wgrp = wgrp.sort_values("grad_unix")
                pool_dict[wallet] = {
                    "grad_unix": wgrp["grad_unix"].to_numpy(dtype=np.float64),
                    "y_rdollar": wgrp["y_rdollar"].to_numpy(dtype=np.float64),
                    "y_pk24":    wgrp["y_pk24"].to_numpy(dtype=np.float64),
                    "weight":    wgrp["weight"].to_numpy(dtype=np.float64),
                }
            self._bank[str(pool)] = pool_dict
        logger.info(
            "[v4_rep_builder] WalletBankLookup loaded: %d rows, pools=%s",
            len(df),
            list(self._bank.keys()),
        )

    def get_wallet_history(
        self,
        pool: str,
        wallet: str,
        grad_unix_T: float,
        H: float,
    ) -> dict | None:
        """Return all bank rows for (pool, wallet) with grad_unix <= T - H.

        Returns None if the wallet is not in the bank for this pool, or has no
        rows satisfying the leak-safe lag constraint.

        Returns a dict with keys: grad_unix, y_rdollar, y_pk24, weight
        (all numpy arrays, filtered to the leak-safe window).
        """
        pool_dict = self._bank.get(pool)
        if pool_dict is None:
            return None
        entry = pool_dict.get(wallet)
        if entry is None:
            return None
        cutoff = grad_unix_T - H
        # grad_unix is sorted ascending; searchsorted gives the count <= cutoff
        k = int(np.searchsorted(entry["grad_unix"], cutoff, side="right"))
        if k == 0:
            return None
        return {
            "grad_unix": entry["grad_unix"][:k],
            "y_rdollar": entry["y_rdollar"][:k],
            "y_pk24":    entry["y_pk24"][:k],
            "weight":    entry["weight"][:k],
        }

    def count_prior_appearances(
        self,
        pool: str,
        wallet: str,
        grad_unix_T: float,
    ) -> int:
        """Count bank rows for (pool, wallet) with grad_unix < T (strict).

        Used for recurrence features: 'prior' count = # of this wallet's earlier
        graduate appearances in the bank for this pool before T.
        """
        pool_dict = self._bank.get(pool)
        if pool_dict is None:
            return 0
        entry = pool_dict.get(wallet)
        if entry is None:
            return 0
        # strict less-than: searchsorted "left" gives count of values < grad_unix_T
        return int(np.searchsorted(entry["grad_unix"], grad_unix_T, side="left"))


# ---------------------------------------------------------------------------
# compute_rep_features — aggregates REP24 for one token at graduation time T
# ---------------------------------------------------------------------------


def compute_rep_features(
    time_buyers: list[dict],
    size_buyers: list[dict],
    grad_unix_T: float,
    bank: WalletBankLookup,
) -> dict:
    """Compute the 24 REP features for one graduating token.

    Aggregates the outcome-weighted wallet reputation over the token's early/big
    buyers.  Mirrors v4_outcome_rep.py:build_pool() aggregation (lines 86-99),
    adapted for the live one-token-at-a-time path.

    Parameters
    ----------
    time_buyers:
        List of dicts {wallet, weight} for the time pool (first-10 by time,
        excluding deployer and *pump addresses).  weight = usd_in, clipped >=1.
    size_buyers:
        List of dicts {wallet, weight} for the size pool (top-10 by USD).
        weight = total_usd, clipped >=1.
    grad_unix_T:
        Token's graduation Unix timestamp (float seconds).
    bank:
        WalletBankLookup instance (pre-loaded from v4_wallet_bank.parquet).

    Returns
    -------
    dict
        24 REP features (REP_FEATURE_NAMES), all float, fillna 0.
    """
    results: dict = {}
    for pool_tag, buyers in [("time", time_buyers), ("size", size_buyers)]:
        for nm, _col, H, _clip_lo, _clip_hi, thr in OUTCOMES:
            y_col = f"y_{nm}"
            # Collect per-buyer repmean, repmax, ngood, weight
            rm_vals: list[float] = []
            rx_vals: list[float] = []
            ng_vals: list[float] = []
            wt_vals: list[float] = []
            ng_sum: float = 0.0

            for b in buyers:
                wallet = b["wallet"]
                wt = float(b.get("weight", 1.0))
                hist = bank.get_wallet_history(pool_tag, wallet, grad_unix_T, float(H))
                if hist is None:
                    # No history: cold start, contributes 0 (fillna 0 at token level)
                    continue
                y = hist[y_col]  # numpy array, may contain NaN
                valid_mask = ~np.isnan(y)
                if not valid_mask.any():
                    # All NaN → no resolved history
                    continue
                y_valid = y[valid_mask]
                rm = float(y_valid.mean())
                rx = float(y_valid.max())
                ng = float((y_valid > thr).sum())
                rm_vals.append(rm)
                rx_vals.append(rx)
                ng_vals.append(ng)
                wt_vals.append(wt)
                ng_sum += ng

            prefix = f"{pool_tag}_{nm}"
            if rm_vals:
                rm_arr = np.array(rm_vals, dtype=np.float64)
                rx_arr = np.array(rx_vals, dtype=np.float64)
                wt_arr = np.array(wt_vals, dtype=np.float64)
                results[f"{prefix}_repmean_mean"] = float(rm_arr.mean())
                results[f"{prefix}_repmean_max"] = float(rm_arr.max())
                results[f"{prefix}_repmax_mean"] = float(rx_arr.mean())
                results[f"{prefix}_ngood_sum"] = float(ng_sum)
                results[f"{prefix}_nhist"] = float(len(rm_vals))
                if wt_arr.sum() > 0:
                    results[f"{prefix}_wmean"] = float(
                        np.average(rm_arr, weights=wt_arr)
                    )
                else:
                    results[f"{prefix}_wmean"] = 0.0
            else:
                # No buyers with resolved history → cold start → all 0
                results[f"{prefix}_repmean_mean"] = 0.0
                results[f"{prefix}_repmean_max"] = 0.0
                results[f"{prefix}_repmax_mean"] = 0.0
                results[f"{prefix}_ngood_sum"] = 0.0
                results[f"{prefix}_nhist"] = 0.0
                results[f"{prefix}_wmean"] = 0.0

    # Ensure all 24 features are present (fillna 0 for any missing)
    for fname in REP_FEATURE_NAMES:
        results.setdefault(fname, 0.0)
    return results


# ---------------------------------------------------------------------------
# compute_recurrence_features — 9 pre_whale_* features
# ---------------------------------------------------------------------------


def compute_recurrence_features(
    time_buyers: list[dict],
    size_buyers: list[dict],
    grad_unix_T: float,
    bank: WalletBankLookup,
) -> dict:
    """Compute the 9 recurrence (pre_whale_*) features for one graduating token.

    Mirrors build_whale_features.py:pool_features() adapted for live one-token
    path.  For each pool, counts each buyer's prior appearances in the bank
    (grad_unix < T, strict, no H buffer — count-only, not outcome-weighted).
    Aggregates nrep3, nrep5, totrep, maxrep over the token's buyers.

    The 9 features from meta.json:
      pre_whale_nrep3_size, pre_whale_nrep5_size, pre_whale_totrep_size, pre_whale_maxrep_size
      pre_whale_nrep3_time, pre_whale_totrep_time
      pre_whale_nrep3_es, pre_whale_totrep_es  (es60 pool → fallback: time pool)
      pre_whale_convergence (= pre_whale_nrep3_size)

    Parameters
    ----------
    time_buyers, size_buyers:
        Same buyer lists as compute_rep_features.
    grad_unix_T:
        Token's graduation Unix timestamp.
    bank:
        WalletBankLookup instance.

    Returns
    -------
    dict
        9 recurrence features, all float.
    """
    results: dict = {}

    def _pool_agg(pool_tag: str, buyers: list[dict]) -> dict[str, float]:
        priors: list[int] = []
        for b in buyers:
            wallet = b["wallet"]
            pr = bank.count_prior_appearances(pool_tag, wallet, grad_unix_T)
            priors.append(pr)
        if not priors:
            return {
                "nrep3": 0, "nrep5": 0, "totrep": 0, "maxrep": 0,
            }
        arr = np.array(priors, dtype=np.int64)
        return {
            "nrep3": int((arr >= 3).sum()),
            "nrep5": int((arr >= 5).sum()),
            "totrep": int(arr.sum()),
            "maxrep": int(arr.max()),
        }

    size_agg = _pool_agg("size", size_buyers)
    time_agg = _pool_agg("time", time_buyers)
    # es60 pool not in the bank → fallback to time pool (conservative; same wallets)
    es_agg = _pool_agg("time", time_buyers)

    results["pre_whale_nrep3_size"] = float(size_agg["nrep3"])
    results["pre_whale_nrep5_size"] = float(size_agg["nrep5"])
    results["pre_whale_totrep_size"] = float(size_agg["totrep"])
    results["pre_whale_maxrep_size"] = float(size_agg["maxrep"])
    results["pre_whale_nrep3_time"] = float(time_agg["nrep3"])
    results["pre_whale_totrep_time"] = float(time_agg["totrep"])
    results["pre_whale_nrep3_es"] = float(es_agg["nrep3"])
    results["pre_whale_totrep_es"] = float(es_agg["totrep"])
    results["pre_whale_convergence"] = float(size_agg["nrep3"])  # = pre_whale_nrep3_size

    # Ensure all 9 features are present
    for fname in RECURRENCE_FEATURE_NAMES:
        results.setdefault(fname, 0.0)
    return results


# ---------------------------------------------------------------------------
# extract_buyers_from_swaps — identify time/size pool buyers from a swap tape
# ---------------------------------------------------------------------------


def extract_buyers_from_swaps(
    swaps: list[dict],
    *,
    deployer: Optional[str] = None,
    top_k: int = 10,
) -> tuple[list[dict], list[dict]]:
    """Extract the time-pool and size-pool buyers from a pre-grad swap tape.

    Mirrors the whale_edges / whale_edges_bysize logic from the lab:
    - time pool: first-10 unique buyers by block_time order
    - size pool: top-10 unique buyers by cumulative usd_in (vol field)
    Both pools exclude the deployer and wallets whose address ends with "pump".

    Parameters
    ----------
    swaps:
        Pre-graduation normalized swap dicts (§7.1 shape, rel < 0, side=buy/sell).
        Should be ALREADY sorted by (block_time, signature) — the same order
        compute_pregrad_features uses.
    deployer:
        Optional deployer wallet address to exclude.
    top_k:
        Number of buyers per pool (default 10, matching the lab's first-10/top-10).

    Returns
    -------
    (time_buyers, size_buyers)
        Each is a list of dicts {wallet, weight} where:
          time: weight = cumulative vol (usd_in) of that buyer, clipped >= 1
          size: weight = cumulative vol (total_usd) of that buyer, clipped >= 1
    """
    buy_swaps = [s for s in swaps if s.get("side") == "buy" and s.get("rel", 0) < 0]

    def _is_excluded(wallet: str) -> bool:
        if not wallet:
            return True
        if deployer and wallet == deployer:
            return True
        return wallet.endswith("pump")

    # Accumulate per-buyer total volume and record first appearance order
    buyer_vol: dict[str, float] = {}
    first_appearance: list[str] = []
    for s in buy_swaps:
        wallet = s.get("owner") or ""
        if _is_excluded(wallet):
            continue
        vol = float(s.get("vol", 0.0))
        if wallet not in buyer_vol:
            buyer_vol[wallet] = 0.0
            first_appearance.append(wallet)
        buyer_vol[wallet] += vol

    # Time pool: first-top_k unique buyers by arrival order
    time_buyers = [
        {"wallet": w, "weight": max(1.0, buyer_vol[w])}
        for w in first_appearance[:top_k]
    ]

    # Size pool: top-top_k unique buyers by cumulative vol
    size_sorted = sorted(buyer_vol.items(), key=lambda x: x[1], reverse=True)
    size_buyers = [
        {"wallet": w, "weight": max(1.0, v)}
        for w, v in size_sorted[:top_k]
    ]

    return time_buyers, size_buyers
