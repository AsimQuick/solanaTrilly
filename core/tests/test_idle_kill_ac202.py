# ---
# module: core.tests.test_idle_kill_ac202
# sprint: sprint-5
# story: US-20 AC-20.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.idle_kill, core.tape.recorder, core.resolver,
#               core.models, core.clock, core.datasource, asyncio, datetime, pytest
# ---
"""AC-20.2 — Idle-kill TTL: deactivate after TTL, re-attach on next swap, config-driven.

Scenario (virtual clock):
  T0:            SWAP for MINT_M — mint becomes active, last_seen = T0
  T0 + TTL + 1s: TICK event     — check_idle fires, MINT_M deactivated
  T0 + TTL + 2s: SWAP for MINT_M — re-attaches AND is recorded

idle_kill_ttl_s is read from get_active_config() (US-11 resolver) on every
check_idle() call, so changing the active config changes the timing.

Tests:
  1. test_idle_deactivates_mint_after_ttl
       IdleKillMonitor.check_idle at T0+TTL → mint deactivated; at T0+TTL-1 → not yet.
  2. test_reattach_on_next_swap
       After deactivation, record_swap re-attaches the mint and returns True.
  3. test_recorder_records_swap_after_reattach
       Full recorder integration: SWAP → TICK (past TTL) → SWAP yields 2 NormalizedSwaps
       with deactivation_events and reattachment_events both logged.
  4. test_changing_config_ttl_changes_deactivation_timing
       Config switched mid-test from TTL=100 to TTL=40; second check_idle at T0+50s
       now deactivates (50 >= 40) where the first check did not (50 < 100).
  5. test_no_getenv_in_idle_kill_module
       AST guard: idle_kill.py must not call os.getenv or reference os.environ
       (Principle #1 — all tunables come from get_active_config, never env vars).
"""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncGenerator

import pytest

from core.datasource import DataSource
from core.models import PipelineConfig

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
IDLE_KILL_PY = REPO_ROOT / "core" / "tape" / "idle_kill.py"

_UTC = timezone.utc
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=_UTC)
_TTL_S = 60  # used in tests 1-3

# Minimal valid config sections for PipelineConfig.
# §5.2 constraints:
#   scoring.window_s (20) > score_at_elapsed_s (10)  ← leak guard
#   tape.idle_kill_ttl_s >= outcome.window_s (30)     ← D4
#   capture_buffer_s (4) >= 3
_SCORING = {"score_at_elapsed_s": 10, "window_s": 20, "capture_buffer_s": 4, "gate": "adaptive_topk"}
_OUTCOME = {"window_s": 30, "label_def": {}}
_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}

_MINT = "IDLE_MINT_1111111111111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"
_GRADUATED_BT = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BT)


def _tape_section(idle_kill_ttl_s: int) -> dict:
    return {
        "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
        "idle_kill_ttl_s": idle_kill_ttl_s,
        "reattach": True,
        "birdeye_interval_s": 15,
    }


def _swap_event(n: int) -> dict:
    """Return a valid raw swap event dict (index *n*, 1-based)."""
    sig = f"IDLESIG{n:04d}" + "X" * 85
    owner = f"IDLEOWN{n:04d}" + "W" * 53
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _GRADUATED_BT + 1000 + n,
        "slot": 5000 + n,
        "signature": sig[:128],
        "side": "buy" if n % 2 == 1 else "sell",
        "price": 0.0001 + n * 0.00001,
        "vol_sol": 1.0 + n * 0.1,
        "vol_usd": 150.0 + n * 10.0,
        "sol_usd": 150.0,
        "owner": owner[:64],
        "base_reserve": 2_000_000 - n * 1000,
        "quote_reserve": 500_000 + n * 1000,
        "quote_mint": _QUOTE_MINT,
        "failed": False,
    }


def _tick_event() -> dict:
    """Non-swap event: no price/vol/mint — triggers idle check without a swap."""
    return {"type": "HEARTBEAT"}


class _TimedReplaySource(DataSource):
    """Test-only DataSource: sets the VirtualClock to each event's target time."""

    def __init__(
        self,
        events_with_times: list[tuple[dict[str, Any], datetime]],
        clock: Any,
    ) -> None:
        self._events_with_times = events_with_times
        self._clock = clock

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        for event, t in self._events_with_times:
            self._clock.set_time(t)
            yield event


