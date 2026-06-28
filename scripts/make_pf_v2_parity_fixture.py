# ---
# module: scripts.make_pf_v2_parity_fixture
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: pandas, numpy, json, glob, pathlib
# ---
"""make_pf_v2_parity_fixture.py — generate the 13-feature parity fixture for trilly_pf_v2.

WHAT IT DOES
============
For each graduated token in a given day of pumpfundata:
1. Runs the TRAINING-SIDE feature computation (directly from build_universe.py logic)
   to get the EXPECTED 13 feature values.
2. Converts the training pumpfundata swap rows to the LIVE swap-dict format
   (as produced by norm_row_to_swap_dict in core/firehose/shared_tape.py).
3. Writes a JSON fixture: list of {mint, gts, expected_features, live_swaps}.

The fixture is committed to core/tests/fixtures/pf_v2_parity_fixture.json.
Tests in test_pf_v2_parity.py assert compute_pf_features(live_swaps, gts)
matches expected_features within 1e-6 relative tolerance for ALL 13 features.

FIELD MAPPING (pumpfundata -> live swap-dict)
=============================================
  lamports_amount / 1e9  ->  vol_sol
  token_amount           ->  token_amount
  user_wallet            ->  owner
  action                 ->  side  ("buy" | "sell")
  timestamp              ->  block_time
  slot_number            ->  slot
  virtual_lamports_reserve/virtual_token_reserve -> price (PX for enrich7)

USAGE
=====
  python scripts/make_pf_v2_parity_fixture.py
  python scripts/make_pf_v2_parity_fixture.py --day 2026-05-01 --max-tokens 10

The script reads pumpfundata from the lake at:
  /Users/asim/NoIcloud/solanatrills/lake/pumpfundata/dt=YYYY-MM-DD/

Writes to:
  core/tests/fixtures/pf_v2_parity_fixture.json

NO CREDITS / NO NETWORK: all data from local pumpfundata.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
FIXTURE_OUT = REPO_ROOT / "core" / "tests" / "fixtures" / "pf_v2_parity_fixture.json"
LAKE = Path("/Users/asim/NoIcloud/solanatrills/lake/pumpfundata")

COLS = [
    "event_type", "token_mint", "slot_number", "timestamp", "real_lamports_reserve",
    "virtual_token_reserve", "virtual_lamports_reserve", "action", "lamports_amount",
    "token_amount", "token_creator", "user_wallet",
]

GRAD_SOL = 85.0
FEATURE_NAMES = [
    "f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1",
    "new_buyers_last60", "new_buyers_last300", "diamond_frac", "top5_buyer_share",
    "repeat_buyer_frac", "price_ret", "price_maxrun",
]


def compute_training_features_for_mint(g: pd.DataFrame, gts: float) -> dict | None:
    """Compute all 13 features for one token using TRAINING-side logic.

    This is a faithful port of build_universe.py:day_features() for a single mint.
    This is the EXPECTED side of the parity test — tests assert the live
    compute_pf_features() matches these values within 1e-6 relative.

    IMPORTANT: this function runs the TRAINING code (from build_universe.py).
    The test code under test is compute_pf_features() in core/pf_features.py.
    They must produce identical results from the same data.
    """
    g = g[g.timestamp <= gts].sort_values(["timestamp", "slot_number"])
    if len(g) < 2:
        return None
    if g.sol.abs().sum() <= 0 or g.rsol.max() <= 0:
        return None

    buys = g[g.action == "buy"]
    sells = g[g.action == "sell"]

    tot_sol = g.sol.abs().sum()
    buy_sol = buys.sol.abs().sum()
    sell_sol = sells.sol.abs().sum()
    first_t = g.timestamp.iloc[0]

    # Buyer concentration
    bw = (
        buys.groupby("user_wallet").sol.apply(lambda s: s.abs().sum()).sort_values(ascending=False)
        if len(buys) else pd.Series(dtype=float)
    )
    bw_tot = bw.sum() if len(bw) else 0.0
    top1 = bw.iloc[0] / bw_tot if bw_tot > 0 else 0.0

    # f_momentum: buyer accel
    mid = first_t + (gts - first_t) / 2.0
    ub1 = buys[buys.timestamp <= mid].user_wallet.nunique()
    ub2 = buys[buys.timestamp > mid].user_wallet.nunique()
    buyer_accel = ub2 / (ub1 + 1.0)

    # f_holders: net holders
    tok = g.token_amount.fillna(0.0)
    signed_tok = np.where(g.action.values == "buy", tok.values, -tok.values)
    wallet_net = pd.Series(signed_tok).groupby(g.user_wallet.values).sum()
    net_holders = int((wallet_net > 0).sum())

    import math
    f_size = math.log1p(float(tot_sol))
    f_life = float(gts - first_t)

    # --- ENRICH7 (per build_universe.py lines ~83-120) ---
    nbuyers = len(bw)
    first_buy_t = (
        buys.groupby("user_wallet").timestamp.min()
        if len(buys) else pd.Series(dtype=float)
    )
    n_buy_by = (
        buys.groupby("user_wallet").size()
        if len(buys) else pd.Series(dtype=int)
    )
    seller_set = set(sells.user_wallet.unique())

    if nbuyers > 0:
        bidx = bw.index
        sold_b = bidx.isin(seller_set)
        diamond_frac = float((~sold_b).mean())
        repeat_buyer_frac = float((n_buy_by.reindex(bidx).fillna(0).values >= 2).mean())
        top5_buyer_share = float(bw.iloc[:5].sum() / bw_tot) if bw_tot > 0 else 0.0
        new_buyers_last60 = int((first_buy_t >= gts - 60).sum())
        new_buyers_last300 = int((first_buy_t >= gts - 300).sum())
    else:
        diamond_frac = repeat_buyer_frac = top5_buyer_share = 0.0
        new_buyers_last60 = new_buyers_last300 = 0

    # price path
    pxs = g.px[g.px > 0]
    first_px = float(pxs.iloc[0]) if len(pxs) else 0.0
    price_ret = float(g.px.iloc[-1] / first_px - 1.0) if first_px > 0 else 0.0
    price_maxrun = float(pxs.max() / first_px - 1.0) if first_px > 0 else 0.0

    return {
        "f_value": float(buy_sol - sell_sol),
        "f_momentum": float(buyer_accel),
        "f_size": float(f_size),
        "f_holders": float(net_holders),
        "f_life": float(f_life),
        "f_top1": float(top1),
        "new_buyers_last60": float(new_buyers_last60),
        "new_buyers_last300": float(new_buyers_last300),
        "diamond_frac": float(diamond_frac),
        "top5_buyer_share": float(top5_buyer_share),
        "repeat_buyer_frac": float(repeat_buyer_frac),
        "price_ret": float(price_ret),
        "price_maxrun": float(price_maxrun),
    }


def df_to_live_swaps(g: pd.DataFrame, gts: float) -> list[dict]:
    """Convert training pumpfundata rows to live swap-dict format.

    Maps:
      lamports_amount / 1e9  ->  vol_sol
      token_amount           ->  token_amount
      user_wallet            ->  owner
      action                 ->  side
      timestamp              ->  block_time
      slot_number            ->  slot
      virtual_lamports/virtual_token_reserve -> price
      token_mint             ->  mint

    Only includes event_type=="swap" rows — bonding_complete events are excluded,
    matching the live tape which receives only swap events from Birdeye/Helius.
    """
    # Filter to swap events only — training groupby(token_mint) on sw already did
    # event_type=="swap" filtering; we must replicate this here.
    if "event_type" in g.columns:
        g = g[g.event_type == "swap"]
    pre = g[g.timestamp <= gts].sort_values(["timestamp", "slot_number"])
    swaps = []
    for _, row in pre.iterrows():
        vt = float(row.get("virtual_token_reserve", 0) or 0)
        vl = float(row.get("virtual_lamports_reserve", 0) or 0)
        px = (vl / vt) if vt > 0 else 0.0
        swaps.append({
            "block_time": float(row["timestamp"]),
            "slot": int(row["slot_number"]),
            "side": str(row["action"]),
            "vol_sol": float((row.get("lamports_amount") or 0) / 1e9),
            "token_amount": float(row.get("token_amount") or 0),
            "owner": str(row.get("user_wallet") or ""),
            "price": float(px),
            "vol": float((row.get("lamports_amount") or 0) / 1e9),
            "vol_usd": 0.0,
            "mint": str(row.get("token_mint", "")),
            "phase": "pre",
            "signature": "",
        })
    return swaps


def load_day(day: str) -> pd.DataFrame | None:
    day_dir = LAKE / f"dt={day}"
    files = sorted(glob.glob(str(day_dir / "*.parquet")))
    if not files:
        return None
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f, columns=COLS))
        except Exception as e:
            print(f"  Warning: could not read {f}: {e}", file=sys.stderr)
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True)


def day_records(day: str, max_tokens: int = 10) -> list[dict]:
    df = load_day(day)
    if df is None:
        print(f"  No data for day {day}", file=sys.stderr)
        return []

    bc = df[df.event_type == "bonding_complete"]
    if bc.empty:
        return []

    gts_map = bc.groupby("token_mint").timestamp.min()
    gmints = set(gts_map.index)
    sw = df[(df.event_type == "swap") & (df.token_mint.isin(gmints))].copy()
    sw["sol"] = sw.lamports_amount.fillna(0) / 1e9
    sw["rsol"] = sw.real_lamports_reserve.fillna(0) / 1e9
    sw["px"] = np.where(
        sw.virtual_token_reserve > 0,
        sw.virtual_lamports_reserve / sw.virtual_token_reserve,
        0.0,
    )

    records = []
    for mint, group in sw.groupby("token_mint", sort=False):
        if len(records) >= max_tokens:
            break
        gts = float(gts_map[mint])
        expected = compute_training_features_for_mint(group.copy(), gts)
        if expected is None:
            continue
        live_swaps = df_to_live_swaps(
            df[df.token_mint == mint].copy(),
            gts,
        )
        if len(live_swaps) < 2:
            continue
        records.append({
            "mint": str(mint),
            "gts": float(gts),
            "expected_features": expected,
            "live_swaps": live_swaps,
        })
        print(f"  {mint[:20]}... gts={gts:.0f} n_swaps={len(live_swaps)}")

    return records


def main():
    parser = argparse.ArgumentParser(description="Generate pf_v2 parity fixture")
    parser.add_argument("--day", default="2026-05-01", help="pumpfundata day (YYYY-MM-DD)")
    parser.add_argument("--max-tokens", type=int, default=10, help="Max tokens to include")
    parser.add_argument("--out", default=str(FIXTURE_OUT), help="Output path")
    args = parser.parse_args()

    print(f"Generating pf_v2 parity fixture from day={args.day} max_tokens={args.max_tokens}")
    records = day_records(args.day, args.max_tokens)
    if not records:
        print("ERROR: no records generated", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)

    print(f"\nWrote {len(records)} records to {out_path}")
    print(f"Features: {FEATURE_NAMES}")


if __name__ == "__main__":
    main()
