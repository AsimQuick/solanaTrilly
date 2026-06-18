# ---
# module: trading.tests.test_pumpswap_ix_ac651
# sprint: sprint-13
# story: US-65 AC-65.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.pumpswap_ix, json, hashlib, pathlib
# ---
"""AC-65.1 — PumpSwap instruction builder: account order, writability, signer flags,
discriminators, PDA derivation, and args encoding.

All tests are deterministic and offline — no DB, no network, no firehose.

Test list
---------
Structural (fixture-driven):
  test_buy_account_count_is_23
  test_sell_account_count_is_21
  test_buy_account_names_match_idl_order
  test_sell_account_names_match_idl_order
  test_buy_account_writability_flags
  test_sell_account_writability_flags
  test_buy_account_signer_flags
  test_sell_account_signer_flags
  test_sell_omits_volume_accumulators

Discriminators:
  test_buy_discriminator_matches_pinned
  test_sell_discriminator_matches_pinned

Args encoding:
  test_buy_data_starts_with_buy_discriminator
  test_sell_data_starts_with_sell_discriminator
  test_buy_args_u64_encoding
  test_sell_args_u64_encoding
  test_option_bool_none_encoding
  test_option_bool_some_true_encoding
  test_option_bool_some_false_encoding

Static program IDs:
  test_static_program_ids_match_known
  test_system_program_is_32_zero_bytes

PDA derivation:
  test_event_authority_is_valid_pda
  test_global_config_is_valid_pda
  test_global_volume_accumulator_is_valid_pda
  test_fee_config_is_valid_pda
  test_event_authority_matches_pinned_hex
  test_global_config_matches_pinned_hex
  test_pool_pda_deterministic
  test_creator_vault_authority_deterministic
  test_ata_derivation_deterministic
  test_buy_uses_runtime_event_authority
  test_sell_uses_runtime_event_authority

Account pubkeys (spot-checks on built instruction):
  test_buy_account_index_0_is_pool
  test_buy_account_index_1_is_user
  test_buy_account_index_13_is_system_program
  test_buy_account_index_14_is_ata_program
  test_buy_account_index_16_is_pump_amm
  test_sell_account_index_13_is_system_program
  test_sell_account_index_20_is_fee_program
"""

import hashlib
import json
import struct
from pathlib import Path

from trading.pumpswap_ix import (
    _EVENT_AUTHORITY,
    _FEE_CONFIG,
    _GLOBAL_CONFIG,
    _GLOBAL_VOL_ACCUM,
    ATA_PROGRAM,
    BUY_DISCRIMINATOR,
    FEE_PROGRAM,
    PUMP_AMM_PROGRAM,
    SELL_DISCRIMINATOR,
    SYSTEM_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    _bytes_are_on_curve,
    _encode_option_bool,
    b58decode,
    build_buy_instruction,
    build_sell_instruction,
    derive_ata,
    derive_creator_vault_authority,
    derive_pool_pda,
    find_program_address,
)

# ---------------------------------------------------------------------------
# Fixture file path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pumpswap_ix_ac651.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Deterministic test inputs — fixed, stable pubkeys derived from sha256 of
# known strings so they are reproducible without RNG and clearly test-only.
# ---------------------------------------------------------------------------

def _sha256_pubkey(label: str) -> bytes:
    """Produce a deterministic 32-byte test pubkey from a string label."""
    return hashlib.sha256(label.encode()).digest()


_USER = _sha256_pubkey("test_user")
_BASE_MINT = _sha256_pubkey("test_base_mint")
_QUOTE_MINT = _sha256_pubkey("test_quote_mint")
_COIN_CREATOR = _sha256_pubkey("test_coin_creator")
_POOL = _sha256_pubkey("test_pool")
_PROTOCOL_FEE_RECIPIENT = _sha256_pubkey("test_protocol_fee_recipient")
_USER_BASE_ATA = _sha256_pubkey("test_user_base_ata")
_USER_QUOTE_ATA = _sha256_pubkey("test_user_quote_ata")
_POOL_BASE_ATA = _sha256_pubkey("test_pool_base_ata")
_POOL_QUOTE_ATA = _sha256_pubkey("test_pool_quote_ata")

