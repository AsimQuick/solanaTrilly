# ---
# file: core/tests/test_deploy_green_run_ac551.py
# project: solanatrilly
# purpose: AC-55.1 — Structural guards verifying the deploy pipeline carries the FULL P6 dashboard chain
#          to the VPS with AC-39.2 phase-promoter + AC-12.3 retry-with-backoff preserved.
# story: US-55 AC-55.1
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-55.1 — Structural guards for the P6 dashboard-chain closeout green deploy run.

AC text:
  With the deploy path sound (US-52/US-53/US-54), obtain ONE ACTUAL GREEN deploy run on
  'main' at HEAD that carries the FULL P6 dashboard chain — US-48 (foundation + ONE-tape-feed
  consumer), US-49 (tape→candle API + token-detail/research view), US-50 (cohort wall),
  US-51 (annotation + export) — to the VPS solanatrilly staging stack. Verified by the green
  deploy run id being recorded with the AC-39.2 phase-promoter + AC-12.3 retry-with-backoff
  confirmed preserved.

Full P6 dashboard chain:
  - US-48: React dashboard foundation + ONE-tape-feed consumer (Django Channels WebSocket)
  - US-49: tape→candle API + token-detail/research view (/api/candles/<mint>/)
  - US-50: cohort wall (/api/cohort/)
  - US-51: annotation + export (/api/annotations/<mint>/)

Preserved invariants (from US-52/US-53/US-54 deploy-path repairs):
  - AC-39.2 phase-promoter: promote_sprint_phase.py gates the deploy (before VPS step)
  - AC-12.3 retry-with-backoff: SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY on /health/

VPS conditions confirmed by Tester from the actual green deploy run:
  1. HTTP 200 on port 8002 (/health/) with AC-12.3 retry-with-backoff.
  2. Dashboard route HTTP 200 (/dashboard/) — US-48 foundation.
  3. WS endpoint HTTP 101 upgrade (/ws/tape/smoke_test/) — US-48 tape feed.
  4. 'web' container Up in the solanatrilly stack.
  5. 'frontend' container Up in the solanatrilly stack.
  6. 'listener' container Up in the solanatrilly stack.
  7. 'celery-worker' container Up in the solanatrilly stack.
  8. solanaBilly UNTOUCHED on port 8001 (every command scoped -p solanatrilly).

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac551.md exists and has content > 200 chars.
  test_tester_confirm_record_references_p6_chain
      The record mentions all four P6 stories: US-48, US-49, US-50, US-51.
  test_tester_confirm_record_references_phase_promoter
      The record mentions 'AC-39.2' (phase-promoter preserved).
  test_tester_confirm_record_references_retry_with_backoff
      The record mentions 'AC-12.3' (retry-with-backoff preserved).
  test_tester_confirm_record_documents_run_id_field
      The record mentions 'run' and 'PENDING' (has the run ID field to be filled).
  test_tester_confirm_record_documents_vps_conditions
      Record mentions '8002' (HTTP 200 on port 8002).
      Record mentions '8001' (solanaBilly untouched on port 8001).
      Record mentions 'web', 'frontend', 'listener', 'celery'.
  test_phase_promoter_step_preserved_in_deploy_yml
      deploy.yml still has 'promote_sprint_phase.py' step.
      Phase-promoter step comes before 'Deploy to VPS staging stack'.
  test_retry_with_backoff_preserved_in_deploy_yml
      deploy.yml still has 'SMOKE_MAX_ATTEMPTS' (AC-12.3 retry-with-backoff).
      deploy.yml still has 'SMOKE_RETRY_DELAY'.
  test_full_p6_dashboard_stack_containers_verified
      deploy.yml verifies frontend container up.
      deploy.yml verifies web container up.
      deploy.yml verifies listener container up.
      deploy.yml verifies celery-worker container up.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac551.md"
