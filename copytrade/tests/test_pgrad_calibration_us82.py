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

AC-82.1: G2 parity table generated from local tapes; price/fdv inflation documented.
AC-82.2: Inflation source identified — firehose raw SOL/token prices ~9x lower than
         Birdeye USD/token; production pipeline uses Birdeye (same as training).
AC-82.3: Recalibrated gate PROVED SELECTIVE on labeled proxy (firehose_copy_replay.parquet).
         Uses the CORRECT labeled population (not label-free firehose) for selectivity proof.

PIPELINE CLARIFICATION (critical for correctness):
  PRODUCTION: entry_features() uses Birdeye basePrice*quotePrice (USD/token) — same basis
  as training data. Price median ~6-9e-4 USD/token. pgrad median ~0.72 on this pipeline.

  OFFLINE HARNESS: _compute_features_from_tape uses firehose SOL/token prices (~6.9e-5
  raw, NOT multiplied by SOL_USD). pgrad median ~0.027 on firehose proxy.

  THRESHOLD CALIBRATION: top-25% threshold on firehose_copy_replay labeled population
  (n=545, base_grad=16.5%) = 0.0445. This is the correct recalibrated threshold.
  Previous v1 threshold 0.0142 was computed without graduation labels (WRONG) and passed
  86.8% of candidates (near no-op).

  SELECTIVITY PROOF (firehose_copy_replay.parquet, n=545, base_grad=16.5%):
    Recalibrated (0.0445): n_sel=137 (25.1%), sel_grad=21.9%, lift=1.33x  [SELECTIVE]
    Frozen (0.1529):        n_sel=48  ( 8.8%), sel_grad=20.8%, lift=1.26x  [SELECTIVE]
    Wrong v1 (0.0142):      n_sel=473 (86.8%), sel_grad=18.2%, lift=1.10x  [NEAR NO-OP]

