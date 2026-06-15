# ---
# module: core.tests.test_staging_compose_ac62
# sprint: sprint-2
# story: US-6 AC-6.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-6.2 — docker-compose.staging.yml structural verification.

Asserts every requirement of AC-6.2 (PRD §15.4):
  1. docker-compose.staging.yml exists at the repo root.
  2. Compose project name is 'solanatrilly' (top-level name: field).
  3. Web service binds to host port 8002.
  4. Postgres volume is distinct from local dev (named solanatrilly_pgdata, not 'pgdata').
  5. Postgres DB name is distinct from local dev ('solanatrilly', not 'app').
  6. A dedicated Docker network is defined (solanatrilly_net).
  7. All services are attached to the dedicated network.
  8. Web service uses a GHCR image — no 'build:' directive (VPS runs the image, not source).
  9. No source-code volume mounts (.:/app) — VPS runs the image as-is.
 10. docker-compose.staging.yml is the ONLY staging/VPS compose file in the repo
     (no duplicate compose files for the VPS — PRD §15.4 "ONLY").

Tests:
  test_staging_compose_exists
  test_staging_compose_project_name
  test_web_port_is_8002
  test_postgres_volume_is_distinct
  test_postgres_db_name_is_distinct
  test_dedicated_network_defined
  test_all_services_on_dedicated_network
  test_web_uses_ghcr_image_not_build
  test_no_source_volume_mounts
  test_no_other_vps_compose_files
