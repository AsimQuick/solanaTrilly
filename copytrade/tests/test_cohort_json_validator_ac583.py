# ---
# module: copytrade.tests.test_cohort_json_validator_ac583
# sprint: sprint-12
# story: US-58 AC-58.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.validators, pydantic, json, pathlib
# ---
"""AC-58.3 — Cohort-JSON validator for the SPEC §2 leaderboard.json contract.

Verifies:
  (a) The banked valid_leaderboard.json fixture is ACCEPTED — validate_cohort_json
      returns a CohortJsonSchema with the correct field values.
  (b) Missing cohort_id → REJECTED with CohortJsonValidationError.
  (c) Bad schema_version (not '1.0') → REJECTED with CohortJsonValidationError.
  (d) Missing wallet address → REJECTED with CohortJsonValidationError.
  (e) Out-of-range trade params — each individually rejected:
        - sol_size_per_trade <= 0
        - take_profit_pct <= 0
        - stop_loss_pct > 100
        - stop_loss_pct <= 0
        - curve_completion_exit_pct > 100
        - curve_completion_exit_pct <= 0
        - max_hold_seconds <= 0
        - max_concurrent_positions <= 0
        - mirror_wallet_sells=True (invariant)
        - mode not in ('observe','live')
  (f) Structural isolation — validators.py imports nothing from core.* (§5).

No firehose required — all tests are offline / pure Python.
"""

import json
from pathlib import Path

import pytest

from copytrade.validators import (
    CohortJsonSchema,
    CohortJsonValidationError,
    validate_cohort_json,
)

# ---------------------------------------------------------------------------
# Shared fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "valid_leaderboard.json"


def _load_fixture() -> dict:
    """Load the banked valid_leaderboard.json fixture as a plain dict."""
    return json.loads(_FIXTURE_PATH.read_text())


def _mutate(overrides: dict) -> dict:
    """Return a deep copy of the fixture with top-level key overrides applied."""
    data = _load_fixture()
    data.update(overrides)
    return data


def _mutate_trade_config(**kwargs) -> dict:
    """Return a deep copy of the fixture with trade_config field overrides applied."""
    data = _load_fixture()
    data["trade_config"] = {**data["trade_config"], **kwargs}
    return data


# ---------------------------------------------------------------------------
# (a) Valid fixture is ACCEPTED
# ---------------------------------------------------------------------------


def test_valid_leaderboard_fixture_is_accepted():
    """The banked valid_leaderboard.json must be parsed and accepted without error."""
    data = _load_fixture()
    result = validate_cohort_json(data)
    assert isinstance(result, CohortJsonSchema)


def test_valid_fixture_schema_version():
    """Accepted fixture must expose schema_version '1.0'."""
    result = validate_cohort_json(_load_fixture())
    assert result.schema_version == "1.0"


def test_valid_fixture_cohort_id():
    """Accepted fixture must expose the correct cohort_id."""
    result = validate_cohort_json(_load_fixture())
    assert result.cohort_id == "whale-ct-2026-06-17-v1"


def test_valid_fixture_wallet_count():
    """Accepted fixture must expose all wallet entries from the fixture."""
    result = validate_cohort_json(_load_fixture())
    assert len(result.wallets) == 5


def test_valid_fixture_first_wallet_address():
    """The first wallet entry must carry the correct address."""
    result = validate_cohort_json(_load_fixture())
    assert result.wallets[0].address == "EohTPNGdZkqH4e3gmhxSm5GqvtZ6PNpZMHmWXDsCVBx"


def test_valid_fixture_trade_config_mode_is_observe():
    """Accepted fixture trade_config must default to mode='observe'."""
    result = validate_cohort_json(_load_fixture())
    assert result.trade_config.mode == "observe"


def test_valid_fixture_trade_config_mirror_wallet_sells_is_false():
    """Accepted fixture trade_config must have mirror_wallet_sells=False."""
    result = validate_cohort_json(_load_fixture())
    assert result.trade_config.mirror_wallet_sells is False


