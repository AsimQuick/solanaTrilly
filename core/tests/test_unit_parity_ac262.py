# ---
# module: core.tests.test_unit_parity_ac262
# sprint: sprint-6
# story: US-26 AC-26.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.normalized_swap, pytest
# ---
"""AC-26.2 — Unit-invariant feature parity (D1 backbone).

Unit-invariant features (shares, ratios, counts) must be byte-identical when
derived from vol_sol versus vol_usd.  This holds because, within a block where
sol_usd is constant, vol_usd_i = vol_sol_i * sol_usd — the sol_usd factor
cancels exactly in every ratio or share, giving the SAME IEEE 754 result on both
code paths.

Quote-unit features (e.g. total dollar volume) are explicitly tagged with their
unit column and are NOT expected to be equal across bases.

Byte-identical guarantee
------------------------
The fixtures below use exact IEEE 754-representable values (powers of 2 and
multiples of them) so that the floating-point operations on both paths go through
identically rounded intermediates and produce the same bit pattern.

Tests
-----
  test_buy_fraction_is_byte_identical_in_sol_and_usd
      buy_vol / total_vol derived via vol_sol == same derived via vol_usd.

  test_flow_ratio_is_byte_identical_in_sol_and_usd
      buy_vol / sell_vol derived via vol_sol == same derived via vol_usd.

  test_per_trader_volume_share_is_byte_identical_in_sol_and_usd
      trader_vol / total_vol for a specific owner, via both bases.

  test_buy_count_fraction_is_trivially_unit_invariant
      n_buys / n_total is a count feature — inherently unit-invariant.

  test_unit_invariance_holds_across_homogeneous_block
      Five swaps in one block; buy fraction is identical in both bases.

  test_total_volume_is_explicitly_unit_dependent
      Total volume differs by exactly the sol_usd factor — confirming that
      quote-unit aggregates are NOT unit-invariant and must declare their unit.
"""
from typing import NamedTuple

from core.normalized_swap import NormalizedSwap

# ---------------------------------------------------------------------------
# Helpers — build NormalizedSwap with the D1 constraint enforced
# ---------------------------------------------------------------------------


class _SwapSpec(NamedTuple):
    side: str
    vol_sol: float
    sol_usd: float
    owner: str = "wallet_A"


def _make_swap(spec: _SwapSpec, idx: int) -> NormalizedSwap:
    """Build a NormalizedSwap with vol_usd = vol_sol * sol_usd (D1 exact product)."""
    return NormalizedSwap(
        rel=float(idx),
        block_time=1_700_000_000 + idx,
        slot=100 + idx,
        signature=f"sig262_{idx}" + "x" * 112,
        price=0.0001,
        side=spec.side,
        vol_sol=spec.vol_sol,
        vol_usd=spec.vol_sol * spec.sol_usd,   # D1: exact product
        sol_usd=spec.sol_usd,
        owner=spec.owner,
        base_reserve=None,
        quote_reserve=None,
        quote_mint="So11111111111111111111111111111111111111112",
        source="birdeye_live",
        phase="post",
    )


# ---------------------------------------------------------------------------
# Feature helpers (no units hardcoded — operate on whichever column is passed)
# ---------------------------------------------------------------------------


def _buy_fraction(swaps: list[NormalizedSwap], *, use_usd: bool) -> float:
    """buy_vol / total_vol — unit-invariant (sol_usd cancels in ratio)."""
    col = "vol_usd" if use_usd else "vol_sol"
    total = sum(getattr(s, col) for s in swaps)
    buys  = sum(getattr(s, col) for s in swaps if s.side == "buy")
    return buys / total


def _flow_ratio(swaps: list[NormalizedSwap], *, use_usd: bool) -> float:
    """buy_vol / sell_vol — unit-invariant (sol_usd cancels in ratio)."""
    col = "vol_usd" if use_usd else "vol_sol"
    buys  = sum(getattr(s, col) for s in swaps if s.side == "buy")
    sells = sum(getattr(s, col) for s in swaps if s.side == "sell")
    return buys / sells


