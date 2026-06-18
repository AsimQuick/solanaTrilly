# ---
# module: trading.tests.test_position_closer_ac663
# sprint: sprint-13
# story: US-66 AC-66.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.position_closer, trading.models, django, pytest, datetime
# ---
"""AC-66.3 — settle_paper_position writes the SAME realized fields as a live CLOSED one.

A settled paper position writes exit_price, exit_trigger, realized_pnl_pct,
peak_price, and closed_at into the shared US-64 Position model.  Dashboard /
analytics treat paper == live via the sentinel: closed_at IS NOT NULL.

Test sections
-------------
  §1  settle_paper_position — field writes (with DB)
        test_settle_writes_exit_price
        test_settle_writes_exit_trigger
        test_settle_writes_realized_pnl_pct
        test_settle_writes_peak_price
        test_settle_writes_closed_at
        test_settle_sets_status_closed

  §2  Shape parity — core AC-66.3 assertion (with DB)
        test_paper_settled_and_live_closed_identical_realized_field_shape

  §3  Sentinel (with DB)
        test_closed_at_not_null_sentinel_works_for_settled_paper
        test_unsettled_paper_excluded_from_sentinel_query

  §4  Guard (no DB)
        test_unentered_raises_value_error

All tests are offline/deterministic — no network, no firehose.
"""

from datetime import datetime, timezone

import pytest

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

MINT = "So11111111111111111111111111111111111111112"
NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settler_result(
    trigger: str = "TAKE_PROFIT_PCT",
    pnl: float = 50.0,
    peak: float = 80.0,
    held: float = 120.0,
) -> dict:
    """Build a minimal enterable settler result dict (no network)."""
    return {
        "enterable": True,
        "trigger": trigger,
        "pnl": pnl,
        "peak": peak,
        "held": held,
        "flow": 5000.0,
    }


def _make_paper_position():
    """Create and persist a PAPER Position row (requires DB)."""
    from trading.models import Position

    return Position.objects.create(
        mint=MINT,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=NOW,
        entry_price=0.001000,
        size_sol=0.1,
    )


def _realized_field_shape(pos) -> set:
    """Return the set of realized field names that are non-null on *pos*.

    Used by the core parity test to assert paper-settled == live-closed shape.
    """
    realized_fields = (
        "exit_price",
        "exit_trigger",
        "realized_pnl_pct",
        "peak_price",
        "closed_at",
    )
    return {f for f in realized_fields if getattr(pos, f) is not None}


# ===========================================================================
# §1  settle_paper_position — field writes
# ===========================================================================


@pytest.mark.django_db
def test_settle_writes_exit_price():
    """After settle, position.exit_price must be non-None."""
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result(pnl=50.0)
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.exit_price is not None, "exit_price must be written by settle_paper_position"


@pytest.mark.django_db
def test_settle_writes_exit_trigger():
    """After settle, position.exit_trigger must equal settler_result['trigger']."""
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result(trigger="STOP_LOSS")
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.exit_trigger == "STOP_LOSS"


@pytest.mark.django_db
def test_settle_writes_realized_pnl_pct():
    """After settle, position.realized_pnl_pct must equal settler_result['pnl']."""
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result(pnl=-15.5)
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.realized_pnl_pct == pytest.approx(-15.5)


@pytest.mark.django_db
def test_settle_writes_peak_price():
    """After settle, position.peak_price must be non-None and > 0."""
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result(peak=80.0)
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.peak_price is not None, "peak_price must be written by settle_paper_position"
    assert pos.peak_price > 0, "peak_price must be positive"


@pytest.mark.django_db
def test_settle_writes_closed_at():
    """After settle, position.closed_at must be non-None."""
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result()
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.closed_at is not None, "closed_at must be written by settle_paper_position"


@pytest.mark.django_db
def test_settle_sets_status_closed():
    """After settle, position.status must be 'CLOSED'."""
    from trading.models import Position
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    assert pos.status == Position.STATUS_PAPER, "pre-condition: status is PAPER"

    result = _make_settler_result()
    settle_paper_position(pos, result, now=NOW)

    pos.refresh_from_db()
    assert pos.status == Position.STATUS_CLOSED


# ===========================================================================
# §2  Shape parity — core AC-66.3 assertion
# ===========================================================================


