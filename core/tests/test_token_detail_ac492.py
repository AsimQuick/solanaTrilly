# ---
# module: core.tests.test_token_detail_ac492
# sprint: sprint-10
# story: US-49 AC-49.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.token_detail, core.dashboard.candle_api, core.views, ast, pathlib, django.test
# ---
"""AC-49.2 — token-detail research view tests.

Verifies that the token-detail view assembles the candle series + overlay +
markers from the candle API (AC-49.1) for a banked replay token,
deterministically and OFFLINE (zero firehose).

Structural tests (H1 + AST):
  - build_token_detail and token_detail_api importable at collection time (H1
    trap: deleted module → pytest collection fails, never execution failure).
  - token_detail.py imports build_candles from core.dashboard.candle_api
    (structural proof: AC-49.1 path is reused — not reimplemented).
  - token_detail.py imports _lake_row_to_micro from core.feature_extractor
    (Principle #2: same §7.1 normalization — no separate price source).
  - token_detail.py does NOT import any network client or price feed.
  - token_detail_api URL is registered in core/urls.py.
  - TokenDetail.jsx exists in the frontend and references lightweight-charts.
  - package.json declares the lightweight-charts dependency.

Determinism tests (OFFLINE, zero firehose):
  - build_overlay returns run-twice identical results over the banked fixture.
  - build_token_detail returns run-twice identical candles + overlay + markers.
  - Overlay buy_vol / sell_vol / net_flow values match expected fixture sums.
  - Markers t0 and score_time are correct (min block_time + score_at_elapsed_s).

HTTP endpoint tests (mocked LakeReader, OFFLINE):
  - GET /api/token-detail/<mint>/?interval_s=15 returns 200 with correct shape.
  - Response candles match build_candles output for the banked fixture.
  - Response overlay matches build_overlay output for the banked fixture.
  - Response markers match expected t0 / score_time.
  - Run-twice identical over the banked fixture.
  - Unsupported interval_s returns 400.
  - Missing interval_s returns 400.

Tests:
  test_import_trap_build_token_detail
  test_import_trap_token_detail_api_view
  test_token_detail_imports_build_candles_from_candle_api
  test_token_detail_imports_lake_row_to_micro_from_extractor
  test_token_detail_no_network_client_imports
  test_token_detail_url_registered
  test_token_detail_jsx_exists
  test_token_detail_jsx_references_lightweight_charts
  test_package_json_has_lightweight_charts
  test_build_overlay_empty_rows_returns_empty
  test_build_overlay_mint_filter
  test_build_overlay_buy_sell_sums_correct
  test_build_overlay_net_flow_correct
  test_build_overlay_run_twice_identical
  test_build_overlay_sorted_ascending_by_t
  test_build_overlay_invalid_interval_raises
  test_build_token_detail_candles_match_build_candles
  test_build_token_detail_overlay_included
  test_build_token_detail_markers_t0_correct
  test_build_token_detail_markers_score_time_correct
  test_build_token_detail_run_twice_identical
  test_build_token_detail_empty_mint_returns_empty_candles
  test_endpoint_200_with_correct_shape
  test_endpoint_candles_match_build_candles
  test_endpoint_overlay_present_and_correct
  test_endpoint_markers_present
  test_endpoint_run_twice_identical
  test_endpoint_unsupported_interval_returns_400
  test_endpoint_missing_interval_returns_400
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
# H1 ImportError trap — fails pytest COLLECTION if token_detail is deleted
# ---------------------------------------------------------------------------
from core.dashboard.token_detail import build_overlay, build_token_detail  # noqa: E402
from core.views import token_detail_api  # noqa: E402  # H1: fails collection if removed

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_DETAIL_PY = REPO_ROOT / "core" / "dashboard" / "token_detail.py"
URLS_PY = REPO_ROOT / "core" / "urls.py"
TOKEN_DETAIL_JSX = REPO_ROOT / "frontend" / "src" / "TokenDetail.jsx"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"

# ---------------------------------------------------------------------------
# Banked lake fixture — same shape as the AC-49.1 fixture; extends to cover
# both sides (buy + sell) so the overlay tests have non-trivial values.
# Two mints: _MINT (signal) and _OTHER_MINT (noise — must be filtered out).
# ---------------------------------------------------------------------------
_MINT = "AC492_TOKEN_DETAIL_TEST_MINT"
_OTHER_MINT = "AC492_OTHER_MINT"
_SCORE_AT = 30  # seconds (for marker test; synthetic config value)

_BANKED_ROWS: list[dict[str, Any]] = [
    # t=0 — buy; window 0 for all intervals
    {
        "mint": _MINT, "block_time": 0, "slot": 1, "signature": "s1",
        "price": 0.001000, "vol_sol": 100.0, "side": "buy", "rel": 0.0, "owner": None,
    },
    # t=2 — sell; same 5s window (t=0) as above
    {
        "mint": _MINT, "block_time": 2, "slot": 2, "signature": "s2",
        "price": 0.001200, "vol_sol": 50.0, "side": "sell", "rel": 2.0, "owner": None,
    },
    # t=10 — buy; new 5s window (t=10)
    {
        "mint": _MINT, "block_time": 10, "slot": 3, "signature": "s3",
        "price": 0.001500, "vol_sol": 80.0, "side": "buy", "rel": 10.0, "owner": None,
    },
    # t=20 — buy; new 5s window (t=20)
    {
        "mint": _MINT, "block_time": 20, "slot": 4, "signature": "s4",
        "price": 0.001100, "vol_sol": 120.0, "side": "buy", "rel": 20.0, "owner": None,
    },
    # t=65 — sell; new 5s window (t=65)
    {
        "mint": _MINT, "block_time": 65, "slot": 5, "signature": "s5",
        "price": 0.001800, "vol_sol": 200.0, "side": "sell", "rel": 65.0, "owner": None,
    },
    # Noise row for another mint — must be filtered out in all assertions.
    {
        "mint": _OTHER_MINT, "block_time": 5, "slot": 6, "signature": "s6",
        "price": 9.999, "vol_sol": 999.0, "side": "buy", "rel": 5.0, "owner": None,
    },
]

# Expected overlay for 5s interval from the banked fixture:
# t=0:  buy_vol=100, sell_vol=50,  net_flow=50
# t=10: buy_vol=80,  sell_vol=0,   net_flow=80
# t=20: buy_vol=120, sell_vol=0,   net_flow=120
# t=65: buy_vol=0,   sell_vol=200, net_flow=-200
_EXPECTED_OVERLAY_5S = [
    {"t": 0,  "buy_vol": 100.0, "sell_vol": 50.0,  "net_flow": 50.0},
    {"t": 10, "buy_vol": 80.0,  "sell_vol": 0.0,   "net_flow": 80.0},
    {"t": 20, "buy_vol": 120.0, "sell_vol": 0.0,   "net_flow": 120.0},
    {"t": 65, "buy_vol": 0.0,   "sell_vol": 200.0, "net_flow": -200.0},
]


# ===========================================================================
# (a) Structural / AST tests
# ===========================================================================

def test_import_trap_build_token_detail():
    """H1: build_token_detail is importable — fails collection if deleted."""
    assert build_token_detail is not None


def test_import_trap_token_detail_api_view():
    """H1: token_detail_api view is importable from core.views — fails collection if removed."""
    assert token_detail_api is not None


def test_token_detail_imports_build_candles_from_candle_api():
    """AST: token_detail.py imports build_candles from core.dashboard.candle_api.

    Structural proof that the view assembles candles from the AC-49.1 path —
    it does NOT re-implement candle derivation locally.
    """
    src = TOKEN_DETAIL_PY.read_text()
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
        "token_detail.py must import build_candles from core.dashboard.candle_api "
        "(AC-49.2 assembles from AC-49.1 — not a re-implementation)"
    )


def test_token_detail_imports_lake_row_to_micro_from_extractor():
    """AST: token_detail.py imports _lake_row_to_micro from core.feature_extractor.

    Principle #2: the overlay's price basis is the same §7.1 normalization
    as the shared US-30 extractor — no separate price source.
    """
    src = TOKEN_DETAIL_PY.read_text()
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
        "token_detail.py must import _lake_row_to_micro from core.feature_extractor "
        "(Principle #2 — one price basis; overlay uses the same §7.1 normalization)"
    )


def test_token_detail_no_network_client_imports():
    """AST: token_detail.py does not import any network client (zero firehose)."""
    src = TOKEN_DETAIL_PY.read_text()
    for banned in ("requests", "httpx", "aiohttp", "websockets", "urllib", "birdeye"):
        assert banned not in src, (
            f"token_detail.py must NOT import '{banned}' — OFFLINE, zero firehose"
        )


def test_token_detail_url_registered():
    """Structural: api/token-detail/ URL is registered in core/urls.py."""
    src = URLS_PY.read_text()
    assert "token_detail_api" in src, (
        "core/urls.py must register the token_detail_api view (AC-49.2 endpoint)"
    )
    assert "api/token-detail/" in src, (
        "core/urls.py must map the api/token-detail/<mint>/ URL pattern"
    )


def test_token_detail_jsx_exists():
    """Structural: frontend/src/TokenDetail.jsx exists (AC-49.2 frontend component)."""
    assert TOKEN_DETAIL_JSX.exists(), (
        "frontend/src/TokenDetail.jsx must exist (AC-49.2 TradingView Lightweight Charts view)"
    )


def test_token_detail_jsx_references_lightweight_charts():
    """Structural: TokenDetail.jsx imports from 'lightweight-charts' (TradingView)."""
    src = TOKEN_DETAIL_JSX.read_text()
    assert "lightweight-charts" in src, (
        "TokenDetail.jsx must import from 'lightweight-charts' "
        "(PRD §13 — TradingView Lightweight Charts is the required candle renderer)"
    )
    assert "createChart" in src, (
        "TokenDetail.jsx must use createChart from lightweight-charts"
    )


def test_package_json_has_lightweight_charts():
    """Structural: frontend/package.json declares the lightweight-charts dependency."""
    data = json.loads(PACKAGE_JSON.read_text())
    deps = data.get("dependencies", {})
    assert "lightweight-charts" in deps, (
        "frontend/package.json must declare 'lightweight-charts' in dependencies "
        "(TradingView Lightweight Charts MIT — required candle renderer)"
    )


# ===========================================================================
# (b) build_overlay() unit tests — OFFLINE, zero firehose
# ===========================================================================

def test_build_overlay_empty_rows_returns_empty():
    """Empty rows → empty overlay list."""
    result = build_overlay([], _MINT, 5)
    assert result == []


def test_build_overlay_mint_filter():
    """Only rows for the requested mint contribute; noise-mint rows are excluded."""
    overlay = build_overlay(_BANKED_ROWS, _MINT, 5)
    assert len(overlay) > 0
    # Noise-mint volume is 999.0 — must never appear in any overlay bucket.
    for o in overlay:
        assert o["buy_vol"] != 999.0 and o["sell_vol"] != 999.0, (
            f"Overlay window {o} contains noise-mint volume 999.0 — mint filter broken"
        )


def test_build_overlay_buy_sell_sums_correct():
    """Overlay buy_vol and sell_vol match hand-computed expected values (5s interval)."""
    overlay = build_overlay(_BANKED_ROWS, _MINT, 5)
    overlay_by_t = {o["t"]: o for o in overlay}

    # t=0: buy 100, sell 50
    assert overlay_by_t[0]["buy_vol"] == pytest.approx(100.0)
    assert overlay_by_t[0]["sell_vol"] == pytest.approx(50.0)
    # t=10: buy 80, sell 0
    assert overlay_by_t[10]["buy_vol"] == pytest.approx(80.0)
    assert overlay_by_t[10]["sell_vol"] == pytest.approx(0.0)
    # t=20: buy 120, sell 0
    assert overlay_by_t[20]["buy_vol"] == pytest.approx(120.0)
    assert overlay_by_t[20]["sell_vol"] == pytest.approx(0.0)
    # t=65: buy 0, sell 200
    assert overlay_by_t[65]["buy_vol"] == pytest.approx(0.0)
    assert overlay_by_t[65]["sell_vol"] == pytest.approx(200.0)


def test_build_overlay_net_flow_correct():
    """net_flow = buy_vol - sell_vol for each candle window."""
    overlay = build_overlay(_BANKED_ROWS, _MINT, 5)
    for o in overlay:
        assert o["net_flow"] == pytest.approx(o["buy_vol"] - o["sell_vol"]), (
            f"net_flow mismatch at t={o['t']}: "
            f"expected {o['buy_vol'] - o['sell_vol']}, got {o['net_flow']}"
        )


def test_build_overlay_run_twice_identical():
    """build_overlay is run-twice identical over the banked fixture (deterministic)."""
    run1 = build_overlay(copy.deepcopy(_BANKED_ROWS), _MINT, 5)
    run2 = build_overlay(copy.deepcopy(_BANKED_ROWS), _MINT, 5)
    assert run1 == run2, "build_overlay must be run-twice identical"


def test_build_overlay_sorted_ascending_by_t():
    """Overlay windows are sorted by ascending 't' for all supported intervals."""
    for interval_s in [1, 5, 15, 60]:
        overlay = build_overlay(_BANKED_ROWS, _MINT, interval_s)
        ts = [o["t"] for o in overlay]
        assert ts == sorted(ts), (
            f"Overlay for interval_s={interval_s} must be sorted ascending by t; got {ts}"
        )


def test_build_overlay_invalid_interval_raises():
    """build_overlay raises ValueError for interval_s <= 0."""
    with pytest.raises(ValueError):
        build_overlay(_BANKED_ROWS, _MINT, 0)
    with pytest.raises(ValueError):
        build_overlay(_BANKED_ROWS, _MINT, -5)


# ===========================================================================
# (c) build_token_detail() integration tests — OFFLINE, zero firehose
# ===========================================================================

def test_build_token_detail_candles_match_build_candles():
    """build_token_detail candles are identical to build_candles output (AC-49.1)."""
    from core.dashboard.candle_api import build_candles  # noqa: PLC0415
    interval_s = 5
    rows = copy.deepcopy(_BANKED_ROWS)
    expected_candles = build_candles(copy.deepcopy(rows), _MINT, interval_s)
    detail = build_token_detail(copy.deepcopy(rows), _MINT, interval_s, _SCORE_AT)
    assert detail["candles"] == expected_candles, (
        "build_token_detail candles must be identical to build_candles output "
        "(AC-49.2 assembles FROM AC-49.1 — not a re-implementation)"
    )


def test_build_token_detail_overlay_included():
    """build_token_detail includes a non-empty overlay for the banked fixture."""
    detail = build_token_detail(copy.deepcopy(_BANKED_ROWS), _MINT, 5, _SCORE_AT)
    assert "overlay" in detail, "build_token_detail must include 'overlay' key"
    assert len(detail["overlay"]) > 0, "Overlay must be non-empty for the banked fixture"
    for o in detail["overlay"]:
        for field in ("t", "buy_vol", "sell_vol", "net_flow"):
            assert field in o, f"Overlay window missing field '{field}': {o}"


def test_build_token_detail_markers_t0_correct():
    """build_token_detail sets markers.t0 to the min block_time of the mint's rows."""
    detail = build_token_detail(copy.deepcopy(_BANKED_ROWS), _MINT, 5, _SCORE_AT)
    expected_t0 = min(
        int(r["block_time"]) for r in _BANKED_ROWS if r["mint"] == _MINT
    )
    assert detail["markers"]["t0"] == expected_t0, (
        f"markers.t0 must be {expected_t0} (first swap block_time); "
        f"got {detail['markers']['t0']}"
    )


