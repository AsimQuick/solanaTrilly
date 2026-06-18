# ---
# module: trading.pumpswap_ix
# sprint: sprint-13
# story: US-65 AC-65.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: stdlib (hashlib, struct, dataclasses)
# ---
"""PumpSwap buy/sell instruction builder — AC-65.1.

Ports §10.1 account lists VERBATIM from the vendored IDL:
  BUY  — 23 accounts in IDL order
  SELL — 21 accounts (omits global_volume_accumulator + user_volume_accumulator)

Discriminators, args, and PDA derivation are all deterministic / offline.
NO mainnet send — this module only constructs instruction data structures.

Account flag legend used in build_* functions:
  W  = writable only
  S  = signer only
  WS = writable + signer
  R  = read-only (neither writable nor signer)
"""

import hashlib
import struct
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Base58 codec — pure Python, no dependencies
# ---------------------------------------------------------------------------

_B58_ALPHA = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP: dict[int, int] = {c: i for i, c in enumerate(_B58_ALPHA)}


def b58decode(s: str) -> bytes:
    """Decode a base58 string to raw bytes."""
    n = 0
    for c in s.encode():
        n = n * 58 + _B58_MAP[c]
    leading = len(s) - len(s.lstrip("1"))
    if n == 0:
        return bytes(leading)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes(leading) + body


def b58encode(b: bytes) -> str:
    """Encode raw bytes to a base58 string."""
    n = int.from_bytes(b, "big")
    chars: list[str] = []
    while n:
        n, r = divmod(n, 58)
        chars.append(chr(_B58_ALPHA[r]))
    chars.extend("1" * (len(b) - len(b.lstrip(b"\x00"))))
    return "".join(reversed(chars))


# ---------------------------------------------------------------------------
# Ed25519 curve membership check — required for PDA nonce search
# ---------------------------------------------------------------------------

_P = 2**255 - 19  # Ed25519 prime modulus
_D = (-121665 * pow(121666, _P - 2, _P)) % _P  # Twisted Edwards d constant


