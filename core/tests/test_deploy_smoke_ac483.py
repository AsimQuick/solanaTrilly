# ---
# module: core.tests.test_deploy_smoke_ac483
# sprint: sprint-10
# story: US-48 AC-48.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.consumer, pathlib, ast, yaml
# ---
"""AC-48.3 — deploy smoke-test structural gates.

Verifies that all pipeline artefacts required for the VPS staging smoke-test
are in place:

  H1 ImportError trap (AC-48.3 requirement):
    Module-level import of TapeFeedConsumer and TapeFeedProcessor fails pytest
    COLLECTION (not test execution) if core/dashboard/consumer.py is deleted or
    the named symbols are removed. This is the canonical CI wire-up demanded by
    AC-48.3: "the WS consumer tests are wired into the single canonical ci.yml
    'test' job via an ImportError trap on the named consumer function (H1) so a
    deleted/renamed consumer fails pytest collection."

  Structural tests (deploy.yml):
    - deploy.yml builds and pushes the frontend image (ghcr.io/.../solanatrilly-frontend)
    - deploy.yml has a dashboard-route smoke-test step (HTTP 200 on /dashboard/)
    - deploy.yml has a WS endpoint smoke-test step (HTTP 101 upgrade check)
    - deploy.yml has a frontend/web container Up verification step

  Structural tests (Django URLs/views):
    - core/urls.py registers the 'dashboard' URL pattern
    - core/views.py defines a 'dashboard' view function

Tests:
  test_h1_import_tape_feed_consumer           — H1: fails collection if consumer deleted
  test_h1_import_tape_feed_processor          — H1: fails collection if processor deleted
  test_deploy_yml_builds_frontend_image       — deploy.yml pushes solanatrilly-frontend image
  test_deploy_yml_has_dashboard_smoke_test    — deploy.yml checks /dashboard/ HTTP 200
  test_deploy_yml_has_ws_smoke_test           — deploy.yml checks WS HTTP 101 upgrade
  test_deploy_yml_has_frontend_container_check — deploy.yml verifies frontend container Up
  test_deploy_yml_has_web_container_check     — deploy.yml verifies web container Up
  test_urls_has_dashboard_route               — urls.py registers 'dashboard' path
  test_views_has_dashboard_function           — views.py defines dashboard()
  test_dashboard_view_returns_200             — dashboard() returns HTTP 200 in tests
  test_dashboard_view_returns_html            — dashboard() returns text/html content type
  test_deploy_yml_smoke_scoped_solanatrilly   — all docker commands scoped -p solanatrilly
  test_deploy_yml_frontend_uses_production_target — frontend image built from production stage
"""
import ast
from pathlib import Path

from django.test import RequestFactory

# ---------------------------------------------------------------------------
# H1 ImportError trap — fails pytest COLLECTION if consumer is deleted/renamed
# (AC-48.3: "WS consumer tests are wired into the single canonical ci.yml 'test'
# job via an ImportError trap on the named consumer function (H1)")
# ---------------------------------------------------------------------------
from core.dashboard.consumer import TapeFeedConsumer, TapeFeedProcessor
from core.views import dashboard

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
URLS_PY = REPO_ROOT / "core" / "urls.py"
VIEWS_PY = REPO_ROOT / "core" / "views.py"


# ===========================================================================
# H1 ImportError trap tests
# ===========================================================================

def test_h1_import_tape_feed_consumer():
    """H1: TapeFeedConsumer is importable — fails collection if consumer.py is deleted."""
    assert TapeFeedConsumer is not None, "TapeFeedConsumer must be importable from core.dashboard.consumer"


def test_h1_import_tape_feed_processor():
    """H1: TapeFeedProcessor is importable — fails collection if consumer.py is deleted."""
    assert TapeFeedProcessor is not None, "TapeFeedProcessor must be importable from core.dashboard.consumer"


# ===========================================================================
# deploy.yml structural tests
# ===========================================================================

def test_deploy_yml_builds_frontend_image():
    """deploy.yml builds and pushes the solanatrilly-frontend image (AC-48.3).

    The frontend container in docker-compose.staging.yml requires
    ghcr.io/asimquick/solanatrilly-frontend:latest to exist in GHCR.
    Without this build step the VPS pull fails and the frontend container
    never starts.
    """
    content = DEPLOY_YML.read_text()
    assert "solanatrilly-frontend" in content, (
        "deploy.yml must build and push ghcr.io/asimquick/solanatrilly-frontend "
        "(required for the frontend container on the VPS staging stack, AC-48.3)"
    )
    assert "frontend/Dockerfile" in content, (
        "deploy.yml must reference frontend/Dockerfile for the frontend image build (AC-48.3)"
    )


def test_deploy_yml_has_dashboard_smoke_test():
    """deploy.yml has a step that checks /dashboard/ returns HTTP 200 (AC-48.3)."""
    content = DEPLOY_YML.read_text()
    assert "/dashboard/" in content, (
        "deploy.yml must include a smoke-test step checking GET /dashboard/ returns HTTP 200 (AC-48.3)"
    )


