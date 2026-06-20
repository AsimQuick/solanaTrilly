# ---
# module: trading.tests.test_tx_signer_us80
# sprint: sprint-15
# story: US-80 (live execution — transaction signer)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, solders, trading.tx_signer, trading.pumpswap_ix
# ---
"""US-80 — transaction signer (offline, capital-safe).

All tests use a throwaway generated keypair and a dummy blockhash; nothing is sent.
They prove the signer loads keys from both formats, converts vendored instructions,
and produces a verifiable signed VersionedTransaction round-trip.
"""
from __future__ import annotations

import base64
import json

from trading import tx_signer
from trading.pumpswap_ix import AccountMeta, Instruction


def _dummy_blockhash() -> str:
    from solders.hash import Hash

    return str(Hash.default())  # all-zero hash, valid base58 — fine for offline signing


def _sample_instruction(payer_pubkey_bytes: bytes) -> Instruction:
    # A trivial 1-account instruction referencing the payer (writable signer).
    return Instruction(
        program_id=bytes(32),
        accounts=[AccountMeta(pubkey=payer_pubkey_bytes, is_writable=True, is_signer=True)],
        data=b"\x01\x02\x03",
    )


def test_load_keypair_absent_returns_none(monkeypatch):
    monkeypatch.delenv(tx_signer.WALLET_ENV_VAR, raising=False)
    assert tx_signer.load_keypair() is None
    assert tx_signer.load_keypair("") is None
    assert tx_signer.load_keypair("   ") is None


def test_load_keypair_base58_roundtrip():
    from solders.keypair import Keypair

    kp = Keypair()
    b58 = str(kp)  # solders Keypair str() is the base58 secret
    loaded = tx_signer.load_keypair(b58)
    assert loaded is not None
    assert loaded.pubkey() == kp.pubkey()


def test_load_keypair_json_array():
    from solders.keypair import Keypair

    kp = Keypair()
    as_json = json.dumps(list(bytes(kp)))
    loaded = tx_signer.load_keypair(as_json)
    assert loaded.pubkey() == kp.pubkey()


def test_load_keypair_from_env(monkeypatch):
    from solders.keypair import Keypair

    kp = Keypair()
    monkeypatch.setenv(tx_signer.WALLET_ENV_VAR, str(kp))
    loaded = tx_signer.load_keypair()
    assert loaded.pubkey() == kp.pubkey()


def test_build_signed_tx_is_verifiable_roundtrip():
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction

    kp = Keypair()
    ix = _sample_instruction(bytes(kp.pubkey()))
    b64 = tx_signer.build_signed_tx_b64(
        [ix], payer_keypair=kp, recent_blockhash=_dummy_blockhash()
    )
    # It is valid base64 and deserializes to a signed VersionedTransaction.
    raw = base64.b64decode(b64)
    tx = VersionedTransaction.from_bytes(raw)
    # Fee payer is account index 0 and matches our wallet.
    assert tx.message.account_keys[0] == kp.pubkey()
    # Exactly one signature, and it is present (non-default).
    assert len(tx.signatures) == 1
    from solders.signature import Signature

    assert tx.signatures[0] != Signature.default()


def test_signing_is_deterministic_and_payer_signs():
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction

    kp = Keypair()
    ix = _sample_instruction(bytes(kp.pubkey()))
    bh = _dummy_blockhash()
    a = tx_signer.build_signed_tx_b64([ix], payer_keypair=kp, recent_blockhash=bh)
    b = tx_signer.build_signed_tx_b64([ix], payer_keypair=kp, recent_blockhash=bh)
    # ed25519 over the same message is deterministic -> identical wire bytes.
    assert a == b
    # The signer of record is the payer (account_keys[0]).
    tx = VersionedTransaction.from_bytes(base64.b64decode(a))
    assert tx.message.account_keys[0] == kp.pubkey()
    # A different wallet produces different signed bytes (binding sanity).
    other_kp = Keypair()
    other = tx_signer.build_signed_tx_b64(
        [_sample_instruction(bytes(other_kp.pubkey()))],
        payer_keypair=other_kp,
        recent_blockhash=bh,
    )
    assert other != a


def test_wallet_pubkey_str_is_base58():
    from solders.keypair import Keypair

    kp = Keypair()
    s = tx_signer.wallet_pubkey_str(kp)
    assert s == str(kp.pubkey())
    assert 32 <= len(s) <= 44  # base58 pubkey length range
