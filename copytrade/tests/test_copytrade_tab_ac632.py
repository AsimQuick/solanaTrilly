# ---
# module: copytrade.tests.test_copytrade_tab_ac632
# sprint: sprint-12
# story: US-63 AC-63.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.api, pathlib, pytest
# ---
"""AC-63.2 — Copy Trade tab frontend wiring checks.

The AC requires:
  - A new 'Copy Trade' tab built FRESH in the US-48 React frontend (§14).
  - Reusing the dashboard component library; routed like the existing cohort/
    control/features views in App.jsx.
  - Renders the SPEC §8 sections (top bar, per-wallet PnL table, open positions,
    recent trades log, cohort summary).
  - The per-wallet PnL table is present and SORTABLE by PnL.
  - Honors §1/§8 NON-goals: NO candle charts/TA, NO per-row action buttons,
    NO per-wallet toggles.
  - All new frontend files carry metadata front matter (project convention).

Tests
-----
H1 ImportError traps (module level — fail pytest COLLECTION if any API surface
is deleted or renamed):
  from copytrade.api import copytrade_pnl_view
  from copytrade.api import copytrade_positions_view
  from copytrade.api import copytrade_trades_view
  from copytrade.api import copytrade_summary_view
  from copytrade.api import copytrade_upload_view
  from copytrade.api import copytrade_engine_view
  from copytrade.api import copytrade_mode_view
  from copytrade.api import copytrade_overrides_view

Frontend wiring checks (reads frontend/src/* as text — structural/CI-safe):
  test_copytrade_tab_jsx_exists
  test_copytrade_tab_jsx_has_metadata_front_matter
  test_copytrade_tab_jsx_references_us63
  test_app_jsx_imports_copytrade_tab
  test_app_jsx_routes_copytrade_view
  test_copytrade_tab_pnl_table_present
  test_copytrade_tab_pnl_table_is_sortable
  test_copytrade_tab_no_candle_chart_import
  test_copytrade_tab_no_per_wallet_toggle
  test_copytrade_tab_top_bar_engine_toggle
  test_copytrade_tab_top_bar_mode_toggle
  test_copytrade_tab_top_bar_upload_json
  test_copytrade_tab_top_bar_status_chips
  test_copytrade_tab_open_positions_section
  test_copytrade_tab_recent_trades_section
  test_copytrade_tab_cohort_summary_section
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# H1 ImportError traps — fail pytest COLLECTION if any API surface is
# deleted or renamed.  Mirrors the standing H1 pattern (AC-11.3, AC-31.1,
# AC-42.3, AC-51.3, AC-56.3, AC-57.1, AC-63.1).
# ---------------------------------------------------------------------------
from copytrade.api import (
    copytrade_engine_view,  # noqa: F401
    copytrade_mode_view,  # noqa: F401
    copytrade_overrides_view,  # noqa: F401
    copytrade_pnl_view,  # noqa: F401
    copytrade_positions_view,  # noqa: F401
    copytrade_summary_view,  # noqa: F401
    copytrade_trades_view,  # noqa: F401
    copytrade_upload_view,  # noqa: F401
)

# Fail collection immediately if any surface is missing
assert copytrade_pnl_view, "copytrade_pnl_view must be importable (H1 guard)"
assert copytrade_positions_view, "copytrade_positions_view must be importable (H1 guard)"
assert copytrade_trades_view, "copytrade_trades_view must be importable (H1 guard)"
assert copytrade_summary_view, "copytrade_summary_view must be importable (H1 guard)"
assert copytrade_upload_view, "copytrade_upload_view must be importable (H1 guard)"
assert copytrade_engine_view, "copytrade_engine_view must be importable (H1 guard)"
assert copytrade_mode_view, "copytrade_mode_view must be importable (H1 guard)"
assert copytrade_overrides_view, "copytrade_overrides_view must be importable (H1 guard)"

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
COPYTRADE_TAB_JSX = REPO_ROOT / "frontend" / "src" / "CopyTradeTab.jsx"
APP_JSX = REPO_ROOT / "frontend" / "src" / "App.jsx"


# ---------------------------------------------------------------------------
# H1 explicit test forms
# ---------------------------------------------------------------------------


def test_h1_copytrade_pnl_view_importable():
    """copytrade_pnl_view is callable (H1 wiring guard, explicit form)."""
    assert callable(copytrade_pnl_view), (
        "copytrade_pnl_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_copytrade_upload_view_importable():
    """copytrade_upload_view is callable (H1 wiring guard, explicit form)."""
    assert callable(copytrade_upload_view), (
        "copytrade_upload_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_copytrade_engine_view_importable():
    """copytrade_engine_view is callable (H1 wiring guard, explicit form)."""
    assert callable(copytrade_engine_view), (
        "copytrade_engine_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


# ---------------------------------------------------------------------------
# Frontend wiring checks — structural, read-only (no DB, no network)
# ---------------------------------------------------------------------------


def test_copytrade_tab_jsx_exists():
    """CopyTradeTab.jsx must exist in frontend/src/ (US-48 React frontend, §14)."""
    assert COPYTRADE_TAB_JSX.exists(), (
        f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}. "
        "The Copy Trade tab must be built FRESH in the US-48 React frontend (§14), "
        "not as a server-rendered page."
    )


def test_copytrade_tab_jsx_has_metadata_front_matter():
    """CopyTradeTab.jsx must carry the metadata front matter block (project convention)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "// ---" in src, (
        "CopyTradeTab.jsx is missing the metadata front matter block (// ---). "
        "All new backend AND frontend files must carry metadata front matter "
        "(project convention, sprint DoD)."
    )


