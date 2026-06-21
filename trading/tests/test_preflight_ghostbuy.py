# ---
# module: trading.tests.test_preflight_ghostbuy
# sprint: hotfix/preflight-ghostbuy
# story: preflight-ghostbuy
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, pytest-django, unittest.mock, trading.execution_core,
#   trading.sender, copytrade.position_opener, copytrade.models, trading.models
# ---
"""Tests for the two gated live-path changes:

  Change 1 — simulateTransaction PREFLIGHT before real send
  ----------------------------------------------------------
  1. simulate ok=True  → execute_buy calls send_buy once (proceeds to send).
  2. simulate ok=False → execute_buy returns sent=False, reason='preflight_failed';
     send_buy NOT called.
  3. simulate ok=None  → inconclusive (network error) → fail-safe, NOT sent,
     reason='preflight_failed'; send_buy NOT called.
  4. Observe path unchanged: trading_enabled=False → simulate never called,
     sent=False, mode='observe'.

  Change 2 — Ghost-buy reconciliation + real landed fill
  -------------------------------------------------------
  5. Ghost received=False → NO CopytradePosition written, NO shared Position row,
     returns (None, exec_result).
  6. Ghost received=None  → same as above (inconclusive → void).
  7. Ghost received=True  → CopytradePosition written with real_entry_price
     and real_sol_spent; shared Position has STATUS_OPEN (non-PAPER).
  8. Landed fill math: real_entry_price == real_sol_spent / real_tokens_received
     for representative lamport values.
  9. Non-PAPER status: a reconciled live position has shared Position status=OPEN;
     an observe position has STATUS_PAPER (unchanged).
  10. Observe path regression: trading_enabled=False → ghost/reconcile code is
      NEVER reached; behavior is byte-for-byte identical to before.
  11. Missing sender: live send with no sender → PAPER fallback (never crashes).

  Sender.simulate() unit tests
  ----------------------------
  12. simulate() parses result.value.err=None → ok=True.
  13. simulate() parses result.value.err != None → ok=False with decoded err.
  14. simulate() on network error → ok=None.
  15. simulate() on malformed response → ok=None.
  16. simulate() on RPC-level error field → ok=None.
  17. SimulateResult fields: ok, err, raw_err.

  SendResult balance fields
  -------------------------
  18. SendResult carries pre/post_balance_lamports and sol_spent_lamports.
  19. _confirm parses preBalances[0] and postBalances[0] correctly.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from trading.execution_core import ExecuteResult, ExecutionCore
from trading.schemas import TradingConfig
from trading.sender import (
    GhostBuyResult,
    SendResult,
    SimulateResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
_MINT = "Mint123PumpXXXXXXXXXXXXXXXXXXXXXXXXXXXXpump"
_COHORT = "cohort-preflight-test"
_WALLET = "Wallet123AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

_LAMPORTS_PER_SOL = 1_000_000_000


def _make_live_config() -> TradingConfig:
    return TradingConfig(
        trading_enabled=True,
        max_daily_spend_sol=1.0,
        max_open_live_positions=5,
    )


def _make_observe_config() -> TradingConfig:
    return TradingConfig(trading_enabled=False)


def _make_sender_mock(
    sim_ok: bool | None = True,
    send_signature: str = "sig123",
) -> MagicMock:
    """Build a mock Sender with simulate + send_buy pre-wired."""
    mock = MagicMock()
    mock.simulate.return_value = SimulateResult(ok=sim_ok)
    mock.send_buy.return_value = SendResult(
        signature=send_signature,
        confirmed=True,
        sol_spent_lamports=20_000_000,  # 0.02 SOL in lamports
    )
    return mock


def _make_live_core(sender: object) -> ExecutionCore:
    """ExecutionCore with trading_enabled=True and a mocked sender + budget bypass."""
    source = MagicMock()
    clock = MagicMock()
    cfg = _make_live_config()
    core = ExecutionCore(source, clock, cfg, sender=sender)
    return core


# ---------------------------------------------------------------------------
# Section 1: Preflight gate on execute_buy
# ---------------------------------------------------------------------------


def test_preflight_ok_calls_send_buy():
    """simulate ok=True → send_buy called exactly once."""
    sender = _make_sender_mock(sim_ok=True)
    core = _make_live_core(sender)

    # Bypass budget DB queries
    with patch("copytrade.models.CopytradePosition.objects") as mock_qs:
        mock_qs.filter.return_value.aggregate.return_value = {"total": 0.0}
        mock_qs.filter.return_value.count.return_value = 0
        result = core.execute_buy("fake_tx_b64", sol_amount=0.02)

    assert result.sent is True
    assert result.mode == "live"
    sender.simulate.assert_called_once_with("fake_tx_b64")
    sender.send_buy.assert_called_once_with("fake_tx_b64")


def test_preflight_fail_err_does_not_call_send_buy():
    """simulate ok=False → send_buy NOT called; returns sent=False reason=preflight_failed."""
    sender = _make_sender_mock(sim_ok=False)
    core = _make_live_core(sender)

    with patch("copytrade.models.CopytradePosition.objects") as mock_qs:
        mock_qs.filter.return_value.aggregate.return_value = {"total": 0.0}
        mock_qs.filter.return_value.count.return_value = 0
        result = core.execute_buy("fake_tx_b64", sol_amount=0.02)

    assert result.sent is False
    assert result.mode == "live"
    assert result.reason == "preflight_failed"
    sender.simulate.assert_called_once_with("fake_tx_b64")
    sender.send_buy.assert_not_called()


def test_preflight_inconclusive_does_not_call_send_buy():
    """simulate ok=None (network error) → fail-safe: NOT sent, reason=preflight_failed."""
    sender = _make_sender_mock(sim_ok=None)
    core = _make_live_core(sender)

    with patch("copytrade.models.CopytradePosition.objects") as mock_qs:
        mock_qs.filter.return_value.aggregate.return_value = {"total": 0.0}
        mock_qs.filter.return_value.count.return_value = 0
        result = core.execute_buy("fake_tx_b64", sol_amount=0.02)

    assert result.sent is False
    assert result.reason == "preflight_failed"
    sender.send_buy.assert_not_called()


def test_observe_path_does_not_call_simulate():
    """trading_enabled=False → simulate is NEVER called (observe path unchanged)."""
    sender = _make_sender_mock(sim_ok=True)
    source, clock = MagicMock(), MagicMock()
    cfg = _make_observe_config()
    core = ExecutionCore(source, clock, cfg, sender=sender)

    result = core.execute_buy("fake_tx_b64")

    assert result.sent is False
    assert result.mode == "observe"
    sender.simulate.assert_not_called()
    sender.send_buy.assert_not_called()


def test_observe_no_sender_does_not_call_simulate():
    """sender=None in observe mode → simulate never called."""
    source, clock = MagicMock(), MagicMock()
    cfg = _make_observe_config()
    core = ExecutionCore(source, clock, cfg, sender=None)

    result = core.execute_buy("fake_tx_b64")

    assert result.sent is False
    assert result.mode == "observe"


# ---------------------------------------------------------------------------
# Section 2: Ghost-buy reconciliation + real landed fill (DB tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ghost_received_false_no_position():
    """Ghost buy (received=False) → NO CopytradePosition row, NO shared Position row."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import open_live_position
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    sender = MagicMock()
    sender.verify_ghost_buy.return_value = GhostBuyResult(received=False, balance=0)

    exec_result = ExecuteResult(
        sent=True,
        mode="live",
        signature="sig123",
        send_result=SendResult(
            signature="sig123",
            confirmed=True,
            sol_spent_lamports=20_000_000,
        ),
    )

    live_mode = CopytradePosition.MODE_LIVE
    cfg = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )

    # Build a mock execution_core whose execute_buy returns our pre-built exec_result
    mock_core = MagicMock()
    mock_core.execute_buy.return_value = exec_result

    before_ct = CopytradePosition.objects.count()
    before_sp = SharedPosition.objects.count()

    position, result = open_live_position(
        record,
        entry_price=0.000001,
        config=cfg,
        execution_core=mock_core,
        serialized_tx_b64="fake",
        sender=sender,
        expected_tokens=1_000_000,
    )

    assert position is None, "Ghost buy must NOT write a CopytradePosition"
    assert CopytradePosition.objects.count() == before_ct, "No CT row must be written"
    assert SharedPosition.objects.count() == before_sp, "No shared Position row must be written"


