# ---
# module: trading.tests.test_analytics_api_ac721
# sprint: sprint-14
# story: US-72 AC-72.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, djangorestframework, trading.models, trading.analytics_api
# ---
"""AC-72.1 — Calibration & PnL analytics DRF API tests over banked Position fixtures.

Verifies GET /api/trading/analytics/calibration-pnl/ over a deterministic in-memory
Position fixture:

  1. win_rate_by_score_band   — 10 bands; win rate computed correctly; H4 zero-guard
  2. pnl_by_exit_trigger      — all closed positions grouped by trigger; H4 zero-guard
  3. scatter_points           — (score, realized_pnl_pct) pairs for scored positions
  4. calibration_curve        — bucket_mid vs actual_win_rate; H4 zero-guard

Key assertions per AC-72.1:
  - Both source=model and source=copytrade rows are included
  - Empty set returns empty/zero-guarded response (no division-by-zero)
  - No live Birdeye call (zero firehose — all data injected at test time)
  - No new PnL math (all pnl values come from the injected rows verbatim)

Banked fixture (all injected, zero firehose):

  Closed positions (6):
    M1  source=model,     score=0.85, trigger=TAKE_PROFIT,    pnl_pct=+80.0  → WIN
    M2  source=model,     score=0.85, trigger=STOP_LOSS,      pnl_pct=-20.0  → LOSS
    M3  source=model,     score=0.25, trigger=STOP_LOSS,      pnl_pct=-15.0  → LOSS
    C1  source=copytrade, score=0.75, trigger=TRAILING,       pnl_pct=+40.0  → WIN
    C2  source=copytrade, score=None, trigger=AUTO_SELL_TIMER,pnl_pct=0.0    → (no score)
    C3  source=copytrade, score=0.35, trigger=TAKE_PROFIT,    pnl_pct=+10.0  → WIN

  Open positions (2 — must NOT appear in analytics):
    M4  source=model,     score=0.90, OPEN  (closed_at IS NULL)
    C4  source=copytrade, score=None, PAPER (closed_at IS NULL)

Test list
---------
  §1 Endpoint basics
    test_endpoint_returns_200
    test_response_has_required_top_level_keys
    test_total_closed_count_is_correct

  §2 source coverage
    test_both_sources_reflected_in_scatter_points
    test_model_source_row_in_scatter
    test_copytrade_source_row_in_scatter

  §3 open positions excluded
    test_open_positions_excluded_from_total_closed
    test_open_position_mint_not_in_scatter

  §4 win_rate_by_score_band
    test_score_band_list_has_10_entries
    test_score_band_labels_cover_full_range
    test_high_band_has_correct_win_rate
    test_low_band_has_correct_win_rate
    test_mid_band_correct_counts

  §5 pnl_by_exit_trigger
    test_pnl_trigger_includes_all_triggers
    test_take_profit_avg_pnl_correct
    test_stop_loss_total_pnl_correct
    test_trailing_trigger_present
    test_both_sources_in_pnl_by_trigger

  §6 scatter_points
    test_scatter_only_includes_scored_rows
    test_no_null_score_in_scatter
    test_scatter_values_match_fixture
    test_scatter_no_new_pnl_math

  §7 calibration_curve
    test_calibration_curve_has_10_buckets
    test_calibration_bucket_mids_correct
    test_calibration_high_bucket_win_rate
    test_calibration_zero_count_bucket_win_rate_is_zero

  §8 empty-set (H4 zero-guard)
    test_empty_db_returns_200
    test_empty_db_no_division_by_zero
    test_empty_db_zero_total_closed
    test_empty_db_score_bands_all_zero_win_rate
    test_empty_db_calibration_all_zero_actual_win_rate
    test_empty_db_pnl_trigger_empty_list
    test_empty_db_scatter_empty_list
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from rest_framework.test import APIClient

from trading.models import Position

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

ENTRY_TS = datetime(2026, 6, 19, 10, 0, 0, tzinfo=timezone.utc)
EXIT_TS = datetime(2026, 6, 19, 10, 5, 0, tzinfo=timezone.utc)
CLOSED_AT = datetime(2026, 6, 19, 10, 5, 1, tzinfo=timezone.utc)

# Mints for closed positions
MINT_M1 = "ModelMint1111111111111111111111111111111111111"  # score=0.85 WIN
MINT_M2 = "ModelMint2222222222222222222222222222222222222"  # score=0.85 LOSS
MINT_M3 = "ModelMint3333333333333333333333333333333333333"  # score=0.25 LOSS
MINT_C1 = "CopytradeMint111111111111111111111111111111111"  # score=0.75 WIN
MINT_C2 = "CopytradeMint222222222222222222222222222222222"  # score=None (no score)
MINT_C3 = "CopytradeMint333333333333333333333333333333333"  # score=0.35 WIN

# Mints for open positions (must be excluded)
MINT_M4 = "ModelMint4444444444444444444444444444444444444"  # open, score=0.90
MINT_C4 = "CopytradeMint444444444444444444444444444444444"  # paper, score=None

URL = "/api/trading/analytics/calibration-pnl/"


@pytest.fixture
def banked_positions(db):
    """Inject the deterministic AC-72.1 fixture set into the DB.

    6 closed positions (both sources, mix of scores and triggers).
    2 open/paper positions (must not appear in analytics output).
    All data is hard-coded — zero firehose, no live calls.
    """
    closed = [
        Position(
            mint=MINT_M1,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=0.85,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.0000018,
            exit_trigger="TAKE_PROFIT",
            realized_pnl_sol=0.08,
            realized_pnl_pct=80.0,
            peak_price=0.0000018,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_M2,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=0.85,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.0000008,
            exit_trigger="STOP_LOSS",
            realized_pnl_sol=-0.02,
            realized_pnl_pct=-20.0,
            peak_price=0.000001,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_M3,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=0.25,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.00000085,
            exit_trigger="STOP_LOSS",
            realized_pnl_sol=-0.015,
            realized_pnl_pct=-15.0,
            peak_price=0.000001,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_C1,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=0.75,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.0000014,
            exit_trigger="TRAILING",
            realized_pnl_sol=0.04,
            realized_pnl_pct=40.0,
            peak_price=0.0000014,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_C2,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=None,  # copytrade row with no model score
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.000001,
            exit_trigger="AUTO_SELL_TIMER",
            realized_pnl_sol=0.0,
            realized_pnl_pct=0.0,
            peak_price=0.000001,
            closed_at=CLOSED_AT,
        ),
        Position(
            mint=MINT_C3,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_CLOSED,
            score=0.35,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
            exit_ts=EXIT_TS,
            exit_price=0.0000011,
            exit_trigger="TAKE_PROFIT",
            realized_pnl_sol=0.01,
            realized_pnl_pct=10.0,
            peak_price=0.0000011,
            closed_at=CLOSED_AT,
        ),
    ]
    Position.objects.bulk_create(closed)

    open_rows = [
        Position(
            mint=MINT_M4,
            source=Position.SOURCE_MODEL,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_OPEN,
            score=0.90,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
        ),
        Position(
            mint=MINT_C4,
            source=Position.SOURCE_COPYTRADE,
            mode=Position.MODE_OBSERVE,
            status=Position.STATUS_PAPER,
            score=None,
            entry_ts=ENTRY_TS,
            entry_price=0.000001,
            size_sol=0.1,
        ),
    ]
    Position.objects.bulk_create(open_rows)


@pytest.fixture
def client():
    return APIClient()


# ===========================================================================
# §1 Endpoint basics
# ===========================================================================


@pytest.mark.django_db
def test_endpoint_returns_200(client, banked_positions):
    resp = client.get(URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_response_has_required_top_level_keys(client, banked_positions):
    data = client.get(URL).json()
    assert "total_closed" in data
    assert "win_rate_by_score_band" in data
    assert "pnl_by_exit_trigger" in data
    assert "scatter_points" in data
    assert "calibration_curve" in data


@pytest.mark.django_db
def test_total_closed_count_is_correct(client, banked_positions):
    data = client.get(URL).json()
    # 6 closed positions injected; 2 open/paper must be excluded
    assert data["total_closed"] == 6


# ===========================================================================
# §2 source coverage
# ===========================================================================


@pytest.mark.django_db
def test_both_sources_reflected_in_scatter_points(client, banked_positions):
    """Both source=model and source=copytrade rows must appear in scatter (where scored)."""
    data = client.get(URL).json()
    # M1/M2/M3 are model, C1/C3 are copytrade (C2 has no score so excluded from scatter)
    points = data["scatter_points"]
    scores = {p["score"] for p in points}
    # model rows: 0.85, 0.85, 0.25; copytrade rows: 0.75, 0.35
    assert 0.85 in scores  # model
    assert 0.75 in scores  # copytrade
    assert 0.35 in scores  # copytrade


@pytest.mark.django_db
def test_model_source_row_in_scatter(client, banked_positions):
    data = client.get(URL).json()
    # MINT_M1: score=0.85, pnl_pct=80.0
    m1_points = [p for p in data["scatter_points"] if p["score"] == 0.85 and p["realized_pnl_pct"] == 80.0]
    assert len(m1_points) >= 1


@pytest.mark.django_db
def test_copytrade_source_row_in_scatter(client, banked_positions):
    data = client.get(URL).json()
    # MINT_C1: score=0.75, pnl_pct=40.0
    c1_points = [p for p in data["scatter_points"] if p["score"] == 0.75 and p["realized_pnl_pct"] == 40.0]
    assert len(c1_points) == 1


# ===========================================================================
# §3 open positions excluded
# ===========================================================================


@pytest.mark.django_db
def test_open_positions_excluded_from_total_closed(client, banked_positions):
    data = client.get(URL).json()
    # MINT_M4 (open, score=0.90) must NOT inflate total_closed
    assert data["total_closed"] == 6


@pytest.mark.django_db
def test_open_position_mint_not_in_scatter(client, banked_positions):
    data = client.get(URL).json()
    # MINT_M4 score=0.90 is open — must not appear in scatter
    scores = [p["score"] for p in data["scatter_points"]]
    # The only row with score=0.90 is the open position; it must be absent
    assert 0.90 not in scores


# ===========================================================================
# §4 win_rate_by_score_band
# ===========================================================================


@pytest.mark.django_db
def test_score_band_list_has_10_entries(client, banked_positions):
    data = client.get(URL).json()
    assert len(data["win_rate_by_score_band"]) == 10


@pytest.mark.django_db
def test_score_band_labels_cover_full_range(client, banked_positions):
    data = client.get(URL).json()
    labels = [b["band"] for b in data["win_rate_by_score_band"]]
    assert labels[0] == "0.0-0.1"
    assert labels[-1] == "0.9-1.0"


@pytest.mark.django_db
def test_high_band_has_correct_win_rate(client, banked_positions):
    """Band 0.8-0.9 contains M1 (WIN, pnl=+80) and M2 (LOSS, pnl=-20) → 1/2 = 0.5."""
    data = client.get(URL).json()
    bands = {b["band"]: b for b in data["win_rate_by_score_band"]}
    high_band = bands["0.8-0.9"]
    assert high_band["count"] == 2
    assert high_band["wins"] == 1
    assert abs(high_band["win_rate"] - 0.5) < 1e-9


@pytest.mark.django_db
def test_low_band_has_correct_win_rate(client, banked_positions):
    """Band 0.2-0.3 contains M3 (LOSS, pnl=-15) → 0/1 = 0.0."""
    data = client.get(URL).json()
    bands = {b["band"]: b for b in data["win_rate_by_score_band"]}
    low_band = bands["0.2-0.3"]
    assert low_band["count"] == 1
    assert low_band["wins"] == 0
    assert low_band["win_rate"] == 0.0


@pytest.mark.django_db
def test_mid_band_correct_counts(client, banked_positions):
    """Band 0.7-0.8 contains C1 (WIN, pnl=+40) → 1/1 = 1.0.
    Band 0.3-0.4 contains C3 (WIN, pnl=+10) → 1/1 = 1.0.
    C2 has score=None → excluded from score-band aggregation.
    """
    data = client.get(URL).json()
    bands = {b["band"]: b for b in data["win_rate_by_score_band"]}

    c1_band = bands["0.7-0.8"]
    assert c1_band["count"] == 1
    assert c1_band["wins"] == 1
    assert c1_band["win_rate"] == 1.0

    c3_band = bands["0.3-0.4"]
    assert c3_band["count"] == 1
    assert c3_band["wins"] == 1
    assert c3_band["win_rate"] == 1.0


# ===========================================================================
# §5 pnl_by_exit_trigger
# ===========================================================================


@pytest.mark.django_db
def test_pnl_trigger_includes_all_triggers(client, banked_positions):
    data = client.get(URL).json()
    triggers = {row["exit_trigger"] for row in data["pnl_by_exit_trigger"]}
    assert "TAKE_PROFIT" in triggers
    assert "STOP_LOSS" in triggers
    assert "TRAILING" in triggers
    assert "AUTO_SELL_TIMER" in triggers


@pytest.mark.django_db
def test_take_profit_avg_pnl_correct(client, banked_positions):
    """TAKE_PROFIT: M1 (+80) + C3 (+10) → total=90, avg=45."""
    data = client.get(URL).json()
    by_trigger = {row["exit_trigger"]: row for row in data["pnl_by_exit_trigger"]}
    tp = by_trigger["TAKE_PROFIT"]
    assert tp["count"] == 2
    assert abs(tp["total_pnl_pct"] - 90.0) < 1e-6
    assert abs(tp["avg_pnl_pct"] - 45.0) < 1e-6


@pytest.mark.django_db
def test_stop_loss_total_pnl_correct(client, banked_positions):
    """STOP_LOSS: M2 (-20) + M3 (-15) → total=-35, avg=-17.5."""
    data = client.get(URL).json()
    by_trigger = {row["exit_trigger"]: row for row in data["pnl_by_exit_trigger"]}
    sl = by_trigger["STOP_LOSS"]
    assert sl["count"] == 2
    assert abs(sl["total_pnl_pct"] - (-35.0)) < 1e-6
    assert abs(sl["avg_pnl_pct"] - (-17.5)) < 1e-6


@pytest.mark.django_db
def test_trailing_trigger_present(client, banked_positions):
    """TRAILING: C1 (+40) → count=1, avg=40."""
    data = client.get(URL).json()
    by_trigger = {row["exit_trigger"]: row for row in data["pnl_by_exit_trigger"]}
    tr = by_trigger["TRAILING"]
    assert tr["count"] == 1
    assert abs(tr["avg_pnl_pct"] - 40.0) < 1e-6


@pytest.mark.django_db
def test_both_sources_in_pnl_by_trigger(client, banked_positions):
    """pnl_by_exit_trigger must include rows from both sources.

    TAKE_PROFIT covers M1 (model) + C3 (copytrade); AUTO_SELL_TIMER covers C2
    (copytrade, score=None). The total count across all triggers must equal 6.
    """
    data = client.get(URL).json()
    total_count = sum(row["count"] for row in data["pnl_by_exit_trigger"])
    assert total_count == 6  # all 6 closed positions, both sources


# ===========================================================================
# §6 scatter_points
# ===========================================================================


@pytest.mark.django_db
def test_scatter_only_includes_scored_rows(client, banked_positions):
    """C2 (score=None) must be excluded from scatter_points."""
    data = client.get(URL).json()
    points = data["scatter_points"]
    # 5 scored rows: M1, M2, M3, C1, C3 — C2 excluded
    assert len(points) == 5


@pytest.mark.django_db
def test_no_null_score_in_scatter(client, banked_positions):
    data = client.get(URL).json()
    for pt in data["scatter_points"]:
        assert pt["score"] is not None


@pytest.mark.django_db
def test_scatter_values_match_fixture(client, banked_positions):
    """Every scatter point must have the exact score + pnl from the injected row."""
    data = client.get(URL).json()
    expected = {
        (0.85, 80.0),   # M1
        (0.85, -20.0),  # M2
        (0.25, -15.0),  # M3
        (0.75, 40.0),   # C1
        (0.35, 10.0),   # C3
    }
    actual = {(p["score"], p["realized_pnl_pct"]) for p in data["scatter_points"]}
    assert actual == expected


@pytest.mark.django_db
def test_scatter_no_new_pnl_math(client, banked_positions):
    """realized_pnl_pct values in scatter must equal the exact injected values.

    This confirms Principle #2: no new PnL calculation was performed.
    """
    data = client.get(URL).json()
    pnl_values = sorted(p["realized_pnl_pct"] for p in data["scatter_points"])
    expected = sorted([-20.0, -15.0, 10.0, 40.0, 80.0])
    assert pnl_values == expected


# ===========================================================================
# §7 calibration_curve
# ===========================================================================


@pytest.mark.django_db
def test_calibration_curve_has_10_buckets(client, banked_positions):
    data = client.get(URL).json()
    assert len(data["calibration_curve"]) == 10


@pytest.mark.django_db
def test_calibration_bucket_mids_correct(client, banked_positions):
    data = client.get(URL).json()
    mids = [b["bucket_mid"] for b in data["calibration_curve"]]
    expected_mids = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    assert mids == expected_mids


@pytest.mark.django_db
def test_calibration_high_bucket_win_rate(client, banked_positions):
    """Bucket 0.8-0.9 (M1 WIN + M2 LOSS): actual_win_rate = 0.5."""
    data = client.get(URL).json()
    buckets = {b["bucket"]: b for b in data["calibration_curve"]}
    b = buckets["0.8-0.9"]
    assert b["count"] == 2
    assert b["wins"] == 1
    assert abs(b["actual_win_rate"] - 0.5) < 1e-9


@pytest.mark.django_db
def test_calibration_zero_count_bucket_win_rate_is_zero(client, banked_positions):
    """Buckets with no positions must return actual_win_rate=0.0 (H4 zero-guard)."""
    data = client.get(URL).json()
    for bucket in data["calibration_curve"]:
        if bucket["count"] == 0:
            assert bucket["actual_win_rate"] == 0.0, (
                f"H4 zero-guard violated: bucket {bucket['bucket']} has count=0 "
                f"but actual_win_rate={bucket['actual_win_rate']}"
            )


# ===========================================================================
# §8 empty-set (H4 zero-guard — no positions in DB)
# ===========================================================================


@pytest.mark.django_db
def test_empty_db_returns_200(client, db):
    resp = client.get(URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_empty_db_no_division_by_zero(client, db):
    """Empty DB must not raise any exception (division-by-zero guard)."""
    resp = client.get(URL)
    assert resp.status_code == 200
    data = resp.json()
    # All keys must exist with safe zero values
    assert data["total_closed"] == 0
    assert isinstance(data["win_rate_by_score_band"], list)
    assert isinstance(data["pnl_by_exit_trigger"], list)
    assert isinstance(data["scatter_points"], list)
    assert isinstance(data["calibration_curve"], list)


@pytest.mark.django_db
def test_empty_db_zero_total_closed(client, db):
    data = client.get(URL).json()
    assert data["total_closed"] == 0


@pytest.mark.django_db
def test_empty_db_score_bands_all_zero_win_rate(client, db):
    """All 10 score bands must have count=0, wins=0, win_rate=0.0."""
    data = client.get(URL).json()
    bands = data["win_rate_by_score_band"]
    assert len(bands) == 10
    for band in bands:
        assert band["count"] == 0
        assert band["wins"] == 0
        assert band["win_rate"] == 0.0, (
            f"H4 zero-guard violated in empty DB: band {band['band']} win_rate={band['win_rate']}"
        )


@pytest.mark.django_db
def test_empty_db_calibration_all_zero_actual_win_rate(client, db):
    """All 10 calibration buckets must have actual_win_rate=0.0 in empty DB."""
    data = client.get(URL).json()
    curve = data["calibration_curve"]
    assert len(curve) == 10
    for bucket in curve:
        assert bucket["actual_win_rate"] == 0.0, (
            f"H4 zero-guard violated: bucket {bucket['bucket']} actual_win_rate={bucket['actual_win_rate']}"
        )


@pytest.mark.django_db
def test_empty_db_pnl_trigger_empty_list(client, db):
    data = client.get(URL).json()
    assert data["pnl_by_exit_trigger"] == []


@pytest.mark.django_db
def test_empty_db_scatter_empty_list(client, db):
    data = client.get(URL).json()
    assert data["scatter_points"] == []
