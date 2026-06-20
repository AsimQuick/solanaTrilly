# ---
# module: copytrade.tests.test_engine_runtime
# sprint: cutover (copy-trade live); PR B2 curve honest-fill
# story: copytrade-runtime, copytrade-curve-honest-fill
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, copytrade.engine_runtime,
#   copytrade.position_opener, copytrade.curve_price, copytrade.price_source,
#   copytrade.models, copytrade.schemas, copytrade.wallet_consumer, core.clock
# ---
"""Offline tests for the copytrade engine runtime (observe/paper only).

All tests are deterministic — zero network, zero wall-clock — with a fully
injected ``curve_state_fn`` (returns a CurveState) and Clock.  PR B2: the engine
prices on ONE basis (the bonding curve); entry/exit fills are simulated against
injected curve reserves (constant-product + 1% fee + size impact), so a booked
``entry_price`` is the realized fill (≈ spot / 0.99), not the raw signal price.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from copytrade.curve_price import CurveState
from copytrade.engine_runtime import EngineState, handle_event, manage_positions
from copytrade.exits import EXIT_MIRROR, EXIT_SL, EXIT_TIMER, EXIT_TP, EXIT_TRAIL
from copytrade.models import CopytradePosition
from copytrade.position_opener import OpenedPositionRecordV2, open_observe_position_v2
from copytrade.price_source import fetch_mint_price_usd
from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit
from copytrade.wallet_consumer import WalletTxEvent
from core.clock import VirtualClock

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
COHORT_ID = "test-cohort-runtime"
MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBpump"
WALLET_MOON = "WalletMoonSHOTaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_SCALP = "WalletSCALPbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SOL_USD = 150.0
USD_SIZE = 25.0
ENTRY_PRICE = 0.00002
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Huge reserves so a ~0.16 SOL ($25) buy/sell has negligible (<1e-4) size impact,
# making the simulated fill price ≈ spot / 0.99 (just the 1% pump fee).
_VTOK_BIG = 10**18


def _curve(price: Optional[float], *, complete: bool = False) -> Optional[CurveState]:
    """Build a CurveState whose spot_price() == ``price`` (None -> unreadable curve)."""
    if price is None or price <= 0:
        return None
    vsol = int(price * _VTOK_BIG)
    return CurveState(
        virtual_sol_reserves=vsol, virtual_token_reserves=_VTOK_BIG, complete=complete
    )


# cohort moonshot exit: SL 50%, trailing 40%, TP 900%
MOON_EXIT = OurTrailingExit(
    type="our_trailing", stop_loss_pct=50, trailing_giveback_pct=40, take_profit_pct=900
)
# cohort scalp exit: mirror, 24h fallback, no hard stop
SCALP_EXIT = MirrorWalletSellExit(
    type="mirror_wallet_sell", max_hold_seconds=86400, hard_stop_loss_pct=None
)

WALLET_TO_STRATEGY = {
    WALLET_MOON: "moonshot",
    WALLET_SCALP: "consistent_scalp",
}
EXIT_BY_STRATEGY = {
    "moonshot": MOON_EXIT,
    "consistent_scalp": SCALP_EXIT,
}


def _make_settings(
    *,
    mode: str = "observe",
    min_trigger_buy_usd: float = 250.0,
    usd_size_per_trade: float = USD_SIZE,
    max_concurrent_positions: int = 30,
    copy_first_buy_only: bool = True,
    dedupe_token_across_wallets: bool = True,
    active_cohort_id: str = COHORT_ID,
    honest_fills_enabled: bool = False,
):
    """Build a minimal settings shim for tests."""
    s = MagicMock()
    s.mode = mode
    s.min_trigger_buy_usd = min_trigger_buy_usd
    s.usd_size_per_trade = usd_size_per_trade
    s.max_concurrent_positions = max_concurrent_positions
    s.copy_first_buy_only = copy_first_buy_only
    s.dedupe_token_across_wallets = dedupe_token_across_wallets
    s.active_cohort_id = active_cohort_id
    s.honest_fills_enabled = honest_fills_enabled
    return s


def _make_event(
    *,
    wallet: str = WALLET_MOON,
    mint: str = MINT_A,
    tx_type: str = "buy",
    sol_amount: float = 2.0,  # 2 SOL * $150 = $300 => >= $250 trigger
    timestamp: datetime = T0,
    raw: Optional[dict] = None,
) -> WalletTxEvent:
    if raw is None:
        raw = {"program": PUMP_PROGRAM, "price": ENTRY_PRICE}
    return WalletTxEvent(
        wallet=wallet,
        mint=mint,
        tx_signature="sig123",
        tx_type=tx_type,
        sol_amount=sol_amount,
        token_amount=1000.0,
        timestamp=timestamp,
        raw=raw,
    )


def _fresh_state(**overrides) -> EngineState:
    kw = dict(
        wallet_to_strategy=WALLET_TO_STRATEGY,
        exit_by_strategy=EXIT_BY_STRATEGY,
    )
    kw.update(overrides)
    return EngineState.new(**kw)


def _open(state, clock, *, wallet=WALLET_MOON, mint=MINT_A, settings=None, curve_price=ENTRY_PRICE):
    """Helper: open a position via handle_event on a live curve."""
    return handle_event(
        _make_event(wallet=wallet, mint=mint, sol_amount=2.0),
        settings=settings or _make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(curve_price),
        clock=clock,
    )


# ===========================================================================
# open_observe_position_v2 tests (direct opener — no curve sim)
# ===========================================================================


@pytest.mark.django_db
def test_v2_opener_sol_in_matches_usd_divided_by_sol_usd():
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.sol_in == pytest.approx(USD_SIZE / SOL_USD, rel=1e-6)


@pytest.mark.django_db
def test_v2_opener_size_usd_set_correctly():
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.size_usd == pytest.approx(USD_SIZE)


@pytest.mark.django_db
def test_v2_opener_strategy_id_tagged():
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.strategy_id == "moonshot"


@pytest.mark.django_db
def test_v2_opener_high_water_seeded_to_entry():
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.high_water_price == pytest.approx(ENTRY_PRICE)


@pytest.mark.django_db
def test_v2_opener_entry_tokens_persisted():
    """PR B2: entry_tokens (held base units) is stored when provided."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD,
        strategy_id="moonshot", entry_tokens=123456.0,
    )
    assert pos.entry_tokens == pytest.approx(123456.0)


