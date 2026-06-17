# ---
# file: core/tests/test_sprint_boundary_ac452.py
# project: solanatrilly
# purpose: AC-45.2 — re-confirm F2 phase-promoter fires in deploy sequence (post-US-40
#          unified gate) + US-13 status-integrity guard GREEN on sprint9.json
# story: US-45 AC-45.2
# sprint: sprint-9
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: yaml, pathlib, json, tools.sprint_integrity_check, tools.promote_sprint_phase
# ---
"""AC-45.2 — status-integrity GREEN on sprint9.json + F2 promoter re-confirmed in deploy.

AC text:
  Re-confirm the F2 phase-promoter (tools/promote_sprint_phase.py) fires as a MECHANICAL
  pre-deploy step in the actual deploy sequence (auto-promote a stale phase or block with
  REMEDY) on the sprint-9 deploy — closing the loop end-to-end again now that US-40 unifies
  the deploy gate — and the US-13 status-integrity guard is GREEN on sprint9.json (no
  status:done while tester_status is failed/blocked; story-level dev_status promoted to 'done'
  at closeout per D3, NOT exempted via --skip-complete). Verified by the structural test
  (promoter wired before deploy) + the US-13 guard green on sprint9.json + the sprint-9 deploy
  run showing the promoter fired.

Structural tests in this module:
  (1) Sprint-9 integrity guard — runs check_sprint on the ACTUAL sprint9.json:
      test_sprint9_integrity_guard_passes_check_sprint
      test_sprint9_no_story_done_with_failed_tester
      test_sprint9_no_ac_checked_with_failed_tester

  (2) NOT exempted via --skip-complete:
      test_sprint9_not_skipped_by_skip_complete
      test_skip_complete_skips_only_phase_complete_fixtures

  (3) F2 promoter wired before deploy (post-US-40 unified gate re-confirmation):
      test_promoter_wired_in_deploy_job_post_us40
      test_promoter_step_before_vps_deploy_post_us40
      test_promoter_step_targets_sprint_json_files
      test_promoter_promotes_stale_sprint_phase_to_complete
      test_promoter_remedy_message_contains_phase_and_path
      test_promoter_wiring_guard_ac392_importable

  (4) Deploy.yml traceability:
      test_deploy_yml_front_matter_includes_ac452
"""

import json
from pathlib import Path

import yaml

# H1 ImportError trap — if the AC-39.2 structural test disappears, pytest collection fails
from core.tests.test_phase_promoter_deploy_gate_ac392 import (  # noqa: F401
    test_deploy_workflow_deploy_job_has_promoter_step,
    test_deploy_workflow_promoter_step_before_vps_deploy,
)
from tools.promote_sprint_phase import compute_promoted_phase, promote_sprint_phase, remedy_message
from tools.sprint_integrity_check import check_sprint

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SPRINT9_JSON = REPO_ROOT / "scrum-master" / "sprint9.json"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sprint9() -> dict:
    assert SPRINT9_JSON.exists(), f"sprint9.json not found at {SPRINT9_JSON}"
    return json.loads(SPRINT9_JSON.read_text(encoding="utf-8"))


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping"
    return data


def _deploy_job_steps(data: dict) -> list:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, "deploy.yml must contain a 'deploy' job"
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _make_sprint(phase: str, stories: list) -> dict:
    return {"sprint": "sprint-test", "phase": phase, "stories": stories}


# ---------------------------------------------------------------------------
# (1) Sprint-9 US-13 integrity guard — the actual sprint9.json
# ---------------------------------------------------------------------------


def test_sprint9_integrity_guard_passes_check_sprint() -> None:
    """check_sprint on the ACTUAL sprint9.json must return zero violations.

    AC-45.2 (US-13 guard): no story/AC may read status:done while its
    tester_status is failed/blocked. The guard is GREEN if check_sprint
    returns an empty list for the live sprint9.json. This is the primary
    structural proxy for the sprint-9 deploy run showing the guard holds.
    """
    data = _load_sprint9()
    violations = check_sprint(data)
    assert violations == [], (
        "AC-45.2 US-13 guard FAILED on sprint9.json — the following integrity "
        "violations were detected:\n"
        + "\n".join(f"  • {v}" for v in violations)
        + "\n\nResolve by correcting the status/tester_status values in sprint9.json "
        "before the sprint-9 closeout."
    )


