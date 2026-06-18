# ---
# module: trading.position_closer
# sprint: sprint-13
# story: US-66 AC-66.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.models, datetime
# ---
"""Position closer: settle_paper_position — writes realized fields for PAPER positions.

Takes a PAPER Position ORM instance and the result dict from simulate_tape_exit
(tape_settler) and writes the same five realized fields as a live CLOSED position:

    exit_price, exit_trigger, realized_pnl_pct, peak_price, closed_at

This makes the dashboard/analytics sentinel (closed_at IS NOT NULL) work
uniformly for both paper-settled and live-closed positions (AC-66.3).

Separation of concerns: this module does NOT import tape_settler.  The caller
runs simulate_tape_exit and passes the result dict here.  The caller is
responsible for ensuring the result is from an enterable position.

Public API
----------
settle_paper_position(position, settler_result, now=None, fill_price=None) -> Position
    Writes realized fields to a PAPER Position and persists via save().
    Raises ValueError if settler_result['enterable'] is False.
    Returns the updated position instance.
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
