# ---
# module: copytrade.position_manager
# sprint: sprint-12, sprint-13, epic/copy-paper-fill-repricing
# story: US-61 AC-61.2, US-68 AC-68.1, EPIC-copy-paper-fill-repricing
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: copytrade.models, copytrade.schemas, trading.models,
#   trading.position_closer, copytrade.tasks
# ---
"""AC-61.2 / AC-68.1: Position exit-condition engine + shared Position settler.

The engine MANAGES each open position itself — it never waits for or mirrors
the watched wallet's sell (SPEC §3).  On each price tick the caller invokes
check_and_close_position; the function evaluates the four exit conditions in
priority order (FIRST-to-fire wins):

    1. TP    — price >= entry * (1 + take_profit_pct / 100)
    2. SL    — price <= entry * (1 - stop_loss_pct / 100)
    3. CURVE — bonding_curve_pct >= curve_completion_exit_pct
               (only when config.exit_before_graduation is True)
    4. TIMER — (current_ts - entry_ts).total_seconds() >= max_hold_seconds

On exit the CopytradePosition row is updated (AC-61.2 — unchanged).

AC-68.1 refactor: close_position NOW ALSO writes the same realized fields to
the shared trading.Position row (via trading.position_closer.close_observe_position)
so BOTH pipelines produce structurally equivalent shared Position rows with
closed_at IS NOT NULL (SPEC §0.1 'same execution path' parity).

§5 ISOLATION PRESERVED: copytrade keeps its own engine/config/ON-OFF.  The
shared Position is updated read-only from copytrade's perspective.  No
trading_enabled or PipelineState field is touched.

Paper-fill PnL formula (observe mode):
    sol_out          = sol_in * (exit_price / entry_price)
    realized_pnl_sol = sol_out - sol_in
    realized_pnl_pct = (realized_pnl_sol / sol_in) * 100

All logic is replay-deterministic: given the same open position, price,
timestamp, and config, two calls always return the same result.
"""
from __future__ import annotations

from datetime import datetime

from copytrade.models import CopytradePnlByWallet, CopytradePosition
from copytrade.schemas import CopyTradeConfig
from trading.models import Position as SharedPosition
from trading.position_closer import close_observe_position


def check_exit_condition(
    position: CopytradePosition,
    current_price: float,
    current_ts: datetime,
    config: CopyTradeConfig,
    curve_completion_pct: float = 0.0,
) -> tuple[str, float] | None:
    """Evaluate the four SPEC §3 exit conditions in priority order.

    Returns ``(exit_reason, exit_price)`` for the FIRST condition that fires,
    or ``None`` if no exit condition is currently met.

    Checked in this order — first match wins:
        TP    price >= entry * (1 + take_profit_pct / 100)
        SL    price <= entry * (1 - stop_loss_pct / 100)
        CURVE curve_completion_pct >= config.curve_completion_exit_pct
              (only when config.exit_before_graduation is True)
        TIMER (current_ts - entry_ts).total_seconds() >= config.max_hold_seconds

    Parameters
    ----------
    position:
        An open CopytradePosition.  entry_price and entry_ts must be set.
    current_price:
        Current token price (injected from replay tape or live feed).
    current_ts:
        Clock timestamp for this tick (injected — never datetime.now()).
    config:
        Active CopyTradeConfig providing the exit-condition thresholds.
    curve_completion_pct:
        Current bonding-curve completion percentage (0–100).  Defaults to 0
        so CURVE never fires unless explicitly provided.
    """
    entry_price: float = position.entry_price  # type: ignore[assignment]
    entry_ts: datetime = position.entry_ts  # type: ignore[assignment]

    # 1. Take Profit
    tp_threshold = entry_price * (1.0 + config.take_profit_pct / 100.0)
    if current_price >= tp_threshold:
        return (CopytradePosition.EXIT_TP, current_price)

    # 2. Stop Loss
    sl_threshold = entry_price * (1.0 - config.stop_loss_pct / 100.0)
    if current_price <= sl_threshold:
        return (CopytradePosition.EXIT_SL, current_price)

    # 3. Curve Completion Exit
    if config.exit_before_graduation and curve_completion_pct >= config.curve_completion_exit_pct:
        return (CopytradePosition.EXIT_CURVE, current_price)

    # 4. Max Hold Timer
    hold_seconds = (current_ts - entry_ts).total_seconds()
    if hold_seconds >= config.max_hold_seconds:
        return (CopytradePosition.EXIT_TIMER, current_price)

    return None


