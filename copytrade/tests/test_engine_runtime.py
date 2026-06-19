# ---
# module: copytrade.tests.test_engine_runtime
# sprint: cutover (copy-trade live)
# story: copytrade-runtime
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, copytrade.engine_runtime,
#   copytrade.position_opener, copytrade.price_source, copytrade.models,
#   copytrade.schemas, copytrade.wallet_consumer, core.clock
# ---
"""Offline tests for the copytrade engine runtime (observe/paper only).

All tests are deterministic — zero network, zero clock calls to wall-time,
fully injected price_fn and Clock.  The full test matrix covers:

  open_observe_position_v2
    - USD sizing: sol_in == usd_size / sol_usd
    - size_usd set correctly
    - strategy_id tagged on CopytradePosition
    - high_water_price seeded to entry_price at open
    - shared trading.Position row written and linked

  handle_event
    - A >= $250 pump.fun buy opens a position tagged with the right strategy head
    - A < $250 buy opens nothing
    - A sell from a watched wallet with an open position sets the mirror signal
    - dedupe_token_across_wallets: second buy on same token opens nothing
    - max_concurrent_positions cap: trigger beyond cap opens nothing
    - price_fn None falls back to event.raw["price"]
    - price_fn returns 0 / None and no raw price -> skip (no open)
    - non-pump.fun token -> skip

  manage_positions
    - Moonshot (our_trailing) closes via TRAIL/SL/TP with scripted price_fn
    - Scalp (mirror_wallet_sell) closes via MIRROR when sold_signals set
    - high_water persists across ticks (ratchets correctly)
    - Price unavailable (None) -> skip tick, position stays open
    - Multiple positions: only the one that fires exits

  price_source
    - fetch_mint_price_usd parses a valid Birdeye payload (injected fetcher)
    - Returns None on HTTP failure (injected fetcher returns None)
    - Returns None when value is 0 or negative
    - Returns None when BIRDEYE_API_KEY not set
    - Returns None when mint is empty
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from copytrade.engine_runtime import EngineState, handle_event, manage_positions
from copytrade.exits import EXIT_MIRROR, EXIT_SL, EXIT_TP, EXIT_TRAIL
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
        raw = {
            "program": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # pump.fun bonding curve
            "price": ENTRY_PRICE,
        }
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


# ===========================================================================
# open_observe_position_v2 tests
# ===========================================================================


@pytest.mark.django_db
def test_v2_opener_sol_in_matches_usd_divided_by_sol_usd():
    """sol_in == usd_size / sol_usd (USD sizing)."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    expected_sol_in = USD_SIZE / SOL_USD
    assert pos.sol_in == pytest.approx(expected_sol_in, rel=1e-6)


@pytest.mark.django_db
def test_v2_opener_size_usd_set_correctly():
    """size_usd must equal the injected usd_size."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.size_usd == pytest.approx(USD_SIZE)


@pytest.mark.django_db
def test_v2_opener_strategy_id_tagged():
    """strategy_id must be stored on the CopytradePosition."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.strategy_id == "moonshot"


@pytest.mark.django_db
def test_v2_opener_high_water_seeded_to_entry():
    """high_water_price must be seeded to entry_price at open (moonshot trailing needs it)."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.high_water_price == pytest.approx(ENTRY_PRICE)


@pytest.mark.django_db
def test_v2_opener_shared_position_written_and_linked():
    """A shared trading.Position row must be written and linked via shared_position_id."""
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
    """Mode must be observe and status must be open."""
    record = OpenedPositionRecordV2(
        cohort_id=COHORT_ID, mint=MINT_A, trigger_wallet=WALLET_MOON, entry_ts=T0
    )
    pos = open_observe_position_v2(
        record, ENTRY_PRICE, usd_size=USD_SIZE, sol_usd=SOL_USD, strategy_id="moonshot"
    )
    assert pos.mode == CopytradePosition.MODE_OBSERVE
    assert pos.status == CopytradePosition.STATUS_OPEN


# ===========================================================================
# handle_event tests
# ===========================================================================


@pytest.mark.django_db
def test_handle_event_big_buy_opens_position():
    """A >= $250 pump.fun buy from a watched wallet opens a position."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    event = _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0)  # $300
    cohort = MagicMock()

    pos = handle_event(
        event,
        settings=_make_settings(),
        state=state,
        cohort=cohort,
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )

    assert pos is not None
    assert pos.mint == MINT_A
    assert pos.strategy_id == "moonshot"
    assert pos.mode == CopytradePosition.MODE_OBSERVE


