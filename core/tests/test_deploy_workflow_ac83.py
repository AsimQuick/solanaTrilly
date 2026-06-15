# ---
# module: core.tests.test_deploy_workflow_ac83
# sprint: sprint-3
# story: US-8 AC-8.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-8.3 — End-to-end CD pipeline structural verification.

AC-8.3 states: a deploy on main (or via workflow_dispatch) succeeds end-to-end:
  - the web image is built and pushed to GHCR (ghcr.io/asimquick/solanatrilly)
  - pulled on the VPS
  - stack brought up with 'docker compose -p solanatrilly -f docker-compose.staging.yml pull && up -d'
  - the CD smoke-test step hits http://VPS:8002/health/ and asserts HTTP 200

These structural tests verify the deploy.yml is correctly wired for that path.
The live green run (with smoke-test passing 200) is the definitive AC verification.

Tests:
  test_build_and_push_job_tags_both_latest_and_sha
  test_build_and_push_job_push_enabled
  test_complete_three_job_pipeline_dependency_chain
  test_deploy_command_references_staging_compose_file
  test_deploy_uses_correct_compose_project_name
  test_smoke_test_has_retry_logic
  test_smoke_test_url_uses_vps_host_variable
  test_staging_compose_web_service_on_port_8002
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"

GHCR_IMAGE = "ghcr.io/asimquick/solanatrilly"
COMPOSE_PROJECT = "solanatrilly"
STAGING_COMPOSE_FILE = "docker-compose.staging.yml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-8.3 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _load_staging_compose() -> dict:
    assert STAGING_COMPOSE.exists(), (
        f"docker-compose.staging.yml not found at {STAGING_COMPOSE}. "
        "AC-8.3 requires the staging compose file for VPS deployment."
    )
    data = yaml.safe_load(STAGING_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "docker-compose.staging.yml must be a valid YAML mapping."
    return data


def _get_build_and_push_job(data: dict) -> dict:
    jobs = data.get("jobs") or {}
    job = jobs.get("build-and-push")
    assert job is not None, (
        "deploy.yml must contain a 'build-and-push' job (AC-8.3). "
        f"Current jobs: {list(jobs.keys())}"
    )
    return job


def _get_deploy_job(data: dict) -> dict:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a 'deploy' job (AC-8.3). "
        f"Current jobs: {list(jobs.keys())}"
    )
    return job


def _get_ci_job(data: dict) -> dict | None:
    jobs = data.get("jobs") or {}
    return jobs.get("ci")


def _get_build_push_step(job: dict) -> dict | None:
    """Return the step that uses docker/build-push-action."""
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses", ""))
        if "build-push-action" in uses:
            return step
    return None


def _deploy_run_scripts(data: dict) -> str:
    """Return all run: script text from the deploy job as one string."""
    job = _get_deploy_job(data)
    parts: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "run" in step:
            parts.append(str(step["run"]))
    return "\n".join(parts)


