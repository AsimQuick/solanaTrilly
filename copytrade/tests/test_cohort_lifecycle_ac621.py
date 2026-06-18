# ---
# module: copytrade.tests.test_cohort_lifecycle_ac621
# sprint: sprint-12
# story: US-62 AC-62.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, copytrade.cohort_lifecycle, copytrade.models,
#   copytrade.position_manager, copytrade.validators
# ---
"""AC-62.1: Cohort fresh-start lifecycle tests (SPEC §6).

Verifies that replace_cohort() performs the SPEC §6 full REPLACEMENT in order:
  (1) auto-stop engine
  (2) settle open positions with EXIT_SETTLE
  (3) purge all copytrade_* records for the old cohort
  (4) validate + persist the new cohort
  (5) subscribe the new wallets

Main scenario (test_main_scenario_cohort_b_over_cohort_a_with_open_positions):
  - Cohort A is active with 2 open positions and 2 wallet rows
  - replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS) is called
  - All of cohort A's records are gone; exactly cohort B remains; 2 settlements
    were recorded via close_position with EXIT_SETTLE reason.

All tests are deterministic (injected settlement_price + settlement_ts; no
firehose; no datetime.now()).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from copytrade.cohort_lifecycle import replace_cohort
from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)
from copytrade.position_manager import close_position as close_position_original
from copytrade.validators import CohortJsonValidationError

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

SETTLEMENT_PRICE = 1.5
SETTLEMENT_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

COHORT_B_JSON = {
    "schema_version": "1.0",
    "cohort_id": "cohort-ac621-B",
    "created_at": "2026-06-18",
    "description": "Cohort B",
    "trade_config": {
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
    },
    "wallets": [
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"},
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2"},
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3"},
    ],
}

ENTRY_TS = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cohort_a():
    """Create and return the active Cohort A row."""
    return CopytradeCohort.objects.create(
        cohort_id="cohort-ac621-A",
        created_at=datetime(2026, 6, 17, 0, 0, 0, tzinfo=timezone.utc),
        description="Cohort A",
        trade_config={},
        active=True,
    )


def _make_settings(engine_on=True, active_cohort_id="cohort-ac621-A"):
    """Ensure the singleton CopyTradeSettings row exists with the given state."""
    s = CopyTradeSettings.get()
    s.engine_on = engine_on
    s.active_cohort_id = active_cohort_id
    # Use update to bypass the Pydantic singleton clean() call for test setup
    CopyTradeSettings.objects.filter(pk=1).update(
        engine_on=engine_on,
        active_cohort_id=active_cohort_id,
    )
    return CopyTradeSettings.get()


def _make_open_position(cohort_id="cohort-ac621-A", mint_suffix="1"):
    """Create and return an open position."""
    return CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint=f"TokenMint{'x' * (40 - len(mint_suffix))}{mint_suffix}",
        trigger_wallet="WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
    )


def _make_wallet(cohort_id="cohort-ac621-A", suffix="1"):
    """Create and return a wallet row."""
    return CopytradeWallet.objects.create(
        cohort_id=cohort_id,
        address=f"WalletA{'a' * (40 - len(suffix))}{suffix}",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_main_scenario_cohort_b_over_cohort_a_with_open_positions():
    """AC-62.1 main test: cohort B replaces cohort A which has 2 open positions."""
    _make_cohort_a()
    _make_settings()
    _make_open_position(mint_suffix="1")
    _make_open_position(mint_suffix="2")
    _make_wallet(suffix="1")
    _make_wallet(suffix="2")

    with patch(
        "copytrade.cohort_lifecycle.close_position",
        wraps=close_position_original,
    ) as mock_cp:
        replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    # close_position was called exactly twice
    assert mock_cp.call_count == 2

    # Each call used EXIT_SETTLE reason + injected price/ts
    for call in mock_cp.call_args_list:
        args, kwargs = call
        # Signature: close_position(position, exit_reason, exit_price, exit_ts)
        exit_reason = args[1] if len(args) > 1 else kwargs.get("exit_reason")
        exit_price = args[2] if len(args) > 2 else kwargs.get("exit_price")
        exit_ts = args[3] if len(args) > 3 else kwargs.get("exit_ts")
        assert exit_reason == CopytradePosition.EXIT_SETTLE
        assert exit_price == SETTLEMENT_PRICE
        assert exit_ts == SETTLEMENT_TS

    # All cohort A records gone
    assert CopytradeCohort.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradeWallet.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradePosition.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradePnlByWallet.objects.filter(cohort_id="cohort-ac621-A").count() == 0

    # Exactly one active cohort — cohort B
    assert CopytradeCohort.objects.filter(active=True).count() == 1
    assert CopytradeCohort.objects.get(cohort_id="cohort-ac621-B").active is True

    # Cohort B wallets present
    assert CopytradeWallet.objects.filter(cohort_id="cohort-ac621-B").count() == 3

    # Only B exists
    assert CopytradeCohort.objects.count() == 1

    # Positions purged (settled positions are gone — purge runs AFTER settle)
    assert CopytradePosition.objects.count() == 0


@pytest.mark.django_db
def test_engine_auto_stopped_on_upload():
    """Engine is auto-stopped (engine_on=False) when replace_cohort is called."""
    _make_cohort_a()
    _make_settings(engine_on=True)

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert CopyTradeSettings.get().engine_on is False


@pytest.mark.django_db
def test_open_positions_settled_with_exit_settle_reason():
    """Three open positions are each closed with EXIT_SETTLE reason."""
    _make_cohort_a()
    _make_settings()
    _make_open_position(mint_suffix="1")
    _make_open_position(mint_suffix="2")
    _make_open_position(mint_suffix="3")

    with patch(
        "copytrade.cohort_lifecycle.close_position",
        wraps=close_position_original,
    ) as mock_cp:
        replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert mock_cp.call_count == 3
    for call in mock_cp.call_args_list:
        args, kwargs = call
        exit_reason = args[1] if len(args) > 1 else kwargs.get("exit_reason")
        assert exit_reason == CopytradePosition.EXIT_SETTLE


@pytest.mark.django_db
def test_all_old_cohort_records_purged():
    """All four copytrade_* table rows for cohort A are purged after replace."""
    _make_cohort_a()
    _make_settings()
    _make_open_position(mint_suffix="1")
    _make_wallet(suffix="1")
    # Add a PnL row manually
    CopytradePnlByWallet.objects.create(
        cohort_id="cohort-ac621-A",
        address="WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
        n_trades=1,
        win_rate=1.0,
        total_pnl_sol=0.1,
        avg_hold_s=60.0,
    )

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert CopytradeCohort.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradeWallet.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradePosition.objects.filter(cohort_id="cohort-ac621-A").count() == 0
    assert CopytradePnlByWallet.objects.filter(cohort_id="cohort-ac621-A").count() == 0


@pytest.mark.django_db
def test_exactly_one_active_cohort_after_replace():
    """Exactly one CopytradeCohort with active=True exists after replace."""
    _make_cohort_a()
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert CopytradeCohort.objects.filter(active=True).count() == 1


@pytest.mark.django_db
def test_new_wallets_subscribed():
    """Cohort B's three wallet addresses are persisted after replace."""
    _make_cohort_a()
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    b_wallets = list(
        CopytradeWallet.objects.filter(cohort_id="cohort-ac621-B").values_list("address", flat=True)
    )
    assert len(b_wallets) == 3
    assert "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1" in b_wallets
    assert "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2" in b_wallets
    assert "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3" in b_wallets


