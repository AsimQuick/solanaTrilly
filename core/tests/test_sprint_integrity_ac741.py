# ---
# module: core.tests.test_sprint_integrity_ac741
# sprint: sprint-14
# story: US-74 AC-74.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: tools.sprint_integrity_check
# ---
"""
AC-74.1 hardening tests for sprint_integrity_check.py.

Two new invariants verified here:
  (1) Phase check: phase in {'complete','done'} + any story tester_status in
      {'failed','blocked'} -> violation (NEW code, AC-74.1).
  (2) Dev-status artifact: a story whose ACs are ALL dev_status:'done' but
      whose story-level dev_status is 'not-started' -> violation (EXISTING code,
      confirmed by planted fixture per AC-74.1).

The planted fixture exhibits BOTH artifacts simultaneously; the normalized
fixture has neither and must pass cleanly.
"""

from tools.sprint_integrity_check import check_sprint

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sprint(stories, phase="in-progress"):
    return {"sprint": "sprint-x", "phase": phase, "stories": stories}


def _story(
    sid,
    status="in-progress",
    tester_status="not-started",
    dev_status="not-started",
    acs=None,
):
    return {
        "id": sid,
        "status": status,
        "tester_status": tester_status,
        "dev_status": dev_status,
        "acceptance_criteria": acs or [],
    }


def _ac(aid, dev_status="not-started", checked=False, tester_status="not-started"):
    return {
        "id": aid,
        "dev_status": dev_status,
        "checked": checked,
        "tester_status": tester_status,
    }


# ---------------------------------------------------------------------------
# AC-74.1 check 1 — phase:complete/done + failed/blocked story
# ---------------------------------------------------------------------------