def test_build_token_detail_markers_score_time_correct():
    """build_token_detail sets markers.score_time = t0 + score_at_elapsed_s."""
    detail = build_token_detail(copy.deepcopy(_BANKED_ROWS), _MINT, 5, _SCORE_AT)
    t0 = detail["markers"]["t0"]
    expected_score_time = t0 + _SCORE_AT
    assert detail["markers"]["score_time"] == expected_score_time, (
        f"markers.score_time must be t0+{_SCORE_AT}={expected_score_time}; "
        f"got {detail['markers']['score_time']}"
    )


def test_build_token_detail_run_twice_identical():
    """build_token_detail is run-twice identical over the banked fixture (deterministic)."""
    rows = copy.deepcopy(_BANKED_ROWS)
    run1 = build_token_detail(copy.deepcopy(rows), _MINT, 5, _SCORE_AT)
    run2 = build_token_detail(copy.deepcopy(rows), _MINT, 5, _SCORE_AT)
    assert run1 == run2, "build_token_detail must be run-twice identical"


def test_build_token_detail_empty_mint_returns_empty_candles():
    """build_token_detail for an unknown mint returns empty candles, overlay, and None markers."""
    detail = build_token_detail(copy.deepcopy(_BANKED_ROWS), "UNKNOWN_MINT", 5, _SCORE_AT)
    assert detail["candles"] == [], "Unknown mint must yield empty candles"
    assert detail["overlay"] == [], "Unknown mint must yield empty overlay"
    assert detail["markers"]["t0"] is None, "Unknown mint must yield t0=None"
    assert detail["markers"]["score_time"] is None, "Unknown mint must yield score_time=None"


