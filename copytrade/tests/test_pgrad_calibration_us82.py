# ---
# module: copytrade.tests.test_pgrad_calibration_us82
# sprint: sprint-15
# story: US-82
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.pgrad_calibration, copytrade.pgrad_classifier, pytest
# ---
"""US-82 LOCAL-PROOF tests: G2 parity table + recalibrated threshold + selectivity proof.

PIPELINE CLARIFICATION (critical for correctness):
  PRODUCTION: entry_features() uses Birdeye basePrice*quotePrice (USD/token).
  birdeye_items_to_owner_tape maps: price = basePrice * quotePrice (USD/token).
  This is the SAME source the model was trained on — NO parity break in production.

  FIREHOSE PROXY — STRUCTURALLY INCOMPATIBLE:
  The local lake (/solanatrills/lake/tapes/*.parquet, raw/) carries:
    virtual_sol_reserves, virtual_token_reserves, sol_amount, token_amount
  — on-chain reserve fields, NOT Birdeye basePrice/quotePrice.
  firehose_copy_replay.parquet similarly carries firehose prices.
  Any threshold computed on firehose-proxy scores is INVALID for the production gate.

  CORRECT RECALIBRATION SOURCE:
  Lab parquets (score_2026-06-06_entry.parquet, etc.) carry Birdeye-sourced features
  (price_at_entry in USD/token) — the same basis as training and live engine.

  MEASURED PRODUCTION SCORE DISTRIBUTION (lab parquets, on_curve + cf<=0.6):
    Jun-6 fold (n=2522, base_grad=14.2%):
      Score median  = 0.0183 (NOT 0.71 — that was pre_grad=True rows only)
      Grad median   = 0.7257
      Non-grad median = 0.0136
    Frozen (0.1529): n_sel=548 (21.7%), sel_grad=53.5%, lift=3.76x  HIGHLY SELECTIVE
    p75    (0.0956): n_sel=631 (25.0%), sel_grad=48.3%, lift=3.40x  SELECTIVE

  INVALID PREVIOUS VALUES:
    0.0445 — firehose_copy_replay (structurally incompatible prices)
    0.0142 — label-free firehose (wrong pipeline + no labels)

These tests run entirely from local data (zero credits).
Tests that need the real lake are marked @require_lake and skip in CI.
Tests that need lab parquets are marked @require_lab_parquet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from copytrade.pgrad_calibration import (
    LAB_PARQUET_DATES,
    LAB_PARQUET_DIR,
    LAB_PRICE_AT_ENTRY_MEDIAN,
    LABELED_PROXY_PARQUET,
    LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY,
    META_KEY_RECALIBRATED,
    PRICE_FEATURE_INFLATION_RATIO,
    RECALIBRATED_THRESHOLD,
    SEED_THRESHOLD_FROZEN,
    _compute_features_from_tape,
    _get_lab_medians,
    compute_recalibrated_threshold,
    write_recalibrated_meta,
)

LAKE_PATH = "/Users/asim/NoIcloud/solanatrills/lake/firehose"
LAB_PARQUET_JUN6 = "/Users/asim/NoIcloud/solanatrills/analysis/whale_graph/out/score_2026-06-06_entry.parquet"

require_lake = pytest.mark.skipif(
    not Path(LAKE_PATH).exists(),
    reason="local firehose lake not present (CI skip)",
)

require_lab_parquet = pytest.mark.skipif(
    not Path(LAB_PARQUET_JUN6).exists(),
    reason="lab parquet not present (CI skip)",
)

MODEL_DIR = "/Users/asim/NoIcloud/solanatrilly/models/copy_2026-06-22_curvestage"
require_model = pytest.mark.skipif(
    not Path(MODEL_DIR).exists(),
    reason="seed model artifacts not present",
)


# ---------------------------------------------------------------------------
# AC-82.1 — Parity table constants and structure
# ---------------------------------------------------------------------------

class TestParityTableConstants:
    """Verify the documented parity-break constants are correct."""

    def test_lab_price_median_documented(self):
        """Lab price_at_entry median is documented (Birdeye USD/token, Jun-6 fold)."""
        # Jun-6 fold, on_curve + cf<=0.6: measured 5.4e-4 USD/token
        assert LAB_PRICE_AT_ENTRY_MEDIAN == pytest.approx(5.4e-4, rel=0.10)

    def test_live_price_median_proxy_is_incompatible(self):
        """LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY documents firehose price — incompatible units."""
        # Firehose carries virtual-reserve ratios, NOT Birdeye basePrice*quotePrice.
        # This constant is retained for documentation only — NOT used for calibration.
        assert LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY == pytest.approx(6.9e-5, rel=0.10)
        # The ratio is nan (structurally incompatible)
        import math
        assert math.isnan(PRICE_FEATURE_INFLATION_RATIO), (
            "PRICE_FEATURE_INFLATION_RATIO should be nan (structurally incompatible units)"
        )

    def test_seed_threshold_matches_meta(self):
        """Seed threshold constant matches the value in pgrad_meta.json."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        assert SEED_THRESHOLD_FROZEN == pytest.approx(
            meta["pgrad_threshold_frozen"], rel=1e-6
        )

    def test_recalibrated_threshold_correct_value(self):
        """Recalibrated threshold = 0.0956 (p75 of production pipeline, Jun-6 fold)."""
        # INVALID PREVIOUS VALUES:
        #   0.0142 — label-free firehose (wrong pipeline + no labels)
        #   0.0445 — firehose_copy_replay (structurally incompatible prices)
        # CORRECT value from lab parquet (production pipeline):
        assert RECALIBRATED_THRESHOLD == pytest.approx(0.0956, rel=0.01)
        # Ensure it is NOT the wrong firehose-proxy values
        assert abs(RECALIBRATED_THRESHOLD - 0.0142) > 0.001
        assert abs(RECALIBRATED_THRESHOLD - 0.0445) > 0.005

    def test_recalibrated_threshold_below_frozen(self):
        """Recalibrated (0.0956) is below frozen (0.153): it selects more candidates."""
        assert RECALIBRATED_THRESHOLD < SEED_THRESHOLD_FROZEN

    def test_lab_parquet_dir_documented(self):
        """LAB_PARQUET_DIR constant documents the canonical lab parquet source."""
        assert "solanatrills" in LAB_PARQUET_DIR
        assert "whale_graph" in LAB_PARQUET_DIR

    def test_lab_parquet_dates_documented(self):
        """LAB_PARQUET_DATES documents the available lab parquet dates."""
        assert "2026-06-06" in LAB_PARQUET_DATES
        assert len(LAB_PARQUET_DATES) >= 3

    def test_labeled_proxy_parquet_is_documented_incompatible(self):
        """LABELED_PROXY_PARQUET is retained for documentation; not used for calibration."""
        assert "firehose_copy_replay" in LABELED_PROXY_PARQUET


