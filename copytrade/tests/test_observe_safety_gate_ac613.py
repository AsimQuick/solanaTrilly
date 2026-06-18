# ---
# module: copytrade.tests.test_observe_safety_gate_ac613
# sprint: sprint-12
# story: US-61 AC-61.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, unittest.mock, copytrade.execution,
#   copytrade.position_opener, copytrade.models, copytrade.schemas,
#   copytrade.trigger_pipeline, core.models, ast, pathlib
# ---
"""AC-61.3: OBSERVE-FIRST SAFETY GATE — AST no-auto-start guard + functional gate.

SPEC §10 safety gate: mode defaults to 'observe'; LIVE (real-order) execution is
OUT OF SCOPE this sprint (P8-gated).  The engine MUST NOT place real orders in
any mode this sprint, and live remains a deliberate, P8-gated operator switch.

Two verification layers:

1. AST guard (US-11 pattern extended to the copytrade path):
   Scans all copytrade source modules (non-test, non-migration) and asserts:
   - NO copytrade boot/resolver/upload/ON path assigns mode='live' automatically
     (no keyword-arg mode='live' in function calls, no attribute assignment
     obj.mode='live' that auto-flips the engine to live without P8 gate).
   - NO copytrade path sets trading_enabled=True — §5 isolation: copytrade must
     not touch PipelineState control flags (§5.3/§15.6 no-auto-start gate).

2. DB-backed functional gate:
   - Flipping engine_on=True with mode='observe' NEVER invokes place_buy_order.
   - CopyTradeSettings.mode remains 'observe' — never auto-flipped to 'live'.
   - PipelineState.trading_enabled remains False — not mutated by any copytrade
     path (§5 isolation / §15.6 no-auto-start gate).

H1 ImportError traps (wiring guards, module-level):
   Imports fail pytest COLLECTION if execution or position_opener is deleted
   or renamed — mirroring the standing H1 pattern (AC-11.3 / AC-42.3 / AC-51.3).

Test structure
--------------
H1 ImportError traps:
    test_place_buy_order_importable_ac613
    test_open_observe_position_importable_ac613

AST guards (US-11 pattern extended to copytrade path):
    test_copytrade_modules_do_not_auto_flip_mode_to_live
    test_copytrade_modules_do_not_set_trading_enabled_true
    test_copytrade_ast_scan_is_non_degenerate

DB-backed functional gate:
    test_engine_on_in_observe_never_places_real_order
    test_engine_on_in_observe_mode_flag_stays_observe
    test_engine_on_in_observe_trading_enabled_stays_false
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copytrade.execution import place_buy_order
from copytrade.models import CopyTradeSettings
from copytrade.position_opener import open_observe_position
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord

assert place_buy_order  # H1 guard — deletion/rename fails collection
assert open_observe_position  # H1 guard — deletion/rename fails collection

# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
COPYTRADE_SRC = REPO_ROOT / "copytrade"

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
_COHORT_ID = "cohort-ac613-safety-gate"
_MINT = "MintAC613PumpXXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_WALLET = "Wa11etAC613AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_ENTRY_PRICE = 0.000_010_000


def _record() -> OpenedPositionRecord:
    return OpenedPositionRecord(
        cohort_id=_COHORT_ID,
        mint=_MINT,
        trigger_wallet=_WALLET,
        entry_ts=_TS,
    )


def _observe_config() -> CopyTradeConfig:
    return CopyTradeConfig(
        mode="observe",
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        exit_before_graduation=True,
        curve_completion_exit_pct=90.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
        mirror_wallet_sells=False,
        engine_on=True,
    )


# ---------------------------------------------------------------------------
# AST guard helpers (US-11 pattern extended to copytrade path)
# ---------------------------------------------------------------------------


def _find_copytrade_source_files() -> list[Path]:
    """Return copytrade source files excluding tests and migrations."""
    excluded_parts = {".git", "__pycache__", "migrations", ".venv", "node_modules", "tests"}
    result = []
    for f in COPYTRADE_SRC.rglob("*.py"):
        if not excluded_parts.intersection(set(f.parts)):
            result.append(f)
    return result


def _find_mode_live_assignments(path: Path) -> list[str]:
    """Return descriptions of mode='live' keyword-arg or attribute assignments.

    Flags two shapes that would auto-flip the engine to live mode:
      - keyword arg:        some_fn(..., mode="live", ...)
      - attribute assign:   obj.mode = "live"

    Does NOT flag:
      - Literal["observe", "live"]  (type annotation subscript — not assignment)
      - MODE_LIVE = "live"          (class constant Name target — not attribute)
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: objects.create(mode="live") / settings.save(mode="live")
        if isinstance(node, ast.keyword):
            if (
                node.arg == "mode"
                and isinstance(node.value, ast.Constant)
                and node.value.value == "live"
            ):
                findings.append(
                    f"{rel}: keyword mode='live' at line {node.value.lineno}"
                )

        # attribute assignment: obj.mode = "live"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "mode"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == "live"
                ):
                    findings.append(
                        f"{rel}: attribute .mode = 'live' at line {node.value.lineno}"
                    )

    return findings


