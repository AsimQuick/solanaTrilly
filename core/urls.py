# ---
# module: core.urls
# sprint: pre-sprint, sprint-10
# story: setup, US-48 AC-48.3, US-49 AC-49.1, US-49 AC-49.2
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-17
# dependencies: django, core
# ---
from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/candles/<str:mint>/", views.candle_api, name="candle_api"),
    path("api/token-detail/<str:mint>/", views.token_detail_api, name="token_detail_api"),
]
