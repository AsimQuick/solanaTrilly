# ---
# module: core.tape.helius_birth_tape_source
# sprint: sprint-8
# story: US-34 AC-34.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.datasource, base64, hashlib, struct, json, typing
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
"""
import base64
import hashlib
import json
import struct
from typing import Any, AsyncGenerator

from core.datasource import DataSource

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