def _bytes_are_on_curve(b: bytes) -> bool:
    """Return True if 32-byte value decodes as a valid compressed Ed25519 point.

    PDAs must be OFF the curve; this check is the core of the nonce search.
    """
    if len(b) != 32:
        return False
    buf = bytearray(b)
    sign_x = buf[31] >> 7
    buf[31] &= 0x7F
    y = int.from_bytes(buf, "little")
    if y >= _P:
        return False
    y2 = y * y % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    v_inv = pow(v, _P - 2, _P)
    x2 = u * v_inv % _P
    if x2 == 0:
        return sign_x == 0
    # P ≡ 5 (mod 8): try x = x2^((P+3)/8)
    x = pow(x2, (_P + 3) // 8, _P)
    if x * x % _P == x2:
        return True
    # Multiply by sqrt(-1) = 2^((P-1)/4) mod P
    sqrt_m1 = pow(2, (_P - 1) // 4, _P)
    x = x * sqrt_m1 % _P
    return x * x % _P == x2


# ---------------------------------------------------------------------------
# Solana PDA derivation — pure Python, no deps
# ---------------------------------------------------------------------------

def create_program_address(seeds: list[bytes], program_id: bytes) -> bytes:
    """Create a program-derived address from seeds + program_id.

    Raises ValueError if the resulting hash is on the Ed25519 curve
    (on-curve hashes are not valid PDAs).
    """
    h = hashlib.sha256()
    for s in seeds:
        h.update(s)
    h.update(program_id)
    h.update(b"ProgramDerivedAddress")
    digest = h.digest()
    if _bytes_are_on_curve(digest):
        raise ValueError("seeds produce an on-curve point; increment nonce")
    return digest


def find_program_address(seeds: list[bytes], program_id: bytes) -> tuple[bytes, int]:
    """Find the canonical PDA for seeds + program_id.

    Iterates nonces 255 → 0, appending the nonce byte to seeds on each try.
    Returns (pda_bytes, nonce).
    """
    for nonce in range(255, -1, -1):
        try:
            pk = create_program_address(seeds + [bytes([nonce])], program_id)
            return pk, nonce
        except ValueError:
            continue
    raise ValueError("No valid PDA found for the given seeds")


# ---------------------------------------------------------------------------
# Known program IDs (base58 → bytes, computed once)
# ---------------------------------------------------------------------------

PUMP_AMM_PROGRAM: bytes = b58decode("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
TOKEN_PROGRAM: bytes = b58decode("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM: bytes = b58decode("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
SYSTEM_PROGRAM: bytes = bytes(32)  # 11111111111111111111111111111111
ATA_PROGRAM: bytes = b58decode("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
FEE_PROGRAM: bytes = b58decode("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")

# WSOL mint (native SOL token wrapper) — the canonical quote_mint for PumpSwap pools
WSOL_MINT: bytes = b58decode("So11111111111111111111111111111111111111112")

# ---------------------------------------------------------------------------
# PDA seed constants (from vendored IDL §10.1)
# ---------------------------------------------------------------------------

_SEED_GLOBAL_CONFIG: bytes = b"global_config"
_SEED_CREATOR_VAULT: bytes = b"creator_vault"
_SEED_EVENT_AUTHORITY: bytes = b"__event_authority"
_SEED_GLOBAL_VOL_ACCUM: bytes = b"global_volume_accumulator"
_SEED_USER_VOL_ACCUM: bytes = b"user_volume_accumulator"

# fee_config PDA: seeds = [b"fee_config", _FEE_CONFIG_CONST_SEED], program = FEE_PROGRAM
# The 32-byte constant seed is hardcoded in the IDL accounts definition.
_FEE_CONFIG_CONST_SEED: bytes = bytes([
    12, 20, 222, 252, 130, 94, 198, 118, 148, 37, 8, 24, 187, 101, 64, 101,
    244, 41, 141, 49, 86, 213, 113, 180, 212, 248, 9, 12, 24, 233, 168, 99,
])

# ---------------------------------------------------------------------------
# Discriminators (§10.1 pinned ground truth — verified in AC-64.3)
# ---------------------------------------------------------------------------

BUY_DISCRIMINATOR: bytes = bytes.fromhex("66063d1201daebea")
SELL_DISCRIMINATOR: bytes = bytes.fromhex("33e685a4017f83ad")

# ---------------------------------------------------------------------------
# Static PDA derivation helpers (deterministic, program-scoped)
# ---------------------------------------------------------------------------


def derive_event_authority() -> bytes:
    """Runtime-derived event_authority — NEVER hardcoded (AC-65.1 requirement).

    Seed: [b"__event_authority"], program: PUMP_AMM_PROGRAM
    """
    pk, _ = find_program_address([_SEED_EVENT_AUTHORITY], PUMP_AMM_PROGRAM)
    return pk


def derive_global_config() -> bytes:
    """global_config PDA: [b"global_config"], program: PUMP_AMM_PROGRAM."""
    pk, _ = find_program_address([_SEED_GLOBAL_CONFIG], PUMP_AMM_PROGRAM)
    return pk


def derive_global_volume_accumulator() -> bytes:
    """global_volume_accumulator PDA: [b"global_volume_accumulator"], program: PUMP_AMM."""
    pk, _ = find_program_address([_SEED_GLOBAL_VOL_ACCUM], PUMP_AMM_PROGRAM)
    return pk


def derive_fee_config() -> bytes:
    """fee_config PDA: [b"fee_config", _FEE_CONFIG_CONST_SEED], program: FEE_PROGRAM."""
    pk, _ = find_program_address([b"fee_config", _FEE_CONFIG_CONST_SEED], FEE_PROGRAM)
    return pk


# ---------------------------------------------------------------------------
# Per-pool / per-user PDA derivation helpers
# ---------------------------------------------------------------------------


def derive_pool_pda(
    index: int,
    creator: bytes,
    base_mint: bytes,
    quote_mint: bytes,
) -> bytes:
    """Pool PDA from IDL create_pool seeds.

    seeds: [b"pool", index_u16_le, creator, base_mint, quote_mint]
    program: PUMP_AMM_PROGRAM
    """
    pk, _ = find_program_address(
        [b"pool", index.to_bytes(2, "little"), creator, base_mint, quote_mint],
        PUMP_AMM_PROGRAM,
    )
    return pk


def derive_creator_vault_authority(coin_creator: bytes) -> bytes:
    """coin_creator_vault_authority PDA.

    seeds: [b"creator_vault", coin_creator], program: PUMP_AMM_PROGRAM
    """
    pk, _ = find_program_address([_SEED_CREATOR_VAULT, coin_creator], PUMP_AMM_PROGRAM)
    return pk


def derive_user_volume_accumulator(user: bytes) -> bytes:
    """user_volume_accumulator PDA (BUY only).

    seeds: [b"user_volume_accumulator", user], program: PUMP_AMM_PROGRAM
    """
    pk, _ = find_program_address([_SEED_USER_VOL_ACCUM, user], PUMP_AMM_PROGRAM)
    return pk


def derive_ata(owner: bytes, token_program: bytes, mint: bytes) -> bytes:
    """Associated Token Account address.

    seeds: [owner, token_program, mint], program: ATA_PROGRAM
    """
    pk, _ = find_program_address([owner, token_program, mint], ATA_PROGRAM)
    return pk


# ---------------------------------------------------------------------------
# Static PDAs — computed once at module import (runtime-derived, never literal)
# ---------------------------------------------------------------------------

_EVENT_AUTHORITY: bytes = derive_event_authority()
_GLOBAL_CONFIG: bytes = derive_global_config()
_GLOBAL_VOL_ACCUM: bytes = derive_global_volume_accumulator()
_FEE_CONFIG: bytes = derive_fee_config()

# ---------------------------------------------------------------------------
# Instruction data structures
# ---------------------------------------------------------------------------


@dataclass
class AccountMeta:
    """One account entry in a Solana instruction."""
    pubkey: bytes       # 32 raw bytes
    is_writable: bool
    is_signer: bool
    name: str = field(default="")   # informational — matches IDL account name


@dataclass
class Instruction:
    """A complete Solana instruction (program + accounts + data)."""
    program_id: bytes
    accounts: list[AccountMeta]
    data: bytes


# ---------------------------------------------------------------------------
# Borsh argument encoding
# ---------------------------------------------------------------------------


def _encode_u64(value: int) -> bytes:
    """Little-endian u64."""
    return struct.pack("<Q", value)


def _encode_option_bool(value: bool | None) -> bytes:
    """Borsh Option<bool>: None → [0x00], Some(False) → [0x01, 0x00], Some(True) → [0x01, 0x01]."""
    if value is None:
        return b"\x00"
    return bytes([0x01, 0x01 if value else 0x00])


# ---------------------------------------------------------------------------
# Instruction builders
# ---------------------------------------------------------------------------


def build_buy_instruction(
    *,
    pool: bytes,
    user: bytes,
    base_mint: bytes,
    quote_mint: bytes,
    user_base_token_account: bytes,
    user_quote_token_account: bytes,
    pool_base_token_account: bytes,
    pool_quote_token_account: bytes,
    protocol_fee_recipient: bytes,
    base_token_program: bytes,
    quote_token_program: bytes,
    coin_creator: bytes,
    base_amount_out: int,
    max_quote_amount_in: int,
    track_volume: bool | None = None,
) -> Instruction:
    """Build a PumpSwap BUY instruction — 23 accounts in IDL §10.1 order.

    All PDAs are derived at call time (runtime-derived, no hardcoded values).
    Returns an Instruction — does NOT send anything to mainnet (AC-65.1).
    """
    # --- Derived PDAs ---
    protocol_fee_ata = derive_ata(protocol_fee_recipient, quote_token_program, quote_mint)
    vault_auth = derive_creator_vault_authority(coin_creator)
    vault_ata = derive_ata(vault_auth, quote_token_program, quote_mint)
    user_vol_accum = derive_user_volume_accumulator(user)

    accounts: list[AccountMeta] = [
        # 00 — pool                              W
        AccountMeta(pool, True, False, "pool"),
        # 01 — user                              WS
        AccountMeta(user, True, True, "user"),
        # 02 — global_config                     R
        AccountMeta(_GLOBAL_CONFIG, False, False, "global_config"),
        # 03 — base_mint                         R
        AccountMeta(base_mint, False, False, "base_mint"),
        # 04 — quote_mint                        R
        AccountMeta(quote_mint, False, False, "quote_mint"),
        # 05 — user_base_token_account           W
        AccountMeta(user_base_token_account, True, False, "user_base_token_account"),
        # 06 — user_quote_token_account          W
        AccountMeta(user_quote_token_account, True, False, "user_quote_token_account"),
        # 07 — pool_base_token_account           W
        AccountMeta(pool_base_token_account, True, False, "pool_base_token_account"),
        # 08 — pool_quote_token_account          W
        AccountMeta(pool_quote_token_account, True, False, "pool_quote_token_account"),
        # 09 — protocol_fee_recipient            R
        AccountMeta(protocol_fee_recipient, False, False, "protocol_fee_recipient"),
        # 10 — protocol_fee_recipient_token_account  W (ATA)
        AccountMeta(protocol_fee_ata, True, False, "protocol_fee_recipient_token_account"),
        # 11 — base_token_program                R
        AccountMeta(base_token_program, False, False, "base_token_program"),
        # 12 — quote_token_program               R
        AccountMeta(quote_token_program, False, False, "quote_token_program"),
        # 13 — system_program                    R
        AccountMeta(SYSTEM_PROGRAM, False, False, "system_program"),
        # 14 — associated_token_program          R
        AccountMeta(ATA_PROGRAM, False, False, "associated_token_program"),
        # 15 — event_authority                   R (runtime-derived PDA)
        AccountMeta(_EVENT_AUTHORITY, False, False, "event_authority"),
        # 16 — program                           R
        AccountMeta(PUMP_AMM_PROGRAM, False, False, "program"),
        # 17 — coin_creator_vault_ata            W (ATA of vault_auth)
        AccountMeta(vault_ata, True, False, "coin_creator_vault_ata"),
        # 18 — coin_creator_vault_authority      R (PDA)
        AccountMeta(vault_auth, False, False, "coin_creator_vault_authority"),
        # 19 — global_volume_accumulator         R (PDA)
        AccountMeta(_GLOBAL_VOL_ACCUM, False, False, "global_volume_accumulator"),
        # 20 — user_volume_accumulator           W (PDA, buy-only)
        AccountMeta(user_vol_accum, True, False, "user_volume_accumulator"),
        # 21 — fee_config                        R (PDA via fee_program)
        AccountMeta(_FEE_CONFIG, False, False, "fee_config"),
        # 22 — fee_program                       R
        AccountMeta(FEE_PROGRAM, False, False, "fee_program"),
    ]

    data = (
        BUY_DISCRIMINATOR
        + _encode_u64(base_amount_out)
        + _encode_u64(max_quote_amount_in)
        + _encode_option_bool(track_volume)
    )

    return Instruction(PUMP_AMM_PROGRAM, accounts, data)


def build_sell_instruction(
    *,
    pool: bytes,
    user: bytes,
    base_mint: bytes,
    quote_mint: bytes,
    user_base_token_account: bytes,
    user_quote_token_account: bytes,
    pool_base_token_account: bytes,
    pool_quote_token_account: bytes,
    protocol_fee_recipient: bytes,
    base_token_program: bytes,
    quote_token_program: bytes,
    coin_creator: bytes,
    base_amount_in: int,
    min_quote_amount_out: int,
) -> Instruction:
    """Build a PumpSwap SELL instruction — 21 accounts in IDL §10.1 order.

    Omits global_volume_accumulator (buy idx 19) and user_volume_accumulator
    (buy idx 20) per §10.1 specification and pin_manifest notes.

    Returns an Instruction — does NOT send anything to mainnet (AC-65.1).
    """
    # --- Derived PDAs ---
    protocol_fee_ata = derive_ata(protocol_fee_recipient, quote_token_program, quote_mint)
    vault_auth = derive_creator_vault_authority(coin_creator)
    vault_ata = derive_ata(vault_auth, quote_token_program, quote_mint)

    accounts: list[AccountMeta] = [
        # 00 — pool                              W
        AccountMeta(pool, True, False, "pool"),
        # 01 — user                              WS
        AccountMeta(user, True, True, "user"),
        # 02 — global_config                     R
        AccountMeta(_GLOBAL_CONFIG, False, False, "global_config"),
        # 03 — base_mint                         R
        AccountMeta(base_mint, False, False, "base_mint"),
        # 04 — quote_mint                        R
        AccountMeta(quote_mint, False, False, "quote_mint"),
        # 05 — user_base_token_account           W
        AccountMeta(user_base_token_account, True, False, "user_base_token_account"),
        # 06 — user_quote_token_account          W
        AccountMeta(user_quote_token_account, True, False, "user_quote_token_account"),
        # 07 — pool_base_token_account           W
        AccountMeta(pool_base_token_account, True, False, "pool_base_token_account"),
        # 08 — pool_quote_token_account          W
        AccountMeta(pool_quote_token_account, True, False, "pool_quote_token_account"),
        # 09 — protocol_fee_recipient            R
        AccountMeta(protocol_fee_recipient, False, False, "protocol_fee_recipient"),
        # 10 — protocol_fee_recipient_token_account  W (ATA)
        AccountMeta(protocol_fee_ata, True, False, "protocol_fee_recipient_token_account"),
        # 11 — base_token_program                R
        AccountMeta(base_token_program, False, False, "base_token_program"),
        # 12 — quote_token_program               R
        AccountMeta(quote_token_program, False, False, "quote_token_program"),
        # 13 — system_program                    R
        AccountMeta(SYSTEM_PROGRAM, False, False, "system_program"),
        # 14 — associated_token_program          R
        AccountMeta(ATA_PROGRAM, False, False, "associated_token_program"),
        # 15 — event_authority                   R (runtime-derived PDA)
        AccountMeta(_EVENT_AUTHORITY, False, False, "event_authority"),
        # 16 — program                           R
        AccountMeta(PUMP_AMM_PROGRAM, False, False, "program"),
        # 17 — coin_creator_vault_ata            W (ATA of vault_auth)
        AccountMeta(vault_ata, True, False, "coin_creator_vault_ata"),
        # 18 — coin_creator_vault_authority      R (PDA)
        AccountMeta(vault_auth, False, False, "coin_creator_vault_authority"),
        # 19 — fee_config                        R (PDA via fee_program)
        AccountMeta(_FEE_CONFIG, False, False, "fee_config"),
        # 20 — fee_program                       R
        AccountMeta(FEE_PROGRAM, False, False, "fee_program"),
    ]

    data = (
        SELL_DISCRIMINATOR
        + _encode_u64(base_amount_in)
        + _encode_u64(min_quote_amount_out)
    )

    return Instruction(PUMP_AMM_PROGRAM, accounts, data)
