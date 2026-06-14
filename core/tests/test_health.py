# ---
# module: core.tests.test_health
# sprint: pre-sprint
# story: setup
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-14
# dependencies: core
# ---
"""Tier 1 contract test for the setup health endpoint."""
import pytest
from django.test import Client


@pytest.mark.django_db
def test_health():
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