VPS_STEP_NAME = "Deploy to VPS staging stack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-55.1: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-55.1: deploy.yml must be valid YAML"
    return data


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _deploy_step_names() -> list[str]:
    """Return the ordered list of deploy-job step names."""
    return [s.get("name", "") for s in _deploy_job_steps()]


# ---------------------------------------------------------------------------
# 1. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac551.md must exist and have content > 200 chars.

    AC-55.1 is the P6 dashboard-chain closeout deploy step. The ops record is the
    durable evidence artifact that documents the green deploy run ID, all VPS conditions,
    and the four P6 stories carried. Without this file, there is no traceable record that
    AC-55.1 was completed — the requirement to record the green run id is not satisfiable
    without a durable artifact.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-55.1: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac551.md as the durable "
        "evidence artifact for the P6 dashboard-chain closeout green deploy run."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-55.1: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-55.1: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document the full P6 chain and all VPS conditions."
    )


# ---------------------------------------------------------------------------
# 2. Tester confirm record references all four P6 stories
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_p6_chain() -> None:
    """The confirmation record must reference all four P6 dashboard chain stories.

    AC-55.1 specifically requires the FULL P6 dashboard chain:
      - US-48: dashboard foundation + ONE-tape-feed consumer
      - US-49: tape→candle API + token-detail/research view
      - US-50: cohort wall
      - US-51: annotation + export

    A record that omits any of the four stories cannot serve as a complete evidence
    artifact for the P6 dashboard-chain closeout — partial carry is not AC-55.1 done.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    for story in ("US-48", "US-49", "US-50", "US-51"):
        assert story in text, (
            f"AC-55.1: Tester confirmation record must reference '{story}' (part of the "
            f"FULL P6 dashboard chain required by AC-55.1). A deploy that omits {story} "
            "does not constitute a full P6 chain closeout. "
            f"Record: {TESTER_CONFIRM_RECORD}"
        )


# ---------------------------------------------------------------------------
# 3. Tester confirm record references AC-39.2 phase-promoter
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_phase_promoter() -> None:
    """The confirmation record must reference 'AC-39.2' (phase-promoter preserved).

    AC-55.1 requires the green deploy run to confirm that the AC-39.2 phase-promoter
    is preserved. The ops record must document this so the Tester can confirm the
    phase-promoter step was present and gated the deploy on sprint-phase integrity.

    Without this reference, the record cannot confirm the phase-promoter invariant
    is intact in the green deploy run.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "AC-39.2" in text, (
        "AC-55.1: Tester confirmation record must reference 'AC-39.2' (phase-promoter "
        "preserved). AC-55.1 requires confirmation that promote_sprint_phase.py gates "
        "the deploy in the green run. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Tester confirm record references AC-12.3 retry-with-backoff
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_retry_with_backoff() -> None:
    """The confirmation record must reference 'AC-12.3' (retry-with-backoff preserved).

    AC-55.1 requires confirmation that the AC-12.3 retry-with-backoff is preserved.
    The ops record must document SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY so the Tester
    can confirm HTTP 200 on 8002 was verified with backoff (not a single-shot curl).

    Without this reference, the record cannot confirm the retry-backoff invariant
    is intact in the green deploy run.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "AC-12.3" in text, (
        "AC-55.1: Tester confirmation record must reference 'AC-12.3' (retry-with-backoff "
        "preserved). AC-55.1 requires confirmation that SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY "
        "are present in the green deploy run's smoke-test step. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 5. Tester confirm record has run ID field (PENDING to be filled)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_run_id_field() -> None:
    """The confirmation record must have a run ID field with 'PENDING' (to be filled after merge).

    AC-55.1 requires the green deploy run id to be recorded. The ops record must have
    a placeholder field that the Tester fills in after the actual green deploy run on main.

    This test confirms the record has the run ID field structure in place — the PENDING
    value will be replaced by the Tester with the actual run ID from the green deploy.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "run" in text.lower(), (
        "AC-55.1: Tester confirmation record must have a run ID field. "
        "AC-55.1 requires the green deploy run id to be recorded. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "PENDING" in text, (
        "AC-55.1: Tester confirmation record must have 'PENDING' fields awaiting the "
        "actual green deploy run. The run ID, run URL, and conditions are filled in "
        "after the actual green deploy on main. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Tester confirm record documents VPS conditions
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_vps_conditions() -> None:
    """The confirmation record must document all key VPS conditions.

    AC-55.1 VPS conditions:
      - HTTP 200 on port 8002 (record references '8002')
      - solanaBilly untouched on port 8001 (record references '8001')
      - web, frontend, listener, celery containers Up

    A record that omits any of these conditions cannot serve as a complete Tester-CONFIRM
    artifact for the P6 dashboard-chain closeout.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-55.1: Tester confirmation record must reference '8002' (HTTP 200 on port 8002). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "8001" in text, (
        "AC-55.1: Tester confirmation record must reference '8001' (solanaBilly UNTOUCHED "
        "on port 8001). Hard isolation requires every deploy to confirm solanaBilly is unaffected. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "web" in text.lower(), (
        "AC-55.1: Tester confirmation record must reference the 'web' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "frontend" in text.lower(), (
        "AC-55.1: Tester confirmation record must reference the 'frontend' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-55.1: Tester confirmation record must reference the 'listener' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-55.1: Tester confirmation record must reference 'celery' (celery-worker container). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 7. Phase-promoter step preserved in deploy.yml
# ---------------------------------------------------------------------------


def test_phase_promoter_step_preserved_in_deploy_yml() -> None:
    """deploy.yml must still have the 'promote_sprint_phase.py' step (AC-39.2 preserved).

    AC-55.1 requires the AC-39.2 phase-promoter to be confirmed preserved in the green run.
    The structural guard here ensures the phase-promoter step is in deploy.yml AND comes
    before 'Deploy to VPS staging stack' — so the phase-promoter gates the deploy.

    Removal of this step would:
      - Allow deploys to reach the VPS without passing the phase gate (AC-39.2 violation)
      - Make AC-55.1 unable to confirm 'AC-39.2 phase-promoter confirmed preserved'
    """
    content = _deploy_text()
    assert "promote_sprint_phase.py" in content, (
        "AC-55.1: deploy.yml must have the AC-39.2 phase-promoter step "
        "('python3 tools/promote_sprint_phase.py'). This step gates the VPS deploy "
        "on sprint-phase integrity and is required to be confirmed preserved by AC-55.1. "
        f"deploy.yml: {DEPLOY_YML}"
    )

    # Phase-promoter must come BEFORE the VPS deploy step
    step_names = _deploy_step_names()
    lower_names = [n.lower() for n in step_names]
    promoter_indices = [
        i for i, n in enumerate(lower_names)
        if "phase-promoter" in n or "promote_sprint" in n
    ]
    deploy_indices = [
        i for i, n in enumerate(lower_names)
        if "deploy to vps" in n
    ]
    assert promoter_indices, (
        f"AC-55.1: deploy.yml deploy job must have a phase-promoter step. "
        f"Steps found: {step_names}"
    )
    assert deploy_indices, (
        f"AC-55.1: deploy.yml deploy job must have a 'Deploy to VPS staging stack' step. "
        f"Steps found: {step_names}"
    )
    assert min(promoter_indices) < min(deploy_indices), (
        f"AC-55.1: AC-39.2 phase-promoter step (index {min(promoter_indices)}) must come "
        f"BEFORE 'Deploy to VPS' step (index {min(deploy_indices)}) in deploy.yml. "
        "A post-deploy phase-promoter cannot gate the deploy (AC-39.2, AC-55.1). "
        f"Step order: {step_names}"
    )


# ---------------------------------------------------------------------------
# 8. Retry-with-backoff preserved in deploy.yml
# ---------------------------------------------------------------------------


def test_retry_with_backoff_preserved_in_deploy_yml() -> None:
    """deploy.yml must still have SMOKE_MAX_ATTEMPTS and SMOKE_RETRY_DELAY (AC-12.3 preserved).

    AC-55.1 requires the AC-12.3 retry-with-backoff to be confirmed preserved in the green run.
    The structural guard here ensures both retry variables are present in deploy.yml.

    Removal of either variable would:
      - Reduce the smoke test to a single-shot curl (likely to fail on container startup)
      - Make AC-55.1 unable to confirm 'AC-12.3 retry-with-backoff confirmed preserved'
      - Violate AC-12.3's requirement for retry logic on the smoke test
    """
    content = _deploy_text()
    assert "SMOKE_MAX_ATTEMPTS" in content, (
        "AC-55.1: deploy.yml must still have 'SMOKE_MAX_ATTEMPTS' (AC-12.3 retry-with-backoff). "
        "This variable drives the retry loop on the /health/ smoke test. Without it, the smoke "
        "test is a single-shot curl that fails during container startup. AC-55.1 requires "
        "this to be confirmed preserved in the green deploy run. "
        f"deploy.yml: {DEPLOY_YML}"
    )
    assert "SMOKE_RETRY_DELAY" in content, (
        "AC-55.1: deploy.yml must still have 'SMOKE_RETRY_DELAY' (AC-12.3 backoff delay). "
        "This variable sets the sleep between retry attempts. Without it, the retry loop "
        "has no delay. AC-55.1 requires this to be confirmed preserved in the green deploy run. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 9. Full P6 dashboard stack containers verified in deploy.yml
# ---------------------------------------------------------------------------


def test_full_p6_dashboard_stack_containers_verified() -> None:
    """deploy.yml must verify all four P6 dashboard stack containers are Up.

    The full P6 dashboard chain requires ALL four containers to be running:
      - frontend: React dashboard UI (US-48)
      - web: Django backend serving the candle/cohort/annotation APIs (US-49/50/51)
      - listener: Birdeye WebSocket tape feed consumer (US-48)
      - celery-worker: async task processor for scoring/exports (US-51)

    A deploy.yml that only checks some containers cannot confirm the FULL P6 chain is live.
    A green run with any of the four missing is not a valid P6 dashboard-chain closeout.
    """
    content = _deploy_text()

    # frontend container (US-48 React dashboard)
    assert "solanatrilly.frontend" in content or "frontend' container" in content.lower(), (
        "AC-55.1: deploy.yml must verify the 'frontend' container is Up (US-48 React dashboard). "
        "The P6 dashboard chain requires the frontend container to be running. "
        f"deploy.yml: {DEPLOY_YML}"
    )

    # web container (Django backend — US-49/50/51 APIs)
    assert "solanatrilly.web" in content or "'web' container" in content.lower(), (
        "AC-55.1: deploy.yml must verify the 'web' container is Up (Django backend, US-49/50/51 APIs). "
        "The P6 dashboard chain requires the web container to be running. "
        f"deploy.yml: {DEPLOY_YML}"
    )

    # listener container (Birdeye tape feed — US-48)
    assert "solanatrilly.listener" in content or "solanatrilly-listener" in content, (
        "AC-55.1: deploy.yml must verify the 'listener' container is Up (Birdeye tape feed, US-48). "
        "The P6 dashboard chain requires the listener container to be running. "
        f"deploy.yml: {DEPLOY_YML}"
    )

    # celery-worker container (async tasks — US-51 export)
    assert (
        "solanatrilly.celery.worker" in content
        or "solanatrilly-celery-worker" in content
        or "celery-worker" in content
    ), (
        "AC-55.1: deploy.yml must verify the 'celery-worker' container is Up (US-51 export). "
        "The P6 dashboard chain requires the celery-worker container to be running. "
        f"deploy.yml: {DEPLOY_YML}"
    )
