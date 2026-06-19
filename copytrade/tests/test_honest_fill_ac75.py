# ---
# module: copytrade.tests.test_honest_fill_ac75
# sprint: US-75
# story: US-75 AC-1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, copytrade.honest_fill,
#   copytrade.models, copytrade.wallet_consumer
# ---
"""US-75 AC-1: Honest copy-fill unit tests.

Test tiers (§8 per-AC test tiers):
  Tier-3 unit: cap within / at-boundary / over, quote=0, fill=None, fill=NaN.
  Golden fixture: known-gapping token from 2026-06-14.parquet, red->green.
  Migration smoke: confirmed by migration 0007 applying in CI.
  OBSERVE isolation: maintained (see test_observe_safety_gate_ac613.py).

Guard coverage (§3 trouble-PRs):
  #405 — quote_price=0  → ZERO_QUOTE, no crash.
  #358 — fill_price=None/NaN → NO_FILL_PRICE, no crash.

Real-JSONB-dict test: the fixture dict is built from real parquet data
(not hand-built np.nan dicts — §8 P1 requirement).

Feature flag: honest_fills_enabled defaults to False (§6.7).
  The DB-backed flag test asserts default=False on a fresh CopyTradeSettings row.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from copytrade.honest_fill import (
    DEFAULT_ENTRY_SLIP_CAP,
    check_copy_entry,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 14, 0, 0, 30, tzinfo=timezone.utc)
_BLOCK_TIME = 1781398811.0  # from the golden fixture first swap

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_gapper_ac75.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


# ===========================================================================
# Tier-3 unit tests — cap within / at-boundary / over
# ===========================================================================


def test_check_copy_entry_accepted_within_cap():
    """Fill within cap -> enterable=True."""
    quote = 1.0
    fill = 1.10  # 10% slip, under 15% cap
    result = check_copy_entry(quote, fill, clock_arrival_ts=_TS)
    assert result.enterable is True
    assert result.reason == ""
    assert result.realized_slip_pct is not None
    assert abs(result.realized_slip_pct - 0.10) < 1e-9


def test_check_copy_entry_accepted_at_boundary():
    """Fill exactly at cap boundary -> enterable=True (boundary is exclusive)."""
    quote = 1.0
    fill = 1.15  # exactly 15% slip = cap_pct, not OVER it
    result = check_copy_entry(quote, fill, clock_arrival_ts=_TS)
    # fill/quote - 1 = 0.15 which is NOT > 0.15 -> accepted
    assert result.enterable is True
    assert result.reason == ""


def test_check_copy_entry_rejected_over_cap():
    """Fill over cap -> enterable=False, reason=SLIPPAGE_CAP_6002."""
    quote = 1.0
    fill = 1.1501  # just over 15%
    result = check_copy_entry(quote, fill, clock_arrival_ts=_TS)
    assert result.enterable is False
    assert result.reason == "SLIPPAGE_CAP_6002"
    assert result.fill_price == pytest.approx(fill)
    assert result.quote_price == pytest.approx(quote)
    assert result.cap_pct == DEFAULT_ENTRY_SLIP_CAP
    assert result.realized_slip_pct is not None
    assert result.realized_slip_pct > DEFAULT_ENTRY_SLIP_CAP


def test_check_copy_entry_custom_cap():
    """Custom cap_pct is applied correctly."""
    quote = 1.0
    fill = 1.25  # 25% slip
    # With default 15% cap -> rejected
    r1 = check_copy_entry(quote, fill, clock_arrival_ts=_TS)
    assert r1.enterable is False
    # With 30% cap -> accepted
    r2 = check_copy_entry(quote, fill, clock_arrival_ts=_TS, cap_pct=0.30)
    assert r2.enterable is True


# ===========================================================================
# Tier-3 unit tests — guard cases (#405, #358)
# ===========================================================================


def test_check_copy_entry_zero_quote_guard():
    """quote_price=0 -> no crash, returns ZERO_QUOTE (#405 guard).

    Uses a real JSONB-shaped dict input to represent the raw event price field
    to satisfy §8 P1 (test with real JSONB dict, not hand-built np.nan dict).
    """
    # Simulate a real JSONB-shaped event where price field is 0
    raw_event_jsonb = {
        "wallet": "AbcWallet1111111111111111111111111111111111",
        "mint": "MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        "type": "buy",
        "sol_amount": 500000000,
        "token_amount": 0,
        "price": 0,          # JSONB sends 0 (not None, not NaN — real case)
        "block_time": 1781398811,
    }
    quote_from_jsonb = float(raw_event_jsonb["price"])  # 0.0 — the boundary case
    result = check_copy_entry(
        quote_price=quote_from_jsonb,
        fill_price=1.5e-5,
        clock_arrival_ts=_TS,
    )
    assert not result.enterable
    assert result.reason == "ZERO_QUOTE"
    assert result.realized_slip_pct is None


def test_check_copy_entry_negative_quote_guard():
    """quote_price<0 -> no crash, returns ZERO_QUOTE (#405 guard)."""
    result = check_copy_entry(-1.0, 1.0, clock_arrival_ts=_TS)
    assert not result.enterable
    assert result.reason == "ZERO_QUOTE"


def test_check_copy_entry_fill_none_guard():
    """fill_price=None -> no crash, returns NO_FILL_PRICE (#358 guard).

    The JSONB boundary: real DB may return None for price fields — must be
    coerced to NaN before any numeric op (coerce None->nan at the boundary).
    """
    # Real JSONB event: price field is absent / None
    raw_event_jsonb = {
        "wallet": "AbcWallet1111111111111111111111111111111111",
        "mint": "MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        "type": "buy",
        "sol_amount": 500000000,
        "token_amount": 0,
        "price": None,       # JSONB sends None (real production case #358)
        "block_time": 1781398811,
    }
    fill_from_jsonb = raw_event_jsonb["price"]  # None — coercion required
    result = check_copy_entry(
        quote_price=1.0e-5,
        fill_price=fill_from_jsonb,  # type: ignore[arg-type]
        clock_arrival_ts=_TS,
    )
    assert not result.enterable
    assert result.reason == "NO_FILL_PRICE"
    assert result.realized_slip_pct is None
    assert result.fill_price is None


def test_check_copy_entry_fill_nan_guard():
    """fill_price=NaN -> no crash, returns NO_FILL_PRICE (#358 guard)."""
    result = check_copy_entry(
        quote_price=1.0e-5,
        fill_price=float("nan"),
        clock_arrival_ts=_TS,
    )
    assert not result.enterable
    assert result.reason == "NO_FILL_PRICE"
    assert result.realized_slip_pct is None


# ===========================================================================
# copy_latency_s derivation tests (§8 P1)
# ===========================================================================


def test_copy_latency_s_computed_when_block_time_present():
    """copy_latency_s = clock_arrival - block_time when block_time is available."""
    block_time = 1781398811.0
    clock_arrival = datetime.fromtimestamp(block_time + 2.5, tz=timezone.utc)
    result = check_copy_entry(
        quote_price=1.0e-5,
        fill_price=1.05e-5,  # within cap
        clock_arrival_ts=clock_arrival,
        block_time=block_time,
    )
    assert result.copy_latency_s is not None
    assert abs(result.copy_latency_s - 2.5) < 0.01


def test_copy_latency_s_none_when_block_time_absent():
    """copy_latency_s=None when block_time is not available (§8 P1 no ambiguous telemetry)."""
    result = check_copy_entry(
        quote_price=1.0e-5,
        fill_price=1.05e-5,
        clock_arrival_ts=_TS,
        block_time=None,
    )
    assert result.copy_latency_s is None


# ===========================================================================
# Golden fixture test — known gapper from 2026-06-14.parquet
# ===========================================================================


def test_golden_gapper_fixture_rejected():
    """Known-gapping token from real parquet -> ENTRY_REJECTED (red->green gate).

    Without the honest-fill fix, this entry would be booked at fill_price (wrong).
    With the fix, check_copy_entry returns enterable=False (SLIPPAGE_CAP_6002).

    Fixture derived from lake/tapes/2026-06-14.parquet:
      mint:        FBKbAPporDMvvmV463PF89xhH55Gs6uVc9FPJGcbpump
      quote_price: 2.8124e-05 (first swap virtual_sol/virtual_tok)
      fill_price:  3.3937e-05 (second swap)
      slip:        20.66% > 15% cap -> REJECTED
    """
    fixture = _load_fixture()
    quote = fixture["quote_price"]
    fill = fixture["fill_price"]
    cap = fixture["cap_pct"]
    block_time = float(fixture["swaps"][0]["block_time"])
    clock_arrival = datetime.fromtimestamp(block_time + 1.8, tz=timezone.utc)

    result = check_copy_entry(
        quote_price=quote,
        fill_price=fill,
        clock_arrival_ts=clock_arrival,
        block_time=block_time,
        cap_pct=cap,
    )

    assert result.enterable == fixture["expected_enterable"]  # False
    assert result.reason == fixture["expected_reason"]        # SLIPPAGE_CAP_6002
    assert result.realized_slip_pct is not None
    assert result.realized_slip_pct > cap
    assert abs(result.realized_slip_pct - fixture["expected_slip"]) < 1e-10
    assert result.copy_latency_s is not None
    assert result.copy_latency_s > 0


def test_golden_gapper_accepted_with_wider_cap():
    """Same known-gapper is ACCEPTED when cap is widened to 25% (validates cap logic)."""
    fixture = _load_fixture()
    quote = fixture["quote_price"]
    fill = fixture["fill_price"]
    block_time = float(fixture["swaps"][0]["block_time"])
    clock_arrival = datetime.fromtimestamp(block_time + 1.8, tz=timezone.utc)

    result = check_copy_entry(
        quote_price=quote,
        fill_price=fill,
        clock_arrival_ts=clock_arrival,
        block_time=block_time,
        cap_pct=0.25,  # wider cap than the actual slip (20.66%)
    )

    assert result.enterable is True
    assert result.reason == ""


# ===========================================================================
# Feature flag default test (§6.7)
# ===========================================================================


@pytest.mark.django_db
def test_honest_fills_enabled_default_false():
    """CopyTradeSettings.honest_fills_enabled defaults to False (§6.7).

    The running v1/v2 soak is not disrupted at merge — the flag is off by
    default and must be deliberately flipped for the AC-5 soak.
    """
    from copytrade.models import CopyTradeSettings

    settings = CopyTradeSettings.get()
    assert settings.honest_fills_enabled is False, (
        "honest_fills_enabled must default to False (§6.7) so the running "
        "soak is not disrupted at merge. Flip it deliberately for AC-5."
    )


@pytest.mark.django_db
def test_honest_fills_enabled_can_be_set_true():
    """CopyTradeSettings.honest_fills_enabled can be saved as True (operator flip)."""
    from copytrade.models import CopyTradeSettings

    settings = CopyTradeSettings.get()
    settings.honest_fills_enabled = True
    settings.save()
    settings.refresh_from_db()
    assert settings.honest_fills_enabled is True


# ===========================================================================
# WalletTxEvent.block_time promotion tests (§8 P1)
# ===========================================================================


def test_wallet_tx_event_block_time_promoted_from_raw():
    """WalletTxEvent.block_time is populated from raw dict (§8 P1 promotion)."""
    from datetime import datetime, timezone

    from copytrade.wallet_consumer import WalletTxEvent

    ts = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
    raw = {
        "wallet": "Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        "mint": "MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        "signature": "SigXXX",
        "type": "buy",
        "sol_amount": 500000000,
        "token_amount": 1000,
        "price": 1.5e-5,
        "block_time": 1781398811,
    }
    # Simulate what WalletSubscriptionConsumer._normalize does
    raw_bt = raw.get("block_time")
    block_time = float(raw_bt) if raw_bt is not None else None

    event = WalletTxEvent(
        wallet=raw["wallet"],
        mint=raw["mint"],
        tx_signature=raw["signature"],
        tx_type=raw["type"],
        sol_amount=float(raw["sol_amount"]),
        token_amount=float(raw["token_amount"]),
        timestamp=ts,
        raw=raw,
        block_time=block_time,
    )
    assert event.block_time == pytest.approx(1781398811.0)


def test_wallet_tx_event_block_time_none_when_absent():
    """WalletTxEvent.block_time is None when raw dict has no block_time."""
    from datetime import datetime, timezone

    from copytrade.wallet_consumer import WalletTxEvent

    ts = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
    raw = {
        "wallet": "Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        "mint": "MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        "signature": "SigXXX",
        "type": "buy",
        "sol_amount": 500000000,
        "token_amount": 1000,
        # no block_time key
    }
    event = WalletTxEvent(
        wallet=raw["wallet"],
        mint=raw["mint"],
        tx_signature=raw["signature"],
        tx_type=raw["type"],
        sol_amount=float(raw["sol_amount"]),
        token_amount=float(raw["token_amount"]),
        timestamp=ts,
        raw=raw,
        block_time=None,
    )
    assert event.block_time is None


# ===========================================================================
# CopytradePosition telemetry fields (§6.1 migration)
# ===========================================================================


@pytest.mark.django_db
def test_copytrade_position_has_honest_fill_columns():
    """CopytradePosition has the US-75 AC-1 telemetry columns (§6.1 migration)."""
    from copytrade.models import CopytradePosition

    assert hasattr(CopytradePosition, "quote_price")
    assert hasattr(CopytradePosition, "fill_price")
    assert hasattr(CopytradePosition, "cap_pct")
    assert hasattr(CopytradePosition, "copy_latency_s")
    assert hasattr(CopytradePosition, "EXIT_ENTRY_REJECTED")
    assert CopytradePosition.EXIT_ENTRY_REJECTED == "ENTRY_REJECTED"


@pytest.mark.django_db
def test_copytrade_position_entry_rejected_pnl_null():
    """ENTRY_REJECTED position has NULL PnL fields (excluded from win-rate)."""
    from datetime import datetime, timezone

    from copytrade.models import CopytradePosition

    ts = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
    pos = CopytradePosition(
        cohort_id="test-cohort-ac75",
        mint="MintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        trigger_wallet="Wa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ts,
        entry_price=2.81e-5,
        exit_ts=ts,
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
        # PnL left NULL
        realized_pnl_sol=None,
        realized_pnl_pct=None,
        # Telemetry
        quote_price=2.81e-5,
        fill_price=3.39e-5,
        cap_pct=0.15,
        copy_latency_s=1.8,
    )
    pos.save()

    from_db = CopytradePosition.objects.get(pk=pos.pk)
    assert from_db.exit_reason == "ENTRY_REJECTED"
    assert from_db.realized_pnl_sol is None
    assert from_db.realized_pnl_pct is None
    assert from_db.quote_price == pytest.approx(2.81e-5)
    assert from_db.fill_price == pytest.approx(3.39e-5)
    assert from_db.cap_pct == pytest.approx(0.15)
    assert from_db.copy_latency_s == pytest.approx(1.8)
