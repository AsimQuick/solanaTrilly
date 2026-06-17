# ---
# file: core/tests/test_deploy_rerun_ac462.py
# project: solanatrilly
# purpose: AC-46.2 — Verify the deploy workflow is correctly fixed to enable a green
#          re-deploy of the US-43 blend serving path to the VPS solanatrilly staging stack.
#          Verifies: --remove-orphans fix, AC-39.2 phase-promoter gate, AC-12.3 smoke-test
#          retry-with-backoff, US-40 workflow_call gate, and the green deploy record file.
# story: US-46 AC-46.2
# sprint: sprint-10
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, yaml, re
# ---
"""AC-46.2 — Clean re-deploy of the US-43 blend serving path via a green deploy run.

AC text:
  Re-run the deploy on 'main' at HEAD (the unified US-40 workflow_call gate —
  gated by the already-green canonical ci.yml 'test' job, NOT a divergent inline
  copy) and obtain an ACTUAL GREEN deploy run that deploys the US-43 blend serving
  path (BlendScorer + score_token Celery task + the LightGBM in-container
  requirement + libgomp1) to the VPS solanatrilly staging stack. Verified by the
  green deploy run id being recorded and the AC-39.2 phase-promoter pre-deploy
  step + the AC-12.3 smoke-test retry-with-backoff confirmed preserved in that run.

Fix applied: `--remove-orphans` added to `docker compose up -d` in deploy.yml.
This resolves the orphaned-container race condition diagnosed in AC-46.1.

Tests in this module:
  test_remove_orphans_present_in_deploy_step
      --remove-orphans is present in the VPS deploy step script.
  test_ac392_phase_promoter_gate_present
      The AC-39.2 phase-promoter step is in the deploy job and invokes
      promote_sprint_phase.py against scrum-master/sprint*.json.
  test_ac123_smoke_test_retry_backoff_preserved
      The AC-12.3 smoke-test step retains SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY
      bounded retry loop on /health/ port 8002.
  test_workflow_call_gate_not_inline_copy
      deploy.yml uses workflow_call to ci.yml (US-40 unified gate);
      no inline pytest job is present.
  test_green_deploy_record_file_exists
      ops/green_deploy_ac462.md exists — the green run record file is committed.
  test_green_deploy_record_cites_remove_orphans_fix
      The record file documents the --remove-orphans fix applied.
  test_green_deploy_record_cites_ac392_and_ac123_steps
      The record file confirms AC-39.2 and AC-12.3 steps are present in the run.
  test_deploy_scoped_with_p_solanatrilly
      Every docker compose command in the deploy step is scoped -p solanatrilly.
  test_deploy_uses_staging_compose_file
      The deploy step references docker-compose.staging.yml (not docker-compose.yml).
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GREEN_DEPLOY_RECORD = REPO_ROOT / "ops" / "green_deploy_ac462.md"

VPS_STEP_NAME = "Deploy to VPS staging stack"
PHASE_PROMOTER_STEP_NAME = "Phase-promoter pre-deploy gate"
SMOKE_STEP_KEYWORD = "smoke"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _deploy_job_steps(data: dict) -> list[dict]:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        f"deploy.yml must contain a 'deploy' job. Current jobs: {list(jobs.keys())}"
    )
    steps = job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _find_step(steps: list[dict], keyword: str) -> dict | None:
    kw = keyword.lower()
    for step in steps:
        if kw in str(step.get("name", "")).lower():
            return step
    return None


def _find_step_by_name_or_run(steps: list[dict], keyword: str) -> dict | None:
    kw = keyword.lower()
    for step in steps:
        if kw in str(step.get("name", "")).lower() or kw in str(step.get("run", "")).lower():
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_remove_orphans_present_in_deploy_step() -> None:
    """--remove-orphans must be in the VPS deploy step script.

    AC-46.2 resolution: add --remove-orphans to docker compose up -d so that
    stale orphaned containers from prior deploy sessions are removed before the
    new stack is started, eliminating the container-reconcile race that caused
    run 27673867804 to fail with 'No such container'.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    vps_step = _find_step(steps, VPS_STEP_NAME)
    assert vps_step is not None, (
        f"AC-46.2: No step named '{VPS_STEP_NAME}' found in deploy job. "
        f"Steps: {[s.get('name') for s in steps]}"
    )
    script = str(vps_step.get("run", ""))
    assert "--remove-orphans" in script, (
        "AC-46.2: '--remove-orphans' must be in the 'Deploy to VPS staging stack' step script. "
        "This flag resolves the orphaned-container race condition diagnosed in AC-46.1 "
        "(run 27673867804). Without it, stale image-hash-prefixed containers from prior "
        "deploys can cause 'No such container' errors during stack reconciliation.\n"
        f"Current script:\n{script}"
    )


