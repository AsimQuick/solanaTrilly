# ---
# module: core.tests.test_compose_topology
# sprint: sprint-1
# story: US-1 AC-1.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: pyyaml
# ---
"""AC-1.1 — verify docker-compose.yml defines the full containerised service topology."""
from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

REQUIRED_SERVICES = {"web", "db", "redis", "celery-worker", "celery-beat"}


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


def test_all_required_services_defined(compose):
    """All five service keys must be present."""
    services = set(compose.get("services", {}).keys())
    missing = REQUIRED_SERVICES - services
    assert not missing, f"Missing services: {missing}"


def test_web_uses_asgi_server(compose):
    """web service command must reference daphne or uvicorn (ASGI, not WSGI)."""
    command = str(compose["services"]["web"].get("command", ""))
    assert any(srv in command for srv in ("daphne", "uvicorn")), (
        f"web service must use an ASGI server (daphne/uvicorn), got: {command!r}"
    )


def test_web_not_using_wsgi_server(compose):
    """web service must NOT use gunicorn (WSGI)."""
    command = str(compose["services"]["web"].get("command", ""))
    assert "gunicorn" not in command, "web service must not use gunicorn (WSGI)"


def test_db_uses_postgres_16(compose):
    """db service must use a postgres:16 image."""
    image = compose["services"]["db"].get("image", "")
    assert image.startswith("postgres:16"), f"db must use postgres:16, got: {image!r}"


def test_redis_service_defined(compose):
    """redis service must be present (not localhost install)."""
    assert "redis" in compose["services"], "redis service must be defined in docker-compose.yml"


def test_celery_services_depend_on_redis(compose):
    """celery-worker and celery-beat must declare a dependency on redis."""
    for svc_name in ("celery-worker", "celery-beat"):
        depends = compose["services"][svc_name].get("depends_on", {})
        dep_names = set(depends.keys()) if isinstance(depends, dict) else set(depends)
        assert "redis" in dep_names, f"'{svc_name}' must declare depends_on: redis"


def test_no_localhost_in_service_environments(compose):
    """No service environment may reference localhost — all inter-service comms use Docker hostnames."""
    for name, svc in compose["services"].items():
        env = svc.get("environment", {})
        env_str = " ".join(env) if isinstance(env, list) else str(env)
        assert "localhost" not in env_str, (
            f"Service '{name}' references localhost in environment — use Docker network hostnames"
        )


def test_celery_worker_command(compose):
    """celery-worker command must invoke `celery worker`."""
    command = str(compose["services"]["celery-worker"].get("command", ""))
    assert "celery" in command and "worker" in command, (
        f"celery-worker command must run celery worker, got: {command!r}"
    )


def test_celery_beat_command(compose):
    """celery-beat command must invoke `celery beat`."""
    command = str(compose["services"]["celery-beat"].get("command", ""))
    assert "celery" in command and "beat" in command, (
        f"celery-beat command must run celery beat, got: {command!r}"
    )
