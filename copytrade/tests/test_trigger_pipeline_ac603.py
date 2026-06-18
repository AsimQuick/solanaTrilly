# ---
# module: copytrade.tests.test_trigger_pipeline_ac603
# sprint: sprint-12
# story: US-60 AC-60.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, ast, asyncio, core.clock, core.replay_source, copytrade.trigger_pipeline, copytrade.schemas
# ---
"""AC-60.3: Deterministic synthetic wallet-tx replay gate for the trigger pipeline.

All tests are deterministic and offline (zero firehose, no @pytest.mark.django_db).
The DataSource seam (ReplaySource) and VirtualClock are the only time/data sources.

Tests verify:

  Static-analysis guards (AST-level):
    1. trigger_pipeline.py has NO concrete Helius/Birdeye/LiveSource/ReplaySource import
       on the production path (Principle #7 — the caller injects the concrete source).
    2. trigger_pipeline.py has NO datetime.now() / time.time() call (US-2 guard).

  Run-twice-identical determinism (core AC-60.3 requirement):
    3. Replaying the same synthetic wallet-tx stream twice yields IDENTICAL
       TriggerResult sequences (predicate_passed, decision.action, decision.reason).
    4. Replaying the same stream twice yields IDENTICAL OpenedPositionRecord sequences
       (cohort_id, mint, trigger_wallet, entry_ts).

  mirror_wallet_sells=False invariant:
    5. Sell events from watched wallets are NEVER passed by the trigger predicate
       (predicate_passed=False for all sells — mirror_wallet_sells stays False).
    6. Transfer events from watched wallets are NEVER passed by the predicate.
    7. CopyTradeConfig.mirror_wallet_sells defaults to False.
    8. CopyTradeConfig construction with mirror_wallet_sells=True raises PydanticValidationError.

  Trigger correctness over synthetic stream:
    9.  Valid pre-graduation pump.fun buy → action == "open" and OpenedPositionRecord populated.
    10. Non-pump.fun token buy → predicate_passed=False (condition 2 fails).
    11. Post-graduation buy (raw graduated=True) → predicate_passed=False (condition 3 fails).
    12. Duplicate buy (same wallet + same token, copy_first_buy_only=True) → action == "skip_first_buy".
    13. Empty event stream → empty result list.
    14. OpenedPositionRecord carries the correct cohort_id injected by the caller.
"""
import ast
import asyncio
import pathlib
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from copytrade.schemas import CopyTradeConfig

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PIPELINE_SRC = pathlib.Path(__file__).resolve().parents[1] / "trigger_pipeline.py"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
COHORT_ID = "cohort-ac603-replay-test"

WALLET_A = "Wa1letAC603AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
WALLET_B = "Wa1letAC603BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
ALL_WALLETS = [WALLET_A, WALLET_B]

# Mints that end in "pump" → pass is_pumpfun_token (suffix check)
MINT_PUMP1 = "MintAC603Pump1XXXXXXXXXXXXXXXXXXXXXXXXXXpump"
MINT_PUMP2 = "MintAC603Pump2YYYYYYYYYYYYYYYYYYYYYYYYYYpump"
# Mint that does NOT end in "pump" → fails is_pumpfun_token
MINT_NONPUMP = "MintAC603NonPumpSOLXXXXXXXXXXXXXXXXXXXXXXsolx"


def _default_config(**overrides) -> CopyTradeConfig:
    """Return a valid CopyTradeConfig with AC-60.3-safe defaults."""
    return CopyTradeConfig(
        mirror_wallet_sells=False,
        copy_first_buy_only=True,
        dedupe_token_across_wallets=True,
        max_concurrent_positions=20,
        **overrides,
    )


