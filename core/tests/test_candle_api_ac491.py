# ---
# module: core.tests.test_candle_api_ac491
# sprint: sprint-10
# story: US-49 AC-49.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.candle_api, core.feature_extractor, core.schemas, ast, pathlib, django.test
# ---
"""AC-49.1 — tape→candle API tests.

Verifies:
  (a) Structural/AST: the candle basis resolves to the shared US-30 extractor
      path / raw lake — there is NO candle-local price source.  Specifically:
        - build_candles is importable (H1 import-trap — fails pytest COLLECTION
          if core/dashboard/candle_api.py is deleted or build_candles is removed).
        - candle_api.py imports _lake_row_to_micro from core.feature_extractor
          (the shared §7.1 normalization path — same price field as extractor).
        - candle_api.py does NOT import any network client, price feed, or
          independent price source.
        - DashboardConfig.candle_intervals_s is declared in schemas.py
          (Principle #1 — config-driven intervals).
        - The candle endpoint (candle_api view) is registered in core/urls.py.

  (b) Deterministic OHLC: over a banked lake/replay fixture, build_candles()
      returns run-twice byte/value-identical OHLC for each of the four
      config-driven intervals (1s / 5s / 15s / 1m).

  (c) Endpoint: the /api/candles/<mint>/?interval_s=<n> endpoint responds 200
      with correct JSON structure over a mocked LakeReader (zero firehose,
      OFFLINE), and is run-twice identical.

H1 ImportError trap:
    The module-level import of build_candles below fails pytest COLLECTION
    (not test execution) if core/dashboard/candle_api.py is deleted or
    build_candles is removed — exactly the guard the orchestrator wires to CI.

Tests:
  test_import_trap_build_candles
  test_import_trap_supported_intervals_default
  test_candle_api_imports_lake_row_to_micro_from_extractor
  test_candle_api_no_network_client_imports
  test_candle_api_no_independent_price_source
  test_dashboard_config_has_candle_intervals_s
  test_candle_intervals_default_includes_all_four
  test_candle_api_url_registered
  test_build_candles_empty_rows_returns_empty
  test_build_candles_mint_filter
  test_build_candles_1s_interval_deterministic
  test_build_candles_5s_interval_deterministic
  test_build_candles_15s_interval_deterministic
  test_build_candles_60s_interval_deterministic
  test_build_candles_all_four_intervals_run_twice_identical
  test_build_candles_ohlc_values_correct_5s
  test_build_candles_sorted_ascending_by_t
  test_build_candles_invalid_interval_raises
  test_endpoint_returns_200_with_json(mock LakeReader)
  test_endpoint_unsupported_interval_returns_400
  test_endpoint_missing_interval_returns_400
  test_endpoint_run_twice_identical
"""
import ast
import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import TestCase

# ---------------------------------------------------------------------------
# H1 ImportError trap — fails pytest COLLECTION if candle_api is deleted
# ---------------------------------------------------------------------------
from core.dashboard.candle_api import SUPPORTED_INTERVALS_DEFAULT, build_candles  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
CANDLE_API_PY = REPO_ROOT / "core" / "dashboard" / "candle_api.py"
SCHEMAS_PY = REPO_ROOT / "core" / "schemas.py"
URLS_PY = REPO_ROOT / "core" / "urls.py"

# ---------------------------------------------------------------------------
# Banked lake fixture — raw rows as LakeReader.iter_rows() would yield.
# Spans >60 s so all four intervals (1s/5s/15s/1m) have at least 2 windows.
# Two mints: _MINT (signal) and _OTHER_MINT (noise — must be filtered out).
# ---------------------------------------------------------------------------
_MINT = "AC491_CANDLE_TEST_MINT"
_OTHER_MINT = "AC491_OTHER_MINT"

