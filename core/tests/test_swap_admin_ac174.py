# ---
# module: core.tests.test_swap_admin_ac174
# sprint: sprint-5
# story: US-17 AC-17.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.admin, django.contrib.auth, django.test
# ---
"""Tests for US-17 AC-17.4: Swap registered in Django admin.

Verifies that an authenticated staff user can access:
  - The Swap admin changelist (HTTP 200)
  - The Swap admin change-detail page (HTTP 200)
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Swap

User = get_user_model()


@pytest.mark.django_db
def test_swap_admin_changelist_returns_200(client):
    """Admin changelist for Swap returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="swap_list_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    url = reverse("admin:core_swap_changelist")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_swap_admin_change_detail_returns_200(client):
    """Admin change-detail page for a Swap instance returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="swap_detail_user", password="testpass", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    swap = Swap.objects.create(
        mint="So11111111111111111111111111111111111111112",
        block_time=335_000_000,
        slot=290_000_000,
        signature="5xSig" + "1" * 83,
        side="buy",
        price=0.001,
        vol_sol=1.0,
        vol_usd=100.0,
        sol_usd=100.0,
        owner="owner111111111111111111111111111111111111111",
        base_reserve=1_000_000,
        quote_reserve=500_000,
        rel=5.0,
    )
    url = reverse("admin:core_swap_change", args=[swap.pk])
    response = client.get(url)
    assert response.status_code == 200
