# ---
# module: core.tests.test_cohort_wall_ac503
# sprint: sprint-10
# story: US-50 AC-50.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.cohort_wall, core.dashboard.cohort_api, core.dashboard.cohort_grouping
# ---
"""Tests for cohort wall assembler (AC-50.3).

H1 import trap — fails pytest COLLECTION if build_cohort_wall is deleted/renamed.
The import below is intentionally at module level so that a missing function
causes collection failure, not a test failure.
"""
from __future__ import annotations

import ast
import os

import pytest

# H1 import trap — fails pytest COLLECTION if build_cohort_wall is deleted/renamed
from core.dashboard.cohort_wall import build_cohort_wall  # noqa: E402

assert build_cohort_wall  # fails collection if None

# ---------------------------------------------------------------------------
# Banked corpus: 3 mints, 5 rows each, spanning >65s for candle coverage
# ---------------------------------------------------------------------------
AC503_MINT_A = "CohortWallMintA11111111111111111111111111111"
AC503_MINT_B = "CohortWallMintB11111111111111111111111111111"
AC503_MINT_C = "CohortWallMintC11111111111111111111111111111"

_T0 = 1_700_000_000  # base timestamp

_CORPUS_ROWS = (
    [
        # Mint A — t0 at _T0 (00:00 UTC = time_of_day period_0), depth=300 (5×60.0 vol_sol)
        # depth_bucket: 300 < 1000 but >= 200 → bucket_2 with default thresholds [50, 200, 1000]
        {
            "mint": AC503_MINT_A,
            "block_time": _T0 + i * 15,
            "price": 1.0 + i * 0.1,
            "vol_sol": 60.0,
            "side": "buy",
            "slot": 1000 + i,
            "signature": f"sigA{i}",
            "rel": float(i * 15),
            "owner": None,
        }
        for i in range(5)
    ]
    + [
        # Mint B — t0 at _T0+50000 (13:53 UTC = time_of_day period_2 with [6,12,18]), depth=40 (5×8.0 vol_sol)
        # depth_bucket: 40 < 50 → bucket_0
        {
            "mint": AC503_MINT_B,
            "block_time": _T0 + 50000 + i * 15,
            "price": 2.0 + i * 0.05,
            "vol_sol": 8.0,
            "side": "sell",
            "slot": 2000 + i,
            "signature": f"sigB{i}",
            "rel": float(i * 15),
            "owner": None,
        }
        for i in range(5)
    ]
    + [
        # Mint C — t0 at _T0+100000 (03:46 UTC = time_of_day period_0 with [6,12,18]), depth=1200 (5×240.0 vol_sol)
        # depth_bucket: 1200 >= 1000 → bucket_3
        {
            "mint": AC503_MINT_C,
            "block_time": _T0 + 100000 + i * 15,
            "price": 0.5 + i * 0.01,
            "vol_sol": 240.0,
            "side": "buy",
            "slot": 3000 + i,
            "signature": f"sigC{i}",
            "rel": float(i * 15),
            "owner": None,
        }
        for i in range(5)
    ]
)

_MINTS = [AC503_MINT_A, AC503_MINT_B, AC503_MINT_C]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    """Return the repository root (two levels up from this test file)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_python(rel_path: str) -> ast.Module:
    """Parse a Python file relative to repo root and return its AST."""
    full = os.path.join(_repo_root(), rel_path)
    with open(full, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=full)


def _read_file(rel_path: str) -> str:
    """Read a text file relative to repo root."""
    full = os.path.join(_repo_root(), rel_path)
    with open(full, encoding="utf-8") as fh:
        return fh.read()


def _get_all_import_module_names(tree: ast.Module) -> list[str]:
    """Extract all imported module names (from ... import ...) from an AST."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def _get_all_imported_symbols(tree: ast.Module) -> list[str]:
    """Extract all imported symbol names from an AST."""
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                symbols.append(alias.name)
    return symbols


# ===========================================================================
# STRUCTURAL TESTS (AST-based)
# ===========================================================================


