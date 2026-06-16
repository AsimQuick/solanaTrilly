# ---
# module: core.tests.test_snapshot_admin_ac234
# sprint: sprint-6
# story: US-23 AC-23.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, core.admin, django.contrib.auth, django.test
# ---
"""Tests for US-23 AC-23.4: Snapshot registered in Django admin.

Verifies that an authenticated staff user can access:
  - The Snapshot admin changelist (HTTP 200)
  - The Snapshot admin change-detail page (HTTP 200)
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import Snapshot

User = get_user_model()


@pytest.mark.django_db
def test_snapshot_admin_changelist_returns_200(client):
    """Admin changelist for Snapshot returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="snapshot_list_user",
        password="testpass",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(staff)
    url = reverse("admin:core_snapshot_changelist")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_snapshot_admin_change_detail_returns_200(client):
    """Admin change-detail page for a Snapshot instance returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="snapshot_detail_user",
        password="testpass",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(staff)
    snapshot = Snapshot.objects.create(
        mint="So11111111111111111111111111111111111111112",
        taken_at=timezone.now(),
        elapsed_s=30,
        raw={"holders": 100, "lp_burned": True},
    )
    url = reverse("admin:core_snapshot_change", args=[snapshot.pk])
    response = client.get(url)
    assert response.status_code == 200
