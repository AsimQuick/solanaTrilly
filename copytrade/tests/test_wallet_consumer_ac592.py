# ---
# module: copytrade.tests.test_wallet_consumer_ac592
# sprint: sprint-12
# story: US-59 AC-59.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, ast, asyncio, core.clock, core.replay_source, copytrade.wallet_consumer
# ---
"""AC-59.2: Wallet-subscription consumer tests.

All tests are deterministic and offline (zero firehose).  They verify:

  Static-analysis guards (AST-level):
    1. wallet_consumer.py has NO concrete Helius/Birdeye/LiveSource import.
    2. wallet_consumer.py has NO datetime.now() / time.time() call.
    3. wallet_consumer.py has NO synchronous .objects. ORM access (K4 guard).

  Normalisation / filtering behaviour:
    4. Emits WalletTxEvent for each watched-wallet tx with a valid mint.
    5. Events from un-watched wallets are dropped (not emitted).
    6. Events with an empty mint are dropped even if the wallet is watched.
    7. All raw events (including filtered) are recorded in _processed.

  Deterministic replay (sprint-11 K4 / AC-59.2 run-twice-identical):
    8. Same fixture stream + same VirtualClock T0 produces byte-identical
       WalletTxEvents on a second run.

  Config-driven channel name (Principle #1):
    9. channel_name is stored exactly as injected — the consumer never
       substitutes or hardcodes a name.
"""
import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONSUMER_SRC = pathlib.Path(__file__).resolve().parents[1] / "wallet_consumer.py"

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
DELTA = timedelta(seconds=1)

WALLET_A = "Wa1letAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
WALLET_B = "Wa1letBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
OTHER_WALLET = "Wa1letXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

FIXTURE_EVENTS: list[dict[str, Any]] = [
    # valid buy from WALLET_A — must be emitted
    {
        "wallet": WALLET_A,
        "mint": MINT_A,
        "signature": "sig_a1",
        "type": "buy",
        "sol_amount": 0.5,
        "token_amount": 1_000_000.0,
    },
    # valid sell from WALLET_B — must be emitted
    {
        "wallet": WALLET_B,
        "mint": MINT_B,
        "signature": "sig_b1",
        "type": "sell",
        "sol_amount": 0.3,
        "token_amount": 500_000.0,
    },
    # from un-watched wallet — must be filtered out
    {
        "wallet": OTHER_WALLET,
        "mint": MINT_A,
        "signature": "sig_x1",
        "type": "buy",
        "sol_amount": 0.1,
        "token_amount": 100_000.0,
    },
    # empty mint — must be filtered out even though wallet is watched
    {
        "wallet": WALLET_A,
        "mint": "",
        "signature": "sig_a2",
        "type": "buy",
        "sol_amount": 0.0,
        "token_amount": 0.0,
    },
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_consumer(
    events_data: list[dict[str, Any]],
    wallet_addresses: list[str],
    channel: str = "copytrade_wallet_updates",
) -> tuple[list, Any]:
    """Run the consumer over events_data; return (emitted_events, consumer)."""
    from copytrade.wallet_consumer import WalletSubscriptionConsumer
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource

    holder: list = []

    async def _inner():
        source = ReplaySource(event_log=events_data)
        clock = VirtualClock(initial_time=T0)
        consumer = WalletSubscriptionConsumer(
            source=source,
            clock=clock,
            wallet_addresses=wallet_addresses,
            channel_name=channel,
        )
        holder.append(consumer)
        results = []
        async for event in consumer.run():
            results.append(event)
            clock.advance(DELTA)
        return results

    events = asyncio.run(_inner())
    return events, holder[0]


# ---------------------------------------------------------------------------
# 1. Static-analysis: no concrete source import
# ---------------------------------------------------------------------------

def test_wallet_consumer_no_concrete_source_import():
    """AST guard: wallet_consumer.py must NOT import any concrete Helius/Birdeye/Live source.

    Consumers depend ONLY on the DataSource abstract interface (Principle #7).
    The caller (composition root) injects the concrete source.
    """
    source_text = CONSUMER_SRC.read_text(encoding="utf-8")
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
        f"wallet_consumer.py has forbidden concrete source imports: {violations}"
    )


# ---------------------------------------------------------------------------
# 2. Static-analysis: no datetime.now() / time.time()
# ---------------------------------------------------------------------------

def test_wallet_consumer_no_direct_time_call():
    """AST guard: wallet_consumer.py must NOT call datetime.now() or time.time().

    All timestamps must come from the injected Clock (Principle #7 / US-2 guard).
    """
    source_text = CONSUMER_SRC.read_text(encoding="utf-8")
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
        f"wallet_consumer.py has forbidden direct time calls: {violations}"
    )


# ---------------------------------------------------------------------------
# 3. K4 async-safety: no synchronous ORM access
# ---------------------------------------------------------------------------

def test_wallet_consumer_k4_no_sync_orm_call():
    """K4 async-safety: wallet_consumer.py must make NO synchronous ORM call.

    Direct .objects. access inside an async consumer causes
    django.db.utils.SynchronousOnlyOperation (the US-48 bug class).
    The consumer receives wallet_addresses from the caller (which used
    sync_to_async to read them) — it must not re-query the DB itself.
    """
    source_text = CONSUMER_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "objects":
            violations.append(
                f"line {node.lineno}: .objects access (sync ORM in async context)"
            )

    assert not violations, (
        f"wallet_consumer.py has synchronous ORM calls: {violations}"
    )


