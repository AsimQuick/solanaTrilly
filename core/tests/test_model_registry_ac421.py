# ---
# module: core.tests.test_model_registry_ac421
# sprint: sprint-9
# story: US-42 AC-42.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.models, core.resolver, core.encoders, django.db, pytest
# ---
"""AC-42.1 — ModelRegistry persistence target: schema, required fields, and
at-most-one-active-model discipline.

The ModelRegistry records a promoted model artifact with all required fields:
  - kind (e.g. 'lightgbm_regression_blend')
  - feature_list (bound ordered feature names)
  - labels_seeds_manifest (3 labels x 5 seeds)
  - blend_transform_descriptor (rank-average recipe params)
  - artifact_content_hashes (content hash(es))
  - model_version / feature_set_version
  - is_active (at-most-one-active discipline via activate_model())

MODEL-AGNOSTIC: v3.2 is used as a representative fixture; no field values are
hardcoded as invariants of the registry schema itself.

H1 ImportError trap — module-level imports of ModelRegistry, activate_model,
and get_active_model must succeed; deletion/rename fails pytest collection.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_model_registry_importable
  test_activate_model_importable
  test_get_active_model_importable

Schema / field introspection — no DB needed:
  test_db_table_name
  test_kind_field_exists
  test_feature_list_field_uses_json_safe_encoder
  test_labels_seeds_manifest_field_uses_json_safe_encoder
  test_blend_transform_descriptor_field_uses_json_safe_encoder
  test_artifact_content_hashes_field_uses_json_safe_encoder
  test_model_version_field_exists
  test_feature_set_version_field_exists
  test_is_active_defaults_false
  test_created_at_is_auto_now_add
  test_notes_field_blank_allowed
  test_history_field_exists

DB-backed — BLEND artifact write:
  test_blend_artifact_records_all_required_fields
  test_blend_artifact_kind_stored
  test_blend_artifact_feature_list_stored_in_order
  test_blend_artifact_labels_seeds_manifest_stored
  test_blend_artifact_blend_transform_descriptor_stored
  test_blend_artifact_content_hashes_stored
  test_blend_artifact_model_version_stored
  test_blend_artifact_feature_set_version_stored

DB-backed — at-most-one-active-model discipline:
  test_new_registry_row_is_inactive_by_default
  test_activate_model_sets_is_active_true
  test_activate_model_deactivates_previous
  test_at_most_one_active_model_at_all_times
  test_get_active_model_returns_none_when_none_active
  test_get_active_model_returns_active_row
  test_activate_model_raises_on_missing_id
"""
import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — failing to import any of these means something was
# deleted or renamed; pytest collection itself fails before any test runs.
# ---------------------------------------------------------------------------
from core.models import ModelRegistry
from core.resolver import activate_model, get_active_model

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FEATURE_LIST = [
    "pre_buy_count",
    "pre_sell_count",
    "pre_net_buyers",
    "pre_buy_vol_sol",
    "pre_sell_vol_sol",
    "pre_vol_sol",
    "pre_buy_frac",
    "pre_vol_usd",
    "pre_price_open",
    "pre_price_close",
    "pre_price_high",
    "pre_price_low",
    "pre_price_range",
    "pre_price_return",
    "pre_unique_buyers",
    "pre_unique_sellers",
    "pre_unique_traders",
    "pre_vwap",
    "pre_trade_size_mean",
    "pre_trade_size_std",
]

_LABELS_SEEDS_MANIFEST = {
    "labels": ["ctrl", "oracle", "liq"],
    "seeds": [0, 1, 2, 3, 4],
    "boosters": {
        "ctrl": [
            "boosters/ctrl_s0.txt",
            "boosters/ctrl_s1.txt",
            "boosters/ctrl_s2.txt",
            "boosters/ctrl_s3.txt",
            "boosters/ctrl_s4.txt",
        ],
        "oracle": [
            "boosters/oracle_s0.txt",
            "boosters/oracle_s1.txt",
            "boosters/oracle_s2.txt",
            "boosters/oracle_s3.txt",
            "boosters/oracle_s4.txt",
        ],
        "liq": [
            "boosters/liq_s0.txt",
            "boosters/liq_s1.txt",
            "boosters/liq_s2.txt",
            "boosters/liq_s3.txt",
            "boosters/liq_s4.txt",
        ],
    },
}