def _find_trading_enabled_true(path: Path) -> list[str]:
    """Return descriptions of trading_enabled=True assignments (US-11 guard pattern).

    Mirrors _find_flag_true_assignments from AC-11.3 / AC-42.3 / AC-51.3, scoped
    to trading_enabled only — copytrade code must never touch PipelineState flags.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: objects.update(trading_enabled=True)
        if isinstance(node, ast.keyword):
            if (
                node.arg == "trading_enabled"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                findings.append(
                    f"{rel}: keyword trading_enabled=True at line {node.value.lineno}"
                )

        # attribute assignment: state.trading_enabled = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "trading_enabled"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    findings.append(
                        f"{rel}: attribute .trading_enabled = True at line {node.value.lineno}"
                    )

    return findings


# ===========================================================================
# H1 ImportError trap tests
# ===========================================================================


def test_place_buy_order_importable_ac613():
    """place_buy_order is importable from copytrade.execution (H1 wiring guard).

    The module-level import already enforces this at collection time; this test
    makes the requirement explicit and self-documenting.
    """
    from copytrade.execution import place_buy_order as _pbo

    assert callable(_pbo), "place_buy_order must be callable"


def test_open_observe_position_importable_ac613():
    """open_observe_position is importable from copytrade.position_opener (H1 guard)."""
    from copytrade.position_opener import open_observe_position as _oop

    assert callable(_oop), "open_observe_position must be callable"


# ===========================================================================
# AST guard tests (US-11 pattern extended to the copytrade path)
# ===========================================================================


def test_copytrade_modules_do_not_auto_flip_mode_to_live():
    """No copytrade source module assigns mode='live' on any boot/ON/upload path.

    Extends the US-11 no-auto-start guard (AC-11.3 / AC-42.3 / AC-51.3) to
    the copytrade path: mode must remain 'observe' unless the operator
    explicitly sets it via a deliberate, P8-gated action (SPEC §10).

    Scanned shapes (auto-flip indicators):
      - keyword arg: some_call(mode="live")
      - attribute assignment: obj.mode = "live"

    NOT flagged (safe constant/annotation uses):
      - Literal["observe", "live"] — type annotation subscript, not assignment
      - MODE_LIVE = "live" — class constant (Name target, not Attribute)
    """
    files = _find_copytrade_source_files()
    violations: list[str] = []
    for path in files:
        violations.extend(_find_mode_live_assignments(path))

    assert not violations, (
        "Copytrade source module assigns mode='live' — no code path may auto-flip "
        "mode to 'live' without an explicit P8-gated operator action (SPEC §10).\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_copytrade_modules_do_not_set_trading_enabled_true():
    """No copytrade source module sets trading_enabled=True (§5 isolation + §15.6).

    The copy-trade engine is §5-isolated: it must not touch PipelineState
    (scoring_enabled / trading_enabled / firehose_active).  This guard mirrors
    the AC-11.3 / AC-42.3 / AC-51.3 pattern, extended to the copytrade path.

    If any copytrade module sets trading_enabled=True it both violates §5
    isolation (touching the model pipeline's control plane) and the SPEC §10
    no-auto-start contract (live remains a deliberate P8-gated operator switch).
    """
    files = _find_copytrade_source_files()
    violations: list[str] = []
    for path in files:
        violations.extend(_find_trading_enabled_true(path))

    assert not violations, (
        "Copytrade source module sets trading_enabled=True — the copytrade engine "
        "must not touch PipelineState.trading_enabled (§5 isolation + §15.6 "
        "no-auto-start guard).  Violations:\n" + "\n".join(violations)
    )


def test_copytrade_ast_scan_is_non_degenerate():
    """The copytrade source scan finds at least one file (degenerate-pass guard).

    If the copytrade/ directory were renamed or emptied the two AST guards above
    would trivially pass — this test prevents that silent bypass.
    """
    files = _find_copytrade_source_files()
    assert len(files) >= 1, (
        "Expected at least one Python source file in copytrade/ for the safety-gate "
        "AST scan to cover.  If the directory was moved, update "
        "_find_copytrade_source_files()."
    )


# ===========================================================================
# DB-backed functional gate
# ===========================================================================


@pytest.mark.django_db
def test_engine_on_in_observe_never_places_real_order():
    """Flipping engine_on=True in observe mode MUST NOT invoke place_buy_order.

    This is the AC-61.3 functional execution guard: the complete 'observe open'
    sequence (engine_on=True, mode='observe', then a valid trigger opens a
    position) is paper-only — no real Solana order is ever submitted.  We patch
    the P8-gated execution stub and assert it is never called.

    'the engine MUST NOT place real orders in any mode this sprint' (AC-61.3).
    """
    settings = CopyTradeSettings.get()
    settings.engine_on = True
    settings.mode = "observe"
    settings.save()

    config = _observe_config()

    mock_order = MagicMock()
    with patch("copytrade.execution.place_buy_order", mock_order):
        open_observe_position(_record(), _ENTRY_PRICE, config)

    mock_order.assert_not_called()


@pytest.mark.django_db
def test_engine_on_in_observe_mode_flag_stays_observe():
    """CopyTradeSettings.mode must remain 'observe' after flipping engine_on=True.

    No code path on the boot/ON/open sequence may auto-flip mode to 'live' —
    live remains a deliberate, P8-gated operator switch (SPEC §10).

    'live remains a deliberate, P8-gated operator switch' (AC-61.3).
    """
    settings = CopyTradeSettings.get()
    settings.engine_on = True
    settings.mode = "observe"
    settings.save()

    config = _observe_config()
    open_observe_position(_record(), _ENTRY_PRICE, config)

    settings.refresh_from_db()
    assert settings.mode == "observe", (
        f"CopyTradeSettings.mode was auto-flipped to {settings.mode!r} — "
        "mode must stay 'observe' unless an operator explicitly sets it via a "
        "deliberate P8-gated action (SPEC §10 / AC-61.3)."
    )


@pytest.mark.django_db
def test_engine_on_in_observe_trading_enabled_stays_false():
    """PipelineState.trading_enabled must remain False after toggling copytrade engine_on.

    The copy-trade engine is §5-isolated: toggling engine_on must not affect the
    model pipeline's trading_enabled flag (§5 / §15.6 no-auto-start gate).  The
    copytrade engine has its own ON/OFF (engine_on) that gates ONLY this engine.

    'live remains a deliberate, P8-gated operator switch' — and it must not be
    an implicit side effect of any copy-trade action (AC-61.3).
    """
    from core.models import PipelineState

    state = PipelineState.get()
    assert state.trading_enabled is False  # baseline

    settings = CopyTradeSettings.get()
    settings.engine_on = True
    settings.mode = "observe"
    settings.save()

    config = _observe_config()
    open_observe_position(_record(), _ENTRY_PRICE, config)

    state.refresh_from_db()
    assert state.trading_enabled is False, (
        "PipelineState.trading_enabled was flipped True — the copytrade engine "
        "must not touch the model pipeline's trading_enabled flag (§5 isolation / "
        "§15.6 no-auto-start guard, AC-61.3)."
    )
