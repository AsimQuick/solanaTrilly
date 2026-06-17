# ---
# module: core.tests.test_cohort_api_ac501
# sprint: sprint-10
# story: US-50 AC-50.1, US-50 AC-50.3
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.cohort_api, core.dashboard.candle_api, core.schemas, ast, pathlib, django.test
# ---
"""AC-50.1 — cohort sparkline endpoint tests.

Verifies:
  (a) Structural/AST: build_cohort_sparklines reuses the shared US-49
      tape→candle path (build_candles) with NO cohort-local price basis.
      Specifically:
        - build_cohort_sparklines is importable (H1 import-trap — fails pytest
          COLLECTION if core/dashboard/cohort_api.py is deleted or
          build_cohort_sparklines is removed).
        - cohort_api.py imports build_candles from core.dashboard.candle_api
          (structural proof: same path as US-49 — Principle #2, no
          cohort-local candle basis).
        - cohort_api.py does NOT import any network client, price feed, or
          independent price source.
        - The cohort endpoint (cohort_api view) is registered in core/urls.py.

  (b) Deterministic sparklines: over a banked corpus fixture with two mints,
      build_cohort_sparklines() returns run-twice identical sparkline series
      for each interval (5s/15s) — OFFLINE, zero firehose.

  (c) Corpus coverage: the cohort result contains one sparkline entry per mint
      in the requested corpus; mints absent from the lake get an empty candle
      list (real-missing preserved, no crash, no fabricated values).

  (d) Endpoint: the /api/cohort/?interval_s=<n>&mints=<...> endpoint responds
      200 with correct JSON structure over a mocked LakeReader (OFFLINE), and
      is run-twice identical.

H1 ImportError trap:
    The module-level import of build_cohort_sparklines below fails pytest
    COLLECTION (not test execution) if core/dashboard/cohort_api.py is deleted
    or build_cohort_sparklines is removed — exactly the guard the orchestrator
    wires to CI.

Tests:
  test_import_trap_build_cohort_sparklines
  test_cohort_api_imports_build_candles_from_candle_api
  test_cohort_api_no_network_client_imports
  test_cohort_api_no_independent_price_source
  test_cohort_url_registered
  test_build_cohort_sparklines_empty_rows_empty_candles
  test_build_cohort_sparklines_returns_all_mints
  test_build_cohort_sparklines_absent_mint_empty_candles
  test_build_cohort_sparklines_5s_run_twice_identical
  test_build_cohort_sparklines_15s_run_twice_identical
  test_build_cohort_sparklines_candle_fields_complete
  test_build_cohort_sparklines_mint_filter_per_sparkline
  test_build_cohort_sparklines_invalid_interval_raises
  test_build_cohort_sparklines_response_shape
  test_endpoint_returns_200_with_json
  test_endpoint_run_twice_identical
  test_endpoint_missing_interval_returns_400
  test_endpoint_unsupported_interval_returns_400
  test_endpoint_auto_discovers_mints_from_lake
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
# H1 ImportError trap — fails pytest COLLECTION if cohort_api is deleted
# ---------------------------------------------------------------------------
from core.dashboard.cohort_api import build_cohort_sparklines  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
COHORT_API_PY = REPO_ROOT / "core" / "dashboard" / "cohort_api.py"
URLS_PY = REPO_ROOT / "core" / "urls.py"

# ---------------------------------------------------------------------------
# Banked corpus fixture — two mints, >15s span, covering 5s and 15s windows.
# Matches the lake row format that LakeReader.iter_rows() yields.
# ---------------------------------------------------------------------------
_MINT_A = "AC501_COHORT_MINT_A"
_MINT_B = "AC501_COHORT_MINT_B"
_ABSENT_MINT = "AC501_ABSENT_MINT"  # not present in any row

_BANKED_ROWS: list[dict[str, Any]] = [
    # Mint A — rows spanning ~65 s
    {
        "mint": _MINT_A, "block_time": 0, "slot": 1, "signature": "a1",
        "price": 0.002000, "vol_sol": 50.0, "side": "buy", "rel": 0.0, "owner": None,
    },
    {
        "mint": _MINT_A, "block_time": 3, "slot": 2, "signature": "a2",
        "price": 0.002500, "vol_sol": 30.0, "side": "buy", "rel": 3.0, "owner": None,
    },
    {
        "mint": _MINT_A, "block_time": 10, "slot": 3, "signature": "a3",
        "price": 0.001800, "vol_sol": 80.0, "side": "sell", "rel": 10.0, "owner": None,
    },
    {
        "mint": _MINT_A, "block_time": 20, "slot": 4, "signature": "a4",
        "price": 0.003000, "vol_sol": 100.0, "side": "buy", "rel": 20.0, "owner": None,
    },
    {
        "mint": _MINT_A, "block_time": 65, "slot": 5, "signature": "a5",
        "price": 0.004000, "vol_sol": 200.0, "side": "buy", "rel": 65.0, "owner": None,
    },
    # Mint B — independent rows
    {
        "mint": _MINT_B, "block_time": 1, "slot": 6, "signature": "b1",
        "price": 0.000500, "vol_sol": 10.0, "side": "buy", "rel": 1.0, "owner": None,
    },
    {
        "mint": _MINT_B, "block_time": 8, "slot": 7, "signature": "b2",
        "price": 0.000700, "vol_sol": 20.0, "side": "sell", "rel": 8.0, "owner": None,
    },
    {
        "mint": _MINT_B, "block_time": 30, "slot": 8, "signature": "b3",
        "price": 0.000900, "vol_sol": 15.0, "side": "buy", "rel": 30.0, "owner": None,
    },
]


# ===========================================================================
# (a) Structural / AST tests
# ===========================================================================

def test_import_trap_build_cohort_sparklines():
    """H1: build_cohort_sparklines is importable — fails collection if deleted."""
    assert build_cohort_sparklines is not None


def test_cohort_api_imports_build_candles_from_candle_api():
    """AST: cohort_api.py imports build_candles from core.dashboard.candle_api.

    This is the structural proof that the cohort sparklines share the SAME
    tape→candle path as US-49 (Principle #2 — no cohort-local candle basis).
    """
    src = COHORT_API_PY.read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "core.dashboard.candle_api"
            and any(alias.name == "build_candles" for alias in node.names)
        ):
            found = True
            break
    assert found, (
        "cohort_api.py must import build_candles from core.dashboard.candle_api "
        "(structural proof: same tape→candle path as US-49, Principle #2)"
    )


def test_cohort_api_no_network_client_imports():
    """AST: cohort_api.py does not import any network client."""
    src = COHORT_API_PY.read_text()
    for banned in ("requests", "httpx", "aiohttp", "websockets", "urllib"):
        assert banned not in src, (
            f"cohort_api.py must NOT import '{banned}' — OFFLINE, zero firehose"
        )


def test_cohort_api_no_independent_price_source():
    """AST: cohort_api.py does not reference any independent price source."""
    src = COHORT_API_PY.read_text()
    for banned in ("birdeye", "PriceFeed", "price_feed", "live_price", "LiveSource", "ReplaySource"):
        assert banned not in src, (
            f"cohort_api.py must NOT reference '{banned}' — Principle #2: one price basis only"
        )


def test_cohort_url_registered():
    """Structural: api/cohort/ URL is registered in core/urls.py."""
    src = URLS_PY.read_text()
    assert "cohort_api" in src, "core/urls.py must register the cohort_api view"
    assert "api/cohort/" in src, "core/urls.py must map the api/cohort/ URL pattern"


# ===========================================================================
# (b) build_cohort_sparklines() determinism tests — OFFLINE, zero firehose
# ===========================================================================

def test_build_cohort_sparklines_empty_rows_empty_candles():
    """Empty rows → sparklines with empty candle lists for every mint."""
    result = build_cohort_sparklines([], [_MINT_A, _MINT_B], 5)
    assert result["count"] == 2
    assert len(result["sparklines"]) == 2
    for s in result["sparklines"]:
        assert s["candles"] == []


def test_build_cohort_sparklines_returns_all_mints():
    """Result contains one sparkline entry per requested mint, in order."""
    result = build_cohort_sparklines(_BANKED_ROWS, [_MINT_A, _MINT_B], 5)
    assert result["count"] == 2
    assert len(result["sparklines"]) == 2
    assert result["sparklines"][0]["mint"] == _MINT_A
    assert result["sparklines"][1]["mint"] == _MINT_B


def test_build_cohort_sparklines_absent_mint_empty_candles():
    """A mint not in the lake rows gets an empty candle list (no crash, no fabrication)."""
    result = build_cohort_sparklines(_BANKED_ROWS, [_MINT_A, _ABSENT_MINT], 5)
    mints_in_result = {s["mint"]: s for s in result["sparklines"]}
    assert _ABSENT_MINT in mints_in_result, "Absent mint must be present in result"
    assert mints_in_result[_ABSENT_MINT]["candles"] == [], (
        "Absent mint must have empty candles — real-missing preserved, not fabricated"
    )


def test_build_cohort_sparklines_5s_run_twice_identical():
    """5s interval: build_cohort_sparklines is run-twice identical over banked fixture."""
    run1 = build_cohort_sparklines(copy.deepcopy(_BANKED_ROWS), [_MINT_A, _MINT_B], 5)
    run2 = build_cohort_sparklines(copy.deepcopy(_BANKED_ROWS), [_MINT_A, _MINT_B], 5)
    assert run1 == run2, "build_cohort_sparklines must be run-twice identical for 5s interval"


def test_build_cohort_sparklines_15s_run_twice_identical():
    """15s interval: build_cohort_sparklines is run-twice identical over banked fixture."""
    run1 = build_cohort_sparklines(copy.deepcopy(_BANKED_ROWS), [_MINT_A, _MINT_B], 15)
    run2 = build_cohort_sparklines(copy.deepcopy(_BANKED_ROWS), [_MINT_A, _MINT_B], 15)
    assert run1 == run2, "build_cohort_sparklines must be run-twice identical for 15s interval"


def test_build_cohort_sparklines_candle_fields_complete():
    """Every candle in every sparkline has all required OHLC fields."""
    required = {"t", "open", "high", "low", "close", "vol", "interval_s"}
    result = build_cohort_sparklines(_BANKED_ROWS, [_MINT_A, _MINT_B], 5)
    for sparkline in result["sparklines"]:
        for c in sparkline["candles"]:
            missing = required - set(c.keys())
            assert not missing, (
                f"Candle for {sparkline['mint']} missing fields: {missing}; got {c}"
            )


def test_build_cohort_sparklines_mint_filter_per_sparkline():
    """Each sparkline only contains candles for its own mint — cross-mint contamination excluded."""
    result = build_cohort_sparklines(_BANKED_ROWS, [_MINT_A, _MINT_B], 5)
    a_candles = next(s["candles"] for s in result["sparklines"] if s["mint"] == _MINT_A)
    b_candles = next(s["candles"] for s in result["sparklines"] if s["mint"] == _MINT_B)

    # Mint A's max price is 0.004; Mint B's max price is 0.0009.
    # A's candles must not contain Mint B prices and vice versa.
    for c in a_candles:
        assert c["high"] <= 0.005, f"Mint A candle high suspiciously large: {c}"
    for c in b_candles:
        assert c["high"] <= 0.002, f"Mint B candle high suspiciously large: {c}"


def test_build_cohort_sparklines_invalid_interval_raises():
    """build_cohort_sparklines raises ValueError for interval_s <= 0."""
    with pytest.raises(ValueError):
        build_cohort_sparklines(_BANKED_ROWS, [_MINT_A], 0)
    with pytest.raises(ValueError):
        build_cohort_sparklines(_BANKED_ROWS, [_MINT_A], -5)


def test_build_cohort_sparklines_response_shape():
    """Result has the expected top-level shape: interval_s, count, sparklines."""
    result = build_cohort_sparklines(_BANKED_ROWS, [_MINT_A, _MINT_B], 15)
    assert "interval_s" in result
    assert "count" in result
    assert "sparklines" in result
    assert result["interval_s"] == 15
    assert result["count"] == 2
    assert isinstance(result["sparklines"], list)
    # Each sparkline has mint and candles keys.
    for s in result["sparklines"]:
        assert "mint" in s
        assert "candles" in s
        assert isinstance(s["candles"], list)


# ===========================================================================
# (c+d) HTTP endpoint tests — Django test client + mocked LakeReader (OFFLINE)
# ===========================================================================

class CohortApiEndpointTest(TestCase):
    """Test the /api/cohort/?interval_s=<n>&mints=<...> endpoint."""

    def _patch_lake(self, rows):
        return patch("core.views.LakeReader", **{
            "return_value.iter_rows.return_value": iter(rows),
        })

    def test_endpoint_returns_200_with_json(self):
        """GET /api/cohort/?interval_s=5&mints=A,B returns 200 with correct shape.

        AC-50.3: the cohort view now always delegates to build_cohort_wall(),
        which returns the wall format (groups/group_by/sort_by) rather than a
        bare sparklines list.  When no group_by is requested, a single group
        with key=None contains all entries.
        """
        mints_param = f"{_MINT_A},{_MINT_B}"
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/cohort/?interval_s=5&mints={mints_param}")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["interval_s"], 5)
        self.assertEqual(data["count"], 2)
        # AC-50.3: response uses wall format (groups) not bare sparklines list
        self.assertIn("groups", data)
        self.assertEqual(len(data["groups"]), 1)
        entries = data["groups"][0]["entries"]
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertIn("mint", entry)
            self.assertIn("candles", entry)
            self.assertIsInstance(entry["candles"], list)

    def test_endpoint_run_twice_identical(self):
        """Calling the endpoint twice on the same banked fixture returns identical JSON."""
        mints_param = f"{_MINT_A},{_MINT_B}"
        rows_snapshot = copy.deepcopy(_BANKED_ROWS)
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp1 = self.client.get(f"/api/cohort/?interval_s=15&mints={mints_param}")
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp2 = self.client.get(f"/api/cohort/?interval_s=15&mints={mints_param}")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(
            json.loads(resp1.content),
            json.loads(resp2.content),
            "Endpoint must be run-twice identical over the same banked corpus fixture",
        )

    def test_endpoint_missing_interval_returns_400(self):
        """GET without interval_s query param returns 400."""
        response = self.client.get("/api/cohort/")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_endpoint_unsupported_interval_returns_400(self):
        """GET with unsupported interval_s (e.g. 99) returns 400."""
        response = self.client.get("/api/cohort/?interval_s=99")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_endpoint_auto_discovers_mints_from_lake(self):
        """Without mints param, endpoint discovers all unique mints from the lake.

        AC-50.3: response uses wall format; mints are in group entries.
        """
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get("/api/cohort/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        # AC-50.3: mints are in groups[0]["entries"]
        entries = data["groups"][0]["entries"]
        discovered_mints = {e["mint"] for e in entries}
        self.assertIn(_MINT_A, discovered_mints)
        self.assertIn(_MINT_B, discovered_mints)
        # count matches number of unique mints in the lake
        self.assertEqual(data["count"], len(discovered_mints))

    def test_endpoint_absent_mint_empty_candles(self):
        """A requested mint not in the lake gets an empty candle list, not a crash.

        AC-50.3: response uses wall format; entries are in groups[0]["entries"].
        """
        mints_param = f"{_MINT_A},{_ABSENT_MINT}"
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/cohort/?interval_s=5&mints={mints_param}")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        # AC-50.3: entries are in groups[0]["entries"] (wall format)
        entries = data["groups"][0]["entries"]
        by_mint = {e["mint"]: e for e in entries}
        self.assertIn(_ABSENT_MINT, by_mint)
        self.assertEqual(by_mint[_ABSENT_MINT]["candles"], [])
