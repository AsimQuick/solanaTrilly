# ---
# module: core.tests.test_vps_verification_ac125
# sprint: sprint-4
# story: US-12 AC-12.5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-12.5 — VPS verification gates 'done': P0 exit confirmation and sprint JSON normalization.

AC-12.5 states:
  The Tester confirms, from the ACTUAL green deploy run, that the running stack
  answers HTTP 200 on 8002 and solanaBilly is untouched on 8001 — not from
  green pytest alone. On confirmation, US-8 AC-8.3/8.4/8.5 and US-1's
  story-level DoD (the sprint-1 deploy blocker, retrospective A1) are
  retroactively CLOSED and P0 is declared EXITED. The US-8 record is
  normalized so no AC reads done while failed (retrospective C4). New/changed
  files carry metadata front matter.

P0 EXIT CONFIRMATION:
  GitHub Actions run 27543707795 (2026-06-15T11:42:59Z, branch: main)
  completed with conclusion: success. All three jobs passed:
    - ci / test: success
    - build-and-push: success
    - deploy: success
      - Deploy to VPS staging stack: success
      - Smoke-test staging stack (AC-6.4 / AC-12.3): success  → HTTP 200 on :8002
      - Verify solanaBilly isolation (AC-6.5 / AC-12.4): success → :8001 untouched

  This is the definitive AC-12.5 evidence. The orchestrator normalizes sprint3.json
  (US-8 AC-8.3/8.4/8.5) and sprint1.json (US-1 story-level DoD) based on this run.

Tests in this file focus on aspects unique to AC-12.5, not already covered by
AC-8.5 (test_deploy_workflow_ac85.py) or AC-12.4 (test_deploy_workflow_ac124.py):

  test_smoke_test_step_name_references_ac123_for_p0_traceability
  test_isolation_step_name_references_ac124_for_p0_traceability
  test_deploy_job_needs_build_and_push_dependency
  test_no_vps_step_has_conditional_skip
  test_deploy_yml_front_matter_includes_ac125
  test_normalization_constraint_accepts_clean_sprint_json_fixture
  test_normalization_constraint_rejects_checked_done_with_fail_tester_status
  test_normalization_constraint_rejects_checked_done_with_failed_tester_status
  test_normalization_constraint_treats_blocked_as_invalid_for_done_ac
  test_normalization_constraint_ignores_not_started_tester_status
"""
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# ---------------------------------------------------------------------------
# Helpers — deploy.yml navigation
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-12.5 requires a .github/workflows/deploy.yml file."
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
# Helper — sprint JSON normalization constraint
# ---------------------------------------------------------------------------

_FAIL_STATUSES = {"fail", "failed", "blocked"}


def _sprint_json_violations(sprint_data: dict) -> list[str]:
    """Return violation strings for any AC that is done/checked while tester_status is fail/failed/blocked.

    This implements the normalization constraint from retrospective C4:
    no AC may read done/checked while its tester_status is a failure status.
    Handles both 'fail' and 'failed' spellings seen in practice across sprint files.
    """
    violations = []
    for story in sprint_data.get("stories", []):
        story_id = story.get("id", "?")
        for ac in story.get("acceptance_criteria", []):
            ac_id = ac.get("id", "?")
            is_done = ac.get("checked", False) or ac.get("dev_status") == "done"
            tester = str(ac.get("tester_status", "")).lower().strip()
            if is_done and tester in _FAIL_STATUSES:
                violations.append(
                    f"{story_id}.{ac_id}: checked/done=True while tester_status={tester!r}"
                )
    return violations


# ---------------------------------------------------------------------------
# Tests — P0 exit traceability via step names
# ---------------------------------------------------------------------------


def test_smoke_test_step_name_references_ac123_for_p0_traceability() -> None:
    """The smoke-test step name must contain 'AC-12.3' for P0 exit traceability.

    AC-12.5 requires the Tester to confirm P0 exit FROM THE ACTUAL DEPLOY LOG.
    When the smoke-test step name includes 'AC-12.3', a Tester reading the
    GitHub Actions deploy log can directly trace which AC mandated the retry-
    with-backoff behaviour — linking the green step to the specific AC that
    upgraded it from single-shot to reliable. Without this reference, the log
    is ambiguous about which requirement the smoke-test satisfies.

    The confirmed P0 exit run (27543707795) shows step name:
    'Smoke-test staging stack (AC-6.4 / AC-12.3)' — this test locks that in.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-12.5: No smoke-test step found in 'deploy' job "
        "(expected a step with 'smoke' in its name)."
    )
    name = str(smoke_step.get("name", ""))
    assert "12.3" in name, (
        "AC-12.5: The smoke-test step name must contain 'AC-12.3' so that "
        "the Tester can trace the green deploy log to the specific AC that "
        "mandated retry-with-backoff. Without this reference, the log is "
        "ambiguous about which requirement the smoke-test satisfies.\n"
        f"Current step name: {name!r}\n"
        "Expected: e.g. 'Smoke-test staging stack (AC-6.4 / AC-12.3)'"
    )