# Deterministic fixture stream used for run-twice-identical tests.
# Schema-faithful to the WalletTxEvent / WalletSubscriptionConsumer._normalize contract.
#
# Expected outcomes with _default_config():
#   Event 1: WALLET_A buys MINT_PUMP1 (pre-grad, pump suffix)  → predicate=True, action="open"
#   Event 2: WALLET_A sells MINT_PUMP1                         → predicate=False (not a buy)
#   Event 3: WALLET_B buys MINT_NONPUMP                        → predicate=False (not pumpfun)
#   Event 4: WALLET_B buys MINT_PUMP2 (pre-grad, pump suffix)  → predicate=True, action="open"
#   Event 5: WALLET_A buys MINT_PUMP1 again                    → predicate=True, action="skip_first_buy"
#   Event 6: WALLET_B buys MINT_PUMP1 (graduated=True)         → predicate=False (post-grad)
FIXTURE_STREAM: list[dict[str, Any]] = [
    {
        "wallet": WALLET_A,
        "mint": MINT_PUMP1,
        "signature": "sig_603_1",
        "type": "buy",
        "sol_amount": 0.25,
        "token_amount": 1_000_000.0,
    },
    {
        "wallet": WALLET_A,
        "mint": MINT_PUMP1,
        "signature": "sig_603_2",
        "type": "sell",
        "sol_amount": 0.30,
        "token_amount": 1_000_000.0,
    },
    {
        "wallet": WALLET_B,
        "mint": MINT_NONPUMP,
        "signature": "sig_603_3",
        "type": "buy",
        "sol_amount": 0.10,
        "token_amount": 200_000.0,
    },
    {
        "wallet": WALLET_B,
        "mint": MINT_PUMP2,
        "signature": "sig_603_4",
        "type": "buy",
        "sol_amount": 0.25,
        "token_amount": 2_000_000.0,
    },
    {
        "wallet": WALLET_A,
        "mint": MINT_PUMP1,
        "signature": "sig_603_5",
        "type": "buy",
        "sol_amount": 0.25,
        "token_amount": 1_000_000.0,
    },
    {
        "wallet": WALLET_B,
        "mint": MINT_PUMP1,
        "signature": "sig_603_6",
        "type": "buy",
        "sol_amount": 0.25,
        "token_amount": 1_000_000.0,
        "graduated": True,
    },
]


# ---------------------------------------------------------------------------
# Helper — synchronous runner for async run_trigger_pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    events_data: list[dict[str, Any]],
    wallet_addresses: list[str] = ALL_WALLETS,
    cohort_id: str = COHORT_ID,
    channel_name: str = "test_wallet_channel",
    config: CopyTradeConfig | None = None,
):
    """Run run_trigger_pipeline synchronously and return the TriggerResult list."""
    from copytrade.trigger_pipeline import run_trigger_pipeline
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource

    if config is None:
        config = _default_config()

    async def _inner():
        source = ReplaySource(event_log=events_data)
        clock = VirtualClock(initial_time=T0)
        return await run_trigger_pipeline(
            cohort_id=cohort_id,
            source=source,
            clock=clock,
            wallet_addresses=wallet_addresses,
            channel_name=channel_name,
            config=config,
        )

    return asyncio.run(_inner())


# ===========================================================================
# 1. Static-analysis: no concrete source import
# ===========================================================================

def test_trigger_pipeline_no_concrete_source_import():
    """AST guard: trigger_pipeline.py must NOT import any concrete source.

    The caller injects the DataSource (Principle #7).  The pipeline module
    depends only on the abstract DataSource interface.
    """
    source_text = PIPELINE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    FORBIDDEN_NAMES = {
        "HeliusWalletSource",
        "BirdeyeSwapSource",
        "HeliusBirthTapeSource",
        "LiveSource",
        "ReplaySource",
    }
    FORBIDDEN_MODULES = {"core.live_source", "core.replay_source"}

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in FORBIDDEN_MODULES:
                violations.append(f"line {node.lineno}: from {node.module} import ...")
            for alias in node.names or []:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES:
                    violations.append(f"line {node.lineno}: import {alias.name}")

    assert not violations, (
        f"trigger_pipeline.py has forbidden concrete source imports: {violations}"
    )


# ===========================================================================
# 2. Static-analysis: no datetime.now() / time.time()
# ===========================================================================

def test_trigger_pipeline_no_direct_time_call():
    """AST guard: trigger_pipeline.py must NOT call datetime.now() or time.time().

    All timestamps must come from the injected Clock (US-2 / Principle #7 guard).
    """
    source_text = PIPELINE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in ("now", "utcnow")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
            ):
                violations.append(f"line {node.lineno}: datetime.{node.func.attr}()")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "time"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
            ):
                violations.append(f"line {node.lineno}: time.time()")

    assert not violations, (
        f"trigger_pipeline.py has forbidden direct time calls: {violations}"
    )


# ===========================================================================
# 3. Run-twice-identical: same stream → identical TriggerResult sequences
# ===========================================================================

