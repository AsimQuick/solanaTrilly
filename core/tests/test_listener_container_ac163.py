# ---
# module: core.tests.test_listener_container_ac163
# sprint: sprint-4
# story: US-16 AC-16.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-16.3 — compose topology test: dedicated 'listener' container in both compose files.

The #289 lesson: detection/recorder/reconciler must NEVER share the web/gunicorn process.
A standalone 'listener' service is required in BOTH docker-compose.yml (local dev) and
docker-compose.staging.yml (VPS staging, -p solanatrilly scope).

Verified assertions:
  Local (docker-compose.yml):
    test_listener_service_exists_in_local_compose
    test_listener_command_is_run_listener_local
    test_listener_does_not_use_gunicorn_or_daphne_local
    test_listener_depends_on_db_local
    test_listener_depends_on_redis_local

  Staging (docker-compose.staging.yml):
    test_listener_service_exists_in_staging_compose
    test_listener_command_is_run_listener_staging
    test_listener_does_not_use_gunicorn_or_daphne_staging
    test_listener_uses_ghcr_image_not_build_staging
    test_listener_on_solanatrilly_net_staging
    test_listener_depends_on_db_staging
    test_listener_depends_on_redis_staging
    test_listener_no_source_volume_mounts_staging
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_COMPOSE = REPO_ROOT / "docker-compose.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"

GHCR_PREFIX = "ghcr.io/asimquick/solanatrilly"
EXPECTED_NETWORK = "solanatrilly_net"
LISTENER_MANAGEMENT_CMD = "run_listener"
WSGI_SERVERS = ("gunicorn", "daphne", "uvicorn")


def _load(path: Path) -> dict:
    assert path.exists(), f"{path.name} not found at {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must be a valid YAML mapping"
    return data


def _listener_command(compose_data: dict) -> str:
    return str(
        compose_data.get("services", {})
        .get("listener", {})
        .get("command", "")
    )


def _listener_depends(compose_data: dict) -> set:
    depends = (
        compose_data.get("services", {})
        .get("listener", {})
        .get("depends_on", {})
    )
    if isinstance(depends, dict):
        return set(depends.keys())
    return set(depends)


# ---------------------------------------------------------------------------
# Local compose tests
# ---------------------------------------------------------------------------


def test_listener_service_exists_in_local_compose() -> None:
    """'listener' service must be defined in docker-compose.yml (AC-16.3)."""
    data = _load(LOCAL_COMPOSE)
    services = set(data.get("services", {}).keys())
    assert "listener" in services, (
        f"'listener' service missing from docker-compose.yml. "
        f"Found services: {services!r}\n"
        "The #289 lesson: detection/recorder/reconciler must never share the web process."
    )


def test_listener_command_is_run_listener_local() -> None:
    """listener command in docker-compose.yml must invoke run_listener (AC-16.3)."""
    data = _load(LOCAL_COMPOSE)
    cmd = _listener_command(data)
    assert LISTENER_MANAGEMENT_CMD in cmd, (
        f"listener command must invoke 'run_listener', got: {cmd!r}\n"
        "Expected: python manage.py run_listener"
    )


def test_listener_does_not_use_gunicorn_or_daphne_local() -> None:
    """listener must NOT run gunicorn/daphne/uvicorn — it is a separate process (AC-16.3)."""
    data = _load(LOCAL_COMPOSE)
    cmd = _listener_command(data)
    for srv in WSGI_SERVERS:
        assert srv not in cmd, (
            f"listener command must not contain '{srv}' in docker-compose.yml.\n"
            f"Got: {cmd!r}\n"
            "The listener is a distinct detection process, not a web server."
        )


def test_listener_depends_on_db_local() -> None:
    """listener service must depend on 'db' in docker-compose.yml (AC-16.3)."""
    data = _load(LOCAL_COMPOSE)
    deps = _listener_depends(data)
    assert "db" in deps, (
        f"listener service must declare depends_on: db in docker-compose.yml. "
        f"Got depends_on: {deps!r}"
    )


