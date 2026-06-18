# ---
# module: trading.position_closer
# sprint: sprint-13
# story: US-66 AC-66.3, US-68 AC-68.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.models, datetime
# ---
"""Position closer: settle_paper_position + close_observe_position.

Two public entry points for writing realized fields to a shared trading.Position:

settle_paper_position(position, settler_result, now=None, fill_price=None) -> Position
    Model-pipeline path: takes a tape settler result dict (simulate_tape_exit)
    and writes the realized fields.  Raises ValueError if not enterable.

close_observe_position(position, exit_trigger, exit_price, exit_ts, realized_pnl_pct,
                       peak_price=None) -> Position
    Copytrade path (AC-68.1): closes a Position directly from a live exit event
    (TP/SL/CURVE/TIMER tick) without a tape settler result.  Writes the same 7
    realized fields as settle_paper_position so both pipelines produce equivalent
    shared Position rows (the 'same execution chassis' parity, SPEC §0.1).

Both produce:  exit_price, exit_trigger, realized_pnl_pct, peak_price,
               closed_at, exit_ts, status='CLOSED'

The dashboard/analytics sentinel (closed_at IS NOT NULL) works uniformly for
both paper-settled (model) and observe-closed (copytrade) positions (AC-66.3 /
AC-68.1).

Separation of concerns: this module does NOT import tape_settler or any
copytrade module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from trading.models import Position


def settle_paper_position(
    position: Position,
    settler_result: dict,
    now: Optional[datetime] = None,
    fill_price: Optional[float] = None,
) -> Position:
    """Write realized exit fields to a PAPER position from a tape settler result.

    Parameters
    ----------
    position:
        A PAPER Position ORM instance (mode='observe', status='PAPER').
        The instance must be saved (have a PK) so update_fields can persist.
    settler_result:
        The dict returned by simulate_tape_exit.  Must have
        ``enterable=True``; raises ValueError otherwise.
        Expected keys when enterable: 'pnl', 'trigger', 'held', 'peak', 'flow'.
    now:
        The settlement timestamp written to closed_at.  Defaults to
        ``datetime.now(timezone.utc)`` when None.
    fill_price:
        The fill price used as the entry-price approximation for computing
        exit_price and peak_price.  Defaults to ``position.entry_price`` when
        None (the honest-fill convention for paper positions).

    Returns
    -------
    Position
        The same position instance with realized fields written and persisted.

    Raises
    ------
    ValueError
        If ``settler_result['enterable']`` is False.  Un-enterable positions
        must not be booked (Principle #5 / tape_settler contract).
    """
    if not settler_result.get("enterable", False):
        reason = settler_result.get("reason", "unknown")
        raise ValueError(
            f"settler_result is not enterable (reason={reason!r}); "
            "un-enterable positions must not be booked"
        )

    # --- Resolve defaults ---
    if now is None:
        now = datetime.now(timezone.utc)
    if fill_price is None:
        fill_price = position.entry_price

    # --- Extract from settler result ---
    pnl_pct: float = settler_result["pnl"]      # percentage, e.g. 50.0 = +50%
    trigger: str = settler_result["trigger"]
    held_s: float = settler_result["held"]       # seconds from entry to trigger
    peak_pct: float = settler_result["peak"]     # peak return percentage from fill

    # --- Compute derived prices ---
    # Net-equivalent exit price from the tape PnL percentage
    exit_price: float = fill_price * (1 + pnl_pct / 100)
    # Absolute peak price from settler's peak percentage
    peak_price_abs: float = fill_price * (1 + peak_pct / 100)
    # Exit timestamp: entry + held seconds
    exit_ts = position.entry_ts + timedelta(seconds=held_s)

    # --- Write realized fields (same shape as live CLOSED positions) ---
    position.exit_price = exit_price
    position.exit_trigger = trigger
    position.realized_pnl_pct = pnl_pct
    position.peak_price = peak_price_abs
    position.closed_at = now
    position.exit_ts = exit_ts
    position.status = Position.STATUS_CLOSED

    # Persist only the updated fields — does not touch entry fields or source/mode
    position.save(
        update_fields=[
            "exit_price",
            "exit_trigger",
            "realized_pnl_pct",
            "peak_price",
            "closed_at",
            "exit_ts",
            "status",
        ]
    )

    return position


def close_observe_position(
    position: Position,
    exit_trigger: str,
    exit_price: float,
    exit_ts: datetime,
    realized_pnl_pct: float,
    peak_price: Optional[float] = None,
) -> Position:
    """Write realized fields to a PAPER trading.Position from a live exit event.

    Copytrade path (AC-68.1): closes a shared Position directly from a live
    price-tick exit (TP / SL / CURVE / TIMER) without going through the tape
    settler.  Writes the SAME 7 realized fields as settle_paper_position so both
    pipelines produce structurally equivalent shared Position rows.

    Parameters
    ----------
    position:
        A PAPER or OPEN Position instance (source='copytrade', mode='observe').
        Must have a PK (saved) for update_fields to work.
    exit_trigger:
        Exit reason string (e.g. 'TP', 'SL', 'CURVE', 'TIMER').
    exit_price:
        The observed price at the moment of exit.
    exit_ts:
        Timestamp at the moment of exit (injected clock — no datetime.now()).
    realized_pnl_pct:
        Realized PnL as a percentage (e.g. 50.0 = +50%).
    peak_price:
        Absolute peak price observed during the hold.  None if not tracked
        by the caller (field stays null in the Position row).

    Returns
    -------
    Position
        The same position instance with realized fields written and persisted.
    """
    position.exit_price = exit_price
    position.exit_trigger = exit_trigger
    position.realized_pnl_pct = realized_pnl_pct
    position.peak_price = peak_price
    position.closed_at = exit_ts
    position.exit_ts = exit_ts
    position.status = Position.STATUS_CLOSED

    position.save(
        update_fields=[
            "exit_price",
            "exit_trigger",
            "realized_pnl_pct",
            "peak_price",
            "closed_at",
            "exit_ts",
            "status",
        ]
    )

    return position