def test_isolation_step_name_references_ac124_for_p0_traceability() -> None:
    """The isolation step name must contain 'AC-12.4' for P0 exit traceability.

    AC-12.5 requires the Tester to confirm P0 exit from the actual deploy log.
    When the isolation step name includes 'AC-12.4', a Tester reading the log
    can directly trace which AC required the solanaBilly isolation check —
    linking the green step to the story that mandated it. This distinguishes
    the AC-12.4 isolation requirement (end-to-end deploy + isolation confirmation)
    from the earlier AC-6.5 (structural isolation requirement).

    The confirmed P0 exit run (27543707795) shows step name:
    'Verify solanaBilly isolation (AC-6.5 / AC-12.4)' — this test locks that in.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.5: No isolation step found in 'deploy' job "
        "(expected a step with 'isolation' in its name)."
    )
    name = str(isolation_step.get("name", ""))
    assert "12.4" in name, (
        "AC-12.5: The isolation step name must contain 'AC-12.4' so that "
        "the Tester can trace the green deploy log to the specific AC that "
        "required isolation confirmation. Without this reference, the log only "
        "shows the original AC-6.5 step, not the AC-12.4 end-to-end requirement.\n"
        f"Current step name: {name!r}\n"
        "Expected: e.g. 'Verify solanaBilly isolation (AC-6.5 / AC-12.4)'"
    )


# ---------------------------------------------------------------------------
# Tests — deployment pipeline job dependency chain
# ---------------------------------------------------------------------------


def test_deploy_job_needs_build_and_push_dependency() -> None:
    """The 'deploy' job must declare 'needs: build-and-push'.

    AC-12.5 requires the Tester's P0 exit confirmation to come from a deploy
    run where the image was freshly built and pushed to GHCR before the VPS
    pull. If the deploy job did not declare 'needs: build-and-push', it could
    run in parallel with (or before) the image build — pulling a stale image
    to the VPS and passing the smoke-test against old code. The 'needs:' chain
    ensures a green deploy run proves the LATEST code is deployed.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}
    deploy_job = jobs.get("deploy")
    assert deploy_job is not None, (
        "AC-12.5: deploy.yml must contain a 'deploy' job.\n"
        f"Current jobs: {list(jobs.keys())}"
    )
    needs = deploy_job.get("needs")
    if isinstance(needs, str):
        needs_list = [needs]
    elif isinstance(needs, list):
        needs_list = needs
    else:
        needs_list = []

    assert "build-and-push" in needs_list, (
        "AC-12.5: The 'deploy' job must declare 'needs: build-and-push' so "
        "the VPS deploy only runs after a successful image push to GHCR.\n"
        "Without this dependency, the deploy job could pull a stale image — "
        "making a green smoke-test an unreliable P0 exit confirmation.\n"
        f"Current 'needs' for 'deploy' job: {needs!r}"
    )


# ---------------------------------------------------------------------------
# Tests — no conditional skip on VPS verification steps
# ---------------------------------------------------------------------------


