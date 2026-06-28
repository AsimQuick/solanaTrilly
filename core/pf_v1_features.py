# ---
# module: core.pf_v1_features
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: math, collections
# ---
"""pf_v1_features.py — pre-grad feature builder for trilly_pf_v1.

Ports the per-token logic from ``analysis/graduated_pf/build_universe.py``
(``day_features``) to operate on the live billy tape swap-dict format produced
by ``core/firehose/shared_tape.py:norm_row_to_swap_dict``.

FIELD MAPPING (pumpfundata  →  live swap-dict)
==============================================
  lamports_amount / 1e9  →  vol_sol   (already in SOL; the NormRow uses vol_sol)
  token_amount           →  token_amount
  user_wallet            →  owner
  action                 →  side   ("buy" | "sell")
  timestamp              →  block_time
  slot_number            →  slot

GUARDS vs training
==================
  training:   len(pre_grad_swaps) >= 2
  training:   sum(abs(sol)) > 0  AND  max(real_lamports_reserve) > 0
  live:       len(pre_grad_swaps) >= 2  (same)
  live:       sum(abs(vol_sol)) > 0     (SOL-only leg; real_reserve not carried)
              # NOTE: the real_lamports_reserve check (DROP_ZERO_LEG's second leg)
              # is OMITTED live: schema-A NormRows carry depth_sol (= real_sol_reserves/1e9)
              # only when the field is present in the raw billy row, and it is NOT a required
              # field of norm_row_to_swap_dict.  Skipping this check live is documented as a
              # deliberate deviation — it may admit a handful of backfill-contaminated tapes
              # but cannot produce a false negative for a genuine grad (real grads always
              # have real SOL legs, matching lab intent).

FEATURE DEFINITIONS (from config.json / build_universe.py)
===========================================================
  f_value     = net_flow_sol  = sum(buy SOL) - sum(sell SOL)   over pre-grad swaps
  f_momentum  = buyer_accel   = uniq_buyers(2nd half) / (uniq_buyers(1st half) + 1)
                                where mid = first_t + (gts - first_t) / 2
  f_size      = log1p(total_sol)  where total_sol = sum(abs(vol_sol)) all pre-grad swaps
  f_holders   = net_holders   = count of wallets with net_token_signed_sum > 0
                                (buy adds +token_amount, sell adds -token_amount)
  f_life      = curve_life_s  = gts - first_swap_block_time
  f_top1      = top1_buyer_share = top buyer's buy_sol / total_buy_sol (grouped by wallet)

PUBLIC API
==========
  compute_pf_v1_features(swaps, grad_ts) -> dict | None

Returns the 6 features keyed by config feature names, or None if guards fail.
"""
from __future__ import annotations

import math
from collections import defaultdict


def compute_pf_v1_features(
    swaps: list[dict],
    grad_ts: float,
) -> dict | None:
    """Compute the 6 pf_v1 features from a pre-grad swap list.

    Parameters
    ----------
    swaps:
        Raw swap dicts as produced by ``norm_row_to_swap_dict`` (or equivalent).
        Keys used: ``block_time``, ``slot``, ``side``, ``vol_sol``,
        ``token_amount``, ``owner``.
        May contain post-grad swaps — they are filtered to ``block_time <= grad_ts``
        before any computation (causal / leak-free, exactly matching training).
    grad_ts:
        Graduation timestamp (epoch seconds, float).  Matches ``gts`` in
        ``build_universe.py``.  Pre-grad window = [0, grad_ts].

    Returns
    -------
    dict with keys ``f_value, f_momentum, f_size, f_holders, f_life, f_top1``,
    or ``None`` if:
      - fewer than 2 pre-grad swaps survive the filter, OR
      - total |vol_sol| is zero (DROP_ZERO_LEG SOL-leg guard).

    Notes
    -----
    - Swaps are sorted by (block_time, slot) before computation, matching
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

    # --- f_value: net_flow_sol ---
    net_flow_sol = buy_sol_total - sell_sol_total

    # --- f_size: log1p(total_sol) ---
    f_size = math.log1p(total_sol)

    # --- f_life: curve_life_s ---
    first_t = float(pre[0]["block_time"])
    curve_life_s = grad_ts - first_t

    # --- f_momentum: buyer_accel ---
    # mid = first_t + (gts - first_t) / 2
    mid = first_t + (grad_ts - first_t) / 2.0
    ub1: set[str] = set()  # unique buyers in [start, mid]
    ub2: set[str] = set()  # unique buyers in (mid, gts]
    for s in buys:
        owner = s.get("owner") or ""
        bt_s = float(s.get("block_time", 0))
        if bt_s <= mid:
            ub1.add(owner)
        else:
            ub2.add(owner)
    buyer_accel = len(ub2) / (len(ub1) + 1.0)

    # --- f_holders: net_holders ---
    # wallet_net = sum of signed token flows (buy=+token_amount, sell=-token_amount)
    wallet_net: dict[str, float] = defaultdict(float)
    for s in pre:
        owner = s.get("owner") or ""
        tok = float(s.get("token_amount") or 0.0)
        side = s.get("side") or "buy"
        wallet_net[owner] += tok if side == "buy" else -tok
    net_holders = sum(1 for v in wallet_net.values() if v > 0)

    # --- f_top1: top1_buyer_share ---
    # Buyer SOL grouped by wallet; top buyer's share of total buy SOL
    buyer_by_wallet: dict[str, float] = defaultdict(float)
    for s in buys:
        owner = s.get("owner") or ""
        buyer_by_wallet[owner] += abs(float(s.get("vol_sol", 0.0)))
    if buy_sol_total > 0.0 and buyer_by_wallet:
        top1_share = max(buyer_by_wallet.values()) / buy_sol_total
    else:
        top1_share = 0.0

    return {
        "f_value": float(net_flow_sol),
        "f_momentum": float(buyer_accel),
        "f_size": float(f_size),
        "f_holders": float(net_holders),
        "f_life": float(curve_life_s),
        "f_top1": float(top1_share),
    }
