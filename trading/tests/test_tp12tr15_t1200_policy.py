# ---
# module: trading.tests.test_tp12tr15_t1200_policy
# sprint: hotfix
# story: fix/model-exit-timer-1200
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: trading.models, trading.schemas, trading.tape_settler
# ---
"""Regression tests for the tp12tr15_t1200 exit-policy fix.

Covers:
  §1  Migration verification (DB) — TradingSettings singleton has the correct
      tp12tr15_t1200 values after migrations are applied.
  §2  Schema defaults — TradingConfig() constructed with no args carries the
      tp12tr15_t1200 defaults (so newly-created singletons start correct).
  §3  Pydantic invariants — the new default values satisfy all three ordering
      invariants (slippage, loss-priority, trailing < tp).
  §4  Settler behaviour — with timer=1200 a position with a multi-minute
      post-grad tape no longer trivially closes at ~0 min via AUTO_SELL_TIMER;
      it reaches TP, trailing (RUG_PULL), or the full 1200s timer correctly.
  §5  Field→pol mapping — _config_to_pol maps each TradingConfig field to the
      correct pol dict key and converts pct→fraction correctly.

All tests are offline/deterministic except §1 (DB-backed, @pytest.mark.django_db).
"""

import pytest

from trading.schemas import TradingConfig
from trading.tape_settler import _LAT, _config_to_pol, simulate_tape_exit

# ---------------------------------------------------------------------------
# §1  Migration verification — singleton has tp12tr15_t1200 values (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_singleton_timer_is_1200_after_migration():
    """TradingSettings.get().auto_sell_timer_s must be 1200 (data migration applied)."""
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    assert settings.auto_sell_timer_s == 1200, (
        f"Expected auto_sell_timer_s=1200 (tp12tr15_t1200 policy), "
        f"got {settings.auto_sell_timer_s}. "
        "The data migration 0006_trading_settings_tp12tr15_t1200 must apply the correct value."
    )


@pytest.mark.django_db
def test_singleton_take_profit_is_12_after_migration():
    """TradingSettings.get().take_profit_pct must be 12.0 (tp12 policy)."""
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    assert settings.take_profit_pct == pytest.approx(12.0), (
        f"Expected take_profit_pct=12.0 (tp12 policy), got {settings.take_profit_pct}."
    )


@pytest.mark.django_db
def test_singleton_trailing_is_15_after_migration():
    """TradingSettings.get().rug_pull_drop_pct must be 15.0 (tr15 trailing/giveback).

    In the settler, rug_pull_drop_pct feeds pol['rugcut'] which is the 15%
    trailing/giveback from peak (the RUG_PULL trigger in _resettle).
    """
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    assert settings.rug_pull_drop_pct == pytest.approx(15.0), (
        f"Expected rug_pull_drop_pct=15.0 (tr15 trailing giveback, settler rugcut), "
        f"got {settings.rug_pull_drop_pct}."
    )


@pytest.mark.django_db
def test_singleton_to_schema_carries_correct_policy():
    """TradingSettings.get().to_schema() returns TradingConfig with tp12tr15_t1200 values."""
    from trading.models import TradingSettings

    cfg = TradingSettings.get().to_schema()
    assert cfg.auto_sell_timer_s == 1200
    assert cfg.take_profit_pct == pytest.approx(12.0)
    assert cfg.rug_pull_drop_pct == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# §2  Schema defaults — no-arg TradingConfig() carries tp12tr15_t1200 (pure schema)
# ---------------------------------------------------------------------------


def test_schema_default_timer_is_1200():
    """TradingConfig() default auto_sell_timer_s must be 1200."""
    cfg = TradingConfig()
    assert cfg.auto_sell_timer_s == 1200


def test_schema_default_take_profit_is_12():
    """TradingConfig() default take_profit_pct must be 12.0."""
    cfg = TradingConfig()
    assert cfg.take_profit_pct == pytest.approx(12.0)


def test_schema_default_rugcut_is_15():
    """TradingConfig() default rug_pull_drop_pct must be 15.0 (tr15 trailing)."""
    cfg = TradingConfig()
    assert cfg.rug_pull_drop_pct == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# §3  Pydantic invariants — new defaults must pass all ordering checks
# ---------------------------------------------------------------------------


def test_default_config_validates_without_error():
    """TradingConfig() with new defaults must construct without raising ValidationError."""
    from pydantic import ValidationError

    try:
        cfg = TradingConfig()
    except ValidationError as exc:
        pytest.fail(
            f"TradingConfig() with tp12tr15_t1200 defaults raised ValidationError:\n{exc}"
        )
    assert cfg is not None


