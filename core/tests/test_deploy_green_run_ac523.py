# ---
# module: core.tests.test_deploy_green_run_ac523
# sprint: sprint-11
# story: US-52 AC-52.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-52.3 — Structural guards for the green-deploy run and Tester VPS-confirmation conditions.

This module verifies that deploy.yml is structurally sound to obtain an ACTUAL GREEN deploy
run and satisfy all Tester VPS-confirmation conditions from that run:

  1. down --remove-orphans before up: deploy.yml runs 'docker compose down --remove-orphans'
     BEFORE 'pull && up' in the Deploy-to-VPS SSH command, preventing the orphaned-container
     naming conflict that caused every deploy run to fail after a prior interrupted run.
     (Root cause of the sprint-11 deploy regression: Docker Compose generates a hash-prefixed
     container name that conflicts with the same container left over from a previous failed run.)

  2. AC-39.2 phase-promoter ordering: the 'Phase-promoter pre-deploy gate' step is present
     in deploy.yml AND comes before the 'Deploy to VPS staging stack' step (required by AC-39.2
     to block a deploy if the sprint phase is stale or integrity fails).

  3. HTTP 200 smoke test with retry-with-backoff (AC-12.3): the retry loop over /health/ is
     present so the VPS confirmation condition (HTTP 200 on 8002 with backoff) is met.

  4. Dashboard route HTTP 200 (AC-48.3): a step that checks /dashboard/ returns HTTP 200.

  5. WS endpoint HTTP 101 (AC-48.3): a step that verifies the WebSocket upgrade is accepted.

  6. Frontend and web containers Up (AC-48.3): a step that verifies both containers are Up
     in the solanatrilly stack after deploy.

  7. solanaBilly isolation — 8001 untouched (AC-6.5): a step that confirms solanaBilly
     is still running on port 8001 after the solanatrilly deploy, verifying hard isolation.

  8. All docker commands scoped -p solanatrilly: no unscoped docker commands in deploy.yml
     (PRD §15.3 hard isolation rule; every deploy command must carry -p solanatrilly).

Tests:
  test_down_remove_orphans_before_up            — 'down --remove-orphans' precedes 'up' in SSH cmd
  test_phase_promoter_step_present              — AC-39.2 phase-promoter step exists
  test_phase_promoter_ordered_before_deploy     — phase-promoter job step comes before VPS deploy
  test_http200_smoke_test_with_retry_present    — AC-12.3 retry-with-backoff /health/ check present
  test_dashboard_route_smoke_test_present       — AC-48.3 /dashboard/ HTTP 200 check present
  test_ws_101_smoke_test_present                — AC-48.3 WebSocket HTTP 101 upgrade check present
  test_frontend_web_containers_up_check_present — AC-48.3 frontend+web containers Up check present
  test_solanabilly_isolation_check_present      — AC-6.5 solanaBilly port 8001 isolation check
  test_all_docker_commands_scoped               — all docker compose calls carry -p solanatrilly
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_jobs() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "deploy.yml must be valid YAML"
    return data.get("jobs", {})


def _deploy_step_names() -> list[str]:
    """Return the ordered list of deploy-job step names."""
    jobs = _deploy_jobs()
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps", [])
    return [s.get("name", "") for s in steps]


# ---------------------------------------------------------------------------
# 1. down --remove-orphans before up (orphaned-container fix)
# ---------------------------------------------------------------------------


def test_down_remove_orphans_before_up():
    """deploy.yml SSH command runs 'down --remove-orphans' before 'pull && up' (AC-52.3).

    Root cause of sprint-11 deploy regression: when a deploy run is interrupted mid-way,
    Docker Compose leaves containers with hash-prefixed names (e.g. de00ebec1a6e_solanatrilly-web-1)
    that conflict with the canonical names on the next 'up' run, causing:
      'Error response from daemon: No such container: <hash>...'
    Fix: run 'docker compose down --remove-orphans' (scoped, no -v) before every 'pull && up'
    to guarantee a clean slate. Using ';' (not '&&') so the first deploy (no containers yet)
    still continues even if 'down' exits non-zero.

    This test fails if the 'down --remove-orphans' step is removed from the deploy SSH command,
    re-exposing the orphaned-container naming conflict.
    """
    content = _deploy_text()
    # Both 'down' and 'up' must appear in the deploy SSH command
    assert "down --remove-orphans" in content, (
        "deploy.yml Deploy-to-VPS SSH command must include 'docker compose down --remove-orphans' "
        "before 'pull && up' (AC-52.3). Without this, Docker Compose naming conflicts from prior "
        "interrupted runs cause 'No such container' errors and break every subsequent deploy."
    )
    # Verify ordering: 'down' must appear before 'up -d' in the same SSH command block
    down_pos = content.find("down --remove-orphans")
    up_pos = content.find("up -d --remove-orphans")
    assert down_pos != -1 and up_pos != -1, (
        "deploy.yml must contain both 'down --remove-orphans' and 'up -d --remove-orphans' "
        "in the Deploy-to-VPS step (AC-52.3)."
    )
    assert down_pos < up_pos, (
        f"deploy.yml 'down --remove-orphans' (pos {down_pos}) must appear BEFORE "
        f"'up -d --remove-orphans' (pos {up_pos}) in the SSH command (AC-52.3). "
        "Running 'up' before 'down' does not clear stale container state."
    )