# ---------------------------------------------------------------------------
# Test 1: mint deactivated after TTL elapses
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_idle_deactivates_mint_after_ttl() -> None:
    """IdleKillMonitor deactivates a mint when check_idle is called past TTL.

    At T0 + TTL - 1s: not yet deactivated.
    At T0 + TTL:      deactivated (elapsed >= ttl_s).
    """
    from core.resolver import get_active_config, invalidate_active_config_cache
    from core.tape.idle_kill import IdleKillMonitor

    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac202-deactivate",
        is_active=True,
        tape=_tape_section(_TTL_S),
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )

    monitor = IdleKillMonitor(get_active_config)
    monitor.record_swap(_MINT, _T0)

    # One second before TTL: still active
    early = _T0 + timedelta(seconds=_TTL_S - 1)
    result = monitor.check_idle(early)
    assert _MINT not in result, "Mint must not be deactivated before TTL elapses."
    assert not monitor.is_deactivated(_MINT)

    # Exactly at TTL: deactivated
    at_ttl = _T0 + timedelta(seconds=_TTL_S)
    result = monitor.check_idle(at_ttl)
    assert _MINT in result, "Mint must be deactivated when elapsed >= idle_kill_ttl_s."
    assert monitor.is_deactivated(_MINT)
    assert len(monitor.deactivation_events) == 1
    assert monitor.deactivation_events[0][0] == _MINT
    assert monitor.deactivation_events[0][1] == at_ttl


# ---------------------------------------------------------------------------
# Test 2: re-attach on next swap after deactivation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reattach_on_next_swap() -> None:
    """record_swap re-attaches a deactivated mint and returns True.

    After re-attachment:
      - is_deactivated(mint) is False
      - reattachment_events has one entry
      - last_swap_time is updated to the re-attach timestamp
    """
    from core.resolver import get_active_config, invalidate_active_config_cache
    from core.tape.idle_kill import IdleKillMonitor

    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac202-reattach",
        is_active=True,
        tape=_tape_section(_TTL_S),
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )

    monitor = IdleKillMonitor(get_active_config)
    monitor.record_swap(_MINT, _T0)
    monitor.check_idle(_T0 + timedelta(seconds=_TTL_S))  # deactivate
    assert monitor.is_deactivated(_MINT), "Precondition: mint must be deactivated."

    # Swap arrives after TTL → re-attach
    t_pump = _T0 + timedelta(seconds=_TTL_S + 5)
    reattached = monitor.record_swap(_MINT, t_pump)

    assert reattached is True, "record_swap must return True when re-attaching."
    assert not monitor.is_deactivated(_MINT), "Mint must be active after re-attach."
    assert len(monitor.reattachment_events) == 1
    assert monitor.reattachment_events[0] == (_MINT, t_pump)

    # Subsequent check_idle should NOT immediately re-deactivate (last_swap just updated)
    still_active = monitor.check_idle(_T0 + timedelta(seconds=_TTL_S + 6))
    assert _MINT not in still_active, (
        "Mint must not be immediately re-deactivated after re-attach: "
        "last_swap_time was just updated."
    )


# ---------------------------------------------------------------------------
# Test 3: recorder records swap before AND after re-attach
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_recorder_records_swap_after_reattach() -> None:
    """Full recorder integration: swap → tick past TTL → swap yields 2 NormalizedSwaps.

    Event sequence (via _TimedReplaySource with VirtualClock):
      1. SWAP for MINT at T0           → recorded, monitor.last_seen = T0
      2. TICK  at T0 + TTL + 1s        → check_idle fires, MINT deactivated
      3. SWAP for MINT at T0 + TTL + 2s → re-attaches, recorded

    Assertions:
      - 2 NormalizedSwaps in recorder.normalized_swaps
      - monitor.deactivation_events has 1 entry for MINT
      - monitor.reattachment_events  has 1 entry for MINT
    """
    from core.clock import VirtualClock
    from core.resolver import get_active_config, invalidate_active_config_cache
    from core.tape.idle_kill import IdleKillMonitor
    from core.tape.recorder import TapeRecorder

    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac202-recorder",
        is_active=True,
        tape=_tape_section(_TTL_S),
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )

    clock = VirtualClock(_T0)
    monitor = IdleKillMonitor(get_active_config)

    events_with_times: list[tuple[dict, datetime]] = [
        (_swap_event(1), _T0),
        (_tick_event(), _T0 + timedelta(seconds=_TTL_S + 1)),
        (_swap_event(2), _T0 + timedelta(seconds=_TTL_S + 2)),
    ]
    source = _TimedReplaySource(events_with_times, clock)

    recorder = TapeRecorder(
        source,
        clock,
        token_store={_MINT: _TOKEN},
        idle_monitor=monitor,
    )
    asyncio.run(recorder.run())

    swaps = recorder.normalized_swaps
    assert len(swaps) == 2, (
        f"Expected 2 NormalizedSwaps (before TTL and after re-attach), got {len(swaps)}."
    )

    assert len(monitor.deactivation_events) == 1, (
        "Expected exactly 1 deactivation event for MINT."
    )
    assert monitor.deactivation_events[0][0] == _MINT

    assert len(monitor.reattachment_events) == 1, (
        "Expected exactly 1 re-attachment event for MINT."
    )
    assert monitor.reattachment_events[0][0] == _MINT