def _find_smoke_test_step(job: dict) -> dict | None:
    for step in (job.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if "smoke" in str(step.get("name", "")).lower():
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_and_push_job_tags_both_latest_and_sha() -> None:
    """The build-and-push job must tag the image as both :latest and :<github.sha>.

    AC-8.3 requires the image to be pushed to ghcr.io/asimquick/solanatrilly.
    Tagging with both :latest (for the deploy pull) and :<sha> (for traceability
    back to a specific commit) is required for the end-to-end path to work
    predictably across deploys.
    """
    data = _load_deploy()
    job = _get_build_and_push_job(data)
    push_step = _get_build_push_step(job)
    assert push_step is not None, (
        "AC-8.3: build-and-push job must contain a step using docker/build-push-action."
    )
    with_block = push_step.get("with") or {}
    tags = str(with_block.get("tags", ""))

    assert f"{GHCR_IMAGE}:latest" in tags, (
        f"AC-8.3: build-push step must tag the image as {GHCR_IMAGE}:latest.\n"
        f"Current tags:\n{tags}\n\n"
        "The :latest tag is what 'docker compose pull' on the VPS fetches."
    )
    assert "github.sha" in tags, (
        f"AC-8.3: build-push step must also tag the image with the commit SHA "
        "(e.g. ghcr.io/asimquick/solanatrilly:${{{{ github.sha }}}}).\n"
        f"Current tags:\n{tags}\n\n"
        "The SHA tag provides traceability: each VPS deploy is tied to a commit."
    )


def test_build_and_push_job_push_enabled() -> None:
    """The build-push step must have 'push: true' so the image actually reaches GHCR.

    A build without push is a no-op for the CD pipeline — the VPS cannot pull
    an image that was never pushed.
    """
    data = _load_deploy()
    job = _get_build_and_push_job(data)
    push_step = _get_build_push_step(job)
    assert push_step is not None, (
        "AC-8.3: build-and-push job must contain a step using docker/build-push-action."
    )
    with_block = push_step.get("with") or {}
    push_flag = with_block.get("push")
    assert push_flag is True, (
        f"AC-8.3: build-push step must have 'push: true' to deliver the image to GHCR.\n"
        f"Got push: {push_flag!r}\n\n"
        "Without 'push: true' the image is built locally in the runner but never pushed."
    )


def test_complete_three_job_pipeline_dependency_chain() -> None:
    """The pipeline must run in order: ci → build-and-push → deploy.

    AC-8.3 proves the full local→GitHub→GHCR→VPS path. That path requires:
      1. CI must pass before the image is built (no broken image in GHCR).
      2. The image must be pushed before the VPS deploy (VPS cannot pull
         a not-yet-pushed image).

    Both dependency links must be explicit 'needs:' declarations in deploy.yml.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}

    # 1. build-and-push must need some CI job
    bap_job = _get_build_and_push_job(data)
    bap_needs = bap_job.get("needs")
    assert bap_needs is not None, (
        "AC-8.3: 'build-and-push' job must declare 'needs:' to gate on CI. "
        "Without it, a broken commit's image can reach GHCR."
    )
    bap_needs_list = [bap_needs] if isinstance(bap_needs, str) else list(bap_needs)
    assert bap_needs_list, (
        "AC-8.3: 'build-and-push' 'needs:' list must be non-empty."
    )

    # Verify that what build-and-push depends on is actually a CI job (calls ci.yml)
    ci_job = _get_ci_job(data)
    if ci_job is not None:
        assert "ci" in bap_needs_list, (
            f"AC-8.3: 'build-and-push' must need the 'ci' job. "
            f"Got needs: {bap_needs_list!r}"
        )

    # 2. deploy must need build-and-push
    deploy_job = _get_deploy_job(data)
    deploy_needs = deploy_job.get("needs")
    assert deploy_needs is not None, (
        "AC-8.3: 'deploy' job must declare 'needs: build-and-push'. "
        "Without it, the VPS deploy can start before the image is in GHCR."
    )
    deploy_needs_list = [deploy_needs] if isinstance(deploy_needs, str) else list(deploy_needs)
    assert "build-and-push" in deploy_needs_list, (
        f"AC-8.3: 'deploy' job must need 'build-and-push'. "
        f"Got needs: {deploy_needs_list!r}"
    )


def test_deploy_command_references_staging_compose_file() -> None:
    """The VPS deploy command must use '-f' with the staging compose file.

    AC-8.3 specifies the stack is brought up with:
      'docker compose -p solanatrilly -f docker-compose.staging.yml pull && up -d'

    The '-f' flag is required so the VPS uses the correct compose file
    (the file SCPed to the VPS, not whatever docker compose might find by default).
    """
    data = _load_deploy()
    all_scripts = _deploy_run_scripts(data)

    assert STAGING_COMPOSE_FILE in all_scripts, (
        f"AC-8.3: the deploy job run scripts must reference '{STAGING_COMPOSE_FILE}' "
        "via the '-f' flag to use the correct staging compose file on the VPS.\n\n"
        "Expected: docker compose -p solanatrilly -f /root/solanatrilly/docker-compose.staging.yml pull"
    )


def test_deploy_uses_correct_compose_project_name() -> None:
    """Every docker compose command in the deploy scripts must use -p solanatrilly.

    The AC-8.3 spec explicitly states '-p solanatrilly' in the bring-up command.
    All VPS docker compose calls must be project-scoped to prevent any cross-stack
    interference with solanaBilly (PRD §15.3 hard isolation).
    """
    data = _load_deploy()
    all_scripts = _deploy_run_scripts(data)

    # Verify -p solanatrilly appears in the pull && up -d portion
    assert f"-p {COMPOSE_PROJECT}" in all_scripts, (
        f"AC-8.3: deploy run scripts must include '-p {COMPOSE_PROJECT}' to scope "
        "all docker compose commands to the solanatrilly project.\n\n"
        "Required: docker compose -p solanatrilly -f ... pull && docker compose -p solanatrilly -f ... up -d"
    )

    # Verify both pull and up -d follow the project-scoped pattern
    pull_re = re.compile(
        r"docker\s+compose\s+.*-p\s+solanatrilly.*pull"
        r"|docker\s+compose\s+.*pull.*-p\s+solanatrilly"
    )
    up_re = re.compile(
        r"docker\s+compose\s+.*-p\s+solanatrilly.*up\s+-d"
        r"|docker\s+compose\s+.*up\s+-d.*-p\s+solanatrilly"
    )

    assert pull_re.search(all_scripts), (
        "AC-8.3: 'docker compose -p solanatrilly ... pull' not found in deploy scripts.\n"
        "The image pull on the VPS must be project-scoped."
    )
    assert up_re.search(all_scripts), (
        "AC-8.3: 'docker compose -p solanatrilly ... up -d' not found in deploy scripts.\n"
        "Bringing the stack up on the VPS must be project-scoped."
    )


def test_smoke_test_has_retry_logic() -> None:
    """The smoke-test step must include retry logic to wait for the container to start.

    After 'docker compose up -d' the web container takes several seconds to run
    migrations and start listening on port 8000. A retry loop with a sleep
    ensures the smoke-test doesn't fail on a slow cold-start.

    AC-8.3 requires the step to 'assert HTTP 200' — retrying before declaring
    failure is necessary for a reliable green run.
    """
    data = _load_deploy()
    deploy_job = _get_deploy_job(data)
    smoke_step = _find_smoke_test_step(deploy_job)
    assert smoke_step is not None, (
        "AC-8.3: The 'deploy' job must contain a smoke-test step "
        "(look for 'smoke' in the step name)."
    )
    script = str(smoke_step.get("run", ""))

    has_retry = (
        "for i in" in script
        or "while " in script
        or "retry" in script.lower()
        or "sleep" in script
    )
    assert has_retry, (
        "AC-8.3: Smoke-test step must include retry logic.\n"
        "The web container takes several seconds to start after 'docker compose up -d'.\n"
        "A retry loop (e.g. 'for i in $(seq 1 12)') prevents false-negative failures "
        "on cold starts.\n"
        f"Current smoke-test script:\n{script}"
    )

    # Verify there's a sleep between retries
    assert "sleep" in script, (
        "AC-8.3: Smoke-test retry loop must include 'sleep' between attempts.\n"
        "Hammering the endpoint immediately after each failed attempt wastes runner "
        "time and does not give the container enough time to finish starting.\n"
        f"Current smoke-test script:\n{script}"
    )


def test_smoke_test_url_uses_vps_host_variable() -> None:
    """The smoke-test URL must be constructed from $VPS_HOST (injected from secrets).

    The smoke-test hits http://<VPS_HOST>:8002/health/ — the hostname must come
    from the VPS_HOST secret, not be hardcoded. Hardcoding the IP breaks if the
    VPS address ever changes and leaks infrastructure details into source control.
    """
    data = _load_deploy()
    deploy_job = _get_deploy_job(data)
    smoke_step = _find_smoke_test_step(deploy_job)
    assert smoke_step is not None, (
        "AC-8.3: The 'deploy' job must contain a smoke-test step."
    )
    script = str(smoke_step.get("run", ""))
    env_block = smoke_step.get("env") or {}

    # Either the env block injects VPS_HOST from secrets, or the script
    # references the env var directly
    vps_host_in_env = any("VPS_HOST" in str(v) for v in env_block.values())
    vps_host_in_script = "VPS_HOST" in script

    assert vps_host_in_env or vps_host_in_script, (
        "AC-8.3: Smoke-test step must reference VPS_HOST (from secrets) to "
        "build the target URL dynamically.\n"
        "Expected: curl ... http://${VPS_HOST}:8002/health/\n"
        f"Smoke-test env block: {env_block}\n"
        f"Smoke-test script (first 300 chars): {script[:300]}"
    )

    # Also verify the URL contains the expected port and endpoint
    assert ":8002" in script, (
        f"AC-8.3: Smoke-test URL must include ':8002' (the solanatrilly staging port).\n"
        f"Script: {script}"
    )
    assert "/health/" in script, (
        f"AC-8.3: Smoke-test URL must include '/health/' (the liveness endpoint).\n"
        f"Script: {script}"
    )


def test_staging_compose_web_service_on_port_8002() -> None:
    """docker-compose.staging.yml must expose the web service on host port 8002.

    The smoke-test hits port 8002, so the staging compose must map 8002 on the
    host to the internal container port. solanaBilly owns 8001 — this mapping
    must never change to 8001 (PRD §15.3).
    """
    data = _load_staging_compose()
    services = data.get("services") or {}
    web = services.get("web")
    assert web is not None, (
        "docker-compose.staging.yml must define a 'web' service (AC-8.3)."
    )
    ports = web.get("ports") or []
    port_strings = [str(p) for p in ports]

    has_8002 = any("8002" in p for p in port_strings)
    assert has_8002, (
        "AC-8.3: docker-compose.staging.yml 'web' service must map host port 8002.\n"
        f"Current ports: {port_strings}\n\n"
        "The smoke-test hits http://VPS:8002/health/ — the host port must be 8002."
    )
    # Ensure port 8001 is NOT used (solanaBilly's port)
    has_8001 = any(
        p.startswith("8001:") or p == "8001"
        for p in port_strings
    )
    assert not has_8001, (
        "AC-8.3 isolation violation: docker-compose.staging.yml 'web' service "
        "must NOT use host port 8001 — that port belongs to live solanaBilly.\n"
        f"Current ports: {port_strings}"
    )