@pytest.mark.django_db
def test_v2_opener_shared_position_written_and_linked():
    from trading.models import Position as SharedPosition

    assert SharedPosition.objects.count() == 0
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.shared_position_id is not None
    shared = SharedPosition.objects.get(pk=pos.shared_position_id)
    assert shared.mint == MINT_A
    assert shared.source == SharedPosition.SOURCE_COPYTRADE
    assert shared.mode == SharedPosition.MODE_OBSERVE
    assert shared.status == SharedPosition.STATUS_PAPER


@pytest.mark.django_db
def test_v2_opener_mode_observe_status_open():
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.mode == CopytradePosition.MODE_OBSERVE
    assert pos.status == CopytradePosition.STATUS_OPEN


# ===========================================================================
# handle_event tests (curve honest-fill)
# ===========================================================================


@pytest.mark.django_db
def test_handle_event_big_buy_opens_position():
    state = _fresh_state()
    pos = _open(state, VirtualClock(T0), wallet=WALLET_MOON, mint=MINT_A)
    assert pos is not None
    assert pos.mint == MINT_A
    assert pos.strategy_id == "moonshot"
    assert pos.mode == CopytradePosition.MODE_OBSERVE


@pytest.mark.django_db
def test_handle_event_books_curve_fill_not_raw_price():
    """Entry is OUR simulated curve fill (≈ spot/0.99 from the 1% fee), with
    entry_tokens recorded — NOT the wallet's raw quote price (a phantom fill)."""
    state = _fresh_state()
    pos = _open(state, VirtualClock(T0), curve_price=ENTRY_PRICE)
    assert pos is not None
    # 1% fee inflates the effective buy price slightly above curve spot.
    assert pos.entry_price == pytest.approx(ENTRY_PRICE / 0.99, rel=1e-3)
    assert pos.entry_price > ENTRY_PRICE
    assert pos.entry_tokens is not None and pos.entry_tokens > 0


@pytest.mark.django_db
def test_handle_event_records_slip_telemetry():
    """quote (wallet curve price) vs our curve fill -> realized-slip telemetry."""
    state = _fresh_state()
    # wallet quoted slightly below where the curve is now (we fill a touch higher).
    quote = ENTRY_PRICE * 0.98
    pos = handle_event(
        _make_event(raw={"program": PUMP_PROGRAM, "price": quote}),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=VirtualClock(T0),
    )
    assert pos is not None
    assert pos.quote_price == pytest.approx(quote)
    assert pos.fill_price == pytest.approx(pos.entry_price)
    assert pos.cap_pct == pytest.approx(0.15)


