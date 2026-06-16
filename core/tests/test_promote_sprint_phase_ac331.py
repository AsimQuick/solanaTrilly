# ---
# module: core.tests.test_promote_sprint_phase_ac331
# sprint: sprint-7
# story: US-33 AC-33.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: tools.promote_sprint_phase, json, subprocess, sys, tempfile, pathlib
# ---
# AC-33.1: F2 — phase-promotion is MECHANICAL, not a checklist.
# Verified by: a sprintN.json reading phase:'planning' while all stories are 'done'
# is auto-promoted (or the deploy is deterministically blocked with the remedy printed),
# reproducing and closing the exact failure mode that tripped the sprint-end deploy
# three consecutive sprints (D2→E1→F2; runs 27555736146/27555822147, 27593136555,
# 27627161427).

import json
import subprocess
import sys
from pathlib import Path

# Compile-time ImportError trap — deleting/renaming these functions breaks collection
from tools.promote_sprint_phase import (
    compute_promoted_phase,
    promote_sprint_phase,
    remedy_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sprint(phase: str, story_statuses: list[str]) -> dict:
    """Build a minimal sprint dict with the given phase and story statuses."""
    stories = [{"id": f"US-{i}", "status": s} for i, s in enumerate(story_statuses, 1)]
    return {"sprint": "sprint-test", "phase": phase, "stories": stories}


def _write_sprint(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# compute_promoted_phase — unit tests
# ---------------------------------------------------------------------------


def test_planning_phase_all_stories_done_returns_complete():
    """Core AC-33.1 case: the D2→E1→F2 failure mode."""
    data = _sprint("planning", ["done", "done", "done"])
    assert compute_promoted_phase(data) == "complete"


def test_in_progress_phase_all_stories_done_returns_complete():
    data = _sprint("in-progress", ["done", "done"])
    assert compute_promoted_phase(data) == "complete"


def test_complete_phase_returns_none():
    data = _sprint("complete", ["done", "done"])
    assert compute_promoted_phase(data) is None


def test_planning_phase_with_incomplete_stories_returns_none():
    data = _sprint("planning", ["done", "in-progress"])
    assert compute_promoted_phase(data) is None


def test_planning_phase_empty_stories_returns_none():
    data = _sprint("planning", [])
    assert compute_promoted_phase(data) is None


def test_unknown_phase_returns_none():
    data = _sprint("review", ["done"])
    assert compute_promoted_phase(data) is None


# ---------------------------------------------------------------------------
# promote_sprint_phase — unit tests
# ---------------------------------------------------------------------------


def test_promote_returns_complete_dict_and_promoted_to():
    data = _sprint("planning", ["done", "done"])
    updated, promoted_to = promote_sprint_phase(data)
    assert promoted_to == "complete"
    assert updated["phase"] == "complete"


def test_promote_does_not_mutate_input():
    data = _sprint("planning", ["done"])
    original_phase = data["phase"]
    promote_sprint_phase(data)
    assert data["phase"] == original_phase


def test_no_promotion_needed_returns_original_and_none():
    data = _sprint("complete", ["done"])
    updated, promoted_to = promote_sprint_phase(data)
    assert promoted_to is None
    assert updated is data


def test_promote_preserves_all_other_fields():
    data = {
        "sprint": "sprint-7",
        "phase": "planning",
        "goal": "some goal",
        "stories": [{"id": "US-1", "status": "done"}],
    }
    updated, _ = promote_sprint_phase(data)
    assert updated["sprint"] == "sprint-7"
    assert updated["goal"] == "some goal"
    assert updated["stories"] == data["stories"]


# ---------------------------------------------------------------------------
# remedy_message — content tests
# ---------------------------------------------------------------------------


def test_remedy_message_contains_stale_phase():
    msg = remedy_message(Path("scrum-master/sprint7.json"), "planning", "complete")
    assert "planning" in msg


def test_remedy_message_contains_promoted_to():
    msg = remedy_message(Path("scrum-master/sprint7.json"), "planning", "complete")
    assert "complete" in msg


def test_remedy_message_contains_filename():
    msg = remedy_message(Path("scrum-master/sprint7.json"), "planning", "complete")
    assert "sprint7.json" in msg


def test_remedy_message_contains_remedy_keyword():
    msg = remedy_message(Path("scrum-master/sprint7.json"), "planning", "complete")
    assert "REMEDY" in msg


# ---------------------------------------------------------------------------
# CLI integration — auto-promote mode (writes files in place)
# ---------------------------------------------------------------------------


def test_cli_auto_promotes_stale_phase_file(tmp_path):
    """Main AC-33.1 integration test: stale file is promoted in place."""
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("planning", ["done", "done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    updated = json.loads(f.read_text())
    assert updated["phase"] == "complete"
    assert "AUTO-PROMOTED" in result.stdout


def test_cli_no_promotion_needed_exits_0(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("complete", ["done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "AUTO-PROMOTED" not in result.stdout


# ---------------------------------------------------------------------------
# CLI integration — --check-only mode (blocks with remedy, does not write)
# ---------------------------------------------------------------------------


def test_cli_check_only_exits_1_on_stale_phase(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("planning", ["done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--check-only", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_cli_check_only_prints_remedy_to_stderr(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("planning", ["done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--check-only", str(f)],
        capture_output=True,
        text=True,
    )
    assert "REMEDY" in result.stderr
    assert "complete" in result.stderr


def test_cli_check_only_does_not_write_file(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("planning", ["done"]))

    subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--check-only", str(f)],
        capture_output=True,
        text=True,
    )
    unchanged = json.loads(f.read_text())
    assert unchanged["phase"] == "planning"


def test_cli_check_only_exits_0_when_no_stale_phase(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("complete", ["done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--check-only", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# CLI integration — --dry-run mode
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_what_would_change_without_writing(tmp_path):
    f = tmp_path / "sprint_test.json"
    _write_sprint(f, _sprint("planning", ["done"]))

    result = subprocess.run(
        [sys.executable, "tools/promote_sprint_phase.py", "--dry-run", str(f)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DRY-RUN" in result.stdout
    unchanged = json.loads(f.read_text())
    assert unchanged["phase"] == "planning"


# ---------------------------------------------------------------------------
# Regression: reproduce the exact D2→E1→F2 failure mode and confirm it is
# now CLOSED by the auto-promotion step running before the integrity check.
# ---------------------------------------------------------------------------


def test_d2_e1_f2_failure_mode_is_closed(tmp_path):
    """Reproduces the three-sprint recurring failure:
    sprint*.json has phase='planning' while all stories are 'done'.
    Without auto-promotion the US-13 guard fails CI.
    With auto-promotion the guard sees phase='complete' and passes.
    """
    from tools.sprint_integrity_check import check_sprint

    stale_sprint = _sprint("planning", ["done", "done", "done"])

    # Before promotion: integrity check fires a violation (the old failure)
    violations_before = check_sprint(stale_sprint)
    assert any("stale" in v.lower() or "phase" in v.lower() for v in violations_before), (
        "Expected the integrity check to detect a stale phase before promotion"
    )

    # After promotion: integrity check sees no phase violation
    promoted_sprint, promoted_to = promote_sprint_phase(stale_sprint)
    assert promoted_to == "complete"
    violations_after = check_sprint(promoted_sprint)
    phase_violations = [v for v in violations_after if "phase" in v.lower()]
    assert phase_violations == [], (
        f"Expected no phase violations after promotion; got: {phase_violations}"
    )


def test_ci_step_order_auto_promote_before_integrity_check():
    """Verify that the CI yaml wires the auto-promote step before the integrity check."""
    ci_path = Path(".github/workflows/ci.yml")
    content = ci_path.read_text()

    promote_pos = content.find("promote_sprint_phase")
    integrity_pos = content.find("sprint_integrity_check")

    assert promote_pos != -1, "promote_sprint_phase.py must be wired in ci.yml (AC-33.1)"
    assert integrity_pos != -1, "sprint_integrity_check.py must still be in ci.yml"
    assert promote_pos < integrity_pos, (
        "Auto-promote step must appear BEFORE the integrity check in ci.yml "
        f"(promote at {promote_pos}, integrity at {integrity_pos})"
    )
