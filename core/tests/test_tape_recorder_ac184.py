# ---
# module: core.tests.test_tape_recorder_ac184
# sprint: sprint-5
# story: US-18 AC-18.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.recorder, core.clock, core.replay_source, asyncio, types
# ---
"""AC-18.4 — Zero-/degenerate-swap guard (S8 / #405).

Policy (DEGENERATE_SWAP_POLICY = "skip"):
  A swap with zero/None reserves, zero volume, or a degenerate price field is
  SKIPPED — never emitted as a NormalizedSwap, never stored as a silent 0-value
  row, never raises ZeroDivisionError or any other exception.
  Skipped swaps are tracked in TapeRecorder.skipped_degenerate for audit.

Tests:
  1.  test_degenerate_policy_is_skip
        DEGENERATE_SWAP_POLICY constant equals "skip" — the policy is explicit
        and inspectable in code, not a magic behaviour.

  2.  test_zero_price_swap_skipped_no_exception
        price=0 → no exception, swap in skipped_degenerate, not in normalized_swaps.

  3.  test_none_price_swap_skipped_no_exception
        price=None → no exception, skipped.

  4.  test_zero_vol_sol_swap_skipped_no_exception
        vol_sol=0 (zero volume) → no exception, skipped.

  5.  test_none_vol_sol_swap_skipped_no_exception
        vol_sol=None (missing volume) → no exception, skipped.

  6.  test_zero_base_reserve_swap_skipped_no_exception
        base_reserve=0 → no exception, skipped.

  7.  test_none_base_reserve_swap_skipped_no_exception
        base_reserve=None → no exception, skipped (zero/None reserves per AC-18.4).

  8.  test_zero_quote_reserve_swap_skipped_no_exception
        quote_reserve=0 → no exception, skipped.

  9.  test_none_quote_reserve_swap_skipped_no_exception
        quote_reserve=None → no exception, skipped (zero/None reserves per AC-18.4).

  10. test_degenerate_swap_still_in_processed
        A degenerate swap IS received (appears in processed) but is NOT emitted —
        the skip is a normalization decision, not a receive error.

  11. test_good_swap_alongside_degenerate_only_good_emitted
        Stream: 1 degenerate + 1 valid landed swap → exactly 1 NormalizedSwap
        (the valid one); degenerate goes to skipped_degenerate.

  12. test_multiple_degenerate_conditions_all_skipped
        Each distinct degenerate condition (price=0, vol_sol=0, base_reserve=0,
        quote_reserve=0, price=None, base_reserve=None, quote_reserve=None) skips
        the swap; 0 NormalizedSwaps emitted across all of them.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_700_000_000
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)

_MINT = "PUMP_MINT_AC184_11111111111111111111111111111111111"
_QUOTE_MINT = "So11111111111111111111111111111111111111112"

_TOKEN_STORE: dict[str, Any] = {_MINT: _TOKEN}
_T0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)

# A fully valid swap — used as the "good swap" baseline and for positive-control.
_VALID_SWAP: dict[str, Any] = {
    "type": "SWAP",
    "mint": _MINT,
    "block_time": 1_700_000_060,
    "slot": 200,
    "signature": "SIG_VALID_AC184_1111111111111111111111111111111111111111111",
    "side": "buy",
    "price": 0.00025,
    "vol_sol": 2.5,
    "vol_usd": 375.0,
    "sol_usd": 150.0,
    "owner": "SIGNER_WALLET_VALID_AC184",
    "base_reserve": 5_000_000,
    "quote_reserve": 1_250_000,
    "quote_mint": _QUOTE_MINT,
    "failed": False,
}


def _make_degenerate(overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of _VALID_SWAP with *overrides* applied to make it degenerate."""
    swap = dict(_VALID_SWAP)
    swap.update(overrides)
    swap["signature"] = f"SIG_DEG_{list(overrides.keys())[0].upper()}_{'X' * 40}"
    return swap


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def _run_recorder(events: list[dict]) -> tuple[list, list, list]:
    """Drive TapeRecorder; return (processed, normalized_swaps, skipped_degenerate)."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = ReplaySource(event_log=events)
        clock = VirtualClock(_T0)
        recorder = TapeRecorder(source, clock, token_store=_TOKEN_STORE)
        await recorder.run()
        return recorder.processed, recorder.normalized_swaps, recorder.skipped_degenerate

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_degenerate_policy_is_skip() -> None:
    """DEGENERATE_SWAP_POLICY constant is 'skip' — policy is explicit and inspectable."""
    from core.tape.recorder import DEGENERATE_SWAP_POLICY

    assert DEGENERATE_SWAP_POLICY == "skip", (
        f"Expected DEGENERATE_SWAP_POLICY == 'skip', got {DEGENERATE_SWAP_POLICY!r}. "
        "The policy must be declared explicitly in the module (AC-18.4)."
    )


def test_zero_price_swap_skipped_no_exception() -> None:
    """price=0 → no exception raised; swap skipped (not in normalized_swaps)."""
    swap = _make_degenerate({"price": 0})

    # Must not raise
    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"price=0 swap must be skipped (not emitted as NormalizedSwap), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"price=0 swap must appear in skipped_degenerate, got {skipped}"
    )


def test_none_price_swap_skipped_no_exception() -> None:
    """price=None → no exception raised; swap skipped."""
    swap = _make_degenerate({"price": None})

    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"price=None swap must be skipped (degenerate price field), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"price=None swap must appear in skipped_degenerate, got {skipped}"
    )


def test_zero_vol_sol_swap_skipped_no_exception() -> None:
    """vol_sol=0 (zero volume) → no exception raised; swap skipped."""
    swap = _make_degenerate({"vol_sol": 0})

    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"vol_sol=0 swap must be skipped (zero volume per AC-18.4), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"vol_sol=0 swap must appear in skipped_degenerate, got {skipped}"
    )


def test_none_vol_sol_swap_skipped_no_exception() -> None:
    """vol_sol=None (missing volume) → no exception raised; swap skipped."""
    swap = _make_degenerate({"vol_sol": None})

    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"vol_sol=None swap must be skipped (missing volume), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"vol_sol=None swap must appear in skipped_degenerate, got {skipped}"
    )


def test_zero_base_reserve_swap_skipped_no_exception() -> None:
    """base_reserve=0 (zero reserves) → no exception raised; swap skipped."""
    swap = _make_degenerate({"base_reserve": 0})

    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"base_reserve=0 swap must be skipped (zero reserves per AC-18.4), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"base_reserve=0 swap must appear in skipped_degenerate, got {skipped}"
    )


def test_none_base_reserve_swap_emitted() -> None:
    """base_reserve=None is VALID (Birdeye carries no reserves, §3.3/§7.1) → emitted.

    Updated for US-22 AC-22.3: the original AC-18.4 wording treated None reserves
    as degenerate, but that was written against synthetic fixtures that always
    had reserves.  Real Birdeye swaps legitimately have None reserves (price =
    tokenPrice, not reserve-derived), so a None-reserve swap must NORMALIZE, not
    skip — otherwise every live Birdeye swap is dropped.
    """
    swap = dict(_VALID_SWAP)
    swap["base_reserve"] = None

    processed, normalized, skipped = _run_recorder([swap])

    assert len(normalized) == 1, (
        f"base_reserve=None swap must be emitted (valid for Birdeye), got {normalized}"
    )
    assert skipped == [], (
        f"base_reserve=None swap must NOT be skipped, got {skipped}"
    )
    assert normalized[0].base_reserve is None


def test_zero_quote_reserve_swap_skipped_no_exception() -> None:
    """quote_reserve=0 (zero reserves) → no exception raised; swap skipped."""
    swap = _make_degenerate({"quote_reserve": 0})

    processed, normalized, skipped = _run_recorder([swap])

    assert normalized == [], (
        f"quote_reserve=0 swap must be skipped (zero reserves per AC-18.4), got {normalized}"
    )
    assert len(skipped) == 1, (
        f"quote_reserve=0 swap must appear in skipped_degenerate, got {skipped}"
    )


def test_none_quote_reserve_swap_emitted() -> None:
    """quote_reserve=None is VALID (Birdeye carries no reserves, §3.3/§7.1) → emitted.

    Updated for US-22 AC-22.3 — see test_none_base_reserve_swap_emitted.
    """
    swap = dict(_VALID_SWAP)
    swap["quote_reserve"] = None

    processed, normalized, skipped = _run_recorder([swap])

    assert len(normalized) == 1, (
        f"quote_reserve=None swap must be emitted (valid for Birdeye), got {normalized}"
    )
    assert skipped == [], (
        f"quote_reserve=None swap must NOT be skipped, got {skipped}"
    )
    assert normalized[0].quote_reserve is None


def test_degenerate_swap_still_in_processed() -> None:
    """A degenerate swap IS received (in processed) but NOT emitted — skip is normalization-only."""
    swap = _make_degenerate({"price": 0})

    processed, normalized, skipped = _run_recorder([swap])

    assert len(processed) == 1, (
        f"Degenerate swap must be in processed (it was received), got {len(processed)}"
    )
    assert normalized == [], "Degenerate swap must NOT appear in normalized_swaps"
    assert len(skipped) == 1, "Degenerate swap must appear in skipped_degenerate"


def test_good_swap_alongside_degenerate_only_good_emitted() -> None:
    """Stream: 1 degenerate + 1 valid → exactly 1 NormalizedSwap; 1 in skipped_degenerate."""
    degenerate = _make_degenerate({"price": 0})
    valid = dict(_VALID_SWAP)

    events = [degenerate, valid]
    processed, normalized, skipped = _run_recorder(events)

    # Both events received
    assert len(processed) == 2, f"Expected 2 processed events, got {len(processed)}"

    # Only the valid swap produced a NormalizedSwap
    assert len(normalized) == 1, (
        f"Expected exactly 1 NormalizedSwap (degenerate skipped), got {len(normalized)}"
    )
    assert normalized[0].signature == _VALID_SWAP["signature"], (
        f"The NormalizedSwap must correspond to the valid swap, "
        f"got signature {normalized[0].signature!r}"
    )

    # The degenerate swap is in skipped_degenerate
    assert len(skipped) == 1, (
        f"Expected 1 entry in skipped_degenerate, got {len(skipped)}"
    )


def test_multiple_degenerate_conditions_all_skipped() -> None:
    """Each degenerate condition produces 0 NormalizedSwaps and 1 skipped_degenerate entry."""
    # NOTE (US-22 AC-22.3): None reserves are NOT degenerate — Birdeye carries no
    # reserves (§3.3/§7.1).  Only price/vol degeneracy and PRESENT-but-zero
    # reserves are degenerate.
    conditions = [
        {"price": 0},
        {"price": None},
        {"vol_sol": 0},
        {"vol_sol": None},
        {"base_reserve": 0},
        {"quote_reserve": 0},
    ]

    for overrides in conditions:
        swap = _make_degenerate(overrides)
        processed, normalized, skipped = _run_recorder([swap])

        assert normalized == [], (
            f"Condition {overrides}: expected no NormalizedSwap, got {len(normalized)}. "
            "Degenerate swap must be skipped (never a silent 0 row — AC-18.4)."
        )
        assert len(skipped) == 1, (
            f"Condition {overrides}: expected 1 skipped_degenerate entry, got {len(skipped)}. "
            "Degenerate swap must be tracked in skipped_degenerate."
        )