@pytest.mark.django_db
def test_handle_event_curve_unavailable_skips():
    """No readable bonding curve (graduated/None) -> skip the open (no phantom fill)."""
    state = _fresh_state()
    pos = handle_event(
        _make_event(),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: None,
        clock=VirtualClock(T0),
    )
    assert pos is None
    assert len(state.open_positions) == 0
    # multiplicity was undone
    assert state.multiplicity.open_positions_count == 0


@pytest.mark.django_db
def test_handle_event_graduated_curve_skips():
    """A complete (graduated) curve is treated as unavailable -> skip (Phase-2 AMM)."""
    state = _fresh_state()
    pos = handle_event(
        _make_event(),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE, complete=True),
        clock=VirtualClock(T0),
    )
    assert pos is None


@pytest.mark.django_db
def test_handle_event_entry_rejected_when_honest_fills_and_slip_exceeds_cap():
    """honest_fills_enabled + fill > quote·1.15 -> ENTRY_REJECTED (PnL NULL, no open)."""
    state = _fresh_state()
    # Wallet quoted at half the current curve price -> our fill is ~2x the quote -> reject.
    quote = ENTRY_PRICE * 0.5
    pos = handle_event(
        _make_event(raw={"program": PUMP_PROGRAM, "price": quote}),
        settings=_make_settings(honest_fills_enabled=True),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=VirtualClock(T0),
        honest_fills_enabled=True,
    )
    assert pos is None
    assert len(state.open_positions) == 0
    rejected = CopytradePosition.objects.filter(exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED)
    assert rejected.count() == 1
    assert rejected.first().realized_pnl_pct is None  # excluded from win-rate


@pytest.mark.django_db
def test_handle_event_flag_off_books_despite_high_slip():
    """With honest_fills_enabled OFF, a high-slip fill still books (telemetry only)."""
    state = _fresh_state()
    quote = ENTRY_PRICE * 0.5
    pos = handle_event(
        _make_event(raw={"program": PUMP_PROGRAM, "price": quote}),
        settings=_make_settings(honest_fills_enabled=False),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=VirtualClock(T0),
    )
    assert pos is not None  # booked (no rejection when flag off)
    assert pos.quote_price == pytest.approx(quote)  # but slip telemetry still recorded


@pytest.mark.django_db
def test_handle_event_small_buy_opens_nothing():
    state = _fresh_state()
    pos = handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=1.0),  # $150 < $250
        settings=_make_settings(min_trigger_buy_usd=250.0),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=VirtualClock(T0),
    )
    assert pos is None
    assert len(state.open_positions) == 0


@pytest.mark.django_db
def test_handle_event_sell_sets_mirror_signal():
    state = _fresh_state()
    clock = VirtualClock(T0)
    _open(state, clock, wallet=WALLET_SCALP, mint=MINT_A)
    assert MINT_A in state.open_positions

    sell_event = _make_event(wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell")
    result = handle_event(
        sell_event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=clock,
    )
    assert result is None
    assert MINT_A in state.sold_signals


@pytest.mark.django_db
def test_handle_event_dedupe_opens_once():
    state = _fresh_state()
    clock = VirtualClock(T0)
    settings = _make_settings(dedupe_token_across_wallets=True)
    pos_a = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A, settings=settings)
    pos_b = _open(state, clock, wallet=WALLET_SCALP, mint=MINT_A, settings=settings)
    assert pos_a is not None
    assert pos_b is None
    assert len(state.open_positions) == 1


@pytest.mark.django_db
def test_handle_event_cap_prevents_extra_open():
    state = _fresh_state()
    clock = VirtualClock(T0)
    settings = _make_settings(
        max_concurrent_positions=1, dedupe_token_across_wallets=False, copy_first_buy_only=False
    )
    pos_a = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A, settings=settings)
    pos_b = _open(state, clock, wallet=WALLET_SCALP, mint=MINT_B, settings=settings)
    assert pos_a is not None
    assert pos_b is None  # cap hit


