# ---
# module: core.tests.test_pipeline_config_ac91
# sprint: sprint-3
# story: US-9 AC-9.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.encoders, django.db
# ---
"""AC-9.1 — PipelineConfig model: schema, JSONField encoder, and DB round-trip.

Verifies:
  - Table name is 'pipeline_config'
  - All required columns exist with correct types
  - Each JSONField uses encoder=JsonSafeEncoder (US-5 guard stays green)
  - feature_set_id and model_id are nullable placeholder slots
  - A config row can be created and every section read back intact
  - makemigrations + migrate applied cleanly (exercised by @pytest.mark.django_db)
"""
import pytest

from core.encoders import JsonSafeEncoder
from core.models import PipelineConfig

# ---------------------------------------------------------------------------
# Schema / field introspection — no DB needed
# ---------------------------------------------------------------------------


def test_pipeline_config_db_table():
    """Meta.db_table must be 'pipeline_config' (PRD §8)."""
    assert PipelineConfig._meta.db_table == "pipeline_config"


def test_version_field_exists():
    field = PipelineConfig._meta.get_field("version")
    assert field is not None


def test_label_field_exists():
    field = PipelineConfig._meta.get_field("label")
    assert field is not None
    assert field.max_length == 255


def test_is_active_field_defaults_false():
    field = PipelineConfig._meta.get_field("is_active")
    assert field.default is False


def test_created_at_is_auto_now_add():
    field = PipelineConfig._meta.get_field("created_at")
    assert field.auto_now_add is True


def test_created_by_is_nullable_fk():
    field = PipelineConfig._meta.get_field("created_by")
    from django.db.models import ForeignKey

    assert isinstance(field, ForeignKey)
    assert field.null is True
    assert field.blank is True


def test_notes_field_is_blank_allowed():
    field = PipelineConfig._meta.get_field("notes")
    assert field.blank is True


def _assert_json_field_uses_encoder(field_name: str) -> None:
    """Helper: named JSONField must declare encoder=JsonSafeEncoder."""
    field = PipelineConfig._meta.get_field(field_name)
    assert field.encoder is JsonSafeEncoder, (
        f"PipelineConfig.{field_name} must set encoder=JsonSafeEncoder "
        f"so that every JSONB write is routed through the canonical safe encoder (H3/US-5)."
    )


def test_detection_field_uses_json_safe_encoder():
    _assert_json_field_uses_encoder("detection")


def test_tape_field_uses_json_safe_encoder():
    _assert_json_field_uses_encoder("tape")


def test_scoring_field_uses_json_safe_encoder():
    _assert_json_field_uses_encoder("scoring")


def test_outcome_field_uses_json_safe_encoder():
    _assert_json_field_uses_encoder("outcome")


def test_trading_field_uses_json_safe_encoder():
    _assert_json_field_uses_encoder("trading")


def test_feature_set_id_is_nullable():
    """feature_set_id is a nullable placeholder slot (P5 will convert to FK)."""
    field = PipelineConfig._meta.get_field("feature_set_id")
    assert field.null is True
    assert field.blank is True


def test_model_id_is_nullable():
    """model_id is a nullable placeholder slot (P7 will convert to FK)."""
    field = PipelineConfig._meta.get_field("model_id")
    assert field.null is True
    assert field.blank is True


# ---------------------------------------------------------------------------
# DB round-trip — creates a row and reads every section back
# ---------------------------------------------------------------------------

DETECTION_PAYLOAD = {
    "graduation_program": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "poll_interval_s": 5,
    "max_tokens_per_batch": 100,
}

TAPE_PAYLOAD = {
    "idle_kill_ttl_s": 3600,
    "capture_buffer_s": 10,
    "birdeye_interval_s": 15,
}

SCORING_PAYLOAD = {
    "window_s": 120,
    "score_at_elapsed_s": 90,
    "capture_buffer_s": 5,
    "gate": "adaptive_topk",
}

OUTCOME_PAYLOAD = {
    "window_s": 300,
    "success_threshold": 1.5,
    "failure_threshold": 0.7,
}

TRADING_PAYLOAD = {
    "gate": "adaptive_topk",
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}


@pytest.mark.django_db
def test_create_pipeline_config_and_read_every_section():
    """Create a PipelineConfig row and verify every section round-trips intact."""
    obj = PipelineConfig.objects.create(
        version=1,
        label="v1-baseline",
        is_active=False,
        notes="Initial config snapshot for AC-9.1 test",
        detection=DETECTION_PAYLOAD,
        tape=TAPE_PAYLOAD,
        scoring=SCORING_PAYLOAD,
        outcome=OUTCOME_PAYLOAD,
        trading=TRADING_PAYLOAD,
        feature_set_id=None,
        model_id=None,
    )

    obj.refresh_from_db()

    assert obj.pk is not None
    assert obj.version == 1
    assert obj.label == "v1-baseline"
    assert obj.is_active is False
    assert obj.notes == "Initial config snapshot for AC-9.1 test"
    assert obj.created_at is not None
    assert obj.created_by is None
    assert obj.feature_set_id is None
    assert obj.model_id is None

    # Every tunable section reads back intact
    assert obj.detection == DETECTION_PAYLOAD
    assert obj.tape == TAPE_PAYLOAD
    assert obj.scoring == SCORING_PAYLOAD
    assert obj.outcome == OUTCOME_PAYLOAD
    assert obj.trading == TRADING_PAYLOAD


@pytest.mark.django_db
def test_pipeline_config_sections_are_independently_readable():
    """Each section can be accessed independently after a DB round-trip."""
    obj = PipelineConfig.objects.create(
        version=2,
        label="v2-sections-test",
        detection={"mode": "firehose"},
        tape={"idle_kill_ttl_s": 1800},
        scoring={"gate": "adaptive_topk"},
        outcome={"window_s": 600},
        trading={"position_size_sol": 0.05},
    )
    obj.refresh_from_db()

    assert obj.detection["mode"] == "firehose"
    assert obj.tape["idle_kill_ttl_s"] == 1800
    assert obj.scoring["gate"] == "adaptive_topk"
    assert obj.outcome["window_s"] == 600
    assert obj.trading["position_size_sol"] == pytest.approx(0.05)


@pytest.mark.django_db
def test_pipeline_config_default_sections_are_empty_dicts():
    """Sections default to {} so a minimal create() doesn't error."""
    obj = PipelineConfig.objects.create(version=3, label="minimal")
    obj.refresh_from_db()

    assert obj.detection == {}
    assert obj.tape == {}
    assert obj.scoring == {}
    assert obj.outcome == {}
    assert obj.trading == {}


@pytest.mark.django_db
def test_pipeline_config_json_safe_encoder_guard():
    """Non-finite floats in sections are stored as null (US-5 guard stays green)."""
    import math

    obj = PipelineConfig.objects.create(
        version=4,
        label="nan-guard",
        scoring={"bad_score": float("nan"), "good_score": 0.95},
    )
    obj.refresh_from_db()

    assert obj.scoring["bad_score"] is None
    assert not math.isnan(0.95)
    assert obj.scoring["good_score"] == pytest.approx(0.95)
