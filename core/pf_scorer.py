# ---
# module: core.pf_scorer
# sprint: sprint-16
# story: pf-v1-serving-lane
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-28
# dependencies: lightgbm, json, pathlib, numpy
# ---
"""pf_scorer.py — CONTRACT-DRIVEN LightGBM scorer for trilly_pf_v* models.

Supersedes pf_v1_scorer.py.  Loads model.txt + contract (contract.json OR
config.json) from a given model directory, exposing a unified scoring API that
handles BOTH:
  - trilly_pf_v1 (6 feat / config.json / binary:sigmoid / flag_threshold 0.795)
  - trilly_pf_v2 (13 feat / contract.json / regression L1 / threshold -0.2425)

CONTRACT DETECTION
==================
  1. If contract.json exists in the model dir -> use it.
     feature_order from contract.feature_vector.order
     threshold from contract.gate.live_pred_threshold
  2. Else if config.json exists -> use it (v1 legacy).
     feature_order from config.features_in_order
     threshold from config.selection.flag_threshold

MODEL_PRESENT FLAG
==================
model.txt is NOT committed to Git.  MODEL_PRESENT is True iff model.txt exists
at import-of-this-class time.  Tests that require actual scoring are decorated::

    @pytest.mark.skipif(not MODEL_PRESENT, reason="model.txt not present (CI)")

For run_firehose.py integration, a PfScorer instance is created from the active
model's artifact_dir; its MODEL_PRESENT attribute reflects whether model.txt was
found at that path at scorer instantiation time.

NO CREDITS / NO NETWORK
========================
All inputs are local.

PUBLIC API
==========
  PfScorer.from_dir(model_dir)           — class method; loads contract + booster
  scorer.feature_order : list[str]       — features in contract order
  scorer.threshold : float               — gate threshold
  scorer.MODEL_PRESENT : bool            — True iff model.txt present
  scorer.score(features: dict) -> float  — predict; raises if MODEL_PRESENT=False
  scorer.gate_passes(score: float) -> bool  — True iff score >= threshold
  is_pf_model(artifact_dir) -> bool     — True iff dir is a pf v* artifact
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public detection helper (used by run_firehose + tests)
# ---------------------------------------------------------------------------


def is_pf_model(artifact_dir: "Path | str | None") -> bool:
    """Return True if the artifact_dir is a trilly_pf_v* artifact.

    Detection criteria (any of):
      1. contract.json exists with a ``name`` starting with ``"trilly_pf"``
         (covers v2+).
      2. config.json exists with a ``name`` starting with ``"trilly_pf"``
         (covers v1 legacy).
      3. model.txt present but no meta.json (raw booster, no BlendScorer contract)
         — backwards-compatible fallback for edge cases.

    Parameters
    ----------
    artifact_dir:
        Path to the model artifact directory (from ModelRegistry.artifact_dir).
    """
    if artifact_dir is None:
        return False
    try:
        p = Path(artifact_dir)
    except (TypeError, ValueError):
        return False

    # contract.json (v2+)
    contract_path = p / "contract.json"
    if contract_path.is_file():
        try:
            with contract_path.open(encoding="utf-8") as fh:
                ctr = json.load(fh)
            if str(ctr.get("name", "")).startswith("trilly_pf"):
                return True
        except Exception:  # noqa: BLE001
            pass

    # config.json (v1 legacy)
    cfg_path = p / "config.json"
    if cfg_path.is_file():
        try:
            with cfg_path.open(encoding="utf-8") as fh:
                cfg = json.load(fh)
            if str(cfg.get("name", "")).startswith("trilly_pf"):
                return True
        except Exception:  # noqa: BLE001
            pass

    # Fallback: raw model.txt without BlendScorer meta.json
    model_txt = p / "model.txt"
    meta_json = p / "meta.json"
    if model_txt.is_file() and not meta_json.is_file():
        return True

    return False


# ---------------------------------------------------------------------------
# PfScorer: contract-driven LightGBM scorer for any trilly_pf_v* model
# ---------------------------------------------------------------------------


class PfScorer:
    """Contract-driven LightGBM scorer for trilly_pf_v* models.

    Reads the contract (contract.json or config.json) from the model dir and
    exposes a uniform scoring API regardless of model version.

    Attributes
    ----------
    model_dir : Path
    model_name : str
    feature_order : list[str]    feature names in contract order
    threshold : float            gate threshold (live_pred_threshold or flag_threshold)
    MODEL_PRESENT : bool         True iff model.txt found at instantiation time
    """

    def __init__(
        self,
        model_dir: Path,
        model_name: str,
        feature_order: list[str],
        threshold: float,
        model_present: bool,
    ) -> None:
        self.model_dir = model_dir
        self.model_name = model_name
        self.feature_order = feature_order
        self.threshold = threshold
        self.MODEL_PRESENT = model_present
        self._booster: Any = None  # loaded lazily

    @classmethod
    def from_dir(cls, model_dir: "Path | str") -> "PfScorer":
        """Load a PfScorer from a model artifact directory.

        Reads contract.json (preferred, v2+) or config.json (legacy v1) to
        determine feature_order and threshold.  model.txt existence sets
        MODEL_PRESENT; actual booster loading is deferred to first score() call.

        Raises
        ------
        FileNotFoundError
            If neither contract.json nor config.json is found in model_dir.
        ValueError
            If the contract is malformed (missing feature_vector.order or threshold).
        """
        p = Path(model_dir)

        contract_path = p / "contract.json"
        config_path = p / "config.json"

        if contract_path.is_file():
            with contract_path.open(encoding="utf-8") as fh:
                ctr = json.load(fh)
            model_name = str(ctr.get("name", p.name))
            fv = ctr.get("feature_vector", {})
            feature_order = fv.get("order")
            if not feature_order:
                raise ValueError(
                    f"contract.json at {contract_path} has no feature_vector.order"
                )
            gate = ctr.get("gate", {})
            threshold = gate.get("live_pred_threshold")
            if threshold is None:
                # fallback to selection.flag_threshold (v1-style contract)
                threshold = ctr.get("selection", {}).get("flag_threshold")
            if threshold is None:
                raise ValueError(
                    f"contract.json at {contract_path} has no gate.live_pred_threshold "
                    "or selection.flag_threshold"
                )
            threshold = float(threshold)
            logger.info(
                "[pf_scorer] Loaded from contract.json: name=%s n_features=%d threshold=%.8f",
                model_name, len(feature_order), threshold,
            )
        elif config_path.is_file():
            with config_path.open(encoding="utf-8") as fh:
                cfg = json.load(fh)
            model_name = str(cfg.get("name", p.name))
            feature_order = cfg.get("features_in_order")
            if not feature_order:
                raise ValueError(
                    f"config.json at {config_path} has no features_in_order"
                )
            threshold = cfg.get("selection", {}).get("flag_threshold")
            if threshold is None:
                raise ValueError(
                    f"config.json at {config_path} has no selection.flag_threshold"
                )
            threshold = float(threshold)
            logger.info(
                "[pf_scorer] Loaded from config.json (legacy v1): name=%s n_features=%d threshold=%.8f",
                model_name, len(feature_order), threshold,
            )
        else:
            raise FileNotFoundError(
                f"No contract.json or config.json found in {p}. "
                "Ensure the model artifact is staged in the model directory."
            )

        model_present = (p / "model.txt").is_file()
        if model_present:
            logger.info("[pf_scorer] model.txt PRESENT at %s", p / "model.txt")
        else:
            logger.warning(
                "[pf_scorer] model.txt NOT FOUND at %s — scoring disabled (CI gate / pre-deploy).",
                p / "model.txt",
            )

        return cls(
            model_dir=p,
            model_name=model_name,
            feature_order=list(feature_order),
            threshold=threshold,
            model_present=model_present,
        )

    def _load_booster(self) -> Any:
        """Load the LightGBM Booster from model.txt (lazy, cached)."""
        if self._booster is not None:
            return self._booster
        if not self.MODEL_PRESENT:
            raise FileNotFoundError(
                f"{self.model_name} model.txt not found at {self.model_dir / 'model.txt'}. "
                "model.txt is a host-only artifact (not committed to Git). "
                "Ensure it is placed in the model dir on the host / VPS."
            )
        try:
            import lightgbm as lgb  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "lightgbm is required to load model.txt. "
                "It is a host-local dependency (not installed in CI)."
            ) from exc
        self._booster = lgb.Booster(model_file=str(self.model_dir / "model.txt"))
        logger.info(
            "[pf_scorer] Loaded booster for %s; n_features=%d threshold=%.8f",
            self.model_name, len(self.feature_order), self.threshold,
        )
        return self._booster

    def score(self, features: dict) -> float:
        """Score a single token with the LightGBM booster.

        Parameters
        ----------
        features:
            Dict with at least the keys in self.feature_order.  Missing values
            default to 0.0 (safe fallback — live code should not call score()
            with missing features, but 0-fill beats a KeyError on the hot path).

        Returns
        -------
        float
            Raw LightGBM prediction.
            - For v2 (regression L1): predicted $ (may be negative).
            - For v1 (binary:sigmoid): probability in [0, 1].

        Raises
        ------
        FileNotFoundError
            If MODEL_PRESENT=False (model.txt absent).
        """
        import numpy as np  # noqa: PLC0415

        booster = self._load_booster()
        row = [float(features.get(f, 0.0)) for f in self.feature_order]
        X = np.array([row], dtype=np.float64)
        result = booster.predict(X)
        return float(result[0])

    def gate_passes(self, raw_score: float) -> bool:
        """Return True iff raw_score >= self.threshold.

        Gate contract:
          - v1: threshold ~0.795 (probability >= 0.795 -> top ~10%/day).
          - v2: threshold -0.2425 (predicted $ >= -0.2425 -> top ~10%/day).
            Negative threshold is expected; the score is a predicted $ value.
        """
        return raw_score >= self.threshold
