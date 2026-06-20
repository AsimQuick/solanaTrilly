# ---
# module: copytrade.reconcile
# sprint: live hotfix (copy-trade real-SOL readiness)
# story: copytrade-orphan-void
# status: implemented
# created-by: operator
# last-updated: 2026-06-20
# dependencies: copytrade.models, trading.models
# ---
"""Void orphaned copy positions (abandoned non-active-cohort open rows).

The live engine only seeds/manages OPEN positions for the *active* cohort
(``run_copytrade_engine`` filters the seed query on ``cohort_id``).  When the
operator switches the active cohort, any positions still OPEN under a previous
cohort are never managed again — they linger in ``copytrade_positions`` as
deceiving ``open`` rows forever (the "30 copies sat open/untracked" the lab
flagged, which the dashboard rendered with a curve-vs-Birdeye basis mismatch as
~-93%).

Voiding closes such an orphan with ``exit_reason=VOID`` and **NULL PnL** — it is
NOT a fabricated outcome and NOT a backfill from the lab's offline oracle
(``ct_reconstructed.csv`` is Birdeye/zero-latency; importing it would inject an
inconsistent optimistic basis — the same class of bug).  A voided row is simply
"abandoned, superseded — excluded from win-rate."

Idempotent: only touches ``status=OPEN`` rows.  Safe to run repeatedly and on
every engine startup.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from copytrade.models import CopytradePosition

logger = logging.getLogger("copytrade")


def void_orphan_positions(active_cohort_id: str, now: datetime) -> list[CopytradePosition]:
    """Close all OPEN copy positions that do NOT belong to *active_cohort_id*.

    Parameters
    ----------
    active_cohort_id:
        The cohort the engine is currently running.  Open positions in ANY other
        cohort are orphans and get voided.  **Guard:** if this is empty/falsy the
        function is a NO-OP and returns ``[]`` — never void everything on a
        misconfigured/empty active cohort.
    now:
        Void timestamp (injected — no ``datetime.now()``), written to ``exit_ts``.

    Returns
    -------
    list[CopytradePosition]
        The rows that were voided this call (empty if none / guarded).
    """
    if not active_cohort_id:
        logger.warning(
            "[copytrade] reconcile: active_cohort_id is empty — skipping orphan void "
            "(refusing to void all open positions)."
        )
        return []

    orphans = list(
        CopytradePosition.objects.filter(status=CopytradePosition.STATUS_OPEN).exclude(
            cohort_id=active_cohort_id
        )
    )
    if not orphans:
        return []

    voided = [_void_one(p, now) for p in orphans]

    logger.warning(
        "[copytrade] reconcile: voided %d orphaned open position(s) from non-active "
        "cohorts (active=%s).",
        len(voided),
        active_cohort_id,
    )
    return voided


def void_unexitable_positions(
    active_cohort_id: str,
    valid_strategy_ids: set,
    now: datetime,
) -> list[CopytradePosition]:
    """Void OPEN active-cohort positions whose strategy_id has NO exit config.

    The live engine looks up each position's exit rule by ``strategy_id`` in
    ``exit_by_strategy``; a position whose ``strategy_id`` is not a key logs
    ``no-exit-cfg ... holding`` EVERY tick and can NEVER close (the stale-timer
    fallback also needs the exit cfg's ``max_hold_seconds``).  This happens to
    positions carried over from a PRIOR cohort session whose strategy/style ids
    differ from the current cohort's — e.g. the live "30 copies sat open" with a
    stale ``strategy_id='scalp'`` the current V2.1 styles don't include.

    Voids them with ``exit_reason=VOID`` and NULL PnL (excluded from win-rate) —
    pre-fix / abandoned, NOT honestly settleable, NOT backfilled from any oracle.
    Idempotent (status=OPEN only).

    GUARD: if ``valid_strategy_ids`` is empty (a failed/empty cohort load), this
    is a NO-OP — never void everything just because the exit configs didn't build.
    """
    if not active_cohort_id or not valid_strategy_ids:
        logger.warning(
            "[copytrade] reconcile: empty active cohort or no valid strategy ids — "
            "skipping unexitable void (refusing to void on an incomplete cohort load)."
        )
        return []

    candidates = list(
        CopytradePosition.objects.filter(
            status=CopytradePosition.STATUS_OPEN,
            cohort_id=active_cohort_id,
        ).exclude(strategy_id__in=list(valid_strategy_ids))
    )
    if not candidates:
        return []

    voided = [_void_one(p, now) for p in candidates]
    logger.warning(
        "[copytrade] reconcile: voided %d unexitable open position(s) — strategy_id "
        "not among the active cohort's %d exit configs (active=%s).",
        len(voided),
        len(valid_strategy_ids),
        active_cohort_id,
    )
    return voided


def _void_one(position: CopytradePosition, now: datetime) -> CopytradePosition:
    """Close one position as VOID (NULL PnL) + void its linked shared row."""
    position.status = CopytradePosition.STATUS_CLOSED
    position.exit_ts = now
    position.exit_reason = CopytradePosition.EXIT_VOID
    position.exit_price = None
    position.sol_out = None
    position.realized_pnl_sol = None  # NULL — excluded from win-rate
    position.realized_pnl_pct = None
    position.save(
        update_fields=[
            "status",
            "exit_ts",
            "exit_reason",
            "exit_price",
            "sol_out",
            "realized_pnl_sol",
            "realized_pnl_pct",
        ]
    )
    _void_shared_position(position.shared_position_id, now)
    return position


def _void_shared_position(shared_position_id: Optional[int], now: datetime) -> None:
    """Void the linked shared trading.Position row (if any) so it isn't left OPEN.

    Lazy import preserves copytrade §5 isolation (no module-level trading import
    chain).  Missing rows are tolerated (the link is FK-free / best-effort).
    """
    if not shared_position_id:
        return
    from trading.models import Position as SharedPosition  # lazy — §5 isolation

    try:
        shared = SharedPosition.objects.get(pk=shared_position_id)
    except SharedPosition.DoesNotExist:
        return

    shared.status = SharedPosition.STATUS_CLOSED
    shared.closed_at = now
    shared.exit_ts = now
    shared.exit_trigger = CopytradePosition.EXIT_VOID
    shared.exit_price = None
    shared.realized_pnl_pct = None
    shared.save(
        update_fields=[
            "status",
            "closed_at",
            "exit_ts",
            "exit_trigger",
            "exit_price",
            "realized_pnl_pct",
        ]
    )


__all__ = ["void_orphan_positions", "void_unexitable_positions"]
