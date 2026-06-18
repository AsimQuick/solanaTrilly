# ---
# module: core.tests.test_calibration_pnl_view_ac722
# sprint: sprint-14
# story: US-72 AC-72.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: trading.analytics_api, pathlib, pytest
# ---
"""AC-72.2 — CalibrationPnL.jsx frontend wiring checks.

The AC requires:
  - CalibrationPnL.jsx exists in frontend/src/.
  - CalibrationPnL.jsx carries metadata front matter with US-72 story tag.
  - CalibrationPnL.jsx has an H1 ImportError trap (throw new Error).
  - App.jsx imports CalibrationPnL.
  - App.jsx routes view='calibration' to CalibrationPnL.
  - CalibrationPnL.jsx references each of the four analytics:
      win_rate_by_score_band, pnl_by_exit_trigger, scatter_points, calibration_curve.
  - The backend API view (calibration_pnl_analytics_view) is importable (H1 trap).

Tests
-----
H1 ImportError trap (module-level — fails pytest COLLECTION if surface is deleted/renamed):
  from trading.analytics_api import calibration_pnl_analytics_view

test_calibration_pnl_jsx_exists
    CalibrationPnL.jsx must exist in frontend/src/.

test_calibration_pnl_jsx_has_metadata_front_matter
    CalibrationPnL.jsx must carry metadata front matter with US-72 story tag.

test_calibration_pnl_jsx_has_import_error_trap
    CalibrationPnL.jsx must contain an H1 ImportError trap (throw new Error).

test_app_jsx_imports_calibration_pnl
    App.jsx must import CalibrationPnL.

test_app_jsx_routes_calibration_view
    App.jsx must route view='calibration' to CalibrationPnL.

test_calibration_pnl_jsx_renders_win_rate_section
    CalibrationPnL.jsx must reference win_rate_by_score_band / Win Rate.

test_calibration_pnl_jsx_renders_pnl_by_trigger_section
    CalibrationPnL.jsx must reference exit_trigger / pnl_by_exit_trigger.

test_calibration_pnl_jsx_renders_scatter_section
    CalibrationPnL.jsx must reference scatter / scatter_points.

test_calibration_pnl_jsx_renders_calibration_curve_section
    CalibrationPnL.jsx must reference calibration_curve / bucket_mid.

test_calibration_pnl_analytics_view_importable
    calibration_pnl_analytics_view is callable (H1 wiring guard, explicit form).
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# H1 ImportError trap — fail pytest COLLECTION if the backend API view is
# deleted or renamed.  Mirrors the standing H1 pattern (AC-11.3, AC-42.3,
# AC-51.3, AC-56.3, AC-57.1, AC-57.2, AC-57.3, AC-72.1).
# ---------------------------------------------------------------------------
from trading.analytics_api import calibration_pnl_analytics_view  # noqa: F401

assert calibration_pnl_analytics_view  # fails collection if None / ImportError

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_JSX = REPO_ROOT / "frontend" / "src" / "CalibrationPnL.jsx"
APP_JSX = REPO_ROOT / "frontend" / "src" / "App.jsx"


# ---------------------------------------------------------------------------
# 1. File existence
# ---------------------------------------------------------------------------


def test_calibration_pnl_jsx_exists():
    """CalibrationPnL.jsx must exist in frontend/src/ (US-48 React frontend, §14)."""
    assert COMPONENT_JSX.exists(), (
        f"CalibrationPnL.jsx not found at {COMPONENT_JSX}. "
        "The Calibration & PnL analytics view must be rendered in the US-48 React "
        "frontend (built fresh per §14), not as a server-rendered table."
    )


# ---------------------------------------------------------------------------
# 2. Metadata front matter
# ---------------------------------------------------------------------------


def test_calibration_pnl_jsx_has_metadata_front_matter():
    """CalibrationPnL.jsx must carry metadata front matter with US-72 story tag."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "// ---" in src, (
        "CalibrationPnL.jsx is missing the metadata front matter block (// ---). "
        "All new backend AND frontend files must carry metadata front matter "
        "(project convention, sprint DoD)."
    )
    assert "US-72" in src, (
        "CalibrationPnL.jsx front matter must reference US-72 (story tag)."
    )


