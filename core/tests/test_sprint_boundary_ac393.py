# ---
# file: core/tests/test_sprint_boundary_ac393.py
# project: solanatrilly
# purpose: AC-39.3 — sprint-8 boundary: status integrity GREEN + deploy checklist structural proof
# story: US-39 AC-39.3
# sprint: sprint-8
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: yaml, pathlib, json, tools.sprint_integrity_check
# ---
"""AC-39.3 — Status integrity GREEN on sprint8.json + deploy checklist at the sprint-8 boundary.

AC text:
  Status integrity GREEN on sprint8.json (US-13 guard — no status:done while tester_status is
  failed/blocked; story-level dev_status promoted to 'done' at closeout per D3, not exempted),
  and the clean final HEAD deploy at the sprint-8 boundary runs GREEN on 'main': the Tester
  confirms from an ACTUAL green deploy run HTTP 200 on 8002 (retaining the AC-12.3
  retry-with-backoff), the 'listener' container (now driving the helius_live birth-tape source)
  Up, and solanaBilly untouched on 8001. New files carry metadata front matter.

Structural tests in this module:
  (1) Sprint-8 integrity guard — runs check_sprint on the ACTUAL sprint8.json:
      test_sprint8_integrity_guard_passes_check_sprint
      test_sprint8_phase_is_not_complete_so_not_skipped_by_skip_complete
      test_sprint8_no_story_done_with_failed_tester
      test_sprint8_no_ac_checked_with_failed_tester

  (2) D3 story-level dev_status promotion — stale detection works:
      test_check_sprint_catches_stale_story_dev_status_not_started
      test_check_sprint_catches_stale_story_dev_status_in_progress
      test_check_sprint_ignores_stale_dev_status_when_story_already_done

  (3) Deploy checklist — listener container step in deploy.yml:
      test_deploy_yml_listener_step_exists
      test_deploy_yml_listener_step_checks_running_or_up_state
      test_deploy_yml_listener_step_uses_docker_compose_ps
      test_deploy_yml_listener_step_references_ac333

  (4) Deploy checklist — smoke-test retains AC-12.3 retry-with-backoff:
      test_deploy_yml_smoke_test_port_8002_targeted
      test_deploy_yml_smoke_test_has_retry_variables

  (5) Deploy checklist — solanaBilly isolation on port 8001:
      test_deploy_yml_isolation_step_checks_port_8001
      test_deploy_yml_isolation_step_references_solanabilly

  (6) Traceability — deploy.yml front matter includes AC-39.3:
      test_deploy_yml_story_front_matter_includes_ac393
"""

import json
import re
from pathlib import Path

import yaml

from tools.sprint_integrity_check import check_sprint

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SPRINT8_JSON = REPO_ROOT / "scrum-master" / "sprint8.json"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sprint8() -> dict:
    assert SPRINT8_JSON.exists(), f"sprint8.json not found at {SPRINT8_JSON}"
    return json.loads(SPRINT8_JSON.read_text(encoding="utf-8"))


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


def _find_step_by_keyword(steps: list, *keywords: str) -> dict | None:
    for step in steps:
        name = str(step.get("name", "")).lower()
        run = str(step.get("run", "")).lower()
        if all(kw.lower() in (name + run) for kw in keywords):
            return step
    return None


# ---------------------------------------------------------------------------
# (1) Sprint-8 integrity guard — the actual sprint8.json
# ---------------------------------------------------------------------------


def test_sprint8_integrity_guard_passes_check_sprint() -> None:
    """check_sprint on the ACTUAL sprint8.json must return zero violations.

    AC-39.3 (US-13 guard): no story/AC may read status:done while its
    tester_status is failed/blocked. The guard is GREEN if check_sprint([])
    returns an empty list for the live sprint8.json.
    """
    data = _load_sprint8()
    violations = check_sprint(data)
    assert violations == [], (
        "AC-39.3 US-13 guard FAILED on sprint8.json — the following integrity "
        "violations were detected:\n"
        + "\n".join(f"  • {v}" for v in violations)
        + "\n\nResolve by correcting the status/tester_status values in sprint8.json "
        "before the sprint-8 closeout."
    )


def test_sprint8_phase_is_not_complete_so_not_skipped_by_skip_complete() -> None:
    """sprint8.json phase must NOT be 'complete' at AC-39.3 implementation time.

    AC-39.3 D3 clause: story-level dev_status is promoted to 'done' at closeout,
    NOT exempted via --skip-complete. The --skip-complete flag only skips sprints
    with phase='complete'. If sprint8.json is 'complete' when AC-39.3 is
    implemented, the guard would not run against it — defeating the whole point.
    This test ensures the sprint is still in scope for the integrity check.
    """
    data = _load_sprint8()
    phase = data.get("phase", "")
    assert phase != "complete", (
        f"AC-39.3: sprint8.json phase is 'complete', which means --skip-complete "
        f"would exempt it from the US-13 guard. Sprint8 must remain in scope "
        f"(phase != 'complete') during AC-39.3 implementation.\nCurrent phase: {phase!r}"
    )


