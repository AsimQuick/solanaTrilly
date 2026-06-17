# ---
# module: core.tests.test_promote_model_gate_ac413
# sprint: sprint-9
# story: US-41 AC-41.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: tools.promote_model, core.feature_reconciler, json, pathlib, pytest
# ---
"""AC-41.3 — Feature-contract gate wired into promote_model.py with H1 ImportError trap.

The reconciliation gate (tools.promote_model.run_feature_contract_gate) is the
hard guard consumed by the promoter (US-42).  This test suite verifies:

  1. The gate is importable via the H1 ImportError trap — deleting or renaming
     run_feature_contract_gate or FeatureContractError fails pytest collection.
  2. The gate ACCEPTS a model with a clean feature contract.
  3. The gate REFUSES (raises FeatureContractError) and PRINTS REMEDY for each
     class of mismatch: missing feature, training-only feature, order divergence,
     and booster mismatch.
  4. The REMEDY message names the specific offending feature and the mismatch kind.

H1 note: no second workflow file is introduced.  All assertions run inside the
single canonical ci.yml 'test' job (H1 — no second workflow).

Tests
-----
Wiring guard (H1 ImportError trap):
  test_promote_model_gate_importable
      run_feature_contract_gate and FeatureContractError must be importable from
      tools.promote_model; missing import fails collection.

Gate acceptance:
  test_gate_accepts_clean_contract
      A model whose feature_list matches columns and live_servable in order is
      accepted (no exception raised).
  test_gate_accepts_clean_contract_with_boosters
      Same as above but with 15 booster feature lists all matching the model list.
  test_gate_returns_reconcile_result_on_clean
      On success the gate returns a ReconcileResult with clean==True.
  test_gate_accepts_v32_real_contract
      The actual v3.2 meta.json:features against a FeatureSet where all 20 are
      live_servable passes the gate (the real contract).

Gate refusal — feature_list mismatches:
  test_gate_refuses_missing_feature
      A model feature absent from feature_set_columns → FeatureContractError.
  test_gate_refuses_training_only_feature
      A model feature absent from live_servable → FeatureContractError.
  test_gate_refuses_order_divergence
      feature_set_columns has model features in wrong relative order → FeatureContractError.
  test_gate_refuses_booster_mismatch
      A booster whose feature list differs from model_feature_list → FeatureContractError.
  test_gate_refuses_multiple_violations
      Multiple violations (missing + training_only) all raise FeatureContractError.

REMEDY output:
  test_remedy_names_missing_feature
      The FeatureContractError message (and printed output) names the missing feature.
  test_remedy_names_training_only_feature
      The FeatureContractError message names the training-only feature.
  test_remedy_names_order_divergence
      The FeatureContractError message mentions ORDER DIVERGENCE.
  test_remedy_names_booster_mismatch
      The FeatureContractError message mentions BOOSTER MISMATCH.
  test_remedy_printed_to_stdout
      With print_remedy=True (default) the REMEDY lines are printed to stdout.
  test_no_remedy_printed_when_disabled
      With print_remedy=False nothing is printed to stdout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — wiring guard (must be module-level imports)
# ---------------------------------------------------------------------------
# If run_feature_contract_gate or FeatureContractError are deleted or renamed
# in tools.promote_model, pytest collection fails here — immediately and loudly.
# This is the canonical H1 compile-time trap.
from tools.promote_model import FeatureContractError, run_feature_contract_gate

# ---------------------------------------------------------------------------
# Fixtures path — banked v3.2 meta.json (offline, no firehose needed)
# ---------------------------------------------------------------------------

_FIXTURES_DIR: Path = Path(__file__).parent / "fixtures" / "trilly_pregrad_v3_2"
_META_PATH: Path = _FIXTURES_DIR / "meta.json"


def _load_v32_features() -> list[str]:
    with _META_PATH.open() as fh:
        return json.load(fh)["features"]


# ---------------------------------------------------------------------------
# H1 wiring guard test
# ---------------------------------------------------------------------------


def test_promote_model_gate_importable() -> None:
    """run_feature_contract_gate and FeatureContractError are importable."""
    assert callable(run_feature_contract_gate)
    assert issubclass(FeatureContractError, Exception)


# ---------------------------------------------------------------------------
# Gate acceptance tests
# ---------------------------------------------------------------------------


def test_gate_accepts_clean_contract() -> None:
    """Clean contract (all features present and live_servable, correct order) passes."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    result = run_feature_contract_gate(
        model_feature_list=model_features,
        feature_set_columns=model_features,
        feature_set_live_servable=model_features,
    )
    assert result.clean


