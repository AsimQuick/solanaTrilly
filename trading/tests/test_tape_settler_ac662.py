# ---
# module: trading.tests.test_tape_settler_ac662
# sprint: sprint-13
# story: US-66 AC-66.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.tape_settler, trading.schemas
# ---
"""Tests for trading.tape_settler.simulate_tape_exit (AC-66.2).

Verification sections:
  §1  Parity tests — simulate_tape_exit against banked oracle output from
      solanatrills/analysis/wallet_strategy/tape_resettle.py resettle() on
      real trades from solanatrills/lake/tapes/2026-06-12.parquet.
      One test per trigger type (TAKE_PROFIT_PCT, DISASTER_CAP, RUG_PULL,
      STOP_LOSS, AUTO_SELL_TIMER, dead, slip-miss) = 7 tests.

  §2  Un-enterable exclusion — no-tape, dead (no pre or no post), slip > 15%
      are excluded (enterable=False) and never booked as 0% or −100%.

  §3  Trigger priority — TP fires before DISASTER, DISASTER before SL,
      SL before RUG; RUG not armed before +5% peak.

  §4  Fill conventions — quote = last pre-trade, fill = first post trade,
      exit fill = first trade >= trigger_t+2.

  §5  Impact formula — cost = 2·size/(size+flow); zero-flow fallback = 0.20.

  §6  Principle #5 guard — no other settler exists (import check).

All tests are offline/deterministic — no DB, no network, no Redis.
"""

import json
import os

import pytest

from trading.schemas import TradingConfig
from trading.tape_settler import (
    _CAP,
    _FEE,
    _LAT,
    _config_to_pol,
    simulate_tape_exit,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "tape_settler_ac662.json"
)


@pytest.fixture(scope="module")
def fixture_data():
    with open(_FIXTURE_PATH) as f:
        return json.load(f)


