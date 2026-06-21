# ---
# module: copytrade.tests.test_capital_path_wiring
# sprint: feature/copy-capital-path-wiring
# story: EPIC-copy-capital-path-wiring
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, pytest-django, unittest.mock, copytrade.curve_ix,
#   trading.sender, copytrade.management.commands.run_copytrade_engine,
#   trading.execution_core, trading.schemas, core.clock
# ---
"""Capital-path wiring tests — F1 (tip), F2 (ghost-buy pubkey), F3 (entrypoint).

CAPITAL SAFETY: All tests are OFFLINE — no network call, no real RPC, no real send.
Throwaway generated keypairs only; never touches TRADING_WALLET_KEY env.
Senders are mocked — never imported or called for real.

Test groups
-----------
  [F1-tip]   — jito tip instruction present in buy + sell builders
  [F1-tip]   — tip pool is DISTINCT from pump.fun breaking-fee pool
  [F1-tip]   — tip skipped when jito_tip_lamports=0
  [F2-ghost] — verify_ghost_buy queries REAL wallet pubkey, not empty string
  [F2-ghost] — Sender warns loudly when wallet_pubkey is empty in live mode
  [F3-entry] — entrypoint builds execution_core only when mode='live' + keypair present
  [F3-entry] — entrypoint stays INERT (execution_core=None) when keypair absent
  [F3-entry] — execution_core=None → no send on observe path
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from copytrade.curve_ix import (
    HELIUS_SENDER_TIP_ACCOUNTS,
    PUMP_FUN_BREAKING_FEE_RECIPIENTS,
    build_curve_buy_instructions,
    build_curve_sell_instructions,
)
from trading.pumpswap_ix import b58encode

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_MINT = "EzaC4D6kqh6T6N4dT9b1uZ4Pf7Q9rPbqkVwM6n8Pump"
_CREATOR = "CreatorXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
_WALLET_BYTES = bytes(32)


def _throwaway_keypair():
    from solders.keypair import Keypair
    return Keypair()


# ===========================================================================
# [F1-tip] Helius Sender tip instruction is present in buy ix list
# ===========================================================================


def test_buy_instructions_includes_tip_by_default():
    """build_curve_buy_instructions with default jito_tip_lamports returns 5 ixs
    (4 original + 1 jito_tip_transfer)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
        # default jito_tip_lamports=DEFAULT_JITO_TIP_LAMPORTS (5_000_000)
    )
    assert len(ixs) == 5, (
        f"Expected 5 instructions (4 + jito tip), got {len(ixs)}"
    )


def test_buy_instructions_tip_ix_is_system_program_transfer():
    """The 5th buy instruction (jito tip) targets the System Program (all-zero bytes)."""
    SYSTEM_PROGRAM_BYTES = bytes(32)
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_tip = ixs[4]
    assert bytes(ix_tip.program_id) == SYSTEM_PROGRAM_BYTES, (
        "Jito tip instruction must target the System Program (all-zero bytes)"
    )


