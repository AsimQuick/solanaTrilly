# ---
# module: core.scorer
# sprint: sprint-9
# story: US-43 AC-43.1, AC-43.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: numpy, lightgbm (in-container), core.resolver, core.models
# ---
"""BlendScorer — ONE shared deterministic scorer for a LightGBM BLEND (AC-43.1/AC-43.2).

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

AC-43.2 — cutover risk resolution:
  Live serving scores ONE token at graduation with no same-day pool, so
  percentile ranks cannot be computed against a live pool.  The fix is a
  ReferenceDistribution: a frozen per-label score distribution banked from
  training data (or a rolling daily pool), stored at ScoringConfig.reference_dist_path
  (Principle #1 — config-driven, never a literal path in code).

  Parity invariant: if the reference distribution is built from the same pool
  that score_pool() ranked against, then score_single(token, ref_dist) produces
  the same label_ranks and blend_score as score_pool(pool)[token_index].

Container placement: intended to run in the scorer/worker container,
NOT in the web/gunicorn process (PRD §7.4 constraint #289).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.models import ModelRegistry


# ---------------------------------------------------------------------------
# AC-43.2 — ReferenceDistribution (frozen per-label score distribution)
# ---------------------------------------------------------------------------


class ReferenceDistribution:
    """Frozen per-label score distribution for live single-token percentile ranking.

    Resolves the cutover risk (oracle §4.2, AC-43.2): live serving arrives one
    token at a time with no same-day pool, so percentile ranks are computed
    against this banked reference instead of a live pool.

    The reference is typically banked from training (the per-label label-score
    distributions of the training/validation set) and stored as a JSON file
    pointed to by ScoringConfig.reference_dist_path (Principle #1 — the path
    comes from config, never hard-coded here).

    Percentile-rank formula (same as pool-based _percentile_rank):
        rank = (count in ref with value <= score) / N_ref
    This is the pool formula applied to the reference distribution instead of a
    live pool.

    Parity invariant (AC-43.2):
        If reference = ReferenceDistribution.from_pool_results(score_pool(pool), labels),
        then for any token i in the pool:
            score_single(pool[i], reference)["label_ranks"] == score_pool(pool)[i]["label_ranks"]
        because the reference contains the same label-score values as the pool.
    """

    def __init__(self, label_scores: dict[str, np.ndarray]) -> None:
        """
        Parameters
        ----------
        label_scores:
            {label: array_of_scores} — one array per label.  Stored sorted
            ascending (sorting is idempotent and enables binary-search if needed
            in the future).
        """
        self._scores: dict[str, np.ndarray] = {
            label: np.sort(np.array(scores, dtype=np.float64))
            for label, scores in label_scores.items()
        }

    @property
    def labels(self) -> list[str]:
        """Labels present in this reference distribution."""
        return list(self._scores.keys())

    @property
    def n_ref(self) -> dict[str, int]:
        """Number of reference samples per label."""
        return {label: int(len(arr)) for label, arr in self._scores.items()}

    def percentile_rank(self, label: str, score: float) -> float:
        """Percentile rank of a single score against the reference distribution.

        rank = (count in ref with value <= score) / N_ref

        Returns 0.0 when N_ref is empty (should not occur in production).
        """
        ref = self._scores[label]
        n = len(ref)
        if n == 0:
            return 0.0
        return float((ref <= score).sum() / n)

    def to_dict(self) -> dict[str, list[float]]:
        """Serialize to a JSON-serializable dict (list per label, sorted)."""
        return {label: arr.tolist() for label, arr in self._scores.items()}

    @classmethod
    def from_dict(cls, data: dict[str, list[float]]) -> "ReferenceDistribution":
        """Deserialize from a JSON-compatible dict (the inverse of to_dict())."""
        return cls({label: np.array(vals, dtype=np.float64) for label, vals in data.items()})

    @classmethod
    def from_pool_results(
        cls,
        pool_results: list[dict],
        labels: list[str],
    ) -> "ReferenceDistribution":
        """Build a reference distribution from score_pool() output.

        Used for parity testing (AC-43.2): constructing the reference from the
        pool's own label scores makes score_single() reproduce score_pool() ranks.

        Parameters
        ----------
        pool_results:
            Output of BlendScorer.score_pool() — list of result dicts.
        labels:
            Label names in manifest order (same as BlendScorer.labels).
        """
        return cls(
            {
                label: np.array(
                    [r["label_scores"][label] for r in pool_results],
                    dtype=np.float64,
                )
                for label in labels
            }
        )

    @classmethod
    def from_file(cls, path: "str | Path") -> "ReferenceDistribution":
        """Load from a JSON file at ScoringConfig.reference_dist_path.

        Supports TWO formats:

        1. Lab serving-bundle format (canonical, US-76 / directives §8/§9, built by
           analysis/graduated/build_serving_bundle.py): a JSON object with a
           ``score_grid_by_label`` key mapping each label to a per-label
           score→percentile GRID (``np.quantile(preds, linspace(0,1,1001))``, sorted
           ascending).  The count-based ``percentile_rank`` over this 1001-point grid
           reproduces the population percentile to grid resolution (~1e-3), which is
           exactly the cross-sectional ranking the live single-token path needs.

        2. Legacy format: a JSON object mapping label names directly to the full
           sorted per-label score list (``_``-prefixed keys are metadata, skipped).

        The lab format is detected by the presence of ``score_grid_by_label``.
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        grid = data.get("score_grid_by_label")
        if isinstance(grid, dict) and grid:
            # Lab serving-bundle format — the per-label quantile grid IS the
            # reference distribution for count-based percentile ranking.
            label_data = {k: v for k, v in grid.items() if isinstance(v, list)}
            return cls.from_dict(label_data)
        label_data = {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}
        return cls.from_dict(label_data)


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

    def score_single(
        self,
        features: dict,
        ref_dist: "ReferenceDistribution",
    ) -> dict:
        """Score ONE token against a frozen reference distribution (AC-43.2).

        Implements the same rank-average blend recipe as score_pool(), but ranks
        the token's per-label scores against the reference distribution instead
        of a live pool.  This resolves the cutover risk (oracle §4.2): live
        serving arrives one token at graduation with no same-day pool.

        The reference distribution is loaded from ScoringConfig.reference_dist_path
        (Principle #1 — config-driven) and banked from training data.

        Parameters
        ----------
        features:
            Feature dict for the single token.  Keys must cover self.feature_list;
            missing keys default to 0.0 (same convention as score_pool()).
        ref_dist:
            Frozen ReferenceDistribution providing per-label score arrays for
            percentile ranking.  Typically loaded via
            ReferenceDistribution.from_file(config.scoring.reference_dist_path).

        Returns
        -------
        dict:
            {
              "label_scores": {label: float},   # per-label seed-averaged prediction
              "label_ranks":  {label: float},   # percentile-rank vs reference, (0, 1]
              "blend_score":  float,             # mean of the 3 label ranks
            }

        Parity invariant (AC-43.2):
            If ref_dist == ReferenceDistribution.from_pool_results(
                    scorer.score_pool(features_list), scorer.labels),
            then for any token i in the pool:
                score_single(features_list[i], ref_dist)["label_ranks"]
                    == score_pool(features_list)[i]["label_ranks"]
            because ref_dist contains the same label scores as the pool, so the
            count-based rank formula yields identical values.
        """
        feature_names = self._feature_list
        X = np.array(
            [[float(features.get(feat, 0.0)) for feat in feature_names]],
            dtype=np.float64,
        )  # shape (1, F)

        # Step 1: per-label seed-average (same formula as score_pool)
        label_scores_out: dict[str, float] = {}
        for label in self._labels:
            label_bsts = self._boosters[label]
            preds = np.array(
                [bst.predict(X)[0] for bst in label_bsts],
                dtype=np.float64,
            )  # shape (n_seeds,)
            label_scores_out[label] = float(preds.mean())

        # Step 2: percentile-rank each label score against the reference distribution
        label_ranks_out: dict[str, float] = {}
        for label in self._labels:
            label_ranks_out[label] = ref_dist.percentile_rank(label, label_scores_out[label])

        # Step 3: blend = mean of the 3 label ranks
        blend_score = float(np.mean(list(label_ranks_out.values()), dtype=np.float64))

        return {
            "label_scores": label_scores_out,
            "label_ranks": label_ranks_out,
            "blend_score": blend_score,
        }
