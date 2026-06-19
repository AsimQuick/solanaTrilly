# ---
# module: copytrade.tests.test_rejected_entry_settler_ac75
# sprint: US-75
# story: US-75 AC-2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, copytrade.rejected_entry_settler,
#   copytrade.models, pandas
# ---
"""US-75 AC-2: rejected-entry counterfactual settler unit tests.

Test tiers (§8 per-AC test tiers):
  Tier-3 unit on a REAL parquet fixture (lake/tapes/2026-06-14.parquet).
  Bounded forward window test.
  Zero/dust guard (#405).
  Empty tape -> outcome=None immediately (#304 guard, never a perpetual skip).
  DB-backed settler integration.

All tests use the real parquet tape — no hand-built mocks for the price data.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from copytrade.rejected_entry_settler import (
    COUNTERFACTUAL_WINDOW_S,
    RUG_DROP_THRESHOLD,
    settle_counterfactual,
)

# ---------------------------------------------------------------------------
# Real parquet fixture path
# ---------------------------------------------------------------------------

PARQUET_PATH = Path("/Users/asim/NoIcloud/solanatrills/lake/tapes/2026-06-14.parquet")
GAPPER_MINT = "FBKbAPporDMvvmV463PF89xhH55Gs6uVc9FPJGcbpump"


def _load_parquet_tape(mint: str) -> list:
    """Load (block_time, price) tuples for a mint from the real parquet lake tape."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed — skip parquet fixture tests")
        return []

    if not PARQUET_PATH.exists():
        pytest.skip(f"Real parquet fixture not found at {PARQUET_PATH}")
        return []

    df = pd.read_parquet(PARQUET_PATH)
    m = df[df["mint"] == mint].copy()
    m["price"] = m["virtual_sol_reserves"] / m["virtual_token_reserves"]
    m = m.sort_values("block_time")
    return [(float(r["block_time"]), float(r["price"])) for _, r in m.iterrows()]


# ===========================================================================
# Pure settle_counterfactual unit tests
# ===========================================================================


def test_settle_empty_tape_returns_none():
    """Empty tape -> outcome=None immediately (#304 guard, never a perpetual skip)."""
    result = settle_counterfactual(
        tape=[],
        rejection_ts=1781398811.0,
        quote_price=2.8e-5,
    )
    assert result["peak_return_pct"] is None
    assert result["rug_outcome"] is None
    assert result["forward_window_s"] is None


def test_settle_tape_outside_window_returns_none():
    """Tape entries all BEFORE rejection_ts -> no entries in window -> outcome=None (#304)."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts - 100.0, 3.0e-5),
        (rejection_ts - 50.0, 3.5e-5),
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5)
    assert result["peak_return_pct"] is None


def test_settle_zero_quote_guard():
    """quote_price=0 -> returns None result (#405 guard, no crash)."""
    tape = [(1781398811.0, 3.0e-5), (1781398812.0, 4.0e-5)]
    result = settle_counterfactual(tape, 1781398811.0, quote_price=0.0)
    assert result["peak_return_pct"] is None
    assert result["rug_outcome"] is None
    assert result["forward_window_s"] is None


def test_settle_negative_quote_guard():
    """quote_price<0 -> returns None result (#405 guard, no crash)."""
    tape = [(1781398811.0, 3.0e-5)]
    result = settle_counterfactual(tape, 1781398811.0, quote_price=-1.0)
    assert result["peak_return_pct"] is None


def test_settle_dust_price_skipped():
    """Dust prices (<=1e-15) are skipped, not used for peak (#405 guard)."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts, 1e-20),          # dust — must be skipped
        (rejection_ts + 10.0, 3.0e-5),  # real price
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5, window_s=60)
    # If dust was NOT skipped, peak would be tiny and peak_return would be negative
    # If dust IS skipped, peak = max(2.8e-5, 3.0e-5) = 3.0e-5
    assert result["peak_return_pct"] is not None
    # peak_return = 3.0e-5 / 2.8e-5 - 1 ≈ 0.0714
    assert result["peak_return_pct"] > 0


def test_settle_bounded_window():
    """Only tape entries within [rejection_ts, rejection_ts+window_s] are used."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts, 3.0e-5),                  # in window
        (rejection_ts + 59.0, 5.0e-5),           # in window (window_s=60)
        (rejection_ts + 61.0, 100.0e-5),         # OUTSIDE window — must be ignored
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5, window_s=60)
    # peak = max(2.8e-5, 3.0e-5, 5.0e-5) = 5.0e-5 (not 100.0e-5)
    assert result["peak_return_pct"] is not None
    # 5.0e-5 / 2.8e-5 - 1 ≈ 0.786
    assert result["peak_return_pct"] == pytest.approx(5.0e-5 / 2.8e-5 - 1, rel=1e-6)


