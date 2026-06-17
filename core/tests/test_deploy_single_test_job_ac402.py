# ---
# file: core/tests/test_deploy_single_test_job_ac402.py
# project: solanatrilly
# purpose: AC-40.2 — structural YAML test verifying the H1 single-canonical-test-job
#          invariant: deploy.yml has no inline test job and the deploy step depends
#          on the canonical ci.yml 'test' job; AC-39.2 phase-promoter gate and
#          AC-12.3 smoke-test retry-with-backoff are preserved
# story: US-40 AC-40.2
# sprint: sprint-9
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, yaml
# ---
"""AC-40.2 — H1 structural verification: one canonical test job; deploy gated on it.

H1 principle ('single canonical test job — no second workflow'):
  There is EXACTLY ONE test job definition — the 'test' job in ci.yml.
  deploy.yml MUST NOT define a second / divergent inline test job.
  The deploy step MUST be gated on the canonical ci.yml test via a dependency chain.

Structural fix already in deploy.yml (verified here):
  - A job in deploy.yml calls ci.yml via `uses: ./.github/workflows/ci.yml`
    (workflow_call — runs the canonical 'test' job, not an inline copy).
  - build-and-push needs that ci-calling job.
  - deploy needs build-and-push → transitively gated on the canonical test job.
  - AC-39.2 phase-promoter step is present in the deploy job.
  - AC-12.3 smoke-test retry-with-backoff step is present in the deploy job.

Tests in this module:
  test_deploy_yml_has_no_inline_pytest_job
      deploy.yml has no job that runs pytest inline (no divergent test job).
  test_deploy_yml_calls_canonical_ci_yml_via_workflow_call
      At least one job uses ./.github/workflows/ci.yml (workflow_call).
  test_canonical_ci_yml_defines_test_job
      ci.yml defines exactly a job named 'test' (the canonical source).
  test_deploy_job_transitively_depends_on_canonical_test
      The dependency chain (deploy → build-and-push → ci) gates the deploy on
      the canonical test job.
  test_deploy_job_has_exactly_one_workflow_call_to_ci_yml
      There is exactly ONE workflow_call to ci.yml; no duplicate.
  test_ac392_phase_promoter_gate_preserved
      The deploy job contains the phase-promoter pre-deploy step (AC-39.2).
  test_ac123_smoke_test_retry_backoff_preserved
      The deploy job contains the smoke-test step with retry-with-backoff (AC-12.3).
"""

from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yml(path: Path) -> dict:
    assert path.exists(), f"Expected workflow file not found: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must parse to a YAML mapping"
    return data


def _deploy_jobs(data: dict) -> dict:
    jobs = data.get("jobs", {})
    assert isinstance(jobs, dict), "deploy.yml 'jobs' must be a mapping"
    return jobs


def _get_deploy_job(data: dict) -> dict:
    jobs = _deploy_jobs(data)
    assert "deploy" in jobs, (
        "deploy.yml must have a 'deploy' job (required by AC-6.3). "
        f"Found jobs: {list(jobs.keys())}"
    )
    return jobs["deploy"]


def _job_steps(job: dict) -> list:
    return job.get("steps") or []


def _step_run(step: dict) -> str:
    return str(step.get("run", ""))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deploy_yml_has_no_inline_pytest_job() -> None:
    """deploy.yml must NOT contain a job that runs pytest inline.

    AC-40.2 (H1 principle): a second inline test job in deploy.yml is a
    divergent copy of the canonical test — it may run in a different
    environment, miss fixtures, or diverge from ci.yml's test job definition.
    The ONLY test job must be the one in ci.yml, called via workflow_call.

    Asserts that no job in deploy.yml has a step whose 'run:' command contains
    'pytest' (which would indicate an inline test invocation, not a workflow_call).
    """
    data = _load_yml(DEPLOY_YML)
    jobs = _deploy_jobs(data)

    inline_pytest_jobs = []
    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        if "uses" in job_def:
            # workflow_call job — not an inline test job
            continue
        for step in _job_steps(job_def):
            if not isinstance(step, dict):
                continue
            if "pytest" in _step_run(step):
                inline_pytest_jobs.append(job_name)
                break

    assert not inline_pytest_jobs, (
        "AC-40.2 H1 violation: deploy.yml contains job(s) with inline pytest steps: "
        f"{inline_pytest_jobs}. "
        "The deploy gate must call the canonical ci.yml test job via workflow_call, "
        "not re-run a divergent inline copy. Remove the inline test step(s) and "
        "use `uses: ./.github/workflows/ci.yml` instead."
    )


def test_deploy_yml_calls_canonical_ci_yml_via_workflow_call() -> None:
    """deploy.yml must call ./.github/workflows/ci.yml via workflow_call.

    AC-40.2: the single-canonical-test-job invariant requires the deploy gate
    to reuse ci.yml's 'test' job, not define its own. A job with
    `uses: ./.github/workflows/ci.yml` is the correct mechanism.
    """
    data = _load_yml(DEPLOY_YML)
    jobs = _deploy_jobs(data)

    ci_calling_jobs = [
        (name, job["uses"])
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]

    assert ci_calling_jobs, (
        "AC-40.2: deploy.yml has no job that calls ci.yml via workflow_call. "
        "Add a job with `uses: ./.github/workflows/ci.yml` so the deploy gate "
        "reuses the canonical test job rather than defining its own copy. "
        f"Current jobs: {list(jobs.keys())}"
    )


