# ---
# module: core.tests.test_gap_reconciler_ac201
# sprint: sprint-5
# story: US-20 AC-20.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.gap_reconciler, core.tape.recorder, core.tape.swap_writer,
#               core.replay_source, core.clock, core.models, ast, asyncio, pytest, types
# ---
"""AC-20.1 — seek_by_time gap reconciliation merges missed swaps, idempotently.

Scenario:
  - A live WS stream covers 5 swaps but misses 2 (the gap: S2, S4 by index).
  - TapeRecorder + SwapWriter records the 3 received swaps (S1, S3, S5) to DB.
  - GapReconciler drives a seek_by_time DataSource (ReplaySource of ALL 5 swaps)
    and merges the gap — DB now holds exactly 5 distinct rows.
  - Re-running the reconciler with the same source yields NO new rows
    (idempotent on (mint, signature)).

Tests:
  1. test_live_stream_records_partial_set
       TapeRecorder over the live events (S1, S3, S5) writes exactly 3 rows.
  2. test_reconcile_fills_gap
       GapReconciler over all 5 swaps adds the 2 missing ones → 5 rows total.
  3. test_reconcile_no_duplicates_for_existing_swaps
       Re-running reconcile over the same window keeps the row count at 5.
  4. test_reconcile_idempotent_on_mint_signature
       Each (mint, signature) pair appears exactly once after reconcile.
  5. test_seek_source_behind_datasource_seam
       AST guard: gap_reconciler.py never imports a concrete source class.
"""
import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import Swap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRADUATED_BT = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BT)

# max_length=64 for Swap.mint; 9 + 44 = 53 chars
_MINT = "GAP_MINT_11111111111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"
_BASE_BT = 1_700_001_000  # 1 000 s after graduation

REPO_ROOT = Path(__file__).resolve().parents[2]
GAP_RECONCILER_PY = REPO_ROOT / "core" / "tape" / "gap_reconciler.py"

# ---------------------------------------------------------------------------
# Raw-event factory
# ---------------------------------------------------------------------------


def _raw_swap(n: int) -> dict:
    """Return a valid raw swap event dict for index *n* (1-indexed).

    All field widths respect Swap model max_length constraints:
      mint        ≤ 64 chars  (passed in as _MINT)
      signature   ≤ 128 chars (6+4+86 = 96 chars)
      owner       ≤ 64 chars  (6+4+54 = 64 chars)
    """
    sig = f"GAPSIG{n:04d}" + "X" * 86
    owner = f"OWNER_{n:04d}" + "W" * 54
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _BASE_BT + n,
        "slot": 1000 + n,
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


# S1..S5 — all 5 swaps in order
_ALL_SWAPS = [_raw_swap(i) for i in range(1, 6)]

