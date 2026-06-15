# ---
# module: core.tests.test_resolver_ac113
# sprint: sprint-3
# story: US-11 AC-11.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.resolver, core.models, core.apps, django, ast
# ---
"""AC-11.3 — No silent auto-start: pipeline_state flags never auto-flip.

Verifies that pipeline_state's firehose_active / trading_enabled / scoring_enabled
are only ever changed by an explicit, deliberate action — no code path sets them
True on boot, on app ready, on resolver read, or on a WS drop (§5.3, §15.6).

Test structure:
  (1) test_pipeline_state_flags_default_false
        — PipelineState.get() returns all False on creation; app boot did not auto-flip
  (2) test_get_active_config_does_not_flip_flags_with_active_config
        — calling get_active_config() with an active config leaves all flags False
  (3) test_get_active_config_does_not_flip_flags_without_active_config
        — calling get_active_config() with no active config (returns None) leaves flags False
  (4) test_flags_remain_false_after_repeated_resolver_calls
        — three consecutive get_active_config() calls leave flags False at every step
  (5) test_no_auto_flip_in_boot_paths
        — static AST guard: apps.py and consumers.py contain no code that auto-sets flags True
  (6) test_resolver_does_not_reference_pipeline_state
        — static AST guard: resolver.py never imports or references PipelineState
"""
import ast
from pathlib import Path

import pytest

from core.models import PipelineConfig, PipelineState
from core.resolver import get_active_config, invalidate_active_config_cache

# ---------------------------------------------------------------------------
# Valid section fixtures — all §5.2 invariants satisfied:
#   scoring.window_s (180) > score_at_elapsed_s (120)       ✓  leak guard
#   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)  ✓  D4
#   scoring.capture_buffer_s (4) >= 3                       ✓  tape tail
#   gate == "adaptive_topk"                                 ✓  id22 lesson
# ---------------------------------------------------------------------------

VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}

# Repo root used by static-analysis guards
REPO_ROOT = Path(__file__).resolve().parents[2]

# Flag names that must never be auto-set to True on boot/resolver/WS-drop
_STATE_FLAGS = {"firehose_active", "scoring_enabled", "trading_enabled"}


# ---------------------------------------------------------------------------
# Test 1: PipelineState created by get() returns all False (boot did not flip)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pipeline_state_flags_default_false():
    """PipelineState.get() returns all flags as False after boot/import.

    The test itself runs inside a fully-booted Django process — if any boot
    code (AppConfig.ready(), post_migrate signal, etc.) had auto-set a flag,
    it would already be True by the time this assertion runs.
    """
    state = PipelineState.get()
    assert state.firehose_active is False, "firehose_active must not be auto-set on boot"
    assert state.scoring_enabled is False, "scoring_enabled must not be auto-set on boot"
    assert state.trading_enabled is False, "trading_enabled must not be auto-set on boot"


# ---------------------------------------------------------------------------
# Test 2: get_active_config() with an active config does not flip any flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_active_config_does_not_flip_flags_with_active_config():
    """Calling get_active_config() when an active config exists does not touch pipeline_state."""
    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac113-active",
        is_active=True,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )

    state = PipelineState.get()
    assert state.firehose_active is False
    assert state.scoring_enabled is False
    assert state.trading_enabled is False

    result = get_active_config()
    assert result is not None  # sanity: the resolver returned a config

    state.refresh_from_db()
    assert state.firehose_active is False, "get_active_config() must not set firehose_active=True"
    assert state.scoring_enabled is False, "get_active_config() must not set scoring_enabled=True"
    assert state.trading_enabled is False, "get_active_config() must not set trading_enabled=True"


# ---------------------------------------------------------------------------
# Test 3: get_active_config() returning None does not flip any flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_active_config_does_not_flip_flags_without_active_config():
    """Calling get_active_config() with no active config (returns None) does not touch pipeline_state."""
    invalidate_active_config_cache()

    state = PipelineState.get()

    result = get_active_config()
    assert result is None  # sanity: no active config exists

    state.refresh_from_db()
    assert state.firehose_active is False
    assert state.scoring_enabled is False
    assert state.trading_enabled is False


# ---------------------------------------------------------------------------
# Test 4: repeated resolver calls never flip flags
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_flags_remain_false_after_repeated_resolver_calls():
    """Three consecutive get_active_config() calls leave all flags False at every step."""
    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac113-repeat",
        is_active=True,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )

    state = PipelineState.get()

    for call_n in range(1, 4):
        get_active_config()
        state.refresh_from_db()
        assert state.firehose_active is False, f"firehose_active flipped on call {call_n}"
        assert state.scoring_enabled is False, f"scoring_enabled flipped on call {call_n}"
        assert state.trading_enabled is False, f"trading_enabled flipped on call {call_n}"


# ---------------------------------------------------------------------------
# Test 5: static AST guard — boot-path files contain no auto-flag-setting code
# ---------------------------------------------------------------------------


def _find_flag_true_assignments(path: Path) -> list[str]:
    """Return a list of human-readable descriptions of flag=True assignments found in path."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword argument: e.g. .update(firehose_active=True) or objects.create(firehose_active=True)
        if isinstance(node, ast.keyword):
            if (
                node.arg in _STATE_FLAGS
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                findings.append(f"{rel}: keyword {node.arg}=True at line {node.value.lineno}")

        # direct attribute assignment: e.g. state.firehose_active = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in _STATE_FLAGS
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    findings.append(
                        f"{rel}: attribute assignment .{target.attr} = True at line {node.value.lineno}"
                    )

    return findings


def test_no_auto_flip_in_boot_paths():
    """Static AST guard: apps.py and consumers.py contain no code that auto-sets pipeline_state flags True.

    These are the two files that Django calls automatically on startup (AppConfig.ready())
    and on WebSocket lifecycle events (connect/disconnect) — neither must touch the state flags.
    Passes trivially now; becomes a regression gate as those files grow.
    """
    boot_path_files = [
        REPO_ROOT / "core" / "apps.py",
        REPO_ROOT / "core" / "consumers.py",
    ]

    violations: list[str] = []
    for path in boot_path_files:
        violations.extend(_find_flag_true_assignments(path))

    assert not violations, (
        "Boot-path files must not auto-set pipeline_state flags to True. "
        "Flags may only be set by explicit operator actions.\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 6: static AST guard — resolver.py never references PipelineState
# ---------------------------------------------------------------------------


def test_resolver_does_not_reference_pipeline_state():
    """resolver.py must not import or reference PipelineState.

    The resolver reads PipelineConfig (the tunable snapshot) only. Pipeline
    control flags (firehose_active etc.) are read and written exclusively by
    explicit operator actions, never by the resolver.
    """
    resolver_path = REPO_ROOT / "core" / "resolver.py"
    source = resolver_path.read_text(encoding="utf-8")
    assert "PipelineState" not in source, (
        "core/resolver.py must not reference PipelineState. "
        "The resolver reads PipelineConfig only; pipeline_state flags are "
        "controlled by explicit operator actions, not by config resolution."
    )
