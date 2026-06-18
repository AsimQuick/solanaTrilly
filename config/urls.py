# ---
# module: config.urls
# sprint: pre-sprint, sprint-12
# story: setup, US-63 AC-63.1
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-18
# dependencies: django, core, copytrade
# ---
"""Root URL configuration."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include("copytrade.urls")),
]
