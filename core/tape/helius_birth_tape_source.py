# ---
# module: core.tape.helius_birth_tape_source
# sprint: sprint-8
# story: US-34 AC-34.1, EPIC-graduation-migrate-detection,
#        hotfix-single-connection-fanout, hotfix-migrate-mint-rpc-resolution
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-22
# dependencies: core.datasource, core.clock, base64, hashlib, struct, json,
#               logging, typing, asyncio, urllib.request, urllib.error
# ---
"""HeliusBirthTapeSource — program-wide Helius transactionSubscribe DataSource.

Subscribes to ALL pump.fun (bonding-curve) program transactions via a Helius
WebSocket transactionNotification stream (one connection, zero selection bias).
Decodes the Anchor TradeEvent discriminator embedded in each transaction's log
messages to extract buy/sell swap data for PRE-graduation tokens.

This source produces internal swap dicts that are NormalizedSwap-compatible
when passed through TapeRecorder.  It is tagged source='helius_live' and is
ADDITIVE alongside birdeye_live / birdeye_backfill / helius_verify.

IMPORTANT architectural constraints (enforced by test_tape_recorder_ac181.py):
- No import of LiveSource, ReplaySource, core.live_source, core.replay_source
- No top-level `import websockets` — lazy import inside connect() only
- No datetime.now() or time.time() calls anywhere in this file

Borsh TradeEvent layout (pump.fun / Anchor):
  disc(8) + mint(32) + sol_amount(u64) + token_amount(u64) + is_buy(1)
  + user(32) + timestamp(i64) + vsol(u64) + vtok(u64)  = 113 bytes minimum

MIGRATE DETECTION (hotfix-migrate-mint-rpc-resolution)
=======================================================
decode_helius_migrate_event() is a PURE detector for the pump.fun 'migrate'
instruction.  It detects the migrate event and extracts ALL transaction
accountKeys as candidate_accounts, but does NOT resolve the SPL mint — the
old innerInstructions heuristic (accounts[2] of the largest 6EF8 CPI) was
returning bonding-curve/fee PDAs ~98% of the time, not real SPL mints.

SPL mint resolution is performed by resolve_spl_mint(), a sync function that
calls getMultipleAccounts via the Helius HTTPS RPC and identifies the unique
SPL-Token-program-owned account whose type is "mint" (excluding WSOL and USDC).
Validated on 6/6 failing frames including non-"pump"-suffix mints.

HeliusMigrateSource._decode_and_dedupe() is async: it runs the pure decode
then dispatches resolve_spl_mint() via asyncio.to_thread() so the blocking
HTTP call never stalls the event loop.

HeliusMigrateSource is a separate DataSource (not a fan-out of
HeliusBirthTapeSource) using the IDENTICAL subscription parameters.  It
emits MEME_DATA graduation dicts shaped for DetectionConsumer, stamped
dex_source="helius_migrate".
"""
import asyncio
import base64
import hashlib
import json
import logging
import struct
import time
import urllib.error
import urllib.request
from typing import Any, AsyncGenerator

from core.clock import Clock, WallClock
from core.datasource import DataSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: pump.fun bonding-curve program address (the program-wide subscription target)
PUMP_FUN_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: Helius mainnet WebSocket endpoint for transactionSubscribe.
#: Live-window finding: the `atlas-mainnet` enhanced endpoint connected but
#: streamed ZERO transactionNotification frames under this account's plan, so
#: the birth-tape collected nothing. The working reference (solanaBilly,
#: app/services/helius_listener.py) drives the SAME `transactionSubscribe`
#: against the STANDARD `wss://mainnet.helius-rpc.com/?api-key=…` endpoint —
#: match it. Do NOT revert to atlas-mainnet without confirming the plan serves
#: Atlas/Geyser transactionSubscribe for this key.
HELIUS_WS_URL: str = "wss://mainnet.helius-rpc.com"

#: Wrapped SOL mint — quote currency for all pump.fun swaps
WSOL_MINT: str = "So11111111111111111111111111111111111111112"

#: Helius mainnet HTTPS RPC endpoint for getMultipleAccounts calls.
#: Distinct from HELIUS_WS_URL (which is the WebSocket endpoint).
HELIUS_RPC_URL: str = "https://mainnet.helius-rpc.com"

#: SPL Token program address — owns real SPL mints.
_SPL_TOKEN_PROGRAM: str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

