# ---
# module: core.tests.test_deploy_workflow_ac85
# sprint: sprint-3
# story: US-8 AC-8.5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-8.5 — VPS verification cannot be bypassed: structural tests.

AC-8.5 states:
  The story is NOT Done on green pytest alone — the Tester confirms, from the
  actual deploy run, that the running stack answers HTTP 200 on 8002 and
  solanaBilly is untouched on 8001. The deploy pipeline must be wired so that
  these verifications cannot be silently skipped or bypassed.

These six tests are distinct from ac64/ac65/ac84 and focus exclusively on
"VPS verification CANNOT be bypassed" invariants:

  test_no_verification_step_has_continue_on_error
  test_isolation_step_is_final_step_in_deploy_job
  test_vps_dual_gate_covers_both_ports
  test_smoke_test_uses_retry_loop_not_single_shot
  test_smoke_test_exits_one_on_max_retries_exhausted
  test_full_deploy_verification_sequence_is_ordered
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
        "AC-8.5 requires a .github/workflows/deploy.yml file."
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


def _find_deploy_vps_step(steps: list[dict]) -> dict | None:
    for step in steps:
        if "deploy to vps" in str(step.get("name", "")).lower():
            return step
    return None


def _all_deploy_job_run_scripts(steps: list[dict]) -> str:
    """Return the combined run: text from all steps in the deploy job."""
    return "\n".join(
        str(step["run"]) for step in steps if "run" in step
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_verification_step_has_continue_on_error() -> None:
    """Neither the smoke-test step nor the isolation step may have continue-on-error: true (AC-8.5).

    If either verification step had 'continue-on-error: true', GitHub Actions
    would advance the workflow even after the step failed — silently allowing a
    broken VPS state (e.g. /health/ returning 500, solanaBilly down) to be
    reported as a pipeline success. Both gates must be hard failures.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-8.5: No smoke-test step found in 'deploy' job. "
        "Expected a step with 'smoke' in its name."
    )

    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-8.5: No isolation step found in 'deploy' job. "
        "Expected a step with 'isolation' in its name."
    )

    assert smoke_step.get("continue-on-error") is not True, (
        "AC-8.5: The smoke-test step must NOT have 'continue-on-error: true'.\n"
        "Setting this flag would allow the pipeline to succeed even when the VPS "
        "health check fails — bypassing the HTTP-200-on-8002 gate.\n"
        f"Smoke-test step name: {smoke_step.get('name')!r}"
    )

    assert isolation_step.get("continue-on-error") is not True, (
        "AC-8.5: The isolation step must NOT have 'continue-on-error: true'.\n"
        "Setting this flag would allow the pipeline to succeed even when solanaBilly "
        "is found to be disrupted — bypassing the isolation gate.\n"
        f"Isolation step name: {isolation_step.get('name')!r}"
    )


def test_isolation_step_is_final_step_in_deploy_job() -> None:
    """The isolation step must be the LAST step in the deploy job (AC-8.5).

    If any step were added after the isolation check, a successful run would be
    reported even if the isolation step had failed and the workflow happened to
    continue (e.g. due to a misconfigured runner). Keeping isolation last
    ensures the pipeline's final verdict is the isolation gate's verdict.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-8.5: No isolation step found in 'deploy' job. "
        "Expected a step with 'isolation' in its name."
    )

    isolation_idx = steps.index(isolation_step)
    last_idx = len(steps) - 1

    assert isolation_idx == last_idx, (
        f"AC-8.5: The isolation step (index {isolation_idx}) must be the LAST step "
        f"in the deploy job (last index: {last_idx}).\n"
        "No step may follow the isolation gate — otherwise a subsequent step could "
        "report success after a failed isolation check.\n"
        f"Current deploy job step names (in order):\n"
        + "\n".join(
            f"  [{i}] {s.get('name', '<unnamed>')}" for i, s in enumerate(steps)
        )
    )


def test_vps_dual_gate_covers_both_ports() -> None:
    """The deploy job must gate on BOTH port 8002 (solanatrilly) AND port 8001 (solanaBilly) (AC-8.5).

    A single-port check is insufficient for the AC-8.5 definition of done:
      - Port 8002 (in the smoke-test step) confirms solanatrilly is up.
      - Port 8001 (in the isolation step) confirms solanaBilly is untouched.
    Both ports must be referenced in the deploy job's run scripts so neither
    gate can be quietly removed without breaking these tests.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-8.5: No smoke-test step found in 'deploy' job."
    )

    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-8.5: No isolation step found in 'deploy' job."
    )

    combined_scripts = _all_deploy_job_run_scripts(steps)

    assert ":8002" in combined_scripts, (
        "AC-8.5 dual-gate: Port 8002 (solanatrilly staging) must be referenced "
        "in the deploy job's run scripts (expected in the smoke-test step).\n"
        "The smoke-test must curl http://<VPS_HOST>:8002/health/ to confirm the "
        "solanatrilly stack is serving HTTP 200."
    )

    assert ":8001" in combined_scripts, (
        "AC-8.5 dual-gate: Port 8001 (solanaBilly) must be referenced in the "
        "deploy job's run scripts (expected in the isolation step).\n"
        "The isolation step must verify http://<VPS_HOST>:8001/ still responds "
        "to confirm solanaBilly was not disrupted by the solanatrilly deployment."
    )


def test_smoke_test_uses_retry_loop_not_single_shot() -> None:
    """The smoke-test step must use a retry loop, not a single-shot curl call (AC-8.5).

    A single-shot curl against /health/ would be brittle: if the container has
    not finished starting, the check fails even though the deploy succeeded.
    The AC requires the smoke-test to give the stack time to start — this must
    be implemented as a loop (for/while/until/seq) so transient startup delays
    do not cause false failures. A retry loop also makes it harder to bypass the
    gate by just changing a timeout value; the gate logic itself must be present.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-8.5: No smoke-test step found in 'deploy' job. "
        "Expected a step with 'smoke' in its name."
    )

    script = str(smoke_step.get("run", ""))

    loop_keywords = ("seq", "for", "while", "until")
    has_loop = any(kw in script for kw in loop_keywords)

    assert has_loop, (
        "AC-8.5: The smoke-test step must use a retry loop (seq / for / while / until) "
        "rather than a single-shot curl call.\n"
        "A single curl is brittle against container startup time. The loop must "
        "retry the /health/ check across multiple attempts before declaring failure.\n"
        f"None of {loop_keywords!r} found in the smoke-test script:\n{script}"
    )


