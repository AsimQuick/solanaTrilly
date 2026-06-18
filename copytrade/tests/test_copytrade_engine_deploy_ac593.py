# ---
# module: copytrade.tests.test_copytrade_engine_deploy_ac593
# sprint: sprint-12
# story: US-59 AC-59.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, yaml, pathlib
# ---
"""AC-59.3 — Structural deploy guard: copytrade_engine container Up on the VPS staging stack.

AC text:
  The copytrade_engine is deployed to the VPS solanatrilly staging stack and
  Tester-CONFIRMED from an ACTUAL green deploy run: the 'copytrade_engine' container
  is Up (docker compose -p solanatrilly ps), HTTP 200 on 8002 still holds (AC-12.3
  retry-with-backoff), the existing listener/web/celery-worker/frontend containers
  remain Up (the new engine does not disturb them — §5 isolation), and solanaBilly
  is UNTOUCHED on 8001. The nine-invariant deploy regression guard (sprint-11 AC-54.2)
  stays green. New/changed files carry metadata front matter.

Tests in this module:

  H1 import trap:
      Imports Command from copytrade.management.commands.run_copytrade_engine at
      module level — fails pytest collection if the management command is deleted.

  test_deploy_yml_has_copytrade_engine_container_check:
      deploy.yml has a step named/containing 'copytrade_engine' and 'AC-59.3' that
      verifies the container is Up; the step greps for solanatrilly.copytrade.engine
      or copytrade_engine in ps output and checks for running/up state.

  test_deploy_yml_copytrade_engine_step_scoped_solanatrilly:
      The new copytrade_engine check step uses -p solanatrilly (isolation).

  test_deploy_yml_copytrade_engine_step_exits_on_failure:
      The copytrade_engine check step has exit 1 on failure.

  test_staging_compose_has_copytrade_engine_service:
      docker-compose.staging.yml contains the copytrade_engine service
      (pinned deployment requirement).

  test_copytrade_engine_step_ordered_after_deploy:
      The copytrade_engine container check step appears AFTER the
      'Deploy to VPS staging stack' step in the deploy job.

  test_nine_invariant_guard_file_exists:
      The nine-invariant guard test file
      core/tests/test_deploy_regression_guard_ac542.py still exists.

  test_nine_invariant_guard_has_all_nine_tests:
      The nine-invariant guard file contains all nine individual invariant
      test functions.
"""

from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# H1 import trap — fails pytest collection if the management command is deleted
# ---------------------------------------------------------------------------
from copytrade.management.commands.run_copytrade_engine import Command  # noqa: F401

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
NINE_INVARIANT_GUARD = REPO_ROOT / "core" / "tests" / "test_deploy_regression_guard_ac542.py"

VPS_DEPLOY_STEP_NAME = "Deploy to VPS staging stack"
COPYTRADE_ENGINE_STEP_MARKER = "AC-59.3"