@pytest.mark.django_db
def test_active_cohort_id_updated_in_settings():
    """CopyTradeSettings.active_cohort_id points to cohort B after replace."""
    _make_cohort_a()
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert CopyTradeSettings.get().active_cohort_id == "cohort-ac621-B"


@pytest.mark.django_db
def test_invalid_json_raises_validation_error():
    """replace_cohort raises CohortJsonValidationError for invalid JSON; no DB mutation."""
    _make_cohort_a()
    _make_settings()

    bad_json = {
        "schema_version": "1.0",
        # cohort_id intentionally missing
        "created_at": "2026-06-18",
        "trade_config": {
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
        },
        "wallets": [{"address": "WalletXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1"}],
    }

    with pytest.raises(CohortJsonValidationError):
        replace_cohort(bad_json, SETTLEMENT_PRICE, SETTLEMENT_TS)

    # Cohort A must still be intact (validation ran before any mutation)
    assert CopytradeCohort.objects.filter(cohort_id="cohort-ac621-A").count() == 1
    assert CopytradeCohort.objects.filter(active=True).count() == 1


@pytest.mark.django_db
def test_no_existing_cohort_first_upload():
    """replace_cohort works with no existing cohort (first upload scenario)."""
    # Ensure the settings singleton exists but has no active cohort
    _make_settings(engine_on=False, active_cohort_id=None)
    # Clear any cohort rows that _make_settings may not have removed
    CopytradeCohort.objects.all().delete()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    assert CopytradeCohort.objects.filter(cohort_id="cohort-ac621-B", active=True).count() == 1
    assert CopytradeWallet.objects.filter(cohort_id="cohort-ac621-B").count() == 3


def test_h1_import_trap():
    """Import trap: replace_cohort must be importable from copytrade.cohort_lifecycle."""
    # If replace_cohort is deleted or renamed CI collection fails here.
    from copytrade.cohort_lifecycle import replace_cohort  # noqa: F401
