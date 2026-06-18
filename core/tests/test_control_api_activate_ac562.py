# ---
# module: core.tests.test_control_api_activate_ac562
# sprint: sprint-11
# story: US-56 AC-56.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.control_api, core.models, core.resolver, djangorestframework, pytest, ast
# ---
"""AC-56.2 — Operator-GATED activate action tests.

Verifies that the config/registry activate views:
  - delegate exclusively to activate_config() / activate_model() (US-9 / US-42)
  - preserve at-most-one-active discipline (exactly one audited activation)
  - never touch PipelineState.firehose_active / scoring_enabled / trading_enabled
  - are gated behind IsAdminUser (unauthenticated callers receive HTTP 403)

Test structure
--------------
H1 ImportError traps (wiring guard):
  test_h1_importable_config_activate_view
  test_h1_importable_registry_activate_view

Operator gate tests (no auth → 403):
  test_config_activate_view_requires_admin
  test_registry_activate_view_requires_admin

Config activation tests:
  test_config_activate_view_activates_target
  test_config_activate_view_exactly_one_active_after_activation
  test_config_activate_view_auto_start_flags_unchanged
  test_config_activate_view_not_found

Model/registry activation tests:
  test_registry_activate_view_activates_target
  test_registry_activate_view_exactly_one_active_after_activation
  test_registry_activate_view_auto_start_flags_unchanged
  test_registry_activate_view_not_found

AST guard — control_api.py must not set auto-start flags True:
  test_ast_guard_control_api_does_not_set_firehose_active_true
  test_ast_guard_control_api_does_not_set_scoring_enabled_true
  test_ast_guard_control_api_does_not_set_trading_enabled_true
"""
import ast
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

# H1 ImportError trap — deletion/rename of either view fails pytest collection
from core.control_api import config_activate_view, registry_activate_view  # noqa: F401
from core.models import ModelRegistry, PipelineConfig, PipelineState

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROL_API_PY = REPO_ROOT / "core" / "control_api.py"

_AUTO_START_FLAGS = {"firehose_active", "scoring_enabled", "trading_enabled"}

# ---------------------------------------------------------------------------
# AST helper — mirrors the AC-11.3 / AC-42.3 pattern
# ---------------------------------------------------------------------------


