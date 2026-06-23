# ---
# module: core.tests.test_helius_migrate_source
# sprint: sprint-14
# story: EPIC-graduation-migrate-detection, hotfix-migrate-mint-rpc-resolution
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-22
# dependencies: core.tape.helius_birth_tape_source, core.detection.consumer,
#               core.tape.birdeye_graduation_source, core.detection.helius_reconciler,
#               core.clock, core.replay_source, asyncio, ast, json, pathlib, pytest,
#               unittest.mock
# ---
"""EPIC-graduation-migrate-detection + hotfix-migrate-mint-rpc-resolution.

Tests (all offline, no network):

1. test_decode_migrate_returns_meme_data_for_real_frame
     decode_helius_migrate_event returns a MEME_DATA event with candidate_accounts
     and address="" (unresolved) for the real fixture.

2. test_decode_migrate_candidate_accounts_includes_real_mint
     candidate_accounts from the real fixture includes the known real mint pubkey
     (proving the account is present in the tx for RPC resolution).

3. test_decode_migrate_none_for_trade_frame
     A synthetic Buy transactionNotification returns None.

4. test_decode_migrate_none_for_non_notification
     A subscription ack / non-transactionNotification dict returns None.

5. test_decode_migrate_none_for_failed_tx
     A migrate frame with meta.err set returns None.

6. test_decode_migrate_none_for_create_frame
     A transactionNotification with only Instruction: Create in logs returns None.

7. test_decode_migrate_event_shape
     The returned event has all required MEME_DATA fields and correct values
     (address="", candidate_accounts=list, correct blockTime/slot).

8. test_detection_consumer_accepts_migrate_event
     DetectionConsumer with a ReplaySource carrying a FULLY-resolved migrate event
     persists a Token row when a matching config is active.

9. test_migrate_source_ast_no_banned_imports
     AST scan: helius_birth_tape_source.py must not import LiveSource,
     ReplaySource, core.live_source, or core.replay_source.

10. test_migrate_source_ast_no_direct_time_calls
      AST scan: helius_birth_tape_source.py must not call datetime.now()
      or time.time() anywhere.

11. test_birdeye_fallback_still_wired
      _build_graduation() in run_firehose.py uses HeliusMigrateSource as
      primary AND _build_reconciler() references BirdeyeGraduationSource
      as secondary.  Verified by AST scan.

12. test_mint_key_equality
      extract_migrate_account_candidates returns a list that includes the known
      real SPL mint from the real fixture.

13. test_helius_migrate_source_is_datasource
      HeliusMigrateSource implements the DataSource interface.

14. test_decode_migrate_dex_source_stamp
      The emitted event has dex_source="helius_migrate".

15. test_decode_migrate_blocktime_equals_fallback_epoch_not_slot
      blockTime and graduated_block_time must equal fallback_epoch, NOT the slot.

16. test_resolve_spl_mint_returns_mint_on_success (mocked — no network)
      resolve_spl_mint with urlopen mocked to return a valid getMultipleAccounts
      response returns the single SPL-Token-owned "mint" account.

17. test_resolve_spl_mint_returns_none_on_zero_mints (mocked)
      resolve_spl_mint with urlopen mocked to return all PDA entries returns None.

18. test_resolve_spl_mint_returns_none_on_rpc_error (mocked)
      resolve_spl_mint with urlopen raising URLError returns None.

19. test_decode_and_dedupe_resolves_mint (mocked)
      _decode_and_dedupe with resolve_spl_mint mocked to return a known mint
      returns event["address"]==that mint.

20. test_decode_and_dedupe_dedupes_second_call (mocked)
      Second call for same mint returns None (dedupe).

21. test_decode_and_dedupe_non_migrate_returns_none
      _decode_and_dedupe returns None for a trade frame (non-migrate).

22. test_extract_migrate_account_candidates_includes_real_mint
      extract_migrate_account_candidates on the real fixture returns a non-empty
      list that includes the known real SPL mint pubkey.

23. test_helius_migrate_source_uses_injected_clock (mocked)
      HeliusMigrateSource events() yields an event with blockTime from injected
      clock when resolve_spl_mint is mocked.
"""
import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REAL_MIGRATE_FIXTURE = FIXTURES_DIR / "helius_migrate_v2_real_grad_1.json"
# Captured fee-program "MigrateBondingCurveCreator" txs — the FALSE POSITIVES the
# old substring marker mis-detected as graduations (must now decode to None).  The
# legacy helius_migrate_tx_real.json is one of these (it was NOT a real graduation).
FALSE_POSITIVE_FIXTURES = [
    FIXTURES_DIR / "helius_migrate_false_pos_feecreator_1.json",
    FIXTURES_DIR / "helius_migrate_false_pos_feecreator_2.json",
    FIXTURES_DIR / "helius_migrate_false_pos_feecreator_3.json",
    FIXTURES_DIR / "helius_migrate_tx_real.json",
]
HELIUS_SOURCE_FILE = REPO_ROOT / "core" / "tape" / "helius_birth_tape_source.py"
RUN_FIREHOSE_FILE = REPO_ROOT / "core" / "management" / "commands" / "run_firehose.py"

