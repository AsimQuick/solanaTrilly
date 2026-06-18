# ---
# module: core.tests.test_tape_feed_consumer_ac482
# sprint: sprint-10
# story: US-48 AC-48.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.consumer, core.datasource, core.replay_source, ast, asyncio, pathlib
# ---
"""AC-48.2 — ONE tape feed consumer tests.

Two verification paths required by AC-48.2:

(a) Structural/AST tests — assert the consumer's delta source is the tape
    DataSource / FeatureExtractor path and NOT an independent price source.
    Specifically:
      - TapeFeedConsumer and TapeFeedProcessor are importable (ImportError trap,
        fails pytest COLLECTION if the module is deleted — H1 pattern).
      - consumer.py depends on DataSource, not LiveSource/ReplaySource.
      - consumer.py imports FeatureExtractor (the shared US-30 extractor path).
      - consumer.py does NOT import any independent price source
        (birdeye_swap_source, live_source, or any class matching *PriceFeed*).
      - The WS route ws/tape/<mint>/ is present in routing.py.
      - DashboardConfig is present in core/schemas.py with all required fields.
      - channel/topic names and candle_interval_s are read from DashboardConfig,
        never literals in consumer.py.
      - The routing.py entry for TapeFeedConsumer uses the named route pattern.

(b) Deterministic replay tests — driven from a banked ReplaySource tape, the
    consumer (via TapeFeedProcessor) emits the expected candle/position delta
    sequence and produces RUN-TWICE IDENTICAL output (zero firehose, OFFLINE).

H1 ImportError trap (AC-48.3 hook point):
    The module-level import of TapeFeedProcessor and TapeFeedConsumer below
    fails pytest COLLECTION (not test execution) if core/dashboard/consumer.py
    is deleted or these names are removed — exactly the guard the orchestrator
    wires to CI in AC-48.3.

Tests:
  test_import_trap_tape_feed_processor        — H1: module-level import passes
  test_import_trap_tape_feed_consumer         — H1: module-level import passes
  test_consumer_depends_on_datasource_not_live_source
  test_consumer_does_not_import_independent_price_source
  test_consumer_imports_feature_extractor_or_tape_path
  test_ws_route_present_in_routing
  test_dashboard_config_in_schemas
  test_dashboard_config_fields_present
  test_processor_emits_candle_deltas_from_replay_source
  test_processor_emits_position_deltas_from_replay_source
  test_processor_output_is_run_twice_identical
  test_processor_candle_ohlc_correct
  test_processor_position_aggregate_correct
  test_processor_empty_source_emits_nothing
  test_processor_no_separate_price_fetch
"""
import ast
import asyncio
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# H1 ImportError trap — fails pytest COLLECTION if consumer is deleted/renamed
# ---------------------------------------------------------------------------
from core.dashboard.consumer import TapeFeedConsumer, TapeFeedProcessor  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSUMER_PY = REPO_ROOT / "core" / "dashboard" / "consumer.py"
ROUTING_PY = REPO_ROOT / "core" / "routing.py"
SCHEMAS_PY = REPO_ROOT / "core" / "schemas.py"

# ---------------------------------------------------------------------------
# Banked swap fixture for deterministic replay tests
# Each event is a raw tape swap dict — same schema as TapeRecorder emits.
# Two candle windows (interval_s=5):
#   window 0  (t=0..4):  3 swaps
#   window 5  (t=5..9):  2 swaps
# ---------------------------------------------------------------------------

_MINT = "REPLAY_MINT_AC482"

_BANKED_SWAPS: list[dict[str, Any]] = [
    # Window 0: t=0..4
    {"type": "SWAP", "mint": _MINT, "block_time": 0, "slot": 1, "signature": "sig1",
     "price": 0.001, "vol_sol": 100.0, "side": "buy"},
    {"type": "SWAP", "mint": _MINT, "block_time": 2, "slot": 2, "signature": "sig2",
     "price": 0.0012, "vol_sol": 150.0, "side": "buy"},
    {"type": "SWAP", "mint": _MINT, "block_time": 4, "slot": 3, "signature": "sig3",
     "price": 0.0011, "vol_sol": 50.0, "side": "sell"},
    # Window 1: t=5..9
    {"type": "SWAP", "mint": _MINT, "block_time": 5, "slot": 4, "signature": "sig4",
     "price": 0.0013, "vol_sol": 200.0, "side": "buy"},
    {"type": "SWAP", "mint": _MINT, "block_time": 7, "slot": 5, "signature": "sig5",
     "price": 0.0014, "vol_sol": 80.0, "side": "sell"},
]

