# ---
# module: core.pf_features
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: math, collections
# ---
"""pf_features.py — pre-grad feature builder for all trilly_pf_v* models.

Supersedes pf_v1_features.py.  Computes ALL 13 base features (6 locked +
7 enrich) from the live billy tape swap-dict format as produced by
``core/firehose/shared_tape.py:norm_row_to_swap_dict``.

FIELD MAPPING (pumpfundata  ->  live swap-dict)
===============================================
  lamports_amount / 1e9  ->  vol_sol   (already in SOL; NormRow uses vol_sol)
  token_amount           ->  token_amount
  user_wallet            ->  owner
  action                 ->  side   ("buy" | "sell")
  timestamp              ->  block_time
  slot_number            ->  slot
  virtual_lamports/virtual_token_reserve -> price  (schema-A PX field)

GUARDS vs training (build_universe.py)
======================================
  training:   len(pre_grad_swaps) >= 2
  training:   sum(abs(sol)) > 0  AND  max(real_lamports_reserve) > 0
  live:       len(pre_grad_swaps) >= 2  (same)
  live:       sum(abs(vol_sol)) > 0     (SOL-only leg; rsol not carried live)
              # NOTE: the real_lamports_reserve check (DROP_ZERO_LEG's 2nd leg)
              # is OMITTED live: schema-A NormRows carry depth_sol only when
              # present in the raw billy row.  Documented deliberate deviation —
              # real grads always have real SOL legs, matching lab intent.

LOCKED 6 FEATURES (f_value, f_momentum, f_size, f_holders, f_life, f_top1)
===========================================================================
  f_value     = net_flow_sol  = sum(buy SOL) - sum(sell SOL) pre-grad
  f_momentum  = buyer_accel   = uniq_buyers(2nd half) / (uniq_buyers(1st half)+1)
  f_size      = log1p(total_sol)
  f_holders   = net_holders   = wallets with net_token_signed_sum > 0
  f_life      = curve_life_s  = gts - first_swap_block_time
  f_top1      = top1_buyer_share = max_wallet_buy_sol / total_buy_sol

ENRICH 7 FEATURES (new_buyers_last60, new_buyers_last300, diamond_frac,
                   top5_buyer_share, repeat_buyer_frac, price_ret, price_maxrun)
==========================================================================
  new_buyers_last60   = # buyers whose first buy >= gts-60s
  new_buyers_last300  = # buyers whose first buy >= gts-300s
  diamond_frac        = fraction of buyer-wallets that NEVER appear as seller
                        pre-grad (~buyer_in_seller_set).mean()
  top5_buyer_share    = sum(top-5 buyers' buy-SOL) / total_buy_SOL; 0 if total<=0
  repeat_buyer_frac   = fraction of buyer-wallets with >= 2 buys
  price_ret           = last_px / first_px - 1  over pre-grad swaps with px>0
  price_maxrun        = max(px) / first_px - 1

  EDGE CASES (per build_universe.py):
    nbuyers == 0 -> all enrich7 = 0
    no px > 0 swaps -> price_ret = price_maxrun = 0

PRICE FIELD (px source)
=======================
  px is the ``price`` field in the live swap-dict.
  In schema-A NormRows: price = virtual_sol_reserves / virtual_token_reserves
  (already divided; set by the Birdeye mapper / norm_row_to_swap_dict).
  first_px = first swap with price > 0.

PUBLIC API
==========
  compute_pf_features(swaps, grad_ts) -> dict | None
    Returns ALL 13 features keyed by contract names, or None if guards fail.
"""
from __future__ import annotations

import math
from collections import defaultdict