# The nine individual invariant test function names pinned by AC-54.2
_NINE_INVARIANT_TESTS = [
    "test_invariant_remove_orphans_in_up_command",
    "test_invariant_down_remove_orphans_before_up",
    "test_invariant_phase_promoter_step_exists",
    "test_invariant_phase_promoter_ordered_before_vps_deploy",
    "test_invariant_smoke_test_retry_with_backoff",
    "test_invariant_workflow_call_gate_no_inline_pytest",
    "test_invariant_rfc6455_valid_ws_key",
    "test_invariant_disk_exhaustion_fix",
    "test_invariant_push_trigger_main",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-59.3: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-59.3: deploy.yml must be valid YAML"
    return data


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _copytrade_engine_step() -> dict | None:
    """Return the step that checks the copytrade_engine container, or None."""
    for step in _deploy_job_steps():
        name = str(step.get("name", ""))
        run = str(step.get("run", ""))
        if COPYTRADE_ENGINE_STEP_MARKER in name or (
            "copytrade_engine" in name.lower() and "Up" in name
        ):
            return step
        # Also accept if the run block contains the AC marker and copytrade_engine check
        if COPYTRADE_ENGINE_STEP_MARKER in run and "copytrade_engine" in run:
            return step
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deploy_yml_has_copytrade_engine_container_check() -> None:
    """deploy.yml must have a step that checks the copytrade_engine container is Up.

    The step must:
    - Have 'AC-59.3' in its name or run block
    - Grep for 'solanatrilly.copytrade.engine' or 'copytrade_engine' in ps output
    - Check for running/up state
    """
    step = _copytrade_engine_step()
    assert step is not None, (
        "AC-59.3: No step checking the copytrade_engine container found in deploy.yml. "
        f"Expected a step with 'AC-59.3' in the name and 'copytrade_engine' in the run block. "
        f"Steps present: {[s.get('name', '') for s in _deploy_job_steps()]}"
    )
    run = str(step.get("run", ""))
    # The step must grep for the container name (either dot-separated or underscore)
    assert ("solanatrilly.copytrade.engine" in run or "copytrade_engine" in run), (
        "AC-59.3: The copytrade_engine check step does not grep for the container name "
        "('solanatrilly.copytrade.engine' or 'copytrade_engine') in the ps output. "
        f"Step run block:\n{run}"
    )
    # The step must check for running/up state
    assert "running" in run.lower() or "up" in run.lower(), (
        "AC-59.3: The copytrade_engine check step does not check for running/up state. "
        f"Step run block:\n{run}"
    )


def test_deploy_yml_copytrade_engine_step_scoped_solanatrilly() -> None:
    """The copytrade_engine check step must use -p solanatrilly (isolation).

    Hard isolation rule: ALL docker commands on the VPS MUST be scoped
    with -p solanatrilly. An unscoped command could affect solanaBilly.
    """
    step = _copytrade_engine_step()
    assert step is not None, (
        "AC-59.3: Cannot verify -p solanatrilly scoping — no copytrade_engine check step found."
    )
    run = str(step.get("run", ""))
    assert "-p solanatrilly" in run, (
        "AC-59.3 VIOLATED: The copytrade_engine container check step does NOT use "
        "'-p solanatrilly'. All docker compose commands must be scoped to the "
        "solanatrilly project to preserve hard isolation from solanaBilly (PRD §15.3). "
        f"Step run block:\n{run}"
    )


def test_deploy_yml_copytrade_engine_step_exits_on_failure() -> None:
    """The copytrade_engine check step must exit 1 on failure (not a no-op check).

    An H1/fail-loud pattern: if the container is not Up, the step must fail
    the deploy pipeline (exit 1), never silently pass.
    """
    step = _copytrade_engine_step()
    assert step is not None, (
        "AC-59.3: Cannot verify exit-on-failure — no copytrade_engine check step found."
    )
    run = str(step.get("run", ""))
    assert "exit 1" in run, (
        "AC-59.3 VIOLATED: The copytrade_engine container check step does NOT contain "
        "'exit 1'. Without an explicit failure exit code, a container that is not Up "
        "would silently pass the deploy gate. The step must 'exit 1' when the container "
        "is not in running/up state (H1 fail-loud pattern). "
        f"Step run block:\n{run}"
    )


def test_staging_compose_has_copytrade_engine_service() -> None:
    """docker-compose.staging.yml must define the copytrade_engine service.

    This pins the deployment requirement: the service must be present in the
    staging compose so that the VPS deploy brings it up.
    """
    assert STAGING_COMPOSE.exists(), (
        f"AC-59.3: docker-compose.staging.yml not found at {STAGING_COMPOSE}"
    )
    data = yaml.safe_load(STAGING_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "AC-59.3: docker-compose.staging.yml must be valid YAML"
    services = data.get("services") or {}
    assert "copytrade_engine" in services, (
        "AC-59.3: 'copytrade_engine' service is NOT present in docker-compose.staging.yml. "
        "The copytrade_engine container cannot be Up on the VPS if it is not defined in "
        "the staging compose file. "
        f"Services currently defined: {list(services.keys())}"
    )


def test_copytrade_engine_step_ordered_after_deploy() -> None:
    """The copytrade_engine container check must appear AFTER 'Deploy to VPS staging stack'.

    Rationale: the container cannot be verified before it is deployed. A check
    step that appears before the deploy step would always test the PREVIOUS
    deploy's state, not the current one.
    """
    steps = _deploy_job_steps()
    step_names = [s.get("name", "") for s in steps]

    vps_deploy_idx = next(
        (i for i, s in enumerate(steps) if VPS_DEPLOY_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert vps_deploy_idx is not None, (
        f"AC-59.3: '{VPS_DEPLOY_STEP_NAME}' step not found in deploy.yml deploy job. "
        f"Steps present: {step_names}"
    )

    copytrade_idx = next(
        (i for i, s in enumerate(steps)
         if COPYTRADE_ENGINE_STEP_MARKER in str(s.get("name", ""))
         or (
             "copytrade_engine" in str(s.get("name", "")).lower()
             and "up" in str(s.get("name", "")).lower()
         )
         or (
             COPYTRADE_ENGINE_STEP_MARKER in str(s.get("run", ""))
             and "copytrade_engine" in str(s.get("run", ""))
         )),
        None,
    )
    assert copytrade_idx is not None, (
        "AC-59.3: copytrade_engine container check step not found in deploy.yml deploy job. "
        f"Steps present: {step_names}"
    )

    assert copytrade_idx > vps_deploy_idx, (
        f"AC-59.3 VIOLATED: The copytrade_engine check step (index {copytrade_idx}) "
        f"appears BEFORE '{VPS_DEPLOY_STEP_NAME}' (index {vps_deploy_idx}). "
        "The container cannot be checked before it is deployed — the check would read "
        "the previous deploy's state rather than the current run's. "
        f"Step order: {step_names}"
    )


def test_nine_invariant_guard_file_exists() -> None:
    """The nine-invariant deploy regression guard file (AC-54.2) must still exist.

    This test pins the presence of the guard file so it cannot be silently
    deleted without breaking the CI suite. The nine-invariant guard is a
    load-bearing safety net for the entire deploy path (sprint-11 AC-54.2).
    """
    assert NINE_INVARIANT_GUARD.exists(), (
        "AC-59.3: The nine-invariant deploy regression guard file is MISSING: "
        f"{NINE_INVARIANT_GUARD}. "
        "This file (sprint-11 AC-54.2) pins all nine load-bearing deploy invariants. "
        "Its removal would allow the deploy path to silently regress on any of the "
        "nine pinned invariants without breaking CI. Restore the file immediately."
    )


def test_nine_invariant_guard_has_all_nine_tests() -> None:
    """The nine-invariant guard file must still contain all nine invariant test functions.

    Each of the nine test functions is a named, load-bearing pin. Deleting any
    one of them reduces the guard to eight invariants — the missing invariant's
    regression would go undetected. This test verifies all nine are present.
    """
    assert NINE_INVARIANT_GUARD.exists(), (
        f"AC-59.3: Nine-invariant guard file not found at {NINE_INVARIANT_GUARD}. "
        "Cannot check for the nine test functions."
    )
    guard_text = NINE_INVARIANT_GUARD.read_text(encoding="utf-8")
    missing = [fn for fn in _NINE_INVARIANT_TESTS if f"def {fn}" not in guard_text]
    assert not missing, (
        "AC-59.3: The following nine-invariant guard test functions are MISSING from "
        f"{NINE_INVARIANT_GUARD}:\n"
        + "\n".join(f"  • {fn}" for fn in missing)
        + "\n\nEach function pins a specific deploy invariant. A missing function means "
        "the corresponding invariant is no longer enforced by CI. Restore all nine "
        "test functions (sprint-11 AC-54.2)."
    )