def _find_flag_true_assignments(path: Path) -> list[str]:
    """Return descriptions of auto-start flag =True assignments in *path*."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: .update(scoring_enabled=True) / objects.create(firehose_active=True)
        if isinstance(node, ast.keyword):
            if (
                node.arg in _AUTO_START_FLAGS
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                findings.append(
                    f"{rel}: keyword {node.arg}=True at line {node.value.lineno}"
                )

        # attribute assignment: state.scoring_enabled = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in _AUTO_START_FLAGS
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    findings.append(
                        f"{rel}: attribute .{target.attr} = True at line {node.value.lineno}"
                    )

    return findings


# ---------------------------------------------------------------------------
# Banked fixtures (deterministic)
# ---------------------------------------------------------------------------

_FIXTURE_CONFIG_V1 = {
    "version": 1,
    "label": "v1-baseline",
    "detection": {"graduation_program": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "poll_interval_s": 5},
    "tape": {"idle_kill_ttl_s": 3600},
    "scoring": {"gate": "adaptive_topk"},
    "outcome": {"window_s": 300},
    "trading": {"position_size_sol": 0.1},
    "is_active": True,
}

_FIXTURE_CONFIG_V2 = {
    "version": 2,
    "label": "v2-candidate",
    "detection": {"graduation_program": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "poll_interval_s": 10},
    "tape": {"idle_kill_ttl_s": 3600},
    "scoring": {"gate": "adaptive_topk"},
    "outcome": {"window_s": 300},
    "trading": {"position_size_sol": 0.1},
    "is_active": False,
}

_FIXTURE_MODEL_BASE = {
    "kind": "lightgbm_regression_blend",
    "feature_list": ["f1", "f2", "f3"],
    "labels_seeds_manifest": {"labels": ["ctrl"], "seeds": [0]},
    "blend_transform_descriptor": {"method": "rank_average"},
    "artifact_content_hashes": {"model.pkl": "abc123"},
    "model_version": "v3.2",
    "feature_set_version": "v1",
    "is_active": True,
}

_FIXTURE_MODEL_CANDIDATE = {
    "kind": "lightgbm_regression_blend",
    "feature_list": ["f1", "f2", "f3", "f4"],
    "labels_seeds_manifest": {"labels": ["ctrl"], "seeds": [0]},
    "blend_transform_descriptor": {"method": "rank_average"},
    "artifact_content_hashes": {"model.pkl": "def456"},
    "model_version": "v3.3",
    "feature_set_version": "v1",
    "is_active": False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin() -> User:
    return User.objects.create_superuser(
        username="operator_ac562", password="testpass123", email="op@test.com"
    )


def _admin_client() -> APIClient:
    client = APIClient()
    user = _make_admin()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# H1 ImportError trap tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_h1_importable_config_activate_view():
    assert config_activate_view is not None


@pytest.mark.django_db
def test_h1_importable_registry_activate_view():
    assert registry_activate_view is not None


# ---------------------------------------------------------------------------
# Operator gate tests — unauthenticated → 403
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_config_activate_view_requires_admin():
    """Unauthenticated POST to the activate endpoint must return 403."""
    config = PipelineConfig.objects.create(**_FIXTURE_CONFIG_V2)
    client = APIClient()
    response = client.post(f"/api/control/config/{config.pk}/activate/")
    assert response.status_code in (401, 403), (
        f"Expected 401/403 for unauthenticated activate, got {response.status_code}"
    )


@pytest.mark.django_db
def test_registry_activate_view_requires_admin():
    """Unauthenticated POST to the activate endpoint must return 403."""
    model = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)
    client = APIClient()
    response = client.post(f"/api/control/registry/{model.pk}/activate/")
    assert response.status_code in (401, 403), (
        f"Expected 401/403 for unauthenticated activate, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Config activation tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_config_activate_view_activates_target():
    """POST by an admin user activates the target config and returns its data."""
    PipelineConfig.objects.create(**_FIXTURE_CONFIG_V1)
    v2 = PipelineConfig.objects.create(**_FIXTURE_CONFIG_V2)

    client = _admin_client()
    response = client.post(f"/api/control/config/{v2.pk}/activate/")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == v2.pk
    assert data["is_active"] is True
    assert data["version"] == 2


@pytest.mark.django_db
def test_config_activate_view_exactly_one_active_after_activation():
    """After activation, exactly one PipelineConfig row has is_active=True
    (the at-most-one-active discipline — the audited path guarantee)."""
    PipelineConfig.objects.create(**_FIXTURE_CONFIG_V1)
    v2 = PipelineConfig.objects.create(**_FIXTURE_CONFIG_V2)

    client = _admin_client()
    client.post(f"/api/control/config/{v2.pk}/activate/")

    active_qs = PipelineConfig.objects.filter(is_active=True)
    assert active_qs.count() == 1, (
        f"Expected exactly 1 active config, found {active_qs.count()}"
    )
    assert active_qs.first().pk == v2.pk


@pytest.mark.django_db
def test_config_activate_view_auto_start_flags_unchanged():
    """Activating a config via the skin must NOT touch PipelineState.
    firehose_active, scoring_enabled, and trading_enabled must remain False."""
    # Ensure the PipelineState singleton exists with all flags False (safe defaults).
    state = PipelineState.get()
    assert state.firehose_active is False
    assert state.scoring_enabled is False
    assert state.trading_enabled is False

    PipelineConfig.objects.create(**_FIXTURE_CONFIG_V1)
    v2 = PipelineConfig.objects.create(**_FIXTURE_CONFIG_V2)

    client = _admin_client()
    response = client.post(f"/api/control/config/{v2.pk}/activate/")
    assert response.status_code == 200

    state.refresh_from_db()
    assert state.firehose_active is False, "activate config must not set firehose_active=True"
    assert state.scoring_enabled is False, "activate config must not set scoring_enabled=True"
    assert state.trading_enabled is False, "activate config must not set trading_enabled=True"


@pytest.mark.django_db
def test_config_activate_view_not_found():
    """POST for a non-existent config pk must return 404."""
    client = _admin_client()
    response = client.post("/api/control/config/99999/activate/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Model/registry activation tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_registry_activate_view_activates_target():
    """POST by an admin user activates the target model and returns its data."""
    ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    candidate = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    client = _admin_client()
    response = client.post(f"/api/control/registry/{candidate.pk}/activate/")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == candidate.pk
    assert data["is_active"] is True
    assert data["model_version"] == "v3.3"


@pytest.mark.django_db
def test_registry_activate_view_exactly_one_active_after_activation():
    """After activation, exactly one ModelRegistry row has is_active=True
    (the at-most-one-active discipline — the audited path guarantee)."""
    ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    candidate = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    client = _admin_client()
    client.post(f"/api/control/registry/{candidate.pk}/activate/")

    active_qs = ModelRegistry.objects.filter(is_active=True)
    assert active_qs.count() == 1, (
        f"Expected exactly 1 active model, found {active_qs.count()}"
    )
    assert active_qs.first().pk == candidate.pk


@pytest.mark.django_db
def test_registry_activate_view_auto_start_flags_unchanged():
    """Activating a model via the skin must NOT touch PipelineState.
    firehose_active, scoring_enabled, and trading_enabled must remain False."""
    state = PipelineState.get()
    assert state.firehose_active is False
    assert state.scoring_enabled is False
    assert state.trading_enabled is False

    ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    candidate = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    client = _admin_client()
    response = client.post(f"/api/control/registry/{candidate.pk}/activate/")
    assert response.status_code == 200

    state.refresh_from_db()
    assert state.firehose_active is False, "activate model must not set firehose_active=True"
    assert state.scoring_enabled is False, "activate model must not set scoring_enabled=True"
    assert state.trading_enabled is False, "activate model must not set trading_enabled=True"


@pytest.mark.django_db
def test_registry_activate_view_not_found():
    """POST for a non-existent registry pk must return 404."""
    client = _admin_client()
    response = client.post("/api/control/registry/99999/activate/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AST guard — control_api.py must not set auto-start flags to True
# (extends the US-11 AC-11.3 guard to the AC-56.2 view path)
# ---------------------------------------------------------------------------


def test_ast_guard_control_api_does_not_set_firehose_active_true():
    """control_api.py must not set firehose_active=True anywhere.

    The activate view path must delegate to activate_config()/activate_model()
    and never touch PipelineState directly.
    """
    violations = [
        v for v in _find_flag_true_assignments(CONTROL_API_PY) if "firehose_active" in v
    ]
    assert not violations, (
        "core/control_api.py sets firehose_active=True — the activate view path must NOT "
        "auto-start the firehose:\n" + "\n".join(violations)
    )


def test_ast_guard_control_api_does_not_set_scoring_enabled_true():
    """control_api.py must not set scoring_enabled=True anywhere.

    Activating a config or model is never a signal to start scoring
    (§5.3/§15.6 — only explicit operator actions may flip this flag).
    """
    violations = [
        v for v in _find_flag_true_assignments(CONTROL_API_PY) if "scoring_enabled" in v
    ]
    assert not violations, (
        "core/control_api.py sets scoring_enabled=True — the activate view path must NOT "
        "auto-start scoring (§5.3/§15.6):\n" + "\n".join(violations)
    )


def test_ast_guard_control_api_does_not_set_trading_enabled_true():
    """control_api.py must not set trading_enabled=True anywhere.

    Activating a config or model must never auto-start trading
    (§5.3/§15.6 — only explicit operator actions may flip this flag).
    """
    violations = [
        v for v in _find_flag_true_assignments(CONTROL_API_PY) if "trading_enabled" in v
    ]
    assert not violations, (
        "core/control_api.py sets trading_enabled=True — the activate view path must NOT "
        "auto-start trading (§5.3/§15.6):\n" + "\n".join(violations)
    )
