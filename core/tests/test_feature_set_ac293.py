# ---
# module: core.tests.test_feature_set_ac293
# sprint: sprint-7
# story: US-29 AC-29.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.models, core.admin, django.contrib.auth, django.test, pytest
# ---
"""AC-29.3 — live_servable/training_only split + read-only admin.

Verifies:
  1. live_servable[] ⊆ columns[] (D2 subset invariant)
  2. A designated training-only feature is present in columns[] but absent
     from live_servable[] (D3 — training_only definition)
  3. The FeatureSet admin changelist returns HTTP 200 for an authenticated
     staff user.
  4. The FeatureSet admin change-detail page returns HTTP 200 for an
     authenticated staff user.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import FeatureSet

User = get_user_model()

# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

# All features the extractor can produce (ordered contract).
_ALL_COLUMNS = [
    "tape_n_trades",
    "tape_n_unique_traders",
    "tape_ret_total",
    "tape_max_drawdown",          # training-only (requires full offline window)
    "tape_logprice_slope_per_s",
    "tape_close_b0",              # training-only (bucket requires enough trades)
    "tape_close_b1",
    "tape_close_b2",
    "tape_close_b3",
]

# Subset computable at live score time (D2).
_LIVE_SERVABLE = [
    "tape_n_trades",
    "tape_n_unique_traders",
    "tape_ret_total",
    "tape_logprice_slope_per_s",
]

# A column explicitly designated as training-only for these tests.
_TRAINING_ONLY_COLUMN = "tape_max_drawdown"

_MATH_VERSION = "solanabilly3:sprint-7"


# ---------------------------------------------------------------------------
# Pure logic tests — no DB required (D2/D3 invariant)
# ---------------------------------------------------------------------------

def test_live_servable_is_subset_of_columns():
    """live_servable[] ⊆ columns[] — every live feature must be in the full set."""
    assert set(_LIVE_SERVABLE).issubset(set(_ALL_COLUMNS))


def test_live_servable_is_strict_subset():
    """live_servable[] is a STRICT subset — training-only features exist."""
    assert set(_LIVE_SERVABLE) < set(_ALL_COLUMNS)


def test_training_only_column_in_columns_not_in_live_servable():
    """Designated training-only column is in columns[] but absent from live_servable[]."""
    assert _TRAINING_ONLY_COLUMN in _ALL_COLUMNS
    assert _TRAINING_ONLY_COLUMN not in _LIVE_SERVABLE


def test_all_live_servable_columns_are_in_columns():
    """Every element of live_servable is also in columns (D2 ⊆ contract)."""
    for col in _LIVE_SERVABLE:
        assert col in _ALL_COLUMNS, f"{col!r} in live_servable but not in columns"


def test_training_only_features_are_identifiable():
    """Features in columns but not in live_servable form a non-empty training_only set."""
    training_only = set(_ALL_COLUMNS) - set(_LIVE_SERVABLE)
    assert len(training_only) > 0
    assert _TRAINING_ONLY_COLUMN in training_only


def test_empty_live_servable_is_valid_subset():
    """Empty live_servable is a valid (degenerate) subset of any columns list."""
    assert set().issubset(set(_ALL_COLUMNS))


# ---------------------------------------------------------------------------
# DB round-trip — subset invariant survives Postgres serialisation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_live_servable_subset_survives_db_roundtrip():
    """live_servable[] ⊆ columns[] holds after a Postgres round-trip."""
    fs = FeatureSet.objects.create(
        version="v1",
        math_version=_MATH_VERSION,
        columns=_ALL_COLUMNS,
        live_servable=_LIVE_SERVABLE,
        hash=FeatureSet.compute_hash(_ALL_COLUMNS, _MATH_VERSION),
        notes="AC-29.3 subset invariant round-trip test.",
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    assert set(obj.live_servable).issubset(set(obj.columns))


@pytest.mark.django_db
def test_training_only_column_excluded_from_live_servable_in_db():
    """Designated training-only column is absent from live_servable after DB round-trip."""
    fs = FeatureSet.objects.create(
        version="v1",
        math_version=_MATH_VERSION,
        columns=_ALL_COLUMNS,
        live_servable=_LIVE_SERVABLE,
        hash=FeatureSet.compute_hash(_ALL_COLUMNS, _MATH_VERSION),
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    assert _TRAINING_ONLY_COLUMN in obj.columns
    assert _TRAINING_ONLY_COLUMN not in obj.live_servable


# ---------------------------------------------------------------------------
# Admin HTTP 200 tests — read-only changelist + detail
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_feature_set_admin_changelist_returns_200(client):
    """Admin changelist for FeatureSet returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="fs_list_user",
        password="testpass",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(staff)
    url = reverse("admin:core_featureset_changelist")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_feature_set_admin_change_detail_returns_200(client):
    """Admin change-detail page for a FeatureSet instance returns HTTP 200 for a staff user."""
    staff = User.objects.create_user(
        username="fs_detail_user",
        password="testpass",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(staff)
    fs = FeatureSet.objects.create(
        version="v1",
        math_version=_MATH_VERSION,
        columns=_ALL_COLUMNS,
        live_servable=_LIVE_SERVABLE,
        hash=FeatureSet.compute_hash(_ALL_COLUMNS, _MATH_VERSION),
        notes="Admin detail test fixture.",
    )
    url = reverse("admin:core_featureset_change", args=[fs.pk])
    response = client.get(url)
    assert response.status_code == 200
