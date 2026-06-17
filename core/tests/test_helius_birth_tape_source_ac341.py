# ---
# module: core.tests.test_helius_birth_tape_source_ac341
# sprint: sprint-8
# story: US-34 AC-34.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.helius_birth_tape_source, core.datasource, core.normalized_swap,
#               core.replay_source, core.tape.mapped_source, core.tape.recorder,
#               core.clock, ast, asyncio, base64, hashlib, struct, pathlib
# ---
"""AC-34.1 — HeliusBirthTapeSource: DataSource seam + decode tests.

All tests are offline and deterministic — no network calls.

Tests:
  1. test_helius_live_in_valid_sources
       Asserts 'helius_live' is in VALID_SOURCES.

  2. test_datasource_seam_compliance
       HeliusBirthTapeSource has connect/disconnect/events and inherits DataSource.

  3. test_decode_helius_trade_event_correct_fields
       decode_helius_trade_event returns correct fields for known packed bytes.

  4. test_decode_helius_notification_buy
       decode_helius_notification returns correct dict for a buy notification.

  5. test_decode_helius_notification_sell
       decode_helius_notification returns correct dict for a sell notification.

  6. test_decode_helius_notification_failed_tx_returns_none
       Notification with meta.err set returns None.

  7. test_decode_helius_notification_non_trade_returns_none
       Notification without Buy/Sell instruction log returns None.

  8. test_decode_determinism
       Two calls with identical input produce equal results.

  9. test_replay_through_source_emits_normalized_swaps
       ReplaySource + MappedSwapSource + TapeRecorder over 3 banked events
       produces 3 NormalizedSwap instances with correct provenance.

  10. test_no_concrete_source_import_in_helius_source
        AST scan: helius_birth_tape_source.py must not import LiveSource/ReplaySource.

  11. test_no_direct_time_call_in_helius_source
        AST scan: helius_birth_tape_source.py must not call datetime.now() or time.time().

  12. test_non_transactionnotification_frames_skipped
        decode_helius_notification returns None for subscription ack frames.
"""
import ast
import asyncio
import base64
import struct
from pathlib import Path
from types import SimpleNamespace

from core.datasource import DataSource
from core.normalized_swap import VALID_SOURCES
from core.tape.helius_birth_tape_source import (
    TRADE_EVENT_DISCRIMINATOR,
    WSOL_MINT,
    HeliusBirthTapeSource,
    decode_helius_notification,
    decode_helius_trade_event,
)

# ---------------------------------------------------------------------------
# Paths for AST guard tests
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
_HELIUS_SOURCE_FILE = REPO_ROOT / "core" / "tape" / "helius_birth_tape_source.py"

# ---------------------------------------------------------------------------
# Fixture bytes — deterministic, known values
# ---------------------------------------------------------------------------

_MINT_BYTES: bytes = bytes(range(1, 33))   # 32 distinct bytes
_USER_BYTES: bytes = bytes(range(33, 65))  # 32 distinct bytes

# Known numeric values for deterministic assertions
_SOL_AMOUNT: int = 500_000_000       # 0.5 SOL in lamports
_TOKEN_AMOUNT: int = 1_000_000_000   # 1 billion base-units
_VSOL: int = 30_000_000_000          # 30 SOL in lamports
_VTOK: int = 600_000_000_000         # 600 billion base-units
_TIMESTAMP: int = 1_748_000_000      # arbitrary unix timestamp
_SLOT: int = 999_888_777
_SIG: str = "5J9fakeHELIUSsignaturexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _pack_trade_event(
    mint_bytes: bytes,
    sol_amount: int,
    token_amount: int,
    is_buy: bool,
    user_bytes: bytes,
    timestamp: int,
    vsol: int,
    vtok: int,
) -> bytes:
    """Pack a minimal Borsh TradeEvent binary blob for testing."""
    return (
        TRADE_EVENT_DISCRIMINATOR
        + mint_bytes
        + struct.pack("<QQ", sol_amount, token_amount)
        + bytes([1 if is_buy else 0])
        + user_bytes
        + struct.pack("<qQQ", timestamp, vsol, vtok)
    )


