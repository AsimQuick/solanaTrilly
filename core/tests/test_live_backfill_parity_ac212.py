# ---
# module: core.tests.test_live_backfill_parity_ac212
# sprint: sprint-5
# story: US-21 AC-21.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.recorder, core.replay_source, core.clock,
#               core.normalized_swap, core.encoders,
#               asyncio, dataclasses, json, types
# ---
"""AC-21.2 — live↔backfill byte-parity on golden token(s) (§16, P3 slice of G2(a)).

The live swap stream and the seek_by_time backfill for the same golden token(s)
produce byte-identical NormalizedSwaps: rel, price, side, vol_*, owner, and
canonical ordering all match (PRD §16 / AC-21.2).

Paths under test:
  Live path     → TapeRecorder(source, swap_source="birdeye_live")
  Backfill path → TapeRecorder(source, swap_source="birdeye_backfill")

Both paths receive the SAME raw events from the same golden fixture.  The
source provenance tag ("birdeye_live" vs "birdeye_backfill") is the ONE
intentional difference — all data fields must be byte-identical.

The golden fixture is a synthetic but schema-faithful Birdeye SUBSCRIBE_TXS
stream (PRD §3.3) for one token.  US-22 may bank a real durable capture here;
for P3, the synthetic fixture is the golden token standard (AC-21.2 / PRD §16).

Tests:
  1. test_golden_fixture_schema_faithful
       All golden fixture events carry every §3.3 Birdeye field with correct types.

  2. test_live_backfill_same_swap_count
       Both paths produce the same count of NormalizedSwaps from the golden fixture.

  3. test_live_backfill_data_fields_byte_identical
       Main gate: rel, price, side, vol_sol, vol_usd, sol_usd, owner are
       byte-identical between paths (JSON payload comparison).

  4. test_live_backfill_canonical_ordering_identical
       Both paths return swaps in the same canonical (block_time, slot, signature) order.

  5. test_live_backfill_all_non_source_fields_identical
       Every NormalizedSwap field EXCEPT 'source' is byte-identical — comprehensive
       coverage beyond the minimum AC-21.2 field list.

  6. test_live_backfill_source_tag_differs_as_expected
       Live path: source='birdeye_live'; backfill path: source='birdeye_backfill'.

  7. test_live_backfill_failed_swap_excluded_from_both
       The failed swap in the golden fixture is absent from output on BOTH paths.

  8. test_live_backfill_parity_single_golden_swap
       Parity holds for the minimal case: one golden swap through both paths.
"""
import asyncio
import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Golden token fixture — schema-faithful Birdeye SUBSCRIBE_TXS (PRD §3.3)
# ---------------------------------------------------------------------------

_GOLDEN_GRADUATED_BT = 1_750_001_000
_GOLDEN_TOKEN = SimpleNamespace(graduated_block_time=_GOLDEN_GRADUATED_BT)

# mint ≤ 64 chars: "GOLD_TOKEN_" (11) + "1" * 43 = 54 chars
_GOLDEN_MINT = "GOLD_TOKEN_11111111111111111111111111111111111111111"
_WSOL = "So11111111111111111111111111111111111111112"

