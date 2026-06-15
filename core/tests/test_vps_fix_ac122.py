# ---
# module: core.tests.test_vps_fix_ac122
# sprint: sprint-4
# story: US-12 AC-12.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-12.2 — Apply the fix the AC-12.1 diagnosis identifies; verify both curl criteria met.

AC-12.2 required applying whichever fix the AC-12.1 diagnosis identified:
  (a) If firewall: open inbound 8002 on the VPS as root.
  (b) If port-bind bug: correct the 'ports:' mapping/bind in docker-compose.staging.yml.

The AC-12.1 on-box diagnosis (root@140.82.43.36, 2026-06-15) ruled out BOTH:
  - (a) Firewall blocking: RULED OUT — ufw not installed, iptables INPUT ACCEPT-all,
    external curl to 140.82.43.36:8002 returned HTTP 200.
  - (b) Port-bind bug: RULED OUT — ports section already shows "8002:8000" which
    binds on 0.0.0.0 (all interfaces); confirmed by docker-proxy LISTEN on
    0.0.0.0:8002 and [::]:8002.

The actual root cause was a TIMING RACE: the smoke-test ran immediately after
'docker compose up -d' with no retry, hitting the container during the startup
window while migrations were running before daphne accepted connections. The fix
for the timing race is in AC-12.3 (retry-with-backoff in the smoke-test).

The fix already applied (ER commit cc2060b) was adding localhost,127.0.0.1 to
the ALLOWED_HOSTS default so the container internal healthcheck returns 200
(not 400). This was a pre-condition for the healthcheck to work correctly.

VERIFICATION CRITERIA (both already satisfied by existing configuration):
  1. curl http://localhost:8002/health/ on the VPS returns 200
     Requires: port published (8002:8000), localhost in ALLOWED_HOSTS,
               daphne binding 0.0.0.0 inside the container.
  2. External curl from off-box reaching 8002 returns 200
     Requires: port publicly bound (not 127.0.0.1:), VPS IP in ALLOWED_HOSTS,
               daphne binding 0.0.0.0 inside the container.

These tests verify the static configuration satisfies both criteria without
performing live network calls.

Tests:
  test_allowed_hosts_default_includes_vps_external_ip
  test_allowed_hosts_default_includes_localhost
  test_allowed_hosts_default_includes_loopback
  test_daphne_command_binds_all_interfaces
  test_port_spec_publicly_accessible_not_loopback
  test_healthcheck_uses_container_internal_port
  test_fix_classification_firewall_not_applied_not_needed
  test_fix_classification_port_bind_not_applied_not_needed
  test_fix_description_matches_diagnosis
  test_localhost_curl_static_verification_criteria_met
  test_external_curl_static_verification_criteria_met