def test_valid_fixture_informational_wallet_fields():
    """Informational wallet fields (rank, precision, etc.) must be parsed correctly."""
    result = validate_cohort_json(_load_fixture())
    w = result.wallets[0]
    assert w.rank == 1
    assert w.precision == pytest.approx(0.38)
    assert w.total_pump_buys == 255
    assert w.median_lead_min == pytest.approx(1.4)


def test_valid_fixture_wallet_missing_informational_fields_is_accepted():
    """A wallet entry with only an address (all informational fields absent) is valid."""
    data = _load_fixture()
    data["wallets"] = [{"address": "EohTPNGdZkqH4e3gmhxSm5GqvtZ6PNpZMHmWXDsCVBx"}]
    result = validate_cohort_json(data)
    assert result.wallets[0].rank is None
    assert result.wallets[0].precision is None


def test_valid_fixture_live_mode_is_accepted():
    """trade_config.mode='live' is valid (it is a Literal option, though observe is default)."""
    data = _mutate_trade_config(mode="live")
    result = validate_cohort_json(data)
    assert result.trade_config.mode == "live"


# ---------------------------------------------------------------------------
# (b) Missing cohort_id → REJECTED
# ---------------------------------------------------------------------------


def test_missing_cohort_id_is_rejected():
    """A payload missing cohort_id entirely must raise CohortJsonValidationError."""
    data = _load_fixture()
    del data["cohort_id"]
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_empty_cohort_id_is_rejected():
    """cohort_id='' (empty string) must raise CohortJsonValidationError."""
    data = _mutate({"cohort_id": ""})
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_whitespace_cohort_id_is_rejected():
    """cohort_id='   ' (whitespace only) must raise CohortJsonValidationError."""
    data = _mutate({"cohort_id": "   "})
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


# ---------------------------------------------------------------------------
# (c) Bad schema_version → REJECTED
# ---------------------------------------------------------------------------


def test_wrong_schema_version_is_rejected():
    """schema_version='2.0' must be rejected (only '1.0' is valid)."""
    data = _mutate({"schema_version": "2.0"})
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_missing_schema_version_is_rejected():
    """A payload missing schema_version entirely must raise CohortJsonValidationError."""
    data = _load_fixture()
    del data["schema_version"]
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_numeric_schema_version_is_rejected():
    """schema_version=1.0 (numeric, not string) must be rejected."""
    data = _mutate({"schema_version": 1.0})
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_schema_version_wrong_string_is_rejected():
    """schema_version='v1.0' must be rejected (exact string '1.0' required)."""
    data = _mutate({"schema_version": "v1.0"})
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


# ---------------------------------------------------------------------------
# (d) Missing wallet address → REJECTED
# ---------------------------------------------------------------------------


def test_wallet_missing_address_key_is_rejected():
    """A wallet entry missing the 'address' key entirely must raise CohortJsonValidationError."""
    data = _load_fixture()
    data["wallets"][0] = {"rank": 1, "precision": 0.38}
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_wallet_empty_address_is_rejected():
    """A wallet entry with address='' must raise CohortJsonValidationError."""
    data = _load_fixture()
    data["wallets"][0] = {"address": ""}
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_wallet_whitespace_address_is_rejected():
    """A wallet entry with address='   ' (whitespace only) must raise CohortJsonValidationError."""
    data = _load_fixture()
    data["wallets"][0] = {"address": "   "}
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_second_wallet_missing_address_is_rejected():
    """Missing address on the second wallet (not just the first) must also be rejected."""
    data = _load_fixture()
    data["wallets"][1] = {"rank": 2, "precision": 0.35}
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


# ---------------------------------------------------------------------------
# (e) Out-of-range trade params → REJECTED
# ---------------------------------------------------------------------------