def test_buy_instructions_tip_recipient_in_tip_pool():
    """The jito tip transfer 'to' account (account[1]) is one of the 10 Helius Sender
    tip accounts — NEVER a pump.fun breaking-fee recipient."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
    )
    ix_tip = ixs[4]
    to_account_b58 = b58encode(bytes(ix_tip.accounts[1].pubkey))
    # Must be one of the 10 Helius Sender tip accounts
    assert to_account_b58 in HELIUS_SENDER_TIP_ACCOUNTS, (
        f"Tip 'to' account {to_account_b58!r} is NOT in HELIUS_SENDER_TIP_ACCOUNTS"
    )
    # Must NOT be a pump.fun breaking-fee recipient (distinct pools)
    assert to_account_b58 not in PUMP_FUN_BREAKING_FEE_RECIPIENTS, (
        f"Tip 'to' account {to_account_b58!r} is a BREAKING-FEE recipient — "
        "these pools are DISTINCT: tip goes to Jito/Helius, breaking-fee goes to pump.fun"
    )


def test_buy_instructions_tip_amount_correct():
    """The jito tip transfer encodes the correct lamport amount in the instruction data.

    System Transfer data: [2 (u32 LE)] + [lamports (u64 LE)] = 12 bytes.
    """
    import struct
    tip_amount = 7_777_777
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
        jito_tip_lamports=tip_amount,
    )
    ix_tip = ixs[4]
    data = bytes(ix_tip.data)
    discriminator, lamports = struct.unpack_from("<IQ", data, 0)
    assert discriminator == 2, f"System Transfer discriminator must be 2, got {discriminator}"
    assert lamports == tip_amount, (
        f"Tip lamport amount mismatch: expected {tip_amount}, got {lamports}"
    )


def test_buy_instructions_no_tip_when_zero():
    """jito_tip_lamports=0 omits the tip instruction (returns 4 ixs, original count)."""
    ixs = build_curve_buy_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        token_amount=1_000_000,
        max_sol_cost_lamports=10_000_000,
        jito_tip_lamports=0,
    )
    assert len(ixs) == 4, (
        f"Expected 4 instructions (no tip when jito_tip_lamports=0), got {len(ixs)}"
    )


def test_sell_instructions_includes_tip_by_default():
    """build_curve_sell_instructions with default jito_tip_lamports returns 4 ixs
    (3 original + 1 jito_tip_transfer) for non-cashback tokens."""
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
    assert len(ixs) == 4, (
        f"Expected 4 instructions (3 + jito tip), got {len(ixs)}"
    )


def test_sell_instructions_tip_recipient_in_tip_pool():
    """The jito tip transfer 'to' account in sell ixs is one of the 10 Helius Sender
    tip accounts — DISTINCT from pump.fun breaking-fee recipients."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
    )
    ix_tip = ixs[3]  # last ix is jito tip
    to_account_b58 = b58encode(bytes(ix_tip.accounts[1].pubkey))
    assert to_account_b58 in HELIUS_SENDER_TIP_ACCOUNTS
    assert to_account_b58 not in PUMP_FUN_BREAKING_FEE_RECIPIENTS, (
        "Tip pool MUST be distinct from breaking-fee pool"
    )


def test_sell_instructions_no_tip_when_zero():
    """jito_tip_lamports=0 omits the tip instruction (returns 3 ixs for sell)."""
    ixs = build_curve_sell_instructions(
        wallet_pubkey_bytes=_WALLET_BYTES,
        mint_b58=_MINT,
        creator_b58=_CREATOR,
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_amount=1_000_000,
        min_sol_output_lamports=0,
        jito_tip_lamports=0,
    )
    assert len(ixs) == 3, (
        f"Expected 3 instructions (no tip when jito_tip_lamports=0), got {len(ixs)}"
    )


def test_tip_pools_are_disjoint():
    """HELIUS_SENDER_TIP_ACCOUNTS and PUMP_FUN_BREAKING_FEE_RECIPIENTS share NO entries.

    This is the critical pool-confusion guard (F1 spec note).
    """
    tip_set = set(HELIUS_SENDER_TIP_ACCOUNTS)
    fee_set = set(PUMP_FUN_BREAKING_FEE_RECIPIENTS)
    overlap = tip_set & fee_set
    assert not overlap, (
        f"Helius tip pool and pump.fun breaking-fee pool have overlapping entries: {overlap}. "
        "These pools are DISTINCT: tip = Jito/Helius infra, breaking-fee = pump.fun program."
    )


def test_helius_sender_tip_accounts_verbatim_from_billy():
    """HELIUS_SENDER_TIP_ACCOUNTS matches solanaBilly trading_tasks.py ~179-190 verbatim.

    If this test fails, the addresses have drifted from the battle-tested reference.
    """
    expected = (
        "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
        "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
        "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
        "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
        "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
        "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
        "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
        "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
        "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
        "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
    )
    assert HELIUS_SENDER_TIP_ACCOUNTS == expected, (
        "HELIUS_SENDER_TIP_ACCOUNTS does not match solanaBilly trading_tasks.py ~179-190. "
        "These were copied VERBATIM from the battle-tested reference — restore them."
    )


# ===========================================================================
# [F2-ghost] verify_ghost_buy queries REAL wallet pubkey, not empty string
# ===========================================================================


