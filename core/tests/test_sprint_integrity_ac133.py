# ---
# module: core.tests.test_sprint_integrity_ac133
# sprint: sprint-4
# story: US-13 AC-13.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOWS_DIR = CI_YML.parent


def _load_ci():
    return yaml.safe_load(CI_YML.read_text())


def _test_job_steps():
    return _load_ci().get("jobs", {}).get("test", {}).get("steps", [])


def _sprint_integrity_step():
    for step in _test_job_steps():
        if "sprint_integrity_check" in step.get("run", ""):
            return step
    return None


def test_ci_yml_exists():
    assert CI_YML.exists(), f"ci.yml not found at {CI_YML}"


def test_single_canonical_workflow_no_extra_files():
    """H1: do NOT add a second workflow file — only ci.yml and deploy.yml allowed."""
    names = {f.name for f in WORKFLOWS_DIR.glob("*.yml")}
    extra = names - {"ci.yml", "deploy.yml"}
    assert not extra, f"Extra workflow file(s) violate H1: {extra}"


def test_sprint_integrity_step_present_in_ci():
    """The sprint integrity check step must exist in the 'test' job."""
    assert _sprint_integrity_step() is not None, (
        "No sprint integrity check step found in ci.yml 'test' job"
    )


def test_sprint_integrity_step_name_is_descriptive():
    step = _sprint_integrity_step()
    assert step is not None
    name = step.get("name", "").lower()
    assert "sprint" in name or "integrity" in name, (
        f"Step name '{step.get('name')}' should mention 'sprint' or 'integrity'"
    )


def test_sprint_integrity_step_invokes_validator_script():
    step = _sprint_integrity_step()
    assert step is not None
    run = step.get("run", "")
    assert "sprint_integrity_check.py" in run, (
        f"Step must invoke sprint_integrity_check.py; got: {run!r}"
    )


def test_sprint_integrity_step_covers_sprint_json_files():
    step = _sprint_integrity_step()
    assert step is not None
    run = step.get("run", "")
    assert "sprint" in run and ".json" in run, (
        f"Step must reference sprint*.json files; got: {run!r}"
    )


def test_sprint_integrity_step_covers_scrum_master_dir():
    """The step must target files under scrum-master/ — not some other directory."""
    step = _sprint_integrity_step()
    assert step is not None
    run = step.get("run", "")
    assert "scrum-master" in run, (
        f"Step must reference scrum-master/ directory; got: {run!r}"
    )


def test_sprint_integrity_step_is_in_test_job():
    """The step is in the 'test' job (the single canonical CI job)."""
    data = _load_ci()
    steps = data.get("jobs", {}).get("test", {}).get("steps", [])
    found = any("sprint_integrity_check" in s.get("run", "") for s in steps)
    assert found, "Sprint integrity step must be inside the 'test' job"


def test_sprint_integrity_step_appears_before_teardown():
    """The step runs before teardown so violations fail the build."""
    steps = _test_job_steps()
    integrity_idx = next(
        (i for i, s in enumerate(steps) if "sprint_integrity_check" in s.get("run", "")),
        None,
    )
    teardown_idx = next(
        (i for i, s in enumerate(steps) if "teardown" in s.get("name", "").lower()),
        None,
    )
    assert integrity_idx is not None, "Sprint integrity step not found"
    assert teardown_idx is None or integrity_idx < teardown_idx, (
        "Sprint integrity step must appear before Teardown"
    )


def test_sprint_integrity_step_comes_after_checkout():
    """The step runs after checkout (files must be on disk first)."""
    steps = _test_job_steps()
    checkout_idx = next(
        (i for i, s in enumerate(steps) if "checkout" in str(s.get("uses", "")).lower()),
        None,
    )
    integrity_idx = next(
        (i for i, s in enumerate(steps) if "sprint_integrity_check" in s.get("run", "")),
        None,
    )
    assert checkout_idx is not None, "checkout step not found in ci.yml"
    assert integrity_idx is not None, "Sprint integrity step not found"
    assert integrity_idx > checkout_idx, (
        "Sprint integrity step must come after the checkout step"
    )
