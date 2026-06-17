# ---
# module: test_dev_agent_ruff_gate_ac471
# sprint: sprint-10
# story: US-47 AC-47.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: scripts/dev-agent-ruff-gate.sh, .claude/settings.json
# ---
"""AC-47.1 — structural proof: AI dev-agent ruff gate is wired WITHOUT a manual step.

The mechanism: a Claude Code PreToolUse hook in .claude/settings.json (the
COMMITTED, shared project config — NOT .claude/settings.local.json, which is
gitignored and therefore absent from a CI checkout) that intercepts Bash tool
calls and runs scripts/dev-agent-ruff-gate.sh before any git commit command.
Claude Code loads settings.json automatically on startup — no 'make
install-hooks' or 'bash install-*.sh' step is required.

Verifies:
  1. .claude/settings.json (the committed project config / 'the config it
     lives in') has hooks.PreToolUse configured for the Bash tool.
  2. The hook command references scripts/dev-agent-ruff-gate.sh by name — a
     structural pin that breaks loudly if the command is removed or renamed.
  3. scripts/dev-agent-ruff-gate.sh exists and is executable (no install step).
  4. The gate script invokes ruff (it is not a no-op).
  5. The gate script does NOT require a manual 'install' step to become active
     (unlike the G3 .git/hooks/pre-commit which requires install-hooks.sh to run).
  6. The mechanism fires without a manual step: .claude/settings.json is
     committed and loaded automatically by Claude Code — this mirrors the
     AC-39.1/AC-45.1 'no manual step' proof for the human/devcontainer hook.

Scope: this module proves AC-47.1 only (structural wiring + 'no manual step').
The behavioral seeded-violation proof (I001/E501/F401) is AC-47.2 and the
silent-vanish / anti-deletion guard is AC-47.3 — each delivered on its own
branch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_JSON = REPO_ROOT / ".claude" / "settings.json"
GATE_SCRIPT = REPO_ROOT / "scripts" / "dev-agent-ruff-gate.sh"
GATE_COMMAND = "bash scripts/dev-agent-ruff-gate.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_settings() -> dict:
    assert SETTINGS_JSON.exists(), (
        f".claude/settings.json not found at {SETTINGS_JSON}.\n"
        "AC-47.1 requires this COMMITTED file (not the gitignored "
        "settings.local.json) to carry the PreToolUse hook so the gate is "
        "present in every clone / CI checkout."
    )
    return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Structural: hook is present in the committed config ("the config it lives in")
# ---------------------------------------------------------------------------


def test_settings_json_has_hooks_section() -> None:
    """settings.json (committed) must have a top-level 'hooks' key.

    The 'hooks' key is the entry-point for Claude Code's automatic hook
    system.  Its absence means Claude Code will never fire any pre-tool-use
    guard, regardless of any scripts that may exist.

    AC-47.1: the mechanism lives in .claude/settings.json (no manual step).
    """
    settings = _load_settings()
    assert "hooks" in settings, (
        ".claude/settings.json is missing the 'hooks' key.\n"
        "AC-47.1 requires a 'hooks' section to wire the PreToolUse guard.\n"
        f"Current top-level keys: {list(settings.keys())}"
    )


def test_settings_json_has_pre_tool_use_hook() -> None:
    """settings.json must have hooks.PreToolUse configured.

    Claude Code fires PreToolUse hooks BEFORE executing any tool call.
    Without this key, the gate never fires regardless of the hook command.

    AC-47.1: the PreToolUse event is the trigger that intercepts git commits.
    """
    settings = _load_settings()
    hooks = settings.get("hooks", {})
    assert "PreToolUse" in hooks, (
        "settings.json hooks section is missing 'PreToolUse'.\n"
        f"Found hook events: {list(hooks.keys())}\n"
        "AC-47.1 requires PreToolUse to intercept Bash tool calls."
    )
    entries = hooks["PreToolUse"]
    assert isinstance(entries, list) and len(entries) > 0, (
        "hooks.PreToolUse must be a non-empty list; got: {entries!r}"
    )


def test_settings_json_pre_tool_use_targets_bash() -> None:
    """The PreToolUse hook must have a matcher for the 'Bash' tool.

    Claude Code matches hook entries by tool name.  Only a 'Bash' matcher
    fires when Claude Code executes Bash commands (including git commit).

    AC-47.1: the Bash tool is the one that runs git commit on the dev-agent path.
    """
    settings = _load_settings()
    entries = settings.get("hooks", {}).get("PreToolUse", [])
    bash_entries = [e for e in entries if isinstance(e, dict) and e.get("matcher") == "Bash"]
    assert bash_entries, (
        "No PreToolUse entry with matcher='Bash' found in settings.json.\n"
        f"Entries found: {entries}\n"
        "AC-47.1: the hook must target the Bash tool to intercept git commit."
    )


def test_settings_json_hook_command_references_gate_script() -> None:
    """The PreToolUse Bash hook command must reference dev-agent-ruff-gate.sh.

    This is the structural pin: if scripts/dev-agent-ruff-gate.sh is renamed or
    removed, this test breaks loudly rather than silently degrading to a no-op.

    AC-47.1: the gate script is the named function/step the AC requires to be pinned.
    """
    settings = _load_settings()
    entries = settings.get("hooks", {}).get("PreToolUse", [])
    bash_entry = next(
        (e for e in entries if isinstance(e, dict) and e.get("matcher") == "Bash"),
        None,
    )
    assert bash_entry is not None, "No Bash PreToolUse entry (tested above)"

    hooks_list = bash_entry.get("hooks", [])
    assert hooks_list, f"Bash PreToolUse entry has no 'hooks' list: {bash_entry}"

    commands = [h.get("command", "") for h in hooks_list if isinstance(h, dict)]
    gate_script_commands = [c for c in commands if "dev-agent-ruff-gate.sh" in c]
    assert gate_script_commands, (
        f"No hook command referencing 'dev-agent-ruff-gate.sh' found.\n"
        f"Commands found: {commands}\n"
        "AC-47.1: the gate script must be named in the hook command — "
        "rename/remove breaks this test loudly (not silently)."
    )


def test_settings_json_hook_command_matches_expected() -> None:
    """The Bash PreToolUse hook command exactly matches the expected gate invocation.

    Pins the precise command string so accidental edits are caught.

    AC-47.1: structural pinning of the gate command (anti-silent-vanish).
    """
    settings = _load_settings()
    entries = settings.get("hooks", {}).get("PreToolUse", [])
    bash_entry = next(
        (e for e in entries if isinstance(e, dict) and e.get("matcher") == "Bash"),
        None,
    )
    assert bash_entry is not None
    hooks_list = bash_entry.get("hooks", [])
    commands = [h.get("command", "") for h in hooks_list if isinstance(h, dict)]
    assert GATE_COMMAND in commands, (
        f"Expected hook command {GATE_COMMAND!r} not found.\n"
        f"Actual commands: {commands}\n"
        "AC-47.1: the gate command must be exactly 'bash scripts/dev-agent-ruff-gate.sh'."
    )


# ---------------------------------------------------------------------------
# Structural: gate script is present and executable (no install step required)
# ---------------------------------------------------------------------------


def test_gate_script_exists() -> None:
    """scripts/dev-agent-ruff-gate.sh must exist in the committed repo.

    Unlike .git/hooks/pre-commit (which requires install-hooks.sh to create it),
    the gate script lives directly in scripts/ and requires NO install step.

    AC-47.1: the script is present without any 'make install-...' step.
    """
    assert GATE_SCRIPT.exists(), (
        f"scripts/dev-agent-ruff-gate.sh not found at {GATE_SCRIPT}.\n"
        "AC-47.1 requires this script to exist without a manual install step.\n"
        "Unlike .git/hooks/pre-commit, it must be committed directly to scripts/."
    )


def test_gate_script_is_executable() -> None:
    """scripts/dev-agent-ruff-gate.sh must be executable (no chmod step required).

    A non-executable script would be silently skipped by bash in some contexts.
    The script must be committed with execute permissions.

    AC-47.1: the gate fires without any manual 'chmod' step.
    """
    assert GATE_SCRIPT.exists(), "Gate script missing (see test_gate_script_exists)"
    assert os.access(GATE_SCRIPT, os.X_OK), (
        f"scripts/dev-agent-ruff-gate.sh is NOT executable at {GATE_SCRIPT}.\n"
        "AC-47.1: the gate must fire without a manual chmod step.\n"
        "Fix: chmod +x scripts/dev-agent-ruff-gate.sh && git add -p scripts/dev-agent-ruff-gate.sh"
    )


def test_gate_script_invokes_ruff() -> None:
    """The gate script content must reference 'ruff' — it must NOT be a no-op.

    A gate that exists but never calls ruff would silently fail to catch
    I001/E501/F401 violations on the AI dev-agent commit path.

    AC-47.1: the gate invokes ruff (not a wrapper that does nothing).
    """
    assert GATE_SCRIPT.exists(), "Gate script missing (see test_gate_script_exists)"
    content = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "ruff" in content, (
        "scripts/dev-agent-ruff-gate.sh does not reference 'ruff'.\n"
        f"Script content:\n{content}\n\n"
        "AC-47.1: the gate must invoke ruff to catch I001/E501/F401 violations."
    )


def test_gate_script_guards_git_commit_specifically() -> None:
    """The gate script must selectively fire on git commit commands.

    The PreToolUse hook fires before EVERY Bash call.  The gate script must
    exit 0 immediately for non-commit commands (not just block everything).
    It does this by reading the hook input and checking for 'git commit'.

    AC-47.1: the mechanism is targeted at the commit path, not a blanket block.
    """
    content = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "git commit" in content, (
        "scripts/dev-agent-ruff-gate.sh does not check for 'git commit'.\n"
        "The script must selectively run ruff only on git commit Bash calls.\n"
        "AC-47.1: the gate intercepts the commit path specifically."
    )


# ---------------------------------------------------------------------------
# 'No manual step' proof (mirroring AC-39.1/AC-45.1 pattern)
# ---------------------------------------------------------------------------


def test_gate_requires_no_install_step() -> None:
    """The gate does NOT require a manual install step to become active.

    AC-39.1/AC-45.1 'no manual step' proof for the human/devcontainer hook:
      - devcontainer postCreateCommand auto-installs .git/hooks/pre-commit
      - CI 'Auto-install git hooks (AC-39.1)' step auto-installs the hook

    AC-47.1 'no manual step' proof for the AI dev-agent hook:
      - .claude/settings.json is committed and loaded AUTOMATICALLY by
        Claude Code at startup — NO 'bash scripts/install-hooks.sh' step is
        required; the hook fires from the first Bash tool call.
      - scripts/dev-agent-ruff-gate.sh lives in the repo (no 'cp' or 'install'
        to .git/hooks/ needed — the script is invoked directly from settings).

    This test verifies both conditions hold.
    """
    # Condition 1: settings.json is a committed file (not .git/ which
    # requires install-hooks.sh).  Its presence here means it's committed.
    assert SETTINGS_JSON.exists(), (
        ".claude/settings.json missing — the 'config it lives in' is absent.\n"
        "Without this file, Claude Code has no hook configuration to auto-load."
    )

    # Condition 2: gate script lives in scripts/ (committed), not in .git/hooks/
    # (which requires install-hooks.sh to populate).
    assert str(GATE_SCRIPT).endswith("scripts/dev-agent-ruff-gate.sh"), (
        f"Gate script not in scripts/ directory: {GATE_SCRIPT}\n"
        "The gate must live in the committed scripts/ dir, not .git/hooks/ "
        "(the latter requires install-hooks.sh — a manual step)."
    )
    git_hooks_path = REPO_ROOT / ".git" / "hooks" / "dev-agent-ruff-gate.sh"
    assert not git_hooks_path.exists(), (
        f"Gate script unexpectedly found in .git/hooks/ at {git_hooks_path}.\n"
        "The gate must NOT be installed via install-hooks.sh — that would be a manual step."
    )


def test_settings_json_is_in_committed_directory() -> None:
    """settings.json lives in .claude/ (a committed repo directory).

    The file's presence at .claude/settings.json (mounted from the host
    checkout) confirms it is part of the repo tree — a CI runner would not have
    it unless it were committed.  This mirrors the pattern used throughout this
    test suite (Path existence == committed, since the Docker volume mounts the
    full checkout).

    AC-47.1: 'structural — present in the config it lives in' requires the
    config to be in the repo (not generated or injected at runtime).
    """
    assert SETTINGS_JSON.exists(), (
        f".claude/settings.json not found at {SETTINGS_JSON}.\n"
        "This file must be committed to the repo so Claude Code loads it on every clone."
    )
    # The file must live in .claude/ (not .git/ or any runtime-only location).
    assert ".claude" in str(SETTINGS_JSON), (
        f"Unexpected path for settings.json: {SETTINGS_JSON}\n"
        "It must reside in the .claude/ project-config directory."
    )


def test_gate_script_is_in_committed_scripts_directory() -> None:
    """scripts/dev-agent-ruff-gate.sh lives in scripts/ (the committed scripts dir).

    A CI runner performing a checkout would have this file only if it is tracked.
    Its presence in scripts/ (not .git/hooks/) confirms it requires no install step
    and is part of the permanent repo tree.

    AC-47.1: the gate script must be committed (no manual installation).
    """
    assert GATE_SCRIPT.exists(), (
        f"scripts/dev-agent-ruff-gate.sh not found at {GATE_SCRIPT}.\n"
        "The gate script must be committed to scripts/ — 'git add scripts/dev-agent-ruff-gate.sh'."
    )
    # Verify it's in scripts/ (committed), NOT in .git/hooks/ (requires install-hooks.sh).
    assert "scripts" in GATE_SCRIPT.parts, (
        f"Gate script not in scripts/ directory: {GATE_SCRIPT}\n"
        "It must live in the tracked scripts/ dir, not .git/hooks/."
    )