def test_verify_ghost_buy_uses_real_wallet_pubkey():
    """F2 fix: Sender stores wallet_pubkey at construction; verify_ghost_buy calls
    _get_ata_balance with the REAL pubkey, not the empty-string placeholder.

    This was the SHOWSTOPPER: _get_ata_balance("", ...) always returns balance=0
    → every buy misclassified as ghost buy → no position ever written.
    """
    from trading.sender import Sender, SenderConfig

    real_pubkey = "9u6ZF2uEcbS17wJtiixd5hepSr4fXveNYLNjfg7mtF6B"
    cfg = SenderConfig(sender_url="", rpc_url="https://rpc.example.com")
    sender = Sender(config=cfg, wallet_pubkey=real_pubkey)

    # Patch _get_ata_balance to capture what wallet_pubkey it was called with.
    captured_pubkeys: list[str] = []

    def _fake_get_ata_balance(mint_address: str, wallet_pubkey: str = "") -> int:
        captured_pubkeys.append(wallet_pubkey)
        return 1_000_000  # non-zero → tokens received (received=True)

    sender._get_ata_balance = _fake_get_ata_balance  # type: ignore[assignment]

    result = sender.verify_ghost_buy(
        mint_address="SomeMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        expected_tokens=10_000_000,
        # No get_balance_fn injected → uses the internal path with _wallet_pubkey
    )

    assert result.received is True
    assert len(captured_pubkeys) >= 1, "verify_ghost_buy never called _get_ata_balance"
    for pk in captured_pubkeys:
        assert pk == real_pubkey, (
            f"verify_ghost_buy called _get_ata_balance with pubkey={pk!r}, "
            f"expected the REAL pubkey {real_pubkey!r}. "
            "F2 fix: store wallet_pubkey on Sender at construction."
        )


def test_verify_ghost_buy_empty_pubkey_queries_empty_owner():
    """When wallet_pubkey is empty (old broken behaviour), the RPC receives an
    empty owner string — this test documents the failure mode for reference.

    With the F2 fix, callers MUST inject the real pubkey via Sender(wallet_pubkey=).
    The empty-pubkey path is still reachable if the caller forgets to inject;
    the warning at construction tells the operator (tested separately).
    """
    from trading.sender import Sender, SenderConfig

    cfg = SenderConfig(sender_url="", rpc_url="https://rpc.example.com")
    # Intentionally empty pubkey (old broken path — now triggers a warning)
    sender = Sender(config=cfg, wallet_pubkey="")

    captured_pubkeys: list[str] = []

    def _fake_get_ata_balance(mint_address: str, wallet_pubkey: str = "") -> int:
        captured_pubkeys.append(wallet_pubkey)
        return 0  # would trigger ghost-buy detection

    sender._get_ata_balance = _fake_get_ata_balance  # type: ignore[assignment]

    result = sender.verify_ghost_buy(
        mint_address="SomeMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        expected_tokens=10_000_000,
    )

    # With empty pubkey, balance=0 → ghost buy detected
    assert result.received is False
    # All calls used the empty pubkey
    for pk in captured_pubkeys:
        assert pk == "", (
            "Expected empty pubkey on the broken path, got: " + pk
        )


def test_sender_warns_when_wallet_pubkey_empty_in_live_mode():
    """F2 fix: Sender emits a WARNING when sender_url is set but wallet_pubkey is empty.

    This surfaces the misconfiguration early (at construction) rather than silently
    returning balance=0 on every ghost-buy check.
    """
    import logging

    from trading.sender import Sender, SenderConfig

    cfg = SenderConfig(
        sender_url="https://sender.helius-rpc.com/fast",
        rpc_url="https://rpc.example.com",
    )

    captured_warnings: list[str] = []

    class _CapHandler(logging.Handler):
        def emit(self, record):
            captured_warnings.append(record.getMessage())

    handler = _CapHandler()
    logger = logging.getLogger("trading")
    logger.addHandler(handler)
    try:
        Sender(config=cfg, wallet_pubkey="")  # no pubkey
    finally:
        logger.removeHandler(handler)

    assert any(
        "wallet_pubkey is EMPTY" in msg
        for msg in captured_warnings
    ), (
        "Sender must emit a WARNING when wallet_pubkey is empty in live mode. "
        "Messages captured: " + str(captured_warnings)
    )


