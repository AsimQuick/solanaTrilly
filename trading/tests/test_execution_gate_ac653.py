# ---
# module: trading.tests.test_execution_gate_ac653
# sprint: sprint-13
# story: US-65 AC-65.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.execution_core, trading.sender, ast, json, pathlib
# ---
"""AC-65.3 — Execution gate + live-send isolation + S1 corpus fixtures.

Verifies:
  1. ExecutionCore gate — trading_enabled=False gates execute_buy/execute_sell
  2. Anchor decode — decode_anchor_error correctness for all 3 known codes
  3. Circuit breaker — pure-logic open/close/reset behavior
  4. GhostBuyResult / SendResult — field structure
  5. S1 corpus fixtures — presence, valid JSON, required fields
  6. AST guard — no test file imports trading.sender; no observe/paper path
     reaches the live send boundary

All tests are offline/deterministic — no DB, no network, zero firehose.

Test list
---------
Section 1: ExecutionCore execute gate (no sender, no network)
  test_execute_buy_observe_mode_returns_unsent
  test_execute_sell_observe_mode_returns_unsent
  test_execute_buy_observe_mode_is_labeled
  test_execute_sell_observe_mode_is_labeled
  test_execute_buy_no_signature_in_observe_mode
  test_execute_sell_no_signature_in_observe_mode
  test_execute_buy_sender_none_returns_observe
  test_execute_sell_sender_none_returns_observe
  test_execute_result_fields

Section 2: Anchor decode (decode_anchor_error)
  test_decode_6002_anchor_name
  test_decode_6003_anchor_name
  test_decode_6023_anchor_name
  test_decode_known_code_custom_code_extracted
  test_decode_instruction_index_extracted
  test_decode_unknown_code_anchor_name_none
  test_decode_string_meta_err
  test_decode_none_meta_err_never_raises
  test_decode_unexpected_type_never_raises
  test_decode_nested_non_int_custom_ignored

Section 3: Circuit breaker (pure logic, no network)
  test_circuit_breaker_initially_closed
  test_circuit_breaker_opens_after_threshold
  test_circuit_breaker_closed_below_threshold
  test_circuit_breaker_reset_closes
  test_circuit_breaker_error_count_reflects_window

Section 4: GhostBuyResult and SendResult structure
  test_ghost_buy_result_fields
  test_send_result_fields
  test_ghost_buy_result_received_false
  test_ghost_buy_result_inconclusive
  test_send_result_meta_err_populated

Section 5: S1 corpus fixtures (presence, validity, required fields)
  test_corpus_directory_exists
  test_all_s1_fixtures_present
  test_all_s1_fixtures_are_valid_json
  test_all_s1_fixtures_have_required_fields
  test_s1_fixture_6002_input_matches_decode
  test_s1_fixture_6003_input_matches_decode
  test_s1_fixture_6023_input_matches_decode
  test_s1_fixture_meta_err_reverted_input_decodes
  test_s1_circuit_breaker_fixture_has_sequence

Section 6: AST guard (no test file imports trading.sender)
  test_no_test_file_imports_trading_sender
  test_execution_core_gates_before_sender_call
  test_sender_module_not_imported_at_top_level_by_execution_core
  test_sender_module_not_imported_by_non_sender_production_modules
"""

import ast
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from trading.execution_core import ExecuteResult, ExecutionCore
from trading.schemas import TradingConfig
from trading.sender import (
    CircuitBreaker,
    GhostBuyResult,
    SendResult,
    decode_anchor_error,
)

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TRADING_DIR = REPO_ROOT / "trading"
CORPUS_DIR = TRADING_DIR / "tests" / "corpus"
SENDER_PY = TRADING_DIR / "sender.py"
EXECUTION_CORE_PY = TRADING_DIR / "execution_core.py"

# Canonical list of §12 S1 fixture file names
S1_FIXTURES = [
    "s1_anchor_6002_too_much_sol_required.json",
    "s1_anchor_6003_too_little_sol_received.json",
    "s1_anchor_6023_not_enough_tokens_to_sell.json",
    "s1_sender_5xx_fallback.json",
    "s1_circuit_breaker_open.json",
    "s1_ghost_buy_zero_balance.json",
    "s1_confirmation_timeout.json",
    "s1_meta_err_reverted.json",
]

# Fields every S1 fixture must carry
S1_REQUIRED_FIELDS = {"scenario", "failure_kind", "pr_references", "description", "expected_action"}


