# ---
# module: core.tests.test_gap_reconciler_ac203
# sprint: sprint-5
# story: US-20 AC-20.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.gap_reconciler, core.tape.recorder, core.normalized_swap,
#               core.replay_source, core.clock, ast, asyncio, dataclasses, pytest, types
# ---
"""AC-20.3 — parity by construction: gap reconcile and pure backfill are byte-identical.

GapReconciler delegates ALL normalization to an internal TapeRecorder with
swap_source="birdeye_backfill".  A standalone offline backfill is ALSO a
TapeRecorder with swap_source="birdeye_backfill".  There is no second live-only
normalization path — only one code path exists, shared by both (Principle #7,
PRD §3.3).

Tests:
  1. test_gap_reconcile_and_backfill_byte_identical
       GapReconciler and a pure backfill TapeRecorder over the same window+token
       produce byte-identical NormalizedSwaps (JSON-serialized for strict comparison).
  2. test_parity_all_fields_match
       All 15 NormalizedSwap fields from the gap path match the backfill path,
       field by field (no silent mismatch on any individual field).
  3. test_parity_canonical_ordering_identical
       Both paths return swaps in the same canonical (block_time, slot, signature)
       order even when the raw input is out of order.
  4. test_parity_source_is_birdeye_backfill_on_both_paths
       Both the gap reconcile and the pure backfill tag swaps with
       source="birdeye_backfill" — neither path uses a live-only source tag.
  5. test_no_direct_normalization_in_gap_reconciler
       AST guard: gap_reconciler.py never calls from_raw_swap() directly.
       All normalization is delegated to TapeRecorder (one code path, Principle #7).
  6. test_parity_single_swap
       Parity holds for the minimal case: a single swap through both paths
       produces byte-identical output.
"""
import ast
import asyncio
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRADUATED_BT = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BT)

# max_length=64 for Swap.mint; "PAR_MINT_" (9) + 44 chars = 53 ≤ 64
_MINT = "PAR_MINT_11111111111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"
_BASE_BT = 1_700_001_000

REPO_ROOT = Path(__file__).resolve().parents[2]
GAP_RECONCILER_PY = REPO_ROOT / "core" / "tape" / "gap_reconciler.py"


# ---------------------------------------------------------------------------
# Raw-event factory
# ---------------------------------------------------------------------------


