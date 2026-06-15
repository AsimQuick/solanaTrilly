# ---
# module: core.tests.test_pipeline_config_schema_ac103
# sprint: sprint-3
# story: US-10 AC-10.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.schemas, pydantic
# ---
"""AC-10.3 — Feature-contract subset invariant (D2, §5.2).

The schema enforces that model.feature_contract is a subset of BOTH
feature_set.columns AND the live_servable set.  Because the FeatureSet and
ModelRegistry tables land in P5/P7, the check is tested against representative
in-memory column/live_servable lists — the 'guard now, regression-gate later'
pattern from US-2.

Test structure:
  (a) Trivial passes — no contract / no sets provided → no constraint fires
  (b) Valid-subset passes — contract ⊆ columns AND contract ⊆ live_servable
  (c) Rejection — contract ∉ feature_set.columns is rejected (matches "D2")
  (d) Rejection — contract ∉ live_servable is rejected (matches "D2")
  (e) Partial-set checks — only columns provided, only live_servable provided

No DB connection is required — this is pure Pydantic validation.
"""

import pytest
from pydantic import ValidationError

from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Canonical valid section payloads — all §5.2 numeric invariants satisfied
# ---------------------------------------------------------------------------

VALID_DETECTION = {
    "source": "birdeye_meme",
    "filter": {"source": "pump_dot_fun", "graduated": True},
    "prestage_progress_pct": 95.0,
    "dedupe_window_s": 60,
    "reconciler": "helius_migrate",
}

VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}

VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}

VALID_OUTCOME = {
    "window_s": 1800,
    "label_def": {},
}

VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
    "paper_size_usd": None,
    "exit_policy": None,
}

# Representative feature universe used across tests
COLUMNS = ["vol_1m", "buy_pct_1m", "price_change_pct", "trade_count_1m", "vwap_ratio"]
LIVE_SERVABLE = ["vol_1m", "buy_pct_1m", "price_change_pct", "trade_count_1m"]


def _build(
    feature_contract=None,
    feature_set_columns=None,
    live_servable=None,
) -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection=VALID_DETECTION,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
        feature_contract=feature_contract,
        feature_set_columns=feature_set_columns,
        live_servable=live_servable,
    )


# ---------------------------------------------------------------------------
# (a) Trivial passes — no contract or no reference sets provided
# ---------------------------------------------------------------------------


def test_no_feature_contract_passes_trivially():
    """No feature_contract → the subset check is skipped entirely (passes)."""
    schema = _build(feature_contract=None, feature_set_columns=COLUMNS, live_servable=LIVE_SERVABLE)
    assert schema.feature_contract is None


def test_no_contract_no_columns_no_servable_passes_trivially():
    """All three optional fields absent → config validates without the subset gate."""
    schema = _build()
    assert schema.feature_contract is None
    assert schema.feature_set_columns is None
    assert schema.live_servable is None


def test_empty_feature_contract_passes_trivially():
    """An empty contract [] is always a valid subset of any set."""
    schema = _build(feature_contract=[], feature_set_columns=COLUMNS, live_servable=LIVE_SERVABLE)
    assert schema.feature_contract == []


# ---------------------------------------------------------------------------
# (b) Valid-subset passes — contract ⊆ columns AND contract ⊆ live_servable
# ---------------------------------------------------------------------------


def test_valid_subset_of_columns_and_live_servable_passes():
    """A contract that is a proper subset of both columns and live_servable passes."""
    contract = ["vol_1m", "buy_pct_1m"]
    schema = _build(
        feature_contract=contract,
        feature_set_columns=COLUMNS,
        live_servable=LIVE_SERVABLE,
    )
    assert set(schema.feature_contract) == set(contract)


def test_contract_equal_to_live_servable_passes():
    """contract == live_servable is the maximal valid case — equality is a valid subset."""
    contract = list(LIVE_SERVABLE)
    schema = _build(
        feature_contract=contract,
        feature_set_columns=COLUMNS,
        live_servable=LIVE_SERVABLE,
    )
    assert set(schema.feature_contract) == set(LIVE_SERVABLE)


def test_single_column_valid_subset_passes():
    """A contract with a single valid column passes the subset invariant."""
    schema = _build(
        feature_contract=["vol_1m"],
        feature_set_columns=COLUMNS,
        live_servable=LIVE_SERVABLE,
    )
    assert schema.feature_contract == ["vol_1m"]


# ---------------------------------------------------------------------------
# (c) Rejection — contract ∉ feature_set.columns (D2, §5.2)
# ---------------------------------------------------------------------------


def test_contract_outside_feature_set_columns_is_rejected():
    """A column in the contract that is not in feature_set.columns must be rejected."""
    contract = ["vol_1m", "unknown_feature"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=COLUMNS,
            live_servable=None,
        )


def test_contract_entirely_outside_columns_is_rejected():
    """A contract made of columns not in feature_set.columns at all is rejected."""
    contract = ["stale_feature_a", "stale_feature_b"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=COLUMNS,
            live_servable=None,
        )


# ---------------------------------------------------------------------------
# (d) Rejection — contract ∉ live_servable (D2, §5.2)
# ---------------------------------------------------------------------------


def test_contract_outside_live_servable_is_rejected():
    """A column in the contract that is not live-servable must be rejected."""
    # vwap_ratio is in COLUMNS but NOT in LIVE_SERVABLE (training-only feature)
    contract = ["vol_1m", "vwap_ratio"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=COLUMNS,
            live_servable=LIVE_SERVABLE,
        )


def test_training_only_feature_outside_live_servable_is_rejected():
    """A training-only feature (absent from live_servable) cannot be in the contract."""
    training_only = "vwap_ratio"
    assert training_only in COLUMNS, "precondition: column exists in feature set"
    assert training_only not in LIVE_SERVABLE, "precondition: column is training-only"

    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=[training_only],
            feature_set_columns=COLUMNS,
            live_servable=LIVE_SERVABLE,
        )


def test_contract_outside_both_columns_and_live_servable_is_rejected():
    """A column absent from both columns and live_servable is rejected (first check fires)."""
    contract = ["completely_unknown"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=COLUMNS,
            live_servable=LIVE_SERVABLE,
        )


# ---------------------------------------------------------------------------
# (e) Partial-set checks — one reference set provided, the other absent
# ---------------------------------------------------------------------------


def test_only_columns_provided_valid_subset_passes():
    """When only feature_set_columns is given (no live_servable), columns check runs."""
    contract = ["vol_1m", "buy_pct_1m"]
    schema = _build(
        feature_contract=contract,
        feature_set_columns=COLUMNS,
        live_servable=None,
    )
    assert set(schema.feature_contract) == set(contract)


def test_only_columns_provided_invalid_column_is_rejected():
    """When only feature_set_columns is given, a missing column is still rejected."""
    contract = ["vol_1m", "not_in_set"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=COLUMNS,
            live_servable=None,
        )


def test_only_live_servable_provided_valid_subset_passes():
    """When only live_servable is given (no feature_set_columns), servable check runs."""
    contract = ["vol_1m", "buy_pct_1m"]
    schema = _build(
        feature_contract=contract,
        feature_set_columns=None,
        live_servable=LIVE_SERVABLE,
    )
    assert set(schema.feature_contract) == set(contract)


def test_only_live_servable_provided_non_servable_column_is_rejected():
    """When only live_servable is given, a non-servable column is rejected."""
    contract = ["vol_1m", "non_servable_col"]
    with pytest.raises(ValidationError, match="D2"):
        _build(
            feature_contract=contract,
            feature_set_columns=None,
            live_servable=LIVE_SERVABLE,
        )