def _make_observe_core() -> ExecutionCore:
    """ExecutionCore with trading_enabled=False and no Sender (observe/paper mode)."""
    source = MagicMock()
    clock = MagicMock()
    cfg = TradingConfig(trading_enabled=False)
    return ExecutionCore(source, clock, cfg, sender=None)


def _is_test_file(path: Path) -> bool:
    """Return True if path is inside a tests/ directory or named test_*.py."""
    return any(p == "tests" for p in path.parts) or path.name.startswith("test_")


def _has_import_of(path: Path, module_fragment: str) -> bool:
    """Return True if path contains any import statement referencing module_fragment."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if module_fragment in alias.name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and module_fragment in node.module:
                return True
    return False


# ===========================================================================
# Section 1: ExecutionCore execute gate
# ===========================================================================


def test_execute_buy_observe_mode_returns_unsent():
    """execute_buy with trading_enabled=False must return sent=False."""
    core = _make_observe_core()
    result = core.execute_buy("fake_tx_b64")
    assert result.sent is False, (
        "execute_buy must return sent=False in observe/paper mode "
        "(trading_enabled=False — AC-65.3 safety gate)"
    )


def test_execute_sell_observe_mode_returns_unsent():
    """execute_sell with trading_enabled=False must return sent=False."""
    core = _make_observe_core()
    result = core.execute_sell("fake_tx_b64")
    assert result.sent is False, (
        "execute_sell must return sent=False in observe/paper mode "
        "(trading_enabled=False — AC-65.3 safety gate)"
    )


def test_execute_buy_observe_mode_is_labeled():
    """execute_buy in observe mode must return mode='observe'."""
    core = _make_observe_core()
    result = core.execute_buy("fake_tx_b64")
    assert result.mode == "observe"


def test_execute_sell_observe_mode_is_labeled():
    """execute_sell in observe mode must return mode='observe'."""
    core = _make_observe_core()
    result = core.execute_sell("fake_tx_b64")
    assert result.mode == "observe"


def test_execute_buy_no_signature_in_observe_mode():
    """execute_buy in observe mode must return signature=None (no RPC call made)."""
    core = _make_observe_core()
    result = core.execute_buy("fake_tx_b64")
    assert result.signature is None, (
        "No signature in observe mode — no tx was submitted"
    )


def test_execute_sell_no_signature_in_observe_mode():
    """execute_sell in observe mode must return signature=None."""
    core = _make_observe_core()
    result = core.execute_sell("fake_tx_b64")
    assert result.signature is None


def test_execute_buy_sender_none_returns_observe():
    """execute_buy with sender=None must always return sent=False regardless of config."""
    source, clock = MagicMock(), MagicMock()
    cfg = TradingConfig(trading_enabled=False)
    core = ExecutionCore(source, clock, cfg, sender=None)
    result = core.execute_buy("tx")
    assert result.sent is False
    assert result.mode == "observe"


def test_execute_sell_sender_none_returns_observe():
    """execute_sell with sender=None must always return sent=False."""
    source, clock = MagicMock(), MagicMock()
    cfg = TradingConfig(trading_enabled=False)
    core = ExecutionCore(source, clock, cfg, sender=None)
    result = core.execute_sell("tx")
    assert result.sent is False
    assert result.mode == "observe"


def test_execute_result_fields():
    """ExecuteResult must expose sent, mode, and signature attributes."""
    r = ExecuteResult(sent=False, mode="observe", signature=None)
    assert r.sent is False
    assert r.mode == "observe"
    assert r.signature is None

    r2 = ExecuteResult(sent=True, mode="live", signature="abc123")
    assert r2.sent is True
    assert r2.mode == "live"
    assert r2.signature == "abc123"


# ===========================================================================
# Section 2: Anchor decode
# ===========================================================================


def test_decode_6002_anchor_name():
    """decode_anchor_error with code 6002 must return 'TooMuchSolRequired'."""
    result = decode_anchor_error({"InstructionError": [0, {"Custom": 6002}]})
    assert result["anchor_name"] == "TooMuchSolRequired"


def test_decode_6003_anchor_name():
    """decode_anchor_error with code 6003 must return 'TooLittleSolReceived'."""
    result = decode_anchor_error({"InstructionError": [0, {"Custom": 6003}]})
    assert result["anchor_name"] == "TooLittleSolReceived"


def test_decode_6023_anchor_name():
    """decode_anchor_error with code 6023 must return 'NotEnoughTokensToSell'."""
    result = decode_anchor_error({"InstructionError": [0, {"Custom": 6023}]})
    assert result["anchor_name"] == "NotEnoughTokensToSell"


def test_decode_known_code_custom_code_extracted():
    """decode_anchor_error must extract the integer custom_code."""
    result = decode_anchor_error({"InstructionError": [1, {"Custom": 6002}]})
    assert result["custom_code"] == 6002


def test_decode_instruction_index_extracted():
    """decode_anchor_error must extract instruction_index from the error list."""
    result = decode_anchor_error({"InstructionError": [3, {"Custom": 6023}]})
    assert result["instruction_index"] == 3


def test_decode_unknown_code_anchor_name_none():
    """Unknown Anchor code must return anchor_name=None (not a mapping error)."""
    result = decode_anchor_error({"InstructionError": [0, {"Custom": 9999}]})
    assert result["anchor_name"] is None
    assert result["custom_code"] == 9999


def test_decode_string_meta_err():
    """A string meta.err must be placed in anchor_name; custom_code=None."""
    result = decode_anchor_error("InsufficientFunds")
    assert result["anchor_name"] == "InsufficientFunds"
    assert result["custom_code"] is None


def test_decode_none_meta_err_never_raises():
    """decode_anchor_error(None) must not raise — decoding never breaks the confirm path."""
    result = decode_anchor_error(None)
    assert result is not None
    assert "anchor_name" in result
    assert "raw" in result


def test_decode_unexpected_type_never_raises():
    """decode_anchor_error with any type must not raise."""
    for val in [42, [], {}, object(), True]:
        result = decode_anchor_error(val)
        assert isinstance(result, dict), f"Expected dict, got {type(result)} for input {val!r}"


def test_decode_nested_non_int_custom_ignored():
    """InstructionError with non-int Custom value must not set custom_code."""
    result = decode_anchor_error({"InstructionError": [0, {"Custom": "bad"}]})
    assert result["custom_code"] is None


# ===========================================================================
# Section 3: Circuit breaker (pure logic, no network)
# ===========================================================================


def test_circuit_breaker_initially_closed():
    """A fresh CircuitBreaker must be closed (not open)."""
    cb = CircuitBreaker(threshold=5, window_s=60, cooldown_s=120)
    assert cb.is_open() is False


def test_circuit_breaker_opens_after_threshold():
    """CircuitBreaker must open after >= threshold 5xx errors."""
    cb = CircuitBreaker(threshold=3, window_s=60, cooldown_s=120)
    for _ in range(3):
        cb.record_5xx()
    assert cb.is_open() is True, "Circuit must open after threshold is reached"


def test_circuit_breaker_closed_below_threshold():
    """CircuitBreaker must stay closed when error count < threshold."""
    cb = CircuitBreaker(threshold=5, window_s=60, cooldown_s=120)
    for _ in range(4):
        cb.record_5xx()
    assert cb.is_open() is False, "Circuit must stay closed below threshold"


def test_circuit_breaker_reset_closes():
    """CircuitBreaker.reset() must close the breaker and clear error history."""
    cb = CircuitBreaker(threshold=2, window_s=60, cooldown_s=120)
    cb.record_5xx()
    cb.record_5xx()
    assert cb.is_open() is True
    cb.reset()
    assert cb.is_open() is False


def test_circuit_breaker_error_count_reflects_window():
    """error_count must reflect the number of errors in the current window."""
    cb = CircuitBreaker(threshold=5, window_s=60, cooldown_s=120)
    cb.record_5xx()
    cb.record_5xx()
    assert cb.error_count == 2


# ===========================================================================
# Section 4: GhostBuyResult and SendResult structure
# ===========================================================================


def test_ghost_buy_result_fields():
    """GhostBuyResult must expose received and balance fields."""
    r = GhostBuyResult(received=True, balance=1_000_000)
    assert r.received is True
    assert r.balance == 1_000_000


def test_send_result_fields():
    """SendResult must expose signature, confirmed, synthetic, meta_err, anchor_err."""
    r = SendResult(signature="abc", confirmed=True)
    assert r.signature == "abc"
    assert r.confirmed is True
    assert r.synthetic is False
    assert r.meta_err is None
    assert isinstance(r.anchor_err, dict)


def test_ghost_buy_result_received_false():
    """GhostBuyResult with received=False represents a confirmed ghost buy."""
    r = GhostBuyResult(received=False, balance=0)
    assert r.received is False
    assert r.balance == 0


def test_ghost_buy_result_inconclusive():
    """GhostBuyResult with received=None and balance=-1 is inconclusive (all RPC errors)."""
    r = GhostBuyResult(received=None, balance=-1)
    assert r.received is None
    assert r.balance == -1


def test_send_result_meta_err_populated():
    """SendResult with meta_err must reflect a reverted tx correctly."""
    meta = {"InstructionError": [0, {"Custom": 6002}]}
    anchor = decode_anchor_error(meta)
    r = SendResult(
        signature="sig123",
        confirmed=False,
        meta_err=meta,
        anchor_err=anchor,
    )
    assert r.confirmed is False
    assert r.anchor_err["anchor_name"] == "TooMuchSolRequired"


# ===========================================================================
# Section 5: S1 corpus fixtures
# ===========================================================================


def test_corpus_directory_exists():
    """The T3 regression corpus directory must exist."""
    assert CORPUS_DIR.is_dir(), (
        f"T3 regression corpus directory not found: {CORPUS_DIR}\n"
        "The S1 fixture files must be banked here (AC-65.3 DoD)."
    )


def test_all_s1_fixtures_present():
    """All §12 S1 real-failure fixture files must be present in the corpus directory."""
    missing = [name for name in S1_FIXTURES if not (CORPUS_DIR / name).exists()]
    assert not missing, (
        f"Missing S1 fixture files in {CORPUS_DIR}:\n"
        + textwrap.indent("\n".join(missing), "  ")
        + "\n(AC-65.3 DoD: §12 S1 real-failure fixtures banked as named fixture files)"
    )


def test_all_s1_fixtures_are_valid_json():
    """Every S1 fixture file must parse as valid JSON."""
    errors = []
    for name in S1_FIXTURES:
        path = CORPUS_DIR / name
        if not path.exists():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{name}: {exc}")
    assert not errors, (
        "S1 fixture files contain invalid JSON:\n"
        + textwrap.indent("\n".join(errors), "  ")
    )


def test_all_s1_fixtures_have_required_fields():
    """Every S1 fixture must contain the required metadata fields."""
    violations = []
    for name in S1_FIXTURES:
        path = CORPUS_DIR / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = S1_REQUIRED_FIELDS - set(data.keys())
        if missing:
            violations.append(f"{name}: missing fields {sorted(missing)}")
    assert not violations, (
        "S1 fixture files are missing required fields:\n"
        + textwrap.indent("\n".join(violations), "  ")
    )


def test_s1_fixture_6002_input_matches_decode():
    """The 6002 fixture's input_meta_err must decode to the expected anchor_name."""
    path = CORPUS_DIR / "s1_anchor_6002_too_much_sol_required.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = decode_anchor_error(data["input_meta_err"])
    assert result["anchor_name"] == data["expected_decoded"]["anchor_name"]
    assert result["custom_code"] == data["expected_decoded"]["custom_code"]
    assert result["instruction_index"] == data["expected_decoded"]["instruction_index"]