def _make_notification(
    mint_bytes: bytes,
    sol_amount: int,
    token_amount: int,
    is_buy: bool,
    user_bytes: bytes,
    timestamp: int,
    vsol: int,
    vtok: int,
    slot: int,
    sig: str,
    meta_err: object = None,
    include_trade_log: bool = True,
) -> dict:
    """Build a synthetic Helius transactionNotification dict."""
    packed = _pack_trade_event(
        mint_bytes, sol_amount, token_amount, is_buy, user_bytes, timestamp, vsol, vtok
    )
    trade_b64 = base64.b64encode(packed).decode()
    instruction = "Buy" if is_buy else "Sell"

    log_messages: list[str] = [
        f"Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
    ]
    if include_trade_log:
        log_messages.append(f"Program log: Instruction: {instruction}")
    log_messages += [
        f"Program data: {trade_b64}",
        "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
    ]

    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 42,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "meta": {
                        "err": meta_err,
                        "logMessages": log_messages,
                    },
                    "transaction": {"signatures": [sig]},
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Pre-built fixtures for replay tests (2 buys, 1 sell)
# ---------------------------------------------------------------------------

_NOTIFICATION_BUY_1 = _make_notification(
    _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, True, _USER_BYTES,
    _TIMESTAMP, _VSOL, _VTOK, _SLOT, _SIG
)
_NOTIFICATION_BUY_2 = _make_notification(
    _MINT_BYTES, _SOL_AMOUNT * 2, _TOKEN_AMOUNT * 2, True, _USER_BYTES,
    _TIMESTAMP + 1, _VSOL, _VTOK, _SLOT + 1, _SIG + "2"
)
_NOTIFICATION_SELL_1 = _make_notification(
    _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, False, _USER_BYTES,
    _TIMESTAMP + 2, _VSOL, _VTOK, _SLOT + 2, _SIG + "3"
)

_BANKED_EVENTS = [_NOTIFICATION_BUY_1, _NOTIFICATION_BUY_2, _NOTIFICATION_SELL_1]

# Compute the expected mint b58 from _MINT_BYTES using the same _b58_from_bytes function
from core.tape.helius_birth_tape_source import _b58_from_bytes as _b58  # noqa: E402

_EXPECTED_MINT_B58: str = _b58(_MINT_BYTES)
_EXPECTED_USER_B58: str = _b58(_USER_BYTES)


# ---------------------------------------------------------------------------
# Test 1: helius_live in VALID_SOURCES
# ---------------------------------------------------------------------------


def test_helius_live_in_valid_sources() -> None:
    """'helius_live' must be present in VALID_SOURCES (AC-34.1)."""
    assert "helius_live" in VALID_SOURCES, (
        "'helius_live' must be in VALID_SOURCES but found: " + str(VALID_SOURCES)
    )


# ---------------------------------------------------------------------------
# Test 2: DataSource seam compliance
# ---------------------------------------------------------------------------


def test_datasource_seam_compliance() -> None:
    """HeliusBirthTapeSource must implement the DataSource seam (Principle #7)."""
    assert issubclass(HeliusBirthTapeSource, DataSource), (
        "HeliusBirthTapeSource must inherit from DataSource"
    )
    source = HeliusBirthTapeSource(api_key="test-key")
    assert hasattr(source, "connect"), "Missing connect() method"
    assert hasattr(source, "disconnect"), "Missing disconnect() method"
    assert hasattr(source, "events"), "Missing events() method"
    assert callable(source.connect), "connect must be callable"
    assert callable(source.disconnect), "disconnect must be callable"
    assert callable(source.events), "events must be callable"


# ---------------------------------------------------------------------------
# Test 3: decode_helius_trade_event correct fields
# ---------------------------------------------------------------------------


def test_decode_helius_trade_event_correct_fields() -> None:
    """decode_helius_trade_event must return correct fields for known input."""
    packed = _pack_trade_event(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, True, _USER_BYTES,
        _TIMESTAMP, _VSOL, _VTOK
    )
    result = decode_helius_trade_event(packed)
    assert result is not None, "Expected a dict result, got None"
    assert result["mint"] == _EXPECTED_MINT_B58, f"mint mismatch: {result['mint']!r}"
    assert result["sol_amount"] == _SOL_AMOUNT
    assert result["token_amount"] == _TOKEN_AMOUNT
    assert result["side"] == "buy"
    assert result["owner"] == _EXPECTED_USER_B58
    assert result["block_time"] == _TIMESTAMP
    assert result["virtual_sol_reserves"] == _VSOL
    assert result["virtual_token_reserves"] == _VTOK


def test_decode_helius_trade_event_sell() -> None:
    """decode_helius_trade_event correctly decodes is_buy=False as side='sell'."""
    packed = _pack_trade_event(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, False, _USER_BYTES,
        _TIMESTAMP, _VSOL, _VTOK
    )
    result = decode_helius_trade_event(packed)
    assert result is not None
    assert result["side"] == "sell"