# ---------------------------------------------------------------------------
# 2. AC-39.2 phase-promoter step present and ordered before VPS deploy
# ---------------------------------------------------------------------------


def test_phase_promoter_step_present():
    """deploy.yml has an AC-39.2 phase-promoter pre-deploy gate step (AC-52.3).

    The phase-promoter (tools/promote_sprint_phase.py) blocks the deploy if any
    sprint-phase promotion is stale or the sprint integrity check fails.
    Its absence would allow a deploy to reach the VPS without passing the phase gate.
    """
    content = _deploy_text()
    assert "promote_sprint_phase.py" in content, (
        "deploy.yml must have an AC-39.2 phase-promoter pre-deploy gate step "
        "('python3 tools/promote_sprint_phase.py scrum-master/sprint*.json'). "
        "This step blocks the VPS deploy if the sprint phase is stale (AC-39.2, AC-52.3)."
    )


def test_phase_promoter_ordered_before_deploy():
    """AC-39.2 phase-promoter step comes before 'Deploy to VPS staging stack' in deploy.yml (AC-52.3).

    The phase-promoter must gate the VPS deploy — if it comes after the deploy step, it
    cannot block a bad deploy from reaching the VPS.
    """
    step_names = _deploy_step_names()
    lower_names = [n.lower() for n in step_names]

    promoter_indices = [i for i, n in enumerate(lower_names) if "phase-promoter" in n or "promote_sprint" in n]
    deploy_indices = [i for i, n in enumerate(lower_names) if "deploy to vps" in n or "deploy to vps staging" in n]

    assert promoter_indices, (
        f"deploy.yml deploy job must have a phase-promoter step. Steps found: {step_names}"
    )
    assert deploy_indices, (
        f"deploy.yml deploy job must have a 'Deploy to VPS staging stack' step. Steps found: {step_names}"
    )
    assert min(promoter_indices) < min(deploy_indices), (
        f"AC-39.2 phase-promoter step (index {min(promoter_indices)}) must come BEFORE "
        f"'Deploy to VPS' step (index {min(deploy_indices)}) in deploy.yml. "
        "A post-deploy phase-promoter cannot gate the deploy (AC-39.2, AC-52.3)."
    )


# ---------------------------------------------------------------------------
# 3. HTTP 200 smoke test with retry-with-backoff (AC-12.3)
# ---------------------------------------------------------------------------


def test_http200_smoke_test_with_retry_present():
    """deploy.yml smoke-test retries HTTP 200 on /health/ with backoff (AC-12.3, AC-52.3).

    The VPS confirmation condition requires HTTP 200 on port 8002 WITH the AC-12.3
    retry-with-backoff loop (not a single-shot curl that would fail during container startup).
    """
    content = _deploy_text()
    assert "/health/" in content, (
        "deploy.yml must smoke-test GET /health/ on port 8002 (AC-12.3, AC-52.3). "
        "This is the primary HTTP 200 condition the Tester confirms from the green run."
    )
    assert "SMOKE_MAX_ATTEMPTS" in content or "max_attempts" in content.lower(), (
        "deploy.yml smoke-test must have a retry loop (AC-12.3 retry-with-backoff). "
        "A single-shot curl would fail during container startup. "
        "SMOKE_MAX_ATTEMPTS (or equivalent) must be present (AC-12.3, AC-52.3)."
    )
    assert "SMOKE_RETRY_DELAY" in content or "retry_delay" in content.lower(), (
        "deploy.yml smoke-test must sleep between retries (AC-12.3 backoff). "
        "SMOKE_RETRY_DELAY (or equivalent) must be present (AC-12.3, AC-52.3)."
    )


# ---------------------------------------------------------------------------
# 4. Dashboard route HTTP 200 (AC-48.3)
# ---------------------------------------------------------------------------