@pytest.mark.django_db
def test_ghost_received_none_no_position():
    """Inconclusive ghost (received=None) → NO CopytradePosition, NO shared Position."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import open_live_position
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    sender = MagicMock()
    sender.verify_ghost_buy.return_value = GhostBuyResult(received=None, balance=-1)

    exec_result = ExecuteResult(
        sent=True,
        mode="live",
        signature="sig456",
        send_result=SendResult(signature="sig456", confirmed=True, sol_spent_lamports=20_000_000),
    )

    live_mode = CopytradePosition.MODE_LIVE
    cfg = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )
    mock_core = MagicMock()
    mock_core.execute_buy.return_value = exec_result

    position, result = open_live_position(
        record, 0.000001, cfg, mock_core, "fake",
        sender=sender, expected_tokens=1_000_000,
    )

    assert position is None
    assert CopytradePosition.objects.count() == 0
    assert SharedPosition.objects.count() == 0


@pytest.mark.django_db
def test_ghost_received_true_real_fill_booked():
    """Ghost received=True → position written with real_entry_price and STATUS_OPEN."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import open_live_position
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    real_tokens = 2_000_000  # 2M raw token units
    sol_spent_lamports = 20_000_000   # 0.02 SOL
    real_sol_spent = sol_spent_lamports / _LAMPORTS_PER_SOL  # 0.02
    expected_entry_price = real_sol_spent / real_tokens

    sender = MagicMock()
    sender.verify_ghost_buy.return_value = GhostBuyResult(received=True, balance=real_tokens)

    exec_result = ExecuteResult(
        sent=True,
        mode="live",
        signature="sig789",
        send_result=SendResult(
            signature="sig789",
            confirmed=True,
            sol_spent_lamports=sol_spent_lamports,
        ),
    )

    live_mode = CopytradePosition.MODE_LIVE
    cfg = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )
    mock_core = MagicMock()
    mock_core.execute_buy.return_value = exec_result

    position, result = open_live_position(
        record, 0.000001, cfg, mock_core, "fake",
        sender=sender, expected_tokens=1_500_000,
    )

    assert position is not None, "Tokens landed — position must be written"
    assert position.entry_price == pytest.approx(expected_entry_price)
    assert position.sol_in == pytest.approx(real_sol_spent)
    assert position.entry_tokens == pytest.approx(float(real_tokens))

    sp = SharedPosition.objects.get(pk=position.shared_position_id)
    assert sp.status == SharedPosition.STATUS_OPEN, (
        "Shared Position must be STATUS_OPEN (non-PAPER) for a confirmed real send"
    )
    assert sp.entry_price == pytest.approx(expected_entry_price)
    assert sp.size_sol == pytest.approx(real_sol_spent)