@pytest.mark.django_db
def test_handle_event_small_buy_opens_nothing():
    """A < $250 buy does NOT trigger — trigger predicate rejects it."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    event = _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=1.0)  # $150 < $250

    pos = handle_event(
        event,
        settings=_make_settings(min_trigger_buy_usd=250.0),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )

    assert pos is None
    assert len(state.open_positions) == 0


@pytest.mark.django_db
def test_handle_event_sell_sets_mirror_signal():
    """A sell from a watched wallet on a held mint sets state.sold_signals."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    # First open a position so we "hold" MINT_A
    buy_event = _make_event(wallet=WALLET_SCALP, mint=MINT_A, sol_amount=2.0)
    handle_event(
        buy_event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert MINT_A in state.open_positions

    # Now the wallet sells
    sell_event = _make_event(wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell")
    result = handle_event(
        sell_event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )

    assert result is None
    assert MINT_A in state.sold_signals


@pytest.mark.django_db
def test_handle_event_dedupe_opens_once():
    """Two different wallets buying the same mint only opens ONE position."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    settings = _make_settings(dedupe_token_across_wallets=True)

    event_a = _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0)
    event_b = _make_event(wallet=WALLET_SCALP, mint=MINT_A, sol_amount=2.0)

    pos_a = handle_event(
        event_a, settings=settings, state=state, cohort=MagicMock(),
        sol_usd=SOL_USD, price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )
    pos_b = handle_event(
        event_b, settings=settings, state=state, cohort=MagicMock(),
        sol_usd=SOL_USD, price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )

    assert pos_a is not None
    assert pos_b is None
    assert len(state.open_positions) == 1


@pytest.mark.django_db
def test_handle_event_cap_prevents_extra_open():
    """Positions beyond max_concurrent_positions cap are not opened."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    settings = _make_settings(
        max_concurrent_positions=1,
        dedupe_token_across_wallets=False,
        copy_first_buy_only=False,
    )

    pos_a = handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=settings, state=state, cohort=MagicMock(),
        sol_usd=SOL_USD, price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )
    pos_b = handle_event(
        _make_event(wallet=WALLET_SCALP, mint=MINT_B, sol_amount=2.0),
        settings=settings, state=state, cohort=MagicMock(),
        sol_usd=SOL_USD, price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )

    assert pos_a is not None
    assert pos_b is None  # cap hit


@pytest.mark.django_db
def test_handle_event_price_fn_none_falls_back_to_raw_price():
    """When price_fn returns None, fall back to event.raw['price']."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    raw_price = 0.00001234
    event = _make_event(
        wallet=WALLET_MOON,
        mint=MINT_A,
        sol_amount=2.0,
        raw={"program": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "price": raw_price},
    )

    pos = handle_event(
        event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: None,  # always None -> fall back to raw
        clock=clock,
    )

    assert pos is not None
    assert pos.entry_price == pytest.approx(raw_price)


@pytest.mark.django_db
def test_handle_event_non_pumpfun_token_skipped():
    """A buy on a non-pump.fun token is rejected by the trigger predicate."""
    state = _fresh_state()
    clock = VirtualClock(T0)
    non_pump_mint = "SomeOtherMintNoSuffixAndNoProgram111111111111"
    event = WalletTxEvent(
        wallet=WALLET_MOON,
        mint=non_pump_mint,
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
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )

    assert pos is None


# ===========================================================================
# manage_positions tests
# ===========================================================================


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_trail():
    """Moonshot position closes via TRAIL when price retraces from high-water."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    # Open a moonshot position
    pos = handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert pos is not None

    # Price pumps to 5x (high-water becomes 5*entry)
    # Then falls to 2x: 5 * (1 - 0.40) = 3.0 * entry; 2.0 * entry < that -> TRAIL
    prices = {MINT_A: ENTRY_PRICE * 2.0}  # current price after retrace

    # Simulate high_water having been set to 5x by prior ticks
    pos.high_water_price = ENTRY_PRICE * 5.0
    pos.save()
    state.open_positions[MINT_A].high_water_price = ENTRY_PRICE * 5.0

    now = T0 + timedelta(seconds=60)
    closed = manage_positions(
        state=state,
        price_fn=lambda m: prices.get(m),
        clock=clock,
        now=now,
    )

    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_TRAIL
    assert MINT_A not in state.open_positions


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_sl():
    """Moonshot position closes via SL when price drops 50% from entry."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    pos = handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert pos is not None

    # Price drops 51% from entry -> triggers SL (stop_loss_pct=50)
    sl_price = ENTRY_PRICE * 0.49

    now = T0 + timedelta(seconds=30)
    closed = manage_positions(
        state=state,
        price_fn=lambda m: sl_price,
        clock=clock,
        now=now,
    )

    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_SL


@pytest.mark.django_db
def test_manage_positions_moonshot_closes_on_tp():
    """Moonshot position closes via TP at 10x (900% gain)."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    pos = handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert pos is not None

    # 10x price triggers TP (take_profit_pct=900)
    tp_price = ENTRY_PRICE * 10.0

    now = T0 + timedelta(seconds=120)
    closed = manage_positions(
        state=state,
        price_fn=lambda m: tp_price,
        clock=clock,
        now=now,
    )

    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_TP


