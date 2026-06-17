# ---
# file: core/tests/test_deploy_boundary_run_ac403.py
# project: solanatrilly
# purpose: AC-40.3 — structural YAML test verifying the sprint-8 deliberate
#          boundary-deploy can be triggered via workflow_dispatch AND that
#          deploy.yml carries all three Tester-confirm conditions:
#            1. HTTP 200 on 8002 with AC-12.3 retry-with-backoff
#            2. 'listener' container Up (driving the helius_live birth-tape source)
#            3. solanaBilly untouched on 8001
# story: US-40 AC-40.3
# sprint: sprint-9
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, yaml
# ---
"""AC-40.3 — deliberate sprint-8 boundary deploy: workflow_dispatch wired + three
Tester-confirm conditions present in deploy.yml.

AC-40.3 states: Re-run the deploy on 'main' at HEAD and Tester-CONFIRM from an
ACTUAL GREEN deploy run:
  1. HTTP 200 on 8002  — smoke-test step with AC-12.3 retry-with-backoff
  2. 'listener' container Up  — driving the helius_live birth-tape source
  3. solanaBilly untouched on 8001  — hard isolation preserved

A partial-evidence PR-merge deploy is NOT sufficient — the deliberate boundary
run at HEAD must be green. workflow_dispatch enables that deliberate manual
trigger on main without requiring a new push commit.

Tests in this module:
  test_workflow_dispatch_trigger_present
      deploy.yml has workflow_dispatch in its on: triggers so a deliberate
      manual boundary run on main can be initiated without a new push.
  test_main_push_trigger_present
      deploy.yml triggers on push to main, confirming merges also deploy.
  test_ac403_three_confirm_conditions_all_wired
      All three AC-40.3 Tester-confirm conditions are wired as distinct steps.
  test_http_200_smoke_test_with_retry_backoff_wired
      HTTP 200 smoke-test on port 8002 with AC-12.3 retry-backoff is present.
  test_listener_container_up_check_wired
      'listener' container Up check is present in the deploy job.
  test_solanabilly_isolation_on_8001_wired
      solanaBilly isolation check on port 8001 is present.
  test_deploy_gate_is_h1_compliant
      deploy.yml has no inline pytest job; canonical ci.yml is called via
      workflow_call (H1 invariant from AC-40.2, preserved through AC-40.3).
"""

import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-40.3 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _deploy_job_steps(data: dict) -> list[dict]:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a 'deploy' job. "
        f"Current jobs: {list(jobs.keys())}"
    )
    steps = job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _find_step_by_keyword(steps: list[dict], keyword: str) -> dict | None:
    kw = keyword.lower()
    for step in steps:
        name = str(step.get("name", "")).lower()
        run = str(step.get("run", "")).lower()
        if kw in name or kw in run:
            return step
    return None


def _all_step_scripts(steps: list[dict]) -> str:
    return "\n".join(str(s.get("run", "")) for s in steps)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_workflow_dispatch_trigger_present() -> None:
    """deploy.yml must have workflow_dispatch in its on: triggers.

    AC-40.3 specifies that "the deliberate boundary run at HEAD must be green"
    and that "a partial-evidence PR-merge deploy is NOT sufficient." A manual
    workflow_dispatch trigger lets the operator/orchestrator fire a deliberate
    deploy on main at HEAD without requiring a new commit. Without it, only
    push-triggered runs are possible, which are tied to merge events rather than
    deliberate sprint-boundary verification runs.
    """
    data = _load_deploy()
    on = data.get("on") or data.get(True) or {}
    trigger_keys = list(on.keys()) if isinstance(on, dict) else [str(on)]
    assert "workflow_dispatch" in trigger_keys, (
        "AC-40.3: deploy.yml must include 'workflow_dispatch' in its on: triggers. "
        "This enables a deliberate boundary-deploy run on main without a new push commit. "
        f"Current triggers: {trigger_keys}"
    )


def test_main_push_trigger_present() -> None:
    """deploy.yml must trigger on push to main branch.

    AC-40.3 requires the deploy to run on 'main' at HEAD. When the sprint-8
    fix PRs are merged, the push-to-main trigger fires automatically. Both
    push (merge-triggered) and workflow_dispatch (deliberate run) must be wired
    so the Tester can confirm from either invocation.
    """
    data = _load_deploy()
    on = data.get("on") or data.get(True) or {}
    assert isinstance(on, dict), (
        f"AC-40.3: deploy.yml on: block must be a mapping. Got: {type(on)}"
    )
    push_cfg = on.get("push") or {}
    branches = push_cfg.get("branches") or []
    assert "main" in branches, (
        "AC-40.3: deploy.yml push trigger must include 'main' so merges to main "
        "deploy automatically. "
        f"Current push.branches: {branches}"
    )


