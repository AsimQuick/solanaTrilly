# ---
# module: core.tests.test_deploy_workflow_ac81
# sprint: sprint-3
# story: US-8 AC-8.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-8.1 — deploy.yml creates the target directory before SCP.

Root cause (retrospective B1, run 27531582183): the SCP to
/root/solanatrilly/docker-compose.staging.yml fails with 'No such file or
directory' because /root/solanatrilly/ does not exist on the VPS at deploy
time.

Fix: an 'ssh … mkdir -p /root/solanatrilly' command must precede the SCP in
the deploy job's run script so the directory is guaranteed to exist before any
file is transferred.

Tests:
  test_mkdir_p_present_in_deploy_script
  test_mkdir_p_precedes_scp_in_deploy_script
  test_mkdir_p_targets_correct_path
  test_scp_target_path_is_root_solanatrilly
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

SCP_TARGET_DIR = "/root/solanatrilly"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-8.1 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _deploy_job(data: dict) -> dict:
    jobs = data.get("jobs") or {}
    job = jobs.get("deploy")
    assert job is not None, (
        "deploy.yml must contain a job named 'deploy'.\n"
        f"Current jobs: {list(jobs.keys())}"
    )
    return job


def _deploy_run_script(data: dict) -> str:
    """Return the combined run: text from all steps in the deploy job."""
    job = _deploy_job(data)
    parts: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "run" in step:
            parts.append(str(step["run"]))
    assert parts, "The 'deploy' job has no 'run:' steps."
    return "\n".join(parts)


def _line_index_of_pattern(script: str, pattern: re.Pattern) -> int:
    """Return the index of the first line matching *pattern*, or -1."""
    for idx, line in enumerate(script.splitlines()):
        if pattern.search(line):
            return idx
    return -1


# Matches a shell line that calls ssh … mkdir -p /root/solanatrilly
# (allows additional flags / quoting around the command).
_MKDIR_RE = re.compile(
    r"\bmkdir\s+-p\s+['\"]?" + re.escape(SCP_TARGET_DIR) + r"['\"]?"
)

# Matches a shell line that SCP-copies a file into /root/solanatrilly/
# Requires a trailing '/' so it does NOT match the mkdir line, which ends with
# just '/root/solanatrilly' followed by a closing quote (no slash after the dir).
_SCP_TARGET_RE = re.compile(re.escape(SCP_TARGET_DIR) + r"/")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mkdir_p_present_in_deploy_script() -> None:
    """The deploy job run script must contain 'mkdir -p /root/solanatrilly' (AC-8.1).

    This command creates the VPS target directory so the subsequent SCP does
    not fail with 'No such file or directory' (retrospective B1).
    """
    script = _deploy_run_script(_load_deploy())
    assert _MKDIR_RE.search(script), (
        f"AC-8.1: deploy job run script must include 'mkdir -p {SCP_TARGET_DIR}' "
        "to create the target directory on the VPS before the SCP transfer.\n"
        "Retrospective B1: SCP to /root/solanatrilly/ fails when the directory "
        "does not exist. Add:\n"
        "  ssh … mkdir -p /root/solanatrilly\n"
        "before the scp step."
    )


def test_mkdir_p_precedes_scp_in_deploy_script() -> None:
    """'mkdir -p /root/solanatrilly' must appear before the SCP in the deploy script (AC-8.1).

    Order matters: the directory must exist before scp tries to write into it.
    If mkdir comes after scp the SCP still fails on a fresh VPS.
    """
    script = _deploy_run_script(_load_deploy())
    mkdir_line = _line_index_of_pattern(script, _MKDIR_RE)
    scp_line = _line_index_of_pattern(script, _SCP_TARGET_RE)

    assert mkdir_line != -1, (
        f"AC-8.1: 'mkdir -p {SCP_TARGET_DIR}' not found in deploy script. "
        "Add the mkdir step before the scp transfer."
    )
    assert scp_line != -1, (
        f"AC-8.1: SCP targeting '{SCP_TARGET_DIR}' not found in deploy script. "
        "The scp step that copies docker-compose.staging.yml must be present."
    )
    assert mkdir_line < scp_line, (
        f"AC-8.1 ordering violation: 'mkdir -p {SCP_TARGET_DIR}' appears at "
        f"line {mkdir_line} but the SCP transfer appears at line {scp_line}.\n"
        f"mkdir MUST come first so the directory exists when scp runs.\n"
        f"Current script:\n{script}"
    )


def test_mkdir_p_targets_correct_path() -> None:
    """The mkdir -p command must target exactly /root/solanatrilly (AC-8.1).

    The SCP destination is '/root/solanatrilly/docker-compose.staging.yml' so
    the parent directory that must be created is /root/solanatrilly.
    A mkdir of any other path does not fix the B1 root cause.
    """
    script = _deploy_run_script(_load_deploy())
    match = _MKDIR_RE.search(script)
    assert match is not None, (
        f"AC-8.1: 'mkdir -p {SCP_TARGET_DIR}' not found in deploy script."
    )
    # The regex already enforces the correct path, so finding a match is sufficient.
    matched_text = match.group(0)
    assert SCP_TARGET_DIR in matched_text, (
        f"AC-8.1: mkdir path must include '{SCP_TARGET_DIR}'. "
        f"Found: {matched_text!r}"
    )


def test_scp_target_path_is_root_solanatrilly() -> None:
    """The SCP in the deploy script must copy to /root/solanatrilly/ (AC-8.1).

    Verifies that the destination path that triggered the B1 bug is still
    present and that the mkdir fix targets the right directory.
    """
    script = _deploy_run_script(_load_deploy())
    assert _SCP_TARGET_RE.search(script), (
        f"AC-8.1: no SCP command targeting '{SCP_TARGET_DIR}' found in the "
        "deploy script. Expected a line like:\n"
        f"  scp … docker-compose.staging.yml ${{VPS_USER}}@${{VPS_HOST}}:{SCP_TARGET_DIR}/...\n"
        "The scp step must be present for the mkdir ordering assertion to be meaningful."
    )
