# ---
# module: core.tests.test_replay_viewer_ac732
# sprint: sprint-14
# story: US-73 AC-73.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: trading.replay_api, pathlib, pytest
# ---
"""AC-73.2 — ReplayViewer.jsx frontend wiring checks.

The AC requires:
  - ReplayViewer.jsx exists in frontend/src/.
  - ReplayViewer.jsx carries metadata front matter with US-73 story tag.
  - ReplayViewer.jsx has an H1 ImportError trap (throw new Error).
  - ReplayViewer.jsx imports (reuses) the US-49 TokenDetail candle component.
  - App.jsx imports ReplayViewer.
  - App.jsx routes view='replay' to ReplayViewer.
  - ReplayViewer.jsx references entry/exit markers and exit_trigger labels.
  - The backend API view (replay_overlay_view) is importable (H1 trap).

Tests
-----
H1 ImportError trap (module-level — fails pytest COLLECTION if surface is deleted/renamed):
  from trading.replay_api import replay_overlay_view

test_replay_viewer_jsx_exists
    ReplayViewer.jsx must exist in frontend/src/.

test_replay_viewer_jsx_has_metadata_front_matter
    ReplayViewer.jsx must carry metadata front matter with US-73 story tag.

test_replay_viewer_jsx_has_import_error_trap
    ReplayViewer.jsx must contain an H1 ImportError trap (throw new Error).

test_replay_viewer_jsx_reuses_token_detail_candle_component
    ReplayViewer.jsx must import the US-49 TokenDetail candle component (not reimplement it).

test_app_jsx_imports_replay_viewer
    App.jsx must import ReplayViewer.

test_app_jsx_routes_replay_view
    App.jsx must route view='replay' to the ReplayViewer component.

test_replay_viewer_jsx_references_entry_marker
    ReplayViewer.jsx must reference entry markers (entry_ts or entry_price or entry marker text).

test_replay_viewer_jsx_references_exit_marker
    ReplayViewer.jsx must reference exit markers (exit_ts or exit_price or exit marker text).

test_replay_viewer_jsx_references_exit_trigger_label
    ReplayViewer.jsx must reference exit_trigger for labels.

test_replay_overlay_view_importable
    replay_overlay_view is callable (H1 wiring guard, explicit form).
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# H1 ImportError trap — fail pytest COLLECTION if the backend API view is
# deleted or renamed.  Mirrors the standing H1 pattern (AC-11.3, AC-42.3,
# AC-51.3, AC-56.3, AC-57.1, AC-57.2, AC-57.3, AC-72.1, AC-73.1).
# ---------------------------------------------------------------------------
from trading.replay_api import replay_overlay_view  # noqa: F401

assert replay_overlay_view  # fails collection if None / ImportError

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_JSX = REPO_ROOT / "frontend" / "src" / "ReplayViewer.jsx"
APP_JSX = REPO_ROOT / "frontend" / "src" / "App.jsx"
TOKEN_DETAIL_JSX = REPO_ROOT / "frontend" / "src" / "TokenDetail.jsx"


# ---------------------------------------------------------------------------
# 1. File existence
# ---------------------------------------------------------------------------


def test_replay_viewer_jsx_exists():
    """ReplayViewer.jsx must exist in frontend/src/ (US-48 React frontend, §14)."""
    assert COMPONENT_JSX.exists(), (
        f"ReplayViewer.jsx not found at {COMPONENT_JSX}. "
        "The Replay viewer position-open/close overlay must be rendered in the US-48 "
        "React frontend, not as a server-rendered table."
    )


# ---------------------------------------------------------------------------
# 2. Metadata front matter
# ---------------------------------------------------------------------------


def test_replay_viewer_jsx_has_metadata_front_matter():
    """ReplayViewer.jsx must carry metadata front matter with US-73 story tag."""
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "// ---" in src, (
        "ReplayViewer.jsx is missing the metadata front matter block (// ---). "
        "All new backend AND frontend files must carry metadata front matter "
        "(project convention, sprint DoD)."
    )
    assert "US-73" in src, (
        "ReplayViewer.jsx front matter must reference US-73 (story tag)."
    )


# ---------------------------------------------------------------------------
# 3. H1 ImportError trap
# ---------------------------------------------------------------------------


def test_replay_viewer_jsx_has_import_error_trap():
    """ReplayViewer.jsx must contain an H1 ImportError trap (throw new Error)."""
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "ImportError" in src or "throw new Error" in src, (
        "ReplayViewer.jsx is missing the H1 ImportError trap. "
        "Add a module-level guard: if (typeof useState !== 'function') { "
        "throw new Error('ReplayViewer: React hooks unavailable ...') }"
    )


# ---------------------------------------------------------------------------
# 4. US-49 TokenDetail candle component reuse
# ---------------------------------------------------------------------------


def test_replay_viewer_jsx_reuses_token_detail_candle_component():
    """ReplayViewer.jsx must import the US-49 TokenDetail candle component (not reimplement it).

    AC-73.2 mandates 'reusing the US-49 token-detail candle component' — this means
    an explicit import of TokenDetail from './TokenDetail.jsx', not a reimplementation
    of chart logic.  The TokenDetail component source must also exist.
    """
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    assert TOKEN_DETAIL_JSX.exists(), f"TokenDetail.jsx not found at {TOKEN_DETAIL_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "TokenDetail" in src, (
        "ReplayViewer.jsx does not import or render TokenDetail. "
        "AC-73.2 requires reusing the US-49 token-detail candle component — "
        "import TokenDetail from './TokenDetail.jsx' and render it per mint."
    )
    assert "TokenDetail.jsx" in src or "import TokenDetail" in src, (
        "ReplayViewer.jsx must explicitly import TokenDetail from './TokenDetail.jsx'. "
        "Found 'TokenDetail' in the source but no import statement."
    )


# ---------------------------------------------------------------------------
# 5–6. App.jsx routing
# ---------------------------------------------------------------------------


def test_app_jsx_imports_replay_viewer():
    """App.jsx must import ReplayViewer (US-48 React frontend routing)."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "ReplayViewer" in src, (
        "App.jsx does not import ReplayViewer. The Replay viewer must be wired into "
        "the US-48 React frontend router (view=replay)."
    )


