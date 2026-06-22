# ---
# module: copytrade.curvestage_settle
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: numpy, bisect
# ---
"""Honest PnL settler for the curve-stage copy cohort — ACCURATE-PnL CORE.

BYTE-FAITHFUL PORT of the lab's
``solanatrills/analysis/whale_graph/copyable_window.py`` honest entry
(``settle_event``) + ride-to-graduation exit (``exit_value`` / ``pnl_grad``).
This is what makes the soak's reconstructed PnL match the offline +$9-15/tr by
construction instead of drifting (the operator/lab DoD: accurate PnL).

The recipe (lab-confirmed 2026-06-22), all on a SINGLE price basis (Birdeye USD):
  ENTRY (honest second-buyer fill):
    quote   = last print at/before the watched buy (pr[bisect_right(ts,buy_ts)-1])
    entry   = first print STRICTLY AFTER the watched buy (pr[bisect_right(ts,buy_ts)])
    slip    = entry/quote - 1;  slip > SLIP_CAP(0.15) -> EXCLUDED (reason slip6002)
    iflow   = USD flow in [buy_ts, buy_ts+60]
    iimp    = 2S/(S+iflow)  (entry-side impact; 0.20 when iflow==0)
  EXIT (ride-to-graduation = pnl_grad):
    exit_t  = gts if (gts and gts>entry_t) else tape_end (= ts at entry_t+TIMEOUT)
    vwap    = USD-volume-weighted price over [exit_t, exit_t+DRAIN(30s)]   (the
              first 30s of post-grad PumpSwap prints, NOT a single print -> avoids
              the dust-fill trap; NOT the pre-grad high-water -> avoids optimism)
    buyflow = buy-side USD in that 30s window
    ximp    = 2S/(S+buyflow)  (exit depth haircut; 0.20 sparse fallback)
    exit_px = vwap * max(1-ximp, 0)
    pnl%    = ((exit_px/entry) * (1-FEE) / (1+iimp) - 1) * 100, clipped [-100, CLIP]

PARITY REQUIREMENT (tester B5): the tape MUST be a single basis.  Source pre-grad
(entry) AND post-grad (exit) from the SAME Birdeye seek_by_time fetch (USD basis,
price = basePrice*quotePrice).  NEVER stitch a Helius SOL-ratio entry leg to a
Birdeye USD exit leg — the entry/exit ratio would cross bases and the PnL is garbage.
"""
from __future__ import annotations

import bisect
from typing import Optional

import numpy as np

# Constants — verbatim from copyable_window.py (do NOT retune; parity-load-bearing).
FEE: float = 0.01          # 1% round-trip
S: float = 25.0            # $25 position size
SLIP_CAP: float = 0.15     # entry slip vs the watched buy price; beyond -> EXCLUDED (6002)
TIMEOUT: int = 900         # max hold seconds (tape_end fallback when no graduation)
DRAIN: int = 30            # exit drain-VWAP window (s)
CLIP: float = 1000.0       # PnL% upper clip (lower clip -100)


def _exit_value(
    ts: list, pr: list, us: list, sd: list, n: int, entry: float, iimp: float, exit_t: float,
) -> float:
    """Realized OUR pnl% selling at *exit_t*: drain-VWAP over [exit_t, exit_t+DRAIN]
    with buy-side counterparty haircut.  Byte-faithful to copyable_window.exit_value
    (returns only the pnl%; the buyflow/counterparty_ok extras are unused here)."""
    xlo = bisect.bisect_left(ts, exit_t)
    xhi = bisect.bisect_right(ts, exit_t + DRAIN)
    sumpu = sumu = buyflow = 0.0
    for k in range(xlo, xhi):
        sumpu += pr[k] * us[k]
        sumu += us[k]
        if sd[k] == "buy":
            buyflow += us[k]
    vwap = (sumpu / sumu) if sumu > 0 else pr[min(xlo, n - 1)]
    ximp = 2 * S / (S + buyflow) if buyflow > 0 else 0.20
    exit_px = vwap * max(1 - ximp, 0.0)
    pnl = ((exit_px / entry) * (1 - FEE) / (1 + iimp) - 1) * 100
    return float(np.clip(pnl, -100, CLIP))


def settle_grad(tr: list, buy_ts: int, gts: Optional[int]) -> dict:
    """Reconstruct the honest ride-to-graduation PnL for ONE copy event.

    Args:
        tr:     The token's stitched tape, sorted ascending, as
                ``[(t, price_usd, usd, side), ...]`` — pre-grad curve (t<gts) +
                post-grad AMM (t>=gts), ALL in Birdeye USD basis (see module note).
        buy_ts: The watched wallet's buy epoch (the trigger; our fill is the first
                print strictly after it).
        gts:    Graduation epoch, or None if the token never graduated (rides to
                tape_end = a loss, exactly the lab's non-grad outcome).

    Returns a dict:
        {"reason": "ok", "pnl_pct": float, "entry": float, "iimp": float,
         "slip": float, "exit_t": float, "graduated": bool}
      or {"reason": "no_fill"|"badfill"|"slip6002", ...} for events that cannot be
      honestly settled (excluded from the soak metric, matching the offline).
    """
    n = len(tr)
    if n == 0:
        return {"reason": "no_fill"}
    ts = [x[0] for x in tr]
    pr = [x[1] for x in tr]
    us = [x[2] for x in tr]
    sd = [x[3] for x in tr]
    # Prefix sum of USD volume for the O(1) entry-impact window.
    Cus = np.concatenate([[0.0], np.cumsum(us)]).tolist()

    qi = max(bisect.bisect_right(ts, buy_ts) - 1, 0)
    quote = pr[qi]
    fi = bisect.bisect_right(ts, buy_ts)  # OUR fill = first print after their buy
    if fi >= n:
        return {"reason": "no_fill"}
    entry_t, entry = ts[fi], pr[fi]
    if entry <= 0:
        return {"reason": "badfill"}
    slip = entry / quote - 1.0 if quote > 0 else 0.0
    if slip > SLIP_CAP:
        return {"reason": "slip6002", "slip": float(slip)}
    iflow = float(Cus[bisect.bisect_right(ts, buy_ts + 60)] - Cus[bisect.bisect_left(ts, buy_ts)])
    iimp = 2 * S / (S + iflow) if iflow > 0 else 0.20

    # Ride-to-graduation exit time: gts if it graduated after our entry, else the
    # tape_end fallback (the forward walk stops at entry_t+TIMEOUT) — the loss case.
    if gts and gts > entry_t:
        exit_t = float(gts)
        graduated = True
    else:
        j = bisect.bisect_right(ts, entry_t + TIMEOUT)
        exit_t = float(ts[min(j, n - 1)])
        graduated = False

    pnl = _exit_value(ts, pr, us, sd, n, entry, iimp, exit_t)
    return {
        "reason": "ok", "pnl_pct": pnl, "entry": float(entry), "iimp": float(iimp),
        "slip": float(slip), "exit_t": exit_t, "graduated": graduated,
    }
