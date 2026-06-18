# ---
# file: core/tests/test_deploy_disk_fix_ac532.py
# project: solanatrilly
# purpose: AC-53.2 — Structural guards verifying the VPS disk-exhaustion fix is applied
#          and that the AC-39.2 phase-promoter + AC-12.3 smoke-test retry-with-backoff
#          are preserved in the re-deploy; and that the green run ops record exists.
# story: US-53 AC-53.2
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml, re
# ---
"""AC-53.2 — Apply the disk-exhaustion root-cause fix and re-deploy for a green run.

AC text:
  Apply the root-cause fix and re-run the deploy on 'main' at HEAD (the unified
  US-40 workflow_call gate — gated by the already-green canonical ci.yml 'test'
  job), obtaining an ACTUAL GREEN deploy run that carries the US-47 ruff-gate
  change to the VPS. Verified by the green deploy run id being recorded and the
  AC-39.2 phase-promoter pre-deploy step + the AC-12.3 smoke-test retry-with-
  backoff confirmed preserved in that run.

Root cause (from AC-53.1 ops/rca_run_27683660493.md):
  VPS disk exhaustion in /var/lib/containerd overlayfs storage. Accumulated image
  layers from prior sprint-10 deploy runs filled the partition; the 195 MB Django
  layer could not be extracted during docker compose pull (ENOSPC). Distinct from
  the orphaned-container failure (AC-52.3) and the WS-key defect (US-52).

Fix applied:
  Prepend 'docker image prune -f; ' to the Deploy-to-VPS SSH command in deploy.yml,
  BEFORE docker compose down/pull/up. This reclaims dangling image layers without
  interactive confirmation and without --all (tagged images like :latest are kept).

Tests in this module:
  test_image_prune_in_deploy_vps_step
      'docker image prune -f' is present in the Deploy-to-VPS SSH command.
  test_image_prune_before_compose_pull
      'docker image prune' appears before 'docker compose pull' in the SSH command.
  test_image_prune_before_compose_down
      'docker image prune' appears before 'docker compose down' (first compose call).
  test_phase_promoter_preserved
      AC-39.2 phase-promoter step still present in deploy job after the fix.
  test_phase_promoter_ordered_before_vps_deploy
      Phase-promoter step comes before 'Deploy to VPS' in deploy job step order.
  test_smoke_test_retry_preserved
      AC-12.3 retry-with-backoff (/health/ loop) still present after the fix.
  test_green_deploy_record_exists
      ops/green_deploy_ac532.md exists as the durable deploy record artifact.
  test_green_deploy_record_documents_image_prune_fix
      The ops record documents 'docker image prune' as the applied fix.
  test_green_deploy_record_cites_ac392_and_ac123
      The ops record references AC-39.2 and AC-12.3 steps as preserved.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
GREEN_DEPLOY_RECORD = REPO_ROOT / "ops" / "green_deploy_ac532.md"

VPS_STEP_NAME = "Deploy to VPS staging stack"
PHASE_PROMOTER_KEYWORD = "phase-promoter"
SMOKE_KEYWORD = "smoke"


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
        f"AC-53.2: No step named '{VPS_STEP_NAME}' found in deploy job. "
        f"Steps: {[s.get('name', '') for s in steps]}"
    )
    return str(vps_step.get("run", ""))


def _find_step(keyword: str) -> dict | None:
    kw = keyword.lower()
    for step in _deploy_job_steps():
        if kw in str(step.get("name", "")).lower():
            return step
    return None


# ---------------------------------------------------------------------------
# 1. docker image prune -f in the Deploy-to-VPS SSH command
# ---------------------------------------------------------------------------


def test_image_prune_in_deploy_vps_step() -> None:
    """'docker image prune -f' must be in the Deploy-to-VPS SSH command (AC-53.2).

    Root cause (AC-53.1): VPS disk exhaustion — /var/lib/containerd overlayfs
    partition filled by accumulated image layers from prior deploy runs, causing
    ENOSPC during 'docker compose pull' (run 27683660493). Fix: prune dangling
    images before each pull to guarantee free space. Without this, any VPS with
    enough sprint history will fail the pull step.

    This test fails if 'docker image prune -f' is removed, re-exposing the disk
    exhaustion failure mode.
    """
    script = _vps_step_script()
    assert "docker image prune -f" in script, (
        "AC-53.2: 'docker image prune -f' must be in the Deploy-to-VPS SSH command. "
        "Root cause of run 27683660493: VPS disk exhaustion during docker compose pull "
        "(ENOSPC, 195 MB Django layer). Fix: prune dangling images before pull so the "
        "VPS always has space regardless of deploy history. "
        f"Current Deploy-to-VPS script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 2. image prune appears BEFORE docker compose pull
# ---------------------------------------------------------------------------


def test_image_prune_before_compose_pull() -> None:
    """'docker image prune' must appear BEFORE 'docker compose pull' in the SSH command.

    The disk must be freed BEFORE the pull attempts to write new image layers.
    A prune that runs after the pull has already failed is useless — the ENOSPC
    error occurs during the pull's layer-extraction write, not after.

    The pull call is 'docker compose -p solanatrilly -f .../docker-compose.staging.yml pull'
    so we search for 'staging.yml pull' as the pull-verb marker (uniquely identifies the
    pull call, since 'pull' follows the staging compose file name in the SSH command).

    This test fails if the ordering is reversed, which would let the pull fail
    on ENOSPC before the prune has run.
    """
    script = _vps_step_script()
    prune_pos = script.find("docker image prune")
    # The pull call looks like: 'docker compose ... docker-compose.staging.yml pull &&'
    # Use 'staging.yml pull' as the unambiguous marker for the pull verb.
    pull_marker = "staging.yml pull"
    compose_pull_pos = script.find(pull_marker)
    assert prune_pos != -1, (
        "AC-53.2: 'docker image prune' not found in Deploy-to-VPS SSH command."
    )
    assert compose_pull_pos != -1, (
        f"AC-53.2: '{pull_marker}' not found in Deploy-to-VPS SSH command. "
        "The pull call (docker compose ... docker-compose.staging.yml pull) must be present."
    )
    assert prune_pos < compose_pull_pos, (
        f"AC-53.2: 'docker image prune' (pos {prune_pos}) must appear BEFORE "
        f"the compose pull call '{pull_marker}' (pos {compose_pull_pos}) in the SSH command. "
        "The disk must be freed before the pull writes new layers (ENOSPC risk)."
    )


# ---------------------------------------------------------------------------
# 3. image prune appears BEFORE docker compose down (first compose call)
# ---------------------------------------------------------------------------


def test_image_prune_before_compose_down() -> None:
    """'docker image prune' must appear before the first 'docker compose' call.

    The prune step must run first, before any compose command, to ensure the
    disk has free space before any compose operation. This test verifies the
    correct sequence: prune → down → pull → up.
    """
    script = _vps_step_script()
    prune_pos = script.find("docker image prune")
    first_compose_pos = script.find("docker compose")
    assert prune_pos != -1, (
        "AC-53.2: 'docker image prune' not found in Deploy-to-VPS SSH command."
    )
    assert first_compose_pos != -1, (
        "AC-53.2: 'docker compose' not found in Deploy-to-VPS SSH command."
    )
    assert prune_pos < first_compose_pos, (
        f"AC-53.2: 'docker image prune' (pos {prune_pos}) must appear BEFORE "
        f"the first 'docker compose' call (pos {first_compose_pos}). "
        "Expected sequence: prune → down → pull → up."
    )


# ---------------------------------------------------------------------------
# 4. AC-39.2 phase-promoter step preserved
# ---------------------------------------------------------------------------


def test_phase_promoter_preserved() -> None:
    """AC-39.2 phase-promoter step must still be present in deploy.yml after the fix (AC-53.2).

    The disk-exhaustion fix must not remove the phase-promoter gate. The phase-
    promoter (tools/promote_sprint_phase.py) blocks the VPS deploy if the sprint
    phase is stale or integrity fails — its absence would allow a deploy to reach
    the VPS without passing the gate.

    AC-53.2 requires: 'the AC-39.2 phase-promoter pre-deploy step confirmed preserved'.
    """
    content = _deploy_text()
    assert "promote_sprint_phase.py" in content, (
        "AC-53.2: The AC-39.2 phase-promoter step (promote_sprint_phase.py) must be "
        "preserved in deploy.yml after applying the disk-exhaustion fix. Its absence "
        "would allow a deploy without the phase gate passing (AC-39.2)."
    )


# ---------------------------------------------------------------------------
# 5. Phase-promoter ordered before VPS deploy
# ---------------------------------------------------------------------------


def test_phase_promoter_ordered_before_vps_deploy() -> None:
    """AC-39.2 phase-promoter step must come before 'Deploy to VPS' in the deploy job (AC-53.2).

    The disk-exhaustion fix must not alter the step ordering. If the phase-promoter
    follows the VPS deploy, it cannot block a bad deploy from reaching the VPS.
    """
    steps = _deploy_job_steps()
    step_names = [s.get("name", "") for s in steps]
    lower_names = [n.lower() for n in step_names]

    promoter_indices = [
        i for i, n in enumerate(lower_names)
        if PHASE_PROMOTER_KEYWORD in n or "promote_sprint" in n
    ]
    deploy_indices = [
        i for i, n in enumerate(lower_names)
        if "deploy to vps" in n
    ]

    assert promoter_indices, (
        f"AC-53.2: No phase-promoter step found in deploy job. Steps: {step_names}"
    )
    assert deploy_indices, (
        f"AC-53.2: No 'Deploy to VPS' step found in deploy job. Steps: {step_names}"
    )
    assert min(promoter_indices) < min(deploy_indices), (
        f"AC-53.2: Phase-promoter (index {min(promoter_indices)}) must come BEFORE "
        f"'Deploy to VPS' (index {min(deploy_indices)}). Steps: {step_names}"
    )


# ---------------------------------------------------------------------------
# 6. AC-12.3 smoke-test retry-with-backoff preserved
# ---------------------------------------------------------------------------


def test_smoke_test_retry_preserved() -> None:
    """AC-12.3 smoke-test retry-with-backoff must be preserved after the fix (AC-53.2).

    The disk-exhaustion fix must not remove the retry loop. The VPS confirmation
    condition requires HTTP 200 on port 8002 WITH the AC-12.3 retry-with-backoff
    loop — a single-shot curl would fail during container startup.

    AC-53.2 requires: 'the AC-12.3 smoke-test retry-with-backoff confirmed preserved'.
    """
    content = _deploy_text()
    assert "SMOKE_MAX_ATTEMPTS" in content, (
        "AC-53.2: SMOKE_MAX_ATTEMPTS (AC-12.3 retry-with-backoff) must be preserved "
        "in deploy.yml after applying the disk-exhaustion fix."
    )
    assert "SMOKE_RETRY_DELAY" in content, (
        "AC-53.2: SMOKE_RETRY_DELAY (AC-12.3 backoff delay) must be preserved "
        "in deploy.yml after applying the disk-exhaustion fix."
    )
    assert "/health/" in content, (
        "AC-53.2: /health/ smoke-test endpoint must be preserved in deploy.yml (AC-12.3)."
    )


# ---------------------------------------------------------------------------
# 7. ops/green_deploy_ac532.md exists
# ---------------------------------------------------------------------------


def test_green_deploy_record_exists() -> None:
    """ops/green_deploy_ac532.md must exist as the green deploy record artifact (AC-53.2).

    AC-53.2: 'Verified by the green deploy run id being recorded'. The ops record
    is the durable evidence artifact committed alongside the workflow fix, so the
    orchestrator can confirm the run ID and URL after the deploy runs on main.
    """
    assert GREEN_DEPLOY_RECORD.exists(), (
        f"AC-53.2: Green deploy record not found at {GREEN_DEPLOY_RECORD}. "
        "This file must be committed as ops/green_deploy_ac532.md."
    )
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert text.strip(), f"AC-53.2: Green deploy record at {GREEN_DEPLOY_RECORD} is empty."
    assert len(text) > 200, (
        f"AC-53.2: Green deploy record at {GREEN_DEPLOY_RECORD} is too short "
        f"({len(text)} chars). It must document the fix and record structure."
    )


# ---------------------------------------------------------------------------
# 8. Ops record documents the image prune fix
# ---------------------------------------------------------------------------


def test_green_deploy_record_documents_image_prune_fix() -> None:
    """The ops record must document 'docker image prune' as the applied fix (AC-53.2).

    The fix is the direct resolution of the root cause diagnosed in AC-53.1
    (VPS disk exhaustion). Without documenting it, the record does not connect
    the green run to the specific code change that enabled it.
    """
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert "image prune" in text, (
        "AC-53.2: Green deploy record must document 'docker image prune' as the fix "
        "applied to resolve the VPS disk-exhaustion root cause (AC-53.1). "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )


# ---------------------------------------------------------------------------
# 9. Ops record references AC-39.2 and AC-12.3
# ---------------------------------------------------------------------------


def test_green_deploy_record_cites_ac392_and_ac123() -> None:
    """The ops record must reference AC-39.2 and AC-12.3 as preserved in the run (AC-53.2).

    AC-53.2 requires confirmation that both steps are preserved in the green run.
    The record must reference both AC markers so the Tester can cross-check.
    """
    text = GREEN_DEPLOY_RECORD.read_text(encoding="utf-8")
    assert "AC-39.2" in text or "ac-39.2" in text.lower(), (
        "AC-53.2: Green deploy record must reference AC-39.2 (phase-promoter gate). "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )
    assert "AC-12.3" in text or "ac-12.3" in text.lower(), (
        "AC-53.2: Green deploy record must reference AC-12.3 (smoke-test retry). "
        f"Record file: {GREEN_DEPLOY_RECORD}"
    )
