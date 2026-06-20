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

    voided: list[CopytradePosition] = []
    for position in orphans:
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
        voided.append(position)

    logger.warning(
        "[copytrade] reconcile: voided %d orphaned open position(s) from non-active "
        "cohorts (active=%s).",
        len(voided),
        active_cohort_id,
    )
    return voided


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


__all__ = ["void_orphan_positions"]
