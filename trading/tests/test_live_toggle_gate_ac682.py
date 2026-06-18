# ---
# module: trading.tests.test_live_toggle_gate_ac682
# sprint: sprint-13
# story: US-68 AC-68.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, copytrade.position_opener, copytrade.schemas,
#   copytrade.trigger_pipeline, trading.execution_core, trading.schemas,
#   trading.models, copytrade.models, core.clock
# ---
"""AC-68.2: Copy-trade Live toggle wired to (capital-OFF) shared execution path.

Verifies that mode=live routes to the shared ExecutionCore gated boundary and
that with trading_enabled=False (the DEFAULT) no real order is placed:

  §1 — Gated boundary: mode=live + trading_enabled=False → sent=False (DB)
  §2 — Sender never called when trading_enabled=False (no DB)
  §3 — Position rows written with correct mode/status (DB)
  §4 — No-auto-start guard: open_live_position does NOT set trading_enabled=True (AST)
  §5 — Ops-doc presence check: firehose_activation_log.md contains required entry
  §6 — H1 ImportError trap: open_live_position is importable and callable

All §1/§3 DB tests use pytest.mark.django_db.
All §2/§4/§5/§6 tests are plain functions (no DB needed).
"""
from __future__ import annotations

import ast
import inspect
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from copytrade.models import CopytradePosition
from copytrade.position_opener import open_live_position
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord
from core.clock import VirtualClock
from trading.execution_core import ExecutionCore
from trading.models import Position
from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# Constants for deterministic replay
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
_COHORT = "cohort-ac682-live-gate"
_MINT = "MintAC682LiveXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_WALLET = "Wa11etAC682AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_ENTRY_PRICE = 0.000_012_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_core(trading_enabled: bool = False, sender=None) -> ExecutionCore:
    """Construct an ExecutionCore with injected mocks for DataSource and Clock."""
    config = TradingConfig(trading_enabled=trading_enabled)
    source = MagicMock()
    clock = VirtualClock(datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc))
    return ExecutionCore(source=source, clock=clock, config=config, sender=sender)


def _live_config() -> CopyTradeConfig:
    return CopyTradeConfig(
        mode="live",
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        exit_before_graduation=True,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
        mirror_wallet_sells=False,
    )


def _record() -> OpenedPositionRecord:
    return OpenedPositionRecord(
        cohort_id=_COHORT,
        mint=_MINT,
        trigger_wallet=_WALLET,
        entry_ts=_T0,
    )


# ===========================================================================
# §1 — Gated boundary: mode=live + trading_enabled=False → sent=False (DB)
# ===========================================================================


@pytest.mark.django_db
def test_live_toggle_mode_live_trading_disabled_sent_false():
    """mode=live with trading_enabled=False must reach the gated boundary and stop.

    ExecuteResult.sent must be False — no real order is placed.
    This is the core AC-68.2 capital-OFF / gated-boundary assertion.
    """
    core = _make_core(trading_enabled=False)
    _ct_pos, exec_result = open_live_position(_record(), _ENTRY_PRICE, _live_config(), core)

    assert exec_result.sent is False, (
        f"Expected sent=False (trading_enabled=False), got sent={exec_result.sent!r}"
    )


# ===========================================================================
# §2 — Sender never called when trading_disabled (no DB)
# ===========================================================================


def test_live_toggle_sender_never_called_when_trading_disabled(db):
    """The Sender's send_buy must NOT be called when trading_enabled=False.

    Injects a MagicMock Sender and asserts send_buy was never invoked.
    """
    mock_sender = MagicMock()
    core = _make_core(trading_enabled=False, sender=mock_sender)
    open_live_position(_record(), _ENTRY_PRICE, _live_config(), core)

    mock_sender.send_buy.assert_not_called()


# ===========================================================================
# §3 — Position rows written with correct mode/status (DB)
# ===========================================================================


@pytest.mark.django_db
def test_live_toggle_shared_position_written_with_live_mode():
    """The shared trading.Position written by open_live_position must have mode='live'."""
    core = _make_core(trading_enabled=False)
    open_live_position(_record(), _ENTRY_PRICE, _live_config(), core)

    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.mode == Position.MODE_LIVE, (
        f"Expected mode={Position.MODE_LIVE!r}, got {shared.mode!r}"
    )


@pytest.mark.django_db
def test_live_toggle_copytrade_position_written_with_live_mode():
    """The CopytradePosition written by open_live_position must have mode='live'."""
    core = _make_core(trading_enabled=False)
    open_live_position(_record(), _ENTRY_PRICE, _live_config(), core)

    ct_pos = CopytradePosition.objects.get(cohort_id=_COHORT)
    assert ct_pos.mode == CopytradePosition.MODE_LIVE, (
        f"Expected mode={CopytradePosition.MODE_LIVE!r}, got {ct_pos.mode!r}"
    )


