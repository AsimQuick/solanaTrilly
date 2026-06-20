# ---
# module: trading.tx_signer
# sprint: sprint-15
# story: US-80 (live execution — transaction signer)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: solders, base64, os
# ---
"""Transaction signer for the live PumpSwap path (US-80) — the missing capital piece.

The pumpswap_ix builders (US-65) produce vendored ``Instruction`` dataclasses
(raw-bytes program_id / account pubkeys); ``ExecutionCore.execute_buy/sell`` want a
**base64-encoded SIGNED transaction**.  This module bridges that gap: it converts
the vendored instructions to solders instructions, compiles a v0 message (fee payer
= the trading wallet), signs with the wallet keypair, and returns the base64 wire
bytes.  ``trading.sender`` then submits those bytes to Helius.

CAPITAL SAFETY:
  - This module performs **NO network I/O** — no RPC, no send.  It is pure
    assemble-and-sign given a recent blockhash (fetched elsewhere, on the live
    path only).  Offline-testable end-to-end with a throwaway keypair.
  - The keypair is loaded from the ``TRADING_WALLET_PRIVATE_KEY`` env var ONLY —
    never from the repo, never logged.  Absent/empty -> the loader returns None and
    the live path stays disabled (callers must gate on trading_enabled anyway).
  - Nothing here is reachable unless the operator sets trading_enabled=True AND a
    wallet key is provisioned; the paper path never imports this module.

Key formats accepted (TRADING_WALLET_KEY):
  - base58 secret string (Phantom/solana-keygen export), or
  - JSON array of 64 ints (solana-keygen file contents).
"""
from __future__ import annotations

import base64
import json
import os
from typing import Optional

#: Env var holding the trading wallet secret (base58 secret or JSON int array).
#: Provisioned in the VPS + local .env (gitignored); NEVER committed or logged.
WALLET_ENV_VAR = "TRADING_WALLET_KEY"


def load_keypair(secret: Optional[str] = None):
    """Load the trading wallet ``Keypair`` from *secret* or the env var.

    Returns ``None`` when no key is configured (the live path stays disabled) so a
    missing key is a safe no-op, not a crash.  Accepts a base58 string or a JSON
    int array.  Never logs the secret.
    """
    from solders.keypair import Keypair

    raw = secret if secret is not None else os.environ.get(WALLET_ENV_VAR, "")
    raw = (raw or "").strip()
    if not raw:
        return None
    # JSON byte-array form (solana-keygen file contents).
    if raw.startswith("["):
        arr = json.loads(raw)
        return Keypair.from_bytes(bytes(arr))
    # base58 secret string.
    return Keypair.from_base58_string(raw)


def _to_solders_instruction(ix):
    """Convert a vendored pumpswap_ix.Instruction into a solders Instruction."""
    from solders.instruction import AccountMeta as SAccountMeta
    from solders.instruction import Instruction as SInstruction
    from solders.pubkey import Pubkey

    metas = [
        SAccountMeta(
            pubkey=Pubkey.from_bytes(bytes(a.pubkey)),
            is_signer=bool(a.is_signer),
            is_writable=bool(a.is_writable),
        )
        for a in ix.accounts
    ]
    return SInstruction(
        program_id=Pubkey.from_bytes(bytes(ix.program_id)),
        data=bytes(ix.data),
        accounts=metas,
    )


def build_signed_tx_b64(
    instructions: list,
    *,
    payer_keypair,
    recent_blockhash: str,
    extra_signers: Optional[list] = None,
) -> str:
    """Compile + sign a v0 transaction from vendored instructions; return base64 bytes.

    Pure (no network).  ``recent_blockhash`` is a base58 blockhash string fetched on
    the live path; ``payer_keypair`` is the fee payer + buyer/seller.  The returned
    string is exactly what ``ExecutionCore.execute_buy/sell`` -> ``Sender`` submit.

    Args:
        instructions:     ordered list of vendored pumpswap_ix.Instruction (e.g.
                          [compute-budget, ATA-create, pumpswap buy]).
        payer_keypair:    solders Keypair (fee payer / wallet).
        recent_blockhash: base58 blockhash string (from getLatestBlockhash).
        extra_signers:    additional solders Keypairs that must co-sign (usually none).

    Returns:
        base64-encoded signed VersionedTransaction wire bytes.
    """
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.transaction import VersionedTransaction

    s_ixs = [_to_solders_instruction(ix) for ix in instructions]
    blockhash = Hash.from_string(recent_blockhash)
    message = MessageV0.try_compile(
        payer=payer_keypair.pubkey(),
        instructions=s_ixs,
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )
    signers = [payer_keypair, *(extra_signers or [])]
    tx = VersionedTransaction(message, signers)
    return base64.b64encode(bytes(tx)).decode("ascii")


def wallet_pubkey_str(keypair) -> str:
    """Return the wallet's base58 public key (safe to log / show the operator)."""
    return str(keypair.pubkey())
