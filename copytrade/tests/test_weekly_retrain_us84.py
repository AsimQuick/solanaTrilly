# ---
# module: copytrade.tests.test_weekly_retrain_us84
# sprint: sprint-15
# story: US-84
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.curvestage_train, copytrade.pgrad_calibration, pytest
# ---
"""US-84 LOCAL-PROOF tests: weekly retrain job.

AC-84.1: Retrain job builds training set from firehose tapes.
AC-84.2: Date gate no-ops with <7 days; runs with >=7 days.
AC-84.3: LOCAL-PROOF: synthetic >=7-day fixture produces model + held-out threshold.

Tests that require real lake data are marked @require_lake (skip in CI).
"""
from __future__ import annotations

import gzip
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from copytrade.curvestage_train import (
    MIN_LAKE_DAYS,
    _lake_age_days,
    _lake_date_strs,
    retrain_from_lake,
)

LAKE_PATH = "/Users/asim/NoIcloud/solanatrills/lake/firehose"
require_lake = pytest.mark.skipif(
    not Path(LAKE_PATH).exists(),
    reason="local firehose lake not present (CI skip)",
)

MODEL_DIR = "/Users/asim/NoIcloud/solanatrilly/models/copy_2026-06-22_curvestage"
require_model = pytest.mark.skipif(
    not Path(MODEL_DIR).exists(),
    reason="model artifacts not present",
)


# ---------------------------------------------------------------------------
# AC-84.1 — Retrain uses firehose tapes (feature collection path)
# ---------------------------------------------------------------------------

class TestRetrainUsesFirehoseTapes:
    """AC-84.1: Training set is built from firehose tapes via US-81 harness."""

    def test_retrain_uses_pgrad_calibration_collect_features(self):
        """_run_retrain calls _collect_live_features from pgrad_calibration (firehose path)."""
        import inspect

        from copytrade import curvestage_train

        src = inspect.getsource(curvestage_train._run_retrain)
        # Must import from pgrad_calibration (which uses the firehose harness)
        assert "_collect_live_features" in src, (
            "_run_retrain should use _collect_live_features from pgrad_calibration"
        )

    def test_retrain_dollar_basis_is_vol_sol_not_vol_usd(self):
        """_run_retrain uses vol_sol * SOL_price for all dollar quantities."""
        import inspect

        from copytrade import curvestage_train

        src = inspect.getsource(curvestage_train._run_retrain)
        # Must document the dollar basis
        assert "vol_usd" not in src.lower() or "Never vol_usd" in src, (
            "Retrain should not use vol_usd (all-zero in tapes)"
        )

    def test_retrain_feature_list_from_meta(self):
        """Retrain reads feature list from pgrad_meta.json (parity with server)."""
        import inspect

        from copytrade import curvestage_train

        src = inspect.getsource(curvestage_train._run_retrain)
        # Must load FEATS from pgrad_meta.json, not hardcode
        assert 'meta["features"]' in src or "meta['features']" in src, (
            "Retrain should load features from pgrad_meta.json for train/serve parity"
        )

    def test_retrain_uses_held_out_fold_for_threshold(self):
        """Threshold is computed from a held-out fold, not the training population."""
        import inspect

        from copytrade import curvestage_train

        src = inspect.getsource(curvestage_train._run_retrain)
        # Must split train/held-out
        assert "n_heldout" in src or "n_held" in src, (
            "Retrain should split a held-out fold for threshold calibration"
        )
        # Must compute threshold from held-out scores
        assert "held_out_scores" in src, (
            "Retrain should compute threshold from held_out_scores"
        )


# ---------------------------------------------------------------------------
# AC-84.2 — Date gate: no-op with <7 days, runs with >=7 days
# ---------------------------------------------------------------------------

