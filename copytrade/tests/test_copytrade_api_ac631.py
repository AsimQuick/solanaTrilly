# ---
# module: copytrade.tests.test_copytrade_api_ac631
# sprint: sprint-12
# story: US-63 AC-63.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, djangorestframework, copytrade.api, copytrade.models
# ---
"""AC-63.1: DRF API tests for the copytrade dashboard backend.

Verifies all 8 endpoints over a banked deterministic copytrade fixture:

GET endpoints:
  /api/copytrade/pnl/       — per-wallet PnL rollup (3 wallets, deterministic PnL)
  /api/copytrade/positions/ — 1 open position
  /api/copytrade/trades/    — 4 closed trades (TP/SL/CURVE/TIMER) with exit_reason + mode
  /api/copytrade/summary/   — total=4, win_rate=0.5, net_pnl=0.20 SOL

POST/action endpoints:
  /api/copytrade/upload/    — triggers SPEC §6 wipe+load (old cohort purged)
  /api/copytrade/engine/    — persists engine_on True/False
  /api/copytrade/mode/      — observe persists; live returns 400 (P8-gated INERT)
  /api/copytrade/overrides/ — persists sol_size / take_profit_pct / stop_loss_pct

Banked fixture (no firehose — all data injected):

  COHORT_ID = "copytrade-ac631-fixture"
  created_at = 2026-06-18

  Wallets: WalletA, WalletB, WalletC

  Closed positions (4):
    1. TP    — WalletA, Mint1, sol_in=0.25, entry=1.0, exit=2.0 → pnl=+0.25
    2. SL    — WalletA, Mint2, sol_in=0.25, entry=1.0, exit=0.5 → pnl=-0.125
    3. CURVE — WalletB, Mint3, sol_in=0.25, entry=1.0, exit=1.5 → pnl=+0.125
    4. TIMER — WalletC, Mint4, sol_in=0.25, entry=1.0, exit=0.8 → pnl=-0.05

  Open position (1):
    WalletA, Mint5

  PnL by wallet:
    WalletA: n=2, wins=1, win_rate=0.5, total_pnl=+0.125, avg_hold=90s
    WalletB: n=1, wins=1, win_rate=1.0, total_pnl=+0.125, avg_hold=90s
    WalletC: n=1, wins=0, win_rate=0.0, total_pnl=-0.05,  avg_hold=180s

  Summary: total_trades=4, win_rate=0.5, net_pnl=+0.20 SOL
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from rest_framework.test import APIClient

from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

COHORT_ID = "copytrade-ac631-fixture"
COHORT_CREATED_AT = datetime(2026, 6, 18, 0, 0, 0, tzinfo=timezone.utc)

WALLET_A = "WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_B = "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
WALLET_C = "WalletCccccccccccccccccccccccccccccccccccccccc"

MINT_1 = "Mint1111111111111111111111111111111111111111"
MINT_2 = "Mint2222222222222222222222222222222222222222"
MINT_3 = "Mint3333333333333333333333333333333333333333"
MINT_4 = "Mint4444444444444444444444444444444444444444"
MINT_5 = "Mint5555555555555555555555555555555555555555"

ENTRY_TS = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)

# exit timestamps (entry + hold_seconds)
EXIT_TS_TP = ENTRY_TS + timedelta(seconds=60)
EXIT_TS_SL = ENTRY_TS + timedelta(seconds=120)
EXIT_TS_CURVE = ENTRY_TS + timedelta(seconds=90)
EXIT_TS_TIMER = ENTRY_TS + timedelta(seconds=180)

# Expected aggregates (derived from fixture):
#   WalletA: TP(+0.25) + SL(-0.125) = +0.125, hold_avg = (60+120)/2 = 90s
#   WalletB: CURVE(+0.125), hold_avg = 90s
#   WalletC: TIMER(-0.05), hold_avg = 180s
#   Net PnL = 0.25 - 0.125 + 0.125 - 0.05 = 0.20

UPLOAD_JSON = {
    "schema_version": "1.0",
    "cohort_id": "copytrade-ac631-new",
    "created_at": "2026-06-18",
    "description": "Upload test cohort",
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
        {"address": "WalletUploadXxxxxxxxxxxxxxxxxxxxxxxxxxxxx1"},
        {"address": "WalletUploadXxxxxxxxxxxxxxxxxxxxxxxxxxxxx2"},
    ],
}


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _build_fixture():
    """Populate the DB with the deterministic AC-63.1 banked fixture."""
    # Cohort
    cohort = CopytradeCohort.objects.create(
        cohort_id=COHORT_ID,
        created_at=COHORT_CREATED_AT,
        description="AC-63.1 test fixture",
        trade_config={},
        active=True,
    )

    # Wallets
    CopytradeWallet.objects.bulk_create(
        [
            CopytradeWallet(cohort_id=COHORT_ID, address=WALLET_A, rank=1),
            CopytradeWallet(cohort_id=COHORT_ID, address=WALLET_B, rank=2),
            CopytradeWallet(cohort_id=COHORT_ID, address=WALLET_C, rank=3),
        ]
    )

    # Closed positions (4) — each with pre-computed PnL
    # 1. TP: exit_price=2.0, sol_out=0.50, pnl=+0.25
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_1,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=EXIT_TS_TP,
        exit_price=2.0,
        sol_out=0.50,
        exit_reason=CopytradePosition.EXIT_TP,
        realized_pnl_sol=0.25,
        realized_pnl_pct=100.0,
    )
    # 2. SL: exit_price=0.5, sol_out=0.125, pnl=-0.125
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_2,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=EXIT_TS_SL,
        exit_price=0.5,
        sol_out=0.125,
        exit_reason=CopytradePosition.EXIT_SL,
        realized_pnl_sol=-0.125,
        realized_pnl_pct=-50.0,
    )
    # 3. CURVE: exit_price=1.5, sol_out=0.375, pnl=+0.125
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_3,
        trigger_wallet=WALLET_B,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=EXIT_TS_CURVE,
        exit_price=1.5,
        sol_out=0.375,
        exit_reason=CopytradePosition.EXIT_CURVE,
        realized_pnl_sol=0.125,
        realized_pnl_pct=50.0,
    )
    # 4. TIMER: exit_price=0.8, sol_out=0.20, pnl=-0.05
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_4,
        trigger_wallet=WALLET_C,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=EXIT_TS_TIMER,
        exit_price=0.8,
        sol_out=0.20,
        exit_reason=CopytradePosition.EXIT_TIMER,
        realized_pnl_sol=-0.05,
        realized_pnl_pct=-20.0,
    )

    # Open position (1)
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_5,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
    )

    # PnL by wallet (pre-computed rollup)
    # WalletA: TP(hold=60s) + SL(hold=120s) → avg=90s, wins=1, total=+0.125
    CopytradePnlByWallet.objects.create(
        cohort_id=COHORT_ID,
        address=WALLET_A,
        n_trades=2,
        win_rate=0.5,
        total_pnl_sol=0.125,
        avg_hold_s=90.0,
    )
    # WalletB: CURVE(hold=90s) → wins=1, total=+0.125
    CopytradePnlByWallet.objects.create(
        cohort_id=COHORT_ID,
        address=WALLET_B,
        n_trades=1,
        win_rate=1.0,
        total_pnl_sol=0.125,
        avg_hold_s=90.0,
    )
    # WalletC: TIMER(hold=180s) → wins=0, total=-0.05
    CopytradePnlByWallet.objects.create(
        cohort_id=COHORT_ID,
        address=WALLET_C,
        n_trades=1,
        win_rate=0.0,
        total_pnl_sol=-0.05,
        avg_hold_s=180.0,
    )

    # Settings singleton pointing to this cohort
    CopyTradeSettings.get()  # ensure singleton exists
    CopyTradeSettings.objects.filter(pk=1).update(
        active_cohort_id=COHORT_ID,
        engine_on=False,
        mode="observe",
    )

    return cohort


# ---------------------------------------------------------------------------
# GET /api/copytrade/pnl/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_pnl_returns_three_wallet_rows():
    """PnL endpoint returns one row per wallet in the active cohort."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/pnl/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] == COHORT_ID
    assert len(data["wallets"]) == 3


