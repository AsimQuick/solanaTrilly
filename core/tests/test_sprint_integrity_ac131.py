# ---
# module: core.tests.test_sprint_integrity_ac131
# sprint: sprint-4
# story: US-13 AC-13.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: tools.sprint_integrity_check
# ---

from tools.sprint_integrity_check import check_sprint


def _sprint(stories):
    return {"sprint": "sprint-4", "stories": stories}


def _story(sid, status, tester_status, acs=None):
    return {
        "id": sid,
        "status": status,
        "tester_status": tester_status,
        "acceptance_criteria": acs or [],
    }


def _ac(aid, checked, tester_status):
    return {"id": aid, "checked": checked, "tester_status": tester_status}


# --- passing fixtures ---


def test_clean_sprint_passes():
    data = _sprint([_story("US-1", "done", "approved", [_ac("1.1", True, "approved")])])
    assert check_sprint(data) == []


def test_done_with_approved_tester_passes():
    data = _sprint([_story("US-2", "done", "approved")])
    assert check_sprint(data) == []


def test_not_done_with_fail_tester_passes():
    data = _sprint([_story("US-3", "in-progress", "failed")])
    assert check_sprint(data) == []


def test_ac_unchecked_with_fail_tester_passes():
    data = _sprint([_story("US-4", "done", "approved", [_ac("4.1", False, "failed")])])
    assert check_sprint(data) == []


def test_empty_stories_passes():
    data = _sprint([])
    assert check_sprint(data) == []


# --- violating fixtures ---


def test_story_done_tester_failed_is_violation():
    data = _sprint([_story("US-8", "done", "failed")])
    result = check_sprint(data)
    assert len(result) == 1
    assert "US-8" in result[0]
    assert "failed" in result[0]


def test_story_done_tester_fail_is_violation():
    data = _sprint([_story("US-8", "done", "fail")])
    result = check_sprint(data)
    assert len(result) == 1


def test_story_done_tester_blocked_is_violation():
    data = _sprint([_story("US-8", "done", "blocked")])
    result = check_sprint(data)
    assert len(result) == 1


def test_ac_checked_tester_failed_is_violation():
    data = _sprint([_story("US-5", "done", "approved", [_ac("5.1", True, "failed")])])
    result = check_sprint(data)
    assert len(result) == 1
    assert "5.1" in result[0]


def test_ac_checked_tester_fail_is_violation():
    data = _sprint([_story("US-5", "done", "approved", [_ac("5.1", True, "fail")])])
    result = check_sprint(data)
    assert len(result) == 1


def test_ac_checked_tester_blocked_is_violation():
    data = _sprint([_story("US-5", "done", "approved", [_ac("5.1", True, "blocked")])])
    result = check_sprint(data)
    assert len(result) == 1


def test_multiple_violations_all_reported():
    data = _sprint(
        [
            _story("US-8", "done", "fail"),
            _story(
                "US-9",
                "done",
                "blocked",
                [_ac("9.1", True, "failed")],
            ),
        ]
    )
    result = check_sprint(data)
    assert len(result) == 3
