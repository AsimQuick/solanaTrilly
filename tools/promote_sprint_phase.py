# ---
# module: tools.promote_sprint_phase
# sprint: sprint-7, sprint-14
# story: US-33 AC-33.1 US-74 AC-74.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: json, sys, argparse, pathlib
# ---
# F2 — mechanical phase-promotion pre-deploy step.
#
# Promotes a stale sprint 'phase' to 'done' when all stories are 'done',
# AND promotes each story's dev_status to 'done' when all its ACs are
# dev_status='done' (NOT exempted — every story is evaluated).
# Run BEFORE sprint_integrity_check.py in the CI/deploy sequence.
#
# Usage:
#   python3 tools/promote_sprint_phase.py [--check-only] [--dry-run] scrum-master/sprint*.json
#
#   Default (no flags): auto-promote stale files in place, exit 0.
#   --check-only : block (exit 1) with a remedy message instead of auto-promoting.
#   --dry-run    : print what would be promoted without writing, exit 0.
#
# This closes the recurring D2→E1→F2 failure (CI runs 27555736146/27555822147,
# 27593136555, 27627161427) where a stale phase:'planning' caused the US-13
# integrity guard to fail the sprint-end deploy three consecutive sprints.
# AC-74.2 extends it to also promote stale story dev_status values so the
# guard never trips on our own staleness at any field.

import argparse
import json
import sys
from pathlib import Path

_INCOMPLETE_PHASES = {"planning", "in-progress", "review"}
_PROMOTED_PHASE = "done"
_DONE_DEV_STATUS = "done"


def compute_promoted_phase(data: dict) -> str | None:
    """Return the promoted phase if the current phase is stale, else None.

    Stale = phase is 'planning', 'in-progress', or 'review' but all stories have status='done'.
    Returns None when no promotion is needed.
    """
    phase = data.get("phase", "")
    if phase not in _INCOMPLETE_PHASES:
        return None
    stories = data.get("stories", [])
    if not stories:
        return None
    if all(s.get("status", "") == "done" for s in stories):
        return _PROMOTED_PHASE
    return None


def promote_story_dev_statuses(stories: list) -> tuple[list, bool]:
    """Promote each story's dev_status to 'done' when all its ACs are dev_status='done'.

    Returns (updated_stories, any_promoted). Does not mutate input stories.
    Only promotes stories that have ACs and whose dev_status is not already 'done'.
    NOT exempted — every story is evaluated without exception.
    """
    updated = []
    any_promoted = False
    for story in stories:
        acs = story.get("acceptance_criteria", [])
        if (
            acs
            and story.get("dev_status") != _DONE_DEV_STATUS
            and all(ac.get("dev_status") == _DONE_DEV_STATUS for ac in acs)
        ):
            story = {**story, "dev_status": _DONE_DEV_STATUS}
            any_promoted = True
        updated.append(story)
    return updated, any_promoted


def promote_sprint_phase(data: dict) -> tuple[dict, str | None]:
    """Promote phase to 'done' and story dev_statuses to the correct closeout values.

    Returns (updated_data, phase_promoted_to) where:
    - phase_promoted_to is 'done' if the phase was promoted, else None.
    - updated_data may have story dev_statuses updated even if the phase was not promoted.
    - Returns (data, None) unchanged if no promotion is needed at all.
    """
    phase_promoted_to = compute_promoted_phase(data)
    stories = data.get("stories", [])
    updated_stories, stories_promoted = promote_story_dev_statuses(stories)

    if phase_promoted_to is None and not stories_promoted:
        return data, None

    updated = {**data}
    if phase_promoted_to is not None:
        updated["phase"] = phase_promoted_to
    if stories_promoted:
        updated["stories"] = updated_stories
    return updated, phase_promoted_to


def remedy_message(path: Path, from_phase: str, promoted_to: str) -> str:
    """Return the deterministic remedy message printed when --check-only is set."""
    return (
        f"[{path.name}] STALE PHASE DETECTED: phase='{from_phase}' but all stories are done.\n"
        f"  REMEDY: Update {path} — set phase to '{promoted_to}' before deploying.\n"
        f"  Or run without --check-only to auto-promote:\n"
        f"    python3 tools/promote_sprint_phase.py {path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "F2 (US-33 AC-33.1, US-74 AC-74.2) — Mechanically promote stale sprint 'phase' "
            "and story dev_status values before the US-13 integrity check runs, closing the "
            "D2→E1→F2 failure mode."
        )
    )
    parser.add_argument("files", nargs="+", help="Sprint JSON file paths")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Do not write files. Block (exit 1) if any stale phase or dev_status is found, "
            "printing a deterministic remedy message."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be promoted without writing files (exit 0).",
    )
    args = parser.parse_args()

    stale_found = False
    for file_path in args.files:
        path = Path(file_path)
        data = json.loads(path.read_text())
        updated, promoted_to = promote_sprint_phase(data)
        if updated is data:
            continue

        stale_found = True
        from_phase = data["phase"]
        phase_changed = promoted_to is not None
        dev_changed = updated.get("stories") is not data.get("stories")

        if args.check_only:
            if phase_changed:
                print(remedy_message(path, from_phase, promoted_to), file=sys.stderr)
            if dev_changed:
                print(
                    f"[{path.name}] STALE DEV_STATUS: one or more stories have all ACs "
                    f"dev_status='done' but story dev_status is not 'done'.\n"
                    f"  REMEDY: Run without --check-only to auto-promote:\n"
                    f"    python3 tools/promote_sprint_phase.py {path}",
                    file=sys.stderr,
                )
        elif args.dry_run:
            msgs = []
            if phase_changed:
                msgs.append(f"phase '{from_phase}' → '{promoted_to}'")
            if dev_changed:
                msgs.append("story dev_statuses → 'done'")
            print(f"[{path.name}] DRY-RUN: would promote {', '.join(msgs)}")
        else:
            path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
            msgs = []
            if phase_changed:
                msgs.append(f"phase '{from_phase}' → '{promoted_to}'")
            if dev_changed:
                msgs.append("story dev_statuses → 'done'")
            print(
                f"[{path.name}] AUTO-PROMOTED: {', '.join(msgs)} "
                f"(stale values corrected before US-13 guard runs)"
            )

    if args.check_only and stale_found:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
