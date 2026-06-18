# ---
# module: core.tests.test_control_api_ac561
# sprint: sprint-11
# story: US-56 AC-56.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.control_api, core.models, core.resolver, djangorestframework, pytest
# ---
"""Pytest/API tests for the US-56 AC-56.1 read-only DRF control API.

Covers: PipelineConfig + ModelRegistry list/detail/history/diff endpoints,
diff determinism, and banked-fixture section-level diff correctness.

All tests are deterministic — no random data, no firehose.
"""
import pytest
from rest_framework.test import APIClient

# H1 ImportError trap — must succeed for pytest collection
from core.control_api import (
    ModelRegistrySerializer,
    PipelineConfigSerializer,
    compute_config_diff,
    compute_model_diff,
    config_control_view,
    config_diff_view,  # noqa: F401
    config_history_view,  # noqa: F401
    registry_control_view,
    registry_diff_view,  # noqa: F401
    registry_history_view,  # noqa: F401
)
from core.models import ModelRegistry, PipelineConfig

# ---------------------------------------------------------------------------
# Banked fixtures (deterministic)
# ---------------------------------------------------------------------------

_FIXTURE_V1 = {
    "version": 1,
    "label": "v1-baseline",
    "detection": {"graduation_program": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "poll_interval_s": 5},
    "tape": {"idle_kill_ttl_s": 3600},
    "scoring": {"gate": "adaptive_topk"},
    "outcome": {"window_s": 300},
    "trading": {"position_size_sol": 0.1},
    "is_active": True,
}

_FIXTURE_V2 = {
    "version": 2,
    "label": "v2-tuned",
    # poll_interval_s changed from 5 → 10
    "detection": {
        "graduation_program": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "poll_interval_s": 10,
    },
    "tape": {"idle_kill_ttl_s": 3600, "capture_buffer_s": 30},  # capture_buffer_s added
    "scoring": {"gate": "adaptive_topk"},
    "outcome": {"window_s": 600},  # changed from 300
    "trading": {"position_size_sol": 0.1},
    "is_active": False,
}

_FIXTURE_MODEL_BASE = {
    "kind": "lightgbm_regression_blend",
    "feature_list": ["f1", "f2", "f3"],
    "labels_seeds_manifest": {"labels": ["ctrl", "oracle", "liq"], "seeds": [0, 1, 2, 3, 4]},
    "blend_transform_descriptor": {"method": "rank_average"},
    "artifact_content_hashes": {"model.pkl": "abc123def456"},
    "model_version": "v3.2",
    "feature_set_version": "v1",
    "is_active": True,
}

_FIXTURE_MODEL_CANDIDATE = {
    "kind": "lightgbm_regression_blend",
    "feature_list": ["f1", "f2", "f3", "f4"],  # f4 added
    "labels_seeds_manifest": {"labels": ["ctrl", "oracle", "liq"], "seeds": [0, 1, 2, 3, 4]},
    "blend_transform_descriptor": {"method": "rank_average"},
    "artifact_content_hashes": {"model.pkl": "999abc000def"},  # changed
    "model_version": "v3.3",  # changed
    "feature_set_version": "v1",
    "is_active": False,
}


# ---------------------------------------------------------------------------
# H1 import tests (no DB needed — but django_db marker still used for safety)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_h1_importable_pipeline_config_serializer():
    assert PipelineConfigSerializer is not None


@pytest.mark.django_db
def test_h1_importable_model_registry_serializer():
    assert ModelRegistrySerializer is not None


@pytest.mark.django_db
def test_h1_importable_compute_config_diff():
    assert compute_config_diff is not None


@pytest.mark.django_db
def test_h1_importable_compute_model_diff():
    assert compute_model_diff is not None


@pytest.mark.django_db
def test_h1_importable_config_control_view():
    assert config_control_view is not None


@pytest.mark.django_db
def test_h1_importable_registry_control_view():
    assert registry_control_view is not None


# ---------------------------------------------------------------------------
# PipelineConfig endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_config_control_view_returns_active_config():
    PipelineConfig.objects.create(**_FIXTURE_V1)
    PipelineConfig.objects.create(**_FIXTURE_V2)

    client = APIClient()
    response = client.get("/api/control/config/")

    assert response.status_code == 200
    data = response.json()
    assert data["active"] is not None
    assert data["active"]["version"] == 1
    assert data["active"]["is_active"] is True
    assert len(data["all"]) == 2


@pytest.mark.django_db
def test_config_control_view_no_active_config():
    PipelineConfig.objects.create(**{**_FIXTURE_V1, "is_active": False})

    client = APIClient()
    response = client.get("/api/control/config/")

    assert response.status_code == 200
    data = response.json()
    assert data["active"] is None


@pytest.mark.django_db
def test_config_detail_view():
    config = PipelineConfig.objects.create(**_FIXTURE_V1)

    client = APIClient()
    response = client.get(f"/api/control/config/{config.pk}/")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1
    assert data["label"] == "v1-baseline"


