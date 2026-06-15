# ---
# module: core.tests.test_tape_recorder_ac181
# sprint: sprint-5
# story: US-18 AC-18.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.recorder, core.clock, core.replay_source, ast, asyncio, pathlib
# ---
"""AC-18.1 — TapeRecorder behind DataSource/Clock seam + static-analysis guard.

Tests:
  1. test_recorder_processes_swap_events_from_replay_source
       Drives TapeRecorder with a ReplaySource holding 3 swap-shaped events.
       Asserts all 3 events are stored in recorder.processed with correct payloads.

  2. test_recorder_timestamps_use_injected_clock
       Drives recorder with 3 events and a VirtualClock starting at a known t0.
       Because VirtualClock does not auto-advance, all timestamps equal t0 —
       proving the recorder reads 'now' from the injected clock, not wall time.

  3. test_recorder_empty_source_processes_zero_events
       ReplaySource([]) → recorder.processed == [].

  4. test_recorder_single_event
       Single-event source → processed length is 1 and the event payload matches.

  5. test_no_concrete_source_import_in_tape_module
       AST-scan all .py files under core/tape/; fail if any imports
       LiveSource, ReplaySource, core.live_source, or core.replay_source.

  6. test_no_direct_time_call_in_tape_module
       AST-scan all .py files under core/tape/; fail if any contains
       a Call node for datetime.now(...) or time.time(...).
"""
import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TAPE_ROOT = REPO_ROOT / "core" / "tape"

_CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}
_CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SWAP_EVENTS: list[dict[str, Any]] = [
    {
        "type": "SWAP",
        "mint": "MINT_A",
        "block_time": 1_700_000_001,
        "slot": 100,
        "signature": "sig1",
        "side": "buy",
        "price": 0.0001,
        "vol_sol": 1.0,
        "vol_usd": 100.0,
        "sol_usd": 100.0,
        "owner": "WALLET_1",
        "base_reserve": 1_000_000,
        "quote_reserve": 2_000_000,
        "quote_mint": "So11111111111111111111111111111111111111112",
        "failed": False,
    },
    {
        "type": "SWAP",
        "mint": "MINT_A",
        "block_time": 1_700_000_002,
        "slot": 101,
        "signature": "sig2",
        "side": "sell",
        "price": 0.00012,
        "vol_sol": 0.5,
        "vol_usd": 50.0,
        "sol_usd": 100.0,
        "owner": "WALLET_2",
        "base_reserve": 900_000,
        "quote_reserve": 2_100_000,
        "quote_mint": "So11111111111111111111111111111111111111112",
        "failed": False,
    },
    {
        "type": "SWAP",
        "mint": "MINT_A",
        "block_time": 1_700_000_003,
        "slot": 102,
        "signature": "sig3",
        "side": "buy",
        "price": 0.00011,
        "vol_sol": 2.0,
        "vol_usd": 200.0,
        "sol_usd": 100.0,
        "owner": "WALLET_3",
        "base_reserve": 950_000,
        "quote_reserve": 2_050_000,
        "quote_mint": "So11111111111111111111111111111111111111112",
        "failed": False,
    },
]

_SINGLE_SWAP_EVENT: dict[str, Any] = _SWAP_EVENTS[0]

# ---------------------------------------------------------------------------
# Behavioral tests (criterion a)
# ---------------------------------------------------------------------------


def test_recorder_processes_swap_events_from_replay_source() -> None:
    """TapeRecorder stores all 3 swap events from a ReplaySource."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=_SWAP_EVENTS)
        clock = VirtualClock(t0)
        recorder = TapeRecorder(source, clock)
        await recorder.run()
        return recorder.processed

    result = asyncio.run(_run())

    assert len(result) == 3, f"Expected 3 processed events, got {len(result)}"
    for i, (event, _ts) in enumerate(result):
        assert event == _SWAP_EVENTS[i], (
            f"Event {i} payload mismatch: expected {_SWAP_EVENTS[i]}, got {event}"
        )


def test_recorder_timestamps_use_injected_clock() -> None:
    """All timestamps in processed equal the VirtualClock's fixed t0.

    VirtualClock does not auto-advance, so every call to clock.now() returns t0.
    This proves the recorder reads time from the injected clock — not wall time.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=_SWAP_EVENTS)
        clock = VirtualClock(t0)
        recorder = TapeRecorder(source, clock)
        await recorder.run()
        return recorder.processed

    result = asyncio.run(_run())

    assert len(result) == 3, f"Expected 3 stamped events, got {len(result)}"
    for i, (_event, ts) in enumerate(result):
        assert ts == t0, (
            f"Event {i}: expected timestamp {t0} (from VirtualClock), got {ts}. "
            "This suggests the recorder is reading wall time instead of the injected clock."
        )