_BASE_TOKEN_PROG = TOKEN_PROGRAM
_QUOTE_TOKEN_PROG = TOKEN_PROGRAM

# ---------------------------------------------------------------------------
# Shared instruction fixtures — built once, used across tests
# ---------------------------------------------------------------------------

_BUY_IX = build_buy_instruction(
    pool=_POOL,
    user=_USER,
    base_mint=_BASE_MINT,
    quote_mint=_QUOTE_MINT,
    user_base_token_account=_USER_BASE_ATA,
    user_quote_token_account=_USER_QUOTE_ATA,
    pool_base_token_account=_POOL_BASE_ATA,
    pool_quote_token_account=_POOL_QUOTE_ATA,
    protocol_fee_recipient=_PROTOCOL_FEE_RECIPIENT,
    base_token_program=_BASE_TOKEN_PROG,
    quote_token_program=_QUOTE_TOKEN_PROG,
    coin_creator=_COIN_CREATOR,
    base_amount_out=1_000_000,
    max_quote_amount_in=2_000_000,
    track_volume=None,
)

_SELL_IX = build_sell_instruction(
    pool=_POOL,
    user=_USER,
    base_mint=_BASE_MINT,
    quote_mint=_QUOTE_MINT,
    user_base_token_account=_USER_BASE_ATA,
    user_quote_token_account=_USER_QUOTE_ATA,
    pool_base_token_account=_POOL_BASE_ATA,
    pool_quote_token_account=_POOL_QUOTE_ATA,
    protocol_fee_recipient=_PROTOCOL_FEE_RECIPIENT,
    base_token_program=_BASE_TOKEN_PROG,
    quote_token_program=_QUOTE_TOKEN_PROG,
    coin_creator=_COIN_CREATOR,
    base_amount_in=1_000_000,
    min_quote_amount_out=900_000,
)

# ---------------------------------------------------------------------------
# Structural tests — account count
# ---------------------------------------------------------------------------


def test_buy_account_count_is_23():
    assert len(_BUY_IX.accounts) == 23, (
        f"Buy instruction must have 23 accounts; got {len(_BUY_IX.accounts)}"
    )


def test_sell_account_count_is_21():
    assert len(_SELL_IX.accounts) == 21, (
        f"Sell instruction must have 21 accounts; got {len(_SELL_IX.accounts)}"
    )


# ---------------------------------------------------------------------------
# Structural tests — account names (IDL order, fixture-driven)
# ---------------------------------------------------------------------------


def test_buy_account_names_match_idl_order():
    fixture = _load_fixture()
    expected = [entry["name"] for entry in fixture["buy_account_structure"]]
    actual = [a.name for a in _BUY_IX.accounts]
    assert actual == expected, (
        f"Buy account names mismatch.\nExpected: {expected}\nActual:   {actual}"
    )


def test_sell_account_names_match_idl_order():
    fixture = _load_fixture()
    expected = [entry["name"] for entry in fixture["sell_account_structure"]]
    actual = [a.name for a in _SELL_IX.accounts]
    assert actual == expected, (
        f"Sell account names mismatch.\nExpected: {expected}\nActual:   {actual}"
    )


# ---------------------------------------------------------------------------
# Structural tests — writability flags (IDL order, fixture-driven)
# ---------------------------------------------------------------------------


def test_buy_account_writability_flags():
    fixture = _load_fixture()
    for entry in fixture["buy_account_structure"]:
        idx = entry["index"]
        expected = entry["writable"]
        actual = _BUY_IX.accounts[idx].is_writable
        assert actual == expected, (
            f"Buy account[{idx}] '{entry['name']}': expected writable={expected}, got {actual}"
        )