class TestCohortWallStructural:
    """AST-based structural tests for cohort_wall.py."""

    def test_h1_import_trap_build_cohort_wall_ac503(self):
        """H1 trap: build_cohort_wall must be callable."""
        assert callable(build_cohort_wall), "build_cohort_wall must be a callable function"

    def test_cohort_wall_no_network_imports(self):
        """cohort_wall.py must not import any network libraries."""
        tree = _parse_python("core/dashboard/cohort_wall.py")
        modules = _get_all_import_module_names(tree)
        forbidden = {"requests", "httpx", "aiohttp", "websockets", "urllib"}
        violations = forbidden & {m.split(".")[0] for m in modules}
        assert not violations, f"cohort_wall.py has forbidden network imports: {violations}"

    def test_cohort_wall_imports_build_cohort_sparklines(self):
        """cohort_wall.py must import build_cohort_sparklines from core.dashboard.cohort_api."""
        tree = _parse_python("core/dashboard/cohort_wall.py")
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "core.dashboard.cohort_api"
                and any(alias.name == "build_cohort_sparklines" for alias in node.names)
            ):
                found = True
                break
        assert found, "cohort_wall.py must import build_cohort_sparklines from core.dashboard.cohort_api"

    def test_cohort_wall_imports_apply_grouping_and_derive_meta(self):
        """cohort_wall.py must import apply_grouping and derive_meta_from_rows from cohort_grouping."""
        tree = _parse_python("core/dashboard/cohort_wall.py")
        symbols_from_grouping = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "core.dashboard.cohort_grouping"
            ):
                for alias in node.names:
                    symbols_from_grouping.add(alias.name)
        assert "apply_grouping" in symbols_from_grouping, (
            "cohort_wall.py must import apply_grouping from core.dashboard.cohort_grouping"
        )
        assert "derive_meta_from_rows" in symbols_from_grouping, (
            "cohort_wall.py must import derive_meta_from_rows from core.dashboard.cohort_grouping"
        )

    def test_views_imports_build_cohort_wall(self):
        """core/views.py must import build_cohort_wall from core.dashboard.cohort_wall."""
        tree = _parse_python("core/views.py")
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "core.dashboard.cohort_wall"
                and any(alias.name == "build_cohort_wall" for alias in node.names)
            ):
                found = True
                break
        assert found, "core/views.py must import build_cohort_wall from core.dashboard.cohort_wall"

    def test_views_does_not_import_build_cohort_sparklines_at_top_level(self):
        """core/views.py must not import build_cohort_sparklines at module level (replaced by build_cohort_wall)."""
        tree = _parse_python("core/views.py")
        # Only look at top-level (non-nested) imports
        for node in ast.iter_child_nodes(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "core.dashboard.cohort_api"
                and any(alias.name == "build_cohort_sparklines" for alias in node.names)
            ):
                pytest.fail(
                    "core/views.py should not have a top-level import of build_cohort_sparklines "
                    "(it was replaced by build_cohort_wall)"
                )


class TestFrontendCohortWall:
    """Tests asserting the CohortWall.jsx frontend file exists and is correctly structured."""

    def test_frontend_cohort_wall_jsx_exists(self):
        """frontend/src/CohortWall.jsx must exist."""
        path = os.path.join(_repo_root(), "frontend", "src", "CohortWall.jsx")
        assert os.path.isfile(path), f"CohortWall.jsx not found at {path}"

    def test_frontend_cohort_wall_jsx_has_metadata_header(self):
        """CohortWall.jsx must contain the metadata front matter block."""
        content = _read_file("frontend/src/CohortWall.jsx")
        assert "// ---" in content, "CohortWall.jsx must have a // --- metadata header"
        assert "story: US-50 AC-50.3" in content, "CohortWall.jsx must reference story: US-50 AC-50.3"

    def test_frontend_cohort_wall_jsx_imports_lightweight_charts(self):
        """CohortWall.jsx must import createChart from lightweight-charts."""
        content = _read_file("frontend/src/CohortWall.jsx")
        assert "createChart" in content, "CohortWall.jsx must use createChart from lightweight-charts"
        assert "lightweight-charts" in content, "CohortWall.jsx must import from 'lightweight-charts'"

    def test_frontend_cohort_wall_jsx_fetches_cohort_endpoint(self):
        """CohortWall.jsx must reference the /api/cohort/ endpoint."""
        content = _read_file("frontend/src/CohortWall.jsx")
        assert "/api/cohort/" in content, "CohortWall.jsx must fetch /api/cohort/"

    def test_frontend_cohort_wall_jsx_has_root_div_id(self):
        """CohortWall.jsx must define the cohort-wall-view root element."""
        content = _read_file("frontend/src/CohortWall.jsx")
        assert "cohort-wall-view" in content, "CohortWall.jsx must have id='cohort-wall-view'"

    def test_frontend_cohort_wall_jsx_has_data_attributes(self):
        """CohortWall.jsx must include data-group-by and data-sort-by attributes."""
        content = _read_file("frontend/src/CohortWall.jsx")
        assert "data-group-by" in content, "CohortWall.jsx must include data-group-by attribute"
        assert "data-sort-by" in content, "CohortWall.jsx must include data-sort-by attribute"

    def test_app_jsx_imports_cohort_wall(self):
        """frontend/src/App.jsx must import CohortWall."""
        content = _read_file("frontend/src/App.jsx")
        assert "CohortWall" in content, "App.jsx must import/use CohortWall"
        assert "CohortWall.jsx" in content, "App.jsx must import CohortWall from './CohortWall.jsx'"


