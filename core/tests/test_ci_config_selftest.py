# ---
# module: core.tests.test_ci_config_selftest
# sprint: sprint-2
# story: US-3 AC-3.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-3.4 — CI-config self-test: asserts all H1 invariants so a future
unpinned 'uses:' or *-latest runner FAILS CI.

This is the authoritative H1 self-test.  It runs inside the merge-gate CI job
(pytest is always run in CI) so any regression in the workflow configuration
is caught before merge.

H1 invariants asserted:
  1. Every GitHub Action 'uses:' ref is pinned to a full 40-char commit SHA.
  2. No job in any workflow file uses a *-latest runner image.
  3. Exactly one workflow file defines a job with id 'test' (single source of
     CI truth — ci.yml).

Rationale (AC-3.4 / PRD §12 S5/H1):
  The Node-24 CI flail (S5) was caused by mutable action refs and runner images
  changing silently under pinned commits.  These three invariants together make
  that class of breakage impossible: an unpinned 'uses:' or '*-latest' runner
  introduced in any future PR will cause this test to FAIL in CI, blocking
  merge before the damage propagates.

Tests:
  test_h1_all_actions_sha_pinned
      Scans every *.yml / *.yaml workflow file under .github/workflows/,
      extracts all step-level 'uses:' values, and asserts each non-exempt ref
      is pinned to exactly 40 lowercase hex characters after the '@'.

  test_h1_runner_frozen_no_latest
      Scans every workflow file and asserts no job's 'runs-on' value contains
      the substring 'latest'.

  test_h1_single_workflow_defines_test_job
      Asserts exactly one workflow file defines a job with the id 'test',
      keeping ci.yml as the single canonical CI workflow.
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _uses_values(path: Path, workflow: dict) -> list[tuple[str, str]]:
    """Return (context_label, uses_value) for every step-level 'uses:' key."""
    results: list[tuple[str, str]] = []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return results
    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        steps = job_def.get("steps") or []
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if uses is not None:
                label = (
                    f"{path.relative_to(REPO_ROOT)}"
                    f" (job '{job_id}', step {idx})"
                )
                results.append((label, str(uses)))
    return results


def _is_exempt(uses_value: str) -> bool:
    return uses_value.startswith("./") or uses_value.startswith("docker://")


def _sha_pinned(uses_value: str) -> bool:
    if "@" not in uses_value:
        return False
    ref = uses_value.rsplit("@", 1)[1].strip()
    return bool(SHA_RE.match(ref))


def _runs_on_contains_latest(runs_on: object) -> bool:
    if isinstance(runs_on, list):
        return "latest" in " ".join(str(x) for x in runs_on).lower()
    return "latest" in str(runs_on).lower()


# ---------------------------------------------------------------------------
# H1 self-tests
# ---------------------------------------------------------------------------


def test_h1_all_actions_sha_pinned() -> None:
    """H1 invariant #1: every GitHub Action ref must be a 40-char commit SHA.

    Exemptions: local actions (./) and Docker container actions (docker://).

    Failure mode prevented: a mutable tag/branch ref (e.g. @v4, @main) can
    pull in upstream changes silently, breaking CI without any local code
    change (PRD §12 S5).
    """
    files = _workflow_files()
    assert files, f"No workflow files found under {WORKFLOWS_DIR}."

    violations: list[str] = []
    for wf_path in files:
        wf = _load(wf_path)
        for label, uses in _uses_values(wf_path, wf):
            if _is_exempt(uses):
                continue
            if not _sha_pinned(uses):
                violations.append(f"  {label}: uses: {uses!r} — not SHA-pinned")

    assert not violations, (
        "H1 violation — unpinned GitHub Action ref(s) detected.\n\n"
        "Every 'uses:' must look like:\n"
        "  uses: owner/repo@<40-hex-chars>  # vX.Y.Z\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_h1_runner_frozen_no_latest() -> None:
    """H1 invariant #2: no job may use a *-latest runner image.

    Failure mode prevented: *-latest runner images are updated by GitHub
    automatically; a new Node.js LTS or glibc version can break builds
    without any code change (the root cause of the Node-24 CI flail, S5).
    Use versioned images such as 'ubuntu-24.04' instead.
    """
    files = _workflow_files()
    assert files, f"No workflow files found under {WORKFLOWS_DIR}."

    violations: list[str] = []
    for wf_path in files:
        wf = _load(wf_path)
        jobs = wf.get("jobs") or {}
        for job_id, job_def in jobs.items():
            if not isinstance(job_def, dict):
                continue
            runs_on = job_def.get("runs-on")
            if runs_on is not None and _runs_on_contains_latest(runs_on):
                rel = wf_path.relative_to(REPO_ROOT)
                violations.append(
                    f"  {rel} (job '{job_id}'): runs-on: {runs_on!r}"
                )

    assert not violations, (
        "H1 violation — *-latest runner image(s) detected.\n\n"
        "Replace with a versioned image, e.g.:\n"
        "  runs-on: ubuntu-24.04\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_h1_single_workflow_defines_test_job() -> None:
    """H1 invariant #3: exactly one workflow file must define a job named 'test'.

    Failure mode prevented: two workflow files each defining a 'test' job
    create split-brain CI — two separate run histories, two status checks,
    and no authoritative merge gate.  ci.yml is the single source of truth.
    """
    files = _workflow_files()
    assert files, f"No workflow files found under {WORKFLOWS_DIR}."

    owners: list[str] = []
    for wf_path in files:
        wf = _load(wf_path)
        jobs = wf.get("jobs") or {}
        if "test" in jobs:
            owners.append(str(wf_path.relative_to(REPO_ROOT)))

    assert len(owners) == 1, (
        "H1 violation — 'test' job must be defined in exactly one workflow file "
        f"(ci.yml), but found {len(owners)}.\n\n"
        + (
            "No workflow file defines a 'test' job — ci.yml must define one."
            if not owners
            else "Files with a 'test' job:\n"
            + "\n".join(f"  {f}" for f in owners)
            + "\n\nRemove duplicate 'test' job definitions."
        )
    )
