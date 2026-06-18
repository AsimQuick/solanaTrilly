# ---
# module: copytrade.tests.test_copytrade_config_ac581
# sprint: sprint-12
# story: US-58 AC-58.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.schemas, copytrade.models, pydantic, django.core.exceptions
# ---
"""AC-58.1 — CopyTradeConfig Pydantic schema + CopyTradeSettings write-path gate.

Verifies:
  (a) A valid config persists — CopyTradeSettings.save() with all valid field
      values produces a DB row.
  (b) Invalid configs are REJECTED at save time — each invariant violation
      raises DjangoValidationError and leaves no row behind.
  (c) The default mode is 'observe' — CopyTradeConfig() with no mode argument
      has mode == 'observe'.

Save-time invariants under test:
  - mirror_wallet_sells=True  → rejected (SPEC §3 never-mirror-sells gate)
  - sol_size_per_trade <= 0   → rejected (must be >0)
  - sol_size_per_trade < 0    → rejected (must be >0)
  - stop_loss_pct > 100       → rejected (bounded ≤ 100)
  - stop_loss_pct == 0        → rejected (must be >0)
  - curve_completion_exit_pct > 100 → rejected (bounded ≤ 100)
  - curve_completion_exit_pct == 0  → rejected (must be >0)
  - mode='invalid_mode'       → rejected (Literal constraint)
  - max_hold_seconds == 0     → rejected (must be >0)
  - max_concurrent_positions == 0   → rejected (must be >0)
  - take_profit_pct == 0      → rejected (must be >0)

No firehose required — all tests are offline / in-memory / DB-only.
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from pydantic import ValidationError as PydanticValidationError

from copytrade.models import CopyTradeSettings
from copytrade.schemas import CopyTradeConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_KWARGS = {
    "mode": "observe",
    "sol_size_per_trade": 0.25,
    "take_profit_pct": 200.0,
    "stop_loss_pct": 40.0,
    "exit_before_graduation": True,
    "curve_completion_exit_pct": 90.0,
    "max_hold_seconds": 1800,
    "max_concurrent_positions": 20,
    "copy_only_pumpfun_curve_buys": True,
    "copy_first_buy_only": True,
    "dedupe_token_across_wallets": True,
    "mirror_wallet_sells": False,
    "active_cohort_id": None,
    "engine_on": False,
}


def _make(**overrides) -> CopyTradeSettings:
    """Build an unsaved CopyTradeSettings from the valid base (not saved)."""
    kwargs = {**VALID_KWARGS, **overrides}
    return CopyTradeSettings(**kwargs)


# ---------------------------------------------------------------------------
# (a) Positive path — a valid config must persist
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_config_saves_successfully():
    """A CopyTradeSettings with all valid fields should save without error."""
    cfg = _make()
    cfg.save()
    assert CopyTradeSettings.objects.filter(pk=1).exists()


@pytest.mark.django_db
def test_valid_config_with_live_mode_saves():
    """mode='live' is a valid value (the P8-gated operator flip) and must persist."""
    cfg = _make(mode="live")
    cfg.save()
    assert CopyTradeSettings.objects.get(pk=1).mode == "live"


@pytest.mark.django_db
def test_singleton_second_save_overwrites_first():
    """Saving twice keeps exactly one row (pk=1 singleton contract)."""
    _make(sol_size_per_trade=0.25).save()
    _make(sol_size_per_trade=0.5).save()
    assert CopyTradeSettings.objects.count() == 1
    assert CopyTradeSettings.objects.get(pk=1).sol_size_per_trade == pytest.approx(0.5)


@pytest.mark.django_db
def test_get_classmethod_creates_singleton_if_absent():
    """CopyTradeSettings.get() must return (and create) the singleton row."""
    assert CopyTradeSettings.objects.count() == 0
    obj = CopyTradeSettings.get()
    assert obj.pk == 1
    assert CopyTradeSettings.objects.count() == 1


@pytest.mark.django_db
def test_to_schema_returns_valid_copytrade_config():
    """to_schema() on a saved row returns a CopyTradeConfig with matching values."""
    cfg = _make(sol_size_per_trade=0.5, mode="observe")
    cfg.save()
    schema = CopyTradeSettings.objects.get(pk=1).to_schema()
    assert isinstance(schema, CopyTradeConfig)
    assert schema.mode == "observe"
    assert schema.sol_size_per_trade == pytest.approx(0.5)
    assert schema.mirror_wallet_sells is False


# ---------------------------------------------------------------------------
# (b) Invalid configs REJECTED at save time — each invariant separately
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_mirror_wallet_sells_true_rejected_at_save():
    """mirror_wallet_sells=True violates the SPEC §3 never-mirror-sells invariant."""
    cfg = _make(mirror_wallet_sells=True)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_mirror_wallet_sells_rejection_writes_no_row():
    """mirror_wallet_sells=True save must not persist any row."""
    cfg = _make(mirror_wallet_sells=True)
    try:
        cfg.save()
    except DjangoValidationError:
        pass
    assert CopyTradeSettings.objects.count() == 0


@pytest.mark.django_db
def test_sol_size_zero_rejected_at_save():
    """sol_size_per_trade=0 violates the >0 constraint."""
    cfg = _make(sol_size_per_trade=0.0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_sol_size_negative_rejected_at_save():
    """sol_size_per_trade < 0 violates the >0 constraint."""
    cfg = _make(sol_size_per_trade=-0.1)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_stop_loss_pct_above_100_rejected_at_save():
    """stop_loss_pct > 100 violates the ≤ 100 bound."""
    cfg = _make(stop_loss_pct=101.0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_stop_loss_pct_zero_rejected_at_save():
    """stop_loss_pct=0 violates the >0 constraint."""
    cfg = _make(stop_loss_pct=0.0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_curve_completion_exit_pct_above_100_rejected_at_save():
    """curve_completion_exit_pct > 100 violates the ≤ 100 bound."""
    cfg = _make(curve_completion_exit_pct=100.1)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_curve_completion_exit_pct_zero_rejected_at_save():
    """curve_completion_exit_pct=0 violates the >0 constraint."""
    cfg = _make(curve_completion_exit_pct=0.0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_invalid_mode_rejected_at_save():
    """mode not in Literal['observe','live'] must be rejected."""
    cfg = _make(mode="autopilot")
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_max_hold_seconds_zero_rejected_at_save():
    """max_hold_seconds=0 violates the >0 constraint."""
    cfg = _make(max_hold_seconds=0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_max_concurrent_positions_zero_rejected_at_save():
    """max_concurrent_positions=0 violates the >0 constraint."""
    cfg = _make(max_concurrent_positions=0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


@pytest.mark.django_db
def test_take_profit_pct_zero_rejected_at_save():
    """take_profit_pct=0 violates the >0 constraint."""
    cfg = _make(take_profit_pct=0.0)
    with pytest.raises(DjangoValidationError):
        cfg.save()


# ---------------------------------------------------------------------------
# (c) Default mode is 'observe' — pure Pydantic, no DB
# ---------------------------------------------------------------------------


def test_default_mode_is_observe():
    """CopyTradeConfig with no explicit mode must default to 'observe'."""
    schema = CopyTradeConfig(
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
    )
    assert schema.mode == "observe"


def test_default_mirror_wallet_sells_is_false():
    """CopyTradeConfig with no explicit mirror_wallet_sells must default to False."""
    schema = CopyTradeConfig(
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
    )
    assert schema.mirror_wallet_sells is False


def test_schema_mirror_wallet_sells_true_raises_pydantic_error():
    """Constructing CopyTradeConfig with mirror_wallet_sells=True raises PydanticValidationError."""
    with pytest.raises(PydanticValidationError, match="mirror_wallet_sells"):
        CopyTradeConfig(
            sol_size_per_trade=0.25,
            take_profit_pct=200.0,
            stop_loss_pct=40.0,
            max_hold_seconds=1800,
            max_concurrent_positions=20,
            mirror_wallet_sells=True,
        )


def test_schema_all_fields_accessible_by_attribute():
    """Every SPEC §2 field and runtime state must be accessible by attribute name."""
    schema = CopyTradeConfig(
        mode="observe",
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        exit_before_graduation=True,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
        copy_only_pumpfun_curve_buys=True,
        copy_first_buy_only=True,
        dedupe_token_across_wallets=True,
        mirror_wallet_sells=False,
        active_cohort_id="whale-ct-2026-06-17-v1",
        engine_on=False,
    )
    assert schema.mode == "observe"
    assert schema.sol_size_per_trade == pytest.approx(0.25)
    assert schema.take_profit_pct == pytest.approx(200.0)
    assert schema.stop_loss_pct == pytest.approx(40.0)
    assert schema.exit_before_graduation is True
    assert schema.curve_completion_exit_pct == pytest.approx(90.0)
    assert schema.max_hold_seconds == 1800
    assert schema.max_concurrent_positions == 20
    assert schema.copy_only_pumpfun_curve_buys is True
    assert schema.copy_first_buy_only is True
    assert schema.dedupe_token_across_wallets is True
    assert schema.mirror_wallet_sells is False
    assert schema.active_cohort_id == "whale-ct-2026-06-17-v1"
    assert schema.engine_on is False


def test_schema_isolation_from_pipeline_config():
    """CopyTradeConfig must NOT import from core.schemas or core.models (§5 isolation).

    Checks that no import statement in copytrade.schemas references core.schemas,
    core.models, PipelineConfig, or PipelineState — only doc/comment mentions are
    allowed (they are NOT imports and do NOT create shared mutable state).
    """
    import importlib
    import re

    mod = importlib.import_module("copytrade.schemas")
    src = mod.__file__

    with open(src) as f:
        lines = f.readlines()

    import_lines = [line for line in lines if re.match(r"^\s*(import|from)\s+", line)]
    combined = "".join(import_lines)

    assert "core.schemas" not in combined, (
        "copytrade.schemas must not import from core.schemas (§5 isolation)"
    )
    assert "core.models" not in combined, (
        "copytrade.schemas must not import from core.models (§5 isolation)"
    )
    assert "PipelineConfig" not in combined, (
        "copytrade.schemas must not import PipelineConfig (§5 isolation)"
    )
    assert "PipelineState" not in combined, (
        "copytrade.schemas must not import PipelineState (§5 isolation)"
    )
