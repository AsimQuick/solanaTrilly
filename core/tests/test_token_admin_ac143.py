# ---
# module: core.tests.test_token_admin_ac143
# sprint: sprint-4
# story: US-14 AC-14.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.admin, django.contrib.auth, django.test
# ---
"""Tests for US-14 AC-14.3: Token registered in Django admin.

Verifies that an authenticated staff user can access:
  - The Token admin changelist (HTTP 200)
  - The Token admin change-detail page (HTTP 200)
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import Token

User = get_user_model()


@pytest.mark.django_db
def test_token_admin_changelist_returns_200(client):
    """Admin changelist for Token returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="token_list_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    url = reverse("admin:core_token_changelist")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_token_admin_change_detail_returns_200(client):
    """Admin change-detail page for a Token instance returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="token_detail_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    token = Token.objects.create(
        mint="So11111111111111111111111111111111111111112",
        pool_address="pool111111111111111111111111111111111111111",
        graduated_at=timezone.now(),
        graduated_block_time=335_000_000,
        dex_source="pump_dot_fun",
        raw_graduation={"test": True},
    )
    url = reverse("admin:core_token_change", args=[token.pk])
    response = client.get(url)
    assert response.status_code == 200