def test_decode_helius_trade_event_wrong_discriminator_returns_none() -> None:
    """decode_helius_trade_event must return None if discriminator doesn't match."""
    packed = _pack_trade_event(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, True, _USER_BYTES,
        _TIMESTAMP, _VSOL, _VTOK
    )
    # Corrupt the discriminator
    corrupted = b"\x00" * 8 + packed[8:]
    result = decode_helius_trade_event(corrupted)
    assert result is None, "Expected None for bad discriminator"


def test_decode_helius_trade_event_too_short_returns_none() -> None:
    """decode_helius_trade_event must return None if buffer is too short."""
    result = decode_helius_trade_event(TRADE_EVENT_DISCRIMINATOR + b"\x00" * 10)
    assert result is None, "Expected None for truncated buffer"


# ---------------------------------------------------------------------------
# Test 4: decode_helius_notification buy
# ---------------------------------------------------------------------------


def test_decode_helius_notification_buy() -> None:
    """decode_helius_notification must return correct dict for a buy notification."""
    result = decode_helius_notification(_NOTIFICATION_BUY_1)
    assert result is not None, "Expected a dict result for buy notification, got None"
    assert result["side"] == "buy"
    assert result["mint"] == _EXPECTED_MINT_B58
    assert result["owner"] == _EXPECTED_USER_B58
    assert abs(result["vol_sol"] - _SOL_AMOUNT / 1e9) < 1e-12
    assert result["vol_usd"] == 0.0
    assert result["sol_usd"] == 0.0
    assert result["failed"] is False
    assert result["quote_mint"] == WSOL_MINT
    assert result["base_reserve"] == int(_VTOK)
    assert result["quote_reserve"] == int(_VSOL)
    assert result["block_time"] == _TIMESTAMP
    assert result["slot"] == _SLOT
    assert result["signature"] == _SIG


# ---------------------------------------------------------------------------
# Test 5: decode_helius_notification sell
# ---------------------------------------------------------------------------


def test_decode_helius_notification_sell() -> None:
    """decode_helius_notification must return correct dict for a sell notification."""
    result = decode_helius_notification(_NOTIFICATION_SELL_1)
    assert result is not None, "Expected a dict result for sell notification, got None"
    assert result["side"] == "sell"
    assert result["mint"] == _EXPECTED_MINT_B58
    assert result["failed"] is False
    assert result["quote_mint"] == WSOL_MINT


# ---------------------------------------------------------------------------
# Test 6: Failed transaction returns None
# ---------------------------------------------------------------------------


def test_decode_helius_notification_failed_tx_returns_none() -> None:
    """decode_helius_notification must return None for a failed transaction."""
    failed_notif = _make_notification(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, True, _USER_BYTES,
        _TIMESTAMP, _VSOL, _VTOK, _SLOT, _SIG,
        meta_err={"InstructionError": [0, "Custom error"]},
    )
    result = decode_helius_notification(failed_notif)
    assert result is None, "Expected None for failed transaction (meta.err is set)"


# ---------------------------------------------------------------------------
# Test 7: Non-trade notification returns None
# ---------------------------------------------------------------------------


def test_decode_helius_notification_non_trade_returns_none() -> None:
    """decode_helius_notification must return None when no Buy/Sell instruction found."""
    notif = _make_notification(
        _MINT_BYTES, _SOL_AMOUNT, _TOKEN_AMOUNT, True, _USER_BYTES,
        _TIMESTAMP, _VSOL, _VTOK, _SLOT, _SIG,
        include_trade_log=False,
    )
    result = decode_helius_notification(notif)
    assert result is None, "Expected None when no Buy/Sell instruction in logs"


# ---------------------------------------------------------------------------
# Test 8: Decode determinism (run-twice byte-identity)
# ---------------------------------------------------------------------------


def test_decode_determinism() -> None:
    """Two calls with identical input must produce equal results."""
    result_a = decode_helius_notification(_NOTIFICATION_BUY_1)
    result_b = decode_helius_notification(_NOTIFICATION_BUY_1)
    assert result_a is not None and result_b is not None
    assert result_a == result_b, (
        "decode_helius_notification must be deterministic — "
        f"first call: {result_a}, second call: {result_b}"
    )


# ---------------------------------------------------------------------------
# Test 9: Replay through source emits NormalizedSwaps
# ---------------------------------------------------------------------------