# ---------------------------------------------------------------------------
# Known values from a captured REAL graduation frame (2026-06-22, slot 428262359):
# a MigrateV2 tx that creates the PumpSwap pool (pAMMBay CreatePool).  Cross-checked
# against Dune pump_evt_completeevent.  (The prior fixture was a fee-program
# MigrateBondingCurveCreator tx — a false positive — now in FALSE_POSITIVE_FIXTURES.)
# ---------------------------------------------------------------------------

# The REAL SPL mint — present in the real graduation frame's accountKeys (index 14).
REAL_FRAME_MINT = "3ZLkpvUaZLSLZKfvQK9oFNcfduaGCYNe7RbZW7RhQTDG"
REAL_FRAME_SIG = "5NyPiTQvqWkcjJsyAiYVDe2jVzmtABTrLLyfhE2ZhkqhmN1QePQps9fM3gy7761nW2nhsGYAuxbuTXEkTDWJJbG5"
REAL_FRAME_SLOT = 428262359

# Fixed epoch used in all decode_helius_migrate_event test calls so tests are
# deterministic.  Value chosen to be obviously a real 2024 Unix timestamp (NOT a slot).
TEST_FALLBACK_EPOCH = 1_719_000_000

# SPL Token program address
_SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
WSOL_MINT = "So11111111111111111111111111111111111111112"

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
    import copy
    frame = _load_real_fixture()
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


def _build_mock_gma_response(mint_pubkey: str, all_pubkeys: list[str]) -> bytes:
    """Build a mock getMultipleAccounts JSON response with one SPL mint."""
    values = []
    for pk in all_pubkeys:
        if pk == mint_pubkey:
            values.append({
                "owner": _SPL_TOKEN_PROGRAM,
                "data": {
                    "parsed": {"type": "mint"},
                    "program": "spl-token",
                    "space": 82,
                },
            })
        elif pk == WSOL_MINT:
            values.append({
                "owner": _SPL_TOKEN_PROGRAM,
                "data": {
                    "parsed": {"type": "mint"},
                    "program": "spl-token",
                    "space": 82,
                },
            })
        else:
            # PDA / system account
            values.append({
                "owner": "11111111111111111111111111111111",
                "data": ["", "base64"],
            })
    response = {
        "jsonrpc": "2.0",
        "result": {"value": values},
        "id": 1,
    }
    return json.dumps(response).encode()


# ---------------------------------------------------------------------------
# Test 1: Real fixture returns MEME_DATA with address="" and candidate_accounts
# ---------------------------------------------------------------------------