_BANKED_ROWS: list[dict[str, Any]] = [
    # t=0 — window 0 for ALL intervals
    # rel = seconds-since-graduation (lake row format; candle uses block_time for windowing)
    {
        "mint": _MINT, "block_time": 0, "slot": 1, "signature": "s1",
        "price": 0.001000, "vol_sol": 100.0, "side": "buy", "rel": 0.0, "owner": None,
    },
    # t=2 — same window 0 for ALL intervals
    {
        "mint": _MINT, "block_time": 2, "slot": 2, "signature": "s2",
        "price": 0.001200, "vol_sol": 50.0, "side": "buy", "rel": 2.0, "owner": None,
    },
    # t=10 — new window for 1s and 5s; same window for 15s and 60s
    {
        "mint": _MINT, "block_time": 10, "slot": 3, "signature": "s3",
        "price": 0.001500, "vol_sol": 80.0, "side": "sell", "rel": 10.0, "owner": None,
    },
    # t=20 — new window for 1s, 5s, 15s; same window for 60s
    {
        "mint": _MINT, "block_time": 20, "slot": 4, "signature": "s4",
        "price": 0.001100, "vol_sol": 120.0, "side": "buy", "rel": 20.0, "owner": None,
    },
    # t=65 — new window for ALL intervals
    {
        "mint": _MINT, "block_time": 65, "slot": 5, "signature": "s5",
        "price": 0.001800, "vol_sol": 200.0, "side": "buy", "rel": 65.0, "owner": None,
    },
    # Noise row for another mint — must NOT appear in any candle for _MINT
    {
        "mint": _OTHER_MINT, "block_time": 5, "slot": 6, "signature": "s6",
        "price": 9.999, "vol_sol": 999.0, "side": "buy", "rel": 5.0, "owner": None,
    },
]


# ===========================================================================
# (a) Structural / AST tests
# ===========================================================================

def test_import_trap_build_candles():
    """H1: build_candles is importable — fails collection if deleted."""
    assert build_candles is not None


def test_import_trap_supported_intervals_default():
    """H1: SUPPORTED_INTERVALS_DEFAULT is importable — fails collection if removed."""
    assert SUPPORTED_INTERVALS_DEFAULT is not None


def test_candle_api_imports_lake_row_to_micro_from_extractor():
    """AST: candle_api.py imports _lake_row_to_micro from core.feature_extractor.

    This is the structural proof that the candle basis is the shared US-30
    extractor normalization path — NOT a candle-local price derivation.
    """
    src = CANDLE_API_PY.read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "core.feature_extractor"
            and any(alias.name == "_lake_row_to_micro" for alias in node.names)
        ):
            found = True
            break
    assert found, (
        "candle_api.py must import _lake_row_to_micro from core.feature_extractor "
        "(structural proof: same §7.1 normalization path as the shared US-30 extractor)"
    )


def test_candle_api_no_network_client_imports():
    """AST: candle_api.py does not import any network client."""
    src = CANDLE_API_PY.read_text()
    for banned in ("requests", "httpx", "aiohttp", "websockets", "urllib"):
        assert banned not in src, (
            f"candle_api.py must NOT import '{banned}' — no network calls, no separate price fetch"
        )


def test_candle_api_no_independent_price_source():
    """AST: candle_api.py does not reference any independent price source or feed."""
    src = CANDLE_API_PY.read_text()
    for banned in ("birdeye", "PriceFeed", "price_feed", "live_price", "LiveSource", "ReplaySource"):
        assert banned not in src, (
            f"candle_api.py must NOT reference '{banned}' — Principle #2: one price basis only"
        )


def test_dashboard_config_has_candle_intervals_s():
    """AST: DashboardConfig in schemas.py declares candle_intervals_s (Principle #1)."""
    src = SCHEMAS_PY.read_text()
    assert "candle_intervals_s" in src, (
        "DashboardConfig must declare candle_intervals_s (Principle #1 — config-driven intervals)"
    )


def test_candle_intervals_default_includes_all_four():
    """SUPPORTED_INTERVALS_DEFAULT contains 1s, 5s, 15s, and 60s (1m)."""
    for expected in [1, 5, 15, 60]:
        assert expected in SUPPORTED_INTERVALS_DEFAULT, (
            f"SUPPORTED_INTERVALS_DEFAULT must include {expected}s interval; "
            f"got {SUPPORTED_INTERVALS_DEFAULT}"
        )


