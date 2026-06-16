# ---
# module: core.tests.test_snapshot_fetcher_ac241
# sprint: sprint-6
# story: US-24 AC-24.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_fetcher, core.snapshot_source, core.clock, ast, pathlib
# ---
"""AC-24.1 — SnapshotFetcher: DataSource seam + injected clock + at-most-one guard.

Two verification prongs per the AC:

(a) Functional tests driving the fetcher from an InMemorySnapshotSource
    (a ReplaySource-style in-memory test double):
      1. fetch() returns the raw payload from the DataSource
      2. The as_of time passed to get_snapshot() equals clock.now()
      3. A second call for the same mint returns None (at-most-one)
      4. The DataSource is NOT called a second time for the same mint
      5. Different mints are each fetched independently
      6. already_fetched() returns False before / True after a fetch

(b) Static-analysis guard (US-2-style):
      7. snapshot_fetcher.py imports no concrete DataSource class
         (LiveSource, ReplaySource, core.live_source, core.replay_source)
      8. snapshot_fetcher.py contains no datetime.now(), datetime.utcnow(),
         or time.time() calls — all time reads come from the injected Clock
"""
import ast
from datetime import datetime, timezone
from pathlib import Path

from core.clock import VirtualClock
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCHER_MODULE = REPO_ROOT / "core" / "snapshot_fetcher.py"

# ---------------------------------------------------------------------------
# In-memory test double
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.45, "count": 1000},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 5000.0,
    "tvl": 4800.0,
    "depth": {"bid": 100.0, "ask": 100.0},
}

TEST_MINT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class InMemorySnapshotSource(SnapshotDataSource):
    """Test double: returns pre-loaded snapshot dicts without network calls."""

    def __init__(self, payloads: dict[str, dict]) -> None:
        self._payloads = payloads
        self.call_count: int = 0
        self.call_args: list[tuple[str, datetime]] = []

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self.call_count += 1
        self.call_args.append((mint, as_of))
        return self._payloads[mint]


# ---------------------------------------------------------------------------
# (a) Functional tests
# ---------------------------------------------------------------------------


def test_fetch_returns_payload_from_source():
    """fetch() returns the raw payload dict from the DataSource."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    result = fetcher.fetch(TEST_MINT)
    assert result == SAMPLE_PAYLOAD


def test_fetch_passes_clock_now_as_as_of():
    """The as_of time passed to get_snapshot() equals clock.now() at fetch time."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    fetcher.fetch(TEST_MINT)

    assert len(source.call_args) == 1
    _, as_of = source.call_args[0]
    assert as_of == T0


def test_fetch_at_most_once_second_call_returns_none():
    """A second fetch() for the same mint returns None (at-most-one discipline)."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    first = fetcher.fetch(TEST_MINT)
    second = fetcher.fetch(TEST_MINT)

    assert first == SAMPLE_PAYLOAD
    assert second is None


def test_fetch_does_not_call_source_on_second_request():
    """The DataSource is NOT called a second time for an already-fetched mint."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    fetcher.fetch(TEST_MINT)
    fetcher.fetch(TEST_MINT)

    assert source.call_count == 1, (
        f"DataSource.get_snapshot() was called {source.call_count} times; "
        "expected exactly 1 (at-most-one discipline — §6.3)"
    )


def test_fetch_different_mints_are_independent():
    """Different mints are each fetched independently; at-most-one is per-mint."""
    from core.snapshot_fetcher import SnapshotFetcher

    mint_a = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
    mint_b = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"
    payload_a = {**SAMPLE_PAYLOAD, "liquidity": 1000.0}
    payload_b = {**SAMPLE_PAYLOAD, "liquidity": 2000.0}

    source = InMemorySnapshotSource({mint_a: payload_a, mint_b: payload_b})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    result_a = fetcher.fetch(mint_a)
    result_b = fetcher.fetch(mint_b)

    assert result_a == payload_a
    assert result_b == payload_b
    assert source.call_count == 2


def test_already_fetched_returns_false_before_fetch():
    """already_fetched() returns False for a mint that has not been fetched."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    assert fetcher.already_fetched(TEST_MINT) is False


def test_already_fetched_returns_true_after_fetch():
    """already_fetched() returns True after a successful fetch."""
    from core.snapshot_fetcher import SnapshotFetcher

    source = InMemorySnapshotSource({TEST_MINT: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    fetcher.fetch(TEST_MINT)
    assert fetcher.already_fetched(TEST_MINT) is True


# ---------------------------------------------------------------------------
# (b) Static-analysis guards
# ---------------------------------------------------------------------------

_CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}
_CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}


def _parse_fetcher() -> ast.Module:
    return ast.parse(FETCHER_MODULE.read_text(encoding="utf-8"))


def _concrete_source_violations(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _CONCRETE_MODULE_NAMES:
                found.append(f"line {node.lineno}: 'from {module} import ...'")
                continue
            for alias in node.names:
                if alias.name in _CONCRETE_CLASS_NAMES:
                    found.append(
                        f"line {node.lineno}: 'from {module} import {alias.name}'"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _CONCRETE_MODULE_NAMES:
                    found.append(f"line {node.lineno}: 'import {alias.name}'")
    return found


def _time_call_violations(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            obj, attr = func.value.id, func.attr
            if obj == "datetime" and attr in ("now", "utcnow"):
                found.append(f"line {node.lineno}: forbidden 'datetime.{attr}()' call")
            if obj == "time" and attr == "time":
                found.append(f"line {node.lineno}: forbidden 'time.time()' call")
    return found


def test_static_analysis_no_concrete_source_import():
    """Static-analysis guard: snapshot_fetcher.py must not import concrete DataSource classes.

    The fetcher must depend only on the abstract SnapshotDataSource interface —
    never on LiveSource, ReplaySource, core.live_source, or core.replay_source.
    This enforces the live/replay seam (US-2 / Principle #7).
    """
    assert FETCHER_MODULE.exists(), f"snapshot_fetcher.py not found at {FETCHER_MODULE}"
    violations = _concrete_source_violations(_parse_fetcher())
    assert not violations, (
        "core/snapshot_fetcher.py must not import concrete DataSource classes. "
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_static_analysis_no_direct_time_calls():
    """Static-analysis guard: snapshot_fetcher.py must not call datetime.now(),
    datetime.utcnow(), or time.time() directly.

    All time reads must come from the injected Clock (Principle #7 / AC-2.2).
    Any such call means the fetcher is bypassing the clock abstraction.
    """
    assert FETCHER_MODULE.exists(), f"snapshot_fetcher.py not found at {FETCHER_MODULE}"
    violations = _time_call_violations(_parse_fetcher())
    assert not violations, (
        "core/snapshot_fetcher.py must not call datetime.now(), datetime.utcnow(), "
        "or time.time() — time must come from the injected Clock (Principle #7).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