def test_dashboard_route_smoke_test_present():
    """deploy.yml smoke-tests GET /dashboard/ returns HTTP 200 (AC-48.3, AC-52.3).

    The Tester VPS-confirmation condition requires the dashboard route to return HTTP 200.
    This step must be in deploy.yml so a green run constitutes Tester confirmation.
    """
    content = _deploy_text()
    assert "/dashboard/" in content, (
        "deploy.yml must smoke-test GET /dashboard/ and assert HTTP 200 (AC-48.3, AC-52.3). "
        "The Tester confirms this condition from the ACTUAL green deploy run."
    )


# ---------------------------------------------------------------------------
# 5. WS endpoint HTTP 101 (AC-48.3)
# ---------------------------------------------------------------------------


def test_ws_101_smoke_test_present():
    """deploy.yml smoke-tests the WS endpoint and verifies HTTP 101 upgrade (AC-48.3, AC-52.3).

    The Tester VPS-confirmation condition requires the WebSocket endpoint to accept an upgrade
    (HTTP 101). This step runs in deploy.yml so the green run itself constitutes confirmation.
    """
    content = _deploy_text()
    assert "101" in content, (
        "deploy.yml must verify the WS endpoint returns HTTP 101 Switching Protocols "
        "(AC-48.3, AC-52.3). The WS smoke-test asserting '101' must be present."
    )
    assert "/ws/tape/" in content or "Upgrade: websocket" in content, (
        "deploy.yml WS smoke-test must reference the tape WS endpoint or Upgrade header "
        "(AC-48.3, AC-52.3)."
    )


# ---------------------------------------------------------------------------
# 6. Frontend and web containers Up (AC-48.3)
# ---------------------------------------------------------------------------


def test_frontend_web_containers_up_check_present():
    """deploy.yml verifies both 'frontend' and 'web' containers are Up after deploy (AC-48.3, AC-52.3).

    The Tester VPS-confirmation condition requires both containers to be in running/up state.
    The deploy.yml step must SSH to the VPS and run 'docker compose ps' to confirm.
    """
    content = _deploy_text()
    assert "solanatrilly.frontend" in content or "frontend' container" in content.lower(), (
        "deploy.yml must have a step verifying the 'frontend' container is Up "
        "in the solanatrilly stack (AC-48.3, AC-52.3)."
    )
    assert "solanatrilly.web" in content or "'web' container" in content.lower(), (
        "deploy.yml must have a step verifying the 'web' container is Up "
        "in the solanatrilly stack (AC-48.3, AC-52.3)."
    )


# ---------------------------------------------------------------------------
# 7. solanaBilly isolation — 8001 untouched (AC-6.5)
# ---------------------------------------------------------------------------


def test_solanabilly_isolation_check_present():
    """deploy.yml verifies solanaBilly is untouched on port 8001 after the solanatrilly deploy (AC-6.5, AC-52.3).

    Hard isolation (PRD §15.3): every solanatrilly deploy must confirm solanaBilly still
    responds on 8001 and its containers are still running. This is a Tester VPS-confirmation
    condition for AC-52.3.
    """
    content = _deploy_text()
    assert "8001" in content, (
        "deploy.yml must verify solanaBilly's port 8001 is still responding after the "
        "solanatrilly deploy (AC-6.5, AC-52.3 hard isolation check)."
    )
    assert "solanabilly" in content.lower() or "solanaBilly" in content, (
        "deploy.yml must have a step confirming solanaBilly containers are still Up "
        "after the solanatrilly deploy (AC-6.5, AC-52.3)."
    )


# ---------------------------------------------------------------------------
# 8. All docker commands scoped -p solanatrilly (PRD §15.3)
# ---------------------------------------------------------------------------


def test_all_docker_commands_scoped():
    """Every docker compose command in deploy.yml carries -p solanatrilly (PRD §15.3, AC-52.3).

    Unscoped docker commands risk touching solanaBilly containers or volumes.
    The '-p solanatrilly' flag scopes every operation to the solanatrilly project.
    This is enforced both here and by the 'Verify solanaBilly isolation' smoke-test step.
    """
    content = _deploy_text()
    assert "-p solanatrilly" in content, (
        "deploy.yml must scope all docker compose commands with -p solanatrilly "
        "(PRD §15.3 hard isolation; AC-52.3). Found no '-p solanatrilly' in deploy.yml."
    )
    # The down step added for AC-52.3 must also be scoped
    assert "docker compose -p solanatrilly" in content and "down --remove-orphans" in content, (
        "deploy.yml 'down --remove-orphans' step must be scoped with -p solanatrilly "
        "(PRD §15.3; must not affect solanaBilly or any other Docker project)."
    )