def test_run_twice_identical_decisions():
    """AC-60.3 core gate: replaying the same stream twice yields identical decisions.

    Both runs use the same FIXTURE_STREAM, the same VirtualClock(T0), and a fresh
    MultiplicityState (created inside run_trigger_pipeline).  The resulting
    TriggerResult sequences must be byte-identical in every field that determines
    trigger behaviour.

    Zero firehose: both runs use ReplaySource.
    """
    results1 = _run_pipeline(FIXTURE_STREAM)
    results2 = _run_pipeline(FIXTURE_STREAM)

    assert len(results1) == len(results2), (
        f"Run lengths differ: {len(results1)} vs {len(results2)}"
    )
    for i, (r1, r2) in enumerate(zip(results1, results2)):
        assert r1.predicate_passed == r2.predicate_passed, (
            f"event {i}: predicate_passed mismatch ({r1.predicate_passed} vs {r2.predicate_passed})"
        )
        if r1.decision is None:
            assert r2.decision is None, f"event {i}: decision None vs non-None"
        else:
            assert r2.decision is not None, f"event {i}: decision non-None vs None"
            assert r1.decision.action == r2.decision.action, (
                f"event {i}: action mismatch ({r1.decision.action!r} vs {r2.decision.action!r})"
            )
            assert r1.decision.reason == r2.decision.reason, (
                f"event {i}: reason mismatch"
            )


# ===========================================================================
# 4. Run-twice-identical: same stream → identical OpenedPositionRecord sequences
# ===========================================================================

def test_run_twice_identical_opened_positions():
    """AC-60.3 core gate: replaying the same stream twice yields identical opened-position rows.

    Extracts only the OpenedPositionRecords (decision.action == "open") from each
    run and compares them field-by-field.

    Zero firehose: both runs use ReplaySource.
    """
    results1 = _run_pipeline(FIXTURE_STREAM)
    results2 = _run_pipeline(FIXTURE_STREAM)

    opened1 = [r.opened_position for r in results1 if r.opened_position is not None]
    opened2 = [r.opened_position for r in results2 if r.opened_position is not None]

    assert len(opened1) == len(opened2), (
        f"Opened-position count differs: {len(opened1)} vs {len(opened2)}"
    )
    for i, (p1, p2) in enumerate(zip(opened1, opened2)):
        assert p1.cohort_id == p2.cohort_id, f"position {i}: cohort_id mismatch"
        assert p1.mint == p2.mint, f"position {i}: mint mismatch"
        assert p1.trigger_wallet == p2.trigger_wallet, f"position {i}: trigger_wallet mismatch"
        assert p1.entry_ts == p2.entry_ts, f"position {i}: entry_ts mismatch"


# ===========================================================================
# 5. mirror_wallet_sells=False: sells are never passed by the predicate
# ===========================================================================

def test_sell_events_never_trigger():
    """Sell events from watched wallets must produce predicate_passed=False.

    mirror_wallet_sells stays False: we NEVER copy the wallet's sells, only the
    entry signal.  should_copy_buy condition 1 (is_buyer: tx_type == "buy") rejects
    all sells before multiplicity controls are ever evaluated.

    Zero firehose: uses ReplaySource.
    """
    sell_stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_sell_1",
            "type": "sell",
            "sol_amount": 0.50,
            "token_amount": 2_000_000.0,
        },
        {
            "wallet": WALLET_B,
            "mint": MINT_PUMP2,
            "signature": "sig_sell_2",
            "type": "sell",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
        },
    ]
    results = _run_pipeline(sell_stream)

    assert len(results) == 2
    for r in results:
        assert r.predicate_passed is False, (
            f"Sell event produced predicate_passed=True: {r.event.tx_signature!r}"
        )
        assert r.decision is None, "Sell event must not reach multiplicity controls"
        assert r.opened_position is None, "Sell event must never open a position"


# ===========================================================================
# 6. Transfers are never passed by the predicate
# ===========================================================================

def test_transfer_events_never_trigger():
    """Transfer events from watched wallets must produce predicate_passed=False.

    Transfers are not buys; they must never trigger a copy position.

    Zero firehose: uses ReplaySource.
    """
    transfer_stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_transfer_1",
            "type": "transfer",
            "sol_amount": 0.0,
            "token_amount": 500_000.0,
        },
    ]
    results = _run_pipeline(transfer_stream)

    assert len(results) == 1
    assert results[0].predicate_passed is False
    assert results[0].opened_position is None


# ===========================================================================
# 7. mirror_wallet_sells defaults to False in CopyTradeConfig
# ===========================================================================

def test_mirror_wallet_sells_defaults_to_false():
    """CopyTradeConfig.mirror_wallet_sells must default to False (SPEC §3 invariant)."""
    config = CopyTradeConfig(
        sol_size_per_trade=0.25,
        take_profit_pct=200.0,
        stop_loss_pct=40.0,
        max_hold_seconds=1800,
        max_concurrent_positions=20,
    )
    assert config.mirror_wallet_sells is False


# ===========================================================================
# 8. CopyTradeConfig rejects mirror_wallet_sells=True
# ===========================================================================