@pytest.mark.django_db
def test_get_pnl_sorted_by_pnl_descending():
    """PnL rows are ordered by total_pnl_sol descending (best wallets first)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/pnl/")
    wallets = resp.json()["wallets"]
    pnl_values = [w["total_pnl_sol"] for w in wallets]
    assert pnl_values == sorted(pnl_values, reverse=True)


@pytest.mark.django_db
def test_get_pnl_deterministic_wallet_a_values():
    """WalletA row has deterministic PnL from banked fixture (n=2, win_rate=0.5)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/pnl/")
    wallets = {w["address"]: w for w in resp.json()["wallets"]}
    wa = wallets[WALLET_A]
    assert wa["n_trades"] == 2
    assert wa["win_rate"] == 0.5
    assert abs(wa["total_pnl_sol"] - 0.125) < 1e-9


@pytest.mark.django_db
def test_get_pnl_no_active_cohort_returns_empty():
    """No active cohort → empty wallets list."""
    CopyTradeSettings.get()  # ensure singleton exists
    client = APIClient()
    resp = client.get("/api/copytrade/pnl/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] is None
    assert data["wallets"] == []


# ---------------------------------------------------------------------------
# GET /api/copytrade/positions/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_open_positions_returns_one():
    """Open positions endpoint returns the single open position."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/positions/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] == COHORT_ID
    assert len(data["positions"]) == 1
    pos = data["positions"][0]
    assert pos["mint"] == MINT_5
    assert pos["trigger_wallet"] == WALLET_A
    assert pos["mode"] == "observe"


@pytest.mark.django_db
def test_get_open_positions_no_cohort():
    """No active cohort → empty positions list."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.get("/api/copytrade/positions/")
    assert resp.status_code == 200
    assert resp.json()["positions"] == []


# ---------------------------------------------------------------------------
# GET /api/copytrade/trades/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_trades_returns_four_closed():
    """Trades endpoint returns all 4 closed positions."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/trades/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] == COHORT_ID
    assert len(data["trades"]) == 4


@pytest.mark.django_db
def test_get_trades_all_exit_reasons_present():
    """All four exit reasons (TP, SL, CURVE, TIMER) appear in the trades."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/trades/")
    reasons = {t["exit_reason"] for t in resp.json()["trades"]}
    assert reasons == {"TP", "SL", "CURVE", "TIMER"}


