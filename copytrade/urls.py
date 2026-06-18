# ---
# module: copytrade.urls
# sprint: sprint-12
# story: US-63 AC-63.1, AC-63.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, copytrade.api
# ---
"""URL routing for the copytrade DRF API (AC-63.1 + AC-63.3 export)."""

from django.urls import path

from copytrade.api import (
    copytrade_engine_view,
    copytrade_export_trigger_view,
    copytrade_mode_view,
    copytrade_overrides_view,
    copytrade_pnl_view,
    copytrade_positions_view,
    copytrade_summary_view,
    copytrade_trades_view,
    copytrade_upload_view,
)

urlpatterns = [
    # GET — read endpoints
    path("api/copytrade/pnl/", copytrade_pnl_view, name="copytrade_pnl"),
    path("api/copytrade/positions/", copytrade_positions_view, name="copytrade_positions"),
    path("api/copytrade/trades/", copytrade_trades_view, name="copytrade_trades"),
    path("api/copytrade/summary/", copytrade_summary_view, name="copytrade_summary"),
    # POST — action endpoints
    path("api/copytrade/upload/", copytrade_upload_view, name="copytrade_upload"),
    path("api/copytrade/engine/", copytrade_engine_view, name="copytrade_engine"),
    path("api/copytrade/mode/", copytrade_mode_view, name="copytrade_mode"),
    path("api/copytrade/overrides/", copytrade_overrides_view, name="copytrade_overrides"),
    # POST — export (AC-63.3 / SPEC §11) — dispatches to celery-worker
    path("api/copytrade/export/trigger/", copytrade_export_trigger_view, name="copytrade_export_trigger"),
]
