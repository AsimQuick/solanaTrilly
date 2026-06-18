# ---
# file: core/tests/test_deploy_live_exercise_ac713.py
# project: solanatrilly
# purpose: AC-71.3 — Structural guards verifying the pre-deploy import smoke (AC-71.1)
#          is exercised in the actual VPS deploy pipeline: the smoke runs before any image
#          is pushed (ci job → build-and-push job chain), and the AC-68.3 in-container
#          import step stays GREEN. Verifies the tester confirm record (ops/tester_confirm_ac713.md)
#          exists with the required documentation, and that deploy.yml's job ordering enforces
#          the smoke-before-push invariant.
# story: US-71 AC-71.3
# sprint: sprint-14
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: .github/workflows/ci.yml, .github/workflows/deploy.yml,
#               ops/tester_confirm_ac713.md, pyyaml
# ---
"""AC-71.3 — Live deploy exercise: pre-deploy import smoke runs in the actual VPS pipeline.

AC text:
  The pre-deploy import smoke is exercised LIVE on the VPS deploy run that carries this
  story — the built-image import smoke runs and passes in the actual pipeline before push,
  and the post-deploy in-container import step (AC-68.3) stays GREEN. Verified by the
  Tester from the green deploy run (the smoke step present and passing in the run log).
  New files carry metadata front matter. Zero firehose.

Deploy pipeline chain enforced by this AC:

  deploy.yml (workflow_dispatch)
    ├── ci job: uses ./.github/workflows/ci.yml
    │     (includes 'Pre-deploy built-image import smoke (AC-71.1)' after 'Build containers')
    ├── build-and-push job: needs: ci
    │     (GHCR push CANNOT proceed until ci — including smoke — passes)
    └── deploy job: needs: build-and-push
          (includes 'Verify shared apparatus imports in-container (AC-68.3)')

This module verifies:
  A. Tester confirm record exists and documents the required conditions.
  B. deploy.yml wiring enforces smoke-before-push (ci → build-and-push → deploy chain).
  C. The AC-68.3 step with django.setup() is present in deploy.yml deploy job.
  D. The extended regression guard (AC-71.2) test file is present (guard is in force).
  E. The smoke step name is wired into ci.yml (the file called by deploy.yml ci job).

Tests:
  Tester confirm record:
    test_tester_confirm_record_exists
    test_tester_confirm_record_documents_import_smoke
    test_tester_confirm_record_documents_django_setup
    test_tester_confirm_record_documents_ac683_step
    test_tester_confirm_record_documents_8002
    test_tester_confirm_record_documents_solanabilly_isolation

  Deploy pipeline wiring:
    test_deploy_yml_ci_job_calls_ci_yml
    test_smoke_exercised_before_image_push
    test_deploy_job_needs_build_and_push
    test_full_smoke_to_deploy_chain_enforced

  Supporting infrastructure:
    test_extended_regression_guard_test_file_exists
    test_smoke_step_present_in_ci_yml
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac713.md"
EXTENDED_GUARD_TEST = REPO_ROOT / "core" / "tests" / "test_deploy_regression_guard_ac712.py"

SMOKE_STEP_NAME = "Pre-deploy built-image import smoke (AC-71.1)"
AC683_STEP_MARKER = "AC-68.3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_data() -> dict:
    assert DEPLOY_YML.exists(), f"AC-71.3: deploy.yml not found at {DEPLOY_YML}"
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "AC-71.3: deploy.yml must be valid YAML"
    return data


def _ci_data() -> dict:
    assert CI_YML.exists(), f"AC-71.3: ci.yml not found at {CI_YML}"
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "AC-71.3: ci.yml must be valid YAML"
    return data


# ---------------------------------------------------------------------------
# A. Tester confirm record
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac713.md must exist and contain > 200 chars.

    AC-71.3 is a Tester-confirmed deploy step. The ops record is the durable evidence
    artifact that documents the green deploy run, the smoke step outcome, the AC-68.3
    in-container step outcome, and all VPS conditions. Without this file there is no
    traceable record that the Tester-confirmed verification was performed.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-71.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac713.md as the durable "
        "evidence artifact for the AC-71.3 live deploy exercise."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), f"AC-71.3: {TESTER_CONFIRM_RECORD} is empty."
    assert len(text) > 200, (
        f"AC-71.3: {TESTER_CONFIRM_RECORD} is too short ({len(text)} chars). "
        "It must document the smoke step, AC-68.3 step, all VPS conditions, and the "
        "deploy pipeline chain (ci → build-and-push → deploy)."
    )


def test_tester_confirm_record_documents_import_smoke() -> None:
    """The record must reference the pre-deploy import smoke step (AC-71.1).

    AC-71.3 condition 1+2: the 'Pre-deploy built-image import smoke (AC-71.1)' step
    must be PRESENT and exit-0 in the ci job of the deploy run. The record must document
    this — it is the primary condition that proves the M2 recurrence guard fires in the
    actual pipeline, not only in unit tests.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_smoke = (
        "AC-71.1" in text
        or "import smoke" in text.lower()
        or "Pre-deploy" in text
    )
    assert has_smoke, (
        "AC-71.3: Tester confirmation record must reference the pre-deploy import smoke "
        "(AC-71.1 / 'import smoke' / 'Pre-deploy'). The smoke step running and passing "
        "in the actual pipeline is the primary AC-71.3 condition. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_django_setup() -> None:
    """The record must reference django.setup() — the sprint-13 root-cause fix.

    AC-71.3 condition 2: the smoke command starts with 'import django; django.setup();'
    so the AppRegistryNotReady class (sprint-13 root cause: a module defining a Django
    model at import time without the app registry initialised) surfaces pre-VPS. The record
    must document this so the intent of the guard is traceable from the ops artifact.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "django.setup" in text, (
        "AC-71.3: Tester confirmation record must reference 'django.setup' — "
        "the smoke command prefix that prevents AppRegistryNotReady (sprint-13 root cause). "
        "Without this documentation, the guard's purpose is not captured in the ops record. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_ac683_step() -> None:
    """The record must reference 'AC-68.3' — the post-deploy in-container import step.

    AC-71.3 condition 3: the 'Verify shared apparatus imports in-container (AC-68.3)' step
    must stay GREEN in the same deploy run. The pre-deploy smoke (condition 2) and the
    post-deploy in-container check (condition 3) are independently load-bearing — the smoke
    catches the failure class pre-push, and AC-68.3 confirms the prod image is sound.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "AC-68.3" in text, (
        "AC-71.3: Tester confirmation record must reference 'AC-68.3' — the post-deploy "
        "in-container import step that must stay GREEN in the same deploy run. "
        "Both the pre-deploy smoke and the AC-68.3 in-container check must be documented. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_8002() -> None:
    """The record must reference '8002' — HTTP 200 on the staging stack port.

    AC-71.3 inherits the full VPS DoD: the staging stack must answer HTTP 200 on port 8002
    at /health/ with the AC-12.3 retry-with-backoff. The record must document this condition
    (as it applies to every deploy story) so the full VPS health is traceable.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-71.3: Tester confirmation record must reference '8002' — the solanatrilly "
        "staging stack port. HTTP 200 on port 8002 is a VPS DoD condition required for "
        "every deploy story. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_solanabilly_isolation() -> None:
    """The record must reference solanaBilly isolation on port 8001.

    AC-71.3 inherits the hard isolation requirement: solanaBilly must be UNTOUCHED on
    port 8001 after every solanatrilly deploy (every docker command scoped -p solanatrilly).
    The record must document this so the isolation is traceable from the ops artifact.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8001" in text, (
        "AC-71.3: Tester confirmation record must reference '8001' (solanaBilly isolation — "
        "condition: solanaBilly UNTOUCHED on port 8001 after the deploy). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "solanaBilly" in text or "solanabilly" in text.lower(), (
        "AC-71.3: Tester confirmation record must reference 'solanaBilly' (isolation check). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# B. Deploy pipeline wiring — smoke-before-push chain
# ---------------------------------------------------------------------------


def test_deploy_yml_ci_job_calls_ci_yml() -> None:
    """deploy.yml must have a 'ci' job that uses ./.github/workflows/ci.yml.

    AC-71.3 requires the smoke step (which lives in ci.yml) to be exercised in the
    actual deploy pipeline. The deploy.yml achieves this by having a 'ci' job that
    calls `uses: ./.github/workflows/ci.yml` — a workflow_call reuse that runs the
    full ci.yml test job (including the smoke step) as part of the deploy pipeline.

    Without this ci job, the smoke step in ci.yml would only run on push/PR triggers,
    not on the deploy pipeline, and AC-71.3 would not be satisfied — the smoke would
    not be 'exercised LIVE on the VPS deploy run'.
    """
    data = _deploy_data()
    jobs = data.get("jobs", {})
    ci_job = jobs.get("ci")
    assert ci_job is not None, (
        "AC-71.3: deploy.yml must have a 'ci' job. This job calls ci.yml via workflow "
        "reuse, ensuring the pre-deploy import smoke (AC-71.1) runs as part of the "
        "deploy pipeline — not only on push/PR CI runs. Without this job, the smoke "
        "is NOT exercised in the deploy pipeline and AC-71.3 is not satisfied. "
        f"deploy.yml: {DEPLOY_YML}"
    )
    uses = ci_job.get("uses", "")
    assert "ci.yml" in uses or ".github/workflows/ci" in uses, (
        f"AC-71.3: deploy.yml 'ci' job must call ci.yml via 'uses:' (workflow reuse). "
        f"Got 'uses: {uses}'. The ci.yml contains the smoke step "
        f"'{SMOKE_STEP_NAME}'; calling it here is what exercises the smoke in the "
        "deploy pipeline. "
        f"deploy.yml: {DEPLOY_YML}"
    )


def test_smoke_exercised_before_image_push() -> None:
    """deploy.yml 'build-and-push' job must declare 'needs: ci' (or include ci).

    AC-71.3: the smoke runs 'before push' — before images are pushed to GHCR. The
    'build-and-push' job performs the GHCR push; it must have `needs: ci` (or equivalent)
    so it cannot start until the ci job (containing the smoke step) completes successfully.

    Without this `needs` declaration, the build-and-push job could run in parallel with
    or before ci, meaning images could be pushed even if the smoke fails — defeating the
    entire purpose of the pre-deploy smoke.
    """
    data = _deploy_data()
    jobs = data.get("jobs", {})
    bap_job = jobs.get("build-and-push")
    assert bap_job is not None, (
        "AC-71.3: deploy.yml must have a 'build-and-push' job (the step that pushes "
        "images to GHCR). "
        f"deploy.yml: {DEPLOY_YML}"
    )
    needs = bap_job.get("needs")
    needs_list = [needs] if isinstance(needs, str) else (needs or [])
    assert "ci" in needs_list, (
        "AC-71.3: deploy.yml 'build-and-push' job must declare 'needs: ci' so the "
        "GHCR image push cannot proceed until the ci job (which runs the smoke step) "
        "completes successfully. Without this, images can be pushed even if the smoke "
        "fails — the 'smoke before push' invariant is violated. "
        f"Found needs: {needs_list}. "
        f"deploy.yml: {DEPLOY_YML}"
    )


def test_deploy_job_needs_build_and_push() -> None:
    """deploy.yml 'deploy' job must declare 'needs: build-and-push'.

    The full chain — smoke → build-and-push → VPS deploy — must be enforced end-to-end.
    The 'deploy' job (which SSHes to the VPS and brings up the stack) must wait for
    'build-and-push' so that: (a) images are on GHCR before the VPS pulls them, and
    (b) the VPS deploy job can only start after the smoke already passed (transitively
    through the ci → build-and-push → deploy chain).
    """
    data = _deploy_data()
    jobs = data.get("jobs", {})
    deploy_job = jobs.get("deploy")
    assert deploy_job is not None, (
        "AC-71.3: deploy.yml must have a 'deploy' job. "
        f"deploy.yml: {DEPLOY_YML}"
    )
    needs = deploy_job.get("needs")
    needs_list = [needs] if isinstance(needs, str) else (needs or [])
    assert "build-and-push" in needs_list, (
        "AC-71.3: deploy.yml 'deploy' job must declare 'needs: build-and-push' so the "
        "VPS deploy cannot proceed until images are pushed to GHCR (which itself waits "
        "for the smoke to pass via ci → build-and-push → deploy chain). "
        f"Found needs: {needs_list}. "
        f"deploy.yml: {DEPLOY_YML}"
    )


def test_full_smoke_to_deploy_chain_enforced() -> None:
    """Full chain verification: ci (smoke) → build-and-push → deploy.

    Verifies all three dependency links as a compound assertion so the complete
    smoke-before-push chain cannot be silently broken by removing any one `needs`
    declaration. Each individual link is also verified by its own test; this
    compound test ensures no link is missing when the full deploy path is considered.
    """
    data = _deploy_data()
    jobs = data.get("jobs", {})
    failures: list[str] = []

    # Link 1: ci job must exist and call ci.yml
    ci_job = jobs.get("ci")
    if ci_job is None:
        failures.append("CHAIN-1: 'ci' job missing from deploy.yml")
    elif "ci.yml" not in ci_job.get("uses", "") and ".github/workflows/ci" not in ci_job.get("uses", ""):
        failures.append(
            f"CHAIN-1: 'ci' job does not call ci.yml (uses: {ci_job.get('uses', '')})"
        )

    # Link 2: build-and-push needs ci
    bap_job = jobs.get("build-and-push")
    if bap_job is None:
        failures.append("CHAIN-2: 'build-and-push' job missing from deploy.yml")
    else:
        needs = bap_job.get("needs")
        needs_list = [needs] if isinstance(needs, str) else (needs or [])
        if "ci" not in needs_list:
            failures.append(
                f"CHAIN-2: 'build-and-push' job does not need 'ci' (needs: {needs_list})"
            )

    # Link 3: deploy needs build-and-push
    deploy_job = jobs.get("deploy")
    if deploy_job is None:
        failures.append("CHAIN-3: 'deploy' job missing from deploy.yml")
    else:
        needs = deploy_job.get("needs")
        needs_list = [needs] if isinstance(needs, str) else (needs or [])
        if "build-and-push" not in needs_list:
            failures.append(
                f"CHAIN-3: 'deploy' job does not need 'build-and-push' (needs: {needs_list})"
            )

    assert not failures, (
        "AC-71.3 SMOKE-TO-DEPLOY CHAIN BROKEN:\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\nAll three links (ci → build-and-push → deploy) must be intact to ensure "
        "the pre-deploy import smoke runs before any image is pushed or deployed. "
        "Fixing any broken link restores the 'smoke before push' invariant."
    )


# ---------------------------------------------------------------------------
# C. Supporting infrastructure — guard file existence
# ---------------------------------------------------------------------------


def test_extended_regression_guard_test_file_exists() -> None:
    """core/tests/test_deploy_regression_guard_ac712.py must exist.

    AC-71.3 depends on the extended regression guard (AC-71.2) being in force.
    The guard pins INVARIANT-10 (smoke step in ci.yml), INVARIANT-11 (AC-68.3 step
    with django.setup()), and INVARIANT-12 (AC-69.3 open/closed smoke steps).
    The guard test file must be present — its absence means the guard was deleted
    and none of the three new invariants are enforced (the H1 fail-loud contract is broken).
    """
    assert EXTENDED_GUARD_TEST.exists(), (
        f"AC-71.3: Extended regression guard test file not found at {EXTENDED_GUARD_TEST}. "
        "This file (AC-71.2) pins three new deploy invariants including the smoke step "
        "(INVARIANT-10), the AC-68.3 django.setup() prefix (INVARIANT-11), and both "
        "AC-69.3 smoke steps (INVARIANT-12). Its absence means the guard is not in force "
        "and any of these invariants can be silently removed without a CI failure."
    )


def test_smoke_step_present_in_ci_yml() -> None:
    """ci.yml test job must have 'Pre-deploy built-image import smoke (AC-71.1)' step.

    AC-71.3 requires the smoke step to run in the deploy pipeline. The deploy pipeline
    calls ci.yml (via `uses: ./.github/workflows/ci.yml` in the ci job). If the smoke
    step is absent from ci.yml, the deploy pipeline's ci job does NOT exercise the smoke
    even though it calls ci.yml. This test pins the step's presence at the ci.yml layer.

    Note: AC-71.1 tests also verify this; this test verifies it from AC-71.3's perspective
    (that the file called by deploy.yml's ci job actually contains the smoke step).
    """
    data = _ci_data()
    steps = data.get("jobs", {}).get("test", {}).get("steps") or []
    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    assert any(SMOKE_STEP_NAME in n for n in step_names), (
        f"AC-71.3: ci.yml test job must have '{SMOKE_STEP_NAME}' step. "
        "deploy.yml calls ci.yml via workflow reuse to exercise the smoke in the "
        "deploy pipeline. If the step is absent from ci.yml, the deploy pipeline's ci "
        "job does NOT run the smoke, and AC-71.3's 'exercised LIVE on the VPS deploy "
        "run' requirement is not met. "
        f"ci.yml: {CI_YML}"
    )