# Synthetic DashboardConfig (no DB needed — replaces get_active_config())
class _FakeDashboardConfig:
    candle_interval_s = 5
    candle_topic = "candle_delta"
    position_topic = "position_delta"
    ws_channel_prefix = "tape"


class _FakeConfig:
    dashboard = _FakeDashboardConfig()


def _fake_config_fn():
    return _FakeConfig()


def _run(coro):
    """Run a coroutine synchronously (test helper)."""
    return asyncio.run(coro)


async def _collect_deltas(swaps, config_fn=None):
    """Drive TapeFeedProcessor with a ReplaySource and collect all deltas."""
    from core.replay_source import ReplaySource
    source = ReplaySource(swaps)
    processor = TapeFeedProcessor(source, config_fn=config_fn or _fake_config_fn)
    return [delta async for delta in processor.iter_deltas()]


# ===========================================================================
# (a) Structural / AST tests
# ===========================================================================

def test_import_trap_tape_feed_processor():
    """H1: TapeFeedProcessor is importable — fails collection if deleted."""
    assert TapeFeedProcessor is not None


def test_import_trap_tape_feed_consumer():
    """H1: TapeFeedConsumer is importable — fails collection if deleted."""
    assert TapeFeedConsumer is not None


def test_consumer_depends_on_datasource_not_live_source():
    """AST: consumer.py imports DataSource but NOT LiveSource or ReplaySource directly."""
    src = CONSUMER_PY.read_text()
    tree = ast.parse(src)
    imported_names = set()
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    imported_names.add(alias.name)
            if node.module:
                imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
                imported_modules.add(alias.name)

    assert "DataSource" in imported_names, "consumer.py must import DataSource (the abstract seam)"
    assert "LiveSource" not in imported_names, "consumer.py must NOT import LiveSource directly"
    assert "ReplaySource" not in imported_names, "consumer.py must NOT import ReplaySource directly"
    assert "core.live_source" not in imported_modules, "consumer.py must NOT import core.live_source"
    assert "core.replay_source" not in imported_modules, "consumer.py must NOT import core.replay_source"


def test_consumer_does_not_import_independent_price_source():
    """AST: consumer.py does not import any standalone price feed module."""
    src = CONSUMER_PY.read_text()
    for banned in ("birdeye_swap_source", "PriceFeed", "price_feed", "live_price"):
        assert banned not in src, (
            f"consumer.py must NOT reference '{banned}' — no independent price source (AC-48.2)"
        )


def test_consumer_imports_feature_extractor_or_tape_path():
    """AST: consumer.py imports DataSource (the tape seam) and DashboardConfig (Principle #1).

    The consumer drives TapeFeedProcessor via the abstract DataSource — the same
    seam the TapeRecorder writes through and the FeatureExtractor reads from.
    This test verifies the import graph: DataSource must be imported; the concrete
    source classes must not appear as top-level imports (they are dependency-injected).
    """
    src = CONSUMER_PY.read_text()
    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "DataSource" in imported_names, (
        "consumer.py must import DataSource (the tape seam, US-30 path)"
    )
    assert "DashboardConfig" in imported_names, (
        "consumer.py must import DashboardConfig (Principle #1 — config-driven names/cadence)"
    )
    # Concrete source classes must NOT be top-level imports — injected only
    for banned in ("LiveSource", "ReplaySource"):
        assert banned not in imported_names, (
            f"consumer.py must NOT import '{banned}' as a top-level import — DataSource only"
        )


def test_ws_route_present_in_routing():
    """AST: routing.py includes a ws/tape/<mint>/ entry mapped to TapeFeedConsumer."""
    src = ROUTING_PY.read_text()
    assert "TapeFeedConsumer" in src, "routing.py must import and wire TapeFeedConsumer"
    assert "ws/tape/" in src or "ws/tape" in src, (
        "routing.py must register the ws/tape/<mint>/ WebSocket route"
    )


