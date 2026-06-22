# ---
# module: copytrade.entry_features
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: numpy
# ---
"""Non-leaky entry features for the curve-stage P(graduate) classifier (cohort
``copy_2026-06-22_curvestage``).

BYTE-FAITHFUL PORT of the lab's
``solanatrills/analysis/whale_graph/entry_select_build.py::entry_features`` (the
feature math the seed classifier was trained on).  Parity guardrail G1/G2
(EPIC-copy-curvestage-integration):

- **G1 (curve_frac):** ``curve_frac = pre_sol_in / 85`` — the TAPE net-buy-SOL
  proxy the lab calibrated theta=0.60 to, NOT the on-chain ``vsol`` reserve.
  Computed here from ``pre_sol_in`` so the gate is parity-true by construction.
- **G2 (train/serve source):** the live token tape is fetched from Birdeye
  ``seek_by_time`` — the SAME source the lab trained on — and mapped by
  ``birdeye_items_to_owner_tape`` which mirrors the lab's ``load_owner_tape``
  exactly (price = basePrice*quotePrice USD/token; usd = |quote.uiAmount|*
  quotePrice; sol = |quote.uiAmount|).  This keeps ``price_at_entry`` / ``fdv_proxy``
  on the lab's USD basis (the only basis-sensitive features) instead of the
  recorder's Helius SOL-ratio price.  Verified by the G2 distribution parity harness.

At the LIVE buy instant the token has not graduated, so ``gts=None`` (curve_ok=True
for all prefix swaps) — identical to how the lab scores an on-curve entry.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: Curve graduation threshold (SOL).  curve_frac = pre_sol_in / GRAD_SOL.
GRAD_SOL: float = 85.0

#: The 20 model features, IN THE ORDER the seed LGBM was trained on
#: (etg_room_classifier.py FEATS).  Do NOT reorder — LGBM is positional via the
#: DataFrame column order used at fit time.
FEATS: list[str] = [
    "tok_age_s", "wallet_buy_usd", "price_at_entry", "fdv_proxy", "pre_sol_in",
    "pre_n_trades", "pre_n_buys", "pre_n_sells", "pre_uniq_buyers", "pre_uniq_sellers",
    "pre_uniq_traders", "pre_buy_usd", "pre_sell_usd", "pre_buysell_ratio", "pre_vol_usd",
    "pre_buys_last60", "buyers_per_min", "sol_in_last60", "etg_s", "curve_frac",
]

_WSOL = "So11111111111111111111111111111111111111112"


def birdeye_items_to_owner_tape(items: list[dict]) -> list[tuple]:
    """Map Birdeye ``seek_by_time`` items to the lab's owner-tape tuple shape.

    Byte-faithful to ``entry_select_build.py::load_owner_tape`` (the source the
    seed trained on): each tuple is ``(t, price_usd, usd, side, owner, sol)`` where
        t        = blockUnixTime
        price_usd = basePrice * quotePrice          (USD per token)
        usd      = |quote.uiAmount| * quotePrice     (USD notional)
        sol      = |quote.uiAmount|                  (SOL notional)
    Items missing t / basePrice / quotePrice, or with basePrice*quotePrice <= 0,
    are skipped (same guard as the lab).  Returns sorted ascending by t.
    """
    out: list[tuple] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("blockUnixTime")
        bp = it.get("basePrice")
        qp = it.get("quotePrice")
        if t is None or bp is None or qp is None:
            continue
        try:
            bp = float(bp)
            qp = float(qp)
        except (TypeError, ValueError):
            continue
        if bp * qp <= 0:
            continue
        q = it.get("quote") or {}
        try:
            sol = abs(float(q.get("uiAmount") or 0.0))
        except (TypeError, ValueError):
            sol = 0.0
        usd = sol * qp
        out.append((int(t), bp * qp, usd, it.get("side"), it.get("owner"), sol))
    out.sort(key=lambda x: x[0])
    return out


def entry_features(otr: list[tuple], buy_ts: int, gts: Optional[int] = None) -> Optional[dict]:
    """Non-leaky features from the tape prefix strictly at/before ``buy_ts``.

    BYTE-FAITHFUL PORT of entry_select_build.py::entry_features (lines 70-125).
    ``otr`` is a sorted list of ``(t, price_usd, usd, side, owner, sol)`` tuples
    (from ``birdeye_items_to_owner_tape``).  ``gts`` is the graduation epoch — at
    the LIVE buy instant pass ``None`` (token not graduated → curve_ok=True for all,
    exactly the lab's on-curve scoring path).  Returns the 20-feature dict (incl
    ``curve_frac``), or None if the tape has no usable prefix.
    """
    if not otr:
        return None
    t0 = otr[0][0]
    pre = [x for x in otr if x[0] <= buy_ts]
    if not pre:
        return None
    price = pre[-1][1]
    buyers: set = set()
    sellers: set = set()
    nb = ns = 0
    bu = su = 0.0
    sol_in = 0.0
    vol = 0.0
    b60 = 0
    sol_in_last60 = 0.0
    owner_buy: dict = {}
    for t, p, usd, side, owner, sol in pre:
        curve_ok = (gts is None) or (t < gts)
        vol += usd
        if side == "buy":
            nb += 1
            bu += usd
            buyers.add(owner)
            if curve_ok:
                sol_in += sol
            owner_buy[owner] = owner_buy.get(owner, 0.0) + usd
            if t >= buy_ts - 60:
                b60 += 1
                if curve_ok:
                    sol_in_last60 += sol
        elif side == "sell":
            ns += 1
            su += usd
            sellers.add(owner)
            if curve_ok:
                sol_in -= sol
    age = max(buy_ts - t0, 1)
    etg_s = float(np.clip((GRAD_SOL - sol_in) / max(sol_in_last60 / 60.0, 1e-6), 0.0, 7200.0))
    shares = sorted(owner_buy.values(), reverse=True)
    top1 = shares[0] / bu if bu > 0 and shares else 0.0
    top5 = sum(shares[:5]) / bu if bu > 0 and shares else 0.0
    hhi = sum((v / bu) ** 2 for v in shares) if bu > 0 else 0.0
    return {
        "top1_buyer_share": top1, "top5_buyer_share": top5, "buy_hhi": hhi,
        "tok_age_s": buy_ts - t0,
        "price_at_entry": price,
        "fdv_proxy": price * 1e9,
        "pre_sol_in": sol_in,
        "sol_in_last60": sol_in_last60, "etg_s": etg_s,
        "pre_n_trades": len(pre), "pre_n_buys": nb, "pre_n_sells": ns,
        "pre_uniq_buyers": len(buyers), "pre_uniq_sellers": len(sellers),
        "pre_uniq_traders": len(buyers | sellers),
        "pre_buy_usd": bu, "pre_sell_usd": su,
        "pre_buysell_ratio": bu / (bu + su) if (bu + su) > 0 else 0.5,
        "pre_vol_usd": vol, "pre_buys_last60": b60,
        "buyers_per_min": len(buyers) / (age / 60.0),
        # G1: curve_frac = pre_sol_in/85 (tape proxy, NOT on-chain reserve).
        "curve_frac": float(np.clip(sol_in / GRAD_SOL, 0.0, 2.0)),
    }