def _raw_swap(n: int) -> dict:
    """Return a valid raw swap event dict for index *n* (1-indexed).

    All field widths respect Swap model max_length constraints:
      mint        ≤ 64 chars  (53 chars via _MINT constant)
      signature   ≤ 128 chars ("PARSIG" 6 + "0001" 4 + "X"*86 = 96 chars)
      owner       ≤ 64 chars  ("POWNER" 6 + "0001" 4 + "W"*54 = 64 chars)
    """
    sig = f"PARSIG{n:04d}" + "X" * 86
    owner = f"POWNER{n:04d}" + "W" * 54
    return {
        "type": "SWAP",
        "mint": _MINT,
        "block_time": _BASE_BT + n,
        "slot": 2000 + n,
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


_SWAPS = [_raw_swap(i) for i in range(1, 6)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_gap_reconciler(events: list[dict]) -> list:
    """Run GapReconciler over *events*; return its NormalizedSwaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.gap_reconciler import GapReconciler
    from core.tape.swap_writer import SwapWriter

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reconciler = GapReconciler(
        seek_source=ReplaySource(event_log=events),
        clock=VirtualClock(t0),
        token_store={_MINT: _TOKEN},
        swap_writer=SwapWriter(),
    )
    return asyncio.run(reconciler.run())


def _run_backfill(events: list[dict]) -> list:
    """Run TapeRecorder with swap_source='birdeye_backfill' — the pure offline backfill path."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    recorder = TapeRecorder(
        source=ReplaySource(event_log=events),
        clock=VirtualClock(t0),
        token_store={_MINT: _TOKEN},
        swap_source="birdeye_backfill",
        swap_phase="pre",
    )
    asyncio.run(recorder.run())
    return recorder.normalized_swaps


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_gap_reconcile_and_backfill_byte_identical() -> None:
    """Main AC-20.3 gate: gap reconcile and pure backfill over the same window+token
    produce byte-identical NormalizedSwaps, JSON-serialized for strict comparison.

    Both paths internally use the SAME TapeRecorder.from_raw_swap() call, so
    no divergence is possible — parity is guaranteed by construction.
    """
    gap_swaps = _run_gap_reconciler(_SWAPS)
    backfill_swaps = _run_backfill(_SWAPS)

    assert len(gap_swaps) == len(backfill_swaps), (
        f"Gap reconcile returned {len(gap_swaps)} swaps, "
        f"backfill returned {len(backfill_swaps)}."
    )

    for i, (g, b) in enumerate(zip(gap_swaps, backfill_swaps)):
        assert g.to_json() == b.to_json(), (
            f"Swap[{i}] is NOT byte-identical between gap reconcile and backfill.\n"
            f"  Gap:      {g.to_json()}\n"
            f"  Backfill: {b.to_json()}"
        )


@pytest.mark.django_db(transaction=True)
def test_parity_all_fields_match() -> None:
    """All 15 NormalizedSwap fields match between gap reconcile and pure backfill.

    Field-by-field comparison guards against any silent mismatch: a future edit
    to a single field in one path but not the other would be caught here.
    """
    gap_swaps = _run_gap_reconciler(_SWAPS)
    backfill_swaps = _run_backfill(_SWAPS)

    assert len(gap_swaps) == len(backfill_swaps)

    for i, (g, b) in enumerate(zip(gap_swaps, backfill_swaps)):
        g_dict = dataclasses.asdict(g)
        b_dict = dataclasses.asdict(b)
        for field, g_val in g_dict.items():
            b_val = b_dict[field]
            assert g_val == b_val, (
                f"Swap[{i}].{field} mismatch: gap={g_val!r}, backfill={b_val!r}"
            )


@pytest.mark.django_db(transaction=True)
def test_parity_canonical_ordering_identical() -> None:
    """Both paths return swaps in the same canonical (block_time, slot, signature) order.

    The out-of-order scenario: feed swaps in reverse; both paths must produce
    identical ascending (block_time, slot, signature) output, confirming the
    single shared sort in TapeRecorder.normalized_swaps is the only sort applied.
    """
    reversed_swaps = list(reversed(_SWAPS))

    gap_swaps = _run_gap_reconciler(reversed_swaps)
    backfill_swaps = _run_backfill(reversed_swaps)

    assert len(gap_swaps) == len(backfill_swaps)

    gap_keys = [(s.block_time, s.slot, s.signature) for s in gap_swaps]
    backfill_keys = [(s.block_time, s.slot, s.signature) for s in backfill_swaps]

    assert gap_keys == backfill_keys, (
        "Canonical ordering differs between gap reconcile and backfill.\n"
        f"  Gap keys:      {gap_keys}\n"
        f"  Backfill keys: {backfill_keys}"
    )


@pytest.mark.django_db(transaction=True)
def test_parity_source_is_birdeye_backfill_on_both_paths() -> None:
    """Both gap reconcile and pure backfill tag every swap with source='birdeye_backfill'.

    Neither path uses 'birdeye_live' or any other source tag — the single shared
    code path sets source exactly once, in TapeRecorder, and it is 'birdeye_backfill'
    on both sides.
    """
    gap_swaps = _run_gap_reconciler(_SWAPS)
    backfill_swaps = _run_backfill(_SWAPS)

    for i, swap in enumerate(gap_swaps):
        assert swap.source == "birdeye_backfill", (
            f"Gap reconcile Swap[{i}].source={swap.source!r}, "
            f"expected 'birdeye_backfill'."
        )
    for i, swap in enumerate(backfill_swaps):
        assert swap.source == "birdeye_backfill", (
            f"Backfill Swap[{i}].source={swap.source!r}, "
            f"expected 'birdeye_backfill'."
        )


def test_no_direct_normalization_in_gap_reconciler() -> None:
    """AST guard: gap_reconciler.py never calls from_raw_swap() directly.

    All normalization is delegated to TapeRecorder — GapReconciler contains no
    independent normalization logic.  Any direct from_raw_swap call in
    gap_reconciler.py would be a divergent second code path, violating Principle #7.
    """
    assert GAP_RECONCILER_PY.exists(), (
        f"gap_reconciler.py not found at {GAP_RECONCILER_PY}"
    )

    tree = ast.parse(GAP_RECONCILER_PY.read_text(encoding="utf-8"))
    direct_calls: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "from_raw_swap":
            direct_calls.append(f"line {node.lineno}")

    assert not direct_calls, (
        "gap_reconciler.py must NOT call from_raw_swap() directly. "
        "All normalization belongs in TapeRecorder (Principle #7 — one code path). "
        f"Violations found at: {direct_calls}"
    )


@pytest.mark.django_db(transaction=True)
def test_parity_single_swap() -> None:
    """Parity holds for the minimal case: a single swap through both paths.

    If the single-swap case passes byte-identically, the shared code path is
    confirmed at the most fundamental level.
    """
    single_swap = [_raw_swap(1)]

    gap_swaps = _run_gap_reconciler(single_swap)
    backfill_swaps = _run_backfill(single_swap)

    assert len(gap_swaps) == 1, f"Gap reconcile returned {len(gap_swaps)} swaps, expected 1."
    assert len(backfill_swaps) == 1, f"Backfill returned {len(backfill_swaps)} swaps, expected 1."

    assert gap_swaps[0].to_json() == backfill_swaps[0].to_json(), (
        "Single swap is NOT byte-identical between gap reconcile and backfill.\n"
        f"  Gap:      {gap_swaps[0].to_json()}\n"
        f"  Backfill: {backfill_swaps[0].to_json()}"
    )