# ===========================================================================
# BANKED FIXTURE + DETERMINISM TESTS
# ===========================================================================


class TestBuildCohortWallDeterminism:
    """Tests that build_cohort_wall produces identical results across calls (offline corpus)."""

    def test_build_cohort_wall_no_grouping_deterministic(self):
        """build_cohort_wall with no grouping returns identical results on two calls."""
        r1 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15)
        r2 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15)
        assert r1 == r2, "build_cohort_wall is non-deterministic without grouping"

    def test_build_cohort_wall_group_by_depth_bucket_deterministic(self):
        """build_cohort_wall with group_by=depth_bucket returns identical results on two calls."""
        r1 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, group_by="depth_bucket")
        r2 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, group_by="depth_bucket")
        assert r1 == r2, "build_cohort_wall is non-deterministic with group_by=depth_bucket"

    def test_build_cohort_wall_sort_by_only_deterministic(self):
        """build_cohort_wall with sort_by only (no group_by) returns identical results on two calls."""
        r1 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, sort_by="depth_bucket")
        r2 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, sort_by="depth_bucket")
        assert r1 == r2, "build_cohort_wall is non-deterministic with sort_by only"

    def test_build_cohort_wall_group_and_sort_deterministic(self):
        """build_cohort_wall with group_by and sort_by returns identical results on two calls."""
        r1 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, group_by="time_of_day", sort_by="depth_bucket")
        r2 = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, group_by="time_of_day", sort_by="depth_bucket")
        assert r1 == r2, "build_cohort_wall is non-deterministic with group_by+sort_by"