def test_app_jsx_routes_replay_view():
    """App.jsx must route view='replay' to the ReplayViewer component."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "'replay'" in src or '"replay"' in src, (
        "App.jsx does not contain a 'replay' view route. "
        "The Replay viewer must be reachable via ?view=replay."
    )
    assert "ReplayViewer" in src, (
        "App.jsx does not render ReplayViewer anywhere. "
        "The view=replay route must render the ReplayViewer component."
    )


# ---------------------------------------------------------------------------
# 7–9. Overlay content checks (entry/exit markers + exit_trigger labels)
# ---------------------------------------------------------------------------


def test_replay_viewer_jsx_references_entry_marker():
    """ReplayViewer.jsx must reference entry markers (entry_ts or entry_price or 'entry')."""
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "entry_ts" in src or "entry_price" in src or "entry marker" in src.lower() or "entry" in src, (
        "ReplayViewer.jsx must render entry markers (entry_ts, entry_price, or an 'entry' "
        "label) for the position-open overlay (AC-73.2: entry/exit markers)."
    )


def test_replay_viewer_jsx_references_exit_marker():
    """ReplayViewer.jsx must reference exit markers (exit_ts or exit_price or 'exit')."""
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "exit_ts" in src or "exit_price" in src or "exit marker" in src.lower() or "exit" in src, (
        "ReplayViewer.jsx must render exit markers (exit_ts, exit_price, or an 'exit' "
        "label) for the position-close overlay (AC-73.2: entry/exit markers)."
    )


def test_replay_viewer_jsx_references_exit_trigger_label():
    """ReplayViewer.jsx must reference exit_trigger for labels (AC-73.2 requirement)."""
    assert COMPONENT_JSX.exists(), f"ReplayViewer.jsx not found at {COMPONENT_JSX}"
    src = COMPONENT_JSX.read_text(encoding="utf-8")
    assert "exit_trigger" in src or "Exit Trigger" in src, (
        "ReplayViewer.jsx must render exit_trigger labels on the overlay "
        "(e.g. STOP_LOSS, TAKE_PROFIT, RUG_PULL) as required by AC-73.2."
    )


# ---------------------------------------------------------------------------
# 10. Backend API view importable — explicit test form
# ---------------------------------------------------------------------------


def test_replay_overlay_view_importable():
    """replay_overlay_view is importable and callable (H1 wiring guard)."""
    assert callable(replay_overlay_view), (
        "replay_overlay_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )
