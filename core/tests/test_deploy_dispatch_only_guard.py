# ---
# module: core.tests.test_deploy_dispatch_only_guard
# sprint: sprint-14
# story: ops-guard (deploy cost regression)
# status: implemented
# created-by: operator
# last-updated: 2026-06-18
# dependencies: pathlib, re
# ---
"""Guard: the Deploy workflow must be DELIBERATE (workflow_dispatch) ONLY.

A `push:` trigger on .github/workflows/deploy.yml fires a full ~5-minute VPS
deploy on EVERY commit to main — including the orchestrator's many
"[US-XX] Mark AC done" sprint-state commits and every PR-merge commit. That
burns GitHub Actions minutes and produces red deploy noise. Deploys are meant
to be dispatched deliberately by the gitops orchestrator at each sprint
boundary (handle_deploy -> workflow_dispatch).

This trigger has been silently re-added by dev agents more than once
(sprints 8-10, then again in sprint-13). This test pins the invariant: the
deploy workflow's trigger block must contain workflow_dispatch and must NOT
contain a push trigger. If a change re-adds `push:`, CI fails and the PR
cannot merge.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def _trigger_block_lines() -> list[str]:
    """Return the lines of the top-level `on:` trigger block in deploy.yml.

    Pure-text parsing (no YAML lib) avoids the well-known gotcha where YAML
    coerces the bare key `on:` to the boolean True. We collect every line after
    the top-level `on:` line up to (but not including) the next top-level key.
    """
    assert DEPLOY_YML.exists(), f"deploy workflow not found at {DEPLOY_YML}"
    lines = DEPLOY_YML.read_text().splitlines()

    start = None
    for i, line in enumerate(lines):
        # top-level `on:` (no indentation), tolerate `on:` or `"on":`
        if re.match(r'^(on|"on"|\'on\'):\s*$', line):
            start = i + 1
            break
    assert start is not None, "deploy.yml must declare a top-level `on:` trigger block"

    block: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            block.append(line)
            continue
        # next top-level key (no leading whitespace) ends the block
        if not line[0].isspace():
            break
        block.append(line)
    return block


def test_deploy_has_no_push_trigger():
    """deploy.yml must NOT trigger on push (would deploy on every commit)."""
    block = _trigger_block_lines()
    offenders = [ln for ln in block if re.match(r'\s*push:\s*$', ln) or re.match(r'\s*push:\s', ln)]
    assert not offenders, (
        "deploy.yml has a `push:` trigger — this deploys on EVERY commit to main "
        "(~5 min each, including sprint-state commits) and is forbidden. Deploys are "
        "dispatched deliberately by the orchestrator at the sprint boundary. "
        f"Offending line(s): {offenders}"
    )


def test_deploy_supports_workflow_dispatch():
    """deploy.yml must remain dispatchable (the orchestrator's deploy mechanism)."""
    block = _trigger_block_lines()
    assert any("workflow_dispatch" in ln for ln in block), (
        "deploy.yml must declare workflow_dispatch — the orchestrator dispatches "
        "the deliberate sprint-boundary deploy through it"
    )