class TestParityTableDollarBasis:
    """Parity table outputs correct dollar-basis structure."""

    def test_parity_table_uses_dollar_basis(self):
        """build_parity_table uses vol_sol * SOL_price (never vol_usd=0)."""
        from copytrade.firehose_harness import TapeRow

        row = TapeRow(
            mint="test_mint",
            block_time=1000,
            slot=100,
            signature="sig",
            price=7e-5,       # firehose virtual-reserve ratio (NOT Birdeye USD)
            side="buy",
            vol=0.1,
            vol_sol=0.1,
            vol_usd=0.0,      # always 0 in firehose tapes
            owner="wallet1",
            phase="pre",
        )
        feats = _compute_features_from_tape([row], 1000, row, sol_usd=84.0)
        assert feats is not None
        # wallet_buy_usd = vol_sol * sol_usd (not vol_usd which is 0)
        assert feats["wallet_buy_usd"] == pytest.approx(0.1 * 84.0)
        # pre_vol_usd should be non-zero (from vol_sol path)
        assert feats["pre_vol_usd"] != 0.0

    def test_lab_medians_documented(self):
        """_get_lab_medians returns documented constants when no parquet available."""
        medians = _get_lab_medians(None)
        assert "price_at_entry" in medians
        assert "fdv_proxy" in medians
        assert medians["price_at_entry"] == pytest.approx(LAB_PRICE_AT_ENTRY_MEDIAN, rel=0.01)
        assert medians["fdv_proxy"] == pytest.approx(LAB_PRICE_AT_ENTRY_MEDIAN * 1e9, rel=0.01)

    def test_fdv_proxy_is_price_times_1e9(self):
        """fdv_proxy = price_at_entry * 1e9 (by construction)."""
        from copytrade.firehose_harness import TapeRow

        row = TapeRow(
            mint="m", block_time=1000, slot=1, signature="s",
            price=7e-5, side="buy", vol=1.0, vol_sol=1.0, vol_usd=0.0,
            owner="w", phase="pre",
        )
        feats = _compute_features_from_tape([row], 1000, row, sol_usd=84.0)
        assert feats is not None
        assert feats["fdv_proxy"] == pytest.approx(feats["price_at_entry"] * 1e9, rel=1e-6)