_T0 = datetime(2025, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

REQUIRED_BIRDEYE_FIELDS = frozenset({
    "type",
    "mint",
    "block_time",
    "slot",
    "signature",
    "side",
    "price",
    "vol_sol",
    "vol_usd",
    "sol_usd",
    "owner",
    "base_reserve",
    "quote_reserve",
    "quote_mint",
    "failed",
})


def _make_golden_event(n: int, *, side: str = "buy", failed: bool = False) -> dict[str, Any]:
    """Build a schema-faithful Birdeye SUBSCRIBE_TXS event dict (PRD §3.3).

    Field widths respect Swap model max_length constraints:
      signature ≤ 128: "GOLDSIG" (7) + f"{n:04d}" (4) + "A" * 85 = 96 chars
      owner     ≤ 64:  "GOLDOWR" (7) + f"{n:04d}" (4) + "B" * 53 = 64 chars
    """
    bt = _GOLDEN_GRADUATED_BT + 500 + n * 10
    sig = f"GOLDSIG{n:04d}" + "A" * 85
    owner = f"GOLDOWR{n:04d}" + "B" * 53
    return {
        "type": "SWAP",
        "mint": _GOLDEN_MINT,
        "block_time": bt,
        "slot": 40_000_000 + n,
        "signature": sig,
        "side": side,
        "price": 0.00005 + n * 0.000005,
        "vol_sol": 2.0 + n * 0.25,
        "vol_usd": 300.0 + n * 37.5,
        "sol_usd": 150.0,
        "owner": owner,
        "base_reserve": 8_000_000 - n * 80_000,
        "quote_reserve": 4_000_000 + n * 40_000,
        "quote_mint": _WSOL,
        "failed": failed,
    }


# 7 events: 5 buy + 1 sell + 1 failed (must be excluded from both paths)
_GOLDEN_FIXTURE: list[dict[str, Any]] = [
    _make_golden_event(1, side="buy"),
    _make_golden_event(2, side="buy"),
    _make_golden_event(3, side="sell"),
    _make_golden_event(4, side="buy"),
    _make_golden_event(5, side="buy"),
    _make_golden_event(6, side="buy"),
    _make_golden_event(7, side="buy", failed=True),
]

_GOLDEN_TOKEN_STORE: dict[str, Any] = {_GOLDEN_MINT: _GOLDEN_TOKEN}
# 6 landed swaps (7 total minus 1 failed)
_EXPECTED_LANDED_COUNT = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_live_path(events: list[dict[str, Any]]) -> list:
    """Run TapeRecorder with swap_source='birdeye_live' — the live stream path."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        recorder = TapeRecorder(
            source=ReplaySource(event_log=events),
            clock=VirtualClock(_T0),
            token_store=_GOLDEN_TOKEN_STORE,
            swap_source="birdeye_live",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _run_backfill_path(events: list[dict[str, Any]]) -> list:
    """Run TapeRecorder with swap_source='birdeye_backfill' — the seek_by_time path."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        recorder = TapeRecorder(
            source=ReplaySource(event_log=events),
            clock=VirtualClock(_T0),
            token_store=_GOLDEN_TOKEN_STORE,
            swap_source="birdeye_backfill",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _ac212_fields_as_json(swap) -> str:
    """Serialize the AC-21.2 listed data fields to JSON for byte comparison.

    Fields per AC-21.2: rel, price, side, vol_sol, vol_usd, sol_usd, owner.
    """
    from core.encoders import JsonSafeEncoder

    return json.dumps(
        {
            "rel": swap.rel,
            "price": swap.price,
            "side": swap.side,
            "vol_sol": swap.vol_sol,
            "vol_usd": swap.vol_usd,
            "sol_usd": swap.sol_usd,
            "owner": swap.owner,
        },
        cls=JsonSafeEncoder,
    )


def _all_non_source_fields_as_json(swap) -> str:
    """Serialize ALL NormalizedSwap fields EXCEPT 'source' to JSON.

    Used for the comprehensive gate: every data field (including block_time,
    slot, signature, base_reserve, quote_reserve, quote_mint, phase) must be
    byte-identical between live and backfill paths.
    """
    from core.encoders import JsonSafeEncoder

    d = dataclasses.asdict(swap)
    d.pop("source")
    return json.dumps(d, cls=JsonSafeEncoder, sort_keys=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_golden_fixture_schema_faithful() -> None:
    """All golden fixture events carry every §3.3 Birdeye SUBSCRIBE_TXS field."""
    for i, event in enumerate(_GOLDEN_FIXTURE):
        missing = REQUIRED_BIRDEYE_FIELDS - set(event.keys())
        assert not missing, (
            f"Golden event[{i}] missing §3.3 required fields: {missing}. "
            "The golden fixture must be schema-faithful (PRD §3.3 / AC-21.2)."
        )
        assert event["type"] == "SWAP", (
            f"Event[{i}] type={event['type']!r}; §3.3 SUBSCRIBE_TXS events must be type='SWAP'."
        )
        assert isinstance(event["block_time"], int), (
            f"Event[{i}] block_time must be int, got {type(event['block_time']).__name__}."
        )
        assert isinstance(event["slot"], int), (
            f"Event[{i}] slot must be int, got {type(event['slot']).__name__}."
        )
        if not event.get("failed", False):
            assert event["side"] in {"buy", "sell"}, (
                f"Event[{i}] side={event['side']!r} not in {{buy, sell}}."
            )
            assert float(event["price"]) > 0, (
                f"Event[{i}] price must be > 0 for landed swaps."
            )


def test_live_backfill_same_swap_count() -> None:
    """Both paths produce the same number of NormalizedSwaps from the golden fixture."""
    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    assert len(live_swaps) == _EXPECTED_LANDED_COUNT, (
        f"Live path: expected {_EXPECTED_LANDED_COUNT} NormalizedSwaps, "
        f"got {len(live_swaps)}."
    )
    assert len(backfill_swaps) == _EXPECTED_LANDED_COUNT, (
        f"Backfill path: expected {_EXPECTED_LANDED_COUNT} NormalizedSwaps, "
        f"got {len(backfill_swaps)}."
    )
    assert len(live_swaps) == len(backfill_swaps), (
        f"Live ({len(live_swaps)}) and backfill ({len(backfill_swaps)}) "
        "paths must process the same number of landed swaps."
    )


def test_live_backfill_data_fields_byte_identical() -> None:
    """Main gate: rel, price, side, vol_sol, vol_usd, sol_usd, owner byte-identical.

    These are the data fields explicitly named in AC-21.2.  The JSON-serialized
    payload of these fields must be character-for-character identical between
    the live and backfill paths, confirming the normalization code path is shared.
    """
    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    assert len(live_swaps) == len(backfill_swaps)

    for i, (live, backfill) in enumerate(zip(live_swaps, backfill_swaps)):
        live_json = _ac212_fields_as_json(live)
        backfill_json = _ac212_fields_as_json(backfill)
        assert live_json == backfill_json, (
            f"Swap[{i}] data fields NOT byte-identical between live and backfill:\n"
            f"  Live:     {live_json}\n"
            f"  Backfill: {backfill_json}\n"
            "rel, price, side, vol_sol, vol_usd, sol_usd, owner must match (AC-21.2)."
        )


def test_live_backfill_canonical_ordering_identical() -> None:
    """Both paths return swaps in the same canonical (block_time, slot, signature) order.

    Canonical ordering must be identical between live and backfill paths.  The
    shared sort in TapeRecorder.normalized_swaps is the only sort applied on
    both paths, guaranteeing identical sequencing from identical raw events.
    """
    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    live_keys = [(s.block_time, s.slot, s.signature) for s in live_swaps]
    backfill_keys = [(s.block_time, s.slot, s.signature) for s in backfill_swaps]

    assert live_keys == backfill_keys, (
        "Canonical ordering differs between live and backfill paths:\n"
        f"  Live keys:     {live_keys}\n"
        f"  Backfill keys: {backfill_keys}\n"
        "Canonical (block_time, slot, signature) order must be identical (AC-21.2)."
    )
    # Verify both paths produce ascending canonical order (not just equal to each other)
    assert live_keys == sorted(live_keys), (
        f"Live path keys are not in ascending canonical order: {live_keys}"
    )


def test_live_backfill_all_non_source_fields_identical() -> None:
    """Every NormalizedSwap field EXCEPT 'source' is byte-identical between paths.

    Comprehensive gate: rel, price, side, vol_sol, vol_usd, sol_usd, owner
    (listed in AC-21.2) PLUS block_time, slot, signature, base_reserve,
    quote_reserve, quote_mint, phase — ALL must match.  Only the 'source'
    provenance tag is expected to differ.
    """
    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    assert len(live_swaps) == len(backfill_swaps)

    for i, (live, backfill) in enumerate(zip(live_swaps, backfill_swaps)):
        live_json = _all_non_source_fields_as_json(live)
        backfill_json = _all_non_source_fields_as_json(backfill)
        assert live_json == backfill_json, (
            f"Swap[{i}] non-source fields NOT byte-identical:\n"
            f"  Live:     {live_json}\n"
            f"  Backfill: {backfill_json}\n"
            "All data fields (excluding source provenance tag) must match (AC-21.2)."
        )


def test_live_backfill_source_tag_differs_as_expected() -> None:
    """Live path: source='birdeye_live'; backfill path: source='birdeye_backfill'.

    The 'source' field is the ONE intentional difference between the two paths:
    the data is identical, but the provenance tag records which path produced
    the swap.  This test confirms the tags are set correctly on both paths.
    """
    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    for i, swap in enumerate(live_swaps):
        assert swap.source == "birdeye_live", (
            f"Live path Swap[{i}].source={swap.source!r}, expected 'birdeye_live'."
        )

    for i, swap in enumerate(backfill_swaps):
        assert swap.source == "birdeye_backfill", (
            f"Backfill path Swap[{i}].source={swap.source!r}, "
            "expected 'birdeye_backfill'."
        )


def test_live_backfill_failed_swap_excluded_from_both() -> None:
    """The failed swap in the golden fixture is absent from output on BOTH paths.

    Both live and backfill paths must DROP failed swaps (§6.2 / AC-18.2).
    The failed-swap guard must hold regardless of which source path is used.
    """
    failed_sig = _make_golden_event(7, side="buy", failed=True)["signature"]

    live_swaps = _run_live_path(_GOLDEN_FIXTURE)
    backfill_swaps = _run_backfill_path(_GOLDEN_FIXTURE)

    live_sigs = {s.signature for s in live_swaps}
    backfill_sigs = {s.signature for s in backfill_swaps}

    assert failed_sig not in live_sigs, (
        f"Failed swap {failed_sig[:20]!r} appeared in live path output. "
        "Failed swaps must be dropped on BOTH paths (§6.2 / AC-18.2)."
    )
    assert failed_sig not in backfill_sigs, (
        f"Failed swap {failed_sig[:20]!r} appeared in backfill path output. "
        "Failed swaps must be dropped on BOTH paths (§6.2 / AC-18.2)."
    )


def test_live_backfill_parity_single_golden_swap() -> None:
    """Parity holds for the minimal case: a single golden swap through both paths.

    If the single-swap case is byte-identical on all non-source fields, the
    shared normalization code path is confirmed at the most fundamental level.
    """
    single_event = [_make_golden_event(1, side="buy")]

    live_swaps = _run_live_path(single_event)
    backfill_swaps = _run_backfill_path(single_event)

    assert len(live_swaps) == 1, (
        f"Live path: expected 1 NormalizedSwap, got {len(live_swaps)}."
    )
    assert len(backfill_swaps) == 1, (
        f"Backfill path: expected 1 NormalizedSwap, got {len(backfill_swaps)}."
    )

    live_json = _all_non_source_fields_as_json(live_swaps[0])
    backfill_json = _all_non_source_fields_as_json(backfill_swaps[0])

    assert live_json == backfill_json, (
        "Single golden swap NOT byte-identical (non-source fields):\n"
        f"  Live:     {live_json}\n"
        f"  Backfill: {backfill_json}\n"
        "Parity must hold for the single-swap minimal case (AC-21.2)."
    )