class TestBuildCohortWallShape:
    """Tests for shape and content of build_cohort_wall output."""

    def test_build_cohort_wall_no_grouping_shape(self):
        """build_cohort_wall with no grouping has expected top-level shape."""
        result = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15)
        assert result["interval_s"] == 15
        assert result["count"] == 3
        assert result["group_by"] is None
        assert result["sort_by"] is None
        groups = result["groups"]
        assert isinstance(groups, list)
        assert len(groups) == 1
        assert groups[0]["key"] is None
        assert groups[0]["count"] == 3
        assert isinstance(groups[0]["entries"], list)
        assert len(groups[0]["entries"]) == 3

    def test_build_cohort_wall_entries_have_mint_candles_meta(self):
        """Every entry in the wall groups has mint, candles, and meta keys."""
        result = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15)
        for group in result["groups"]:
            for entry in group["entries"]:
                assert "mint" in entry, f"Entry missing 'mint': {entry}"
                assert "candles" in entry, f"Entry missing 'candles': {entry}"
                assert "meta" in entry, f"Entry missing 'meta': {entry}"
                assert isinstance(entry["candles"], list)
                assert isinstance(entry["meta"], dict)
                # meta must have the five standard keys
                meta_keys = {"t0", "depth", "outcome", "score", "exit_trigger"}
                assert meta_keys == set(entry["meta"].keys()), (
                    f"meta dict has unexpected keys: {set(entry['meta'].keys())}"
                )

    def test_build_cohort_wall_meta_computable_fields(self):
        """For MINT_A (rows present), meta.t0 == _T0 and meta.depth == 300.0."""
        result = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15)
        entries_by_mint = {
            e["mint"]: e for group in result["groups"] for e in group["entries"]
        }
        meta_a = entries_by_mint[AC503_MINT_A]["meta"]
        assert meta_a["t0"] == _T0, f"Expected t0={_T0}, got {meta_a['t0']}"
        assert meta_a["depth"] == pytest.approx(300.0), f"Expected depth=300.0, got {meta_a['depth']}"
        # Offline fields are None
        assert meta_a["outcome"] is None
        assert meta_a["score"] is None
        assert meta_a["exit_trigger"] is None

    def test_build_cohort_wall_group_by_depth_bucket_partition(self):
        """With group_by=depth_bucket, mints land in expected buckets.

        Default thresholds [50, 200, 1000]:
          _bucket_value assigns bucket_i where value < sorted_thresholds[i].
          MINT_A depth=300: 300<50? No. 300<200? No. 300<1000? Yes → bucket_2.
          MINT_B depth=40:  40<50? Yes → bucket_0.
          MINT_C depth=1200: 1200>=1000 for all → bucket_3 (len(thresholds)).

        """
        result = build_cohort_wall(_CORPUS_ROWS, _MINTS, 15, group_by="depth_bucket")
        groups_by_key = {g["key"]: g for g in result["groups"]}

        # MINT_B → bucket_0 (depth=40 < 50)
        assert "bucket_0" in groups_by_key, f"Expected bucket_0, got keys: {list(groups_by_key)}"
        mints_b0 = [e["mint"] for e in groups_by_key["bucket_0"]["entries"]]
        assert AC503_MINT_B in mints_b0, f"MINT_B should be in bucket_0; got {mints_b0}"

        # MINT_A → bucket_2 (depth=300; 300<1000)
        assert "bucket_2" in groups_by_key, f"Expected bucket_2, got keys: {list(groups_by_key)}"
        mints_b2 = [e["mint"] for e in groups_by_key["bucket_2"]["entries"]]
        assert AC503_MINT_A in mints_b2, f"MINT_A should be in bucket_2; got {mints_b2}"

        # MINT_C → bucket_3 (depth=1200 >= 1000)
        assert "bucket_3" in groups_by_key, f"Expected bucket_3, got keys: {list(groups_by_key)}"
        mints_b3 = [e["mint"] for e in groups_by_key["bucket_3"]["entries"]]
        assert AC503_MINT_C in mints_b3, f"MINT_C should be in bucket_3; got {mints_b3}"

    def test_build_cohort_wall_absent_mint_no_crash(self):
        """An absent mint produces empty candles + None meta without crashing."""
        absent_mint = "MintNotInRows111111111111111111111111111111"
        result = build_cohort_wall(_CORPUS_ROWS, _MINTS + [absent_mint], 15)
        assert result["count"] == 4
        entries_by_mint = {
            e["mint"]: e for group in result["groups"] for e in group["entries"]
        }
        assert absent_mint in entries_by_mint, "Absent mint must still produce an entry"
        absent_entry = entries_by_mint[absent_mint]
        assert absent_entry["candles"] == [], f"Absent mint should have empty candles, got {absent_entry['candles']}"
        meta = absent_entry["meta"]
        assert meta["t0"] is None
        assert meta["depth"] is None
        assert meta["outcome"] is None
        assert meta["score"] is None
        assert meta["exit_trigger"] is None

    def test_build_cohort_wall_invalid_interval_raises(self):
        """build_cohort_wall with interval_s=0 raises ValueError."""
        with pytest.raises(ValueError):
            build_cohort_wall([], [], 0)

    def test_build_cohort_wall_empty_corpus(self):
        """build_cohort_wall with empty rows and mints returns count=0 and one empty group."""
        result = build_cohort_wall([], [], 5)
        assert result["count"] == 0
        assert result["interval_s"] == 5
        assert result["group_by"] is None
        assert result["sort_by"] is None
        groups = result["groups"]
        assert len(groups) == 1
        assert groups[0]["key"] is None
        assert groups[0]["count"] == 0
        assert groups[0]["entries"] == []


# ===========================================================================
# VIEWS.PY STRUCTURAL TEST
# ===========================================================================


class TestViewsStructural:
    """Structural tests for core/views.py cohort_api integration."""

    def test_cohort_wall_endpoint_uses_build_cohort_wall(self):
        """core/views.py must import and call build_cohort_wall (AC-50.3 refactor)."""
        content = _read_file("core/views.py")
        assert "build_cohort_wall" in content, (
            "core/views.py must reference build_cohort_wall after AC-50.3 refactor"
        )

    def test_cohort_wall_module_has_metadata_header(self):
        """core/dashboard/cohort_wall.py must contain the metadata front matter."""
        content = _read_file("core/dashboard/cohort_wall.py")
        assert "# ---" in content, "cohort_wall.py must have a # --- metadata header"
        assert "story: US-50 AC-50.3" in content, "cohort_wall.py must reference story: US-50 AC-50.3"
