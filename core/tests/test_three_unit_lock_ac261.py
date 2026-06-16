# ---
# module: core.tests.test_three_unit_lock_ac261
# sprint: sprint-6
# story: US-26 AC-26.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, core.normalized_swap, core.tape.swap_writer, pytest
# ---
"""AC-26.1 — Three-unit lock: vol_sol, vol_usd, sol_usd must all be present,
non-zero, and satisfy the locked relationship  vol_usd ≈ vol_sol · sol_usd (D1).

Tolerance
---------
REL_TOL = 1e-6  (relative tolerance on the three-unit relationship)

Rationale: test fixtures set vol_usd = vol_sol * sol_usd as the exact IEEE 754
double-precision product, so the recomputed product differs from the stored value
by at most 1 ULP (≈ 2e-16) — well within REL_TOL.  The 1e-6 bound is chosen to
catch genuine integrity failures (dropped field, wrong unit, zeroed value) while
tolerating the harmless rounding differences that arise when an upstream provider
independently computes vol_usd rather than deriving it as the product of the other
two.

Tests
-----
  test_all_three_unit_fields_present_and_nonzero
      Three Swap rows; assert vol_sol, vol_usd, sol_usd are all non-None
      and non-zero for each.

  test_three_unit_relationship_holds_for_recorded_swaps
      Three Swap rows; assert |vol_usd - vol_sol*sol_usd|/vol_usd ≤ REL_TOL
      for each row.

  test_three_unit_relationship_via_swap_writer
      Three NormalizedSwaps written via SwapWriter, queried from DB; assert
      the D1 invariant holds on each persisted row.

  test_three_unit_relationship_varying_sol_usd
      Four Swap rows with different per-block sol_usd values; D1 invariant
      holds for each row individually (invariant is per-row, not aggregate).

  test_vol_usd_not_silently_dropped
      A Swap row written with a specific vol_usd reads back that exact value
      confirming it was not silently dropped or zeroed.
"""
import pytest

from core.models import Swap
from core.normalized_swap import NormalizedSwap
from core.tape.swap_writer import SwapWriter

# ---------------------------------------------------------------------------
# Named tolerance constant (AC-26.1 requirement)
# ---------------------------------------------------------------------------
# See module docstring for the rationale behind 1e-6.
REL_TOL = 1e-6

# Shared per-block SOL/USD reference price used in fixtures (D1 — per-block).
_SOL_USD = 150.0

# (side, vol_sol, sol_usd, vol_usd)  — vol_usd = vol_sol * sol_usd exactly.
_SWAP_FIXTURES = [
    ("buy",  2.0,  _SOL_USD, 2.0  * _SOL_USD),   # 300.0
    ("sell", 3.5,  _SOL_USD, 3.5  * _SOL_USD),   # 525.0
    ("buy",  0.75, _SOL_USD, 0.75 * _SOL_USD),   # 112.5
]