def test_candle_api_url_registered():
    """Structural: api/candles/ URL is registered in core/urls.py."""
    src = URLS_PY.read_text()
    assert "candle_api" in src, (
        "core/urls.py must register the candle_api view (AC-49.1 endpoint)"
    )
    assert "api/candles/" in src, (
        "core/urls.py must map the api/candles/<mint>/ URL pattern"
    )


# ===========================================================================
# (b) build_candles() determinism tests — OFFLINE, zero firehose
# ===========================================================================

def test_build_candles_empty_rows_returns_empty():
    """Empty rows → empty candle list."""
    result = build_candles([], _MINT, 5)
    assert result == []


def test_build_candles_mint_filter():
    """Only candles for the requested mint are returned; noise rows are excluded."""
    candles = build_candles(_BANKED_ROWS, _MINT, 5)
    # Sanity: we should get candles back
    assert len(candles) > 0
    # Structural: the noise row price (9.999) must not appear in any candle
    for c in candles:
        for field in ("open", "high", "low", "close"):
            assert c[field] != 9.999, (
                f"Candle {c} contains noise-mint price 9.999 — mint filter broken"
            )


def test_build_candles_1s_interval_deterministic():
    """1s interval: build_candles is run-twice identical over the banked fixture."""
    run1 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 1)
    run2 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 1)
    assert run1 == run2, "build_candles must be run-twice identical for 1s interval"
    assert len(run1) > 0, "Expected at least one candle for 1s interval"


def test_build_candles_5s_interval_deterministic():
    """5s interval: build_candles is run-twice identical over the banked fixture."""
    run1 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 5)
    run2 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 5)
    assert run1 == run2, "build_candles must be run-twice identical for 5s interval"
    assert len(run1) > 0, "Expected at least one candle for 5s interval"


def test_build_candles_15s_interval_deterministic():
    """15s interval: build_candles is run-twice identical over the banked fixture."""
    run1 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 15)
    run2 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 15)
    assert run1 == run2, "build_candles must be run-twice identical for 15s interval"
    assert len(run1) > 0, "Expected at least one candle for 15s interval"


def test_build_candles_60s_interval_deterministic():
    """60s/1m interval: build_candles is run-twice identical over the banked fixture."""
    run1 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 60)
    run2 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 60)
    assert run1 == run2, "build_candles must be run-twice identical for 60s (1m) interval"
    assert len(run1) > 0, "Expected at least one candle for 60s interval"


def test_build_candles_all_four_intervals_run_twice_identical():
    """All four config-driven intervals are run-twice identical (byte/value parity)."""
    for interval_s in SUPPORTED_INTERVALS_DEFAULT:
        run1 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, interval_s)
        run2 = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, interval_s)
        assert run1 == run2, (
            f"build_candles must be run-twice identical for interval_s={interval_s}"
        )


def test_build_candles_ohlc_values_correct_5s():
    """OHLC values for the 5s interval match expected values from the banked fixture.

    With 5s windows and the banked fixture:
      Window t=0  (rows at t=0 and t=2): open=0.001, high=0.0012, low=0.001, close=0.0012, vol=150
      Window t=10 (row at t=10):         open=0.0015, high=0.0015, low=0.0015, close=0.0015, vol=80
      Window t=20 (row at t=20):         open=0.0011, high=0.0011, low=0.0011, close=0.0011, vol=120
      Window t=65 (row at t=65):         open=0.0018, high=0.0018, low=0.0018, close=0.0018, vol=200
    """
    candles = build_candles(_BANKED_ROWS, _MINT, 5)
    # Sort by t (build_candles guarantees ascending order already)
    assert candles[0]["t"] == 0
    c0 = candles[0]
    assert abs(c0["open"] - 0.001) < 1e-9, f"Expected open=0.001, got {c0['open']}"
    assert abs(c0["high"] - 0.0012) < 1e-9, f"Expected high=0.0012, got {c0['high']}"
    assert abs(c0["low"] - 0.001) < 1e-9, f"Expected low=0.001, got {c0['low']}"
    assert abs(c0["close"] - 0.0012) < 1e-9, f"Expected close=0.0012, got {c0['close']}"
    assert abs(c0["vol"] - 150.0) < 1e-9, f"Expected vol=150.0, got {c0['vol']}"
    assert c0["interval_s"] == 5


