# ---
# module: core.tests.test_feature_set_ac291
# sprint: sprint-7
# story: US-29 AC-29.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, django.db, pytest
# ---
"""AC-29.1 — FeatureSet model round-trip test.

Verifies that the FeatureSet model (table 'feature_sets') can create a row
with all required columns and read every column back from real Postgres.
"""
import pytest

from core.models import FeatureSet

# ---------------------------------------------------------------------------
# Field introspection — schema invariants
# ---------------------------------------------------------------------------

def test_feature_set_table_name():
    assert FeatureSet._meta.db_table == "feature_sets"


def test_version_is_charfield():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("version")
    assert isinstance(field, dj_models.CharField)
    assert field.max_length == 64


def test_math_version_is_charfield():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("math_version")
    assert isinstance(field, dj_models.CharField)
    assert field.max_length == 64


def test_columns_is_jsonfield():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("columns")
    assert isinstance(field, dj_models.JSONField)


def test_live_servable_is_jsonfield():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("live_servable")
    assert isinstance(field, dj_models.JSONField)


def test_hash_is_charfield():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("hash")
    assert isinstance(field, dj_models.CharField)
    assert field.max_length == 64


def test_notes_is_textfield_blank():
    from django.db import models as dj_models
    field = FeatureSet._meta.get_field("notes")
    assert isinstance(field, dj_models.TextField)
    assert field.blank is True


def test_hash_field_is_indexed():
    field = FeatureSet._meta.get_field("hash")
    assert field.db_index is True


# ---------------------------------------------------------------------------
# Round-trip: create a row and read every column back from Postgres
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_feature_set_row_roundtrip_all_columns():
    """Create a FeatureSet row and verify every column survives a Postgres round-trip."""
    cols = [
        "tape_n_trades",
        "tape_n_unique_traders",
        "tape_ret_total",
        "tape_max_drawdown",
        "tape_logprice_slope_per_s",
        "tape_close_b0",
        "tape_close_b1",
        "tape_close_b2",
        "tape_close_b3",
        "tape_close_b4",
        "tape_close_b5",
        "tape_close_b6",
        "tape_close_b7",
    ]
    live = [
        "tape_n_trades",
        "tape_n_unique_traders",
        "tape_ret_total",
        "tape_logprice_slope_per_s",
    ]
    content_hash = "a" * 64

    fs = FeatureSet.objects.create(
        version="v1",
        math_version="solanabilly3:sprint-7",
        columns=cols,
        live_servable=live,
        hash=content_hash,
        notes="Sprint-7 baseline extraction contract.",
    )

    obj = FeatureSet.objects.get(pk=fs.pk)

    assert obj.version == "v1"
    assert obj.math_version == "solanabilly3:sprint-7"
    assert obj.columns == cols
    assert obj.live_servable == live
    assert obj.hash == content_hash
    assert obj.notes == "Sprint-7 baseline extraction contract."


@pytest.mark.django_db
def test_feature_set_notes_defaults_to_empty():
    """notes is optional — omitting it stores an empty string."""
    fs = FeatureSet.objects.create(
        version="v2",
        math_version="solanabilly3:sprint-7",
        columns=["tape_n_trades"],
        live_servable=["tape_n_trades"],
        hash="b" * 64,
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    assert obj.notes == ""


@pytest.mark.django_db
def test_feature_set_empty_lists_round_trip():
    """columns and live_servable accept empty lists (degenerate contract)."""
    fs = FeatureSet.objects.create(
        version="v0",
        math_version="solanabilly3:sprint-7",
        columns=[],
        live_servable=[],
        hash="c" * 64,
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    assert obj.columns == []
    assert obj.live_servable == []