def _create_swap(tag: str, idx: int, side: str, vol_sol: float, sol_usd: float, vol_usd: float) -> Swap:
    mint = (f"M261{tag}{idx}" + "Z" * 59)[:64]
    sig  = (f"S261{tag}{idx}" + "Z" * 124)[:128]
    return Swap.objects.create(
        mint=mint,
        block_time=1_700_000_001,
        slot=100 + idx,
        signature=sig,
        side=side,
        price=0.0001,
        vol_sol=vol_sol,
        vol_usd=vol_usd,
        sol_usd=sol_usd,
        owner=None,
        base_reserve=None,
        quote_reserve=None,
        rel=1.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_all_three_unit_fields_present_and_nonzero() -> None:
    """vol_sol, vol_usd, and sol_usd must all be non-None and non-zero on every Swap row."""
    for i, (side, vol_sol, sol_usd, vol_usd) in enumerate(_SWAP_FIXTURES):
        row = _create_swap("a", i, side, vol_sol, sol_usd, vol_usd)
        obj = Swap.objects.get(pk=row.pk)

        assert obj.vol_sol is not None, f"swap[{i}]: vol_sol is None"
        assert obj.vol_usd is not None, f"swap[{i}]: vol_usd is None"
        assert obj.sol_usd is not None, f"swap[{i}]: sol_usd is None"

        assert obj.vol_sol != 0.0, f"swap[{i}]: vol_sol is zero"
        assert obj.vol_usd != 0.0, f"swap[{i}]: vol_usd is zero"
        assert obj.sol_usd != 0.0, f"swap[{i}]: sol_usd is zero"


@pytest.mark.django_db
def test_three_unit_relationship_holds_for_recorded_swaps() -> None:
    """vol_usd ≈ vol_sol · sol_usd within REL_TOL for every recorded Swap (D1 lock)."""
    for i, (side, vol_sol, sol_usd, vol_usd) in enumerate(_SWAP_FIXTURES):
        row = _create_swap("b", i, side, vol_sol, sol_usd, vol_usd)
        obj = Swap.objects.get(pk=row.pk)

        expected = obj.vol_sol * obj.sol_usd
        rel_err = abs(obj.vol_usd - expected) / obj.vol_usd
        assert rel_err <= REL_TOL, (
            f"swap[{i}]: D1 invariant violated — "
            f"vol_usd={obj.vol_usd}, vol_sol={obj.vol_sol}, sol_usd={obj.sol_usd}, "
            f"expected≈{expected:.8f}, rel_err={rel_err:.2e} > REL_TOL={REL_TOL}"
        )


@pytest.mark.django_db
def test_three_unit_relationship_via_swap_writer() -> None:
    """SwapWriter persists all three unit fields; D1 invariant holds on every DB row."""
    writer = SwapWriter()
    pairs: list[tuple[str, NormalizedSwap]] = []

    for i, (side, vol_sol, sol_usd, vol_usd) in enumerate(_SWAP_FIXTURES):
        mint = (f"M261c{i}" + "Z" * 59)[:64]
        ns = NormalizedSwap(
            rel=float(i + 1),
            block_time=1_700_001_000 + i,
            slot=300 + i,
            signature=(f"SWR261c{i}" + "Z" * 120)[:128],
            price=0.0002,
            side=side,
            vol_sol=vol_sol,
            vol_usd=vol_usd,
            sol_usd=sol_usd,
            owner=None,
            base_reserve=None,
            quote_reserve=None,
            quote_mint="So11111111111111111111111111111111111111112",
            source="birdeye_live",
            phase="post",
        )
        pairs.append((mint, ns))

    writer.write(pairs)

    for i, (mint, ns) in enumerate(pairs):
        obj = Swap.objects.get(mint=mint, signature=ns.signature)

        assert obj.vol_sol != 0.0 and obj.vol_usd != 0.0 and obj.sol_usd != 0.0, (
            f"writer swap[{i}]: one or more unit fields missing/zero"
        )

        expected = obj.vol_sol * obj.sol_usd
        rel_err = abs(obj.vol_usd - expected) / obj.vol_usd
        assert rel_err <= REL_TOL, (
            f"writer swap[{i}]: D1 invariant violated after SwapWriter — "
            f"rel_err={rel_err:.2e} > REL_TOL={REL_TOL}"
        )


@pytest.mark.django_db
def test_three_unit_relationship_varying_sol_usd() -> None:
    """D1 invariant holds per-row when sol_usd (per-block price) differs across swaps."""
    block_prices = [100.0, 150.0, 200.0, 175.5]
    for i, sol_usd in enumerate(block_prices):
        vol_sol = 1.0 + i * 0.5
        vol_usd = vol_sol * sol_usd
        row = _create_swap("d", i, "buy", vol_sol, sol_usd, vol_usd)
        obj = Swap.objects.get(pk=row.pk)

        expected = obj.vol_sol * obj.sol_usd
        rel_err = abs(obj.vol_usd - expected) / obj.vol_usd
        assert rel_err <= REL_TOL, (
            f"block_price={sol_usd}: D1 invariant violated — rel_err={rel_err:.2e}"
        )


@pytest.mark.django_db
def test_vol_usd_not_silently_dropped() -> None:
    """A specific vol_usd value written to DB reads back unchanged — not silently dropped."""
    vol_usd_written = 2.0 * _SOL_USD  # 300.0
    row = _create_swap("e", 0, "buy", 2.0, _SOL_USD, vol_usd_written)
    obj = Swap.objects.get(pk=row.pk)

    assert obj.vol_usd == pytest.approx(vol_usd_written), (
        f"vol_usd silently dropped or altered: written={vol_usd_written}, "
        f"read back={obj.vol_usd}"
    )
