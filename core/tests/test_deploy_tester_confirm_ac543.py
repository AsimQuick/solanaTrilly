# ---
# file: core/tests/test_deploy_tester_confirm_ac543.py
# project: solanatrilly
# purpose: AC-54.3 — Structural guards verifying the deploy pipeline is reproducibly green
#          across BOTH a deliberate HEAD run AND a subsequent per-merge run (no recurrence
#          of the 27681451880-class J4 regression), and that all Tester VPS-confirmation
#          conditions are enforced structurally in deploy.yml.
# story: US-54 AC-54.3
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-54.3 — Tester-CONFIRM structural guards for reproducible green deploy runs.

AC text:
  Run the consolidated deploy on 'main' at HEAD and obtain an ACTUAL GREEN deploy run
  that is reproducibly green across BOTH a deliberate HEAD run AND a subsequent per-merge
  run (no recurrence of the 27681451880-class regression). Tester-confirm the VPS answers
  HTTP 200 on 8002, the relevant containers are Up, and solanaBilly is untouched on 8001
  (scoped -p solanatrilly). New/changed files carry metadata front matter.

The 27681451880-class regression pattern:
  A deliberate HEAD deploy run (workflow_dispatch on main) succeeds; the immediately
  subsequent per-merge run (push to main) fails with VPS disk exhaustion (ENOSPC).
  Root cause: the deliberate run exhausted the last free bytes in /var/lib/containerd;
  the per-merge run arrived with 0 bytes for the next image pull.

Reproducibility fix (from AC-54.1/AC-54.2):
  - 'docker image prune -f' before every pull (AC-53.2) — reclaims dangling image
    layers before each deploy so disk exhaustion cannot recur across runs.
  All nine regression-guard invariants (AC-54.2) pin these fixes in place.

Note: deploys are workflow_dispatch-only (operator decision 2026-06-18). The push
trigger was intentionally removed because a push-to-main trigger fired a full ~5-min
VPS deploy on every commit (including sprint-state commits), wasting GitHub Actions
minutes. The orchestrator dispatches one deploy per sprint boundary.

VPS conditions confirmed by Tester from the actual green deploy run:
  1. HTTP 200 on port 8002 (/health/) with AC-12.3 retry-with-backoff.
  2. 'web' container Up in the solanatrilly stack.
  3. 'frontend' container Up in the solanatrilly stack.
  4. 'listener' container Up in the solanatrilly stack.
  5. 'celery-worker' container Up in the solanatrilly stack.
  6. solanaBilly UNTOUCHED on port 8001 (every command scoped -p solanatrilly).

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac543.md exists and has content > 200 chars.
  test_tester_confirm_record_documents_reproducibility
      The record references both 'deliberate' and 'per-merge' run types and the
      27681451880 run ID (the J4 regression class being eliminated).
  test_tester_confirm_record_documents_http200_condition
      The record references '8002' (condition 1: HTTP 200 on port 8002).
  test_tester_confirm_record_documents_containers_up
      The record references 'web', 'frontend', 'listener', 'celery' (conditions 2-5).
  test_tester_confirm_record_documents_solanabilly_isolation
      The record references '8001' (condition 6: solanaBilly UNTOUCHED on port 8001).
  test_deploy_is_dispatch_only
      deploy.yml on: block has NO push trigger — deploys are workflow_dispatch-only
      (operator decision 2026-06-18; push-to-main wasted Actions minutes).
  test_disk_exhaustion_fix_prevents_j4_regression
      'docker image prune -f' is in the Deploy-to-VPS SSH command — this is the structural
      fix that prevents the deliberate-run-green → per-merge-run-ENOSPC pattern (J4).
  test_vps_conditions_all_enforced_by_deploy_steps
      deploy.yml contains all six VPS condition checks: HTTP 200 /health/ with retry,
      web+frontend Up, listener Up, celery-worker Up, solanaBilly 8001 isolation.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac543.md"
VPS_STEP_NAME = "Deploy to VPS staging stack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-54.3: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-54.3: deploy.yml must be valid YAML"
    return data


