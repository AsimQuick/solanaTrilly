# ---
# module: core.tape_microstructure
# sprint: sprint-7
# story: US-28 AC-28.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: numpy
# vendored-from: solanabilly3/src/tape_microstructure.py
# vendored-commit: verbatim copy — do NOT hand-edit this file
# sanctioned-edit: rels.ptp() → np.ptp(rels) (numpy 2.x compatibility, line ~105)
# re-vendor-note: |
#   To update this file, re-copy solanabilly3/src/tape_microstructure.py verbatim
#   and re-apply ONLY the one sanctioned edit above. Never edit the math body directly.
#   Source: /Users/asim/NoIcloud/solanabilly3/src/tape_microstructure.py
#   Regenerator for golden fixture: solanabilly3/scripts/make_microstructure_golden.py
# ---
"""
CANONICAL pre-entry microstructure derivation — the SINGLE SOURCE OF TRUTH shared by
offline feature-building (solanabilly3) and live inference (solanaBilly).

THE CONTRACT (offline == online, byte-for-byte)
  Both repos MUST compute the `tape_*` features by calling THIS function on a list of
  normalized swaps. solanaBilly vendors this file verbatim (do not fork the math) and
  feeds it swaps read from the live tape-recorder buffer; solanabilly3 feeds it swaps
  read from the historical Birdeye tapes. Same inputs -> same features. A golden
  fixture (data/microstructure/golden_fixture_*.json) pins the expected output so the
  live side can prove parity. See scrum-master/microstructure-live-contract.md.

NORMALIZED SWAP (what each repo's reader must produce)
  {
    "rel":   float,   # seconds since the token's launch (block_time - launch_ts).
                      #   ANCHOR = the DB launch_timestamp solanaBilly already uses for
                      #   poll scheduling. Both repos MUST use the same anchor.
    "price": float,   # >0. Offline: Birdeye tokenPrice (USD/token). Live: curve
                      #   virtual_sol_reserves / virtual_token_reserves (SOL/token raw
                      #   ratio). SHAPE features (returns/slopes/drawdowns/ratios) are
                      #   unit-invariant and transfer; absolute-volume cols do not — keep
                      #   one source per build and carry a vol_unit tag.
    "side":  "buy" | "sell",
    "vol":   float,   # swap size in the quote unit (USD offline, SOL live). >=0.
    "owner": str | None,   # signer wallet (for unique-trader counts).
  }

LEAK SAFETY (non-negotiable)
  Only swaps with 0 <= rel < window_s are used. window_s = entry poll in seconds
  (t2 = 120, t4 = 240). Nothing at/after entry touches the features — post-entry price
  is the OUTCOME window. Live: the feature window must be CLOSED before scoring (score
  at launch + window_s + a small capture buffer so the recorder has the tail).

DETERMINISM
  Pure function of the input list. No clock, no RNG, no I/O. Float ops only.
"""
from __future__ import annotations

import numpy as np

DEFAULT_WINDOW_S = 120
DEFAULT_BUCKET_S = 15
ALL_BUY_RATIO_SENTINEL = 1000.0  # buy/sell vol ratio when there are buys but zero sells


