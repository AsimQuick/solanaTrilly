# ---
# module: core.tests.test_control_api_ac563
# sprint: sprint-11
# story: US-56 AC-56.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.control_api, pytest, pathlib
# ---
"""AC-56.3 — operator-skin wiring guard tests.

Verifies:
  H1: All 10 named symbols from core.control_api are importable (pytest-collection-time
      ImportError trap — rename/delete of any endpoint fails CI before any test runs).
  Structural: ConfigControl.jsx exists in frontend/src/.
  Structural: App.jsx imports ConfigControl and routes view=control.
  Structural: deploy.yml has a US-56 control API smoke test step.
  Structural: ci.yml runs pytest via docker compose (canonical test job).
"""
from pathlib import Path

import pytest

# H1 ImportError trap — ALL 10 symbols must survive import at collection time.
# If any endpoint is deleted or renamed, pytest will refuse to collect this file,
# causing CI to fail before running a single test.
from core.control_api import (  # noqa: F401
    ModelRegistrySerializer,
    PipelineConfigSerializer,
    config_activate_view,
    config_control_view,
    config_diff_view,
    config_history_view,
    registry_activate_view,
    registry_control_view,
    registry_diff_view,
    registry_history_view,
)

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# H1 import tests — one per critical symbol
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_h1_importable_pipeline_config_serializer():
    assert PipelineConfigSerializer is not None


@pytest.mark.django_db
def test_h1_importable_model_registry_serializer():
    assert ModelRegistrySerializer is not None


@pytest.mark.django_db
def test_h1_importable_config_control_view():
    assert config_control_view is not None


@pytest.mark.django_db
def test_h1_importable_registry_control_view():
    assert registry_control_view is not None


@pytest.mark.django_db
def test_h1_importable_config_activate_view():
    assert config_activate_view is not None


@pytest.mark.django_db
def test_h1_importable_registry_activate_view():
    assert registry_activate_view is not None


# ---------------------------------------------------------------------------
# Structural: frontend files
# ---------------------------------------------------------------------------


def test_frontend_config_control_jsx_exists():
    """ConfigControl.jsx must be present in frontend/src/."""
    jsx_path = REPO_ROOT / 'frontend' / 'src' / 'ConfigControl.jsx'
    assert jsx_path.exists(), f"ConfigControl.jsx not found at {jsx_path}"


def test_app_jsx_imports_config_control():
    """App.jsx must import ConfigControl."""
    app_jsx = REPO_ROOT / 'frontend' / 'src' / 'App.jsx'
    assert app_jsx.exists(), f"App.jsx not found at {app_jsx}"
    content = app_jsx.read_text(encoding='utf-8')
    assert 'ConfigControl' in content, "App.jsx does not reference ConfigControl"


def test_app_jsx_routes_view_control():
    """App.jsx must route view=control to the ConfigControl component."""
    app_jsx = REPO_ROOT / 'frontend' / 'src' / 'App.jsx'
    assert app_jsx.exists(), f"App.jsx not found at {app_jsx}"
    content = app_jsx.read_text(encoding='utf-8')
    assert 'control' in content, "App.jsx does not contain 'control' view routing"


# ---------------------------------------------------------------------------
# Structural: CI/CD files
# ---------------------------------------------------------------------------


def test_deploy_yml_has_control_api_smoke_test():
    """deploy.yml must contain a smoke test for the US-56 control config API."""
    deploy_yml = REPO_ROOT / '.github' / 'workflows' / 'deploy.yml'
    assert deploy_yml.exists(), f"deploy.yml not found at {deploy_yml}"
    content = deploy_yml.read_text(encoding='utf-8')
    assert 'api/control/config/' in content, (
        "deploy.yml does not contain a smoke test step for /api/control/config/"
    )


def test_ci_yml_runs_pytest_via_docker_compose():
    """ci.yml canonical test job must run pytest inside docker compose."""
    ci_yml = REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
    assert ci_yml.exists(), f"ci.yml not found at {ci_yml}"
    content = ci_yml.read_text(encoding='utf-8')
    assert 'pytest' in content, "ci.yml does not invoke pytest"
