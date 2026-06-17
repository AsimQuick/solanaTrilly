# ---
# module: core.tests.test_frontend_compose_ac481
# sprint: sprint-10
# story: US-48 AC-48.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pyyaml, pathlib
# ---
"""AC-48.1 — structural tests for the React+Vite frontend compose scaffold.

Verifies:
  1. 'frontend' service exists in docker-compose.yml (local dev).
  2. 'frontend' service exists in docker-compose.staging.yml (staging).
  3. No host port in either compose file maps to 8001 (solanaBilly's port — no collision).
  4. Staging compose remains isolatable with -p solanatrilly
     (top-level name: solanatrilly is present — unchanged by this AC).
  5. Local compose frontend service uses a build directive (Node/Vite in-container).
  6. Staging compose frontend service uses a GHCR image (no source build on VPS).
  7. Staging frontend service is on the solanatrilly_net network (isolation).
  8. No source-tree volume mount in the staging frontend service.
  9. Frontend scaffold files exist: Dockerfile, package.json, vite.config.js,
     index.html, src/main.jsx, src/App.jsx.
 10. React app mount point present: index.html references #root and src/main.jsx.
 11. Frontend Dockerfile contains both 'development' and 'production' build stages.
 12. package.json declares 'dev' and 'build' scripts (Vite dev server + prod build).
 13. Local compose frontend port does NOT collide with 8001 or 8002.
 14. Staging compose frontend service does NOT expose host port 8001.

Tests:
  test_frontend_service_in_local_compose
  test_frontend_service_in_staging_compose
  test_no_host_port_8001_in_local_compose
  test_no_host_port_8001_in_staging_compose
  test_staging_project_name_unchanged
  test_local_frontend_uses_build_directive
  test_staging_frontend_uses_ghcr_image
  test_staging_frontend_on_solanatrilly_net
  test_staging_frontend_no_source_volume_mount
  test_frontend_scaffold_files_exist
  test_react_mount_point_in_index_html
  test_main_jsx_creates_root
  test_frontend_dockerfile_has_dev_and_prod_stages
  test_package_json_has_dev_and_build_scripts
  test_local_frontend_port_not_8001_or_8002
"""
import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_COMPOSE = REPO_ROOT / "docker-compose.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
FRONTEND_DIR = REPO_ROOT / "frontend"

SOLANABILLY_PORT = 8001
STAGING_WEB_PORT = 8002
EXPECTED_STAGING_PROJECT = "solanatrilly"
EXPECTED_STAGING_NETWORK = "solanatrilly_net"
FRONTEND_GHCR_PREFIX = "ghcr.io/asimquick/solanatrilly-frontend"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must be a valid YAML mapping"
    return data


def _all_host_ports(compose: dict) -> list[int]:
    """Return all host port numbers declared across all services."""
    ports: list[int] = []
    for svc in compose.get("services", {}).values():
        if not isinstance(svc, dict):
            continue
        for spec in svc.get("ports", []):
            s = str(spec)
            host_part = s.split(":")[0] if ":" in s else s
            try:
                ports.append(int(host_part))
            except ValueError:
                pass
    return ports


def _service_host_ports(svc_def: dict) -> list[int]:
    """Return host port numbers for a single service definition."""
    ports: list[int] = []
    for spec in svc_def.get("ports", []):
        s = str(spec)
        host_part = s.split(":")[0] if ":" in s else s
        try:
            ports.append(int(host_part))
        except ValueError:
            pass
    return ports


# ---------------------------------------------------------------------------
# Tests — compose structural assertions
# ---------------------------------------------------------------------------


def test_frontend_service_in_local_compose() -> None:
    """'frontend' service must exist in docker-compose.yml (AC-48.1)."""
    compose = _load_yaml(LOCAL_COMPOSE)
    services = set(compose.get("services", {}).keys())
    assert "frontend" in services, (
        "'frontend' service not found in docker-compose.yml.\n"
        "AC-48.1 requires a 'frontend' (Vite dev) container in the local compose."
    )