@pytest.mark.django_db
def test_get_trades_includes_mode_field():
    """Each trade row includes the mode field (observe/live)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/trades/")
    for trade in resp.json()["trades"]:
        assert "mode" in trade
        assert trade["mode"] == "observe"


@pytest.mark.django_db
def test_get_trades_includes_pnl_fields():
    """Each trade includes realized_pnl_sol and exit_reason."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/trades/")
    for trade in resp.json()["trades"]:
        assert "realized_pnl_sol" in trade
        assert "exit_reason" in trade
        assert trade["exit_reason"] is not None


@pytest.mark.django_db
def test_get_trades_no_cohort():
    """No active cohort → empty trades list."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.get("/api/copytrade/trades/")
    assert resp.status_code == 200
    assert resp.json()["trades"] == []


# ---------------------------------------------------------------------------
# GET /api/copytrade/summary/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_summary_total_trades():
    """Summary total_trades equals 4 (the four closed positions)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] == COHORT_ID
    assert data["total_trades"] == 4


@pytest.mark.django_db
def test_get_summary_win_rate():
    """Summary win_rate is 0.5 (2 wins out of 4 — TP and CURVE are positive)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    data = resp.json()
    assert abs(data["win_rate"] - 0.5) < 1e-9


@pytest.mark.django_db
def test_get_summary_net_pnl():
    """Summary net_pnl_sol is +0.20 SOL (0.25-0.125+0.125-0.05=0.20)."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    data = resp.json()
    assert abs(data["net_pnl_sol"] - 0.20) < 1e-9