_BLEND_TRANSFORM_DESCRIPTOR = {
    "method": "rank_average",
    "labels": ["ctrl", "oracle", "liq"],
    "per_label": "seed_average_then_percentile_rank",
    "blend": "mean_of_3_percentile_ranks",
    "selection": "top_k_by_blend",
}

_ARTIFACT_CONTENT_HASHES = {
    "boosters/ctrl_s0.txt": "abc001",
    "boosters/ctrl_s1.txt": "abc002",
    "boosters/oracle_s0.txt": "abc003",
    "meta.json": "abc004",
}


def _make_blend_artifact(**kwargs):
    """Return a ModelRegistry dict with all required BLEND fields."""
    defaults = {
        "kind": "lightgbm_regression_blend",
        "feature_list": _FEATURE_LIST,
        "labels_seeds_manifest": _LABELS_SEEDS_MANIFEST,
        "blend_transform_descriptor": _BLEND_TRANSFORM_DESCRIPTOR,
        "artifact_content_hashes": _ARTIFACT_CONTENT_HASHES,
        "model_version": "trilly_pregrad_v3_2",
        "feature_set_version": "v3.2",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Wiring guard (H1 ImportError trap) — not real tests, but import presence
# ---------------------------------------------------------------------------


def test_model_registry_importable():
    """ModelRegistry must be importable from core.models; removal fails collection."""
    assert ModelRegistry is not None


def test_activate_model_importable():
    """activate_model must be importable from core.resolver; removal fails collection."""
    assert activate_model is not None


def test_get_active_model_importable():
    """get_active_model must be importable from core.resolver; removal fails collection."""
    assert get_active_model is not None


# ---------------------------------------------------------------------------
# Schema / field introspection — no DB needed
# ---------------------------------------------------------------------------


def test_db_table_name():
    assert ModelRegistry._meta.db_table == "model_registry"


def test_kind_field_exists():
    field = ModelRegistry._meta.get_field("kind")
    assert field.max_length == 128


def test_feature_list_field_uses_json_safe_encoder():
    from core.encoders import JsonSafeEncoder

    field = ModelRegistry._meta.get_field("feature_list")
    assert field.encoder is JsonSafeEncoder


def test_labels_seeds_manifest_field_uses_json_safe_encoder():
    from core.encoders import JsonSafeEncoder

    field = ModelRegistry._meta.get_field("labels_seeds_manifest")
    assert field.encoder is JsonSafeEncoder


def test_blend_transform_descriptor_field_uses_json_safe_encoder():
    from core.encoders import JsonSafeEncoder

    field = ModelRegistry._meta.get_field("blend_transform_descriptor")
    assert field.encoder is JsonSafeEncoder


def test_artifact_content_hashes_field_uses_json_safe_encoder():
    from core.encoders import JsonSafeEncoder

    field = ModelRegistry._meta.get_field("artifact_content_hashes")
    assert field.encoder is JsonSafeEncoder


def test_model_version_field_exists():
    field = ModelRegistry._meta.get_field("model_version")
    assert field.max_length == 128


def test_feature_set_version_field_exists():
    field = ModelRegistry._meta.get_field("feature_set_version")
    assert field.max_length == 128


def test_is_active_defaults_false():
    field = ModelRegistry._meta.get_field("is_active")
    assert field.default is False


def test_created_at_is_auto_now_add():
    field = ModelRegistry._meta.get_field("created_at")
    assert field.auto_now_add is True


def test_notes_field_blank_allowed():
    field = ModelRegistry._meta.get_field("notes")
    assert field.blank is True


def test_history_field_exists():
    """HistoricalRecords must be attached (audit trail per P1 conventions)."""
    assert hasattr(ModelRegistry, "history")


# ---------------------------------------------------------------------------
# DB-backed — BLEND artifact write with all required fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_blend_artifact_records_all_required_fields():
    """A BLEND artifact can be created and all required fields round-trip intact."""
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    loaded = ModelRegistry.objects.get(pk=entry.pk)
    assert loaded.kind == "lightgbm_regression_blend"
    assert loaded.feature_list == _FEATURE_LIST
    assert loaded.labels_seeds_manifest == _LABELS_SEEDS_MANIFEST
    assert loaded.blend_transform_descriptor == _BLEND_TRANSFORM_DESCRIPTOR
    assert loaded.artifact_content_hashes == _ARTIFACT_CONTENT_HASHES
    assert loaded.model_version == "trilly_pregrad_v3_2"
    assert loaded.feature_set_version == "v3.2"


@pytest.mark.django_db
def test_blend_artifact_kind_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact(kind="lightgbm_regression_blend"))
    assert ModelRegistry.objects.get(pk=entry.pk).kind == "lightgbm_regression_blend"