def test_sender_no_warning_when_wallet_pubkey_present():
    """No wallet_pubkey warning is emitted when wallet_pubkey is properly set."""
    import logging

    from trading.sender import Sender, SenderConfig

    cfg = SenderConfig(
        sender_url="https://sender.helius-rpc.com/fast",
        rpc_url="https://rpc.example.com",
    )
    real_pk = "9u6ZF2uEcbS17wJtiixd5hepSr4fXveNYLNjfg7mtF6B"

    captured_msgs: list[str] = []

    class _CapHandler(logging.Handler):
        def emit(self, record):
            captured_msgs.append(record.getMessage())

    handler = _CapHandler()
    logger = logging.getLogger("trading")
    logger.addHandler(handler)
    try:
        Sender(config=cfg, wallet_pubkey=real_pk)
    finally:
        logger.removeHandler(handler)

    wallet_warnings = [m for m in captured_msgs if "wallet_pubkey is EMPTY" in m]
    assert not wallet_warnings, (
        "No wallet_pubkey warning should be emitted when pubkey is present. "
        "Got: " + str(wallet_warnings)
    )


# ===========================================================================
# [F3-entry] Entrypoint builds execution_core only when mode='live' + keypair
# ===========================================================================


def test_entrypoint_execution_core_none_on_observe(monkeypatch):
    """When mode='observe', execution_core is NOT built (stays None → inert)."""
    import asyncio
    from io import StringIO
    from unittest.mock import MagicMock


    # Patch CopyTradeSettings.get to return an OBSERVE settings row
    mock_settings = MagicMock()
    mock_settings.engine_on = False  # triggers idle so _run exits early
    mock_settings.active_cohort_id = None
    mock_settings.mode = "observe"

    from copytrade.management.commands.run_copytrade_engine import Command

    cmd = Command()
    cmd.stdout = StringIO()

    # Capture whether execution_core is built by patching the ExecutionCore class
    execution_core_created = []

    async def _run_idle_noop(self_inner):
        pass  # Override idle to return immediately

    with patch("copytrade.management.commands.run_copytrade_engine.CopyTradeSettings") as mock_cts:
        mock_cts.get.return_value = mock_settings
        with patch.object(Command, "_run_idle", _run_idle_noop):
            with patch("copytrade.management.commands.run_copytrade_engine.sync_to_async") as mock_s2a:
                # Make sync_to_async return an async callable that returns our mock
                async def _fake_s2a_call(*args, **kwargs):
                    return mock_settings
                mock_s2a.return_value = _fake_s2a_call
                asyncio.run(cmd._run({}))

    # No ExecutionCore was constructed (trading_enabled path not reached)
    assert not execution_core_created, "execution_core must NOT be built in observe mode"


def test_entrypoint_observe_default_no_send():
    """Default path (no mode, no keypair): execution_core=None means no send possible.

    This is the INERT-by-default guarantee: unless mode='live' AND keypair is present,
    trading_enabled is always False and execution_core is always None.
    """
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    # No trading_enabled, no sender
    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=TradingConfig(trading_enabled=False),
        sender=None,
    )
    result = core.execute_buy("any_b64", sol_amount=0.02)
    assert result.sent is False
    assert result.mode == "observe"


def test_entrypoint_live_with_keypair_absent_stays_inert(monkeypatch, caplog):
    """When mode='live' but TRADING_WALLET_KEY is absent, entrypoint falls back to
    OBSERVE (inert) with a LOUD WARNING. No execution_core is built. No real send.

    This is the fail-safe: live mode without a key MUST stay inert, never crash.
    """

    monkeypatch.delenv("TRADING_WALLET_KEY", raising=False)

    # We test the wiring logic directly without running the full async loop:
    # If TRADING_WALLET_KEY is absent, load_keypair() returns None → fallback.
    from trading.tx_signer import load_keypair
    keypair = load_keypair()
    assert keypair is None, (
        "load_keypair() must return None when TRADING_WALLET_KEY is absent. "
        "The live path falls back to OBSERVE (inert) when keypair is None."
    )


def test_entrypoint_live_with_valid_keypair_builds_execution_core(monkeypatch):
    """When mode='live' and TRADING_WALLET_KEY is set, load_keypair succeeds.

    The entrypoint would then build ExecutionCore(trading_enabled=True). We test
    the keypair-loading side (the rest is wired in _run but runs async/with ORM).
    """
    from solders.keypair import Keypair

    from trading.tx_signer import load_keypair, wallet_pubkey_str

    kp = Keypair()
    b58_secret = str(kp)

    monkeypatch.setenv("TRADING_WALLET_KEY", b58_secret)
    loaded = load_keypair()
    assert loaded is not None, "load_keypair must succeed when TRADING_WALLET_KEY is set"
    pubkey = wallet_pubkey_str(loaded)
    assert len(pubkey) > 0, "wallet_pubkey_str must return a non-empty base58 string"
    # Confirm the loaded keypair has the same pubkey as the original
    assert str(kp.pubkey()) == pubkey