@pytest.mark.django_db
def test_get_summary_since_matches_cohort_created_at():
    """Summary 'since' field matches the cohort's created_at timestamp."""
    _build_fixture()
    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    data = resp.json()
    assert data["since"] is not None
    assert "2026-06-18" in data["since"]


@pytest.mark.django_db
def test_get_summary_no_cohort_returns_zeros():
    """No active cohort → zero totals, engine_on and mode still present."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.get("/api/copytrade/summary/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cohort_id"] is None
    assert data["total_trades"] == 0
    assert data["win_rate"] == 0.0
    assert data["net_pnl_sol"] == 0.0
    assert "engine_on" in data
    assert "mode" in data


# ---------------------------------------------------------------------------
# POST /api/copytrade/upload/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_upload_triggers_wipe_and_loads_new_cohort():
    """Upload triggers SPEC §6 wipe: old cohort purged, new cohort active."""
    _build_fixture()  # builds COHORT_ID as active with positions
    client = APIClient()
    resp = client.post("/api/copytrade/upload/", data=UPLOAD_JSON, format="json")
    assert resp.status_code == 201
    data = resp.json()
    assert data["cohort_id"] == "copytrade-ac631-new"
    assert data["active"] is True
    assert data["status"] == "loaded"


@pytest.mark.django_db
def test_post_upload_old_cohort_records_purged():
    """After upload the original cohort's records no longer exist."""
    _build_fixture()
    client = APIClient()
    client.post("/api/copytrade/upload/", data=UPLOAD_JSON, format="json")

    assert CopytradeCohort.objects.filter(cohort_id=COHORT_ID).count() == 0
    assert CopytradeWallet.objects.filter(cohort_id=COHORT_ID).count() == 0
    assert CopytradePosition.objects.filter(cohort_id=COHORT_ID).count() == 0
    assert CopytradePnlByWallet.objects.filter(cohort_id=COHORT_ID).count() == 0


@pytest.mark.django_db
def test_post_upload_exactly_one_active_cohort():
    """After upload exactly one cohort is active."""
    _build_fixture()
    client = APIClient()
    client.post("/api/copytrade/upload/", data=UPLOAD_JSON, format="json")
    assert CopytradeCohort.objects.filter(active=True).count() == 1


@pytest.mark.django_db
def test_post_upload_invalid_json_returns_400():
    """Upload with missing cohort_id returns 400 with error message."""
    CopyTradeSettings.get()
    bad_payload = dict(UPLOAD_JSON)
    bad_payload.pop("cohort_id")
    client = APIClient()
    resp = client.post("/api/copytrade/upload/", data=bad_payload, format="json")
    assert resp.status_code == 400
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/copytrade/engine/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_engine_on_persists():
    """POST engine/ with engine_on=true sets engine_on=True in DB."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post("/api/copytrade/engine/", data={"engine_on": True}, format="json")
    assert resp.status_code == 200
    assert resp.json()["engine_on"] is True
    assert CopyTradeSettings.get().engine_on is True


@pytest.mark.django_db
def test_post_engine_off_persists():
    """POST engine/ with engine_on=false sets engine_on=False in DB."""
    CopyTradeSettings.get()  # ensure singleton exists
    CopyTradeSettings.objects.filter(pk=1).update(engine_on=True)
    client = APIClient()
    resp = client.post("/api/copytrade/engine/", data={"engine_on": False}, format="json")
    assert resp.status_code == 200
    assert resp.json()["engine_on"] is False
    assert CopyTradeSettings.get().engine_on is False


@pytest.mark.django_db
def test_post_engine_missing_field_returns_400():
    """POST engine/ without engine_on field returns 400."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post("/api/copytrade/engine/", data={}, format="json")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/copytrade/mode/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_mode_observe_persists():
    """POST mode/ with observe sets mode in CopyTradeSettings."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post("/api/copytrade/mode/", data={"mode": "observe"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "observe"
    assert CopyTradeSettings.get().mode == "observe"


@pytest.mark.django_db
def test_post_mode_live_is_inert_returns_400():
    """POST mode/ with live is P8-gated — returns 400 with guard message."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post("/api/copytrade/mode/", data={"mode": "live"}, format="json")
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data
    assert data.get("mode_unchanged") is True
    assert "P8" in data["error"]


