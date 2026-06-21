# ---
# module: trading.tests.test_tp12tr15_t1200_policy
# sprint: hotfix, fix/model-exit-faithful-tp12tr15
# story: fix/model-exit-timer-1200, fix/model-exit-faithful-tp12tr15
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: trading.models, trading.schemas, trading.tape_settler
# ---
"""Regression tests for the tp12tr15_t1200 exit-policy fix.

WHY sl/disaster are None (fix/model-exit-faithful-tp12tr15):
  PR #355 invented stop_loss_pct=8.0 and disaster_cap_pct=12.0 because the
  Pydantic invariant at the time required all three of sl/disaster/rug to be
  non-None.  The VALIDATED policy has NEITHER:

    Lab source of truth (v31_winner_check.py line 15):
      dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)

  With sl=8.0 active, every losing observe position was cut at −8% instead of
  riding to the tr15 trailing stop or the 1200s timer — corrupting the model's
  observe measurement with a trigger the model was never validated with.

  Resolution:
    - TradingConfig.stop_loss_pct / disaster_cap_pct are now Optional[float]=None
    - TradingSettings columns are null=True (migration 0007 + 0008)
    - _config_to_pol emits pol["sl"]=None / pol["disaster"]=None when None
    - _resettle already checks `if pol["sl"] is not None` before firing STOP_LOSS
    - _resettle already checks `if pol["disaster"] is not None` before DISASTER_CAP

Covers:
  §1  Migration verification (DB) — TradingSettings singleton has the correct
      tp12tr15_t1200 values after migrations are applied.
  §2  Schema defaults — TradingConfig() constructed with no args carries the
      tp12tr15_t1200 defaults (so newly-created singletons start correct).
  §3  Pydantic invariants — the new default values satisfy all three ordering
      invariants; sl=None / disaster=None are accepted and bypass ordering checks.
  §4  Settler behaviour — with timer=1200 a position with a multi-minute
      post-grad tape no longer trivially closes at ~0 min via AUTO_SELL_TIMER;
      it reaches TP, trailing (RUG_PULL), or the full 1200s timer correctly.
  §5  Field→pol mapping — _config_to_pol maps each TradingConfig field to the
      correct pol dict key and converts pct→fraction correctly; None passes
      through as None.
  §6  STOP_LOSS / DISASTER_CAP never fire — with sl=None / disaster=None the
      settler never emits those triggers even on deeply-negative tapes.

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
def test_singleton_stop_loss_is_none_after_migration():
    """TradingSettings.get().stop_loss_pct must be None after fix migration.

    The validated tp12tr15_t1200 policy has no stop-loss.
    PR #355 invented sl=8.0 to satisfy the old Pydantic invariant; migration
    0008 nulls it out to faithfully represent the lab policy.
    """
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    assert settings.stop_loss_pct is None, (
        f"Expected stop_loss_pct=None (tp12tr15_t1200 has no stop-loss), "
        f"got {settings.stop_loss_pct}. "
        "Migration 0008_data_null_sl_disaster_singleton must null this field."
    )


@pytest.mark.django_db
def test_singleton_disaster_cap_is_none_after_migration():
    """TradingSettings.get().disaster_cap_pct must be None after fix migration.

    The validated tp12tr15_t1200 policy has no disaster cap.
    PR #355 invented disaster=12.0 to satisfy the old Pydantic invariant;
    migration 0008 nulls it out to faithfully represent the lab policy.
    """
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    assert settings.disaster_cap_pct is None, (
        f"Expected disaster_cap_pct=None (tp12tr15_t1200 has no disaster cap), "
        f"got {settings.disaster_cap_pct}. "
        "Migration 0008_data_null_sl_disaster_singleton must null this field."
    )


@pytest.mark.django_db
def test_singleton_to_schema_carries_correct_policy():
    """TradingSettings.get().to_schema() returns TradingConfig with tp12tr15_t1200 values."""
    from trading.models import TradingSettings

    cfg = TradingSettings.get().to_schema()
    assert cfg.auto_sell_timer_s == 1200
    assert cfg.take_profit_pct == pytest.approx(12.0)
    assert cfg.rug_pull_drop_pct == pytest.approx(15.0)
    assert cfg.stop_loss_pct is None, (
        f"Expected stop_loss_pct=None in schema from singleton, got {cfg.stop_loss_pct}"
    )
    assert cfg.disaster_cap_pct is None, (
        f"Expected disaster_cap_pct=None in schema from singleton, got {cfg.disaster_cap_pct}"
    )


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


def test_schema_default_stop_loss_is_none():
    """TradingConfig() default stop_loss_pct must be None.

    The validated tp12tr15_t1200 policy (lab source v31_winner_check.py:15)
    has no stop-loss guard.  PR #355 invented 8.0; this fix restores None.
    """
    cfg = TradingConfig()
    assert cfg.stop_loss_pct is None, (
        f"Expected stop_loss_pct=None (tp12tr15_t1200 has no stop-loss), "
        f"got {cfg.stop_loss_pct}"
    )


def test_schema_default_disaster_cap_is_none():
    """TradingConfig() default disaster_cap_pct must be None.

    The validated tp12tr15_t1200 policy (lab source v31_winner_check.py:15)
    has no disaster-cap guard.  PR #355 invented 12.0; this fix restores None.
    """
    cfg = TradingConfig()
    assert cfg.disaster_cap_pct is None, (
        f"Expected disaster_cap_pct=None (tp12tr15_t1200 has no disaster cap), "
        f"got {cfg.disaster_cap_pct}"
    )


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


def test_loss_priority_ordering_skipped_when_sl_none():
    """When stop_loss_pct=None, no sl<disaster or sl<rug ordering is enforced.

    The Pydantic invariant only checks ordering among NON-None values.
    A config with sl=None, disaster=None, rug=15.0 must validate without error.
    """
    from pydantic import ValidationError

    try:
        cfg = TradingConfig(stop_loss_pct=None, disaster_cap_pct=None, rug_pull_drop_pct=15.0)
    except ValidationError as exc:
        pytest.fail(
            f"TradingConfig with sl=None, disaster=None raised ValidationError:\n{exc}"
        )
    assert cfg.stop_loss_pct is None
    assert cfg.disaster_cap_pct is None


def test_loss_priority_ordering_enforced_when_both_set():
    """When sl and disaster are both non-None, sl < disaster must still hold."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TradingConfig(
            stop_loss_pct=40.0,
            disaster_cap_pct=40.0,  # equal — violated
            rug_pull_drop_pct=50.0,
        )


