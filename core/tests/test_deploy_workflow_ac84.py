# ---
# module: core.tests.test_deploy_workflow_ac84
# sprint: sprint-3, sprint-11
# story: US-8 AC-8.4 US-52 AC-52.3 US-53 AC-53.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, re, yaml
# ---
"""AC-8.4 — solanaBilly hard isolation: scope and no-destructive-command invariants.

AC-8.4 states:
  - solanaBilly hard isolation is verified on the VPS after the solanatrilly deploy
  - every docker command in deploy.yml remains -p solanatrilly-scoped
  - NO unscoped down / up --force-recreate / prune / volume-removal anywhere in the file
  - closes US-6 AC-6.5; PRD §15.3
  - verified by the deploy run's isolation step passing AND these structural tests

Tests:
  test_no_docker_compose_down_in_deploy_yml
  test_no_force_recreate_in_deploy_yml
  test_no_docker_prune_in_deploy_yml
  test_no_volume_removal_in_deploy_yml
  test_all_docker_compose_calls_have_project_scope
  test_solanatrilly_deploy_commands_use_solanatrilly_scope
  test_isolation_step_uses_read_only_ps_check
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

COMPOSE_PROJECT = "solanatrilly"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-8.4 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _all_run_scripts(data: dict) -> str:
    """Return the combined run: text from every step in every job."""
    parts: list[str] = []
    for job in (data.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in (job.get("steps") or []):
            if isinstance(step, dict) and "run" in step:
                parts.append(str(step["run"]))
    return "\n".join(parts)


def _deploy_job_run_scripts(data: dict) -> str:
    """Return the combined run: text from the deploy job only."""
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, "deploy.yml must contain a 'deploy' job."
    parts: list[str] = []
    for step in (job.get("steps") or []):
        if isinstance(step, dict) and "run" in step:
            parts.append(str(step["run"]))
    return "\n".join(parts)


def _find_isolation_step(data: dict) -> dict | None:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy") or {}
    for step in (job.get("steps") or []):
        if isinstance(step, dict) and "isolation" in str(step.get("name", "")).lower():
            return step
    return None


def _docker_compose_invocations(script: str) -> list[str]:
    """Return each discrete docker compose / docker-compose sub-command fragment.

    Splits the script on shell command separators so each fragment contains
    at most one docker compose invocation. Only returns fragments that
    actually contain a docker compose call.
    """
    fragments = re.split(r"&&|\|\||;|\n", script)
    return [
        f.strip()
        for f in fragments
        # Match 'docker compose' (two words) or 'docker-compose' (hyphenated command,
        # not a filename like docker-compose.staging.yml — hence the negative lookahead).
        if re.search(r"\bdocker\s+compose\b|\bdocker-compose(?!\.)\b", f)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_docker_compose_down_in_deploy_yml() -> None:
    """deploy.yml must not contain any UNSCOPED 'docker compose down' command (AC-8.4).

    AC-8.4 bans UNSCOPED 'docker compose down' — running 'down' without '-p <project>'
    defaults to the directory name and risks destroying a co-located stack (e.g. solanaBilly).

    A SCOPED 'docker compose -p solanatrilly down --remove-orphans' (without -v / --volumes)
    is ALLOWED — it is necessary to clear orphaned containers from interrupted prior deploys
    (sprint-11 AC-52.3 fix) and is safely isolated to the solanatrilly project.

    This test fails if:
      - An unscoped 'docker compose down' appears (no -p flag)
      - A 'docker compose down -v' or 'down --volumes' appears (volume removal is Forbidden)
    """
    data = _load_deploy()
    scripts = _all_run_scripts(data)
    fragments = re.split(r"&&|\|\||;|\n", scripts)

    for fragment in fragments:
        stripped = fragment.strip()
        if not re.search(r"\bdocker(?:\s+compose|-compose(?!\.))\b.*\bdown\b", stripped):
            continue
        # This fragment contains a 'docker compose down' call.
        # It is forbidden if: (a) not project-scoped, OR (b) uses -v / --volumes.
        is_scoped = bool(re.search(r"-p\s+\S+|--project-name\s+\S+", stripped))
        has_volume_removal = bool(re.search(r"\s-v\b|--volumes\b", stripped))
        assert is_scoped, (
            "AC-8.4: UNSCOPED 'docker compose down' found in deploy.yml run scripts.\n"
            f"Fragment: {stripped!r}\n\n"
            "PRD §15.3 / CLAUDE.md Docker Rules: every 'docker compose down' must carry "
            "'-p <project>' to prevent it from defaulting to the directory name and "
            "potentially destroying a co-located stack (e.g. solanaBilly)."
        )
        assert not has_volume_removal, (
            "AC-8.4: 'docker compose down -v / --volumes' found in deploy.yml run scripts.\n"
            f"Fragment: {stripped!r}\n\n"
            "PRD §15.3 / CLAUDE.md Docker Rules: volume removal is a Forbidden operation "
            "in deploy.yml — it permanently destroys persistent data across ALL Docker "
            "projects on the host. Remove the -v / --volumes flag."
        )


def test_no_force_recreate_in_deploy_yml() -> None:
    """deploy.yml must not contain '--force-recreate' anywhere (AC-8.4).

    '--force-recreate' stops and restarts every container even when the image
    or config has not changed. Combined with an un-scoped compose invocation
    it can disrupt co-located stacks. It is banned from deploy.yml per PRD §15.3.
    """
    data = _load_deploy()
    scripts = _all_run_scripts(data)

    assert "--force-recreate" not in scripts, (
        "AC-8.4: '--force-recreate' found in deploy.yml run scripts.\n\n"
        "PRD §15.3 / CLAUDE.md Docker Rules: '--force-recreate' is a Forbidden "
        "flag. The deploy must not forcibly recreate containers — 'up -d' with "
        "an updated image is sufficient and non-destructive."
    )


def test_no_docker_prune_in_deploy_yml() -> None:
    """deploy.yml must not contain dangerous 'docker ... prune' commands (AC-8.4 / AC-53.2).

    Destructive prune commands (system prune, container prune, network prune,
    image prune --all) remove resources across ALL Docker projects on the host,
    not just solanatrilly. They could destroy solanaBilly's networks, stopped
    containers, and all cached images.

    AC-53.2 EXEMPTION — 'docker image prune -f' (dangling images, without --all/-a) IS ALLOWED:
      Dangling images are untagged layers unreferenced by any running container. They
      accumulate from prior deploy runs and cause ENOSPC disk exhaustion during
      docker compose pull (root cause of run 27683660493, AC-53.1). Pruning them with
      -f (no --all) is safe: tagged images like solanaBilly's :latest are never removed
      because they are named references, not dangling layers.

    Banned: docker system prune, docker container prune, docker network prune,
            docker image prune --all, docker image prune -a
    Allowed: docker image prune -f (without --all / -a)
    """
    data = _load_deploy()
    scripts = _all_run_scripts(data)

    prune_re = re.compile(r"\bdocker\b.*\bprune\b")

    for match in prune_re.finditer(scripts):
        matched = match.group(0).strip()
        # Safe exemption: 'docker image prune -f' without --all or -a
        is_safe_image_prune = bool(
            re.search(r"\bdocker\s+image\s+prune\b", matched)
        ) and not bool(re.search(r"\b--all\b|\s-a\b", matched))
        assert is_safe_image_prune, (
            "AC-8.4: dangerous 'docker ... prune' found in deploy.yml run scripts.\n"
            f"Matched text: {matched!r}\n\n"
            "PRD §15.3 / CLAUDE.md Docker Rules: destructive prune commands are Forbidden —\n"
            "they affect ALL Docker resources on the host, not just -p solanatrilly.\n\n"
            "Only 'docker image prune -f' (without --all/-a) is permitted as an exemption\n"
            "to prevent VPS disk exhaustion (AC-53.2 / run 27683660493).\n"
            "Detected form is not the safe exemption — remove it or replace with the\n"
            "allowed 'docker image prune -f' form."
        )


def test_no_volume_removal_in_deploy_yml() -> None:
    """deploy.yml must not contain any docker volume removal command (AC-8.4).

    'docker volume rm' and 'docker volume prune' destroy persistent data.
    Running either on the VPS risks wiping solanaBilly's database volume.
    Both are banned from deploy.yml per PRD §15.3.
    """
    data = _load_deploy()
    scripts = _all_run_scripts(data)

    vol_rm_re = re.compile(r"\bdocker\s+volume\s+(?:rm|remove|prune)\b")
    match = vol_rm_re.search(scripts)
    assert match is None, (
        "AC-8.4: 'docker volume rm/remove/prune' found in deploy.yml run scripts.\n"
        f"Matched text: {match.group(0)!r}\n\n"
        "PRD §15.3 / CLAUDE.md Docker Rules: volume removal commands are "
        "Forbidden — they can permanently destroy persistent data across ALL "
        "Docker projects on the host."
    )


def test_all_docker_compose_calls_have_project_scope() -> None:
    """Every 'docker compose' call in deploy.yml must include a -p / --project-name scope (AC-8.4).

    An unscoped 'docker compose' call defaults to the directory name as the
    project, which can collide with other stacks on the same host. Every call
    must explicitly declare its project with '-p <name>' or '--project-name <name>'
    so there is zero ambiguity about which stack is being touched.
    """
    data = _load_deploy()
    scripts = _all_run_scripts(data)

    invocations = _docker_compose_invocations(scripts)
    assert invocations, (
        "AC-8.4: No 'docker compose' calls found in deploy.yml run scripts. "
        "The deploy must issue at least one docker compose command."
    )

    unscoped = [
        inv
        for inv in invocations
        if "-p " not in inv and "--project-name " not in inv
    ]
    assert not unscoped, (
        "AC-8.4 scope violation: the following docker compose invocations in "
        "deploy.yml lack a '-p' / '--project-name' flag:\n"
        + "\n".join(f"  {inv!r}" for inv in unscoped)
        + "\n\nEvery docker compose call must be project-scoped (PRD §15.3)."
    )


def test_solanatrilly_deploy_commands_use_solanatrilly_scope() -> None:
    """The deploy job's pull and up -d commands must use -p solanatrilly (AC-8.4).

    The deployment of the solanatrilly stack must be explicitly scoped to
    '-p solanatrilly'. This is the primary isolation guard: without this scope
    a 'docker compose up -d' would default to whatever project the working
    directory implies and could touch an unexpected stack.
    """
    data = _load_deploy()
    scripts = _deploy_job_run_scripts(data)

    pull_re = re.compile(
        r"docker\s+compose\s+.*-p\s+solanatrilly.*\bpull\b"
        r"|docker\s+compose\s+.*\bpull\b.*-p\s+solanatrilly"
    )
    up_re = re.compile(
        r"docker\s+compose\s+.*-p\s+solanatrilly.*\bup\s+-d\b"
        r"|docker\s+compose\s+.*\bup\s+-d\b.*-p\s+solanatrilly"
    )

    assert pull_re.search(scripts), (
        f"AC-8.4: 'docker compose -p {COMPOSE_PROJECT} ... pull' not found in "
        "the deploy job run scripts.\n"
        "The image pull on the VPS must be scoped to the solanatrilly project."
    )
    assert up_re.search(scripts), (
        f"AC-8.4: 'docker compose -p {COMPOSE_PROJECT} ... up -d' not found in "
        "the deploy job run scripts.\n"
        "Bringing the stack up on the VPS must be scoped to the solanatrilly project."
    )


def test_isolation_step_uses_read_only_ps_check() -> None:
    """The isolation verification step must use only 'docker compose -p solanabilly ps' (AC-8.4).

    The isolation step checks that solanaBilly is still running after the
    solanatrilly deploy. It must use 'ps' (read-only status query), not any
    mutating command (down, restart, rm, etc.). The step must be explicitly
    scoped to '-p solanabilly' so it only queries solanaBilly's project.
    """
    data = _load_deploy()
    isolation_step = _find_isolation_step(data)
    assert isolation_step is not None, (
        "AC-8.4: No isolation step found in the 'deploy' job. "
        "Expected a step with 'isolation' in its name that checks solanaBilly's status."
    )

    script = str(isolation_step.get("run", ""))

    # Must query solanaBilly's project with docker compose -p solanabilly ps
    assert "docker compose -p solanabilly ps" in script, (
        "AC-8.4: Isolation step must run 'docker compose -p solanabilly ps' to "
        "check solanaBilly's container state without touching the project.\n"
        f"Current isolation step script:\n{script}"
    )

    # Must NOT run any mutating command against solanaBilly
    mutating_re = re.compile(
        r"\bdocker\b.*-p\s+solanabilly\b.*\b(?:down|up|restart|rm|stop|kill|prune)\b"
        r"|\bdocker\b.*\b(?:down|up|restart|rm|stop|kill|prune)\b.*-p\s+solanabilly\b"
    )
    match = mutating_re.search(script)
    assert match is None, (
        "AC-8.4: Isolation step must NOT run mutating docker commands against "
        "solanaBilly (-p solanabilly). Only 'ps' (read-only) is permitted.\n"
        f"Found mutating command: {match.group(0)!r}\n"
        f"Full isolation step script:\n{script}"
    )