# ---------------------------------------------------------------------------
# 3. H1 ImportError trap
# ---------------------------------------------------------------------------


def test_calibration_pnl_jsx_has_import_error_trap():
    """CalibrationPnL.jsx must contain an H1 ImportError trap (throw new Error)."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "ImportError" in src or "throw new Error" in src, (
        "CalibrationPnL.jsx is missing the H1 ImportError trap. "
        "Add a module-level guard: if (typeof useState !== 'function') { "
        "throw new Error('CalibrationPnL: React hooks unavailable ...') }"
    )


# ---------------------------------------------------------------------------
# 4–5. App.jsx routing
# ---------------------------------------------------------------------------


def test_app_jsx_imports_calibration_pnl():
    """App.jsx must import CalibrationPnL (US-48 React frontend routing)."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "CalibrationPnL" in src, (
        "App.jsx does not import CalibrationPnL. The Calibration & PnL analytics view "
        "must be wired into the US-48 React frontend router (view=calibration)."
    )


def test_app_jsx_routes_calibration_view():
    """App.jsx must route view='calibration' to the CalibrationPnL component."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "'calibration'" in src or '"calibration"' in src, (
        "App.jsx does not contain a 'calibration' view route. "
        "The Calibration & PnL view must be reachable via ?view=calibration."
    )
    assert "CalibrationPnL" in src, (
        "App.jsx does not render CalibrationPnL anywhere. "
        "The view=calibration route must render the CalibrationPnL component."
    )


# ---------------------------------------------------------------------------
# 6–9. Four analytics surface checks
# ---------------------------------------------------------------------------


def test_calibration_pnl_jsx_renders_win_rate_section():
    """CalibrationPnL.jsx must reference win_rate_by_score_band / Win Rate."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "win_rate" in src or "Win Rate" in src or "win-rate" in src.lower(), (
        "CalibrationPnL.jsx must render the Win Rate by Score Band analytic. "
        "Expected to find 'win_rate', 'Win Rate', or 'win-rate' in the source."
    )


def test_calibration_pnl_jsx_renders_pnl_by_trigger_section():
    """CalibrationPnL.jsx must reference exit_trigger / pnl_by_exit_trigger."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "exit_trigger" in src or "Exit Trigger" in src or "pnl_by_exit" in src, (
        "CalibrationPnL.jsx must render the Realized PnL by Exit Trigger analytic. "
        "Expected to find 'exit_trigger', 'Exit Trigger', or 'pnl_by_exit' in the source."
    )


def test_calibration_pnl_jsx_renders_scatter_section():
    """CalibrationPnL.jsx must reference scatter / scatter_points."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "scatter" in src.lower() or "scatter_points" in src, (
        "CalibrationPnL.jsx must render the Score vs Actual PnL Scatter analytic. "
        "Expected to find 'scatter' or 'scatter_points' in the source."
    )


def test_calibration_pnl_jsx_renders_calibration_curve_section():
    """CalibrationPnL.jsx must reference calibration_curve / bucket_mid."""
    assert COMPONENT_JSX.exists(), f"CalibrationPnL.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "calibration_curve" in src or "Calibration Curve" in src or "bucket_mid" in src, (
        "CalibrationPnL.jsx must render the Calibration Curve analytic. "
        "Expected to find 'calibration_curve', 'Calibration Curve', or 'bucket_mid' in the source."
    )


# ---------------------------------------------------------------------------
# 10. Backend API view importable — explicit test form
# ---------------------------------------------------------------------------


def test_calibration_pnl_analytics_view_importable():
    """calibration_pnl_analytics_view is importable and callable (H1 wiring guard)."""
    assert callable(calibration_pnl_analytics_view), (
        "calibration_pnl_analytics_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )
