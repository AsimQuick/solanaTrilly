# ---
# module: copytrade.tests.test_curve_ix
# sprint: feat/copy-live-exec-curve-ix, hotfix/preflight-ghostbuy
# story: copy-live-exec, preflight-ghostbuy
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, pytest-django, ast, pathlib, copytrade.curve_ix,
#   copytrade.blockhash_fetcher, trading.tx_signer, trading.execution_core,
#   trading.schemas, copytrade.models, trading.sender
# ---
"""Offline tests for the bonding-curve ix builders + budget kill-switch.

CAPITAL SAFETY:
  - All tests are OFFLINE — no network call, no real RPC, no sendTransaction.
  - Throwaway generated keypair only; never touches TRADING_WALLET_KEY env.
  - The Sender is mocked — never imported or called.
  - trading_enabled defaults False throughout; live path forced only where the
    test explicitly needs it AND a mock Sender is wired.

Test groups
-----------
  [curve_ix] — build_curve_buy_instructions shape + discriminators
  [curve_ix] — build_curve_sell_instructions shape + discriminators
  [curve_ix] — is_mayhem_mode fee recipient routing
  [blockhash] — get_latest_blockhash injectable fetcher
  [budget]    — daily SOL cap enforcement in ExecutionCore.execute_buy
  [budget]    — open live positions cap enforcement
  [full-chain]— execute_buy with MOCK Sender: returns sent=True, mock called once
  [ast-guard] — no real sendTransaction in curve_ix + engine_runtime live path
  [observe]   — existing observe safety: place_buy_order NotImplementedError gate;
                ExecutionCore returned sent=False when trading_enabled=False
"""
from __future__ import annotations

import ast
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from copytrade.blockhash_fetcher import get_latest_blockhash
from copytrade.curve_ix import (
    BUY_DISCRIMINATOR,
    PUMP_FUN_EVENT_AUTHORITY,
    PUMP_FUN_FEE_PROGRAM,
    PUMP_FUN_FEE_RECIPIENT_LEGACY,
    PUMP_FUN_FEE_RECIPIENT_MAYHEM,
    PUMP_FUN_GLOBAL,
    PUMP_FUN_PROGRAM_ID,
    SELL_DISCRIMINATOR,
    build_curve_buy_instructions,
    build_curve_sell_instructions,
)
from trading.pumpswap_ix import b58encode


# ---------------------------------------------------------------------------
# [curve_ix] Pinned program constants — verified vs solanaBilly trading_tasks.py
# (117/128/129). Wrong values => every on-chain tx fails + burns fees. This test
# is the regression guard for the 2026-06-21 capital-review NO-GO findings.
# ---------------------------------------------------------------------------
def test_pump_fun_constants_match_billy_ground_truth():
    assert PUMP_FUN_PROGRAM_ID == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    assert PUMP_FUN_GLOBAL == "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
    assert PUMP_FUN_EVENT_AUTHORITY == "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
    assert PUMP_FUN_FEE_PROGRAM == "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
    # The fee program must be DISTINCT from the bonding-curve program.
    assert PUMP_FUN_FEE_PROGRAM != PUMP_FUN_PROGRAM_ID
    assert PUMP_FUN_FEE_RECIPIENT_LEGACY == "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"
    assert PUMP_FUN_FEE_RECIPIENT_MAYHEM == "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS"

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# A deterministic throwaway mint address (not real).
_MINT = "EzaC4D6kqh6T6N4dT9b1uZ4Pf7Q9rPbqkVwM6n8Pump"
# A deterministic throwaway creator address.
_CREATOR = "CreatorXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
# A stable fake wallet pubkey (32 zero bytes).
_WALLET_BYTES = bytes(32)

# ---------------------------------------------------------------------------
# Throwaway keypair helper (solders; no network)
# ---------------------------------------------------------------------------


def _throwaway_keypair():
    from solders.keypair import Keypair
    return Keypair()


def _dummy_blockhash() -> str:
    from solders.hash import Hash
    return str(Hash.default())


# ===========================================================================
# [curve_ix] build_curve_buy_instructions
# ===========================================================================


def test_buy_instructions_returns_four_no_tip():
    """buy builder returns exactly 4 instructions when jito_tip_lamports=0 (no tip)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
        jito_tip_lamports=0,  # explicitly skip tip for shape test
    )
    assert len(ixs) == 4, f"expected 4 instructions (no tip), got {len(ixs)}"


def test_buy_instructions_returns_five_with_default_tip():
    """buy builder returns exactly 5 instructions with default jito tip (F1)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
        # default jito_tip_lamports=DEFAULT_JITO_TIP_LAMPORTS
    )
    assert len(ixs) == 5, f"expected 5 instructions (4 + jito tip), got {len(ixs)}"