def test_disaster_must_be_less_than_rug_when_set():
    """When disaster_cap_pct is set (non-None), disaster < rug_pull must hold."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TradingConfig(
            stop_loss_pct=None,
            disaster_cap_pct=55.0,
            rug_pull_drop_pct=50.0,  # disaster >= rug — violated
        )


def test_sl_must_be_less_than_rug_when_disaster_none():
    """When disaster=None but sl is set, sl < rug_pull must hold."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TradingConfig(
            stop_loss_pct=20.0,
            disaster_cap_pct=None,
            rug_pull_drop_pct=15.0,  # sl(20) >= rug(15) — violated
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
    """Return a TradingConfig with the faithful tp12tr15_t1200 policy values.

    sl=None, disaster=None — exactly matching the lab policy dict:
      dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)
    """
    defaults = dict(
        auto_sell_timer_s=1200,
        take_profit_pct=12.0,
        rug_pull_drop_pct=15.0,
        stop_loss_pct=None,       # NOT in the validated policy
        disaster_cap_pct=None,    # NOT in the validated policy
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
    # Price stays flat — no TP(+12%), no rug.
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

    # Tape with price that doesn't hit TP(+12%) or rug (no +5% peak first).
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

    # Both fire AUTO_SELL_TIMER (flat price — no TP/RUG)
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

    Key design constraints to ensure RUG_PULL fires (not TP):
      - Peak must be < TP(+12%) so TP doesn't fire during the peak swap.
      - After the peak the price must drop 15% from peak.
      - Peak > fill * 1.05 → armed.
      - sl=None, disaster=None → STOP_LOSS and DISASTER_CAP never fire.
    """
    config = _tp12tr15_config()
    entry = 10000.0
    fill = 1.0
    # Peak = +10% from fill: armed (>5%), TP NOT triggered (<12%).
    peak_price = fill * 1.10
    # Rug price = 15% drop from peak = 1.10 * 0.85 = 0.935
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
        "A 15% drop from a +10% peak should trigger the tr15 trailing stop."
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
    cfg = TradingConfig(take_profit_pct=12.0, rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["tp"] == pytest.approx(0.12)


def test_config_to_pol_sl_is_none_when_field_is_none():
    """pol['sl'] must be None when stop_loss_pct=None (guard disabled).

    The tp12tr15_t1200 policy has no stop-loss; _config_to_pol must pass
    None through, not attempt /100 on it.
    """
    cfg = TradingConfig(stop_loss_pct=None)
    pol = _config_to_pol(cfg)
    assert pol["sl"] is None, (
        f"Expected pol['sl']=None when stop_loss_pct=None, got {pol['sl']}"
    )


def test_config_to_pol_disaster_is_none_when_field_is_none():
    """pol['disaster'] must be None when disaster_cap_pct=None (guard disabled).

    The tp12tr15_t1200 policy has no disaster cap; _config_to_pol must pass
    None through, not attempt /100 on it.
    """
    cfg = TradingConfig(disaster_cap_pct=None)
    pol = _config_to_pol(cfg)
    assert pol["disaster"] is None, (
        f"Expected pol['disaster']=None when disaster_cap_pct=None, got {pol['disaster']}"
    )


def test_config_to_pol_sl_converts_pct_to_fraction_when_set():
    """pol['sl'] = stop_loss_pct / 100 when stop_loss_pct is not None: 8.0 → 0.08."""
    cfg = TradingConfig(
        take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
        rug_pull_drop_pct=15.0, trailing_pct=10.0,
    )
    pol = _config_to_pol(cfg)
    assert pol["sl"] == pytest.approx(0.08)


def test_config_to_pol_disaster_converts_pct_to_fraction_when_set():
    """pol['disaster'] = disaster_cap_pct / 100 when disaster_cap_pct is not None: 12.0 → 0.12."""
    cfg = TradingConfig(
        take_profit_pct=12.0, stop_loss_pct=8.0, disaster_cap_pct=12.0,
        rug_pull_drop_pct=15.0, trailing_pct=10.0,
    )
    pol = _config_to_pol(cfg)
    assert pol["disaster"] == pytest.approx(0.12)


def test_config_to_pol_rugcut_is_trailing_giveback():
    """pol['rugcut'] = rug_pull_drop_pct / 100: 15.0 → 0.15 (the tr15 trailing)."""
    cfg = TradingConfig(rug_pull_drop_pct=15.0, trailing_pct=10.0)
    pol = _config_to_pol(cfg)
    assert pol["rugcut"] == pytest.approx(0.15), (
        "rug_pull_drop_pct=15.0 must map to pol['rugcut']=0.15 (the tr15 trailing "
        "giveback in the settler — RUG_PULL fires when price drops 15% from peak)."
    )


def test_full_tp12tr15_t1200_pol_dict():
    """End-to-end: TradingConfig defaults → _config_to_pol → faithful tp12tr15_t1200 pol dict.

    Source of truth (v31_winner_check.py:15):
      dict(tp=0.12, tr=0.15, timer=1200, holder=None, ratio=None)
    Expected pol: timer=1200, tp=0.12, sl=None, disaster=None, rugcut=0.15
    """
    cfg = TradingConfig()  # new defaults
    pol = _config_to_pol(cfg)
    assert pol["timer"] == 1200
    assert pol["tp"] == pytest.approx(0.12)
    assert pol["sl"] is None, f"Expected pol['sl']=None (no stop-loss in policy), got {pol['sl']}"
    assert pol["disaster"] is None, (
        f"Expected pol['disaster']=None (no disaster cap in policy), got {pol['disaster']}"
    )
    assert pol["rugcut"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# §6  STOP_LOSS / DISASTER_CAP never fire with the faithful tp12tr15_t1200 policy
# ---------------------------------------------------------------------------


def test_stop_loss_never_fires_with_none_policy():
    """With stop_loss_pct=None, a deeply-negative tape never emits STOP_LOSS.

    The settler must fall through to AUTO_SELL_TIMER (or RUG_PULL if armed).
    A STOP_LOSS trigger with sl=None would be a bug: it means the invented
    sl=8.0 guard from PR #355 is still active and corrupting observe results.
    """
    config = _tp12tr15_config()  # sl=None, disaster=None
    entry = 10000.0
    fill = 1.0

    # Price drops sharply to -50% (well below the old sl=-8% threshold)
    # then recovers slightly. With sl=None, STOP_LOSS must NOT fire.
    deep_drop = fill * 0.50  # -50% — would have triggered old sl=-8%
    trades = [
        (entry - 5, fill, 100.0),            # pre (quote)
        (entry + _LAT, fill, 50.0),          # fill
        (entry + 30, deep_drop, 50.0),       # -50% drop (sl=-8% would fire here)
        (entry + 60, deep_drop * 0.99, 50.0),
        (entry + 120, deep_drop * 0.98, 50.0),
        (entry + 1202, deep_drop, 50.0),     # past timer
        (entry + 1204, deep_drop, 50.0),     # exit fill
    ]

    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=140.0)
    assert result["enterable"] is True
    assert result["trigger"] != "STOP_LOSS", (
        f"STOP_LOSS fired with stop_loss_pct=None — the guard must be disabled. "
        f"trigger={result['trigger']}. "
        "This means the sl=8% invented by PR #355 is still active, "
        "corrupting the observe measurement."
    )
    # Should hit AUTO_SELL_TIMER (no TP, no RUG since peak never > fill*1.05)
    assert result["trigger"] == "AUTO_SELL_TIMER", (
        f"Expected AUTO_SELL_TIMER on a flat/declining tape with sl=None, "
        f"got {result['trigger']}"
    )


def test_disaster_cap_never_fires_with_none_policy():
    """With disaster_cap_pct=None, a deeply-negative tape never emits DISASTER_CAP.

    The settler must fall through to AUTO_SELL_TIMER (or RUG_PULL if armed).
    """
    config = _tp12tr15_config()  # sl=None, disaster=None
    entry = 10000.0
    fill = 1.0

    # Price drops to -80% (well below the old disaster=-12% threshold)
    deep_crash = fill * 0.20  # -80%
    trades = [
        (entry - 5, fill, 100.0),             # pre (quote)
        (entry + _LAT, fill, 50.0),           # fill
        (entry + 30, deep_crash, 50.0),       # -80% (disaster=-12% would fire here)
        (entry + 60, deep_crash * 0.99, 50.0),
        (entry + 1202, deep_crash, 50.0),     # past timer
        (entry + 1204, deep_crash, 50.0),     # exit fill
    ]

    result = simulate_tape_exit(trades, entry, config, size_sol=0.1, sol_usd=140.0)
    assert result["enterable"] is True
    assert result["trigger"] != "DISASTER_CAP", (
        f"DISASTER_CAP fired with disaster_cap_pct=None — the guard must be disabled. "
        f"trigger={result['trigger']}. "
        "This means the disaster=12% invented by PR #355 is still active."
    )
    assert result["trigger"] == "AUTO_SELL_TIMER", (
        f"Expected AUTO_SELL_TIMER on a crashing tape with disaster=None, "
        f"got {result['trigger']}"
    )