def test_landed_fill_math_representative_values():
    """real_entry_price == real_sol_spent / real_tokens_received for representative values."""
    # 0.02 SOL / 10,000,000 tokens = 2e-9 SOL per token
    real_tokens = 10_000_000
    sol_spent_lamports = 20_000_000
    real_sol_spent = sol_spent_lamports / _LAMPORTS_PER_SOL  # 0.02
    real_entry_price = real_sol_spent / real_tokens

    assert real_entry_price == pytest.approx(2e-9)
    assert real_sol_spent == pytest.approx(0.02)

    # Edge: 0.015 SOL / 5,000,000 tokens
    real_tokens2 = 5_000_000
    sol2 = 15_000_000
    sol_spent2 = sol2 / _LAMPORTS_PER_SOL
    price2 = sol_spent2 / real_tokens2
    assert price2 == pytest.approx(3e-9)


@pytest.mark.django_db
def test_reconciled_live_position_status_open():
    """A reconciled live position has shared Position status=OPEN; observe stays PAPER."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import OpenedPositionRecordV2, open_live_position, open_observe_position_v2
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    # --- LIVE reconciled ---
    sender = MagicMock()
    sender.verify_ghost_buy.return_value = GhostBuyResult(received=True, balance=1_000_000)
    exec_result = ExecuteResult(
        sent=True, mode="live", signature="sigABC",
        send_result=SendResult(signature="sigABC", confirmed=True, sol_spent_lamports=20_000_000),
    )
    live_mode = CopytradePosition.MODE_LIVE
    cfg = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )
    mock_core = MagicMock()
    mock_core.execute_buy.return_value = exec_result

    live_pos, _ = open_live_position(
        record, 0.000001, cfg, mock_core, "fake",
        sender=sender, expected_tokens=500_000,
    )
    live_sp = SharedPosition.objects.get(pk=live_pos.shared_position_id)
    assert live_sp.status == SharedPosition.STATUS_OPEN

    # --- OBSERVE (paper) position unchanged ---
    obs_record = OpenedPositionRecordV2(
        cohort_id=_COHORT,
        mint="ObserveMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        trigger_wallet=_WALLET,
        entry_ts=_TS,
    )
    obs_pos = open_observe_position_v2(
        obs_record, 0.000002, usd_size=25.0, sol_usd=150.0,
        strategy_id="scalp",
    )
    obs_sp = SharedPosition.objects.get(pk=obs_pos.shared_position_id)
    assert obs_sp.status == SharedPosition.STATUS_PAPER, (
        "Observe/paper position must remain STATUS_PAPER (unchanged)"
    )


@pytest.mark.django_db
def test_observe_path_regression_no_ghost_code_reached():
    """trading_enabled=False → ghost/reconcile code never reached; behavior unchanged."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import open_live_position
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    # Sender mock that would fail loudly if called
    sender = MagicMock()
    sender.verify_ghost_buy.side_effect = AssertionError("MUST NOT be called on observe path")

    source, clock = MagicMock(), MagicMock()
    cfg_trading = TradingConfig(trading_enabled=False)
    core = ExecutionCore(source, clock, cfg_trading, sender=None)

    live_mode = CopytradePosition.MODE_LIVE
    cfg_copy = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )

    # Should not raise, and position must be written as PAPER (observe gate)
    position, exec_result = open_live_position(
        record, 0.000001, cfg_copy, core, "",
        sender=sender,
    )

    assert exec_result.sent is False
    assert exec_result.mode == "observe"
    # PAPER position IS written (observe/gate fallback path)
    assert position is not None
    sp = SharedPosition.objects.get(pk=position.shared_position_id)
    assert sp.status == SharedPosition.STATUS_PAPER
    # verify_ghost_buy was NEVER called
    sender.verify_ghost_buy.assert_not_called()