def test_frontend_service_in_staging_compose() -> None:
    """'frontend' service must exist in docker-compose.staging.yml (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    services = set(compose.get("services", {}).keys())
    assert "frontend" in services, (
        "'frontend' service not found in docker-compose.staging.yml.\n"
        "AC-48.1 requires the 'frontend' service in BOTH compose files."
    )


def test_no_host_port_8001_in_local_compose() -> None:
    """No host port in docker-compose.yml may be 8001 (solanaBilly — no collision) (AC-48.1)."""
    compose = _load_yaml(LOCAL_COMPOSE)
    host_ports = _all_host_ports(compose)
    assert SOLANABILLY_PORT not in host_ports, (
        f"docker-compose.yml maps host port {SOLANABILLY_PORT} — "
        "solanaBilly owns that port. Choose a different host port (AC-48.1)."
    )


def test_no_host_port_8001_in_staging_compose() -> None:
    """No host port in docker-compose.staging.yml may be 8001 (solanaBilly) (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    host_ports = _all_host_ports(compose)
    assert SOLANABILLY_PORT not in host_ports, (
        f"docker-compose.staging.yml maps host port {SOLANABILLY_PORT} — "
        "solanaBilly owns that port. No staging service may bind 8001 (AC-48.1)."
    )


def test_staging_project_name_unchanged() -> None:
    """Staging compose must still declare name: solanatrilly (-p isolatability) (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    name = compose.get("name")
    assert name == EXPECTED_STAGING_PROJECT, (
        f"docker-compose.staging.yml 'name:' must be '{EXPECTED_STAGING_PROJECT}' "
        f"for -p solanatrilly isolation. Got: {name!r}"
    )


def test_local_frontend_uses_build_directive() -> None:
    """Local frontend service must use a 'build:' directive (Vite runs IN the container) (AC-48.1)."""
    compose = _load_yaml(LOCAL_COMPOSE)
    frontend_svc = compose["services"]["frontend"]
    assert "build" in frontend_svc, (
        "Local 'frontend' service must have a 'build:' directive.\n"
        "Docker Rules: Node/Vite NEVER installed on the host — must run in-container (AC-48.1)."
    )


def test_staging_frontend_uses_ghcr_image() -> None:
    """Staging frontend service must reference the GHCR image (no build on VPS) (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    frontend_svc = compose["services"]["frontend"]
    assert "build" not in frontend_svc, (
        "Staging 'frontend' service must NOT have a 'build:' directive.\n"
        "The VPS pulls the pre-built GHCR image — never builds from source."
    )
    image = str(frontend_svc.get("image", ""))
    assert FRONTEND_GHCR_PREFIX in image, (
        f"Staging 'frontend' service must use an image starting with '{FRONTEND_GHCR_PREFIX}'.\n"
        f"Got: image={image!r} (AC-48.1)."
    )


def test_staging_frontend_on_solanatrilly_net() -> None:
    """Staging frontend service must be on solanatrilly_net (project isolation) (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    frontend_svc = compose["services"]["frontend"]
    svc_networks = frontend_svc.get("networks", [])
    if isinstance(svc_networks, dict):
        network_names = set(svc_networks.keys())
    else:
        network_names = set(svc_networks)
    assert EXPECTED_STAGING_NETWORK in network_names, (
        f"Staging 'frontend' service must be attached to '{EXPECTED_STAGING_NETWORK}'.\n"
        f"Got networks: {network_names!r}\n"
        "All staging services must be on the dedicated network for -p solanatrilly isolation."
    )


def test_staging_frontend_no_source_volume_mount() -> None:
    """Staging frontend service must NOT mount the source tree (VPS runs image as-is) (AC-48.1)."""
    compose = _load_yaml(STAGING_COMPOSE)
    frontend_svc = compose["services"]["frontend"]
    violations = [
        vol
        for vol in frontend_svc.get("volumes", [])
        if ".:" in str(vol) or str(vol).startswith(".")
    ]
    assert not violations, (
        "Staging 'frontend' service must not mount the source tree.\n"
        f"Found source mounts: {violations!r}\n"
        "The VPS runs the GHCR image as-is — no .:/app mounts in staging (AC-48.1)."
    )


# ---------------------------------------------------------------------------
# Tests — frontend scaffold file existence
# ---------------------------------------------------------------------------


def test_frontend_scaffold_files_exist() -> None:
    """Core frontend scaffold files must exist (AC-48.1)."""
    required = [
        FRONTEND_DIR / "Dockerfile",
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "package-lock.json",
        FRONTEND_DIR / "vite.config.js",
        FRONTEND_DIR / "index.html",
        FRONTEND_DIR / "src" / "main.jsx",
        FRONTEND_DIR / "src" / "App.jsx",
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in required if not p.exists()]
    assert not missing, (
        "Frontend scaffold files are missing:\n"
        + "\n".join(f"  {m}" for m in missing)
        + "\nAC-48.1 requires a complete React+Vite scaffold."
    )


def test_react_mount_point_in_index_html() -> None:
    """index.html must reference the #root mount point and src/main.jsx (AC-48.1)."""
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="root"' in html or "id='root'" in html, (
        "frontend/index.html must contain <div id=\"root\"> — the React mount point (AC-48.1)."
    )
    assert "main.jsx" in html or "main.js" in html, (
        "frontend/index.html must reference src/main.jsx as the Vite entry point (AC-48.1)."
    )


def test_main_jsx_creates_root() -> None:
    """src/main.jsx must call createRoot to mount the React app (AC-48.1)."""
    main_jsx = (FRONTEND_DIR / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "createRoot" in main_jsx, (
        "frontend/src/main.jsx must call createRoot() to mount the React app.\n"
        "This is the 'React app mounts' verification required by AC-48.1."
    )
    assert "root" in main_jsx, (
        "frontend/src/main.jsx must reference the #root DOM element (AC-48.1)."
    )


def test_frontend_dockerfile_has_dev_and_prod_stages() -> None:
    """frontend/Dockerfile must declare both 'development' and 'production' build stages (AC-48.1)."""
    dockerfile = (FRONTEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "AS development" in dockerfile, (
        "frontend/Dockerfile must have a 'development' stage (for local Vite dev server).\n"
        "Expected: FROM node:... AS development (AC-48.1)."
    )
    assert "AS production" in dockerfile, (
        "frontend/Dockerfile must have a 'production' stage (for GHCR/staging image).\n"
        "Expected: FROM nginx:... AS production (AC-48.1)."
    )


def test_package_json_has_dev_and_build_scripts() -> None:
    """package.json must declare 'dev' (Vite dev server) and 'build' scripts (AC-48.1)."""
    pkg = json.loads((FRONTEND_DIR / "package.json").read_text(encoding="utf-8"))
    scripts = pkg.get("scripts", {})
    assert "dev" in scripts, (
        "frontend/package.json must declare a 'dev' script (Vite dev server) (AC-48.1).\n"
        f"Got scripts: {list(scripts.keys())!r}"
    )
    assert "build" in scripts, (
        "frontend/package.json must declare a 'build' script (production build) (AC-48.1).\n"
        f"Got scripts: {list(scripts.keys())!r}"
    )


def test_local_frontend_port_not_8001_or_8002() -> None:
    """Local 'frontend' service must not bind host port 8001 (solanaBilly) or 8002 (staging web)."""
    compose = _load_yaml(LOCAL_COMPOSE)
    frontend_svc = compose["services"]["frontend"]
    host_ports = _service_host_ports(frontend_svc)
    for forbidden in (SOLANABILLY_PORT, STAGING_WEB_PORT):
        assert forbidden not in host_ports, (
            f"Local 'frontend' service must not bind host port {forbidden}.\n"
            f"Got host ports: {host_ports!r}\n"
            "Use a distinct port (e.g. 5173, the Vite default) to avoid collisions (AC-48.1)."
        )