def test_ac403_three_confirm_conditions_all_wired() -> None:
    """All three AC-40.3 Tester-confirm conditions must be distinct steps.

    AC-40.3 Tester confirmation requires:
      1. HTTP 200 on 8002 — smoke-test step with retry-backoff (AC-12.3)
      2. 'listener' container Up — driving the helius_live birth-tape source
      3. solanaBilly untouched on 8001 — isolation preserved (AC-6.5)

    Each condition must appear as a distinct, named step so each one appears
    independently in the deploy run log and can be confirmed individually by
    the Tester.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    all_scripts = _all_step_scripts(steps)

    # Condition 1: HTTP 200 on port 8002 with smoke-test
    smoke_step = _find_step_by_keyword(steps, "smoke")
    assert smoke_step is not None, (
        "AC-40.3 condition 1 missing: No smoke-test step found in deploy job. "
        "A step checking HTTP 200 on 8002 is required for Tester confirmation. "
        f"Current step names: {[s.get('name') for s in steps]}"
    )
    assert "8002" in str(smoke_step.get("run", "")), (
        "AC-40.3 condition 1: Smoke-test step must check port 8002. "
        f"Step run: {smoke_step.get('run', '')!r}"
    )

    # Condition 2: listener container Up
    listener_step = _find_step_by_keyword(steps, "listener")
    assert listener_step is not None, (
        "AC-40.3 condition 2 missing: No listener check step found in deploy job. "
        "A step verifying the 'listener' container is Up is required. "
        f"Current step names: {[s.get('name') for s in steps]}"
    )

    # Condition 3: solanaBilly isolation on port 8001
    isolation_step = _find_step_by_keyword(steps, "isolation")
    assert isolation_step is not None, (
        "AC-40.3 condition 3 missing: No solanaBilly isolation step found in deploy job. "
        "A step verifying solanaBilly is untouched on 8001 is required. "
        f"Current step names: {[s.get('name') for s in steps]}"
    )
    assert "8001" in str(isolation_step.get("run", "")), (
        "AC-40.3 condition 3: Isolation step must check port 8001 (solanaBilly). "
        f"Step run: {isolation_step.get('run', '')!r}"
    )

    _ = all_scripts  # confirmed via individual step checks above


def test_http_200_smoke_test_with_retry_backoff_wired() -> None:
    """HTTP 200 smoke-test on 8002 must retain AC-12.3 retry-with-backoff.

    AC-40.3 explicitly states 'HTTP 200 on 8002 (with the AC-12.3
    retry-with-backoff)'. The smoke-test step must use SMOKE_MAX_ATTEMPTS and
    SMOKE_RETRY_DELAY with a bounded retry loop — not a single-attempt curl.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_step_by_keyword(steps, "smoke")
    assert smoke_step is not None, (
        "AC-40.3 / AC-12.3: No smoke-test step found in deploy job."
    )
    script = str(smoke_step.get("run", ""))

    assert re.search(r"SMOKE_MAX_ATTEMPTS\s*=\s*\d+", script) is not None, (
        "AC-40.3 / AC-12.3: SMOKE_MAX_ATTEMPTS must be defined in the smoke-test step."
    )
    assert re.search(r"SMOKE_RETRY_DELAY", script) is not None, (
        "AC-40.3 / AC-12.3: SMOKE_RETRY_DELAY must be defined in the smoke-test step."
    )
    assert re.search(r"seq\s+1\s+[\"']?\$\{?SMOKE_MAX_ATTEMPTS\}?", script) is not None, (
        "AC-40.3 / AC-12.3: The retry loop must be bounded by $SMOKE_MAX_ATTEMPTS."
    )
    assert re.search(r"sleep\s+[\"']?\$\{?SMOKE_RETRY_DELAY\}?", script) is not None, (
        "AC-40.3 / AC-12.3: The retry loop must sleep $SMOKE_RETRY_DELAY between attempts."
    )
    assert "exit 0" in script, (
        "AC-40.3 / AC-12.3: The smoke-test must exit 0 inside the loop on success."
    )
    assert "exit 1" in script, (
        "AC-40.3 / AC-12.3: The smoke-test must exit 1 after exhausting all attempts."
    )