def test_slippage_ordering_invariant_holds():
    """Slippage ordering: tight(800) < normal(1500) < loss(2500) still holds."""
    cfg = TradingConfig()
    assert cfg.slippage_tight_bps < cfg.slippage_normal_bps < cfg.slippage_loss_bps


def test_loss_priority_ordering_invariant_holds():
    """Loss-priority ordering: stop_loss(8) < disaster(12) < rug_pull(15) holds."""
    cfg = TradingConfig()
    assert cfg.stop_loss_pct < cfg.disaster_cap_pct < cfg.rug_pull_drop_pct, (
        f"Expected stop_loss({cfg.stop_loss_pct}) < "
        f"disaster({cfg.disaster_cap_pct}) < "
        f"rug_pull({cfg.rug_pull_drop_pct})"
    )


def test_trailing_less_than_tp_invariant_holds():
    """Invariant 3: trailing_pct(10) < take_profit_pct(12) holds."""
    cfg = TradingConfig()
    assert cfg.trailing_pct < cfg.take_profit_pct, (
        f"trailing_pct({cfg.trailing_pct}) must be < take_profit_pct({cfg.take_profit_pct})"
    )


# ---------------------------------------------------------------------------
# §4  Settler behaviour — timer=1200 prevents near-zero AUTO_SELL_TIMER exits
# ---------------------------------------------------------------------------


def _tp12tr15_config(**overrides) -> TradingConfig:
    """Return a TradingConfig with the tp12tr15_t1200 policy values."""
    defaults = dict(
        auto_sell_timer_s=1200,
        take_profit_pct=12.0,
        rug_pull_drop_pct=15.0,
        stop_loss_pct=8.0,
        disaster_cap_pct=12.0,
        trailing_pct=10.0,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def test_timer_1200_does_not_fire_at_zero_on_thin_early_tape():
    """With timer=1200, a sparse early tape (300s of swaps) does NOT exit via
    AUTO_SELL_TIMER at near-zero held time.

    Regression for the root-cause bug: with timer=300 the post window was
    entry+2 <= t <= entry+300+30 = entry+332. On sparse tapes with only a
    handful of early swaps, the first post trade fired AUTO_SELL_TIMER at
    held ~= (first_post_t - entry), sometimes < 60s.

    With timer=1200 the post window is entry+2 <= t <= entry+1230. The tape
    built here only covers 300s of post-grad swaps; the position should remain
    OPEN (enterable but no trigger fires within the window).

    In the settler, when trig_t is None after exhausting all post trades, it
    falls back to post[-1][0] with trigger AUTO_SELL_TIMER. With timer=1200
    and only 300s of post tape, `held` for the last post trade is ~300s < 1200s,
    so the timer condition (held >= timer) never fires mid-walk. The fallback
    at the end sets trig = AUTO_SELL_TIMER at post[-1][0], but the held time
    is the honest wall-clock of the last available swap (not a near-zero exit).
    """
    config = _tp12tr15_config()
    entry = 10000.0
    fill = 1.0

    # Sparse early tape: 5 swaps at regular intervals, all within first 300s
    # Price stays flat — no TP(+12%), no disaster(-12%), no SL(-8%), no rug.
    trades = [
        (entry - 5, fill, 100.0),     # pre-trade (quote)
        (entry + _LAT, fill, 50.0),   # fill (post[0])
        (entry + 60, fill, 50.0),     # 60s after entry
        (entry + 120, fill, 50.0),    # 120s
        (entry + 180, fill, 50.0),    # 180s
        (entry + 300, fill, 50.0),    # 300s — last available swap
        (entry + 302, fill, 50.0),    # exit fill candidate
    ]

    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=140.0)
    assert result["enterable"] is True

    # The AUTO_SELL_TIMER fallback fires at post[-1], which is at entry+302.
    # held = post[-1][0] - entry = 302s — NOT near-zero.
    # With the old timer=300, the timer condition (held >= 300) would have fired
    # at t=entry+302 (held=300), triggering at the very first iteration.
    # With the new timer=1200, the timer condition (held >= 1200) cannot fire
    # within this 302s tape, so trig_t falls back to post[-1] at ~302s held.
    # In either case the exit trigger is AUTO_SELL_TIMER, but with timer=1200
    # the fallback only applies at the end of the tape, not mid-walk.
    assert result["trigger"] == "AUTO_SELL_TIMER"
    # held must be >= 300 (the last trade is at entry+302, held = 302 - entry_lat_adj)
    # _resettle: held = trig_t - entry (NOT trig_t - (entry + _LAT))
    # trig_t = post[-1][0] = entry + 302 for the fallback case with timer=1200
    assert result["held"] >= 300, (
        f"Expected held >= 300s (last swap at entry+302), got {result['held']}s. "
        "With timer=1200, the fallback AUTO_SELL_TIMER fires at the last available "
        "swap, not at near-zero."
    )