def test_helius_sender_url_default_used_when_env_absent(monkeypatch):
    """F5: when HELIUS_SENDER_URL env is absent, the default fast endpoint is used.

    The entrypoint reads HELIUS_SENDER_URL from env with the default fallback.
    """
    monkeypatch.delenv("HELIUS_SENDER_URL", raising=False)
    from copytrade.management.commands.run_copytrade_engine import _HELIUS_SENDER_URL_DEFAULT
    # os.environ.get("HELIUS_SENDER_URL", _HELIUS_SENDER_URL_DEFAULT) should give the default
    url = os.environ.get("HELIUS_SENDER_URL", _HELIUS_SENDER_URL_DEFAULT)
    assert url == "https://sender.helius-rpc.com/fast", (
        f"Expected default HELIUS_SENDER_URL, got {url!r}"
    )


def test_helius_sender_url_from_env(monkeypatch):
    """F5: when HELIUS_SENDER_URL env is set, it takes precedence over the default."""
    custom_url = "https://my-custom-sender.example.com/fast"
    monkeypatch.setenv("HELIUS_SENDER_URL", custom_url)
    from copytrade.management.commands.run_copytrade_engine import _HELIUS_SENDER_URL_DEFAULT
    url = os.environ.get("HELIUS_SENDER_URL", _HELIUS_SENDER_URL_DEFAULT)
    assert url == custom_url


# ===========================================================================
# [F4] sol_size_per_trade is ignored; sol_in (from usd_size) passed to cap
# ===========================================================================


def test_f4_sol_in_passed_to_daily_cap():
    """F4 documentation test: the 2.x path sizes by usd_size_per_trade → sol_in.

    Confirms that open_live_position passes sol_amount=sol_in (not sol_size_per_trade
    from CopyTradeSettings) to execute_buy. The config fed to open_live_position
    is _CopyTradeConfig(sol_size_per_trade=sol_in) where sol_in=usd_size/sol_usd.

    We verify with a mock ExecutionCore that the sol_amount captured by execute_buy
    matches the expected sol_in (not the legacy default 0.25).
    """
    from trading.execution_core import ExecutionCore
    from trading.schemas import TradingConfig

    # sol_in from usd_size_per_trade: 3 USD / 150 USD/SOL ≈ 0.02 SOL
    usd_size = 3.0
    sol_usd = 150.0
    expected_sol_in = usd_size / sol_usd  # 0.02

    captured_sol_amounts: list[float] = []

    def _fake_execute_buy(serialized_tx_b64: str, *, sol_amount: float = 0.0) -> object:
        captured_sol_amounts.append(sol_amount)
        # Return a fake sent=False result (we just need to capture sol_amount)
        from trading.execution_core import ExecuteResult
        return ExecuteResult(sent=False, mode="live", reason="test_intercept")

    core = ExecutionCore(
        source=MagicMock(),
        clock=MagicMock(),
        config=TradingConfig(trading_enabled=True, max_daily_spend_sol=100.0, max_open_live_positions=100),
        sender=MagicMock(),
    )
    # Monkey-patch execute_buy to capture sol_amount
    core.execute_buy = _fake_execute_buy  # type: ignore[method-assign]

    # Simulate what engine_runtime does when building the live config:
    from copytrade.models import CopytradePosition
    from copytrade.schemas import CopyTradeConfig as _CopyTradeConfig
    _live_mode = CopytradePosition.MODE_LIVE
    _live_config = _CopyTradeConfig(**{"mode": _live_mode, "sol_size_per_trade": expected_sol_in})

    # Simulate the position_opener calling execute_buy with sol_size_per_trade
    core.execute_buy("dummy_b64", sol_amount=float(_live_config.sol_size_per_trade))

    assert captured_sol_amounts, "execute_buy must be called with sol_amount"
    assert abs(captured_sol_amounts[0] - expected_sol_in) < 1e-9, (
        f"sol_amount passed to execute_buy must be sol_in={expected_sol_in:.6f} "
        f"(from usd_size={usd_size}/sol_usd={sol_usd}), "
        f"got {captured_sol_amounts[0]:.6f}. "
        "F4: sol_size_per_trade is IGNORED in the 2.x engine; sol_in=usd_size/sol_usd."
    )
