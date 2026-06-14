# ---
# module: core.tests.test_ci_sha_pins
# sprint: sprint-2
# story: US-3 AC-3.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-3.1 — Every GitHub Action in workflow files must be pinned to a full
40-character commit SHA, not a mutable tag or branch ref.

Tests:
  test_all_workflow_actions_sha_pinned
      Scans all *.yml / *.yaml files under .github/workflows/, extracts every
      'uses:' step value, and asserts that any GitHub Action ref (i.e. not a
      local action starting with './' and not a Docker container action starting
      with 'docker://') is pinned to exactly 40 lowercase hex characters after
      the '@'.

      Fails with a clear message listing every violation so the author knows
      exactly which workflow file, step, and ref is unpinned.

      Rationale (AC-3.1 / PRD §12 S5/H1):
        Mutable refs (tags like @v4, branches like @main) silently pull in
        upstream code changes at runtime.  The Node-24 CI flail (S5) was caused
        by exactly this class of drift.  Full-SHA pins are immutable — the only
        way to update is an explicit, reviewed commit.
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

# A valid SHA-pinned ref is exactly 40 lowercase hex characters.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_workflow_files() -> list[Path]:
    """Return all *.yml and *.yaml files under .github/workflows/."""
    yml_files = list(WORKFLOWS_DIR.glob("*.yml"))
    yaml_files = list(WORKFLOWS_DIR.glob("*.yaml"))
    return sorted(yml_files + yaml_files)


def _extract_uses_values(workflow_path: Path) -> list[tuple[str, str]]:
    """Parse *workflow_path* with yaml.safe_load and return all 'uses:' values.

    Returns a list of (job_name_or_context, uses_value) tuples so violation
    messages can pinpoint the exact location.

    Only step-level 'uses:' keys are extracted (jobs.<id>.steps[].uses).
    """
    content = workflow_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    results: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return results

    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return results

    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        steps = job_def.get("steps")
        if not isinstance(steps, list):
            continue
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses_value = step.get("uses")
            if uses_value is not None:
                context = (
                    f"{workflow_path.relative_to(REPO_ROOT)}"
                    f" (job '{job_id}', step {step_index})"
                )
                results.append((context, str(uses_value)))

    return results


def _is_exempt(uses_value: str) -> bool:
    """Return True if *uses_value* is exempt from SHA-pinning.

    Exempt categories:
      - Local actions:         starts with './'  (e.g. ./.github/actions/setup)
      - Docker container acts: starts with 'docker://'
    """
    return uses_value.startswith("./") or uses_value.startswith("docker://")


def _ref_is_sha_pinned(uses_value: str) -> bool:
    """Return True if the ref portion of *uses_value* is a 40-char hex SHA.

    A GitHub Action uses value has the form  owner/repo@ref  or
    owner/repo/subdir@ref.  The ref is everything after the final '@'.
    If there is no '@', the action is unpinned (no ref at all).
    """
    if "@" not in uses_value:
        return False
    ref = uses_value.rsplit("@", 1)[1]
    # Strip an inline comment that may follow the SHA (e.g. "abc...@sha  # v4")
    # yaml.safe_load handles YAML comments, so the value here should already be
    # the clean string — but guard against any stray whitespace.
    ref = ref.strip()
    return bool(SHA_RE.match(ref))


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_all_workflow_actions_sha_pinned() -> None:
    """Every GitHub Action 'uses:' ref in all workflow files must be SHA-pinned.

    Validates that no workflow file in .github/workflows/ references a GitHub
    Action by a mutable tag (e.g. @v4) or branch (e.g. @main).  Only full
    40-character lowercase hex commit SHAs are accepted.

    Local actions (./...) and Docker container actions (docker://...) are
    exempt from SHA-pinning since they do not pull external code at runtime.

    Fails with a clear listing of every violation found across all workflow
    files, enabling a single-pass fix.
    """
    workflow_files = _collect_workflow_files()

    assert workflow_files, (
        f"No workflow files found under {WORKFLOWS_DIR}. "
        "Expected at least one *.yml or *.yaml file."
    )

    violations: list[str] = []

    for wf_path in workflow_files:
        for context, uses_value in _extract_uses_values(wf_path):
            if _is_exempt(uses_value):
                continue
            if not _ref_is_sha_pinned(uses_value):
                violations.append(
                    f"  {context}: uses: {uses_value!r} — not SHA-pinned"
                )

    assert not violations, (
        "AC-3.1 violation: the following GitHub Actions are not pinned to a "
        "full 40-character commit SHA (PRD §12 S5/H1).\n\n"
        "Every 'uses:' line must look like:\n"
        "  uses: owner/repo@<40-hex-chars>  # vX.Y.Z\n\n"
        "Violations:\n" + "\n".join(violations) + "\n\n"
        "To fix: look up the SHA for the desired tag at\n"
        "  https://github.com/<owner>/<repo>/tags\n"
        "and replace the tag ref with the full commit SHA."
    )