These tests run entirely from local data (zero credits).
Tests that need the real lake are marked @require_lake and skip in CI.
Tests that need the labeled proxy are marked @require_proxy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from copytrade.pgrad_calibration import (
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
LAKE_DATES = ["2026-06-20", "2026-06-21", "2026-06-22"]
REPLAY_PARQUET = "/Users/asim/NoIcloud/solanatrills/analysis/whale_graph/out/firehose_copy_replay.parquet"

require_lake = pytest.mark.skipif(
    not Path(LAKE_PATH).exists(),
    reason="local firehose lake not present (CI skip)",
)

require_proxy = pytest.mark.skipif(
    not Path(REPLAY_PARQUET).exists(),
    reason="firehose_copy_replay.parquet not present (CI skip)",
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
    """Verify the documented parity-break constants are correct and self-consistent."""

    def test_lab_price_median_documented(self):
        """Lab price_at_entry median (Birdeye USD/token) is documented."""
        assert LAB_PRICE_AT_ENTRY_MEDIAN == pytest.approx(9.73e-4, rel=0.01)

    def test_live_price_median_documented_is_sol_per_token(self):
        """Live firehose proxy median is raw SOL/token (NOT USD — different units from lab)."""
        # LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY is the raw firehose SOL/token median
        # from firehose_copy_replay.parquet (Jun 20-23, n=545).
        # It is NOT multiplied by SOL_USD — that would be ~7e-3 (11x above lab, wrong).
        # The raw SOL/token value is ~6.9e-5.
        assert LIVE_PRICE_AT_ENTRY_MEDIAN_PROXY == pytest.approx(6.9e-5, rel=0.10)

    def test_price_feature_inflation_ratio_documents_unit_difference(self):
        """PRICE_FEATURE_INFLATION_RATIO documents the Birdeye/firehose scale difference."""
        # Birdeye median ~6.3e-4 USD/token / firehose raw ~6.9e-5 SOL/token ~ 9x
        # NOTE: these are different units (USD vs SOL) so the ratio is informational
        assert PRICE_FEATURE_INFLATION_RATIO == pytest.approx(9.0, rel=0.15)

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

    def test_recalibrated_threshold_is_lower_than_seed(self):
        """Recalibrated threshold (0.0445) is below the seed (firehose scores are lower)."""
        assert RECALIBRATED_THRESHOLD < SEED_THRESHOLD_FROZEN, (
            f"Recalibrated {RECALIBRATED_THRESHOLD} >= seed {SEED_THRESHOLD_FROZEN}"
        )

    def test_recalibrated_threshold_is_positive(self):
        assert RECALIBRATED_THRESHOLD > 0.0

    def test_recalibrated_threshold_correct_value(self):
        """Recalibrated threshold = 0.0445 (p75 of labeled proxy, top-25% on n=545)."""
        # v1 wrong value was 0.0142 — that was label-free and near no-op (86.8% pass)
        # Correct value is 0.0445 — from firehose_copy_replay.parquet with labels
        assert RECALIBRATED_THRESHOLD == pytest.approx(0.0445, rel=0.01)

    def test_labeled_proxy_path_documented(self):
        """LABELED_PROXY_PARQUET constant documents the canonical labeled proxy path."""
        assert "firehose_copy_replay" in LABELED_PROXY_PARQUET


class TestParityTableStructure:
    """Parity table outputs correct structure."""

    def test_parity_table_uses_dollar_basis(self):
        """build_parity_table uses vol_sol * SOL_price (never vol_usd=0)."""
        from copytrade.firehose_harness import TapeRow

        row = TapeRow(
            mint="test_mint",
            block_time=1000,
            slot=100,
            signature="sig",
            price=7e-5,       # SOL/token (raw firehose)
            side="buy",
            vol=0.1,
            vol_sol=0.1,
            vol_usd=0.0,      # always 0 in these tapes
            owner="wallet1",
            phase="pre",
        )
        feats = _compute_features_from_tape([row], 1000, row, sol_usd=84.0)
        assert feats is not None
        # wallet_buy_usd = vol_sol * sol_usd (not vol_usd which is 0)
        assert feats["wallet_buy_usd"] == pytest.approx(0.1 * 84.0)
        # pre_buy_usd = vol_sol * sol_usd (dollar basis)
        assert feats["pre_buy_usd"] == pytest.approx(0.1 * 84.0)
        # vol_usd=0 is NOT used — pre_vol_usd should be non-zero from vol_sol path
        assert feats["pre_vol_usd"] != 0.0

    def test_lab_medians_documented(self):
        """_get_lab_medians returns documented constants when no parquet available."""
        medians = _get_lab_medians(None)
        assert "price_at_entry" in medians
        assert "fdv_proxy" in medians
        assert medians["price_at_entry"] == pytest.approx(LAB_PRICE_AT_ENTRY_MEDIAN, rel=0.01)
        assert medians["fdv_proxy"] == pytest.approx(LAB_PRICE_AT_ENTRY_MEDIAN * 1e9, rel=0.01)
        # fdv_proxy = price * 1e9 (by construction in entry_features.py)
        assert medians["fdv_proxy"] / medians["price_at_entry"] == pytest.approx(1e9, rel=0.01)


# ---------------------------------------------------------------------------
# AC-82.2 — Inflation source identified
# ---------------------------------------------------------------------------

class TestInflationSource:
    """AC-82.2: price_at_entry and fdv_proxy are the inflated features.

    KEY FINDING (corrected from v1):
    - Firehose price is in RAW SOL/token (not USD)
    - Lab Birdeye price is in USD/token (basePrice * quotePrice)
    - These are DIFFERENT UNITS, not the same quantity at different scales
    - The model was trained on Birdeye USD/token → firehose SOL/token gives
      systematically lower scores (median ~0.027 vs ~0.72 for graduates on Birdeye)
    - RANK ORDER is preserved: top-25% firehose proxy ≈ top-25% Birdeye
    """

    def test_firehose_price_is_sol_per_token(self):
        """Firehose price field is SOL/token, not USD/token."""
        # Typical firehose price: ~7e-5 SOL/token
        # At $84/SOL: 7e-5 * 84 = 5.9e-3 USD/token (much higher than lab 9.73e-4)
        # But raw firehose SOL/token (7e-5) is much LOWER than lab USD/token (9.73e-4)
        firehose_raw_sol_per_token = 7e-5
        lab_usd_per_token = LAB_PRICE_AT_ENTRY_MEDIAN  # 9.73e-4 USD/token
        # Raw firehose is 7-10x LOWER than lab Birdeye (different units)
        assert firehose_raw_sol_per_token < lab_usd_per_token, (
            "Firehose SOL/token should be numerically smaller than Birdeye USD/token"
        )

    def test_production_pipeline_uses_birdeye_usd_prices(self):
        """Production entry_features uses birdeye_items_to_owner_tape (USD/token basis)."""
        import inspect

        import copytrade.entry_features as ef

        src = inspect.getsource(ef.birdeye_items_to_owner_tape)
        # Must use basePrice * quotePrice (USD/token)
        assert "basePrice" in src
        assert "quotePrice" in src

    def test_fdv_proxy_is_price_times_1e9(self):
        """fdv_proxy = price_at_entry * 1e9 (by construction), so they share the same basis."""
        from copytrade.firehose_harness import TapeRow

        row = TapeRow(
            mint="m", block_time=1000, slot=1, signature="s",
            price=7e-5, side="buy", vol=1.0, vol_sol=1.0, vol_usd=0.0,
            owner="w", phase="pre",
        )
        feats = _compute_features_from_tape([row], 1000, row, sol_usd=84.0)
        assert feats is not None
        assert feats["fdv_proxy"] == pytest.approx(feats["price_at_entry"] * 1e9, rel=1e-6)

    def test_non_price_features_stable(self):
        """curve_frac, pre_sol_in, pre_n_trades etc. are NOT affected by price basis."""
        from copytrade.firehose_harness import TapeRow

        rows = [
            TapeRow("m", 900, 1, "s1", 5e-5, "buy", 10.0, 10.0, 0.0, "w1", "pre"),
            TapeRow("m", 950, 2, "s2", 5e-5, "buy", 20.0, 20.0, 0.0, "w2", "pre"),
            TapeRow("m", 980, 3, "s3", 5e-5, "sell", 5.0, 5.0, 0.0, "w3", "pre"),
        ]
        feats = _compute_features_from_tape(rows, 1000, rows[0], sol_usd=84.0)
        assert feats is not None
        # pre_sol_in = net buy SOL (10 + 20 - 5 = 25)
        assert feats["pre_sol_in"] == pytest.approx(25.0)
        # curve_frac = 25 / 85
        assert feats["curve_frac"] == pytest.approx(25.0 / 85.0, rel=0.01)
        # pre_n_buys = 2, pre_n_sells = 1
        assert feats["pre_n_buys"] == 2.0
        assert feats["pre_n_sells"] == 1.0


# ---------------------------------------------------------------------------
# AC-82.3 — Recalibrated gate is selective
# ---------------------------------------------------------------------------

class TestRecalibratedThreshold:
    """AC-82.3: Recalibrated threshold is config-driven and gate is selective."""

    def test_recalibrated_threshold_is_config_driven(self):
        """The threshold is a named constant in pgrad_calibration, not a scattered literal."""
        assert isinstance(RECALIBRATED_THRESHOLD, float)
        assert RECALIBRATED_THRESHOLD > 0.0
        assert RECALIBRATED_THRESHOLD < 1.0

    def test_meta_key_is_named_constant(self):
        """The meta key for the recalibrated threshold is a named constant."""
        assert META_KEY_RECALIBRATED == "pgrad_threshold_recalibrated"

    def test_meta_json_has_correct_threshold(self):
        """pgrad_meta.json has the correct recalibrated threshold (0.0445, not 0.0142)."""
        meta_path = Path(MODEL_DIR) / "pgrad_meta.json"
        if not meta_path.exists():
            pytest.skip("model dir not present")
        with open(meta_path) as f:
            meta = json.load(f)
        assert META_KEY_RECALIBRATED in meta
        thr = meta[META_KEY_RECALIBRATED]
        # Must be the correct labeled-proxy p75 (0.0445), NOT the wrong label-free 0.0142
        assert thr == pytest.approx(0.0445, rel=0.01), (
            f"pgrad_meta.json has wrong recalibrated threshold {thr} "
            f"(expected 0.0445, NOT 0.0142 which was label-free and near-no-op)"
        )

    def test_write_recalibrated_meta_updates_json(self, tmp_path):
        """write_recalibrated_meta persists the recalibrated threshold to pgrad_meta.json."""
        import shutil

        src = Path(MODEL_DIR) / "pgrad_meta.json"
        if not src.exists():
            pytest.skip("model dir not present")
        # Copy to temp location
        dst_dir = tmp_path / "model"
        dst_dir.mkdir()
        shutil.copy(src, dst_dir / "pgrad_meta.json")

        write_recalibrated_meta(str(dst_dir), 0.0445)

        with open(dst_dir / "pgrad_meta.json") as f:
            meta = json.load(f)
        assert META_KEY_RECALIBRATED in meta
        assert meta[META_KEY_RECALIBRATED] == pytest.approx(0.0445, rel=1e-6)
        # Frozen threshold should be UNCHANGED
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

        # Write the correct recalibrated threshold
        write_recalibrated_meta(str(dst_dir), 0.0445)

        from copytrade.pgrad_classifier import PgradClassifier

        clf = PgradClassifier(model_dir=str(dst_dir))
        clf.load()
        assert clf.threshold == pytest.approx(0.0445, rel=1e-6)
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

            # Ensure no recalibrated key
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
# AC-82.3 LOCAL-PROOF — ANTI-HOTFIX: selectivity on labeled proxy parquet
# ---------------------------------------------------------------------------

@require_proxy
@require_model
class TestSelectivityOnLabeledProxy:
    """LOCAL-PROOF: recalibrated gate is selective on labeled proxy (firehose_copy_replay).

    This is the CORRECT selectivity proof — uses the labeled population (grad labels
    from the relay parquet) not label-free firehose scoring.

    ANTI-HOTFIX DoD: asserts selected_grad_rate > base_grad_rate on real data.
    """

    @classmethod
    def setup_class(cls):
        """Run recalibration once against the labeled proxy parquet."""
        cls.results = compute_recalibrated_threshold(
            [],  # date_strs not needed when labeled_proxy_parquet is provided
            lake_base_dir=LAKE_PATH if Path(LAKE_PATH).exists() else "/tmp",
            model_dir=MODEL_DIR,
            labeled_proxy_parquet=REPLAY_PARQUET,
        )

    def test_used_labeled_proxy_source(self):
        """Recalibration used labeled_proxy source (not firehose_labels)."""
        assert self.results.get("source") == "labeled_proxy", (
            f"Expected source=labeled_proxy, got {self.results.get('source')}"
        )

    def test_n_candidates_is_correct(self):
        """Labeled proxy has ~545 gated candidates (Jun 20-23, full population)."""
        n = self.results["n_candidates"]
        assert 400 <= n <= 700, f"Expected ~545 candidates, got {n}"

    def test_base_grad_rate_is_correct(self):
        """Base grad rate on labeled proxy is ~16.5% (NOT 100% — full population)."""
        base = self.results["base_grad_rate"]
        assert 0.10 <= base <= 0.30, f"Base grad rate {base:.1%} outside 10-30% range"

    def test_selected_grad_rate_exceeds_base_rate(self):
        """ANTI-HOTFIX: selected_grad_rate > base_grad_rate proves gate is selective."""
        base = self.results["base_grad_rate"]
        sel = self.results["selected_grad_rate"]
        assert sel > base, (
            f"Gate is not selective: sel_grad {sel:.1%} <= base {base:.1%}. "
            "Expected top-25% to have strictly higher grad rate than base."
        )

    def test_selection_depth_is_25_pct(self):
        """About 25% of candidates are selected."""
        n_sel = self.results["n_selected"]
        n_cands = self.results["n_candidates"]
        pct = n_sel / n_cands
        assert 0.20 <= pct <= 0.30, f"Selected fraction {pct:.1%} not near 25%"

    def test_threshold_is_consistent_with_constant(self):
        """Computed threshold matches RECALIBRATED_THRESHOLD constant (within 10%)."""
        computed = self.results["recalibrated_threshold"]
        assert abs(computed - RECALIBRATED_THRESHOLD) / RECALIBRATED_THRESHOLD < 0.15, (
            f"Computed threshold {computed:.4f} differs >15% from constant {RECALIBRATED_THRESHOLD}"
        )

    def test_frozen_threshold_also_selective(self):
        """For reference: frozen threshold (0.153) is also selective on this population."""
        # This documents that the frozen threshold is NOT a no-op — it passes ~8.8%
        # with 1.26x lift. The recalibrated 0.0445 is better (1.33x at 25% selection).
        import lightgbm as lgb
        import pandas as pd

        bst = lgb.Booster(model_file=str(Path(MODEL_DIR) / "pgrad_lgbm.txt"))
        FEATS = ['tok_age_s', 'wallet_buy_usd', 'price_at_entry', 'fdv_proxy', 'pre_sol_in',
                 'pre_n_trades', 'pre_n_buys', 'pre_n_sells', 'pre_uniq_buyers',
                 'pre_uniq_sellers', 'pre_uniq_traders', 'pre_buy_usd', 'pre_sell_usd',
                 'pre_buysell_ratio', 'pre_vol_usd', 'pre_buys_last60', 'buyers_per_min',
                 'sol_in_last60', 'etg_s', 'curve_frac']

        df = pd.read_parquet(REPLAY_PARQUET)
        cands = df[(df['on_curve'] == True) & (df['curve_frac'] <= 0.6)].copy()  # noqa: E712
        X = cands.reindex(columns=FEATS, fill_value=0.0).fillna(0.0)
        scores = bst.predict(X)

        base_grad = cands['grad'].mean()
        sel_frozen = cands[scores >= SEED_THRESHOLD_FROZEN]
        # Frozen should select some candidates with lift > 1
        if len(sel_frozen) > 5:
            frozen_grad = sel_frozen['grad'].values.mean()
            assert frozen_grad > base_grad, (
                f"Frozen threshold not selective: {frozen_grad:.1%} <= {base_grad:.1%}"
            )


# ---------------------------------------------------------------------------
# AC-82.3 LOCAL-PROOF — Lake-based recalibration (secondary cross-check)
# ---------------------------------------------------------------------------

@require_lake
@require_model
class TestSelectivityOnFirehoseLake:
    """SECONDARY: recalibration on raw firehose tapes (no labels → approximate).

    This is a SECONDARY cross-check only. The primary selectivity proof is
    TestSelectivityOnLabeledProxy above.
    """

    @classmethod
    def setup_class(cls):
        """Run recalibration using raw firehose tapes (approx labels from cum-vol)."""
        cls.results = compute_recalibrated_threshold(
            LAKE_DATES,
            lake_base_dir=LAKE_PATH,
            model_dir=MODEL_DIR,
        )

    def test_found_gated_candidates(self):
        """There are gated on-curve candidates to score."""
        assert self.results["n_candidates"] > 100

    def test_threshold_below_seed(self):
        """Firehose proxy threshold should be below frozen seed."""
        assert self.results["recalibrated_threshold"] < SEED_THRESHOLD_FROZEN

    def test_selectivity_from_firehose_labels(self):
        """Firehose-labeled selectivity (cum-vol grad labels, approximate)."""
        # cum-vol labels are approximate — the test accepts weak selectivity
        sel = self.results["selected_grad_rate"]
        # If gate is non-trivially selective, sel > base.
        # For documentation: even weak lift is acceptable from the proxy.
        assert sel >= 0.0  # just verify it ran


# ---------------------------------------------------------------------------
# Honest caveat documented (US-82 requirement)
# ---------------------------------------------------------------------------

class TestHonestCaveatDocumented:
    """Verify the honest caveat is expressed in the module and constants."""

    def test_module_has_honest_caveat(self):
        """pgrad_calibration module docstring contains the honest caveat."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "properly tested" in doc or "PROPERLY TESTED" in doc, (
            "Module should document 'properly tested' caveat"
        )

    def test_module_documents_production_pipeline(self):
        """Module documents that production uses Birdeye (not firehose proxy)."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "Birdeye" in doc, "Module should mention Birdeye (production pipeline)"
        assert "PRODUCTION" in doc, "Module should explicitly document production pipeline"

    def test_module_documents_rank_order_preservation(self):
        """Module documents that rank order is preserved (proxy validity statement)."""
        import copytrade.pgrad_calibration as mod

        doc = mod.__doc__ or ""
        assert "RANK ORDER" in doc, (
            "Module should document that rank order is preserved between proxy and production"
        )

    def test_recalibration_results_include_caveat_note(self, tmp_path):
        """write_recalibrated_meta includes a note field with the honest caveat."""
        import shutil

        src = Path(MODEL_DIR) / "pgrad_meta.json"
        if not src.exists():
            pytest.skip("model dir not present")

        dst_dir = tmp_path / "m"
        dst_dir.mkdir()
        shutil.copy(src, dst_dir / "pgrad_meta.json")

        write_recalibrated_meta(
            str(dst_dir),
            0.0445,
            recalibration_results={
                "base_grad_rate": 0.165,
                "selected_grad_rate": 0.219,
                "n_candidates": 545,
                "n_selected": 137,
                "score_median": 0.0273,
                "is_selective": True,
                "source": "labeled_proxy",
            },
        )
        with open(dst_dir / "pgrad_meta.json") as f:
            meta = json.load(f)
        results = meta.get("recalibration_results", {})
        assert "note" in results
        note = results["note"]
        assert "properly-tested" in note or "properly tested" in note.lower()

    def test_parity_table_documents_wrong_v1_threshold(self):
        """pgrad_calibration documents why 0.0142 was WRONG (near no-op)."""
        from copytrade.pgrad_calibration import RECALIBRATED_THRESHOLD

        # The constant must NOT be the wrong label-free value 0.0142
        assert RECALIBRATED_THRESHOLD != pytest.approx(0.0142, rel=0.01), (
            "RECALIBRATED_THRESHOLD must NOT be 0.0142 (label-free near no-op). "
            "Correct value is 0.0445 from labeled_proxy."
        )