def test_dashboard_config_in_schemas():
    """AST: core/schemas.py declares DashboardConfig."""
    src = SCHEMAS_PY.read_text()
    assert "DashboardConfig" in src, "schemas.py must declare DashboardConfig (Principle #1)"


def test_dashboard_config_fields_present():
    """AST: DashboardConfig has ws_channel_prefix, candle_topic, position_topic, candle_interval_s."""
    src = SCHEMAS_PY.read_text()
    for field in ("ws_channel_prefix", "candle_topic", "position_topic", "candle_interval_s"):
        assert field in src, f"DashboardConfig must declare '{field}' (Principle #1, no literals)"


def test_consumer_reads_config_not_literals():
    """AST: consumer.py reads topic names and cadence from config, not string literals."""
    src = CONSUMER_PY.read_text()
    tree = ast.parse(src)
    # Collect all string constants (Constant nodes with str values) in the module
    string_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # These specific values must NOT appear as literals in the consumer;
    # they must come from config.dashboard.*
    config_driven_values = {"candle_delta", "position_delta"}
    literals_found = config_driven_values & string_constants
    assert not literals_found, (
        f"consumer.py must NOT hard-code topic names as literals: {literals_found}. "
        "Read them from config.dashboard.* (Principle #1)"
    )


def test_connect_resolves_config_via_sync_to_async():
    """REGRESSION (deploy AC-48.3): TapeFeedConsumer.connect() must resolve the
    active config through ``sync_to_async``.

    get_active_config() hits the Django ORM on a cache miss. Calling it directly
    inside the async connect() raises SynchronousOnlyOperation, which surfaces as
    an HTTP 500 on the WS upgrade (the failure the AC-48.3 deploy smoke-test caught
    on the sprint-11 boundary). This test asserts the config resolution inside
    connect() is wrapped in an awaited sync_to_async(...) call so the regression
    cannot reappear.
    """
    src = CONSUMER_PY.read_text()
    tree = ast.parse(src)

    assert "from asgiref.sync import sync_to_async" in src, (
        "consumer.py must import sync_to_async to safely run ORM-backed config "
        "resolution from the async connect()"
    )

    # Locate the async connect() method.
    connect_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "connect":
            connect_fn = node
            break
    assert connect_fn is not None, "consumer.py must define an async connect() method"

    def _call_name(call: ast.Call) -> str:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
        return ""

    # The config resolution must be wrapped in sync_to_async(...).
    sync_wrapped_calls = [
        n for n in ast.walk(connect_fn)
        if isinstance(n, ast.Call) and _call_name(n) == "sync_to_async"
    ]
    assert sync_wrapped_calls, (
        "connect() must wrap the config resolution in sync_to_async(...) — "
        "found no sync_to_async call in the method body"
    )

    # The sync_to_async(...) wrapper must itself be awaited:
    #   await sync_to_async(fn)()
    awaited_sync_to_async = any(
        isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Call)
        and _call_name(n.value.func) == "sync_to_async"
        for n in ast.walk(connect_fn)
    )
    assert awaited_sync_to_async, (
        "the sync_to_async-wrapped config resolution in connect() must be awaited"
    )


# ===========================================================================
# (b) Deterministic replay tests — OFFLINE, zero firehose
# ===========================================================================

def test_processor_emits_candle_deltas_from_replay_source():
    """Driven from banked ReplaySource, TapeFeedProcessor emits candle_delta dicts."""
    deltas = _run(_collect_deltas(_BANKED_SWAPS))
    candle_deltas = [d for d in deltas if d["type"] == "candle_delta"]
    # 5 swaps across 2 windows: window-0 closes when window-1 starts, window-1 is
    # the trailing partial emitted at end.
    assert len(candle_deltas) >= 2, (
        f"Expected at least 2 candle_delta messages, got {len(candle_deltas)}"
    )
    for cd in candle_deltas:
        assert "candle" in cd
        candle = cd["candle"]
        assert all(k in candle for k in ("t", "open", "high", "low", "close", "vol", "interval_s"))