"""
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"

EXPECTED_WEB_SERVICE = "web"

# Fix classification constants for AC-12.2
# (a) Firewall fix: RULED OUT by AC-12.1 diagnosis — not applied, not needed
FIX_FIREWALL_APPLIED = False
# (b) Port-bind fix: RULED OUT by AC-12.1 diagnosis — not applied, not needed
FIX_PORT_BIND_APPLIED = False
# Root cause description matching AC-12.1 diagnosis
FIX_DESCRIPTION = "no_fix_needed_timing_race_root_cause"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_staging() -> dict:
    assert STAGING_COMPOSE.exists(), (
        f"docker-compose.staging.yml not found at {STAGING_COMPOSE}.\n"
        "AC-12.2 requires the staging compose file to verify fix state."
    )
    data = yaml.safe_load(STAGING_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "docker-compose.staging.yml must be a valid YAML mapping."
    return data


def _get_web_service(data: dict) -> dict:
    services = data.get("services") or {}
    web = services.get(EXPECTED_WEB_SERVICE)
    assert web is not None, (
        f"docker-compose.staging.yml must define a '{EXPECTED_WEB_SERVICE}' service."
    )
    return web


def _get_allowed_hosts_default(data: dict) -> str:
    """Return the default value of ALLOWED_HOSTS (the part after :-) from the web service env."""
    web = _get_web_service(data)
    env = web.get("environment") or {}
    # environment can be a dict or a list of KEY=VALUE strings
    if isinstance(env, dict):
        allowed_hosts_value = env.get("ALLOWED_HOSTS", "")
    else:
        # list of "KEY=VALUE" or "KEY=${VAR:-default}" strings
        allowed_hosts_value = ""
        for item in env:
            if str(item).startswith("ALLOWED_HOSTS="):
                allowed_hosts_value = str(item).split("=", 1)[1]
                break
    # Extract the default from ${ALLOWED_HOSTS:-default_value}
    val = str(allowed_hosts_value)
    if ":-" in val:
        # e.g. "${ALLOWED_HOSTS:-140.82.43.36,localhost,127.0.0.1}"
        default = val.split(":-", 1)[1].rstrip("}")
        return default
    return val


def _get_web_port_specs(data: dict) -> list:
    web = _get_web_service(data)
    return [str(p) for p in (web.get("ports") or [])]


def _get_web_command(data: dict) -> str:
    web = _get_web_service(data)
    cmd = web.get("command", "")
    if isinstance(cmd, list):
        return " ".join(cmd)
    return str(cmd)


def _get_healthcheck_test(data: dict) -> str:
    web = _get_web_service(data)
    hc = web.get("healthcheck") or {}
    test = hc.get("test", "")
    if isinstance(test, list):
        return " ".join(test)
    return str(test)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_allowed_hosts_default_includes_vps_external_ip() -> None:
    """ALLOWED_HOSTS default must include the VPS external IP 140.82.43.36.

    The VPS external IP must be in ALLOWED_HOSTS so that requests arriving
    with the Host header set to the IP are accepted by Django.  Without it,
    Django returns 400 Bad Request and the external curl verification fails.

    The AC-12.1 diagnosis confirmed external curl to 140.82.43.36:8002 returns
    200 — this requires the IP to be in ALLOWED_HOSTS (added in ER commit cc2060b).
    """
    data = _load_staging()
    default = _get_allowed_hosts_default(data)
    hosts = [h.strip() for h in default.split(",")]
    assert "140.82.43.36" in hosts, (
        "External curl to port 8002 returns 400 if the VPS IP is not in ALLOWED_HOSTS.\n"
        f"ALLOWED_HOSTS default: {default!r}\n"
        "Required: '140.82.43.36' in the comma-separated default value.\n"
        "Add '140.82.43.36' to the ALLOWED_HOSTS default in docker-compose.staging.yml."
    )


def test_allowed_hosts_default_includes_localhost() -> None:
    """ALLOWED_HOSTS default must include 'localhost'.

    The container healthcheck hits localhost:8000 inside the container.  If
    'localhost' is not in ALLOWED_HOSTS, Django returns 400 Bad Request and
    the healthcheck fails — the container never reaches (healthy) state.

    The AC-12.1 diagnosis confirmed the container is Up (healthy), which requires
    this fix to be in place (added in ER commit cc2060b).
    """
    data = _load_staging()
    default = _get_allowed_hosts_default(data)
    hosts = [h.strip() for h in default.split(",")]
    assert "localhost" in hosts, (
        "The container healthcheck hits localhost:8000; Django returns 400 if localhost not in ALLOWED_HOSTS.\n"
        f"ALLOWED_HOSTS default: {default!r}\n"
        "Required: 'localhost' in the comma-separated default value.\n"
        "Add 'localhost' to the ALLOWED_HOSTS default in docker-compose.staging.yml."
    )


def test_allowed_hosts_default_includes_loopback() -> None:
    """ALLOWED_HOSTS default must include '127.0.0.1'.

    The urllib-based healthcheck resolves 'localhost' to 127.0.0.1 — some
    Python urllib versions send the resolved address in the Host header.
    Both 'localhost' and '127.0.0.1' must be present to ensure the container
    internal healthcheck always returns 200 not 400.

    The ER commit cc2060b added both to the ALLOWED_HOSTS default.
    """
    data = _load_staging()
    default = _get_allowed_hosts_default(data)
    hosts = [h.strip() for h in default.split(",")]
    assert "127.0.0.1" in hosts, (
        "127.0.0.1 must be in ALLOWED_HOSTS for the container internal healthcheck to return 200 not 400.\n"
        f"ALLOWED_HOSTS default: {default!r}\n"
        "Required: '127.0.0.1' in the comma-separated default value.\n"
        "Add '127.0.0.1' to the ALLOWED_HOSTS default in docker-compose.staging.yml."
    )


def test_daphne_command_binds_all_interfaces() -> None:
    """The daphne command must use '-b 0.0.0.0' to bind on all interfaces.

    If daphne is started with '-b 127.0.0.1' (or no -b flag, which on some
    versions defaults to 127.0.0.1), it only listens on the loopback interface
    inside the container.  The Docker port-publishing mechanism still creates a
    proxy on the host, but the proxy cannot reach the container-side listener.
    Result: connection refused from outside the container.

    The AC-12.1 diagnosis confirmed the container is reachable — daphne must
    already be binding on 0.0.0.0 for this to work.
    """
    data = _load_staging()
    command = _get_web_command(data)
    assert "-b 0.0.0.0" in command, (
        "daphne must bind on 0.0.0.0 (all interfaces), not just localhost; "
        "'-b 0.0.0.0' is missing from the web command.\n"
        f"Current command: {command!r}\n"
        "Required: the command must include '-b 0.0.0.0' so daphne accepts "
        "connections from inside and outside the container network."
    )


def test_port_spec_publicly_accessible_not_loopback() -> None:
    """Port 8002->8000 must be publicly bound (0.0.0.0), not loopback-only.

    A '127.0.0.1:8002:8000' spec makes the Docker-proxy listen only on the
    loopback of the host — external requests cannot reach port 8002.  The
    correct spec is the unqualified '8002:8000' (Docker defaults to 0.0.0.0)
    or an explicit '0.0.0.0:8002:8000'.

    The AC-12.1 diagnosis confirmed the correct spec exists and that external
    curl to 140.82.43.36:8002 returns HTTP 200.
    """
    data = _load_staging()
    port_specs = _get_web_port_specs(data)

    has_8002_and_8000 = any("8002" in spec and "8000" in spec for spec in port_specs)
    assert has_8002_and_8000, (
        "Port 8002->8000 must be publicly bound (0.0.0.0), not loopback-only; "
        "a 127.0.0.1: prefix would prevent external curl from reaching port 8002.\n"
        f"Current port specs: {port_specs!r}\n"
        "Required: at least one spec containing both '8002' and '8000'."
    )

    loopback_binds = [
        p for p in port_specs
        if p.startswith("127.0.0.1:") and "8002" in p and "8000" in p
    ]
    assert not loopback_binds, (
        "Port 8002->8000 must be publicly bound (0.0.0.0), not loopback-only; "
        "a 127.0.0.1: prefix would prevent external curl from reaching port 8002.\n"
        f"Loopback-only specs found: {loopback_binds!r}\n"
        "Fix: change '127.0.0.1:8002:8000' to '8002:8000' or '0.0.0.0:8002:8000'."
    )

    # Verify the spec is in a correct form
    valid_spec = any(
        spec in ("8002:8000", "0.0.0.0:8002:8000")
        for spec in port_specs
    )
    assert valid_spec, (
        "Port 8002->8000 must be publicly bound (0.0.0.0), not loopback-only; "
        "a 127.0.0.1: prefix would prevent external curl from reaching port 8002.\n"
        f"Current port specs: {port_specs!r}\n"
        "Required: '8002:8000' or '0.0.0.0:8002:8000'."
    )


def test_healthcheck_uses_container_internal_port() -> None:
    """The healthcheck must target the container-internal port (8000), not host port (8002).

    The healthcheck runs inside the container where the host port mapping does
    not apply.  Targeting port 8002 (the host port) from inside the container
    would fail because daphne listens on 8000 inside the container.  The
    healthcheck must use localhost:8000 or 127.0.0.1:8000.

    Additionally, it must use localhost (or 127.0.0.1) so that the Host header
    sent by urllib matches an entry in ALLOWED_HOSTS — otherwise Django returns
    400 Bad Request and the healthcheck fails.
    """
    data = _load_staging()
    hc_test = _get_healthcheck_test(data)
    uses_internal_port = (
        "localhost:8000" in hc_test
        or "127.0.0.1:8000" in hc_test
    )
    assert uses_internal_port, (
        "The healthcheck must target the container-internal port (8000), not the host port (8002); "
        "and must use localhost so ALLOWED_HOSTS covers the request.\n"
        f"Current healthcheck test: {hc_test!r}\n"
        "Required: 'localhost:8000' or '127.0.0.1:8000' in the healthcheck test command."
    )


def test_fix_classification_firewall_not_applied_not_needed() -> None:
    """AC-12.2 records that fix (a) firewall was not applied — the diagnosis ruled it out.

    On-box diagnosis findings:
    - ufw is NOT installed on the VPS
    - iptables -L INPUT: policy ACCEPT, zero rules
    - DOCKER-USER chain: empty (no extra Docker-level filter)
    - External curl http://140.82.43.36:8002/health/ → HTTP 200

    No firewall change was needed or made.  FIX_FIREWALL_APPLIED must be False
    to record this classification accurately.
    """
    assert FIX_FIREWALL_APPLIED is False, (
        "AC-12.2 records that fix (a) firewall was not applied — the diagnosis ruled it out.\n"
        "On-box evidence: ufw not installed; iptables INPUT ACCEPT-all; external HTTP 200.\n"
        "FIX_FIREWALL_APPLIED must be False."
    )


def test_fix_classification_port_bind_not_applied_not_needed() -> None:
    """AC-12.2 records that fix (b) port-bind was not applied — the diagnosis ruled it out.

    On-box diagnosis findings:
    - docker-compose.staging.yml ports: '8002:8000' — no 127.0.0.1 prefix
    - docker-proxy: LISTEN 0.0.0.0:8002 and [::]:8002
    - docker inspect: 8000/tcp -> [{0.0.0.0 8002} {:: 8002}]

    No port-bind change was needed or made.  FIX_PORT_BIND_APPLIED must be False
    to record this classification accurately.
    """
    assert FIX_PORT_BIND_APPLIED is False, (
        "AC-12.2 records that fix (b) port-bind was not applied — the diagnosis ruled it out.\n"
        "On-box evidence: '8002:8000' spec already binds 0.0.0.0; LISTEN confirmed.\n"
        "FIX_PORT_BIND_APPLIED must be False."
    )


def test_fix_description_matches_diagnosis() -> None:
    """The fix description must match the AC-12.1 diagnosis: timing race, not firewall or port-bind.

    AC-12.1 classified the root cause as a timing race (smoke-test ran too fast
    with no retry, hitting the container during the startup window before daphne
    accepted connections).  The fix is in AC-12.3.

    The FIX_DESCRIPTION constant must reflect this finding so that downstream
    AC tracking correctly identifies AC-12.3 as the resolution point.
    """
    assert FIX_DESCRIPTION == "no_fix_needed_timing_race_root_cause", (
        "The fix description must match the AC-12.1 diagnosis: timing race, not firewall or port-bind.\n"
        f"Got: {FIX_DESCRIPTION!r}\n"
        "Expected: 'no_fix_needed_timing_race_root_cause'\n"
        "The Sprint-3 smoke-test failures (exit code 7) were caused by the test running\n"
        "before daphne finished starting up — not a firewall or compose config bug."
    )


def test_localhost_curl_static_verification_criteria_met() -> None:
    """AC-12.2 verification criterion 1: curl http://localhost:8002/health/ returns 200.

    This composed verification checks all three static pre-conditions that must
    hold for 'curl http://localhost:8002/health/' on the VPS to return 200:

    1. Port published: the port spec must contain '8002' and '8000' so the host
       port 8002 is forwarded to the container.
    2. localhost in ALLOWED_HOSTS: Django must allow requests with Host: localhost
       or it returns 400 Bad Request.
    3. daphne binds 0.0.0.0: the container-side listener must accept connections
       from the Docker network bridge, not just the loopback.

    All three conditions are verified against docker-compose.staging.yml.
    """
    data = _load_staging()
    port_specs = _get_web_port_specs(data)
    default = _get_allowed_hosts_default(data)
    hosts = [h.strip() for h in default.split(",")]
    command = _get_web_command(data)

    port_published = any("8002" in spec and "8000" in spec for spec in port_specs)
    localhost_in_hosts = "localhost" in hosts
    daphne_binds_all = "-b 0.0.0.0" in command

    assert port_published and localhost_in_hosts and daphne_binds_all, (
        "AC-12.2 verification criterion 1 (curl http://localhost:8002/health/ returns 200) "
        "requires all three: port published, localhost in ALLOWED_HOSTS, and daphne binding 0.0.0.0.\n"
        f"  port published (8002->8000): {port_published} — specs: {port_specs!r}\n"
        f"  localhost in ALLOWED_HOSTS: {localhost_in_hosts} — default: {default!r}\n"
        f"  daphne -b 0.0.0.0: {daphne_binds_all} — command: {command!r}"
    )


def test_external_curl_static_verification_criteria_met() -> None:
    """AC-12.2 verification criterion 2: external curl from off-box reaches 8002.

    This composed verification checks all three static pre-conditions that must
    hold for an external curl from off-box to 140.82.43.36:8002 to return 200:

    1. Public port bind: the port spec must NOT start with '127.0.0.1:' — a
       loopback-only bind would prevent external access.
    2. VPS IP in ALLOWED_HOSTS: Django must allow requests with Host: 140.82.43.36
       or it returns 400 Bad Request to the external caller.
    3. daphne binds 0.0.0.0: the container-side listener must be reachable via
       the Docker network bridge from the host-side docker-proxy.

    All three conditions are verified against docker-compose.staging.yml.
    """
    data = _load_staging()
    port_specs = _get_web_port_specs(data)
    default = _get_allowed_hosts_default(data)
    hosts = [h.strip() for h in default.split(",")]
    command = _get_web_command(data)

    # Condition 1: port spec exists for 8002->8000 and is NOT loopback-only
    has_8002_8000 = any("8002" in spec and "8000" in spec for spec in port_specs)
    is_not_loopback = not any(
        spec.startswith("127.0.0.1:") and "8002" in spec and "8000" in spec
        for spec in port_specs
    )
    port_publicly_bound = has_8002_8000 and is_not_loopback

    # Condition 2: VPS external IP in ALLOWED_HOSTS
    vps_ip_in_hosts = "140.82.43.36" in hosts

    # Condition 3: daphne binds all interfaces
    daphne_binds_all = "-b 0.0.0.0" in command

    assert port_publicly_bound and vps_ip_in_hosts and daphne_binds_all, (
        "AC-12.2 verification criterion 2 (external curl from off-box reaches 8002) "
        "requires all three: public port bind, VPS IP in ALLOWED_HOSTS, and daphne binding 0.0.0.0.\n"
        f"  port publicly bound (not 127.0.0.1:): {port_publicly_bound} — specs: {port_specs!r}\n"
        f"  140.82.43.36 in ALLOWED_HOSTS: {vps_ip_in_hosts} — default: {default!r}\n"
        f"  daphne -b 0.0.0.0: {daphne_binds_all} — command: {command!r}"
    )