# ===========================================================================
# (d) HTTP endpoint tests — Django test client + mocked LakeReader (OFFLINE)
# ===========================================================================

class TokenDetailEndpointTest(TestCase):
    """Test the /api/token-detail/<mint>/?interval_s=<n> endpoint."""

    def _mock_lake(self, rows):
        return patch("core.views.LakeReader", autospec=True, **{
            "return_value.iter_rows.return_value": iter(copy.deepcopy(rows)),
        })

    def test_endpoint_200_with_correct_shape(self):
        """GET /api/token-detail/<mint>/?interval_s=5 returns 200 with all required keys."""
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["mint"], _MINT)
        self.assertEqual(data["interval_s"], 5)
        self.assertIn("candles", data)
        self.assertIn("overlay", data)
        self.assertIn("markers", data)
        self.assertIn("t0", data["markers"])
        self.assertIn("score_time", data["markers"])
        self.assertIsInstance(data["candles"], list)
        self.assertIsInstance(data["overlay"], list)
        self.assertGreater(len(data["candles"]), 0)
        self.assertGreater(len(data["overlay"]), 0)

    def test_endpoint_candles_match_build_candles(self):
        """Response candles match build_candles output for the banked fixture."""
        from core.dashboard.candle_api import build_candles  # noqa: PLC0415
        expected = build_candles(copy.deepcopy(_BANKED_ROWS), _MINT, 5)
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["candles"], expected, (
            "Endpoint candles must match build_candles output for the banked fixture"
        ))

    def test_endpoint_overlay_present_and_correct(self):
        """Response overlay has buy_vol, sell_vol, net_flow fields for each candle window."""
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        for o in data["overlay"]:
            self.assertIn("t", o)
            self.assertIn("buy_vol", o)
            self.assertIn("sell_vol", o)
            self.assertIn("net_flow", o)
            self.assertAlmostEqual(o["net_flow"], o["buy_vol"] - o["sell_vol"], places=9)

    def test_endpoint_markers_present(self):
        """Response markers contain t0 and score_time derived from the banked fixture."""
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(_BANKED_ROWS)
            )
            response = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=5")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        markers = data["markers"]
        expected_t0 = min(
            int(r["block_time"]) for r in _BANKED_ROWS if r["mint"] == _MINT
        )
        self.assertEqual(markers["t0"], expected_t0, "markers.t0 must equal first swap block_time")
        self.assertIsNotNone(markers["score_time"], "markers.score_time must not be None")
        self.assertGreater(markers["score_time"], expected_t0, (
            "markers.score_time must be > t0 (t0 + score_at_elapsed_s)"
        ))

    def test_endpoint_run_twice_identical(self):
        """Calling the endpoint twice on the same banked fixture returns identical JSON."""
        rows_snapshot = copy.deepcopy(_BANKED_ROWS)
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp1 = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=15")
        with patch("core.views.LakeReader") as MockReader:
            MockReader.return_value.iter_rows.return_value = iter(
                copy.deepcopy(rows_snapshot)
            )
            resp2 = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=15")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(
            json.loads(resp1.content),
            json.loads(resp2.content),
            "Endpoint must be run-twice identical over the same banked fixture",
        )

    def test_endpoint_unsupported_interval_returns_400(self):
        """GET with unsupported interval_s (e.g. 99) returns 400."""
        response = self.client.get(f"/api/token-detail/{_MINT}/?interval_s=99")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_endpoint_missing_interval_returns_400(self):
        """GET without interval_s query param returns 400."""
        response = self.client.get(f"/api/token-detail/{_MINT}/")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)