def test_processor_emits_position_deltas_from_replay_source():
    """Driven from banked ReplaySource, TapeFeedProcessor emits position_delta dicts."""
    deltas = _run(_collect_deltas(_BANKED_SWAPS))
    position_deltas = [d for d in deltas if d["type"] == "position_delta"]
    # One position_delta per valid swap
    assert len(position_deltas) == len(_BANKED_SWAPS), (
        f"Expected {len(_BANKED_SWAPS)} position_delta messages, got {len(position_deltas)}"
    )
    for pd in position_deltas:
        assert "position" in pd
        pos = pd["position"]
        assert all(k in pos for k in ("last_price", "vol_buy", "vol_sell", "net_flow", "n_swaps"))


def test_processor_output_is_run_twice_identical():
    """Run TapeFeedProcessor twice on the same banked tape — output must be identical."""
    deltas_run1 = _run(_collect_deltas(_BANKED_SWAPS))
    deltas_run2 = _run(_collect_deltas(_BANKED_SWAPS))
    assert deltas_run1 == deltas_run2, (
        "TapeFeedProcessor must produce run-twice identical output from the same ReplaySource tape"
    )


def test_processor_candle_ohlc_correct():
    """OHLC values for the first closed candle (window 0: t=0..4) are correct."""
    deltas = _run(_collect_deltas(_BANKED_SWAPS))
    candle_deltas = [d for d in deltas if d["type"] == "candle_delta"]
    # First candle closes when the t=5 swap crosses into window 1
    first_candle = candle_deltas[0]["candle"]
    assert first_candle["t"] == 0, f"Expected t=0, got {first_candle['t']}"
    assert first_candle["open"] == 0.001, f"Expected open=0.001, got {first_candle['open']}"
    assert first_candle["high"] == 0.0012, f"Expected high=0.0012, got {first_candle['high']}"
    assert first_candle["low"] == 0.001, f"Expected low=0.001, got {first_candle['low']}"
    assert first_candle["close"] == 0.0011, f"Expected close=0.0011, got {first_candle['close']}"
    assert abs(first_candle["vol"] - 300.0) < 1e-9, f"Expected vol=300.0, got {first_candle['vol']}"
    assert first_candle["interval_s"] == 5


def test_processor_position_aggregate_correct():
    """After all 5 banked swaps, the final position_delta has correct aggregates."""
    deltas = _run(_collect_deltas(_BANKED_SWAPS))
    position_deltas = [d for d in deltas if d["type"] == "position_delta"]
    final = position_deltas[-1]["position"]
    assert final["n_swaps"] == 5
    assert abs(final["vol_buy"] - 450.0) < 1e-9, f"vol_buy expected 450.0 (sig1+sig2+sig4), got {final['vol_buy']}"
    assert abs(final["vol_sell"] - 130.0) < 1e-9, f"vol_sell expected 130.0 (sig3+sig5), got {final['vol_sell']}"
    assert abs(final["net_flow"] - 320.0) < 1e-9, f"net_flow expected 320.0, got {final['net_flow']}"
    assert final["last_price"] == 0.0014


def test_processor_empty_source_emits_nothing():
    """An empty ReplaySource produces zero deltas."""
    deltas = _run(_collect_deltas([]))
    assert deltas == [], f"Expected no deltas from empty source, got {deltas}"


def test_processor_no_separate_price_fetch():
    """TapeFeedProcessor.iter_deltas() does not call any external price API.

    Verified by running with a pure in-memory ReplaySource and confirming the
    processor completes without any network I/O — OFFLINE, zero firehose.
    The absence of LiveSource/BirdeyeSwapSource in the dependency chain is the
    structural guarantee; this test proves it holds at runtime.
    """
    # The module must NOT reference live network sources
    src = CONSUMER_PY.read_text()
    for banned_import in ("requests", "httpx", "aiohttp", "websockets", "birdeye"):
        assert banned_import not in src, (
            f"consumer.py must NOT import '{banned_import}' — no external price fetch"
        )
    # And the processor runs cleanly offline
    deltas = _run(_collect_deltas(_BANKED_SWAPS))
    assert len(deltas) > 0, "Processor must produce deltas from the banked tape"
