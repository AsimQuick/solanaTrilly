# ---
# module: core.tests.test_deploy_workflow_ac333
# sprint: sprint-7
# story: US-33 AC-33.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, re, yaml
# ---
"""AC-33.3 — F3: clean final deploy structural verification.

AC-33.3 states: the first sprint-7 sprint-boundary deploy runs GREEN on 'main'
and the Tester confirms from an ACTUAL green deploy run:
  1. HTTP 200 on 8002            (retained smoke-test — AC-6.4 / AC-12.3)
  2. 'listener' container Up     (NEW — added by this AC)
  3. solanaBilly untouched 8001  (retained isolation — AC-6.5 / AC-12.4)

The smoke-test MUST retain the US-12 runtime retry-with-backoff (AC-12.3):
SMOKE_MAX_ATTEMPTS / SMOKE_RETRY_DELAY variables, bounded for-loop, sleep
inside loop, exit 0 on success inside loop, exit 1 after loop.

Tests in this module:
  test_listener_check_step_exists_in_deploy_job
  test_listener_check_step_name_references_ac333
  test_listener_check_uses_solanatrilly_project_scope
  test_listener_check_greps_for_listener_service
  test_listener_check_greps_running_or_up_case_insensitive
  test_listener_check_exits_1_on_failure
  test_listener_check_exits_0_on_pass
  test_listener_check_cleans_up_deploy_key
  test_smoke_test_retained_with_retry_backoff
  test_three_conditions_all_present_in_deploy_job
"""
import re
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
        "AC-33.3 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _deploy_job_steps(data: dict) -> list[dict]:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a 'deploy' job.\n"
        f"Current jobs: {list(jobs.keys())}"
    )
    steps = job.get("steps") or []
    assert steps, "The 'deploy' job must have at least one step."
    return [s for s in steps if isinstance(s, dict)]


def _find_listener_step(steps: list[dict]) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        if "listener" in name:
            return step
    return None


def _find_smoke_step(steps: list[dict]) -> dict | None:
    for step in steps:
        if "smoke" in str(step.get("name", "")).lower():
            return step
    return None


def _find_isolation_step(steps: list[dict]) -> dict | None:
    for step in steps:
        if "isolation" in str(step.get("name", "")).lower():
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_listener_check_step_exists_in_deploy_job() -> None:
    """AC-33.3 requires a deploy step that verifies the 'listener' container is Up.

    The 'listener' service is the dedicated Birdeye WebSocket detection /
    reconciler container (docker-compose.staging.yml). It is a first-class
    process-hardening gate: without it, a deploy could leave the listener
    silently crashed and appear green.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No step referencing 'listener' found in the 'deploy' job.\n"
        "Expected a step with 'listener' in its name that verifies the container is Up.\n"
        f"Current step names: {[s.get('name') for s in steps]}"
    )


def test_listener_check_step_name_references_ac333() -> None:
    """The listener check step name must reference AC-33.3 for deploy log traceability.

    AC-33.3 is the sprint-7 process-hardening gate that closes the carried
    sprint-6 DoD VPS clause. Embedding the AC number in the step name makes
    it trivial to find the evidence in a deploy run log.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    name = str(listener_step.get("name", ""))
    assert "33.3" in name, (
        "AC-33.3: The listener check step name must include '33.3' for deploy log traceability.\n"
        f"Current name: {name!r}\n"
        "Expected e.g.: 'Verify listener container Up (AC-33.3)'"
    )


def test_listener_check_uses_solanatrilly_project_scope() -> None:
    """The listener check must scope its docker compose command with -p solanatrilly.

    CLAUDE.md hard isolation rule: ALL docker commands on the VPS MUST be scoped
    '-p solanatrilly'. An unscoped 'docker compose ps' could accidentally inspect
    the solanaBilly project and return false-positive running containers.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    assert "-p solanatrilly" in script, (
        "AC-33.3: The listener check step must scope its docker compose command with "
        "'-p solanatrilly' (CLAUDE.md hard isolation rule).\n"
        f"Script:\n{script}"
    )


def test_listener_check_greps_for_listener_service() -> None:
    """The listener check step must search the 'docker compose ps' output for 'listener'.

    The step must filter the ps output for the specific 'listener' service row —
    not just check that some container is running. Without filtering, an Up web
    container would satisfy the check even if the listener crashed on startup.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    assert re.search(r"grep.*listener", script, re.IGNORECASE) is not None, (
        "AC-33.3: The listener check step must grep the 'docker compose ps' output "
        "for 'listener' to isolate that specific service row.\n"
        f"Script:\n{script}"
    )


def test_listener_check_greps_running_or_up_case_insensitive() -> None:
    """The listener check must grep for 'running' or 'up' (case-insensitive).

    Docker Compose ps output uses 'Up' (v1) or 'running' (v2) depending on the
    version installed on the VPS. The check must handle both forms to be robust
    across Docker Compose versions.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    has_state_check = bool(
        re.search(r"grep.*[Rr]unning", script)
        or re.search(r"grep.*\bup\b", script, re.IGNORECASE)
        or re.search(r"running\|up", script, re.IGNORECASE)
    )
    assert has_state_check, (
        "AC-33.3: The listener check step must grep for 'running' or 'up' to verify "
        "the container state (case-insensitive, to handle docker compose v1/v2 output).\n"
        f"Script:\n{script}"
    )


def test_listener_check_exits_1_on_failure() -> None:
    """The listener check step must exit 1 when the listener is not running.

    Without 'exit 1' on failure the step returns success even when the listener
    container crashed, silently passing AC-33.3 on a broken deploy.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    assert "exit 1" in script, (
        "AC-33.3: The listener check step must contain 'exit 1' to fail the deploy "
        "when the listener container is not Up.\n"
        f"Script:\n{script}"
    )