def test_decode_migrate_returns_meme_data_for_real_frame() -> None:
    """decode_helius_migrate_event returns MEME_DATA with address='' and candidate_accounts."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    assert REAL_MIGRATE_FIXTURE.exists(), (
        f"Real migrate fixture not found at {REAL_MIGRATE_FIXTURE}. "
        "Run the capture script first."
    )
    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None, "decode_helius_migrate_event returned None for real migrate frame"
    assert event.get("type") == "MEME_DATA", f"Expected type='MEME_DATA', got {event.get('type')!r}"
    assert event.get("graduated") is True, "Expected graduated=True"
    # address is now UNRESOLVED — set to "" by the pure detector
    assert event.get("address") == "", (
        f"address should be '' (unresolved) after pure detect; got {event.get('address')!r}"
    )
    # candidate_accounts must be a non-empty list
    candidates = event.get("candidate_accounts")
    assert isinstance(candidates, list) and len(candidates) > 0, (
        "candidate_accounts must be a non-empty list for RPC resolution"
    )


# ---------------------------------------------------------------------------
# Test 2: candidate_accounts includes the real mint pubkey
# ---------------------------------------------------------------------------


def test_decode_migrate_candidate_accounts_includes_real_mint() -> None:
    """candidate_accounts from the real fixture includes the known real SPL mint pubkey."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None
    candidates = event.get("candidate_accounts") or []
    assert REAL_FRAME_MINT in candidates, (
        f"Real mint {REAL_FRAME_MINT!r} must be present in candidate_accounts. "
        f"Got: {candidates}"
    )


# ---------------------------------------------------------------------------
# Test 2b: creator-fee "MigrateBondingCurveCreator" frames must NOT graduate.
# These are the ~99% false-positive class the old substring marker caught (a
# pump.fun FEE-program tx, program pfeeUxB…, no PumpSwap pool).  Real captured
# frames — see memory graduation-detection-migratev2-substring-bug.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fp_fixture", FALSE_POSITIVE_FIXTURES, ids=lambda p: p.name
)
def test_decode_migrate_rejects_creator_fee_false_positives(fp_fixture) -> None:
    """A pump.fun FEE-program creator-fee tx (MigrateBondingCurveCreator) is NOT a
    graduation and must decode to None: it never creates a PumpSwap pool, which the
    old 'Instruction: Migrate' substring marker ignored (mis-detecting every
    creator-fee tx as a graduation)."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    assert fp_fixture.exists(), f"false-positive fixture missing: {fp_fixture}"
    with fp_fixture.open(encoding="utf-8") as fh:
        frame = json.load(fh)
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is None, (
        f"{fp_fixture.name} is a creator-fee tx (no PumpSwap pool) and must NOT be "
        f"detected as a graduation; got {event!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: None for trade frame (Buy)
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_trade_frame() -> None:
    """A Buy transactionNotification returns None (not a migrate event)."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_synthetic_trade_frame("Buy")
    assert decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH) is None


# ---------------------------------------------------------------------------
# Test 4: None for non-notification
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_non_notification() -> None:
    """A subscription ack dict returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    ack = _make_subscription_ack()
    assert decode_helius_migrate_event(ack, fallback_epoch=TEST_FALLBACK_EPOCH) is None


# ---------------------------------------------------------------------------
# Test 5: None for failed tx
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_failed_tx() -> None:
    """A migrate frame with meta.err set returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_migrate_frame_with_meta_err()
    assert decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH) is None


# ---------------------------------------------------------------------------
# Test 6: None for Create frame
# ---------------------------------------------------------------------------


