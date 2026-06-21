# ---
# module: copytrade.curve_ix
# sprint: feat/copy-live-exec-curve-ix, feature/copy-capital-path-wiring
# story: copy-live-exec, EPIC-copy-capital-path-wiring
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: copytrade.curve_price, trading.pumpswap_ix, struct, random
# ---
"""Bonding-curve (6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P) instruction builders.

Ported verbatim from solanaBilly app/tasks/trading_tasks.py:
  - _build_buy_transaction (line ~2860)
  - _execute_bonding_curve_sell (line ~4100)

Produces vendored Instruction objects (same dataclass as trading.pumpswap_ix)
so build_signed_tx_b64 (trading.tx_signer) can consume them directly.

CAPITAL SAFETY:
  - NO network I/O — pure derivation + struct packing.
  - NO real send of any kind — callers pass the result to execution_core
    which gates on trading_enabled=False.
  - Offline-testable end-to-end (see copytrade/tests/test_curve_ix.py).

Program ID:
  Bonding curve: 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
  (NOT the post-grad PumpSwap AMM pAMMBay6...)

Account layout (18 accounts for buy, 14-17 for sell) is post-2026-04-28
upgrade. The trailing breaking_fee_recipient is REQUIRED or the program
rejects every tx with Anchor 6062 BuybackFeeRecipientMissing.
"""

from __future__ import annotations

import random
import struct
from typing import Optional

from trading.pumpswap_ix import (
    AccountMeta,
    Instruction,
    b58decode,
    find_program_address,
)

# ---------------------------------------------------------------------------
# Program constants
# ---------------------------------------------------------------------------

#: pump.fun bonding-curve program (PRE-grad, NOT the post-grad AMM).
PUMP_FUN_PROGRAM_ID: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_FUN_PROGRAM_BYTES: bytes = b58decode(PUMP_FUN_PROGRAM_ID)

#: Legacy fee_recipient (pre-mayhem tokens).
PUMP_FUN_FEE_RECIPIENT_LEGACY: str = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"

#: Mayhem-mode fee_recipient (is_mayhem_mode=True tokens).
PUMP_FUN_FEE_RECIPIENT_MAYHEM: str = "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS"

#: Known SPL programs
SPL_TOKEN_PROGRAM: str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SPL_TOKEN_2022_PROGRAM: str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SPL_ATA_PROGRAM: str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM_ID: str = "11111111111111111111111111111111"
# Ground-truth pump.fun bonding-curve constants — verified against solanaBilly
# app/tasks/trading_tasks.py:117/128/129 (the proven live reference). Getting any
# of these wrong makes EVERY buy/sell tx fail on-chain (Anchor constraint) and
# burns fees. Pinned + asserted in test_curve_ix.py so they cannot regress.
PUMP_FUN_GLOBAL: str = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
PUMP_FUN_EVENT_AUTHORITY: str = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
#: The fee program is a DISTINCT program (pfee...), NOT the bonding-curve program.
#: The fee_config PDA is derived UNDER this program (see _derive_fee_config).
PUMP_FUN_FEE_PROGRAM: str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
PUMP_FUN_FEE_PROGRAM_BYTES: bytes = b58decode(PUMP_FUN_FEE_PROGRAM)

#: Anchor discriminators (sha256("global:<fn>")[:8]) — pinned ground truth.
BUY_DISCRIMINATOR: bytes = bytes([102, 6, 61, 18, 1, 218, 235, 234])
SELL_DISCRIMINATOR: bytes = bytes([51, 230, 133, 164, 1, 127, 131, 173])

#: track_volume Option<bool>=Some(true) — required trailing bytes (post-upgrade).
TRACK_VOLUME_SOME_TRUE: bytes = bytes([1, 1])

# ---------------------------------------------------------------------------
# Breaking-fee recipient pool (post-2026-04-28 upgrade)
# ---------------------------------------------------------------------------

