# ---
# module: trading.tests.test_shared_position_parity_ac681
# sprint: sprint-13
# story: US-68 AC-68.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, copytrade.position_opener,
#   copytrade.position_manager, copytrade.models, copytrade.schemas,
#   copytrade.trigger_pipeline, trading.models, trading.position_closer
# ---
"""AC-68.1: Shared execution chassis parity tests.

Verifies that BOTH pipelines flow through the SHARED US-64 Position model
(SPEC §0.1 'same execution path' parity):

  §1 — open_observe_position creates a shared trading.Position row
  §2 — close_position settles the shared trading.Position row
  §3 — copytrade observe and model paper positions produce equivalent shared
        Position rows + settle identically (the core parity assertion)
  §4 — §5 isolation guards: copytrade imports stay clean; shared_position_id
        linkage uses IntegerField (no FK / no Django cascade)

Tests:

  §1 Shared Position creation:
    1. open_observe_position writes a trading.Position with source='copytrade'
    2. shared Position has correct mode, status, mint, entry_price, size_sol
    3. CopytradePosition.shared_position_id is set to the new Position pk

  §2 Shared Position settlement:
    4. close_position writes exit fields to the linked trading.Position
    5. closed_at IS NOT NULL sentinel is set on the shared Position
    6. exit_trigger on shared Position matches CopytradePosition.exit_reason
    7. realized_pnl_pct on shared Position matches CopytradePosition value

  §3 Parity: copytrade observe == model paper (same field shape after settle):
    8.  Both produce status='CLOSED' on their shared Position row
    9.  Both produce closed_at IS NOT NULL on their shared Position row
    10. Both shared Position rows carry the same 5 realized field names
    11. closed_at sentinel query finds BOTH settled rows

  §4 Isolation / wiring:
    12. close_observe_position is importable from trading.position_closer (H1)
    13. CopytradePosition.shared_position_id is IntegerField (no FK — §5 isolation)
    14. shared Position source='copytrade' (never source='model') for CT path
    15. opening without going through shared path leaves shared_position_id null

All tests are offline (no firehose, no network, no live price feed).
DB tests use pytest.mark.django_db; non-DB tests are plain functions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from copytrade.models import CopytradePosition
from copytrade.position_manager import close_position
from copytrade.position_opener import open_observe_position
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord
from trading.models import Position
from trading.position_closer import settle_paper_position

# ---------------------------------------------------------------------------
# Constants for deterministic replay
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(seconds=120)

_COHORT_CT = "cohort-ac681-copytrade"
_MINT_CT = "MintCT681PumpXXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_MINT_MODEL = "MintMD681ModelXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_WALLET = "Wa11etAC681AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_ENTRY_PRICE = 0.000_015_000
_SOL_SIZE = 0.25
_EXIT_PRICE_TP = _ENTRY_PRICE * 3.0  # +200% — clear TP
_SETTLER_PNL_PCT = 200.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ct_record(mint: str = _MINT_CT) -> OpenedPositionRecord:
    return OpenedPositionRecord(
        cohort_id=_COHORT_CT,
        mint=mint,
        trigger_wallet=_WALLET,
        entry_ts=_T0,
    )


def _ct_config() -> CopyTradeConfig:
    return CopyTradeConfig(
        mode="observe",
        sol_size_per_trade=_SOL_SIZE,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        exit_before_graduation=True,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=3600,
        max_concurrent_positions=20,
        mirror_wallet_sells=False,
    )


def _model_settler_result(pnl_pct: float = _SETTLER_PNL_PCT) -> dict:
    """Minimal tape settler result for a model paper position."""
    return {
        "enterable": True,
        "pnl": pnl_pct,
        "trigger": "TAKE_PROFIT_PCT",
        "held": 120.0,
        "peak": pnl_pct + 10.0,
        "flow": 500.0,
    }


# ===========================================================================
# §1 — open_observe_position creates a shared trading.Position row
# ===========================================================================


@pytest.mark.django_db
def test_open_observe_creates_shared_position_row():
    """open_observe_position must write exactly one shared trading.Position row.

    The shared Position has source='copytrade' (AC-68.1 shared chassis).
    """
    assert Position.objects.count() == 0

    open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())

    assert Position.objects.count() == 1
    shared = Position.objects.get()
    assert shared.source == Position.SOURCE_COPYTRADE


@pytest.mark.django_db
def test_open_observe_shared_position_fields_correct():
    """The shared Position must carry the correct mode, status, mint, entry fields."""
    open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())

    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.mode == Position.MODE_OBSERVE
    assert shared.status == Position.STATUS_PAPER
    assert shared.mint == _MINT_CT
    assert shared.entry_price == pytest.approx(_ENTRY_PRICE)
    assert shared.size_sol == pytest.approx(_SOL_SIZE)
    assert shared.entry_ts == _T0


@pytest.mark.django_db
def test_open_observe_shared_position_id_stored_on_copytrade_position():
    """CopytradePosition.shared_position_id must point to the new Position pk."""
    open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())

    ct_pos = CopytradePosition.objects.get()
    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)

    assert ct_pos.shared_position_id is not None
    assert ct_pos.shared_position_id == shared.pk


# ===========================================================================
# §2 — close_position settles the shared trading.Position row
# ===========================================================================


@pytest.mark.django_db
def test_close_position_settles_shared_position_exit_fields():
    """close_position must write exit_price and exit_trigger to the shared Position."""
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)

    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.exit_price == pytest.approx(_EXIT_PRICE_TP)
    assert shared.exit_trigger == CopytradePosition.EXIT_TP
    assert shared.exit_ts == _T1


@pytest.mark.django_db
def test_close_position_shared_position_sentinel_set():
    """closed_at IS NOT NULL must be set on the shared Position after close."""
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)

    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.closed_at is not None
    assert shared.status == Position.STATUS_CLOSED


@pytest.mark.django_db
def test_close_position_shared_exit_trigger_matches_copytrade_exit_reason():
    """shared Position.exit_trigger must equal CopytradePosition.exit_reason."""
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_SL, _ENTRY_PRICE * 0.5, _T1)

    ct_pos.refresh_from_db()
    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.exit_trigger == ct_pos.exit_reason == CopytradePosition.EXIT_SL


@pytest.mark.django_db
def test_close_position_shared_realized_pnl_pct_matches():
    """shared Position.realized_pnl_pct must equal CopytradePosition.realized_pnl_pct."""
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)

    ct_pos.refresh_from_db()
    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.realized_pnl_pct == pytest.approx(ct_pos.realized_pnl_pct, rel=1e-5)


# ===========================================================================
# §3 — Parity: copytrade observe == model paper (same realized field shape)
# ===========================================================================


@pytest.mark.django_db
def test_both_pipelines_produce_closed_status_on_shared_position():
    """Both copytrade and model positions must show status='CLOSED' after settling."""
    # --- Copytrade observe position ---
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)

    # --- Model paper position (direct Position creation + tape settler) ---
    model_pos = Position.objects.create(
        mint=_MINT_MODEL,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=_T0,
        entry_price=_ENTRY_PRICE,
        size_sol=_SOL_SIZE,
    )
    settle_paper_position(model_pos, _model_settler_result(), now=_T1)

    ct_shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    md_shared = Position.objects.get(source=Position.SOURCE_MODEL)

    assert ct_shared.status == Position.STATUS_CLOSED
    assert md_shared.status == Position.STATUS_CLOSED


@pytest.mark.django_db
def test_both_pipelines_produce_closed_at_not_null_sentinel():
    """Both copytrade and model shared Positions must have closed_at IS NOT NULL."""
    # Copytrade path
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)

    # Model paper path
    model_pos = Position.objects.create(
        mint=_MINT_MODEL,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=_T0,
        entry_price=_ENTRY_PRICE,
        size_sol=_SOL_SIZE,
    )
    settle_paper_position(model_pos, _model_settler_result(), now=_T1)

    # The sentinel query (closed_at IS NOT NULL) must find BOTH rows
    settled = list(Position.objects.filter(closed_at__isnull=False))
    assert len(settled) == 2


@pytest.mark.django_db
def test_both_pipelines_produce_equivalent_realized_field_shape():
    """Both pipelines write the same 5 realized fields to their shared Position.

    This is the core AC-68.1 parity assertion: after settlement, both a
    copytrade observe position and a model paper position carry an identical
    realized field structure — exit_price, exit_trigger, realized_pnl_pct,
    exit_ts, closed_at all populated; status='CLOSED'.

    The SPEC §0.1 'same execution chassis' means the STRUCTURE is identical;
    the values naturally differ (different mints, different exit prices).
    """
    # Copytrade path
    ct_pos = open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    close_position(ct_pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)
    ct_shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)

    # Model paper path
    model_pos = Position.objects.create(
        mint=_MINT_MODEL,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=_T0,
        entry_price=_ENTRY_PRICE,
        size_sol=_SOL_SIZE,
    )
    settle_paper_position(model_pos, _model_settler_result(), now=_T1)
    model_pos.refresh_from_db()

    def _realized_field_shape(pos: Position) -> dict:
        """Extract the realized field structure (names → populated or null)."""
        return {
            "exit_price_set": pos.exit_price is not None,
            "exit_trigger_set": pos.exit_trigger is not None,
            "realized_pnl_pct_set": pos.realized_pnl_pct is not None,
            "exit_ts_set": pos.exit_ts is not None,
            "closed_at_set": pos.closed_at is not None,
            "status": pos.status,
        }

    ct_shape = _realized_field_shape(ct_shared)
    md_shape = _realized_field_shape(model_pos)

    assert ct_shape == md_shape, (
        f"Copytrade and model realized field shapes differ:\n"
        f"  copytrade: {ct_shape}\n"
        f"  model:     {md_shape}"
    )


# ===========================================================================
# §4 — Isolation / wiring guards
# ===========================================================================


def test_close_observe_position_importable():
    """close_observe_position is importable from trading.position_closer (H1 guard)."""
    from trading.position_closer import close_observe_position as _cop

    assert callable(_cop), "close_observe_position must be callable"


def test_shared_position_id_is_integer_field_not_fk():
    """CopytradePosition.shared_position_id must be an IntegerField (not FK).

    §5 isolation: copytrade must not declare a ForeignKey into the trading
    namespace — a plain IntegerField preserves §5 isolation (no Django cascade
    semantics, no cross-app FK constraint).
    """
    from django.db.models import ForeignKey, IntegerField

    field = CopytradePosition._meta.get_field("shared_position_id")
    assert isinstance(field, IntegerField), (
        f"shared_position_id must be IntegerField, got {type(field).__name__}"
    )
    assert not isinstance(field, ForeignKey), (
        "shared_position_id must NOT be a ForeignKey (§5 isolation)"
    )


@pytest.mark.django_db
def test_open_observe_shared_position_source_is_copytrade():
    """The shared Position written by copytrade must always have source='copytrade'."""
    open_observe_position(_ct_record(), _ENTRY_PRICE, _ct_config())
    shared = Position.objects.get()
    assert shared.source == Position.SOURCE_COPYTRADE


@pytest.mark.django_db
def test_direct_copytrade_position_creation_has_null_shared_position_id():
    """CopytradePosition created directly (not via open_observe_position) has null shared_position_id.

    Confirms the refactoring is additive: existing code that creates
    CopytradePosition directly still works, and close_position gracefully
    skips the shared-position settlement when shared_position_id is None.
    """
    pos = CopytradePosition(
        cohort_id=_COHORT_CT,
        mint=_MINT_CT,
        trigger_wallet=_WALLET,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=_T0,
        entry_price=_ENTRY_PRICE,
        sol_in=_SOL_SIZE,
        # shared_position_id intentionally omitted — simulates pre-AC-68.1 row
    )
    pos.save()

    assert pos.shared_position_id is None

    # close_position must not raise even when shared_position_id is None
    closed = close_position(pos, CopytradePosition.EXIT_TP, _EXIT_PRICE_TP, _T1)
    assert closed.status == CopytradePosition.STATUS_CLOSED

    # No shared Position row should have been written
    assert Position.objects.count() == 0
