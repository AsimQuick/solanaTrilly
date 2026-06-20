# ---
# module: copytrade.tests.test_reconcile_void_orphans
# sprint: live hotfix (copy-trade real-SOL readiness)
# story: copytrade-orphan-void
# status: implemented
# created-by: operator
# last-updated: 2026-06-20
# dependencies: pytest, django, copytrade.models, copytrade.reconcile, trading.models
# ---
"""Tests for copytrade.reconcile.void_orphan_positions.

Covers: voids non-active-cohort OPEN rows (NULL PnL, exit_reason=VOID), leaves
the active cohort untouched, leaves already-closed rows untouched, voids the
linked shared trading.Position, is idempotent, and refuses to act when the
active cohort id is empty (the all-void guard).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from copytrade.models import CopytradePosition
from copytrade.reconcile import void_orphan_positions

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
_ENTRY = datetime(2026, 6, 19, 23, 0, 0, tzinfo=timezone.utc)


def _open_pos(cohort_id: str, mint: str, *, shared_position_id: int | None = None) -> CopytradePosition:
    return CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint=mint,
        trigger_wallet="W" + mint,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=_ENTRY,
        entry_price=5.69e-05,  # curve-basis (the inflated pre-fix value)
        sol_in=0.16,
        size_usd=25.0,
        shared_position_id=shared_position_id,
    )


def test_voids_non_active_cohort_open_rows():
    old = _open_pos("copy_2026-06-19_v2", "AAA")
    void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)

    old.refresh_from_db()
    assert old.status == CopytradePosition.STATUS_CLOSED
    assert old.exit_reason == CopytradePosition.EXIT_VOID
    assert old.exit_ts == _NOW
    # NULL PnL — excluded from win-rate; not a fabricated outcome.
    assert old.realized_pnl_pct is None
    assert old.realized_pnl_sol is None
    assert old.exit_price is None


def test_leaves_active_cohort_untouched():
    active = _open_pos("copy_2026-06-20_jun_deploy", "BBB")
    void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)

    active.refresh_from_db()
    assert active.status == CopytradePosition.STATUS_OPEN
    assert active.exit_reason is None


def test_leaves_closed_rows_untouched():
    closed = _open_pos("copy_2026-06-19_v2", "CCC")
    closed.status = CopytradePosition.STATUS_CLOSED
    closed.exit_reason = CopytradePosition.EXIT_MIRROR
    closed.realized_pnl_pct = 11.7
    closed.save()

    void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)

    closed.refresh_from_db()
    assert closed.exit_reason == CopytradePosition.EXIT_MIRROR  # unchanged
    assert closed.realized_pnl_pct == pytest.approx(11.7)


def test_voids_linked_shared_position():
    from trading.models import Position as SharedPosition

    shared = SharedPosition.objects.create(
        mint="DDD",
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_OBSERVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=_ENTRY,
        entry_price=5.69e-05,
        size_sol=0.16,
    )
    _open_pos("copy_2026-06-19_v2", "DDD", shared_position_id=shared.pk)

    void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)

    shared.refresh_from_db()
    assert shared.status == SharedPosition.STATUS_CLOSED
    assert shared.closed_at == _NOW
    assert shared.exit_trigger == CopytradePosition.EXIT_VOID
    assert shared.realized_pnl_pct is None


def test_idempotent():
    _open_pos("copy_2026-06-19_v2", "EEE")
    first = void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)
    second = void_orphan_positions("copy_2026-06-20_jun_deploy", _NOW)
    assert len(first) == 1
    assert second == []  # nothing left open to void


def test_empty_active_cohort_is_noop_guard():
    """An empty active cohort must NOT void everything (the all-void guard)."""
    a = _open_pos("copy_2026-06-19_v2", "FFF")
    b = _open_pos("copy_2026-06-20_jun_deploy", "GGG")
    result = void_orphan_positions("", _NOW)

    assert result == []
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.status == CopytradePosition.STATUS_OPEN
    assert b.status == CopytradePosition.STATUS_OPEN
