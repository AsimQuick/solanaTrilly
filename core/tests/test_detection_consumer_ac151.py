# ---
# module: core.tests.test_detection_consumer_ac151
# sprint: sprint-4
# story: US-15 AC-15.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.consumer, core.clock, core.replay_source, ast, asyncio, pathlib
# ---
"""AC-15.1 — DetectionConsumer behind DataSource/Clock seam + static-analysis guard.

Tests:
  1. test_consumer_processes_meme_events_from_replay_source
       Drives DetectionConsumer with a ReplaySource holding 3 MEME-shaped events.
       Asserts all 3 events are stored in consumer.processed with correct payloads.

  2. test_consumer_timestamps_use_injected_clock
       Drives consumer with 3 events and a VirtualClock starting at a known t0.
       Because VirtualClock does not auto-advance, all timestamps equal t0 —
       proving the consumer reads 'now' from the injected clock, not wall time.

  3. test_consumer_empty_source_processes_zero_events
       ReplaySource([]) → consumer.processed == [].

  4. test_consumer_single_graduation_event
       Single-event source → processed length is 1 and the event payload matches.

  5. test_no_concrete_source_import_in_detection_module
       AST-scan all .py files under core/detection/; fail if any imports
       LiveSource, ReplaySource, core.live_source, or core.replay_source.

  6. test_no_direct_time_call_in_detection_module
       AST-scan all .py files under core/detection/; fail if any contains
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
DETECTION_ROOT = REPO_ROOT / "core" / "detection"

# Concrete class/module names forbidden inside the detection package.
_CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}
_CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_MEME_EVENTS: list[dict[str, Any]] = [
    {
        "type": "MEME_DATA",
        "address": "MINT_1",
        "graduated": True,
        "source": "pump_dot_fun",
        "progress_percent": 100.0,
    },
    {
        "type": "MEME_DATA",
        "address": "MINT_2",
        "graduated": True,
        "source": "pump_dot_fun",
        "progress_percent": 100.0,
    },
    {
        "type": "MEME_DATA",
        "address": "MINT_3",
        "graduated": True,
        "source": "pump_dot_fun",
        "progress_percent": 100.0,
    },
]

_SINGLE_GRADUATION_EVENT: dict[str, Any] = {
    "type": "MEME_DATA",
    "address": "SOLO_MINT",
    "graduated": True,
    "source": "pump_dot_fun",
    "progress_percent": 100.0,
}

# ---------------------------------------------------------------------------
# Behavioral tests (criterion a)
# ---------------------------------------------------------------------------


def test_consumer_processes_meme_events_from_replay_source() -> None:
    """DetectionConsumer stores all 3 MEME events from a ReplaySource."""
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=_MEME_EVENTS)
        clock = VirtualClock(t0)
        consumer = DetectionConsumer(source, clock)
        await consumer.run()
        return consumer.processed

    result = asyncio.run(_run())

    assert len(result) == 3, f"Expected 3 processed events, got {len(result)}"
    for i, (event, _ts) in enumerate(result):
        assert event == _MEME_EVENTS[i], (
            f"Event {i} payload mismatch: expected {_MEME_EVENTS[i]}, got {event}"
        )


def test_consumer_timestamps_use_injected_clock() -> None:
    """All timestamps in processed equal the VirtualClock's fixed t0.

    VirtualClock does not auto-advance, so every call to clock.now() returns t0.
    This proves the consumer reads time from the injected clock — not wall time.
    If wall time were used, the timestamps would differ from t0.
    """
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=_MEME_EVENTS)
        clock = VirtualClock(t0)
        consumer = DetectionConsumer(source, clock)
        await consumer.run()
        return consumer.processed

    result = asyncio.run(_run())

    assert len(result) == 3, f"Expected 3 stamped events, got {len(result)}"
    for i, (_event, ts) in enumerate(result):
        assert ts == t0, (
            f"Event {i}: expected timestamp {t0} (from VirtualClock), got {ts}. "
            "This suggests the consumer is reading wall time instead of the injected clock."
        )


def test_consumer_empty_source_processes_zero_events() -> None:
    """DetectionConsumer with an empty ReplaySource stores no events."""
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=[])
        clock = VirtualClock(t0)
        consumer = DetectionConsumer(source, clock)
        await consumer.run()
        return consumer.processed

    result = asyncio.run(_run())
    assert result == [], f"Expected empty processed list, got {result}"


def test_consumer_single_graduation_event() -> None:
    """DetectionConsumer with one event stores exactly 1 entry with the correct payload."""
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=[_SINGLE_GRADUATION_EVENT])
        clock = VirtualClock(t0)
        consumer = DetectionConsumer(source, clock)
        await consumer.run()
        return consumer.processed

    result = asyncio.run(_run())

    assert len(result) == 1, f"Expected 1 processed event, got {len(result)}"
    event, ts = result[0]
    assert event == _SINGLE_GRADUATION_EVENT, (
        f"Event payload mismatch: expected {_SINGLE_GRADUATION_EVENT}, got {event}"
    )
    assert ts == t0, f"Timestamp mismatch: expected {t0}, got {ts}"


# ---------------------------------------------------------------------------
# Static-analysis guards (criterion b)
# ---------------------------------------------------------------------------


def _detection_py_files() -> list[Path]:
    """Return all .py files under core/detection/ (if the package exists)."""
    if not DETECTION_ROOT.is_dir():
        return []
    return list(DETECTION_ROOT.rglob("*.py"))


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

        # Detect datetime.now(...)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datetime"
        ):
            found.append(
                f"{rel}:{node.lineno}: forbidden 'datetime.now()' call in detection module"
            )

        # Detect time.time(...)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "time"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            found.append(
                f"{rel}:{node.lineno}: forbidden 'time.time()' call in detection module"
            )

    return found


def test_no_concrete_source_import_in_detection_module() -> None:
    """Static-analysis guard: core/detection/ must never import concrete DataSource classes.

    Scans all Python files under core/detection/ and fails if any file imports
    LiveSource, ReplaySource, core.live_source, or core.replay_source.
    Detection code must depend only on the DataSource interface (Principle #7).
    """
    all_violations: list[str] = []
    for py_file in _detection_py_files():
        all_violations.extend(_concrete_source_violations(py_file))

    assert not all_violations, (
        "Detection modules must depend only on the DataSource interface (core.datasource), "
        "never on a concrete source class (Principle #7 / PRD §4).\n"
        "Violations found:\n" + "\n".join(f"  {v}" for v in all_violations)
    )


def test_no_direct_time_call_in_detection_module() -> None:
    """Static-analysis guard: core/detection/ must never call datetime.now() or time.time().

    Scans all Python files under core/detection/ using AST Call-node analysis.
    Detection code must read 'now' only via the injected Clock abstraction (AC-2.2).
    """
    all_violations: list[str] = []
    for py_file in _detection_py_files():
        all_violations.extend(_time_call_violations(py_file))

    assert not all_violations, (
        "Detection modules must read 'now' only from the injected Clock abstraction "
        "(AC-2.2 / PRD §4).  Direct datetime.now() or time.time() calls bypass "
        "the Clock seam and break replay parity.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in all_violations)
    )
