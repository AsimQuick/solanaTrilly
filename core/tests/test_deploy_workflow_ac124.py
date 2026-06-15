# ---
# module: core.tests.test_deploy_workflow_ac124
# sprint: sprint-4
# story: US-12 AC-12.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-12.4 — End-to-end deploy GREEN: GHCR push + VPS pull + HTTP 200 + isolation.

AC-12.4 states: a deploy on main (or via workflow_dispatch) succeeds END-TO-END
and GREEN: image built+pushed to GHCR, pulled on the VPS, stack up with
'docker compose -p solanatrilly -f docker-compose.staging.yml pull && up -d',
the smoke-test hits http://VPS:8002/health/ and asserts HTTP 200, and the
isolation step confirms solanaBilly is still up on 8001
('docker compose -p solanabilly ps' + 8001 responds).

These structural tests verify the deploy.yml is correctly wired for that
complete path. Each test checks an aspect not fully covered by the existing
AC-8.3/8.4/8.5/6.5/12.3 suites.

The live GREEN deploy run (with both smoke-test and isolation steps passing) is
the definitive AC-12.4 verification; these tests confirm the pipeline is
structured to produce that outcome.

Tests:
  test_workflow_dispatch_trigger_present
  test_smoke_test_success_condition_is_exactly_200
  test_isolation_grep_matches_running_or_up_case_insensitive
  test_isolation_curl_8001_has_max_time_guard
  test_isolation_rejects_000_or_empty_8001_response
  test_vps_deploy_path_is_root_solanatrilly
  test_isolation_step_name_references_ac124
  test_all_three_jobs_present_for_e2e_pipeline
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
        "AC-12.4 requires a .github/workflows/deploy.yml file."
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_workflow_dispatch_trigger_present() -> None:
    """The deploy workflow must support workflow_dispatch to enable on-demand green runs.

    AC-12.4 specifies the deploy can be triggered 'on main (or via
    workflow_dispatch)'. Without workflow_dispatch in the triggers, a green
    deploy run can only be produced by pushing to main — making it impossible
    to verify the pipeline without a code change.

    Note: PyYAML parses the YAML 'on:' key as the boolean True (YAML 1.1
    default), so the lookup uses True as the key, not the string "on".
    """
    data = _load_deploy()
    # PyYAML parses YAML 1.1 bare 'on' as boolean True
    on_block = data.get(True) or data.get("on") or {}
    if isinstance(on_block, str):
        on_block = {}

    assert "workflow_dispatch" in on_block, (
        "AC-12.4: deploy.yml must include 'workflow_dispatch:' in its 'on:' triggers "
        "so a green deploy run can be triggered on-demand without a push to main.\n"
        f"Current 'on:' triggers: {list(on_block.keys())}"
    )


def test_smoke_test_success_condition_is_exactly_200() -> None:
    """The smoke-test success condition must compare status to exactly '200', not any 2xx.

    AC-12.4 requires 'the smoke-test hits http://VPS:8002/health/ and asserts
    HTTP 200'. A comparison like '[ "$status" = "200" ]' proves the gate is
    checking for 200 specifically — not 301, 302, 404, or other codes that
    curl might return. Accepting anything other than 200 could mask a broken
    /health/ endpoint that redirects or serves an error page.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-12.4: No smoke-test step found in 'deploy' job "
        "(expected a step with 'smoke' in its name)."
    )
    script = str(smoke_step.get("run", ""))

    # The comparison must be an exact string match against "200"
    assert re.search(r'=\s*["\']?200["\']?', script) is not None, (
        "AC-12.4: Smoke-test step must compare the HTTP status to exactly '200'.\n"
        "Expected pattern: [ \"$status\" = \"200\" ] (exact match, not a 2xx range).\n"
        f"Script:\n{script}"
    )


def test_isolation_grep_matches_running_or_up_case_insensitive() -> None:
    """The isolation step must grep 'docker compose ps' output for 'running' or 'up'.

    AC-12.4 specifies: 'docker compose -p solanabilly ps' confirms solanaBilly
    is still up. The step must parse the ps output to detect the running/up
    state — not just capture the output and ignore it. The grep must be
    case-insensitive (grep -i or grep -iE) to handle docker compose ps output
    variations across versions ('Up', 'running', 'Running').
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.4: No isolation step found in 'deploy' job "
        "(expected a step with 'isolation' in its name)."
    )
    script = str(isolation_step.get("run", ""))

    # Must grep for 'running' and/or 'up' in the PS output
    assert re.search(r"grep.*[Rr]unning|grep.*\bup\b", script, re.IGNORECASE) is not None, (
        "AC-12.4: Isolation step must grep the 'docker compose -p solanabilly ps' "
        "output for 'running' or 'up' to detect container state.\n"
        "Expected: grep -qiE 'running|up' (case-insensitive match).\n"
        f"Script:\n{script}"
    )