@pytest.mark.django_db
def test_live_toggle_shared_position_status_paper_capital_off():
    """The shared Position must have status='PAPER' (capital-off until Cutover).

    mode=live does NOT mean capital is committed this sprint.  status=PAPER
    is the capital-OFF sentinel — status transitions to OPEN only at Cutover
    (PRD §16) when trading_enabled=True and a real order is sent.
    """
    core = _make_core(trading_enabled=False)
    open_live_position(_record(), _ENTRY_PRICE, _live_config(), core)

    shared = Position.objects.get(source=Position.SOURCE_COPYTRADE)
    assert shared.status == Position.STATUS_PAPER, (
        f"Expected status={Position.STATUS_PAPER!r} (capital-off), got {shared.status!r}"
    )


# ===========================================================================
# §4 — No-auto-start guard: open_live_position never sets trading_enabled=True
# ===========================================================================


def test_live_toggle_no_auto_start_open_live_position_does_not_set_trading_enabled_true():
    """open_live_position source must NOT contain any `trading_enabled=True` assignment.

    AST-level guard: reads the source of copytrade.position_opener and walks the
    AST to confirm no assignment sets trading_enabled to True.  This mirrors the
    AC-61.3 observe-safety-gate pattern to ensure the live path cannot
    auto-enable capital.
    """
    import copytrade.position_opener as _mod

    source_path = inspect.getfile(_mod)
    with open(source_path, "r", encoding="utf-8") as fh:
        source_text = fh.read()

    tree = ast.parse(source_text)

    for node in ast.walk(tree):
        # Look for assignments: trading_enabled = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "trading_enabled":
                    if isinstance(node.value, ast.Constant) and node.value.value is True:
                        raise AssertionError(
                            "open_live_position must NOT contain "
                            "'trading_enabled = True' assignment — "
                            "capital activation is operator-driven at Cutover only."
                        )
        # Look for keyword arguments: trading_enabled=True
        if isinstance(node, ast.keyword):
            if node.arg == "trading_enabled":
                if isinstance(node.value, ast.Constant) and node.value.value is True:
                    raise AssertionError(
                        "open_live_position must NOT contain "
                        "'trading_enabled=True' keyword argument — "
                        "capital activation is operator-driven at Cutover only."
                    )


# ===========================================================================
# §5 — Ops-doc presence check
# ===========================================================================


def _ops_doc_path() -> str:
    """Resolve the absolute path to ops/firehose_activation_log.md."""
    # Walk up from this test file's location to find the project root.
    here = os.path.dirname(os.path.abspath(__file__))
    # trading/tests/ → trading/ → project root
    project_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(project_root, "ops", "firehose_activation_log.md")


def test_ops_doc_helius_wallet_subscription_entry_present():
    """ops/firehose_activation_log.md must contain the required Helius activation entry.

    Checks for:
    - "Helius wallet-subscription" — the entry type
    - "AC-68.2" — the story reference
    - "NOT YET ACTIVATED" — the planned-only status
    """
    path = _ops_doc_path()
    assert os.path.isfile(path), f"ops/firehose_activation_log.md not found at {path}"

    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    assert "Helius wallet-subscription" in content, (
        "ops/firehose_activation_log.md must contain 'Helius wallet-subscription' "
        "(AC-68.2 ops-doc gate)"
    )
    assert "AC-68.2" in content, (
        "ops/firehose_activation_log.md must contain 'AC-68.2' "
        "(story reference required by AC-68.2 gate)"
    )
    assert "NOT YET ACTIVATED" in content, (
        "ops/firehose_activation_log.md must contain 'NOT YET ACTIVATED' "
        "(confirms the entry is planned-only, no spend has occurred)"
    )


def test_ops_doc_helius_entry_is_planned_not_activated():
    """The Helius AC-68.2 entry must be PLANNED and must NOT contain an actual activation date.

    The entry must contain 'PLANNED' to confirm its planned-only status.
    It must NOT contain a real activation date line like 'Date: 2026-' paired
    with AC-68.2, which would indicate the budget was actually consumed.
    """
    path = _ops_doc_path()
    assert os.path.isfile(path), f"ops/firehose_activation_log.md not found at {path}"

    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    assert "PLANNED" in content, (
        "ops/firehose_activation_log.md must contain 'PLANNED' for the AC-68.2 entry"
    )

    # The ops doc must NOT have an activation table row that would indicate the
    # AC-68.2 Helius wallet-subscription spend was actually consumed.
    # A real activation row would look like: "| 2026-06-18 | ... | Helius accountSubscribe |"
    # We check that no row pairing a date with "accountSubscribe" and "copy-trade LIVE" exists.
    lines = content.splitlines()
    for line in lines:
        if (
            "accountSubscribe" in line
            and "copy-trade LIVE" in line
            and line.strip().startswith("|")
            and "2026-" in line
            and "NOT YET ACTIVATED" not in line
            and "PLANNED" not in line
        ):
            raise AssertionError(
                f"Found a line that looks like an activated AC-68.2 Helius row: {line!r}. "
                "The Helius wallet-subscription must remain PLANNED (not activated) this sprint."
            )


# ===========================================================================
# §6 — H1 ImportError trap
# ===========================================================================


def test_open_live_position_importable_ac682():
    """open_live_position must be importable from copytrade.position_opener and callable."""
    from copytrade.position_opener import open_live_position as _olp

    assert callable(_olp), "open_live_position must be callable"
