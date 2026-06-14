# ---
# module: core.tests.test_clock
# sprint: sprint-2
# story: US-2 AC-2.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.clock, core.replay_source, datetime, pathlib, ast, asyncio
# ---
"""AC-2.2 — Injectable Clock abstraction tests.

Tests:
  1. test_wall_clock_returns_utc_datetime
       WallClock().now() returns a UTC-aware datetime.
  2. test_virtual_clock_returns_initial_time
       VirtualClock(t).now() returns t unchanged.
  3. test_virtual_clock_advance
       After advance(timedelta(seconds=5)) the virtual clock is t + 5s.
  4. test_virtual_clock_set_time
       set_time(t2) makes now() return t2.
  5. test_stamp_events_uses_injected_clock
       Drives stamp_events() with a ReplaySource (3 events) and a
       VirtualClock, advancing the clock by 10 s between events.
       Each yielded timestamp must equal the virtual clock's value at
       the moment of that yield — proving core logic reads 'now' from
       the injected clock, never from system time.
  6. test_no_direct_datetime_now_in_core
       Static-analysis guard: no .py file under core/ (outside tests/)
       may call datetime.now( or time.time( except inside WallClock.now
       in core/clock.py.  Any other occurrence is a violation.
"""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "core"
TESTS_DIR = CORE_ROOT / "tests"

# ---------------------------------------------------------------------------
# 1. WallClock
# ---------------------------------------------------------------------------


def test_wall_clock_returns_utc_datetime() -> None:
    """WallClock().now() must return a datetime with UTC tzinfo."""
    from core.clock import WallClock

    result = WallClock().now()
    assert isinstance(result, datetime), "now() must return a datetime"
    assert result.tzinfo is not None, "now() must be timezone-aware"
    assert result.tzinfo == timezone.utc or result.utcoffset() == timedelta(0), (
        "now() tzinfo must represent UTC"
    )


# ---------------------------------------------------------------------------
# 2. VirtualClock — initial value
# ---------------------------------------------------------------------------


def test_virtual_clock_returns_initial_time() -> None:
    """VirtualClock(t).now() returns t unchanged."""
    from core.clock import VirtualClock

    t = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vc = VirtualClock(t)
    assert vc.now() == t


# ---------------------------------------------------------------------------
# 3. VirtualClock — advance
# ---------------------------------------------------------------------------


def test_virtual_clock_advance() -> None:
    """After advance(timedelta(seconds=5)), now() returns initial + 5 s."""
    from core.clock import VirtualClock

    t = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    vc = VirtualClock(t)
    vc.advance(timedelta(seconds=5))
    assert vc.now() == t + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# 4. VirtualClock — set_time
# ---------------------------------------------------------------------------


def test_virtual_clock_set_time() -> None:
    """set_time(t2) makes now() return t2."""
    from core.clock import VirtualClock

    t1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 15, 9, 30, 0, tzinfo=timezone.utc)
    vc = VirtualClock(t1)
    vc.set_time(t2)
    assert vc.now() == t2


# ---------------------------------------------------------------------------
# 5. stamp_events uses injected clock (KEY TEST)
# ---------------------------------------------------------------------------


def test_stamp_events_uses_injected_clock() -> None:
    """stamp_events() timestamps each event with the injected VirtualClock.

    Three events are replayed.  The VirtualClock starts at t0 and is
    advanced by 10 seconds AFTER collecting each event.  We record the
    expected timestamp at each step and assert the yielded timestamps
    match exactly — proving the core never reaches for system time.
    """
    from core.clock import VirtualClock, stamp_events
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    delta = timedelta(seconds=10)

    raw_events: list[dict[str, Any]] = [
        {"seq": 0, "mint": "AAA"},
        {"seq": 1, "mint": "BBB"},
        {"seq": 2, "mint": "CCC"},
    ]

    async def _run() -> list[tuple[dict[str, Any], datetime]]:
        source = ReplaySource(event_log=raw_events)
        vc = VirtualClock(t0)

        results: list[tuple[dict[str, Any], datetime]] = []
        async for event, ts in stamp_events(source, vc):
            results.append((event, ts))
            vc.advance(delta)          # advance AFTER stamping, like real replay
        return results

    results = asyncio.run(_run())

    assert len(results) == 3, "Expected exactly 3 stamped events"

    expected_times = [
        t0,
        t0 + delta,
        t0 + 2 * delta,
    ]
    for i, (event, ts) in enumerate(results):
        assert event == raw_events[i], f"Event {i} payload mismatch"
        assert ts == expected_times[i], (
            f"Event {i}: expected timestamp {expected_times[i]}, got {ts}"
        )


# ---------------------------------------------------------------------------
# 6. Static-analysis guard — no direct datetime.now() / time.time() on core path
# ---------------------------------------------------------------------------

# The single allowed location for datetime.now() usage.
# WallClock.now in core/clock.py is the sole place that may call datetime.now().
_ALLOWED_FILE = CORE_ROOT / "clock.py"
_ALLOWED_FUNCTION = "now"  # method name inside WallClock


def _core_source_files() -> list[Path]:
    """All .py files under core/ that are NOT under core/tests/."""
    files: list[Path] = []
    for py_file in CORE_ROOT.rglob("*.py"):
        # Exclude anything inside the tests directory.
        try:
            py_file.relative_to(TESTS_DIR)
            continue  # inside tests/ — skip
        except ValueError:
            pass
        files.append(py_file)
    return files


def _enclosing_function(node: ast.AST, tree: ast.Module) -> str | None:
    """Return the name of the innermost function/method containing *node*."""
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(candidate, "end_lineno", None)
            if end is not None and candidate.lineno <= node.lineno <= end:
                return candidate.name
    return None


def _forbidden_calls_in_file(py_file: Path) -> list[str]:
    """Return descriptions of forbidden time-access calls in *py_file*.

    Uses the AST to detect actual Call nodes — ignores strings in docstrings,
    comments, and import lines that happen to contain the forbidden text.

    Forbidden calls:
        - datetime.now(...)       — anywhere except WallClock.now in clock.py
        - time.time(...)          — everywhere in core source
    """
    source_text = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    rel = py_file.relative_to(REPO_ROOT)
    is_allowed_file = py_file.resolve() == _ALLOWED_FILE.resolve()
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Detect: datetime.now(...)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datetime"
        ):
            fn_name = _enclosing_function(node, tree)
            # Allow only inside WallClock.now in clock.py
            if is_allowed_file and fn_name == _ALLOWED_FUNCTION:
                continue
            found.append(
                f"{rel}:{node.lineno}: forbidden 'datetime.now()' call "
                f"(in function '{fn_name}')"
            )

        # Detect: time.time(...)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "time"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            fn_name = _enclosing_function(node, tree)
            found.append(
                f"{rel}:{node.lineno}: forbidden 'time.time()' call "
                f"(in function '{fn_name}')"
            )

    return found


def test_no_direct_datetime_now_in_core() -> None:
    """Static analysis: datetime.now() and time.time() must not appear on the core path.

    Uses AST Call-node analysis (not line scanning) so docstrings, comments,
    and import text are ignored — only actual call expressions are checked.

    The ONLY allowed occurrence of datetime.now() is inside the 'now' method
    of WallClock in core/clock.py.  Any other occurrence is a violation that
    proves some core module is bypassing the Clock abstraction.
    """
    violations: list[str] = []

    for py_file in _core_source_files():
        violations.extend(_forbidden_calls_in_file(py_file))

    assert not violations, (
        "Core modules must read 'now' only from the injected Clock abstraction "
        "(AC-2.2 / PRD §4).  Direct datetime.now() or time.time() calls on the "
        "core path bypass the Clock seam and break replay parity.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