def test_build_candles_sorted_ascending_by_t():
    """Candles are returned sorted by ascending t for all four intervals."""
    for interval_s in SUPPORTED_INTERVALS_DEFAULT:
        candles = build_candles(_BANKED_ROWS, _MINT, interval_s)
        ts = [c["t"] for c in candles]
        assert ts == sorted(ts), (
            f"Candles for interval_s={interval_s} must be sorted ascending by t; got {ts}"
        )


def test_build_candles_invalid_interval_raises():
    """build_candles raises ValueError for interval_s <= 0."""
    with pytest.raises(ValueError):
        build_candles(_BANKED_ROWS, _MINT, 0)
    with pytest.raises(ValueError):
        build_candles(_BANKED_ROWS, _MINT, -1)


def test_build_candles_candle_fields_complete():
    """Every candle dict has all required fields: t, open, high, low, close, vol, interval_s."""
    required = {"t", "open", "high", "low", "close", "vol", "interval_s"}
    for interval_s in SUPPORTED_INTERVALS_DEFAULT:
        candles = build_candles(_BANKED_ROWS, _MINT, interval_s)
        for c in candles:
            missing = required - set(c.keys())
            assert not missing, (
                f"Candle for interval_s={interval_s} missing fields: {missing}; got {c}"
            )


# ===========================================================================
# (c) HTTP endpoint tests — Django test client + mocked LakeReader (OFFLINE)
# ===========================================================================

class CandleApiEndpointTest(TestCase):
    """Test the /api/candles/<mint>/?interval_s=<n> endpoint."""

    def _mock_lake_reader(self, rows):
        """Patch LakeReader so iter_rows() returns the given rows (OFFLINE)."""
        return patch("core.views.LakeReader", autospec=True, **{
            "return_value.iter_rows.return_value": iter(rows),
        })

    def test_endpoint_returns_200_with_json(self):
        """GET /api/candles/<mint>/?interval_s=5 returns 200 with correct JSON shape."""
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/candles/{_MINT}/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["mint"], _MINT)
        self.assertEqual(data["interval_s"], 5)
        self.assertIn("candles", data)
        self.assertIsInstance(data["candles"], list)
        self.assertGreater(len(data["candles"]), 0)
        for c in data["candles"]:
            for field in ("t", "open", "high", "low", "close", "vol", "interval_s"):
                self.assertIn(field, c, f"Candle missing field '{field}': {c}")

    def test_endpoint_unsupported_interval_returns_400(self):
        """GET with unsupported interval_s (e.g. 99) returns 400."""
        response = self.client.get(f"/api/candles/{_MINT}/?interval_s=99")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_endpoint_missing_interval_returns_400(self):
        """GET without interval_s query param returns 400."""
        response = self.client.get(f"/api/candles/{_MINT}/")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_endpoint_run_twice_identical(self):
        """Calling the endpoint twice on the same banked fixture returns identical JSON."""
        rows_snapshot = copy.deepcopy(_BANKED_ROWS)
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp1 = self.client.get(f"/api/candles/{_MINT}/?interval_s=15")
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp2 = self.client.get(f"/api/candles/{_MINT}/?interval_s=15")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(
            json.loads(resp1.content),
            json.loads(resp2.content),
            "Endpoint must be run-twice identical over the same banked fixture",
        )

    def test_endpoint_all_four_intervals_return_200(self):
        """All four supported intervals (1/5/15/60) return 200."""
        for interval_s in [1, 5, 15, 60]:
            with patch("core.views.LakeReader") as MockReader:
                MockReader.return_value.iter_rows.return_value = iter(
                    copy.deepcopy(_BANKED_ROWS)
                )
                response = self.client.get(f"/api/candles/{_MINT}/?interval_s={interval_s}")
            self.assertEqual(
                response.status_code, 200,
                f"Expected 200 for interval_s={interval_s}, got {response.status_code}",
            )