@pytest.mark.django_db
def test_missing_sender_live_path_paper_fallback():
    """Live send with no sender → PAPER fallback written (never crashes)."""
    from copytrade.models import CopytradePosition
    from copytrade.position_opener import open_live_position
    from copytrade.schemas import CopyTradeConfig
    from copytrade.trigger_pipeline import OpenedPositionRecord
    from trading.models import Position as SharedPosition

    # Core mock returns sent=True (as if budget gate passed and send happened)
    exec_result = ExecuteResult(
        sent=True, mode="live", signature="sigFALL",
        send_result=SendResult(signature="sigFALL", confirmed=True, sol_spent_lamports=20_000_000),
    )
    mock_core = MagicMock()
    mock_core.execute_buy.return_value = exec_result

    live_mode = CopytradePosition.MODE_LIVE
    cfg = CopyTradeConfig(**{"mode": live_mode, "sol_size_per_trade": 0.02})
    record = OpenedPositionRecord(
        cohort_id=_COHORT, mint=_MINT, trigger_wallet=_WALLET, entry_ts=_TS
    )

    position, result = open_live_position(
        record, 0.000001, cfg, mock_core, "fake",
        sender=None,  # no sender — PAPER fallback
    )

    assert position is not None, "PAPER fallback must still write a position"
    sp = SharedPosition.objects.get(pk=position.shared_position_id)
    # Falls back to PAPER since ghost-buy could not be verified
    assert sp.status == SharedPosition.STATUS_PAPER