def test_sprint8_no_story_done_with_failed_tester() -> None:
    """No story in sprint8.json may have status='done' while tester_status is failed/blocked.

    This is the primary US-13 guard assertion applied to the real sprint8.json.
    Failing this test means the sprint file has a contradiction that must be resolved
    before the sprint-8 boundary deploy.
    """
    data = _load_sprint8()
    fail_statuses = {"failed", "fail", "blocked"}
    violations = []
    for story in data.get("stories", []):
        sid = story.get("id", "?")
        if story.get("status") == "done" and story.get("tester_status", "") in fail_statuses:
            violations.append(
                f"Story {sid}: status='done' but tester_status={story.get('tester_status')!r}"
            )
    assert not violations, (
        "AC-39.3: story-level US-13 violations in sprint8.json:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_sprint8_no_ac_checked_with_failed_tester() -> None:
    """No AC in sprint8.json may have checked=True while tester_status is failed/blocked.

    AC-level US-13 guard on the real sprint8.json. A checked AC with a
    failed/blocked tester_status is a contradiction — either the AC is not
    actually done (uncheck it) or the tester_status must be resolved.
    """
    data = _load_sprint8()
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
        "AC-39.3: AC-level US-13 violations in sprint8.json:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# (2) D3 story-level dev_status promotion — stale detection works
# ---------------------------------------------------------------------------


def _make_sprint(stories: list) -> dict:
    return {"sprint": "sprint-test", "phase": "in-progress", "stories": stories}


def test_check_sprint_catches_stale_story_dev_status_not_started() -> None:
    """check_sprint must flag a story whose dev_status='not-started' while all ACs are done.

    AC-39.3 D3 clause: at sprint closeout, if all ACs have dev_status='done' but
    the story-level dev_status is still 'not-started', check_sprint must report it
    as a violation so the promoter or orchestrator is forced to update it.
    Proves the D3 stale-detection mechanism works for the 'not-started' spelling.
    """
    story = {
        "id": "US-99",
        "status": "in-progress",
        "dev_status": "not-started",
        "tester_status": "approved",
        "acceptance_criteria": [
            {"id": "99.1", "checked": True, "dev_status": "done", "tester_status": "approved"},
            {"id": "99.2", "checked": True, "dev_status": "done", "tester_status": "approved"},
        ],
    }
    data = _make_sprint([story])
    violations = check_sprint(data)
    stale_violations = [v for v in violations if "US-99" in v and "stale" in v.lower()]
    assert stale_violations, (
        "AC-39.3 D3: check_sprint must detect a stale story dev_status='not-started' "
        "when all ACs have dev_status='done'. No stale violation found.\n"
        f"All violations: {violations}"
    )


def test_check_sprint_catches_stale_story_dev_status_in_progress() -> None:
    """check_sprint must flag a story whose dev_status='in-progress' while all ACs are done.

    Same as the 'not-started' test but for the 'in-progress' spelling. Both values
    appear in sprint files; the check must catch both so the D3 closeout rule is
    enforced regardless of which stale value the story holds.
    """
    story = {
        "id": "US-98",
        "status": "in-progress",
        "dev_status": "in-progress",
        "tester_status": "approved",
        "acceptance_criteria": [
            {"id": "98.1", "checked": True, "dev_status": "done", "tester_status": "approved"},
        ],
    }
    data = _make_sprint([story])
    violations = check_sprint(data)
    stale_violations = [v for v in violations if "US-98" in v and "stale" in v.lower()]
    assert stale_violations, (
        "AC-39.3 D3: check_sprint must detect a stale story dev_status='in-progress' "
        "when all ACs have dev_status='done'. No stale violation found.\n"
        f"All violations: {violations}"
    )


def test_check_sprint_ignores_stale_dev_status_when_story_already_done() -> None:
    """check_sprint must NOT flag stale dev_status on a story already marked status='done'.

    A story that is status='done' (the Tester accepted it) is past the dev tracking
    phase. A stale story-level dev_status on an accepted story is a harmless historical
    artifact — raising a violation for it would produce false positives on closed stories.
    The stale-detection check only applies to stories still in-progress.
    """
    story = {
        "id": "US-97",
        "status": "done",
        "dev_status": "not-started",
        "tester_status": "approved",
        "acceptance_criteria": [
            {"id": "97.1", "checked": True, "dev_status": "done", "tester_status": "approved"},
        ],
    }
    data = _make_sprint([story])
    violations = check_sprint(data)
    stale_violations = [v for v in violations if "US-97" in v and "stale" in v.lower()]
    assert not stale_violations, (
        "AC-39.3 D3: check_sprint must NOT flag stale dev_status on a story with "
        "status='done' — it is a historical artifact, not an active violation.\n"
        f"Unexpected violations: {stale_violations}"
    )


# ---------------------------------------------------------------------------
# (3) Deploy checklist — listener container step in deploy.yml
# ---------------------------------------------------------------------------


def test_deploy_yml_listener_step_exists() -> None:
    """deploy.yml's deploy job must have a step that verifies the listener container.

    AC-39.3: the Tester confirms from an ACTUAL green deploy run that the 'listener'
    container (now driving the helius_live birth-tape source) is Up. The structural
    proxy is that deploy.yml has a step which checks the listener service state.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_steps = [
        s for s in steps
        if "listener" in str(s.get("name", "")).lower()
        or "listener" in str(s.get("run", "")).lower()
    ]
    assert listener_steps, (
        "AC-39.3: deploy.yml's deploy job must have a step that verifies the "
        "'listener' container is Up. No listener step found.\n"
        f"Current step names: {[s.get('name', '') for s in steps]}"
    )


def test_deploy_yml_listener_step_checks_running_or_up_state() -> None:
    """The listener verification step must check for 'running' or 'up' state.

    AC-39.3: the Tester needs confirmation that the listener container is *Up*,
    not merely defined. The step's run script must grep for 'running' or 'up'
    in the docker compose ps output — a step that only lists containers without
    checking their state cannot confirm they are actually running.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_step_by_keyword(steps, "listener")
    assert listener_step is not None, (
        "AC-39.3: No listener step found in deploy.yml's deploy job."
    )
    run_script = str(listener_step.get("run", ""))
    has_state_check = bool(
        re.search(r"running|up", run_script, re.IGNORECASE)
        and ("grep" in run_script or "match" in run_script or "if" in run_script)
    )
    assert has_state_check, (
        "AC-39.3: The listener step must grep/check for 'running' or 'up' state "
        "in the docker compose ps output to confirm the container is actually Up.\n"
        f"Listener step run script:\n{run_script}"
    )


def test_deploy_yml_listener_step_uses_docker_compose_ps() -> None:
    """The listener verification step must use 'docker compose ps' to inspect state.

    'docker compose ps' is the canonical way to confirm a container's runtime state
    in a scoped (-p solanatrilly) stack. A step that only checks container existence
    via 'docker images' or 'docker inspect' cannot verify the container is Up.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_step_by_keyword(steps, "listener")
    assert listener_step is not None, (
        "AC-39.3: No listener step found in deploy.yml's deploy job."
    )
    run_script = str(listener_step.get("run", ""))
    assert "compose" in run_script and "ps" in run_script, (
        "AC-39.3: The listener step must use 'docker compose ps' to inspect the "
        "container's runtime state.\n"
        f"Listener step run script:\n{run_script}"
    )


def test_deploy_yml_listener_step_references_ac333() -> None:
    """The listener step in deploy.yml must reference AC-33.3 for traceability.

    AC-33.3 established the listener container as a dedicated service separate from
    web/gunicorn (the #289 lesson). AC-39.3 reaffirms this at the sprint-8 boundary.
    Referencing AC-33.3 in the step name links the green deploy log to the specific
    AC that mandated the listener as a separate container.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    listener_step = _find_step_by_keyword(steps, "listener")
    assert listener_step is not None, (
        "AC-39.3: No listener step found in deploy.yml's deploy job."
    )
    name = str(listener_step.get("name", ""))
    assert "33.3" in name or "33" in name, (
        "AC-39.3: The listener step name must reference AC-33.3 for traceability "
        "to the story that mandated the dedicated listener container.\n"
        f"Current step name: {name!r}"
    )


# ---------------------------------------------------------------------------
# (4) Deploy checklist — smoke-test retains AC-12.3 retry-with-backoff
# ---------------------------------------------------------------------------


def test_deploy_yml_smoke_test_port_8002_targeted() -> None:
    """The smoke-test step must target port 8002 — the solanatrilly staging port.

    AC-39.3: the Tester confirms HTTP 200 on 8002. The smoke-test step must
    explicitly target port 8002 (not 8000 or 80) so the confirmation is
    unambiguous. Port 8002 is the solanatrilly staging port; 8001 belongs to
    solanaBilly (never touch it).
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_step_by_keyword(steps, "smoke")
    assert smoke_step is not None, (
        "AC-39.3: No smoke-test step found in deploy.yml's deploy job."
    )
    run_script = str(smoke_step.get("run", ""))
    assert "8002" in run_script, (
        "AC-39.3: The smoke-test step must target port 8002 (the solanatrilly staging "
        "port) to confirm HTTP 200 on the correct stack.\n"
        f"Smoke-test run script:\n{run_script}"
    )


def test_deploy_yml_smoke_test_has_retry_variables() -> None:
    """The smoke-test step must retain the AC-12.3 retry-with-backoff variables.

    AC-39.3 explicitly requires the AC-12.3 retry-with-backoff to be retained at
    the sprint-8 boundary. SMOKE_MAX_ATTEMPTS and SMOKE_RETRY_DELAY are the named
    variables that implement this (verified in depth by test_deploy_workflow_ac123.py).
    This test confirms they are still present — a quick regression guard.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    smoke_step = _find_step_by_keyword(steps, "smoke")
    assert smoke_step is not None, (
        "AC-39.3: No smoke-test step found in deploy.yml's deploy job."
    )
    run_script = str(smoke_step.get("run", ""))
    assert "SMOKE_MAX_ATTEMPTS" in run_script, (
        "AC-39.3: SMOKE_MAX_ATTEMPTS variable must be present in the smoke-test step "
        "to retain the AC-12.3 retry-with-backoff at the sprint-8 boundary.\n"
        f"Script:\n{run_script}"
    )
    assert "SMOKE_RETRY_DELAY" in run_script, (
        "AC-39.3: SMOKE_RETRY_DELAY variable must be present in the smoke-test step "
        "to retain the AC-12.3 retry-with-backoff at the sprint-8 boundary.\n"
        f"Script:\n{run_script}"
    )


# ---------------------------------------------------------------------------
# (5) Deploy checklist — solanaBilly isolation on port 8001
# ---------------------------------------------------------------------------


def test_deploy_yml_isolation_step_checks_port_8001() -> None:
    """The isolation step must explicitly check port 8001 (solanaBilly's port).

    AC-39.3: the Tester confirms solanaBilly is untouched on 8001. The isolation
    step must curl/check port 8001 — not just inspect docker containers — to
    confirm solanaBilly is still answering at its expected port after the
    solanatrilly deploy.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_step_by_keyword(steps, "isolation")
    assert isolation_step is not None, (
        "AC-39.3: No isolation step found in deploy.yml's deploy job "
        "(expected a step with 'isolation' in its name)."
    )
    run_script = str(isolation_step.get("run", ""))
    assert "8001" in run_script, (
        "AC-39.3: The isolation step must explicitly check port 8001 "
        "(solanaBilly's port) to confirm it is untouched after the deploy.\n"
        f"Isolation step run script:\n{run_script}"
    )


def test_deploy_yml_isolation_step_references_solanabilly() -> None:
    """The isolation step must mention solanaBilly in its name or run script.

    The isolation check exists specifically to verify solanaBilly is untouched.
    Mentioning solanaBilly makes the deploy log unambiguous: a Tester reading the
    log can immediately see which service is being protected and why.
    """
    data = _load_deploy()
    steps = _deploy_job_steps(data)
    isolation_step = _find_step_by_keyword(steps, "isolation")
    assert isolation_step is not None, (
        "AC-39.3: No isolation step found in deploy.yml's deploy job."
    )
    combined = (
        str(isolation_step.get("name", "")).lower()
        + str(isolation_step.get("run", "")).lower()
    )
    assert "solanabilly" in combined or "solana-billy" in combined.replace("_", "-"), (
        "AC-39.3: The isolation step must reference solanaBilly so the deploy log "
        "clearly identifies which service is being checked for isolation.\n"
        f"Step name: {isolation_step.get('name')!r}"
    )


# ---------------------------------------------------------------------------
# (6) Traceability — deploy.yml front matter includes AC-39.3
# ---------------------------------------------------------------------------


def test_deploy_yml_story_front_matter_includes_ac393() -> None:
    """deploy.yml front matter must list AC-39.3 in its 'story:' field.

    CLAUDE.md requires all code files to carry structured metadata front matter.
    The deploy.yml 'story:' field documents which ACs the workflow satisfies.
    AC-39.3 is the sprint-8 boundary deploy checklist AC — it must appear in the
    story field so future maintainers can trace the deploy pipeline's VPS checklist
    requirements back to the originating AC without reading the full sprint JSON.
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
        "AC-39.3: deploy.yml must have a 'story:' field in its front matter.\n"
        "CLAUDE.md requires metadata front matter on all code files."
    )
    assert "39.3" in story_line, (
        "AC-39.3: The deploy.yml 'story:' front-matter field must include 'AC-39.3' "
        "or '39.3' so the deploy pipeline's sprint-8 checklist is traceable.\n"
        f"Current story field: {story_line!r}"
    )
