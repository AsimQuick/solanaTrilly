# ---
# module: core.pf_v1_scorer
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: lightgbm, json, pathlib, numpy
# ---
"""pf_v1_scorer.py — LightGBM scorer for trilly_pf_v1.

Loads ``models/trilly_pf_v1/model.txt`` (a single raw LightGBM Booster, NOT
the BlendScorer/meta.json format used by v3.2/v4/v7) and
``models/trilly_pf_v1/config.json`` (feature order + gate threshold).

HOST-LOCAL GATE
===============
``model.txt`` is NOT committed to Git (like the v7 boosters) and is NOT present
in CI.  The module-level flag ``MODEL_PRESENT`` is ``True`` iff model.txt exists
at import time.  Tests that require actual scoring are decorated::

    @pytest.mark.skipif(not MODEL_PRESENT, reason="model.txt not present (CI)")

Tests that validate wiring, feature order, and gate logic run unconditionally.

ZERO CREDITS
============
All inputs are local.  No Birdeye/Helius/Dune calls.

PUBLIC API
==========
  MODEL_PRESENT : bool  — True iff model.txt is present
  CONFIG        : dict  — parsed config.json (available without model.txt)
  FEATURE_ORDER : list[str]  — the 6 feature names in config order
  FLAG_THRESHOLD: float — gate threshold from config.selection.flag_threshold
  score(features: dict) -> float   — predict; raises if MODEL_PRESENT=False
  gate_passes(score: float) -> bool — True iff score >= FLAG_THRESHOLD
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PF_V1_DIR = _REPO_ROOT / "models" / "trilly_pf_v1"
_CONFIG_PATH = _PF_V1_DIR / "config.json"
_MODEL_PATH = _PF_V1_DIR / "model.txt"

# ---------------------------------------------------------------------------
# Config (always available — committed to Git)
# ---------------------------------------------------------------------------

if _CONFIG_PATH.is_file():
    with _CONFIG_PATH.open(encoding="utf-8") as _fh:
        CONFIG: dict = json.load(_fh)
else:
    # Fallback so the module is importable in environments without the artifact dir.
    # This should not happen in production (config.json is committed).
    CONFIG = {
        "name": "trilly_pf_v1",
        "features_in_order": ["f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1"],
        "selection": {"flag_threshold": 0.7952236368890475},
    }
    logger.warning("[pf_v1_scorer] config.json not found at %s — using hardcoded defaults.", _CONFIG_PATH)

FEATURE_ORDER: list[str] = CONFIG.get("features_in_order", [
    "f_value", "f_momentum", "f_size", "f_holders", "f_life", "f_top1"
])
FLAG_THRESHOLD: float = float(CONFIG.get("selection", {}).get("flag_threshold", 0.7952236368890475))

# ---------------------------------------------------------------------------
# MODEL_PRESENT flag — mirrors BOOSTERS_PRESENT in v7_scorer
# ---------------------------------------------------------------------------

MODEL_PRESENT: bool = _MODEL_PATH.is_file()

# ---------------------------------------------------------------------------
# Booster singleton (loaded lazily on first score() call if MODEL_PRESENT)
# ---------------------------------------------------------------------------

_booster: Any = None  # lightgbm.Booster once loaded


def _load_booster() -> Any:
    """Load the LightGBM Booster from model.txt (once, cached).

    Raises ImportError if lightgbm is not installed (host-local dep).
    Raises FileNotFoundError if model.txt is absent.
    """
    global _booster  # noqa: PLW0603
    if _booster is not None:
        return _booster
    if not MODEL_PRESENT:
        raise FileNotFoundError(
            f"trilly_pf_v1 model.txt not found at {_MODEL_PATH}. "
            "model.txt is a host-only artifact (not committed to Git). "
            "Ensure it is placed in models/trilly_pf_v1/ on the host / VPS."
        )
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise ImportError(
            "lightgbm is required to load pf_v1 model.txt. "
            "It is a host-local dependency (not installed in CI)."
        ) from exc
    _booster = lgb.Booster(model_file=str(_MODEL_PATH))
    logger.info(
        "[pf_v1_scorer] Loaded trilly_pf_v1 booster from %s; "
        "features=%s flag_threshold=%.8f",
        _MODEL_PATH,
        FEATURE_ORDER,
        FLAG_THRESHOLD,
    )
    return _booster


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------


def score(features: dict) -> float:
    """Score a single token with the pf_v1 LightGBM booster.

    Parameters
    ----------
    features:
        Dict with at least the 6 keys in FEATURE_ORDER.  Missing values default
        to 0.0 (consistent with the training guard that rejected zero-total-SOL
        tapes — live code should not call score() with missing features, but a
        safe fallback is better than a KeyError on the hot path).

    Returns
    -------
    float
        Raw LightGBM predict probability (the booster is binary:sigmoid,
        so output is in [0, 1]).

    Raises
    ------
    FileNotFoundError
        If model.txt is not present (MODEL_PRESENT=False).
    """
    import numpy as np  # noqa: PLC0415

    booster = _load_booster()
    row = [float(features.get(f, 0.0)) for f in FEATURE_ORDER]
    X = np.array([row], dtype=np.float64)
    result = booster.predict(X)
    return float(result[0])


def gate_passes(raw_score: float) -> bool:
    """Return True iff raw_score >= FLAG_THRESHOLD.

    The flag_threshold from config.json is ``0.7952236368890475`` (top~10%/day
    in the training population).  This is the sole gate — there is no rank-cut
    / reference-dist mechanism for pf_v1 (the threshold is absolute, not
    population-relative).
    """
    return raw_score >= FLAG_THRESHOLD


def is_pf_v1_model(artifact_dir: "Path | str | None") -> bool:
    """Return True if the artifact_dir looks like a trilly_pf_v1 artifact.

    Detection criteria (either is sufficient):
      1. artifact_dir contains config.json with ``"name": "trilly_pf_v1"``.
      2. artifact_dir contains model.txt but NOT meta.json (raw booster, no
         BlendScorer contract).

    This is the canonical detection used by ``_build_scoring_context_sync``
    in run_firehose.py to route to the pf_v1 path instead of BlendScorer.

    Parameters
    ----------
    artifact_dir:
        Path to the model artifact directory (from ModelRegistry.artifact_dir).

    Returns
    -------
    bool
    """
    if artifact_dir is None:
        return False
    try:
        p = Path(artifact_dir)
    except (TypeError, ValueError):
        return False

    cfg_path = p / "config.json"
    if cfg_path.is_file():
        try:
            with cfg_path.open(encoding="utf-8") as fh:
                cfg = json.load(fh)
            if cfg.get("name") == "trilly_pf_v1":
                return True
        except Exception:  # noqa: BLE001
            pass

    # Fallback: raw model.txt present but no meta.json (BlendScorer contract)
    model_txt = p / "model.txt"
    meta_json = p / "meta.json"
    if model_txt.is_file() and not meta_json.is_file():
        return True

    return False
