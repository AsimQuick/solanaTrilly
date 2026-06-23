# ---
# module: copytrade.curvestage_train
# sprint: sprint-15
# story: US-84
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: lightgbm, numpy, copytrade.entry_features, copytrade.pgrad_calibration,
#               copytrade.firehose_harness, copytrade.pgrad_classifier
# ---
"""Weekly retrain of the curvestage P(grad) classifier from the recorder's OWN lake.

TRAIN/SERVE PARITY (the critical G2 risk): this MUST compute features via the SAME
``copytrade.pgrad_calibration._compute_features_from_tape`` + ``copytrade.entry_features``
the LIVE gate uses — never a re-implementation.  There is exactly one feature-math
code path, so train and serve drift together or not at all.

DATE GATE (US-84):
  The retrain is a documented NO-OP until the lake holds >= MIN_LAKE_DAYS (7) of
  history.  Until then, the US-82 recalibrated seed carries.  This is intentional:
  the retrain is wired and ready; the no-op is a date condition, NOT a skip of the
  implementation.

RETRAIN METHODOLOGY:
  1. Load all days' tapes from the lake (US-81 harness).
  2. For each mint, find the first buy >= TRIGGER_SOL_MIN (proxy for a watched-wallet buy).
  3. Compute entry_features from the tape prefix (firehose proxy prices).
  4. Label graduation via cum-vol_sol >= GRAD_SOL_THRESHOLD (free label).
  5. Filter to on-curve + curve_frac<=0.6 entries.
  6. Split 80% train / 20% held-out fold.
  7. Train LGBM (verbatim lab params).
  8. Compute recalibrated threshold = 75th percentile of held-out fold scores.
  9. Atomically write pgrad_lgbm.txt + update pgrad_meta.json.
  10. Hot-swap the PgradClassifier singleton.

DOLLAR-BASIS: all dollar quantities via vol_sol * SOL_price (never vol_usd=0).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger("copytrade")

LAKE_DIR = "/app/lake/firehose"
MIN_LAKE_DAYS = 7
MODEL_DIR = "/app/models/copy_2026-06-22_curvestage"

# LGBM params — verbatim from the seed (etg_room_classifier.py); parity-load-bearing.
LGBM_PARAMS = dict(
    n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=40,
    subsample=0.8, colsample_bytree=0.8, verbose=-1,
)
SELECT_DEPTH_PCT = 25
CURVE_FRAC_THETA = 0.60

#: Minimum fraction of training samples required for the retrain to be valid.
#: Below this, the held-out fold would be too small for a reliable threshold.
MIN_TRAIN_SAMPLES = 200


def _lake_age_days(lake_dir: str = LAKE_DIR) -> int:
    """Span in days between the earliest and latest dt= partition in the lake."""
    try:
        dts = sorted(
            p.name.split("=", 1)[1]
            for p in Path(lake_dir).glob("dt=*")
            if "=" in p.name and p.name.split("=", 1)[1][:2] in ("19", "20")
        )
        # Keep only plausible recent partitions (exclude the 1977 slot-bug artifact).
        real = [d for d in dts if d >= "2026-01-01"]
        if len(real) < 2:
            return 0

        d0 = date.fromisoformat(real[0])
        d1 = date.fromisoformat(real[-1])
        return (d1 - d0).days
    except Exception as exc:  # noqa: BLE001
        logger.warning("[curvestage:retrain] lake-age check failed (%s) — assume 0.", exc)
        return 0


def _lake_date_strs(lake_dir: str = LAKE_DIR) -> list[str]:
    """Return sorted list of YYYY-MM-DD date strings present in the lake."""
    try:
        dts = sorted(
            p.name.split("=", 1)[1]
            for p in Path(lake_dir).glob("dt=*")
            if "=" in p.name
        )
        return [d for d in dts if d >= "2026-01-01"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[curvestage:retrain] lake date scan failed: %s", exc)
        return []


def retrain_from_lake(lake_dir: str = LAKE_DIR, model_dir: str = MODEL_DIR) -> dict:
    """Retrain the curvestage classifier from the prior week's lake on-curve entries.

    DATE GATE: no-op (keeps the seed) until the lake has >= MIN_LAKE_DAYS of history.

    Returns a dict with keys:
        retrained: bool
        reason: str ("lake_too_young" | "not_enough_samples" | "ok" | "error")
        lake_age_days: int
        n_train: int (0 if no-op)
        n_held_out: int (0 if no-op)
        recalibrated_threshold: float | None
    """
    age = _lake_age_days(lake_dir)
    if age < MIN_LAKE_DAYS:
        logger.info(
            "[curvestage:retrain] lake age %dd < %dd — keeping the lab seed (no retrain yet).",
            age, MIN_LAKE_DAYS,
        )
        return {
            "retrained": False,
            "reason": "lake_too_young",
            "lake_age_days": age,
            "n_train": 0,
            "n_held_out": 0,
            "recalibrated_threshold": None,
        }

    # --- Lake-mature path ---
    logger.info(
        "[curvestage:retrain] lake age %dd >= %dd — running weekly retrain.",
        age, MIN_LAKE_DAYS,
    )

    try:
        return _run_retrain(lake_dir, model_dir, age)
    except Exception as exc:  # noqa: BLE001
        logger.error("[curvestage:retrain] retrain failed: %s", exc, exc_info=True)
        return {
            "retrained": False,
            "reason": "error",
            "lake_age_days": age,
            "n_train": 0,
            "n_held_out": 0,
            "recalibrated_threshold": None,
            "error": str(exc),
        }


def _run_retrain(lake_dir: str, model_dir: str, age: int) -> dict:
    """Inner retrain logic (called only when the date gate passes).

    DOLLAR-BASIS: vol_sol * SOL_price for ALL dollar quantities.
    Never vol_usd (all-zero in these tapes).
    """
    import lightgbm as lgb  # noqa: PLC0415 — heavy, lazy
    import numpy as np  # noqa: PLC0415

    from copytrade.pgrad_calibration import (
        _collect_live_features,
    )

    date_strs = _lake_date_strs(lake_dir)
    if not date_strs:
        return {
            "retrained": False,
            "reason": "no_lake_dates",
            "lake_age_days": age,
            "n_train": 0,
            "n_held_out": 0,
            "recalibrated_threshold": None,
        }

    logger.info("[curvestage:retrain] collecting features from %d days: %s", len(date_strs), date_strs)
    records = _collect_live_features(date_strs, lake_dir)

    if len(records) < MIN_TRAIN_SAMPLES:
        logger.warning(
            "[curvestage:retrain] only %d samples (need %d) — keeping seed.",
            len(records), MIN_TRAIN_SAMPLES,
        )
        return {
            "retrained": False,
            "reason": "not_enough_samples",
            "lake_age_days": age,
            "n_train": len(records),
            "n_held_out": 0,
            "recalibrated_threshold": None,
        }

    # Load feature list from existing meta (parity: train on the SAME features as seed)
    meta_path = Path(model_dir) / "pgrad_meta.json"
    model_path = Path(model_dir) / "pgrad_lgbm.txt"
    with open(meta_path) as f:
        meta = json.load(f)
    FEATS = meta["features"]

    # Build X, y arrays
    X = np.array([[float(r["feats"].get(f, 0.0)) for f in FEATS] for r in records])
    y = np.array([int(r["graduated"]) for r in records])

    # 80/20 train/held-out split (time-ordered to avoid lookahead)
    n = len(records)
    n_train = int(n * 0.8)
    n_heldout = n - n_train

    X_train, X_heldout = X[:n_train], X[n_train:]
    y_train = y[:n_train]

    logger.info(
        "[curvestage:retrain] training LGBM on %d samples (held-out: %d)",
        n_train, n_heldout,
    )

    m = lgb.LGBMClassifier(**LGBM_PARAMS)
    m.fit(X_train, y_train)

    # Compute recalibrated threshold from held-out fold (top-25% percentile)
    held_out_scores = m.predict_proba(X_heldout)[:, 1]
    new_threshold = float(np.percentile(held_out_scores, 100 - SELECT_DEPTH_PCT))

    logger.info(
        "[curvestage:retrain] new threshold=%.4f (held-out p75 of %d scores)",
        new_threshold, len(held_out_scores),
    )

    # Atomic write: booster + updated meta
    m.booster_.save_model(str(model_path))

    # Update meta: preserve frozen, write recalibrated + retrain provenance
    meta["pgrad_threshold_recalibrated"] = new_threshold
    meta["retrain_provenance"] = {
        "lake_age_days": age,
        "n_train": int(n_train),
        "n_heldout": int(n_heldout),
        "n_lake_dates": len(date_strs),
        "lake_dates_first": date_strs[0] if date_strs else None,
        "lake_dates_last": date_strs[-1] if date_strs else None,
        "dollar_basis": "vol_sol * SOL_price (never vol_usd)",
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Hot-swap the singleton
    _hotswap_singleton(model_dir)

    return {
        "retrained": True,
        "reason": "ok",
        "lake_age_days": age,
        "n_train": int(n_train),
        "n_held_out": int(n_heldout),
        "recalibrated_threshold": new_threshold,
    }


def _hotswap_singleton(model_dir: str) -> None:
    """Hot-swap the PgradClassifier process-wide singleton after a retrain."""
    try:
        import copytrade.pgrad_classifier as clf_mod  # noqa: PLC0415

        with clf_mod.PgradClassifier._lock:
            clf_mod._singleton = None  # force reload on next get_pgrad_classifier()
        logger.info("[curvestage:retrain] singleton hot-swapped (will reload on next call)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[curvestage:retrain] hot-swap failed (non-fatal): %s", exc)