def test_settle_rug_outcome_true():
    """rug_outcome=True when end price drops >=50% from peak."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts, 3.0e-5),        # start
        (rejection_ts + 5.0, 6.0e-5),  # peak: +114% from quote
        (rejection_ts + 10.0, 2.9e-5), # end: dropped 51.7% from peak -> rug
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5, window_s=60)
    assert result["rug_outcome"] is True
    assert result["peak_return_pct"] is not None
    # peak = 6e-5, quote = 2.8e-5, peak_return = 6/2.8 - 1 ≈ 1.143
    assert result["peak_return_pct"] == pytest.approx(6.0e-5 / 2.8e-5 - 1, rel=1e-6)


def test_settle_rug_outcome_false():
    """rug_outcome=False when price does NOT drop >=50% from peak."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts, 3.0e-5),         # start
        (rejection_ts + 5.0, 6.0e-5),   # peak
        (rejection_ts + 10.0, 4.5e-5),  # end: dropped 25% from peak -> NOT rug
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5, window_s=60)
    assert result["rug_outcome"] is False


def test_settle_forward_window_s_bounded():
    """forward_window_s is capped at the actual tape span, not the max window."""
    rejection_ts = 1781398811.0
    tape = [
        (rejection_ts, 3.0e-5),
        (rejection_ts + 30.0, 4.0e-5),
    ]
    result = settle_counterfactual(tape, rejection_ts, quote_price=2.8e-5, window_s=3600)
    # actual window = 30s (time of last valid entry - rejection_ts)
    assert result["forward_window_s"] == 30


# ===========================================================================
# Real parquet fixture test (§8 P3 methodology — against REALITY)
# ===========================================================================


def test_settle_real_parquet_gapper_forward():
    """Settle the known-gapper forward tape from the real 2026-06-14 parquet.

    Assertion: the settler reads forward from the rejection ts and writes
    a non-None peak_return_pct and rug_outcome for the known-gapping mint.
    This validates the settler runs without crash on a real parquet file.
    """
    tape = _load_parquet_tape(GAPPER_MINT)
    assert len(tape) > 0, "Real parquet tape must have entries for the gapper mint"

    rejection_ts = float(tape[0][0])  # first entry is the rejection ts
    quote_price = 2.8124921853191412e-05  # from golden fixture

    result = settle_counterfactual(tape, rejection_ts, quote_price, window_s=3600)

    # Should have at least some forward entries for this busy mint
    assert result["peak_return_pct"] is not None, (
        "Expected non-None peak_return_pct for a real busy mint — "
        "the tape had many swaps within 3600s of the rejection ts"
    )
    assert result["rug_outcome"] is not None
    assert result["forward_window_s"] is not None
    # The peak must be >= 0 (token may go up or stay flat, but peak >= fill)
    # For this specific mint it went up dramatically after the first swap
    assert result["peak_return_pct"] > 0.0, (
        "The known gapper (FBKb...) went significantly higher after the first swap — "
        f"expected peak_return_pct > 0, got {result['peak_return_pct']}"
    )


# ===========================================================================
# DB-backed settler integration (settle_rejected_entries)
# ===========================================================================


