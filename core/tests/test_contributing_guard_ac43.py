# ---
# module: core.tests.test_contributing_guard_ac43
# sprint: sprint-2
# story: US-4 AC-4.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-4.3 — CONTRIBUTING.md dev workflow guard and CI manifest gate verification.

These tests assert:
1. CONTRIBUTING.md exists at the repo root.
2. CONTRIBUTING.md contains the required 'git show --stat HEAD' step.
3. CONTRIBUTING.md contains a warning against 'git stash' (the S6 scar guard).
4. The CI 'test' job has a 'run:' step containing 'pytest', proving the manifest
   tests are picked up by the CI merge gate via pytest auto-discovery.
"""
from pathlib import Path

import yaml

# Repo root is three levels up from this file:
# core/tests/test_contributing_guard_ac43.py -> core/tests -> core -> repo-root
REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_contributing_md_exists():
    """Assert CONTRIBUTING.md exists at the repo root."""
    assert CONTRIBUTING_PATH.exists(), (
        f"CONTRIBUTING.md not found at expected path: {CONTRIBUTING_PATH}\n"
        "Create CONTRIBUTING.md at the repo root documenting the dev workflow guard."
    )


def test_contributing_md_contains_git_show_stat_step():
    """Assert CONTRIBUTING.md contains the literal text 'git show --stat HEAD'.

    This step is required by AC-4.3: developers must run 'git show --stat HEAD'
    before every push to verify that the staged content is what they intended.
    """
    assert CONTRIBUTING_PATH.exists(), (
        "CONTRIBUTING.md not found — cannot check for 'git show --stat HEAD'."
    )
    content = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "git show --stat HEAD" in content, (
        "CONTRIBUTING.md does not contain 'git show --stat HEAD'.\n"
        "The dev workflow guard must document this step so developers verify "
        "their commit content before every push."
    )


def test_contributing_md_warns_against_git_stash():
    """Assert CONTRIBUTING.md contains 'git stash' (as an explicit warning against it).

    AC-4.3 requires a warning: never run 'git stash' between 'git add' and
    'git commit' (S6 scars — this caused a task registration to be silently
    missing from a commit, causing the manifest to diverge from the registry).
    """
    assert CONTRIBUTING_PATH.exists(), (
        "CONTRIBUTING.md not found — cannot check for 'git stash' warning."
    )
    content = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "git stash" in content, (
        "CONTRIBUTING.md does not contain 'git stash'.\n"
        "The dev workflow guard must warn against running 'git stash' between "
        "'git add' and 'git commit' (S6 scar prevention)."
    )


def test_ci_runs_manifest_test_via_pytest():
    """Assert ci.yml's 'test' job has a 'run:' step containing 'pytest'.

    The manifest tests (AC-4.1 and AC-4.2) are picked up by pytest auto-discovery.
    This test proves they run inside the CI merge gate by verifying that the 'test'
    job invokes pytest in at least one 'run:' step.
    """
    assert CI_WORKFLOW_PATH.exists(), (
        f"ci.yml not found at: {CI_WORKFLOW_PATH}"
    )
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        f"No 'test' job found in ci.yml. Jobs present: {list(jobs.keys())}"
    )

    test_job = jobs["test"]
    steps = test_job.get("steps", [])
    assert steps, "The 'test' job in ci.yml has no steps."

    pytest_steps = [
        step for step in steps
        if isinstance(step.get("run"), str) and "pytest" in step["run"]
    ]
    assert pytest_steps, (
        "The 'test' job in ci.yml has no 'run:' step containing 'pytest'.\n"
        "The manifest tests must be picked up by pytest auto-discovery inside CI.\n"
        f"Steps found: {[step.get('name', step.get('run', '?')) for step in steps]}"
    )
