# ---
# module: trading.urls
# sprint: sprint-13
# story: US-69 AC-69.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, trading.api
# ---
"""URL routing for the trading DRF API (AC-69.2 — Live Positions board)."""

from django.urls import path

from trading.api import positions_closed_view, positions_open_view

urlpatterns = [
    path("api/trading/positions/open/", positions_open_view, name="trading_positions_open"),
    path("api/trading/positions/closed/", positions_closed_view, name="trading_positions_closed"),
]