def test_ac392_phase_promoter_gate_present() -> None:
    """The AC-39.2 phase-promoter step must be in the deploy job and invoke the script.

    AC-46.2 requires the green run to confirm 'the AC-39.2 phase-promoter pre-deploy
    step preserved in that run'. The step must call promote_sprint_phase.py against
    scrum-master/sprint*.json and must appear BEFORE the VPS deploy step.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    promoter_step = _find_step(steps, PHASE_PROMOTER_STEP_NAME)
    assert promoter_step is not None, (
        f"AC-46.2 / AC-39.2: No step containing '{PHASE_PROMOTER_STEP_NAME}' found "
        "in the deploy job. The phase-promoter pre-deploy gate must be present. "
        f"Steps: {[s.get('name') for s in steps]}"
    )
    script = str(promoter_step.get("run", ""))
    assert "promote_sprint_phase.py" in script, (
        "AC-46.2 / AC-39.2: The phase-promoter step must invoke promote_sprint_phase.py. "
        f"Current run script: {script!r}"
    )
    assert "sprint" in script.lower(), (
        "AC-46.2 / AC-39.2: The phase-promoter step must reference sprint JSON file(s). "
        f"Current run script: {script!r}"
    )

    # The promoter step must appear before the VPS deploy step
    step_names = [s.get("name", "") for s in steps]
    promoter_idx = next(
        (i for i, s in enumerate(steps) if PHASE_PROMOTER_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    vps_idx = next(
        (i for i, s in enumerate(steps) if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert promoter_idx is not None and vps_idx is not None and promoter_idx < vps_idx, (
        "AC-46.2 / AC-39.2: Phase-promoter step must appear BEFORE the VPS deploy step. "
        f"Step order: {step_names}"
    )


def test_ac123_smoke_test_retry_backoff_preserved() -> None:
    """The AC-12.3 smoke-test retry-with-backoff must be preserved in the deploy job.

    AC-46.2 requires the green run to confirm 'the AC-12.3 smoke-test retry-with-backoff
    confirmed preserved in that run'. The smoke-test step must retain:
    - SMOKE_MAX_ATTEMPTS (bounded retry count)
    - SMOKE_RETRY_DELAY (sleep between attempts)
    - A for-loop bounded by $SMOKE_MAX_ATTEMPTS
    - exit 0 on success inside the loop, exit 1 after exhausting all attempts
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_step_by_name_or_run(steps, SMOKE_STEP_KEYWORD)
    assert smoke_step is not None, (
        "AC-46.2 / AC-12.3: No smoke-test step found in deploy job. "
        f"Steps: {[s.get('name') for s in steps]}"
    )
    script = str(smoke_step.get("run", ""))

    assert re.search(r"SMOKE_MAX_ATTEMPTS\s*=\s*\d+", script) is not None, (
        "AC-46.2 / AC-12.3: SMOKE_MAX_ATTEMPTS must be set in the smoke-test step."
    )
    assert "SMOKE_RETRY_DELAY" in script, (
        "AC-46.2 / AC-12.3: SMOKE_RETRY_DELAY must be set in the smoke-test step."
    )
    assert re.search(r"seq\s+1\s+[\"']?\$\{?SMOKE_MAX_ATTEMPTS\}?", script) is not None, (
        "AC-46.2 / AC-12.3: Retry loop must be bounded by $SMOKE_MAX_ATTEMPTS."
    )
    assert re.search(r"sleep\s+[\"']?\$\{?SMOKE_RETRY_DELAY\}?", script) is not None, (
        "AC-46.2 / AC-12.3: Retry loop must sleep $SMOKE_RETRY_DELAY between attempts."
    )
    assert "8002" in script, (
        "AC-46.2 / AC-12.3: Smoke-test must check port 8002 (/health/ endpoint)."
    )
    assert "exit 0" in script and "exit 1" in script, (
        "AC-46.2 / AC-12.3: Smoke-test must exit 0 on success and exit 1 on failure."
    )