def test_listener_depends_on_redis_local() -> None:
    """listener service must depend on 'redis' in docker-compose.yml (AC-16.3)."""
    data = _load(LOCAL_COMPOSE)
    deps = _listener_depends(data)
    assert "redis" in deps, (
        f"listener service must declare depends_on: redis in docker-compose.yml. "
        f"Got depends_on: {deps!r}"
    )


# ---------------------------------------------------------------------------
# Staging compose tests
# ---------------------------------------------------------------------------


def test_listener_service_exists_in_staging_compose() -> None:
    """'listener' service must be defined in docker-compose.staging.yml (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    services = set(data.get("services", {}).keys())
    assert "listener" in services, (
        f"'listener' service missing from docker-compose.staging.yml. "
        f"Found services: {services!r}\n"
        "The staging VPS stack must include the dedicated listener container."
    )


def test_listener_command_is_run_listener_staging() -> None:
    """listener command in docker-compose.staging.yml must invoke run_listener (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    cmd = _listener_command(data)
    assert LISTENER_MANAGEMENT_CMD in cmd, (
        f"listener command must invoke 'run_listener' in docker-compose.staging.yml, got: {cmd!r}"
    )


def test_listener_does_not_use_gunicorn_or_daphne_staging() -> None:
    """listener in staging must NOT run gunicorn/daphne/uvicorn (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    cmd = _listener_command(data)
    for srv in WSGI_SERVERS:
        assert srv not in cmd, (
            f"listener staging command must not contain '{srv}'. Got: {cmd!r}"
        )


def test_listener_uses_ghcr_image_not_build_staging() -> None:
    """listener in staging must use the GHCR image, not a local build (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    listener_svc = data.get("services", {}).get("listener", {})
    assert "build" not in listener_svc, (
        "listener service must NOT have a 'build:' directive in docker-compose.staging.yml.\n"
        "The VPS runs the pre-built GHCR image."
    )
    image = listener_svc.get("image", "")
    assert GHCR_PREFIX in str(image), (
        f"listener service must use the GHCR image starting with '{GHCR_PREFIX}'.\n"
        f"Got: image={image!r}"
    )


def test_listener_on_solanatrilly_net_staging() -> None:
    """listener in staging must be on the 'solanatrilly_net' network (AC-16.3 isolation)."""
    data = _load(STAGING_COMPOSE)
    listener_svc = data.get("services", {}).get("listener", {})
    svc_networks = listener_svc.get("networks", [])
    if isinstance(svc_networks, dict):
        network_names = set(svc_networks.keys())
    else:
        network_names = set(svc_networks)
    assert EXPECTED_NETWORK in network_names, (
        f"listener service must be attached to '{EXPECTED_NETWORK}' in docker-compose.staging.yml.\n"
        f"Got networks: {network_names!r}\n"
        "Isolation requires every service to join the project-scoped network."
    )


def test_listener_depends_on_db_staging() -> None:
    """listener service must depend on 'db' in docker-compose.staging.yml (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    deps = _listener_depends(data)
    assert "db" in deps, (
        f"listener service must declare depends_on: db in docker-compose.staging.yml. "
        f"Got: {deps!r}"
    )


def test_listener_depends_on_redis_staging() -> None:
    """listener service must depend on 'redis' in docker-compose.staging.yml (AC-16.3)."""
    data = _load(STAGING_COMPOSE)
    deps = _listener_depends(data)
    assert "redis" in deps, (
        f"listener service must declare depends_on: redis in docker-compose.staging.yml. "
        f"Got: {deps!r}"
    )


def test_listener_no_source_volume_mounts_staging() -> None:
    """listener in staging must not mount the source tree (.:/app) (AC-16.3, AC-6.2 rule)."""
    data = _load(STAGING_COMPOSE)
    listener_svc = data.get("services", {}).get("listener", {})
    for vol in listener_svc.get("volumes", []):
        assert not (".:" in str(vol) or str(vol).startswith(".")), (
            f"listener staging service must not mount the source tree. "
            f"Found volume: {vol!r}"
        )