def test_recorder_empty_source_processes_zero_events() -> None:
    """TapeRecorder with an empty ReplaySource stores no events."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=[])
        clock = VirtualClock(t0)
        recorder = TapeRecorder(source, clock)
        await recorder.run()
        return recorder.processed

    result = asyncio.run(_run())
    assert result == [], f"Expected empty processed list, got {result}"


def test_recorder_single_event() -> None:
    """TapeRecorder with one event stores exactly 1 entry with the correct payload."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=[_SINGLE_SWAP_EVENT])
        clock = VirtualClock(t0)
        recorder = TapeRecorder(source, clock)
        await recorder.run()
        return recorder.processed

    result = asyncio.run(_run())

    assert len(result) == 1, f"Expected 1 processed event, got {len(result)}"
    event, ts = result[0]
    assert event == _SINGLE_SWAP_EVENT, (
        f"Event payload mismatch: expected {_SINGLE_SWAP_EVENT}, got {event}"
    )
    assert ts == t0, f"Timestamp mismatch: expected {t0}, got {ts}"


# ---------------------------------------------------------------------------
# Static-analysis guards (criterion b)
# ---------------------------------------------------------------------------


def _tape_py_files() -> list[Path]:
    """Return all .py files under core/tape/ (if the package exists)."""
    if not TAPE_ROOT.is_dir():
        return []
    return list(TAPE_ROOT.rglob("*.py"))


def _concrete_source_violations(py_file: Path) -> list[str]:
    """Return import-violation descriptions found in *py_file*.

    Detects any import of LiveSource, ReplaySource, core.live_source,
    or core.replay_source via AST analysis.
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _CONCRETE_MODULE_NAMES:
                found.append(f"{rel}: 'from {module} import ...'")
                continue
            for alias in node.names:
                if alias.name in _CONCRETE_CLASS_NAMES:
                    found.append(f"{rel}: 'from {module} import {alias.name}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _CONCRETE_MODULE_NAMES:
                    found.append(f"{rel}: 'import {alias.name}'")

    return found


def _time_call_violations(py_file: Path) -> list[str]:
    """Return forbidden time-call descriptions found in *py_file*.

    Detects Call nodes for datetime.now(...) or time.time(...) via AST analysis.
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datetime"
        ):
            found.append(
                f"{rel}:{node.lineno}: forbidden 'datetime.now()' call in tape module"
            )

        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "time"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            found.append(
                f"{rel}:{node.lineno}: forbidden 'time.time()' call in tape module"
            )

    return found


def test_no_concrete_source_import_in_tape_module() -> None:
    """Static-analysis guard: core/tape/ must never import concrete DataSource classes.

    Scans all Python files under core/tape/ and fails if any file imports
    LiveSource, ReplaySource, core.live_source, or core.replay_source.
    Tape code must depend only on the DataSource interface (Principle #7).
    """
    all_violations: list[str] = []
    for py_file in _tape_py_files():
        all_violations.extend(_concrete_source_violations(py_file))

    assert not all_violations, (
        "Tape modules must depend only on the DataSource interface (core.datasource), "
        "never on a concrete source class (Principle #7 / PRD §4).\n"
        "Violations found:\n" + "\n".join(f"  {v}" for v in all_violations)
    )


def test_no_direct_time_call_in_tape_module() -> None:
    """Static-analysis guard: core/tape/ must never call datetime.now() or time.time().

    Scans all Python files under core/tape/ using AST Call-node analysis.
    Tape code must read 'now' only via the injected Clock abstraction (AC-2.2).
    """
    all_violations: list[str] = []
    for py_file in _tape_py_files():
        all_violations.extend(_time_call_violations(py_file))

    assert not all_violations, (
        "Tape modules must read 'now' only from the injected Clock abstraction "
        "(AC-2.2 / PRD §4).  Direct datetime.now() or time.time() calls bypass "
        "the Clock seam and break replay parity.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in all_violations)
    )