# ---------------------------------------------------------------------------
# Test 4: changing active config TTL changes deactivation timing
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_changing_config_ttl_changes_deactivation_timing() -> None:
    """Switching active config changes idle_kill_ttl_s honored by check_idle.

    Step 1: config with TTL=100; check at T0+50s → NOT deactivated (50 < 100).
    Step 2: activate new config with TTL=40; check at T0+50s → deactivated (50 >= 40).

    This verifies that idle_kill_ttl_s is read from get_active_config() on every
    call (Principle #1), not cached at monitor construction time.
    """
    from core.resolver import activate_config, get_active_config, invalidate_active_config_cache
    from core.tape.idle_kill import IdleKillMonitor

    invalidate_active_config_cache()

    # Config 1: TTL=100 (>= outcome.window_s=30)
    config1 = PipelineConfig.objects.create(
        version=1,
        label="ac202-ttl100",
        is_active=False,
        tape=_tape_section(100),
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )
    activate_config(config1.id)

    monitor = IdleKillMonitor(get_active_config)
    monitor.record_swap(_MINT, _T0)

    # At T0 + 50s: NOT deactivated with TTL=100
    result_before = monitor.check_idle(_T0 + timedelta(seconds=50))
    assert _MINT not in result_before, (
        "Mint must NOT be deactivated at 50s when idle_kill_ttl_s=100."
    )
    assert not monitor.is_deactivated(_MINT)

    # Config 2: TTL=40 (>= outcome.window_s=30); activate it, cache invalidated
    config2 = PipelineConfig.objects.create(
        version=2,
        label="ac202-ttl40",
        is_active=False,
        tape=_tape_section(40),
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )
    activate_config(config2.id)  # also calls invalidate_active_config_cache()

    # Same check at T0 + 50s: NOW deactivated because TTL changed to 40
    result_after = monitor.check_idle(_T0 + timedelta(seconds=50))
    assert _MINT in result_after, (
        "Mint MUST be deactivated at 50s when idle_kill_ttl_s=40 "
        "(the active config was changed — monitor must re-read it dynamically)."
    )
    assert monitor.is_deactivated(_MINT)


# ---------------------------------------------------------------------------
# Test 5: static-analysis guard — no os.getenv/environ in idle_kill.py
# ---------------------------------------------------------------------------


def test_no_getenv_in_idle_kill_module() -> None:
    """idle_kill.py must not use os.getenv or os.environ (Principle #1).

    All tunables — including idle_kill_ttl_s — must come from the injected
    config_resolver (get_active_config).  Direct env-var access bypasses the
    US-11 resolver and breaks the Principle #1 contract.
    """
    assert IDLE_KILL_PY.exists(), f"idle_kill.py not found at {IDLE_KILL_PY}"

    source = IDLE_KILL_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IDLE_KILL_PY))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                violations.append(f"line {node.lineno}: os.getenv(...)")
            if isinstance(func, ast.Name) and func.id == "getenv":
                violations.append(f"line {node.lineno}: getenv(...)")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            violations.append(f"line {node.lineno}: os.environ")

    assert not violations, (
        "idle_kill.py must not use os.getenv or os.environ (Principle #1). "
        "Use the injected config_resolver (get_active_config) instead.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