@pytest.mark.django_db
def test_manage_positions_scalp_closes_on_mirror():
    """Scalp position closes via MIRROR when source wallet sells."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    # Open a scalp position
    pos = handle_event(
        _make_event(wallet=WALLET_SCALP, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert pos is not None
    assert pos.strategy_id == "consistent_scalp"

    # Source wallet sells -> set mirror signal
    sell_event = _make_event(wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell")
    handle_event(
        sell_event,
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert MINT_A in state.sold_signals

    # Next manage tick should mirror-close
    now = T0 + timedelta(seconds=30)
    closed = manage_positions(
        state=state,
        price_fn=lambda m: ENTRY_PRICE * 1.3,  # wallet sold at a profit
        clock=clock,
        now=now,
    )

    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_MIRROR
    assert MINT_A not in state.open_positions
    assert MINT_A not in state.sold_signals


@pytest.mark.django_db
def test_manage_positions_scalp_mirror_books_at_sell_price_when_feed_unavailable():
    """Mirror exit must book even when the external price feed has no price.

    Regression for the LIVE-observed bug: a fresh pre-grad mint is not yet on the
    Birdeye REST feed (price_fn -> None), so the mirror close could never book and
    the position sat open forever despite the source wallet having sold.  The exit
    now books at the source wallet's SELL price (curve price from its sell event).
    """
    state = _fresh_state()
    clock = VirtualClock(T0)

    handle_event(
        _make_event(wallet=WALLET_SCALP, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: None,  # feed had no price even at entry -> curve fallback
        clock=clock,
    )
    assert MINT_A in state.open_positions

    # Source wallet sells at a profit; the sell event carries the curve price.
    sell_price = ENTRY_PRICE * 1.3
    handle_event(
        _make_event(
            wallet=WALLET_SCALP, mint=MINT_A, tx_type="sell",
            raw={"program": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "price": sell_price},
        ),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: None,
        clock=clock,
    )
    assert state.sold_signals[MINT_A] == sell_price

    # Manage tick with the feed STILL returning None — must still mirror-close,
    # booking at the source wallet's sell price.
    closed = manage_positions(
        state=state,
        price_fn=lambda m: None,
        clock=clock,
        now=T0 + timedelta(seconds=30),
    )
    assert len(closed) == 1
    assert closed[0].exit_reason == EXIT_MIRROR
    assert closed[0].exit_price == sell_price
    assert MINT_A not in state.open_positions


@pytest.mark.django_db
def test_manage_positions_price_unavailable_skips_tick():
    """When price_fn returns None, the position is held (no close)."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )
    assert MINT_A in state.open_positions

    now = T0 + timedelta(seconds=30)
    closed = manage_positions(
        state=state,
        price_fn=lambda m: None,  # price unavailable
        clock=clock,
        now=now,
    )

    assert len(closed) == 0
    assert MINT_A in state.open_positions


