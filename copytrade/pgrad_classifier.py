# ---
# module: copytrade.pgrad_classifier
# sprint: sprint-15
# story: US-82
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: lightgbm, json
# ---
"""P(graduate) classifier scorer for the curve-stage copy cohort.

Loads the LGBM booster + frozen-threshold metadata produced by the seed build
(or the weekly retrain) and scores an ``entry_features`` dict at the live buy
instant.  Gate 3 = ``p_grad >= pgrad_threshold`` (≈ top-25% among gated
candidates — the frozen threshold replaces a relative rank since live scoring
sees one token at a time, exactly like the model track's rank_cut).

THRESHOLD HIERARCHY (US-82):
  1. ``pgrad_threshold_recalibrated`` in pgrad_meta.json — written by the weekly
     retrain (US-84) or the calibration tool (US-82); PREFERRED when present.
  2. ``pgrad_threshold_frozen`` — the seed threshold set at training time; used
     when no recalibrated value is present yet.

  The recalibrated threshold is the 75th percentile of the live P(grad) score
  distribution on local firehose tapes, making the gate top-25% relative to the
  actual live population (vs the lab's historical training population).

Pure w.r.t. the loaded model: same model + same features → same score.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from copytrade.entry_features import FEATS

DEFAULT_MODEL_DIR = "/app/models/copy_2026-06-22_curvestage"

#: Config key for the recalibrated threshold (written by US-82 calibration tool).
_META_KEY_RECALIBRATED = "pgrad_threshold_recalibrated"


class PgradClassifier:
    """Loads ``pgrad_lgbm.txt`` + ``pgrad_meta.json`` and scores feature dicts."""

    _lock = threading.Lock()

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR) -> None:
        self._model_dir = Path(model_dir)
        self._booster = None
        self._meta: dict = {}
        self._feats: list[str] = FEATS
        self._threshold: float = 1.0  # fail-closed until loaded
        self._threshold_source: str = "not-loaded"

    def load(self) -> "PgradClassifier":
        """Load the booster + meta (idempotent; lightgbm imported lazily).

        Threshold selection (US-82):
          - If ``pgrad_threshold_recalibrated`` is present in meta, use it
            (live population top-25% percentile, preferred).
          - Otherwise fall back to ``pgrad_threshold_frozen`` (seed threshold).
        """
        import lightgbm as lgb  # noqa: PLC0415 — heavy import, lazy

        meta_path = self._model_dir / "pgrad_meta.json"
        model_path = self._model_dir / "pgrad_lgbm.txt"
        with self._lock:
            self._meta = json.loads(meta_path.read_text())
            # Use the model's own feature order if present (defensive against drift).
            self._feats = list(self._meta.get("features", FEATS))
            # US-82: prefer recalibrated threshold when present
            if _META_KEY_RECALIBRATED in self._meta:
                self._threshold = float(self._meta[_META_KEY_RECALIBRATED])
                self._threshold_source = "recalibrated"
            else:
                self._threshold = float(self._meta["pgrad_threshold_frozen"])
                self._threshold_source = "frozen_seed"
            self._booster = lgb.Booster(model_file=str(model_path))
        return self

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def threshold_source(self) -> str:
        """'recalibrated' | 'frozen_seed' | 'not-loaded'"""
        return self._threshold_source

    @property
    def meta(self) -> dict:
        return self._meta

    def predict(self, features: dict) -> float:
        """Return P(graduate) for one ``entry_features`` dict.

        Raises if the classifier has not been loaded (fail-loud at startup, never
        silently score 0).  Missing/non-numeric features default to 0.0 (matches
        the lab's ``fillna(0.0)`` in etg_room_classifier.prep).
        """
        if self._booster is None:
            raise RuntimeError("PgradClassifier.predict called before load()")
        row = []
        for f in self._feats:
            v = features.get(f, 0.0)
            try:
                row.append(float(v))
            except (TypeError, ValueError):
                row.append(0.0)
        # Booster.predict expects a 2D array-like; returns a 1-element array.
        return float(self._booster.predict([row])[0])

    def passes(self, features: dict) -> tuple[bool, float]:
        """Gate 3: (p_grad >= frozen threshold, p_grad)."""
        p = self.predict(features)
        return (p >= self._threshold, p)


_singleton: Optional[PgradClassifier] = None


def get_pgrad_classifier(model_dir: str = DEFAULT_MODEL_DIR) -> PgradClassifier:
    """Process-wide singleton (load once at engine startup, never per-trigger)."""
    global _singleton
    if _singleton is None:
        _singleton = PgradClassifier(model_dir).load()
    return _singleton
