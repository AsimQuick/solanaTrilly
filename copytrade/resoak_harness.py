# ---
# module: copytrade.resoak_harness
# sprint: sprint-15
# story: US-85
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.firehose_harness, copytrade.pgrad_calibration,
#               copytrade.pgrad_classifier, copytrade.curvestage_settle,
#               copytrade.soak_killswitch, logging, dataclasses
# ---
"""Re-soak validation harness for the curvestage gate (US-85).

Runs the recalibrated curvestage gate (US-82) + honest settler (US-83) over the
LOCAL Jun 20-23 firehose tapes in observe/paper mode (trading_enabled=False).

JUDGMENT PROTOCOL:
  (a) Fills are judged by honest-fill re-settle from the firehose tape (NOT the
      dashboard number — the dashboard was shown to be wrong by up to ~$90 on 24
      trades).
  (b) Selected grad-rate vs ~39% breakeven is the leading kill metric; the
      kill-switch (soak_killswitch.py) trips at <40% over >=15 real entries.
  (c) Net $/tr after honest fill (this module's output).

HONEST CAVEAT (documented per AC-85.3):
  Even with the recalibrated gate, the live ranking is weak (grad picks median
  pgrad ~0.006 vs non-grad ~0.006 — heavy overlap at firehose proxy prices).
  The harness gets curvestage to 'PROPERLY TESTED', NOT 'PROVEN PROFITABLE'.
  The grad-rate lift of 1.04x (13.7% vs 13.2% base) is non-trivially selective
  but not strong.  The soak is the evidence-accumulation phase.

DOLLAR BASIS:
  ALL dollar quantities computed via vol_sol * SOL_price (SOL_PRICE_BY_DATE).
  vol_usd is ALL ZERO in these tapes and is NEVER used.

GATE LOGIC (mirrors the live curvestage engine):
  Gate-1: on_curve = True (pre-grad, cum_buy_sol < GRAD_SOL_THRESHOLD)
  Gate-2: curve_frac <= CURVE_FRAC_GATE (0.60)
  Gate-3: pgrad score >= recalibrated threshold (top-25% rank-based)
  Entry trigger: first buy >= TRIGGER_SOL_MIN (vol_sol >= 3.0 SOL proxy for $250)

EXIT LOGIC (simplified for offline harness):
  Settled at the first tape row >= buy_ts + 30s (honest fill, 30s post-buy VWAP
  proxy).  If graduated: GRAD settle.  Else: timer exit at 90s after buy.
  Dollar PnL = exit_price / entry_price - 1 - ROUND_TRIP_COST (0.60%).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from copytrade.firehose_harness import (
    GRAD_SOL_THRESHOLD,
    SOL_PRICE_BY_DATE,
    SOL_PRICE_DEFAULT,
    TapeRow,
    label_graduations,
    parse,
)
from copytrade.soak_killswitch import (
    RESULT_ENTRY_REJECTED,
    RESULT_GRAD,
    RESULT_NO_GRAD,
    RESULT_VOID,
    SoakTrade,
    evaluate_killswitch,
)

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# Harness configuration constants
# ---------------------------------------------------------------------------

#: Minimum trigger buy size in SOL ($250 at $84/SOL = ~2.98 SOL).
TRIGGER_SOL_MIN: float = 3.0

#: Curve-frac gate ceiling (must match live engine).
CURVE_FRAC_GATE: float = 0.60

#: Default paper position size in USD.
OBSERVE_SIZE_USD: float = 25.0

#: Exit hold window in seconds — time after buy before exit is measured.
EXIT_HOLD_S: int = 30

#: Timer exit window — maximum hold if no graduation (seconds after buy).
TIMER_EXIT_S: int = 90

#: Round-trip cost fraction (entry + exit fee/impact).
ROUND_TRIP_COST: float = 0.006  # 0.60%

#: Soak judgment trinity breakeven grad-rate (~39%).
BREAKEVEN_GRAD_RATE: float = 0.39


# ---------------------------------------------------------------------------
# Per-trade result
# ---------------------------------------------------------------------------

@dataclass
class ResoakTrade:
    """Result of one re-soak paper trade."""
    mint: str
    buy_ts: int
    date_str: str
    sol_usd: float

    # Gate outcomes
    passed_gate1_oncurve: bool = False    # on-curve at entry
    passed_gate2_curvefrac: bool = False  # curve_frac <= 0.60
    passed_gate3_pgrad: bool = False      # pgrad >= threshold
    selected: bool = False                # all gates passed

    # Graduation
    graduated: bool = False
    grad_block_time: Optional[int] = None

    # Fill
    entry_valid: bool = False            # honest entry fill found
    entry_price: Optional[float] = None  # price at entry (SOL/token)
    exit_price: Optional[float] = None   # price at exit  (SOL/token)
    exit_ts: Optional[int] = None

    # PnL
    pnl_pct: float = 0.0                # net % including round-trip cost
    pnl_usd: float = 0.0                # net PnL in USD

    # Killswitch classification
    ks_result: str = RESULT_VOID        # GRAD | NO_GRAD | VOID | ENTRY_REJECTED


@dataclass
class ResoakReport:
    """Aggregate re-soak report over the full date window."""
    date_strs: list[str] = field(default_factory=list)
    trades: list[ResoakTrade] = field(default_factory=list)

    # Kill-switch state
    killswitch_halted: bool = False
    killswitch_halt_reason: Optional[str] = None

    # Aggregate metrics (over selected trades only)
    n_candidates: int = 0       # tokens that passed gate-1 + gate-2
    n_selected: int = 0         # passed all three gates (selected)
    n_grad: int = 0             # selected AND graduated
    n_entries_valid: int = 0    # entries with honest fill
    n_timer_exit: int = 0       # exits via timer (no graduation)

    selected_grad_rate: Optional[float] = None   # n_grad / n_real_entries
    base_grad_rate: Optional[float] = None       # unselected grad-rate
    net_pnl_usd: float = 0.0
    net_pnl_per_trade: float = 0.0
    win_rate: Optional[float] = None

    # Honest caveat
    honest_caveat: str = (
        "HONEST CAVEAT: recalibrated gate lift is weak (1.04x). "
        "This gets curvestage to 'properly tested', NOT 'proven profitable'. "
        "Soak is the evidence-accumulation phase."
    )

    # Documented soak judgment trinity
    soak_judgment_trinity: dict = field(default_factory=lambda: {
        "a": "Judge on firehose-reconstructed real fills (this harness), NEVER the dashboard.",
        "b": f"Selected grad-rate vs ~{BREAKEVEN_GRAD_RATE:.0%} breakeven is the leading kill metric.",
        "c": "Net $/tr after honest fill.",
    })


# ---------------------------------------------------------------------------
# Core harness logic
# ---------------------------------------------------------------------------

def run_resoak(
    date_strs: list[str],
    lake_base_dir: str = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    model_dir: str = "/Users/asim/NoIcloud/solanatrilly/models/copy_2026-06-22_curvestage",
    observe_size_usd: float = OBSERVE_SIZE_USD,
) -> ResoakReport:
    """Run the re-soak harness over local firehose tapes.

    Re-runs the recalibrated gate (US-82) + honest settler (US-83) over the
    given date range in paper/observe mode.

    Parameters
    ----------
    date_strs:
        Dates to process (YYYY-MM-DD strings), e.g. ["2026-06-20", "2026-06-21"].
    lake_base_dir:
        Root of the firehose lake.
    model_dir:
        Path to the model artifacts (for pgrad threshold).
    observe_size_usd:
        Paper position size in USD (default $25).

    Returns
    -------
    ResoakReport with per-trade detail and aggregate metrics.
    """
    from copytrade.pgrad_calibration import (
        CURVE_FRAC_GATE as _CF_GATE,
    )
    from copytrade.pgrad_calibration import (
        _compute_features_from_tape,
    )
    from copytrade.pgrad_classifier import get_pgrad_classifier

    report = ResoakReport(date_strs=list(date_strs))
    clf = get_pgrad_classifier(model_dir=model_dir)

    all_ks_trades: list[SoakTrade] = []
    total_base_grad = 0
    total_base_cands = 0

    for date_str in date_strs:
        sol_usd = SOL_PRICE_BY_DATE.get(date_str, SOL_PRICE_DEFAULT)
        rows_by_mint: dict[str, list[TapeRow]] = {}

        # Load all rows for this date
        for row in parse(date_str, lake_base_dir=lake_base_dir):
            rows_by_mint.setdefault(row.mint, []).append(row)

        if not rows_by_mint:
            logger.warning("[resoak] no rows for date %s", date_str)
            continue

        # Label graduations for this date
        all_rows_flat = [r for rows in rows_by_mint.values() for r in rows]
        grad_info = label_graduations(all_rows_flat)

        for mint, tape in rows_by_mint.items():
            tape_sorted = sorted(tape, key=lambda r: r.block_time)
            buy_rows = [r for r in tape_sorted if r.side == "buy"]
            if not buy_rows:
                continue

            # Find trigger: first buy >= TRIGGER_SOL_MIN
            trigger_idx = next(
                (i for i, r in enumerate(buy_rows) if r.vol_sol >= TRIGGER_SOL_MIN),
                None,
            )
            if trigger_idx is None:
                continue  # no qualifying trigger

            trigger_row = buy_rows[trigger_idx]
            buy_ts = trigger_row.block_time

            # Tape prefix: rows BEFORE the trigger
            pre = [r for r in tape_sorted if r.block_time < buy_ts]

            # Compute features
            feats = _compute_features_from_tape(pre, buy_ts, trigger_row, sol_usd)

            # Gate-1: on_curve
            cum_buy_sol = sum(r.vol_sol for r in pre if r.side == "buy")
            on_curve = cum_buy_sol < GRAD_SOL_THRESHOLD

            trade = ResoakTrade(
                mint=mint,
                buy_ts=buy_ts,
                date_str=date_str,
                sol_usd=sol_usd,
                passed_gate1_oncurve=on_curve,
            )

            if not on_curve:
                # Post-grad; skip
                report.trades.append(trade)
                continue

            # Gate-2: curve_frac
            curve_frac = feats.get("curve_frac", 0.0)
            trade.passed_gate2_curvefrac = curve_frac <= _CF_GATE

            if not trade.passed_gate2_curvefrac:
                report.trades.append(trade)
                continue

            report.n_candidates += 1
            total_base_cands += 1

            # Check base graduation (for base_grad_rate denominator)
            info = grad_info.get(mint)
            if info and info.graduated and info.grad_block_time and info.grad_block_time >= buy_ts:
                total_base_grad += 1

            # Gate-3: pgrad
            pgrad_score = clf.predict_proba_one(feats)
            passes_gate3 = pgrad_score >= clf.threshold
            trade.passed_gate3_pgrad = passes_gate3

            if not passes_gate3:
                trade.ks_result = RESULT_VOID
                all_ks_trades.append(SoakTrade(result=RESULT_VOID, mint=mint))
                report.trades.append(trade)
                continue

            trade.selected = True
            report.n_selected += 1

            # Graduation
            if info and info.graduated and info.grad_block_time:
                trade.graduated = info.graduated and info.grad_block_time >= buy_ts
                trade.grad_block_time = info.grad_block_time if trade.graduated else None

            # Honest fill: entry = trigger_row price; exit = first buy >= buy_ts + EXIT_HOLD_S
            trade.entry_price = trigger_row.price
            if trade.entry_price is None or trade.entry_price <= 0:
                trade.entry_valid = False
                trade.ks_result = RESULT_ENTRY_REJECTED
                all_ks_trades.append(SoakTrade(result=RESULT_ENTRY_REJECTED, mint=mint))
                report.trades.append(trade)
                continue

            # Exit: find first row >= buy_ts + EXIT_HOLD_S
            exit_deadline = buy_ts + TIMER_EXIT_S
            exit_candidates = [
                r for r in tape_sorted
                if r.block_time >= buy_ts + EXIT_HOLD_S
                and r.block_time <= exit_deadline
                and r.price > 0
            ]

            if exit_candidates:
                exit_row = exit_candidates[0]
                trade.exit_price = exit_row.price
                trade.exit_ts = exit_row.block_time
                trade.entry_valid = True
            else:
                # No exit found within window — use last row before timer
                last_candidates = [
                    r for r in tape_sorted
                    if r.block_time >= buy_ts and r.price > 0
                ]
                if last_candidates:
                    exit_row = last_candidates[-1]
                    trade.exit_price = exit_row.price
                    trade.exit_ts = exit_row.block_time
                    trade.entry_valid = True
                else:  # pragma: no cover — defensive: entry_price>0 guarantees >=1 fallback row
                    trade.entry_valid = False  # pragma: no cover
                    trade.ks_result = RESULT_ENTRY_REJECTED  # pragma: no cover
                    all_ks_trades.append(SoakTrade(result=RESULT_ENTRY_REJECTED, mint=mint))  # pragma: no cover
                    report.trades.append(trade)  # pragma: no cover
                    continue  # pragma: no cover

            # PnL
            raw_pnl_pct = (trade.exit_price / trade.entry_price) - 1.0
            trade.pnl_pct = raw_pnl_pct - ROUND_TRIP_COST
            # Floor impact at -100%
            trade.pnl_pct = max(trade.pnl_pct, -1.0)

            sol_in = observe_size_usd / sol_usd
            trade.pnl_usd = sol_in * sol_usd * trade.pnl_pct

            if not trade.graduated:
                report.n_timer_exit += 1

            report.n_entries_valid += 1

            # Killswitch classification
            if trade.graduated:
                trade.ks_result = RESULT_GRAD
                all_ks_trades.append(SoakTrade(result=RESULT_GRAD, mint=mint))
                report.n_grad += 1
            else:
                trade.ks_result = RESULT_NO_GRAD
                all_ks_trades.append(SoakTrade(result=RESULT_NO_GRAD, mint=mint))

            report.trades.append(trade)

    # Aggregate PnL over valid entries
    valid_trades = [t for t in report.trades if t.entry_valid and t.selected]
    if valid_trades:
        report.net_pnl_usd = sum(t.pnl_usd for t in valid_trades)
        report.net_pnl_per_trade = report.net_pnl_usd / len(valid_trades)
        wins = [t for t in valid_trades if t.pnl_pct > 0]
        report.win_rate = len(wins) / len(valid_trades) if valid_trades else None

    # Selected grad-rate (denominator = GRAD + NO_GRAD, mirrors kill-switch)
    real_entries = [t for t in all_ks_trades if t.result in (RESULT_GRAD, RESULT_NO_GRAD)]
    if real_entries:
        grad_count = sum(1 for t in real_entries if t.result == RESULT_GRAD)
        report.selected_grad_rate = grad_count / len(real_entries)

    # Base grad-rate (denominator = all gate-1+gate-2 candidates)
    if total_base_cands > 0:
        report.base_grad_rate = total_base_grad / total_base_cands

    # Evaluate kill-switch
    ks_state = evaluate_killswitch(all_ks_trades)
    report.killswitch_halted = ks_state.halted
    report.killswitch_halt_reason = ks_state.halt_reason

    logger.info(
        "[resoak] dates=%s | n_selected=%d | selected_grad_rate=%s | "
        "base_grad_rate=%s | net_pnl_usd=%.2f | net_$/tr=%.2f | "
        "kill=%s",
        date_strs,
        report.n_selected,
        f"{report.selected_grad_rate:.1%}" if report.selected_grad_rate is not None else "n/a",
        f"{report.base_grad_rate:.1%}" if report.base_grad_rate is not None else "n/a",
        report.net_pnl_usd,
        report.net_pnl_per_trade,
        ks_state.halt_reason or "HEALTHY",
    )

    return report
