# ---
# module: core.tests.test_pipeline_config_ac94
# sprint: sprint-3
# story: US-9 AC-9.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.admin, django.contrib.auth, django.test
# ---
"""Tests for US-9 AC-9.4: PipelineConfig registered in Django admin.

Verifies that an authenticated staff/superuser can access:
  - The PipelineConfig admin changelist (HTTP 200)
  - The PipelineConfig admin change-detail page (HTTP 200)
with django-simple-history's audit/diff history available via SimpleHistoryAdmin.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import PipelineConfig

User = get_user_model()


@pytest.mark.django_db
def test_pipeline_config_admin_changelist_returns_200(client):
    """Admin changelist for PipelineConfig returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="admin_list_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    url = reverse("admin:core_pipelineconfig_changelist")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_pipeline_config_admin_change_detail_returns_200(client):
    """Admin change-detail page for a PipelineConfig instance returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="admin_detail_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    config = PipelineConfig.objects.create(version=1, label="ac94-admin-test")
    url = reverse("admin:core_pipelineconfig_change", args=[config.pk])
    response = client.get(url)
    assert response.status_code == 200