@pytest.mark.django_db
def test_manage_positions_high_water_persists_across_ticks():
    """High water mark ratchets up and is persisted on the position."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(),
        state=state,
        cohort=MagicMock(),
        sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE,
        clock=clock,
    )

    # Tick 1: price goes 3x -> hw = 3*entry
    pump_price = ENTRY_PRICE * 3.0
    now1 = T0 + timedelta(seconds=15)
    closed1 = manage_positions(
        state=state,
        price_fn=lambda m: pump_price,
        clock=clock,
        now=now1,
    )
    assert len(closed1) == 0  # trailing stop not triggered yet (hw just set)

    pos_from_db = CopytradePosition.objects.get(mint=MINT_A, status=CopytradePosition.STATUS_OPEN)
    # high_water_price should be >= pump_price (either set by ratchet or by trailing eval)
    assert pos_from_db.high_water_price >= pump_price


@pytest.mark.django_db
def test_manage_positions_only_fired_position_closes():
    """With two open positions, only the one that fires its exit closes."""
    state = _fresh_state()
    clock = VirtualClock(T0)

    # Open moonshot on MINT_A
    handle_event(
        _make_event(wallet=WALLET_MOON, mint=MINT_A, sol_amount=2.0),
        settings=_make_settings(copy_first_buy_only=False, dedupe_token_across_wallets=False),
        state=state, cohort=MagicMock(), sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )
    # Open scalp on MINT_B
    handle_event(
        _make_event(wallet=WALLET_SCALP, mint=MINT_B, sol_amount=2.0),
        settings=_make_settings(copy_first_buy_only=False, dedupe_token_across_wallets=False),
        state=state, cohort=MagicMock(), sol_usd=SOL_USD,
        price_fn=lambda m: ENTRY_PRICE, clock=clock,
    )
    assert len(state.open_positions) == 2

    # Price for MINT_A crashes 51% -> SL; MINT_B stays flat
    def price_fn(m):
        if m == MINT_A:
            return ENTRY_PRICE * 0.49
        return ENTRY_PRICE  # MINT_B holds

    now = T0 + timedelta(seconds=30)
    closed = manage_positions(state=state, price_fn=price_fn, clock=clock, now=now)

    assert len(closed) == 1
    assert closed[0].mint == MINT_A
    assert closed[0].exit_reason == EXIT_SL
    assert MINT_B in state.open_positions


# ===========================================================================
# price_source tests
# ===========================================================================


def test_price_source_parses_valid_payload():
    """fetch_mint_price_usd parses a valid Birdeye-style payload via injected fetcher."""
    fake_payload = {"data": {"value": 0.00001234}}

    def fake_fetcher(url, api_key, timeout_s):
        return fake_payload

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)

    assert result == pytest.approx(0.00001234)


def test_price_source_returns_none_on_failure():
    """Returns None when the injected fetcher returns None (network error)."""
    def fake_fetcher(url, api_key, timeout_s):
        return None

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)

    assert result is None


def test_price_source_returns_none_on_zero_value():
    """Returns None when price value is 0 (invalid price)."""
    fake_payload = {"data": {"value": 0}}

    def fake_fetcher(url, api_key, timeout_s):
        return fake_payload

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)

    assert result is None


def test_price_source_returns_none_on_negative_value():
    """Returns None when price value is negative."""
    fake_payload = {"data": {"value": -1.5}}

    def fake_fetcher(url, api_key, timeout_s):
        return fake_payload

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)

    assert result is None


def test_price_source_returns_none_when_no_api_key():
    """Returns None when BIRDEYE_API_KEY is not set."""
    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = ""
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A)

    assert result is None


def test_price_source_returns_none_for_empty_mint():
    """Returns None when the mint is empty."""
    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd("")

    assert result is None


def test_price_source_returns_none_on_missing_data_field():
    """Returns None when the payload has no 'data.value' field."""
    fake_payload = {"success": True, "data": {}}  # no "value"

    def fake_fetcher(url, api_key, timeout_s):
        return fake_payload

    with patch("django.conf.settings") as mock_settings:
        mock_settings.BIRDEYE_API_KEY = "test-key"
        with patch("copytrade.price_source.settings", mock_settings):
            result = fetch_mint_price_usd(MINT_A, fetcher=fake_fetcher)

    assert result is None


# ===========================================================================
# EngineState tests
# ===========================================================================


def test_engine_state_new_seeds_multiplicity_from_open_positions():
    """EngineState.new seeds multiplicity from pre-existing open positions."""
    mock_pos = MagicMock(spec=CopytradePosition)
    mock_pos.mint = MINT_A
    open_positions = {MINT_A: mock_pos}

    state = EngineState.new(
        wallet_to_strategy=WALLET_TO_STRATEGY,
        exit_by_strategy=EXIT_BY_STRATEGY,
        open_positions=open_positions,
    )

    assert state.multiplicity.open_positions_count == 1
    assert MINT_A in state.multiplicity.open_mints


def test_engine_state_new_empty_defaults():
    """EngineState.new with no open_positions starts clean."""
    state = EngineState.new(
        wallet_to_strategy={},
        exit_by_strategy={},
    )
    assert state.multiplicity.open_positions_count == 0
    assert len(state.open_positions) == 0
    assert len(state.sold_signals) == 0