#: SPL Token-2022 program address — owns Token-2022 mints.
_SPL_TOKEN_2022_PROGRAM: str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

#: USDC mint — excluded from SPL mint candidates (stablecoin, not the graduated token).
_USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

#: Anchor event discriminator = sha256("event:TradeEvent")[:8]
#: Verified bit-exact against real pump.fun trades.
TRADE_EVENT_DISCRIMINATOR: bytes = hashlib.sha256(b"event:TradeEvent").digest()[:8]

#: Minimum byte length of the stable TradeEvent prefix we read:
#: disc(8)+mint(32)+sol(8)+token(8)+is_buy(1)+user(32)+ts(8)+vsol(8)+vtok(8) = 113
#: pump.fun has appended fields over time; we only read this prefix (forward-compatible).
_TRADE_EVENT_MIN_LEN: int = 113

#: Log line prefix that marks an emitted Anchor event
_PROGRAM_DATA_PREFIX: str = "Program data: "

#: Source tag for NormalizedSwap provenance (AC-34.1)
SOURCE_TAG: str = "helius_live"

# ---------------------------------------------------------------------------
# Migrate-detection constants (EPIC-graduation-migrate-detection / hotfix-migrate-mint-rpc-resolution)
# ---------------------------------------------------------------------------

#: PumpSwap AMM program address (the sole pump.fun graduation destination).
PUMP_AMM_PROGRAM: str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

#: Log line that indicates a migrate instruction in the pump.fun program.
_MIGRATE_LOG_MARKER: str = "Instruction: Migrate"

#: dex_source stamp for migrate-detected graduation events.
MIGRATE_DEX_SOURCE: str = "helius_migrate"

# ---------------------------------------------------------------------------
# Base58 encoding — standalone implementation (solders NOT in requirements.txt)
# ---------------------------------------------------------------------------

_BASE58_ALPHABET: bytes = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58_from_bytes(b: bytes) -> str:
    """Encode raw bytes as a base58 string (Bitcoin/Solana pubkey style).

    Args:
        b: Raw bytes to encode (typically 32 bytes for a Solana pubkey).

    Returns:
        Base58-encoded string.
    """
    n = int.from_bytes(b, "big")
    result: list[str] = []
    while n > 0:
        n, remainder = divmod(n, 58)
        result.append(chr(_BASE58_ALPHABET[remainder]))
    # Count leading zero bytes — each maps to the first base58 char ('1')
    leading_zeros = len(b) - len(b.lstrip(b"\x00"))
    return "1" * leading_zeros + "".join(reversed(result))


# ---------------------------------------------------------------------------
# Pure helper functions — no I/O, no time, no randomness
# ---------------------------------------------------------------------------


def _result_envelope(data: dict) -> dict | None:
    """Navigate to params.result of a transactionNotification, or None."""
    try:
        return data["params"]["result"]
    except (KeyError, TypeError):
        return None


def _extract_log_messages(result: dict) -> list[str]:
    """Extract logMessages from a transaction result dict, or empty list."""
    try:
        return result["transaction"]["meta"]["logMessages"] or []
    except (KeyError, TypeError):
        return []


def _meta_err(result: dict) -> object:
    """Return the meta.err value from a transaction result dict, or None."""
    try:
        return result["transaction"]["meta"].get("err")
    except (KeyError, TypeError, AttributeError):
        return None


def _signature(result: dict) -> str | None:
    """Extract the first transaction signature from a result dict, or None."""
    sig = result.get("signature")
    if isinstance(sig, str):
        return sig
    try:
        sigs = result["transaction"]["transaction"]["signatures"]
        return sigs[0] if sigs else None
    except (KeyError, TypeError, IndexError):
        return None


def _is_trade_log(log_messages: list[str]) -> bool:
    """Return True if log_messages contain a pump.fun Buy or Sell instruction.

    Checks for 'Instruction: Buy' or 'Instruction: Sell' substring in any
    log message.  Create instructions ('Instruction: Create') are NOT trades.
    """
    for m in log_messages:
        if "Instruction: Buy" in m or "Instruction: Sell" in m:
            return True
    return False


def _is_migrate_log(log_messages: list[str]) -> bool:
    """Return True if log_messages contain the pump.fun Migrate instruction marker.

    A migrate transaction always emits 'Instruction: Migrate' as part of its
    6EF8rrecthR5… program log.  Verified on 3 real transactionNotification frames
    (2026-06-21).  Returns False for trade (Buy/Sell) and create frames.
    """
    for m in log_messages:
        if _MIGRATE_LOG_MARKER in m:
            return True
    return False