def test_buy_main_ix_program_id():
    """The 4th instruction (the bonding-curve BUY) uses the correct program ID."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_buy = ixs[3]
    assert b58encode(bytes(ix_buy.program_id)) == PUMP_FUN_PROGRAM_ID, (
        f"Expected program {PUMP_FUN_PROGRAM_ID}, got {b58encode(bytes(ix_buy.program_id))}"
    )


def test_buy_discriminator_matches_constant():
    """The BUY instruction data starts with BUY_DISCRIMINATOR."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=None,
        token_amount=500_000,
        max_sol_cost_lamports=5_000_000,
    )
    ix_buy = ixs[3]
    assert bytes(ix_buy.data[:8]) == BUY_DISCRIMINATOR, (
        f"Discriminator mismatch: got {list(ix_buy.data[:8])}"
    )


def test_buy_ix_has_18_accounts():
    """The BUY instruction has exactly 18 accounts (post-2026-04-28 upgrade layout)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_buy = ixs[3]
    assert len(ix_buy.accounts) == 18, (
        f"Expected 18 accounts in buy ix, got {len(ix_buy.accounts)}"
    )


def test_buy_amounts_packed_correctly():
    """token_amount and max_sol_cost_lamports are packed as u64 LE after the discriminator."""
    _tok = 123_456_789
    _sol = 999_888_777
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=_tok,
        max_sol_cost_lamports=_sol,
    )
    ix_buy = ixs[3]
    data = bytes(ix_buy.data)
    tok_unpacked, sol_unpacked = struct.unpack_from("<QQ", data, 8)
    assert tok_unpacked == _tok
    assert sol_unpacked == _sol


def test_buy_raises_on_empty_creator():
    """build_curve_buy_instructions raises ValueError when creator_b58 is empty."""
    with pytest.raises(ValueError, match="creator_b58 is required"):
        build_curve_buy_instructions(
            wallet_pubkey_bytes=_WALLET_BYTES,
            mint_b58=_MINT,
            creator_b58="",
            is_mayhem_mode=False,
            token_amount=1_000_000,
            max_sol_cost_lamports=10_000_000,
        )


# ===========================================================================
# [curve_ix] fee_recipient routing (is_mayhem_mode)
# ===========================================================================


def test_buy_legacy_fee_recipient_when_mayhem_false():
    """is_mayhem_mode=False -> fee_recipient is the LEGACY account (account #2)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_buy = ixs[3]
    # Account index 1 (0-based) = fee_recipient (second account in the 18-account layout)
    fee_recipient_b58 = b58encode(bytes(ix_buy.accounts[1].pubkey))
    assert fee_recipient_b58 == PUMP_FUN_FEE_RECIPIENT_LEGACY, (
        f"Expected LEGACY fee recipient, got {fee_recipient_b58}"
    )


def test_buy_mayhem_fee_recipient_when_mayhem_true():
    """is_mayhem_mode=True -> fee_recipient is the MAYHEM account (account #2)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=True,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_buy = ixs[3]
    fee_recipient_b58 = b58encode(bytes(ix_buy.accounts[1].pubkey))
    assert fee_recipient_b58 == PUMP_FUN_FEE_RECIPIENT_MAYHEM, (
        f"Expected MAYHEM fee recipient, got {fee_recipient_b58}"
    )


def test_buy_none_mayhem_uses_legacy():
    """is_mayhem_mode=None -> treated as LEGACY (falsy)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=None,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_buy = ixs[3]
    fee_recipient_b58 = b58encode(bytes(ix_buy.accounts[1].pubkey))
    assert fee_recipient_b58 == PUMP_FUN_FEE_RECIPIENT_LEGACY


# ===========================================================================
# [curve_ix] build_curve_sell_instructions
# ===========================================================================


def test_sell_instructions_returns_three_non_cashback_no_tip():
    """sell builder returns 3 instructions for non-cashback tokens when jito_tip=0."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
        jito_tip_lamports=0,  # explicitly skip tip for shape test
    )
    assert len(ixs) == 3


def test_sell_instructions_returns_four_non_cashback_with_default_tip():
    """sell builder returns 4 instructions for non-cashback tokens with default tip (F1)."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
        # default jito_tip_lamports=DEFAULT_JITO_TIP_LAMPORTS
    )
    assert len(ixs) == 4


