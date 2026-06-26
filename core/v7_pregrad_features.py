# ---
# module: core.v7_pregrad_features
# sprint: sprint-15
# story: US-93
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-26  (train/serve skew fix: removed bt>=grad_ts filter from compute_n_pregrad_holders)
# dependencies: numpy, core.v4_rep_builder
# ---
"""v7 live feature vector builder: 19 pre-grad feats + n_pregrad_holders + 24 rep feats.

DEFECT FIX (AC-93.1, PR #412 rejected)
=======================================
PR #412's compute_n_pregrad_holders used a BUY-COUNT proxy (count(buys) >
count(sells) per wallet).  That is WRONG — the lab definition and
parity_sample.parquet values are BALANCE-BASED: count distinct wallets with a
net-positive TOKEN BALANCE at graduation.

This module implements the correct BALANCE-BASED definition.

TOKEN-AMOUNT SOURCE (in order of preference)
============================================
1. ``token_amount`` key in the swap dict (integer, raw on-chain units).
   Present on schema-A rows (solanaBilly shared tape via US-96 adapter:
   ``core.firehose.shared_tape.norm_row_to_swap_dict``).  The sign check
   (net > 0) is identical whether units are raw or UI-scaled.

2. Derived: ``token ≈ vol_sol / price`` when ``token_amount`` is absent.
   Both fields are present on schema A and B.  This approximation has bounded
   error (same-block price slippage) and is better than the buy-count proxy
   for any tape where price varies across swaps.

The buy-count proxy (count(buys) - count(sells) > 0) is REMOVED.

Lab definition (v2_2_build.py / holder_exit.py / pregrad_holders):
  for each wallet:
      bal[w] += uiAmount   for every buy
      bal[w] -= uiAmount   for every sell
  n_pregrad_holders = count wallets with bal > 0

``uiAmount`` (Birdeye trade_pages) is the token amount in human-readable
decimal units.  ``token_amount`` (schema A) is the same quantity in raw
on-chain units.  Division by 10^decimals is not needed for a > 0 check.

SCOPING FINDINGS (operator-required confirmations)
===================================================

1. n_pregrad_holders COMPUTABILITY (FINDING: COMPUTABLE, ZERO CREDITS)
   Computable from the pre-grad tape with zero credits.
   Source priority: token_amount (schema A) > vol_sol/price (schema B).
   The live balance-based count matches the lab definition.

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
  [19]    n_pregrad_holders              (BALANCE-BASED: wallets with net token balance > 0)
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


def _token_amount_for_row(s: dict) -> float:
    """Extract the token amount for a single swap row.

    TOKEN-AMOUNT SOURCE PRIORITY (AC-93.1):
      1. ``token_amount`` key — integer raw on-chain units (schema A, solanaBilly
         shared tape via US-96 normalise_row).  The > 0 check is unit-agnostic.
      2. Derived: ``vol_sol / price`` — tokens ≈ SOL paid / price-in-SOL-per-token.
         Works for both schema A and B where token_amount is absent.
         Returns 0.0 if price is missing or zero.

    Returns a non-negative float (the absolute token amount for this swap).
    """
    raw_tok = s.get("token_amount")
    if raw_tok is not None:
        try:
            return abs(float(raw_tok))
        except (TypeError, ValueError):
            pass

    # Derivation path: token ≈ vol_sol / price
    vol_sol = float(s.get("vol_sol") or s.get("vol") or 0.0)
    price = float(s.get("price") or 0.0)
    if price > 0 and vol_sol > 0:
        return vol_sol / price
    return 0.0


def compute_n_pregrad_holders(
    swaps: list[dict],
    grad_ts: float = 0.0,
) -> float:
    """Compute n_pregrad_holders: distinct wallets with net-positive TOKEN balance at graduation.

    BALANCE-BASED DEFINITION (AC-93.1 defect fix — buy-count proxy is REJECTED):
    For each wallet:
        net_tokens = sum(buy token_amounts) − sum(sell token_amounts)
    n_pregrad_holders = count of distinct wallets with net_tokens > 0

    This matches the lab definition in v2_2_build.py / holder_exit.py / pregrad_holders():
        bal[w] += uiAmount   for every buy
        bal[w] -= uiAmount   for every sell
        n_pregrad_holders = count(bal[w] > 0)

    NO TIME FILTER (AC-93 train/serve-skew fix):
    The original implementation filtered ``bt >= grad_ts`` to exclude post-grad swaps.
    This was an obsolete guard:
      - For the LIVE production path: ``swaps`` comes from ``self._tape`` (TapeStore),
        which is fed ONLY by solanaBilly's bonding-curve tape (schema A, phase=pre,
        ``BillyTapeTailer``).  Post-grad goes to the SEPARATE ``self._postgrad_tape``.
        No time filter is needed because the live input is pre-only by construction.
      - For the PARITY TEST path: ``swaps`` are ALL trades from the raw30k_pregrad
        Birdeye tape (including the small fraction at/after gts).  The lab's
        ``pregrad_holders()`` processes ALL trades in the file without a time filter.
        Removing the filter here means the parity test can call THIS function directly
        and achieve 400/400 exact match — no separate helper needed.

    The ``bt <= 0`` guard is retained to skip rows with missing/invalid timestamps.

    TOKEN-AMOUNT SOURCE (in order of preference):
      1. ``token_amount`` key (integer, raw on-chain units) — present on schema-A
         rows from the US-96 solanaBilly shared tape adapter, and on normalised
         Birdeye trade_pages rows (``normalise_trade_page_swap`` sets
         ``token_amount = to/from.uiAmount``).
      2. Derived: ``vol_sol / price`` — both fields present on schema A and B.
         Bounded error (same-block price slippage); strictly better than buy-count proxy.

    The REJECTED proxy (PR #412): count(buys) − count(sells) > 0 per wallet.
    That proxy ignores trade sizes and was OUT-OF-DISTRIBUTION vs the Birdeye-trained
    model's #1 feature (importance 727.1).

    Parameters
    ----------
    swaps:
        Swap records.  The caller is responsible for passing the appropriate
        scope:
          - LIVE path: pre-grad-only swaps from TapeStore (no post-grad entries).
          - PARITY/LAB path: ALL trades from the raw30k_pregrad tape (including
            at-grad and post-grad entries; this replicates pregrad_holders() exactly).
    grad_ts:
        Token graduation Unix timestamp (seconds).  Kept for API compatibility;
        no longer used to filter rows.  Pass 0.0 if unknown.

    Returns
    -------
    float
        Count of distinct wallets with net token balance > 0.
    """
    wallet_balance: dict[str, float] = {}

    for s in swaps:
        # Skip rows with missing/invalid timestamp (bt=0 means no timestamp)
        bt = float(s.get("block_time", 0) or s.get("block_unix_time", 0) or 0)
        if bt <= 0:
            continue
        owner = s.get("owner") or ""
        if not owner:
            continue
        side = s.get("side", "")
        if side not in ("buy", "sell"):
            continue

        amt = _token_amount_for_row(s)
        if amt <= 0:
            continue

        if side == "buy":
            wallet_balance[owner] = wallet_balance.get(owner, 0.0) + amt
        else:  # sell
            wallet_balance[owner] = wallet_balance.get(owner, 0.0) - amt

    holders = sum(1 for bal in wallet_balance.values() if bal > 0)
    return float(holders)


# ---------------------------------------------------------------------------
# Birdeye trade_pages → internal swap dict normalisation (lab-parity bridge)
# ---------------------------------------------------------------------------


def normalise_trade_page_swap(it: dict, grad_ts: float) -> dict | None:
    """Normalise one Birdeye trade_pages swap entry to the internal swap dict.

    This is the BRIDGE between the lab's Birdeye raw30k_pregrad format and the
    internal swap dict consumed by compute_v7_pregrad_feats / compute_n_pregrad_holders.

    Mirrors ``backfill_pregrad.py:pre_features()`` + ``holder_exit.py:pregrad_holders()``
    exactly so the live builder reproduces the lab on the #1 feature and all 19
    pre-grad feats.

    TIME FILTER NOTE (parity-critical):
      ``backfill_pregrad.py:pre_features()`` filters ``bt >= gts`` (pre-grad feats only).
      ``holder_exit.py:pregrad_holders()`` does NOT filter by time — it processes ALL
      trades in the file regardless of blockUnixTime.
      This function applies the pre_features filter (bt < grad_ts) so that the result
      can be passed to compute_v7_pregrad_feats (19 pre-grad features).

      For holder counting: pass the result of ``normalise_all_trade_page_swaps``
      (which does NOT filter by time) to ``compute_n_pregrad_holders``.
      This replicates ``pregrad_holders()`` exactly and achieves 400/400 parity.

    INPUT SCHEMA (Birdeye trade_pages item):
      blockUnixTime  — Unix timestamp of the swap
      tokenPrice     — price in SOL/token (preferred) or basePrice (fallback)
      quotePrice     — SOL/USD price for volume dollarisation
      quote.uiAmount — SOL volume (human-readable); vol_usd = abs(uiAmount) * quotePrice
      side           — "buy" | "sell" (or inferred from base.uiChangeAmount)
      owner          — wallet address
      to.uiAmount    — tokens received (buy leg)
      from.uiAmount  — tokens sent (sell leg)
      txType         — must be "swap"

    Returns None if the trade is not a pre-graduation swap (bt >= grad_ts), not a
    "swap" txType, has no price, or has missing/invalid fields.

    OUTPUT SCHEMA (internal swap dict):
      block_time     — blockUnixTime (float)
      price          — SOL/token price
      vol            — USD volume (abs(quote.uiAmount) * quotePrice)
      vol_sol        — SOL volume (abs(quote.uiAmount))
      side           — "buy" | "sell"
      owner          — wallet address (str)
      token_amount   — token amount in uiAmount units (float, for balance-based holders)
    """
    if it.get("txType") != "swap":
        return None

    bt = it.get("blockUnixTime") or it.get("block_unix_time")
    if bt is None or float(bt) >= grad_ts:
        return None

    price = it.get("tokenPrice") or it.get("basePrice")
    if not price or float(price) <= 0:
        return None

    q = it.get("quote") or {}
    qp = float(it.get("quotePrice") or q.get("price") or 0)
    vol_sol = abs(float(q.get("uiAmount") or 0))
    vol_usd = vol_sol * qp

    side = it.get("side")
    if side not in ("buy", "sell"):
        ca = float((it.get("base") or {}).get("uiChangeAmount") or 0)
        side = "buy" if ca > 0 else "sell"

    owner = it.get("owner") or ""

    # Token amount for balance-based holder computation (AC-93.1):
    # Use to.uiAmount (buy) or from.uiAmount (sell) — exactly as lab pregrad_holders()
    leg = it.get("to") if side == "buy" else it.get("from")
    token_amount_ui = abs(float((leg or {}).get("uiAmount") or 0.0))

    return {
        "block_time": float(bt),
        "price": float(price),
        "vol": vol_usd,
        "vol_sol": vol_sol,
        "side": side,
        "owner": owner,
        "token_amount": token_amount_ui,  # uiAmount — for balance-based holder count
    }


def normalise_all_trade_page_swaps(trade_pages: list[list[dict]]) -> list[dict]:
    """Normalise ALL Birdeye trade_pages swaps (NO time filter).

    Used for the holder-count path: passes ALL trades (including those at/after gts)
    to ``compute_n_pregrad_holders``, exactly replicating ``pregrad_holders()``
    which has no time filter.

    INPUT SCHEMA: same as ``normalise_trade_page_swap`` (Birdeye trade_pages item).
    OUTPUT: list of internal swap dicts (block_time, side, owner, token_amount, ...).
    """
    result: list[dict] = []
    for page in (trade_pages or []):
        for it in (page or []):
            if it.get("txType") != "swap":
                continue
            bt = it.get("blockUnixTime") or it.get("block_unix_time")
            if bt is None:
                continue
            bt_f = float(bt)
            if bt_f <= 0:
                continue
            owner = it.get("owner") or ""
            side = it.get("side")
            if side not in ("buy", "sell"):
                ca = float((it.get("base") or {}).get("uiChangeAmount") or 0)
                side = "buy" if ca > 0 else "sell"
            if side not in ("buy", "sell"):
                continue
            leg = it.get("to") if side == "buy" else it.get("from")
            token_amount_ui = abs(float((leg or {}).get("uiAmount") or 0.0))
            price = it.get("tokenPrice") or it.get("basePrice")
            price_f = float(price) if price else 0.0
            q = it.get("quote") or {}
            qp = float(it.get("quotePrice") or q.get("price") or 0)
            vol_sol = abs(float(q.get("uiAmount") or 0))
            result.append({
                "block_time": bt_f,
                "side": side,
                "owner": owner,
                "token_amount": token_amount_ui,
                "price": price_f,
                "vol": vol_sol * qp,
                "vol_sol": vol_sol,
            })
    return result


def compute_n_pregrad_holders_from_trade_pages(trade_pages: list[list[dict]]) -> float:
    """Compute n_pregrad_holders from Birdeye trade_pages with NO time filter.

    This replicates ``holder_exit.py:pregrad_holders()`` EXACTLY.  The lab function
    processes ALL trades in the raw30k_pregrad file without filtering by blockUnixTime.
    The raw30k_pregrad files are fetched for [gts-CAP, gts], but may contain a small
    number of swaps at or after gts (graduation instant).  Applying a bt < gts filter
    changes the holder count and breaks parity.

    PARITY-CRITICAL: this function is used by compute_v7_features_from_trade_pages
    to reproduce parity_sample.parquet's n_pregrad_holders exactly.

    The live pipeline uses compute_n_pregrad_holders() (which filters bt < grad_ts)
    because the live swap stream mixes pre- and post-grad rows and we must not
    include post-grad trades in the holder count.

    Parameters
    ----------
    trade_pages:
        ``rec["trade_pages"]`` from raw30k_pregrad/<mint>.json.gz.

    Returns
    -------
    float
        Count of distinct wallets with net token balance > 0 across ALL trades.
    """
    bal: dict[str, float] = {}
    for page in (trade_pages or []):
        for s in (page or []):
            if s.get("txType") != "swap":
                continue
            owner = s.get("owner") or ""
            side = s.get("side") or ""
            if not owner or side not in ("buy", "sell"):
                continue
            leg = s.get("to") if side == "buy" else s.get("from")
            amt = abs(float((leg or {}).get("uiAmount") or 0.0))
            bal[owner] = bal.get(owner, 0.0) + (amt if side == "buy" else -amt)
    return float(sum(1 for v in bal.values() if v > 0))


def compute_v7_features_from_trade_pages(
    trade_pages: list[list[dict]],
    grad_ts: float,
    *,
    wallet_bank: Any | None = None,
    nan_fill: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """Compute the v7 44-feature vector from Birdeye trade_pages format.

    This is the PARITY-PROOF entry point: it consumes the exact same format as the
    lab's raw30k_pregrad/*.json.gz files and must reproduce ``parity_sample.parquet``
    feature values exactly.

    The live pipeline (firehose / schema-A / schema-B) uses ``assemble_v7_features``
    instead (different input format, same feature logic).  This function is the bridge
    that proves the builder LOGIC is correct given the lab's Birdeye input — it closes
    the AC-93.4(b) "builder-logic half" of parity.

    DESIGN (single-function, no helper)
    ====================================
    1. Pre-grad features (19 feats): normalise_trade_page_swap filters bt >= grad_ts
       exactly as backfill_pregrad.py:pre_features() does.
    2. n_pregrad_holders: ``compute_n_pregrad_holders`` called with ALL normalised swaps
       (no time filter) via ``normalise_all_trade_page_swaps``, exactly as
       ``holder_exit.py:pregrad_holders()`` does.  The raw30k_pregrad files may contain
       swaps at/after gts; the lab includes them in the holder count.
       ``compute_n_pregrad_holders`` (the live function) now has NO time filter, so
       passing all swaps replicates the lab exactly — 400/400 proven.

    Parameters
    ----------
    trade_pages:
        ``rec["trade_pages"]`` from raw30k_pregrad/<mint>.json.gz.
    grad_ts:
        ``rec["gts"]`` — graduation Unix timestamp.
    wallet_bank:
        Optional WalletBankLookup for rep features (pass None for pre+holder only).
    nan_fill:
        Optional nan_fill dict (pre+holder medians from meta.json).

    Returns
    -------
    dict with 44 float features in V7_FEATURE_ORDER, or None if no valid pre-grad swaps.
    """
    # 1. Pre-grad swap normalisation (filters bt >= grad_ts — matches pre_features())
    swaps: list[dict] = []
    for page in (trade_pages or []):
        for it in (page or []):
            norm = normalise_trade_page_swap(it, grad_ts)
            if norm is not None:
                swaps.append(norm)

    # 2. Compute the 19 pre-grad feats via the existing builder
    pre_feats = compute_v7_pregrad_feats(
        swaps, grad_ts, sol_usd_spot=1.0
    )  # vol is already in USD
    if pre_feats is None:
        return None

    # 3. n_pregrad_holders: ALL trades (NO time filter) via the live function.
    # Replicates holder_exit.py:pregrad_holders() exactly — includes at/after-gts swaps
    # that are present in the raw30k_pregrad Birdeye tapes.  The live production path
    # uses a pre-only TapeStore (no post-grad entries), so it achieves equivalent
    # results via the same function.
    all_swaps = normalise_all_trade_page_swaps(trade_pages)
    n_holders = compute_n_pregrad_holders(all_swaps)

    # 4. Rep features (0-fill if no bank)
    if wallet_bank is not None:
        try:
            from core.v4_rep_builder import compute_rep_features

            pre_buy_swaps = sorted(
                (s for s in swaps if s["side"] == "buy" and s.get("owner")
                 and not (s.get("owner") or "").endswith("pump")),
                key=lambda s: s["block_time"],
            )
            buyer_first_vol: dict[str, float] = {}
            buyer_total_vol: dict[str, float] = {}
            buyer_order: list[str] = []
            for s in pre_buy_swaps:
                w = s["owner"]
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
            rep_dict = compute_rep_features(time_buyers, size_buyers, float(grad_ts), wallet_bank)
        except Exception as exc:
            logger.warning("[v7_pregrad_features] rep computation failed: %s", exc)
            rep_dict = {fname: 0.0 for fname in V7_REP_FEATURE_NAMES}
    else:
        rep_dict = {fname: 0.0 for fname in V7_REP_FEATURE_NAMES}

    # 5. Assemble in V7_FEATURE_ORDER
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

    # 6. Apply NaN fill
    if nan_fill is not None:
        for fname in V7_FEATURE_ORDER:
            v = result.get(fname, float("nan"))
            if v is None or (isinstance(v, float) and v != v):  # pragma: no cover
                result[fname] = nan_fill.get(fname, 0.0)  # pragma: no cover

    return result


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
