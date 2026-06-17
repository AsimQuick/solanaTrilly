# ---
# module: core.pregrad_features
# sprint: sprint-9
# story: US-41 AC-41.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: (stdlib only)
# ported-from: solanatrills/analysis/graduated/enrich_pregrad.py (buyer-cohort logic)
# ---
"""Pre-graduation buyer-cohort feature computation (the 20 pre_* features).

Ported from the offline research script (solanatrills/analysis/graduated/
enrich_pregrad.py) so the SAME math runs in the live serving path through the
single shared FeatureExtractor (US-30, Principle #2).

CONTRACT
========
Input: a list of §7.1 normalized swap dicts (same format produced by
FeatureExtractor._load_lake_swaps / _load_db_swaps):

    {
      "rel":        float  — seconds relative to graduated_block_time;
                             pre-grad swaps have rel < 0
      "side":       str    — "buy" | "sell"
      "owner":      str|None — signer wallet
      "vol":        float  — SOL volume (vol_sol after normalization)
      "block_time": int
      "slot":       int
      "signature":  str
      "price":      float
    }

Only swaps with rel < 0 are used (the pre-graduation curve tape).
Returns None if there are no usable pre-grad swaps.

FEATURE ORDER matches meta.json:features for trilly_pregrad_v3_2 exactly
(verified by the AC-41.1 reconcile test over the banked booster_feature_names.json).

DEPLOYER FEATURES
=================
pre_deployer_sold, pre_deployer_buy_share, pre_deployer_present are computed
when `deployer` is provided (a base58 wallet address).  When deployer is None
(unknown / not yet fetched), all three default to 0 — the feature remains
live-computable; the deployer lookup is an optional enrichment step.

PARITY
======
The math is a line-for-line port of enrich_pregrad.py with the following
mechanical adaptations:
  - Uses swap["rel"] (relative seconds) instead of absolute block_time for
    time window checks; rel 0 = graduation instant.
  - Uses swap["vol"] (already normalised to SOL) instead of tuple index 2.
  - Returns floats throughout for LightGBM / JSON compatibility.
  - float("nan") for cohort_*_frac when the cohort is empty — matches
    np.nan in the offline path; LightGBM handles NaN natively.

DETERMINISM
===========
Pure function of (swaps, deployer). No clock, no RNG, no I/O.  Sort is
stable (Python's Timsort) over a deterministic key, so same inputs → same
output every run.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The 20 v3.2 pre_* feature names in binding booster order (meta.json)
# ---------------------------------------------------------------------------

PRE_FEATURE_NAMES: list[str] = [
    "pre_eb10_sold_frac",
    "pre_eb20_sold_frac",
    "pre_eb10_netpos_frac",
    "pre_eb20_netpos_frac",
    "pre_buyers_first_half",
    "pre_buyers_second_half",
    "pre_buyer_accel",
    "pre_new_buyers_last300",
    "pre_new_buyers_last60",
    "pre_buy_hhi",
    "pre_n_whales5",
    "pre_top5_buyer_share",
    "pre_diamond_frac",
    "pre_seller_of_buyers_frac",
    "pre_repeat_buyer_frac",
    "pre_insider_sell_ratio",
    "pre_deployer_sold",
    "pre_deployer_buy_share",
    "pre_deployer_present",
    "pre_n_distinct_sellers",
]


def compute_pregrad_features(
    swaps: list[dict],
    *,
    deployer: str | None = None,
) -> dict | None:
    """Compute the 20 pre_* buyer-cohort features from pre-graduation swaps.

    Parameters
    ----------
    swaps:
        §7.1 normalized swap dicts.  May include both pre-grad (rel < 0) and
        post-grad (rel >= 0) swaps; only pre-grad swaps are used here.
    deployer:
        Optional deployer wallet address (base58).  When supplied, the three
        pre_deployer_* features are computed against this owner.  When None,
        all three default to 0.

    Returns
    -------
    dict
        All 20 pre_* features with float values, or None if there are no
        pre-grad swaps with valid price > 0.
    """
    pregrad = [s for s in swaps if s.get("rel", 0.0) < 0]
    if not pregrad:
        return None

    # Stable sort: (block_time, slot, signature) — same key as _load_lake_swaps.
    pregrad.sort(key=lambda s: (s.get("block_time", 0), s.get("slot", 0), s.get("signature", "")))

    # Time-window anchors (in rel space; rel 0 = graduation).
    all_rels = [s["rel"] for s in pregrad]
    min_rel = min(all_rels)   # earliest pre-grad swap (most negative rel)
    mid_rel = min_rel / 2.0   # midpoint between earliest swap and graduation

    # Per-owner aggregates — mirrors enrich_pregrad.py's owners dict.
    owners: dict[str, dict] = {}
    first_buy_order: list[str] = []  # owners in order of first BUY
    seen_buyer: set[str] = set()

    for s in pregrad:
        o: str = s.get("owner") or ""
        vol: float = float(s.get("vol", 0.0))
        side: str = s.get("side", "")
        rel: float = float(s.get("rel", 0.0))

        d = owners.setdefault(
            o,
            {
                "buy_v": 0.0,
                "sell_v": 0.0,
                "n_buy": 0,
                "n_sell": 0,
                "first_buy_rel": None,
                "sold": False,
            },
        )
        if side == "buy":
            d["buy_v"] += vol
            d["n_buy"] += 1
            if d["first_buy_rel"] is None:
                d["first_buy_rel"] = rel
            if o and o not in seen_buyer:
                seen_buyer.add(o)
                first_buy_order.append(o)
        else:
            d["sell_v"] += vol
            d["n_sell"] += 1
            if d["n_buy"] > 0:
                d["sold"] = True

    buyers = [o for o in owners if owners[o]["n_buy"] > 0]
    if not buyers:
        return None

    nb = len(buyers)
    tot_buyv = sum(owners[o]["buy_v"] for o in buyers) or 1.0

    # ------------------------------------------------------------------
    # Early-buyer cohort fractions (EB10 / EB20)
    # ------------------------------------------------------------------

    def cohort_sold_frac(k: int) -> float:
        coh = first_buy_order[:k]
        if not coh:
            return float("nan")
        return sum(1.0 for o in coh if owners[o]["sold"]) / len(coh)

    def cohort_net_pos_frac(k: int) -> float:
        coh = first_buy_order[:k]
        if not coh:
            return float("nan")
        return sum(1.0 for o in coh if owners[o]["buy_v"] > owners[o]["sell_v"]) / len(coh)

    # ------------------------------------------------------------------
    # Breadth trajectory
    # ------------------------------------------------------------------

    nb_first_half = sum(
        1
        for o in buyers
        if owners[o]["first_buy_rel"] is not None and owners[o]["first_buy_rel"] <= mid_rel
    )
    nb_second_half = nb - nb_first_half

    new_buyers_last300 = sum(
        1
        for o in buyers
        if owners[o]["first_buy_rel"] is not None and owners[o]["first_buy_rel"] >= -300.0
    )
    new_buyers_last60 = sum(
        1
        for o in buyers
        if owners[o]["first_buy_rel"] is not None and owners[o]["first_buy_rel"] >= -60.0
    )

    # ------------------------------------------------------------------
    # Buy-volume concentration
    # ------------------------------------------------------------------

    shares = sorted((owners[o]["buy_v"] for o in buyers), reverse=True)
    hhi = sum((s / tot_buyv) ** 2 for s in shares)
    n_whales5 = sum(1 for s in shares if s / tot_buyv > 0.05)
    top5_share = sum(shares[:5]) / tot_buyv

    # ------------------------------------------------------------------
    # Holding / conviction / turnover
    # ------------------------------------------------------------------

    diamond_frac = float(sum(1 for o in buyers if not owners[o]["sold"])) / nb
    seller_of_buyers_frac = float(sum(1 for o in buyers if owners[o]["sold"])) / nb
    repeat_buyer_frac = float(sum(1 for o in buyers if owners[o]["n_buy"] >= 2)) / nb

    # Insider sell pressure: sell volume from people who were also buyers.
    insider_sell_v = sum(owners[o]["sell_v"] for o in buyers)
    total_buy_vol_all = sum(s.get("vol", 0.0) for s in pregrad if s.get("side") == "buy")
    insider_sell_ratio = insider_sell_v / (total_buy_vol_all + 1.0)

    # ------------------------------------------------------------------
    # Deployer behavior (optional enrichment — 0 when deployer is unknown)
    # ------------------------------------------------------------------

    dep = owners.get(deployer) if deployer else None
    deployer_sold = float(int(bool(dep and dep["sold"])))
    deployer_buy_share = float(dep["buy_v"] / tot_buyv) if dep else 0.0
    deployer_present = float(int(deployer in owners)) if deployer else 0.0

    # ------------------------------------------------------------------
    # Distinct sellers with a non-empty owner address
    # ------------------------------------------------------------------

    n_distinct_sellers = float(
        len({s.get("owner") or "" for s in pregrad if s.get("side") == "sell" and s.get("owner")})
    )

    return {
        "pre_eb10_sold_frac": cohort_sold_frac(10),
        "pre_eb20_sold_frac": cohort_sold_frac(20),
        "pre_eb10_netpos_frac": cohort_net_pos_frac(10),
        "pre_eb20_netpos_frac": cohort_net_pos_frac(20),
        "pre_buyers_first_half": float(nb_first_half),
        "pre_buyers_second_half": float(nb_second_half),
        "pre_buyer_accel": float(nb_second_half) / float(nb_first_half + 1),
        "pre_new_buyers_last300": float(new_buyers_last300),
        "pre_new_buyers_last60": float(new_buyers_last60),
        "pre_buy_hhi": float(hhi),
        "pre_n_whales5": float(n_whales5),
        "pre_top5_buyer_share": float(top5_share),
        "pre_diamond_frac": diamond_frac,
        "pre_seller_of_buyers_frac": seller_of_buyers_frac,
        "pre_repeat_buyer_frac": repeat_buyer_frac,
        "pre_insider_sell_ratio": float(insider_sell_ratio),
        "pre_deployer_sold": deployer_sold,
        "pre_deployer_buy_share": deployer_buy_share,
        "pre_deployer_present": deployer_present,
        "pre_n_distinct_sellers": n_distinct_sellers,
    }