def test_sell_discriminator_matches_constant():
    """The SELL instruction data starts with SELL_DISCRIMINATOR."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=None,
        token_amount=888_888,
        min_sol_output_lamports=0,
    )
    ix_sell = ixs[2]
    assert bytes(ix_sell.data[:8]) == SELL_DISCRIMINATOR, (
        f"Discriminator mismatch: got {list(ix_sell.data[:8])}"
    )


def test_sell_ix_account_count_non_cashback():
    """Non-cashback sell: 16 accounts (14 base + bonding_curve_v2 + breaking_fee)."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
    )
    ix_sell = ixs[2]
    assert len(ix_sell.accounts) == 16, (
        f"Expected 16 accounts, got {len(ix_sell.accounts)}"
    )


def test_sell_ix_account_count_cashback():
    """Cashback sell: 17 accounts (14 base + user_volume_accum + bonding_curve_v2 + breaking_fee)."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=True,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
    )
    ix_sell = ixs[2]
    assert len(ix_sell.accounts) == 17, (
        f"Expected 17 accounts, got {len(ix_sell.accounts)}"
    )


def test_sell_program_id_matches():
    """SELL instruction uses the bonding-curve program."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
    )
    ix_sell = ixs[2]
    assert b58encode(bytes(ix_sell.program_id)) == PUMP_FUN_PROGRAM_ID


def test_sell_raises_on_empty_creator():
    """build_curve_sell_instructions raises ValueError when creator_b58 is empty."""
    with pytest.raises(ValueError, match="creator_b58 is required"):
        build_curve_sell_instructions(
            wallet_pubkey_bytes=_WALLET_BYTES,
            mint_b58=_MINT,
            creator_b58="",
            is_mayhem_mode=False,
            is_cashback_coin=False,
            token_amount=1_000_000,
            min_sol_output_lamports=0,
        )