@pytest.mark.django_db
def test_post_mode_live_does_not_change_stored_mode():
    """After a live mode attempt, stored mode is still observe."""
    CopyTradeSettings.get()
    client = APIClient()
    client.post("/api/copytrade/mode/", data={"mode": "live"}, format="json")
    assert CopyTradeSettings.get().mode == "observe"


@pytest.mark.django_db
def test_post_mode_missing_field_returns_400():
    """POST mode/ without mode field returns 400."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post("/api/copytrade/mode/", data={}, format="json")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/copytrade/overrides/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_overrides_persist_all_three_fields():
    """Override endpoint persists sol_size, take_profit_pct, stop_loss_pct."""
    CopyTradeSettings.get()
    client = APIClient()
    payload = {
        "sol_size_per_trade": 0.5,
        "take_profit_pct": 150.0,
        "stop_loss_pct": 30.0,
    }
    resp = client.post("/api/copytrade/overrides/", data=payload, format="json")
    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["sol_size_per_trade"] - 0.5) < 1e-9
    assert abs(data["take_profit_pct"] - 150.0) < 1e-9
    assert abs(data["stop_loss_pct"] - 30.0) < 1e-9

    s = CopyTradeSettings.get()
    assert abs(s.sol_size_per_trade - 0.5) < 1e-9
    assert abs(s.take_profit_pct - 150.0) < 1e-9
    assert abs(s.stop_loss_pct - 30.0) < 1e-9


@pytest.mark.django_db
def test_post_overrides_partial_update_only_changed_fields():
    """Providing only sol_size_per_trade leaves other fields unchanged."""
    CopyTradeSettings.get()
    # Set known values first
    CopyTradeSettings.objects.filter(pk=1).update(
        take_profit_pct=200.0, stop_loss_pct=40.0
    )
    client = APIClient()
    resp = client.post(
        "/api/copytrade/overrides/",
        data={"sol_size_per_trade": 0.75},
        format="json",
    )
    assert resp.status_code == 200
    s = CopyTradeSettings.get()
    assert abs(s.sol_size_per_trade - 0.75) < 1e-9
    assert abs(s.take_profit_pct - 200.0) < 1e-9
    assert abs(s.stop_loss_pct - 40.0) < 1e-9


@pytest.mark.django_db
def test_post_overrides_invalid_value_rejected():
    """Out-of-range sol_size_per_trade (<=0) is rejected by Pydantic gate."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post(
        "/api/copytrade/overrides/",
        data={"sol_size_per_trade": -1.0},
        format="json",
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


@pytest.mark.django_db
def test_post_overrides_unknown_field_rejected():
    """Unknown fields in override body return 400."""
    CopyTradeSettings.get()
    client = APIClient()
    resp = client.post(
        "/api/copytrade/overrides/",
        data={"unknown_field": 99.0},
        format="json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# H1 import traps — CI collection fails if view functions are deleted/renamed
# ---------------------------------------------------------------------------


def test_h1_import_trap_all_views():
    """H1 import trap: all 8 view functions must be importable from copytrade.api."""
    from copytrade.api import (  # noqa: F401
        copytrade_engine_view,
        copytrade_mode_view,
        copytrade_overrides_view,
        copytrade_pnl_view,
        copytrade_positions_view,
        copytrade_summary_view,
        copytrade_trades_view,
        copytrade_upload_view,
    )


def test_h1_import_trap_urls():
    """H1 import trap: copytrade.urls must be importable."""
    import copytrade.urls  # noqa: F401