# ---------------------------------------------------------------------------
# Helpers for mocking `requests` (not installed in the test Docker image;
# sender.py defers the import so we can inject a fake module via sys.modules)
# ---------------------------------------------------------------------------


@contextmanager
def _mock_requests(post_return=None, post_side_effect=None):
    """Context manager that injects a fake `requests` module into sys.modules.

    sender.py does `import requests` inside each live method (deferred import).
    Since `requests` is not installed in the test Docker image, we inject a
    MagicMock module so the deferred import succeeds and we can control
    the response.
    """
    fake_requests = types.ModuleType("requests")
    fake_post = MagicMock(return_value=post_return, side_effect=post_side_effect)
    fake_requests.post = fake_post

    # Provide a fake exceptions module so `requests.exceptions.ConnectionError` works
    fake_exc = types.ModuleType("requests.exceptions")
    fake_exc.ConnectionError = ConnectionError  # map to stdlib ConnectionError
    fake_requests.exceptions = fake_exc

    # Inject
    prev = sys.modules.get("requests")
    prev_exc = sys.modules.get("requests.exceptions")
    sys.modules["requests"] = fake_requests
    sys.modules["requests.exceptions"] = fake_exc
    try:
        yield fake_requests
    finally:
        if prev is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = prev
        if prev_exc is None:
            sys.modules.pop("requests.exceptions", None)
        else:
            sys.modules["requests.exceptions"] = prev_exc


def _make_resp(body: dict) -> MagicMock:
    """Build a mock HTTP response that returns body from .json()."""
    r = MagicMock()
    r.json.return_value = body
    r.raise_for_status.return_value = None
    r.status_code = 200
    return r


# ---------------------------------------------------------------------------
# Section 3: Sender.simulate() unit tests (no network — mock requests module)
# ---------------------------------------------------------------------------


def _make_sender_with_rpc() -> object:
    """Create a Sender with a dummy rpc_url (no live network)."""
    from trading.sender import Sender, SenderConfig
    cfg = SenderConfig(rpc_url="http://mock-rpc.invalid")
    return Sender(cfg)


def test_simulate_ok_true_when_no_err():
    """simulate() with result.value.err=None → SimulateResult(ok=True)."""
    sender = _make_sender_with_rpc()
    resp = _make_resp({
        "jsonrpc": "2.0", "id": 1,
        "result": {"value": {"err": None, "logs": []}},
    })
    with _mock_requests(post_return=resp):
        result = sender.simulate("tx_b64_here")

    assert result.ok is True
    assert result.err is None


def test_simulate_ok_false_when_err_present():
    """simulate() with result.value.err != None → SimulateResult(ok=False) with decoded err."""
    sender = _make_sender_with_rpc()
    err_obj = {"InstructionError": [0, {"Custom": 6002}]}
    resp = _make_resp({
        "jsonrpc": "2.0", "id": 1,
        "result": {"value": {"err": err_obj, "logs": []}},
    })
    with _mock_requests(post_return=resp):
        result = sender.simulate("tx_b64_here")

    assert result.ok is False
    assert result.err is not None
    assert result.err["anchor_name"] == "TooMuchSolRequired"
    assert result.raw_err == err_obj


