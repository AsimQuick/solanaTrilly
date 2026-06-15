# ---
# module: tools.sprint_integrity_check
# sprint: sprint-4
# story: US-13 AC-13.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: json, sys, argparse, pathlib
# ---

import argparse
import json
import sys
from pathlib import Path

_FAIL_STATUSES = {"failed", "fail", "blocked"}


def check_sprint(data: dict) -> list[str]:
    """Return a list of integrity violation strings for the given sprint dict."""
    violations = []
    for story in data.get("stories", []):
        story_id = story.get("id", "<unknown>")
        story_status = story.get("status", "")
        story_tester = story.get("tester_status", "")
        if story_status == "done" and story_tester in _FAIL_STATUSES:
            violations.append(
                f"Story {story_id}: status='done' but tester_status='{story_tester}'"
            )
        for ac in story.get("acceptance_criteria", []):
            ac_id = ac.get("id", "<unknown>")
            ac_checked = ac.get("checked", False)
            ac_tester = ac.get("tester_status", "")
            if ac_checked and ac_tester in _FAIL_STATUSES:
                violations.append(
                    f"AC {ac_id} (story {story_id}): checked=true but tester_status='{ac_tester}'"
                )
    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint integrity checker")
    parser.add_argument("files", nargs="+", help="Sprint JSON file paths")
    args = parser.parse_args()

    all_violations = []
    for file_path in args.files:
        path = Path(file_path)
        data = json.loads(path.read_text())
        violations = check_sprint(data)
        for v in violations:
            print(f"[{path.name}] {v}", file=sys.stderr)
        all_violations.extend(violations)

    sys.exit(1 if all_violations else 0)


if __name__ == "__main__":
    main()
