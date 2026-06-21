# ---
# module: core.tape.helius_birth_tape_source
# sprint: sprint-8
# story: US-34 AC-34.1, EPIC-graduation-migrate-detection
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: core.datasource, core.clock, base64, hashlib, struct, json, logging, typing
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

MIGRATE DETECTION (EPIC-graduation-migrate-detection)
=====================================================
decode_helius_migrate_event() is a pure mapper for the pump.fun 'migrate'
instruction.  A migrate transaction contains the log line
"Instruction: Migrate" and has NO Borsh event log to decode.  The SPL mint
is extracted from the 6EF8 CPI in innerInstructions (the CPI with the most
accounts — consistently 13 in real frames — has the mint at accounts[2]).

Verified against 3 real transactionNotification frames captured 2026-06-21
(slots 427956147, 427956654, and one additional).  Account indices confirmed:
  innerInstructions: 6EF8 CPI with 13 accounts → accounts[2] = SPL mint
  Pool: not reliably extractable from this tx structure; emitted as "".

HeliusMigrateSource is a separate DataSource (not a fan-out of
HeliusBirthTapeSource) using the IDENTICAL subscription parameters.  It
emits MEME_DATA graduation dicts shaped for DetectionConsumer, stamped
dex_source="helius_migrate".
"""
import base64
import hashlib
import json
import logging
import struct
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
# Migrate-detection constants (EPIC-graduation-migrate-detection)
# ---------------------------------------------------------------------------

#: PumpSwap AMM program address (the sole pump.fun graduation destination).
PUMP_AMM_PROGRAM: str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

#: Log line that indicates a migrate instruction in the pump.fun program.
_MIGRATE_LOG_MARKER: str = "Instruction: Migrate"

#: dex_source stamp for migrate-detected graduation events.
MIGRATE_DEX_SOURCE: str = "helius_migrate"

#: Minimum number of accounts in the 6EF8 CPI that carries the migrate instruction.
#: Verified on 3 real frames: the migrate CPI consistently has 13 accounts.
#: The CPI with fewer accounts (5) is MigrateBondingCurveCreator, not migrate.
_MIGRATE_CPI_MIN_ACCOUNTS: int = 8  # conservative lower bound; real frames have 13

#: Account index for the SPL mint within the 6EF8 migrate CPI accounts list.
#: Verified against pump.fun IDL (accounts[2]=mint) and 3 real frames.
_MIGRATE_CPI_MINT_INDEX: int = 2

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


def _extract_migrate_mint(inner_instructions: list[dict]) -> str | None:
    """Extract the graduated SPL mint from innerInstructions of a migrate tx.

    Walks all inner instruction groups and finds the CPI to the pump.fun
    6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P program that has the most
    accounts (consistently 13 in real frames).  The mint is at accounts[2]
    of that CPI, matching the pump.fun IDL 'migrate' instruction layout and
    verified against 3 real frames captured 2026-06-21.

    Args:
        inner_instructions: meta.innerInstructions from the transaction result.

    Returns:
        The SPL mint pubkey string, or None if extraction fails.
    """
    best_cpi: dict | None = None
    best_count: int = 0

    for group in inner_instructions:
        for ix in group.get("instructions") or []:
            if ix.get("programId") != PUMP_FUN_PROGRAM:
                continue
            accounts: list[str] = ix.get("accounts") or []
            if len(accounts) > best_count:
                best_count = len(accounts)
                best_cpi = ix

    if best_cpi is None or best_count < _MIGRATE_CPI_MIN_ACCOUNTS:
        return None

    accounts = best_cpi.get("accounts") or []
    if len(accounts) <= _MIGRATE_CPI_MINT_INDEX:
        return None

    return accounts[_MIGRATE_CPI_MINT_INDEX] or None


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
    """Map a raw Helius transactionNotification dict to a MEME_DATA graduation event.

    Returns a graduation event dict in the EXACT shape DetectionConsumer expects
    (same as map_new_pair_frame in birdeye_graduation_source.py), or None for
    non-migrate frames.

    Detection criteria:
      - method == "transactionNotification"
      - meta.err is None (landed transactions only)
      - meta.logMessages contains "Instruction: Migrate"

    Mint extraction:
      Walk meta.innerInstructions for the 6EF8rrecthR5… CPI with the most
      accounts (≥8); take accounts[2] as the SPL mint.  Verified against
      pump.fun IDL (accounts[2]=mint) and 3 real frames (2026-06-21).

    Pool address:
      Not reliably extractable from this transaction structure (the pAMM
      create_pool CPI accounts in innerInstructions don't expose the pool PDA
      directly via the jsonParsed encoding; the pfee wrapper obscures the full
      account list).  Emitted as "" so DetectionConsumer's coerce logic
      (poolAddress=None → "") persists cleanly.

    Emitted event shape (matches DetectionConsumer._is_graduation_event checks):
      {
        "type":                 "MEME_DATA",
        "graduated":            True,
        "address":              <SPL mint>,
        "source":               <event_source>,   # default "pump_dot_fun"
        "poolAddress":          "",               # not extractable; fallback
        "blockTime":            <fallback_epoch>, # event-arrival Unix epoch (NOT slot)
        "graduated_block_time": <fallback_epoch>, # same as blockTime; real epoch seconds
        "creation_time":        0,
        "progress_percent":     0.0,
        "decimals":             None,
        "raw":                  <transaction result dict>,
        "dex_source":           "helius_migrate",
        "slot":                 <slot int>,       # real slot for audit/ordering
      }

    Args:
        data:           Raw dict from the Helius WebSocket frame (already JSON-parsed).
        event_source:   The "source" stamp value; must match config.detection.filter.source
                        ("pump_dot_fun") for DetectionConsumer to persist the Token row.
        fallback_epoch: Unix epoch seconds to use for blockTime and graduated_block_time.
                        A migrate notification arrives within ~1 s of the block, so the
                        caller-supplied wall-clock arrival time is second-accurate.
                        This mirrors map_new_pair_frame's fallback_epoch pattern exactly.
                        The real slot is preserved in the "slot" field for audit use.

    Returns:
        A MEME_DATA graduation event dict, or None if this frame is not a migrate.
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

    # Extract mint from innerInstructions
    try:
        inner = result["transaction"]["meta"].get("innerInstructions") or []
    except (KeyError, TypeError, AttributeError):
        inner = []

    mint = _extract_migrate_mint(inner)
    if not mint:
        logger.warning(
            "[FIREHOSE] migrate: could not extract mint from innerInstructions "
            "(sig=%s) — skipping",
            _signature(result) or "unknown",
        )
        return None

    # Real slot for audit/ordering (kept as-is — it is NOT used as a timestamp).
    # blockTime and graduated_block_time are set to fallback_epoch (the caller's
    # wall-clock arrival time), which is a real Unix epoch second — identical in
    # intent to map_new_pair_frame's fallback_epoch pattern.  A migrate
    # notification arrives within ~1 s of the block, so arrival-time gives a
    # second-accurate anchor.  Using the slot as the epoch was a bug: slot
    # ~427_972_941 interpreted as Unix seconds = 1983-07-25, which poisons
    # graduated_block_time and breaks all rel = swap_time − grad_time scoring.
    slot_raw = result.get("slot")
    slot: int = int(slot_raw) if slot_raw is not None else 0

    sig = _signature(result) or ""

    event: dict[str, Any] = {
        "type": "MEME_DATA",
        "graduated": True,
        "address": mint,
        "source": event_source,
        "poolAddress": "",          # not extractable from this tx — fallback
        "blockTime": fallback_epoch,           # real Unix epoch (arrival time)
        "graduated_block_time": fallback_epoch,  # canonical field; real epoch seconds
        "creation_time": 0,
        "progress_percent": 0.0,
        "decimals": None,
        "raw": result,              # verbatim transaction result for audit
        "dex_source": MIGRATE_DEX_SOURCE,
        "signature": sig,
        "slot": slot,               # real slot for audit/ordering (NOT a timestamp)
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

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield MEME_DATA graduation event dicts for each migrated pump.fun token.

        For each inbound Helius transactionNotification:
          1. JSON-parse.
          2. Decode via decode_helius_migrate_event — skip non-migrate frames.
          3. Deduplicate by mint address (emit each mint at most once).
          4. Log and yield the graduation event.

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

            fallback_epoch = int(self._clock.now().timestamp())
            event = decode_helius_migrate_event(
                parsed,
                event_source=self._event_source,
                fallback_epoch=fallback_epoch,
            )
            if event is None:
                continue

            mint: str = event["address"]

            # Deduplicate: emit at most once per mint per session
            if mint in self._seen_mints:
                logger.debug(
                    "[FIREHOSE] migrate-source: dedupe mint=%s sig=%s",
                    mint,
                    event.get("signature", ""),
                )
                continue

            self._seen_mints.add(mint)
            logger.info(
                "[FIREHOSE] migrate-source: graduation detected mint=%s sig=%s slot=%s",
                mint,
                event.get("signature", ""),
                event.get("slot", ""),
            )
            yield event
