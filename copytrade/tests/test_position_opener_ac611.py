# ---
# module: copytrade.tests.test_position_opener_ac611
# sprint: sprint-12
# story: US-61 AC-61.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, unittest.mock, copytrade.position_opener,
#   copytrade.execution, copytrade.models, copytrade.schemas, copytrade.trigger_pipeline
# ---
"""AC-61.1: Observe-mode position opener tests.

Verifies:

  DB-write correctness (pytest.mark.django_db):
    1. An observe open writes EXACTLY ONE CopytradePosition row.
    2. The position has mode='observe' and status='open'.
    3. The position entry_price equals the injected booked fill price.
    4. The position sol_in equals config.sol_size_per_trade.
    5. The position cohort_id / mint / trigger_wallet / entry_ts match
       the OpenedPositionRecord (all fields correct).

  Execution apparatus guard (the AC-61.1 safety gate):
    6. Calling open_observe_position in observe mode NEVER invokes
       copytrade.execution.place_buy_order (assert_not_called).

  P8 gating (no django_db required):
    7. copytrade.execution.place_buy_order raises NotImplementedError
       — the live path is gated until P8 ships.
    8. open_observe_position raises ValueError when config.mode == 'live'
       — the function enforces the observe-only gate.

All DB tests run offline with no firehose.  The injected entry_price and
the OpenedPositionRecord are constructed directly — no trigger pipeline run
is required.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from copytrade.execution import place_buy_order
from copytrade.models import CopytradePosition
from copytrade.position_opener import open_observe_position
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
_COHORT_ID = "cohort-ac611-test"
_MINT = "MintAC611PumpXXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_WALLET = "Wa11etAC611AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_ENTRY_PRICE = 0.000_012_345
_SOL_SIZE = 0.5


def _record() -> OpenedPositionRecord:
    """Build a minimal OpenedPositionRecord for AC-61.1 tests."""
    return OpenedPositionRecord(
        cohort_id=_COHORT_ID,
        mint=_MINT,
        trigger_wallet=_WALLET,
        entry_ts=_TS,
    )


def _config(**overrides) -> CopyTradeConfig:
    """Return a valid CopyTradeConfig with observe-mode defaults."""
    defaults = {
        "mode": "observe",
        "sol_size_per_trade": _SOL_SIZE,
        "take_profit_pct": 200.0,
        "stop_loss_pct": 40.0,
        "exit_before_graduation": True,
        "curve_completion_exit_pct": 90.0,
        "max_hold_seconds": 1800,
        "max_concurrent_positions": 20,
        "mirror_wallet_sells": False,
    }
    defaults.update(overrides)
    return CopyTradeConfig(**defaults)


# ===========================================================================
# 1. Exactly one position row is written
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_writes_exactly_one_position():
    """open_observe_position must write exactly ONE CopytradePosition row.

    Before the call: 0 rows.
    After the call:  1 row.
    """
    assert CopytradePosition.objects.count() == 0

    open_observe_position(_record(), _ENTRY_PRICE, _config())

    assert CopytradePosition.objects.count() == 1


# ===========================================================================
# 2. mode='observe' and status='open'
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_has_mode_observe_and_status_open():
    """The opened position must have mode='observe' and status='open'."""
    open_observe_position(_record(), _ENTRY_PRICE, _config())

    pos = CopytradePosition.objects.get()
    assert pos.mode == CopytradePosition.MODE_OBSERVE
    assert pos.status == CopytradePosition.STATUS_OPEN


# ===========================================================================
# 3. entry_price equals the booked fill price
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_entry_price_matches_booked_fill():
    """The position entry_price must equal the injected booked fill price.

    This is the 'books a fill at the current curve price' invariant from
    AC-61.1: the price passed by the caller IS the recorded entry price.
    """
    open_observe_position(_record(), _ENTRY_PRICE, _config())

    pos = CopytradePosition.objects.get()
    assert pos.entry_price == pytest.approx(_ENTRY_PRICE)


# ===========================================================================
# 4. sol_in equals config.sol_size_per_trade
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_sol_in_matches_config_sol_size():
    """The position sol_in must equal config.sol_size_per_trade."""
    open_observe_position(_record(), _ENTRY_PRICE, _config())

    pos = CopytradePosition.objects.get()
    assert pos.sol_in == pytest.approx(_SOL_SIZE)


# ===========================================================================
# 5. All fields from the OpenedPositionRecord are stored correctly
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_fields_match_record():
    """cohort_id / mint / trigger_wallet / entry_ts must match the record."""
    record = _record()
    open_observe_position(record, _ENTRY_PRICE, _config())

    pos = CopytradePosition.objects.get()
    assert pos.cohort_id == record.cohort_id
    assert pos.mint == record.mint
    assert pos.trigger_wallet == record.trigger_wallet
    assert pos.entry_ts == record.entry_ts


# ===========================================================================
# 6. Execution apparatus is NEVER invoked in observe mode (AC-61.1 safety gate)
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_never_invokes_execution_apparatus():
    """In OBSERVE mode, open_observe_position MUST NOT call place_buy_order.

    This is the core AC-61.1 execution-apparatus guard.  We patch
    copytrade.execution.place_buy_order with a MagicMock, call the observe
    opener, then assert the mock was never called.

    'assert the execution apparatus is never invoked in observe mode'
    (AC-61.1 verbatim).
    """
    mock_order = MagicMock()
    with patch("copytrade.execution.place_buy_order", mock_order):
        open_observe_position(_record(), _ENTRY_PRICE, _config())

    mock_order.assert_not_called()


# ===========================================================================
# 7. place_buy_order raises NotImplementedError (P8 gating)
# ===========================================================================


def test_execution_apparatus_raises_not_implemented():
    """copytrade.execution.place_buy_order always raises NotImplementedError.

    The P8 execution path is not yet implemented.  Calling the stub must
    raise NotImplementedError to prevent accidental live-order attempts.
    """
    with pytest.raises(NotImplementedError, match="P8"):
        place_buy_order(mint=_MINT, sol_amount=_SOL_SIZE)


# ===========================================================================
# 8. open_observe_position raises ValueError for mode='live'
# ===========================================================================


@pytest.mark.django_db
def test_observe_open_rejects_live_mode():
    """open_observe_position MUST raise ValueError when mode='live'.

    Live execution is P8-gated; the opener enforces the observe-only gate
    so the live path cannot be reached accidentally.
    """
    live_config = _config(mode="live")

    with pytest.raises(ValueError, match="P8-gated"):
        open_observe_position(_record(), _ENTRY_PRICE, live_config)

    # No position row must have been written.
    assert CopytradePosition.objects.count() == 0