@pytest.mark.django_db
def test_config_detail_view_not_found():
    client = APIClient()
    response = client.get("/api/control/config/99999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_config_history_view_returns_audit_trail():
    config = PipelineConfig.objects.create(**_FIXTURE_V1)

    client = APIClient()
    response = client.get(f"/api/control/config/{config.pk}/history/")

    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    first = data[0]
    assert "history_id" in first
    assert "history_date" in first
    assert "history_type" in first


@pytest.mark.django_db
def test_config_diff_view_correct_diff_over_banked_fixture():
    v1 = PipelineConfig.objects.create(**_FIXTURE_V1)
    v2 = PipelineConfig.objects.create(**_FIXTURE_V2)

    client = APIClient()
    response = client.get(f"/api/control/config/diff/?v1={v1.pk}&v2={v2.pk}")

    assert response.status_code == 200
    data = response.json()

    # meta diffs
    assert data["meta"]["version"] == {"from": 1, "to": 2}
    assert data["meta"]["label"] == {"from": "v1-baseline", "to": "v2-tuned"}

    # detection: poll_interval_s changed
    assert data["sections"]["detection"]["changed"]["poll_interval_s"] == {"from": 5, "to": 10}

    # tape: capture_buffer_s added
    assert data["sections"]["tape"]["added"]["capture_buffer_s"] == 30

    # outcome: window_s changed
    assert data["sections"]["outcome"]["changed"]["window_s"] == {"from": 300, "to": 600}

    # scoring and trading are unchanged — must NOT appear in sections
    assert "scoring" not in data["sections"]
    assert "trading" not in data["sections"]


@pytest.mark.django_db
def test_config_diff_deterministic():
    v1 = PipelineConfig.objects.create(**_FIXTURE_V1)
    v2 = PipelineConfig.objects.create(**_FIXTURE_V2)

    result1 = compute_config_diff(v1, v2)
    result2 = compute_config_diff(v1, v2)

    assert result1 == result2


@pytest.mark.django_db
def test_config_diff_view_missing_params():
    client = APIClient()
    response = client.get("/api/control/config/diff/")
    assert response.status_code == 400


@pytest.mark.django_db
def test_config_diff_view_not_found():
    client = APIClient()
    response = client.get("/api/control/config/diff/?v1=99999&v2=99998")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# ModelRegistry endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_registry_control_view_returns_active_model():
    ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    client = APIClient()
    response = client.get("/api/control/registry/")

    assert response.status_code == 200
    data = response.json()
    assert data["active"] is not None
    assert data["active"]["model_version"] == "v3.2"
    assert len(data["all"]) == 2


@pytest.mark.django_db
def test_registry_control_view_no_active_model():
    ModelRegistry.objects.create(**{**_FIXTURE_MODEL_CANDIDATE, "is_active": False})

    client = APIClient()
    response = client.get("/api/control/registry/")

    assert response.status_code == 200
    data = response.json()
    assert data["active"] is None


@pytest.mark.django_db
def test_registry_detail_view():
    base = ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)

    client = APIClient()
    response = client.get(f"/api/control/registry/{base.pk}/")

    assert response.status_code == 200
    data = response.json()
    assert data["model_version"] == "v3.2"


@pytest.mark.django_db
def test_registry_detail_view_not_found():
    client = APIClient()
    response = client.get("/api/control/registry/99999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_registry_history_view_returns_audit_trail():
    base = ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)

    client = APIClient()
    response = client.get(f"/api/control/registry/{base.pk}/history/")

    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    first = data[0]
    assert "history_id" in first
    assert "history_date" in first
    assert "history_type" in first


@pytest.mark.django_db
def test_registry_diff_view_correct_diff_over_banked_fixture():
    base = ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    candidate = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    client = APIClient()
    response = client.get(f"/api/control/registry/diff/?candidate={candidate.pk}")

    assert response.status_code == 200
    data = response.json()

    assert data["base_id"] == base.pk
    assert data["candidate_id"] == candidate.pk
    assert data["diff"]["model_version"] == {"from": "v3.2", "to": "v3.3"}
    assert data["diff"]["feature_list"]["from"] == ["f1", "f2", "f3"]
    assert data["diff"]["feature_list"]["to"] == ["f1", "f2", "f3", "f4"]


@pytest.mark.django_db
def test_registry_diff_deterministic():
    base = ModelRegistry.objects.create(**_FIXTURE_MODEL_BASE)
    candidate = ModelRegistry.objects.create(**_FIXTURE_MODEL_CANDIDATE)

    result1 = compute_model_diff(base, candidate)
    result2 = compute_model_diff(base, candidate)

    assert result1 == result2


@pytest.mark.django_db
def test_registry_diff_view_missing_params():
    client = APIClient()
    response = client.get("/api/control/registry/diff/")
    assert response.status_code == 400


@pytest.mark.django_db
def test_registry_diff_view_no_active_model():
    candidate = ModelRegistry.objects.create(**{**_FIXTURE_MODEL_CANDIDATE, "is_active": False})

    client = APIClient()
    response = client.get(f"/api/control/registry/diff/?candidate={candidate.pk}")

    assert response.status_code == 200
    data = response.json()
    assert data["base_id"] is None