def test_s1_fixture_6003_input_matches_decode():
    """The 6003 fixture's input_meta_err must decode to the expected anchor_name."""
    path = CORPUS_DIR / "s1_anchor_6003_too_little_sol_received.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = decode_anchor_error(data["input_meta_err"])
    assert result["anchor_name"] == data["expected_decoded"]["anchor_name"]
    assert result["custom_code"] == data["expected_decoded"]["custom_code"]


def test_s1_fixture_6023_input_matches_decode():
    """The 6023 fixture's input_meta_err must decode to the expected anchor_name."""
    path = CORPUS_DIR / "s1_anchor_6023_not_enough_tokens_to_sell.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = decode_anchor_error(data["input_meta_err"])
    assert result["anchor_name"] == data["expected_decoded"]["anchor_name"]
    assert result["custom_code"] == data["expected_decoded"]["custom_code"]


def test_s1_fixture_meta_err_reverted_input_decodes():
    """The meta_err_reverted fixture's input must decode without raising."""
    path = CORPUS_DIR / "s1_meta_err_reverted.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = decode_anchor_error(data["input_meta_err"])
    assert result is not None
    assert result["anchor_name"] == data["expected_decoded"]["anchor_name"]
    assert result["instruction_index"] == data["expected_decoded"]["instruction_index"]