@pytest.mark.django_db
def test_paper_settled_and_live_closed_identical_realized_field_shape():
    """Settled PAPER position and live CLOSED position carry identical realized-field shape.

    Core AC-66.3 assertion:
      - Both must have the SAME 5 realized fields non-null:
            exit_price, exit_trigger, realized_pnl_pct, peak_price, closed_at
      - Both must appear in Position.objects.filter(closed_at__isnull=False)
        (the dashboard/analytics query sentinel).
    """
    from datetime import timedelta

    from trading.models import Position
    from trading.position_closer import settle_paper_position

    # --- Build and settle a PAPER position ---
    paper_pos = _make_paper_position()
    result = _make_settler_result(trigger="TAKE_PROFIT_PCT", pnl=50.0, peak=80.0, held=120.0)
    settle_paper_position(paper_pos, result, now=NOW)
    paper_pos.refresh_from_db()

    # --- Build a live CLOSED position by directly writing all fields ---
    # (simulates what the live execution engine writes)
    live_entry_ts = datetime(2026, 6, 18, 11, 0, 0, tzinfo=timezone.utc)
    live_exit_ts = live_entry_ts + timedelta(seconds=180.0)
    live_closed_at = datetime(2026, 6, 18, 11, 3, 5, tzinfo=timezone.utc)

    live_pos = Position.objects.create(
        mint=MINT,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_LIVE,
        status=Position.STATUS_CLOSED,
        entry_ts=live_entry_ts,
        entry_price=0.002000,
        size_sol=0.1,
        exit_ts=live_exit_ts,
        exit_price=0.003000,
        exit_trigger="TAKE_PROFIT_PCT",
        realized_pnl_pct=50.0,
        peak_price=0.003200,
        closed_at=live_closed_at,
    )

    # --- Assert identical realized-field shape ---
    paper_shape = _realized_field_shape(paper_pos)
    live_shape = _realized_field_shape(live_pos)

    expected_shape = {"exit_price", "exit_trigger", "realized_pnl_pct", "peak_price", "closed_at"}

    assert paper_shape == expected_shape, (
        f"Paper position realized-field shape {paper_shape!r} "
        f"does not match expected {expected_shape!r}"
    )
    assert live_shape == expected_shape, (
        f"Live position realized-field shape {live_shape!r} "
        f"does not match expected {expected_shape!r}"
    )
    assert paper_shape == live_shape, (
        f"Paper and live realized-field shapes differ: "
        f"paper={paper_shape!r}, live={live_shape!r}"
    )

    # --- Both must appear in the dashboard/analytics sentinel query ---
    settled_pks = set(
        Position.objects.filter(closed_at__isnull=False).values_list("pk", flat=True)
    )
    assert paper_pos.pk in settled_pks, (
        "Settled PAPER position must appear in closed_at IS NOT NULL query"
    )
    assert live_pos.pk in settled_pks, (
        "Live CLOSED position must appear in closed_at IS NOT NULL query"
    )


# ===========================================================================
# §3  Sentinel
# ===========================================================================


@pytest.mark.django_db
def test_closed_at_not_null_sentinel_works_for_settled_paper():
    """After settling a PAPER position, it appears in closed_at IS NOT NULL query."""
    from trading.models import Position
    from trading.position_closer import settle_paper_position

    pos = _make_paper_position()
    result = _make_settler_result()
    settle_paper_position(pos, result, now=NOW)

    count = Position.objects.filter(closed_at__isnull=False).count()
    assert count >= 1, (
        "A settled PAPER position must be included in closed_at IS NOT NULL query "
        "(AC-66.3 sentinel)"
    )
    # Verify our specific position is included
    assert Position.objects.filter(pk=pos.pk, closed_at__isnull=False).exists(), (
        "The settled PAPER position's pk must be in the closed_at IS NOT NULL result set"
    )


@pytest.mark.django_db
def test_unsettled_paper_excluded_from_sentinel_query():
    """An unsettled PAPER position has closed_at=None, excluded from the sentinel query."""
    from trading.models import Position

    pos = _make_paper_position()

    # Confirm closed_at is None before settlement
    assert pos.closed_at is None, "Pre-condition: unsettled paper position has closed_at=None"

    # Confirm it is excluded from the sentinel query
    assert not Position.objects.filter(pk=pos.pk, closed_at__isnull=False).exists(), (
        "Unsettled PAPER position must NOT appear in closed_at IS NOT NULL query"
    )


# ===========================================================================
# §4  Guard (no DB)
# ===========================================================================


def test_unentered_raises_value_error():
    """Calling settle_paper_position with enterable=False must raise ValueError."""
    from unittest.mock import MagicMock

    from trading.position_closer import settle_paper_position

    # Use a mock position — no DB needed for the guard test
    mock_pos = MagicMock()
    mock_pos.entry_price = 0.001

    unentered_result = {"enterable": False, "reason": "dead"}

    with pytest.raises(ValueError, match="not enterable"):
        settle_paper_position(mock_pos, unentered_result)