def test_listener_check_exits_0_on_pass() -> None:
    """The listener check step must echo a PASS message when the listener is Up.

    A clear PASS message with 'exit 0' (or script fall-through on success) makes
    the deploy log unambiguous — no guessing whether the step ran or was skipped.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    assert re.search(r"echo.*[Pp][Aa][Ss][Ss]", script) is not None, (
        "AC-33.3: The listener check step must echo a PASS message when the "
        "listener container is Up, so the deploy log is unambiguous.\n"
        f"Script:\n{script}"
    )


def test_listener_check_cleans_up_deploy_key() -> None:
    """The listener check step must remove the SSH deploy key after use.

    The deploy key is a secret written to /tmp/deploy_key. Leaving it on the
    runner filesystem after the step creates a window where a subsequent step
    could read it. The step must 'rm -f /tmp/deploy_key' before exiting.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_listener_step(steps)
    assert listener_step is not None, (
        "AC-33.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))
    assert "rm -f /tmp/deploy_key" in script, (
        "AC-33.3: The listener check step must clean up the SSH deploy key "
        "('rm -f /tmp/deploy_key') before it exits.\n"
        f"Script:\n{script}"
    )


def test_smoke_test_retained_with_retry_backoff() -> None:
    """The smoke-test step (HTTP 200 on 8002) must still have AC-12.3 retry-with-backoff.

    AC-33.3 states: 'The smoke-test retains the US-12 runtime retry-with-backoff
    (AC-12.3)'. This test confirms that adding the listener check has not removed
    or altered the existing retry loop.

    Checks: SMOKE_MAX_ATTEMPTS defined, loop bound uses variable, sleep inside loop,
    exit 0 inside loop on success, exit 1 after loop on exhaustion.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-33.3: No smoke-test step found in deploy job. The smoke-test "
        "(HTTP 200 on 8002 with retry-backoff) must be retained."
    )
    script = str(smoke_step.get("run", ""))

    assert re.search(r"SMOKE_MAX_ATTEMPTS\s*=\s*\d+", script) is not None, (
        "AC-33.3 / AC-12.3: SMOKE_MAX_ATTEMPTS must still be defined in the smoke-test."
    )
    assert re.search(r"seq\s+1\s+[\"']?\$\{?SMOKE_MAX_ATTEMPTS\}?[\"']?", script) is not None, (
        "AC-33.3 / AC-12.3: The loop bound must still reference $SMOKE_MAX_ATTEMPTS."
    )
    assert re.search(r"sleep\s+[\"']?\$\{?SMOKE_RETRY_DELAY\}?[\"']?", script) is not None, (
        "AC-33.3 / AC-12.3: The sleep call must still reference $SMOKE_RETRY_DELAY."
    )
    assert "exit 0" in script, (
        "AC-33.3 / AC-12.3: 'exit 0' must be inside the loop body for early success exit."
    )
    assert "exit 1" in script, (
        "AC-33.3 / AC-12.3: 'exit 1' must follow the loop to fail when attempts exhausted."
    )
    assert "8002" in script, (
        "AC-33.3: The smoke-test must still check port 8002 (the solanatrilly web port)."
    )


def test_three_conditions_all_present_in_deploy_job() -> None:
    """All three AC-33.3 conditions must be wired as steps in the deploy job.

    AC-33.3 Tester confirmation requires:
      1. HTTP 200 on 8002 — smoke-test step (AC-6.4 / AC-12.3)
      2. 'listener' container Up — listener check step (AC-33.3)
      3. solanaBilly untouched on 8001 — isolation step (AC-6.5 / AC-12.4)

    All three must be present as distinct, named steps so each condition
    appears independently in the deploy run log.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    listener_step = _find_listener_step(steps)
    isolation_step = _find_isolation_step(steps)

    assert smoke_step is not None, (
        "AC-33.3 condition 1 missing: No smoke-test step (HTTP 200 on 8002) found.\n"
        f"Step names: {[s.get('name') for s in steps]}"
    )
    assert listener_step is not None, (
        "AC-33.3 condition 2 missing: No listener check step found.\n"
        f"Step names: {[s.get('name') for s in steps]}"
    )
    assert isolation_step is not None, (
        "AC-33.3 condition 3 missing: No solanaBilly isolation step found.\n"
        f"Step names: {[s.get('name') for s in steps]}"
    )

    # Verify each step's script covers the right port/container
    smoke_script = str(smoke_step.get("run", ""))
    assert "8002" in smoke_script, (
        "AC-33.3: Smoke-test step must check port 8002."
    )

    isolation_script = str(isolation_step.get("run", ""))
    assert "8001" in isolation_script, (
        "AC-33.3: Isolation step must check port 8001 (solanaBilly)."
    )
