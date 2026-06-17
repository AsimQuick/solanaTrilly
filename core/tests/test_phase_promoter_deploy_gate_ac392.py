# ---
# file: core/tests/test_phase_promoter_deploy_gate_ac392.py
# project: solanatrilly
# purpose: AC-39.2 — structural test proving the F2 phase-promoter is wired into the deploy sequence
# story: US-39 AC-39.2
# sprint: sprint-8
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: yaml, pathlib
# ---
"""AC-39.2 — G4: phase-promoter proven in the actual deploy sequence.

Verifies:
  (a) deploy.yml's `deploy` job has an explicit step running promote_sprint_phase.py
      BEFORE the VPS deploy step — the promoter is wired in, not just a standalone tool.
  (b) ci.yml also runs the promoter (it is called by deploy.yml's `ci` job), so the
      promoter fires at two points in the pipeline: the CI gate and the deploy gate.

Part (b) of the AC (the sprint-8 sprint-end deploy run showing the promoter actually fired)
is inherently a runtime observation made during the actual deploy; the structural assertions
here are the testable proxy that proves the wiring is in place so it WILL fire.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_workflow(name: str) -> dict:
    path = REPO_ROOT / ".github" / "workflows" / name
    assert path.exists(), f"{name} missing from .github/workflows/"
    with path.open() as f:
        return yaml.safe_load(f)


def _get_steps(workflow: dict, job_name: str) -> list[dict]:
    jobs = workflow.get("jobs", {})
    assert job_name in jobs, f"Job '{job_name}' not found in workflow; found: {list(jobs)}"
    return [s for s in jobs[job_name].get("steps", []) if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# Structural tests — deploy.yml deploy job has the promoter step
# ---------------------------------------------------------------------------


def test_deploy_workflow_deploy_job_has_promoter_step():
    """deploy.yml's `deploy` job must have a step that invokes promote_sprint_phase.py."""
    deploy_wf = _load_workflow("deploy.yml")
    steps = _get_steps(deploy_wf, "deploy")
    step_names = [s.get("name", "") for s in steps]

    promoter_steps = [
        s for s in steps
        if "promote_sprint_phase" in s.get("run", "")
    ]
    assert promoter_steps, (
        f"deploy.yml `deploy` job must have a step running promote_sprint_phase.py; "
        f"found steps: {step_names}"
    )


def test_deploy_workflow_promoter_step_named_with_ac392():
    """The promoter step in deploy.yml's deploy job must reference AC-39.2 in its name."""
    deploy_wf = _load_workflow("deploy.yml")
    steps = _get_steps(deploy_wf, "deploy")

    matching = [
        s for s in steps
        if "promote_sprint_phase" in s.get("run", "") and "39.2" in s.get("name", "")
    ]
    assert matching, (
        "The promoter step in deploy.yml's deploy job must include '39.2' in its name; "
        "no such step found"
    )


def test_deploy_workflow_promoter_step_before_vps_deploy():
    """The promoter step must appear BEFORE the 'Deploy to VPS' step in deploy.yml's deploy job."""
    deploy_wf = _load_workflow("deploy.yml")
    steps = _get_steps(deploy_wf, "deploy")

    promoter_idx = next(
        (i for i, s in enumerate(steps) if "promote_sprint_phase" in s.get("run", "")),
        None,
    )
    vps_deploy_idx = next(
        (i for i, s in enumerate(steps) if "Deploy to VPS" in s.get("name", "")),
        None,
    )

    assert promoter_idx is not None, (
        "No promoter step found in deploy.yml's deploy job"
    )
    assert vps_deploy_idx is not None, (
        "No 'Deploy to VPS' step found in deploy.yml's deploy job"
    )
    assert promoter_idx < vps_deploy_idx, (
        f"Promoter step (index {promoter_idx}) must appear before the VPS deploy step "
        f"(index {vps_deploy_idx})"
    )


def test_deploy_workflow_promoter_step_runs_sprint_json():
    """The promoter step must pass sprint*.json glob to promote_sprint_phase.py."""
    deploy_wf = _load_workflow("deploy.yml")
    steps = _get_steps(deploy_wf, "deploy")

    promoter_steps = [
        s for s in steps
        if "promote_sprint_phase" in s.get("run", "")
    ]
    assert promoter_steps, "No promoter step in deploy.yml's deploy job"

    run_cmd = promoter_steps[0].get("run", "")
    assert "sprint" in run_cmd and ".json" in run_cmd, (
        f"Promoter step must pass sprint*.json to the script; got: {run_cmd!r}"
    )


# ---------------------------------------------------------------------------
# Structural tests — ci.yml also has the promoter (called from deploy.yml's ci job)
# ---------------------------------------------------------------------------


def test_ci_workflow_has_promoter_step():
    """ci.yml must have the promoter step (it is called by deploy.yml's `ci` job)."""
    ci_wf = _load_workflow("ci.yml")
    steps = _get_steps(ci_wf, "test")
    step_names = [s.get("name", "") for s in steps]

    promoter_steps = [
        s for s in steps
        if "promote_sprint_phase" in s.get("run", "")
    ]
    assert promoter_steps, (
        f"ci.yml must have a step running promote_sprint_phase.py; "
        f"found steps: {step_names}"
    )


def test_deploy_workflow_ci_job_calls_ci_yml():
    """deploy.yml must have a `ci` job that calls .github/workflows/ci.yml."""
    deploy_wf = _load_workflow("deploy.yml")
    jobs = deploy_wf.get("jobs", {})
    assert "ci" in jobs, f"deploy.yml must have a `ci` job; found: {list(jobs)}"

    ci_job = jobs["ci"]
    uses = ci_job.get("uses", "")
    assert "ci.yml" in uses, (
        f"deploy.yml's `ci` job must call ci.yml via `uses`; got: {uses!r}"
    )


def test_deploy_workflow_deploy_job_needs_build_after_ci():
    """deploy.yml's `deploy` job must depend on `build-and-push` which needs `ci`."""
    deploy_wf = _load_workflow("deploy.yml")
    jobs = deploy_wf.get("jobs", {})

    build_job = jobs.get("build-and-push", {})
    build_needs = build_job.get("needs", [])
    if isinstance(build_needs, str):
        build_needs = [build_needs]
    assert "ci" in build_needs, (
        f"`build-and-push` job must need `ci`; got needs: {build_needs}"
    )

    deploy_job = jobs.get("deploy", {})
    deploy_needs = deploy_job.get("needs", [])
    if isinstance(deploy_needs, str):
        deploy_needs = [deploy_needs]
    assert "build-and-push" in deploy_needs, (
        f"`deploy` job must need `build-and-push`; got needs: {deploy_needs}"
    )