def test_copytrade_tab_jsx_references_us63():
    """CopyTradeTab.jsx front matter must reference US-63 (story tag)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "US-63" in src, (
        "CopyTradeTab.jsx front matter must reference US-63 (story tag). "
        "All new frontend files must carry metadata front matter with the story ID "
        "(project convention)."
    )


def test_app_jsx_imports_copytrade_tab():
    """App.jsx must import CopyTradeTab (US-48 React frontend routing, §14)."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "CopyTradeTab" in src, (
        "App.jsx does not import CopyTradeTab. The Copy Trade tab must be wired "
        "into the US-48 React frontend router (view=copytrade) like the existing "
        "cohort/control/features views."
    )


def test_app_jsx_routes_copytrade_view():
    """App.jsx must route view=copytrade to the CopyTradeTab component."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "'copytrade'" in src or '"copytrade"' in src, (
        "App.jsx does not contain a 'copytrade' view route. "
        "The Copy Trade tab must be reachable via ?view=copytrade in the US-48 frontend "
        "(routed like the existing cohort/control/features views)."
    )
    assert "CopyTradeTab" in src, (
        "App.jsx does not render CopyTradeTab anywhere. "
        "The view=copytrade route must render the CopyTradeTab component."
    )


def test_copytrade_tab_pnl_table_present():
    """CopyTradeTab.jsx must contain the per-wallet PnL table (SPEC §8 section 2)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    # The PnL table must reference wallet PnL fields
    assert "total_pnl_sol" in src, (
        "CopyTradeTab.jsx must contain the per-wallet PnL table rendering 'total_pnl_sol'. "
        "SPEC §8 section 2 requires the KEY per-wallet PnL table with total realized PnL."
    )
    assert "win_rate" in src, (
        "CopyTradeTab.jsx must render 'win_rate' in the per-wallet PnL table. "
        "SPEC §8 section 2 requires: address, #trades, win-rate, total PnL SOL & %, avg hold, last-trigger."
    )
    assert "n_trades" in src, (
        "CopyTradeTab.jsx must render 'n_trades' in the per-wallet PnL table. "
        "SPEC §8 section 2 requires #trades column."
    )
    assert "avg_hold" in src.lower(), (
        "CopyTradeTab.jsx must render avg hold in the per-wallet PnL table. "
        "SPEC §8 section 2 requires avg hold column."
    )