def test_decode_migrate_none_for_create_frame() -> None:
    """A transactionNotification with only Instruction: Create returns None."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _make_create_frame()
    assert decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH) is None


# ---------------------------------------------------------------------------
# Test 7: Full event shape validation
# ---------------------------------------------------------------------------


def test_decode_migrate_event_shape() -> None:
    """The returned event has all required MEME_DATA fields with correct types.

    Key regression guard: blockTime and graduated_block_time must equal the
    passed fallback_epoch (a real Unix timestamp) — NOT the slot number.
    address must be '' (unresolved); candidate_accounts must be a non-empty list.
    """
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None

    # Required fields and types
    assert event["type"] == "MEME_DATA"
    assert event["graduated"] is True
    # address is now unresolved (empty string — will be set by _decode_and_dedupe)
    assert event["address"] == "", (
        f"address should be '' (unresolved); got {event['address']!r}"
    )
    # candidate_accounts must be a list for the resolver
    assert isinstance(event.get("candidate_accounts"), list), (
        "candidate_accounts must be a list"
    )
    assert len(event["candidate_accounts"]) > 0, (
        "candidate_accounts must be non-empty for RPC resolution"
    )
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

    # --- Regression guard: blockTime must be the passed fallback_epoch, NOT the slot ---
    assert event["blockTime"] == TEST_FALLBACK_EPOCH, (
        f"blockTime should equal fallback_epoch={TEST_FALLBACK_EPOCH}, "
        f"got {event['blockTime']} (if this looks like a slot number, the 1983 bug is back)"
    )
    assert event["graduated_block_time"] == TEST_FALLBACK_EPOCH, (
        f"graduated_block_time should equal fallback_epoch={TEST_FALLBACK_EPOCH}, "
        f"got {event['graduated_block_time']}"
    )
    # Real slot must still be preserved for audit/ordering
    assert event.get("slot") == REAL_FRAME_SLOT, (
        f"slot field should hold the real slot {REAL_FRAME_SLOT}, got {event.get('slot')!r}"
    )
    # The slot must NOT equal the blockTime (that would be the 1983 bug)
    assert event["slot"] != event["blockTime"], (
        "slot and blockTime must differ: slot is an ordering index, blockTime is a Unix epoch"
    )


# ---------------------------------------------------------------------------
# Test 8: DetectionConsumer accepts migrate event (with pre-resolved address)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_detection_consumer_accepts_migrate_event() -> None:
    """DetectionConsumer persists a Token row when fed a FULLY-resolved migrate event.

    Simulates the full pipeline: decode (address='', candidate_accounts) → manually
    set address to the known real mint (as _decode_and_dedupe does after RPC) →
    feed to DetectionConsumer via ReplaySource.
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

    # Decode the real migrate frame and resolve the mint (simulating _decode_and_dedupe)
    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None, "Decoder failed on real fixture"
    # Simulate the RPC resolution step (what _decode_and_dedupe does after resolve_spl_mint)
    event["address"] = REAL_FRAME_MINT

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
    assert Token.objects.filter(mint=REAL_FRAME_MINT).exists(), (
        f"Token row for mint={REAL_FRAME_MINT} not created by DetectionConsumer"
    )
    token = Token.objects.get(mint=REAL_FRAME_MINT)
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
    """BirdeyeGraduationSource is still imported in run_firehose.py (gap-fill secondary)."""
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
# Test 12: extract_migrate_account_candidates includes real mint
# ---------------------------------------------------------------------------


def test_mint_key_equality() -> None:
    """extract_migrate_account_candidates includes the known real SPL mint pubkey."""
    from core.tape.helius_birth_tape_source import extract_migrate_account_candidates

    frame = _load_real_fixture()
    candidates = extract_migrate_account_candidates(frame)

    assert isinstance(candidates, list) and len(candidates) > 0, (
        "extract_migrate_account_candidates must return a non-empty list"
    )
    assert REAL_FRAME_MINT in candidates, (
        f"Real mint {REAL_FRAME_MINT!r} must be in candidate_accounts. Got: {candidates}"
    )

    # All entries must be valid base58 pubkey strings
    _BASE58_CHARS = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    for pk in candidates:
        assert all(c in _BASE58_CHARS for c in pk), (
            f"Non-base58 pubkey in candidates: {pk!r}"
        )
        assert 32 <= len(pk) <= 50, (
            f"Unexpected pubkey length for {pk!r}: {len(pk)}"
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
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None
    assert event.get("dex_source") == "helius_migrate", (
        f"Expected dex_source='helius_migrate', got {event.get('dex_source')!r}"
    )
    assert MIGRATE_DEX_SOURCE == "helius_migrate"


# ---------------------------------------------------------------------------
# Test 15: graduated_block_time == fallback_epoch, slot preserved separately
# ---------------------------------------------------------------------------


def test_decode_migrate_blocktime_equals_fallback_epoch_not_slot() -> None:
    """graduated_block_time and blockTime must equal fallback_epoch, NOT the slot."""
    from core.tape.helius_birth_tape_source import decode_helius_migrate_event

    frame = _load_real_fixture()
    event = decode_helius_migrate_event(frame, fallback_epoch=TEST_FALLBACK_EPOCH)
    assert event is not None

    assert event["blockTime"] == TEST_FALLBACK_EPOCH, (
        f"blockTime={event['blockTime']} != fallback_epoch={TEST_FALLBACK_EPOCH}. "
        "If blockTime looks like a slot number (~4e8), the 1983 bug has returned."
    )
    assert event["graduated_block_time"] == TEST_FALLBACK_EPOCH, (
        f"graduated_block_time={event['graduated_block_time']} != fallback_epoch={TEST_FALLBACK_EPOCH}."
    )
    assert event.get("slot") == REAL_FRAME_SLOT, (
        f"slot field lost: expected {REAL_FRAME_SLOT}, got {event.get('slot')!r}"
    )
    assert event["slot"] != event["blockTime"], (
        "slot == blockTime: slot must NOT be used as a timestamp"
    )


# ---------------------------------------------------------------------------
# Test 16: resolve_spl_mint — mock success
# ---------------------------------------------------------------------------


def test_resolve_spl_mint_returns_mint_on_success() -> None:
    """resolve_spl_mint returns the SPL mint when urlopen returns a valid response."""
    from unittest.mock import MagicMock

    from core.tape.helius_birth_tape_source import resolve_spl_mint

    candidates = ["SomePDA111111111111111111111111111111111111",
                  REAL_FRAME_MINT,
                  WSOL_MINT]
    # Build a response: REAL_FRAME_MINT is SPL Token owned + "mint" type;
    # WSOL is also SPL Token "mint" but excluded by WSOL exclusion;
    # SomePDA is system account.
    mock_response_body = json.dumps({
        "jsonrpc": "2.0",
        "result": {
            "value": [
                # SomePDA — system account, NOT SPL Token
                {
                    "owner": "11111111111111111111111111111111",
                    "data": ["", "base64"],
                },
                # REAL_FRAME_MINT — SPL Token owned, type=mint
                {
                    "owner": _SPL_TOKEN_PROGRAM,
                    "data": {
                        "parsed": {"type": "mint"},
                        "program": "spl-token",
                        "space": 82,
                    },
                },
                # WSOL — SPL Token owned, type=mint, BUT excluded
                {
                    "owner": _SPL_TOKEN_PROGRAM,
                    "data": {
                        "parsed": {"type": "mint"},
                        "program": "spl-token",
                        "space": 82,
                    },
                },
            ]
        },
        "id": 1,
    }).encode()

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = mock_response_body

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = resolve_spl_mint(candidates, "test-api-key")

    assert result == REAL_FRAME_MINT, (
        f"Expected resolve_spl_mint to return {REAL_FRAME_MINT!r}, got {result!r}"
    )


def test_resolve_spl_mint_uses_confirmed_commitment() -> None:
    """Regression: getMultipleAccounts MUST query at commitment='confirmed'.

    A migrate notification arrives at 'confirmed'; the RPC default 'finalized' lags
    ~13s and reads the just-confirmed mint account as null, so the mint is missed
    and the graduation wrongly skipped (found live 2026-06-22: ~94% of live grads
    skipped at finalized vs resolved at confirmed).
    """
    from unittest.mock import MagicMock

    from core.tape.helius_birth_tape_source import resolve_spl_mint

    captured: dict = {}
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps({
        "result": {"value": [
            {"owner": _SPL_TOKEN_PROGRAM, "data": {"parsed": {"type": "mint"}}},
        ]},
        "id": 1,
    }).encode()

    def _capture(req, *a, **k):
        captured["body"] = req.data
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=_capture):
        result = resolve_spl_mint([REAL_FRAME_MINT], "test-api-key")

    assert result == REAL_FRAME_MINT
    cfg = json.loads(captured["body"].decode())["params"][1]
    assert cfg.get("commitment") == "confirmed", (
        f"getMultipleAccounts must use commitment='confirmed', got {cfg!r}"
    )


def test_resolve_spl_mint_disambiguates_token_from_lp_mint() -> None:
    """When token + pool LP mint are both visible (at 'confirmed'), pick the token.

    pump.fun token mints have a RENOUNCED (null) mintAuthority; the freshly-created
    PumpSwap LP mint has a non-null authority (the pool) and supply 0.  Found live
    2026-06-22: confirmed-commitment makes the LP mint visible too, so without this
    disambiguation ~13% of graduations skip as ambiguous.
    """
    from unittest.mock import MagicMock

    from core.tape.helius_birth_tape_source import resolve_spl_mint

    token = "DoMza6BcwHeWy5TMjjAsiUqxdCyo4AdprJmfN9y1pump"  # renounced authority
    lp = "HXRDSS3d7Sgj2NpinKzo9CAckX9CpHvcM5k14RbRHxHC"     # pool-owned authority
    candidates = [lp, token]

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps({
        "result": {"value": [
            # LP mint — non-null mintAuthority, supply 0
            {"owner": _SPL_TOKEN_PROGRAM, "data": {"parsed": {
                "type": "mint",
                "info": {
                    "mintAuthority": "8sWRutJTP3id6rToRxsQQNW4rvKjnTyKbPzSj3mLNA4K",
                    "supply": "0", "decimals": 9,
                },
            }}},
            # token mint — renounced (null) authority
            {"owner": _SPL_TOKEN_PROGRAM, "data": {"parsed": {
                "type": "mint",
                "info": {"mintAuthority": None, "supply": "1000000000000000", "decimals": 6},
            }}},
        ]},
        "id": 1,
    }).encode()

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = resolve_spl_mint(candidates, "test-api-key")

    assert result == token, f"Expected the renounced token mint {token!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Test 17: resolve_spl_mint — zero mints → None
# ---------------------------------------------------------------------------


def test_resolve_spl_mint_returns_none_on_zero_mints() -> None:
    """resolve_spl_mint returns None when no account is an SPL mint."""
    from unittest.mock import MagicMock

    from core.tape.helius_birth_tape_source import resolve_spl_mint

    candidates = ["SomePDA1111111111111111111111111111111111111",
                  "SomePDA2222222222222222222222222222222222222"]
    mock_response_body = json.dumps({
        "jsonrpc": "2.0",
        "result": {
            "value": [
                {"owner": "11111111111111111111111111111111", "data": ["", "base64"]},
                {"owner": "11111111111111111111111111111111", "data": ["", "base64"]},
            ]
        },
        "id": 1,
    }).encode()

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = mock_response_body

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = resolve_spl_mint(candidates, "test-api-key")

    assert result is None, f"Expected None for zero mints, got {result!r}"


# ---------------------------------------------------------------------------
# Test 18: resolve_spl_mint — RPC error → None
# ---------------------------------------------------------------------------


def test_resolve_spl_mint_returns_none_on_rpc_error() -> None:
    """resolve_spl_mint returns None when urlopen raises URLError."""
    import urllib.error

    from core.tape.helius_birth_tape_source import resolve_spl_mint

    candidates = [REAL_FRAME_MINT]

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = resolve_spl_mint(candidates, "test-api-key")

    assert result is None, f"Expected None on RPC error, got {result!r}"


# ---------------------------------------------------------------------------
# Test 19: _decode_and_dedupe — mock resolve_spl_mint, returns resolved event
# ---------------------------------------------------------------------------


def test_decode_and_dedupe_resolves_mint() -> None:
    """_decode_and_dedupe returns event['address']==resolved mint when resolver mocked."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="test", event_source="pump_dot_fun", clock=clock)

    migrate = _load_real_fixture()

    with patch(
        "core.tape.helius_birth_tape_source.resolve_spl_mint",
        return_value=REAL_FRAME_MINT,
    ):
        event = asyncio.run(src._decode_and_dedupe(migrate))

    assert event is not None, "_decode_and_dedupe should return an event for a new mint"
    assert event["type"] == "MEME_DATA"
    assert event["address"] == REAL_FRAME_MINT, (
        f"Expected address={REAL_FRAME_MINT!r}, got {event['address']!r}"
    )


# ---------------------------------------------------------------------------
# Test 20: _decode_and_dedupe — dedupe on second call
# ---------------------------------------------------------------------------


def test_decode_and_dedupe_dedupes_second_call() -> None:
    """_decode_and_dedupe returns None on second call for the same mint."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="test", event_source="pump_dot_fun", clock=clock)

    migrate = _load_real_fixture()

    with patch(
        "core.tape.helius_birth_tape_source.resolve_spl_mint",
        return_value=REAL_FRAME_MINT,
    ):
        first = asyncio.run(src._decode_and_dedupe(migrate))
        second = asyncio.run(src._decode_and_dedupe(migrate))

    assert first is not None, "First call should return an event"
    assert second is None, (
        "_decode_and_dedupe should return None on second call (same mint, per-session dedupe)"
    )