def compute_pf_features(
    swaps: list[dict],
    grad_ts: float,
) -> dict | None:
    """Compute all 13 pf features from a pre-grad swap list.

    Parameters
    ----------
    swaps:
        Raw swap dicts as produced by ``norm_row_to_swap_dict`` (or equivalent).
        Keys used: ``block_time``, ``slot``, ``side``, ``vol_sol``,
        ``token_amount``, ``owner``, ``price``.
        May contain post-grad swaps — they are filtered to ``block_time <= grad_ts``
        (causal / leak-free, matching training).
    grad_ts:
        Graduation timestamp (epoch seconds, float).  Matches ``gts`` in
        ``build_universe.py``.

    Returns
    -------
    dict with keys matching contract feature_vector.order (all 13 feature names),
    or ``None`` if:
      - fewer than 2 pre-grad swaps survive the filter, OR
      - total |vol_sol| is zero (DROP_ZERO_LEG SOL-leg guard).

    Notes
    -----
    - Swaps sorted by (block_time, slot) before computation, matching
      ``build_universe.py``'s ``sort_values(["timestamp","slot_number"])``.
    - The ``real_lamports_reserve > 0`` check from training's DROP_ZERO_LEG
      is NOT applied here — see module docstring for rationale.
    """
    # --- Filter: pre-grad only (block_time <= grad_ts) ---
    pre: list[dict] = []
    for s in swaps:
        bt = s.get("block_time")
        if bt is not None and float(bt) <= grad_ts:
            pre.append(s)

    # Guard 1: need >= 2 pre-grad swaps (training parity)
    if len(pre) < 2:
        return None

    # Sort by (block_time, slot) — mirrors training's sort_values
    pre.sort(key=lambda s: (float(s.get("block_time", 0)), int(s.get("slot", 0))))

    # Guard 2: SOL-only DROP_ZERO_LEG
    total_sol = sum(abs(float(s.get("vol_sol", 0.0))) for s in pre)
    if total_sol <= 0.0:
        return None

    # --- Separate buys / sells ---
    buys = [s for s in pre if s.get("side") == "buy"]
    sells = [s for s in pre if s.get("side") == "sell"]

    buy_sol_total = sum(abs(float(s.get("vol_sol", 0.0))) for s in buys)
    sell_sol_total = sum(abs(float(s.get("vol_sol", 0.0))) for s in sells)

    # ==========================================================================
    # LOCKED 6 FEATURES
    # ==========================================================================

    # --- f_value: net_flow_sol ---
    net_flow_sol = buy_sol_total - sell_sol_total

    # --- f_size: log1p(total_sol) ---
    f_size = math.log1p(total_sol)

    # --- f_life: curve_life_s ---
    first_t = float(pre[0]["block_time"])
    curve_life_s = grad_ts - first_t

    # --- f_momentum: buyer_accel ---
    mid = first_t + (grad_ts - first_t) / 2.0
    ub1: set[str] = set()
    ub2: set[str] = set()
    for s in buys:
        owner = s.get("owner") or ""
        bt_s = float(s.get("block_time", 0))
        if bt_s <= mid:
            ub1.add(owner)
        else:
            ub2.add(owner)
    buyer_accel = len(ub2) / (len(ub1) + 1.0)

    # --- f_holders: net_holders ---
    wallet_net: dict[str, float] = defaultdict(float)
    for s in pre:
        owner = s.get("owner") or ""
        tok = float(s.get("token_amount") or 0.0)
        side = s.get("side") or "buy"
        wallet_net[owner] += tok if side == "buy" else -tok
    net_holders = sum(1 for v in wallet_net.values() if v > 0)

    # --- f_top1: top1_buyer_share ---
    buyer_by_wallet: dict[str, float] = defaultdict(float)
    for s in buys:
        owner = s.get("owner") or ""
        buyer_by_wallet[owner] += abs(float(s.get("vol_sol", 0.0)))
    if buy_sol_total > 0.0 and buyer_by_wallet:
        top1_share = max(buyer_by_wallet.values()) / buy_sol_total
    else:
        top1_share = 0.0

    # ==========================================================================
    # ENRICH 7 FEATURES
    # (per build_universe.py lines ~83-120; nbuyers==0 -> all enrich = 0)
    # ==========================================================================

    # buyer-indexed aggregates (all keyed by owner/wallet)
    # bw: dict wallet -> buy_sol (sorted descending for top-k)
    bw_dict: dict[str, float] = buyer_by_wallet  # already computed above
    nbuyers = len(bw_dict)

    if nbuyers > 0:
        # Per-buyer first-buy timestamp (= min block_time among that owner's buys)
        first_buy_t: dict[str, float] = {}
        for s in buys:
            owner = s.get("owner") or ""
            bt_s = float(s.get("block_time", 0))
            if owner not in first_buy_t or bt_s < first_buy_t[owner]:
                first_buy_t[owner] = bt_s

        # Per-buyer buy count
        n_buys_by_wallet: dict[str, int] = defaultdict(int)
        for s in buys:
            owner = s.get("owner") or ""
            n_buys_by_wallet[owner] += 1

        # Seller set
        seller_set: set[str] = {s.get("owner") or "" for s in sells}

        # diamond_frac: fraction of buyer-wallets that never appear in seller_set
        # build_universe.py: (~sold_b).mean() where sold_b = bidx.isin(seller_set)
        n_diamonds = sum(1 for w in bw_dict if w not in seller_set)
        diamond_frac = float(n_diamonds) / float(nbuyers)

        # repeat_buyer_frac: fraction of buyer-wallets with >= 2 buys
        n_repeat = sum(1 for w in bw_dict if n_buys_by_wallet[w] >= 2)
        repeat_buyer_frac = float(n_repeat) / float(nbuyers)

        # top5_buyer_share: top-5 buyers' buy-SOL / total buy-SOL
        # sort descending by SOL, take first 5
        sorted_bw = sorted(bw_dict.values(), reverse=True)
        if buy_sol_total > 0.0:
            top5_buyer_share = float(sum(sorted_bw[:5])) / float(buy_sol_total)
        else:
            top5_buyer_share = 0.0

        # new_buyers_last60 / new_buyers_last300
        # build_universe.py: int((first_buy_t >= gts - 60).sum())
        new_buyers_last60 = sum(
            1 for w, t in first_buy_t.items() if t >= (grad_ts - 60.0)
        )
        new_buyers_last300 = sum(
            1 for w, t in first_buy_t.items() if t >= (grad_ts - 300.0)
        )
    else:
        diamond_frac = 0.0
        repeat_buyer_frac = 0.0
        top5_buyer_share = 0.0
        new_buyers_last60 = 0
        new_buyers_last300 = 0

    # price_ret / price_maxrun
    # px is the live `price` field (schema-A: virtual_sol/virtual_token set by mapper)
    # first_px = first swap with price > 0
    px_values: list[float] = []
    for s in pre:
        px = float(s.get("price", 0.0) or 0.0)
        if px > 0.0:
            px_values.append(px)

    if px_values:
        first_px = px_values[0]
        # price_ret: last px (last swap's price) / first_px - 1
        # build_universe.py: float(g.px.iloc[-1]/first_px - 1.0)
        # We need last px from pre (sorted), not last px > 0
        last_px = float(pre[-1].get("price", 0.0) or 0.0)
        price_ret = float(last_px / first_px - 1.0)
        price_maxrun = float(max(px_values) / first_px - 1.0)
    else:
        price_ret = 0.0
        price_maxrun = 0.0

    return {
        "f_value": float(net_flow_sol),
        "f_momentum": float(buyer_accel),
        "f_size": float(f_size),
        "f_holders": float(net_holders),
        "f_life": float(curve_life_s),
        "f_top1": float(top1_share),
        "new_buyers_last60": float(new_buyers_last60),
        "new_buyers_last300": float(new_buyers_last300),
        "diamond_frac": float(diamond_frac),
        "top5_buyer_share": float(top5_buyer_share),
        "repeat_buyer_frac": float(repeat_buyer_frac),
        "price_ret": float(price_ret),
        "price_maxrun": float(price_maxrun),
    }
