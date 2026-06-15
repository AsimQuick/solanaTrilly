# ---
# module: core.tests.test_deploy_workflow_ac61
# sprint: sprint-2
# story: US-6 AC-6.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-6.1 — deploy.yml GitHub Actions workflow structural verification.

Asserts every H1/AC-6.1 requirement of deploy.yml:
  1. File exists at .github/workflows/deploy.yml.
  2. Triggers on push to main (merge trigger).
  3. Has permissions: packages: write at workflow level.
  4. Every action 'uses:' ref is SHA-pinned (40-char hex, H1).
  5. No custom secrets referenced — only secrets.GITHUB_TOKEN.
  6. Pushes image to ghcr.io/asimquick/solanatrilly.
  7. build-and-push job depends on a CI job (ordered: CI runs first).

Tests:
  test_deploy_yml_exists
  test_deploy_yml_triggers_on_push_to_main
  test_deploy_yml_has_packages_write_permission
  test_deploy_yml_all_actions_sha_pinned
  test_deploy_yml_no_custom_secrets
  test_deploy_yml_pushes_to_correct_ghcr_registry
  test_deploy_yml_build_job_needs_ci_job
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Matches secrets.GITHUB_TOKEN (the built-in, allowed) vs custom secrets
GITHUB_TOKEN_RE = re.compile(r"\$\{\{\s*secrets\.GITHUB_TOKEN\s*\}\}")
ANY_SECRET_RE = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-6.1 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be valid YAML mapping at top level."
    return data


def _is_exempt(uses_value: str) -> bool:
    return uses_value.startswith("./") or uses_value.startswith("docker://")


def _sha_pinned(uses_value: str) -> bool:
    if "@" not in uses_value:
        return False
    ref = uses_value.rsplit("@", 1)[1].strip()
    return bool(SHA_RE.match(ref))


def _all_uses_in_jobs(jobs: dict) -> list[tuple[str, str]]:
    """Return (label, uses_value) for every step-level uses: in *jobs*."""
    results: list[tuple[str, str]] = []
    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        steps = job_def.get("steps") or []
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if uses is not None:
                results.append((f"job '{job_id}', step {idx}", str(uses)))
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deploy_yml_exists() -> None:
    """deploy.yml must exist at .github/workflows/deploy.yml (AC-6.1)."""
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}.\n"
        "Create .github/workflows/deploy.yml for AC-6.1."
    )


def test_deploy_yml_triggers_on_push_to_main() -> None:
    """deploy.yml must trigger on push to main (the merge event) (AC-6.1)."""
    data = _load_deploy()
    on_block = data.get("on") or data.get(True)
    assert isinstance(on_block, dict), (
        "deploy.yml 'on:' block must be a mapping, not a scalar."
    )
    push = on_block.get("push")
    assert isinstance(push, dict), (
        "deploy.yml must have an 'on.push' trigger."
    )
    branches = push.get("branches") or []
    assert "main" in branches, (
        f"deploy.yml 'on.push.branches' must include 'main'. Got: {branches!r}"
    )


def test_deploy_yml_has_packages_write_permission() -> None:
    """deploy.yml must grant packages: write at the workflow level (AC-6.1).

    packages: write is required for GITHUB_TOKEN to push to GHCR — no extra
    secret needed (per po-requests.md item 2).
    """
    data = _load_deploy()
    permissions = data.get("permissions")
    assert isinstance(permissions, dict), (
        "deploy.yml must have a top-level 'permissions:' block.\n"
        "Required: permissions:\n  packages: write"
    )
    pkg_perm = permissions.get("packages")
    assert pkg_perm == "write", (
        f"deploy.yml permissions.packages must be 'write'. Got: {pkg_perm!r}\n"
        "Required: permissions:\n  packages: write"
    )