@pytest.mark.django_db
def test_handle_event_non_pumpfun_token_skipped():
    state = _fresh_state()
    event = WalletTxEvent(
        wallet=WALLET_MOON,
        mint="SomeOtherMintNoSuffixAndNoProgram111111111111",
        tx_signature="sig456",
        tx_type="buy",
        sol_amount=2.0,
        token_amount=100.0,
        timestamp=T0,
        raw={"program": "SOME_OTHER_PROGRAM", "price": ENTRY_PRICE},
    )
    pos = handle_event(
        event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=VirtualClock(T0),
    )
    assert pos is None


# ===========================================================================
# manage_positions tests (curve exit sim)
# ===========================================================================


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_trail():
    state = _fresh_state()
    clock = VirtualClock(T0)
    pos = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A)
    assert pos is not None

    # high_water set to 5x entry by prior ticks; current retraces to 2x -> TRAIL
    # (trail stop = 5x*(1-0.40) = 3x; 2x < 3x).
    pos.high_water_price = pos.entry_price * 5.0
    pos.save()
    state.open_positions[MINT_A].high_water_price = pos.entry_price * 5.0
    current = pos.entry_price * 2.0

    closed = manage_positions(
        state=state,
        curve_state_fn=lambda m: _curve(current),
        clock=clock,
        now=T0 + timedelta(seconds=60),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_TRAIL
    assert MINT_A not in state.open_positions


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_sl():
    state = _fresh_state()
    clock = VirtualClock(T0)
    pos = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A)
    assert pos is not None
    current = pos.entry_price * 0.49  # 51% drop -> SL (stop_loss_pct=50)

    closed = manage_positions(
        state=state,
        curve_state_fn=lambda m: _curve(current),
        clock=clock,
        now=T0 + timedelta(seconds=30),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_SL


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_tp():
    state = _fresh_state()
    clock = VirtualClock(T0)
    pos = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A)
    assert pos is not None
    current = pos.entry_price * 11.0  # >900% gain -> TP (clear of the 10x boundary)

    closed = manage_positions(
        state=state,
        curve_state_fn=lambda m: _curve(current),
        clock=clock,
        now=T0 + timedelta(seconds=120),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_TP


@pytest.mark.django_db
def test_manage_positions_scalp_closes_on_mirror_live_curve():
    """Scalp mirror-closes when the source wallet sells; exit booked via curve sell sim."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    pos = _open(state, clock, wallet=WALLET_SCALP, mint=MINT_A)
    assert pos is not None
    assert pos.strategy_id == "consistent_scalp"

    # Source wallet sells -> set mirror signal
    handle_event(
        _make_event(wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell"),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=clock,
    )
    assert MINT_A in state.sold_signals

    # Curve still live at +30% -> mirror closes, exit booked at our size-impacted sell.
    closed = manage_positions(
        state=state,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE * 1.3),
        clock=clock,
        now=T0 + timedelta(seconds=30),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_MIRROR
    assert MINT_A not in state.open_positions
    assert MINT_A not in state.sold_signals
    # Profitable mirror -> positive realized PnL (the edge, honest round-trip).
    assert float(closed[0].realized_pnl_pct) > 0


@pytest.mark.django_db
def test_manage_positions_mirror_books_at_sell_price_when_curve_gone():
    """When the curve is gone (graduated/None) but the wallet sold, the mirror books
    at the source wallet's curve SELL price (same basis) — not held forever."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    _open(state, clock, wallet=WALLET_SCALP, mint=MINT_A)
    assert MINT_A in state.open_positions

    sell_price = ENTRY_PRICE * 1.3
    handle_event(
        _make_event(
            wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell",
            raw={"program": PUMP_PROGRAM, "price": sell_price},
        ),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        curve_state_fn=lambda m: _curve(ENTRY_PRICE),
        clock=clock,
    )
    assert state.sold_signals[MINT_A] == sell_price

    # Curve now unreadable (None) -> mirror books at the wallet's sell price.
    closed = manage_positions(
        state=state,
        curve_state_fn=lambda m: None,
        clock=clock,
        now=T0 + timedelta(seconds=30),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_MIRROR
    assert closed[0].exit_price == pytest.approx(sell_price)
    assert MINT_A not in state.open_positions


@pytest.mark.django_db
def test_manage_positions_force_closes_dead_token_past_max_hold():
    """A graduated/dead curve (None) with no mirror sell still hits its max-hold TIMER
    (the 15h-stuck bug; also how pre-fix stale opens get cleared)."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    _open(state, clock, wallet=WALLET_SCALP, mint=MINT_A)
    assert MINT_A in state.open_positions

    # Curve gone, no mirror sell, WITHIN max_hold (24h) -> still held.
    closed = manage_positions(
        state=state, curve_state_fn=lambda m: None, clock=clock, now=T0 + timedelta(hours=1)
    )
    assert closed == []
    assert MINT_A in state.open_positions

    # PAST max_hold -> forced TIMER close at entry (flat, no fabricated gain).
    closed = manage_positions(
        state=state, curve_state_fn=lambda m: None, clock=clock,
        now=T0 + timedelta(seconds=86401),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_TIMER
    assert MINT_A not in state.open_positions
    assert abs(float(closed[0].realized_pnl_pct or 0.0)) < 1e-6  # booked flat at entry


@pytest.mark.django_db
def test_manage_positions_curve_unavailable_within_hold_skips_tick():
    """Curve unreadable + no mirror sell + within max_hold -> held (no close)."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    _open(state, clock, wallet=WALLET_MOON, mint=MINT_A)
    assert MINT_A in state.open_positions

    closed = manage_positions(
        state=state, curve_state_fn=lambda m: None, clock=clock, now=T0 + timedelta(seconds=30)
    )
    assert len(closed) == 0
    assert MINT_A in state.open_positions


@pytest.mark.django_db
def test_manage_positions_high_water_persists_across_ticks():
    state = _fresh_state()
    clock = VirtualClock(T0)
    pos = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A)
    pump = pos.entry_price * 3.0

    closed1 = manage_positions(
        state=state, curve_state_fn=lambda m: _curve(pump), clock=clock,
        now=T0 + timedelta(seconds=15),
    )
    assert len(closed1) == 0  # trailing stop not yet triggered (hw just set)
    db_pos = CopytradePosition.objects.get(mint=MINT_A, status=CopytradePosition.STATUS_OPEN)
    assert db_pos.high_water_price >= pump * 0.999


@pytest.mark.django_db
def test_manage_positions_only_fired_position_closes():
    state = _fresh_state()
    clock = VirtualClock(T0)
    settings = _make_settings(copy_first_buy_only=False, dedupe_token_across_wallets=False)
    pos_a = _open(state, clock, wallet=WALLET_MOON, mint=MINT_A, settings=settings)
    _open(state, clock, wallet=WALLET_SCALP, mint=MINT_B, settings=settings)
    assert len(state.open_positions) == 2

    # MINT_A crashes 51% -> SL; MINT_B stays flat.
    def cs(m):
        if m == MINT_A:
            return _curve(pos_a.entry_price * 0.49)
        return _curve(ENTRY_PRICE)

    closed = manage_positions(
        state=state, curve_state_fn=cs, clock=clock, now=T0 + timedelta(seconds=30)
    )
    assert len(closed) == 1
    assert closed[0].mint == MINT_A
    assert closed[0].exit_reason == EXIT_SL
    assert MINT_B in state.open_positions


# ===========================================================================
# price_source tests (the standalone Birdeye REST helper — still used elsewhere)
# ===========================================================================


def test_price_source_parses_valid_payload():
    def fake_fetcher(url, api_key, timeout_s):
        return {"data": {"value": 0.00001234}}

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)
    assert result == pytest.approx(0.00001234)