def _trader_share(swaps: list[NormalizedSwap], owner: str, *, use_usd: bool) -> float:
    """trader_vol / total_vol for a specific owner — unit-invariant."""
    col = "vol_usd" if use_usd else "vol_sol"
    total  = sum(getattr(s, col) for s in swaps)
    trader = sum(getattr(s, col) for s in swaps if s.owner == owner)
    return trader / total


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_buy_fraction_is_byte_identical_in_sol_and_usd() -> None:
    """buy_vol / total_vol is byte-identical whether computed via vol_sol or vol_usd.

    Fixture: two swaps with exactly equal vol_sol (4.0 each), constant sol_usd=100.
    buy_fraction = 4.0 / 8.0 = 0.5 exactly in IEEE 754, both paths.
    """
    _SOL_USD = 100.0
    specs = [
        _SwapSpec("buy",  4.0, _SOL_USD),
        _SwapSpec("sell", 4.0, _SOL_USD),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    frac_sol = _buy_fraction(swaps, use_usd=False)
    frac_usd = _buy_fraction(swaps, use_usd=True)

    assert frac_sol == frac_usd, (
        f"buy_fraction should be byte-identical: sol={frac_sol!r}, usd={frac_usd!r}"
    )


def test_flow_ratio_is_byte_identical_in_sol_and_usd() -> None:
    """buy_vol / sell_vol is byte-identical via vol_sol and vol_usd.

    Fixture: buy=8.0 SOL, sell=4.0 SOL, constant sol_usd=100.
    ratio = 8.0 / 4.0 = 2.0 (exact IEEE 754) on both paths.
    """
    _SOL_USD = 100.0
    specs = [
        _SwapSpec("buy",  8.0, _SOL_USD),
        _SwapSpec("sell", 4.0, _SOL_USD),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    ratio_sol = _flow_ratio(swaps, use_usd=False)
    ratio_usd = _flow_ratio(swaps, use_usd=True)

    assert ratio_sol == ratio_usd, (
        f"flow_ratio should be byte-identical: sol={ratio_sol!r}, usd={ratio_usd!r}"
    )


def test_per_trader_volume_share_is_byte_identical_in_sol_and_usd() -> None:
    """Trader volume share (trader_vol / total_vol) is byte-identical across unit bases.

    Fixture: wallet_A buys 8.0, wallet_B buys 4.0, constant sol_usd=100.
    wallet_A share = 8.0 / 12.0 = 2/3 — same IEEE 754 result on both paths.
    """
    _SOL_USD = 100.0
    specs = [
        _SwapSpec("buy", 8.0, _SOL_USD, owner="wallet_A"),
        _SwapSpec("buy", 4.0, _SOL_USD, owner="wallet_B"),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    share_sol = _trader_share(swaps, "wallet_A", use_usd=False)
    share_usd = _trader_share(swaps, "wallet_A", use_usd=True)

    assert share_sol == share_usd, (
        f"trader share should be byte-identical: sol={share_sol!r}, usd={share_usd!r}"
    )


def test_buy_count_fraction_is_trivially_unit_invariant() -> None:
    """n_buys / n_total is a count feature — unit-invariant by construction."""
    _SOL_USD = 150.0
    specs = [
        _SwapSpec("buy",  1.0, _SOL_USD),
        _SwapSpec("buy",  2.0, _SOL_USD),
        _SwapSpec("sell", 3.0, _SOL_USD),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    n_buys  = sum(1 for s in swaps if s.side == "buy")
    n_total = len(swaps)
    count_fraction = n_buys / n_total

    # Constant regardless of vol_sol or vol_usd values — confirmed by both paths.
    frac_sol = _buy_fraction(swaps, use_usd=False)
    frac_usd = _buy_fraction(swaps, use_usd=True)

    assert frac_sol == frac_usd, (
        "buy_fraction (volume-based) must be identical regardless of unit basis"
    )
    # Count fraction differs from volume fraction only because volumes differ —
    # confirming both are internally consistent (not an assertion of equality here).
    assert 0.0 < count_fraction <= 1.0


def test_unit_invariance_holds_across_homogeneous_block() -> None:
    """Five swaps in one block (constant sol_usd); buy fraction identical on both bases."""
    _SOL_USD = 175.5
    specs = [
        _SwapSpec("buy",  2.0,  _SOL_USD),
        _SwapSpec("buy",  4.0,  _SOL_USD),
        _SwapSpec("sell", 1.0,  _SOL_USD),
        _SwapSpec("sell", 3.0,  _SOL_USD),
        _SwapSpec("buy",  2.0,  _SOL_USD),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    frac_sol = _buy_fraction(swaps, use_usd=False)
    frac_usd = _buy_fraction(swaps, use_usd=True)

    assert frac_sol == frac_usd, (
        f"buy_fraction in homogeneous block should be byte-identical: "
        f"sol={frac_sol!r}, usd={frac_usd!r}"
    )

    ratio_sol = _flow_ratio(swaps, use_usd=False)
    ratio_usd = _flow_ratio(swaps, use_usd=True)

    assert ratio_sol == ratio_usd, (
        f"flow_ratio in homogeneous block should be byte-identical: "
        f"sol={ratio_sol!r}, usd={ratio_usd!r}"
    )


def test_total_volume_is_explicitly_unit_dependent() -> None:
    """Total volume is NOT unit-invariant — it scales by sol_usd (quote-unit feature).

    This test documents the boundary: quote-unit aggregates must declare their
    unit column (vol_sol or vol_usd) — they are NOT invariant across bases.
    """
    _SOL_USD = 150.0
    specs = [
        _SwapSpec("buy",  2.0, _SOL_USD),
        _SwapSpec("sell", 3.0, _SOL_USD),
    ]
    swaps = [_make_swap(s, i) for i, s in enumerate(specs)]

    total_sol = sum(s.vol_sol for s in swaps)   # 5.0 SOL
    total_usd = sum(s.vol_usd for s in swaps)   # 750.0 USD

    assert total_sol != total_usd, (
        "total volume should differ across unit bases (quote-unit feature) — "
        "this confirms unit-dependent aggregates must declare their unit column"
    )
    # The ratio equals sol_usd exactly (within floating-point precision).
    assert abs(total_usd / total_sol - _SOL_USD) < 1e-9, (
        f"total_usd / total_sol should equal sol_usd={_SOL_USD}"
    )