def test_copytrade_tab_pnl_table_is_sortable():
    """CopyTradeTab.jsx per-wallet PnL table must be sortable by PnL (AC-63.2)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    # Sortable table requires sort state
    assert "sortCol" in src or "sort_col" in src, (
        "CopyTradeTab.jsx must implement sortable columns (per-wallet PnL table must "
        "be sortable by PnL per AC-63.2). Expected sort column state (sortCol) not found."
    )
    assert "sortDir" in src or "sort_dir" in src, (
        "CopyTradeTab.jsx must implement sort direction state (sortDir) for the "
        "sortable per-wallet PnL table (AC-63.2)."
    )
    assert "handleSort" in src or "setSortCol" in src, (
        "CopyTradeTab.jsx must implement a sort handler (handleSort or setSortCol) for "
        "the sortable per-wallet PnL table (AC-63.2). Clicking a column header should "
        "change the sort order."
    )


def test_copytrade_tab_no_candle_chart_import():
    """CopyTradeTab.jsx must NOT import lightweight-charts or any candle-chart library.

    SPEC §1/§8 NON-goals: NO candle charts/TA in the Copy Trade tab.
    The existing candle-chart infrastructure (TokenDetail.jsx) must not leak into
    the copy-trade view.
    """
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "lightweight-charts" not in src, (
        "CopyTradeTab.jsx imports 'lightweight-charts'. "
        "SPEC §1/§8 NON-goals: NO candle charts / TA in the Copy Trade tab. "
        "Remove any lightweight-charts import from CopyTradeTab.jsx."
    )
    assert "createChart" not in src, (
        "CopyTradeTab.jsx references 'createChart'. "
        "SPEC §1/§8 NON-goals: NO candle charts / TA in the Copy Trade tab. "
        "The copy-trade view must not include any charting library calls."
    )
    assert "IChartApi" not in src, (
        "CopyTradeTab.jsx references 'IChartApi' (lightweight-charts type). "
        "SPEC §1/§8 NON-goals: NO candle charts / TA in the Copy Trade tab."
    )


def test_copytrade_tab_no_per_wallet_toggle():
    """CopyTradeTab.jsx must NOT render per-wallet toggles (SPEC §1/§8 NON-goals)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    # Per-wallet toggle would be an engine toggle on a per-row basis; the global
    # ON/OFF is fine, but individual wallet pause/enable buttons are NOT allowed.
    # We check the per-row mapped section does not have wallet-specific engine calls.
    # The per-wallet PnL table rows must not each have a toggle/pause button.
    assert "wallet_on" not in src and "walletOn" not in src, (
        "CopyTradeTab.jsx appears to have per-wallet ON/OFF toggle state (wallet_on / "
        "walletOn). SPEC §1/§8 NON-goals: NO per-wallet toggles in v1. Only the global "
        "engine ON/OFF is allowed."
    )


def test_copytrade_tab_top_bar_engine_toggle():
    """CopyTradeTab.jsx must render the ON/OFF master engine toggle (SPEC §8 section 1)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "engine_on" in src or "engineOn" in src, (
        "CopyTradeTab.jsx must render the ON/OFF master engine toggle (SPEC §8 top bar). "
        "Expected engine_on/engineOn state reference."
    )
    assert "/api/copytrade/engine/" in src, (
        "CopyTradeTab.jsx must call /api/copytrade/engine/ for the ON/OFF toggle. "
        "The engine toggle must POST to the DRF endpoint (AC-63.1)."
    )


def test_copytrade_tab_top_bar_mode_toggle():
    """CopyTradeTab.jsx must render the Observe/Live mode toggle (SPEC §8 section 1)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "observe" in src.lower(), (
        "CopyTradeTab.jsx must render the Observe/Live mode toggle. "
        "Expected 'observe' mode reference (SPEC §8 top bar)."
    )
    assert "/api/copytrade/mode/" in src, (
        "CopyTradeTab.jsx must call /api/copytrade/mode/ for the mode toggle. "
        "The mode toggle must POST to the DRF endpoint (AC-63.1)."
    )