def test_gate_accepts_clean_contract_with_boosters() -> None:
    """Clean contract with all boosters matching the model list passes."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    booster_lists = [list(model_features) for _ in range(15)]  # 15 boosters all matching
    result = run_feature_contract_gate(
        model_feature_list=model_features,
        feature_set_columns=model_features,
        feature_set_live_servable=model_features,
        booster_feature_lists=booster_lists,
    )
    assert result.clean
    assert result.booster_mismatches == []


def test_gate_returns_reconcile_result_on_clean() -> None:
    """On success, the gate returns a ReconcileResult with clean==True."""
    from core.feature_reconciler import ReconcileResult

    model_features = ["feat_x", "feat_y"]
    result = run_feature_contract_gate(
        model_feature_list=model_features,
        feature_set_columns=model_features + ["feat_extra"],
        feature_set_live_servable=model_features + ["feat_extra"],
    )
    assert isinstance(result, ReconcileResult)
    assert result.clean
    assert "feat_extra" in result.extra  # extra is informational only


def test_gate_accepts_v32_real_contract() -> None:
    """The real v3.2 20-feature contract passes the gate when all are live_servable."""
    meta_features = _load_v32_features()
    assert len(meta_features) == 20, "Fixture must have exactly 20 features"

    # All 20 pre_* features live-computable (the AC-41.2 acceptance check)
    extra = ["tape_n_trades", "tape_ret_total"]
    result = run_feature_contract_gate(
        model_feature_list=meta_features,
        feature_set_columns=meta_features + extra,
        feature_set_live_servable=meta_features + extra,
    )
    assert result.clean
    assert result.missing == []
    assert result.training_only == []
    assert result.order_divergence is False


# ---------------------------------------------------------------------------
# Gate refusal tests
# ---------------------------------------------------------------------------


def test_gate_refuses_missing_feature() -> None:
    """Missing model feature in FeatureSet.columns → FeatureContractError."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    columns_without_b = ["feat_a", "feat_c"]

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=columns_without_b,
            feature_set_live_servable=columns_without_b,
            print_remedy=False,
        )


def test_gate_refuses_training_only_feature() -> None:
    """Model feature not in live_servable (training_only) → FeatureContractError."""
    model_features = ["feat_a", "feat_b", "feat_c"]

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=model_features,
            feature_set_live_servable=["feat_a", "feat_c"],  # feat_b locked out
            print_remedy=False,
        )


def test_gate_refuses_order_divergence() -> None:
    """Wrong relative order of model features in columns → FeatureContractError."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    swapped = ["feat_b", "feat_a", "feat_c"]  # a and b swapped in columns

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=swapped,
            feature_set_live_servable=swapped,
            print_remedy=False,
        )


def test_gate_refuses_booster_mismatch() -> None:
    """A booster with the wrong feature order → FeatureContractError."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    good_booster = list(model_features)
    bad_booster = ["feat_b", "feat_a", "feat_c"]  # wrong order

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=model_features,
            feature_set_live_servable=model_features,
            booster_feature_lists=[good_booster, bad_booster],
            print_remedy=False,
        )


def test_gate_refuses_multiple_violations() -> None:
    """Multiple violations (missing + training_only) both raise FeatureContractError."""
    model_features = ["feat_a", "feat_b", "feat_c", "feat_d"]

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=["feat_a", "feat_c", "feat_d"],  # feat_b missing
            feature_set_live_servable=["feat_a", "feat_c"],  # feat_d training_only
            print_remedy=False,
        )


# ---------------------------------------------------------------------------
# REMEDY output tests
# ---------------------------------------------------------------------------


def test_remedy_names_missing_feature() -> None:
    """FeatureContractError message names the missing feature."""
    model_features = ["feat_a", "feat_missing", "feat_c"]
    columns = ["feat_a", "feat_c"]

    with pytest.raises(FeatureContractError) as exc_info:
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=columns,
            feature_set_live_servable=columns,
            print_remedy=False,
        )

    assert "feat_missing" in str(exc_info.value)
    assert "MISSING" in str(exc_info.value)


def test_remedy_names_training_only_feature() -> None:
    """FeatureContractError message names the training-only feature."""
    model_features = ["feat_a", "feat_locked", "feat_c"]

    with pytest.raises(FeatureContractError) as exc_info:
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=model_features,
            feature_set_live_servable=["feat_a", "feat_c"],  # feat_locked not live
            print_remedy=False,
        )

    assert "feat_locked" in str(exc_info.value)
    assert "TRAINING-ONLY" in str(exc_info.value)


def test_remedy_names_order_divergence() -> None:
    """FeatureContractError message mentions ORDER DIVERGENCE."""
    model_features = ["feat_a", "feat_b", "feat_c"]

    with pytest.raises(FeatureContractError) as exc_info:
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=["feat_b", "feat_a", "feat_c"],
            feature_set_live_servable=["feat_b", "feat_a", "feat_c"],
            print_remedy=False,
        )

    assert "ORDER DIVERGENCE" in str(exc_info.value)


def test_remedy_names_booster_mismatch() -> None:
    """FeatureContractError message mentions BOOSTER MISMATCH."""
    model_features = ["feat_a", "feat_b", "feat_c"]
    bad_booster = ["feat_b", "feat_a", "feat_c"]

    with pytest.raises(FeatureContractError) as exc_info:
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=model_features,
            feature_set_live_servable=model_features,
            booster_feature_lists=[bad_booster],
            print_remedy=False,
        )

    assert "BOOSTER MISMATCH" in str(exc_info.value)


def test_remedy_printed_to_stdout(capsys: pytest.CaptureFixture) -> None:
    """With print_remedy=True (default), REMEDY lines are printed to stdout."""
    model_features = ["feat_a", "feat_gone", "feat_c"]
    columns = ["feat_a", "feat_c"]

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=columns,
            feature_set_live_servable=columns,
            print_remedy=True,  # default
        )

    captured = capsys.readouterr()
    assert "PROMOTION REFUSED" in captured.out
    assert "REMEDY" in captured.out
    assert "feat_gone" in captured.out


def test_no_remedy_printed_when_disabled(capsys: pytest.CaptureFixture) -> None:
    """With print_remedy=False, nothing is printed to stdout on refusal."""
    model_features = ["feat_a", "feat_gone", "feat_c"]
    columns = ["feat_a", "feat_c"]

    with pytest.raises(FeatureContractError):
        run_feature_contract_gate(
            model_feature_list=model_features,
            feature_set_columns=columns,
            feature_set_live_servable=columns,
            print_remedy=False,
        )

    captured = capsys.readouterr()
    assert captured.out == ""
