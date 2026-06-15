# ---
# module: core.tests.test_tape_recorder_ac182
# sprint: sprint-5
# story: US-18 AC-18.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.recorder, core.clock, core.replay_source,
#               core.normalized_swap, asyncio, types
# ---
"""AC-18.2 — NormalizedSwap emission, owner=signer, all three units, drop-failed.

Tests:
  1. test_failed_swap_produces_no_normalized_swap
       Feed a swap with failed=True → normalized_swaps is empty.

  2. test_landed_swap_maps_every_field_correctly
       Feed a landed swap → exactly one NormalizedSwap with all §7.1 fields
       mapped correctly (rel, block_time, slot, signature, price, side, vol_sol,
       vol_usd, sol_usd, owner, base_reserve, quote_reserve, quote_mint, source,
       phase).

  3. test_owner_equals_signer
       NormalizedSwap.owner == raw event's 'owner' field (tx signer, §3.3).

  4. test_all_three_unit_fields_populated
       vol_sol, vol_usd, sol_usd are all non-None and > 0 on a landed swap.

  5. test_mixed_swaps_only_landed_emitted
       Stream with 1 failed + 2 landed → exactly 2 NormalizedSwaps emitted,
       none corresponding to the failed swap.

  6. test_rel_anchored_to_graduated_block_time
       rel == block_time − token.graduated_block_time (never derived from first swap).

  7. test_failed_swap_still_appears_in_processed
       A failed swap IS appended to processed (it was received), but generates
       no NormalizedSwap — the drop is a normalization decision, not a receive error.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000

# A token-like object with graduated_block_time — used to anchor rel.
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)

_MINT = "PUMP_MINT_1111111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"

_LANDED_SWAP: dict[str, Any] = {
    "type": "SWAP",
    "mint": _MINT,
    "block_time": 1_700_000_060,
    "slot": 200,
    "signature": "SIG_LANDED_1111111111111111111111111111111111111111111111111",
    "side": "buy",
    "price": 0.00025,
    "vol_sol": 2.5,
    "vol_usd": 375.0,
    "sol_usd": 150.0,
    "owner": "SIGNER_WALLET_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "base_reserve": 5_000_000,
    "quote_reserve": 1_250_000,
    "quote_mint": _QUOTE_MINT,
    "failed": False,
}

_FAILED_SWAP: dict[str, Any] = {
    "type": "SWAP",
    "mint": _MINT,
    "block_time": 1_700_000_030,
    "slot": 195,
    "signature": "SIG_FAILED_1111111111111111111111111111111111111111111111111",
    "side": "sell",
    "price": 0.00020,
    "vol_sol": 1.0,
    "vol_usd": 150.0,
    "sol_usd": 150.0,
    "owner": "SIGNER_WALLET_FAILED_XXXXXX",
    "base_reserve": 4_800_000,
    "quote_reserve": 1_200_000,
    "quote_mint": _QUOTE_MINT,
    "failed": True,
}

_LANDED_SWAP_2: dict[str, Any] = {
    "type": "SWAP",
    "mint": _MINT,
    "block_time": 1_700_000_090,
    "slot": 210,
    "signature": "SIG_LANDED_2222222222222222222222222222222222222222222222222",
    "side": "sell",
    "price": 0.00030,
    "vol_sol": 1.2,
    "vol_usd": 180.0,
    "sol_usd": 150.0,
    "owner": "SIGNER_WALLET_BCDEFGHIJKLMNOPQRSTUVWXYZA",
    "base_reserve": 4_500_000,
    "quote_reserve": 1_350_000,
    "quote_mint": _QUOTE_MINT,
    "failed": False,
}

_TOKEN_STORE: dict[str, Any] = {_MINT: _TOKEN}
_T0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_recorder(events, token_store=None):
    """Drive TapeRecorder synchronously; return (processed, normalized_swaps)."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    store = token_store if token_store is not None else _TOKEN_STORE

    async def _inner():
        source = ReplaySource(event_log=events)
        clock = VirtualClock(_T0)
        recorder = TapeRecorder(source, clock, token_store=store)
        await recorder.run()
        return recorder.processed, recorder.normalized_swaps

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_failed_swap_produces_no_normalized_swap() -> None:
    """A swap with failed=True must produce NO NormalizedSwap (§6.2 / AC-18.2)."""
    _processed, normalized = _run_recorder([_FAILED_SWAP])

    assert normalized == [], (
        f"Expected no NormalizedSwaps for a failed swap, got {len(normalized)}: {normalized}"
    )


