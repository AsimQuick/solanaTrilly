# ---
# module: trading.tests.test_amm_quote_ac652
# sprint: sprint-13
# story: US-65 AC-65.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.amm_quote, fixtures/amm_quote_ac652.json
# ---
"""Unit tests for AC-65.2 — PumpSwap constant-product AMM quote + fee math.

Coverage:
  §1  AmmFees total_fee_bps property
  §2  spot_price — normal + H4 zero-reserve guards (zero and None)
  §3  quote_buy  — pinned fixture + fee-on-top assertion + H4 guards + edge cases
  §4  quote_sell — pinned fixture + fee-deducted assertion + H4 guards + edge cases
  §5  Fixture integrity — GlobalConfig values match AC-65.2 spec (0.25%/0.30%)

All tests are offline/deterministic — zero firehose, no Django DB, no network.
"""

import json
import pathlib

import pytest

from trading.amm_quote import (
    AmmFees,
    ZeroReserveError,
    quote_buy,
    quote_sell,
    spot_price,
)

# ---------------------------------------------------------------------------
# Load pinned fixture
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "amm_quote_ac652.json"
)


@pytest.fixture(scope="module")
def fx():
    """Return the parsed fixture dict (loaded once per module)."""
    with open(_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fees(fx):
    """AmmFees loaded from the pinned GlobalConfig fixture."""
    gc = fx["global_config"]
    return AmmFees(
        lp_fee_bps=gc["lp_fee_basis_points"],
        protocol_fee_bps=gc["protocol_fee_basis_points"],
        creator_fee_bps=gc["coin_creator_fee_basis_points"],
    )


@pytest.fixture(scope="module")
def pool(fx):
    """Pinned pool reserves."""
    return fx["pool_reserve_fixture"]


# ===========================================================================
# §1  AmmFees
# ===========================================================================

class TestAmmFees:
    def test_total_fee_bps_sum(self):
        f = AmmFees(lp_fee_bps=25, protocol_fee_bps=5, creator_fee_bps=0)
        assert f.total_fee_bps == 30

    def test_total_fee_bps_with_creator_fee(self):
        f = AmmFees(lp_fee_bps=20, protocol_fee_bps=5, creator_fee_bps=5)
        assert f.total_fee_bps == 30

    def test_zero_fees(self):
        f = AmmFees(lp_fee_bps=0, protocol_fee_bps=0, creator_fee_bps=0)
        assert f.total_fee_bps == 0

    def test_frozen(self):
        f = AmmFees(lp_fee_bps=25, protocol_fee_bps=5, creator_fee_bps=0)
        with pytest.raises((AttributeError, TypeError)):
            f.lp_fee_bps = 99  # type: ignore[misc]


# ===========================================================================
# §2  spot_price
# ===========================================================================

class TestSpotPrice:
    def test_normal(self, pool):
        price = spot_price(pool["base_reserve"], pool["quote_reserve"])
        assert price == pytest.approx(0.085, rel=1e-9)

    def test_matches_pinned_fixture(self, fx, pool):
        price = spot_price(pool["base_reserve"], pool["quote_reserve"])
        assert price == pytest.approx(fx["spot_price_fixture"]["expected_price"], rel=1e-9)

    def test_zero_base_reserve_raises(self, pool):
        with pytest.raises(ZeroReserveError):
            spot_price(0, pool["quote_reserve"])

    def test_zero_quote_reserve_raises(self, pool):
        with pytest.raises(ZeroReserveError):
            spot_price(pool["base_reserve"], 0)

    def test_both_zero_raises(self):
        with pytest.raises(ZeroReserveError):
            spot_price(0, 0)

    def test_none_base_reserve_raises(self, pool):
        with pytest.raises(ZeroReserveError):
            spot_price(None, pool["quote_reserve"])  # type: ignore[arg-type]

    def test_none_quote_reserve_raises(self, pool):
        with pytest.raises(ZeroReserveError):
            spot_price(pool["base_reserve"], None)  # type: ignore[arg-type]

    def test_negative_base_reserve_raises(self, pool):
        with pytest.raises(ZeroReserveError):
            spot_price(-1, pool["quote_reserve"])


# ===========================================================================
# §3  quote_buy
# ===========================================================================

class TestQuoteBuy:
    def test_sol_in_pre_fee_matches_fixture(self, fx, pool, fees):
        bf = fx["buy_fixture"]
        _, sol_in_pre_fee = quote_buy(
            pool["base_reserve"], pool["quote_reserve"], bf["base_amount_out"], fees
        )
        assert sol_in_pre_fee == bf["sol_in_pre_fee"]

    def test_sol_in_with_fee_matches_fixture(self, fx, pool, fees):
        bf = fx["buy_fixture"]
        sol_in_with_fee, _ = quote_buy(
            pool["base_reserve"], pool["quote_reserve"], bf["base_amount_out"], fees
        )
        assert sol_in_with_fee == bf["sol_in_with_fee"]

    def test_fee_is_on_top(self, fx, pool, fees):
        """sol_in_with_fee > sol_in_pre_fee (buy fee added on top, not deducted)."""
        bf = fx["buy_fixture"]
        sol_in_with_fee, sol_in_pre_fee = quote_buy(
            pool["base_reserve"], pool["quote_reserve"], bf["base_amount_out"], fees
        )
        assert sol_in_with_fee > sol_in_pre_fee

    def test_zero_fee_no_markup(self, pool):
        zero_fees = AmmFees(lp_fee_bps=0, protocol_fee_bps=0, creator_fee_bps=0)
        base_amount_out = 1_000_000_000
        sol_with, sol_pre = quote_buy(
            pool["base_reserve"], pool["quote_reserve"], base_amount_out, zero_fees
        )
        assert sol_with == sol_pre

    def test_zero_base_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_buy(0, pool["quote_reserve"], 1_000_000, fees)

    def test_zero_quote_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_buy(pool["base_reserve"], 0, 1_000_000, fees)

    def test_none_base_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_buy(None, pool["quote_reserve"], 1_000_000, fees)  # type: ignore[arg-type]

    def test_none_quote_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_buy(pool["base_reserve"], None, 1_000_000, fees)  # type: ignore[arg-type]

    def test_zero_base_amount_out_raises(self, pool, fees):
        with pytest.raises(ValueError):
            quote_buy(pool["base_reserve"], pool["quote_reserve"], 0, fees)

    def test_base_amount_out_equals_reserve_raises(self, pool, fees):
        """Draining the entire pool is disallowed."""
        with pytest.raises(ValueError):
            quote_buy(
                pool["base_reserve"], pool["quote_reserve"],
                pool["base_reserve"], fees,
            )

    def test_base_amount_out_exceeds_reserve_raises(self, pool, fees):
        with pytest.raises(ValueError):
            quote_buy(
                pool["base_reserve"], pool["quote_reserve"],
                pool["base_reserve"] + 1, fees,
            )


# ===========================================================================
# §4  quote_sell
# ===========================================================================

class TestQuoteSell:
    def test_sol_out_pre_fee_matches_fixture(self, fx, pool, fees):
        sf = fx["sell_fixture"]
        _, sol_out_pre_fee = quote_sell(
            pool["base_reserve"], pool["quote_reserve"], sf["base_amount_in"], fees
        )
        assert sol_out_pre_fee == sf["sol_out_pre_fee"]

    def test_sol_out_after_fee_matches_fixture(self, fx, pool, fees):
        sf = fx["sell_fixture"]
        sol_out_after_fee, _ = quote_sell(
            pool["base_reserve"], pool["quote_reserve"], sf["base_amount_in"], fees
        )
        assert sol_out_after_fee == sf["sol_out_after_fee"]

    def test_fee_is_deducted(self, fx, pool, fees):
        """sol_out_after_fee < sol_out_pre_fee (sell fee deducted, not added on top)."""
        sf = fx["sell_fixture"]
        sol_out_after_fee, sol_out_pre_fee = quote_sell(
            pool["base_reserve"], pool["quote_reserve"], sf["base_amount_in"], fees
        )
        assert sol_out_after_fee < sol_out_pre_fee

    def test_zero_fee_no_deduction(self, pool):
        zero_fees = AmmFees(lp_fee_bps=0, protocol_fee_bps=0, creator_fee_bps=0)
        base_amount_in = 1_000_000_000
        sol_after, sol_pre = quote_sell(
            pool["base_reserve"], pool["quote_reserve"], base_amount_in, zero_fees
        )
        assert sol_after == sol_pre

    def test_zero_base_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_sell(0, pool["quote_reserve"], 1_000_000, fees)

    def test_zero_quote_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_sell(pool["base_reserve"], 0, 1_000_000, fees)

    def test_none_base_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_sell(None, pool["quote_reserve"], 1_000_000, fees)  # type: ignore[arg-type]

    def test_none_quote_reserve_raises(self, pool, fees):
        with pytest.raises(ZeroReserveError):
            quote_sell(pool["base_reserve"], None, 1_000_000, fees)  # type: ignore[arg-type]

    def test_zero_base_amount_in_raises(self, pool, fees):
        with pytest.raises(ValueError):
            quote_sell(pool["base_reserve"], pool["quote_reserve"], 0, fees)

    def test_negative_base_amount_in_raises(self, pool, fees):
        with pytest.raises(ValueError):
            quote_sell(pool["base_reserve"], pool["quote_reserve"], -1, fees)

    def test_buy_out_gt_sell_out_same_amount(self, pool, fees):
        """For the same base token amount, buying costs more SOL than selling returns —
        the spread confirms the fee model is asymmetric in the correct direction."""
        amount = 1_000_000_000
        sol_in_with_fee, _ = quote_buy(
            pool["base_reserve"], pool["quote_reserve"], amount, fees
        )
        sol_out_after_fee, _ = quote_sell(
            pool["base_reserve"], pool["quote_reserve"], amount, fees
        )
        assert sol_in_with_fee > sol_out_after_fee


# ===========================================================================
# §5  Fixture integrity — GlobalConfig matches AC-65.2 spec
# ===========================================================================

class TestFixtureIntegrity:
    def test_fixture_file_exists(self):
        assert _FIXTURE_PATH.exists(), f"fixture not found: {_FIXTURE_PATH}"

    def test_lp_fee_is_25_bps(self, fx):
        assert fx["global_config"]["lp_fee_basis_points"] == 25

    def test_protocol_fee_is_5_bps(self, fx):
        assert fx["global_config"]["protocol_fee_basis_points"] == 5

    def test_creator_fee_is_0_bps(self, fx):
        assert fx["global_config"]["coin_creator_fee_basis_points"] == 0

    def test_total_fee_is_30_bps(self, fx):
        gc = fx["global_config"]
        computed = (
            gc["lp_fee_basis_points"]
            + gc["protocol_fee_basis_points"]
            + gc["coin_creator_fee_basis_points"]
        )
        assert computed == 30
        assert gc["total_fee_basis_points"] == 30

    def test_zero_reserve_guard_cases_present(self, fx):
        cases = fx["zero_reserve_guard_cases"]["cases"]
        assert len(cases) >= 5

    def test_zero_reserve_guard_cases_all_raise(self, fx, fees):
        """Every zero-reserve case in the fixture must raise ZeroReserveError."""
        for case in fx["zero_reserve_guard_cases"]["cases"]:
            br = case["base_reserve"]
            qr = case["quote_reserve"]
            with pytest.raises(ZeroReserveError):
                spot_price(br, qr)
