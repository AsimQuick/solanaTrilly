# ---
# module: copytrade.tests.test_buy_trigger_ac601
# sprint: sprint-12
# story: US-60 AC-60.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, copytrade.buy_trigger, copytrade.wallet_consumer
# ---
"""AC-60.1: BUY-COPY trigger predicate tests.

All tests are deterministic and offline (zero firehose).  They verify:

  Positive case:
    1. A valid pre-graduation pump.fun curve buy fires the trigger.

  Explicit REJECT cases (SPEC §3 — each produces NO trigger):
    2. A SELL → rejected (condition 1: not a buyer).
    3. A TRANSFER → rejected (condition 1: not a buyer).
    4. A non-pump.fun token buy → rejected (condition 2: not pump.fun).
    5. A post-graduation buy (PumpSwap AMM) → rejected (condition 3: graduated).
    6. A post-graduation buy (graduated=True flag) → rejected (condition 3).

  Condition isolation:
    7. is_buyer() accepts "buy"; rejects "sell", "transfer", "unknown".
    8. is_pumpfun_token() accepts mint-suffix and program-id; rejects others.
    9. is_on_bonding_curve() rejects PumpSwap, Raydium, graduated=True; accepts curve.
"""
from datetime import datetime, timezone
from typing import Any

import pytest

from copytrade.buy_trigger import (
    PUMPFUN_BONDING_CURVE_PROGRAM,
    PUMPSWAP_AMM,
    RAYDIUM_AMM_PROGRAMS,
    is_buyer,
    is_on_bonding_curve,
    is_pumpfun_token,
    should_copy_buy,
)
from copytrade.wallet_consumer import WalletTxEvent

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

# A pump.fun token mint (ends with the "pump" suffix).
PUMPFUN_MINT = "Token1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"

# A non-pump.fun token mint (no suffix, unrelated program).
NON_PUMPFUN_MINT = "Token2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

_WATCHED_WALLET = "WatchedWallet111111111111111111111111111111"