def test_landed_swap_maps_every_field_correctly() -> None:
    """A landed swap produces exactly one NormalizedSwap with all §7.1 fields mapped."""
    _processed, normalized = _run_recorder([_LANDED_SWAP])

    assert len(normalized) == 1, (
        f"Expected exactly 1 NormalizedSwap for a landed swap, got {len(normalized)}"
    )
    ns = normalized[0]

    expected_rel = float(_LANDED_SWAP["block_time"] - _GRADUATED_BLOCK_TIME)
    assert ns.rel == expected_rel, f"rel mismatch: expected {expected_rel}, got {ns.rel}"
    assert ns.block_time == _LANDED_SWAP["block_time"]
    assert ns.slot == _LANDED_SWAP["slot"]
    assert ns.signature == _LANDED_SWAP["signature"]
    assert ns.price == _LANDED_SWAP["price"]
    assert ns.side == _LANDED_SWAP["side"]
    assert ns.vol_sol == _LANDED_SWAP["vol_sol"]
    assert ns.vol_usd == _LANDED_SWAP["vol_usd"]
    assert ns.sol_usd == _LANDED_SWAP["sol_usd"]
    assert ns.owner == _LANDED_SWAP["owner"]
    assert ns.base_reserve == _LANDED_SWAP["base_reserve"]
    assert ns.quote_reserve == _LANDED_SWAP["quote_reserve"]
    assert ns.quote_mint == _LANDED_SWAP["quote_mint"]
    assert ns.source == "birdeye_live"
    assert ns.phase == "pre"


def test_owner_equals_signer() -> None:
    """NormalizedSwap.owner must equal the raw event's 'owner' (tx signer, §3.3)."""
    _processed, normalized = _run_recorder([_LANDED_SWAP])

    assert len(normalized) == 1
    assert normalized[0].owner == _LANDED_SWAP["owner"], (
        f"owner mismatch: expected {_LANDED_SWAP['owner']!r}, got {normalized[0].owner!r}. "
        "owner must be the tx signer (Birdeye 'owner' field, canonical both sides per §3.3)."
    )


def test_all_three_unit_fields_populated() -> None:
    """vol_sol, vol_usd, and sol_usd are all non-None and > 0 on a landed swap (D1)."""
    _processed, normalized = _run_recorder([_LANDED_SWAP])

    assert len(normalized) == 1
    ns = normalized[0]

    assert ns.vol_sol is not None, "vol_sol must not be None (D1 three-units requirement)"
    assert ns.vol_usd is not None, "vol_usd must not be None (D1 three-units requirement)"
    assert ns.sol_usd is not None, "sol_usd must not be None (D1 three-units requirement)"

    assert ns.vol_sol > 0, f"vol_sol must be > 0 for a landed swap, got {ns.vol_sol}"
    assert ns.vol_usd > 0, f"vol_usd must be > 0 for a landed swap, got {ns.vol_usd}"
    assert ns.sol_usd > 0, f"sol_usd must be > 0 for a landed swap, got {ns.sol_usd}"


def test_mixed_swaps_only_landed_emitted() -> None:
    """Stream with failed + landed swaps → only landed swaps produce NormalizedSwaps."""
    events = [_FAILED_SWAP, _LANDED_SWAP, _LANDED_SWAP_2]
    _processed, normalized = _run_recorder(events)

    # All 3 events were received
    assert len(_processed) == 3, f"Expected 3 processed events, got {len(_processed)}"

    # Only the 2 landed swaps produced NormalizedSwaps
    assert len(normalized) == 2, (
        f"Expected 2 NormalizedSwaps (1 failed dropped), got {len(normalized)}"
    )

    sigs = {ns.signature for ns in normalized}
    assert _LANDED_SWAP["signature"] in sigs
    assert _LANDED_SWAP_2["signature"] in sigs
    assert _FAILED_SWAP["signature"] not in sigs, (
        "Failed swap's signature must NOT appear in normalized_swaps"
    )


def test_rel_anchored_to_graduated_block_time() -> None:
    """rel == block_time − token.graduated_block_time, not derived from first swap."""
    # Use two swaps with different block_times to confirm each is anchored to the token
    events = [_LANDED_SWAP, _LANDED_SWAP_2]
    _processed, normalized = _run_recorder(events)

    assert len(normalized) == 2

    expected_rel_1 = float(_LANDED_SWAP["block_time"] - _GRADUATED_BLOCK_TIME)
    expected_rel_2 = float(_LANDED_SWAP_2["block_time"] - _GRADUATED_BLOCK_TIME)

    assert normalized[0].rel == expected_rel_1, (
        f"rel for swap 1: expected {expected_rel_1}, got {normalized[0].rel}. "
        "rel must be anchored to token.graduated_block_time, not first swap's block_time."
    )
    assert normalized[1].rel == expected_rel_2, (
        f"rel for swap 2: expected {expected_rel_2}, got {normalized[1].rel}"
    )


def test_failed_swap_still_appears_in_processed() -> None:
    """Failed swaps ARE received and stored in processed — the drop is normalization-only."""
    _processed, normalized = _run_recorder([_FAILED_SWAP])

    assert len(_processed) == 1, (
        f"Failed swap must be in processed (it was received): got {len(_processed)}"
    )
    assert _processed[0][0] == _FAILED_SWAP
    assert normalized == []