def test_replay_through_source_emits_normalized_swaps() -> None:
    """ReplaySource + MappedSwapSource + TapeRecorder emits 3 NormalizedSwaps with correct provenance."""
    from datetime import datetime, timezone

    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder

    async def _run():
        source = MappedSwapSource(
            ReplaySource(_BANKED_EVENTS),
            decode_helius_notification,
        )
        clock = VirtualClock(datetime(2025, 3, 1, tzinfo=timezone.utc))
        token_store = {
            _EXPECTED_MINT_B58: SimpleNamespace(graduated_block_time=_TIMESTAMP - 100)
        }
        recorder = TapeRecorder(
            source,
            clock,
            token_store=token_store,
            swap_source="helius_live",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    swaps = asyncio.run(_run())

    assert len(swaps) == 3, f"Expected 3 NormalizedSwaps, got {len(swaps)}"
    assert all(s.source == "helius_live" for s in swaps), (
        f"Expected all swaps to have source='helius_live', got: {[s.source for s in swaps]}"
    )
    assert all(s.phase == "pre" for s in swaps), (
        f"Expected all swaps to have phase='pre', got: {[s.phase for s in swaps]}"
    )
    # Swaps sorted by (block_time, slot, signature): buy_1, buy_2, sell_1
    assert swaps[0].side == "buy", f"Expected swaps[0].side='buy', got {swaps[0].side!r}"
    assert swaps[1].side == "buy", f"Expected swaps[1].side='buy', got {swaps[1].side!r}"
    assert swaps[2].side == "sell", f"Expected swaps[2].side='sell', got {swaps[2].side!r}"
    assert swaps[0].vol_usd == 0.0
    assert swaps[0].sol_usd == 0.0
    assert swaps[0].quote_mint == WSOL_MINT
    assert all(s.quote_mint == WSOL_MINT for s in swaps)


# ---------------------------------------------------------------------------
# Test 10: No concrete source import in helius_birth_tape_source.py
# ---------------------------------------------------------------------------

_CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}
_CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}


def _concrete_source_violations_in_file(py_file: Path) -> list[str]:
    """Return import-violation descriptions found in *py_file*."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _CONCRETE_MODULE_NAMES:
                found.append(f"{rel}: 'from {module} import ...'")
                continue
            for alias in node.names:
                if alias.name in _CONCRETE_CLASS_NAMES:
                    found.append(f"{rel}: 'from {module} import {alias.name}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _CONCRETE_MODULE_NAMES:
                    found.append(f"{rel}: 'import {alias.name}'")
    return found


def test_no_concrete_source_import_in_helius_source() -> None:
    """helius_birth_tape_source.py must not import LiveSource or ReplaySource (Principle #7)."""
    violations = _concrete_source_violations_in_file(_HELIUS_SOURCE_FILE)
    assert not violations, (
        "helius_birth_tape_source.py must depend only on the DataSource interface, "
        "never on a concrete source class.\nViolations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 11: No direct time call in helius_birth_tape_source.py
# ---------------------------------------------------------------------------


def _time_call_violations_in_file(py_file: Path) -> list[str]:
    """Return forbidden time-call descriptions found in *py_file* via AST."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datetime"
        ):
            found.append(f"{rel}:{node.lineno}: forbidden 'datetime.now()' call")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "time"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ):
            found.append(f"{rel}:{node.lineno}: forbidden 'time.time()' call")
    return found


def test_no_direct_time_call_in_helius_source() -> None:
    """helius_birth_tape_source.py must not call datetime.now() or time.time() (AC-2.2)."""
    violations = _time_call_violations_in_file(_HELIUS_SOURCE_FILE)
    assert not violations, (
        "helius_birth_tape_source.py must read 'now' only from the injected Clock "
        "abstraction — direct time calls bypass the Clock seam.\nViolations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 12: Non-transactionNotification frames are skipped
# ---------------------------------------------------------------------------


def test_non_transactionnotification_frames_skipped() -> None:
    """decode_helius_notification must return None for subscription ack frames."""
    # Subscription ack (no 'method' key)
    ack_frame = {"jsonrpc": "2.0", "id": 1, "result": 99999}
    assert decode_helius_notification(ack_frame) is None, (
        "Expected None for subscription ack frame (no 'method' key)"
    )
    # Wrong method
    heartbeat = {"jsonrpc": "2.0", "method": "heartbeat", "params": {}}
    assert decode_helius_notification(heartbeat) is None, (
        "Expected None for non-transactionNotification method frame"
    )
    # Non-dict input
    assert decode_helius_notification("not a dict") is None  # type: ignore[arg-type]