def test_price_source_returns_none_on_failure():
    def fake_fetcher(url, api_key, timeout_s):
        return None

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)
    assert result is None


def test_price_source_returns_none_on_zero_value():
    def fake_fetcher(url, api_key, timeout_s):
        return {"data": {"value": 0}}

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)
    assert result is None


def test_price_source_returns_none_when_no_api_key():
    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = ""
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A)
    assert result is None


def test_price_source_returns_none_for_empty_mint():
    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd("")
    assert result is None


# ===========================================================================
# EngineState tests
# ===========================================================================


def test_engine_state_new_seeds_multiplicity_from_open_positions():
    mock_pos = MagicMock(spec=CopytradePosition)
    mock_pos.mint = MINT_A
    state = EngineState.new(
        wallet_to_strategy=WALLET_TO_STRATEGY,
        exit_by_strategy=EXIT_BY_STRATEGY,
        open_positions={MINT_A: mock_pos},
    )
    assert state.multiplicity.open_positions_count == 1
    assert MINT_A in state.multiplicity.open_mints


def test_engine_state_new_empty_defaults():
    state = EngineState.new(wallet_to_strategy={}, exit_by_strategy={})
    assert state.multiplicity.open_positions_count == 0
    assert len(state.open_positions) == 0
    assert len(state.sold_signals) == 0