def test_copytrade_tab_top_bar_upload_json():
    """CopyTradeTab.jsx must render an Upload JSON control (SPEC §8 section 1)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "Upload JSON" in src or "upload" in src.lower(), (
        "CopyTradeTab.jsx must render an Upload JSON control (SPEC §8 top bar). "
        "Expected 'Upload JSON' label or upload-related reference."
    )
    assert "/api/copytrade/upload/" in src, (
        "CopyTradeTab.jsx must call /api/copytrade/upload/ for the JSON upload. "
        "The upload action must POST to the DRF endpoint (AC-63.1)."
    )
    # Wipe confirm must be present (§6 wipe confirm)
    assert "confirm" in src.lower() or "wipe" in src.lower(), (
        "CopyTradeTab.jsx must include a wipe confirmation dialog for the JSON upload "
        "(§6: uploading a new cohort JSON WIPES the old cohort). "
        "Expected a confirm() call or wipe warning text."
    )


def test_copytrade_tab_top_bar_status_chips():
    """CopyTradeTab.jsx must render status chips (engine state, mode, # wallets,
    # open positions, cohort_id, created_at) per SPEC §8 section 1."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    # Check for the key status fields surfaced in chips
    assert "n_wallets" in src or "nWallets" in src, (
        "CopyTradeTab.jsx must display # wallets status chip (SPEC §8 top bar). "
        "Expected n_wallets/nWallets reference."
    )
    assert "n_open_positions" in src or "nOpen" in src, (
        "CopyTradeTab.jsx must display # open positions status chip (SPEC §8 top bar). "
        "Expected n_open_positions/nOpen reference."
    )
    assert "cohort_id" in src or "cohortId" in src, (
        "CopyTradeTab.jsx must display cohort_id status chip (SPEC §8 top bar). "
        "Expected cohort_id/cohortId reference."
    )


def test_copytrade_tab_open_positions_section():
    """CopyTradeTab.jsx must render the open positions section (SPEC §8 section 3)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "/api/copytrade/positions/" in src, (
        "CopyTradeTab.jsx must fetch open positions from /api/copytrade/positions/. "
        "SPEC §8 section 3 requires an open positions table."
    )
    assert "Open Position" in src or "openPosition" in src or "positions" in src.lower(), (
        "CopyTradeTab.jsx must render an open positions section (SPEC §8 section 3). "
        "Expected 'Open Positions' label or positions-related reference."
    )


def test_copytrade_tab_recent_trades_section():
    """CopyTradeTab.jsx must render the recent trades log (SPEC §8 section 4)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "/api/copytrade/trades/" in src, (
        "CopyTradeTab.jsx must fetch recent trades from /api/copytrade/trades/. "
        "SPEC §8 section 4 requires a recent trades log."
    )
    assert "exit_reason" in src, (
        "CopyTradeTab.jsx must display exit_reason in the recent trades log "
        "(TP/SL/CURVE/TIMER — SPEC §8 section 4)."
    )


def test_copytrade_tab_cohort_summary_section():
    """CopyTradeTab.jsx must render the cohort summary (SPEC §8 section 5)."""
    assert COPYTRADE_TAB_JSX.exists(), f"CopyTradeTab.jsx not found at {COPYTRADE_TAB_JSX}"
    src = COPYTRADE_TAB_JSX.read_text(encoding="utf-8")
    assert "/api/copytrade/summary/" in src, (
        "CopyTradeTab.jsx must fetch cohort summary from /api/copytrade/summary/. "
        "SPEC §8 section 5 requires a cohort summary."
    )
    assert "net_pnl_sol" in src or "netPnl" in src, (
        "CopyTradeTab.jsx must display net PnL SOL in the cohort summary "
        "(SPEC §8 section 5: total trades, overall win-rate, net PnL SOL, since)."
    )
    assert "total_trades" in src or "totalTrades" in src, (
        "CopyTradeTab.jsx must display total trades in the cohort summary "
        "(SPEC §8 section 5)."
    )
