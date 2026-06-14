# ---
# module: core.tests.test_datasource_interface
# sprint: sprint-2
# story: US-2 AC-2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.datasource, core.live_source, core.replay_source
# ---
"""AC-2.1 — DataSource interface contract + static-analysis guard.

Tests:
  1. DataSource is abstract and cannot be instantiated.
  2. LiveSource and ReplaySource are concrete implementations.
  3. ReplaySource yields events in the order they were supplied.
  4. Static analysis: no consumer module (detection / feature-assembly /
     scoring / exit / settlement) directly imports a concrete source class
     (LiveSource or ReplaySource) — enforcing Principle #7 / PRD §4.
"""
import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

# ----- paths ----------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Consumer sub-packages that must never import concrete DataSource classes.
CONSUMER_PACKAGE_NAMES = [
    "detection",
    "features",
    "scoring",
    "exit",
    "settlement",
]

# Concrete class names that consumers are forbidden from importing.
CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}

# Concrete module paths that consumers are forbidden from importing.
CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}


# ----- helpers --------------------------------------------------------------

def _violations_in_file(py_file: Path) -> list[str]:
    """Return import-violation descriptions found in *py_file*."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # `from core.live_source import ...` or `from core.replay_source import ...`
            if module in CONCRETE_MODULE_NAMES:
                found.append(f"{rel}: 'from {module} import ...'")
                continue
            # `from anywhere import LiveSource` / `from anywhere import ReplaySource`
            for alias in node.names:
                if alias.name in CONCRETE_CLASS_NAMES:
                    found.append(f"{rel}: 'from {module} import {alias.name}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CONCRETE_MODULE_NAMES:
                    found.append(f"{rel}: 'import {alias.name}'")

    return found


def _consumer_py_files() -> list[Path]:
    """Return all .py files under consumer sub-packages that currently exist."""
    files: list[Path] = []
    core_root = REPO_ROOT / "core"
    for pkg in CONSUMER_PACKAGE_NAMES:
        pkg_path = core_root / pkg
        if pkg_path.is_dir():
            files.extend(pkg_path.rglob("*.py"))
    return files


# ----- interface contract tests ---------------------------------------------

def test_datasource_is_abstract():
    """DataSource cannot be instantiated — it requires all abstract methods."""
    from core.datasource import DataSource

    with pytest.raises(TypeError):
        DataSource()  # type: ignore[abstract]


def test_live_source_is_subclass_of_datasource():
    from core.datasource import DataSource
    from core.live_source import LiveSource

    assert issubclass(LiveSource, DataSource)


def test_replay_source_is_subclass_of_datasource():
    from core.datasource import DataSource
    from core.replay_source import ReplaySource

    assert issubclass(ReplaySource, DataSource)


def test_replay_source_yields_events_in_order():
    """ReplaySource yields every event from the supplied log, in order."""
    from core.replay_source import ReplaySource

    async def _run() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [
            {"type": "swap", "mint": "AAA", "price": 1.0},
            {"type": "swap", "mint": "BBB", "price": 2.0},
            {"type": "swap", "mint": "CCC", "price": 3.0},
        ]
        source = ReplaySource(event_log=events)
        await source.connect()
        collected = [e async for e in source.events()]
        await source.disconnect()
        return collected, events  # type: ignore[return-value]

    collected, events = asyncio.run(_run())
    assert collected == events


def test_replay_source_empty_log_yields_nothing():
    """ReplaySource with an empty log completes immediately."""
    from core.replay_source import ReplaySource

    async def _run() -> list[dict[str, Any]]:
        source = ReplaySource(event_log=[])
        await source.connect()
        collected = [e async for e in source.events()]
        await source.disconnect()
        return collected

    assert asyncio.run(_run()) == []


def test_live_source_instantiates_and_connects():
    """LiveSource (stub) can be instantiated, connected, and disconnected without error."""
    from core.live_source import LiveSource

    async def _run() -> None:
        source = LiveSource()
        await source.connect()
        await source.disconnect()

    asyncio.run(_run())


# ----- static-analysis guard ------------------------------------------------

def test_no_consumer_imports_concrete_source_class():
    """Static-analysis guard: consumer modules must never import concrete DataSource classes.

    Scans all Python files under core/<consumer-package>/ directories and fails
    if any file imports LiveSource, ReplaySource, core.live_source, or core.replay_source.
    Passes trivially while the consumer packages do not yet exist; becomes a
    regression guard as they are added.
    """
    consumer_files = _consumer_py_files()
    all_violations: list[str] = []

    for py_file in consumer_files:
        all_violations.extend(_violations_in_file(py_file))

    assert not all_violations, (
        "Consumer modules must depend only on the DataSource interface (core.datasource), "
        "never on a concrete source class (Principle #7 / PRD §4).\n"
        "Violations found:\n" + "\n".join(f"  {v}" for v in all_violations)
    )
