# ---
# module: core.tests.test_deploy_workflow_ac63
# sprint: sprint-2
# story: US-6 AC-6.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-6.3 — VPS SSH deploy step structural verification.

Asserts every AC-6.3 requirement of the deploy.yml workflow:
  1. A 'deploy' job exists and runs after build-and-push.
  2. The deploy job references VPS_SSH_KEY, VPS_USER, VPS_HOST secrets.
  3. Every 'docker compose' invocation in the deploy job is -p solanatrilly-scoped.
  4. No unscoped dangerous docker commands exist in deploy.yml
     (docker compose down, up --force-recreate, system prune, volume rm).
  5. Both 'pull' and 'up -d' are present in the deploy run scripts.
  6. No reference to solanabilly or port 8001 appears in docker commands
     (PRD §15.3, hard isolation from live stack).
  7. All GitHub Actions 'uses:' in the deploy job are SHA-pinned (H1).

Tests:
  test_deploy_job_exists
  test_deploy_job_needs_build_and_push
  test_deploy_job_uses_vps_ssh_key_secret
  test_deploy_job_uses_vps_user_and_host_secrets
  test_all_deploy_docker_compose_commands_project_scoped
  test_no_unscoped_dangerous_docker_commands_in_deploy_yml
  test_deploy_commands_include_pull_and_up_d
  test_no_solanabilly_reference_in_deploy_docker_commands
  test_deploy_job_actions_sha_pinned
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Patterns for dangerous unscoped docker commands
# These would affect ALL stacks on the host, not just -p solanatrilly.
_DANGEROUS_PATTERNS = [
    # docker compose down without a project scope
    re.compile(r"docker\s+compose\s+down\b(?!.*-p\s+solanatrilly)"),
    # docker compose up --force-recreate without a project scope
    re.compile(r"docker\s+compose\s+up\s+--force-recreate\b(?!.*-p\s+solanatrilly)"),
    # docker system prune (always unscoped — affects all containers/images)
    re.compile(r"docker\s+system\s+prune\b"),
    # docker volume rm (always unscoped — can destroy any volume)
    re.compile(r"docker\s+volume\s+rm\b"),
    # docker compose prune (not a real command, but guard anyway)
    re.compile(r"docker\s+compose\s+prune\b"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-6.3 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _deploy_job(data: dict) -> dict:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a job named 'deploy' (AC-6.3).\n"
        f"Current jobs: {list(jobs.keys())}"
    )
    assert isinstance(job, dict), "'deploy' job definition must be a YAML mapping."
    return job


def _run_scripts_from_job(job: dict) -> list[str]:
    """Return all run: script texts from a job's steps."""
    scripts: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "run" in step:
            scripts.append(str(step["run"]))
    return scripts


def _is_sha_pinned(uses: str) -> bool:
    if "@" not in uses:
        return False
    ref = uses.rsplit("@", 1)[1].strip()
    return bool(SHA_RE.match(ref))


def _is_exempt(uses: str) -> bool:
    return uses.startswith("./") or uses.startswith("docker://")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deploy_job_exists() -> None:
    """deploy.yml must contain a 'deploy' job (AC-6.3)."""
    data = _load_deploy()
    jobs = data.get("jobs") or {}
    assert "deploy" in jobs, (
        "AC-6.3 requires a 'deploy' job in deploy.yml.\n"
        f"Current jobs: {list(jobs.keys())}"
    )


def test_deploy_job_needs_build_and_push() -> None:
    """The deploy job must run after build-and-push to use the pushed GHCR image (AC-6.3)."""
    data = _load_deploy()
    job = _deploy_job(data)
    needs = job.get("needs")
    assert needs is not None, (
        "The 'deploy' job must declare 'needs: build-and-push' so it only runs "
        "after the GHCR image has been successfully pushed."
    )
    needs_list = [needs] if isinstance(needs, str) else list(needs)
    assert "build-and-push" in needs_list, (
        f"'deploy' job must need 'build-and-push'. Got needs: {needs_list!r}"
    )


def test_deploy_job_uses_vps_ssh_key_secret() -> None:
    """The deploy job must reference secrets.VPS_SSH_KEY for SSH authentication (AC-6.3)."""
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    assert "secrets.VPS_SSH_KEY" in raw or "secrets.VPS_SSH_KEY" in raw, (
        "AC-6.3 requires secrets.VPS_SSH_KEY for SSH key authentication.\n"
        "Add: VPS_SSH_KEY: ${{ secrets.VPS_SSH_KEY }} to the deploy step's env: block."
    )
    assert "VPS_SSH_KEY" in raw, (
        "AC-6.3: VPS_SSH_KEY secret must be referenced in deploy.yml."
    )


def test_deploy_job_uses_vps_user_and_host_secrets() -> None:
    """The deploy job must reference VPS_USER and VPS_HOST secrets for SSH targeting (AC-6.3)."""
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    for secret in ("VPS_USER", "VPS_HOST"):
        assert secret in raw, (
            f"AC-6.3: secrets.{secret} must be referenced in deploy.yml.\n"
            "The deploy step needs VPS_USER@VPS_HOST to know where to SSH."
        )


def test_all_deploy_docker_compose_commands_project_scoped() -> None:
    """Every 'docker compose' call in the deploy job run scripts must be project-scoped.

    Hard isolation rule (PRD §15.3): unscoped docker compose commands affect all
    stacks on the host. Every call must have a -p <project> flag.
    Solanatrilly state-changes use -p solanatrilly; the AC-6.5 isolation check
    uses -p solanabilly for a read-only 'ps' — both are considered scoped.
    """
    data = _load_deploy()
    job = _deploy_job(data)
    scripts = _run_scripts_from_job(job)
    assert scripts, (
        "The 'deploy' job has no 'run:' steps. "
        "AC-6.3 requires shell commands that SSH to the VPS and run docker compose."
    )

    _scope_re = re.compile(r"-p\s+\S+")
    violations: list[str] = []
    for script in scripts:
        for line in script.splitlines():
            stripped = line.strip()
            if "docker compose" in stripped and not _scope_re.search(stripped):
                violations.append(f"  {stripped!r}")

    assert not violations, (
        "AC-6.3 violation: docker compose commands without a -p <project> scope:\n"
        + "\n".join(violations)
        + "\n\nEvery docker compose call in the deploy job must include -p <project>."
    )


def test_no_unscoped_dangerous_docker_commands_in_deploy_yml() -> None:
    """deploy.yml must not contain unscoped docker down/prune/volume-rm commands (AC-6.3).

    Unscoped 'docker compose down', 'docker system prune', or 'docker volume rm'
    would affect every stack on the VPS host, potentially destroying solanaBilly
    (PRD §15.3 hard isolation).
    """
    raw = DEPLOY_YML.read_text(encoding="utf-8")
    violations: list[str] = []
    for pattern in _DANGEROUS_PATTERNS:
        matches = pattern.findall(raw)
        for match in matches:
            violations.append(f"  pattern {pattern.pattern!r} matched: {match!r}")

    assert not violations, (
        "AC-6.3 isolation violation in deploy.yml — dangerous unscoped docker command(s):\n"
        + "\n".join(violations)
        + "\n\nForbidden: docker compose down (unscoped), docker system prune, "
        "docker volume rm, up --force-recreate (unscoped)."
    )


def test_deploy_commands_include_pull_and_up_d() -> None:
    """The deploy job run scripts must include 'pull' and 'up -d' commands (AC-6.3).

    'pull' fetches the freshly built GHCR image; 'up -d' starts/restarts the stack.
    Both are required for the deploy to have any effect.
    """
    data = _load_deploy()
    job = _deploy_job(data)
    all_script = "\n".join(_run_scripts_from_job(job))

    assert "pull" in all_script, (
        "AC-6.3: The deploy job run scripts must include 'docker compose ... pull' "
        "to fetch the latest GHCR image before restarting the stack."
    )
    assert "up -d" in all_script, (
        "AC-6.3: The deploy job run scripts must include 'docker compose ... up -d' "
        "to bring the staging stack up in detached mode."
    )


def test_no_solanabilly_reference_in_deploy_docker_commands() -> None:
    """No state-modifying docker command in the deploy job may reference solanabilly (PRD §15.3).

    The solanaBilly live stack must never be modified by the solanatrilly deploy.
    AC-6.5 exception: a read-only 'docker compose -p solanabilly ps' is permitted
    for isolation verification — it observes but never modifies solanaBilly's stack.
    Port 8001 may appear in curl commands (non-docker lines) for the same check.
    """
    data = _load_deploy()
    job = _deploy_job(data)
    all_script = "\n".join(_run_scripts_from_job(job))

    for line in all_script.splitlines():
        stripped = line.strip()
        if "docker" not in stripped.lower():
            continue
        # Allow the read-only isolation check (AC-6.5): docker compose -p solanabilly ps
        if (
            "docker compose" in stripped
            and "-p solanabilly" in stripped
            and "ps" in stripped
        ):
            continue
        assert "solanabilly" not in stripped.lower(), (
            f"AC-6.3 isolation violation: docker command references 'solanabilly':\n"
            f"  {stripped!r}\n"
            "State-modifying docker commands must not touch solanaBilly's stack.\n"
            "Only 'docker compose -p solanabilly ps' (read-only, AC-6.5) is permitted."
        )
        assert "8001" not in stripped, (
            f"AC-6.3 isolation violation: docker command references port 8001:\n"
            f"  {stripped!r}\n"
            "Port 8001 belongs to live solanaBilly — docker commands must not reference it.\n"
            "(Port 8001 HTTP checks via curl on non-docker lines are permitted for AC-6.5.)"
        )


def test_deploy_job_actions_sha_pinned() -> None:
    """Every GitHub Action 'uses:' in the deploy job must be SHA-pinned (H1, AC-6.3).

    Local actions (./) and Docker container actions (docker://) are exempt.
    """
    data = _load_deploy()
    job = _deploy_job(data)
    violations: list[str] = []
    for idx, step in enumerate(job.get("steps") or []):
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if uses is None:
            continue
        uses_str = str(uses)
        if _is_exempt(uses_str):
            continue
        if not _is_sha_pinned(uses_str):
            name = step.get("name", f"step {idx}")
            violations.append(f"  step {idx} ({name!r}): uses: {uses_str!r}")

    assert not violations, (
        "H1 violation in deploy.yml deploy job — unpinned GitHub Action ref(s):\n"
        + "\n".join(violations)
        + "\n\nAll 'uses:' must be pinned to a 40-char commit SHA."
    )