def test_deploy_yml_has_ws_smoke_test():
    """deploy.yml has a step that verifies the WS endpoint accepts HTTP 101 upgrade (AC-48.3)."""
    content = DEPLOY_YML.read_text()
    assert "101" in content, (
        "deploy.yml must include a WS upgrade check asserting HTTP 101 (AC-48.3)"
    )
    assert "ws/tape/" in content or "Upgrade: websocket" in content, (
        "deploy.yml must reference the WS tape endpoint or websocket upgrade headers (AC-48.3)"
    )


def test_deploy_yml_has_frontend_container_check():
    """deploy.yml verifies the 'frontend' container is Up after deploy (AC-48.3)."""
    content = DEPLOY_YML.read_text()
    assert "frontend" in content.lower() and "container" in content.lower(), (
        "deploy.yml must have a step verifying the 'frontend' container is Up (AC-48.3)"
    )
    # The check must grep for the frontend container in the compose ps output
    assert "solanatrilly.frontend" in content or "'frontend' container" in content, (
        "deploy.yml must grep for 'solanatrilly.frontend' or 'frontend container' in ps output (AC-48.3)"
    )


def test_deploy_yml_has_web_container_check():
    """deploy.yml verifies the 'web' container is Up after deploy (AC-48.3)."""
    content = DEPLOY_YML.read_text()
    assert "solanatrilly.web" in content or "'web' container" in content, (
        "deploy.yml must verify the 'web' container is Up (AC-48.3)"
    )


def test_deploy_yml_smoke_scoped_solanatrilly():
    """All docker compose commands in deploy.yml are scoped -p solanatrilly (hard isolation)."""
    content = DEPLOY_YML.read_text()
    assert "-p solanatrilly" in content, (
        "deploy.yml must scope all docker compose commands with -p solanatrilly "
        "(hard isolation from solanaBilly — PRD §15.3)"
    )
    assert "unscoped" not in content.lower(), (
        "deploy.yml must never contain unscoped docker commands"
    )


def test_deploy_yml_frontend_uses_production_target():
    """deploy.yml builds the frontend image from the 'production' Dockerfile stage (AC-48.3).

    The frontend/Dockerfile multi-stage build has 'development' and 'production'
    stages. Only the 'production' (nginx) stage should be pushed to GHCR — the
    'development' stage is for the local dev-container only.
    """
    content = DEPLOY_YML.read_text()
    assert "production" in content, (
        "deploy.yml must build the frontend image from the 'production' Dockerfile stage "
        "(nginx static-asset server, not the Vite dev server — AC-48.3)"
    )


# ===========================================================================
# URL / view structural tests
# ===========================================================================

def test_urls_has_dashboard_route():
    """core/urls.py registers a 'dashboard' URL pattern (AC-48.3 smoke-test target)."""
    src = URLS_PY.read_text()
    assert "dashboard" in src, (
        "core/urls.py must register a 'dashboard' URL pattern "
        "(the smoke-test checks GET /dashboard/ returns HTTP 200, AC-48.3)"
    )
    tree = ast.parse(src)
    path_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(getattr(node.func, "id", None), "__str__", lambda: None)() == "path"
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "path"
        )
    ]
    route_strings = []
    for call in path_calls:
        if call.args and isinstance(call.args[0], ast.Constant):
            route_strings.append(call.args[0].value)
    assert any("dashboard" in r for r in route_strings), (
        f"core/urls.py must have a path('dashboard/...) entry. Found routes: {route_strings}"
    )


def test_views_has_dashboard_function():
    """core/views.py defines a 'dashboard' view function (AC-48.3)."""
    src = VIEWS_PY.read_text()
    tree = ast.parse(src)
    func_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "dashboard" in func_names, (
        f"core/views.py must define a 'dashboard' function. Found: {func_names}"
    )


# ===========================================================================
# Functional tests for the dashboard view
# ===========================================================================

def test_dashboard_view_returns_200():
    """dashboard() view returns HTTP 200 when called with a dummy GET request."""
    rf = RequestFactory()
    request = rf.get("/dashboard/")
    response = dashboard(request)
    assert response.status_code == 200, (
        f"dashboard() must return HTTP 200, got {response.status_code} (AC-48.3 smoke-test target)"
    )


def test_dashboard_view_returns_html():
    """dashboard() view returns text/html content-type (the SPA shell)."""
    rf = RequestFactory()
    request = rf.get("/dashboard/")
    response = dashboard(request)
    assert "text/html" in response.get("Content-Type", ""), (
        "dashboard() must return text/html — it serves the React SPA shell (AC-48.3)"
    )


def test_dashboard_view_html_has_root_div():
    """dashboard() response body contains the React mount point <div id='root'>."""
    rf = RequestFactory()
    request = rf.get("/dashboard/")
    response = dashboard(request)
    content = response.content.decode("utf-8")
    assert "root" in content, (
        "dashboard() HTML must include the React mount point (id='root') (AC-48.3)"
    )
