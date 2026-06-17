# ---
# module: core.scorer
# sprint: sprint-9
# story: US-43 AC-43.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: numpy, lightgbm (in-container), core.resolver, core.models
# ---
"""BlendScorer — ONE shared deterministic scorer for a LightGBM BLEND (AC-43.1).

Selection recipe (meta.json:selection_recipe, PRD §7.4):
  For each of the 3 labels:
    1. Average the 5 seed boosters' raw predictions → label_score
    2. Percentile-rank that label_score across the candidate pool
  blend = mean of the 3 percentile-ranks

Config-driven (Principle #1, PRD §6.4.4): labels, seeds, and feature order come
from ModelRegistry (populated from meta.json via promote_blend()), loaded via
get_active_model().  No literals embedded in this module.

Reads features via caller-supplied feature dicts (Principle #2, PRD §6.4.5):
the scorer never assembles features itself; it receives pre-extracted feature
dicts produced by the single shared US-30 extractor (FeatureExtractor).

Percentile-rank formula:
  rank(i) = (number of tokens in pool with score <= score_i) / N
  Values are in (0, 1]; the highest-scoring token receives rank 1.0.
  Equal scores receive equal rank (natural for the blend average).

Container placement: intended to run in the scorer/worker container,
NOT in the web/gunicorn process (PRD §7.4 constraint #289).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.models import ModelRegistry


def _percentile_rank(scores: np.ndarray) -> np.ndarray:
    """Pool percentile rank: rank(i) = (n with score <= scores[i]) / N.

    Returns a float64 array of the same length in (0, 1].
    """
    N = len(scores)
    return np.array([(scores <= s).sum() / N for s in scores], dtype=np.float64)


class BlendScorer:
    """ONE shared deterministic scorer for a LightGBM BLEND artifact (AC-43.1).

    Implements the rank-average blend recipe from meta.json:selection_recipe:
      1. For each label: average the 5 seed boosters' raw predictions.
      2. Percentile-rank that label score across the candidate pool.
      3. blend = mean of the 3 label percentile-ranks.

    Instantiate via from_registry() for live use (loads boosters from the
    active ModelRegistry entry's artifact_dir), or directly for testing/replay
    (pass model_entry and boosters explicitly).
    """

    def __init__(
        self,
        model_entry: "ModelRegistry",
        boosters: dict[str, list],
    ) -> None:
        """
        Parameters
        ----------
        model_entry:
            ModelRegistry row providing the blend config (labels, seeds,
            feature_list).  Must have a populated labels_seeds_manifest.
        boosters:
            {label: [booster_seed0, ..., booster_seedN]} mapping.  Each list
            must be ordered by ascending seed index and match the manifest.
        """
        self._model_entry = model_entry
        self._boosters = boosters
        manifest = model_entry.labels_seeds_manifest
        self._labels: list[str] = list(manifest["labels"])
        self._feature_list: list[str] = list(model_entry.feature_list)

    # ------------------------------------------------------------------
    # Factory — live use
    # ------------------------------------------------------------------

    @classmethod
    def from_registry(
        cls,
        model_entry: "ModelRegistry | None" = None,
    ) -> "BlendScorer":
        """Load boosters from the active ModelRegistry entry's artifact_dir.

        Parameters
        ----------
        model_entry:
            If provided, use this registry row directly.  If None, load the
            currently active row from the DB via get_active_model().

        Raises
        ------
        RuntimeError
            When no active model is registered (call promote_blend() first).
        ImportError
            When lightgbm is not installed (not in the scorer container).
        FileNotFoundError
            When a booster file is missing from artifact_dir.
        """
        if model_entry is None:
            from core.resolver import get_active_model

            model_entry = get_active_model()
        if model_entry is None:
            raise RuntimeError(
                "No active model in ModelRegistry — run promote_blend() first"
            )

        import lightgbm as lgb  # in-container dependency (Docker Rules, AC-42.2)

        artifact_dir = Path(model_entry.artifact_dir)
        manifest = model_entry.labels_seeds_manifest
        labels: list[str] = list(manifest["labels"])
        seeds: list[int] = list(manifest["seeds"])

        boosters: dict[str, list] = {}
        for label in labels:
            label_boosters = []
            for seed in seeds:
                path = artifact_dir / "boosters" / f"{label}_s{seed}.txt"
                bst = lgb.Booster(model_file=str(path))
                label_boosters.append(bst)
            boosters[label] = label_boosters

        return cls(model_entry, boosters)

    # ------------------------------------------------------------------
    # Properties (config-driven — values come from ModelRegistry, no literals)
    # ------------------------------------------------------------------

    @property
    def labels(self) -> list[str]:
        """Label names from the blend manifest (3 labels, e.g. ctrl/oracle/liq)."""
        return list(self._labels)

    @property
    def feature_list(self) -> list[str]:
        """Ordered feature names in booster binding order (PRD §7.4)."""
        return list(self._feature_list)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_pool(self, features_list: list[dict]) -> list[dict]:
        """Score a pool of tokens per the rank-average blend recipe.

        Parameters
        ----------
        features_list:
            Each dict must contain the features in self.feature_list.
            Missing features default to 0.0.

        Returns
        -------
        list[dict]
            One result dict per token in input order:
            {
              "label_scores": {label: float},  # per-label seed-averaged prediction
              "label_ranks":  {label: float},  # percentile-rank in pool, (0, 1]
              "blend_score":  float,            # mean of the 3 label ranks
            }
        """
        if not features_list:
            return []

        feature_names = self._feature_list
        X = np.array(
            [[float(f.get(feat, 0.0)) for feat in feature_names] for f in features_list],
            dtype=np.float64,
        )

        # Step 1: per-label seed-average
        label_scores: dict[str, np.ndarray] = {}
        for label in self._labels:
            label_bsts = self._boosters[label]
            preds = np.array(
                [bst.predict(X) for bst in label_bsts],
                dtype=np.float64,
            )  # shape (n_seeds, N)
            label_scores[label] = preds.mean(axis=0)  # shape (N,)

        # Step 2: percentile-rank each label score across the pool
        label_ranks: dict[str, np.ndarray] = {}
        for label in self._labels:
            label_ranks[label] = _percentile_rank(label_scores[label])

        # Step 3: blend = mean of the 3 label ranks
        blend_arr = np.mean(
            [label_ranks[label] for label in self._labels],
            axis=0,
            dtype=np.float64,
        )

        results = []
        for i in range(len(features_list)):
            results.append(
                {
                    "label_scores": {
                        label: float(label_scores[label][i]) for label in self._labels
                    },
                    "label_ranks": {
                        label: float(label_ranks[label][i]) for label in self._labels
                    },
                    "blend_score": float(blend_arr[i]),
                }
            )

        return results