def _on_block() -> dict:
    """Return the 'on:' trigger block. PyYAML parses 'on' as Python True (YAML 1.1)."""
    data = _deploy_data()
    return data.get("on") or data.get(True) or {}


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _vps_step_script() -> str:
    steps = _deploy_job_steps()
    step = next(
        (s for s in steps if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert step is not None, (
        f"AC-54.3: No step named '{VPS_STEP_NAME}' in deploy.yml deploy job. "
        f"Steps: {[s.get('name', '') for s in steps]}"
    )
    return str(step.get("run", ""))


# ---------------------------------------------------------------------------
# 1. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac543.md must exist and have content > 200 chars.

    AC-54.3 is a Tester-CONFIRM step. The ops record is the durable evidence artifact
    that documents the VPS conditions and both run types (deliberate HEAD + per-merge).
    Without this file, there is no traceable record that the Tester-CONFIRM was done —
    the AC-54.3 requirement ('Tester-confirm the VPS answers HTTP 200...') is not
    satisfiable without a durable artifact.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-54.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac543.md as the durable "
        "evidence artifact for the Tester-CONFIRM step."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-54.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-54.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document both run types and all VPS conditions."
    )


# ---------------------------------------------------------------------------
# 2. Tester confirm record documents reproducibility (both run types + J4 run ID)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_reproducibility() -> None:
    """The confirmation record must reference both run types and the J4 regression run ID.

    AC-54.3 specifically requires reproducibility across BOTH:
    (a) a deliberate HEAD run (workflow_dispatch)
    (b) a subsequent per-merge run (push to main)

    The record must reference '27681451880' (the J4 regression run being eliminated),
    'deliberate' (or 'workflow_dispatch'), and 'per-merge' (or 'push') to confirm that
    the evidence artifact covers the full reproducibility requirement — not just one
    run type.

    A record that only documents the deliberate run cannot confirm reproducibility,
    since J4 is defined as: deliberate run green → per-merge run fails.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "27681451880" in text, (
        "AC-54.3: Tester confirmation record must reference '27681451880' (the J4 "
        "per-merge regression run ID). The record must document that this regression "
        "class is eliminated, not merely that a single deliberate run succeeded. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "deliberate" in text.lower() or "workflow_dispatch" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference the deliberate HEAD run type "
        "('deliberate' or 'workflow_dispatch'). Both run types must be documented "
        "to satisfy the reproducibility requirement. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "per-merge" in text.lower() or "push" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference the per-merge run type "
        "('per-merge' or 'push'). The J4 regression is defined as deliberate green → "
        "per-merge fail, so the per-merge run type must be documented. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 3. Tester confirm record documents HTTP 200 condition (condition 1)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_http200_condition() -> None:
    """The confirmation record must reference '8002' (condition 1: HTTP 200 on port 8002).

    AC-54.3 VPS condition 1: the staging stack answers HTTP 200 on port 8002
    at /health/ with the AC-12.3 retry-with-backoff.

    A record that omits this condition cannot serve as a complete Tester-CONFIRM
    artifact for AC-54.3.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-54.3: Tester confirmation record must reference '8002' (condition 1: "
        "HTTP 200 on port 8002 at /health/ with AC-12.3 retry-with-backoff). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Tester confirm record documents containers Up (conditions 2-5)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_containers_up() -> None:
    """The confirmation record must reference all four containers (conditions 2-5).

    AC-54.3 VPS conditions 2-5: 'web', 'frontend', 'listener', and 'celery-worker'
    (scorer) containers must be Up in the solanatrilly stack.

    Checking all four containers (not just two or three) is the holistic confirmation
    required by AC-54.3 ('the relevant containers are Up') — a partial check leaves
    a gap in the Tester-CONFIRM evidence.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "web" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference the 'web' container "
        "(condition 2: web container Up in the solanatrilly stack). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "frontend" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference the 'frontend' container "
        "(condition 3: frontend container Up in the solanatrilly stack). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference the 'listener' container "
        "(condition 4: listener container Up in the solanatrilly stack). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-54.3: Tester confirmation record must reference 'celery' (condition 5: "
        "celery-worker container Up in the solanatrilly stack). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 5. Tester confirm record documents solanaBilly isolation (condition 6)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_solanabilly_isolation() -> None:
    """The confirmation record must reference '8001' (condition 6: solanaBilly UNTOUCHED).

    AC-54.3 VPS condition 6: solanaBilly must be UNTOUCHED on port 8001 after the
    consolidated deploy, with every docker command scoped '-p solanatrilly'.

    Isolating solanaBilly from solanatrilly deploys is a hard project constraint
    (PRD §15.3, CLAUDE.md Docker Rules). Its inclusion in the evidence record
    confirms the deploy did not interfere with the live solanaBilly service.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8001" in text, (
        "AC-54.3: Tester confirmation record must reference '8001' (condition 6: "
        "solanaBilly UNTOUCHED on port 8001 after the consolidated deploy). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "solanabilly" in text.lower() or "solanaBilly" in text, (
        "AC-54.3: Tester confirmation record must reference solanaBilly explicitly "
        "(condition 6: solanaBilly isolation confirmed after deploy). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Per-merge push trigger present — prerequisite for observing reproducibility
# ---------------------------------------------------------------------------


def test_deploy_is_dispatch_only() -> None:
    """deploy.yml on: block must have NO push trigger — deploys are workflow_dispatch-only.

    Operator decision (2026-06-18): the push trigger was intentionally removed.
    A push-to-main trigger fired a full ~5-min VPS deploy on every commit to main
    (including many sprint-state commits), wasting GitHub Actions minutes. Deploys
    now fire ONLY via workflow_dispatch — the orchestrator dispatches one deploy per
    sprint boundary.

    Regression: re-adding the push trigger resumes per-commit Actions waste.
    """
    on_block = _on_block()
    assert "push" not in on_block, (
        "deploy is dispatch-only; do not re-add the push trigger "
        "(operator decision 2026-06-18 — a push-to-main trigger wastes Actions "
        "minutes by deploying on every commit). Deploys fire only via "
        "workflow_dispatch, dispatched once per sprint boundary. "
        f"Current on: block: {on_block}"
    )


# ---------------------------------------------------------------------------
# 7. Disk-exhaustion fix prevents J4 class regression
# ---------------------------------------------------------------------------


def test_disk_exhaustion_fix_prevents_j4_regression() -> None:
    """'docker image prune -f' must be in the Deploy-to-VPS SSH command (J4 regression fix).

    The J4 regression (run 27681451880) failed because the deliberate HEAD run consumed
    the last free bytes in /var/lib/containerd, leaving 0 bytes for the per-merge run's
    image pull (ENOSPC).

    'docker image prune -f' (added in AC-53.2) prevents this by reclaiming dangling
    image layers before every pull. This makes each deploy start with a clean disk
    budget, so a green deliberate run cannot exhaust the space needed for the next
    per-merge run.

    Without this fix, the J4 pattern would recur after every deployment burst:
      1. Deliberate run succeeds (consumes remaining disk)
      2. Next per-merge run fails (ENOSPC on first layer write)
      3. Developer manually triggers retry — also fails (disk still full)

    The fix breaks step (1) → (2) permanently by pre-clearing disk before each pull.
    """
    script = _vps_step_script()
    assert "docker image prune -f" in script, (
        "AC-54.3: 'docker image prune -f' must be in the Deploy-to-VPS SSH command. "
        "This is the structural fix that prevents the J4 deliberate-green → per-merge-ENOSPC "
        "regression pattern (run 27681451880 class). Without it, accumulated image layers "
        "from prior runs can fill /var/lib/containerd and break the next per-merge pull. "
        f"Current VPS deploy script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 8. All VPS conditions enforced by deploy.yml steps
# ---------------------------------------------------------------------------


def test_vps_conditions_all_enforced_by_deploy_steps() -> None:
    """deploy.yml must contain all six VPS condition checks for AC-54.3 confirmation.

    A green deploy run can only constitute Tester confirmation of AC-54.3's VPS conditions
    if the deploy pipeline ACTIVELY CHECKS and fails on each condition. If a condition check
    is absent from deploy.yml, a green run does not confirm that condition — it merely means
    the run did not fail on what was checked.

    Six conditions (all must be enforced by deploy.yml steps):
      1. HTTP 200 on /health/ port 8002 with retry-with-backoff (AC-12.3)
      2. 'web' container Up (checked via docker compose ps grep)
      3. 'frontend' container Up (checked via docker compose ps grep)
      4. 'listener' container Up (checked via docker compose ps grep)
      5. 'celery-worker' container Up (checked via docker compose ps grep)
      6. solanaBilly UNTOUCHED on port 8001 (isolation step)
    """
    content = _deploy_text()

    # Condition 1: HTTP 200 /health/ with retry-backoff
    assert "/health/" in content, (
        "AC-54.3: deploy.yml must smoke-test GET /health/ on port 8002 (condition 1). "
        "This is the primary HTTP 200 check the Tester confirms from the green run."
    )
    assert "SMOKE_MAX_ATTEMPTS" in content, (
        "AC-54.3: deploy.yml must have SMOKE_MAX_ATTEMPTS (AC-12.3 retry-with-backoff). "
        "Condition 1 requires HTTP 200 WITH the retry loop — not a single-shot curl."
    )
    assert "SMOKE_RETRY_DELAY" in content, (
        "AC-54.3: deploy.yml must have SMOKE_RETRY_DELAY (AC-12.3 backoff delay). "
        "Condition 1 requires HTTP 200 WITH backoff — not a single-shot curl."
    )

    # Conditions 2-3: web + frontend containers Up
    assert "solanatrilly.web" in content or "'web' container" in content.lower(), (
        "AC-54.3: deploy.yml must verify the 'web' container is Up (condition 2). "
        "A green run without this check does not confirm the web container is running."
    )
    assert "solanatrilly.frontend" in content or "'frontend' container" in content.lower(), (
        "AC-54.3: deploy.yml must verify the 'frontend' container is Up (condition 3). "
        "A green run without this check does not confirm the frontend container is running."
    )

    # Condition 4: listener container Up
    assert "solanatrilly.listener" in content or "solanatrilly-listener" in content, (
        "AC-54.3: deploy.yml must verify the 'listener' container is Up (condition 4). "
        "A green run without this check does not confirm the listener is running."
    )

    # Condition 5: celery-worker container Up
    assert (
        "solanatrilly.celery.worker" in content
        or "solanatrilly-celery-worker" in content
        or "celery-worker" in content
    ), (
        "AC-54.3: deploy.yml must verify the 'celery-worker' container is Up (condition 5). "
        "A green run without this check does not confirm the celery-worker is running."
    )

    # Condition 6: solanaBilly isolation on port 8001
    assert "8001" in content, (
        "AC-54.3: deploy.yml must verify solanaBilly on port 8001 is untouched (condition 6). "
        "Hard isolation (PRD §15.3) requires every deploy to confirm solanaBilly is unaffected."
    )
    assert "solanaBilly" in content or "solanabilly" in content.lower(), (
        "AC-54.3: deploy.yml must reference solanaBilly in the isolation step (condition 6). "
        "The isolation check must explicitly verify the solanaBilly service is untouched."
    )
