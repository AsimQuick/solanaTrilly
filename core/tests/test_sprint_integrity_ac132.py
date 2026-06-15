# ---
# module: core.tests.test_sprint_integrity_ac132
# sprint: sprint-4
# story: US-13 AC-13.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: tools.sprint_integrity_check
# ---

from tools.sprint_integrity_check import check_sprint


def _sprint(stories, phase="planning"):
    return {"sprint": "sprint-4", "phase": phase, "stories": stories}


def _story(sid, status, tester_status="not-started", dev_status="not-started", acs=None):
    return {
        "id": sid,
        "status": status,
        "tester_status": tester_status,
        "dev_status": dev_status,
        "acceptance_criteria": acs or [],
    }


def _ac(aid, checked=False, tester_status="not-started", dev_status="not-started"):
    return {
        "id": aid,
        "checked": checked,
        "tester_status": tester_status,
        "dev_status": dev_status,
    }


# --- stale story dev_status: violation cases ---


def test_story_all_acs_done_story_not_started_is_violation():
    """All ACs have dev_status:done but story dev_status is not-started → stale."""
    data = _sprint(
        [
            _story(
                "US-1",
                "in-progress",
                dev_status="not-started",
                acs=[_ac("1.1", dev_status="done"), _ac("1.2", dev_status="done")],
            )
        ]
    )
    result = check_sprint(data)
    assert len(result) == 1
    assert "US-1" in result[0]
    assert "stale" in result[0]
    assert "not-started" in result[0]


def test_story_all_acs_done_story_in_progress_is_violation():
    """All ACs have dev_status:done but story dev_status is in-progress → stale."""
    data = _sprint(
        [
            _story(
                "US-2",
                "in-progress",
                dev_status="in-progress",
                acs=[_ac("2.1", dev_status="done")],
            )
        ]
    )
    result = check_sprint(data)
    assert len(result) == 1
    assert "US-2" in result[0]
    assert "stale" in result[0]
    assert "in-progress" in result[0]


# --- stale story dev_status: passing cases ---


def test_story_all_acs_done_story_done_passes():
    """All ACs done and story done → consistent, no violation."""
    data = _sprint(
        [
            _story(
                "US-3",
                "done",
                tester_status="approved",
                dev_status="done",
                acs=[_ac("3.1", dev_status="done"), _ac("3.2", dev_status="done")],
            )
        ],
        phase="done",
    )
    assert check_sprint(data) == []


def test_story_partial_acs_done_story_in_progress_passes():
    """Only some ACs are done — story in-progress is not stale yet."""
    data = _sprint(
        [
            _story(
                "US-4",
                "in-progress",
                dev_status="in-progress",
                acs=[_ac("4.1", dev_status="done"), _ac("4.2", dev_status="not-started")],
            )
        ]
    )
    assert check_sprint(data) == []


def test_story_no_acs_not_started_passes():
    """A story with no ACs cannot be stale via this rule."""
    data = _sprint([_story("US-5", "not-started", dev_status="not-started", acs=[])])
    assert check_sprint(data) == []


def test_story_acs_have_no_dev_status_passes():
    """ACs without dev_status field are not treated as done — no violation."""
    data = _sprint(
        [
            _story(
                "US-6",
                "in-progress",
                dev_status="in-progress",
                acs=[{"id": "6.1", "checked": False, "tester_status": "not-started"}],
            )
        ]
    )
    assert check_sprint(data) == []


# --- stale sprint phase: violation cases ---


def test_sprint_phase_planning_all_stories_done_is_violation():
    """phase='planning' while all stories are done → stale phase."""
    data = _sprint(
        [
            _story("US-1", "done", tester_status="approved", dev_status="done"),
            _story("US-2", "done", tester_status="approved", dev_status="done"),
        ],
        phase="planning",
    )
    result = check_sprint(data)
    # Expect exactly the phase violation (stories pass AC-13.1 and stale-story checks)
    phase_violations = [v for v in result if "phase" in v]
    assert len(phase_violations) == 1
    assert "planning" in phase_violations[0]
    assert "stale" in phase_violations[0]


def test_sprint_phase_in_progress_all_stories_done_is_violation():
    """phase='in-progress' while all stories are done → stale phase."""
    data = _sprint(
        [_story("US-1", "done", tester_status="approved", dev_status="done")],
        phase="in-progress",
    )
    result = check_sprint(data)
    phase_violations = [v for v in result if "phase" in v]
    assert len(phase_violations) == 1
    assert "in-progress" in phase_violations[0]


# --- stale sprint phase: passing cases ---


def test_sprint_phase_planning_stories_not_all_done_passes():
    """phase='planning' with mixed story statuses → not stale."""
    data = _sprint(
        [
            _story("US-1", "done", tester_status="approved", dev_status="done"),
            _story("US-2", "in-progress", dev_status="in-progress"),
        ],
        phase="planning",
    )
    phase_violations = [v for v in check_sprint(data) if "phase" in v]
    assert phase_violations == []


def test_sprint_phase_done_all_stories_done_passes():
    """phase='done' with all stories done → consistent, no violation."""
    data = _sprint(
        [_story("US-1", "done", tester_status="approved", dev_status="done")],
        phase="done",
    )
    assert check_sprint(data) == []


def test_sprint_phase_planning_no_stories_passes():
    """phase='planning' with no stories → no violation (nothing to compare)."""
    data = _sprint([], phase="planning")
    assert check_sprint(data) == []


# --- combined cases ---


def test_both_stale_violations_reported_together():
    """Stale story dev_status fires for in-progress stories; stale phase fires when all done."""
    # Stale story: story is in-progress (not done) with all ACs finished
    stale_story_data = _sprint(
        [
            _story(
                "US-1",
                "in-progress",
                dev_status="not-started",
                acs=[_ac("1.1", dev_status="done")],
            )
        ],
        phase="planning",
    )
    story_result = check_sprint(stale_story_data)
    stale_story = [v for v in story_result if "stale" in v and "phase" not in v]
    assert len(stale_story) == 1
    assert "US-1" in stale_story[0]

    # Stale phase: all stories done but phase still planning
    stale_phase_data = _sprint(
        [_story("US-2", "done", tester_status="approved", dev_status="done")],
        phase="planning",
    )
    phase_result = check_sprint(stale_phase_data)
    stale_phase = [v for v in phase_result if "phase" in v and "stale" in v]
    assert len(stale_phase) == 1


def test_story_done_with_stale_dev_status_not_flagged():
    """A done story with stale dev_status is not flagged — closed stories are past dev tracking."""
    data = _sprint(
        [
            _story(
                "US-1",
                "done",
                tester_status="approved",
                dev_status="not-started",
                acs=[_ac("1.1", dev_status="done")],
            )
        ],
        phase="planning",
    )
    result = check_sprint(data)
    stale_story = [v for v in result if "stale" in v and "phase" not in v]
    assert stale_story == []