def test_workflow_call_gate_not_inline_copy() -> None:
    """deploy.yml must use the US-40 workflow_call gate to ci.yml — no inline pytest.

    AC-46.2 specifies 'the unified US-40 workflow_call gate — gated by the
    already-green canonical ci.yml test job, NOT a divergent inline copy'.
    The run 27673867804 passed this gate (ci/test job 81844340487 was GREEN),
    confirming the failure was in the deploy-context path, not the test gate.
    This invariant must be preserved in the green re-run.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}

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
        "AC-46.2 H1 violation: deploy.yml has inline pytest in job(s): "
        f"{inline_pytest_jobs}. The canonical ci.yml test job must be called "
        "via workflow_call, not duplicated inline."
    )

    # ci.yml must be called via workflow_call
    ci_calling_jobs = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]
    assert ci_calling_jobs, (
        "AC-46.2: deploy.yml must call ci.yml via workflow_call (US-40 gate). "
        f"Current jobs: {list(jobs.keys())}"
    )


def test_green_deploy_record_file_exists() -> None:
    """ops/green_deploy_ac462.md must exist as the green deploy record file.

    AC-46.2: 'Verified by the green deploy run id being recorded'. The record
    file is the durable evidence artifact — committed to the repo alongside the
    workflow fix so the orchestrator can fill in the actual run ID and URL after
    the deploy runs on main.
    """
    assert GREEN_DEPLOY_RECORD.exists(), (
        f"AC-46.2: Green deploy record file not found at {GREEN_DEPLOY_RECORD}. "
        "This file must be committed as ops/green_deploy_ac462.md."
    )
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert text.strip(), f"AC-46.2: Green deploy record file at {GREEN_DEPLOY_RECORD} is empty."
    assert len(text) > 200, (
        f"AC-46.2: Green deploy record file at {GREEN_DEPLOY_RECORD} is too short "
        f"({len(text)} chars). It must document the fix and run record structure."
    )


def test_green_deploy_record_cites_remove_orphans_fix() -> None:
    """The green deploy record must document the --remove-orphans fix applied.

    The fix is the direct resolution of the root cause diagnosed in AC-46.1.
    Without documenting it, the record does not connect the green run to the
    specific code change that enabled it.
    """
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert "--remove-orphans" in text, (
        "AC-46.2: Green deploy record must document the '--remove-orphans' fix. "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )


def test_green_deploy_record_cites_ac392_and_ac123_steps() -> None:
    """The record must confirm both AC-39.2 phase-promoter and AC-12.3 smoke-test are present.

    AC-46.2: 'AC-39.2 phase-promoter pre-deploy step + the AC-12.3 smoke-test
    retry-with-backoff confirmed preserved in that run'. The record file must
    reference both AC markers so the Tester can cross-check against the deploy log.
    """
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert "AC-39.2" in text or "ac-39.2" in text.lower(), (
        "AC-46.2: Green deploy record must reference AC-39.2 (phase-promoter gate). "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )
    assert "AC-12.3" in text or "ac-12.3" in text.lower(), (
        "AC-46.2: Green deploy record must reference AC-12.3 (smoke-test retry-with-backoff). "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )


def test_deploy_scoped_with_p_solanatrilly() -> None:
    """Every docker compose command in the VPS deploy step must be scoped -p solanatrilly.

    CLAUDE.md hard isolation rule: ALL docker compose commands must use
    '-p solanatrilly' to prevent cross-contamination with solanaBilly's stack.
    This must hold for both the 'pull' and 'up -d --remove-orphans' commands.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    vps_step = _find_step(steps, VPS_STEP_NAME)
    assert vps_step is not None, (
        f"AC-46.2: No step named '{VPS_STEP_NAME}' found in deploy job."
    )
    script = str(vps_step.get("run", ""))
    compose_calls = re.findall(r"docker compose[^\n]*", script)
    assert compose_calls, (
        "AC-46.2: No 'docker compose' commands found in the VPS deploy step."
    )
    for call in compose_calls:
        assert "-p solanatrilly" in call, (
            "AC-46.2: Every docker compose call in the VPS deploy step must include "
            f"'-p solanatrilly'. Unscoped call found: {call!r}"
        )


def test_deploy_uses_staging_compose_file() -> None:
    """The VPS deploy step must reference docker-compose.staging.yml (not docker-compose.yml).

    The staging compose file is the CD entrypoint — it defines the stack that
    runs on VPS with GHCR image references, not the local dev compose file.
    Using the wrong compose file would deploy the wrong image or topology.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    vps_step = _find_step(steps, VPS_STEP_NAME)
    assert vps_step is not None, (
        f"AC-46.2: No step named '{VPS_STEP_NAME}' found in deploy job."
    )
    script = str(vps_step.get("run", ""))
    assert "docker-compose.staging.yml" in script, (
        "AC-46.2: VPS deploy step must reference 'docker-compose.staging.yml'. "
        f"Current script:\n{script}"
    )
    assert "docker-compose.staging.yml" in script and "docker-compose.yml" not in script.replace(
        "docker-compose.staging.yml", ""
    ), (
        "AC-46.2: VPS deploy step must use docker-compose.staging.yml, not the dev compose file."
    )
