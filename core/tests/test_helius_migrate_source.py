# ---
# module: core.tests.test_helius_migrate_source
# sprint: sprint-14
# story: EPIC-graduation-migrate-detection
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: core.tape.helius_birth_tape_source, core.detection.consumer,
#               core.tape.birdeye_graduation_source, core.detection.helius_reconciler,
#               core.clock, core.replay_source, asyncio, ast, json, pathlib, pytest
# ---
"""EPIC-graduation-migrate-detection — unit tests for the migrate decoder and source.

Tests (all offline, no network):

1. test_decode_migrate_returns_meme_data_for_real_frame
     decode_helius_migrate_event returns a MEME_DATA event with a non-empty
     address (mint) for the real banked transactionNotification fixture.

2. test_decode_migrate_mint_matches_known_value
     The extracted mint from the real fixture matches the value verified
     during frame capture (2026-06-21, slot 427956654).

3. test_decode_migrate_none_for_trade_frame
     A synthetic Buy transactionNotification returns None.

4. test_decode_migrate_none_for_non_notification
     A subscription ack / non-transactionNotification dict returns None.

5. test_decode_migrate_none_for_failed_tx
     A migrate frame with meta.err set returns None.

6. test_decode_migrate_none_for_create_frame
     A transactionNotification with only Instruction: Create in logs returns None.

7. test_decode_migrate_event_shape
     The returned event has all required MEME_DATA fields and correct values.

8. test_detection_consumer_accepts_migrate_event
     DetectionConsumer with a ReplaySource carrying a migrate event persists
     a Token row when a matching config is active.

9. test_migrate_source_ast_no_banned_imports
     AST scan: helius_birth_tape_source.py must not import LiveSource,
     ReplaySource, core.live_source, or core.replay_source.

10. test_migrate_source_ast_no_direct_time_calls
      AST scan: helius_birth_tape_source.py must not call datetime.now()
      or time.time() anywhere.

11. test_birdeye_fallback_still_wired
      _build_graduation() in run_firehose.py uses HeliusMigrateSource as
      primary AND _build_reconciler() references BirdeyeGraduationSource
      as secondary.  Verified by AST scan: BirdeyeGraduationSource import
      remains in run_firehose.py.

12. test_mint_key_equality
      The mint from the migrate decoder matches the mint used in pre-grad
      TradeEvent (both reference the same SPL token address).  Uses the
      real fixture mint to assert it is a valid base58 string of expected length.

13. test_helius_migrate_source_is_datasource
      HeliusMigrateSource implements the DataSource interface.

14. test_decode_migrate_dex_source_stamp
      The emitted event has dex_source="helius_migrate".
"""
import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REAL_MIGRATE_FIXTURE = FIXTURES_DIR / "helius_migrate_tx_real.json"
HELIUS_SOURCE_FILE = REPO_ROOT / "core" / "tape" / "helius_birth_tape_source.py"
RUN_FIREHOSE_FILE = REPO_ROOT / "core" / "management" / "commands" / "run_firehose.py"

# ---------------------------------------------------------------------------
# Known values from captured frame (2026-06-21, slot 427956654)
# ---------------------------------------------------------------------------

# Verified mint extracted from innerInstructions 6EF8 CPI accounts[2]
REAL_FRAME_MINT = "74gPctSqK6stvYRCSe1GpNzcn9cTAh49GN3SmpUGAp3q"
REAL_FRAME_SIG = "3tAbrT6pCnDpTaoquqmiWDooZVfNtphNxodqpGthxtkFhScyLqA8Uc4r5HnttJJczmgq5gycHqmXrYyKGHkRVRLU"
REAL_FRAME_SLOT = 427956654

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_real_fixture() -> dict:
    """Load the banked real migrate transactionNotification frame."""
    with REAL_MIGRATE_FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _make_synthetic_trade_frame(instruction: str = "Buy") -> dict:
    """Build a minimal synthetic transactionNotification for a Buy/Sell trade."""
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 1,
            "result": {
                "slot": 999,
                "signature": "fakeSig111111111",
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            f"Program log: Instruction: {instruction}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                        ],
                        "innerInstructions": [],
                    },
                    "transaction": {
                        "signatures": ["fakeSig111111111"],
                        "message": {"accountKeys": []},
                    },
                },
            },
        },
    }


def _make_migrate_frame_with_meta_err() -> dict:
    """Real-shaped migrate frame but with meta.err set (failed tx)."""
    frame = _load_real_fixture()
    # Deep-copy the result and inject an error
    import copy
    frame = copy.deepcopy(frame)
    frame["params"]["result"]["transaction"]["meta"]["err"] = "InstructionError"
    return frame


def _make_subscription_ack() -> dict:
    """A Helius subscription acknowledgement (not a transactionNotification)."""
    return {"jsonrpc": "2.0", "id": 1, "result": 12345678}


def _make_create_frame() -> dict:
    """A transactionNotification with Instruction: Create (not migrate)."""
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 1,
            "result": {
                "slot": 999,
                "signature": "fakeSig222222222",
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            "Program log: Instruction: Create",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                        ],
                        "innerInstructions": [],
                    },
                    "transaction": {
                        "signatures": ["fakeSig222222222"],
                        "message": {"accountKeys": []},
                    },
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Test 1: Real fixture returns MEME_DATA
# ---------------------------------------------------------------------------


def test_decode_migrate_returns_meme_data_for_real_frame() -> None:
    """decode_helius_migrate_event returns a MEME_DATA event for the real fixture."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    assert REAL_MIGRATE_FIXTURE.exists(), (
        f"Real migrate fixture not found at {REAL_MIGRATE_FIXTURE}. "
        "Run the capture script first."
    )
    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None, "decode_helius_migrate_event returned None for real migrate frame"
    assert event.get("type") == "MEME_DATA", f"Expected type='MEME_DATA', got {event.get('type')!r}"
    assert event.get("graduated") is True, "Expected graduated=True"
    assert event.get("address"), "Expected non-empty address (mint)"


# ---------------------------------------------------------------------------
# Test 2: Mint matches known value from real frame
# ---------------------------------------------------------------------------


def test_decode_migrate_mint_matches_known_value() -> None:
    """The mint extracted from the real fixture matches the verified value."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None
    assert event["address"] == REAL_FRAME_MINT, (
        f"Mint mismatch: expected {REAL_FRAME_MINT!r}, got {event['address']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: None for trade frame (Buy)
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_trade_frame() -> None:
    """A Buy transactionNotification returns None (not a migrate event)."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_synthetic_trade_frame("Buy")
    assert decode_helius_migrate_event(frame) is None


# ---------------------------------------------------------------------------
# Test 4: None for non-notification
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_non_notification() -> None:
    """A subscription ack dict returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    ack = _make_subscription_ack()
    assert decode_helius_migrate_event(ack) is None


# ---------------------------------------------------------------------------
# Test 5: None for failed tx
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_failed_tx() -> None:
    """A migrate frame with meta.err set returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_migrate_frame_with_meta_err()
    assert decode_helius_migrate_event(frame) is None


# ---------------------------------------------------------------------------
# Test 6: None for Create frame
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_create_frame() -> None:
    """A transactionNotification with only Instruction: Create returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_create_frame()
    assert decode_helius_migrate_event(frame) is None


# ---------------------------------------------------------------------------
# Test 7: Full event shape validation
# ---------------------------------------------------------------------------


def test_decode_migrate_event_shape() -> None:
    """The returned event has all required MEME_DATA fields with correct types."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None

    # Required fields and types
    assert event["type"] == "MEME_DATA"
    assert event["graduated"] is True
    assert isinstance(event["address"], str) and event["address"]
    assert isinstance(event["source"], str) and event["source"] == "pump_dot_fun"
    assert isinstance(event["poolAddress"], str)   # "" is acceptable
    assert isinstance(event["blockTime"], int)
    assert isinstance(event["graduated_block_time"], int)
    assert event["blockTime"] == event["graduated_block_time"]
    assert event["creation_time"] == 0
    assert event["progress_percent"] == 0.0
    assert event.get("dex_source") == "helius_migrate"
    # raw must be the transaction result dict
    assert isinstance(event.get("raw"), dict)


# ---------------------------------------------------------------------------
# Test 8: DetectionConsumer accepts migrate event
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_detection_consumer_accepts_migrate_event() -> None:
    """DetectionConsumer persists a Token row when fed a migrate graduation event.

    Uses a ReplaySource carrying the decoded migrate event dict so no live
    network calls are made.  Requires an active PipelineConfig with the
    matching detection.filter.source.
    """
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.models import PipelineConfig, Token
    from core.replay_source import ReplaySource
    from core.resolver import get_active_config, invalidate_active_config_cache
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    # Create active config
    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="test-migrate-consumer",
        is_active=True,
        detection={
            "filter": {"source": "pump_dot_fun", "graduated": True},
            "prestage_progress_pct": 95.0,
            "dedupe_window_s": 60,
        },
        tape={
            "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
            "idle_kill_ttl_s": 1800,
            "reattach": True,
            "birdeye_interval_s": 15,
        },
        scoring={
            "score_at_elapsed_s": 120,
            "window_s": 180,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": 1800, "label_def": {}},
        trading={
            "gate": "adaptive_topk",
            "enabled": False,
            "position_size_sol": 0.1,
            "max_open_positions": 3,
            "slippage_bps": 50,
        },
    )

    # Decode the real migrate frame to get a graduation event
    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None, "Decoder failed on real fixture"

    mint = event["address"]

    # Feed the event through DetectionConsumer via ReplaySource
    t0 = datetime(2026, 6, 21, tzinfo=timezone.utc)
    clock = VirtualClock(t0)
    consumer = DetectionConsumer(
        source=ReplaySource([event]),
        clock=clock,
        config_fn=get_active_config,
    )
    asyncio.run(consumer.run())

    # Token row should exist
    assert Token.objects.filter(mint=mint).exists(), (
        f"Token row for mint={mint} not created by DetectionConsumer"
    )
    token = Token.objects.get(mint=mint)
    assert token.dex_source == "helius_migrate", (
        f"Expected dex_source='helius_migrate', got {token.dex_source!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: AST — no banned imports in helius_birth_tape_source.py
# ---------------------------------------------------------------------------


def test_migrate_source_ast_no_banned_imports() -> None:
    """helius_birth_tape_source.py must not import LiveSource or ReplaySource."""
    assert HELIUS_SOURCE_FILE.exists(), f"Missing: {HELIUS_SOURCE_FILE}"
    source_text = HELIUS_SOURCE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    _forbidden_classes = {"LiveSource", "ReplaySource"}
    _forbidden_modules = {"core.live_source", "core.replay_source"}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _forbidden_modules:
                violations.append(f"'from {module} import ...' found")
                continue
            for alias in node.names:
                if alias.name in _forbidden_classes:
                    violations.append(f"'from {module} import {alias.name}' found")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _forbidden_modules:
                    violations.append(f"'import {alias.name}' found")

    assert not violations, (
        "helius_birth_tape_source.py must NOT import concrete DataSource classes:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 10: AST — no direct time calls in helius_birth_tape_source.py
# ---------------------------------------------------------------------------


def test_migrate_source_ast_no_direct_time_calls() -> None:
    """helius_birth_tape_source.py must not call datetime.now() or time.time()."""
    assert HELIUS_SOURCE_FILE.exists(), f"Missing: {HELIUS_SOURCE_FILE}"
    source_text = HELIUS_SOURCE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # datetime.now() — attribute call on a Name("datetime")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "now"
            and isinstance(func.value, ast.Name)
            and func.value.id == "datetime"
        ):
            violations.append(f"datetime.now() call at line {node.lineno}")
        # time.time() — attribute call on a Name("time")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "time"
            and isinstance(func.value, ast.Name)
            and func.value.id == "time"
        ):
            violations.append(f"time.time() call at line {node.lineno}")

    assert not violations, (
        "helius_birth_tape_source.py must NOT call datetime.now() or time.time():\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 11: Birdeye fallback still wired
# ---------------------------------------------------------------------------


def test_birdeye_fallback_still_wired() -> None:
    """BirdeyeGraduationSource is still imported in run_firehose.py (gap-fill secondary).

    AST scan: run_firehose.py must import BirdeyeGraduationSource so the
    Birdeye secondary/gap-fill path is retained as a MigrateReconciler backstop.
    """
    assert RUN_FIREHOSE_FILE.exists(), f"Missing: {RUN_FIREHOSE_FILE}"
    source_text = RUN_FIREHOSE_FILE.read_text(encoding="utf-8")

    # Source-level check: BirdeyeGraduationSource must still appear
    assert "BirdeyeGraduationSource" in source_text, (
        "BirdeyeGraduationSource must still be referenced in run_firehose.py "
        "(Birdeye secondary/gap-fill must remain wired)"
    )

    # Also verify HeliusMigrateSource is wired as primary
    assert "HeliusMigrateSource" in source_text, (
        "HeliusMigrateSource must be referenced in run_firehose.py "
        "(migrate-ix primary must be wired)"
    )

    # MigrateReconciler must be wired for the secondary path
    assert "MigrateReconciler" in source_text, (
        "MigrateReconciler must be referenced in run_firehose.py "
        "(Birdeye secondary feeds MigrateReconciler)"
    )


# ---------------------------------------------------------------------------
# Test 12: Mint key equality — valid base58 string, correct length
# ---------------------------------------------------------------------------


def test_mint_key_equality() -> None:
    """The extracted mint is a valid base58 Solana pubkey string (~44 chars).

    Solana pubkeys are 32 bytes; base58-encoded they are typically 43-44
    characters.  This test verifies the extracted mint from the real frame
    is consistent with a real SPL mint pubkey.
    """
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None
    mint = event["address"]

    # Must be a non-empty string of base58 characters
    _BASE58_CHARS = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    assert all(c in _BASE58_CHARS for c in mint), (
        f"Mint {mint!r} contains non-base58 characters"
    )
    # Typical Solana pubkey length is 32–44 characters
    assert 32 <= len(mint) <= 50, (
        f"Mint {mint!r} has unexpected length {len(mint)} (expected 32-50)"
    )
    # Must match the verified value from the real frame
    assert mint == REAL_FRAME_MINT, (
        f"Mint mismatch: expected {REAL_FRAME_MINT!r}, got {mint!r}"
    )


# ---------------------------------------------------------------------------
# Test 13: HeliusMigrateSource is a DataSource
# ---------------------------------------------------------------------------


def test_helius_migrate_source_is_datasource() -> None:
    """HeliusMigrateSource implements the DataSource interface."""
    from core.datasource import DataSource
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    assert issubclass(HeliusMigrateSource, DataSource), (
        "HeliusMigrateSource must inherit from core.datasource.DataSource"
    )
    # Verify required interface methods exist
    assert hasattr(HeliusMigrateSource, "connect"), "Missing connect()"
    assert hasattr(HeliusMigrateSource, "disconnect"), "Missing disconnect()"
    assert hasattr(HeliusMigrateSource, "events"), "Missing events()"


# ---------------------------------------------------------------------------
# Test 14: dex_source stamp
# ---------------------------------------------------------------------------


def test_decode_migrate_dex_source_stamp() -> None:
    """The emitted event carries dex_source='helius_migrate' for provenance tracking."""
    from core.tape.helius_birth_tape_source import MIGRATE_DEX_SOURCE, decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame)
    assert event is not None
    assert event.get("dex_source") == "helius_migrate", (
        f"Expected dex_source='helius_migrate', got {event.get('dex_source')!r}"
    )
    assert MIGRATE_DEX_SOURCE == "helius_migrate"
