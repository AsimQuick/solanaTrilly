# ---
# module: tools.promote_sprint_phase
# sprint: sprint-7
# story: US-33 AC-33.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: json, sys, argparse, pathlib
# ---
# F2 — mechanical phase-promotion pre-deploy step.
#
# Promotes a stale sprint 'phase' to 'complete' when all stories are 'done',
# so the US-13 integrity guard is never tripped by our own staleness.
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

import argparse
import json
import sys
from pathlib import Path

_INCOMPLETE_PHASES = {"planning", "in-progress"}
_PROMOTED_PHASE = "complete"


def compute_promoted_phase(data: dict) -> str | None:
    """Return the promoted phase if the current phase is stale, else None.

    Stale = phase is 'planning' or 'in-progress' but all stories have status='done'.
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


def promote_sprint_phase(data: dict) -> tuple[dict, str | None]:
    """Compute the promoted sprint dict and the phase it was promoted to.

    Returns (data_with_promoted_phase, promoted_to) if promotion is needed,
    or (original_data, None) if no promotion is required.
    Does not modify the input dict in place.
    """
    promoted_to = compute_promoted_phase(data)
    if promoted_to is None:
        return data, None
    updated = {**data, "phase": promoted_to}
    return updated, promoted_to


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
            "F2 (US-33 AC-33.1) — Mechanically promote stale sprint 'phase' to its real "
            "value before the US-13 integrity check runs, closing the D2→E1→F2 failure mode."
        )
    )
    parser.add_argument("files", nargs="+", help="Sprint JSON file paths")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Do not write files. Block (exit 1) if a stale phase is found, "
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
        if promoted_to is None:
            continue

        stale_found = True
        from_phase = data["phase"]

        if args.check_only:
            print(remedy_message(path, from_phase, promoted_to), file=sys.stderr)
        elif args.dry_run:
            print(
                f"[{path.name}] DRY-RUN: would promote phase '{from_phase}' → '{promoted_to}'"
            )
        else:
            path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
            print(
                f"[{path.name}] AUTO-PROMOTED: phase '{from_phase}' → '{promoted_to}' "
                f"(all stories done — stale phase corrected before US-13 guard runs)"
            )

    if args.check_only and stale_found:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