@pytest.mark.django_db
def test_blend_artifact_feature_list_stored_in_order():
    """feature_list round-trips with order preserved (binding order per PRD §7.4)."""
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    loaded_list = ModelRegistry.objects.get(pk=entry.pk).feature_list
    assert loaded_list == _FEATURE_LIST
    assert loaded_list[0] == "pre_buy_count"
    assert loaded_list[-1] == "pre_trade_size_std"


@pytest.mark.django_db
def test_blend_artifact_labels_seeds_manifest_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    manifest = ModelRegistry.objects.get(pk=entry.pk).labels_seeds_manifest
    assert manifest["labels"] == ["ctrl", "oracle", "liq"]
    assert manifest["seeds"] == [0, 1, 2, 3, 4]
    assert len(manifest["boosters"]["ctrl"]) == 5


@pytest.mark.django_db
def test_blend_artifact_blend_transform_descriptor_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    descriptor = ModelRegistry.objects.get(pk=entry.pk).blend_transform_descriptor
    assert descriptor["method"] == "rank_average"
    assert descriptor["blend"] == "mean_of_3_percentile_ranks"


@pytest.mark.django_db
def test_blend_artifact_content_hashes_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    hashes = ModelRegistry.objects.get(pk=entry.pk).artifact_content_hashes
    assert "meta.json" in hashes


@pytest.mark.django_db
def test_blend_artifact_model_version_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact(model_version="trilly_pregrad_v3_2"))
    assert ModelRegistry.objects.get(pk=entry.pk).model_version == "trilly_pregrad_v3_2"


@pytest.mark.django_db
def test_blend_artifact_feature_set_version_stored():
    entry = ModelRegistry.objects.create(**_make_blend_artifact(feature_set_version="v3.2"))
    assert ModelRegistry.objects.get(pk=entry.pk).feature_set_version == "v3.2"


# ---------------------------------------------------------------------------
# DB-backed — at-most-one-active-model discipline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_registry_row_is_inactive_by_default():
    """A freshly created ModelRegistry row has is_active=False."""
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    assert entry.is_active is False


@pytest.mark.django_db
def test_activate_model_sets_is_active_true():
    """activate_model(id) sets the target row's is_active to True."""
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    activated = activate_model(entry.pk)
    assert activated.is_active is True
    assert ModelRegistry.objects.get(pk=entry.pk).is_active is True


@pytest.mark.django_db
def test_activate_model_deactivates_previous():
    """activate_model() deactivates the previously active row."""
    first = ModelRegistry.objects.create(**_make_blend_artifact(model_version="v1"))
    activate_model(first.pk)
    assert ModelRegistry.objects.get(pk=first.pk).is_active is True

    second = ModelRegistry.objects.create(**_make_blend_artifact(model_version="v2"))
    activate_model(second.pk)

    assert ModelRegistry.objects.get(pk=first.pk).is_active is False
    assert ModelRegistry.objects.get(pk=second.pk).is_active is True


@pytest.mark.django_db
def test_at_most_one_active_model_at_all_times():
    """After activate_model(), exactly one row has is_active=True."""
    rows = [
        ModelRegistry.objects.create(**_make_blend_artifact(model_version=f"v{i}"))
        for i in range(3)
    ]
    for row in rows:
        activate_model(row.pk)
        active_count = ModelRegistry.objects.filter(is_active=True).count()
        assert active_count == 1, f"Expected 1 active model, got {active_count}"


@pytest.mark.django_db
def test_get_active_model_returns_none_when_none_active():
    """get_active_model() returns None when no row has is_active=True."""
    ModelRegistry.objects.create(**_make_blend_artifact())
    assert get_active_model() is None


@pytest.mark.django_db
def test_get_active_model_returns_active_row():
    """get_active_model() returns the row activated via activate_model()."""
    entry = ModelRegistry.objects.create(**_make_blend_artifact())
    activate_model(entry.pk)
    active = get_active_model()
    assert active is not None
    assert active.pk == entry.pk
    assert active.kind == "lightgbm_regression_blend"


@pytest.mark.django_db
def test_activate_model_raises_on_missing_id():
    """activate_model(999999) raises ModelRegistry.DoesNotExist for a missing row."""
    with pytest.raises(ModelRegistry.DoesNotExist):
        activate_model(999999)
