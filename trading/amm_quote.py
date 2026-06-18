# ---
# module: trading.amm_quote
# sprint: sprint-13
# story: US-65 AC-65.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: stdlib (dataclasses)
# ---
"""PumpSwap constant-product AMM quote + fee math — AC-65.2.

Implements §10.1 constant-product math (k = base_reserve * quote_reserve)
and the two-sided fee model:
  BUY : fee added ON TOP of the swap quote (user pays more SOL)
  SELL: fee DEDUCTED from the swap quote (user receives less SOL)

Fee components are read from pinned GlobalConfig fixture values — never
from a live RPC call (offline constraint, zero-firehose).

H4 invariant: every division is guarded; ZeroReserveError is raised
before any division when either reserve is zero or None.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AmmFees",
    "ZeroReserveError",
    "spot_price",
    "quote_buy",
    "quote_sell",
]

# ---------------------------------------------------------------------------
# H4 zero-reserve guard exception
# ---------------------------------------------------------------------------


class ZeroReserveError(ZeroDivisionError):
    """Raised when a pool reserve is zero or None — H4 guard, never silent."""


# ---------------------------------------------------------------------------
# Fee container (loaded from pinned GlobalConfig fixture)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmmFees:
    """Immutable fee rates sourced from a pinned GlobalConfig fixture.

    Basis points (bps): 1 bps = 0.01%, 10 000 bps = 100%.

    GlobalConfig stores three fee components:
      lp_fee_bps        — accrues to LP providers (0.25% = 25 bps typical)
      protocol_fee_bps  — accrues to protocol treasury (0.05% = 5 bps typical)
      creator_fee_bps   — accrues to coin creator vault (0% = 0 bps default)

    Total 0.30% (30 bps) is the canonical PumpSwap default derived from the
    pinned GlobalConfig fixture values in amm_quote_ac652.json.
    """

    lp_fee_bps: int
    protocol_fee_bps: int
    creator_fee_bps: int

    @property
    def total_fee_bps(self) -> int:
        return self.lp_fee_bps + self.protocol_fee_bps + self.creator_fee_bps


# ---------------------------------------------------------------------------
# Reserve guard helper
# ---------------------------------------------------------------------------


def _check_reserves(base_reserve: object, quote_reserve: object) -> None:
    """Raise ZeroReserveError if either reserve is None or non-positive."""
    if base_reserve is None or quote_reserve is None:
        raise ZeroReserveError(
            f"pool reserves must not be None: base={base_reserve!r}, quote={quote_reserve!r}"
        )
    if base_reserve <= 0:
        raise ZeroReserveError(
            f"base_reserve must be > 0, got {base_reserve!r}"
        )
    if quote_reserve <= 0:
        raise ZeroReserveError(
            f"quote_reserve must be > 0, got {quote_reserve!r}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def spot_price(base_reserve: int, quote_reserve: int) -> float:
    """Instantaneous AMM price: quote_reserve / base_reserve.

    Returns the price in quote units per base unit (lamports per base token
    unit).  For human-readable SOL-per-token, the caller must divide by
    LAMPORTS_PER_SOL and multiply by 10**TOKEN_DECIMALS.

    Raises ZeroReserveError (H4) if either reserve is zero or None.
    """
    _check_reserves(base_reserve, quote_reserve)
    return quote_reserve / base_reserve


def quote_buy(
    base_reserve: int,
    quote_reserve: int,
    base_amount_out: int,
    fees: AmmFees,
) -> tuple[int, int]:
    """Constant-product BUY quote.

    Returns (sol_in_with_fee, sol_in_pre_fee) — both in lamports (integers).

    Constant-product formula (k = base_reserve * quote_reserve):
        sol_in_pre_fee = base_amount_out * quote_reserve
                         // (base_reserve - base_amount_out)

    Fee applied ON TOP of the swap amount (buy fee):
        sol_in_with_fee = sol_in_pre_fee * (10_000 + total_fee_bps) // 10_000

    Raises:
        ZeroReserveError: either reserve is zero/None (H4).
        ValueError: base_amount_out is not positive, or >= base_reserve
                    (would drain or violate the pool invariant).
    """
    _check_reserves(base_reserve, quote_reserve)

    if base_amount_out <= 0:
        raise ValueError(f"base_amount_out must be positive, got {base_amount_out!r}")
    if base_amount_out >= base_reserve:
        raise ValueError(
            f"base_amount_out ({base_amount_out}) must be less than base_reserve "
            f"({base_reserve}); cannot drain the pool"
        )

    denominator = base_reserve - base_amount_out
    # denominator > 0 guaranteed by the guard above; H4 satisfied
    sol_in_pre_fee: int = base_amount_out * quote_reserve // denominator

    sol_in_with_fee: int = sol_in_pre_fee * (10_000 + fees.total_fee_bps) // 10_000

    return sol_in_with_fee, sol_in_pre_fee


def quote_sell(
    base_reserve: int,
    quote_reserve: int,
    base_amount_in: int,
    fees: AmmFees,
) -> tuple[int, int]:
    """Constant-product SELL quote.

    Returns (sol_out_after_fee, sol_out_pre_fee) — both in lamports (integers).

    Constant-product formula (k = base_reserve * quote_reserve):
        sol_out_pre_fee = base_amount_in * quote_reserve
                          // (base_reserve + base_amount_in)

    Fee DEDUCTED from the swap output (sell fee):
        sol_out_after_fee = sol_out_pre_fee * (10_000 - total_fee_bps) // 10_000

    Raises:
        ZeroReserveError: either reserve is zero/None (H4).
        ValueError: base_amount_in is not positive.
    """
    _check_reserves(base_reserve, quote_reserve)

    if base_amount_in <= 0:
        raise ValueError(f"base_amount_in must be positive, got {base_amount_in!r}")

    denominator = base_reserve + base_amount_in
    # denominator >= base_reserve + 1 > 0; H4 satisfied (base_reserve already guarded > 0)
    sol_out_pre_fee: int = base_amount_in * quote_reserve // denominator

    sol_out_after_fee: int = sol_out_pre_fee * (10_000 - fees.total_fee_bps) // 10_000

    return sol_out_after_fee, sol_out_pre_fee