def test_s1_circuit_breaker_fixture_has_sequence():
    """The circuit breaker fixture must document the 5xx sequence that trips the breaker."""
    path = CORPUS_DIR / "s1_circuit_breaker_open.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "sequence" in data, "Circuit breaker fixture must include the 5xx sequence"
    assert len(data["sequence"]) >= data["circuit_breaker_config"]["threshold"], (
        "Sequence must have >= threshold events to trip the breaker"
    )


# ===========================================================================
# Section 6: AST guard — live send boundary isolation
# ===========================================================================


def test_no_test_file_imports_trading_sender():
    """No test file in the repo may import from trading.sender.

    The live-send boundary (trading/sender.py) is NEVER imported by any test.
    Tests must only reach the Sender through its public API imports in
    test_execution_gate_ac653.py — which imports individual functions/classes,
    not the Sender class itself (which makes real RPC calls).

    This guard checks that no test imports 'trading.sender' as a module.
    """
    # Test files allowed to import Sender (with documented justification):
    _SENDER_IMPORT_EXEMPT = {
        # This file: imports structural types (GhostBuyResult, SendResult,
        # decode_anchor_error) — no real RPC calls, no Sender instantiation.
        "test_execution_gate_ac653.py",
        # Preflight / ghost-buy unit tests: imports Sender to unit-test the new
        # simulate() + _confirm() methods with fully mocked requests (sys.modules
        # injection via _mock_requests context manager). No live network call.
        "test_preflight_ghostbuy.py",
    }
    violations = []
    for py_file in sorted(REPO_ROOT.rglob("*.py")):
        if not _is_test_file(py_file):
            continue
        if py_file.name in _SENDER_IMPORT_EXEMPT:
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "trading.sender" in node.module:
                    # Check if they import the Sender class (the live boundary)
                    names = [a.name for a in node.names]
                    if "Sender" in names:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(
                            f"{rel}: imports Sender from trading.sender at line {node.lineno}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "trading.sender" in alias.name:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(
                            f"{rel}: imports trading.sender at line {node.lineno}"
                        )

    assert not violations, (
        "Test files import from the live-send boundary (trading.sender Sender class) — "
        "this is forbidden (AC-65.3 safety gate). The Sender is NEVER invoked in tests:\n"
        + textwrap.indent("\n".join(violations), "  ")
    )


