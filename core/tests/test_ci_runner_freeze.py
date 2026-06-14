# ---
# module: core.tests.test_ci_runner_freeze
# sprint: sprint-2
# story: US-3 AC-3.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-3.2 — CI runner frozen to a specific OS image; ci.yml is the single
canonical workflow (one source of CI truth, no parallel/duplicate workflows).

Tests:
  test_no_latest_runner_in_any_workflow
      Scans all workflow files under .github/workflows/ and asserts that no
      job's 'runs-on' value contains the string 'latest'.  A *-latest runner
      (ubuntu-latest, windows-latest, macos-latest, etc.) ties builds to a
      moving target: the CI image can change silently under a pinned SHA,
      reintroducing the class of drift this story prevents.  The only
      acceptable runner strings are explicit versioned images such as
      'ubuntu-24.04'.

  test_single_workflow_defines_test_job
      Asserts that exactly one workflow file under .github/workflows/ contains
      a job with the id 'test'.  Duplicate or parallel workflows that also
      define a 'test' job create split-brain CI: two different run histories,
      two different status checks, and no single source of truth for the merge
      gate defined by AC-3.3.

  Rationale (AC-3.2 / PRD §12 S5/H1):
    The Node-24 CI flail (S5) was partly caused by the runner image changing
    without any code change triggering the breakage.  Freezing the OS image to
    an explicit version string (ubuntu-24.04) makes the environment as
    reproducible as the pinned action SHAs from AC-3.1.
"""
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_workflow_files() -> list[Path]:
    """Return sorted list of all *.yml and *.yaml files under .github/workflows/."""
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _load_workflow(path: Path) -> dict:
    """Parse *path* as YAML and return the document dict (empty dict on failure)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _runs_on_values(workflow: dict) -> list[tuple[str, object]]:
    """Return all (job_id, runs_on_value) pairs from *workflow*.

    Handles the three legal forms for 'runs-on':
      - simple string: runs-on: ubuntu-24.04
      - matrix expression: runs-on: ${{ matrix.os }}
      - list (self-hosted): runs-on: [self-hosted, linux]
    """
    results: list[tuple[str, object]] = []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return results
    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        runs_on = job_def.get("runs-on")
        if runs_on is not None:
            results.append((job_id, runs_on))
    return results


def _contains_latest(runs_on_value: object) -> bool:
    """Return True if *runs_on_value* contains the substring 'latest'.

    Converts list values to a single string for substring search.
    """
    if isinstance(runs_on_value, list):
        combined = " ".join(str(item) for item in runs_on_value)
        return "latest" in combined.lower()
    return "latest" in str(runs_on_value).lower()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_latest_runner_in_any_workflow() -> None:
    """No job in any workflow file may use a *-latest runner image.

    A versioned image (e.g. ubuntu-24.04) must be used instead.  This
    ensures the CI environment is as frozen as the action SHAs from AC-3.1
    and prevents silent OS-image drift from breaking builds.
    """
    workflow_files = _collect_workflow_files()
    assert workflow_files, (
        f"No workflow files found under {WORKFLOWS_DIR}. "
        "Expected at least one *.yml or *.yaml file."
    )

    violations: list[str] = []

    for wf_path in workflow_files:
        workflow = _load_workflow(wf_path)
        rel = wf_path.relative_to(REPO_ROOT)
        for job_id, runs_on_value in _runs_on_values(workflow):
            if _contains_latest(runs_on_value):
                violations.append(
                    f"  {rel} (job '{job_id}'): runs-on: {runs_on_value!r} "
                    f"— contains '*-latest'"
                )

    assert not violations, (
        "AC-3.2 violation: the following jobs use a *-latest runner image.\n\n"
        "Replace with an explicit versioned image, e.g.:\n"
        "  runs-on: ubuntu-24.04\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_single_workflow_defines_test_job() -> None:
    """Exactly one workflow file must define a job with id 'test'.

    ci.yml is the single canonical CI workflow (one source of CI truth).
    If two workflow files both define a 'test' job there are two competing
    status checks and no authoritative merge gate, violating AC-3.2 and
    undermining AC-3.3.
    """
    workflow_files = _collect_workflow_files()
    assert workflow_files, (
        f"No workflow files found under {WORKFLOWS_DIR}. "
        "Expected at least one *.yml or *.yaml file."
    )

    files_with_test_job: list[str] = []

    for wf_path in workflow_files:
        workflow = _load_workflow(wf_path)
        jobs = workflow.get("jobs")
        if isinstance(jobs, dict) and "test" in jobs:
            files_with_test_job.append(str(wf_path.relative_to(REPO_ROOT)))

    assert len(files_with_test_job) == 1, (
        "AC-3.2 violation: exactly one workflow file must define a job named "
        f"'test', but found {len(files_with_test_job)}.\n\n"
        + (
            "No workflow file defines a 'test' job — ci.yml must define one."
            if not files_with_test_job
            else "Files defining a 'test' job:\n"
            + "\n".join(f"  {f}" for f in files_with_test_job)
            + "\n\nRemove duplicate 'test' job definitions so ci.yml is the "
            "single canonical CI workflow."
        )
    )