def extract_migrate_account_candidates(data: dict) -> list[str]:
    """Extract all account pubkeys from a migrate transactionNotification result.

    Returns the flat list of pubkey strings from
    transaction.transaction.message.accountKeys (the full account list for the
    migrate transaction).  These are the candidates passed to resolve_spl_mint()
    via getMultipleAccounts to identify the real SPL mint.

    Pure function — no I/O, no time, no randomness.  Handles both dict-form
    accountKeys (jsonParsed encoding, each element is {"pubkey": "...", ...})
    and string-form accountKeys (legacy encoding, each element is a pubkey string).

    Args:
        data: Raw dict from the Helius WebSocket frame (already JSON-parsed).
              Expected to be a transactionNotification result dict (i.e., already
              navigated to params.result or the full frame — the function looks
              for both shapes).

    Returns:
        List of pubkey strings (may be empty if structure not found).
    """
    # Accept both the full frame (with params.result) and a bare result dict.
    result = _result_envelope(data)
    if result is None:
        # Try treating data itself as the result (bare result dict in tests).
        result = data

    try:
        account_keys = result["transaction"]["transaction"]["message"]["accountKeys"]
    except (KeyError, TypeError):
        return []

    if not isinstance(account_keys, list):
        return []

    pubkeys: list[str] = []
    for entry in account_keys:
        if isinstance(entry, str):
            pubkeys.append(entry)
        elif isinstance(entry, dict):
            pk = entry.get("pubkey")
            if isinstance(pk, str) and pk:
                pubkeys.append(pk)
    return pubkeys


