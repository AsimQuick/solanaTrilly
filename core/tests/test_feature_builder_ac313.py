# ---
# module: core.tests.test_feature_builder_ac313
# sprint: sprint-7
# story: US-31 AC-31.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_builder, core.tasks, pytest
# ---
"""AC-31.3 — Leak-free label validation: label_def drawing from within [0, window_s)
is rejected at task submission time."""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# ImportError gate — pins AC-31.2 gate function so renaming/deleting it fails
# pytest collection (mirrors AC-28.3 / AC-30.3 pattern)
# ---------------------------------------------------------------------------
from core.feature_builder import validate_label_def
from core.tasks import build_features
from core.tests.test_feature_builder_ac312 import (
    test_manifest_fields_all_populated,
    test_manifest_run_twice_byte_identical_content_hash,
)


def test_label_def_with_label_start_s_within_window_is_rejected():
    """label_start_s=60 with window_s=120 must raise ValueError (overlap violation)."""
    with pytest.raises(ValueError, match="label_start_s"):
        validate_label_def({"label_start_s": 60, "horizon_s": 60}, window_s=120)


def test_label_def_with_label_start_s_at_zero_is_rejected():
    """label_start_s=0 with window_s=120 must raise ValueError (draws from t=0)."""
    with pytest.raises(ValueError, match="label_start_s"):
        validate_label_def({"label_start_s": 0}, window_s=120)


def test_label_def_with_label_start_s_just_before_window_is_rejected():
    """label_start_s=window_s-1 must raise ValueError (just inside the feature window)."""
    window_s = 120
    with pytest.raises(ValueError, match="label_start_s"):
        validate_label_def({"label_start_s": window_s - 1}, window_s=window_s)


def test_label_def_with_label_start_s_at_window_boundary_is_allowed():
    """label_start_s=window_s must NOT raise (starts exactly at feature window end)."""
    validate_label_def({"label_start_s": 120}, window_s=120)


def test_label_def_with_label_start_s_after_window_is_allowed():
    """label_start_s=300 with window_s=120 must NOT raise (well after feature window)."""
    validate_label_def({"label_start_s": 300, "horizon_s": 60}, window_s=120)


def test_label_def_without_label_start_s_is_allowed():
    """label_def without label_start_s must NOT raise (backward compatible)."""
    validate_label_def({"outcome": "pump", "horizon_s": 300}, window_s=120)


def test_error_message_is_clear():
    """ValueError message must mention label_start_s, the value, window_s, and 'window'."""
    with pytest.raises(ValueError) as exc_info:
        validate_label_def({"label_start_s": 60}, window_s=120)

    msg = str(exc_info.value)
    assert "label_start_s" in msg, f"Error message missing 'label_start_s': {msg!r}"
    assert "60" in msg, f"Error message missing value '60': {msg!r}"
    assert "120" in msg, f"Error message missing window_s '120': {msg!r}"
    assert "window" in msg, f"Error message missing 'window': {msg!r}"


def test_rejection_fires_at_task_invocation_before_db_lookup():
    """Validation fires BEFORE DB lookup: pk=999999 (non-existent) but ValueError, not DoesNotExist."""
    with pytest.raises(Exception, match="label_start_s"):
        build_features.apply(
            args=[999999, [], {"label_start_s": 10}],
            kwargs={"window_s": 120},
        ).get()


def test_ac313_gate_functions_callable():
    """Pinned AC-31.2 gate functions are callable (companion to module-level ImportError trap)."""
    assert callable(test_manifest_fields_all_populated)
    assert callable(test_manifest_run_twice_byte_identical_content_hash)