def test_smoke_test_exits_one_on_max_retries_exhausted() -> None:
    """The smoke-test step must contain an explicit 'exit 1' failure path (AC-8.5).

    When all retry attempts are exhausted without a 200 response, the smoke-test
    must explicitly exit with code 1 so GitHub Actions marks the workflow run as
    failed. An implicit non-zero exit (e.g. a failed command without 'exit 1')
    is not sufficient — the failure path must be deliberate and auditable in the
    script. This is distinct from ac64's looser check (which accepts either
    'exit 0' OR 'exit 1' anywhere); this test requires 'exit 1' specifically
    to be present as the explicit exhausted-retries failure path.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-8.5: No smoke-test step found in 'deploy' job. "
        "Expected a step with 'smoke' in its name."
    )

    script = str(smoke_step.get("run", ""))

    assert "exit 1" in script, (
        "AC-8.5: The smoke-test step must contain an explicit 'exit 1' statement "
        "for the case where all retries are exhausted without HTTP 200.\n"
        "Without 'exit 1', the step may silently succeed (exit code 0) even when "
        "the VPS health check never returned 200 — bypassing the gate entirely.\n"
        f"Current smoke-test script:\n{script}"
    )


def test_full_deploy_verification_sequence_is_ordered() -> None:
    """Assert the complete three-step ordering: deploy -> smoke-test -> isolation (AC-8.5).

    AC-8.5 requires the verification gates to be ordered correctly:
      1. 'Deploy to VPS staging stack' — bring the stack up.
      2. Smoke-test step ('smoke' in name) — confirm solanatrilly serves HTTP 200.
      3. Isolation step ('isolation' in name) — confirm solanaBilly is untouched.

    This test asserts all three pairwise ordering constraints simultaneously,
    which is more comprehensive than the individual pairwise tests in ac64/ac65:
      - smoke comes after deploy (ac64 covers this individually)
      - isolation comes after smoke (ac65 covers this individually)
      - the full chain deploy < smoke < isolation must hold together
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    deploy_step = _find_deploy_vps_step(steps)
    smoke_step = _find_smoke_step(steps)
    isolation_step = _find_isolation_step(steps)

    assert deploy_step is not None, (
        "AC-8.5: No 'Deploy to VPS' step found in the 'deploy' job. "
        "Expected a step with 'deploy to vps' in its name (case-insensitive).\n"
        f"Current step names: {[s.get('name', '<unnamed>') for s in steps]}"
    )
    assert smoke_step is not None, (
        "AC-8.5: No smoke-test step found in the 'deploy' job. "
        "Expected a step with 'smoke' in its name.\n"
        f"Current step names: {[s.get('name', '<unnamed>') for s in steps]}"
    )
    assert isolation_step is not None, (
        "AC-8.5: No isolation step found in the 'deploy' job. "
        "Expected a step with 'isolation' in its name.\n"
        f"Current step names: {[s.get('name', '<unnamed>') for s in steps]}"
    )

    deploy_idx = steps.index(deploy_step)
    smoke_idx = steps.index(smoke_step)
    isolation_idx = steps.index(isolation_step)

    step_names = [s.get("name", "<unnamed>") for s in steps]

    assert deploy_idx < smoke_idx, (
        f"AC-8.5 ordering violation: 'Deploy to VPS' step (index {deploy_idx}) "
        f"must come BEFORE the smoke-test step (index {smoke_idx}).\n"
        f"Current step order: {step_names}"
    )
    assert smoke_idx < isolation_idx, (
        f"AC-8.5 ordering violation: smoke-test step (index {smoke_idx}) "
        f"must come BEFORE the isolation step (index {isolation_idx}).\n"
        f"Current step order: {step_names}"
    )
    assert deploy_idx < isolation_idx, (
        f"AC-8.5 ordering violation: 'Deploy to VPS' step (index {deploy_idx}) "
        f"must come BEFORE the isolation step (index {isolation_idx}).\n"
        f"Current step order: {step_names}"
    )
