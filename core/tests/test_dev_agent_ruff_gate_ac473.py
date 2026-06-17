# ---
# module: core.tests.test_dev_agent_ruff_gate_ac473
# project: solanatrilly
# purpose: AC-47.3 — the AI dev-agent ruff gate cannot silently vanish: structural/AST
#          tests pin the entrypoint by name + an ImportError-trap guard at module level
#          ensures a deleted/renamed/disabled gate fails pytest COLLECTION, never a no-op.
# story: US-47 AC-47.3
# sprint: sprint-10
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dev_agent_ruff_gate_guard, ast, json, pathlib
# ---
"""AC-47.3 — the AI dev-agent ruff gate cannot silently vanish.

The mechanism (scripts/dev-agent-ruff-gate.sh wired via .claude/settings.json
PreToolUse hook, delivered in AC-47.1) is pinned here so that:

  1. A DELETED or RENAMED gate script causes pytest COLLECTION to fail (not
     merely a test-execution failure) — via the H1 ImportError-trap pattern.

  2. A DISABLED hook (the hook command removed from settings.json) causes
     pytest COLLECTION to fail — same mechanism.

  3. A STRUCTURAL/AST test pins the entrypoint by name, so the gate command
     string cannot drift without breaking a test.

  4. The ImportError-trap ANTI-PATTERN (swallowing the exception in a
     try/except) is explicitly verified absent — an AST scan of the guard
     module confirms no try/except wraps the ImportError raises.

H1 ImportError trap (module level — mirroring test_scorer_ac432.py pattern):
─────────────────────────────────────────────────────────────────────────────
The three lines below execute during pytest COLLECTION, not test execution.
If core.dev_agent_ruff_gate_guard raises ImportError (because the gate script
or settings.json hook is missing), THIS MODULE fails to collect, surfacing
the violation before any test even runs.

Tests in this module
────────────────────
H1 ImportError-trap wiring:
  test_gate_wired_sentinel_is_true_ac473
  test_gate_script_constant_points_to_existing_file_ac473
  test_gate_command_constant_matches_settings_json_ac473

Structural: entrypoint pinned by name
  test_guard_module_references_gate_script_by_name_ac473
  test_guard_module_references_expected_gate_command_ac473
  test_settings_json_hook_command_pinned_by_name_in_guard_module_ac473

AST: guard module has no try/except anti-pattern
  test_guard_module_has_no_try_except_swallowing_importerror_ac473
  test_guard_module_raises_importerror_not_swallows_it_ac473

Structural: removed guard fails loudly (never degrades to no-op)
  test_guard_module_importerror_messages_name_the_gate_script_ac473
  test_guard_module_guards_all_three_conditions_ac473
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# H1 ImportError trap — module-level import, NOT wrapped in try/except.
#
# If scripts/dev-agent-ruff-gate.sh is deleted   → guard raises ImportError
#                                                  → THIS FILE fails to collect
# If .claude/settings.json hook is removed        → guard raises ImportError
#                                                  → THIS FILE fails to collect
# If core/dev_agent_ruff_gate_guard.py is deleted → ImportError from Python
#                                                  → THIS FILE fails to collect
#
# Any of those three conditions → loud collection failure, never a silent no-op.
# ---------------------------------------------------------------------------
from core.dev_agent_ruff_gate_guard import (  # noqa: E402
    GATE_COMMAND,
    GATE_SCRIPT,
    GATE_WIRED,
)

assert GATE_WIRED  # H1: guard deleted or gate missing → ImportError at collection
assert GATE_SCRIPT.exists()  # belt-and-suspenders: file must exist at collection time

# ---------------------------------------------------------------------------
# Paths for structural/AST analysis
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_MODULE = REPO_ROOT / "core" / "dev_agent_ruff_gate_guard.py"
SETTINGS_JSON_PATH = REPO_ROOT / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# H1 ImportError-trap wiring tests
# (Confirm the sentinel values imported above have the expected content.)
# ---------------------------------------------------------------------------


def test_gate_wired_sentinel_is_true_ac473() -> None:
    """GATE_WIRED is True — confirms the guard module's three checks all passed.

    GATE_WIRED is set to True only AFTER all three module-level guards in
    core.dev_agent_ruff_gate_guard execute without raising ImportError.
    If any guard failed, this module would not have collected.

    AC-47.3: a removed guard fails pytest collection before this test even runs.
    """
    assert GATE_WIRED is True, (
        "GATE_WIRED is not True — the guard module's collection-time checks failed.\n"
        "AC-47.3: the gate mechanism cannot silently vanish."
    )


def test_gate_script_constant_points_to_existing_file_ac473() -> None:
    """GATE_SCRIPT constant (imported from the guard module) points to an existing file.

    Pins the gate script path at test-collection time via the module-level
    import above. If the script is deleted, pytest collection fails before
    this function is ever called.

    AC-47.3: structural pin on scripts/dev-agent-ruff-gate.sh by name.
    """
    assert GATE_SCRIPT.exists(), (
        f"GATE_SCRIPT does not exist: {GATE_SCRIPT}\n"
        "AC-47.3: scripts/dev-agent-ruff-gate.sh has been deleted or renamed.\n"
        "Restore the gate script."
    )
    assert GATE_SCRIPT.name == "dev-agent-ruff-gate.sh", (
        f"GATE_SCRIPT has wrong name: {GATE_SCRIPT.name!r}\n"
        "AC-47.3: the gate script must be named 'dev-agent-ruff-gate.sh'."
    )


def test_gate_command_constant_matches_settings_json_ac473() -> None:
    """GATE_COMMAND constant matches the hook command in .claude/settings.json.

    Cross-checks that the named command string ('bash scripts/dev-agent-ruff-gate.sh')
    is present BOTH in the guard module's constant AND in the live settings.json —
    so a settings.json edit that changes the command is caught even if the constant
    is not updated.

    AC-47.3: the entrypoint is pinned by name in both places.
    """
    assert SETTINGS_JSON_PATH.exists(), f"settings.json missing: {SETTINGS_JSON_PATH}"
    settings = json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
    pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])
    bash_entries = [e for e in pre_tool_use if isinstance(e, dict) and e.get("matcher") == "Bash"]
    commands = [
        h.get("command", "")
        for entry in bash_entries
        for h in entry.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert GATE_COMMAND in commands, (
        f"GATE_COMMAND {GATE_COMMAND!r} not found in settings.json PreToolUse[Bash] commands.\n"
        f"Commands found: {commands!r}\n"
        "AC-47.3: the hook command must exactly match the guard module's GATE_COMMAND constant."
    )


# ---------------------------------------------------------------------------
# Structural: entrypoint pinned by name in the guard module
# ---------------------------------------------------------------------------


def test_guard_module_references_gate_script_by_name_ac473() -> None:
    """The guard module source references 'dev-agent-ruff-gate.sh' by name.

    If someone renames the gate script in the source without updating the guard,
    the guard would stop pinning the right file. This test ensures the literal
    script name appears in the guard module.

    AC-47.3: structural pin of the entrypoint name.
    """
    assert GUARD_MODULE.exists(), f"Guard module not found: {GUARD_MODULE}"
    source = GUARD_MODULE.read_text(encoding="utf-8")
    assert "dev-agent-ruff-gate.sh" in source, (
        "core/dev_agent_ruff_gate_guard.py does not reference 'dev-agent-ruff-gate.sh'.\n"
        "AC-47.3: the guard module must pin the gate script by name so a rename breaks loudly."
    )


def test_guard_module_references_expected_gate_command_ac473() -> None:
    """The guard module source references the full gate command string.

    Pins 'bash scripts/dev-agent-ruff-gate.sh' in the guard module so that
    the expected command is not a floating string — it's anchored in two places
    (the guard module constant AND this test).

    AC-47.3: belt-and-suspenders naming pin.
    """
    assert GUARD_MODULE.exists(), f"Guard module not found: {GUARD_MODULE}"
    source = GUARD_MODULE.read_text(encoding="utf-8")
    assert "bash scripts/dev-agent-ruff-gate.sh" in source, (
        "core/dev_agent_ruff_gate_guard.py does not contain the full command "
        "'bash scripts/dev-agent-ruff-gate.sh'.\n"
        "AC-47.3: the expected gate command must be explicitly named in the guard module."
    )


def test_settings_json_hook_command_pinned_by_name_in_guard_module_ac473() -> None:
    """The hook command in settings.json matches the guard module's named constant.

    Structural check (JSON parse, not text grep) that the live settings.json
    carries exactly the command the guard module expects.  A settings.json edit
    that changes the command is caught here AND at collection time (via Guard 3
    in the guard module which raises ImportError if the command is absent).

    AC-47.3: entrypoint pinned by name in the wired config.
    """
    assert SETTINGS_JSON_PATH.exists(), f"settings.json missing: {SETTINGS_JSON_PATH}"
    settings = json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
    pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])
    bash_entries = [e for e in pre_tool_use if isinstance(e, dict) and e.get("matcher") == "Bash"]
    commands = [
        h.get("command", "")
        for entry in bash_entries
        for h in entry.get("hooks", [])
        if isinstance(h, dict)
    ]
    # The literal name 'dev-agent-ruff-gate.sh' must appear in at least one command.
    gate_commands = [c for c in commands if "dev-agent-ruff-gate.sh" in c]
    assert gate_commands, (
        "No hook command referencing 'dev-agent-ruff-gate.sh' in settings.json.\n"
        f"PreToolUse[Bash] commands: {commands!r}\n"
        "AC-47.3: the entrypoint name must be pinned in the live hook config."
    )


# ---------------------------------------------------------------------------
# AST: guard module has no try/except anti-pattern
# (AC-47.3 explicitly excludes 'the ImportError-trap anti-pattern of a swallowed exception')
# ---------------------------------------------------------------------------


def _load_guard_module_ast() -> ast.Module:
    """Return the parsed AST for core/dev_agent_ruff_gate_guard.py."""
    assert GUARD_MODULE.exists(), f"Guard module not found: {GUARD_MODULE}"
    source = GUARD_MODULE.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(GUARD_MODULE))


def test_guard_module_has_no_try_except_swallowing_importerror_ac473() -> None:
    """AST scan: the guard module has NO try/except blocks at all.

    A try/except anywhere in the guard module would risk swallowing the
    ImportError raises that are the mechanism's loud-failure guarantee.
    The anti-pattern 'try: import X; except ImportError: pass' is the
    explicitly excluded pattern in AC-47.3.

    This test uses AST parsing (not text search) so it catches the pattern
    even if it spans multiple lines or uses 'except Exception'.

    AC-47.3: 'the ImportError-trap anti-pattern of a swallowed exception is
    explicitly excluded'.
    """
    tree = _load_guard_module_ast()
    try_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert not try_nodes, (
        f"core/dev_agent_ruff_gate_guard.py contains {len(try_nodes)} try/except block(s).\n"
        "AC-47.3: the guard module must NOT use try/except — any try/except risks swallowing\n"
        "the ImportError raises that make the gate deletion detectable at collection time.\n"
        "Remove ALL try/except blocks from the guard module."
    )


def test_guard_module_raises_importerror_not_swallows_it_ac473() -> None:
    """AST scan: the guard module has at least one top-level 'raise ImportError(...)'.

    Verifies via AST that the guard module actively RAISES ImportError (the loud-failure
    mechanism) rather than merely catching or ignoring it.  At least three such raises
    are expected (one per guard condition: script missing, settings.json missing,
    hook command missing).

    AC-47.3: a removed guard fails loudly via ImportError, never a no-op.
    """
    tree = _load_guard_module_ast()

    raise_nodes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            continue
        # Match: raise ImportError(...)
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "ImportError":
            raise_nodes.append(node)

    assert len(raise_nodes) >= 3, (
        f"Expected at least 3 'raise ImportError(...)' statements in the guard module; "
        f"found {len(raise_nodes)}.\n"
        "AC-47.3: the guard must raise ImportError for each condition (script missing,\n"
        "settings.json missing, hook command missing) so ALL three failure modes are loud."
    )


# ---------------------------------------------------------------------------
# Structural: removed guard fails loudly (never degrades to no-op)
# ---------------------------------------------------------------------------


def test_guard_module_importerror_messages_name_the_gate_script_ac473() -> None:
    """The guard module's ImportError messages name 'dev-agent-ruff-gate.sh'.

    Error messages that DON'T name the gate script make failures hard to diagnose —
    an operator sees a generic ImportError with no guidance. All three ImportError
    raises in the guard module must include the script name so the failure is
    self-diagnosing.

    AC-47.3: a removed guard fails loudly AND informatively.
    """
    assert GUARD_MODULE.exists(), f"Guard module not found: {GUARD_MODULE}"
    source = GUARD_MODULE.read_text(encoding="utf-8")

    # Each raise ImportError(...) block must be followed (within a few lines) by
    # a message containing the gate script name.  We verify by checking the full
    # source contains the name at least as many times as there are ImportError raises.
    gate_name_count = source.count("dev-agent-ruff-gate.sh")
    # Minimum: once for the path constant + once per ImportError message
    assert gate_name_count >= 3, (
        f"'dev-agent-ruff-gate.sh' appears only {gate_name_count} time(s) in the guard module.\n"
        "Each ImportError raise (script-missing, settings-missing, hook-missing) should\n"
        "name the gate script so the failure is self-diagnosing.\n"
        "AC-47.3: loud failure requires informative error messages."
    )


def test_guard_module_guards_all_three_conditions_ac473() -> None:
    """The guard module checks all three conditions required for the gate to be wired.

    Three conditions are required for the gate to be active:
      1. scripts/dev-agent-ruff-gate.sh exists (the gate script itself)
      2. .claude/settings.json exists (the Claude Code hook config)
      3. The hook command ('bash scripts/dev-agent-ruff-gate.sh') is in settings.json

    This test verifies the guard module references all three by inspecting its source
    for the key artifacts — so a partial guard (guarding only 1 or 2 conditions) is
    caught.

    AC-47.3: the gate mechanism cannot silently vanish through any of these three paths.
    """
    assert GUARD_MODULE.exists(), f"Guard module not found: {GUARD_MODULE}"
    source = GUARD_MODULE.read_text(encoding="utf-8")

    # Condition 1: gate script path
    assert "dev-agent-ruff-gate.sh" in source, (
        "Guard module does not reference 'dev-agent-ruff-gate.sh' — Condition 1 is unguarded."
    )
    # Condition 2: settings.json
    assert "settings.json" in source, (
        "Guard module does not reference 'settings.json' — Condition 2 is unguarded."
    )
    # Condition 3: the hook command string (or enough of it to pin it)
    assert "PreToolUse" in source, (
        "Guard module does not reference 'PreToolUse' — Condition 3 (hook wiring) is unguarded."
    )