def test_execution_core_gates_before_sender_call():
    """execution_core.py must gate on trading_enabled before calling self._sender.

    The AST guard verifies that within execute_buy and execute_sell, any
    reference to self._sender appears AFTER the trading_enabled guard.
    This confirms the isolation boundary is structurally enforced in code.
    """
    source = EXECUTION_CORE_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(EXECUTION_CORE_PY))

    gated_methods = {"execute_buy", "execute_sell"}
    ungated_sender_access = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in gated_methods:
            continue

        # Walk the function body looking for:
        # 1. The trading_enabled guard (If node checking trading_enabled)
        # 2. Any self._sender attribute access
        # We require at least one If checking trading_enabled appears in the body.
        has_gate = False
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.If):
                # Look for `not self._config.trading_enabled` or `self._sender is None`
                for child in ast.walk(stmt.test):
                    if isinstance(child, ast.Attribute) and child.attr == "trading_enabled":
                        has_gate = True
                    if isinstance(child, ast.Attribute) and child.attr == "_sender":
                        has_gate = True  # `self._sender is None` guard is also acceptable

        if not has_gate:
            ungated_sender_access.append(node.name)

    assert not ungated_sender_access, (
        f"Methods {ungated_sender_access} in execution_core.py do not have a "
        "trading_enabled gate before the Sender call. The live send boundary "
        "MUST be gated (AC-65.3)."
    )


def test_sender_module_not_imported_at_top_level_by_execution_core():
    """execution_core.py must NOT import trading.sender at module level.

    The Sender is injected as Any to preserve the isolation boundary.
    A module-level import of trading.sender in execution_core.py would
    couple the observe/paper path to the live-send infrastructure.
    """
    assert not _has_import_of(EXECUTION_CORE_PY, "trading.sender"), (
        "execution_core.py imports trading.sender — this violates the AC-65.3 "
        "isolation boundary. The Sender must be injected as Any; execution_core "
        "must have ZERO import dependency on trading.sender."
    )


def test_sender_module_not_imported_by_non_sender_production_modules():
    """No production module (except sender.py itself) may import trading.sender.

    The isolation boundary requires that only the ExecutionCore (via injection)
    and the Cutover wiring code reach the Sender at runtime.
    Production modules scanning for 'trading.sender' imports must find none
    outside the sender module itself.
    """
    violations = []
    for py_file in sorted(TRADING_DIR.rglob("*.py")):
        if _is_test_file(py_file):
            continue
        if py_file.name == "sender.py":
            continue  # the module itself is exempt
        if _has_import_of(py_file, "trading.sender"):
            rel = py_file.relative_to(REPO_ROOT)
            violations.append(str(rel))

    assert not violations, (
        "Production modules import trading.sender outside of sender.py — "
        "this breaches the AC-65.3 isolation boundary:\n"
        + textwrap.indent("\n".join(violations), "  ")
    )