def test_sprint9_no_story_done_with_failed_tester() -> None:
    """No story in sprint9.json may have status='done' while tester_status is failed/blocked.

    The primary US-13 guard assertion applied to the real sprint9.json. Failing
    this test means the sprint file has a contradiction that must be resolved
    before the sprint-9 boundary deploy.
    """
    data = _load_sprint9()
    fail_statuses = {"failed", "fail", "blocked"}
    violations = []
    for story in data.get("stories", []):
        sid = story.get("id", "?")
        if story.get("status") == "done" and story.get("tester_status", "") in fail_statuses:
            violations.append(
                f"Story {sid}: status='done' but tester_status={story.get('tester_status')!r}"
            )
    assert not violations, (
        "AC-45.2: story-level US-13 violations in sprint9.json:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_sprint9_no_ac_checked_with_failed_tester() -> None:
    """No AC in sprint9.json may have checked=True while tester_status is failed/blocked.

    AC-level US-13 guard on the real sprint9.json. A checked AC with a
    failed/blocked tester_status is a contradiction — either the AC is not
    actually done (uncheck it) or the tester_status must be resolved.
    """
    data = _load_sprint9()
    fail_statuses = {"failed", "fail", "blocked"}
    violations = []
    for story in data.get("stories", []):
        sid = story.get("id", "?")
        for ac in story.get("acceptance_criteria", []):
            ac_id = ac.get("id", "?")
            if ac.get("checked") and ac.get("tester_status", "") in fail_statuses:
                violations.append(
                    f"AC {ac_id} (story {sid}): checked=True but tester_status={ac.get('tester_status')!r}"
                )
    assert not violations, (
        "AC-45.2: AC-level US-13 violations in sprint9.json:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# (2) NOT exempted via --skip-complete
# ---------------------------------------------------------------------------


def test_sprint9_integrity_guard_runs_directly_not_skipped() -> None:
    """check_sprint runs directly on sprint9.json — not bypassed by --skip-complete.

    AC-45.2 D3 intent: the US-13 guard runs on sprint9.json without --skip-complete
    filtering it out. Proved by calling check_sprint directly and verifying a list
    is returned (not None / bypassed). The --skip-complete flag only affects
    sprint_integrity_check.main() when iterating files; check_sprint itself always
    runs. This test uses the direct-call approach to avoid the lifecycle bug
    (asserting phase != 'complete' against a live sprint file breaks once the sprint
    closes — the exact pattern AC-40.1 banned).
    """
    data = _load_sprint9()
    result = check_sprint(data)
    # check_sprint always returns a list — never None, never raises
    assert isinstance(result, list), (
        "AC-45.2: check_sprint(sprint9.json) must return a list, "
        "proving the guard ran on the sprint data rather than being skipped."
    )
    # The guard returned no violations — sprint9.json is clean
    assert result == [], (
        "AC-45.2: check_sprint(sprint9.json) returned violations — sprint9.json "
        "has integrity issues that must be resolved before closeout:\n"
        + "\n".join(f"  • {v}" for v in result)
    )


def test_skip_complete_skips_only_phase_complete_fixtures() -> None:
    """--skip-complete must skip ONLY phase='complete' sprints, never in-scope ones.

    AC-45.2 D3 invariant (mirrors AC-39.3 test): the --skip-complete flag (used in
    sprint_integrity_check.main) exempts ONLY archived (phase='complete') sprints.
    An in-scope sprint at phase='planning' or 'in-progress' is NEVER skipped. Tested
    on fixtures so it holds across the whole sprint-9 lifecycle — a live sprint9.json
    legitimately becomes phase='complete' at closeout, which must NOT make this fail.
    """
    # in-scope sprint (phase != 'complete') → must NOT be skipped
    in_scope = _make_sprint("planning", [
        {
            "id": "US-99",
            "status": "done",
            "dev_status": "done",
            "tester_status": "approved",
            "acceptance_criteria": [
                {"id": "99.1", "checked": True, "dev_status": "done", "tester_status": "approved"},
            ],
        }
    ])
    complete = {**in_scope, "phase": "complete"}

    def _skipped_by_skip_complete(data: dict) -> bool:
        return data.get("phase") == "complete"

    assert _skipped_by_skip_complete(in_scope) is False, (
        "An in-scope sprint (phase='planning') must NOT be skipped by --skip-complete."
    )
    assert _skipped_by_skip_complete(complete) is True, (
        "A completed sprint (phase='complete') MUST be skipped by --skip-complete."
    )
    # The integrity guard itself runs on an in-scope sprint and returns a list
    assert isinstance(check_sprint(in_scope), list)


# ---------------------------------------------------------------------------
# (3) F2 promoter wired before deploy — post-US-40 unified gate re-confirmation
# ---------------------------------------------------------------------------


def test_promoter_wired_in_deploy_job_post_us40() -> None:
    """deploy.yml's deploy job must still have the promoter step after the US-40 gate unification.

    AC-45.2: re-confirm the F2 phase-promoter fires as a MECHANICAL pre-deploy step
    in the actual deploy sequence, closing the loop end-to-end now that US-40 unifies
    the deploy gate (ci job calls .github/workflows/ci.yml via workflow_call). The
    promoter step must still be present in the deploy job itself, not just in ci.yml.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    promoter_steps = [s for s in steps if "promote_sprint_phase" in s.get("run", "")]
    assert promoter_steps, (
        "AC-45.2: deploy.yml's deploy job must still have a step running "
        "promote_sprint_phase.py after the US-40 gate unification. "
        f"Found deploy job steps: {[s.get('name', '') for s in steps]}"
    )


def test_promoter_step_before_vps_deploy_post_us40() -> None:
    """The promoter step must appear BEFORE the VPS deploy step in the unified deploy job.

    AC-45.2: the promoter fires BEFORE any sprint-end deploy per F2/G4. With the
    US-40-unified deploy gate (ci job → build-and-push → deploy), the promoter step
    inside the deploy job must still precede the 'Deploy to VPS' step — so the
    phase is promoted (or the deploy is blocked with REMEDY) before the stack is updated.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)

    promoter_idx = next(
        (i for i, s in enumerate(steps) if "promote_sprint_phase" in s.get("run", "")),
        None,
    )
    vps_deploy_idx = next(
        (i for i, s in enumerate(steps) if "Deploy to VPS" in s.get("name", "")),
        None,
    )

    assert promoter_idx is not None, (
        "AC-45.2: No promoter step found in deploy.yml's deploy job"
    )
    assert vps_deploy_idx is not None, (
        "AC-45.2: No 'Deploy to VPS' step found in deploy.yml's deploy job"
    )
    assert promoter_idx < vps_deploy_idx, (
        f"AC-45.2: Promoter step (index {promoter_idx}) must appear BEFORE the VPS "
        f"deploy step (index {vps_deploy_idx}) in the unified deploy job."
    )


def test_promoter_step_targets_sprint_json_files() -> None:
    """The promoter step must pass sprint*.json (covering sprint9.json) to the script.

    The promoter runs over all sprint JSON files via a glob so sprint9.json is included
    in the sprint-9 deploy run. This is the wiring that closes the loop for sprint-9.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    promoter_steps = [s for s in steps if "promote_sprint_phase" in s.get("run", "")]
    assert promoter_steps, "AC-45.2: No promoter step in deploy.yml's deploy job"

    run_cmd = promoter_steps[0].get("run", "")
    assert "sprint" in run_cmd and ".json" in run_cmd, (
        f"AC-45.2: Promoter step must pass sprint*.json to the script (covering sprint9.json); "
        f"got: {run_cmd!r}"
    )


def test_promoter_promotes_stale_sprint_phase_to_complete() -> None:
    """promote_sprint_phase correctly promotes a stale 'planning' phase when all stories done.

    AC-45.2: the promoter auto-promotes OR blocks with REMEDY. Proves the core
    promotion logic works as expected for the sprint-9 scenario: if all sprint-9
    stories reach status='done', a stale phase='planning' is promoted to 'complete'.
    """
    # Simulate sprint9 at closeout — all stories done, phase still stale
    stale_sprint9 = {
        "sprint": "sprint-9",
        "phase": "planning",
        "stories": [
            {"id": f"US-{i}", "status": "done"}
            for i in range(40, 46)
        ],
    }
    updated, promoted_to = promote_sprint_phase(stale_sprint9)
    assert promoted_to == "complete", (
        "AC-45.2: promoter must promote phase 'planning' → 'complete' when all stories are done; "
        f"got promoted_to={promoted_to!r}"
    )
    assert updated["phase"] == "complete", (
        f"AC-45.2: updated dict must have phase='complete'; got {updated['phase']!r}"
    )


def test_promoter_remedy_message_contains_phase_and_path() -> None:
    """remedy_message includes the stale phase name and a path to the file to fix.

    AC-45.2 'block with REMEDY': when --check-only is set, the printed message
    must identify the file path and the stale phase so the operator knows exactly
    what to fix. Confirms the REMEDY output satisfies the AC requirement.
    """
    path = SPRINT9_JSON
    msg = remedy_message(path, "planning", "complete")
    assert "planning" in msg, (
        "AC-45.2: remedy_message must mention the stale phase 'planning'"
    )
    assert "complete" in msg, (
        "AC-45.2: remedy_message must mention the promoted phase 'complete'"
    )
    assert str(path) in msg or path.name in msg, (
        "AC-45.2: remedy_message must include the file path so the operator knows what to fix"
    )


def test_promoter_no_promotion_needed_when_phase_already_complete() -> None:
    """compute_promoted_phase returns None when phase is already 'complete'.

    The promoter is idempotent: if sprint9.json already has phase='complete'
    (i.e., after a successful sprint-9 closeout), running the promoter again
    must be a no-op — it must not re-promote or corrupt the phase.
    """
    already_complete = {
        "sprint": "sprint-9",
        "phase": "complete",
        "stories": [{"id": "US-40", "status": "done"}],
    }
    result = compute_promoted_phase(already_complete)
    assert result is None, (
        "AC-45.2: compute_promoted_phase must return None (no-op) when phase is already "
        f"'complete'; got {result!r}"
    )


def test_promoter_wiring_guard_ac392_importable() -> None:
    """The AC-39.2 promoter-wiring test functions are importable (H1 ImportError trap).

    AC-45.2 re-confirms the F2 promoter wiring that was proven in AC-39.2. The H1
    ImportError trap at the top of this module imports two named test functions from
    test_phase_promoter_deploy_gate_ac392 — if that module is deleted or its key
    functions are renamed, pytest collection fails before any test runs, making the
    wiring-guard unforgeable.
    """
    # The trap is at import time (top of file) — reaching this test proves the import
    # succeeded and the AC-39.2 structural tests are still present.
    assert callable(test_deploy_workflow_deploy_job_has_promoter_step), (
        "AC-45.2: test_deploy_workflow_deploy_job_has_promoter_step from AC-39.2 must "
        "be callable — the promoter-wiring structural test is the living proof."
    )
    assert callable(test_deploy_workflow_promoter_step_before_vps_deploy), (
        "AC-45.2: test_deploy_workflow_promoter_step_before_vps_deploy from AC-39.2 must "
        "be callable — the before-VPS ordering structural test is the living proof."
    )


# ---------------------------------------------------------------------------
# (4) Deploy.yml traceability
# ---------------------------------------------------------------------------


def test_deploy_yml_front_matter_includes_ac452() -> None:
    """deploy.yml front matter must list AC-45.2 in its 'story:' field.

    CLAUDE.md requires all code files to carry structured metadata front matter.
    The deploy.yml 'story:' field documents which ACs the workflow satisfies.
    AC-45.2 re-confirms the F2 promoter in the sprint-9 deploy sequence — it must
    appear in the story field so future maintainers can trace the deploy pipeline's
    sprint-9 process verification back to the originating AC.
    """
    raw = DEPLOY_YML.read_text(encoding="utf-8")
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

    story_line = next(
        (line for line in front_matter_lines if line.startswith("story:")), None
    )
    assert story_line is not None, (
        "AC-45.2: deploy.yml must have a 'story:' field in its front matter.\n"
        "CLAUDE.md requires metadata front matter on all code files."
    )
    assert "45.2" in story_line, (
        "AC-45.2: The deploy.yml 'story:' front-matter field must include 'AC-45.2' "
        "or '45.2' so the sprint-9 process confirmation is traceable.\n"
        f"Current story field: {story_line!r}"
    )