#: 8-account pool. Pick one at random per tx to spread write-lock contention.
PUMP_FUN_BREAKING_FEE_RECIPIENTS: list[str] = [
    "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
    "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
    "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
    "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
    "5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6",
    "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL",
    "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD",
    "A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW",
]


def _pick_breaking_fee_recipient() -> str:
    """Pick one of the 8 breaking-fee recipients at random (write-lock spread)."""
    return random.choice(PUMP_FUN_BREAKING_FEE_RECIPIENTS)


# ---------------------------------------------------------------------------
# Helius Sender tip-account rotation (F1 — capital path wiring)
# ---------------------------------------------------------------------------
# Copied VERBATIM from solanaBilly app/tasks/trading_tasks.py lines ~179-205.
# The Helius Sender plan routes transactions through Jito infrastructure via
# a dedicated endpoint (HELIUS_SENDER_URL). Sender requires one of these 10
# specific tip accounts — using the old generic Jito address
# (96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5) with the Sender endpoint
# has no effect and wastes ~0.003 SOL per transaction.
#
# We rotate randomly across all 10 on each transaction build rather than using
# a fixed account or a round-robin counter. The rationale: Jito write-lock
# contention. Each tip account is write-locked during a slot while the
# Sender infrastructure processes it. If multiple concurrent senders all pick
# the same account, their transactions queue behind each other. True random
# selection (random.choice) distributes load without any shared-counter state,
# which also keeps the helper stateless and easy to test.
#
# IMPORTANT: This pool is DISTINCT from PUMP_FUN_BREAKING_FEE_RECIPIENTS above.
# The breaking-fee pool goes to pump.fun's program; these tip accounts go to
# the Helius Sender / Jito infrastructure. Never confuse the two.
HELIUS_SENDER_TIP_ACCOUNTS: tuple[str, ...] = (
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

#: Default Jito tip per transaction in lamports (5_000_000 = 0.005 SOL).
#: Matches solanaBilly's per-tx tip (trading_tasks.py ~167-205 context).
DEFAULT_JITO_TIP_LAMPORTS: int = 5_000_000


def _pick_helius_sender_tip_account() -> str:
    """Return a randomly-selected Helius Sender tip account.

    Copied VERBATIM from solanaBilly app/tasks/trading_tasks.py lines ~193-205.
    Picks from HELIUS_SENDER_TIP_ACCOUNTS using random.choice (not
    deterministically seeded) so each transaction uses a different account
    from the pool. This avoids write-lock contention when the Sender
    infrastructure processes concurrent tips from multiple senders in the
    same slot.

    Returns:
        Base58 tip account address string.
    """
    return random.choice(HELIUS_SENDER_TIP_ACCOUNTS)


# ---------------------------------------------------------------------------
# Compute-budget helpers
# ---------------------------------------------------------------------------


def _build_system_transfer(from_bytes: bytes, to_bytes: bytes, lamports: int) -> Instruction:
    """Build a System Program Transfer instruction (pure derivation).

    Used for the Helius Sender Jito tip transfer appended to buy/sell ix lists.

    Args:
        from_bytes:  32-byte pubkey of the payer/sender (signer + writable).
        to_bytes:    32-byte pubkey of the tip account (writable, not signer).
        lamports:    Amount to transfer in lamports.

    Returns:
        Instruction — serialised System Program Transfer (discriminator 2 + u64 LE).
    """
    SYSTEM_PROGRAM_BYTES = bytes(32)  # 11111...
    # System program Transfer discriminator = 2 (u32 LE) + amount (u64 LE)
    data = struct.pack("<IQ", 2, lamports)
    return Instruction(
        program_id=SYSTEM_PROGRAM_BYTES,
        accounts=[
            AccountMeta(pubkey=from_bytes, is_signer=True, is_writable=True, name="from"),
            AccountMeta(pubkey=to_bytes, is_signer=False, is_writable=True, name="to"),
        ],
        data=data,
    )


def _set_compute_unit_price(micro_lamports: int) -> Instruction:
    """Build a ComputeBudget::SetComputeUnitPrice instruction (pure derivation)."""
    COMPUTE_BUDGET_PROGRAM = b58decode("ComputeBudget111111111111111111111111111111")
    # Borsh: discriminator 3 (SetComputeUnitPrice) + u64 LE micro-lamports.
    data = bytes([3]) + struct.pack("<Q", micro_lamports)
    return Instruction(
        program_id=COMPUTE_BUDGET_PROGRAM,
        accounts=[],
        data=data,
    )


def _set_compute_unit_limit(units: int) -> Instruction:
    """Build a ComputeBudget::SetComputeUnitLimit instruction (pure derivation)."""
    COMPUTE_BUDGET_PROGRAM = b58decode("ComputeBudget111111111111111111111111111111")
    # Borsh: discriminator 2 (SetComputeUnitLimit) + u32 LE units.
    data = bytes([2]) + struct.pack("<I", units)
    return Instruction(
        program_id=COMPUTE_BUDGET_PROGRAM,
        accounts=[],
        data=data,
    )


# ---------------------------------------------------------------------------
# PDA helpers — all pure Python (no network)
# ---------------------------------------------------------------------------


def _derive_bonding_curve_pda(mint_bytes: bytes) -> bytes:
    """Derive [b'bonding-curve', mint] PDA under the bonding-curve program."""
    pda, _ = find_program_address([b"bonding-curve", mint_bytes], PUMP_FUN_PROGRAM_BYTES)
    return pda


def _derive_bonding_curve_v2(mint_bytes: bytes) -> bytes:
    """Derive [b'bonding-curve-v2', mint] PDA (required post-upgrade account)."""
    pda, _ = find_program_address([b"bonding-curve-v2", mint_bytes], PUMP_FUN_PROGRAM_BYTES)
    return pda


def _derive_creator_vault(creator_bytes: bytes) -> bytes:
    """Derive [b'creator-vault', creator] PDA under the bonding-curve program."""
    pda, _ = find_program_address([b"creator-vault", creator_bytes], PUMP_FUN_PROGRAM_BYTES)
    return pda


def _derive_global_volume_accumulator() -> bytes:
    """Derive [b'global_volume_accumulator'] PDA."""
    pda, _ = find_program_address([b"global_volume_accumulator"], PUMP_FUN_PROGRAM_BYTES)
    return pda


def _derive_user_volume_accumulator(user_bytes: bytes) -> bytes:
    """Derive [b'user_volume_accumulator', user] PDA."""
    pda, _ = find_program_address([b"user_volume_accumulator", user_bytes], PUMP_FUN_PROGRAM_BYTES)
    return pda


def _derive_fee_config() -> bytes:
    """Derive the fee_config PDA: seeds [b'fee_config', bonding-curve-program],
    AUTHORITY = the DISTINCT fee program (pfee...).

    Matches solanaBilly _derive_fee_config (trading_tasks.py:1502-1507):
    find_program_address([b"fee_config", bytes(PUMP_FUN_PROGRAM_ID)], PUMP_FUN_FEE_PROGRAM).
    The seed is the bonding-curve program id; the derivation authority is the fee
    program. Using the bonding-curve program as the authority yields a wrong,
    nonexistent PDA -> account-not-found on every tx.
    """
    pda, _ = find_program_address([b"fee_config", PUMP_FUN_PROGRAM_BYTES], PUMP_FUN_FEE_PROGRAM_BYTES)
    return pda


def _derive_ata(owner_bytes: bytes, token_program_bytes: bytes, mint_bytes: bytes) -> bytes:
    """Derive an Associated Token Account PDA.

    seeds: [owner, token_program, mint], program: SPL_ATA_PROGRAM.
    """
    ata_program = b58decode(SPL_ATA_PROGRAM)
    pda, _ = find_program_address([owner_bytes, token_program_bytes, mint_bytes], ata_program)
    return pda


# ---------------------------------------------------------------------------
# build_curve_buy_instructions
# ---------------------------------------------------------------------------


def build_curve_buy_instructions(
    *,
    wallet_pubkey_bytes: bytes,
    mint_b58: str,
    creator_b58: str,
    is_mayhem_mode: Optional[bool],
    token_amount: int,
    max_sol_cost_lamports: int,
    token_program_b58: str = SPL_TOKEN_PROGRAM,
    compute_unit_price: int = 1000,
    compute_unit_limit: int = 200_000,
    jito_tip_lamports: int = DEFAULT_JITO_TIP_LAMPORTS,
) -> list[Instruction]:
    """Build pump.fun bonding-curve BUY instruction list (18 accounts, post-upgrade).

    Returns [set_compute_unit_price, set_compute_unit_limit, create_ata_idempotent,
             bonding_curve_buy, jito_tip_transfer] — 5 instructions, ready for
             build_signed_tx_b64.

    The jito_tip_transfer (last ix) sends jito_tip_lamports from the wallet to a
    randomly-chosen Helius Sender tip account (HELIUS_SENDER_TIP_ACCOUNTS). This
    pool is DISTINCT from the pump.fun breaking-fee pool — do not confuse them.
    Copied from solanaBilly trading_tasks.py ~167-205 (F1 — capital path wiring).

    No network I/O. Pure derivation + struct packing. CAPITAL SAFE.

    Args:
        wallet_pubkey_bytes:   32-byte wallet (payer / buyer) pubkey.
        mint_b58:              Token mint address (base58).
        creator_b58:           Creator pubkey from CurveState.creator (base58).
                               Required — raises ValueError if empty.
        is_mayhem_mode:        From CurveState.is_mayhem_mode. None -> LEGACY.
        token_amount:          Exact token base units to request (u64).
        max_sol_cost_lamports: Maximum SOL to spend (slippage cap, u64).
        token_program_b58:     SPL Token or Token-2022 — owner of this mint.
        compute_unit_price:    Priority fee in micro-lamports.
        compute_unit_limit:    CU limit for the transaction.
        jito_tip_lamports:     Lamports to transfer to the Helius Sender Jito tip
                               account. Default 5_000_000 (0.005 SOL). Set to 0 to
                               skip the tip instruction (offline/test only).

    Returns:
        List of 5 vendored Instruction objects (4 + jito tip transfer).

    Raises:
        ValueError: If creator_b58 is empty (cannot derive creator_vault PDA).
    """
    if not creator_b58:
        raise ValueError("creator_b58 is required to build a bonding-curve buy instruction")

    mint_bytes = b58decode(mint_b58)
    creator_bytes = b58decode(creator_b58)
    token_program_bytes = b58decode(token_program_b58)
    program_bytes = PUMP_FUN_PROGRAM_BYTES
    system_program_bytes = bytes(32)
    ata_program_bytes = b58decode(SPL_ATA_PROGRAM)

    # Static pubkeys
    global_bytes = b58decode(PUMP_FUN_GLOBAL)
    event_auth_bytes = b58decode(PUMP_FUN_EVENT_AUTHORITY)
    fee_program_bytes = PUMP_FUN_FEE_PROGRAM_BYTES  # account #16 = the DISTINCT pfee program

    # Fee recipient: is_mayhem_mode selects MAYHEM; None/False -> LEGACY.
    if is_mayhem_mode:
        fee_recipient_bytes = b58decode(PUMP_FUN_FEE_RECIPIENT_MAYHEM)
    else:
        fee_recipient_bytes = b58decode(PUMP_FUN_FEE_RECIPIENT_LEGACY)

    # Derived PDAs
    bonding_curve_bytes = _derive_bonding_curve_pda(mint_bytes)
    ata_bytes = _derive_ata(wallet_pubkey_bytes, token_program_bytes, mint_bytes)
    curve_ata_bytes = _derive_ata(bonding_curve_bytes, token_program_bytes, mint_bytes)
    creator_vault_bytes = _derive_creator_vault(creator_bytes)
    bonding_curve_v2_bytes = _derive_bonding_curve_v2(mint_bytes)
    global_vol_accum_bytes = _derive_global_volume_accumulator()
    user_vol_accum_bytes = _derive_user_volume_accumulator(wallet_pubkey_bytes)
    fee_config_bytes = _derive_fee_config()
    breaking_fee_bytes = b58decode(_pick_breaking_fee_recipient())

    # --- Compute budget instructions ---
    ix_cu_price = _set_compute_unit_price(compute_unit_price)
    ix_cu_limit = _set_compute_unit_limit(compute_unit_limit)

    # --- Idempotent ATA creation (discriminator 1 = create_idempotent) ---
    ix_create_ata = Instruction(
        program_id=ata_program_bytes,
        accounts=[
            AccountMeta(pubkey=wallet_pubkey_bytes, is_signer=True, is_writable=True, name="funding_account"),
            AccountMeta(pubkey=ata_bytes, is_signer=False, is_writable=True, name="associated_token_account"),
            AccountMeta(pubkey=wallet_pubkey_bytes, is_signer=False, is_writable=False, name="wallet_address"),
            AccountMeta(pubkey=mint_bytes, is_signer=False, is_writable=False, name="token_mint_address"),
            AccountMeta(pubkey=system_program_bytes, is_signer=False, is_writable=False, name="system_program"),
            AccountMeta(pubkey=token_program_bytes, is_signer=False, is_writable=False, name="token_program_id"),
        ],
        data=bytes([1]),  # create_idempotent discriminator
    )

    # --- Buy instruction data: discriminator + amount + max_sol_cost + track_volume ---
    buy_data = BUY_DISCRIMINATOR + struct.pack("<QQ", token_amount, max_sol_cost_lamports) + TRACK_VOLUME_SOME_TRUE

    # --- 18-account buy layout (post-2026-04-28 upgrade) ---
    #  1  global                   R
    #  2  fee_recipient            W
    #  3  mint                     R
    #  4  bonding_curve            W
    #  5  curve_ata                W
    #  6  user_ata                 W
    #  7  wallet (user/payer)      WS
    #  8  system_program           R
    #  9  token_program            R
    # 10  creator_vault            W
    # 11  event_authority          R
    # 12  program                  R
    # 13  global_volume_accum      R  (readonly to avoid write-lock contention)
    # 14  user_volume_accum        W
    # 15  fee_config               R
    # 16  fee_program              R
    # 17  bonding_curve_v2         R
    # 18  breaking_fee_recipient   W  (NEW post-2026-04-28 upgrade)
    ix_buy = Instruction(
        program_id=program_bytes,
        accounts=[
            AccountMeta(pubkey=global_bytes, is_signer=False, is_writable=False, name="global"),  # 1
            AccountMeta(pubkey=fee_recipient_bytes, is_signer=False, is_writable=True, name="fee_recipient"),  # 2
            AccountMeta(pubkey=mint_bytes, is_signer=False, is_writable=False, name="mint"),  # 3
            AccountMeta(pubkey=bonding_curve_bytes, is_signer=False, is_writable=True, name="bonding_curve"),  # 4
            AccountMeta(
                pubkey=curve_ata_bytes, is_signer=False, is_writable=True, name="associated_bonding_curve"
            ),  # 5
            AccountMeta(pubkey=ata_bytes, is_signer=False, is_writable=True, name="associated_user"),  # 6
            AccountMeta(pubkey=wallet_pubkey_bytes, is_signer=True, is_writable=True, name="user"),  # 7
            AccountMeta(pubkey=system_program_bytes, is_signer=False, is_writable=False, name="system_program"),  # 8
            AccountMeta(pubkey=token_program_bytes, is_signer=False, is_writable=False, name="token_program"),  # 9
            AccountMeta(pubkey=creator_vault_bytes, is_signer=False, is_writable=True, name="creator_vault"),  # 10
            AccountMeta(pubkey=event_auth_bytes, is_signer=False, is_writable=False, name="event_authority"),  # 11
            AccountMeta(pubkey=program_bytes, is_signer=False, is_writable=False, name="program"),  # 12
            AccountMeta(
                pubkey=global_vol_accum_bytes, is_signer=False, is_writable=False, name="global_volume_accumulator"
            ),  # 13
            AccountMeta(
                pubkey=user_vol_accum_bytes, is_signer=False, is_writable=True, name="user_volume_accumulator"
            ),  # 14
            AccountMeta(pubkey=fee_config_bytes, is_signer=False, is_writable=False, name="fee_config"),  # 15
            AccountMeta(pubkey=fee_program_bytes, is_signer=False, is_writable=False, name="fee_program"),  # 16
            AccountMeta(
                pubkey=bonding_curve_v2_bytes, is_signer=False, is_writable=False, name="bonding_curve_v2"
            ),  # 17
            AccountMeta(
                pubkey=breaking_fee_bytes, is_signer=False, is_writable=True, name="breaking_fee_recipient"
            ),  # 18
        ],
        data=buy_data,
    )

    # --- Helius Sender Jito tip transfer (F1 — capital path wiring) ---
    # Appended after the main buy ix (same tx).  Tip pool is DISTINCT from the
    # pump.fun breaking-fee pool above.  Copied from solanaBilly ~167-205.
    ixs: list[Instruction] = [ix_cu_price, ix_cu_limit, ix_create_ata, ix_buy]
    if jito_tip_lamports > 0:
        tip_account_bytes = b58decode(_pick_helius_sender_tip_account())
        ix_jito_tip = _build_system_transfer(
            from_bytes=wallet_pubkey_bytes,
            to_bytes=tip_account_bytes,
            lamports=jito_tip_lamports,
        )
        ixs.append(ix_jito_tip)

    return ixs


# ---------------------------------------------------------------------------
# build_curve_sell_instructions
# ---------------------------------------------------------------------------


def build_curve_sell_instructions(
    *,
    wallet_pubkey_bytes: bytes,
    mint_b58: str,
    creator_b58: str,
    is_mayhem_mode: Optional[bool],
    is_cashback_coin: Optional[bool],
    token_amount: int,
    min_sol_output_lamports: int,
    token_program_b58: str = SPL_TOKEN_PROGRAM,
    compute_unit_price: int = 1000,
    compute_unit_limit: int = 200_000,
    jito_tip_lamports: int = DEFAULT_JITO_TIP_LAMPORTS,
) -> list[Instruction]:
    """Build pump.fun bonding-curve SELL instruction list (14-17 accounts, post-upgrade).

    Returns [set_compute_unit_price, set_compute_unit_limit, bonding_curve_sell,
             jito_tip_transfer] — 4 instructions, ready for build_signed_tx_b64.

    The user_volume_accumulator account is ONLY included when is_cashback_coin=True
    (Anchor 6024 if omitted on cashback tokens; harmless to omit for non-cashback).
    bonding_curve_v2 and breaking_fee_recipient are always appended (post-upgrade).

    The jito_tip_transfer (last ix) sends jito_tip_lamports from the wallet to a
    randomly-chosen Helius Sender tip account (HELIUS_SENDER_TIP_ACCOUNTS). This
    pool is DISTINCT from the pump.fun breaking-fee pool — do not confuse them.
    Copied from solanaBilly trading_tasks.py ~167-205 (F1 — capital path wiring).

    NOTE: min_sol_output_lamports=0 is a deliberate validation choice — accepts any
    return from the curve. Production needs slippage tiering (see F6 / billy's
    per-attempt tier logic in trading_tasks.py ~3820 sell_position). (F6 doc)

    No network I/O. Pure derivation + struct packing. CAPITAL SAFE.

    Args:
        wallet_pubkey_bytes:      32-byte wallet (payer / seller) pubkey.
        mint_b58:                 Token mint address (base58).
        creator_b58:              Creator pubkey from CurveState.creator (base58).
                                  Required — raises ValueError if empty.
        is_mayhem_mode:           From CurveState.is_mayhem_mode. None -> LEGACY.
        is_cashback_coin:         From CurveState.is_cashback_coin. True -> include
                                  user_volume_accumulator in sell accounts.
        token_amount:             Token base units to sell (u64 exact-in).
        min_sol_output_lamports:  Minimum SOL to receive (slippage floor, u64).
                                  0 = no floor (validation path — accepts any return).
        token_program_b58:        SPL Token or Token-2022 — owner of this mint.
        compute_unit_price:       Priority fee in micro-lamports.
        compute_unit_limit:       CU limit for the transaction.
        jito_tip_lamports:        Lamports to transfer to the Helius Sender Jito tip
                                  account. Default 5_000_000 (0.005 SOL). Set to 0 to
                                  skip the tip instruction (offline/test only).

    Returns:
        List of 4 vendored Instruction objects (3 + jito tip transfer when tip > 0).

    Raises:
        ValueError: If creator_b58 is empty.
    """
    if not creator_b58:
        raise ValueError("creator_b58 is required to build a bonding-curve sell instruction")

    mint_bytes = b58decode(mint_b58)
    creator_bytes = b58decode(creator_b58)
    token_program_bytes = b58decode(token_program_b58)
    program_bytes = PUMP_FUN_PROGRAM_BYTES
    system_program_bytes = bytes(32)

    # Static pubkeys
    global_bytes = b58decode(PUMP_FUN_GLOBAL)
    event_auth_bytes = b58decode(PUMP_FUN_EVENT_AUTHORITY)
    fee_program_bytes = PUMP_FUN_FEE_PROGRAM_BYTES  # the DISTINCT pfee program (sell account)

    # Fee recipient
    if is_mayhem_mode:
        fee_recipient_bytes = b58decode(PUMP_FUN_FEE_RECIPIENT_MAYHEM)
    else:
        fee_recipient_bytes = b58decode(PUMP_FUN_FEE_RECIPIENT_LEGACY)

    # Derived PDAs
    bonding_curve_bytes = _derive_bonding_curve_pda(mint_bytes)
    ata_bytes = _derive_ata(wallet_pubkey_bytes, token_program_bytes, mint_bytes)
    curve_ata_bytes = _derive_ata(bonding_curve_bytes, token_program_bytes, mint_bytes)
    creator_vault_bytes = _derive_creator_vault(creator_bytes)
    bonding_curve_v2_bytes = _derive_bonding_curve_v2(mint_bytes)
    fee_config_bytes = _derive_fee_config()
    breaking_fee_bytes = b58decode(_pick_breaking_fee_recipient())

    # --- Compute budget ---
    ix_cu_price = _set_compute_unit_price(compute_unit_price)
    ix_cu_limit = _set_compute_unit_limit(compute_unit_limit)

    # --- Sell instruction data ---
    sell_data = SELL_DISCRIMINATOR + struct.pack("<QQ", token_amount, min_sol_output_lamports) + TRACK_VOLUME_SOME_TRUE

    # Base sell accounts (14): post-upgrade layout (creator_vault swapped to position 9,
    # token_program to position 10 — differs from buy layout).
    #  1  global                   R
    #  2  fee_recipient            W
    #  3  mint                     R
    #  4  bonding_curve            W
    #  5  curve_ata                W
    #  6  user_ata                 W
    #  7  wallet (user/seller)     WS
    #  8  system_program           R
    #  9  creator_vault            W   (swapped with token_program vs buy layout)
    # 10  token_program            R
    # 11  event_authority          R
    # 12  program                  R
    # 13  fee_config               R
    # 14  fee_program              R
    # [15] user_volume_accumulator W   (only if is_cashback_coin=True)
    # [n]  bonding_curve_v2        R   (always appended)
    # [n+1] breaking_fee_recipient W   (always appended, post-2026-04-28)
    sell_accounts: list[AccountMeta] = [
        AccountMeta(pubkey=global_bytes, is_signer=False, is_writable=False, name="global"),
        AccountMeta(pubkey=fee_recipient_bytes, is_signer=False, is_writable=True, name="fee_recipient"),
        AccountMeta(pubkey=mint_bytes, is_signer=False, is_writable=False, name="mint"),
        AccountMeta(pubkey=bonding_curve_bytes, is_signer=False, is_writable=True, name="bonding_curve"),
        AccountMeta(pubkey=curve_ata_bytes, is_signer=False, is_writable=True, name="associated_bonding_curve"),
        AccountMeta(pubkey=ata_bytes, is_signer=False, is_writable=True, name="associated_user"),
        AccountMeta(pubkey=wallet_pubkey_bytes, is_signer=True, is_writable=True, name="user"),
        AccountMeta(pubkey=system_program_bytes, is_signer=False, is_writable=False, name="system_program"),
        AccountMeta(pubkey=creator_vault_bytes, is_signer=False, is_writable=True, name="creator_vault"),
        AccountMeta(pubkey=token_program_bytes, is_signer=False, is_writable=False, name="token_program"),
        AccountMeta(pubkey=event_auth_bytes, is_signer=False, is_writable=False, name="event_authority"),
        AccountMeta(pubkey=program_bytes, is_signer=False, is_writable=False, name="program"),
        AccountMeta(pubkey=fee_config_bytes, is_signer=False, is_writable=False, name="fee_config"),
        AccountMeta(pubkey=fee_program_bytes, is_signer=False, is_writable=False, name="fee_program"),
    ]

    # Conditional: user_volume_accumulator (cashback coins only)
    if is_cashback_coin:
        user_vol_accum_bytes = _derive_user_volume_accumulator(wallet_pubkey_bytes)
        sell_accounts.append(
            AccountMeta(pubkey=user_vol_accum_bytes, is_signer=False, is_writable=True, name="user_volume_accumulator")
        )

    # Always appended: bonding_curve_v2 + breaking_fee_recipient
    sell_accounts.append(
        AccountMeta(pubkey=bonding_curve_v2_bytes, is_signer=False, is_writable=False, name="bonding_curve_v2")
    )
    sell_accounts.append(
        AccountMeta(pubkey=breaking_fee_bytes, is_signer=False, is_writable=True, name="breaking_fee_recipient")
    )

    ix_sell = Instruction(
        program_id=program_bytes,
        accounts=sell_accounts,
        data=sell_data,
    )

    # --- Helius Sender Jito tip transfer (F1 — capital path wiring) ---
    # Appended after the main sell ix (same tx). Tip pool is DISTINCT from the
    # pump.fun breaking-fee pool above. Copied from solanaBilly ~167-205.
    ixs: list[Instruction] = [ix_cu_price, ix_cu_limit, ix_sell]
    if jito_tip_lamports > 0:
        tip_account_bytes = b58decode(_pick_helius_sender_tip_account())
        ix_jito_tip = _build_system_transfer(
            from_bytes=wallet_pubkey_bytes,
            to_bytes=tip_account_bytes,
            lamports=jito_tip_lamports,
        )
        ixs.append(ix_jito_tip)

    return ixs


__all__ = [
    "PUMP_FUN_PROGRAM_ID",
    "PUMP_FUN_PROGRAM_BYTES",
    "PUMP_FUN_FEE_RECIPIENT_LEGACY",
    "PUMP_FUN_FEE_RECIPIENT_MAYHEM",
    "PUMP_FUN_BREAKING_FEE_RECIPIENTS",
    "HELIUS_SENDER_TIP_ACCOUNTS",
    "DEFAULT_JITO_TIP_LAMPORTS",
    "BUY_DISCRIMINATOR",
    "SELL_DISCRIMINATOR",
    "build_curve_buy_instructions",
    "build_curve_sell_instructions",
]