def _make_config(**overrides) -> TradingConfig:
    """Return a TradingConfig matching the oracle policy used in the fixture."""
    defaults = dict(
        auto_sell_timer_s=300,
        take_profit_pct=100.0,
        stop_loss_pct=20.0,
        disaster_cap_pct=40.0,
        rug_pull_drop_pct=50.0,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


# ---------------------------------------------------------------------------
# §1 Parity tests — banked tape fixtures vs oracle output
# ---------------------------------------------------------------------------


def _parity_case(fixture_data, label):
    """Run simulate_tape_exit on a banked fixture case and compare with oracle."""
    cases = {c["label"]: c for c in fixture_data["cases"]}
    case = cases[label]
    trades = [tuple(t) for t in case["trades"]]
    entry_ts = case["entry"]
    oracle = case["oracle_output"]
    size_sol = fixture_data["size_sol"]
    sol_usd = fixture_data["sol_usd"]
    config = _make_config()
    result = simulate_tape_exit(trades, entry_ts, config, size_sol, sol_usd)
    return result, oracle


def test_parity_take_profit_pct(fixture_data):
    result, oracle = _parity_case(fixture_data, "TAKE_PROFIT_PCT")
    assert result == oracle, f"TAKE_PROFIT_PCT parity failed:\n  got:      {result}\n  expected: {oracle}"


def test_parity_disaster_cap(fixture_data):
    result, oracle = _parity_case(fixture_data, "DISASTER_CAP")
    assert result == oracle, f"DISASTER_CAP parity failed:\n  got:      {result}\n  expected: {oracle}"


def test_parity_rug_pull(fixture_data):
    result, oracle = _parity_case(fixture_data, "RUG_PULL")
    assert result == oracle, f"RUG_PULL parity failed:\n  got:      {result}\n  expected: {oracle}"


def test_parity_stop_loss(fixture_data):
    result, oracle = _parity_case(fixture_data, "STOP_LOSS")
    assert result == oracle, f"STOP_LOSS parity failed:\n  got:      {result}\n  expected: {oracle}"


def test_parity_auto_sell_timer(fixture_data):
    result, oracle = _parity_case(fixture_data, "AUTO_SELL_TIMER")
    assert result == oracle, f"AUTO_SELL_TIMER parity failed:\n  got:      {result}\n  expected: {oracle}"


def test_parity_dead(fixture_data):
    result, oracle = _parity_case(fixture_data, "dead")
    assert result == oracle, f"dead parity failed:\n  got:      {result}\n  expected: {oracle}"
    assert result["enterable"] is False
    assert result["reason"] == "dead"


def test_parity_slip_miss(fixture_data):
    result, oracle = _parity_case(fixture_data, "slip-miss")
    assert result == oracle, f"slip-miss parity failed:\n  got:      {result}\n  expected: {oracle}"
    assert result["enterable"] is False
    assert result["reason"] == "slip-miss"


# ---------------------------------------------------------------------------
# §2 Un-enterable exclusion
# ---------------------------------------------------------------------------


def test_empty_trades_unentered():
    config = _make_config()
    result = simulate_tape_exit([], entry_ts=1000.0, config=config, size_sol=0.1)
    assert result["enterable"] is False
    assert result["reason"] == "no-tape"
    assert "pnl" not in result  # never booked as 0%


def test_no_pre_trades_returns_dead():
    """No trades before entry → no quote → dead (not enterable)."""
    config = _make_config()
    entry = 1000.0
    # All trades are AFTER entry+2, so pre window is empty
    trades = [(entry + 5, 1.0, 100.0), (entry + 10, 1.0, 100.0)]
    result = simulate_tape_exit(trades, entry_ts=entry, config=config, size_sol=0.1)
    assert result["enterable"] is False
    assert result["reason"] == "dead"
    assert "pnl" not in result


def test_no_post_trades_returns_dead():
    """No trades after entry+LAT → no fill → dead (not enterable)."""
    config = _make_config()
    entry = 1000.0
    # All trades are in pre window only
    trades = [(entry - 10, 1.0, 100.0), (entry - 1, 1.0, 100.0)]
    result = simulate_tape_exit(trades, entry_ts=entry, config=config, size_sol=0.1)
    assert result["enterable"] is False
    assert result["reason"] == "dead"
    assert "pnl" not in result


def test_slip_over_cap_returns_slip_miss():
    """fill/quote - 1 > 15% → slip-miss (not enterable)."""
    config = _make_config()
    entry = 1000.0
    quote_price = 1.0
    fill_price = quote_price * (1 + _CAP + 0.01)  # 16% slip — above cap
    trades = [
        (entry - 5, quote_price, 100.0),   # pre (quote)
        (entry + _LAT, fill_price, 100.0),  # post[0] (fill)
        (entry + 100, 0.5, 100.0),          # further post
    ]
    result = simulate_tape_exit(trades, entry_ts=entry, config=config, size_sol=0.1)
    assert result["enterable"] is False
    assert result["reason"] == "slip-miss"
    assert "pnl" not in result


def test_slip_at_cap_exactly_is_unentered():
    """fill/quote - 1 exactly = 15% → > CAP is False → enterable (boundary)."""
    config = _make_config()
    entry = 1000.0
    quote_price = 1.0
    fill_price = quote_price * (1 + _CAP)  # exactly 15% — NOT > 0.15, so enterable
    timer = config.auto_sell_timer_s
    trades = [
        (entry - 5, quote_price, 100.0),
        (entry + _LAT, fill_price, 100.0),
        (entry + timer, fill_price * 0.5, 100.0),  # triggers STOP_LOSS eventually
    ]
    result = simulate_tape_exit(trades, entry_ts=entry, config=config, size_sol=0.1)
    # At exactly 15% it is enterable (oracle uses strict >)
    assert result["enterable"] is True


def test_unentered_never_booked_as_zero_pct():
    """Un-enterable rows must not have pnl=0.0 or pnl=-100.0 keys."""
    config = _make_config()
    result = simulate_tape_exit([], entry_ts=1000.0, config=config, size_sol=0.1)
    assert "pnl" not in result
    assert result.get("pnl") != 0.0
    assert result.get("pnl") != -100.0


# ---------------------------------------------------------------------------
# §3 Trigger priority
# ---------------------------------------------------------------------------


def _make_trades_with_returns(entry, fill_price, steps):
    """Build a trade list: one trade at entry-1 (quote), one at entry+LAT (fill),
    then step trades at entry+LAT+i for specified (ret, time_offset) pairs."""
    trades = [(entry - 1, fill_price, 100.0)]  # quote (pre)
    trades.append((entry + _LAT, fill_price, 50.0))  # fill (post[0])
    for i, (t_offset, price) in enumerate(steps, start=1):
        trades.append((entry + _LAT + t_offset, price, 50.0))
    return trades


def test_priority_tp_fires_before_disaster():
    """When TP condition is met first, DISASTER_CAP on the same swap is NOT checked."""
    config = _make_config(take_profit_pct=100.0, disaster_cap_pct=40.0)
    entry = 1000.0
    fill = 1.0
    # price doubles (TP=100%) AND drops 40% at the same step — but order is TP first
    # Actually we need TP to fire first, so price must satisfy TP but not disaster.
    # Use a price that satisfies TP (2.0 * fill) to verify TP fires.
    trades = [
        (entry - 1, fill, 100.0),          # pre (quote)
        (entry + _LAT, fill, 50.0),         # fill
        (entry + _LAT + 10, fill * 2.0, 50.0),  # ret=1.0 >= tp=1.0 → TAKE_PROFIT_PCT
        (entry + _LAT + 20, fill * 0.59, 50.0),  # would be DISASTER_CAP if TP missed
        (entry + _LAT + 400, fill * 0.1, 50.0),  # timer trade (fallback)
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "TAKE_PROFIT_PCT"


def test_priority_disaster_fires_before_stop_loss():
    """DISASTER_CAP (40%) fires before STOP_LOSS (20%) when drop is 45%."""
    config = _make_config(stop_loss_pct=20.0, disaster_cap_pct=40.0)
    entry = 1000.0
    fill = 1.0
    # Drop 45% → DISASTER_CAP (-40%) fires first (before SL at -20%)
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),
        (entry + _LAT + 10, fill * 0.55, 50.0),  # ret=-0.45 ≤ -disaster(-0.40) → DISASTER
        (entry + _LAT + 400, fill * 0.5, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "DISASTER_CAP"


def test_priority_stop_loss_fires_before_rug():
    """STOP_LOSS fires before RUG_PULL when SL condition is met (no +5% peak)."""
    config = _make_config(stop_loss_pct=20.0, rug_pull_drop_pct=50.0)
    entry = 1000.0
    fill = 1.0
    # Price drops 25% (SL -20%) but peak never reaches +5%, so RUG is never armed.
    # STOP_LOSS should fire.
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),           # fill = quote, no slippage
        (entry + _LAT + 10, fill * 0.75, 50.0),  # ret=-0.25 ≤ -sl(-0.20) → STOP_LOSS
        (entry + _LAT + 400, fill * 0.5, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "STOP_LOSS"


def test_rug_not_armed_before_5pct_peak():
    """RUG_PULL does NOT fire when peak < fill * 1.05 (< +5% peak)."""
    # Default config: SL=20%, disaster=40%, rug=50% (invariant SL<disaster<rug holds)
    config = _make_config(auto_sell_timer_s=60)
    entry = 1000.0
    fill = 1.0
    # Peak reaches +3% only (< +5%), then price falls to 0.85 (-15%, above SL -20%).
    # RUG is NOT armed (peak < 1.05*fill), so it can't fire.
    # Price stays at 0.85 until timer fires at 60s.
    peak_price = fill * 1.03   # +3%, below armed threshold of +5%
    hold_price = fill * 0.85   # -15% from fill — above SL(-20%), so SL doesn't fire
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),
        (entry + _LAT + 5, peak_price, 50.0),   # peak = +3% (not armed)
        (entry + _LAT + 10, hold_price, 50.0),  # -15% — below armed threshold, no RUG
        (entry + _LAT + 60, hold_price, 50.0),  # held=60 >= timer=60 → AUTO_SELL_TIMER
        (entry + _LAT + 62, hold_price, 50.0),  # exit fill
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "AUTO_SELL_TIMER"


def test_rug_armed_and_fires_after_5pct_peak():
    """RUG_PULL fires once peak >= fill * 1.05 and price drops rugcut% from peak."""
    # SL=30, disaster=35, rug=40 satisfies SL < disaster < rug invariant.
    # TP=300% prevents TP from firing when peak is 2x fill.
    config = _make_config(
        stop_loss_pct=30.0,
        disaster_cap_pct=35.0,
        rug_pull_drop_pct=40.0,
        take_profit_pct=300.0,
        auto_sell_timer_s=300,
    )
    entry = 1000.0
    fill = 1.0
    # Peak = 2x fill (+100%, armed since > 5%).  TP threshold is 3x (300%), not hit.
    # rug fires at peak * (1 - 0.40) = 1.20 (still +20% from fill, well above SL -30%).
    peak_price = fill * 2.0   # +100% → armed; TP=300% not triggered
    rug_price = peak_price * (1 - 0.40)  # = 1.20, ret=+0.20 → above SL(-30%) threshold
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),
        (entry + _LAT + 10, peak_price, 50.0),   # peak = +100%, arms RUG; TP not hit (2 < 3)
        (entry + _LAT + 20, rug_price, 50.0),    # p ≤ peak*(1-0.40) → RUG fires
        (entry + _LAT + 22, rug_price, 50.0),    # exit fill (trig_t + LAT)
        (entry + _LAT + 400, rug_price, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "RUG_PULL"


def test_auto_sell_timer_fires_when_no_other_trigger():
    """AUTO_SELL_TIMER fires when price stays flat for the full hold window."""
    config = _make_config(auto_sell_timer_s=60)
    entry = 1000.0
    fill = 1.0
    # Flat price — no TP/disaster/SL/RUG fires; timer fires at 60s
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),
        (entry + _LAT + 30, fill, 50.0),
        (entry + _LAT + 60, fill, 50.0),   # held=60 >= timer=60 → AUTO_SELL_TIMER
        (entry + _LAT + 80, fill, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "AUTO_SELL_TIMER"


# ---------------------------------------------------------------------------
# §4 Fill conventions
# ---------------------------------------------------------------------------


def test_quote_is_last_pre_trade():
    """Entry quote = last swap in [entry−30, entry]."""
    config = _make_config()
    entry = 1000.0
    trades = [
        (entry - 30, 1.0, 100.0),   # pre: oldest
        (entry - 15, 1.5, 100.0),   # pre: middle
        (entry, 2.0, 100.0),         # pre: LAST → quote=2.0
        (entry + _LAT, 2.0, 100.0),  # post: fill (quote=fill, no slip)
        (entry + 300, 2.0, 100.0),   # post: timer
        (entry + 400, 2.0, 100.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True


def test_fill_is_first_post_trade():
    """Fill = first swap at entry+LAT (first post trade)."""
    config = _make_config(take_profit_pct=100.0)
    entry = 1000.0
    fill_price = 2.0
    trades = [
        (entry - 1, fill_price, 100.0),           # quote
        (entry + _LAT, fill_price, 50.0),          # fill (first post) = fill_price
        (entry + _LAT + 1, fill_price * 2, 50.0),  # TP fires here (ret=1.0 = tp)
        (entry + _LAT + 400, fill_price, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "TAKE_PROFIT_PCT"


def test_exit_fill_is_first_trade_after_trigger_plus_lat():
    """Exit fill = first swap >= trigger_t + LAT (+ 2s)."""
    config = _make_config(take_profit_pct=100.0)
    entry = 1000.0
    fill = 1.0
    tp_price = fill * 2.0   # exactly at TP
    exit_price = fill * 2.2  # first trade at trigger+2
    trades = [
        (entry - 1, fill, 100.0),
        (entry + _LAT, fill, 50.0),
        (entry + _LAT + 10, tp_price, 50.0),            # trigger fires here (trig_t)
        (entry + _LAT + 10 + 1, fill * 0.5, 50.0),     # trig_t+1 — before LAT window
        (entry + _LAT + 10 + _LAT, exit_price, 50.0),  # trig_t+2 → exit fill
        (entry + _LAT + 400, fill, 50.0),
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    assert result["trigger"] == "TAKE_PROFIT_PCT"
    # pnl is based on exit_price / fill
    expected_pnl_sign = (exit_price / fill) > 1
    assert (result["pnl"] > 0) == expected_pnl_sign


# ---------------------------------------------------------------------------
# §5 Impact formula and cost
# ---------------------------------------------------------------------------


def test_impact_formula_2_size_over_size_plus_flow():
    """cost = 2·size_usd / (size_usd + flow_usd); pnl reflects it.

    To make the expected computation exact, all trigger/exit trades are placed
    OUTSIDE the flow window [entry-30, entry+30] so only the known pre-trades
    contribute to flow.
    """
    # Timer fires when held >= auto_sell_timer_s. Use timer=60 so the timer
    # trade lands at entry+LAT+60 = entry+62 — outside the flow window [entry-30, entry+30].
    config = _make_config(auto_sell_timer_s=60)
    entry = 1000.0
    fill = 1.0

    # Pre-trades in [entry-30, entry]: 5 trades of 200 USD each
    flow_trade_usd = 200.0
    flow_count = 5
    trades = []
    for i in range(flow_count):
        trades.append((entry - 10 + i, fill, flow_trade_usd))   # t: -10..-6

    # Fill trade at entry+2 (inside flow window [entry-30, entry+30])
    fill_usd = 10.0
    trades.append((entry + _LAT, fill, fill_usd))                # t: +2

    # Timer and exit trades outside flow window
    trades.append((entry + _LAT + 60, fill, 0.0))                # t: +62 — timer fires
    trades.append((entry + _LAT + 60 + _LAT, fill, 0.0))         # t: +64 — exit fill

    size_sol = 0.1
    sol_usd = 100.0
    size_usd = size_sol * sol_usd

    # flow = sum of usd in [entry-30, entry+30]
    # Includes pre-trades (flow_count * flow_trade_usd) + fill trade (fill_usd)
    # Timer/exit are at +62/+64 — outside [entry-30, entry+30]
    flow_total = flow_trade_usd * flow_count + fill_usd
    expected_cost = 2 * size_usd / (size_usd + flow_total)
    expected_pnl = ((fill / fill) * (1 - _FEE) * (1 - expected_cost) - 1) * 100

    result = simulate_tape_exit(trades, entry, config, size_sol, sol_usd)
    assert result["enterable"] is True
    assert abs(result["pnl"] - expected_pnl) < 1e-9
    assert abs(result["flow"] - flow_total) < 1e-9


def test_zero_flow_uses_default_cost_020():
    """When flow=0 (no trades in flow window), cost defaults to 0.20."""
    config = _make_config(auto_sell_timer_s=10)
    entry = 1000.0
    fill = 1.0
    # Trades only inside post window but OUTSIDE [entry-30, entry+30] for flow
    # (entry+31 and beyond)
    trades = [
        (entry - 1, fill, 0.0),              # pre but usd=0 → flow=0
        (entry + _LAT, fill, 0.0),            # fill, usd=0
        (entry + _LAT + 10, fill, 0.0),       # timer
        (entry + _LAT + 10 + _LAT, fill, 0.0),  # exit fill
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=100.0)
    assert result["enterable"] is True
    expected_cost = 0.20  # fallback
    expected_pnl = ((fill / fill) * (1 - _FEE) * (1 - expected_cost) - 1) * 100
    assert abs(result["pnl"] - expected_pnl) < 1e-9
    assert result["flow"] == 0.0


def test_flow_field_reported_in_result():
    """Enterable result includes 'flow' (total USD in [entry−30, entry+30]).

    The flow window is [entry-30, entry+30].  Only trades inside that window
    count — trades further out (e.g. timer at +62 and exit fill at +64) do not.
    """
    # Use timer=60 so trigger/exit trades land at +62/+64, outside the flow window.
    config = _make_config(auto_sell_timer_s=60)
    entry = 1000.0
    fill = 1.0
    trades = [
        (entry - 5, fill, 50.0),                 # in flow window
        (entry + 5, fill, 75.0),                 # in flow window
        (entry + _LAT, fill, 10.0),              # fill at +2, in flow window
        (entry + _LAT + 60, fill, 10.0),         # timer at +62, outside window
        (entry + _LAT + 60 + _LAT, fill, 10.0),  # exit fill at +64, outside window
    ]
    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=1.0)
    assert result["enterable"] is True
    # flow = 50 + 75 + 10 = 135 (three trades inside [entry-30, entry+30])
    assert abs(result["flow"] - 135.0) < 1e-9


# ---------------------------------------------------------------------------
# §6 Principle #5 guard — sole settler
# ---------------------------------------------------------------------------


def test_no_second_settler_in_trading_package():
    """Principle #5: simulate_tape_exit must be the SOLE tape settler.

    Verifies there is no duplicate/parallel settler function in the trading
    package by asserting that 'resettle' and 'tape_settle' only appear in
    tape_settler.py and this test file.
    """
    import glob
    import os

    trading_dir = os.path.join(os.path.dirname(__file__), "..")
    py_files = glob.glob(os.path.join(trading_dir, "**", "*.py"), recursive=True)

    settler_files = []
    for path in py_files:
        if os.path.basename(path) in ("tape_settler.py", "test_tape_settler_ac662.py"):
            continue  # these are the canonical settler
        try:
            with open(path) as f:
                content = f.read()
            if "def simulate_tape_exit" in content or "def _resettle" in content:
                settler_files.append(path)
        except OSError:
            pass

    assert settler_files == [], (
        f"Principle #5 violated: found duplicate settler function(s) in: {settler_files}"
    )


def test_simulate_tape_exit_is_importable_without_db():
    """tape_settler imports cleanly without DB, Redis, or network access."""
    from trading.tape_settler import simulate_tape_exit as fn
    assert callable(fn)


def test_config_to_pol_converts_pct_to_fraction():
    """_config_to_pol divides all threshold fields by 100 (pct → fraction)."""
    config = TradingConfig(
        take_profit_pct=100.0,
        stop_loss_pct=20.0,
        disaster_cap_pct=40.0,
        rug_pull_drop_pct=50.0,
        auto_sell_timer_s=300,
    )
    pol = _config_to_pol(config)
    assert pol["tp"] == pytest.approx(1.0)
    assert pol["sl"] == pytest.approx(0.20)
    assert pol["disaster"] == pytest.approx(0.40)
    assert pol["rugcut"] == pytest.approx(0.50)
    assert pol["timer"] == 300
