# ---
# module: core.tests.test_deploy_workflow_ac65
# sprint: sprint-2
# story: US-6 AC-6.5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-6.5 — Hard isolation verification structural tests.

Asserts that deploy.yml contains an isolation check step that, after the
solanatrilly deploy, confirms solanaBilly's stack is untouched:
  1. Step with "isolation" in its name exists in the deploy job.
  2. Step runs 'docker compose -p solanabilly ps' on the VPS (read-only check).
  3. Step verifies port 8001 still responds (curl check).
  4. Step exits non-zero on isolation breach (fails the pipeline if solanaBilly is down).
  5. Step comes after the smoke-test step (deployment is verified first, then isolation).
  6. Step injects VPS_SSH_KEY and VPS_USER secrets for SSH access.
  7. Step injects VPS_HOST secret for the port 8001 curl check.

Tests:
  test_isolation_step_exists_in_deploy_job
  test_isolation_step_verifies_solanabilly_containers_running
  test_isolation_step_verifies_port_8001_responds
  test_isolation_step_exits_nonzero_on_isolation_breach
  test_isolation_step_comes_after_smoke_test_step
  test_isolation_step_uses_vps_ssh_key_secret
  test_isolation_step_uses_vps_host_secret
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
        "AC-6.5 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be valid YAML mapping at top level."
    return data


def _deploy_job_steps(data: dict) -> list[dict]:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a 'deploy' job (AC-6.3).\n"
        f"Current jobs: {list(jobs.keys())}"
    )
    steps = job.get("steps") or []
    assert steps, "The 'deploy' job must have at least one step."
    return [s for s in steps if isinstance(s, dict)]


def _find_isolation_step(steps: list[dict]) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        if "isolation" in name:
            return step
    return None


def _find_smoke_test_step(steps: list[dict]) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        if "smoke" in name:
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_isolation_step_exists_in_deploy_job() -> None:
    """The deploy job must contain an isolation verification step (AC-6.5).

    AC-6.5 requires the pipeline to confirm solanaBilly is untouched after every
    solanatrilly deployment — this must be a named step in the deploy job.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-6.5: The 'deploy' job must contain an isolation verification step.\n"
        "Add a step with 'isolation' in its name that checks solanaBilly's status.\n"
        f"Current step names: {[s.get('name', '<unnamed>') for s in steps]}"
    )


def test_isolation_step_verifies_solanabilly_containers_running() -> None:
    """The isolation step must run 'docker compose -p solanabilly ps' on the VPS (AC-6.5).

    'docker compose -p solanabilly ps' shows solanaBilly's container states.
    The -p solanabilly scope ensures only solanaBilly's project is queried.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, "AC-6.5: No isolation step found in 'deploy' job."
    script = str(isolation_step.get("run", ""))
    assert "docker compose -p solanabilly ps" in script, (
        "AC-6.5: Isolation step must run 'docker compose -p solanabilly ps' to verify\n"
        "solanaBilly's containers are still running after the solanatrilly deployment.\n"
        f"Current isolation step script:\n{script}"
    )


def test_isolation_step_verifies_port_8001_responds() -> None:
    """The isolation step must verify that port 8001 still responds (AC-6.5).

    Port 8001 is solanaBilly's exclusive port. The AC requires checking that
    port 8001 responds after the solanatrilly deployment — confirming no
    network or port conflict was introduced.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, "AC-6.5: No isolation step found in 'deploy' job."
    script = str(isolation_step.get("run", ""))
    assert ":8001" in script or ":8001/" in script, (
        "AC-6.5: Isolation step must check that port 8001 still responds.\n"
        "Expected: curl ... http://<VPS_HOST>:8001/ (or similar URL with :8001)\n"
        f"Current isolation step script:\n{script}"
    )


def test_isolation_step_exits_nonzero_on_isolation_breach() -> None:
    """The isolation step must fail the pipeline if solanaBilly is down (AC-6.5).

    An isolation breach (solanaBilly containers down or port 8001 unresponsive)
    must cause the step to exit non-zero, failing the CD pipeline and alerting
    the operator that the deployment affected the live stack.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, "AC-6.5: No isolation step found in 'deploy' job."
    script = str(isolation_step.get("run", ""))
    assert "exit 1" in script, (
        "AC-6.5: Isolation step must exit with code 1 on isolation breach so\n"
        "GitHub Actions marks the workflow run as failed.\n"
        f"Current isolation step script:\n{script}"
    )


def test_isolation_step_comes_after_smoke_test_step() -> None:
    """The isolation step must run after the smoke-test step (AC-6.5).

    The smoke-test (AC-6.4) proves the solanatrilly stack is up; the isolation
    check (AC-6.5) then confirms solanaBilly was not disrupted. Running them in
    this order means both stacks are verified after the same deployment.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_test_step(steps)
    isolation_step = _find_isolation_step(steps)

    assert smoke_step is not None, (
        "AC-6.4: No smoke-test step found in the 'deploy' job. "
        "Expected a step with 'smoke' in its name."
    )
    assert isolation_step is not None, (
        "AC-6.5: No isolation step found in the 'deploy' job. "
        "Expected a step with 'isolation' in its name."
    )

    smoke_idx = steps.index(smoke_step)
    isolation_idx = steps.index(isolation_step)
    assert isolation_idx > smoke_idx, (
        f"AC-6.5: Isolation step (index {isolation_idx}) must come AFTER the "
        f"smoke-test step (index {smoke_idx}) in the 'deploy' job.\n"
        "The smoke-test verifies solanatrilly first; isolation check follows."
    )


def test_isolation_step_uses_vps_ssh_key_secret() -> None:
    """The isolation step must reference VPS_SSH_KEY to SSH into the VPS (AC-6.5).

    The docker compose ps command runs on the VPS — SSH access requires
    VPS_SSH_KEY to be injected from repo secrets.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, "AC-6.5: No isolation step found in 'deploy' job."
    env_block = isolation_step.get("env") or {}
    script = str(isolation_step.get("run", ""))
    has_key_in_env = any("VPS_SSH_KEY" in str(v) for v in env_block.values())
    has_key_in_script = "VPS_SSH_KEY" in script
    assert has_key_in_env or has_key_in_script, (
        "AC-6.5: The isolation step must reference secrets.VPS_SSH_KEY\n"
        "(injected via env: block) to SSH to the VPS and run docker compose.\n"
        f"Isolation step env: {env_block}\n"
        f"Isolation step script (excerpt): {script[:300]}"
    )


def test_isolation_step_uses_vps_host_secret() -> None:
    """The isolation step must reference VPS_HOST to target the correct server (AC-6.5).

    VPS_HOST is needed for both the SSH target and the port 8001 curl check.
    It must come from the repo secret, not a hardcoded address.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, "AC-6.5: No isolation step found in 'deploy' job."
    env_block = isolation_step.get("env") or {}
    script = str(isolation_step.get("run", ""))
    has_host_in_env = any("VPS_HOST" in str(v) for v in env_block.values())
    has_host_in_script = "VPS_HOST" in script
    assert has_host_in_env or has_host_in_script, (
        "AC-6.5: The isolation step must reference secrets.VPS_HOST\n"
        "(injected via env: block) so the VPS address is not hardcoded.\n"
        f"Isolation step env: {env_block}\n"
        f"Isolation step script (excerpt): {script[:300]}"
    )