# Live stream: S1, S3, S5 (S2 and S4 are the gap)
_LIVE_SWAPS = [_ALL_SWAPS[0], _ALL_SWAPS[2], _ALL_SWAPS[4]]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_recorder(events: list[dict], *, swap_writer=None):
    """Run TapeRecorder over *events* synchronously; return the recorder."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    recorder = TapeRecorder(
        ReplaySource(event_log=events),
        VirtualClock(t0),
        token_store={_MINT: _TOKEN},
        swap_writer=swap_writer,
    )
    asyncio.run(recorder.run())
    return recorder


def _run_reconciler(events: list[dict], *, swap_writer) -> list:
    """Run GapReconciler over *events* synchronously; return merged swaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.gap_reconciler import GapReconciler

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reconciler = GapReconciler(
        seek_source=ReplaySource(event_log=events),
        clock=VirtualClock(t0),
        token_store={_MINT: _TOKEN},
        swap_writer=swap_writer,
    )
    return asyncio.run(reconciler.run())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_live_stream_records_partial_set() -> None:
    """TapeRecorder over the live stream (S1, S3, S5) writes exactly 3 Swap rows."""
    from core.tape.swap_writer import SwapWriter

    count_before = Swap.objects.count()
    _run_recorder(_LIVE_SWAPS, swap_writer=SwapWriter())

    assert Swap.objects.count() == count_before + 3, (
        "Expected exactly 3 rows after recording the partial live stream (S1, S3, S5)."
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_fills_gap() -> None:
    """GapReconciler over all 5 swaps adds the 2 missing ones → 5 rows total.

    Step 1: live stream records 3 swaps (S1, S3, S5).
    Step 2: seek_by_time reconcile over all 5 swaps merges S2 and S4.
    Result: DB holds exactly 5 distinct rows.
    """
    from core.tape.swap_writer import SwapWriter

    writer = SwapWriter()
    count_before = Swap.objects.count()

    _run_recorder(_LIVE_SWAPS, swap_writer=writer)
    assert Swap.objects.count() == count_before + 3, "Live stream must produce 3 rows."

    merged = _run_reconciler(_ALL_SWAPS, swap_writer=writer)

    assert len(merged) == 5, f"Reconciler must return 5 NormalizedSwaps, got {len(merged)}"
    assert Swap.objects.count() == count_before + 5, (
        f"Expected 5 rows after reconcile, got {Swap.objects.count() - count_before}"
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_no_duplicates_for_existing_swaps() -> None:
    """Re-running reconcile over the same window produces NO new rows.

    Swaps already in the table (by (mint, signature)) are updated in-place,
    not duplicated — idempotent on (mint, signature) per AC-20.1.
    """
    from core.tape.swap_writer import SwapWriter

    writer = SwapWriter()
    count_before = Swap.objects.count()

    _run_recorder(_LIVE_SWAPS, swap_writer=writer)
    _run_reconciler(_ALL_SWAPS, swap_writer=writer)
    count_after_first = Swap.objects.count()

    # Second reconcile over the same window — must not grow the row count
    _run_reconciler(_ALL_SWAPS, swap_writer=writer)
    count_after_second = Swap.objects.count()

    assert count_after_first == count_before + 5, (
        f"Expected 5 rows after first reconcile, got {count_after_first - count_before}"
    )
    assert count_after_second == count_after_first, (
        f"Re-running reconcile created duplicate rows. "
        f"Count went from {count_after_first} to {count_after_second}."
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_idempotent_on_mint_signature() -> None:
    """Each (mint, signature) pair appears exactly once in the DB after reconcile.

    Verifies that the idempotency guarantee holds at the individual-row level:
    no (mint, signature) pair has more than one Swap row, even when the
    reconciler runs over a source that includes already-recorded swaps.
    """
    from core.tape.swap_writer import SwapWriter

    writer = SwapWriter()

    _run_recorder(_LIVE_SWAPS, swap_writer=writer)
    _run_reconciler(_ALL_SWAPS, swap_writer=writer)

    for swap_event in _ALL_SWAPS:
        sig = swap_event["signature"]
        row_count = Swap.objects.filter(mint=_MINT, signature=sig).count()
        assert row_count == 1, (
            f"Expected exactly 1 row for signature {sig!r}, got {row_count}."
        )


def test_seek_source_behind_datasource_seam() -> None:
    """Static-analysis guard: gap_reconciler.py must not import concrete source classes.

    GapReconciler depends only on the DataSource abstract interface (Principle #7).
    It must never import LiveSource, ReplaySource, core.live_source, or
    core.replay_source — those concrete classes belong in the listener wiring,
    not on the core recorder path.
    """
    _CONCRETE_CLASSES = {"LiveSource", "ReplaySource"}
    _CONCRETE_MODULES = {"core.live_source", "core.replay_source"}

    assert GAP_RECONCILER_PY.exists(), (
        f"gap_reconciler.py not found at {GAP_RECONCILER_PY}"
    )

    tree = ast.parse(GAP_RECONCILER_PY.read_text(encoding="utf-8"))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _CONCRETE_MODULES:
                violations.append(f"from {module} import ...")
                continue
            for alias in node.names:
                if alias.name in _CONCRETE_CLASSES:
                    violations.append(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _CONCRETE_MODULES:
                    violations.append(f"import {alias.name}")

    assert not violations, (
        "gap_reconciler.py must depend only on the DataSource interface "
        "(Principle #7 / PRD §4). Violations found:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