def test_planted_phase_complete_failed_story_is_violation():
    """phase:complete + story tester_status:failed must be caught."""
    data = _sprint(
        [_story("US-A", status="done", tester_status="failed", dev_status="done")],
        phase="complete",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert len(phase_violations) == 1
    assert "US-A" in phase_violations[0]
    assert "failed" in phase_violations[0]


def test_planted_phase_complete_blocked_story_is_violation():
    """phase:complete + story tester_status:blocked must be caught."""
    data = _sprint(
        [_story("US-B", status="done", tester_status="blocked", dev_status="done")],
        phase="complete",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert len(phase_violations) == 1
    assert "US-B" in phase_violations[0]
    assert "blocked" in phase_violations[0]


def test_planted_phase_done_failed_story_is_violation():
    """phase:done + story tester_status:failed must also be caught."""
    data = _sprint(
        [_story("US-C", status="done", tester_status="failed", dev_status="done")],
        phase="done",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert len(phase_violations) == 1
    assert "US-C" in phase_violations[0]


def test_planted_phase_done_blocked_story_is_violation():
    """phase:done + story tester_status:blocked must also be caught."""
    data = _sprint(
        [_story("US-D", status="done", tester_status="blocked", dev_status="done")],
        phase="done",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert len(phase_violations) == 1


def test_planted_phase_complete_multiple_failing_stories_all_reported():
    """All failing stories in a terminal-phase sprint are reported, not just the first."""
    data = _sprint(
        [
            _story("US-A", status="done", tester_status="failed", dev_status="done"),
            _story("US-B", status="done", tester_status="blocked", dev_status="done"),
        ],
        phase="complete",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert len(phase_violations) == 2
    ids = {v for v in phase_violations}
    assert any("US-A" in v for v in ids)
    assert any("US-B" in v for v in ids)


# ---------------------------------------------------------------------------
# AC-74.1 check 1 — passing cases for the phase check
# ---------------------------------------------------------------------------


def test_phase_complete_all_approved_passes():
    """phase:complete + all tester_status:approved -> no terminal-phase violation."""
    data = _sprint(
        [_story("US-A", status="done", tester_status="approved", dev_status="done")],
        phase="complete",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert phase_violations == []


def test_phase_done_all_approved_passes():
    """phase:done + all tester_status:approved -> no terminal-phase violation."""
    data = _sprint(
        [_story("US-A", status="done", tester_status="approved", dev_status="done")],
        phase="done",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert phase_violations == []


def test_phase_complete_no_stories_passes():
    """phase:complete with no stories -> no terminal-phase violation."""
    data = _sprint([], phase="complete")
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert phase_violations == []


def test_phase_in_progress_failed_story_no_terminal_phase_violation():
    """phase:in-progress + failed story -> no TERMINAL-phase violation (other checks may fire)."""
    data = _sprint(
        [_story("US-A", status="done", tester_status="failed", dev_status="done")],
        phase="in-progress",
    )
    violations = check_sprint(data)
    phase_violations = [v for v in violations if "terminal phase" in v]
    assert phase_violations == []


# ---------------------------------------------------------------------------
# AC-74.1 check 2 — dev_status:not-started artifact (existing code, confirmed)
# ---------------------------------------------------------------------------


def test_planted_dev_status_not_started_all_acs_done_is_violation():
    """Story with all ACs dev_status:done but story dev_status:not-started is flagged (stale)."""
    data = _sprint(
        [
            _story(
                "US-B",
                status="in-progress",  # not-done so existing check applies
                tester_status="not-started",
                dev_status="not-started",
                acs=[_ac("B.1", dev_status="done"), _ac("B.2", dev_status="done")],
            )
        ],
        phase="in-progress",
    )
    violations = check_sprint(data)
    stale = [v for v in violations if "stale" in v and "phase" not in v]
    assert len(stale) == 1
    assert "US-B" in stale[0]
    assert "not-started" in stale[0]


# ---------------------------------------------------------------------------
# Planted combined fixture — BOTH artifacts caught simultaneously
# ---------------------------------------------------------------------------


def test_planted_combined_fixture_both_violations_caught():
    """
    Planted fixture exhibiting the full AC-74.1 artifact:
      - phase:complete
      - Story US-A: tester_status:failed  -> terminal-phase violation
      - Story US-B: all ACs dev_status:done but story dev_status:not-started
                    (status:in-progress)  -> stale dev_status violation
    Both violations must be present.
    """
    data = _sprint(
        [
            _story(
                "US-A",
                status="done",
                tester_status="failed",
                dev_status="done",
                acs=[_ac("A.1", dev_status="done", checked=True, tester_status="approved")],
            ),
            _story(
                "US-B",
                status="in-progress",
                tester_status="not-started",
                dev_status="not-started",
                acs=[_ac("B.1", dev_status="done"), _ac("B.2", dev_status="done")],
            ),
        ],
        phase="complete",
    )
    violations = check_sprint(data)

    phase_violations = [v for v in violations if "terminal phase" in v]
    stale_violations = [v for v in violations if "stale" in v and "phase" not in v]

    assert len(phase_violations) >= 1, f"Expected terminal-phase violation; got: {violations}"
    assert len(stale_violations) >= 1, f"Expected stale dev_status violation; got: {violations}"
    assert any("US-A" in v for v in phase_violations)
    assert any("US-B" in v for v in stale_violations)


# ---------------------------------------------------------------------------
# Normalized fixture — guard passes cleanly
# ---------------------------------------------------------------------------


def test_normalized_fixture_passes():
    """
    A normalized (guard-clean) sprint: phase in-progress, stories have consistent
    statuses. No violations expected.
    """
    data = _sprint(
        [
            _story(
                "US-1",
                status="done",
                tester_status="approved",
                dev_status="done",
                acs=[
                    _ac("1.1", dev_status="done", checked=True, tester_status="approved"),
                    _ac("1.2", dev_status="done", checked=True, tester_status="approved"),
                ],
            ),
            _story(
                "US-2",
                status="in-progress",
                tester_status="not-started",
                dev_status="in-progress",
                acs=[
                    _ac("2.1", dev_status="done"),
                    _ac("2.2", dev_status="not-started"),
                ],
            ),
        ],
        phase="in-progress",
    )
    assert check_sprint(data) == []
