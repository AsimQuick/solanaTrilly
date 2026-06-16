# ---
# module: core.tests.test_feature_set_ac293
# sprint: sprint-7
# story: US-29 AC-29.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.models, core.admin, django.contrib.auth, django.test, pytest
# ---
"""AC-29.3 — live_servable ⊆ columns split + read-only Django admin.

Verified properties:
  1. live_servable[] is a strict subset of columns[] (the D2/D3 split).
  2. A designated training-only column (present in columns[], absent from
     live_servable[]) is correctly excluded — confirming the D3 contract.
  3. The FeatureSet admin changelist returns HTTP 200 for a staff user.
  4. The FeatureSet admin change-detail page returns HTTP 200 for a staff user.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import FeatureSet

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

# Full extraction contract — all features produced by the extractor.
_ALL_COLUMNS = [
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

# Subset computable at live score time (D2) — excludes training-only features.
_LIVE_SERVABLE = [
    "tape_n_trades",
    "tape_n_unique_traders",
    "tape_ret_total",
    "tape_logprice_slope_per_s",
]

# Designated training-only column — present in _ALL_COLUMNS, absent from _LIVE_SERVABLE.
_TRAINING_ONLY_COLUMN = "tape_max_drawdown"

_MATH_VERSION = "solanabilly3:sprint-7"


# ---------------------------------------------------------------------------
# D2/D3 split: live_servable ⊆ columns
# ---------------------------------------------------------------------------


def test_live_servable_is_subset_of_columns():
    """live_servable[] must be a subset of columns[] (D2 ⊆ columns, §6.4.5)."""
    assert set(_LIVE_SERVABLE) <= set(_ALL_COLUMNS), (
        "live_servable contains columns not in columns[]: "
        f"{set(_LIVE_SERVABLE) - set(_ALL_COLUMNS)}"
    )


def test_training_only_column_in_columns_but_not_live_servable():
    """Designated training-only column is in columns[] but absent from live_servable[] (D3)."""
    assert _TRAINING_ONLY_COLUMN in _ALL_COLUMNS, (
        f"Training-only column '{_TRAINING_ONLY_COLUMN}' must be in columns[]"
    )
    assert _TRAINING_ONLY_COLUMN not in _LIVE_SERVABLE, (
        f"Training-only column '{_TRAINING_ONLY_COLUMN}' must not appear in live_servable[]"
    )


@pytest.mark.django_db
def test_feature_set_row_live_servable_subset_of_columns():
    """DB round-trip: live_servable read back from Postgres is a subset of columns."""
    fs = FeatureSet.objects.create(
        version="v1",
        math_version=_MATH_VERSION,
        columns=_ALL_COLUMNS,
        live_servable=_LIVE_SERVABLE,
        hash=FeatureSet.compute_hash(_ALL_COLUMNS, _MATH_VERSION),
        notes="AC-29.3 split test",
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    assert set(obj.live_servable) <= set(obj.columns), (
        "live_servable is not a subset of columns after Postgres round-trip"
    )


@pytest.mark.django_db
def test_feature_set_row_training_only_column_excluded():
    """DB round-trip: designated training-only column is in columns but absent from live_servable."""
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


@pytest.mark.django_db
def test_live_servable_strict_subset_multiple_training_only():
    """Multiple training-only columns are all absent from live_servable[]."""
    training_only = [f"tape_close_b{i}" for i in range(8)]
    live = [c for c in _ALL_COLUMNS if c not in training_only]
    fs = FeatureSet.objects.create(
        version="v2",
        math_version=_MATH_VERSION,
        columns=_ALL_COLUMNS,
        live_servable=live,
        hash=FeatureSet.compute_hash(_ALL_COLUMNS, _MATH_VERSION),
    )
    obj = FeatureSet.objects.get(pk=fs.pk)
    live_set = set(obj.live_servable)
    for col in training_only:
        assert col not in live_set, f"training-only '{col}' must not be in live_servable"
    assert live_set <= set(obj.columns)


# ---------------------------------------------------------------------------
# Read-only Django admin: HTTP 200 for changelist + detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_set_admin_changelist_returns_200(client):
    """Admin changelist for FeatureSet returns HTTP 200 for an authenticated staff user."""
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
def test_feature_set_admin_detail_returns_200(client):
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
        notes="Admin detail test row",
    )
    url = reverse("admin:core_featureset_change", args=[fs.pk])
    response = client.get(url)
    assert response.status_code == 200
