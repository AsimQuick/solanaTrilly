# ---
# module: core.tests.test_ci_merge_gate
# sprint: sprint-2
# story: US-3 AC-3.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, json, yaml
# ---
"""AC-3.3 — CI is a hard merge gate: the 'test' job runs pytest with the
>=80% coverage gate (--cov-fail-under=80) and must be green before merge,
enforced via the gitops orchestrator (require_ci_pass: true).

GitHub Free does not support branch protection on private repos (po-requests.md
item 1), so the gitops.json config carries the machine-readable enforcement
policy that the project-lead orchestrator reads before allowing a merge.

Tests:
  test_ci_test_job_has_coverage_gate
      Parses ci.yml, locates the 'test' job, inspects every 'run:' step, and
      asserts that at least one step invokes pytest with --cov-fail-under=<N>
      where N >= 80.  Fails if the flag is absent or the threshold is below 80.

  test_gitops_config_requires_ci_pass
      Reads gitops.json (repo root) and asserts require_ci_pass is True.
      This is the machine-readable enforcement policy the gitops orchestrator
      checks; if it is False or absent the orchestrator may merge without a
      green 'test' run, breaking the hard merge-gate guarantee.

  test_gitops_coverage_threshold_matches_ci
      Cross-checks that gitops.json's coverage_threshold matches the value
      asserted by the --cov-fail-under flag in ci.yml, preventing silent drift
      between the two sources of truth.
"""
import json
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GITOPS_JSON = REPO_ROOT / "gitops.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MINIMUM_COVERAGE = 80

# Matches --cov-fail-under=<digits> anywhere in a shell command string.
COV_FAIL_UNDER_RE = re.compile(r"--cov-fail-under[= ](\d+)")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_ci_yml() -> dict:
    """Parse ci.yml and return the document as a dict."""
    assert CI_YML.exists(), f"ci.yml not found at {CI_YML}"
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ci.yml did not parse to a dict"
    return data


def _extract_pytest_cov_thresholds(workflow: dict, job_id: str = "test") -> list[int]:
    """Return all --cov-fail-under=<N> values found in 'run:' steps of *job_id*.

    Returns a list of integers (may be empty if no pytest coverage step is found).
    """
    jobs = workflow.get("jobs", {})
    job = jobs.get(job_id)
    if not isinstance(job, dict):
        return []

    thresholds: list[int] = []
    for step in job.get("steps", []):
        if not isinstance(step, dict):
            continue
        run_cmd = step.get("run", "")
        if not isinstance(run_cmd, str):
            continue
        for match in COV_FAIL_UNDER_RE.finditer(run_cmd):
            thresholds.append(int(match.group(1)))
    return thresholds


def _load_gitops() -> dict:
    """Load and return gitops.json as a dict."""
    assert GITOPS_JSON.exists(), (
        f"gitops.json not found at {GITOPS_JSON}. "
        "AC-3.3 requires a gitops.json at the repo root with "
        "'require_ci_pass': true so the orchestrator enforces the merge gate."
    )
    return json.loads(GITOPS_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ci_test_job_has_coverage_gate() -> None:
    """The 'test' job in ci.yml must run pytest with --cov-fail-under>=80.

    Parses ci.yml and asserts that the 'test' job contains at least one 'run:'
    step that invokes pytest with --cov-fail-under=<N> where N >= 80.

    This ensures coverage below 80% causes the CI 'test' job to fail, making
    it impossible for an under-covered PR to be green (AC-3.3 / PRD §12 H1).
    """
    workflow = _load_ci_yml()

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml does not define a job named 'test'. "
        "AC-3.3 requires the merge-gate job to be named 'test' so the gitops "
        "orchestrator and GitHub status checks can reference it by name."
    )

    thresholds = _extract_pytest_cov_thresholds(workflow, job_id="test")

    assert thresholds, (
        "AC-3.3 violation: the 'test' job in ci.yml has no 'run:' step that "
        "invokes pytest with --cov-fail-under=<N>.\n\n"
        "Add --cov-fail-under=80 (or higher) to the pytest invocation in the "
        "'test' job so coverage below 80% fails the job and blocks the merge."
    )

    below_threshold = [t for t in thresholds if t < MINIMUM_COVERAGE]
    assert not below_threshold, (
        f"AC-3.3 violation: --cov-fail-under threshold(s) {below_threshold} "
        f"in ci.yml's 'test' job are below the required minimum of "
        f"{MINIMUM_COVERAGE}%.\n\n"
        f"Raise every --cov-fail-under value to at least {MINIMUM_COVERAGE}."
    )


def test_gitops_config_requires_ci_pass() -> None:
    """gitops.json must exist at the repo root and set require_ci_pass to true.

    The gitops orchestrator reads gitops.json before merging any PR.  If
    require_ci_pass is True the orchestrator polls the GitHub Checks API and
    refuses to merge until the 'test' job is green.  Without this flag a red
    CI run can be silently ignored, breaking the hard merge-gate guarantee.

    Since GitHub Free does not support branch protection on private repos
    (po-requests.md item 1), this config is the sole enforcement mechanism.
    """
    config = _load_gitops()

    assert config.get("require_ci_pass") is True, (
        "AC-3.3 violation: gitops.json does not set 'require_ci_pass' to true.\n\n"
        f"Current value: {config.get('require_ci_pass')!r}\n\n"
        "Set 'require_ci_pass': true in gitops.json so the orchestrator "
        "enforces CI as a hard merge gate (po-requests.md item 1)."
    )


def test_gitops_coverage_threshold_matches_ci() -> None:
    """gitops.json's coverage_threshold must match --cov-fail-under in ci.yml.

    Prevents silent drift between the two sources of truth: if ci.yml is
    updated to --cov-fail-under=90 but gitops.json still says 80, the
    orchestrator's enforcement policy is inconsistent with the CI gate.
    """
    config = _load_gitops()
    workflow = _load_ci_yml()

    gitops_threshold = config.get("coverage_threshold")
    assert gitops_threshold is not None, (
        "AC-3.3 violation: gitops.json is missing 'coverage_threshold'. "
        "Add 'coverage_threshold': 80 to keep it in sync with ci.yml."
    )

    ci_thresholds = _extract_pytest_cov_thresholds(workflow, job_id="test")
    assert ci_thresholds, (
        "AC-3.3 violation: no --cov-fail-under value found in ci.yml's 'test' "
        "job. Cannot cross-check against gitops.json's coverage_threshold."
    )

    for ci_value in ci_thresholds:
        assert ci_value == gitops_threshold, (
            f"AC-3.3 violation: coverage threshold mismatch.\n"
            f"  ci.yml --cov-fail-under: {ci_value}\n"
            f"  gitops.json coverage_threshold: {gitops_threshold}\n\n"
            "Keep both values in sync so the CI gate and the orchestrator "
            "policy agree on the required coverage floor."
        )