def test_no_vps_step_has_conditional_skip() -> None:
    """Neither the smoke-test nor the isolation step may have an 'if:' condition.

    AC-12.5 requires VPS verification to gate 'done' unconditionally. An 'if:'
    condition on either step could skip the gate under certain branch, event, or
    output conditions — silently passing the pipeline without actually verifying
    the VPS. For example, 'if: github.ref == refs/heads/main' would let any
    workflow_dispatch run skip the gate, invalidating the P0 exit confirmation.

    Both verification steps must run on every deploy job execution, with no
    conditional path that allows them to be skipped.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    smoke_step = _find_smoke_step(steps)
    assert smoke_step is not None, (
        "AC-12.5: No smoke-test step found in 'deploy' job."
    )
    isolation_step = _find_isolation_step(steps)
    assert isolation_step is not None, (
        "AC-12.5: No isolation step found in 'deploy' job."
    )

    assert "if" not in smoke_step, (
        "AC-12.5: The smoke-test step must NOT have an 'if:' conditional.\n"
        "An 'if:' condition could allow the VPS gate to be silently skipped "
        "under certain trigger conditions — invalidating the P0 exit confirmation.\n"
        f"Smoke-test step name: {smoke_step.get('name')!r}"
    )

    assert "if" not in isolation_step, (
        "AC-12.5: The isolation step must NOT have an 'if:' conditional.\n"
        "An 'if:' condition could allow the solanaBilly isolation check to be "
        "silently skipped — letting a disrupted solanaBilly pass undetected.\n"
        f"Isolation step name: {isolation_step.get('name')!r}"
    )


# ---------------------------------------------------------------------------
# Tests — deploy.yml front matter references AC-12.5
# ---------------------------------------------------------------------------


def test_deploy_yml_front_matter_includes_ac125() -> None:
    """The deploy.yml front matter must list AC-12.5 in its 'story:' field.

    CLAUDE.md requires all code files to carry structured metadata front matter.
    The deploy.yml file's story field documents which ACs the workflow implements
    and satisfies. AC-12.5 is the VPS verification gate AC — it must appear in
    the story field so future maintainers can trace the pipeline's VPS gate
    requirements back to the originating AC without reading the full sprint JSON.
    """
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    # The front matter is at the top of the file as a block comment
    front_matter_lines = []
    in_front_matter = False
    for line in raw.splitlines():
        stripped = line.lstrip("# ").strip()
        if stripped == "---":
            if not in_front_matter:
                in_front_matter = True
                continue
            else:
                break
        if in_front_matter:
            front_matter_lines.append(stripped)

    story_line = next((line for line in front_matter_lines if line.startswith("story:")), None)
    assert story_line is not None, (
        "AC-12.5: deploy.yml must have a 'story:' field in its front matter.\n"
        "CLAUDE.md requires metadata front matter on all code files. The story "
        "field documents which acceptance criteria the file implements."
    )
    assert "12.5" in story_line, (
        "AC-12.5: The deploy.yml 'story:' front-matter field must include 'AC-12.5' "
        "so the pipeline's VPS verification gate is traceable to its origin AC.\n"
        f"Current story field: {story_line!r}"
    )


# ---------------------------------------------------------------------------
# Tests — sprint JSON normalization constraint (fixture-based)
# ---------------------------------------------------------------------------


def test_normalization_constraint_accepts_clean_sprint_json_fixture() -> None:
    """The normalization constraint produces no violations on a clean sprint fixture.

    A sprint JSON where every done/checked AC has tester_status 'pass', 'approved',
    or 'not-started' (not fail/failed/blocked) should produce zero violations.
    This is the expected state AFTER the orchestrator normalizes sprint3.json
    to reflect the confirmed green deploy run (run 27543707795).

    Retrospective C4 violation: US-8 in sprint3.json had checked=True and
    tester_status='fail' for AC-8.3/8.4/8.5 — this fixture models the
    normalized (correct) state for those ACs.
    """
    clean_fixture = {
        "stories": [
            {
                "id": "US-8",
                "acceptance_criteria": [
                    {"id": "8.3", "checked": True, "dev_status": "done", "tester_status": "pass"},
                    {"id": "8.4", "checked": True, "dev_status": "done", "tester_status": "pass"},
                    {"id": "8.5", "checked": True, "dev_status": "done", "tester_status": "pass"},
                ],
            }
        ]
    }
    violations = _sprint_json_violations(clean_fixture)
    assert violations == [], (
        "AC-12.5 normalization: Expected no violations on a clean sprint JSON fixture "
        "(all done ACs have tester_status='pass'), but got:\n"
        + "\n".join(violations)
    )


def test_normalization_constraint_rejects_checked_done_with_fail_tester_status() -> None:
    """The normalization constraint detects the 'fail' spelling of tester_status.

    Sprint-3 US-8 used the spelling 'fail' for tester_status on ACs 8.3/8.4/8.5.
    This is the exact C4 violation the normalization constraint must catch:
    checked=True (AC was marked done by dev) + tester_status='fail' (Tester
    explicitly failed it) is a contradictory state that must be detected.

    Both 'fail' and 'failed' appear in existing sprint JSON files — the constraint
    must handle both spellings.
    """
    violating_fixture = {
        "stories": [
            {
                "id": "US-8",
                "acceptance_criteria": [
                    {"id": "8.3", "checked": True, "dev_status": "done", "tester_status": "fail"},
                ],
            }
        ]
    }
    violations = _sprint_json_violations(violating_fixture)
    assert len(violations) == 1, (
        "AC-12.5 normalization: Expected exactly 1 violation for US-8.3 "
        "(checked=True + tester_status='fail') but got:\n"
        + "\n".join(violations)
    )
    assert "8.3" in violations[0], (
        "AC-12.5 normalization: The violation message must identify AC 8.3.\n"
        f"Got: {violations[0]!r}"
    )
    assert "fail" in violations[0], (
        "AC-12.5 normalization: The violation message must include the tester_status value.\n"
        f"Got: {violations[0]!r}"
    )


def test_normalization_constraint_rejects_checked_done_with_failed_tester_status() -> None:
    """The normalization constraint detects the 'failed' spelling of tester_status.

    Some sprint JSON files use 'failed' rather than 'fail' for tester_status.
    The constraint must handle both spellings since the controlled vocabulary
    is not consistently enforced across all sprint files (retrospective C4 / US-13
    tester_notes). This test verifies the 'failed' variant is caught.
    """
    violating_fixture = {
        "stories": [
            {
                "id": "US-12",
                "acceptance_criteria": [
                    {"id": "12.1", "checked": True, "dev_status": "done", "tester_status": "failed"},
                ],
            }
        ]
    }
    violations = _sprint_json_violations(violating_fixture)
    assert len(violations) == 1, (
        "AC-12.5 normalization: Expected exactly 1 violation for US-12.1 "
        "(checked=True + tester_status='failed') but got:\n"
        + "\n".join(violations)
    )
    assert "failed" in violations[0], (
        "AC-12.5 normalization: The violation message must include the 'failed' spelling.\n"
        f"Got: {violations[0]!r}"
    )


def test_normalization_constraint_treats_blocked_as_invalid_for_done_ac() -> None:
    """The normalization constraint treats 'blocked' tester_status as invalid for done ACs.

    A done/checked AC with tester_status='blocked' is a contradictory state:
    the AC is marked done by the developer but the Tester has not yet been
    able to verify it (and is blocked). This matches the US-1 story state
    in sprint1.json (dev_status='in-progress', tester_status='blocked') which
    AC-12.5 retroactively closes once P0 exits.

    The constraint must reject 'blocked' alongside 'fail'/'failed' for done ACs.
    """
    violating_fixture = {
        "stories": [
            {
                "id": "US-1",
                "acceptance_criteria": [
                    {"id": "1.1", "checked": True, "dev_status": "done", "tester_status": "blocked"},
                ],
            }
        ]
    }
    violations = _sprint_json_violations(violating_fixture)
    assert len(violations) == 1, (
        "AC-12.5 normalization: Expected exactly 1 violation for US-1.1 "
        "(checked=True + tester_status='blocked') but got:\n"
        + "\n".join(violations)
    )
    assert "blocked" in violations[0], (
        "AC-12.5 normalization: The violation message must include 'blocked'.\n"
        f"Got: {violations[0]!r}"
    )


def test_normalization_constraint_ignores_not_started_tester_status() -> None:
    """The normalization constraint does NOT flag done ACs with tester_status 'not-started'.

    'not-started' means the Tester has not yet reviewed the AC — this is
    distinct from 'fail'/'failed' (actively rejected) and 'blocked' (impeded).
    A done AC awaiting Tester review is a valid intermediate state (e.g. the
    Tester is about to review it). The constraint must not produce false positives
    for in-flight ACs where the Tester simply hasn't started yet.
    """
    not_started_fixture = {
        "stories": [
            {
                "id": "US-12",
                "acceptance_criteria": [
                    {"id": "12.5", "checked": False, "dev_status": "done", "tester_status": "not-started"},
                ],
            }
        ]
    }
    violations = _sprint_json_violations(not_started_fixture)
    assert violations == [], (
        "AC-12.5 normalization: 'not-started' tester_status must NOT produce a violation "
        "for a done AC — it is a valid intermediate state, not a failure.\n"
        "Got violations:\n" + "\n".join(violations)
    )