def test_deploy_yml_all_actions_sha_pinned() -> None:
    """Every GitHub Action 'uses:' in deploy.yml must be SHA-pinned (H1, AC-6.1).

    Local actions (./) and Docker container actions (docker://) are exempt.
    Reusable workflow calls (./.github/workflows/ci.yml) are also local and exempt.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}
    violations: list[str] = []
    for label, uses in _all_uses_in_jobs(jobs):
        if _is_exempt(uses):
            continue
        if not _sha_pinned(uses):
            violations.append(f"  {label}: uses: {uses!r} — not SHA-pinned")
    assert not violations, (
        "H1 violation in deploy.yml — unpinned GitHub Action ref(s):\n\n"
        + "\n".join(violations)
        + "\n\nEvery 'uses:' must be pinned to a 40-char commit SHA:\n"
        "  uses: owner/repo@<40-hex-chars>  # vX.Y.Z"
    )


def test_deploy_yml_no_custom_secrets() -> None:
    """deploy.yml must use only secrets.GITHUB_TOKEN — no extra custom secrets (AC-6.1).

    Per po-requests.md item 2: GITHUB_TOKEN (built-in) is the only allowed
    secret for GHCR authentication.  Any other secrets.FOO reference is a
    violation of this AC.
    """
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    secret_names = {m.group(1) for m in ANY_SECRET_RE.finditer(raw)}
    custom_secrets = secret_names - {"GITHUB_TOKEN"}
    assert not custom_secrets, (
        "AC-6.1 violation — deploy.yml references custom secrets:\n"
        + "\n".join(f"  secrets.{s}" for s in sorted(custom_secrets))
        + "\n\nOnly secrets.GITHUB_TOKEN (the built-in token) is allowed."
    )


def test_deploy_yml_pushes_to_correct_ghcr_registry() -> None:
    """deploy.yml must push the web image to ghcr.io/asimquick/solanatrilly (AC-6.1)."""
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    assert "ghcr.io/asimquick/solanatrilly" in raw, (
        "deploy.yml must reference 'ghcr.io/asimquick/solanatrilly' as the "
        "push destination. AC-6.1 specifies this exact GHCR path."
    )


def test_deploy_yml_build_job_needs_ci_job() -> None:
    """The build-and-push job must declare a 'needs' dependency on a CI job (AC-6.1).

    This ensures CI (H1) completes before the image is pushed, preventing a
    broken image from reaching GHCR.
    """
    data = _load_deploy()
    jobs = data.get("jobs") or {}

    # Find the job that does the docker push (must reference ghcr.io push)
    build_job_id = None
    for job_id, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        steps = job_def.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses", "")
            with_block = step.get("with") or {}
            tags = with_block.get("tags", "")
            if "build-push-action" in uses or "ghcr.io" in str(tags):
                build_job_id = job_id
                break
        if build_job_id:
            break

    assert build_job_id is not None, (
        "deploy.yml must have a job that uses docker/build-push-action or "
        "references ghcr.io in a 'with.tags' field."
    )

    build_job = jobs[build_job_id]
    needs = build_job.get("needs")
    assert needs is not None, (
        f"The build-and-push job ('{build_job_id}') must declare 'needs:' "
        "to ensure CI runs before the image is pushed. Got needs=None."
    )

    # Find which job runs CI (calls ci.yml or runs pytest)
    ci_job_ids = set()
    for job_id, job_def in jobs.items():
        if job_id == build_job_id:
            continue
        if isinstance(job_def, dict):
            # Reusable workflow call pattern
            if ".github/workflows/ci.yml" in str(job_def.get("uses", "")):
                ci_job_ids.add(job_id)

    needs_list = [needs] if isinstance(needs, str) else list(needs)
    if ci_job_ids:
        overlap = ci_job_ids & set(needs_list)
        assert overlap, (
            f"Build job '{build_job_id}' needs: {needs_list!r} but the CI job "
            f"is '{ci_job_ids}'. The build job must depend on the CI job."
        )
    else:
        assert needs_list, (
            f"Build job '{build_job_id}' must depend on at least one other job "
            "to ensure CI gates the push."
        )