def build_grid(swaps: list[dict], window_s: int = DEFAULT_WINDOW_S,
               bucket_s: int = DEFAULT_BUCKET_S) -> list[dict]:
    """Per-bucket OHLCV + buy/sell flow for the pre-entry window [0, window_s).
    Returns window_s//bucket_s bins (empty bins carry None prices / 0 flow)."""
    n_buckets = window_s // bucket_s
    by_bucket: dict[int, list[dict]] = {}
    for s in swaps:
        if 0 <= s["rel"] < window_s and s.get("price") and s["price"] > 0:
            by_bucket.setdefault(int(s["rel"] // bucket_s), []).append(s)
    bins: list[dict] = []
    for b in range(n_buckets):
        ss = by_bucket.get(b, [])
        if not ss:
            bins.append({"bin": b, "t_start_s": b * bucket_s, "n_buy": 0, "n_sell": 0,
                         "buy_vol": 0.0, "sell_vol": 0.0, "open": None, "high": None,
                         "low": None, "close": None, "n_unique": 0})
            continue
        prices = [s["price"] for s in ss]
        buys = [s for s in ss if s["side"] == "buy"]
        sells = [s for s in ss if s["side"] == "sell"]
        bins.append({
            "bin": b, "t_start_s": b * bucket_s,
            "n_buy": len(buys), "n_sell": len(sells),
            "buy_vol": float(sum(s["vol"] for s in buys)),
            "sell_vol": float(sum(s["vol"] for s in sells)),
            "open": prices[0], "high": max(prices), "low": min(prices), "close": prices[-1],
            "n_unique": len({s["owner"] for s in ss if s["owner"]}),
        })
    return bins


def compute_features(swaps: list[dict], window_s: int = DEFAULT_WINDOW_S,
                     bucket_s: int = DEFAULT_BUCKET_S) -> dict | None:
    """Compact leak-safe shape/flow features over the pre-entry window. Returns None
    if there are no usable pre-entry swaps (a token that didn't trade before entry —
    the caller should record that as a no-feature mint, not a zero row)."""
    grid = build_grid(swaps, window_s, bucket_s)
    tr = [s for s in swaps if 0 <= s["rel"] < window_s and s.get("price") and s["price"] > 0]
    if not tr:
        return None
    tr.sort(key=lambda s: s["rel"])
    prices = np.array([s["price"] for s in tr], dtype=float)
    rels = np.array([s["rel"] for s in tr], dtype=float)
    open_p, close_p = prices[0], prices[-1]
    high_p, low_p = prices.max(), prices.min()
    run_max = np.maximum.accumulate(prices)
    run_min = np.minimum.accumulate(prices)
    max_dd = float((prices / run_max - 1.0).min())
    max_runup = float((prices / np.maximum(run_min, 1e-30) - 1.0).max())
    logp = np.log(prices)
    slope = accel = 0.0
    n_distinct_t = len(np.unique(rels))
    if n_distinct_t >= 2 and np.ptp(rels) > 0:
        slope = float(np.polyfit(rels, logp, 1)[0])
        if n_distinct_t >= 3:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    accel = float(np.polyfit(rels, logp, 2)[0])
            except (np.linalg.LinAlgError, ValueError):
                accel = 0.0
    buys = [s for s in tr if s["side"] == "buy"]
    sells = [s for s in tr if s["side"] == "sell"]
    buy_vol = sum(s["vol"] for s in buys)
    sell_vol = sum(s["vol"] for s in sells)
    vol_total = buy_vol + sell_vol
    first_sell_rel = next((s["rel"] for s in tr if s["side"] == "sell"), float(window_s))
    peak_rel = float(rels[int(np.argmax(prices))])
    last_close = next((b["close"] for b in reversed(grid) if b["close"] is not None), close_p)
    prev_close = (next((b["close"] for b in reversed(grid[:-1]) if b["close"] is not None), open_p)
                  if len(grid) >= 2 else open_p)
    feat = {
        "tape_n_trades": len(tr), "tape_n_buy": len(buys), "tape_n_sell": len(sells),
        "tape_n_unique_traders": len({s["owner"] for s in tr if s["owner"]}),
        "tape_vol_total": float(vol_total), "tape_buy_vol": float(buy_vol), "tape_sell_vol": float(sell_vol),
        "tape_buy_sell_vol_ratio": float(buy_vol / sell_vol) if sell_vol > 0 else (
            ALL_BUY_RATIO_SENTINEL if buy_vol > 0 else 0.0),
        "tape_all_buy_window": int(sell_vol == 0 and buy_vol > 0),
        "tape_sell_frac_cnt": float(len(sells) / len(tr)),
        "tape_ret_total": float(close_p / open_p - 1.0),
        "tape_max_runup": max_runup, "tape_max_drawdown": max_dd,
        "tape_high_over_open": float(high_p / open_p - 1.0),
        "tape_low_over_open": float(low_p / open_p - 1.0),
        "tape_logprice_slope_per_s": slope, "tape_logprice_accel": accel,
        "tape_time_to_peak_s": peak_rel, "tape_time_to_first_sell_s": float(first_sell_rel),
        "tape_late_bucket_ret": float(last_close / prev_close - 1.0) if prev_close else 0.0,
        "tape_n_buckets_active": int(sum(1 for b in grid if b["close"] is not None)),
        "tape_first_swap_rel_s": float(rels[0]),
    }
    n_buckets = window_s // bucket_s
    last = open_p
    for b in range(n_buckets):
        c = grid[b]["close"] if b < len(grid) and grid[b]["close"] is not None else last
        last = c
        feat[f"tape_close_b{b}"] = float(c / open_p) if open_p else 1.0
    return feat


def compute_row(swaps: list[dict], window_s: int = DEFAULT_WINDOW_S,
                bucket_s: int = DEFAULT_BUCKET_S) -> tuple[list[dict], dict | None]:
    """Convenience: (grid, features) in one call."""
    grid = build_grid(swaps, window_s, bucket_s)
    return grid, compute_features(swaps, window_s, bucket_s)