def test_timer_300_would_fire_early_but_timer_1200_does_not():
    """Demonstrate the bug fix: timer=300 fires mid-walk; timer=1200 does not.

    With timer=300, the condition `held >= timer` fires during the tape walk
    at the first swap where held >= 300. With timer=1200 the condition never
    fires within a 400s tape, so the fallback at post[-1] applies.

    This is the root-cause regression scenario.
    """
    entry = 10000.0
    fill = 1.0

    # Tape with price that doesn't hit TP(+12%), SL(-8%), or disaster(-12%).
    # Swaps from entry+2 to entry+400.
    trades = [
        (entry - 5, fill, 100.0),     # pre (quote)
        (entry + _LAT, fill, 50.0),   # post[0]: fill
        (entry + 100, fill, 50.0),
        (entry + 200, fill, 50.0),
        (entry + 300, fill, 50.0),    # held=298 if entry+_LAT is reference, but _resettle uses t - (entry+_LAT)
        (entry + 305, fill, 50.0),    # held ~= 303 >= timer=300 → fires with old timer
        (entry + 400, fill, 50.0),
        (entry + 402, fill, 50.0),    # exit fill candidate
    ]

    config_300 = _tp12tr15_config(auto_sell_timer_s=300)
    result_300 = simulate_tape_exit(trades, entry, config_300, size_sol=0.1, sol_usd=140.0)

    config_1200 = _tp12tr15_config(auto_sell_timer_s=1200)
    result_1200 = simulate_tape_exit(trades, entry, config_1200, size_sol=0.1, sol_usd=140.0)

    # Both should be enterable (same tape, same fill)
    assert result_300["enterable"] is True
    assert result_1200["enterable"] is True

    # Both fire AUTO_SELL_TIMER (flat price — no TP/SL/RUG)
    assert result_300["trigger"] == "AUTO_SELL_TIMER"
    assert result_1200["trigger"] == "AUTO_SELL_TIMER"

    # With timer=300, the timer fires mid-walk at a relatively short held time.
    # With timer=1200, the fallback fires at the LAST post trade (entry+402):
    # held = (entry+402) - entry = 402s.
    # The key assertion: timer=1200 produces a LARGER held than timer=300.
    assert result_1200["held"] > result_300["held"], (
        f"Expected timer=1200 to produce longer held than timer=300. "
        f"timer=300 held={result_300['held']}s, timer=1200 held={result_1200['held']}s"
    )


def test_tp_fires_correctly_with_tp12_policy():
    """With take_profit_pct=12, a +12% gain triggers TAKE_PROFIT_PCT."""
    config = _tp12tr15_config()
    entry = 10000.0
    fill = 1.0

    trades = [
        (entry - 5, fill, 100.0),           # pre (quote)
        (entry + _LAT, fill, 50.0),         # fill (0% slip)
        (entry + 60, fill * 1.12, 50.0),    # +12% gain → TAKE_PROFIT_PCT fires
        (entry + 62, fill * 1.12, 50.0),    # exit fill
        (entry + 1200, fill, 50.0),         # well past timer (not reached)
    ]

    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=140.0)
    assert result["enterable"] is True
    assert result["trigger"] == "TAKE_PROFIT_PCT", (
        f"Expected TAKE_PROFIT_PCT, got {result['trigger']}. "
        "A +12% gain should trigger TP at take_profit_pct=12.0."
    )
    assert result["held"] < 1200, (
        f"TP should fire long before the 1200s timer. held={result['held']}s"
    )