# ---------------------------------------------------------------------------
# Test 21: _decode_and_dedupe — non-migrate returns None
# ---------------------------------------------------------------------------


def test_decode_and_dedupe_non_migrate_returns_none() -> None:
    """_decode_and_dedupe returns None for a trade frame (non-migrate)."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    clock = VirtualClock(datetime.fromtimestamp(TEST_FALLBACK_EPOCH, tz=timezone.utc))
    src = HeliusMigrateSource(api_key="test", event_source="pump_dot_fun", clock=clock)

    trade_frame = _make_synthetic_trade_frame("Buy")

    # resolve_spl_mint should NOT be called for non-migrate frames
    with patch(
        "core.tape.helius_birth_tape_source.resolve_spl_mint",
        side_effect=AssertionError("resolve_spl_mint must not be called for non-migrate frames"),
    ):
        result = asyncio.run(src._decode_and_dedupe(trade_frame))

    assert result is None, "_decode_and_dedupe must return None for a trade frame"


# ---------------------------------------------------------------------------
# Test 22: extract_migrate_account_candidates
# ---------------------------------------------------------------------------


def test_extract_migrate_account_candidates_includes_real_mint() -> None:
    """extract_migrate_account_candidates on real fixture returns list with known mint."""
    from core.tape.helius_birth_tape_source import extract_migrate_account_candidates

    frame = _load_real_fixture()
    candidates = extract_migrate_account_candidates(frame)

    assert isinstance(candidates, list), "Must return a list"
    assert len(candidates) > 0, "Must be non-empty"
    assert REAL_FRAME_MINT in candidates, (
        f"Real mint {REAL_FRAME_MINT!r} must be in candidates. Got: {candidates}"
    )


# ---------------------------------------------------------------------------
# Test 23: HeliusMigrateSource events() uses injected clock (mocked resolver)
# ---------------------------------------------------------------------------


def test_helius_migrate_source_uses_injected_clock() -> None:
    """HeliusMigrateSource events() yields event with blockTime from injected clock."""
    from core.clock import VirtualClock
    from core.tape.helius_birth_tape_source import HeliusMigrateSource

    CLOCK_EPOCH = 1_750_000_000   # approx 2025-06-15
    clock = VirtualClock(datetime.fromtimestamp(CLOCK_EPOCH, tz=timezone.utc))

    frame = _load_real_fixture()

    class _FakeWS:
        def __init__(self, frames: list[str]) -> None:
            self._frames = frames

        def __aiter__(self):
            self._it = iter(self._frames)
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

        async def close(self) -> None:
            pass

    src = HeliusMigrateSource(api_key="testkey", clock=clock)
    src._ws = _FakeWS([json.dumps(frame)])

    events_collected: list[dict] = []

    async def _run() -> None:
        with patch(
            "core.tape.helius_birth_tape_source.resolve_spl_mint",
            return_value=REAL_FRAME_MINT,
        ):
            async for ev in src.events():
                events_collected.append(ev)

    asyncio.run(_run())

    assert len(events_collected) == 1, (
        f"Expected 1 graduation event from fake WS, got {len(events_collected)}"
    )
    ev = events_collected[0]
    assert ev["graduated_block_time"] == CLOCK_EPOCH, (
        f"graduated_block_time={ev['graduated_block_time']} should equal "
        f"CLOCK_EPOCH={CLOCK_EPOCH} (the injected clock's epoch). "
        "If it looks like a slot number, the clock injection is broken."
    )
    assert ev["blockTime"] == CLOCK_EPOCH
    assert ev.get("slot") == REAL_FRAME_SLOT
    assert ev["address"] == REAL_FRAME_MINT, (
        f"Expected resolved address={REAL_FRAME_MINT!r}, got {ev['address']!r}"
    )