"""
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
LOCAL_COMPOSE = REPO_ROOT / "docker-compose.yml"

EXPECTED_PROJECT = "solanatrilly"
EXPECTED_WEB_HOST_PORT = 8002
EXPECTED_NETWORK = "solanatrilly_net"
EXPECTED_VOLUME = "solanatrilly_pgdata"
EXPECTED_DB_NAME = "solanatrilly"
GHCR_PREFIX = "ghcr.io/asimquick/solanatrilly"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_staging() -> dict:
    assert STAGING_COMPOSE.exists(), (
        f"docker-compose.staging.yml not found at {STAGING_COMPOSE}.\n"
        "AC-6.2 requires a docker-compose.staging.yml at the repo root."
    )
    data = yaml.safe_load(STAGING_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), (
        "docker-compose.staging.yml must be a valid YAML mapping."
    )
    return data


def _port_mapping_host(port_spec) -> int | None:
    """Extract host port from a Docker Compose port spec (string or int)."""
    s = str(port_spec)
    if ":" in s:
        return int(s.split(":")[0])
    return int(s)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_staging_compose_exists() -> None:
    """docker-compose.staging.yml must exist at the repo root (AC-6.2)."""
    assert STAGING_COMPOSE.exists(), (
        f"docker-compose.staging.yml not found at {STAGING_COMPOSE}.\n"
        "Create docker-compose.staging.yml for AC-6.2."
    )


def test_staging_compose_project_name() -> None:
    """Top-level 'name: solanatrilly' must be set so -p solanatrilly is the project (AC-6.2)."""
    data = _load_staging()
    name = data.get("name")
    assert name == EXPECTED_PROJECT, (
        f"docker-compose.staging.yml must declare 'name: solanatrilly' at the top level.\n"
        f"Got: name={name!r}\n"
        "This enforces the -p solanatrilly compose project scope (PRD §15.4)."
    )


def test_web_port_is_8002() -> None:
    """Web service must bind to host port 8002 (solanaBilly owns 8001) (AC-6.2)."""
    data = _load_staging()
    web_svc = data["services"]["web"]
    ports = web_svc.get("ports", [])
    assert ports, "web service must declare at least one port mapping."
    host_ports = [_port_mapping_host(p) for p in ports]
    assert EXPECTED_WEB_HOST_PORT in host_ports, (
        f"web service must map host port {EXPECTED_WEB_HOST_PORT}. "
        f"Got host ports: {host_ports!r}\n"
        "solanaBilly owns port 8001 — solanatrilly must use 8002 (AC-6.2)."
    )


def test_postgres_volume_is_distinct() -> None:
    """Postgres volume must be named 'solanatrilly_pgdata' — distinct from dev 'pgdata' (AC-6.2)."""
    data = _load_staging()
    top_level_volumes = set(data.get("volumes", {}).keys())
    assert EXPECTED_VOLUME in top_level_volumes, (
        f"docker-compose.staging.yml must declare a top-level volume '{EXPECTED_VOLUME}'.\n"
        f"Got top-level volumes: {top_level_volumes!r}\n"
        "The volume must be named distinctly from the local dev 'pgdata' volume (AC-6.2)."
    )
    db_svc = data["services"]["db"]
    db_volumes = db_svc.get("volumes", [])
    db_vol_str = " ".join(str(v) for v in db_volumes)
    assert EXPECTED_VOLUME in db_vol_str, (
        f"db service must mount the '{EXPECTED_VOLUME}' volume.\n"
        f"Got db volumes: {db_volumes!r}"
    )


def test_postgres_db_name_is_distinct() -> None:
    """Postgres POSTGRES_DB must be 'solanatrilly' — distinct from dev 'app' (AC-6.2)."""
    data = _load_staging()
    db_svc = data["services"]["db"]
    env = db_svc.get("environment", {})
    if isinstance(env, list):
        env_dict = {}
        for item in env:
            if "=" in str(item):
                k, _, v = str(item).partition("=")
                env_dict[k] = v
        env = env_dict
    pg_db = str(env.get("POSTGRES_DB", ""))
    assert pg_db == EXPECTED_DB_NAME, (
        f"db service POSTGRES_DB must be '{EXPECTED_DB_NAME}' (distinct from dev 'app').\n"
        f"Got: POSTGRES_DB={pg_db!r} (AC-6.2)."
    )


def test_dedicated_network_defined() -> None:
    """A dedicated Docker network 'solanatrilly_net' must be defined (AC-6.2)."""
    data = _load_staging()
    networks = data.get("networks", {})
    assert EXPECTED_NETWORK in networks, (
        f"docker-compose.staging.yml must define a top-level network '{EXPECTED_NETWORK}'.\n"
        f"Got networks: {list(networks.keys())!r}\n"
        "A distinct Docker network isolates the stack from solanaBilly (AC-6.2)."
    )


def test_all_services_on_dedicated_network() -> None:
    """Every service must be attached to 'solanatrilly_net' (AC-6.2 isolation)."""
    data = _load_staging()
    services = data.get("services", {})
    violations: list[str] = []
    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        svc_networks = svc_def.get("networks", [])
        if isinstance(svc_networks, dict):
            svc_network_names = set(svc_networks.keys())
        else:
            svc_network_names = set(svc_networks)
        if EXPECTED_NETWORK not in svc_network_names:
            violations.append(
                f"  service '{svc_name}' networks={svc_network_names!r} "
                f"— missing '{EXPECTED_NETWORK}'"
            )
    assert not violations, (
        "Every service must be attached to the dedicated network "
        f"'{EXPECTED_NETWORK}':\n" + "\n".join(violations)
    )


def test_web_uses_ghcr_image_not_build() -> None:
    """Web service must use the GHCR image, not a local 'build:' directive (AC-6.2).

    The VPS pulls the tested image from GHCR — it never builds from source.
    """
    data = _load_staging()
    web_svc = data["services"]["web"]
    assert "build" not in web_svc, (
        "web service must NOT have a 'build:' directive in docker-compose.staging.yml.\n"
        "The VPS runs the pre-built GHCR image; 'build:' is for local dev only (AC-6.2)."
    )
    image = web_svc.get("image", "")
    assert GHCR_PREFIX in str(image), (
        f"web service must use the GHCR image starting with '{GHCR_PREFIX}'.\n"
        f"Got: image={image!r} (AC-6.2)."
    )


def test_no_source_volume_mounts() -> None:
    """No service may mount the source tree (.:/app) — VPS runs the image as-is (AC-6.2)."""
    data = _load_staging()
    services = data.get("services", {})
    violations: list[str] = []
    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        for vol in svc_def.get("volumes", []):
            if ".:" in str(vol) or str(vol).startswith("."):
                violations.append(f"  service '{svc_name}': volume {vol!r}")
    assert not violations, (
        "No service in docker-compose.staging.yml may mount the source tree "
        "(.:/app or relative host paths).\n"
        "Found source mounts:\n" + "\n".join(violations) + "\n\n"
        "The VPS runs the GHCR image as-is — source mounts are for local dev only (AC-6.2)."
    )


def test_no_other_vps_compose_files() -> None:
    """docker-compose.staging.yml must be the ONLY VPS compose file in the repo (PRD §15.4).

    Scans for any other compose file that looks like it targets staging or VPS.
    The local dev docker-compose.yml is expected and excluded.
    """
    forbidden_patterns = [
        "docker-compose.staging*.yml",
        "docker-compose.vps*.yml",
        "docker-compose.prod*.yml",
        "docker-compose.deploy*.yml",
    ]
    extra_files: list[Path] = []
    for pattern in forbidden_patterns:
        for found in REPO_ROOT.glob(pattern):
            if found != STAGING_COMPOSE:
                extra_files.append(found)

    assert not extra_files, (
        "PRD §15.4 requires docker-compose.staging.yml to be the ONLY VPS compose file.\n"
        "Found extra VPS-scoped compose files:\n"
        + "\n".join(f"  {f.relative_to(REPO_ROOT)}" for f in extra_files)
    )