def _make_event(
    tx_type: str = "buy",
    mint: str = PUMPFUN_MINT,
    program: str = PUMPFUN_BONDING_CURVE_PROGRAM,
    graduated: bool = False,
    extra_raw: dict[str, Any] | None = None,
) -> WalletTxEvent:
    """Build a WalletTxEvent with controllable fields for offline testing."""
    raw: dict[str, Any] = {"program": program}
    if graduated:
        raw["graduated"] = True
    if extra_raw:
        raw.update(extra_raw)
    return WalletTxEvent(
        wallet=_WATCHED_WALLET,
        mint=mint,
        tx_signature="testsig_ac601",
        tx_type=tx_type,
        sol_amount=0.25,
        token_amount=1_000_000.0,
        timestamp=_TS,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# 1. Positive case: valid pre-graduation pump.fun curve buy triggers
# ---------------------------------------------------------------------------


def test_valid_pregraduation_curve_buy_triggers():
    """A buy on a pump.fun token still on the bonding curve must trigger."""
    event = _make_event(
        tx_type="buy",
        mint=PUMPFUN_MINT,
        program=PUMPFUN_BONDING_CURVE_PROGRAM,
    )
    assert should_copy_buy(event) is True


# ---------------------------------------------------------------------------
# 2. REJECT: sell
# ---------------------------------------------------------------------------


def test_reject_sell():
    """A SELL from a watched wallet must NOT trigger (condition 1: not a buyer)."""
    event = _make_event(tx_type="sell")
    assert should_copy_buy(event) is False


# ---------------------------------------------------------------------------
# 3. REJECT: transfer
# ---------------------------------------------------------------------------


def test_reject_transfer():
    """A TRANSFER must NOT trigger (condition 1: not a buyer)."""
    event = _make_event(tx_type="transfer")
    assert should_copy_buy(event) is False


# ---------------------------------------------------------------------------
# 4. REJECT: non-pump.fun token
# ---------------------------------------------------------------------------


def test_reject_non_pumpfun_token():
    """A buy of a non-pump.fun token must NOT trigger (condition 2 fails)."""
    event = _make_event(
        tx_type="buy",
        mint=NON_PUMPFUN_MINT,
        program="SomeOtherProgramAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    assert should_copy_buy(event) is False


# ---------------------------------------------------------------------------
# 5. REJECT: post-graduation buy via PumpSwap AMM
# ---------------------------------------------------------------------------


def test_reject_post_graduation_buy_pumpswap():
    """A buy on an already-graduated token (PumpSwap AMM) must NOT trigger.

    The mint still looks like pump.fun (condition 2 passes) but the swap program
    is PumpSwap — the token has migrated off the bonding curve (condition 3 fails).
    This is the key correctness-critical edge case from SPEC §3.
    """
    event = _make_event(
        tx_type="buy",
        mint=PUMPFUN_MINT,
        program=PUMPSWAP_AMM,
    )
    assert should_copy_buy(event) is False


# ---------------------------------------------------------------------------
# 6. REJECT: post-graduation buy via graduated=True flag
# ---------------------------------------------------------------------------


def test_reject_post_graduation_buy_graduated_flag():
    """A buy with graduated=True in the raw event must NOT trigger (condition 3 fails)."""
    event = _make_event(
        tx_type="buy",
        mint=PUMPFUN_MINT,
        program=PUMPFUN_BONDING_CURVE_PROGRAM,
        graduated=True,
    )
    assert should_copy_buy(event) is False


# ---------------------------------------------------------------------------
# 7. is_buyer() — condition 1 isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tx_type,expected",
    [
        ("buy", True),
        ("sell", False),
        ("transfer", False),
        ("unknown", False),
    ],
)
def test_is_buyer_accepts_only_buy(tx_type: str, expected: bool) -> None:
    """is_buyer() returns True only for tx_type='buy'."""
    event = _make_event(tx_type=tx_type)
    assert is_buyer(event) is expected


# ---------------------------------------------------------------------------
# 8. is_pumpfun_token() — condition 2 isolation
# ---------------------------------------------------------------------------


def test_is_pumpfun_token_accepts_mint_suffix():
    """is_pumpfun_token() returns True when mint ends with 'pump'."""
    event = _make_event(mint=PUMPFUN_MINT, program="SomeOtherProgram")
    assert is_pumpfun_token(event) is True


def test_is_pumpfun_token_accepts_bonding_curve_program():
    """is_pumpfun_token() returns True when raw program is the bonding-curve program."""
    event = _make_event(mint=NON_PUMPFUN_MINT, program=PUMPFUN_BONDING_CURVE_PROGRAM)
    assert is_pumpfun_token(event) is True


def test_is_pumpfun_token_rejects_non_pumpfun():
    """is_pumpfun_token() returns False when neither mint suffix nor program matches."""
    event = _make_event(
        mint=NON_PUMPFUN_MINT,
        program="SomeCompletelyUnrelatedProgram11111111111",
    )
    assert is_pumpfun_token(event) is False


def test_is_pumpfun_token_mint_suffix_case_insensitive():
    """is_pumpfun_token() accepts mints ending in 'pump' regardless of case."""
    event_lower = _make_event(mint="Token1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump")
    event_upper = _make_event(mint="Token2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPUMP")
    assert is_pumpfun_token(event_lower) is True
    assert is_pumpfun_token(event_upper) is True


# ---------------------------------------------------------------------------
# 9. is_on_bonding_curve() — condition 3 isolation
# ---------------------------------------------------------------------------


def test_is_on_bonding_curve_rejects_pumpswap():
    """is_on_bonding_curve() returns False when program is the PumpSwap AMM."""
    event = _make_event(program=PUMPSWAP_AMM)
    assert is_on_bonding_curve(event) is False


def test_is_on_bonding_curve_rejects_raydium():
    """is_on_bonding_curve() returns False when program is a Raydium AMM."""
    raydium_program = next(iter(RAYDIUM_AMM_PROGRAMS))
    event = _make_event(program=raydium_program)
    assert is_on_bonding_curve(event) is False


def test_is_on_bonding_curve_rejects_graduated_flag():
    """is_on_bonding_curve() returns False when raw event has graduated=True."""
    event = _make_event(program=PUMPFUN_BONDING_CURVE_PROGRAM, graduated=True)
    assert is_on_bonding_curve(event) is False


def test_is_on_bonding_curve_accepts_curve_program():
    """is_on_bonding_curve() returns True when program is the bonding-curve program."""
    event = _make_event(program=PUMPFUN_BONDING_CURVE_PROGRAM)
    assert is_on_bonding_curve(event) is True