def test_trailing_fires_correctly_with_tr15_policy():
    """With rug_pull_drop_pct=15 (tr15), a 15% drop from a +10% peak fires RUG_PULL.

    The settler's RUG_PULL trigger fires when:
      peak / fill > 1.05 (armed at +5% peak)
      AND p <= peak * (1 - rugcut)  where rugcut = rug_pull_drop_pct/100 = 0.15

    Key design constraints to ensure RUG_PULL fires (not TP or SL):
      - Peak must be < TP(+12%) so TP doesn't fire during the peak swap.
      - After the peak the price must drop 15% from peak.
      - The resulting rug_price must stay above the SL threshold to avoid STOP_LOSS
        firing before RUG_PULL.
        SL threshold = fill * (1 - stop_loss_pct/100) = fill * (1 - 0.08) = 0.92
        With peak = fill * 1.10 → rug_price = 1.10 * 0.85 = 0.935 > 0.92 ✓
      - Peak > fill * 1.05 → armed.
      - No disaster: disaster threshold = fill * (1 - 0.12) = 0.88; rug_price(0.935) > 0.88 ✓
    """
    config = _tp12tr15_config()
    entry = 10000.0
    fill = 1.0
    # Peak = +10% from fill: armed (>5%), TP NOT triggered (<12%).
    peak_price = fill * 1.10
    # Rug price = 15% drop from peak = 1.10 * 0.85 = 0.935
    # SL fires at fill * (1 - 0.08) = 0.92 → rug_price(0.935) > 0.92, SL does NOT fire ✓
    # Disaster fires at fill * (1 - 0.12) = 0.88 → rug_price(0.935) > 0.88, disaster does NOT fire ✓
    rug_price = peak_price * (1 - 0.15)  # = 0.935

    trades = [
        (entry - 5, fill, 100.0),            # pre (quote)
        (entry + _LAT, fill, 50.0),          # fill (0% slip)
        (entry + 60, peak_price, 50.0),      # peak = +10%, arms RUG; TP not triggered
        (entry + 120, rug_price, 50.0),      # drop 15% from peak → RUG_PULL fires
        (entry + 122, rug_price, 50.0),      # exit fill (trig_t + LAT)
        (entry + 1200, fill * 0.5, 50.0),   # timer (not reached)
    ]

    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=140.0)
    assert result["enterable"] is True
    assert result["trigger"] == "RUG_PULL", (
        f"Expected RUG_PULL (tr15 trailing), got {result['trigger']}. "
        f"peak={peak_price} (+10%, armed), rug_price={rug_price:.4f} (15% drop from peak). "
        "A 15% drop from a +10% peak should trigger the tr15 trailing stop "
        "(rug_price=0.935 > SL threshold=0.92, so SL must not fire first)."
    )
    assert result["held"] < 1200, (
        f"RUG_PULL should fire before the 1200s timer. held={result['held']}s"
    )


# ---------------------------------------------------------------------------
# §5  Field→pol mapping — _config_to_pol correctness
# ---------------------------------------------------------------------------


def test_config_to_pol_timer():
    """pol['timer'] equals auto_sell_timer_s (not divided by 100)."""
    cfg = TradingConfig(auto_sell_timer_s=1200)
    pol = _config_to_pol(cfg)
    assert pol["timer"] == 1200


def test_config_to_pol_tp_converts_pct_to_fraction():
    """pol['tp'] = take_profit_pct / 100: 12.0 → 0.12."""
    cfg = TradingConfig(take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
                        rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["tp"] == pytest.approx(0.12)


def test_config_to_pol_sl_converts_pct_to_fraction():
    """pol['sl'] = stop_loss_pct / 100: 8.0 → 0.08."""
    cfg = TradingConfig(take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
                        rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["sl"] == pytest.approx(0.08)


def test_config_to_pol_disaster_converts_pct_to_fraction():
    """pol['disaster'] = disaster_cap_pct / 100: 12.0 → 0.12."""
    cfg = TradingConfig(take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
                        rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["disaster"] == pytest.approx(0.12)


def test_config_to_pol_rugcut_is_trailing_giveback():
    """pol['rugcut'] = rug_pull_drop_pct / 100: 15.0 → 0.15 (the tr15 trailing)."""
    cfg = TradingConfig(take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
                        rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["rugcut"] == pytest.approx(0.15), (
        "rug_pull_drop_pct=15.0 must map to pol['rugcut']=0.15 (the tr15 trailing "
        "giveback in the settler — RUG_PULL fires when price drops 15% from peak)."
    )


def test_full_tp12tr15_t1200_pol_dict():
    """End-to-end: TradingConfig defaults → _config_to_pol → tp12tr15_t1200 pol dict."""
    cfg = TradingConfig()  # new defaults
    pol = _config_to_pol(cfg)
    assert pol["timer"] == 1200
    assert pol["tp"] == pytest.approx(0.12)
    assert pol["sl"] == pytest.approx(0.08)
    assert pol["disaster"] == pytest.approx(0.12)
    assert pol["rugcut"] == pytest.approx(0.15)
