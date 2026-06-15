# ---
# module: core.tests.test_pipeline_config_ac92
# sprint: sprint-3
# story: US-9 AC-9.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, django.contrib.auth, simple_history
# ---
"""Tests for US-9 AC-9.2: django-simple-history audit on PipelineConfig.

Verifies that every create/activate of a PipelineConfig writes a historical
record capturing who made the change and when.
"""

import pytest
from django.contrib.auth import get_user_model

from core.models import PipelineConfig

User = get_user_model()


@pytest.mark.django_db
def test_history_created_on_pipeline_config_create():
    """A newly created PipelineConfig produces exactly one '+' history record."""
    config = PipelineConfig.objects.create(version=1, label="audit-test")
    assert config.history.count() == 1
    assert config.history.first().history_type == "+"


@pytest.mark.django_db
def test_history_written_on_pipeline_config_activate():
    """Activating a PipelineConfig writes a second '~' history record."""
    config = PipelineConfig.objects.create(version=1, label="audit-test")
    config.is_active = True
    config.save()

    assert config.history.count() == 2
    latest = config.history.first()
    assert latest.history_type == "~"
    assert latest.is_active is True


@pytest.mark.django_db
def test_history_captures_when():
    """history_date is populated (captures 'when' the change occurred)."""
    config = PipelineConfig.objects.create(version=1, label="audit-test")
    config.is_active = True
    config.save()

    assert config.history.first().history_date is not None


@pytest.mark.django_db
def test_history_captures_who():
    """history_user is captured when _history_user is set on the model instance."""
    user = User.objects.create_user(username="auditor", password="testpass")
    config = PipelineConfig.objects.create(version=1, label="audit-test")

    config._history_user = user
    config.is_active = True
    config.save()

    assert config.history.first().history_user == user


@pytest.mark.django_db
def test_create_then_activate_sequence_has_two_history_records():
    """Full create-then-activate sequence produces exactly 2 ordered history records."""
    config = PipelineConfig.objects.create(version=1, label="audit-test")
    config.is_active = True
    config.save()

    records = list(config.history.order_by("history_date", "history_id"))
    assert len(records) == 2

    # First record: the create
    assert records[0].history_type == "+"
    # Second record: the activate update
    assert records[1].history_type == "~"
