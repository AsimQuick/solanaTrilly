# ---
# module: core.tests.test_pipeline_config_ac93
# sprint: sprint-3
# story: US-9 AC-9.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models
# ---
"""Tests for US-9 AC-9.3: PipelineState singleton model.

Verifies:
  - All three control flags (firehose_active, scoring_enabled, trading_enabled)
    default to False (explicit start, no silent auto-recovery per PRD §5.3).
  - The model is a true singleton: a second instance is normalised to pk=1.
  - PipelineState.get() always returns the singleton at pk=1.
  - The DB table name is 'pipeline_state' (PRD §8).
"""
import pytest

from core.models import PipelineState


# ---------------------------------------------------------------------------
# Field defaults — no DB needed
# ---------------------------------------------------------------------------


def test_pipeline_state_firehose_active_default_false():
    """firehose_active field must default to False (PRD §5.3)."""
    field = PipelineState._meta.get_field("firehose_active")
    assert field.default is False


def test_pipeline_state_scoring_enabled_default_false():
    """scoring_enabled field must default to False (PRD §5.3)."""
    field = PipelineState._meta.get_field("scoring_enabled")
    assert field.default is False


def test_pipeline_state_trading_enabled_default_false():
    """trading_enabled field must default to False (PRD §5.3)."""
    field = PipelineState._meta.get_field("trading_enabled")
    assert field.default is False


def test_pipeline_state_db_table_name():
    """Meta.db_table must be 'pipeline_state' (PRD §8)."""
    assert PipelineState._meta.db_table == "pipeline_state"


# ---------------------------------------------------------------------------
# DB-backed singleton tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pipeline_state_flags_default_false():
    """Singleton created via PipelineState.get() must have all flags False."""
    state = PipelineState.get()
    assert state.firehose_active is False
    assert state.scoring_enabled is False
    assert state.trading_enabled is False


@pytest.mark.django_db
def test_pipeline_state_singleton_second_save_normalises_to_id1():
    """Saving a second PipelineState instance overwrites pk=1; count stays at 1."""
    PipelineState.get()  # create the singleton

    second = PipelineState()
    second.save()  # save() forces pk=1

    assert PipelineState.objects.count() == 1
    assert PipelineState.objects.get().pk == 1


@pytest.mark.django_db
def test_pipeline_state_singleton_direct_create_normalises_to_id1():
    """Two successive objects.create() calls result in exactly one row at pk=1."""
    PipelineState.objects.create()
    PipelineState.objects.create()  # save() inside create() forces pk=1

    assert PipelineState.objects.count() == 1
    assert PipelineState.objects.get().pk == 1


@pytest.mark.django_db
def test_pipeline_state_get_class_method_returns_pk1():
    """PipelineState.get() always returns the singleton at pk=1."""
    state = PipelineState.get()
    assert state.pk == 1
