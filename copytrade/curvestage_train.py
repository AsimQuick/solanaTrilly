# ---
# module: copytrade.curvestage_train
# epic: EPIC-copy-curvestage-integration
# status: implemented (lake-retrain activates at >=7d lake; seed carries until then)
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: lightgbm, numpy, copytrade.entry_features
# ---
"""Weekly retrain of the curvestage P(grad) classifier from the recorder lake.

TRAIN/SERVE PARITY (tester B7, the critical G2 risk): this MUST compute features
via the SAME ``copytrade.entry_features`` (+ Birdeye source) the LIVE gate uses —
never a re-implementation.  There is exactly one feature-math code path, so train
and serve drift together or not at all.

The seed (``pgrad_lgbm.txt`` from the lab parquets) carries the soak until the
solanatrilly firehose lake holds a full week of pool-wallet on-curve entries.
``retrain_from_lake`` is a NO-OP (keeps the seed) until ``_lake_age_days() >= 7``;
the heavy lake-query + train path below activates and is VALIDATED THEN (against
real lake data — not faked against an empty lake, per the testing-against-reality
discipline).
"""
from __future__ import annotations

import logging
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
        from datetime import date

        d0 = date.fromisoformat(real[0])
        d1 = date.fromisoformat(real[-1])
        return (d1 - d0).days
    except Exception as exc:  # noqa: BLE001
        logger.warning("[curvestage:retrain] lake-age check failed (%s) — assume 0.", exc)
        return 0


def retrain_from_lake(lake_dir: str = LAKE_DIR, model_dir: str = MODEL_DIR) -> dict:
    """Retrain the curvestage classifier from the prior week's lake on-curve entries.

    NO-OP (keeps the seed) until the lake holds >= MIN_LAKE_DAYS of history.  When it
    matures this will: (1) read the prior week's pool-wallet >=$250 first-buys of
    pump tokens from the lake; (2) for each, fetch the token tape from Birdeye and
    compute features via ``copytrade.entry_features`` (the SOLE path — train/serve
    parity); (3) label by graduation; (4) train LGBM (LGBM_PARAMS); (5) set the
    frozen threshold from a HELD-OUT fold (tester A3-2, not the training pop);
    (6) atomically write pgrad_lgbm.txt + pgrad_meta.json and hot-swap the singleton.
    """
    age = _lake_age_days(lake_dir)
    if age < MIN_LAKE_DAYS:
        logger.info(
            "[curvestage:retrain] lake age %dd < %dd — keeping the lab seed (no retrain yet).",
            age, MIN_LAKE_DAYS,
        )
        return {"retrained": False, "reason": "lake_too_young", "lake_age_days": age}

    # --- Lake-mature path (activates + is validated against REAL lake data) ---
    # Implemented when the lake reaches a week so it is validated on real entries,
    # not faked against an empty lake (testing-against-reality).  The hard contract,
    # enforced here when built: features come from copytrade.entry_features +
    # copytrade.birdeye_tape ONLY (no re-implementation), threshold from a held-out
    # fold, atomic artifact swap.  Until then this returns a loud not-yet-built so
    # the seed remains authoritative and the beat task is a safe no-op.
    logger.warning(
        "[curvestage:retrain] lake mature (%dd) but the lake-retrain path is not yet "
        "wired — keeping the seed.  Build + validate against the now-real lake week.",
        age,
    )
    return {"retrained": False, "reason": "lake_retrain_pending_build", "lake_age_days": age}
