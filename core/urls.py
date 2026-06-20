# ---
# module: core.urls
# sprint: pre-sprint, sprint-10, sprint-11
# story: setup, US-48 AC-48.3, US-49 AC-49.1, US-49 AC-49.2, US-50 AC-50.1, US-51 AC-51.1,
#        US-56 AC-56.1 AC-56.2, US-57 AC-57.1 AC-57.2
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-18
# dependencies: django, core
# ---
from django.urls import path

from . import views
from .control_api import (
    config_activate_view,
    config_control_view,
    config_detail_view,
    config_diff_view,
    config_history_view,
    registry_activate_view,
    registry_control_view,
    registry_detail_view,
    registry_diff_view,
    registry_history_view,
)
from .data_contract_api import data_contract_export_trigger_view
from .export_api import feature_export_trigger_view
from .export_result_api import export_result_view

urlpatterns = [
    path("health/", views.health, name="health"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/candles/<str:mint>/", views.candle_api, name="candle_api"),
    path("api/token-detail/<str:mint>/", views.token_detail_api, name="token_detail_api"),
    path("api/cohort/", views.cohort_api, name="cohort_api"),
    path("api/annotations/<str:mint>/", views.annotation_list, name="annotation_list"),
    path("api/annotations/<str:mint>/create/", views.annotation_create, name="annotation_create"),
    # US-56 AC-56.1: Config & model control read-only API
    # diff/ must come before <int:pk>/ to avoid routing conflicts
    path("api/control/config/diff/", config_diff_view, name="config_diff"),
    path("api/control/config/", config_control_view, name="config_control"),
    path("api/control/config/<int:pk>/", config_detail_view, name="config_detail"),
    path("api/control/config/<int:pk>/history/", config_history_view, name="config_history"),
    path("api/control/registry/diff/", registry_diff_view, name="registry_diff"),
    path("api/control/registry/", registry_control_view, name="registry_control"),
    path("api/control/registry/<int:pk>/", registry_detail_view, name="registry_detail"),
    path("api/control/registry/<int:pk>/history/", registry_history_view, name="registry_history"),
    # US-56 AC-56.2: Operator-gated activate actions
    path("api/control/config/<int:pk>/activate/", config_activate_view, name="config_activate"),
    path("api/control/registry/<int:pk>/activate/", registry_activate_view, name="registry_activate"),
    # US-57 AC-57.1: Feature Builder UI — trigger the US-31 §6.5 labeled export
    path("api/export/trigger/", feature_export_trigger_view, name="feature_export_trigger"),
    # US-78 AC-78.1/78.2: data-contract Parquet export (swaps + tokens surfaces)
    path(
        "api/export/data-contract/trigger/",
        data_contract_export_trigger_view,
        name="data_contract_export_trigger",
    ),
    # US-57 AC-57.2: Feature Builder UI — read back export result + MANIFEST
    path(
        "api/export/result/<str:task_id>/",
        export_result_view,
        name="export_result",
    ),
]
