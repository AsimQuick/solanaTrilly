# ---
# module: trading.urls
# sprint: sprint-13, sprint-14
# story: US-69 AC-69.2, US-72 AC-72.1, US-73 AC-73.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: django, trading.api, trading.analytics_api, trading.replay_api
# ---
"""URL routing for the trading DRF API."""

from django.urls import path

from trading.analytics_api import calibration_pnl_analytics_view
from trading.api import positions_closed_view, positions_open_view
from trading.replay_api import replay_overlay_view

urlpatterns = [
    path("api/trading/positions/open/", positions_open_view, name="trading_positions_open"),
    path("api/trading/positions/closed/", positions_closed_view, name="trading_positions_closed"),
    path(
        "api/trading/analytics/calibration-pnl/",
        calibration_pnl_analytics_view,
        name="trading_analytics_calibration_pnl",
    ),
    path(
        "api/trading/replay/overlay/",
        replay_overlay_view,
        name="trading_replay_overlay",
    ),
]