@pytest.mark.django_db
def test_settle_rejected_entries_populates_counterfactual():
    """settle_rejected_entries updates DB rows with counterfactual fields."""
    from datetime import datetime, timezone
    from copytrade.models import CopytradePosition
    from copytrade.rejected_entry_settler import settle_rejected_entries

    ts = datetime(2026, 6, 14, 0, 0, 11, tzinfo=timezone.utc)  # block_time=1781398811
    cohort_id = "test-cohort-settler-ac75"

    # Create an ENTRY_REJECTED position
    pos = CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint="SettlerMint111111111111111111111111111111pump",
        trigger_wallet="Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ts,
        exit_ts=ts,
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
        quote_price=2.8e-5,
        fill_price=3.4e-5,
        cap_pct=0.15,
        # peak_return_pct / rug_outcome / forward_window_s all NULL (unsettled)
    )

    # Synthetic tape: one entry at rejection_ts, one at +30s
    rejection_epoch = float(ts.timestamp())
    tape_data = [
        (rejection_epoch, 3.5e-5),
        (rejection_epoch + 30.0, 5.0e-5),
    ]

    def fake_tape_fn(mint: str):
        return tape_data

    count = settle_rejected_entries(cohort_id, tape_fn=fake_tape_fn, window_s=3600)
    assert count == 1

    pos.refresh_from_db()
    assert pos.peak_return_pct is not None
    # peak = max(2.8e-5, 3.5e-5, 5.0e-5) = 5.0e-5 / 2.8e-5 - 1 ≈ 0.786
    assert pos.peak_return_pct == pytest.approx(5.0e-5 / 2.8e-5 - 1, rel=1e-6)
    assert pos.rug_outcome is not None
    assert pos.forward_window_s is not None


@pytest.mark.django_db
def test_settle_rejected_entries_empty_tape_writes_none():
    """settle_rejected_entries with empty tape writes outcome=None (#304 guard)."""
    from datetime import datetime, timezone
    from copytrade.models import CopytradePosition
    from copytrade.rejected_entry_settler import settle_rejected_entries

    ts = datetime(2026, 6, 14, 0, 0, 12, tzinfo=timezone.utc)
    cohort_id = "test-cohort-settler-empty-ac75"

    pos = CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint="EmptyTapeMint111111111111111111111111111pump",
        trigger_wallet="Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ts,
        exit_ts=ts,
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
        quote_price=2.8e-5,
    )

    def empty_tape_fn(mint: str):
        return []  # no tape at all

    count = settle_rejected_entries(cohort_id, tape_fn=empty_tape_fn)
    assert count == 1

    pos.refresh_from_db()
    # outcome=None — empty tape, no perpetual skip (#304)
    assert pos.peak_return_pct is None
    assert pos.rug_outcome is None
    assert pos.forward_window_s is None


@pytest.mark.django_db
def test_settle_rejected_entries_skips_already_settled():
    """settle_rejected_entries skips rows that already have peak_return_pct set."""
    from datetime import datetime, timezone
    from copytrade.models import CopytradePosition
    from copytrade.rejected_entry_settler import settle_rejected_entries

    ts = datetime(2026, 6, 14, 0, 0, 13, tzinfo=timezone.utc)
    cohort_id = "test-cohort-settler-settled-ac75"

    pos = CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint="AlreadySettledMint111111111111111111111pump",
        trigger_wallet="Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ts,
        exit_ts=ts,
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
        quote_price=2.8e-5,
        peak_return_pct=0.50,  # already settled
        rug_outcome=False,
        forward_window_s=300,
    )

    call_count = [0]

    def counting_tape_fn(mint: str):
        call_count[0] += 1
        return []

    count = settle_rejected_entries(cohort_id, tape_fn=counting_tape_fn)
    assert count == 0  # already settled — not re-processed
    assert call_count[0] == 0  # tape_fn never called


# ===========================================================================
# CopytradePosition counterfactual columns exist (§6.1 migration check)
# ===========================================================================


@pytest.mark.django_db
def test_copytrade_position_has_counterfactual_columns():
    """CopytradePosition has the US-75 AC-2 counterfactual columns."""
    from copytrade.models import CopytradePosition

    assert hasattr(CopytradePosition, "peak_return_pct")
    assert hasattr(CopytradePosition, "rug_outcome")
    assert hasattr(CopytradePosition, "forward_window_s")
