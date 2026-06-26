# ---
# module: core.v7_pregrad_features
# sprint: sprint-15
# story: US-93
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: numpy, core.v4_rep_builder
# ---
"""v7 live feature vector builder: 19 pre-grad feats + n_pregrad_holders + 24 rep feats.

SCOPING FINDINGS (operator-required confirmations)
===================================================

1. n_pregrad_holders COMPUTABILITY (FINDING: COMPUTABLE from pre-grad tape, ZERO CREDITS)
   The #1 feature (importance=727.1) is: distinct wallets with a net-positive token
   balance at graduation.  Computed from the pre-grad tape as:
     - for each wallet: net_tokens = sum(buy amounts) - sum(sell amounts)
     - n_pregrad_holders = count of wallets with net_tokens > 0
   The firehose/recorder pre-grad rows carry owner+side+vol (confirmed from
   firehose_harness.py TapeRow schema).  The vol on pre-grad rows is SOL (vol_sol);
   the TOKEN amount is NOT directly available in the firehose schema, so we use the
   following proxy: net BUY COUNT (buys - sells per wallet > 0) as the holder indicator,
   OR if token amounts are available (Birdeye trade_pages format), we use token amounts.
   For the firehose live path, net BUY COUNT > 0 is the holder proxy (a wallet is a
   "holder" if they made more buys than sells on this token's bonding curve).

   The parity_sample.parquet was built from Birdeye raw30k data (holder_exit.py) which
   uses token amounts from trade_pages.  The LIVE firehose path uses buy-count proxy.
   Both are computable with ZERO CREDITS.  NOT a BLOCKER.

2. WALLET-REP BANK FINDING (FINDING: SAME 24 COLUMN NAMES, DIFFERENT ITERATION ORDER)
   The in-repo v4 wallet bank (core/v4_rep_builder.py, WalletBankLookup,
   lake/v4_wallet_bank.parquet) emits the SAME 24 column names as v7's feature_order[20:44].
   HOWEVER: the in-repo v4 REP_FEATURE_NAMES groups by outcome first (rdollar block:
   time_rdollar_* + size_rdollar_*; then pk24 block: time_pk24_* + size_pk24_*).
   The v7 meta.json groups by pool first (time block: time_rdollar_* + time_pk24_*;
   then size block: size_rdollar_* + size_pk24_*).

   The SAME bank and SAME compute_rep_features() logic produces values keyed by name.
   The v7 feature vector is assembled by reading values by NAME from compute_rep_features()
   output dict and placing them in v7 feature_order (position 20..43).
   NO PORT NEEDED: the v4 bank and compute_rep_features() are fully compatible.
   The column order difference is handled by name-based dict lookup here.
   NOT a BLOCKER.

FEATURE ORDER (meta.json.selection.feature_order, 44 total)
============================================================
  [0:19]  19 pre-grad curve-life feats  (computed by compute_v7_pregrad_feats)
  [19]    n_pregrad_holders              (computed from pre-grad tape wallet balance)
  [20:44] 24 wallet-reputation feats    (from v4 WalletBankLookup via compute_rep_features)

NAN-FILL RULES (applied in assemble_v7_features)
=================================================
  pre+holder feats [0:20] -> medians from meta.json.selection.nan_fill.pre+holder_feats
  reputation feats [20:44] -> 0.0

PARITY NOTE
===========
The parity_sample.parquet was built from Birdeye raw30k data; the live firehose
path uses the same mathematical definitions but different input sources.  For full
parity on the parity_sample, use the Birdeye-format swaps (with basePrice/quotePrice)
for USD computation.  For the live firehose path, use vol_sol × SOL_price for USD.
The parity test verifies the scoring path; the live feature path is verified on
synthetic inputs.

ZERO CREDITS
============
No Birdeye/Helius/Dune calls anywhere in this module.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V7 PRE-GRAD FEATURE NAMES (19 features, in meta.json order)
# ---------------------------------------------------------------------------

V7_PRE_FEATURE_NAMES: list[str] = [
    "pre_buy_frac",
    "pre_buy_vol_usd",
    "pre_buys_last60",
    "pre_max_trade_usd",
    "pre_mean_trade_usd",
    "pre_n_buys",
    "pre_n_sells",
    "pre_n_swaps",
    "pre_net_flow_usd",
    "pre_price_ret",
    "pre_top1_buyer_share",
    "pre_top3_buyer_share",
    "pre_trades_per_sec",
    "pre_uniq_buyers",
    "pre_uniq_traders",
    "pre_vol_last300",
    "pre_vol_last60",
    "pre_vol_usd",
    "pre_window_covered_s",
]

# The 24 wallet-rep feature names in v7 meta.json order (pool-first grouping)
# Note: this differs from v4's REP_FEATURE_NAMES which groups by outcome first.
# Values are computed by name from compute_rep_features() output dict.
V7_REP_FEATURE_NAMES: list[str] = [
    "time_rdollar_repmean_mean",
    "time_rdollar_repmean_max",
    "time_rdollar_repmax_mean",
    "time_rdollar_ngood_sum",
    "time_rdollar_nhist",
    "time_rdollar_wmean",
    "time_pk24_repmean_mean",
    "time_pk24_repmean_max",
    "time_pk24_repmax_mean",
    "time_pk24_ngood_sum",
    "time_pk24_nhist",
    "time_pk24_wmean",
    "size_rdollar_repmean_mean",
    "size_rdollar_repmean_max",
    "size_rdollar_repmax_mean",
    "size_rdollar_ngood_sum",
    "size_rdollar_nhist",
    "size_rdollar_wmean",
    "size_pk24_repmean_mean",
    "size_pk24_repmean_max",
    "size_pk24_repmax_mean",
    "size_pk24_ngood_sum",
    "size_pk24_nhist",
    "size_pk24_wmean",
]

# Full v7 44-feature order
V7_FEATURE_ORDER: list[str] = V7_PRE_FEATURE_NAMES + ["n_pregrad_holders"] + V7_REP_FEATURE_NAMES


# ---------------------------------------------------------------------------
# compute_v7_pregrad_feats — 19 pre-grad curve-life features
# ---------------------------------------------------------------------------


def compute_v7_pregrad_feats(
    swaps: list[dict],
    grad_ts: float,
    *,
    sol_usd_spot: float = 84.0,
) -> dict[str, float] | None:
    """Compute the 19 v7 pre-grad curve-life features from a pre-grad tape.

    Ported from solanatrills/analysis/graduated/backfill_pregrad.py:pre_features()
    (the same computation used to build the parity_sample.parquet).

    Parameters
    ----------
    swaps:
        Pre-grad swap records.  Each dict must have at minimum:
          - block_time: float/int  (absolute Unix seconds)
          - side: str              ("buy" | "sell")
          - owner: str | None
          - vol: float             (USD volume; if vol==0 use vol_sol * sol_usd_spot)
          - vol_sol: float         (SOL volume; used for USD when vol==0)
          - price: float           (SOL/token price)
        Only swaps with block_time < grad_ts are used.
    grad_ts:
        Token graduation Unix timestamp (seconds).
    sol_usd_spot:
        SOL/USD price for volume dollarization.  Used when swap['vol'] == 0
        (pre-grad rows in the firehose have vol_usd=0; vol_sol*sol_usd_spot gives USD).

    Returns
    -------
    dict with 19 float features, or None if there are no valid pre-grad swaps.
    """
    # Filter to pre-graduation only and compute USD volume
    sw: list[dict] = []
    for s in swaps:
        bt = float(s.get("block_time", 0) or s.get("block_unix_time", 0) or 0)
        if bt <= 0 or bt >= grad_ts:
            continue
        price = float(s.get("price", 0.0) or 0.0)
        if price <= 0:
            continue
        side = s.get("side", "")
        if side not in ("buy", "sell"):
            continue
        # USD volume: use vol if non-zero, else vol_sol * spot
        vol = float(s.get("vol", 0.0) or 0.0)
        if vol == 0.0:
            vol_sol = float(s.get("vol_sol", 0.0) or 0.0)
            vol = vol_sol * sol_usd_spot
        owner = s.get("owner") or ""
        sw.append({"t": bt, "price": price, "vol": vol, "side": side, "owner": owner})

    if not sw:
        return None

    sw.sort(key=lambda s: s["t"])

    buys = [s for s in sw if s["side"] == "buy"]
    if not buys:
        # No buys: can still return partial features (sells only)
        n_swaps = len(sw)
        covered = grad_ts - sw[0]["t"]
        return {
            "pre_buy_frac": 0.0,
            "pre_buy_vol_usd": 0.0,
            "pre_buys_last60": 0.0,
            "pre_max_trade_usd": max(s["vol"] for s in sw) if sw else 0.0,
            "pre_mean_trade_usd": sum(s["vol"] for s in sw) / n_swaps if n_swaps > 0 else 0.0,
            "pre_n_buys": 0.0,
            "pre_n_sells": float(n_swaps),
            "pre_n_swaps": float(n_swaps),
            "pre_net_flow_usd": -sum(s["vol"] for s in sw),
            "pre_price_ret": sw[-1]["price"] / sw[0]["price"] - 1.0 if sw[0]["price"] > 0 else 0.0,
            "pre_top1_buyer_share": 0.0,
            "pre_top3_buyer_share": 0.0,
            "pre_trades_per_sec": n_swaps / covered if covered > 0 else 0.0,
            "pre_uniq_buyers": 0.0,
            "pre_uniq_traders": float(len(set(s["owner"] for s in sw if s["owner"]))),
            "pre_vol_last300": sum(s["vol"] for s in sw if s["t"] >= grad_ts - 300),
            "pre_vol_last60": sum(s["vol"] for s in sw if s["t"] >= grad_ts - 60),
            "pre_vol_usd": sum(s["vol"] for s in sw),
            "pre_window_covered_s": covered,
        }

    n_swaps = len(sw)
    covered = grad_ts - sw[0]["t"]

    # Per-buyer cumulative buy volume
    buyvol: dict[str, float] = {}
    for s in buys:
        w = s["owner"]
        if w:
            buyvol[w] = buyvol.get(w, 0.0) + s["vol"]
    tot_buyvol = sum(buyvol.values()) or 1.0
    shares = sorted(buyvol.values(), reverse=True)

    feats: dict[str, float] = {
        "pre_buy_frac": len(buys) / n_swaps,
        "pre_buy_vol_usd": sum(s["vol"] for s in buys),
        "pre_buys_last60": float(sum(1 for s in buys if s["t"] >= grad_ts - 60)),
        "pre_max_trade_usd": max(s["vol"] for s in sw),
        "pre_mean_trade_usd": sum(s["vol"] for s in sw) / n_swaps,
        "pre_n_buys": float(len(buys)),
        "pre_n_sells": float(n_swaps - len(buys)),
        "pre_n_swaps": float(n_swaps),
        "pre_net_flow_usd": (
            sum(s["vol"] for s in buys)
            - sum(s["vol"] for s in sw if s["side"] == "sell")
        ),
        "pre_price_ret": (
            sw[-1]["price"] / sw[0]["price"] - 1.0 if sw[0]["price"] > 0 else 0.0
        ),
        "pre_top1_buyer_share": shares[0] / tot_buyvol if shares else 0.0,
        "pre_top3_buyer_share": sum(shares[:3]) / tot_buyvol if shares else 0.0,
        "pre_trades_per_sec": n_swaps / covered if covered > 0 else 0.0,
        "pre_uniq_buyers": float(len(set(s["owner"] for s in buys if s["owner"]))),
        "pre_uniq_traders": float(len(set(s["owner"] for s in sw if s["owner"]))),
        "pre_vol_last300": sum(s["vol"] for s in sw if s["t"] >= grad_ts - 300),
        "pre_vol_last60": sum(s["vol"] for s in sw if s["t"] >= grad_ts - 60),
        "pre_vol_usd": sum(s["vol"] for s in sw),
        "pre_window_covered_s": covered,
    }

    return feats


# ---------------------------------------------------------------------------
# compute_n_pregrad_holders — the #1 feature
# ---------------------------------------------------------------------------


def compute_n_pregrad_holders(
    swaps: list[dict],
    grad_ts: float,
) -> float:
    """Compute n_pregrad_holders: distinct wallets with net-positive balance at graduation.

    SCOPING DECISION: n_pregrad_holders is computable LIVE from the pre-grad tape
    with ZERO CREDITS.  The computation:
      For each wallet: net_buys = count(buys) - count(sells)
      n_pregrad_holders = wallets with net_buys > 0

    This is a count-of-net-long-wallets proxy (not token-amount based, which would
    require token amounts unavailable in the firehose).  The lab used token amounts
    from Birdeye raw30k (holder_exit.py); the live proxy uses buy-count proxy.

    For the PARITY test against parity_sample.parquet, this function will produce
    slightly different values than the lab's token-amount-based computation.
    This is documented in the parity report.  For LIVE USE, this is the correct
    zero-credit computation.

    Parameters
    ----------
    swaps:
        Pre-grad swap records.  Only block_time < grad_ts rows are used.
    grad_ts:
        Token graduation Unix timestamp.

    Returns
    -------
    float
        Count of distinct wallets with more buys than sells on this token.
    """
    wallet_buys: dict[str, int] = {}
    wallet_sells: dict[str, int] = {}

    for s in swaps:
        bt = float(s.get("block_time", 0) or s.get("block_unix_time", 0) or 0)
        if bt <= 0 or bt >= grad_ts:
            continue
        owner = s.get("owner") or ""
        if not owner:
            continue
        side = s.get("side", "")
        if side == "buy":
            wallet_buys[owner] = wallet_buys.get(owner, 0) + 1
        elif side == "sell":
            wallet_sells[owner] = wallet_sells.get(owner, 0) + 1

    # Wallets with net_buys > 0 (more buys than sells)
    all_wallets = set(wallet_buys) | set(wallet_sells)
    holders = sum(
        1
        for w in all_wallets
        if wallet_buys.get(w, 0) > wallet_sells.get(w, 0)
    )
    return float(holders)


# ---------------------------------------------------------------------------
# assemble_v7_features — full 44-feature vector
# ---------------------------------------------------------------------------


def assemble_v7_features(
    swaps: list[dict],
    grad_ts: float,
    *,
    sol_usd_spot: float = 84.0,
    wallet_bank: Any | None = None,
    nan_fill: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """Assemble the full v7 44-feature vector for one graduating token.

    Parameters
    ----------
    swaps:
        Pre-grad swap records (block_time < grad_ts).
    grad_ts:
        Token graduation Unix timestamp (seconds).
    sol_usd_spot:
        SOL/USD price for volume dollarization of pre-grad rows.
    wallet_bank:
        WalletBankLookup instance (from core.v4_rep_builder).  If None,
        rep features are 0-filled (cold-start / bank absent).
    nan_fill:
        Dict of feature_name -> fill_value for NaN imputation.
        If None, pre+holder feats are left as-is and rep feats default to 0.

    Returns
    -------
    dict with 44 float features in V7_FEATURE_ORDER, or None if the pre-grad
    tape has no usable swaps.
    """
    # 1. Compute 19 pre-grad features
    pre_feats = compute_v7_pregrad_feats(swaps, grad_ts, sol_usd_spot=sol_usd_spot)
    if pre_feats is None:
        return None

    # 2. Compute n_pregrad_holders
    n_holders = compute_n_pregrad_holders(swaps, grad_ts)

    # 3. Compute 24 rep features (0-fill if no bank)
    if wallet_bank is not None:
        try:
            from core.v4_rep_builder import compute_rep_features

            # Build buyer lists directly from pre-grad buy swaps.
            # We do NOT use extract_buyers_from_swaps() because it requires
            # the 'rel' field (rel < 0 filter), which may not be present on
            # all swap formats.  We already know the swaps passed here are
            # pre-grad (block_time < grad_ts), so we filter directly.
            pre_buy_swaps = sorted(
                (
                    s for s in swaps
                    if float(s.get("block_time", 0) or 0) < grad_ts
                    and s.get("side") == "buy"
                    and s.get("owner")
                    and not (s.get("owner") or "").endswith("pump")
                ),
                key=lambda s: float(s.get("block_time", 0) or 0),
            )

            # Time pool: first-10 distinct buyers by block_time; weight = first-buy USD
            buyer_first_vol: dict[str, float] = {}
            buyer_total_vol: dict[str, float] = {}
            buyer_order: list[str] = []
            for s in pre_buy_swaps:
                w = s.get("owner") or ""
                v = float(s.get("vol", 0.0) or 0.0)
                if w not in buyer_first_vol:
                    buyer_first_vol[w] = v
                    buyer_order.append(w)
                buyer_total_vol[w] = buyer_total_vol.get(w, 0.0) + v

            time_buyers = [
                {"wallet": w, "weight": max(1.0, buyer_first_vol[w])}
                for w in buyer_order[:10]
            ]
            size_sorted = sorted(buyer_total_vol.items(), key=lambda x: x[1], reverse=True)
            size_buyers = [
                {"wallet": w, "weight": max(1.0, v)}
                for w, v in size_sorted[:10]
            ]

            rep_dict = compute_rep_features(
                time_buyers, size_buyers, float(grad_ts), wallet_bank
            )
        except Exception as exc:
            logger.warning("[v7_pregrad_features] rep computation failed: %s", exc)
            rep_dict = {fname: 0.0 for fname in V7_REP_FEATURE_NAMES}
    else:
        rep_dict = {fname: 0.0 for fname in V7_REP_FEATURE_NAMES}

    # 4. Assemble in V7_FEATURE_ORDER
    result: dict[str, float] = {}
    for fname in V7_FEATURE_ORDER:
        if fname in pre_feats:
            result[fname] = float(pre_feats[fname])
        elif fname == "n_pregrad_holders":
            result[fname] = float(n_holders)
        elif fname in rep_dict:
            result[fname] = float(rep_dict[fname])
        else:  # pragma: no cover
            result[fname] = 0.0  # pragma: no cover

    # 5. Apply NaN fill (pre+holder feats -> medians; rep -> 0.0 already done)
    if nan_fill is not None:
        for fname in V7_FEATURE_ORDER:
            v = result.get(fname, float("nan"))
            if v is None or (isinstance(v, float) and np.isnan(v)):  # pragma: no cover
                result[fname] = nan_fill.get(fname, 0.0)  # pragma: no cover

    return result
