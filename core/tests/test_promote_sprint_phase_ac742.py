# ---
# module: core.tests.test_promote_sprint_phase_ac742
# sprint: sprint-14
# story: US-74 AC-74.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: tools.promote_sprint_phase, json, subprocess, sys, pathlib
# ---
# AC-74.2: A mechanical phase-promotion tool (tools/promote_sprint_phase.py) promotes
# a sprint's top-level phase AND each story's dev_status to the correct closeout value
# (NOT exempted/carved-out) and is wired to run BEFORE any sprint-end deploy (D2/D3).
#
# Verified by:
#   (1) Unit tests covering three phase transitions (planning→done, in-progress→done,
#       review→done) at closeout.
#   (2) Unit tests covering dev_status promotion (story with all ACs done gets
#       dev_status='done'; partial ACs and already-done stories are not changed).
#   (3) A structural check that the tool is referenced in the sprint-end deploy/closeout
#       path (ci.yml and deploy.yml — both BEFORE the VPS deploy step).

import json
import subprocess
import sys
from pathlib import Path

from tools.promote_sprint_phase import (
    compute_promoted_phase,
    promote_sprint_phase,
    promote_story_dev_statuses,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _story(story_id: str, story_status: str, dev_status: str, ac_dev_statuses: list[str]) -> dict:
    """Build a story dict with optional ACs, each having the given dev_status."""
    acs = [
        {"id": f"{story_id}.{i}", "dev_status": s, "checked": s == "done"}
        for i, s in enumerate(ac_dev_statuses, 1)
    ]
    s = {"id": story_id, "status": story_status, "dev_status": dev_status}
    if acs:
        s["acceptance_criteria"] = acs
    return s


def _sprint_with_stories(phase: str, stories: list[dict]) -> dict:
    return {"sprint": "sprint-test", "phase": phase, "stories": stories}


def _all_done_stories() -> list[dict]:
    """Three stories all fully done with all ACs done."""
    return [
        _story("US-A", "done", "done", ["done", "done"]),
        _story("US-B", "done", "done", ["done"]),
        _story("US-C", "done", "done", ["done", "done", "done"]),
    ]


def _stale_dev_status_stories() -> list[dict]:
    """Stories with all ACs done but dev_status='not-started' (the recurring artifact)."""
    return [
        _story("US-A", "done", "not-started", ["done", "done"]),
        _story("US-B", "done", "not-started", ["done"]),
    ]


# ---------------------------------------------------------------------------
# ImportError trap — these symbols must stay importable (AC-74.2 tool integrity)
# ---------------------------------------------------------------------------


def test_promote_story_dev_statuses_importable():
    """promote_story_dev_statuses must be importable from the tool module."""
    assert callable(promote_story_dev_statuses)


# ---------------------------------------------------------------------------
# Phase transitions — three starting phases all promote to 'done' (AC-74.2)
# ---------------------------------------------------------------------------


def test_planning_phase_all_stories_done_promotes_to_done():
    """Transition 1 of 3: planning → done when all stories are done."""
    stories = [{"id": "US-1", "status": "done"}, {"id": "US-2", "status": "done"}]
    data = _sprint_with_stories("planning", stories)
    result = compute_promoted_phase(data)
    assert result == "done", f"Expected 'done', got {result!r}"


def test_in_progress_phase_all_stories_done_promotes_to_done():
    """Transition 2 of 3: in-progress → done when all stories are done."""
    stories = [{"id": "US-1", "status": "done"}]
    data = _sprint_with_stories("in-progress", stories)
    result = compute_promoted_phase(data)
    assert result == "done", f"Expected 'done', got {result!r}"


def test_review_phase_all_stories_done_promotes_to_done():
    """Transition 3 of 3: review → done when all stories are done (AC-74.2 new case)."""
    stories = [{"id": "US-1", "status": "done"}, {"id": "US-2", "status": "done"}]
    data = _sprint_with_stories("review", stories)
    result = compute_promoted_phase(data)
    assert result == "done", f"Expected 'done', got {result!r}"


def test_done_phase_returns_none():
    """Already at closeout: done → no promotion needed."""
    stories = [{"id": "US-1", "status": "done"}]
    data = _sprint_with_stories("done", stories)
    assert compute_promoted_phase(data) is None


def test_complete_phase_returns_none():
    """Already at closeout: complete → no promotion needed."""
    stories = [{"id": "US-1", "status": "done"}]
    data = _sprint_with_stories("complete", stories)
    assert compute_promoted_phase(data) is None


def test_review_phase_with_incomplete_stories_returns_none():
    """review phase with an in-progress story should NOT promote."""
    stories = [{"id": "US-1", "status": "done"}, {"id": "US-2", "status": "in-progress"}]
    data = _sprint_with_stories("review", stories)
    assert compute_promoted_phase(data) is None


# ---------------------------------------------------------------------------
# dev_status promotion — core AC-74.2 behavior
# ---------------------------------------------------------------------------


def test_story_with_all_acs_done_gets_dev_status_promoted():
    """When all ACs are dev_status='done', the story's dev_status is promoted to 'done'."""
    stories = [_story("US-A", "done", "not-started", ["done", "done"])]
    updated, any_promoted = promote_story_dev_statuses(stories)
    assert any_promoted is True
    assert updated[0]["dev_status"] == "done"


def test_story_already_done_dev_status_not_changed():
    """A story whose dev_status is already 'done' should not be touched."""
    stories = [_story("US-A", "done", "done", ["done", "done"])]
    updated, any_promoted = promote_story_dev_statuses(stories)
    assert any_promoted is False
    assert updated[0]["dev_status"] == "done"


def test_story_with_partial_acs_not_promoted():
    """If any AC is not dev_status='done', the story dev_status should not be promoted."""
    stories = [_story("US-A", "done", "not-started", ["done", "not-started"])]
    updated, any_promoted = promote_story_dev_statuses(stories)
    assert any_promoted is False
    assert updated[0]["dev_status"] == "not-started"


def test_story_without_acs_not_promoted():
    """Stories with no ACs are not promoted (no basis for the condition)."""
    stories = [{"id": "US-A", "status": "done", "dev_status": "not-started"}]
    updated, any_promoted = promote_story_dev_statuses(stories)
    assert any_promoted is False
    assert updated[0]["dev_status"] == "not-started"


def test_mixed_stories_only_eligible_ones_promoted():
    """Only stories meeting the all-ACs-done condition are promoted; others unchanged."""
    stories = [
        _story("US-A", "done", "not-started", ["done", "done"]),  # eligible
        _story("US-B", "done", "not-started", ["done", "not-started"]),  # not eligible
        _story("US-C", "done", "done", ["done"]),  # already done
    ]
    updated, any_promoted = promote_story_dev_statuses(stories)
    assert any_promoted is True
    assert updated[0]["dev_status"] == "done"
    assert updated[1]["dev_status"] == "not-started"
    assert updated[2]["dev_status"] == "done"


def test_promote_story_dev_statuses_does_not_mutate_input():
    """promote_story_dev_statuses must not modify the input stories in place."""
    stories = [_story("US-A", "done", "not-started", ["done"])]
    original_dev_status = stories[0]["dev_status"]
    promote_story_dev_statuses(stories)
    assert stories[0]["dev_status"] == original_dev_status


# ---------------------------------------------------------------------------
# Combined: promote_sprint_phase promotes BOTH phase and dev_status
# ---------------------------------------------------------------------------


def test_closeout_promotes_both_phase_and_dev_status():
    """At closeout: stale phase + stale dev_statuses are both promoted in one call."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("planning", stories)
    updated, promoted_to = promote_sprint_phase(data)
    assert promoted_to == "done"
    assert updated["phase"] == "done"
    for story in updated["stories"]:
        assert story["dev_status"] == "done", (
            f"Story {story['id']} dev_status should be 'done', got {story['dev_status']!r}"
        )


def test_closeout_promotes_dev_status_even_if_phase_already_done():
    """If phase is already 'done' but dev_statuses are stale, dev_statuses are promoted."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("done", stories)
    updated, promoted_to = promote_sprint_phase(data)
    assert promoted_to is None  # phase unchanged
    assert updated is not data  # data was modified (dev_statuses)
    for story in updated["stories"]:
        assert story["dev_status"] == "done"


def test_no_promotion_needed_returns_original_object():
    """When nothing needs promotion, the SAME object is returned (not a copy)."""
    stories = _all_done_stories()
    data = _sprint_with_stories("done", stories)
    updated, promoted_to = promote_sprint_phase(data)
    assert promoted_to is None
    assert updated is data


def test_promote_sprint_phase_does_not_mutate_input():
    """promote_sprint_phase must never modify the input dict in place."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("planning", stories)
    original_phase = data["phase"]
    original_dev = data["stories"][0]["dev_status"]
    promote_sprint_phase(data)
    assert data["phase"] == original_phase
    assert data["stories"][0]["dev_status"] == original_dev


# ---------------------------------------------------------------------------
# CLI integration — promote both phase and dev_status via subprocess
# ---------------------------------------------------------------------------


def _write_sprint(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_cli_auto_promotes_phase_and_dev_status(tmp_path):
    """CLI default mode promotes both phase and dev_status in place."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("planning", stories)
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, data)

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    updated = json.loads(f.read_text())
    assert updated["phase"] == "done"
    for story in updated["stories"]:
        assert story["dev_status"] == "done"
    assert "AUTO-PROMOTED" in result.stdout