def resolve_spl_mint(
    account_keys: list[str],
    api_key: str,
    *,
    endpoint: str = HELIUS_RPC_URL,
    timeout_s: float = 15.0,
) -> "str | None":
    """Resolve the real SPL mint from a migrate transaction's account keys.

    Calls getMultipleAccounts (ONE RPC call) over the migrate transaction's
    account key list and identifies the single account owned by the SPL Token
    program (or Token-2022) whose parsed type is "mint", excluding WSOL and USDC.

    Uses urllib.request (NOT requests — not in the prod image).  Mirrors the
    pattern of core/pricing/sol_usd.py and core/backfill/birdeye_backfill.py.

    Args:
        account_keys: All pubkey strings from the migrate transaction's
                      message.accountKeys (from extract_migrate_account_candidates).
        api_key:      Helius API key (appended as ?api-key= query param).
        endpoint:     Helius HTTPS RPC base URL (default: HELIUS_RPC_URL).
        timeout_s:    HTTP timeout in seconds (default 15.0).

    Returns:
        The single SPL mint pubkey string if exactly one is found, or None:
          - Zero matches: logs a warning and returns None.
          - Multiple matches: logs a warning and returns None (ambiguous).
          - RPC error / HTTP error / JSON decode error: returns None (safe).
    """
    if not account_keys or not api_key:
        return None

    url = f"{endpoint}/?api-key={api_key}"
    _SPL_PROGRAMS = {_SPL_TOKEN_PROGRAM, _SPL_TOKEN_2022_PROGRAM}
    _EXCLUDED_MINTS = {WSOL_MINT, _USDC_MINT}

    # COMMITMENT (critical, found live 2026-06-22): a migrate notification arrives
    # at "confirmed".  getMultipleAccounts defaults to "finalized", which lags
    # ~13s behind and reads freshly-confirmed accounts as null — the mint is then
    # missed and the graduation wrongly skipped (observed: ~94% of live grads
    # skipped at finalized vs resolved at confirmed; the offline 147/149 passed
    # only because those frames were long-finalized).  Query at "confirmed" to
    # match the notification level.  Retry ONCE after a short delay to absorb
    # confirmed-slot propagation lag across RPC nodes (this runs in a worker
    # thread via asyncio.to_thread, so time.sleep does not block the event loop).
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleAccounts",
        "params": [
            account_keys,
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    }).encode()

    for attempt in range(2):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode()
            data = json.loads(body)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TypeError) as exc:
            logger.warning("[FIREHOSE] resolve_spl_mint: RPC error (%s) — returning None.", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[FIREHOSE] resolve_spl_mint: unexpected error (%s) — returning None.", exc)
            return None

        try:
            values = data["result"]["value"]
        except (KeyError, TypeError):
            logger.warning("[FIREHOSE] resolve_spl_mint: unexpected RPC response shape — returning None.")
            return None
        if not isinstance(values, list):
            return None

        mints_found: list[tuple[str, object]] = []  # (pubkey, mintAuthority)
        for i, entry in enumerate(values):
            if not isinstance(entry, dict):
                continue
            if entry.get("owner") not in _SPL_PROGRAMS:
                continue
            try:
                parsed = entry["data"]["parsed"]
                if parsed.get("type") != "mint":
                    continue
                authority = (parsed.get("info") or {}).get("mintAuthority")
            except (KeyError, TypeError, AttributeError):
                continue
            if i >= len(account_keys):
                continue
            pubkey = account_keys[i]
            if pubkey in _EXCLUDED_MINTS:
                continue
            mints_found.append((pubkey, authority))

        if len(mints_found) == 1:
            return mints_found[0][0]
        if len(mints_found) > 1:
            # Disambiguate the pump.fun TOKEN mint from the freshly-created
            # PumpSwap pool LP mint (both visible at "confirmed").  The token mint
            # has a RENOUNCED (null) mint authority; the LP mint has a non-null
            # authority (the pool) and supply 0.  Prefer the renounced mint.
            # Without this, ~13% of grads skip as "ambiguous" (found live 2026-06-22).
            renounced = [pk for pk, auth in mints_found if auth is None]
            if len(renounced) == 1:
                return renounced[0]
            logger.warning(
                "[FIREHOSE] resolve_spl_mint: ambiguous — %d SPL mints (%d renounced) (%s) — returning None.",
                len(mints_found),
                len(renounced),
                [pk for pk, _ in mints_found][:3],
            )
            return None
        # Zero mints found.  Retry once after a short delay (confirmed-slot
        # propagation lag); only give up after the retry.
        if attempt == 0:
            time.sleep(1.5)
            continue
        logger.warning(
            "[FIREHOSE] resolve_spl_mint: no SPL mint found among %d candidates "
            "(after retry) — returning None.",
            len(account_keys),
        )
        return None

    return None


# ---------------------------------------------------------------------------
# Core Borsh decode — pure, no I/O
# ---------------------------------------------------------------------------


def decode_helius_trade_event(decoded: bytes) -> dict | None:
    """Borsh-decode the stable TradeEvent prefix from already-base64-decoded bytes.

    Returns a field dict on success, or None if the discriminator doesn't match
    or the buffer is too short.  Pure + side-effect-free (unit-testable without
    any envelope).

    Borsh layout after the 8-byte discriminator:
      mint(32) | sol_amount(u64) token_amount(u64) | is_buy(1) | user(32) |
      timestamp(i64) virtual_sol(u64) virtual_token(u64)

    Args:
        decoded: Raw bytes (already base64-decoded from the Program data: log line).

    Returns:
        Dict with keys: mint, sol_amount, token_amount, side, owner, block_time,
        virtual_sol_reserves, virtual_token_reserves — or None on failure.
    """
    if len(decoded) < _TRADE_EVENT_MIN_LEN or decoded[:8] != TRADE_EVENT_DISCRIMINATOR:
        return None
    # Layout after discriminator:
    #   [8:40]   mint bytes (32)
    #   [40:48]  sol_amount (u64 LE)
    #   [48:56]  token_amount (u64 LE)
    #   [56]     is_buy (u8)
    #   [57:89]  user bytes (32)
    #   [89:97]  timestamp (i64 LE)
    #   [97:105] virtual_sol_reserves (u64 LE)
    #   [105:113] virtual_token_reserves (u64 LE)
    mint: str = _b58_from_bytes(decoded[8:40])
    sol_amount: int
    token_amount: int
    sol_amount, token_amount = struct.unpack_from("<QQ", decoded, 40)
    is_buy: bool = bool(decoded[56])
    user: str = _b58_from_bytes(decoded[57:89])
    block_time: int
    vsol: int
    vtok: int
    block_time, vsol, vtok = struct.unpack_from("<qQQ", decoded, 89)
    return {
        "mint": mint,
        "sol_amount": sol_amount,
        "token_amount": token_amount,
        "side": "buy" if is_buy else "sell",
        "owner": user,
        "block_time": block_time,
        "virtual_sol_reserves": vsol,
        "virtual_token_reserves": vtok,
    }


# ---------------------------------------------------------------------------
# Notification mapper — the mapper for MappedSwapSource
# ---------------------------------------------------------------------------


def decode_helius_notification(data: dict) -> dict | None:
    """Map a raw Helius transactionNotification dict to an internal swap dict.

    This is the mapper function for MappedSwapSource.  Returns None for:
    - Non-transactionNotification messages (subscription acks, heartbeats)
    - Failed transactions (meta.err is not None — landed-only tape, §6.2)
    - Transactions without a pump.fun Buy/Sell instruction log
    - Any structural decode failure

    The returned dict is NormalizedSwap-compatible via NormalizedSwap.from_raw_swap().
    Price is derived from virtual reserves: vsol / vtok (lamports ratio).
    vol_sol is sol_amount / 1e9 (lamports → SOL).
    vol_usd and sol_usd are 0.0 — the Helius live path has no USD price oracle;
    callers that need USD enrichment must apply it downstream.

    Args:
        data: Raw dict from the Helius WebSocket frame (already JSON-parsed).

    Returns:
        Internal swap dict with keys matching NormalizedSwap.from_raw_swap()
        expectations, or None if the event should be skipped.
    """
    # Only process transactionNotification method frames
    if not isinstance(data, dict) or data.get("method") != "transactionNotification":
        return None

    result = _result_envelope(data)
    if result is None:
        return None

    # Landed-only: drop failed transactions (§6.2)
    if _meta_err(result) is not None:
        return None

    logs = _extract_log_messages(result)
    if not logs or not _is_trade_log(logs):
        return None

    # Walk log lines looking for the Anchor Program data: event
    for log_line in logs:
        if not log_line.startswith(_PROGRAM_DATA_PREFIX):
            continue
        try:
            raw_bytes = base64.b64decode(log_line[len(_PROGRAM_DATA_PREFIX):])
        except Exception:
            continue

        fields = decode_helius_trade_event(raw_bytes)
        if fields is None:
            continue

        vsol: int = fields["virtual_sol_reserves"]
        vtok: int = fields["virtual_token_reserves"]
        # Avoid ZeroDivisionError on degenerate reserve snapshots
        price: float = float(vsol) / float(vtok) if vtok > 0 else 0.0
        vol_sol: float = fields["sol_amount"] / 1e9

        slot_raw = result.get("slot")
        slot: int = int(slot_raw) if slot_raw is not None else 0

        sig = _signature(result)

        return {
            "mint": fields["mint"],
            "block_time": fields["block_time"],
            "slot": slot,
            "signature": sig or "",
            "side": fields["side"],
            "price": price,
            "vol_sol": vol_sol,
            "vol_usd": 0.0,
            "sol_usd": 0.0,
            "owner": fields["owner"],
            "base_reserve": int(vtok),
            "quote_reserve": int(vsol),
            "quote_mint": WSOL_MINT,
            "failed": False,
        }

    return None


# ---------------------------------------------------------------------------
# Migrate event decoder — pure, no I/O (EPIC-graduation-migrate-detection)
# ---------------------------------------------------------------------------


def decode_helius_migrate_event(
    data: dict,
    *,
    event_source: str = "pump_dot_fun",
    fallback_epoch: int = 0,
) -> dict | None:
    """Detect a pump.fun migrate and return a PARTIALLY-resolved MEME_DATA event.

    This function is now a PURE DETECTOR: it detects the migrate transaction and
    extracts all transaction accountKeys as candidate_accounts, but does NOT
    resolve the SPL mint.  The old innerInstructions heuristic (accounts[2] of
    the largest 6EF8 CPI) was returning bonding-curve/fee PDAs ~98% of the time.

    SPL mint resolution is delegated to resolve_spl_mint(), called by
    HeliusMigrateSource._decode_and_dedupe() via asyncio.to_thread().

    Detection criteria:
      - method == "transactionNotification"
      - meta.err is None (landed transactions only)
      - meta.logMessages contains "Instruction: Migrate"

    Emitted event shape (matches DetectionConsumer._is_graduation_event checks):
      {
        "type":                 "MEME_DATA",
        "graduated":            True,
        "address":              "",                  # UNRESOLVED — set by caller
        "candidate_accounts":   [<pubkey>, ...],     # for resolve_spl_mint()
        "source":               <event_source>,
        "poolAddress":          "",
        "blockTime":            <fallback_epoch>,
        "graduated_block_time": <fallback_epoch>,
        "creation_time":        0,
        "progress_percent":     0.0,
        "decimals":             None,
        "raw":                  <transaction result dict>,
        "dex_source":           "helius_migrate",
        "slot":                 <slot int>,
        "signature":            <sig str>,
      }

    Args:
        data:           Raw dict from the Helius WebSocket frame (already JSON-parsed).
        event_source:   The "source" stamp value; must match config.detection.filter.source
                        ("pump_dot_fun") for DetectionConsumer to persist the Token row.
        fallback_epoch: Unix epoch seconds to use for blockTime and graduated_block_time.
                        A migrate notification arrives within ~1 s of the block, so the
                        caller-supplied wall-clock arrival time is second-accurate.

    Returns:
        A partially-resolved MEME_DATA event dict (address="", candidate_accounts=[...]),
        or None if this frame is not a migrate.  The caller MUST resolve address via
        resolve_spl_mint(event["candidate_accounts"], api_key) before using the event.
    """
    # Only process transactionNotification frames
    if not isinstance(data, dict) or data.get("method") != "transactionNotification":
        return None

    result = _result_envelope(data)
    if result is None:
        return None

    # Landed-only: drop failed transactions
    if _meta_err(result) is not None:
        return None

    logs = _extract_log_messages(result)
    if not logs or not _is_migrate_log(logs):
        return None

    # Real slot for audit/ordering (NOT used as a timestamp — the 1983 bug set
    # blockTime to the slot number, producing 1983-07-25 in the DB).
    slot_raw = result.get("slot")
    slot: int = int(slot_raw) if slot_raw is not None else 0

    sig = _signature(result) or ""

    # Extract all accountKeys as candidates for RPC-based SPL mint resolution.
    # The caller (HeliusMigrateSource._decode_and_dedupe) will call
    # resolve_spl_mint(candidate_accounts, api_key) to find the real mint.
    candidate_accounts = extract_migrate_account_candidates(data)

    event: dict[str, Any] = {
        "type": "MEME_DATA",
        "graduated": True,
        "address": "",               # UNRESOLVED — set by _decode_and_dedupe after RPC call
        "candidate_accounts": candidate_accounts,  # for resolve_spl_mint()
        "source": event_source,
        "poolAddress": "",           # not extractable from this tx — fallback
        "blockTime": fallback_epoch,             # real Unix epoch (arrival time)
        "graduated_block_time": fallback_epoch,  # canonical field; real epoch seconds
        "creation_time": 0,
        "progress_percent": 0.0,
        "decimals": None,
        "raw": result,               # verbatim transaction result for audit
        "dex_source": MIGRATE_DEX_SOURCE,
        "signature": sig,
        "slot": slot,                # real slot for audit/ordering (NOT a timestamp)
    }
    return event


# ---------------------------------------------------------------------------
# Concrete DataSource — Helius program-wide transactionNotification stream
# ---------------------------------------------------------------------------


class HeliusBirthTapeSource(DataSource):
    """Live swap-event source backed by the Helius program-wide transactionSubscribe.

    Subscribes to ALL pump.fun bonding-curve program transactions via one
    WebSocket connection.  Each yielded event is a raw transactionNotification
    dict for use with decode_helius_notification (the MappedSwapSource mapper).

    Args:
        api_key:  Helius API key (passed as a query parameter in the WS URL).
        endpoint: Base Helius WebSocket URL (default: HELIUS_WS_URL).

    Note:
        ``websockets`` is imported lazily inside ``connect()`` to avoid import-
        time side effects during structural tests that only scan the AST.
    """

    def __init__(self, api_key: str, endpoint: str = HELIUS_WS_URL) -> None:
        self._api_key: str = api_key
        self._endpoint: str = endpoint
        self._ws: Any = None
        self._sub_id: int | None = None

    async def connect(self) -> None:
        """Open the Helius WebSocket and send the transactionSubscribe request.

        Subscribes to all transactions involving the pump.fun bonding-curve
        program with commitment='confirmed', full transaction details, and
        jsonParsed encoding.  The subscription ID from the server response is
        stored in self._sub_id for potential future use (e.g. unsubscribe).
        """
        import websockets  # lazy import — keeps import-time free of network side effects

        url = f"{self._endpoint}/?api-key={self._api_key}"
        self._ws = await websockets.connect(
            url,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=10,
        )
        subscribe_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        "accountInclude": [PUMP_FUN_PROGRAM],
                    },
                    {
                        "commitment": "confirmed",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }
        )
        await self._ws.send(subscribe_msg)
        # Read the subscription acknowledgement to capture the subscription ID
        try:
            ack_raw = await self._ws.recv()
            ack = json.loads(ack_raw)
            self._sub_id = ack.get("result")
        except Exception:
            pass  # Non-fatal: events() will still yield correctly

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield raw transactionNotification dicts from the Helius stream.

        Each raw WebSocket message is JSON-parsed; only frames where
        parsed.get("method") == "transactionNotification" are yielded.
        Subscription acks, heartbeats, and JSON parse errors are skipped.

        Yields nothing if the connection was never opened (self._ws is None).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(parsed, dict):
                continue

            if parsed.get("method") == "transactionNotification":
                yield parsed


