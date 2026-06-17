# ---
# module: core.dev_agent_ruff_gate_guard
# project: solanatrilly
# purpose: ImportError-trap guard for the AI dev-agent ruff gate (US-47 AC-47.3).
#          Importing this module at collection time pins the gate mechanism by name.
#          Raises ImportError (fails pytest collection) if scripts/dev-agent-ruff-gate.sh
#          is deleted/renamed OR if .claude/settings.json no longer carries the
#          PreToolUse hook — so the gate can NEVER silently vanish.
# story: US-47 AC-47.3
# sprint: sprint-10
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, json
# ---
"""Guard module: raises ImportError at import time if the AI dev-agent ruff gate is gone.

This is the ImportError-trap mechanism for AC-47.3. The pattern mirrors H1 used
across the canonical test job (e.g. test_scorer_ac432.py, test_scorer_wiring_ac433.py):
a module-level import that fails pytest COLLECTION — not merely test execution — if
the named symbol/entrypoint is deleted or renamed.

Usage (in test files — at module level, NOT inside a try/except):

    from core.dev_agent_ruff_gate_guard import GATE_WIRED, GATE_SCRIPT, GATE_COMMAND
    assert GATE_WIRED  # H1: guard deleted → ImportError at collection

Anti-pattern explicitly excluded (AC-47.3): wrapping the import in try/except
would swallow the ImportError and turn a loud collection failure into a silent no-op:

    # ANTI-PATTERN — never do this:
    try:
        from core.dev_agent_ruff_gate_guard import GATE_WIRED
    except ImportError:
        pass  # gate vanished, but nothing fails — silent no-op
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate repo artefacts — pinned by name (the H1 'named function/step' anchor)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The named entrypoint / loop step being guarded:
_GATE_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dev-agent-ruff-gate.sh"
_SETTINGS_JSON_PATH = _REPO_ROOT / ".claude" / "settings.json"
_EXPECTED_GATE_COMMAND = "bash scripts/dev-agent-ruff-gate.sh"

# ---------------------------------------------------------------------------
# Guard 1: gate script must exist
# If scripts/dev-agent-ruff-gate.sh is deleted or renamed, this ImportError
# fires during pytest collection — the mechanism cannot silently vanish.
# ---------------------------------------------------------------------------

if not _GATE_SCRIPT_PATH.exists():
    raise ImportError(
        "[AC-47.3 ImportError trap] scripts/dev-agent-ruff-gate.sh NOT FOUND.\n"
        "The AI dev-agent ruff gate (US-47 AC-47.1) has been deleted or renamed.\n"
        "Restore 'scripts/dev-agent-ruff-gate.sh' or this module cannot be imported,\n"
        "causing pytest collection failure in test_dev_agent_ruff_gate_ac473.py.\n"
        "This is intentional — the gate must never silently vanish (AC-47.3)."
    )

# ---------------------------------------------------------------------------
# Guard 2: settings.json must exist
# ---------------------------------------------------------------------------

if not _SETTINGS_JSON_PATH.exists():
    raise ImportError(
        "[AC-47.3 ImportError trap] .claude/settings.json NOT FOUND.\n"
        "The Claude Code hook config is missing — the AI dev-agent ruff gate\n"
        "has no entrypoint. Restore '.claude/settings.json'.\n"
        "This ImportError fails pytest collection intentionally (AC-47.3)."
    )

# ---------------------------------------------------------------------------
# Guard 3: settings.json must carry the named hook command
# Parses JSON — structured analysis, not text search — to verify the named
# entrypoint ('bash scripts/dev-agent-ruff-gate.sh') is wired in the
# PreToolUse[Bash] hooks list.
# ---------------------------------------------------------------------------

_settings = json.loads(_SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
_pre_tool_use = _settings.get("hooks", {}).get("PreToolUse", [])
_bash_entries = [e for e in _pre_tool_use if isinstance(e, dict) and e.get("matcher") == "Bash"]
_hook_commands = [
    h.get("command", "")
    for entry in _bash_entries
    for h in entry.get("hooks", [])
    if isinstance(h, dict)
]

if not any("dev-agent-ruff-gate.sh" in cmd for cmd in _hook_commands):
    raise ImportError(
        "[AC-47.3 ImportError trap] 'dev-agent-ruff-gate.sh' NOT found in\n"
        ".claude/settings.json → hooks.PreToolUse[matcher=Bash] hook commands.\n"
        f"Commands found: {_hook_commands!r}\n"
        "The PreToolUse hook for the AI dev-agent ruff gate has been removed or\n"
        "renamed. Restore the hook. This ImportError fails pytest collection\n"
        "intentionally — the gate must never silently vanish (AC-47.3)."
    )

# ---------------------------------------------------------------------------
# Public constants — importable by test files (the 'named symbols' being pinned)
# ---------------------------------------------------------------------------

#: Sentinel: True only if all three guards above passed at import time.
GATE_WIRED: bool = True

#: Absolute path to the gate script (pinned by name).
GATE_SCRIPT: Path = _GATE_SCRIPT_PATH

#: Absolute path to .claude/settings.json.
SETTINGS_JSON: Path = _SETTINGS_JSON_PATH

#: The exact hook command string that must appear in settings.json.
GATE_COMMAND: str = _EXPECTED_GATE_COMMAND