def close_position(
    position: CopytradePosition,
    exit_reason: str,
    exit_price: float,
    exit_ts: datetime,
) -> CopytradePosition:
    """Close a position, persist exit fields and PnL, then roll up pnl_by_wallet.

    AC-61.2: Updates CopytradePosition with exit fields and PnL (unchanged).
    AC-68.1: Also settles the shared trading.Position row via
             close_observe_position so both pipelines produce equivalent
             shared Position rows (SPEC §0.1 'same execution path' parity).

    Paper-fill PnL (observe mode):
        sol_out          = sol_in * (exit_price / entry_price)
        realized_pnl_sol = sol_out - sol_in
        realized_pnl_pct = (realized_pnl_sol / sol_in) * 100

    Parameters
    ----------
    position:    The open CopytradePosition to close (mutated in place).
    exit_reason: One of EXIT_TP / EXIT_SL / EXIT_CURVE / EXIT_TIMER constants.
    exit_price:  Price at the moment of exit.
    exit_ts:     Timestamp at the moment of exit (injected clock).
    """
    entry_price: float = position.entry_price  # type: ignore[assignment]
    sol_in: float = position.sol_in  # type: ignore[assignment]

    sol_out = sol_in * (exit_price / entry_price)
    realized_pnl_sol = sol_out - sol_in
    realized_pnl_pct = (realized_pnl_sol / sol_in) * 100.0

    position.status = CopytradePosition.STATUS_CLOSED
    position.exit_ts = exit_ts
    position.exit_price = exit_price
    position.sol_out = sol_out
    position.exit_reason = exit_reason
    position.realized_pnl_sol = realized_pnl_sol
    position.realized_pnl_pct = realized_pnl_pct
    position.save()

    _update_pnl_by_wallet(position)

    # --- EPIC-copy-paper-fill-repricing: dispatch retrospective repricing task ---
    # Wrapped in bare except so close_position NEVER crashes due to this dispatch.
    # The task runs on celery-worker (which mounts the lake); do NOT dispatch for
    # ENTRY_REJECTED positions (already rejected at live path; nothing to reprice).
    if exit_reason != CopytradePosition.EXIT_ENTRY_REJECTED:
        try:
            from copytrade.tasks import reprice_copy_fill
            reprice_copy_fill.delay(position.pk)
        except Exception:  # noqa: BLE001 — bare except: close must never crash
            pass

    # --- AC-68.1: settle the shared trading.Position row (if linked) ---
    if position.shared_position_id:
        try:
            shared_pos = SharedPosition.objects.get(pk=position.shared_position_id)
            close_observe_position(
                shared_pos,
                exit_trigger=exit_reason,
                exit_price=exit_price,
                exit_ts=exit_ts,
                realized_pnl_pct=realized_pnl_pct,
            )
        except SharedPosition.DoesNotExist:
            pass

    return position


def check_and_close_position(
    position: CopytradePosition,
    current_price: float,
    current_ts: datetime,
    config: CopyTradeConfig,
    curve_completion_pct: float = 0.0,
) -> CopytradePosition | None:
    """Check exit conditions and close the position if one fires.

    Returns the closed CopytradePosition when an exit is triggered, or
    ``None`` when the position remains open.

    This is the main entry point for the position-management loop: call it on
    every price tick for every open position.
    """
    result = check_exit_condition(position, current_price, current_ts, config, curve_completion_pct)
    if result is None:
        return None
    exit_reason, exit_price = result
    return close_position(position, exit_reason, exit_price, current_ts)


def _update_pnl_by_wallet(position: CopytradePosition) -> None:
    """Upsert copytrade_pnl_by_wallet after a position closes.

    Re-aggregates all closed positions for the (cohort_id, trigger_wallet)
    pair so the rollup stays accurate without floating-point drift.

    EPIC-copy-paper-fill-repricing: EXCLUDES ENTRY_REJECTED positions from
    the trade count and win-rate (never bought, lost nothing — moving 32% → 47%
    as described in the epic spec).  PnL rollup also excludes ENTRY_REJECTED
    (PnL is NULL for those rows).
    """
    closed = list(
        CopytradePosition.objects.filter(
            cohort_id=position.cohort_id,
            trigger_wallet=position.trigger_wallet,
            status=CopytradePosition.STATUS_CLOSED,
        ).exclude(exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED)
    )
    n_trades = len(closed)
    if n_trades == 0:
        return

    total_pnl_sol = sum(float(p.realized_pnl_sol or 0.0) for p in closed)
    wins = sum(1 for p in closed if float(p.realized_pnl_sol or 0.0) > 0)
    win_rate = wins / n_trades

    total_hold_s = sum(
        (p.exit_ts - p.entry_ts).total_seconds()
        for p in closed
        if p.exit_ts and p.entry_ts
    )
    avg_hold_s = total_hold_s / n_trades

    CopytradePnlByWallet.objects.update_or_create(
        cohort_id=position.cohort_id,
        address=position.trigger_wallet,
        defaults={
            "n_trades": n_trades,
            "win_rate": win_rate,
            "total_pnl_sol": total_pnl_sol,
            "avg_hold_s": avg_hold_s,
        },
    )