def test_canonical_ci_yml_defines_test_job() -> None:
    """ci.yml must define a job named 'test' — the canonical single test job.

    AC-40.2: the 'single canonical test job' is the 'test' job in ci.yml.
    This test confirms the canonical source still exists; if it is renamed,
    the H1 invariant is broken at its source.
    """
    data = _load_yml(CI_YML)
    jobs = data.get("jobs", {})
    assert "test" in jobs, (
        "AC-40.2: ci.yml must define a job named 'test' — that is the canonical "
        "test job that both PR CI and the deploy gate consume. "
        f"Found jobs in ci.yml: {list(jobs.keys())}"
    )


def test_deploy_job_transitively_depends_on_canonical_test() -> None:
    """The deploy job must be transitively gated on the canonical ci.yml test.

    AC-40.2: the dependency chain must be:
      deploy → build-and-push → <ci-calling job> → [ci.yml test job]

    Specifically:
      1. Some job X calls ci.yml via workflow_call.
      2. Some job Y that gates the deploy (e.g. build-and-push) needs job X.
      3. The deploy job needs job Y (or directly needs job X).

    This ensures the deploy step cannot proceed unless the canonical test job
    has succeeded.
    """
    data = _load_yml(DEPLOY_YML)
    jobs = _deploy_jobs(data)

    # Find the job(s) that call ci.yml
    ci_calling_job_names = {
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    }
    assert ci_calling_job_names, (
        "AC-40.2: no job in deploy.yml calls ci.yml — cannot establish dependency chain."
    )

    # Build a needs-graph: job_name -> set of jobs it directly needs
    needs_graph: dict[str, set] = {}
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        raw_needs = job.get("needs", [])
        if isinstance(raw_needs, str):
            raw_needs = [raw_needs]
        needs_graph[name] = set(raw_needs or [])

    def _transitive_needs(start: str) -> set:
        """Return all jobs that 'start' transitively depends on."""
        visited: set = set()
        queue = list(needs_graph.get(start, []))
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(needs_graph.get(current, []))
        return visited

    deploy_transitive = _transitive_needs("deploy")
    gated = deploy_transitive & ci_calling_job_names

    assert gated, (
        "AC-40.2: the 'deploy' job does not transitively depend on the ci.yml-calling job. "
        f"ci.yml-calling jobs: {ci_calling_job_names}. "
        f"deploy's transitive needs: {deploy_transitive}. "
        "The deploy job must depend (directly or transitively) on the job that calls "
        "ci.yml so it is gated on the canonical test job."
    )


def test_deploy_job_has_exactly_one_workflow_call_to_ci_yml() -> None:
    """deploy.yml calls ci.yml via workflow_call exactly once — no duplication.

    AC-40.2: calling ci.yml twice would re-run the test job redundantly and
    could obscure which invocation gates the deploy. There must be exactly one
    workflow_call to ci.yml.
    """
    data = _load_yml(DEPLOY_YML)
    jobs = _deploy_jobs(data)

    ci_calling_jobs = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]

    assert len(ci_calling_jobs) == 1, (
        f"AC-40.2: expected exactly 1 workflow_call to ci.yml, found {len(ci_calling_jobs)}: "
        f"{ci_calling_jobs}. "
        "There must be exactly ONE canonical test job invocation in deploy.yml."
    )


def test_ac392_phase_promoter_gate_preserved() -> None:
    """The deploy job must still contain the AC-39.2 phase-promoter pre-deploy step.

    AC-40.2 preservation requirement: the AC-39.2 phase-promoter gate
    (tools/promote_sprint_phase.py) must remain wired into the deploy job as a
    pre-deploy mechanical gate. Restructuring the CI call must not accidentally
    remove it.
    """
    data = _load_yml(DEPLOY_YML)
    deploy_job = _get_deploy_job(data)
    steps = _job_steps(deploy_job)

    promoter_steps = [
        step.get("name", "")
        for step in steps
        if isinstance(step, dict) and "promote_sprint_phase.py" in _step_run(step)
    ]

    assert promoter_steps, (
        "AC-40.2 preservation: the deploy job must contain a step that runs "
        "tools/promote_sprint_phase.py (AC-39.2 phase-promoter pre-deploy gate). "
        "This step was present before the AC-40.2 structural fix and must be preserved. "
        f"deploy job steps: {[s.get('name') for s in steps if isinstance(s, dict)]}"
    )


def test_ac123_smoke_test_retry_backoff_preserved() -> None:
    """The deploy job must still contain the AC-12.3 smoke-test retry-with-backoff step.

    AC-40.2 preservation requirement: the AC-12.3 runtime smoke-test retry loop
    (SMOKE_MAX_ATTEMPTS / SMOKE_RETRY_DELAY) must remain wired into the deploy job.
    Restructuring the CI call must not accidentally remove the smoke-test.
    """
    data = _load_yml(DEPLOY_YML)
    deploy_job = _get_deploy_job(data)
    steps = _job_steps(deploy_job)

    smoke_steps = [
        step.get("name", "")
        for step in steps
        if isinstance(step, dict) and "SMOKE_MAX_ATTEMPTS" in _step_run(step)
    ]

    assert smoke_steps, (
        "AC-40.2 preservation: the deploy job must contain a smoke-test step with "
        "SMOKE_MAX_ATTEMPTS retry-with-backoff (AC-12.3). "
        "This step must be preserved after the AC-40.2 structural fix. "
        f"deploy job steps: {[s.get('name') for s in steps if isinstance(s, dict)]}"
    )
