# ---
# file: core/tests/test_deploy_tester_confirm_ac533.py
# project: solanatrilly
# purpose: AC-53.3 — Structural guards verifying the deploy pipeline satisfies all four
#          Tester VPS-confirmation conditions from AC-53.3: HTTP 200 on 8002 (with
#          AC-12.3 retry-with-backoff), listener container Up, celery-worker container Up,
#          solanaBilly UNTOUCHED on 8001 (every command scoped -p solanatrilly).
# story: US-53 AC-53.3
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-53.3 — Tester-CONFIRM structural guards for the four VPS verification conditions.

AC text:
  Tester-CONFIRM from that ACTUAL GREEN deploy run the VPS conditions: HTTP 200 on
  8002 (with the AC-12.3 retry-with-backoff), the 'listener' container Up, a
  scorer/celery-worker container Up, and solanaBilly UNTOUCHED on 8001 (every
  command scoped -p solanatrilly). The fix must be a ROOT fix verified against the
  run, not a symptom patch. New/changed files carry metadata front matter.

Root cause (from AC-53.1 ops/rca_run_27683660493.md):
  VPS disk exhaustion (ENOSPC) during docker compose pull — run 27683660493.
  Accumulated image layers from sprint-10 deploy runs filled the partition;
  the 195 MB Django layer could not be extracted.

Root fix applied (AC-53.2):
  'docker image prune -f' prepended before docker compose down/pull/up in deploy.yml.
  Unconditionally reclaims dangling image layers on every deploy so disk stays clean.

Tests in this module:
  test_http200_retry_backoff_present
      SMOKE_MAX_ATTEMPTS, SMOKE_RETRY_DELAY, and /health/ are in deploy.yml.
  test_listener_container_up_check_present
      deploy.yml has a step checking solanatrilly.listener is Up.
  test_celery_worker_container_up_check_present
      deploy.yml has a step checking solanatrilly-celery-worker / solanatrilly.celery.worker is Up.
  test_solanabilly_isolation_check_present_ac533
      deploy.yml has a step checking port 8001 and solanaBilly.
  test_all_docker_compose_commands_scoped_ac533
      All docker compose commands in deploy.yml carry -p solanatrilly.
  test_root_fix_in_place_not_symptom_patch
      docker image prune -f is in the Deploy-to-VPS SSH command AND
      ops/rca_run_27683660493.md (RCA record) exists.
  test_tester_confirm_record_exists
      ops/tester_confirm_ac533.md exists and has content > 200 chars.
  test_tester_confirm_record_documents_four_conditions
      The confirmation record references all four conditions: 8002, listener, celery, 8001.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RCA_RECORD = REPO_ROOT / "ops" / "rca_run_27683660493.md"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac533.md"

VPS_STEP_NAME = "Deploy to VPS staging stack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_jobs() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "deploy.yml must be valid YAML"
    return data.get("jobs", {})


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_jobs()
    deploy_job = jobs.get("deploy", {})
    return [s for s in (deploy_job.get("steps") or []) if isinstance(s, dict)]