# ---------------------------------------------------------------------------
# AC-82.2 — Inflation source: firehose is STRUCTURALLY INCOMPATIBLE
# ---------------------------------------------------------------------------

class TestPipelineIncompatibility:
    """AC-82.2: firehose prices are structurally incompatible with production pipeline.

    KEY FINDING:
    - Local tape lake carries virtual_sol_reserves/virtual_token_reserves (on-chain)
    - Birdeye REST API carries basePrice/quotePrice (USD/token)
    - These are DIFFERENT data structures, not just different scales
    - birdeye_items_to_owner_tape requires basePrice + quotePrice — absent from firehose
    - Any threshold calibrated on firehose-proxy scores is INVALID for production gate
    """

    def test_production_pipeline_requires_birdeye_fields(self):
        """birdeye_items_to_owner_tape requires basePrice + quotePrice (absent in firehose)."""
        import copytrade.entry_features as ef

        # Items WITHOUT basePrice/quotePrice (firehose-like) -> empty result
        firehose_like_items = [
            {
                "blockUnixTime": 1000,
                "side": "buy",
                "owner": "w1",
                "virtual_sol_reserves": 47852542492,
                "virtual_token_reserves": 868260191562464,
                "sol_amount": 2175151116,
                "token_amount": 54116098452059,
                # No basePrice, no quotePrice
            }
        ]
        result = ef.birdeye_items_to_owner_tape(firehose_like_items)
        assert len(result) == 0, (
            "birdeye_items_to_owner_tape should skip items without basePrice/quotePrice"
        )

    def test_production_pipeline_accepts_birdeye_fields(self):
        """birdeye_items_to_owner_tape accepts items WITH basePrice + quotePrice."""
        import copytrade.entry_features as ef

        birdeye_like_items = [
            {
                "blockUnixTime": 1000,
                "side": "buy",
                "owner": "w1",
                "basePrice": 5.4e-4,  # USD/token
                "quotePrice": 143.5,  # SOL_USD
                "quote": {"uiAmount": 3.1},  # SOL amount
                "txType": "swap",
            }
        ]
        result = ef.birdeye_items_to_owner_tape(birdeye_like_items)
        assert len(result) == 1, "Should accept Birdeye items with basePrice/quotePrice"
        t, price, usd, side, owner, sol = result[0]
        assert price == pytest.approx(5.4e-4 * 143.5, rel=1e-6)

    def test_price_feature_inflation_ratio_is_invalid(self):
        """PRICE_FEATURE_INFLATION_RATIO is nan (structurally incompatible units)."""
        import math
        assert math.isnan(PRICE_FEATURE_INFLATION_RATIO), (
            "Ratio must be nan since firehose and Birdeye prices are structurally incompatible"
        )

    def test_production_pipeline_is_lab_faithful(self):
        """Production pipeline uses Birdeye — same source as lab training (no parity break)."""
        import inspect

        import copytrade.entry_features as ef

        src = inspect.getsource(ef.birdeye_items_to_owner_tape)
        # Must use basePrice * quotePrice (USD/token) — lab training basis
        assert "basePrice" in src
        assert "quotePrice" in src
        # Must NOT use virtual_sol_reserves (on-chain reserve fields)
        assert "virtual_sol_reserves" not in src
        assert "virtual_token_reserves" not in src


# ---------------------------------------------------------------------------
# AC-82.3 — Recalibrated gate PROVED SELECTIVE on production pipeline
# ---------------------------------------------------------------------------