def test_isolation_curl_8001_has_max_time_guard() -> None:
    """The isolation step's 8001 curl must use --max-time to prevent pipeline hangs.

    AC-12.4 requires the isolation step to confirm 8001 'responds'. If solanaBilly
    hangs on accept but never sends a response, a curl without --max-time would
    block indefinitely, causing the GitHub Actions runner to time out after its
    maximum job duration rather than failing quickly and cleanly. The --max-time
    flag ensures the isolation check terminates in bounded time.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.4: No isolation step found in 'deploy' job "
        "(expected a step with 'isolation' in its name)."
    )
    script = str(isolation_step.get("run", ""))

    assert "--max-time" in script, (
        "AC-12.4: The isolation step's curl against port 8001 must include "
        "'--max-time <seconds>' to prevent an unresponsive solanaBilly from "
        "hanging the GitHub Actions runner indefinitely.\n"
        f"Script:\n{script}"
    )


def test_isolation_rejects_000_or_empty_8001_response() -> None:
    """The isolation step must treat an empty or '000' HTTP status as a failure.

    AC-12.4 requires that 8001 'responds' — not just that curl exits without error.
    curl returns status '000' (and often exits 0 with ||true) when the connection
    is refused, the host is unreachable, or the request times out. An isolation
    step that accepts '000' would silently pass even when solanaBilly is down.
    The check must explicitly reject '000' and the empty string.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.4: No isolation step found in 'deploy' job "
        "(expected a step with 'isolation' in its name)."
    )
    script = str(isolation_step.get("run", ""))

    # Must check for "000" (curl's connection-refused code)
    assert '"000"' in script or "'000'" in script, (
        "AC-12.4: Isolation step must explicitly reject status '000' for the "
        "port 8001 curl check.\n"
        "curl returns HTTP code '000' when the connection is refused or the host "
        "is unreachable; accepting it would pass the check even when solanaBilly is down.\n"
        f"Script:\n{script}"
    )


def test_vps_deploy_path_is_root_solanatrilly() -> None:
    """The VPS deploy must write files under /root/solanatrilly/ (CLAUDE.md requirement).

    AC-12.4 requires the stack to be brought up on the VPS. CLAUDE.md specifies
    the deploy path as /root/solanatrilly/ (the root user's home directory).
    Using any other path would mean the compose file is written to the wrong
    location, the pull would target the wrong project, and the smoke-test would
    fail.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    # Find the VPS deploy step (not isolation, not smoke)
    all_run_scripts = "\n".join(
        str(step.get("run", "")) for step in steps if "run" in step
    )

    assert "/root/solanatrilly" in all_run_scripts, (
        "AC-12.4: The deploy job must write the compose file to "
        "'/root/solanatrilly/' on the VPS per CLAUDE.md.\n"
        "CLAUDE.md: 'VPS: ssh root@140.82.43.36; deploy to /root/solanatrilly/'\n"
        "Without the correct path, 'docker compose pull && up -d' targets the wrong project."
    )


def test_isolation_step_name_references_ac124() -> None:
    """The isolation step name must reference AC-12.4.

    AC-12.4 explicitly requires the isolation step to confirm solanaBilly is
    still up on 8001 — the step name must reflect this to make traceability
    between the deploy log and the AC clear. Without the AC-12.4 reference in
    the step name, auditing a deploy run log for AC-12.4 compliance requires
    guessing which step implements it.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.4: No isolation step found in 'deploy' job "
        "(expected a step with 'isolation' in its name)."
    )
    name = str(isolation_step.get("name", ""))
    assert "12.4" in name, (
        "AC-12.4: The isolation step name must include '12.4' so the deploy "
        "log clearly references AC-12.4 compliance.\n"
        f"Current step name: {name!r}\n"
        "Expected: e.g. 'Verify solanaBilly isolation (AC-6.5 / AC-12.4)'"
    )


def test_all_three_jobs_present_for_e2e_pipeline() -> None:
    """The deploy workflow must contain all three jobs: ci, build-and-push, deploy.

    AC-12.4 requires the complete E2E path: image built+pushed to GHCR (the
    'build-and-push' job), then pulled on the VPS and smoke-tested (the 'deploy'
    job), gated on CI (the 'ci' job). All three jobs must be present — removing
    any one of them breaks the AC-12.4 end-to-end guarantee.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}
    job_names = list(jobs.keys())

    assert "ci" in job_names, (
        "AC-12.4: deploy.yml must contain a 'ci' job to gate image builds on "
        "passing tests.\n"
        f"Current jobs: {job_names}"
    )
    assert "build-and-push" in job_names, (
        "AC-12.4: deploy.yml must contain a 'build-and-push' job to build and "
        "push the web image to GHCR before the VPS deploy.\n"
        f"Current jobs: {job_names}"
    )
    assert "deploy" in job_names, (
        "AC-12.4: deploy.yml must contain a 'deploy' job to pull the image on "
        "the VPS, run 'docker compose up -d', and run the smoke-test + isolation.\n"
        f"Current jobs: {job_names}"
    )