def test_sell_mayhem_fee_recipient():
    """is_mayhem_mode=True selects MAYHEM fee_recipient on sell."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=True,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
    )
    ix_sell = ixs[2]
    fee_recipient_b58 = b58encode(bytes(ix_sell.accounts[1].pubkey))
    assert fee_recipient_b58 == PUMP_FUN_FEE_RECIPIENT_MAYHEM


# ===========================================================================
# [blockhash] get_latest_blockhash injectable fetcher
# ===========================================================================


def _make_blockhash_fetcher(blockhash: str):
    """Return a fake fetcher that returns a valid getLatestBlockhash response."""
    def _fetcher(rpc_url: str, timeout_s: float):
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "context": {"slot": 123456789},
                "value": {
                    "blockhash": blockhash,
                    "lastValidBlockHeight": 999999999,
                },
            },
        }
    return _fetcher


def test_get_latest_blockhash_offline():
    """get_latest_blockhash returns the blockhash string from an injected fetcher."""
    bh = _dummy_blockhash()
    fetcher = _make_blockhash_fetcher(bh)
    result = get_latest_blockhash("https://example.com", fetcher=fetcher)
    assert result == bh


def test_get_latest_blockhash_error_payload():
    """get_latest_blockhash raises RuntimeError when RPC returns an error."""
    def _err_fetcher(rpc_url, timeout_s):
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}

    with pytest.raises(RuntimeError, match="RPC error"):
        get_latest_blockhash("https://example.com", fetcher=_err_fetcher)


def test_get_latest_blockhash_none_response():
    """get_latest_blockhash raises RuntimeError when the fetcher returns None."""
    def _none_fetcher(rpc_url, timeout_s):
        return None

    with pytest.raises(RuntimeError, match="fetcher returned None"):
        get_latest_blockhash("https://example.com", fetcher=_none_fetcher)


def test_get_latest_blockhash_missing_field():
    """get_latest_blockhash raises RuntimeError when the result shape is unexpected."""
    def _bad_fetcher(rpc_url, timeout_s):
        return {"jsonrpc": "2.0", "id": 1, "result": {"value": {}}}  # no blockhash

    with pytest.raises(RuntimeError):
        get_latest_blockhash("https://example.com", fetcher=_bad_fetcher)


# ===========================================================================
# [full-chain] execute_buy with MOCK Sender: sent=True, mock called once
# ===========================================================================


@pytest.mark.django_db
def test_execute_buy_live_path_mock_sender_sent_true():
    """Full chain: build ixs -> sign -> ExecutionCore.execute_buy with a mock Sender.

    Asserts:
      - execute_buy returns sent=True, mode='live'
      - the mock Sender's send_buy was called exactly once
      - the b64 arg passed to send_buy is non-empty (the signed tx)
    """
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    # Mock Sender returns a fake SendResult
    from trading.sender import SendResult, SimulateResult
    mock_send_result = SendResult(
        signature="fake_sig_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        confirmed=True,
        sol_spent_lamports=10_000_000,
    )
    mock_sender = MagicMock()
    mock_sender.send_buy.return_value = mock_send_result
    # Preflight must pass (ok=True) for execute_buy to proceed to send_buy
    mock_sender.simulate.return_value = SimulateResult(ok=True)

    # TradingConfig with trading_enabled=True; generous caps so they don't fire
    config = TradingConfig(
        trading_enabled=True,
        max_daily_spend_sol=100.0,
        max_open_live_positions=100,
    )
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
        sender=mock_sender,
    )

    # Build a real (but offline) signed tx with a throwaway keypair
    kp = _throwaway_keypair()
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=bytes(kp.pubkey()),
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    from trading.tx_signer import build_signed_tx_b64
    tx_b64 = build_signed_tx_b64(ixs, payer_keypair=kp, recent_blockhash=_dummy_blockhash())

    result = core.execute_buy(tx_b64, sol_amount=0.01)

    assert result.sent is True, f"Expected sent=True, got sent={result.sent}"
    assert result.mode == "live"
    mock_sender.send_buy.assert_called_once()
    # The b64 arg passed to send_buy must be the signed tx (non-empty string)
    call_arg = mock_sender.send_buy.call_args[0][0]
    assert isinstance(call_arg, str) and len(call_arg) > 10


# ===========================================================================
# [budget] daily SOL cap enforcement
# ===========================================================================


@pytest.mark.django_db
def test_execute_buy_refused_at_daily_cap():
    """execute_buy returns sent=False, reason='daily_cap_exceeded' when today's
    live sol_in sum would exceed max_daily_spend_sol."""
    from datetime import datetime, timezone

    from copytrade.models import CopytradePosition
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    # Plant a live position for today that has sol_in = 0.035 SOL.
    # The cap is 0.04, so adding 0.01 more would exceed it.
    now = datetime.now(timezone.utc)
    pos = CopytradePosition(
        cohort_id="test-daily-cap",
        mint=_MINT,
        trigger_wallet=_CREATOR,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_LIVE,
        entry_ts=now,
        entry_price=1.0,
        sol_in=0.035,
    )
    pos.save()

    mock_sender = MagicMock()
    config = TradingConfig(
        trading_enabled=True,
        max_daily_spend_sol=0.04,
        max_open_live_positions=100,
    )
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
        sender=mock_sender,
    )

    result = core.execute_buy("dummy_b64", sol_amount=0.01)

    assert result.sent is False
    assert result.reason == "daily_cap_exceeded"
    mock_sender.send_buy.assert_not_called()


@pytest.mark.django_db
def test_execute_buy_allowed_just_under_daily_cap():
    """execute_buy is NOT refused when the daily cap is not exceeded."""
    from datetime import datetime, timezone

    from copytrade.models import CopytradePosition
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    # Existing spend 0.02, cap 0.04, new spend 0.015 -> total 0.035 < 0.04: allow.
    now = datetime.now(timezone.utc)
    pos = CopytradePosition(
        cohort_id="test-daily-cap-allow",
        mint=_MINT,
        trigger_wallet=_CREATOR,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_LIVE,
        entry_ts=now,
        entry_price=1.0,
        sol_in=0.02,
    )
    pos.save()

    from trading.sender import SendResult, SimulateResult
    mock_send_result = SendResult(
        signature="sig_OK",
        confirmed=True,
        sol_spent_lamports=15_000_000,
    )
    mock_sender = MagicMock()
    mock_sender.send_buy.return_value = mock_send_result
    # Preflight must pass for execute_buy to reach send_buy
    mock_sender.simulate.return_value = SimulateResult(ok=True)

    config = TradingConfig(
        trading_enabled=True,
        max_daily_spend_sol=0.04,
        max_open_live_positions=100,
    )
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
        sender=mock_sender,
    )

    result = core.execute_buy("dummy_b64", sol_amount=0.015)

    assert result.sent is True
    mock_sender.send_buy.assert_called_once()


# ===========================================================================
# [budget] open live positions cap enforcement
# ===========================================================================


@pytest.mark.django_db
def test_execute_buy_refused_at_open_live_cap():
    """execute_buy returns sent=False, reason='open_live_cap_exceeded' when there
    are already max_open_live_positions open LIVE positions."""
    from datetime import datetime, timezone

    from copytrade.models import CopytradePosition
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    now = datetime.now(timezone.utc)
    # Create 2 open live positions — cap is 2.
    for i in range(2):
        CopytradePosition(
            cohort_id="test-open-cap",
            mint=f"Mint{i}XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
            trigger_wallet=_CREATOR,
            status=CopytradePosition.STATUS_OPEN,
            mode=CopytradePosition.MODE_LIVE,
            entry_ts=now,
            entry_price=1.0,
            sol_in=0.001,
        ).save()

    mock_sender = MagicMock()
    config = TradingConfig(
        trading_enabled=True,
        max_daily_spend_sol=100.0,  # daily cap is not the binding constraint here
        max_open_live_positions=2,
    )
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
        sender=mock_sender,
    )

    result = core.execute_buy("dummy_b64", sol_amount=0.001)

    assert result.sent is False
    assert result.reason == "open_live_cap_exceeded"
    mock_sender.send_buy.assert_not_called()


@pytest.mark.django_db
def test_execute_buy_observe_path_never_hits_budget_check():
    """When trading_enabled=False, execute_buy returns sent=False, mode='observe'
    WITHOUT touching the DB (no budget query — the observe gate fires first)."""
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    config = TradingConfig(trading_enabled=False)
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
    )

    result = core.execute_buy("dummy_b64", sol_amount=99.0)

    assert result.sent is False
    assert result.mode == "observe"
    assert result.reason is None


# ===========================================================================
# [observe] existing observe safety gate still green
# ===========================================================================


def test_place_buy_order_raises_not_implemented():
    """copytrade.execution.place_buy_order raises NotImplementedError (observe gate).

    This is the pre-existing observe-safety test.  Must not regress.
    place_buy_order(mint, sol_amount) — 2 positional args.
    """
    from copytrade.execution import place_buy_order

    with pytest.raises(NotImplementedError):
        place_buy_order("SomeMint", 0.1)


@pytest.mark.django_db
def test_execution_core_observe_sent_false():
    """ExecutionCore.execute_buy returns sent=False when trading_enabled=False."""
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    config = TradingConfig(trading_enabled=False)
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=config,
    )
    result = core.execute_buy("any_b64")
    assert result.sent is False
    assert result.mode == "observe"


# ===========================================================================
# [ast-guard] no real sendTransaction in curve_ix + engine_runtime live path
# ===========================================================================

_COPYTRADE_SRC = Path(__file__).resolve().parents[1]

# Files to scan for accidental sendTransaction / requests.post calls.
_LIVE_PATH_FILES = [
    _COPYTRADE_SRC / "curve_ix.py",
    _COPYTRADE_SRC / "engine_runtime.py",
    _COPYTRADE_SRC / "blockhash_fetcher.py",
]


def _find_send_transaction_calls(path: Path) -> list[str]:
    """Return descriptions of any 'sendTransaction' string literal in the file.

    We check for the string literal "sendTransaction" appearing in the AST as a
    constant — that's the JSON-RPC method name used by the real Sender.  It must
    NOT appear in curve_ix.py, engine_runtime.py, or blockhash_fetcher.py
    (these modules are pure builders; only trading.sender may call the chain).
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "sendTransaction":
            findings.append(
                f"{path.name}: literal 'sendTransaction' at line {node.lineno}"
            )
    return findings


def test_no_send_transaction_in_curve_ix_or_engine_runtime():
    """curve_ix.py, engine_runtime.py, blockhash_fetcher.py must NOT contain
    'sendTransaction' — only trading.sender is allowed to call the chain.

    This AST guard ensures no accidental RPC send was introduced when wiring
    the live path.
    """
    violations: list[str] = []
    for path in _LIVE_PATH_FILES:
        if path.exists():
            violations.extend(_find_send_transaction_calls(path))

    assert not violations, (
        "Accidental sendTransaction call found in live-path helpers "
        "(curve_ix / engine_runtime / blockhash_fetcher). "
        "Only trading.sender may call the chain.\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_ast_guard_scan_is_non_degenerate():
    """At least one of the target files exists (degenerate-pass guard)."""
    found = [p for p in _LIVE_PATH_FILES if p.exists()]
    assert found, "No live-path files found — update _LIVE_PATH_FILES if paths changed."