def test_config_rejects_mirror_wallet_sells_true():
    """Constructing CopyTradeConfig with mirror_wallet_sells=True must raise PydanticValidationError."""
    with pytest.raises(PydanticValidationError, match="mirror_wallet_sells"):
        CopyTradeConfig(
            sol_size_per_trade=0.25,
            take_profit_pct=200.0,
            stop_loss_pct=40.0,
            max_hold_seconds=1800,
            max_concurrent_positions=20,
            mirror_wallet_sells=True,
        )


# ===========================================================================
# 9. Valid pre-graduation pump.fun buy → action == "open"
# ===========================================================================

def test_valid_pumpfun_buy_opens_position():
    """A valid pre-graduation pump.fun buy by a watched wallet must open a position.

    Verifies the happy path end-to-end: DataSource → consumer → predicate →
    multiplicity → OpenedPositionRecord.

    Zero firehose: uses ReplaySource.
    """
    stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_valid_buy",
            "type": "buy",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
        },
    ]
    results = _run_pipeline(stream)

    assert len(results) == 1
    r = results[0]
    assert r.predicate_passed is True
    assert r.decision is not None
    assert r.decision.action == "open"
    assert r.opened_position is not None
    assert r.opened_position.mint == MINT_PUMP1
    assert r.opened_position.trigger_wallet == WALLET_A
    assert r.opened_position.entry_ts == T0  # VirtualClock(T0) never advanced → all events get T0


# ===========================================================================
# 10. Non-pump.fun token buy → predicate_passed=False
# ===========================================================================

def test_non_pumpfun_buy_is_rejected():
    """A buy on a non-pump.fun token (mint does not end in 'pump', no bonding-curve program)
    must be rejected by the predicate (condition 2 fails).

    Zero firehose: uses ReplaySource.
    """
    stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_NONPUMP,
            "signature": "sig_nonpump",
            "type": "buy",
            "sol_amount": 0.10,
            "token_amount": 500_000.0,
        },
    ]
    results = _run_pipeline(stream)

    assert len(results) == 1
    assert results[0].predicate_passed is False
    assert results[0].opened_position is None


# ===========================================================================
# 11. Post-graduation buy → predicate_passed=False
# ===========================================================================

def test_graduated_buy_is_rejected():
    """A buy with graduated=True in the raw event must be rejected (condition 3 fails).

    This is the SPEC §3 edge: 'our edge is curve entry' — a buy on an already-
    graduated token is the wrong signal.

    Zero firehose: uses ReplaySource.
    """
    stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_graduated",
            "type": "buy",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
            "graduated": True,
        },
    ]
    results = _run_pipeline(stream)

    assert len(results) == 1
    assert results[0].predicate_passed is False
    assert results[0].opened_position is None


# ===========================================================================
# 12. Duplicate buy (copy_first_buy_only) → action == "skip_first_buy"
# ===========================================================================

def test_duplicate_buy_same_wallet_is_skipped():
    """Second buy of the same token by the same wallet → action == "skip_first_buy".

    copy_first_buy_only=True (default config): the same (wallet, mint) pair may
    only trigger once per cohort session.

    Zero firehose: uses ReplaySource.
    """
    stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_first",
            "type": "buy",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
        },
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_second",
            "type": "buy",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
        },
    ]
    results = _run_pipeline(stream)

    assert len(results) == 2
    assert results[0].decision.action == "open"
    assert results[1].decision.action == "skip_first_buy"
    assert results[1].opened_position is None


# ===========================================================================
# 13. Empty event stream → empty result list
# ===========================================================================

def test_empty_stream_returns_empty_results():
    """An empty DataSource stream must return an empty TriggerResult list.

    Zero firehose: uses ReplaySource(event_log=[]).
    """
    results = _run_pipeline([])
    assert results == []


# ===========================================================================
# 14. OpenedPositionRecord carries the correct injected cohort_id
# ===========================================================================

def test_opened_position_carries_correct_cohort_id():
    """OpenedPositionRecord.cohort_id must match the cohort_id injected by the caller.

    The pipeline stamps every opened record with the caller's cohort_id — it must
    never substitute or hardcode a different value.

    Zero firehose: uses ReplaySource.
    """
    custom_cohort = "whale-cohort-2026-06-test"
    stream: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": MINT_PUMP1,
            "signature": "sig_cohort_check",
            "type": "buy",
            "sol_amount": 0.25,
            "token_amount": 1_000_000.0,
        },
    ]
    results = _run_pipeline(stream, cohort_id=custom_cohort)

    assert len(results) == 1
    assert results[0].opened_position is not None
    assert results[0].opened_position.cohort_id == custom_cohort
