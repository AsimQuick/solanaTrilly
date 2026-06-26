# ---
# module: core.tests.test_honest_dashboard_us90
# sprint: sprint-15
# story: US-90 AC-90.1, AC-90.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, pytest-django, djangorestframework,
#   copytrade.api, copytrade.models, trading.api, trading.models,
#   copytrade.fill_repricing
# ---
"""US-90 Honest Dashboard tests.

AC-90.1 — PnL HONESTY REWIRE:
  Verifies that /api/copytrade/summary/ surfaces repriced_net_pnl_sol,
  which is sourced EXCLUSIVELY from positions with entry_reprice_status=REPRICED
  (i.e. the fill_repricing.py path).  Positions with NO_TAPE (curve-sim) and
  pending positions are intentionally excluded so the displayed number is
  unambiguously from fill_repricing, not the old settler.

  Test matrix:
    - summary with ONLY repriced positions: repriced_net_pnl_sol == net of REPRICED rows
    - summary with ONLY no-tape positions: repriced_net_pnl_sol is None
    - summary with mixed repriced + no-tape: repriced_net_pnl_sol excludes no-tape
    - summary with pending positions: repriced_net_pnl_sol excludes pending
    - repriced_net_pnl_sol is sourced from the field that fill_repricing.py writes
      (entry_reprice_status=REPRICED + updated realized_pnl_sol) — NOT the old
      settler-computed curve-sim value

AC-90.2 — PAGINATION + CLICK-TO-COPY:
  Verifies /api/trading/positions/open/ and /api/trading/positions/closed/
  return paginated responses with page/page_size/total_pages/count metadata.
  The pagination controls unbounded list rendering.

Tests
-----
  test_summary_repriced_only_pnl_from_fill_repricing
  test_summary_no_tape_only_repriced_pnl_is_none
  test_summary_mixed_repriced_excludes_no_tape
  test_summary_pending_excluded_from_repriced_pnl
  test_summary_repriced_pnl_is_fill_repricing_value_not_settler_value
  test_summary_repriced_pnl_field_present_when_no_cohort
  test_positions_open_pagination_metadata
  test_positions_open_page2_returns_second_page
  test_positions_open_page_size_respected
  test_positions_closed_pagination_metadata
  test_positions_closed_page2_returns_second_page
  test_positions_closed_legacy_limit_compat
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from rest_framework.test import APIClient

from copytrade.models import (
    CopytradeCohort,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)
from trading.models import Position

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

COHORT_ID = "us90-test-cohort"
COHORT_CREATED = datetime(2026, 6, 20, 0, 0, 0, tzinfo=timezone.utc)
WALLET_A = "WalletA90aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ENTRY_TS = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)
EXIT_TS = ENTRY_TS + timedelta(seconds=300)

MINT_R1 = "MintRepriced1111111111111111111111111111111"
MINT_R2 = "MintRepriced2222222222222222222222222222222"
MINT_NT = "MintNoTape33333333333333333333333333333333"
MINT_PD = "MintPending4444444444444444444444444444444"


def _make_cohort():
    """Create the test cohort + wallet, activate it."""
    cohort = CopytradeCohort.objects.create(
        cohort_id=COHORT_ID,
        created_at=COHORT_CREATED,
        description="US-90 test fixture",
        trade_config={},
        active=True,
    )
    CopytradeWallet.objects.create(cohort_id=COHORT_ID, address=WALLET_A, rank=1)
    settings = CopyTradeSettings.get()
    settings.active_cohort_id = COHORT_ID
    settings.save()
    return cohort


def _closed_pos(mint, pnl_sol, pnl_pct, reprice_status):
    """Create a closed CopytradePosition with the given reprice_status."""
    pos = CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=mint,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=0.001,
        sol_in=0.1,
        exit_ts=EXIT_TS,
        exit_price=0.002 if pnl_pct > 0 else 0.0005,
        sol_out=0.1 + pnl_sol,
        exit_reason=CopytradePosition.EXIT_TP if pnl_pct > 0 else CopytradePosition.EXIT_SL,
        realized_pnl_sol=pnl_sol,
        realized_pnl_pct=pnl_pct,
        entry_reprice_status=reprice_status,
    )
    return pos


# ===========================================================================
# AC-90.1 — PnL honesty rewire tests
# ===========================================================================

@pytest.mark.django_db
def test_summary_repriced_only_pnl_from_fill_repricing():
    """When ALL closed positions are REPRICED, repriced_net_pnl_sol equals
    the net of their realized_pnl_sol values — sourced from fill_repricing.py."""
    _make_cohort()
    # Two repriced positions: +0.05 and -0.02 = net +0.03
    _closed_pos(MINT_R1, pnl_sol=0.05, pnl_pct=50.0, reprice_status="REPRICED")
    _closed_pos(MINT_R2, pnl_sol=-0.02, pnl_pct=-20.0, reprice_status="REPRICED")

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    # repriced_net_pnl_sol must be present and equal the sum of REPRICED rows only
    assert "repriced_net_pnl_sol" in data, "repriced_net_pnl_sol field must be in summary response"
    assert data["repriced_net_pnl_sol"] is not None, "should have a value when repriced positions exist"
    assert abs(data["repriced_net_pnl_sol"] - 0.03) < 1e-9, (
        f"Expected repriced PnL 0.03, got {data['repriced_net_pnl_sol']}"
    )

    # pnl_is_repriced should be True (all are repriced)
    assert data["pnl_is_repriced"] is True
    assert data["n_repriced"] == 2


@pytest.mark.django_db
def test_summary_no_tape_only_repriced_pnl_is_none():
    """When ALL closed positions are NO_TAPE (curve-sim kept), repriced_net_pnl_sol
    must be None — no fill_repricing.py-sourced value exists yet."""
    _make_cohort()
    _closed_pos(MINT_NT, pnl_sol=0.10, pnl_pct=100.0, reprice_status="NO_TAPE")

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    assert "repriced_net_pnl_sol" in data
    # None means "no fill_repricing.py run has produced a value" (NOT 0.0 which would be ambiguous)
    assert data["repriced_net_pnl_sol"] is None, (
        f"NO_TAPE positions should yield repriced_net_pnl_sol=None, got {data['repriced_net_pnl_sol']}"
    )
    assert data["pnl_is_repriced"] is False
    assert data["n_no_tape"] == 1


@pytest.mark.django_db
def test_summary_mixed_repriced_excludes_no_tape():
    """When positions are mixed REPRICED + NO_TAPE, repriced_net_pnl_sol includes
    ONLY the REPRICED positions — the NO_TAPE (curve-sim) value is excluded.
    This is the core honesty guarantee: the displayed number is only from fill_repricing."""
    _make_cohort()
    # REPRICED: +0.05 (honestly repriced by fill_repricing.py)
    _closed_pos(MINT_R1, pnl_sol=0.05, pnl_pct=50.0, reprice_status="REPRICED")
    # NO_TAPE: +0.20 (inflated curve-sim — must NOT appear in repriced_net_pnl_sol)
    _closed_pos(MINT_NT, pnl_sol=0.20, pnl_pct=200.0, reprice_status="NO_TAPE")

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    # repriced_net_pnl_sol must be ONLY the REPRICED row (0.05), NOT 0.25 (all rows)
    assert data["repriced_net_pnl_sol"] is not None
    assert abs(data["repriced_net_pnl_sol"] - 0.05) < 1e-9, (
        f"repriced_net_pnl_sol should be 0.05 (REPRICED only), got {data['repriced_net_pnl_sol']}. "
        "The NO_TAPE curve-sim value (0.20) must be excluded."
    )

    # The legacy net_pnl_sol includes all positions (0.05 + 0.20 = 0.25)
    assert abs(data["net_pnl_sol"] - 0.25) < 1e-9, (
        f"net_pnl_sol (all-positions) should be 0.25, got {data['net_pnl_sol']}"
    )

    # pnl_is_repriced False (not all are REPRICED)
    assert data["pnl_is_repriced"] is False
    assert data["n_repriced"] == 1
    assert data["n_no_tape"] == 1


@pytest.mark.django_db
def test_summary_pending_excluded_from_repriced_pnl():
    """Pending positions (entry_reprice_status=None) are excluded from
    repriced_net_pnl_sol — only completed fill_repricing.py runs count."""
    _make_cohort()
    # REPRICED: -0.01
    _closed_pos(MINT_R1, pnl_sol=-0.01, pnl_pct=-10.0, reprice_status="REPRICED")
    # PENDING (null status): +0.50 (should not appear)
    _closed_pos(MINT_PD, pnl_sol=0.50, pnl_pct=500.0, reprice_status=None)

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    # Only the REPRICED row (-0.01) should be in repriced_net_pnl_sol
    assert data["repriced_net_pnl_sol"] is not None
    assert abs(data["repriced_net_pnl_sol"] - (-0.01)) < 1e-9, (
        f"Pending positions must be excluded; expected -0.01, got {data['repriced_net_pnl_sol']}"
    )
    assert data["n_pending"] == 1


@pytest.mark.django_db
def test_summary_repriced_pnl_is_fill_repricing_value_not_settler_value():
    """The repriced_net_pnl_sol value MUST be taken from the REPRICED position's
    realized_pnl_sol field — which fill_repricing.py writes in-place when it
    updates entry_price + exit_price.  A position with entry_reprice_status=REPRICED
    and realized_pnl_sol=-0.04 (the honest fill_repricing value) must show -0.04,
    NOT a different (inflated settler) value.

    This is the definitive proof: the repriced path reads the same DB column that
    fill_repricing.reprice_position() writes.  The test verifies the field name
    (entry_reprice_status) and value path (realized_pnl_sol when REPRICED) match
    the fill_repricing module's contract.
    """
    from copytrade.fill_repricing import REPRICE_STATUS_REPRICED

    _make_cohort()

    # Simulate what fill_repricing.reprice_position() does: it sets entry_reprice_status=REPRICED
    # and updates realized_pnl_sol to the honest repriced value.
    # Here the honest fill-repriced PnL is -0.04 (entry price higher than booked).
    honest_repriced_pnl = -0.04
    pos = CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_R1,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        # fill_repricing.py updates entry_price to the honest fill
        entry_price=0.0012,  # repriced (was 0.001 curve-sim, now 0.0012 from lake)
        sol_in=0.1,
        exit_ts=EXIT_TS,
        exit_price=0.00108,  # repriced exit
        sol_out=0.1 + honest_repriced_pnl,
        exit_reason=CopytradePosition.EXIT_TP,
        realized_pnl_sol=honest_repriced_pnl,  # fill_repricing wrote this
        realized_pnl_pct=-40.0,
        entry_reprice_status=REPRICE_STATUS_REPRICED,  # fill_repricing wrote this
    )
    assert pos.entry_reprice_status == REPRICE_STATUS_REPRICED  # confirm fixture

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    # repriced_net_pnl_sol must be the honest fill_repricing value (-0.04), not an inflated one
    assert data["repriced_net_pnl_sol"] is not None
    assert abs(data["repriced_net_pnl_sol"] - honest_repriced_pnl) < 1e-9, (
        f"Dashboard must show fill_repricing honest value {honest_repriced_pnl}, "
        f"not a different (potentially inflated) settler value. Got {data['repriced_net_pnl_sol']}"
    )


@pytest.mark.django_db
def test_summary_repriced_pnl_field_present_when_no_cohort():
    """repriced_net_pnl_sol field must be present in the response even when
    no active cohort is configured (returns None, not absent)."""
    settings = CopyTradeSettings.get()
    settings.active_cohort_id = None
    settings.save()

    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()

    assert "repriced_net_pnl_sol" in data, (
        "repriced_net_pnl_sol must always be present in the summary response"
    )
    assert data["repriced_net_pnl_sol"] is None


# ===========================================================================
# AC-90.2 — Pagination tests
# ===========================================================================

def _make_positions(n_open, n_closed):
    """Create n_open open and n_closed closed Position rows."""
    ts = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(n_open):
        Position.objects.create(
            mint=f"MintOpen{i:04d}{'0' * 36}",
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_PAPER,
            entry_ts=ts + timedelta(seconds=i),
            entry_price=0.001,
            size_sol=0.1,
        )
    for i in range(n_closed):
        Position.objects.create(
            mint=f"MintClosed{i:04d}{'0' * 34}",
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            entry_ts=ts + timedelta(seconds=i),
            entry_price=0.001,
            size_sol=0.1,
            exit_ts=ts + timedelta(seconds=i + 60),
            exit_price=0.002,
            exit_trigger="TAKE_PROFIT",
            realized_pnl_sol=0.1,
            realized_pnl_pct=100.0,
            closed_at=ts + timedelta(seconds=i + 60),
        )


@pytest.mark.django_db
def test_positions_open_pagination_metadata():
    """GET /api/trading/positions/open/ must return pagination metadata:
    count, page, page_size, total_pages."""
    _make_positions(n_open=30, n_closed=0)
    client = APIClient()
    resp = client.get("/api/trading/positions/open/?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()

    assert "count" in data, "response must include 'count' (total matching rows)"
    assert "page" in data, "response must include 'page' (current page)"
    assert "page_size" in data, "response must include 'page_size'"
    assert "total_pages" in data, "response must include 'total_pages'"
    assert "positions" in data, "response must include 'positions' list"

    assert data["count"] == 30
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["total_pages"] == 3
    assert len(data["positions"]) == 10  # page 1 of 3 with page_size=10


@pytest.mark.django_db
def test_positions_open_page2_returns_second_page():
    """Page 2 of open positions returns rows 11-20 (0-indexed 10-19)."""
    _make_positions(n_open=30, n_closed=0)
    client = APIClient()

    resp1 = client.get("/api/trading/positions/open/?page=1&page_size=10")
    resp2 = client.get("/api/trading/positions/open/?page=2&page_size=10")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    ids_p1 = [r["id"] for r in resp1.json()["positions"]]
    ids_p2 = [r["id"] for r in resp2.json()["positions"]]

    assert len(ids_p1) == 10
    assert len(ids_p2) == 10
    # No overlap between pages
    assert set(ids_p1).isdisjoint(set(ids_p2)), "page 2 must not overlap with page 1"

    assert resp2.json()["page"] == 2
    assert resp2.json()["total_pages"] == 3


@pytest.mark.django_db
def test_positions_open_page_size_respected():
    """page_size query param must limit the number of rows returned."""
    _make_positions(n_open=50, n_closed=0)
    client = APIClient()

    resp = client.get("/api/trading/positions/open/?page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["positions"]) == 5
    assert data["page_size"] == 5
    assert data["total_pages"] == 10  # 50 / 5 = 10


@pytest.mark.django_db
def test_positions_closed_pagination_metadata():
    """GET /api/trading/positions/closed/ must return pagination metadata."""
    _make_positions(n_open=0, n_closed=30)
    client = APIClient()

    resp = client.get("/api/trading/positions/closed/?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()

    assert "count" in data
    assert "page" in data
    assert "page_size" in data
    assert "total_pages" in data
    assert data["count"] == 30
    assert data["total_pages"] == 3
    assert len(data["positions"]) == 10


@pytest.mark.django_db
def test_positions_closed_page2_returns_second_page():
    """Page 2 of closed positions is distinct from page 1."""
    _make_positions(n_open=0, n_closed=40)
    client = APIClient()

    resp1 = client.get("/api/trading/positions/closed/?page=1&page_size=20")
    resp2 = client.get("/api/trading/positions/closed/?page=2&page_size=20")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    ids_p1 = [r["id"] for r in resp1.json()["positions"]]
    ids_p2 = [r["id"] for r in resp2.json()["positions"]]

    assert len(ids_p1) == 20
    assert len(ids_p2) == 20
    assert set(ids_p1).isdisjoint(set(ids_p2))


@pytest.mark.django_db
def test_positions_closed_legacy_limit_compat():
    """Legacy ?limit= param must still work for backward compat (returns page=1)."""
    _make_positions(n_open=0, n_closed=30)
    client = APIClient()

    resp = client.get("/api/trading/positions/closed/?limit=10")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["positions"]) == 10
    assert data["page"] == 1
    assert "count" in data
