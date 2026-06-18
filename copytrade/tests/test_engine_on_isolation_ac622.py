# ---
# module: copytrade.tests.test_engine_on_isolation_ac622
# sprint: sprint-12
# story: US-62 AC-62.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, copytrade.engine_control, copytrade.models,
#   core.models, ast, pathlib
# ---
"""AC-62.2: engine ON/OFF is RUNTIME state gating ONLY the copytrade engine (SPEC §2/§5).

Tests verify bidirectional isolation between the copytrade engine toggle and the
model pipeline control flags (PipelineState.firehose_active / scoring_enabled).

  - Toggling copytrade engine_on leaves PipelineState.firehose_active unchanged.
  - Toggling copytrade engine_on leaves PipelineState.scoring_enabled unchanged.
  - Toggling PipelineState.firehose_active does not affect CopyTradeSettings.engine_on.
  - Toggling PipelineState.scoring_enabled does not affect CopyTradeSettings.engine_on.
  - Copytrade ON/OFF and model-pipeline ON/OFF are fully independent (bidirectional).

AST guard: copytrade.engine_control must import nothing from core.* so the §5 isolation
boundary is enforced at the module level (not just by convention).

H1 import trap: set_engine_on must be importable from copytrade.engine_control.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from copytrade.engine_control import set_engine_on
from copytrade.models import CopyTradeSettings
from core.models import PipelineState

assert set_engine_on  # H1 guard — deletion/rename fails collection

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# AST guard
# ---------------------------------------------------------------------------


def test_engine_control_imports_no_core_modules():
    """AST guard: copytrade/engine_control.py must NOT import from core.* (§5 isolation)."""
    src = (REPO_ROOT / "copytrade" / "engine_control.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("core"), (
                f"engine_control.py imports from {node.module!r} — "
                "violates §5 isolation: the copytrade ON/OFF toggle must have "
                "zero imports from the model-pipeline control plane."
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("core"), (
                    f"engine_control.py imports {alias.name!r} — §5 isolation violated."
                )


# ---------------------------------------------------------------------------
# set_engine_on → PipelineState isolation (copytrade ON/OFF does not affect pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_set_engine_on_true_leaves_firehose_active_unchanged():
    """set_engine_on(True) must NOT change PipelineState.firehose_active."""
    state = PipelineState.get()
    initial = state.firehose_active  # False by default

    set_engine_on(True)

    state.refresh_from_db()
    assert state.firehose_active == initial, (
        "set_engine_on(True) mutated PipelineState.firehose_active — §5 isolation violated."
    )


@pytest.mark.django_db
def test_set_engine_on_false_leaves_firehose_active_unchanged():
    """set_engine_on(False) must NOT change PipelineState.firehose_active (even when it is True)."""
    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    set_engine_on(False)

    state.refresh_from_db()
    assert state.firehose_active is True, (
        "set_engine_on(False) cleared PipelineState.firehose_active — §5 isolation violated."
    )


@pytest.mark.django_db
def test_set_engine_on_true_leaves_scoring_enabled_unchanged():
    """set_engine_on(True) must NOT change PipelineState.scoring_enabled."""
    state = PipelineState.get()
    initial = state.scoring_enabled  # False by default

    set_engine_on(True)

    state.refresh_from_db()
    assert state.scoring_enabled == initial, (
        "set_engine_on(True) mutated PipelineState.scoring_enabled — §5 isolation violated."
    )


@pytest.mark.django_db
def test_set_engine_on_false_leaves_scoring_enabled_unchanged():
    """set_engine_on(False) must NOT change PipelineState.scoring_enabled (even when it is True)."""
    state = PipelineState.get()
    state.scoring_enabled = True
    state.save()

    set_engine_on(False)

    state.refresh_from_db()
    assert state.scoring_enabled is True, (
        "set_engine_on(False) cleared PipelineState.scoring_enabled — §5 isolation violated."
    )


# ---------------------------------------------------------------------------
# PipelineState → CopyTradeSettings isolation (pipeline ON/OFF does not affect copytrade)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_firehose_active_toggle_leaves_copytrade_engine_on_unchanged():
    """Toggling PipelineState.firehose_active must NOT change CopyTradeSettings.engine_on."""
    CopyTradeSettings.get()  # ensure row exists before raw UPDATE
    CopyTradeSettings.objects.filter(pk=1).update(engine_on=True)

    state = PipelineState.get()
    state.firehose_active = True
    state.save()
    state.firehose_active = False
    state.save()

    assert CopyTradeSettings.get().engine_on is True, (
        "PipelineState.firehose_active toggle changed CopyTradeSettings.engine_on — "
        "§5 isolation violated."
    )


@pytest.mark.django_db
def test_scoring_enabled_toggle_leaves_copytrade_engine_on_unchanged():
    """Toggling PipelineState.scoring_enabled must NOT change CopyTradeSettings.engine_on."""
    CopyTradeSettings.get()  # ensure row exists before raw UPDATE
    CopyTradeSettings.objects.filter(pk=1).update(engine_on=True)

    state = PipelineState.get()
    state.scoring_enabled = True
    state.save()
    state.scoring_enabled = False
    state.save()

    assert CopyTradeSettings.get().engine_on is True, (
        "PipelineState.scoring_enabled toggle changed CopyTradeSettings.engine_on — "
        "§5 isolation violated."
    )


# ---------------------------------------------------------------------------
# Bidirectional independence — full sequence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bidirectional_independence_copytrade_and_model_pipeline():
    """Copytrade ON/OFF and model-pipeline ON/OFF are fully independent.

    Full sequence:
      1. Both start OFF.
      2. Copytrade ON  → model pipeline still OFF.
      3. Model pipeline ON → copytrade still ON.
      4. Copytrade OFF → model pipeline still ON.
      5. Model pipeline OFF → copytrade still OFF.
    """
    # Step 1: both OFF
    state = PipelineState.get()
    state.firehose_active = False
    state.scoring_enabled = False
    state.save()
    CopyTradeSettings.get()  # ensure row exists before raw UPDATE
    CopyTradeSettings.objects.filter(pk=1).update(engine_on=False)

    # Step 2: copytrade ON
    set_engine_on(True)
    state.refresh_from_db()
    assert CopyTradeSettings.get().engine_on is True
    assert state.firehose_active is False, "Copytrade ON affected firehose_active."
    assert state.scoring_enabled is False, "Copytrade ON affected scoring_enabled."

    # Step 3: model pipeline ON
    state.firehose_active = True
    state.scoring_enabled = True
    state.save()
    assert CopyTradeSettings.get().engine_on is True, "Pipeline ON affected copytrade engine_on."

    # Step 4: copytrade OFF
    set_engine_on(False)
    state.refresh_from_db()
    assert CopyTradeSettings.get().engine_on is False
    assert state.firehose_active is True, "Copytrade OFF affected firehose_active."
    assert state.scoring_enabled is True, "Copytrade OFF affected scoring_enabled."

    # Step 5: model pipeline OFF
    state.firehose_active = False
    state.scoring_enabled = False
    state.save()
    assert CopyTradeSettings.get().engine_on is False, "Pipeline OFF affected copytrade engine_on."


# ---------------------------------------------------------------------------
# H1 import trap
# ---------------------------------------------------------------------------


def test_h1_import_trap():
    """Import trap: set_engine_on must be importable from copytrade.engine_control."""
    from copytrade.engine_control import set_engine_on as _fn  # noqa: F401
    assert _fn is not None
