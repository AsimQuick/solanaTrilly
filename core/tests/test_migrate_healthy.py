# ---
# module: core.tests.test_migrate_healthy
# sprint: sprint-1
# story: US-1 AC-1.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, pyyaml
# ---
"""AC-1.4 — verify migrations apply cleanly and all services reach healthy state."""
from pathlib import Path

import pytest
import yaml


@pytest.mark.django_db
def test_no_pending_migrations():
    """Verify no pending migrations exist — migrate has already been applied."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connections["default"])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert plan == [], f"Pending migrations found: {[str(m) for m, _ in plan]}"


@pytest.mark.django_db
def test_database_tables_exist_after_migrate():
    """Verify Django core tables were created by migrate."""
    from django.db import connection

    tables = connection.introspection.table_names()
    for expected in ("django_migrations", "auth_user", "django_content_type"):
        assert expected in tables, f"Expected table '{expected}' not found in DB after migrate"


@pytest.mark.django_db
def test_health_endpoint_returns_ok():
    """Verify /health/ returns HTTP 200 with status: ok."""
    from django.test import Client

    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_compose_web_has_healthcheck():
    """Verify docker-compose.yml defines a healthcheck for the web service."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    with compose_path.open() as f:
        compose = yaml.safe_load(f)

    web_healthcheck = compose["services"]["web"].get("healthcheck")
    assert web_healthcheck is not None, "web service has no healthcheck defined"
    assert web_healthcheck.get("test"), "web healthcheck 'test' is empty or missing"


def test_compose_all_services_have_healthchecks():
    """Verify all five services define a healthcheck — prerequisite for docker compose up -d healthy state."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    with compose_path.open() as f:
        compose = yaml.safe_load(f)

    expected_services = {"web", "db", "redis", "celery-worker", "celery-beat"}
    missing = []
    for svc in expected_services:
        if not compose["services"].get(svc, {}).get("healthcheck"):
            missing.append(svc)

    assert missing == [], f"Services missing healthcheck: {missing}"


def test_compose_web_healthcheck_uses_health_endpoint():
    """Verify the web service healthcheck references the /health/ endpoint."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    with compose_path.open() as f:
        compose = yaml.safe_load(f)

    test_cmd = compose["services"]["web"]["healthcheck"]["test"]
    # test_cmd is a list like ["CMD-SHELL", "...health..."]
    test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    assert "health" in test_str, (
        f"web healthcheck does not reference /health/ endpoint. Got: {test_str!r}"
    )