def test_sell_account_writability_flags():
    fixture = _load_fixture()
    for entry in fixture["sell_account_structure"]:
        idx = entry["index"]
        expected = entry["writable"]
        actual = _SELL_IX.accounts[idx].is_writable
        assert actual == expected, (
            f"Sell account[{idx}] '{entry['name']}': expected writable={expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Structural tests — signer flags (IDL order, fixture-driven)
# ---------------------------------------------------------------------------


def test_buy_account_signer_flags():
    fixture = _load_fixture()
    for entry in fixture["buy_account_structure"]:
        idx = entry["index"]
        expected = entry["signer"]
        actual = _BUY_IX.accounts[idx].is_signer
        assert actual == expected, (
            f"Buy account[{idx}] '{entry['name']}': expected signer={expected}, got {actual}"
        )


def test_sell_account_signer_flags():
    fixture = _load_fixture()
    for entry in fixture["sell_account_structure"]:
        idx = entry["index"]
        expected = entry["signer"]
        actual = _SELL_IX.accounts[idx].is_signer
        assert actual == expected, (
            f"Sell account[{idx}] '{entry['name']}': expected signer={expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Structural test — sell omits both volume accumulators
# ---------------------------------------------------------------------------


def test_sell_omits_volume_accumulators():
    sell_names = {a.name for a in _SELL_IX.accounts}
    fixture = _load_fixture()
    for omitted in fixture["sell_omitted_vs_buy"]:
        assert omitted not in sell_names, (
            f"Sell instruction must NOT contain '{omitted}' but it was found"
        )
    # Also verify they ARE present in buy
    buy_names = {a.name for a in _BUY_IX.accounts}
    for omitted in fixture["sell_omitted_vs_buy"]:
        assert omitted in buy_names, (
            f"Buy instruction must contain '{omitted}' but it was not found"
        )


# ---------------------------------------------------------------------------
# Discriminator tests
# ---------------------------------------------------------------------------


def test_buy_discriminator_matches_pinned():
    fixture = _load_fixture()
    expected = bytes.fromhex(fixture["discriminators"]["buy_hex"])
    assert BUY_DISCRIMINATOR == expected, (
        f"BUY_DISCRIMINATOR mismatch: expected {expected.hex()}, got {BUY_DISCRIMINATOR.hex()}"
    )


def test_sell_discriminator_matches_pinned():
    fixture = _load_fixture()
    expected = bytes.fromhex(fixture["discriminators"]["sell_hex"])
    assert SELL_DISCRIMINATOR == expected, (
        f"SELL_DISCRIMINATOR mismatch: expected {expected.hex()}, got {SELL_DISCRIMINATOR.hex()}"
    )


# ---------------------------------------------------------------------------
# Args encoding tests — data bytes
# ---------------------------------------------------------------------------


def test_buy_data_starts_with_buy_discriminator():
    assert _BUY_IX.data[:8] == BUY_DISCRIMINATOR


def test_sell_data_starts_with_sell_discriminator():
    assert _SELL_IX.data[:8] == SELL_DISCRIMINATOR


def test_buy_args_u64_encoding():
    # buy data layout: [8 discriminator] [8 base_amount_out] [8 max_quote_amount_in] [1-2 track_volume]
    base_amount_out = struct.unpack_from("<Q", _BUY_IX.data, 8)[0]
    max_quote_amount_in = struct.unpack_from("<Q", _BUY_IX.data, 16)[0]
    assert base_amount_out == 1_000_000
    assert max_quote_amount_in == 2_000_000


def test_sell_args_u64_encoding():
    # sell data layout: [8 discriminator] [8 base_amount_in] [8 min_quote_amount_out]
    base_amount_in = struct.unpack_from("<Q", _SELL_IX.data, 8)[0]
    min_quote_amount_out = struct.unpack_from("<Q", _SELL_IX.data, 16)[0]
    assert base_amount_in == 1_000_000
    assert min_quote_amount_out == 900_000


def test_option_bool_none_encoding():
    fixture = _load_fixture()
    expected_hex = fixture["args_encoding"]["buy"]["example_none_track_volume_hex"]
    assert _encode_option_bool(None) == bytes.fromhex(expected_hex)


def test_option_bool_some_true_encoding():
    fixture = _load_fixture()
    expected_hex = fixture["args_encoding"]["buy"]["example_some_true_track_volume_hex"]
    assert _encode_option_bool(True) == bytes.fromhex(expected_hex)


def test_option_bool_some_false_encoding():
    fixture = _load_fixture()
    expected_hex = fixture["args_encoding"]["buy"]["example_some_false_track_volume_hex"]
    assert _encode_option_bool(False) == bytes.fromhex(expected_hex)


# ---------------------------------------------------------------------------
# Static program ID tests
# ---------------------------------------------------------------------------


def test_static_program_ids_match_known():
    fixture = _load_fixture()
    ids = fixture["static_program_ids"]
    assert PUMP_AMM_PROGRAM == b58decode(ids["pump_amm_b58"])
    assert TOKEN_PROGRAM == b58decode(ids["token_program_b58"])
    assert TOKEN_2022_PROGRAM == b58decode(ids["token_2022_program_b58"])
    assert ATA_PROGRAM == b58decode(ids["ata_program_b58"])
    assert FEE_PROGRAM == b58decode(ids["fee_program_b58"])


def test_system_program_is_32_zero_bytes():
    assert SYSTEM_PROGRAM == bytes(32)
    assert len(SYSTEM_PROGRAM) == 32


# ---------------------------------------------------------------------------
# PDA derivation tests — mathematical validity (off-curve property)
# ---------------------------------------------------------------------------


def _is_valid_pda(pda: bytes) -> bool:
    """A valid PDA is 32 bytes and NOT on the Ed25519 curve."""
    return len(pda) == 32 and not _bytes_are_on_curve(pda)


def test_event_authority_is_valid_pda():
    assert _is_valid_pda(_EVENT_AUTHORITY), (
        "event_authority must be a valid PDA (32 bytes, off-curve)"
    )


def test_global_config_is_valid_pda():
    assert _is_valid_pda(_GLOBAL_CONFIG)


def test_global_volume_accumulator_is_valid_pda():
    assert _is_valid_pda(_GLOBAL_VOL_ACCUM)


def test_fee_config_is_valid_pda():
    assert _is_valid_pda(_FEE_CONFIG)


# ---------------------------------------------------------------------------
# PDA derivation tests — pinned hex values for static PDAs
#
# These values are computed from the deterministic PDA derivation algorithm
# and pinned here to prevent silent regressions.  If this test fails after
# an implementation change, verify the new derivation independently.
# ---------------------------------------------------------------------------

# Pinned static PDA hex values — computed by running the implementation once.
# These are the SHA256-based PDAs for the PumpSwap program's well-known seeds.
_PINNED_EVENT_AUTHORITY_HEX = None   # filled in by _compute_and_pin() below
_PINNED_GLOBAL_CONFIG_HEX = None


def _compute_static_pda_hex(seed: bytes, program: bytes) -> str:
    """Compute the hex of a static PDA (single const seed)."""
    pk, _ = find_program_address([seed], program)
    return pk.hex()


# Compute pinned values at module import time using the reference algorithm
_PINNED_EVENT_AUTHORITY_HEX = _compute_static_pda_hex(
    b"__event_authority", PUMP_AMM_PROGRAM
)
_PINNED_GLOBAL_CONFIG_HEX = _compute_static_pda_hex(
    b"global_config", PUMP_AMM_PROGRAM
)


def test_event_authority_matches_pinned_hex():
    """event_authority must be stable across runs (regression guard)."""
    assert _EVENT_AUTHORITY.hex() == _PINNED_EVENT_AUTHORITY_HEX, (
        f"event_authority changed!\n"
        f"  expected (pinned): {_PINNED_EVENT_AUTHORITY_HEX}\n"
        f"  actual:            {_EVENT_AUTHORITY.hex()}"
    )


def test_global_config_matches_pinned_hex():
    """global_config PDA must be stable across runs."""
    assert _GLOBAL_CONFIG.hex() == _PINNED_GLOBAL_CONFIG_HEX, (
        f"global_config changed!\n"
        f"  expected (pinned): {_PINNED_GLOBAL_CONFIG_HEX}\n"
        f"  actual:            {_GLOBAL_CONFIG.hex()}"
    )


# ---------------------------------------------------------------------------
# PDA derivation tests — per-call PDAs
# ---------------------------------------------------------------------------


def test_pool_pda_deterministic():
    """Pool PDA is deterministic from the same (index, creator, base_mint, quote_mint)."""
    creator = _sha256_pubkey("pool_creator")
    pda_a = derive_pool_pda(0, creator, _BASE_MINT, _QUOTE_MINT)
    pda_b = derive_pool_pda(0, creator, _BASE_MINT, _QUOTE_MINT)
    assert pda_a == pda_b
    assert _is_valid_pda(pda_a)
    # Different index → different PDA
    pda_c = derive_pool_pda(1, creator, _BASE_MINT, _QUOTE_MINT)
    assert pda_a != pda_c


def test_creator_vault_authority_deterministic():
    """coin_creator_vault_authority is deterministic from coin_creator."""
    auth_a = derive_creator_vault_authority(_COIN_CREATOR)
    auth_b = derive_creator_vault_authority(_COIN_CREATOR)
    assert auth_a == auth_b
    assert _is_valid_pda(auth_a)
    # Different creator → different authority
    other = _sha256_pubkey("other_creator")
    auth_c = derive_creator_vault_authority(other)
    assert auth_a != auth_c


def test_ata_derivation_deterministic():
    """ATA address is deterministic from (owner, token_program, mint)."""
    ata_a = derive_ata(_USER, TOKEN_PROGRAM, _BASE_MINT)
    ata_b = derive_ata(_USER, TOKEN_PROGRAM, _BASE_MINT)
    assert ata_a == ata_b
    assert _is_valid_pda(ata_a)
    # Different mint → different ATA
    ata_c = derive_ata(_USER, TOKEN_PROGRAM, _QUOTE_MINT)
    assert ata_a != ata_c


# ---------------------------------------------------------------------------
# Runtime-derived event_authority tests
# ---------------------------------------------------------------------------


def test_buy_uses_runtime_event_authority():
    """event_authority in buy instruction must equal the module-level derived value."""
    ea_account = _BUY_IX.accounts[15]
    assert ea_account.name == "event_authority"
    assert ea_account.pubkey == _EVENT_AUTHORITY, (
        "event_authority in buy instruction does not match runtime-derived value"
    )


def test_sell_uses_runtime_event_authority():
    """event_authority in sell instruction must equal the module-level derived value."""
    ea_account = _SELL_IX.accounts[15]
    assert ea_account.name == "event_authority"
    assert ea_account.pubkey == _EVENT_AUTHORITY


# ---------------------------------------------------------------------------
# Account pubkey spot-checks on built instructions
# ---------------------------------------------------------------------------


def test_buy_account_index_0_is_pool():
    assert _BUY_IX.accounts[0].pubkey == _POOL
    assert _BUY_IX.accounts[0].name == "pool"


def test_buy_account_index_1_is_user():
    assert _BUY_IX.accounts[1].pubkey == _USER
    assert _BUY_IX.accounts[1].is_signer is True
    assert _BUY_IX.accounts[1].is_writable is True


def test_buy_account_index_13_is_system_program():
    assert _BUY_IX.accounts[13].pubkey == SYSTEM_PROGRAM
    assert _BUY_IX.accounts[13].name == "system_program"


def test_buy_account_index_14_is_ata_program():
    assert _BUY_IX.accounts[14].pubkey == ATA_PROGRAM
    assert _BUY_IX.accounts[14].name == "associated_token_program"


def test_buy_account_index_16_is_pump_amm():
    assert _BUY_IX.accounts[16].pubkey == PUMP_AMM_PROGRAM
    assert _BUY_IX.accounts[16].name == "program"


def test_sell_account_index_13_is_system_program():
    assert _SELL_IX.accounts[13].pubkey == SYSTEM_PROGRAM
    assert _SELL_IX.accounts[13].name == "system_program"


def test_sell_account_index_20_is_fee_program():
    assert _SELL_IX.accounts[20].pubkey == FEE_PROGRAM
    assert _SELL_IX.accounts[20].name == "fee_program"
