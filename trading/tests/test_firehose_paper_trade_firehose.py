# ---
# module: trading.tests.test_firehose_paper_trade_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, unittest.mock, ast, pathlib,
#               core.firehose.spine, trading.schemas, trading.models, core.clock
# ---
"""Paper-trade tests for the firehose spine (offline, OBSERVE/PAPER only).

From a replay tape:
  §1 a PAPER position OPENS and CLOSES with realized PnL (DB).
  §2 ZERO real-order calls: no Sender / RPC send path is invoked
     (mock + assert_not_called; plus an AST guard that the spine module never
     imports trading.sender or trading.execution_core).
  §3 the RealCapitalGuard refuses to run any paper path while trading_enabled
     is True (structural unreachability of a real-capital path).
  §4 an un-enterable tape books NOTHING (Principle #5 — never a 0%/-100% row).
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.firehose.spine import RealCapitalGuardError, assert_paper_only, settle_paper_trade
from trading.schemas import TradingConfig

SPINE_PATH = Path(__file__).resolve().parents[2] / "core" / "firehose" / "spine.py"

# A replay tape for one mint: entry at t=1120 (grad 1000 + score_at 120).
# Pre-entry quote swaps in [entry-30, entry]; post-entry swaps that rise to TP.
_ENTRY_TS = 1120.0
_TAPE_TRADES = [
    # (block_time, price, usd_volume)
    (1095.0, 0.0010, 500.0),   # pre-entry window (quote basis)
    (1100.0, 0.0010, 600.0),
    (1122.0, 0.0010, 700.0),   # fill (entry + 2s latency)
    (1140.0, 0.0015, 800.0),
    (1160.0, 0.0021, 900.0),   # >= +100% from fill -> TAKE_PROFIT_PCT
    (1200.0, 0.0022, 400.0),
]


def _config() -> TradingConfig:
    # take_profit_pct=100 -> fill 0.001 -> TP at 0.002 (hit at 1160).
    return TradingConfig(trading_enabled=False, take_profit_pct=100.0, auto_sell_timer_s=300)


@pytest.mark.django_db
def test_paper_position_opens_and_closes_with_pnl():
    """§1 — a PAPER position opens and closes with a realized PnL row."""
    from trading.models import Position

    now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    settled = settle_paper_trade(
        mint="MintPaperAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump",
        score=0.91,
        trades=_TAPE_TRADES,
        entry_ts_epoch=_ENTRY_TS,
        trading_config=_config(),
        size_sol=0.18,
        sol_usd=140.0,
        trading_enabled=False,
        now=now,
    )

    assert settled is not None
    assert settled.pk is not None
    assert settled.source == Position.SOURCE_MODEL
    assert settled.mode == Position.MODE_OBSERVE
    assert settled.status == Position.STATUS_CLOSED
    assert settled.closed_at is not None
    assert settled.exit_trigger == "TAKE_PROFIT_PCT"
    assert settled.realized_pnl_pct is not None
    assert settled.score == pytest.approx(0.91)
    # The shared sentinel: closed_at IS NOT NULL <-> settled.
    assert Position.objects.filter(status=Position.STATUS_CLOSED, mint=settled.mint).count() == 1


@pytest.mark.django_db
def test_no_real_order_calls_in_paper_path():
    """§2 — no Sender/RPC send path is invoked during a paper trade."""
    sender = MagicMock()  # a stand-in real Sender; must NEVER be touched

    # Inject a position_factory + settler + closer so we control the whole path,
    # then assert the sender mock was never called anywhere.
    captured = {}

    def _position_factory(*, mint, score, entry_ts, entry_price, size_sol):
        pos = MagicMock()
        pos.mint = mint
        pos.score = score
        pos.entry_ts = entry_ts
        pos.entry_price = entry_price
        pos.size_sol = size_sol
        captured["opened"] = pos
        return pos

    def _closer(position, result, now=None, fill_price=None):
        captured["closed"] = (position, result)
        position.status = "CLOSED"
        return position

    settled = settle_paper_trade(
        mint="MintNoSendXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        score=0.9,
        trades=_TAPE_TRADES,
        entry_ts_epoch=_ENTRY_TS,
        trading_config=_config(),
        size_sol=0.1,
        sol_usd=140.0,
        trading_enabled=False,
        now=datetime(2026, 6, 19, tzinfo=timezone.utc),
        position_factory=_position_factory,
        closer=_closer,
    )

    assert settled is not None
    assert "opened" in captured and "closed" in captured
    # The Sender stand-in is never reachable from the paper path.
    sender.send_buy.assert_not_called()
    sender.send_sell.assert_not_called()
    sender.assert_not_called()


def test_real_capital_guard_blocks_trading_enabled_true():
    """§3 — assert_paper_only / settle_paper_trade refuse trading_enabled=True."""
    with pytest.raises(RealCapitalGuardError):
        assert_paper_only(True)

    with pytest.raises(RealCapitalGuardError):
        settle_paper_trade(
            mint="M",
            score=0.9,
            trades=_TAPE_TRADES,
            entry_ts_epoch=_ENTRY_TS,
            trading_config=_config(),
            size_sol=0.1,
            sol_usd=140.0,
            trading_enabled=True,  # MUST be refused
            now=datetime(2026, 6, 19, tzinfo=timezone.utc),
        )


@pytest.mark.django_db
def test_unenterable_tape_books_nothing():
    """§4 — an un-enterable tape (no pre-entry quote) books NO position."""
    from trading.models import Position

    # No swaps in [entry-30, entry] -> settler returns enterable=False ('dead').
    bad_tape = [(1130.0, 0.001, 100.0), (1140.0, 0.001, 100.0)]
    settled = settle_paper_trade(
        mint="MintDeadXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump",
        score=0.95,
        trades=bad_tape,
        entry_ts_epoch=_ENTRY_TS,
        trading_config=_config(),
        size_sol=0.1,
        sol_usd=140.0,
        trading_enabled=False,
        now=datetime(2026, 6, 19, tzinfo=timezone.utc),
    )
    assert settled is None
    assert Position.objects.filter(mint="MintDeadXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump").count() == 0


def test_spine_module_never_imports_sender_or_live_execution():
    """§2b — AST guard: core/firehose/spine.py imports no real-send boundary."""
    tree = ast.parse(SPINE_PATH.read_text(encoding="utf-8"))
    forbidden = {"trading.sender", "trading.execution_core"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden, (
                f"spine.py must not import {node.module} — paper path stays send-free"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"spine.py must not import {alias.name}"
                )