# ---------------------------------------------------------------------------
# _QueueDataSource — queue-backed DataSource adapter (fan-out helper)
# ---------------------------------------------------------------------------


class _QueueDataSource(DataSource):
    """DataSource that yields items drained from an asyncio.Queue.

    Used by _helius_loop to fan-out a single raw WebSocket stream into two
    consumers (collection and graduation detection) without opening a second
    physical connection.

    connect() and disconnect() are no-ops — the source is driven by the pump
    task that pushes frames into the queue.  events() yields until a None
    sentinel is received (put by the teardown path when the pump ends), then
    returns cleanly so the consumer's run() also returns cleanly.

    IMPORTANT architectural constraints (same as HeliusBirthTapeSource):
    - No import of LiveSource, ReplaySource, core.live_source, core.replay_source
    - No top-level ``import websockets`` — lazy import inside connect() only
    - No ``datetime.now()`` / ``time.time()`` calls anywhere in this class
    """

    def __init__(self, queue: "asyncio.Queue[dict | None]") -> None:
        self._queue: "asyncio.Queue[dict | None]" = queue

    async def connect(self) -> None:  # no-op
        pass

    async def disconnect(self) -> None:  # no-op
        pass

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield frames from the queue until a None sentinel is received."""
        while True:
            item = await self._queue.get()
            if item is None:
                # Sentinel: pump has ended; signal done by returning
                return
            yield item


# ---------------------------------------------------------------------------
# HeliusMigrateSource — graduation detection on the migrate instruction
# (EPIC-graduation-migrate-detection)
# ---------------------------------------------------------------------------


class HeliusMigrateSource(DataSource):
    """Live graduation-event source backed by the Helius transactionSubscribe stream.

    Uses the IDENTICAL subscription parameters as HeliusBirthTapeSource
    (same endpoint, same accountInclude=[PUMP_FUN_PROGRAM], same encoding).
    Filters for migrate instructions and emits MEME_DATA graduation event
    dicts shaped for DetectionConsumer — one event per graduated SPL mint.

    This is the PRIMARY graduation source (replaces BirdeyeGraduationSource as
    primary).  It emits only real pump.fun graduations (zero AMM noise), push
    not poll, event-driven, with zero false positives.

    Deduplication: each mint is emitted at most once per session (by mint
    address).  The ``_seen_mints`` set prevents duplicate graduation events
    from multiple migrate frames referencing the same token (rare but possible
    in edge cases).

    Args:
        api_key:      Helius API key (query parameter in the WS URL).
        event_source: The "source" stamp on emitted events; must match
                      config.detection.filter.source ("pump_dot_fun") for
                      DetectionConsumer to persist the Token row.
        endpoint:     Helius WebSocket base URL (default: HELIUS_WS_URL).

    IMPORTANT architectural constraints (same as HeliusBirthTapeSource):
    - No import of LiveSource, ReplaySource, core.live_source, core.replay_source
    - No top-level ``import websockets`` — lazy import inside connect() only
    - No ``datetime.now()`` / ``time.time()`` calls anywhere in this class
    """

    def __init__(
        self,
        api_key: str,
        event_source: str = "pump_dot_fun",
        endpoint: str = HELIUS_WS_URL,
        clock: Clock | None = None,
    ) -> None:
        self._api_key: str = api_key
        self._event_source: str = event_source
        self._endpoint: str = endpoint
        self._clock: Clock = clock or WallClock()
        self._ws: Any = None
        self._sub_id: int | None = None
        self._seen_mints: set[str] = set()

    async def connect(self) -> None:
        """Open the Helius WebSocket and subscribe with identical params to HeliusBirthTapeSource.

        Subscribes to all pump.fun (6EF8rrecthR5…) transactions with
        commitment='confirmed', jsonParsed encoding, full transaction details,
        and maxSupportedTransactionVersion=0 — the same subscription that
        HeliusBirthTapeSource uses for the birth tape.
        """
        import websockets  # lazy import — no network at import time

        url = f"{self._endpoint}/?api-key={self._api_key}"
        self._ws = await websockets.connect(
            url,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=10,
        )
        subscribe_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        "accountInclude": [PUMP_FUN_PROGRAM],
                    },
                    {
                        "commitment": "confirmed",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }
        )
        await self._ws.send(subscribe_msg)
        try:
            ack_raw = await self._ws.recv()
            ack = json.loads(ack_raw)
            self._sub_id = ack.get("result")
        except Exception:
            pass

        logger.info(
            "[FIREHOSE] migrate-source: connected sub_id=%s endpoint=%s",
            self._sub_id,
            self._endpoint,
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _decode_and_dedupe(self, raw_frame: dict) -> "dict | None":
        """Decode a raw transactionNotification frame, resolve the SPL mint, and dedupe.

        ASYNC: runs the pure detect (decode_helius_migrate_event) inline, then
        dispatches the blocking RPC call (resolve_spl_mint) via asyncio.to_thread
        so it never stalls the event loop.

        Used by BOTH the WS-backed events() path AND the queue-backed fan-out path
        (events_from_queue).

        Args:
            raw_frame: A JSON-parsed dict from the Helius WebSocket stream.

        Returns:
            A MEME_DATA graduation event dict (with address= resolved SPL mint)
            if this is a new-mint migrate frame, or None if the frame is not a
            migrate, mint resolution failed, or the mint was already seen this session.
        """
        fallback_epoch = int(self._clock.now().timestamp())
        event = decode_helius_migrate_event(
            raw_frame,
            event_source=self._event_source,
            fallback_epoch=fallback_epoch,
        )
        if event is None:
            return None

        # Resolve the real SPL mint via getMultipleAccounts (ONE blocking RPC call).
        # asyncio.to_thread dispatches to the thread pool so the event loop stays free.
        candidate_accounts: list[str] = event.get("candidate_accounts") or []
        mint: str | None = await asyncio.to_thread(
            resolve_spl_mint,
            candidate_accounts,
            self._api_key,
            endpoint=HELIUS_RPC_URL,
        )

        if mint is None:
            logger.warning(
                "[FIREHOSE] migrate: could not resolve SPL mint (sig=%s) — skipping",
                event.get("signature", "unknown"),
            )
            return None

        # Set the resolved mint on the event before deduplication.
        event["address"] = mint

        # Deduplicate: emit at most once per mint per session
        if mint in self._seen_mints:
            logger.debug(
                "[FIREHOSE] migrate-source: dedupe mint=%s sig=%s",
                mint,
                event.get("signature", ""),
            )
            return None

        self._seen_mints.add(mint)
        logger.info(
            "[FIREHOSE] migrate-source: graduation detected mint=%s sig=%s slot=%s",
            mint,
            event.get("signature", ""),
            event.get("slot", ""),
        )
        return event

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts for each migrated pump.fun token.

        For each inbound Helius transactionNotification:
          1. JSON-parse.
          2. Detect+resolve+dedupe via _decode_and_dedupe (async — RPC call via to_thread).
          3. Yield the graduation event (with address= resolved SPL mint).

        Yields nothing if the connection was never opened (self._ws is None).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(parsed, dict):
                continue

            event = await self._decode_and_dedupe(parsed)
            if event is None:
                continue

            yield event

    async def events_from_queue(
        self, queue: "asyncio.Queue[dict | None]"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation events from a pre-populated asyncio.Queue.

        Alternative to events() for the fan-out path where a single physical
        WebSocket (HeliusBirthTapeSource) fans raw frames into a queue.  Drains
        the queue until the None sentinel (put by the pump teardown), decoding,
        resolving, and deduping each frame via _decode_and_dedupe (async).

        This method is used by _helius_loop in run_firehose to serve graduation
        detection off the shared collection socket rather than a second WS.

        Args:
            queue: asyncio.Queue[dict | None] fed by the pump task in _helius_loop.

        Yields:
            MEME_DATA graduation event dicts (same shape as events()).
        """
        while True:
            item = await queue.get()
            if item is None:
                return  # sentinel: pump ended

            if not isinstance(item, dict):
                continue

            event = await self._decode_and_dedupe(item)
            if event is None:
                continue

            yield event