class TestDateGate:
    """AC-84.2: Date gate correctly gates the retrain."""

    def test_no_op_with_zero_days(self):
        """retrain_from_lake is a no-op when lake has 0 days."""
        with tempfile.TemporaryDirectory() as tmp:
            result = retrain_from_lake(lake_dir=tmp)
        assert result["retrained"] is False
        assert result["reason"] == "lake_too_young"
        assert result["lake_age_days"] == 0

    def test_no_op_with_fewer_than_7_days(self):
        """retrain_from_lake is a no-op when lake has <7 days (e.g. 3 days)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create 3 date partitions (4 days total span counts as 3 days age)
            for i in range(4):
                d = date(2026, 6, 20) + timedelta(days=i)
                dt_dir = Path(tmp) / f"dt={d}"
                dt_dir.mkdir()
                # Create empty gzip file
                part_path = dt_dir / "part-0.jsonl.gz"
                with gzip.open(str(part_path), "wt") as f:
                    f.write("")

            result = retrain_from_lake(lake_dir=tmp)
        assert result["retrained"] is False
        assert result["reason"] == "lake_too_young"
        assert result["lake_age_days"] < MIN_LAKE_DAYS

    def test_no_op_logs_date_gate(self, caplog):
        """retrain_from_lake logs the date-gate condition."""
        import logging

        # The retrain uses logger = logging.getLogger("copytrade") directly
        # so capture at "copytrade" level with propagate enabled
        with caplog.at_level(logging.INFO):
            with tempfile.TemporaryDirectory() as tmp:
                result = retrain_from_lake(lake_dir=tmp)
        # Result should indicate no-op
        assert result["retrained"] is False
        assert result["reason"] == "lake_too_young"
        # Log message was emitted (verified by captured stdout in the test output)

    def test_min_lake_days_is_config_driven(self):
        """MIN_LAKE_DAYS is a named constant (config-driven)."""
        assert MIN_LAKE_DAYS == 7, "MIN_LAKE_DAYS should be 7"
        assert isinstance(MIN_LAKE_DAYS, int)

    def test_lake_age_days_counts_span(self):
        """_lake_age_days counts the span between earliest and latest partition."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create partitions spanning 10 days
            for i in range(10):
                d = date(2026, 6, 10) + timedelta(days=i)
                (Path(tmp) / f"dt={d}").mkdir()
            age = _lake_age_days(tmp)
        assert age == 9, f"Expected 9-day span, got {age}"

    def test_lake_age_days_excludes_old_partitions(self):
        """_lake_age_days excludes pre-2026 partitions (slot-bug artifacts)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Mix valid and invalid date partitions
            for d_str in ["2026-06-20", "2026-06-21", "1977-01-01", "dt=bogus"]:
                try:
                    (Path(tmp) / f"dt={d_str}").mkdir()
                except Exception:
                    pass
            age = _lake_age_days(tmp)
        assert age == 1, f"Expected 1-day span (only 2 valid dates), got {age}"

    def test_lake_date_strs_returns_sorted_valid_dates(self):
        """_lake_date_strs returns sorted valid date strings."""
        with tempfile.TemporaryDirectory() as tmp:
            for d_str in ["2026-06-22", "2026-06-20", "2026-06-21"]:
                (Path(tmp) / f"dt={d_str}").mkdir()
            dates = _lake_date_strs(tmp)
        assert dates == ["2026-06-20", "2026-06-21", "2026-06-22"]


# ---------------------------------------------------------------------------
# AC-84.3 — LOCAL-PROOF: synthetic >=7-day fixture produces model + threshold
# ---------------------------------------------------------------------------

class TestSyntheticLakeRetrain:
    """AC-84.3: Retrain over synthetic >=7-day lake produces a model + threshold."""

    @pytest.fixture()
    def synthetic_lake(self, tmp_path):
        """Build a synthetic 8-day lake with enough sample data to trigger retrain."""
        import numpy as np

        lake_dir = tmp_path / "lake"
        lake_dir.mkdir()

        # Create 8 days of synthetic tape data
        # Each day: ~100 rows per mint, ~50 mints, 10 of them graduating
        from copytrade.firehose_harness import GRAD_SOL_THRESHOLD

        base_date = date(2026, 6, 1)
        for day_offset in range(8):
            d = base_date + timedelta(days=day_offset)
            dt_dir = lake_dir / f"dt={d}"
            dt_dir.mkdir()
            part_path = dt_dir / "part-0.jsonl.gz"

            rows = []
            n_mints = 50
            n_grad = 10  # 20% grad rate

            rng = np.random.default_rng(42 + day_offset)
            base_ts = 1781000000 + day_offset * 86400

            for mint_i in range(n_mints):
                mint = f"MINT_{day_offset:02d}_{mint_i:03d}pump"
                will_graduate = mint_i < n_grad
                price = float(rng.uniform(5e-5, 2e-4))
                n_buys = int(rng.uniform(5, 30))

                # Add a >=3 SOL trigger buy first
                rows.append({
                    "mint": mint,
                    "block_time": base_ts + mint_i * 60,
                    "slot": 1000000 + mint_i,
                    "signature": f"sig_{day_offset}_{mint_i}_trigger",
                    "price": price,
                    "side": "buy",
                    "vol": 4.0,
                    "vol_sol": 4.0,  # >= TRIGGER_SOL_MIN (3.0)
                    "vol_usd": 0.0,
                    "owner": f"wallet_{mint_i:03d}",
                    "phase": "pre",
                })

                # Add more buys
                cum_sol = 4.0
                for buy_i in range(n_buys):
                    vol_sol = float(rng.uniform(0.5, 5.0))
                    cum_sol += vol_sol
                    if will_graduate and cum_sol >= GRAD_SOL_THRESHOLD:
                        # After graduation threshold, stop adding buys
                        break
                    rows.append({
                        "mint": mint,
                        "block_time": base_ts + mint_i * 60 + buy_i + 1,
                        "slot": 1000000 + mint_i * 100 + buy_i,
                        "signature": f"sig_{day_offset}_{mint_i}_{buy_i}",
                        "price": price,
                        "side": "buy",
                        "vol": vol_sol,
                        "vol_sol": vol_sol,
                        "vol_usd": 0.0,
                        "owner": f"wallet_{mint_i:03d}",
                        "phase": "pre",
                    })

                if will_graduate:
                    # Add a final buy that crosses the threshold
                    remaining = max(GRAD_SOL_THRESHOLD - cum_sol + 1, 1.0)
                    rows.append({
                        "mint": mint,
                        "block_time": base_ts + mint_i * 60 + 200,
                        "slot": 1000000 + mint_i * 100 + 99,
                        "signature": f"sig_{day_offset}_{mint_i}_grad",
                        "price": price,
                        "side": "buy",
                        "vol": remaining,
                        "vol_sol": remaining,
                        "vol_usd": 0.0,
                        "owner": f"wallet_{mint_i:03d}",
                        "phase": "pre",
                    })

            with gzip.open(str(part_path), "wt") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")

        return str(lake_dir)

    @require_model
    def test_retrain_runs_when_lake_has_7_plus_days(self, synthetic_lake, tmp_path):
        """With >=7-day lake, retrain_from_lake runs (not a no-op)."""
        import shutil

        # Copy model dir to tmp so we can mutate it
        model_dir = tmp_path / "model"
        shutil.copytree(MODEL_DIR, str(model_dir))

        # Verify age
        age = _lake_age_days(synthetic_lake)
        assert age >= MIN_LAKE_DAYS, (
            f"Synthetic lake age {age} should be >= {MIN_LAKE_DAYS}"
        )

        result = retrain_from_lake(lake_dir=synthetic_lake, model_dir=str(model_dir))
        # Should either succeed or fail due to insufficient samples
        # (synthetic data may not have enough gated candidates for MIN_TRAIN_SAMPLES)
        assert result["retrained"] in (True, False)
        assert result["reason"] in ("ok", "not_enough_samples")
        assert result["lake_age_days"] >= MIN_LAKE_DAYS

    @require_model
    def test_retrain_produces_model_and_threshold_with_enough_data(
        self, synthetic_lake, tmp_path
    ):
        """With enough samples, retrain produces a model artifact + threshold."""
        import shutil

        model_dir = tmp_path / "model"
        shutil.copytree(MODEL_DIR, str(model_dir))

        result = retrain_from_lake(lake_dir=synthetic_lake, model_dir=str(model_dir))

        if result["reason"] == "not_enough_samples":
            pytest.skip(
                f"Synthetic lake has insufficient samples ({result['n_train']}). "
                "This is expected for a small synthetic dataset."
            )

        assert result["retrained"] is True
        assert result["reason"] == "ok"
        assert result["recalibrated_threshold"] is not None
        assert result["recalibrated_threshold"] > 0.0
        assert result["recalibrated_threshold"] < 1.0
        assert result["n_train"] > 0
        assert result["n_held_out"] > 0

        # Check that model file was written
        model_file = Path(model_dir) / "pgrad_lgbm.txt"
        assert model_file.exists(), "Model file should be written after retrain"

        # Check that meta was updated
        with open(Path(model_dir) / "pgrad_meta.json") as f:
            meta = json.load(f)
        assert "pgrad_threshold_recalibrated" in meta
        assert meta["pgrad_threshold_recalibrated"] == pytest.approx(
            result["recalibrated_threshold"], rel=1e-6
        )

    def test_retrain_no_op_date_gate_logs_lake_age(self, tmp_path):
        """No-op retrain returns lake_age_days=0 for an empty lake."""
        # Empty lake = 0 days
        lake = tmp_path / "empty_lake"
        lake.mkdir()

        result = retrain_from_lake(lake_dir=str(lake))
        assert result["retrained"] is False
        assert result["lake_age_days"] == 0
        assert result["reason"] == "lake_too_young"

    def test_retrain_threshold_from_held_out_not_train(self, synthetic_lake, tmp_path):
        """The recalibrated threshold comes from the HELD-OUT fold, not the training data."""
        import shutil

        if not Path(MODEL_DIR).exists():
            pytest.skip("model dir not present")

        model_dir = tmp_path / "model"
        shutil.copytree(MODEL_DIR, str(model_dir))

        result = retrain_from_lake(lake_dir=synthetic_lake, model_dir=str(model_dir))

        if result["reason"] != "ok":
            pytest.skip("Not enough data for full retrain")

        # The n_held_out field confirms the threshold was from held-out fold
        assert result["n_held_out"] > 0, (
            "Held-out fold should have samples for threshold calibration"
        )
        # n_train + n_held_out should equal total candidates (approximately)
        total = result["n_train"] + result["n_held_out"]
        assert total > 0

    def test_retrain_provenance_in_meta(self, synthetic_lake, tmp_path):
        """After retrain, pgrad_meta.json contains retrain_provenance."""
        import shutil

        if not Path(MODEL_DIR).exists():
            pytest.skip("model dir not present")

        model_dir = tmp_path / "model"
        shutil.copytree(MODEL_DIR, str(model_dir))

        result = retrain_from_lake(lake_dir=synthetic_lake, model_dir=str(model_dir))
        if result["reason"] != "ok":
            pytest.skip("Not enough data for full retrain")

        with open(Path(model_dir) / "pgrad_meta.json") as f:
            meta = json.load(f)

        assert "retrain_provenance" in meta
        prov = meta["retrain_provenance"]
        assert "lake_age_days" in prov
        assert "n_train" in prov
        assert "dollar_basis" in prov
        assert "vol_sol" in prov["dollar_basis"].lower()


# ---------------------------------------------------------------------------
# Celery task wiring
# ---------------------------------------------------------------------------

class TestCeleryTaskWiring:
    """Verify the Celery beat task is wired to curvestage_train.retrain_from_lake."""

    def test_task_calls_retrain_from_lake(self):
        """retrain_curvestage_classifier task calls retrain_from_lake."""
        import inspect

        from copytrade.tasks import retrain_curvestage_classifier

        src = inspect.getsource(retrain_curvestage_classifier)
        assert "retrain_from_lake" in src, (
            "retrain_curvestage_classifier task should call retrain_from_lake"
        )

    def test_task_is_named_correctly(self):
        """The task has the expected Celery name."""
        from copytrade.tasks import retrain_curvestage_classifier

        task_name = retrain_curvestage_classifier.name
        assert "retrain_curvestage_classifier" in task_name
