# ---
# module: tools.sprint_integrity_check
# sprint: sprint-4
# story: US-13 AC-13.1 AC-13.2 AC-13.3 US-74 AC-74.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: json, sys, argparse, pathlib
# ---

import argparse
import json
import sys
from pathlib import Path

_FAIL_STATUSES = {"failed", "fail", "blocked"}
# Story dev_status values that indicate work is not finished
_INCOMPLETE_DEV_STATUSES = {"not-started", "in-progress"}
# Sprint phase values that indicate work is not finished
_INCOMPLETE_PHASES = {"planning", "in-progress"}
# Sprint phase values that indicate work is fully closed (AC-74.1)
_TERMINAL_PHASES = {"complete", "done"}


def check_sprint(data: dict) -> list[str]:
    """Return a list of integrity violation strings for the given sprint dict."""
    violations = []
    stories = data.get("stories", [])

    for story in stories:
        story_id = story.get("id", "<unknown>")
        story_status = story.get("status", "")
        story_tester = story.get("tester_status", "")
        story_dev = story.get("dev_status", "")

        # AC-13.1: done + failed/blocked is forbidden
        if story_status == "done" and story_tester in _FAIL_STATUSES:
            violations.append(
                f"Story {story_id}: status='done' but tester_status='{story_tester}'"
            )

        acs = story.get("acceptance_criteria", [])

        for ac in acs:
            ac_id = ac.get("id", "<unknown>")
            ac_checked = ac.get("checked", False)
            ac_tester = ac.get("tester_status", "")
            if ac_checked and ac_tester in _FAIL_STATUSES:
                violations.append(
                    f"AC {ac_id} (story {story_id}): checked=true but tester_status='{ac_tester}'"
                )

        # AC-13.2: stale story dev_status — all ACs done but story still not-started/in-progress.
        # Skip for already-done stories: a stale dev_status on an accepted story is a harmless
        # historical artifact (the story has been promoted past dev tracking by the tester).
        if acs and story_dev in _INCOMPLETE_DEV_STATUSES and story_status != "done":
            ac_dev_statuses = [ac.get("dev_status", "") for ac in acs]
            if all(s == "done" for s in ac_dev_statuses):
                violations.append(
                    f"Story {story_id}: all ACs have dev_status='done' but "
                    f"story dev_status='{story_dev}' (stale)"
                )

    # AC-13.2: stale sprint phase — planning/in-progress while all stories are done
    phase = data.get("phase", "")
    if phase in _INCOMPLETE_PHASES and stories:
        if all(s.get("status", "") == "done" for s in stories):
            violations.append(
                f"Sprint phase='{phase}' but all stories have status='done' (stale)"
            )

    # AC-74.1: terminal phase (complete/done) while any story tester_status is failed/blocked
    if phase in _TERMINAL_PHASES:
        for story in stories:
            story_id = story.get("id", "<unknown>")
            story_tester = story.get("tester_status", "")
            if story_tester in _FAIL_STATUSES:
                violations.append(
                    f"Sprint phase='{phase}' but story {story_id} has "
                    f"tester_status='{story_tester}' (terminal phase with failing story)"
                )

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint integrity checker")
    parser.add_argument("files", nargs="+", help="Sprint JSON file paths")
    parser.add_argument(
        "--skip-complete",
        action="store_true",
        help="Skip sprint files whose phase is 'complete' (archived sprints)",
    )
    args = parser.parse_args()

    all_violations = []
    for file_path in args.files:
        path = Path(file_path)
        data = json.loads(path.read_text())
        if args.skip_complete and data.get("phase") == "complete":
            continue
        violations = check_sprint(data)
        for v in violations:
            print(f"[{path.name}] {v}", file=sys.stderr)
        all_violations.extend(violations)

    sys.exit(1 if all_violations else 0)


if __name__ == "__main__":
    main()
