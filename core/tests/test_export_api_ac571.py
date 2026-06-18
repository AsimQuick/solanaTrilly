# ---
# module: core.tests.test_export_api_ac571
# sprint: sprint-11
# story: US-57 AC-57.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.export_api, core.tasks, core.models, ast, pytest, rest_framework
# ---
"""AC-57.1 — Feature Builder UI: DRF endpoint delegates to the US-31 build_features task.

The AC requires:
  - A DRF endpoint triggers the EXISTING §6.5 one-click labeled export (US-31, NO new math)
  - The UI invokes the existing export Celery task (core.tasks.build_features)
  - The task runs OFF the celery container (NEVER web/gunicorn, #289)
  - Export parameters (feature_set_id, label_def) are config-driven (Principle #1)
  - Verified by pytest/AST that the view delegates to the US-31 task (not re-implemented)
  - Verified that triggering is a Celery task dispatch (.delay()), not a web-view-side export

Tests
-----
H1 ImportError trap — module-level wiring guards:
  (import) feature_export_trigger_view from core.export_api
  (import) build_features from core.tasks

test_build_features_is_us31_celery_task
    build_features.name == "core.tasks.build_features" (the US-31 registered task name).

test_build_features_has_celery_task_interface
    build_features has .delay, .apply, .apply_async (Celery task interface,
    not a plain function).

test_export_api_uses_celery_delay_not_inline
    AST: core/export_api.py source contains a .delay( call on build_features —
    the triggering is a Celery task dispatch, never an inline call on the web container.

test_export_api_does_not_reimplement_extraction
    AST: core/export_api.py does NOT directly call build_features_core, FeatureExtractor,
    or extract_from_lake — confirming no new export math in the view (US-31 delegates).

test_export_api_is_config_driven
    AST: core/export_api.py imports from core.resolver (get_active_config) and reads
    from PipelineConfig — export parameters are config-driven (Principle #1).

test_build_features_not_in_views
    AST: core/views.py does NOT define a build_features function or export trigger
    — the export never runs on the web/gunicorn process (#289).

test_export_trigger_url_registered
    core/urls.py contains the /api/export/trigger/ path wired to feature_export_trigger_view
    — the H1 ImportError trap fires at collection time if the view is deleted/renamed,
    and this test pins the URL route name for regression.

test_feature_export_trigger_view_requires_post
    The endpoint rejects GET requests with HTTP 405 Method Not Allowed (DRF behaviour).

test_feature_export_trigger_view_400_when_no_feature_set_id
    When the active PipelineConfig has no feature_set_id set, the view returns HTTP 400
    with a clear error message — no silent export with unknown parameters.

test_feature_export_trigger_view_queues_task_and_returns_task_id
    With a valid PipelineConfig (feature_set_id set), the view returns HTTP 200 with
    task_id, dataset_id, feature_set_id, and status='queued' — Celery is mocked so
    the test is OFFLINE (zero firehose, no celery worker needed).
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

# ---------------------------------------------------------------------------
# H1 ImportError traps — fail pytest COLLECTION if view or task are removed/renamed
# ---------------------------------------------------------------------------
from core.export_api import feature_export_trigger_view  # noqa: F401
from core.tasks import build_features  # noqa: F401

assert feature_export_trigger_view  # fails collection if None / ImportError
assert build_features  # fails collection if None / ImportError

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_API_PATH = REPO_ROOT / "core" / "export_api.py"
VIEWS_PATH = REPO_ROOT / "core" / "views.py"
TASKS_PATH = REPO_ROOT / "core" / "tasks.py"
URLS_PATH = REPO_ROOT / "core" / "urls.py"


# ---------------------------------------------------------------------------
# 1. build_features is the US-31 registered Celery task
# ---------------------------------------------------------------------------


def test_build_features_is_us31_celery_task():
    """The registered Celery task name must be 'core.tasks.build_features' (US-31)."""
    assert build_features.name == "core.tasks.build_features", (
        f"Expected task name 'core.tasks.build_features', got {build_features.name!r}. "
        "The view must delegate to the existing US-31 task, not a re-implementation."
    )


# ---------------------------------------------------------------------------
# 2. build_features has the Celery task interface
# ---------------------------------------------------------------------------


def test_build_features_has_celery_task_interface():
    """build_features must have .delay, .apply, .apply_async (Celery task interface, not a plain fn)."""
    for attr in ("delay", "apply", "apply_async"):
        assert hasattr(build_features, attr), (
            f"build_features is missing Celery task attribute: {attr!r}. "
            "Triggering must be a Celery dispatch, not an inline call (#289)."
        )


# ---------------------------------------------------------------------------
# 3. AST: export_api.py uses .delay( — Celery dispatch, not inline call
# ---------------------------------------------------------------------------


def test_export_api_uses_celery_delay_not_inline():
    """export_api.py source must contain build_features.delay( — a Celery dispatch, not inline."""
    assert EXPORT_API_PATH.exists(), f"core/export_api.py not found at {EXPORT_API_PATH}"
    src = EXPORT_API_PATH.read_text(encoding="utf-8")

    assert "build_features.delay(" in src, (
        "core/export_api.py must call build_features.delay() to dispatch to the celery-worker. "
        "An inline call (build_features(...)) would run the export on web/gunicorn, "
        "violating the #289 lesson (NEVER on web/gunicorn)."
    )


# ---------------------------------------------------------------------------
# 4. AST: export_api.py does NOT re-implement extraction
# ---------------------------------------------------------------------------


def test_export_api_does_not_reimplement_extraction():
    """export_api.py must NOT call build_features_core, FeatureExtractor, or extract_from_lake.

    If any of these appear in the source, new export math has been re-implemented in
    the view layer (Principle #2 violation — no new export math; US-31 task owns all logic).
    """
    assert EXPORT_API_PATH.exists(), f"core/export_api.py not found at {EXPORT_API_PATH}"
    src = EXPORT_API_PATH.read_text(encoding="utf-8")

    forbidden = ["build_features_core", "FeatureExtractor", "extract_from_lake"]
    for symbol in forbidden:
        assert symbol not in src, (
            f"core/export_api.py must NOT reference '{symbol}'. "
            "The view must delegate entirely to the US-31 build_features Celery task. "
            "No new export math in the view layer (Principle #2)."
        )


# ---------------------------------------------------------------------------
# 5. AST: export_api.py is config-driven (imports from core.resolver)
# ---------------------------------------------------------------------------


def test_export_api_is_config_driven():
    """export_api.py must import from core.resolver and core.models (config-driven, Principle #1)."""
    assert EXPORT_API_PATH.exists(), f"core/export_api.py not found at {EXPORT_API_PATH}"
    src = EXPORT_API_PATH.read_text(encoding="utf-8")

    assert "get_active_config" in src, (
        "core/export_api.py must call get_active_config() to read export parameters "
        "(config-driven, Principle #1). Export destination and dataset_id must not be literals."
    )
    assert "PipelineConfig" in src, (
        "core/export_api.py must read feature_set_id from PipelineConfig "
        "(config-driven, Principle #1)."
    )


# ---------------------------------------------------------------------------
# 6. AST: build_features is a shared_task in tasks.py (not a view in views.py)
# ---------------------------------------------------------------------------


def test_build_features_not_in_views():
    """core/views.py must NOT define build_features or a feature export trigger.

    The export trigger NEVER runs on the web/gunicorn process (#289 lesson).
    """
    assert VIEWS_PATH.exists(), f"core/views.py not found at {VIEWS_PATH}"
    src = VIEWS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(VIEWS_PATH))

    forbidden_in_views = {"build_features", "feature_export_trigger_view", "build_features_core"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in forbidden_in_views, (
                f"'{node.name}' must NOT be defined in core/views.py. "
                "Feature export must run on the celery-worker, NEVER web/gunicorn (#289)."
            )


# ---------------------------------------------------------------------------
# 7. URL route is registered in core/urls.py
# ---------------------------------------------------------------------------


def test_export_trigger_url_registered():
    """core/urls.py must import feature_export_trigger_view and route /api/export/trigger/."""
    assert URLS_PATH.exists(), f"core/urls.py not found at {URLS_PATH}"
    src = URLS_PATH.read_text(encoding="utf-8")

    assert "feature_export_trigger_view" in src, (
        "core/urls.py must import and register feature_export_trigger_view "
        "(H1 ImportError trap: deletion/rename fails pytest collection)."
    )
    assert "api/export/trigger/" in src, (
        "core/urls.py must contain the path 'api/export/trigger/' wired to "
        "feature_export_trigger_view."
    )


# ---------------------------------------------------------------------------
# 8. Endpoint rejects GET (DRF @api_view(["POST"]) behaviour)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_export_trigger_view_requires_post():
    """GET /api/export/trigger/ must return HTTP 405 Method Not Allowed."""
    client = APIClient()
    resp = client.get("/api/export/trigger/")
    assert resp.status_code == 405, (
        f"Expected 405 for GET on /api/export/trigger/, got {resp.status_code}. "
        "The endpoint must only accept POST (DRF @api_view(['POST']))."
    )


# ---------------------------------------------------------------------------
# 9. HTTP 400 when no feature_set_id on the active config
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_export_trigger_view_400_when_no_feature_set_id():
    """When the active PipelineConfig has no feature_set_id, the view returns HTTP 400."""
    from core.models import PipelineConfig

    # Ensure there is an active config without feature_set_id
    PipelineConfig.objects.filter(is_active=True).update(feature_set_id=None)
    # Also make sure at least one active config exists with feature_set_id=None
    if not PipelineConfig.objects.filter(is_active=True).exists():
        PipelineConfig.objects.create(
            version=1,
            label="test",
            is_active=True,
            feature_set_id=None,
            detection={},
            tape={"idle_kill_ttl_s": 300},
            scoring={"score_at_elapsed_s": 60, "window_s": 120},
            outcome={"window_s": 120},
            trading={},
        )

    client = APIClient()
    resp = client.post("/api/export/trigger/", data={}, format="json")
    assert resp.status_code == 400, (
        f"Expected HTTP 400 when no feature_set_id is set on the active config, "
        f"got {resp.status_code}."
    )
    data = resp.json()
    assert "error" in data, "Response body must contain an 'error' key explaining the 400."


# ---------------------------------------------------------------------------
# 10. HTTP 200 + task_id returned when feature_set_id is set (Celery mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_export_trigger_view_queues_task_and_returns_task_id():
    """With a valid PipelineConfig (feature_set_id set), the view dispatches via .delay() and
    returns task_id, dataset_id, feature_set_id, and status='queued'. Celery is mocked so
    the test is OFFLINE (no celery worker or firehose activation).
    """
    from core.models import PipelineConfig

    # Create an active config with a feature_set_id
    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    config = PipelineConfig.objects.create(
        version=571,
        label="ac571-test",
        is_active=True,
        feature_set_id=42,
        detection={},
        tape={"idle_kill_ttl_s": 300},
        scoring={"score_at_elapsed_s": 60, "window_s": 120},
        outcome={"window_s": 120, "label_def": {"label_start_s": 120}},
        trading={},
    )

    fake_task_id = "test-task-id-ac571"
    mock_async_result = MagicMock()
    mock_async_result.id = fake_task_id

    with patch("core.tasks.build_features") as mock_task:
        # build_features is imported lazily inside the view; patch at the source module
        mock_task.delay.return_value = mock_async_result

        client = APIClient()
        resp = client.post("/api/export/trigger/", data={}, format="json")

    assert resp.status_code == 200, (
        f"Expected HTTP 200 when feature_set_id is configured, got {resp.status_code}. "
        f"Response: {resp.content}"
    )
    data = resp.json()

    assert "task_id" in data, "Response must contain 'task_id'."
    assert "dataset_id" in data, "Response must contain 'dataset_id'."
    assert "feature_set_id" in data, "Response must contain 'feature_set_id'."
    assert data["status"] == "queued", (
        f"Expected status='queued', got {data['status']!r}. "
        "The task is dispatched to Celery, not completed synchronously."
    )
    assert data["feature_set_id"] == 42, (
        f"Expected feature_set_id=42 (from active config), got {data['feature_set_id']!r}."
    )
    assert "42" in data["dataset_id"], (
        f"dataset_id must be derived from feature_set_id=42, got {data['dataset_id']!r}."
    )

    # Confirm .delay() was called (not build_features_core, not inline)
    mock_task.delay.assert_called_once()
    call_kwargs = mock_task.delay.call_args

    # feature_set_id must be passed as 42 (from active config — config-driven, Principle #1)
    args, kwargs = call_kwargs
    all_kwargs = {}
    if args:
        # positional: build_features(feature_set_id, mint_cohort, label_def, ...)
        param_names = ["feature_set_id", "mint_cohort", "label_def"]
        all_kwargs = dict(zip(param_names, args))
    all_kwargs.update(kwargs)

    assert all_kwargs.get("feature_set_id") == 42, (
        f"build_features.delay() must be called with feature_set_id=42 (config-driven), "
        f"got: {all_kwargs}"
    )

    # Clean up
    config.delete()
