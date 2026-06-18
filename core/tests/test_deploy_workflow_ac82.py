# ---
# module: core.tests.test_deploy_workflow_ac82
# sprint: sprint-3
# story: US-8 AC-8.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, yaml
# ---
"""AC-8.2 — deploy.yml must include a 'workflow_dispatch:' trigger.

Root cause (retrospective B2): the orchestrator's manual dispatch of deploy.yml
returned HTTP 422 'Workflow does not have workflow_dispatch trigger' because the
workflow's 'on:' block only contained 'push: branches: [main]'.

Fix: add 'workflow_dispatch:' to the 'on:' block so the workflow can be fired
on demand via the GitHub UI or the gh CLI, independent of a push.

Operator decision (2026-06-18): deploys are now deliberate-dispatch-only — the
push trigger was intentionally removed because a push-to-main trigger fired a full
~5-min VPS deploy on every commit (including sprint-state commits), wasting GitHub
Actions minutes. The orchestrator dispatches one deploy per sprint boundary via
workflow_dispatch. The push trigger must therefore be ABSENT.

Tests:
  test_workflow_dispatch_trigger_present
  test_workflow_dispatch_is_top_level_on_key
  test_push_trigger_absent_dispatch_only
"""
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


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-8.2 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_workflow_dispatch_trigger_present() -> None:
    """deploy.yml must declare 'workflow_dispatch:' in its 'on:' block (AC-8.2).

    Without this trigger GitHub returns HTTP 422 when the workflow is fired
    on demand via 'gh workflow run' or the GitHub UI (retrospective B2).
    """
    data = _load_deploy()
    on_block = data.get("on") or data.get(True)  # YAML 1.1 maps 'on' to True
    assert on_block is not None, (
        "AC-8.2: deploy.yml has no 'on:' block. "
        "Add 'on: workflow_dispatch:' (dispatch-only — no push trigger)."
    )
    assert "workflow_dispatch" in on_block, (
        "AC-8.2: 'workflow_dispatch' trigger is missing from deploy.yml's 'on:' block.\n"
        "The orchestrator dispatch (retrospective B2) returned HTTP 422 because this "
        "trigger was absent.\n"
        "deploy.yml is dispatch-only (operator decision 2026-06-18); add:\n\n"
        "  on:\n"
        "    workflow_dispatch:\n"
    )


def test_workflow_dispatch_is_top_level_on_key() -> None:
    """'workflow_dispatch' must be a direct key of the 'on:' mapping (AC-8.2).

    A nested or mis-scoped placement would not register the trigger with GitHub.
    """
    data = _load_deploy()
    on_block = data.get("on") or data.get(True)
    assert isinstance(on_block, dict), (
        "AC-8.2: deploy.yml 'on:' block must be a mapping (dict) so both "
        "'push' and 'workflow_dispatch' can be listed as sibling keys."
    )
    keys = set(on_block.keys())
    assert "workflow_dispatch" in keys, (
        f"AC-8.2: 'workflow_dispatch' not found as a top-level key of 'on:'. "
        f"Current 'on:' keys: {sorted(str(k) for k in keys)}"
    )


def test_push_trigger_absent_dispatch_only() -> None:
    """deploy.yml must have NO 'push' trigger — deploys are workflow_dispatch-only.

    Operator decision (2026-06-18): the push trigger was intentionally removed.
    A push-to-main trigger fired a full ~5-min VPS deploy on every commit to main
    (including many sprint-state commits), wasting GitHub Actions minutes. Deploys
    now fire ONLY via workflow_dispatch — the orchestrator dispatches one deploy
    per sprint boundary. workflow_dispatch remains present (asserted above).
    """
    data = _load_deploy()
    on_block = data.get("on") or data.get(True)
    assert on_block is not None, "AC-8.2: deploy.yml has no 'on:' block."
    assert "push" not in on_block, (
        "deploy is dispatch-only; do not re-add the push trigger "
        "(operator decision 2026-06-18 — a push-to-main trigger wastes Actions "
        "minutes by deploying on every commit). Deploys fire only via "
        "workflow_dispatch, dispatched once per sprint boundary."
    )
