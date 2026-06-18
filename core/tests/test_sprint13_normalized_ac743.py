# ---
# module: core.tests.test_sprint13_normalized_ac743
# sprint: sprint-14
# story: US-74 AC-74.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: tools.sprint_integrity_check, tools.promote_sprint_phase, pathlib, json
# ---
"""
AC-74.3 verification tests — sprint13.json normalization.

Verifies that sprint13.json is normalized to a guard-clean, truthful state:
  - top-level phase is 'done' (not 'planning')
  - all stories (US-64..US-69) carry status='done', tester_status='approved'
  - every story-level dev_status is 'done' (not 'not-started')
  - check_sprint(sprint13) returns zero violations (guard passes directly)
  - check_sprint(sprint14) returns zero violations
  - promote_sprint_phase(sprint13) returns unchanged data (already normalized,
    promoter has nothing further to do)
"""

import json
from pathlib import Path

import pytest

from tools.promote_sprint_phase import promote_sprint_phase
from tools.sprint_integrity_check import check_sprint

_REPO_ROOT = Path(__file__).parent.parent.parent
SPRINT13_PATH = _REPO_ROOT / "scrum-master" / "sprint13.json"
SPRINT14_PATH = _REPO_ROOT / "scrum-master" / "sprint14.json"

_EXPECTED_STORY_IDS = {"US-64", "US-65", "US-66", "US-67", "US-68", "US-69"}


@pytest.fixture(scope="module")
def sprint13():
    return json.loads(SPRINT13_PATH.read_text())


@pytest.fixture(scope="module")
def sprint14():
    return json.loads(SPRINT14_PATH.read_text())


# ---------------------------------------------------------------------------
# sprint13.json file presence
# ---------------------------------------------------------------------------


def test_sprint13_file_exists():
    """sprint13.json must be present in scrum-master/."""
    assert SPRINT13_PATH.exists(), f"Missing: {SPRINT13_PATH}"


# ---------------------------------------------------------------------------
# sprint13.json content assertions — truthfulness
# ---------------------------------------------------------------------------


def test_sprint13_phase_is_done(sprint13):
    """Top-level phase must be 'done', not 'planning' (the pre-normalization artifact)."""
    assert sprint13["phase"] == "done", (
        f"sprint13.json phase is '{sprint13['phase']}'; expected 'done'. "
        "AC-74.3 requires the file to reflect the actual closed state of sprint-13."
    )


def test_sprint13_stories_are_us64_to_us69(sprint13):
    """sprint13.json must contain exactly the US-64..US-69 story set."""
    found = {s["id"] for s in sprint13["stories"]}
    assert found == _EXPECTED_STORY_IDS, (
        f"sprint13.json stories are {found}; expected {_EXPECTED_STORY_IDS}."
    )


def test_sprint13_all_stories_status_done(sprint13):
    """Every story must have status='done' (sprint-13 is fully closed via US-70/PR #296)."""
    bad = [
        (s["id"], s.get("status")) for s in sprint13["stories"] if s.get("status") != "done"
    ]
    assert not bad, f"Stories with status != 'done': {bad}"


def test_sprint13_all_stories_tester_approved(sprint13):
    """Every story must have tester_status='approved'."""
    bad = [
        (s["id"], s.get("tester_status"))
        for s in sprint13["stories"]
        if s.get("tester_status") != "approved"
    ]
    assert not bad, f"Stories with tester_status != 'approved': {bad}"


def test_sprint13_all_stories_dev_status_done(sprint13):
    """Every story-level dev_status must be 'done' (closing the AC-74.3 artifact).

    The pre-normalization sprint13.json had US-64..US-69 with dev_status='not-started'
    even though all their ACs were dev_status='done'. This is the artifact AC-74.3 closes.
    """
    bad = [
        (s["id"], s.get("dev_status"))
        for s in sprint13["stories"]
        if s.get("dev_status") != "done"
    ]
    assert not bad, (
        f"Stories with dev_status != 'done': {bad}. "
        "AC-74.3 requires story-level dev_status to be promoted to 'done' at closeout."
    )


# ---------------------------------------------------------------------------
# Guard passes directly on sprint13.json (main AC-74.3 verification)
# ---------------------------------------------------------------------------


def test_sprint13_guard_passes_directly(sprint13):
    """check_sprint(sprint13.json) must return zero violations after normalization.

    This is the primary AC-74.3 verification: the hardened US-13 guard (AC-74.1)
    is GREEN on sprint13.json without needing the CI auto-promoter to fix it first.
    """
    violations = check_sprint(sprint13)
    assert violations == [], (
        "sprint13.json guard violations (should be zero after normalization):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_sprint13_promoter_has_nothing_to_do(sprint13):
    """promote_sprint_phase(sprint13.json) must return the same object unchanged.

    If the promoter still returns new data, sprint13.json still has stale values —
    the normalization is incomplete and the file is not guard-clean from a direct read.
    """
    updated, promoted_to = promote_sprint_phase(sprint13)
    assert updated is sprint13, (
        "promote_sprint_phase(sprint13) returned new data — the file still has stale values "
        f"(promoted_to={promoted_to!r}). AC-74.3 requires the file to be already normalized."
    )


# ---------------------------------------------------------------------------
# Guard passes on sprint14.json
# ---------------------------------------------------------------------------


def test_sprint14_guard_passes(sprint14):
    """check_sprint(sprint14.json) must return zero violations.

    AC-74.3 requires the guard to be GREEN on BOTH sprint13.json and sprint14.json.
    """
    violations = check_sprint(sprint14)
    assert violations == [], (
        "sprint14.json guard violations (should be zero):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