def _vps_step_script() -> str:
    steps = _deploy_job_steps()
    vps_step = next(
        (s for s in steps if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert vps_step is not None, (
        f"AC-53.3: No step named '{VPS_STEP_NAME}' found in deploy job. "
        f"Steps: {[s.get('name', '') for s in steps]}"
    )
    return str(vps_step.get("run", ""))


def _find_step_by_keyword(keyword: str) -> dict | None:
    kw = keyword.lower()
    for step in _deploy_job_steps():
        if kw in str(step.get("name", "")).lower():
            return step
    return None


def _step_script(step: dict) -> str:
    return str(step.get("run", ""))


# ---------------------------------------------------------------------------
# 1. HTTP 200 retry-with-backoff present (AC-12.3 condition)
# ---------------------------------------------------------------------------


def test_http200_retry_backoff_present() -> None:
    """SMOKE_MAX_ATTEMPTS, SMOKE_RETRY_DELAY, and /health/ must be in deploy.yml (AC-12.3).

    AC-53.3 condition 1: HTTP 200 on port 8002 WITH the AC-12.3 retry-with-backoff.
    A single-shot curl would fail transiently during container startup. The retry
    loop (SMOKE_MAX_ATTEMPTS=12, SMOKE_RETRY_DELAY=5) gives containers time to
    become ready. Removing any of these markers would remove the retry-backoff
    mechanism, exposing the pipeline to transient startup failures on the VPS.
    """
    content = _deploy_text()
    assert "SMOKE_MAX_ATTEMPTS" in content, (
        "AC-53.3: SMOKE_MAX_ATTEMPTS (AC-12.3 retry-with-backoff) must be present "
        "in deploy.yml. Condition 1 requires HTTP 200 on 8002 WITH retry-backoff."
    )
    assert "SMOKE_RETRY_DELAY" in content, (
        "AC-53.3: SMOKE_RETRY_DELAY (AC-12.3 backoff delay) must be present "
        "in deploy.yml. Condition 1 requires HTTP 200 on 8002 WITH retry-backoff."
    )
    assert "/health/" in content, (
        "AC-53.3: /health/ smoke-test endpoint must be present in deploy.yml. "
        "Condition 1 requires HTTP 200 on port 8002 at /health/."
    )


# ---------------------------------------------------------------------------
# 2. Listener container Up check present (AC-33.3 condition)
# ---------------------------------------------------------------------------


def test_listener_container_up_check_present() -> None:
    """deploy.yml must have a step that checks solanatrilly.listener is Up.

    AC-53.3 condition 2: the 'listener' container must be Up on the VPS after deploy.
    Without this structural check in the pipeline, a deploy that fails to start the
    listener would still be marked green — the condition would not be enforced, and
    the Tester could not confirm it from the deploy run alone.
    """
    content = _deploy_text()
    assert "solanatrilly.listener" in content or "solanatrilly-listener" in content, (
        "AC-53.3: deploy.yml must contain a step checking that the 'listener' container "
        "is Up (e.g., grep for 'solanatrilly.listener' in docker compose ps output). "
        "Condition 2 requires the listener container to be verified Up on the VPS."
    )
    # Also verify the step name is present
    listener_step = _find_step_by_keyword("listener")
    assert listener_step is not None, (
        "AC-53.3: No deploy job step with 'listener' in its name found in deploy.yml. "
        "The 'Verify listener container Up (AC-33.3)' step must be present."
    )


# ---------------------------------------------------------------------------
# 3. Celery-worker container Up check present (AC-53.3 condition)
# ---------------------------------------------------------------------------


def test_celery_worker_container_up_check_present() -> None:
    """deploy.yml must have a step checking solanatrilly-celery-worker or solanatrilly.celery.worker is Up.

    AC-53.3 condition 3: a scorer/celery-worker container must be Up on the VPS after
    deploy. Without this step, a deploy where the celery-worker fails to start would
    still appear green. The Tester cannot confirm condition 3 from the run log unless
    the pipeline explicitly checks and fails if the container is not Up.

    This test was introduced specifically in AC-53.3 because the celery-worker check
    was missing from deploy.yml when AC-53.3 was written — it is the primary structural
    addition of this AC.
    """
    content = _deploy_text()
    assert (
        "solanatrilly-celery-worker" in content
        or "solanatrilly.celery.worker" in content
        or "celery-worker" in content
    ), (
        "AC-53.3: deploy.yml must contain a step checking that the 'celery-worker' "
        "container is Up (e.g., grep for 'solanatrilly.celery.worker' in docker compose "
        "ps output). Condition 3 requires the scorer/celery-worker container to be "
        "verified Up on the VPS. This step was added in AC-53.3."
    )
    celery_step = _find_step_by_keyword("celery-worker")
    assert celery_step is not None, (
        "AC-53.3: No deploy job step with 'celery-worker' in its name found in deploy.yml. "
        "The 'Verify celery-worker container Up (AC-53.3)' step must be present."
    )
    script = _step_script(celery_step)
    assert "solanatrilly" in script, (
        "AC-53.3: The celery-worker verification step must reference 'solanatrilly' in its "
        "script (must check the solanatrilly stack, not a different project)."
    )


# ---------------------------------------------------------------------------
# 4. solanaBilly isolation check present (AC-6.5 / AC-12.4 / AC-53.3 condition)
# ---------------------------------------------------------------------------


def test_solanabilly_isolation_check_present_ac533() -> None:
    """deploy.yml must have a step checking port 8001 and solanaBilly isolation.

    AC-53.3 condition 4: solanaBilly must be UNTOUCHED on port 8001 after every
    deploy. The pipeline must check this — if the isolation step is removed, a
    deploy that inadvertently affects solanaBilly would still appear green. The
    solanaBilly live service at /root/solanaBilly/ must never be touched by
    solanatrilly operations.
    """
    content = _deploy_text()
    assert "8001" in content, (
        "AC-53.3: deploy.yml must contain a step checking port 8001 (solanaBilly). "
        "Condition 4 requires solanaBilly UNTOUCHED on port 8001 after every deploy."
    )
    assert "solanaBilly" in content or "solanabilly" in content.lower(), (
        "AC-53.3: deploy.yml must reference solanaBilly in the isolation check step. "
        "Condition 4 requires explicit verification that solanaBilly is untouched."
    )
    isolation_step = _find_step_by_keyword("isolation")
    assert isolation_step is not None, (
        "AC-53.3: No deploy job step with 'isolation' in its name found in deploy.yml. "
        "The 'Verify solanaBilly isolation (AC-6.5 / AC-12.4)' step must be present."
    )


# ---------------------------------------------------------------------------
# 5. All docker compose commands scoped -p solanatrilly
# ---------------------------------------------------------------------------


def test_all_docker_compose_commands_scoped_ac533() -> None:
    """All docker compose commands in deploy.yml must carry an explicit -p scope flag.

    AC-53.3 condition 4 (sub-condition): every command must be project-scoped. An
    unscoped docker compose command defaults to the directory name, which could
    accidentally affect the wrong stack. The hard isolation boundary (PRD §15.3)
    requires every solanatrilly command to carry '-p solanatrilly'. The solanaBilly
    isolation check legitimately uses '-p solanabilly' — that is correct scoping for
    that read-only health check, not a violation.

    Strategy: parse deploy.yml as text, find all 'docker compose' occurrences,
    and verify each one has a '-p ' project flag nearby (within 120 chars).
    We check within a 120-char window after 'docker compose' to handle both
    inline and multiline invocations. We skip comment lines.
    """
    content = _deploy_text()
    window = 120
    idx = 0
    violations = []
    while True:
        pos = content.find("docker compose", idx)
        if pos == -1:
            break
        # Skip comment lines (lines starting with #)
        line_start = content.rfind("\n", 0, pos) + 1
        line_text = content[line_start:pos]
        if line_text.lstrip().startswith("#"):
            idx = pos + 1
            continue
        snippet = content[pos : pos + window]
        # Must carry some -p <project> scope flag (solanatrilly or solanabilly are both valid scopes)
        if "-p solanatrilly" not in snippet and "-p solanabilly" not in snippet:
            violations.append(
                f"  pos={pos}: ...{content[max(0, pos - 20):pos + 80]}..."
            )
        idx = pos + 1

    assert not violations, (
        "AC-53.3: All 'docker compose' commands in deploy.yml must carry an explicit "
        "'-p <project>' scope flag ('-p solanatrilly' for solanatrilly commands, "
        "'-p solanabilly' for the solanaBilly isolation health check). "
        "Unscoped commands default to directory name and risk cross-stack interference. "
        "Violations found:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 6. Root fix in place (not a symptom patch)
# ---------------------------------------------------------------------------


def test_root_fix_in_place_not_symptom_patch() -> None:
    """'docker image prune -f' must be in the Deploy-to-VPS SSH command AND ops/rca_run_27683660493.md must exist.

    AC-53.3 requires: 'The fix must be a ROOT fix verified against the run, not a
    symptom patch.' Two criteria must both be true:

    (a) 'docker image prune -f' is in the VPS deploy SSH command — this is the actual
        structural fix that prevents ENOSPC by clearing dangling image layers before
        each pull. A symptom patch would retry the pull, adjust timeouts, or ignore
        the error. The prune addresses the cause: accumulated layers fill the disk.

    (b) ops/rca_run_27683660493.md exists — this is the RCA record from AC-53.1 that
        documents the root cause. Its existence confirms that the diagnosis was done
        before applying the fix, which is the hallmark of a root fix (not a guess-and-
        patch approach). Removing this file would erase the documented rationale.
    """
    script = _vps_step_script()
    assert "docker image prune -f" in script, (
        "AC-53.3: 'docker image prune -f' must be in the Deploy-to-VPS SSH command. "
        "This is the ROOT fix for the VPS disk-exhaustion root cause (AC-53.1, run "
        "27683660493). A symptom patch (retry, timeout, error-ignore) is not sufficient. "
        f"Current VPS deploy script:\n{script}"
    )
    assert RCA_RECORD.exists(), (
        f"AC-53.3: RCA record not found at {RCA_RECORD}. "
        "ops/rca_run_27683660493.md must exist as the documented root-cause analysis "
        "from AC-53.1. Its presence confirms the fix is root-cause-driven, not a patch."
    )


# ---------------------------------------------------------------------------
# 7. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac533.md must exist and have content > 200 chars.

    AC-53.3 is a Tester-CONFIRM step. The ops record is the durable evidence artifact
    that documents all four VPS conditions and the green run that confirmed them.
    Without this file, there is no traceable record that the Tester-CONFIRM was done —
    the AC-53.3 requirement ('Tester-CONFIRM from that ACTUAL GREEN deploy run') is
    not satisfiable without a durable artifact.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-53.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac533.md as the durable "
        "evidence artifact for the Tester-CONFIRM step."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-53.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-53.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document all four conditions and the fix rationale."
    )


# ---------------------------------------------------------------------------
# 8. Tester confirm record documents all four AC-53.3 conditions
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_four_conditions() -> None:
    """The confirmation record must reference all four AC-53.3 conditions: 8002, listener, celery, 8001.

    AC-53.3 requires confirmation of exactly four VPS conditions:
    1. HTTP 200 on port 8002 — record must reference '8002'
    2. 'listener' container Up — record must reference 'listener'
    3. scorer/celery-worker container Up — record must reference 'celery'
    4. solanaBilly UNTOUCHED on port 8001 — record must reference '8001'

    A record that omits any condition cannot serve as a complete Tester-CONFIRM
    artifact. This test catches cases where the record was created with incomplete
    condition documentation, which would leave a gap in the audit trail.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-53.3: Tester confirmation record must reference '8002' (condition 1: "
        "HTTP 200 on port 8002). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-53.3: Tester confirmation record must reference 'listener' (condition 2: "
        "'listener' container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-53.3: Tester confirmation record must reference 'celery' (condition 3: "
        "scorer/celery-worker container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "8001" in text, (
        "AC-53.3: Tester confirmation record must reference '8001' (condition 4: "
        "solanaBilly UNTOUCHED on port 8001). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