def test_simulate_inconclusive_on_network_error():
    """simulate() when the network call raises → SimulateResult(ok=None)."""
    sender = _make_sender_with_rpc()
    with _mock_requests(post_side_effect=ConnectionError("timeout")):
        result = sender.simulate("tx_b64")

    assert result.ok is None


def test_simulate_inconclusive_on_malformed_response():
    """simulate() with no result.value → SimulateResult(ok=None)."""
    sender = _make_sender_with_rpc()
    resp = _make_resp({"jsonrpc": "2.0", "id": 1, "result": {}})
    with _mock_requests(post_return=resp):
        result = sender.simulate("tx_b64")

    assert result.ok is None


def test_simulate_inconclusive_on_rpc_error_field():
    """simulate() when response has 'error' field → SimulateResult(ok=None)."""
    sender = _make_sender_with_rpc()
    resp = _make_resp({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    })
    with _mock_requests(post_return=resp):
        result = sender.simulate("tx_b64")

    assert result.ok is None


def test_simulate_result_fields():
    """SimulateResult exposes ok, err, raw_err fields."""
    r = SimulateResult(ok=True)
    assert r.ok is True
    assert r.err is None
    assert r.raw_err is None

    r2 = SimulateResult(ok=False, err={"anchor_name": "TooMuchSolRequired"}, raw_err={"x": 1})
    assert r2.ok is False
    assert r2.err["anchor_name"] == "TooMuchSolRequired"
    assert r2.raw_err == {"x": 1}

    r3 = SimulateResult(ok=None)
    assert r3.ok is None


# ---------------------------------------------------------------------------
# Section 4: SendResult balance fields
# ---------------------------------------------------------------------------


def test_send_result_balance_fields_present():
    """SendResult carries pre/post_balance_lamports and sol_spent_lamports."""
    sr = SendResult(
        signature="abc",
        confirmed=True,
        pre_balance_lamports=1_000_000_000,
        post_balance_lamports=980_000_000,
        sol_spent_lamports=20_000_000,
    )
    assert sr.pre_balance_lamports == 1_000_000_000
    assert sr.post_balance_lamports == 980_000_000
    assert sr.sol_spent_lamports == 20_000_000


def test_send_result_balance_fields_default_none():
    """SendResult balance fields default to None when not provided."""
    sr = SendResult(signature="abc", confirmed=True)
    assert sr.pre_balance_lamports is None
    assert sr.post_balance_lamports is None
    assert sr.sol_spent_lamports is None


def test_confirm_parses_pre_post_balances():
    """_confirm must parse preBalances[0] and postBalances[0] into sol_spent_lamports."""
    from trading.sender import Sender, SenderConfig

    cfg = SenderConfig(rpc_url="http://mock-rpc.invalid", confirm_retries=1)
    sndr = Sender(cfg)

    tx_response = {
        "jsonrpc": "2.0", "id": 1,
        "result": {
            "meta": {
                "err": None,
                "preBalances": [1_000_000_000, 500_000_000],
                "postBalances": [980_000_000, 500_000_000],
            },
            "transaction": {},
        },
    }
    resp = _make_resp(tx_response)
    with _mock_requests(post_return=resp):
        result = sndr._confirm("fakesig")

    assert result.pre_balance_lamports == 1_000_000_000
    assert result.post_balance_lamports == 980_000_000
    assert result.sol_spent_lamports == 20_000_000


def test_confirm_handles_missing_balances():
    """_confirm must not raise when preBalances/postBalances are absent."""
    from trading.sender import Sender, SenderConfig

    cfg = SenderConfig(rpc_url="http://mock-rpc.invalid", confirm_retries=1)
    sndr = Sender(cfg)

    tx_response = {
        "jsonrpc": "2.0", "id": 1,
        "result": {
            "meta": {"err": None},
            "transaction": {},
        },
    }
    resp = _make_resp(tx_response)
    with _mock_requests(post_return=resp):
        result = sndr._confirm("fakesig")

    assert result.pre_balance_lamports is None
    assert result.post_balance_lamports is None
    assert result.sol_spent_lamports is None
