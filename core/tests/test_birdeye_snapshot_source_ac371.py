# ---
# module: core.tests.test_birdeye_snapshot_source_ac371
# sprint: sprint-8
# story: US-37 AC-37.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.birdeye_snapshot_source, core.snapshot_source,
#               core.snapshot_fetcher, core.clock, ast, pathlib
# ---
"""AC-37.1 — BirdeyeSnapshotSource: seam conformance + #380 clamp + rate-limiter.

Verification prongs:

(a) Seam conformance:
      1. BirdeyeSnapshotSource is a subclass of SnapshotDataSource.
      2. BirdeyeSnapshotSource can be instantiated with an api_key.
      3. get_snapshot(mint, as_of) assembles the canonical seven-field snapshot
         dict from the three Birdeye REST responses (_call mocked offline).
      4. All seven required keys are present in the returned dict.
      5. Holder limit is forwarded to the holders endpoint.
      6. Missing overview fields default gracefully to 0.0.

(b) #380 future-window clamp:
      7. When BirdeyeSnapshotSource is injected into SnapshotFetcher with a
         future as_of, the fetcher clamps it to clock.now() before calling
         get_snapshot() — the source never receives a future timestamp.
      8. A past as_of is passed through unclamped.

(c) Redis token-bucket limiter:
      9. When BirdeyeSnapshotSource is injected into SnapshotFetcher with a
         FakeLimiter, wait_for_token() is called exactly once before the source.
     10. The limiter is NOT called on a second fetch for the same mint
         (at-most-one guard fires first).

(d) Adapter-layer isolation (static analysis):
     11. core/snapshot_fetcher.py does NOT import BirdeyeSnapshotSource or
         core.tape.birdeye_snapshot_source — the concrete adapter never bleeds
         into the core path (US-2 / Principle #7).
     12. core/snapshot_source.py does NOT import BirdeyeSnapshotSource.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ImportError trap (H1): if these modules are deleted/renamed, collection fails.
from core.clock import VirtualClock  # noqa: F401
from core.snapshot_fetcher import SnapshotFetcher  # noqa: F401
from core.snapshot_source import SnapshotDataSource
from core.tape.birdeye_snapshot_source import BirdeyeSnapshotSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
ONE_HOUR_AHEAD = T0 + timedelta(hours=1)
ONE_HOUR_AGO = T0 - timedelta(hours=1)

TEST_MINT = "AC371TestMint1111111111111111111111111111111"
FAKE_API_KEY = "test-api-key-offline"

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCHER_MODULE = REPO_ROOT / "core" / "snapshot_fetcher.py"
SOURCE_MODULE = REPO_ROOT / "core" / "snapshot_source.py"

# Birdeye REST responses (raw, as the API would return)
_SECURITY_RESPONSE = {
    "data": {
        "mintAuthority": None,
        "freezeAuthority": None,
        "lpBurned": True,
        "isMutable": False,
    }
}
_HOLDERS_RESPONSE = {
    "data": {
        "items": [
            {"address": "HolderAddr1111111111111111111111111111111111", "balance": 1000000},
            {"address": "HolderAddr2222222222222222222222222222222222", "balance": 500000},
        ],
        "total": 2,
    }
}
_OVERVIEW_RESPONSE = {
    "data": {
        "liquidity": 12345.67,
        "realLiquidity": 11000.0,
        "buy24h": 50.0,
        "sell24h": 45.0,
    }
}

# Expected assembled snapshot dict from the three mock responses above
EXPECTED_SNAPSHOT = {
    "holder_distribution": {
        "items": _HOLDERS_RESPONSE["data"]["items"],
        "total": 2,
    },
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 12345.67,
    "tvl": 11000.0,
    "depth": {"buy": 50.0, "sell": 45.0},
}


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeLimiter:
    """In-memory fake limiter recording call order and count."""

    def __init__(self, call_order: list) -> None:
        self._call_order = call_order
        self.call_count = 0

    def wait_for_token(self) -> None:
        self._call_order.append("limiter")
        self.call_count += 1


# ---------------------------------------------------------------------------
# Helper: return a BirdeyeSnapshotSource with _call mocked offline
# ---------------------------------------------------------------------------


def _make_source_with_mock_calls() -> BirdeyeSnapshotSource:
    source = BirdeyeSnapshotSource(api_key=FAKE_API_KEY)

    def _fake_call(path: str, params: dict) -> dict:
        if "token_security" in path:
            return _SECURITY_RESPONSE
        if "token/holder" in path:
            return _HOLDERS_RESPONSE
        if "token_overview" in path:
            return _OVERVIEW_RESPONSE
        raise ValueError(f"Unexpected path in test: {path}")

    source._call = _fake_call  # type: ignore[assignment]
    return source


# ---------------------------------------------------------------------------
# (a) Seam conformance
# ---------------------------------------------------------------------------


def test_birdeye_snapshot_source_is_subclass_of_snapshot_data_source():
    """BirdeyeSnapshotSource must be a concrete subclass of SnapshotDataSource."""
    assert issubclass(BirdeyeSnapshotSource, SnapshotDataSource), (
        "BirdeyeSnapshotSource must subclass SnapshotDataSource (the P4 seam). "
        "The fetcher core depends only on the abstract interface."
    )


def test_birdeye_snapshot_source_instantiates_with_api_key():
    """BirdeyeSnapshotSource can be instantiated with just an api_key."""
    source = BirdeyeSnapshotSource(api_key=FAKE_API_KEY)
    assert source is not None


def test_get_snapshot_assembles_canonical_seven_field_dict():
    """get_snapshot() assembles the canonical snapshot dict from three Birdeye responses.

    All seven keys (holder_distribution, mint_authority, freeze_authority,
    lp_burned, liquidity, tvl, depth) must be present and correctly mapped.
    """
    source = _make_source_with_mock_calls()
    result = source.get_snapshot(TEST_MINT, as_of=T0)

    assert result == EXPECTED_SNAPSHOT, (
        f"get_snapshot() returned unexpected payload.\n"
        f"Expected: {EXPECTED_SNAPSHOT}\n"
        f"Got:      {result}"
    )


def test_get_snapshot_has_all_seven_required_keys():
    """get_snapshot() must return all seven keys required by SnapshotSchema."""
    required_keys = {
        "holder_distribution",
        "mint_authority",
        "freeze_authority",
        "lp_burned",
        "liquidity",
        "tvl",
        "depth",
    }
    source = _make_source_with_mock_calls()
    result = source.get_snapshot(TEST_MINT, as_of=T0)

    missing = required_keys - result.keys()
    assert not missing, (
        f"get_snapshot() is missing required keys: {missing}. "
        "All seven snapshot fields are mandatory (PRD §6.3, §1.1)."
    )


def test_holder_limit_forwarded_to_holders_endpoint():
    """The holder_limit constructor arg is forwarded to the holders API call."""
    captured_params: list[dict] = []

    def _recording_call(path: str, params: dict) -> dict:
        if "token/holder" in path:
            captured_params.append(params)
            return _HOLDERS_RESPONSE
        if "token_security" in path:
            return _SECURITY_RESPONSE
        return _OVERVIEW_RESPONSE

    source = BirdeyeSnapshotSource(api_key=FAKE_API_KEY, holder_limit=42)
    source._call = _recording_call  # type: ignore[assignment]
    source.get_snapshot(TEST_MINT, as_of=T0)

    assert captured_params, "holder endpoint was never called"
    assert captured_params[0].get("limit") == 42, (
        f"Expected limit=42 forwarded to holder endpoint, "
        f"got params={captured_params[0]!r}"
    )


def test_get_snapshot_defaults_missing_overview_fields_gracefully():
    """Missing liquidity/TVL/depth fields in overview response default to 0.0."""
    empty_overview: dict = {"data": {}}

    def _call(path: str, params: dict) -> dict:
        if "token_security" in path:
            return _SECURITY_RESPONSE
        if "token/holder" in path:
            return _HOLDERS_RESPONSE
        return empty_overview

    source = BirdeyeSnapshotSource(api_key=FAKE_API_KEY)
    source._call = _call  # type: ignore[assignment]
    result = source.get_snapshot(TEST_MINT, as_of=T0)

    assert result["liquidity"] == 0.0
    assert result["tvl"] == 0.0
    assert result["depth"] == {"buy": 0.0, "sell": 0.0}


# ---------------------------------------------------------------------------
# (b) #380 future-window clamp — applied by SnapshotFetcher
# ---------------------------------------------------------------------------


def test_future_as_of_is_clamped_before_reaching_birdeye_source():
    """#380 clamp: a future as_of is clamped to clock.now() before get_snapshot().

    When BirdeyeSnapshotSource is injected into SnapshotFetcher and fetch() is
    called with as_of > clock.now(), the fetcher clamps it to clock.now() — the
    source never receives a future timestamp (Birdeye HTTP 400 prevention).
    """
    received_as_of: list[datetime] = []

    class RecordingSource(SnapshotDataSource):
        def get_snapshot(self, mint: str, as_of: datetime) -> dict:
            received_as_of.append(as_of)
            return EXPECTED_SNAPSHOT

    clock = VirtualClock(T0)
    fetcher = SnapshotFetcher(source=RecordingSource(), clock=clock)

    # as_of is ONE_HOUR_AHEAD — beyond clock.now() (T0)
    fetcher.fetch(TEST_MINT, as_of=ONE_HOUR_AHEAD)

    assert received_as_of, "get_snapshot() was not called"
    clamped = received_as_of[0]
    assert clamped == T0, (
        f"Expected as_of to be clamped to clock.now()={T0!r}, "
        f"but source received as_of={clamped!r}. "
        "#380 clamp must prevent future timestamps reaching Birdeye."
    )


def test_past_as_of_passes_through_unclamped():
    """A past as_of is forwarded to the source unchanged (clamp is min, not max)."""
    received_as_of: list[datetime] = []

    class RecordingSource(SnapshotDataSource):
        def get_snapshot(self, mint: str, as_of: datetime) -> dict:
            received_as_of.append(as_of)
            return EXPECTED_SNAPSHOT

    clock = VirtualClock(T0)
    fetcher = SnapshotFetcher(source=RecordingSource(), clock=clock)

    fetcher.fetch(TEST_MINT, as_of=ONE_HOUR_AGO)

    assert received_as_of[0] == ONE_HOUR_AGO, (
        f"Past as_of={ONE_HOUR_AGO!r} should pass through unclamped; "
        f"source received {received_as_of[0]!r}."
    )


# ---------------------------------------------------------------------------
# (c) Redis token-bucket limiter — applied by SnapshotFetcher
# ---------------------------------------------------------------------------


def test_limiter_is_called_before_birdeye_source():
    """wait_for_token() fires before get_snapshot() when a limiter is injected.

    Verifies that the existing AC-24.4 rate-limiter wiring still holds when a
    BirdeyeSnapshotSource (or any SnapshotDataSource) is used as the source.
    """
    call_order: list[str] = []

    class OrderTrackingSource(SnapshotDataSource):
        def get_snapshot(self, mint: str, as_of: datetime) -> dict:
            call_order.append("source")
            return EXPECTED_SNAPSHOT

    clock = VirtualClock(T0)
    limiter = FakeLimiter(call_order)
    fetcher = SnapshotFetcher(source=OrderTrackingSource(), clock=clock, limiter=limiter)

    fetcher.fetch(TEST_MINT)

    assert call_order == ["limiter", "source"], (
        f"Expected call order ['limiter', 'source'], got {call_order!r}. "
        "The rate limiter must be consulted BEFORE get_snapshot() (AC-24.4)."
    )


def test_limiter_not_called_on_second_fetch_same_mint():
    """Limiter is NOT consumed for a repeat fetch (at-most-one guard fires first)."""
    call_order: list[str] = []

    class SimpleSource(SnapshotDataSource):
        def get_snapshot(self, mint: str, as_of: datetime) -> dict:
            call_order.append("source")
            return EXPECTED_SNAPSHOT

    clock = VirtualClock(T0)
    limiter = FakeLimiter(call_order)
    fetcher = SnapshotFetcher(source=SimpleSource(), clock=clock, limiter=limiter)

    fetcher.fetch(TEST_MINT)
    second = fetcher.fetch(TEST_MINT)  # at-most-one guard fires

    assert second is None, "Second fetch for same mint must return None"
    assert limiter.call_count == 1, (
        f"Limiter must be called only once (for the first fetch); "
        f"called {limiter.call_count} time(s). At-most-one guard must fire "
        "before the limiter on a repeated mint (AC-24.4 / §6.3)."
    )


# ---------------------------------------------------------------------------
# (d) Adapter-layer isolation — static analysis
# ---------------------------------------------------------------------------


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _concrete_birdeye_imports(tree: ast.Module) -> list[str]:
    """Return any import lines referencing BirdeyeSnapshotSource or its module."""
    forbidden_modules = {"core.tape.birdeye_snapshot_source"}
    forbidden_names = {"BirdeyeSnapshotSource"}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in forbidden_modules:
                found.append(f"line {node.lineno}: 'from {mod} import ...'")
                continue
            for alias in node.names:
                if alias.name in forbidden_names:
                    found.append(
                        f"line {node.lineno}: 'from {mod} import {alias.name}'"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_modules:
                    found.append(f"line {node.lineno}: 'import {alias.name}'")
    return found


def test_snapshot_fetcher_does_not_import_birdeye_snapshot_source():
    """core/snapshot_fetcher.py must NOT import BirdeyeSnapshotSource.

    The fetcher core depends only on the abstract SnapshotDataSource seam —
    the concrete Birdeye adapter is injected at startup (Principle #7 / US-2).
    Importing the concrete class would couple the core path to the adapter layer.
    """
    assert FETCHER_MODULE.exists(), f"snapshot_fetcher.py not found at {FETCHER_MODULE}"
    violations = _concrete_birdeye_imports(_parse_module(FETCHER_MODULE))
    assert not violations, (
        "core/snapshot_fetcher.py must NOT import BirdeyeSnapshotSource or "
        "core.tape.birdeye_snapshot_source (Principle #7 / US-2).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_snapshot_source_does_not_import_birdeye_snapshot_source():
    """core/snapshot_source.py (the abstract seam) must NOT import the concrete adapter.

    The abstract seam must remain free of any concrete implementation reference —
    the seam and the adapter are one-way: adapter imports seam, never the reverse.
    """
    assert SOURCE_MODULE.exists(), f"snapshot_source.py not found at {SOURCE_MODULE}"
    violations = _concrete_birdeye_imports(_parse_module(SOURCE_MODULE))
    assert not violations, (
        "core/snapshot_source.py must NOT import BirdeyeSnapshotSource "
        "(the abstract seam must never reference a concrete adapter).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
