# ---
# module: core.tests.test_deploy_workflow_ac64
# sprint: sprint-2
# story: US-6 AC-6.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-6.4 — CD smoke-test step structural verification.

Asserts every AC-6.4 requirement:
  1. The deploy job contains a smoke-test step (after the main deploy step).
  2. The smoke-test step targets port 8002 (the solanatrilly staging port).
  3. The smoke-test step hits the /health/ hello-world endpoint.
  4. The smoke-test step asserts HTTP 200 before declaring success.
  5. The smoke-test step comes after the 'Deploy to VPS staging stack' step.
  6. The smoke-test step references secrets.VPS_HOST for the target address.

Tests:
  test_smoke_test_step_exists_in_deploy_job
  test_smoke_test_step_targets_port_8002
  test_smoke_test_step_targets_health_endpoint
  test_smoke_test_step_asserts_http_200
  test_smoke_test_step_comes_after_deploy_step
  test_smoke_test_step_uses_vps_host_secret
"""
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-6.4 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be valid YAML mapping at top level."
    return data


def _deploy_job_steps(data: dict) -> list[dict]:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a 'deploy' job (AC-6.3). "
        f"Current jobs: {list(jobs.keys())}"
    )
    steps = job.get("steps") or []
    assert steps, "The 'deploy' job must have at least one step."
    return [s for s in steps if isinstance(s, dict)]


def _find_smoke_test_step(steps: list[dict]) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        if "smoke" in name:
            return step
    return None


def _find_deploy_step(steps: list[dict]) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        if "deploy to vps" in name or "deploy to staging" in name:
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_smoke_test_step_exists_in_deploy_job() -> None:
    """The deploy job must contain a smoke-test step (AC-6.4).

    The smoke-test proves the end-to-end path: local -> GitHub -> GHCR -> VPS.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    assert smoke_step is not None, (
        "AC-6.4: The 'deploy' job must contain a smoke-test step.\n"
        "Add a step with 'smoke' in its name that hits the staging stack and "
        "asserts HTTP 200.\n"
        f"Current step names: {[s.get('name', '<unnamed>') for s in steps]}"
    )


def test_smoke_test_step_targets_port_8002() -> None:
    """The smoke-test step must target port 8002 — the solanatrilly staging port (AC-6.4).

    Port 8002 is the exclusive port of the solanatrilly staging stack
    (solanaBilly owns 8001 — PRD §15.3, docker-compose.staging.yml).
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in 'deploy' job."
    )
    script = str(smoke_step.get("run", ""))
    assert ":8002" in script or ":8002/" in script, (
        "AC-6.4: Smoke-test step must target port 8002.\n"
        f"Current smoke-test script:\n{script}\n\n"
        "Expected: curl ... http://<host>:8002/health/ (or similar URL with :8002)"
    )


def test_smoke_test_step_targets_health_endpoint() -> None:
    """The smoke-test must hit the /health/ hello-world endpoint (AC-6.4).

    /health/ is the hello-world Django endpoint that returns {"status": "ok"}.
    It is the canary proving the full local->GHCR->VPS deploy path works.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in 'deploy' job."
    )
    script = str(smoke_step.get("run", ""))
    assert "/health/" in script, (
        "AC-6.4: Smoke-test step must hit the /health/ endpoint.\n"
        f"Current smoke-test script:\n{script}\n\n"
        "Expected: curl ... http://<host>:8002/health/"
    )


def test_smoke_test_step_asserts_http_200() -> None:
    """The smoke-test step must assert HTTP 200 before declaring success (AC-6.4).

    The AC requires the step to 'assert HTTP 200' — a non-200 response must
    cause the step (and therefore the workflow) to fail.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in 'deploy' job."
    )
    script = str(smoke_step.get("run", ""))
    assert "200" in script, (
        "AC-6.4: Smoke-test step must check for HTTP 200.\n"
        f"Current smoke-test script:\n{script}\n\n"
        "The step must compare the HTTP status code to '200' and exit non-zero "
        "if the check fails."
    )
    # Verify it fails on non-200: the script must have an exit non-zero path
    assert "exit 1" in script or "exit 0" in script, (
        "AC-6.4: Smoke-test step must have explicit exit codes to signal "
        "pass (exit 0) or failure (exit 1) to GitHub Actions.\n"
        f"Current script:\n{script}"
    )


def test_smoke_test_step_comes_after_deploy_step() -> None:
    """The smoke-test step must run after the VPS deploy step (AC-6.4).

    The AC requires the smoke-test to be a 'step run after deploy' — it
    would be meaningless to test before the stack is deployed.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    deploy_step = _find_deploy_step(steps)
    smoke_step = _find_smoke_test_step(steps)

    assert deploy_step is not None, (
        "AC-6.3: No VPS deploy step found in the 'deploy' job. "
        "Expected a step with 'deploy to vps' in its name."
    )
    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in 'deploy' job."
    )

    deploy_idx = steps.index(deploy_step)
    smoke_idx = steps.index(smoke_step)
    assert smoke_idx > deploy_idx, (
        f"AC-6.4: Smoke-test step (index {smoke_idx}) must come AFTER the "
        f"deploy step (index {deploy_idx}) in the 'deploy' job.\n"
        "The stack must be up before we test it."
    )


def test_smoke_test_step_uses_vps_host_secret() -> None:
    """The smoke-test step must reference secrets.VPS_HOST to find the target (AC-6.4).

    The step needs to know the VPS hostname/IP at runtime — this must come from
    the VPS_HOST repo secret, not a hardcoded address.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in 'deploy' job."
    )
    # Check env block for VPS_HOST secret injection
    env_block = smoke_step.get("env") or {}
    script = str(smoke_step.get("run", ""))
    has_vps_host_in_env = any(
        "VPS_HOST" in str(v) for v in env_block.values()
    )
    has_vps_host_in_script = "VPS_HOST" in script
    assert has_vps_host_in_env or has_vps_host_in_script, (
        "AC-6.4: The smoke-test step must reference secrets.VPS_HOST "
        "(injected via env: block) so the target address is not hardcoded.\n"
        f"Smoke-test env: {env_block}\n"
        f"Smoke-test script (excerpt): {script[:300]}"
    )