def test_cli_check_only_blocks_on_stale_dev_status(tmp_path):
    """--check-only exits 1 when any story has stale dev_status."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("done", stories)  # phase already done, but dev_status stale
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, data)

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--check-only", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "STALE DEV_STATUS" in result.stderr


def test_cli_dry_run_reports_both_changes(tmp_path):
    """--dry-run prints both phase and dev_status changes without writing."""
    stories = _stale_dev_status_stories()
    data = _sprint_with_stories("planning", stories)
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, data)

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--dry-run", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DRY-RUN" in result.stdout
    unchanged = json.loads(f.read_text())
    assert unchanged["phase"] == "planning"  # not written


# ---------------------------------------------------------------------------
# Deploy-path reference check — tool wired BEFORE sprint-end deploy (AC-74.2)
# ---------------------------------------------------------------------------


def test_promote_sprint_phase_referenced_in_ci_yml():
    """promote_sprint_phase.py must be referenced in ci.yml (it runs before the integrity check)."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml missing"
    content = ci_path.read_text()
    assert "promote_sprint_phase" in content, (
        "promote_sprint_phase.py must be wired in ci.yml as the pre-integrity-check step"
    )


def test_promote_sprint_phase_referenced_in_deploy_yml():
    """promote_sprint_phase.py must be referenced in deploy.yml deploy job before VPS deploy."""
    deploy_path = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
    assert deploy_path.exists(), "deploy.yml missing"
    content = deploy_path.read_text()
    assert "promote_sprint_phase" in content, (
        "promote_sprint_phase.py must be wired in deploy.yml before the VPS deploy step"
    )


def test_promote_step_before_vps_deploy_in_deploy_yml():
    """In deploy.yml, the promoter step must appear before 'Deploy to VPS'."""
    deploy_path = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
    content = deploy_path.read_text()
    promote_pos = content.find("promote_sprint_phase")
    deploy_pos = content.find("Deploy to VPS")
    assert promote_pos != -1, "promote_sprint_phase not found in deploy.yml"
    assert deploy_pos != -1, "'Deploy to VPS' step not found in deploy.yml"
    assert promote_pos < deploy_pos, (
        f"promoter (pos {promote_pos}) must appear before 'Deploy to VPS' (pos {deploy_pos})"
    )


def test_promote_step_before_integrity_check_in_ci_yml():
    """In ci.yml, the promoter step must appear before the integrity check."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    content = ci_path.read_text()
    promote_pos = content.find("promote_sprint_phase")
    integrity_pos = content.find("sprint_integrity_check")
    assert promote_pos != -1, "promote_sprint_phase not found in ci.yml"
    assert integrity_pos != -1, "sprint_integrity_check not found in ci.yml"
    assert promote_pos < integrity_pos, (
        f"promoter (pos {promote_pos}) must appear before integrity check (pos {integrity_pos})"
    )