def test_sol_size_per_trade_zero_rejected():
    """sol_size_per_trade=0 violates the >0 constraint."""
    data = _mutate_trade_config(sol_size_per_trade=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_sol_size_per_trade_negative_rejected():
    """sol_size_per_trade < 0 violates the >0 constraint."""
    data = _mutate_trade_config(sol_size_per_trade=-0.1)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_take_profit_pct_zero_rejected():
    """take_profit_pct=0 violates the >0 constraint."""
    data = _mutate_trade_config(take_profit_pct=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_take_profit_pct_negative_rejected():
    """take_profit_pct < 0 violates the >0 constraint."""
    data = _mutate_trade_config(take_profit_pct=-10)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_stop_loss_pct_zero_rejected():
    """stop_loss_pct=0 violates the >0 constraint."""
    data = _mutate_trade_config(stop_loss_pct=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_stop_loss_pct_above_100_rejected():
    """stop_loss_pct=101 violates the ≤100 bound."""
    data = _mutate_trade_config(stop_loss_pct=101)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_curve_completion_exit_pct_zero_rejected():
    """curve_completion_exit_pct=0 violates the >0 constraint."""
    data = _mutate_trade_config(curve_completion_exit_pct=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_curve_completion_exit_pct_above_100_rejected():
    """curve_completion_exit_pct=100.1 violates the ≤100 bound."""
    data = _mutate_trade_config(curve_completion_exit_pct=100.1)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_max_hold_seconds_zero_rejected():
    """max_hold_seconds=0 violates the >0 constraint."""
    data = _mutate_trade_config(max_hold_seconds=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_max_hold_seconds_negative_rejected():
    """max_hold_seconds < 0 violates the >0 constraint."""
    data = _mutate_trade_config(max_hold_seconds=-1)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_max_concurrent_positions_zero_rejected():
    """max_concurrent_positions=0 violates the >0 constraint."""
    data = _mutate_trade_config(max_concurrent_positions=0)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_mirror_wallet_sells_true_rejected():
    """mirror_wallet_sells=True violates the never-mirror-sells invariant (SPEC §3)."""
    data = _mutate_trade_config(mirror_wallet_sells=True)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_invalid_mode_rejected():
    """mode='autopilot' is not a valid Literal value and must be rejected."""
    data = _mutate_trade_config(mode="autopilot")
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


# ---------------------------------------------------------------------------
# (e) Additional structural rejection cases
# ---------------------------------------------------------------------------


def test_missing_trade_config_rejected():
    """A payload missing trade_config entirely must be rejected."""
    data = _load_fixture()
    del data["trade_config"]
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_missing_wallets_key_rejected():
    """A payload missing the wallets key entirely must be rejected."""
    data = _load_fixture()
    del data["wallets"]
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_json(data)


def test_empty_wallets_list_is_accepted():
    """An empty wallets list is structurally valid (zero wallets is a valid cohort)."""
    data = _mutate({"wallets": []})
    result = validate_cohort_json(data)
    assert result.wallets == []


def test_error_message_is_informative():
    """CohortJsonValidationError message must mention 'leaderboard.json' for clarity."""
    data = _load_fixture()
    del data["cohort_id"]
    with pytest.raises(CohortJsonValidationError, match="leaderboard.json"):
        validate_cohort_json(data)


# ---------------------------------------------------------------------------
# (f) Structural isolation — no import from core.* (§5 isolation)
# ---------------------------------------------------------------------------


def test_validators_module_does_not_import_core():
    """copytrade/validators.py must not import anything from core.* (§5 isolation).

    Checks that no import line in validators.py references core.schemas,
    core.models, PipelineConfig, or PipelineState — same guard as AC-58.1.
    """
    import importlib
    import re

    mod = importlib.import_module("copytrade.validators")
    src = mod.__file__

    with open(src) as f:
        lines = f.readlines()

    import_lines = [line for line in lines if re.match(r"^\s*(import|from)\s+", line)]
    combined = "".join(import_lines)

    assert "core.schemas" not in combined, (
        "copytrade.validators must not import from core.schemas (§5 isolation)"
    )
    assert "core.models" not in combined, (
        "copytrade.validators must not import from core.models (§5 isolation)"
    )
    assert "PipelineConfig" not in combined, (
        "copytrade.validators must not import PipelineConfig (§5 isolation)"
    )
    assert "PipelineState" not in combined, (
        "copytrade.validators must not import PipelineState (§5 isolation)"
    )