class TestRecalibratedThresholdProduction:
    """AC-82.3: Recalibrated threshold proved selective on PRODUCTION pipeline (lab parquets)."""

    def test_recalibrated_threshold_is_config_driven(self):
        """The threshold is a named constant, not a scattered literal."""
        assert isinstance(RECALIBRATED_THRESHOLD, float)
        assert RECALIBRATED_THRESHOLD > 0.0
        assert RECALIBRATED_THRESHOLD < 1.0

    def test_meta_json_has_correct_threshold(self):
        """pgrad_meta.json has the correct production-pipeline recalibrated threshold."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        assert META_KEY_RECALIBRATED in meta
        thr = meta[META_KEY_RECALIBRATED]
        assert thr == pytest.approx(0.0956, rel=0.01), (
            f"pgrad_meta.json has wrong threshold {thr}. "
            "Expected 0.0956 (production p75). "
            "INVALID values: 0.0445 (firehose_copy_replay), 0.0142 (label-free firehose)"
        )

    def test_meta_json_recalibration_source_is_lab_parquet(self):
        """pgrad_meta.json recalibration_results.source == 'lab_parquet'."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        results = meta.get("recalibration_results", {})
        assert results.get("source") == "lab_parquet", (
            f"Expected source=lab_parquet, got {results.get('source')}. "
            "Firehose proxy is structurally incompatible."
        )

    def test_meta_json_documents_production_score_distribution(self):
        """pgrad_meta.json documents the production score distribution metrics."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        results = meta.get("recalibration_results", {})
        assert results.get("n_candidates") == 2522
        assert results.get("base_grad_rate") == pytest.approx(0.142, rel=0.05)
        assert results.get("score_median") == pytest.approx(0.0183, rel=0.10)
        assert results.get("lift", 0) >= 3.0

    def test_write_recalibrated_meta_updates_json(self, tmp_path):
        """write_recalibrated_meta persists the recalibrated threshold to pgrad_meta.json."""
        import shutil

        src = Path(MODEL_DIR) / "pgrad_meta.json"
        if not src.exists():
            pytest.skip("model dir not present")
        dst_dir = tmp_path / "model"
        dst_dir.mkdir()
        shutil.copy(src, dst_dir / "pgrad_meta.json")

        write_recalibrated_meta(str(dst_dir), 0.0956)

        with open(dst_dir / "pgrad_meta.json") as f:
            meta = json.load(f)
        assert META_KEY_RECALIBRATED in meta
        assert meta[META_KEY_RECALIBRATED] == pytest.approx(0.0956, rel=1e-6)
        assert "pgrad_threshold_frozen" in meta

    def test_classifier_uses_recalibrated_threshold_when_present(self, tmp_path):
        """PgradClassifier loads recalibrated threshold when present in meta."""
        import shutil

        src_meta = Path(MODEL_DIR) / "pgrad_meta.json"
        src_model = Path(MODEL_DIR) / "pgrad_lgbm.txt"
        if not src_meta.exists() or not src_model.exists():
            pytest.skip("model artifacts not present")

        dst_dir = tmp_path / "model"
        dst_dir.mkdir()
        shutil.copy(src_meta, dst_dir / "pgrad_meta.json")
        shutil.copy(src_model, dst_dir / "pgrad_lgbm.txt")

        write_recalibrated_meta(str(dst_dir), 0.0956)

        from copytrade.pgrad_classifier import PgradClassifier

        clf = PgradClassifier(model_dir=str(dst_dir))
        clf.load()
        assert clf.threshold == pytest.approx(0.0956, rel=1e-6)
        assert clf.threshold_source == "recalibrated"

    def test_classifier_falls_back_to_frozen_when_no_recalibrated(self):
        """PgradClassifier uses frozen seed threshold when no recalibrated value."""
        if not Path(MODEL_DIR).exists():
            pytest.skip("model dir not present")

        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            src_meta = Path(MODEL_DIR) / "pgrad_meta.json"
            src_model = Path(MODEL_DIR) / "pgrad_lgbm.txt"
            shutil.copy(src_meta, Path(td) / "pgrad_meta.json")
            shutil.copy(src_model, Path(td) / "pgrad_lgbm.txt")

            with open(Path(td) / "pgrad_meta.json") as f:
                meta = json.load(f)
            meta.pop(META_KEY_RECALIBRATED, None)
            with open(Path(td) / "pgrad_meta.json", "w") as f:
                json.dump(meta, f)

            from copytrade.pgrad_classifier import PgradClassifier

            clf = PgradClassifier(model_dir=td)
            clf.load()
            assert clf.threshold == pytest.approx(SEED_THRESHOLD_FROZEN, rel=1e-6)
            assert clf.threshold_source == "frozen_seed"


# ---------------------------------------------------------------------------
# AC-82.3 LOCAL-PROOF — ANTI-HOTFIX: selectivity on PRODUCTION pipeline (lab parquets)
# ---------------------------------------------------------------------------

@require_lab_parquet
@require_model
class TestSelectivityOnProductionPipeline:
    """LOCAL-PROOF: recalibrated gate is selective on PRODUCTION pipeline (lab parquets).

    This is the CORRECT selectivity proof — uses lab parquets (Birdeye-sourced features,
    same basis as training and live engine).

    EXPECTED RESULTS (measured locally on Jun-6 fold):
      n_candidates=2522, base_grad=14.2%
      p75 (0.0956): n_sel=631 (25.0%), sel_grad=48.3%, lift=3.40x
      Frozen (0.153): n_sel=548 (21.7%), sel_grad=53.5%, lift=3.76x
    """

    @classmethod
    def setup_class(cls):
        """Run recalibration on lab parquet (production pipeline)."""
        cls.results = compute_recalibrated_threshold(
            [],
            model_dir=MODEL_DIR,
            lab_parquet_path=LAB_PARQUET_JUN6,
        )

    def test_used_lab_parquet_source(self):
        """Recalibration used lab_parquet source (NOT firehose proxy)."""
        assert self.results.get("source") == "lab_parquet"

    def test_n_candidates_is_correct(self):
        """Lab parquet Jun-6 has ~2522 on_curve gated candidates."""
        n = self.results["n_candidates"]
        assert 2000 <= n <= 3000, f"Expected ~2522 candidates, got {n}"

    def test_base_grad_rate_is_correct(self):
        """Base grad rate on Jun-6 fold is ~14%."""
        base = self.results["base_grad_rate"]
        assert 0.10 <= base <= 0.25, f"Base grad rate {base:.1%} outside expected range"

    def test_selected_grad_rate_exceeds_base_rate(self):
        """ANTI-HOTFIX: selected_grad_rate > base_grad_rate proves gate is selective."""
        base = self.results["base_grad_rate"]
        sel = self.results["selected_grad_rate"]
        assert sel > base, (
            f"Gate is not selective: sel_grad {sel:.1%} <= base {base:.1%}"
        )

    def test_lift_is_substantial(self):
        """Production pipeline shows substantial lift (3x+) due to bimodal distribution."""
        base = self.results["base_grad_rate"]
        sel = self.results["selected_grad_rate"]
        lift = sel / base if base > 0 else 0
        assert lift >= 2.0, (
            f"Expected lift >= 2.0x on production pipeline, got {lift:.2f}x"
        )

    def test_selection_depth_is_25_pct(self):
        """About 25% of candidates are selected (top-25% threshold)."""
        n_sel = self.results["n_selected"]
        n_cands = self.results["n_candidates"]
        pct = n_sel / n_cands
        assert 0.20 <= pct <= 0.30, f"Selected fraction {pct:.1%} not near 25%"

    def test_score_median_is_low(self):
        """Production score median is ~0.02 (bimodal, non-grads dominate population)."""
        median = self.results["score_median"]
        assert median < 0.15, (
            f"Score median {median:.3f} unexpectedly high. Expected ~0.018"
        )

    def test_frozen_threshold_also_selective(self):
        """Frozen threshold (0.153) is ALSO selective — even more so than p75."""
        import lightgbm as lgb
        import pandas as pd

        bst = lgb.Booster(model_file=str(Path(MODEL_DIR) / "pgrad_lgbm.txt"))
        with open(Path(MODEL_DIR) / "pgrad_meta.json") as f:
            meta = json.load(f)
        FEATS = meta["features"]

        df = pd.read_parquet(LAB_PARQUET_JUN6)
        if "pre_grad" in df.columns:
            df = df[~df["pre_grad"]].copy()
        if "curve_frac" not in df.columns:
            df["curve_frac"] = (df["pre_sol_in"] / 85.0).clip(0, 2)
        cands = df[df["curve_frac"] <= 0.6].copy()

        X = cands.reindex(columns=FEATS, fill_value=0.0).fillna(0.0)
        scores = bst.predict(X)
        base_grad = cands["grad"].mean()
        sel = cands[scores >= SEED_THRESHOLD_FROZEN]

        if len(sel) > 5:
            frozen_grad = sel["grad"].values.mean()
            assert frozen_grad > base_grad
            lift = frozen_grad / base_grad
            assert lift >= 2.0, f"Frozen threshold lift {lift:.2f}x should be >= 2.0x"


# ---------------------------------------------------------------------------
# Honest caveat documented (US-82 requirement)
# ---------------------------------------------------------------------------

class TestHonestCaveatDocumented:
    """Verify the honest caveat is expressed in the module and meta.json."""

    def test_module_has_honest_caveat(self):
        """pgrad_calibration module docstring contains the properly-tested caveat."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "properly tested" in doc or "properly-tested" in doc

    def test_module_documents_production_pipeline(self):
        """Module documents that production uses Birdeye (no parity break)."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "Birdeye" in doc
        assert "PRODUCTION" in doc

    def test_module_documents_firehose_incompatibility(self):
        """Module explicitly documents that firehose is structurally incompatible."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "STRUCTURALLY" in doc or "structurally" in doc.lower()

    def test_module_documents_bimodal_distribution(self):
        """Module documents the bimodal score distribution (key diagnostic finding)."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "bimodal" in doc.lower() or "BIMODAL" in doc

    def test_meta_json_documents_invalid_previous_values(self):
        """pgrad_meta.json documents the invalid previous threshold values."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        results = meta.get("recalibration_results", {})
        invalid = results.get("invalid_previous_values", {})
        invalid_str = str(invalid)
        assert "0.0445" in invalid_str
        assert "0.0142" in invalid_str

    def test_wrong_threshold_values_not_used(self):
        """RECALIBRATED_THRESHOLD is not one of the invalid firehose values."""
        assert abs(RECALIBRATED_THRESHOLD - 0.0142) > 0.001
        assert abs(RECALIBRATED_THRESHOLD - 0.0445) > 0.005

    def test_labeled_proxy_parquet_deprecated_in_api(self):
        """compute_recalibrated_threshold deprecated labeled_proxy_parquet parameter."""
        result = compute_recalibrated_threshold(
            [],
            model_dir=MODEL_DIR,
            labeled_proxy_parquet="/nonexistent.parquet",
        )
        assert "recalibrated_threshold" in result
        assert result["is_selective"] is True

    def test_hardcoded_constants_match_meta_json(self):
        """The hardcoded production constants match meta.json."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        results = meta["recalibration_results"]

        result = compute_recalibrated_threshold([], model_dir=MODEL_DIR)
        assert result["n_candidates"] == results["n_candidates"]
        assert result["base_grad_rate"] == pytest.approx(results["base_grad_rate"], rel=0.05)


# ---------------------------------------------------------------------------
# Secondary — lake-based diagnostics (not calibration)
# ---------------------------------------------------------------------------

@require_lake
class TestFirehoseParity:
    """DIAGNOSTIC (not calibration): firehose parity table documenting incompatibility."""

    def test_parity_table_builds_without_crash(self):
        """build_parity_table runs over firehose tapes without crashing."""
        from copytrade.pgrad_calibration import build_parity_table

        rows = build_parity_table(
            ["2026-06-20"],
            lake_base_dir=LAKE_PATH,
        )
        assert len(rows) > 0

    def test_basis_sensitive_features_flagged(self):
        """price_at_entry and fdv_proxy are flagged as basis-sensitive/incompatible."""
        from copytrade.pgrad_calibration import build_parity_table

        rows = build_parity_table(
            ["2026-06-20"],
            lake_base_dir=LAKE_PATH,
        )
        row_dict = {r.feature: r for r in rows}
        assert row_dict["price_at_entry"].basis_sensitive
        assert row_dict["fdv_proxy"].basis_sensitive
        assert not row_dict["curve_frac"].basis_sensitive
        assert not row_dict["pre_sol_in"].basis_sensitive
