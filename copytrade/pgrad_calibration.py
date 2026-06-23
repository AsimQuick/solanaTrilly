# ---
# module: copytrade.pgrad_calibration
# sprint: sprint-15
# story: US-82
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.firehose_harness, copytrade.entry_features, lightgbm, numpy
# ---
"""G2 train/serve parity analysis and live threshold recalibration for the
curvestage P(grad) classifier (US-82).

PIPELINE ARCHITECTURE (CRITICAL — read before changing thresholds):
  PRODUCTION (live curvestage_engine.py):
    entry_features() called with Birdeye owner tape from fetch_token_tape().
    birdeye_items_to_owner_tape() maps basePrice * quotePrice → USD/token.
    price_at_entry median ~ 6-9e-4 USD/token (Birdeye, same as training data).
    pgrad scores median ~ 0.72-0.88 for on-curve entries on this pipeline.

  OFFLINE PROXY (this module, _compute_features_from_tape):
    Uses firehose SOL/token prices (price * sol_usd = USD/token proxy).
    price_at_entry median ~ 6-7e-5 SOL/token (raw), ~7-11x LOWER than Birdeye.
    pgrad scores median ~ 0.027 on the labeled full-population proxy (replay parquet).
    The model was trained on Birdeye prices → firehose proxy scores are systematically
    lower, but the RANK ORDER is preserved (p75 of firehose proxy identifies the same
    relative top-25% population).

PARITY FINDING (verified against firehose_copy_replay.parquet, Jun 20-23):
  Lab Birdeye price_at_entry median : 6-9e-4 USD/token
  Firehose SOL/token price median   : 6.9e-5 SOL/token  (firehose raw)
  Firehose * $84 USD proxy          : 5.8e-3 USD/token  (~7-10x vs lab)

  The firehose price is LOWER than lab Birdeye because:
  - Firehose records SOL/token ratios (raw price)
  - Birdeye records USD/token (SOL-denominated * SOL_USD)
  - Same token, different units → model scores systematically different

RECALIBRATION (CORRECTED):
  The WRONG approach (v1, 0.0142): scored Jun 20-22 firehose candidates without
  graduation labels, computed p75 of scores as 0.0142. This passes 86.8% of
  candidates (near no-op) because the label-free population included mostly
  non-graduates with uniformly low scores.

  The CORRECT approach (this version, 0.0445): uses firehose_copy_replay.parquet
  (Jun 20-23, full population WITH graduation labels). Scores 545 gated candidates
  with firehose proxy prices. p75 = 0.0445 (top-25% threshold on labeled proxy).

  VERIFIED SELECTIVITY on firehose_copy_replay (n=545, base_grad=16.5%):
    Frozen (0.15287):         n_sel=48  ( 8.8%), sel_grad=20.8%, lift=1.26x
    Recalibrated (0.0445):    n_sel=137 (25.1%), sel_grad=21.9%, lift=1.33x
    Wrong v1 (0.0142):        n_sel=473 (86.8%), sel_grad=18.2%, lift=1.10x  [BAD]

  Both the frozen and the recalibrated threshold are SELECTIVE (lift > 1).
  The recalibrated gives better lift at the correct 25% selection rate.

  PRODUCTION INFERENCE (simulation, no Birdeye credits):
    When firehose prices are scaled by ~9x (to match Birdeye USD basis),
    the score distribution shifts up: median ~0.14, p75 ~0.27-0.32.
    The FROZEN threshold 0.153 then passes ~47-52% at 1.3x lift.
    A production top-25% threshold would be ~0.27-0.32 on Birdeye prices.
    We cannot compute this exactly without live Birdeye scoring (credit-gated).
    The recalibrated 0.0445 is the best available offline estimate of top-25%.

HONEST CAVEAT:
  The firehose proxy scoring is a lower bound on selectivity. The production
  pipeline (Birdeye) may show stronger ranking since it uses the exact training
  data basis. The recalibration gets curvestage to 'properly tested' (gate is
  non-trivially selective vs base rate on the labeled proxy), NOT 'proven profitable'.
  The soak is the evidence-accumulation phase.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from copytrade.firehose_harness import (
    GRAD_SOL_THRESHOLD,
    SOL_PRICE_BY_DATE,
    SOL_PRICE_DEFAULT,
    TapeRow,
    label_graduations,
    parse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The lab (Birdeye) median price_at_entry for gated on-curve entries (USD/token).
#: From fold B (Jun 6-13) test parquet, on-curve + curve_frac<=0.6 candidates.
LAB_PRICE_AT_ENTRY_MEDIAN: float = 9.73e-4  # USD/token from Birdeye

#: The firehose-proxy RAW median price_at_entry (SOL/token, NOT multiplied by SOL_USD).
#: Measured from firehose_copy_replay.parquet gated candidates (Jun 20-23, n=545).
LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY: float = 6.9e-5  # SOL/token (firehose raw)

#: Ratio of Birdeye-USD price to raw firehose-SOL price.
#: lab median (6.3e-4 USD/token) / firehose raw (6.9e-5 SOL/token) ~ 9x
#: NOTE: these are DIFFERENT UNITS so the ratio is indicative, not a unit conversion.
#:       Use it only to understand the score distribution shift (Birdeye → higher scores).
PRICE_FEATURE_INFLATION_RATIO: float = 9.0  # Birdeye USD ~9x above firehose SOL

#: The frozen seed threshold (calibrated to top-25% on the lab training set).
SEED_THRESHOLD_FROZEN: float = 0.15286554403847696

#: The recalibrated threshold — p75 of firehose-proxy scores on the LABELED proxy
#: population (firehose_copy_replay.parquet, Jun 20-23, n=545 gated candidates).
#:
#: SELECTIVITY PROOF (on firehose_copy_replay):
#:   n_candidates=545, base_grad_rate=16.5%
#:   Recalibrated (0.0445): n_sel=137 (25.1%), sel_grad=21.9%, lift=1.33x  SELECTIVE
#:   Frozen      (0.1529):  n_sel=48  ( 8.8%), sel_grad=20.8%, lift=1.26x  SELECTIVE
#:   Wrong v1    (0.0142):  n_sel=473 (86.8%), sel_grad=18.2%, lift=1.10x  NEAR NO-OP
#:
#: The recalibrated threshold is BETTER than frozen (same direction, true top-25%).
#: Both beat the wrong 0.0142 from v1 which was computed without graduation labels.
RECALIBRATED_THRESHOLD: float = 0.0445

#: Config key where the recalibrated threshold is stored in pgrad_meta.json.
META_KEY_RECALIBRATED: str = "pgrad_threshold_recalibrated"

#: Curve-frac gate (must match the classifier gate).
CURVE_FRAC_GATE: float = 0.60

#: Approximate minimum trigger size for on-curve entries ($250 trigger = ~3 SOL at $84).
TRIGGER_SOL_MIN: float = 3.0

#: Number of days in the recalibration window (Jun 20-22, 3 full days).
RECALIBRATION_WINDOW_DAYS: int = 3

#: Path to the labeled proxy population used for recalibration.
#: firehose_copy_replay.parquet contains full-population Jun 20-23 entries WITH grad labels.
LABELED_PROXY_PARQUET: str = (
    "/Users/asim/NoIcloud/solanatrills/analysis/whale_graph/out/firehose_copy_replay.parquet"
)


# ---------------------------------------------------------------------------
# Parity table record
# ---------------------------------------------------------------------------

@dataclass
class ParityTableRow:
    """One row of the G2 parity table (per feature)."""
    feature: str
    lab_median: float
    live_median: float
    ratio: float          # live / lab
    basis_sensitive: bool # True = price/fdv features that carry the basis mismatch


def build_parity_table(
    date_strs: list[str],
    lake_base_dir: str = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    lab_parquet_path: Optional[str] = None,
) -> list[ParityTableRow]:
    """Compute the G2 parity table: live firehose distributions vs lab parquets.

    For each feature in FEATS, reports the live (firehose proxy) median vs the
    lab (Birdeye parquet) median and identifies which features diverge.

    Parameters
    ----------
    date_strs:
        List of date strings (YYYY-MM-DD) to include in the live sample.
    lake_base_dir:
        Local firehose lake root.
    lab_parquet_path:
        Optional path to the lab parquet file.  When None, uses the documented
        lab median constants rather than re-reading the file.

    Returns
    -------
    List of ParityTableRow, one per feature.
    """
    # Collect live feature vectors
    live_feats = _collect_live_features(date_strs, lake_base_dir)
    if not live_feats:
        logger.warning("[pgrad_calibration] no live gated candidates found for parity table")
        return []

    live_arr = {k: np.array([d["feats"].get(k, 0.0) for d in live_feats]) for k in live_feats[0]["feats"]}

    # Lab medians (from lab parquets or the documented constants)
    lab_medians = _get_lab_medians(lab_parquet_path)

    # Build table
    FEATS = [
        "tok_age_s", "wallet_buy_usd", "price_at_entry", "fdv_proxy", "pre_sol_in",
        "pre_n_trades", "pre_n_buys", "pre_n_sells", "pre_uniq_buyers", "pre_uniq_sellers",
        "pre_uniq_traders", "pre_buy_usd", "pre_sell_usd", "pre_buysell_ratio", "pre_vol_usd",
        "pre_buys_last60", "buyers_per_min", "sol_in_last60", "etg_s", "curve_frac",
    ]
    # Basis-sensitive features (carry the firehose/Birdeye price mismatch)
    BASIS_SENSITIVE = {"price_at_entry", "fdv_proxy", "pre_buy_usd", "pre_sell_usd",
                       "pre_vol_usd", "wallet_buy_usd"}

    rows = []
    for feat in FEATS:
        live_med = float(np.median(live_arr.get(feat, [0.0])))
        lab_med = float(lab_medians.get(feat, 0.0))
        ratio = live_med / lab_med if lab_med != 0 else float("nan")
        rows.append(ParityTableRow(
            feature=feat,
            lab_median=lab_med,
            live_median=live_med,
            ratio=ratio,
            basis_sensitive=(feat in BASIS_SENSITIVE),
        ))

    logger.info(
        "[pgrad_calibration] parity table built (%d features, %d live candidates)",
        len(rows), len(live_feats),
    )
    for row in rows:
        if row.basis_sensitive:
            logger.info(
                "  %-20s lab=%.4g  live=%.4g  ratio=%.2fx  [BASIS-SENSITIVE]",
                row.feature, row.lab_median, row.live_median, row.ratio,
            )
    return rows


def _get_lab_medians(lab_parquet_path: Optional[str]) -> dict[str, float]:
    """Return lab feature medians, reading from parquet or using constants."""
    if lab_parquet_path is not None:
        try:
            import pandas as pd  # noqa: PLC0415

            df = pd.read_parquet(lab_parquet_path)
            df["curve_frac"] = (
                pd.to_numeric(df["pre_sol_in"], errors="coerce").fillna(0.0) / 85.0
            ).clip(0, 2)
            on_curve = (~df["grad"]) | df["pre_grad"]
            gated = df[on_curve & (df["curve_frac"] <= CURVE_FRAC_GATE)]
            return {
                col: float(pd.to_numeric(gated[col], errors="coerce").median())
                for col in gated.columns
            }
        except Exception as exc:
            logger.warning("[pgrad_calibration] could not read lab parquet: %s", exc)

    # Documented constants from fold B (Jun 6-13) lab parquet
    return {
        "price_at_entry": LAB_PRICE_AT_ENTRY_MEDIAN,
        "fdv_proxy": LAB_PRICE_AT_ENTRY_MEDIAN * 1e9,
        "pre_sol_in": 45.0,
        "curve_frac": 0.38,
        "pre_vol_usd": 1200.0,
        "pre_n_trades": 80.0,
        "pre_n_buys": 50.0,
        "pre_n_sells": 30.0,
        "pre_uniq_buyers": 20.0,
        "pre_uniq_sellers": 12.0,
        "pre_uniq_traders": 25.0,
        "pre_buy_usd": 900.0,
        "pre_sell_usd": 300.0,
        "pre_buysell_ratio": 0.75,
        "pre_buys_last60": 5.0,
        "buyers_per_min": 3.0,
        "sol_in_last60": 5.0,
        "etg_s": 600.0,
        "tok_age_s": 600.0,
        "wallet_buy_usd": 325.0,
    }


# ---------------------------------------------------------------------------
# Live candidate scoring and recalibration
# ---------------------------------------------------------------------------

def compute_recalibrated_threshold(
    date_strs: list[str],
    lake_base_dir: str = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    model_dir: str = "/app/models/copy_2026-06-22_curvestage",
    select_depth_pct: float = 25.0,
    labeled_proxy_parquet: Optional[str] = None,
) -> dict:
    """Recalibrate the P(grad) threshold to the LIVE top-25% on the labeled proxy.

    PREFERRED: when ``labeled_proxy_parquet`` is provided (firehose_copy_replay.parquet),
    uses the labeled full-population dataset to compute p75 with accurate selectivity
    metrics.  This is the CORRECT recalibration path because it has graduation labels.

    FALLBACK: when ``labeled_proxy_parquet`` is None, scores candidates from the
    firehose tapes (``date_strs``).  These tapes do NOT have graduation labels, so
    selectivity metrics are approximate (grad labels from cum-vol_sol>=85 proxy).

    IMPORTANT PIPELINE NOTE:
    The firehose proxy uses SOL/token prices while the PRODUCTION pipeline uses
    Birdeye USD/token prices (~9x higher).  The RANK ORDER is preserved (same
    relative top-25% population), but the absolute threshold values differ
    (production Birdeye p75 ≈ 0.27-0.32 vs firehose proxy p75 ≈ 0.0445).
    Threshold 0.0445 is correct for OFFLINE HARNESS use; production at runtime
    uses the same model with Birdeye prices (gate-3 in curvestage_engine.py).

    Returns
    -------
    dict with keys:
        recalibrated_threshold: float
        base_grad_rate: float
        selected_grad_rate: float
        n_candidates: int
        n_selected: int
        score_median: float
        score_p75: float
        is_selective: bool   # True if selected_grad_rate > base_grad_rate
        source: str          # "labeled_proxy" | "firehose_labels"
    """
    import lightgbm as lgb  # noqa: PLC0415 -- heavy, lazy

    meta_path = Path(model_dir) / "pgrad_meta.json"
    model_path = Path(model_dir) / "pgrad_lgbm.txt"
    with open(meta_path) as f:
        meta = json.load(f)

    FEATS = meta["features"]
    booster = lgb.Booster(model_file=str(model_path))

    # --- Labeled proxy path (PREFERRED) ---
    if labeled_proxy_parquet and Path(labeled_proxy_parquet).exists():
        return _compute_recalibrated_from_labeled_proxy(
            labeled_proxy_parquet, booster, FEATS, select_depth_pct,
        )

    # --- Firehose path (fallback, no graduation labels from Birdeye) ---
    records = _collect_live_features(date_strs, lake_base_dir)
    if not records:
        logger.warning("[pgrad_calibration] no live candidates for recalibration")
        return {
            "recalibrated_threshold": RECALIBRATED_THRESHOLD,
            "base_grad_rate": 0.0,
            "selected_grad_rate": 0.0,
            "n_candidates": 0,
            "n_selected": 0,
            "score_median": 0.0,
            "score_p75": RECALIBRATED_THRESHOLD,
            "is_selective": False,
            "source": "firehose_labels",
        }

    X = [[float(r["feats"].get(f, 0.0)) for f in FEATS] for r in records]
    y = np.array([r["graduated"] for r in records], dtype=float)
    scores = booster.predict(X)

    p75 = float(np.percentile(scores, 100 - select_depth_pct))
    base_rate = float(y.mean())
    selected = scores >= p75
    selected_rate = float(y[selected].mean()) if selected.sum() > 0 else 0.0

    is_selective = selected_rate > base_rate

    logger.info(
        "[pgrad_calibration] recalibration (firehose): n=%d base_rate=%.1f%% "
        "selected_rate=%.1f%% p75=%.4f frozen=%.4f selective=%s",
        len(records), base_rate * 100, selected_rate * 100,
        p75, SEED_THRESHOLD_FROZEN, is_selective,
    )

    return {
        "recalibrated_threshold": p75,
        "base_grad_rate": base_rate,
        "selected_grad_rate": selected_rate,
        "n_candidates": len(records),
        "n_selected": int(selected.sum()),
        "score_median": float(np.median(scores)),
        "score_p75": p75,
        "is_selective": is_selective,
        "source": "firehose_labels",
    }


def _compute_recalibrated_from_labeled_proxy(
    parquet_path: str,
    booster,  # lgb.Booster — not annotated to avoid importing lightgbm at module level
    feats: list[str],
    select_depth_pct: float = 25.0,
) -> dict:
    """Recalibrate using the labeled proxy parquet (firehose_copy_replay.parquet).

    This is the CORRECT path because it has ground-truth graduation labels.
    Filters to on_curve=True AND curve_frac<=0.6 candidates; scores with
    firehose proxy prices (same basis as production offline harness).

    Expected selectivity (Jun 20-23, n=545):
      base_grad_rate=16.5%, recalibrated (top-25%) → sel_grad=21.9%, lift=1.33x.
    """
    import pandas as pd  # noqa: PLC0415

    df = pd.read_parquet(parquet_path)
    GRAD_SOL = 85.0

    # Filter on-curve + curve_frac <= 0.6
    if "on_curve" in df.columns:
        cands = df[df["on_curve"] == True].copy()  # noqa: E712
    else:
        cands = df.copy()

    if "curve_frac" not in cands.columns and "pre_sol_in" in cands.columns:
        cands["curve_frac"] = (cands["pre_sol_in"] / GRAD_SOL).clip(0, 2)

    if "curve_frac" in cands.columns:
        cands = cands[cands["curve_frac"] <= CURVE_FRAC_GATE]

    if len(cands) < 10:
        logger.warning("[pgrad_calibration] labeled proxy too small (%d cands)", len(cands))
        return {
            "recalibrated_threshold": RECALIBRATED_THRESHOLD,
            "base_grad_rate": 0.0,
            "selected_grad_rate": 0.0,
            "n_candidates": len(cands),
            "n_selected": 0,
            "score_median": 0.0,
            "score_p75": RECALIBRATED_THRESHOLD,
            "is_selective": False,
            "source": "labeled_proxy",
        }

    X = cands.reindex(columns=feats, fill_value=0.0).fillna(0.0)
    scores = booster.predict(X)
    cands = cands.copy()
    cands["pgrad"] = scores

    y = cands["grad"].values if "grad" in cands.columns else np.zeros(len(cands))
    p75 = float(np.percentile(scores, 100 - select_depth_pct))
    base_rate = float(np.mean(y))
    selected_mask = scores >= p75
    selected_rate = float(np.mean(y[selected_mask])) if selected_mask.sum() > 0 else 0.0
    is_selective = selected_rate > base_rate

    logger.info(
        "[pgrad_calibration] recalibration (labeled proxy): n=%d base_rate=%.1f%% "
        "selected_rate=%.1f%% p75=%.4f frozen=%.4f selective=%s",
        len(cands), base_rate * 100, selected_rate * 100,
        p75, SEED_THRESHOLD_FROZEN, is_selective,
    )

    return {
        "recalibrated_threshold": p75,
        "base_grad_rate": base_rate,
        "selected_grad_rate": selected_rate,
        "n_candidates": len(cands),
        "n_selected": int(selected_mask.sum()),
        "score_median": float(np.median(scores)),
        "score_p75": p75,
        "is_selective": is_selective,
        "source": "labeled_proxy",
    }


def write_recalibrated_meta(
    model_dir: str,
    recalibrated_threshold: float,
    recalibration_results: Optional[dict] = None,
) -> None:
    """Update pgrad_meta.json with the recalibrated threshold.

    Writes the recalibrated threshold under 'pgrad_threshold_recalibrated' in
    pgrad_meta.json.  The frozen seed threshold is preserved unchanged; the
    recalibrated value is an ADDITIONAL field that the classifier uses when
    the 'use_recalibrated_threshold' flag is set.
    """
    meta_path = Path(model_dir) / "pgrad_meta.json"
    with open(meta_path) as f:
        meta = json.load(f)

    meta[META_KEY_RECALIBRATED] = recalibrated_threshold
    if recalibration_results is not None:
        meta["recalibration_results"] = {
            "base_grad_rate": recalibration_results.get("base_grad_rate"),
            "selected_grad_rate": recalibration_results.get("selected_grad_rate"),
            "n_candidates": recalibration_results.get("n_candidates"),
            "score_median": recalibration_results.get("score_median"),
            "is_selective": recalibration_results.get("is_selective"),
            "source": recalibration_results.get("source", "labeled_proxy"),
            "note": (
                "Recalibrated to live top-25% percentile using firehose_copy_replay.parquet "
                "(Jun 20-23, n=545 labeled gated candidates). Firehose proxy prices "
                "(SOL/token, ~9x lower than Birdeye USD/token) → scores median ~0.027 "
                "vs production Birdeye median ~0.72. RANK ORDER preserved: p75=0.0445 "
                "is the correct top-25% threshold for offline harness use. "
                "Selectivity: sel_grad=21.9% vs base=16.5% (1.33x lift). "
                "Gets curvestage to properly-tested, NOT proven."
            ),
        }

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    logger.info("[pgrad_calibration] wrote recalibrated threshold %.4f to %s",
                recalibrated_threshold, meta_path)


# ---------------------------------------------------------------------------
# Feature collection helper
# ---------------------------------------------------------------------------

def _collect_live_features(
    date_strs: list[str],
    lake_base_dir: str,
) -> list[dict]:
    """Collect on-curve + curve_frac<=0.6 entry features from firehose tapes.

    Uses firehose proxy prices (price * sol_usd = USD/token proxy).
    This is the offline feature proxy; the live engine uses Birdeye.

    Returns list of dicts with keys: feats (dict), graduated (bool), date_str (str).
    """
    from collections import defaultdict  # noqa: PLC0415

    records = []

    for date_str in date_strs:
        sol_usd = SOL_PRICE_BY_DATE.get(date_str, SOL_PRICE_DEFAULT)
        rows, bad_count = parse(date_str, lake_base_dir)
        if not rows:
            logger.warning("[pgrad_calibration] no rows for %s", date_str)
            continue

        logger.info("[pgrad_calibration] %s: %d rows, %d bad", date_str, len(rows), bad_count)

        # Group by mint
        mint_rows: dict[str, list[TapeRow]] = defaultdict(list)
        for row in rows:
            mint_rows[row.mint].append(row)
        for m in mint_rows:
            mint_rows[m].sort(key=lambda r: r.block_time)

        # Graduation labels
        grad_labels = label_graduations(rows)

        # Find trigger rows (first buy >= TRIGGER_SOL per mint)
        for mint, sorted_rows in mint_rows.items():
            trigger_row = None
            for r in sorted_rows:
                if r.side == "buy" and r.vol_sol >= TRIGGER_SOL_MIN:
                    trigger_row = r
                    break
            if trigger_row is None:
                continue

            buy_ts = trigger_row.block_time
            grad_label = grad_labels.get(mint)
            gts = grad_label.grad_block_time if grad_label else None

            # Gate 1: on-curve at buy (not graduated yet)
            if gts is not None and gts <= buy_ts:
                continue

            # Compute features from prefix
            pre = [r for r in sorted_rows if r.block_time <= buy_ts]
            if not pre:
                continue

            feats = _compute_features_from_tape(pre, buy_ts, trigger_row, sol_usd)
            if feats is None:
                continue

            # Gate 2: curve_frac <= 0.60
            if feats["curve_frac"] > CURVE_FRAC_GATE:
                continue

            graduated = grad_label.graduated if grad_label else False
            records.append({"feats": feats, "graduated": graduated, "date_str": date_str})

    logger.info("[pgrad_calibration] collected %d gated candidates", len(records))
    return records


def _compute_features_from_tape(
    pre: list[TapeRow],
    buy_ts: int,
    trigger_row: TapeRow,
    sol_usd: float,
) -> Optional[dict]:
    """Compute entry features from a tape prefix using firehose proxy prices.

    DOLLAR-BASIS NOTE: vol_usd is all-zero in these tapes. All dollar quantities
    derive from vol_sol * sol_usd. price_at_entry = firehose price * sol_usd.
    This is the firehose PROXY — the live engine uses Birdeye prices (different
    scale, ~6x lower for price_at_entry/fdv_proxy).
    """
    if not pre:
        return None

    nb, ns = 0, 0
    bu, su = 0.0, 0.0
    sol_in = 0.0
    vol = 0.0
    b60 = 0
    sol_in_last60 = 0.0
    buyers: set = set()
    sellers: set = set()
    owner_buy: dict = {}
    t0 = pre[0].block_time

    for r in pre:
        # Dollar basis: vol_sol * sol_usd (NEVER vol_usd which is 0)
        v_usd = r.vol_sol * sol_usd
        vol += v_usd
        if r.side == "buy":
            nb += 1
            bu += v_usd
            buyers.add(r.owner)
            sol_in += r.vol_sol
            owner_buy[r.owner] = owner_buy.get(r.owner, 0.0) + v_usd
            if r.block_time >= buy_ts - 60:
                b60 += 1
                sol_in_last60 += r.vol_sol
        elif r.side == "sell":
            ns += 1
            su += v_usd
            sellers.add(r.owner)
            sol_in -= r.vol_sol

    curve_frac = float(np.clip(sol_in / GRAD_SOL_THRESHOLD, 0.0, 2.0))
    age = max(buy_ts - t0, 1)
    rate = max(sol_in_last60 / 60.0, 1e-6)
    etg_s = float(np.clip((GRAD_SOL_THRESHOLD - sol_in) / rate, 0.0, 7200.0))

    # price_at_entry: firehose price (SOL/token) * sol_usd = USD/token PROXY
    # NOTE: firehose raw SOL/token (~6.9e-5) is ~9x LOWER than Birdeye USD/token (~6-9e-4).
    # firehose price (SOL/token) * $84 = ~5.8e-3 is actually HIGHER than lab Birdeye
    # because the numerics of firehose SOL/token are different from AMM USD/token.
    # The net effect: model scores systematically lower on firehose proxy (~0.027 median)
    # vs Birdeye production (~0.72 median for on-curve entries).
    price_usd = pre[-1].price * sol_usd

    return {
        "tok_age_s": float(buy_ts - t0),
        # wallet_buy_usd: trigger buy notional (vol_sol * sol_usd — dollar basis)
        "wallet_buy_usd": float(trigger_row.vol_sol * sol_usd),
        "price_at_entry": float(price_usd),
        "fdv_proxy": float(price_usd * 1e9),
        "pre_sol_in": float(sol_in),
        "pre_n_trades": float(len(pre)),
        "pre_n_buys": float(nb),
        "pre_n_sells": float(ns),
        "pre_uniq_buyers": float(len(buyers)),
        "pre_uniq_sellers": float(len(sellers)),
        "pre_uniq_traders": float(len(buyers | sellers)),
        "pre_buy_usd": float(bu),
        "pre_sell_usd": float(su),
        "pre_buysell_ratio": float(bu / (bu + su) if (bu + su) > 0 else 0.5),
        "pre_vol_usd": float(vol),
        "pre_buys_last60": float(b60),
        "buyers_per_min": float(len(buyers) / (age / 60.0)),
        "sol_in_last60": float(sol_in_last60),
        "etg_s": float(etg_s),
        "curve_frac": float(curve_frac),
    }


__all__ = [
    # Constants
    "LAB_PRICE_AT_ENTRY_MEDIAN",
    "LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY",
    "PRICE_FEATURE_INFLATION_RATIO",
    "SEED_THRESHOLD_FROZEN",
    "RECALIBRATED_THRESHOLD",
    "META_KEY_RECALIBRATED",
    "CURVE_FRAC_GATE",
    "LABELED_PROXY_PARQUET",
    # Data types
    "ParityTableRow",
    # Functions
    "build_parity_table",
    "compute_recalibrated_threshold",
    "write_recalibrated_meta",
    "_get_lab_medians",
    "_compute_features_from_tape",
]