def test_listener_container_up_check_wired() -> None:
    """The listener check step must verify the 'listener' container is Running/Up.

    AC-40.3 requires the Tester to confirm the 'listener' container is Up
    (driving the helius_live birth-tape source). The check must grep the
    docker compose ps output for the 'listener' service row and its running
    state, and fail the deploy (exit 1) if the container is not Up.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_step_by_keyword(steps, "listener")
    assert listener_step is not None, (
        "AC-40.3: No listener check step found in deploy job."
    )
    script = str(listener_step.get("run", ""))

    assert "-p solanatrilly" in script, (
        "AC-40.3: Listener check must scope docker compose with '-p solanatrilly' "
        "(CLAUDE.md hard isolation rule). "
        f"Script:\n{script}"
    )
    assert re.search(r"grep.*listener", script, re.IGNORECASE) is not None, (
        "AC-40.3: Listener check must grep for 'listener' in docker compose ps output. "
        f"Script:\n{script}"
    )
    has_state_check = bool(
        re.search(r"running\|up", script, re.IGNORECASE)
        or re.search(r"grep.*running", script, re.IGNORECASE)
        or re.search(r"grep.*\bup\b", script, re.IGNORECASE)
    )
    assert has_state_check, (
        "AC-40.3: Listener check must grep for 'running' or 'up' to verify container state. "
        f"Script:\n{script}"
    )
    assert "exit 1" in script, (
        "AC-40.3: Listener check must exit 1 if the container is not Up, "
        "so the deploy fails rather than silently passing. "
        f"Script:\n{script}"
    )


def test_solanabilly_isolation_on_8001_wired() -> None:
    """solanaBilly isolation check must verify port 8001 responds and containers are Up.

    AC-40.3 requires the Tester to confirm solanaBilly is untouched on 8001
    after the solanatrilly deploy. The isolation step must check the solanabilly
    project's container state AND that port 8001 still responds, failing the
    deploy if either condition is violated.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_step_by_keyword(steps, "isolation")
    assert isolation_step is not None, (
        "AC-40.3: No isolation check step found in deploy job. "
        f"Current step names: {[s.get('name') for s in steps]}"
    )
    script = str(isolation_step.get("run", ""))

    assert "8001" in script, (
        "AC-40.3: Isolation check must verify port 8001 (solanaBilly). "
        f"Script:\n{script}"
    )
    assert "solanabilly" in script.lower(), (
        "AC-40.3: Isolation check must scope the solanabilly project "
        "(e.g. -p solanabilly in docker compose). "
        f"Script:\n{script}"
    )
    assert "exit 1" in script, (
        "AC-40.3: Isolation check must exit 1 on isolation failure, "
        "so the deploy fails if solanaBilly is disturbed. "
        f"Script:\n{script}"
    )


def test_deploy_gate_is_h1_compliant() -> None:
    """deploy.yml must be H1-compliant: no inline pytest; canonical ci.yml called.

    AC-40.3 inherits the H1 invariant from AC-40.2: the deploy gate must be the
    canonical ci.yml 'test' job via workflow_call, not a divergent inline copy.
    This test confirms the AC-40.2 fix is preserved through the AC-40.3 boundary
    deploy so the deliberate run uses the same canonical test gate that CI uses.
    """
    data = _load_deploy()
    jobs = data.get("jobs", {})

    # No inline pytest in any non-workflow_call job
    inline_pytest_jobs = []
    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        if "uses" in job_def:
            continue
        for step in (job_def.get("steps") or []):
            if isinstance(step, dict) and "pytest" in str(step.get("run", "")):
                inline_pytest_jobs.append(job_name)
                break

    assert not inline_pytest_jobs, (
        "AC-40.3 H1 violation: deploy.yml has inline pytest job(s): "
        f"{inline_pytest_jobs}. "
        "The deploy gate must call the canonical ci.yml test job via workflow_call, "
        "not run a divergent inline copy."
    )

    # Canonical ci.yml must be called via workflow_call
    ci_calling_jobs = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]
    assert ci_calling_jobs, (
        "AC-40.3 H1 violation: deploy.yml does not call ci.yml via workflow_call. "
        "The deploy gate must reuse the canonical test job from ci.yml. "
        f"Current jobs: {list(jobs.keys())}"
    )
