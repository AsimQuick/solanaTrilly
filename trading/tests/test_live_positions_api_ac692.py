# ---
# module: trading.tests.test_live_positions_api_ac692
# sprint: sprint-13, sprint-15
# story: US-69 AC-69.2, US-90 AC-90.2
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, pytest-django, djangorestframework, trading.models, trading.api
# ---
"""AC-69.2 — Live Positions DRF API tests over banked Position fixtures.

Verifies the two endpoints over a deterministic in-memory Position fixture:

GET /api/trading/positions/open/
  - returns PAPER and OPEN rows with closed_at IS NULL
  - returns both source=model and source=copytrade rows
  - ?source=model filter returns only model rows
  - ?source=copytrade filter returns only copytrade rows
  - no unrealized PnL field, no current price field (zero firehose)

GET /api/trading/positions/closed/
  - returns rows where closed_at IS NOT NULL
  - returns both source=model and source=copytrade rows
  - includes exit_trigger and realized_pnl_pct
  - ?source=model filter returns only model rows
  - ?source=copytrade filter returns only copytrade rows
  - ?limit=1 respects limit param

Banked fixture (zero firehose — all data injected at test time):

  Open positions (3):
    1. source=model,     mode=observe, status=PAPER — MintM1
    2. source=copytrade, mode=observe, status=PAPER — MintC1
    3. source=model,     mode=observe, status=OPEN  — MintM2

  Closed positions (4):
    1. source=model,     mode=observe, exit_trigger=TAKE_PROFIT  — MintM3
    2. source=copytrade, mode=observe, exit_trigger=STOP_LOSS    — MintC2
    3. source=model,     mode=observe, exit_trigger=AUTO_SELL_TIMER — MintM4
    4. source=copytrade, mode=live,    exit_trigger=TRAILING      — MintC3

Test list
---------
  test_open_endpoint_returns_200
  test_open_returns_both_sources
  test_open_contains_model_row
  test_open_contains_copytrade_row
  test_open_excludes_closed_positions
  test_open_filter_by_source_model
  test_open_filter_by_source_copytrade
  test_open_no_unrealized_pnl_field
  test_open_no_current_price_field
  test_open_no_time_held_field
  test_closed_endpoint_returns_200
  test_closed_returns_both_sources
  test_closed_contains_model_row
  test_closed_contains_copytrade_row
  test_closed_excludes_open_positions
  test_closed_filter_by_source_model
  test_closed_filter_by_source_copytrade
  test_closed_has_exit_trigger_field
  test_closed_has_realized_pnl_field
  test_closed_limit_param
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from rest_framework.test import APIClient

from trading.models import Position

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

MINT_M1 = "ModelMint1111111111111111111111111111111111111"
MINT_M2 = "ModelMint2222222222222222222222222222222222222"
MINT_M3 = "ModelMint3333333333333333333333333333333333333"
MINT_M4 = "ModelMint4444444444444444444444444444444444444"
MINT_C1 = "CopytradeMint111111111111111111111111111111111"
MINT_C2 = "CopytradeMint222222222222222222222222222222222"
MINT_C3 = "CopytradeMint333333333333333333333333333333333"

ENTRY_TS = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)
EXIT_TS = datetime(2026, 6, 18, 10, 5, 0, tzinfo=timezone.utc)
CLOSED_AT = datetime(2026, 6, 18, 10, 5, 1, tzinfo=timezone.utc)

ENTRY_PRICE = 0.000001
EXIT_PRICE_TP = 0.000002
EXIT_PRICE_SL = 0.0000008

URL_OPEN = "/api/trading/positions/open/"
URL_CLOSED = "/api/trading/positions/closed/"


# ---------------------------------------------------------------------------
# Pytest fixture — inject banked Position rows
# ---------------------------------------------------------------------------


@pytest.fixture
def banked_positions(db):
    """Inject the deterministic AC-69.2 fixture set into the DB."""
    # --- Open positions ---
    open_rows = [
        Position(
            mint=MINT_M1,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_PAPER,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
        ),
        Position(
            mint=MINT_C1,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_PAPER,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
        ),
        Position(
            mint=MINT_M2,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_OPEN,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
        ),
    ]
    Position.objects.bulk_create(open_rows)

    # --- Closed positions ---
    closed_rows = [
        Position(
            mint=MINT_M3,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=EXIT_PRICE_TP,
            exit_trigger="TAKE_PROFIT",
            realized_pnl_sol=0.1,
            realized_pnl_pct=100.0,
            peak_price=EXIT_PRICE_TP,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_C2,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=EXIT_PRICE_SL,
            exit_trigger="STOP_LOSS",
            realized_pnl_sol=-0.02,
            realized_pnl_pct=-20.0,
            peak_price=ENTRY_PRICE,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_M4,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=ENTRY_PRICE,
            exit_trigger="AUTO_SELL_TIMER",
            realized_pnl_sol=0.0,
            realized_pnl_pct=0.0,
            peak_price=ENTRY_PRICE,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_C3,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_LIVE,
            status=Position.STATUS_CLOSED,
            entry_ts=ENTRY_TS,
            entry_price=ENTRY_PRICE,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=EXIT_PRICE_TP,
            exit_trigger="TRAILING",
            realized_pnl_sol=0.05,
            realized_pnl_pct=50.0,
            peak_price=EXIT_PRICE_TP,
            closed_at=CLOSED_AT,
        ),
    ]
    Position.objects.bulk_create(closed_rows)


@pytest.fixture
def client():
    return APIClient()


# ===========================================================================
# Section 1: Open positions endpoint
# ===========================================================================


@pytest.mark.django_db
def test_open_endpoint_returns_200(client, banked_positions):
    resp = client.get(URL_OPEN)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_open_returns_both_sources(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    sources = {p["source"] for p in data["positions"]}
    assert Position.SOURCE_MODEL in sources
    assert Position.SOURCE_COPYTRADE in sources


@pytest.mark.django_db
def test_open_contains_model_row(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    model_mints = {p["mint"] for p in data["positions"] if p["source"] == Position.SOURCE_MODEL}
    assert MINT_M1 in model_mints


@pytest.mark.django_db
def test_open_contains_copytrade_row(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    ct_mints = {p["mint"] for p in data["positions"] if p["source"] == Position.SOURCE_COPYTRADE}
    assert MINT_C1 in ct_mints


@pytest.mark.django_db
def test_open_excludes_closed_positions(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_M3 not in mints
    assert MINT_C2 not in mints


@pytest.mark.django_db
def test_open_filter_by_source_model(client, banked_positions):
    resp = client.get(URL_OPEN + "?source=model")
    data = resp.json()
    assert all(p["source"] == Position.SOURCE_MODEL for p in data["positions"])
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_M1 in mints
    assert MINT_C1 not in mints


@pytest.mark.django_db
def test_open_filter_by_source_copytrade(client, banked_positions):
    resp = client.get(URL_OPEN + "?source=copytrade")
    data = resp.json()
    assert all(p["source"] == Position.SOURCE_COPYTRADE for p in data["positions"])
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_C1 in mints
    assert MINT_M1 not in mints


@pytest.mark.django_db
def test_open_no_unrealized_pnl_field(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    for pos in data["positions"]:
        assert "unrealized_pnl" not in pos
        assert "unrealized_pnl_pct" not in pos


@pytest.mark.django_db
def test_open_no_current_price_field(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    for pos in data["positions"]:
        assert "current_price" not in pos


@pytest.mark.django_db
def test_open_no_time_held_field(client, banked_positions):
    resp = client.get(URL_OPEN)
    data = resp.json()
    for pos in data["positions"]:
        assert "time_held" not in pos
        assert "held_s" not in pos


# ===========================================================================
# Section 2: Closed positions endpoint
# ===========================================================================


@pytest.mark.django_db
def test_closed_endpoint_returns_200(client, banked_positions):
    resp = client.get(URL_CLOSED)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_closed_returns_both_sources(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    sources = {p["source"] for p in data["positions"]}
    assert Position.SOURCE_MODEL in sources
    assert Position.SOURCE_COPYTRADE in sources


@pytest.mark.django_db
def test_closed_contains_model_row(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    model_mints = {p["mint"] for p in data["positions"] if p["source"] == Position.SOURCE_MODEL}
    assert MINT_M3 in model_mints


@pytest.mark.django_db
def test_closed_contains_copytrade_row(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    ct_mints = {p["mint"] for p in data["positions"] if p["source"] == Position.SOURCE_COPYTRADE}
    assert MINT_C2 in ct_mints


@pytest.mark.django_db
def test_closed_excludes_open_positions(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_M1 not in mints
    assert MINT_C1 not in mints
    assert MINT_M2 not in mints


@pytest.mark.django_db
def test_closed_filter_by_source_model(client, banked_positions):
    resp = client.get(URL_CLOSED + "?source=model")
    data = resp.json()
    assert all(p["source"] == Position.SOURCE_MODEL for p in data["positions"])
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_M3 in mints
    assert MINT_C2 not in mints


@pytest.mark.django_db
def test_closed_filter_by_source_copytrade(client, banked_positions):
    resp = client.get(URL_CLOSED + "?source=copytrade")
    data = resp.json()
    assert all(p["source"] == Position.SOURCE_COPYTRADE for p in data["positions"])
    mints = {p["mint"] for p in data["positions"]}
    assert MINT_C2 in mints
    assert MINT_M3 not in mints


@pytest.mark.django_db
def test_closed_has_exit_trigger_field(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    for pos in data["positions"]:
        assert "exit_trigger" in pos
    triggers = {p["exit_trigger"] for p in data["positions"]}
    assert "TAKE_PROFIT" in triggers
    assert "STOP_LOSS" in triggers


@pytest.mark.django_db
def test_closed_has_realized_pnl_field(client, banked_positions):
    resp = client.get(URL_CLOSED)
    data = resp.json()
    for pos in data["positions"]:
        assert "realized_pnl_pct" in pos
    # the TP trade has positive PnL
    tp_pos = next(p for p in data["positions"] if p["mint"] == MINT_M3)
    assert tp_pos["realized_pnl_pct"] == 100.0
    # the SL trade has negative PnL
    sl_pos = next(p for p in data["positions"] if p["mint"] == MINT_C2)
    assert sl_pos["realized_pnl_pct"] == -20.0


@pytest.mark.django_db
def test_closed_limit_param(client, banked_positions):
    # US-90 AC-90.2: ?limit= is a legacy backward-compat param that caps the
    # returned rows to legacy_limit.  'count' is now the TOTAL matching rows
    # (before the limit cap), so count=4 with the full banked fixture.
    # The positions list is still capped to 1 row.
    resp = client.get(URL_CLOSED + "?limit=1")
    data = resp.json()
    assert len(data["positions"]) == 1
    # count reflects the total matching rows (4 closed in the fixture), not the limit
    assert data["count"] == 4
