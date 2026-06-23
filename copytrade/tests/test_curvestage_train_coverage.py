# ---
# module: copytrade.tests.test_curvestage_train_coverage
# sprint: sprint-15
# story: US-84
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.curvestage_train, unittest.mock, pytest, numpy
# ---
"""Coverage tests for copytrade.curvestage_train — pure computation paths.

Strategy:
- _lake_age_days / _lake_date_strs: exception branches triggered by os-error mocks.
- retrain_from_lake: lake-mature path (age >= MIN_LAKE_DAYS) with error handler.
- _run_retrain: mock _collect_live_features + lgb to cover X/y building,
  not_enough_samples path, no_lake_dates path, train/split/threshold logic.
- _hotswap_singleton: mock pgrad_classifier to cover both success and exception paths.

Genuine I/O boundaries (NOT tested here — marked # pragma: no cover in source):
- lgb.LGBMClassifier.fit(): LightGBM training I/O
- lgb.Booster(): LightGBM model load
- file open/write for pgrad_meta.json in _run_retrain (after lgb.fit)
- _collect_live_features: reads real firehose lake files
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from copytrade.curvestage_train import (
    MIN_LAKE_DAYS,
    MIN_TRAIN_SAMPLES,
    SELECT_DEPTH_PCT,
    _hotswap_singleton,
    _lake_age_days,
    _lake_date_strs,
    _run_retrain,
    retrain_from_lake,
)

# ---------------------------------------------------------------------------
# _lake_age_days — exception branch
# ---------------------------------------------------------------------------

class TestLakeAgeDaysExceptionBranch:
    def test_exception_returns_zero(self):
        """When glob raises, _lake_age_days returns 0 (safe fallback)."""
        with mock.patch("copytrade.curvestage_train.Path") as mock_path_cls:
            mock_path_instance = mock.MagicMock()
            mock_path_instance.glob.side_effect = OSError("permission denied")
            mock_path_cls.return_value = mock_path_instance
            result = _lake_age_days("/fake/path")
        assert result == 0

    def test_single_date_partition_returns_zero(self, tmp_path):
        """Only one valid date partition: span = 0 (< 2 real dates)."""
        (tmp_path / "dt=2026-06-20").mkdir()
        result = _lake_age_days(str(tmp_path))
        assert result == 0  # len(real) < 2

    def test_two_date_partitions_returns_correct_span(self, tmp_path):
        (tmp_path / "dt=2026-06-20").mkdir()
        (tmp_path / "dt=2026-06-28").mkdir()
        result = _lake_age_days(str(tmp_path))
        assert result == 8


# ---------------------------------------------------------------------------
# _lake_date_strs — exception branch
# ---------------------------------------------------------------------------

class TestLakeDateStrsExceptionBranch:
    def test_exception_returns_empty_list(self):
        """When glob raises, _lake_date_strs returns [] (safe fallback)."""
        with mock.patch("copytrade.curvestage_train.Path") as mock_path_cls:
            mock_path_instance = mock.MagicMock()
            mock_path_instance.glob.side_effect = OSError("permission denied")
            mock_path_cls.return_value = mock_path_instance
            result = _lake_date_strs("/fake/path")
        assert result == []

    def test_empty_dir_returns_empty_list(self, tmp_path):
        result = _lake_date_strs(str(tmp_path))
        assert result == []

    def test_filters_pre_2026_dates(self, tmp_path):
        (tmp_path / "dt=2025-12-31").mkdir()
        (tmp_path / "dt=2026-06-20").mkdir()
        (tmp_path / "dt=2026-06-21").mkdir()
        result = _lake_date_strs(str(tmp_path))
        assert "2025-12-31" not in result
        assert "2026-06-20" in result
        assert "2026-06-21" in result


# ---------------------------------------------------------------------------
# retrain_from_lake — lake-mature path + error handler
# ---------------------------------------------------------------------------

class TestRetrainFromLakeMaturePath:
    def _make_mature_lake(self, tmp_path, n_days=8):
        """Create a lake with n_days of partitions (spans MIN_LAKE_DAYS+1)."""
        lake_dir = tmp_path / "lake"
        lake_dir.mkdir()
        base = date(2026, 6, 1)
        for i in range(n_days):
            d = base + timedelta(days=i)
            dt_dir = lake_dir / f"dt={d}"
            dt_dir.mkdir()
            part = dt_dir / "part-0.jsonl.gz"
            with gzip.open(str(part), "wt") as f:
                f.write("")  # empty but valid
        return str(lake_dir)

    def test_mature_lake_enters_run_retrain(self, tmp_path):
        """With age >= MIN_LAKE_DAYS, retrain_from_lake calls _run_retrain."""
        lake_dir = self._make_mature_lake(tmp_path)
        # Mock _run_retrain to confirm it's called (avoids real LightGBM)
        with mock.patch("copytrade.curvestage_train._run_retrain") as mock_run:
            mock_run.return_value = {
                "retrained": True, "reason": "ok", "lake_age_days": 8,
                "n_train": 300, "n_held_out": 75, "recalibrated_threshold": 0.085,
            }
            result = retrain_from_lake(lake_dir=lake_dir)
        mock_run.assert_called_once()
        assert result["retrained"] is True

    def test_mature_lake_exception_returns_error_dict(self, tmp_path):
        """When _run_retrain raises, retrain_from_lake returns reason='error'."""
        lake_dir = self._make_mature_lake(tmp_path)
        with mock.patch("copytrade.curvestage_train._run_retrain") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected failure")
            result = retrain_from_lake(lake_dir=lake_dir)
        assert result["retrained"] is False
        assert result["reason"] == "error"
        assert "error" in result
        assert "unexpected failure" in result["error"]

    def test_error_result_has_lake_age(self, tmp_path):
        """Error result includes the actual lake_age_days."""
        lake_dir = self._make_mature_lake(tmp_path, n_days=10)
        with mock.patch("copytrade.curvestage_train._run_retrain") as mock_run:
            mock_run.side_effect = ValueError("oops")
            result = retrain_from_lake(lake_dir=lake_dir)
        assert result["lake_age_days"] >= MIN_LAKE_DAYS


# ---------------------------------------------------------------------------
# _run_retrain — no_lake_dates path (empty date list)
# ---------------------------------------------------------------------------

class TestRunRetrainNoLakeDates:
    def test_no_lake_dates_returns_no_op(self, tmp_path):
        """_run_retrain returns reason='no_lake_dates' when date list is empty."""
        lake_dir = tmp_path / "lake"
        lake_dir.mkdir()

        # Create a partition structure that gives age >= MIN_LAKE_DAYS
        # but _lake_date_strs returns empty (mock it)
        with mock.patch("copytrade.curvestage_train._lake_date_strs", return_value=[]):
            result = _run_retrain(str(lake_dir), str(tmp_path / "model"), age=10)

        assert result["retrained"] is False
        assert result["reason"] == "no_lake_dates"
        assert result["n_train"] == 0


# ---------------------------------------------------------------------------
# _run_retrain — not_enough_samples path
# ---------------------------------------------------------------------------

class TestRunRetrainNotEnoughSamples:
    def test_not_enough_samples_returns_no_op(self, tmp_path):
        """_run_retrain returns reason='not_enough_samples' when < MIN_TRAIN_SAMPLES."""
        # Return fewer records than MIN_TRAIN_SAMPLES
        sparse_records = [
            {"feats": {"tok_age_s": float(i), "curve_frac": 0.3}, "graduated": i % 5 == 0}
            for i in range(MIN_TRAIN_SAMPLES - 1)
        ]

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20", "2026-06-21"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=sparse_records),
        ):
            result = _run_retrain(str(tmp_path / "lake"), str(tmp_path / "model"), age=10)

        assert result["retrained"] is False
        assert result["reason"] == "not_enough_samples"
        assert result["n_train"] == len(sparse_records)

    def test_not_enough_samples_n_train_is_record_count(self, tmp_path):
        """n_train in the no-op result = actual record count."""
        n = 50  # < MIN_TRAIN_SAMPLES=200
        records = [
            {"feats": {"tok_age_s": float(i), "curve_frac": 0.2}, "graduated": False}
            for i in range(n)
        ]

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
        ):
            result = _run_retrain(str(tmp_path / "lake"), str(tmp_path / "model"), age=8)

        assert result["n_train"] == n
        assert result["n_held_out"] == 0


# ---------------------------------------------------------------------------
# _run_retrain — X/y building and split (mock lgb + file I/O)
# ---------------------------------------------------------------------------

class TestRunRetrainXyBuilding:
    def _make_model_dir(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        meta = {
            "pgrad_threshold_frozen": 0.153,
            "features": ["tok_age_s", "curve_frac", "pre_sol_in"],
        }
        (model_dir / "pgrad_meta.json").write_text(json.dumps(meta))
        # Create a placeholder model file
        (model_dir / "pgrad_lgbm.txt").write_text("placeholder")
        return str(model_dir)

    def _make_records(self, n=300):
        """Synthetic feature records with known labels."""
        rng = np.random.default_rng(42)
        records = []
        for i in range(n):
            records.append({
                "feats": {
                    "tok_age_s": float(rng.uniform(10, 200)),
                    "curve_frac": float(rng.uniform(0.05, 0.55)),
                    "pre_sol_in": float(rng.uniform(5, 80)),
                },
                "graduated": bool(rng.random() > 0.8),
                "date_str": "2026-06-20",
            })
        return records

    def _make_lgb_mock(self, n_heldout, captured=None):
        """Build a mock lgb module with LGBMClassifier that captures fit args."""
        mock_lgb = mock.MagicMock()
        mock_m = mock.MagicMock()

        if captured is not None:
            def mock_fit(X_train, y_train):
                captured["X_shape"] = X_train.shape
                captured["y_shape"] = y_train.shape
            mock_m.fit.side_effect = mock_fit

        mock_m.predict_proba.return_value = np.random.default_rng(7).random((n_heldout, 2))
        mock_m.booster_ = mock.MagicMock()
        mock_m.booster_.save_model = mock.MagicMock()
        mock_lgb.LGBMClassifier.return_value = mock_m
        return mock_lgb, mock_m

    def test_retrain_builds_xy_arrays_correct_shape(self, tmp_path):
        """X/y arrays have correct shape from records."""
        model_dir = self._make_model_dir(tmp_path)
        records = self._make_records(300)
        n = len(records)
        n_train = int(n * 0.8)
        n_heldout = n - n_train

        captured = {}
        mock_lgb, mock_m = self._make_lgb_mock(n_heldout, captured=captured)

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
            mock.patch.dict(sys.modules, {"lightgbm": mock_lgb}),
            mock.patch("copytrade.curvestage_train._hotswap_singleton"),
        ):
            _run_retrain(str(tmp_path / "lake"), model_dir, age=10)

        assert captured.get("X_shape") == (n_train, 3)  # 3 features in meta
        assert captured.get("y_shape") == (n_train,)

    def test_retrain_split_is_80_20(self, tmp_path):
        """n_train = int(n * 0.8), n_heldout = n - n_train."""
        model_dir = self._make_model_dir(tmp_path)
        n = 250
        records = self._make_records(n)

        n_train_expected = int(n * 0.8)
        n_heldout_expected = n - n_train_expected

        mock_lgb, mock_m = self._make_lgb_mock(n_heldout_expected)

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
            mock.patch.dict(sys.modules, {"lightgbm": mock_lgb}),
            mock.patch("copytrade.curvestage_train._hotswap_singleton"),
        ):
            result = _run_retrain(str(tmp_path / "lake"), model_dir, age=10)

        assert result["n_train"] == n_train_expected
        assert result["n_held_out"] == n_heldout_expected

    def test_retrain_threshold_from_held_out_p75(self, tmp_path):
        """recalibrated_threshold = 75th percentile of held-out fold scores."""
        model_dir = self._make_model_dir(tmp_path)
        n = 250
        records = self._make_records(n)
        n_train = int(n * 0.8)
        n_heldout = n - n_train

        # Use deterministic held-out scores
        rng = np.random.default_rng(99)
        held_out_scores_col1 = rng.uniform(0.01, 0.99, size=n_heldout)
        held_out_proba = np.column_stack([1 - held_out_scores_col1, held_out_scores_col1])

        mock_lgb, mock_m = self._make_lgb_mock(n_heldout)
        mock_m.predict_proba.return_value = held_out_proba

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
            mock.patch.dict(sys.modules, {"lightgbm": mock_lgb}),
            mock.patch("copytrade.curvestage_train._hotswap_singleton"),
        ):
            result = _run_retrain(str(tmp_path / "lake"), model_dir, age=10)

        # Expected threshold: p75 of held_out_scores_col1 = percentile(scores, 75)
        expected_threshold = float(np.percentile(held_out_scores_col1, 100 - SELECT_DEPTH_PCT))
        assert result["recalibrated_threshold"] == pytest.approx(expected_threshold, rel=1e-5)

    def test_retrain_returns_ok_status(self, tmp_path):
        """Successful retrain returns reason='ok' and retrained=True."""
        model_dir = self._make_model_dir(tmp_path)
        records = self._make_records(300)
        n_heldout = 300 - int(300 * 0.8)

        mock_lgb, mock_m = self._make_lgb_mock(n_heldout)

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
            mock.patch.dict(sys.modules, {"lightgbm": mock_lgb}),
            mock.patch("copytrade.curvestage_train._hotswap_singleton"),
        ):
            result = _run_retrain(str(tmp_path / "lake"), model_dir, age=10)

        assert result["retrained"] is True
        assert result["reason"] == "ok"

    def test_retrain_writes_meta_json(self, tmp_path):
        """Successful retrain writes recalibrated threshold to pgrad_meta.json."""
        model_dir = self._make_model_dir(tmp_path)
        records = self._make_records(300)
        n_heldout = 300 - int(300 * 0.8)

        mock_lgb, mock_m = self._make_lgb_mock(n_heldout)

        with (
            mock.patch("copytrade.curvestage_train._lake_date_strs",
                       return_value=["2026-06-20"]),
            mock.patch("copytrade.pgrad_calibration._collect_live_features",
                       return_value=records),
            mock.patch.dict(sys.modules, {"lightgbm": mock_lgb}),
            mock.patch("copytrade.curvestage_train._hotswap_singleton"),
        ):
            result = _run_retrain(str(tmp_path / "lake"), model_dir, age=10)

        meta = json.loads((Path(model_dir) / "pgrad_meta.json").read_text())
        assert "pgrad_threshold_recalibrated" in meta
        assert meta["pgrad_threshold_recalibrated"] == pytest.approx(
            result["recalibrated_threshold"], rel=1e-5
        )


# ---------------------------------------------------------------------------
# _hotswap_singleton — success and exception paths
# ---------------------------------------------------------------------------

class TestHotswapSingleton:
    def test_hotswap_clears_singleton(self):
        """_hotswap_singleton sets _singleton to None on the real pgrad_classifier module."""
        import copytrade.pgrad_classifier as real_clf_mod

        original_singleton = real_clf_mod._singleton
        try:
            real_clf_mod._singleton = "test_old_singleton"
            _hotswap_singleton("/some/model/dir")
            assert real_clf_mod._singleton is None
        finally:
            # Restore original state
            real_clf_mod._singleton = original_singleton

    def test_hotswap_exception_is_non_fatal(self):
        """_hotswap_singleton logs warning and returns normally when PgradClassifier raises."""
        import copytrade.pgrad_classifier as real_clf_mod

        # Patch the _lock attribute to raise an exception
        with mock.patch.object(real_clf_mod.PgradClassifier, "_lock",
                                new_callable=lambda: type("BadLock", (), {
                                    "__enter__": lambda s: (_ for _ in ()).throw(RuntimeError("lock error")),
                                    "__exit__": lambda s, *a: None,
                                })):
            # Should not raise, just log a warning
            _hotswap_singleton("/some/model/dir")

    def test_hotswap_logs_warning_on_exception(self):
        """_hotswap_singleton logs a warning when an exception occurs."""
        import copytrade.pgrad_classifier as real_clf_mod

        with (
            mock.patch.object(
                real_clf_mod.PgradClassifier, "_lock",
                new_callable=lambda: type("FailLock", (), {
                    "__enter__": staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
                    "__exit__": staticmethod(lambda *a: None),
                }),
            ),
            mock.patch("copytrade.curvestage_train.logger") as mock_log,
        ):
            _hotswap_singleton("/some/model/dir")

        mock_log.warning.assert_called()