# ---------------------------------------------------------------------------
# 4. Normalization: emits events for watched wallets
# ---------------------------------------------------------------------------

def test_wallet_consumer_emits_watched_wallet_events():
    """Emits WalletTxEvent for each tx from a watched wallet with a non-empty mint."""
    events, _ = _run_consumer(FIXTURE_EVENTS, wallet_addresses=[WALLET_A, WALLET_B])

    # 4 fixture events; 2 pass the filter (sig_a1 + sig_b1)
    assert len(events) == 2

    buy = events[0]
    assert buy.wallet == WALLET_A
    assert buy.mint == MINT_A
    assert buy.tx_signature == "sig_a1"
    assert buy.tx_type == "buy"
    assert buy.sol_amount == 0.5
    assert buy.token_amount == 1_000_000.0
    assert buy.timestamp == T0  # first event gets the initial VirtualClock value

    sell = events[1]
    assert sell.wallet == WALLET_B
    assert sell.mint == MINT_B
    assert sell.tx_type == "sell"
    assert sell.timestamp == T0 + DELTA  # clock was advanced once after first event


# ---------------------------------------------------------------------------
# 5. Filtering: un-watched wallet is dropped
# ---------------------------------------------------------------------------

def test_wallet_consumer_filters_unwatched_wallet():
    """Events from wallets not in wallet_addresses are never emitted."""
    events, _ = _run_consumer(FIXTURE_EVENTS, wallet_addresses=[WALLET_A])

    # Only WALLET_A's buy with valid mint passes; WALLET_B and OTHER_WALLET dropped
    assert len(events) == 1
    assert events[0].wallet == WALLET_A
    assert events[0].tx_signature == "sig_a1"


# ---------------------------------------------------------------------------
# 6. Filtering: empty-mint event is dropped
# ---------------------------------------------------------------------------

def test_wallet_consumer_filters_empty_mint():
    """Events with an empty mint are dropped even if the wallet is watched."""
    only_empty_mint: list[dict[str, Any]] = [
        {
            "wallet": WALLET_A,
            "mint": "",
            "signature": "sig_nomint",
            "type": "buy",
            "sol_amount": 0.1,
            "token_amount": 1_000.0,
        },
    ]
    events, _ = _run_consumer(only_empty_mint, wallet_addresses=[WALLET_A])
    assert events == []


# ---------------------------------------------------------------------------
# 7. _processed includes ALL raw events (including filtered)
# ---------------------------------------------------------------------------

def test_wallet_consumer_processed_includes_all_raw():
    """_processed records every (raw_event, ts) pair seen, including filtered ones."""
    _, consumer = _run_consumer(FIXTURE_EVENTS, wallet_addresses=[WALLET_A, WALLET_B])
    assert len(consumer._processed) == len(FIXTURE_EVENTS)


# ---------------------------------------------------------------------------
# 8. Deterministic replay — run-twice-identical
# ---------------------------------------------------------------------------

def test_wallet_consumer_replay_identical_twice():
    """Same fixture + same VirtualClock T0 → byte-identical WalletTxEvents twice.

    This is the sprint-12 AC-59.2 run-twice-identical determinism gate.
    Zero firehose: both runs use ReplaySource (offline-only).
    """
    wallets = [WALLET_A, WALLET_B]
    events1, _ = _run_consumer(FIXTURE_EVENTS, wallet_addresses=wallets)
    events2, _ = _run_consumer(FIXTURE_EVENTS, wallet_addresses=wallets)

    assert len(events1) == len(events2), (
        f"Run lengths differ: {len(events1)} vs {len(events2)}"
    )
    for i, (e1, e2) in enumerate(zip(events1, events2)):
        assert e1.wallet == e2.wallet, f"event {i}: wallet mismatch"
        assert e1.mint == e2.mint, f"event {i}: mint mismatch"
        assert e1.tx_signature == e2.tx_signature, f"event {i}: signature mismatch"
        assert e1.tx_type == e2.tx_type, f"event {i}: tx_type mismatch"
        assert e1.sol_amount == e2.sol_amount, f"event {i}: sol_amount mismatch"
        assert e1.token_amount == e2.token_amount, f"event {i}: token_amount mismatch"
        assert e1.timestamp == e2.timestamp, f"event {i}: timestamp mismatch"


# ---------------------------------------------------------------------------
# 9. Config-driven channel name (Principle #1)
# ---------------------------------------------------------------------------

def test_wallet_consumer_channel_name_config_driven():
    """channel_name is stored exactly as injected — never substituted (Principle #1)."""
    from copytrade.wallet_consumer import WalletSubscriptionConsumer
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource

    custom_name = "my_custom_wallet_channel_test_xyz_42"
    consumer = WalletSubscriptionConsumer(
        source=ReplaySource(event_log=[]),
        clock=VirtualClock(initial_time=T0),
        wallet_addresses=[WALLET_A],
        channel_name=custom_name,
    )
    assert consumer._channel_name == custom_name
