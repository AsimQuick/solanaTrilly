# ---
# module: core.tests.test_json_safe_jsonfield_ac52
# sprint: sprint-2
# story: US-5 AC-5.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.encoders, core.models, json, math, decimal, datetime, django.db
# ---
"""AC-5.2 — JsonSafeEncoder applied at every JSONField write site.

Verifies that the RawEvent.payload JSONField uses JsonSafeEncoder and that a
round-trip through Postgres produces psycopg-safe, spec-valid JSON:

  - NaN / Inf / -Inf values in payload  → stored as JSON null  → retrieved as None
  - Decimal values                       → stored as JSON number → retrieved as float
  - datetime values                      → stored as ISO-8601 string
  - Raw JSON column text is parseable by json.loads (no non-finite literals)
"""
import json
import math
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from django.db import connection

from core.encoders import JsonSafeEncoder
from core.models import RawEvent


# ---------------------------------------------------------------------------
# Field introspection — encoder must be set at the field level
# ---------------------------------------------------------------------------


def test_rawevent_payload_field_uses_json_safe_encoder():
    """RawEvent.payload must declare encoder=JsonSafeEncoder."""
    field = RawEvent._meta.get_field("payload")
    assert field.encoder is JsonSafeEncoder, (
        "RawEvent.payload must set encoder=JsonSafeEncoder so that every JSONB "
        "write is routed through the canonical safe encoder (AC-5.2)."
    )


# ---------------------------------------------------------------------------
# Round-trip: NaN / Inf / Decimal / datetime through Postgres
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_nan_stored_as_null_and_retrieved_as_none():
    """NaN in payload must be stored as JSON null and retrieved as Python None."""
    obj = RawEvent.objects.create(payload={"value": float("nan")})
    obj.refresh_from_db()
    assert obj.payload["value"] is None


@pytest.mark.django_db
def test_positive_inf_stored_as_null():
    obj = RawEvent.objects.create(payload={"value": float("inf")})
    obj.refresh_from_db()
    assert obj.payload["value"] is None


@pytest.mark.django_db
def test_negative_inf_stored_as_null():
    obj = RawEvent.objects.create(payload={"value": float("-inf")})
    obj.refresh_from_db()
    assert obj.payload["value"] is None


@pytest.mark.django_db
def test_decimal_stored_and_retrieved_as_float():
    """Decimal in payload must be stored and retrieved as a JSON float."""
    obj = RawEvent.objects.create(payload={"price": Decimal("9.99")})
    obj.refresh_from_db()
    value = obj.payload["price"]
    assert isinstance(value, float)
    assert value == pytest.approx(9.99)


@pytest.mark.django_db
def test_datetime_stored_as_iso8601_string():
    """datetime in payload must be stored as an ISO-8601 string."""
    dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    obj = RawEvent.objects.create(payload={"ts": dt})
    obj.refresh_from_db()
    value = obj.payload["ts"]
    assert isinstance(value, str), "datetime must be serialised to a string"
    assert "2025-06-15" in value
    assert "12:00:00" in value


@pytest.mark.django_db
def test_mixed_payload_round_trip():
    """Full mixed payload (NaN, Inf, Decimal, datetime) round-trips correctly."""
    dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nan_val": float("nan"),
        "inf_val": float("inf"),
        "neg_inf_val": float("-inf"),
        "decimal_val": Decimal("3.14"),
        "dt_val": dt,
        "normal_str": "hello",
        "normal_int": 42,
        "normal_none": None,
    }
    obj = RawEvent.objects.create(payload=payload)
    obj.refresh_from_db()

    assert obj.payload["nan_val"] is None
    assert obj.payload["inf_val"] is None
    assert obj.payload["neg_inf_val"] is None
    assert isinstance(obj.payload["decimal_val"], float)
    assert obj.payload["decimal_val"] == pytest.approx(3.14)
    assert isinstance(obj.payload["dt_val"], str)
    assert obj.payload["normal_str"] == "hello"
    assert obj.payload["normal_int"] == 42
    assert obj.payload["normal_none"] is None


# ---------------------------------------------------------------------------
# Raw JSON in DB is psycopg-safe (json.loads must not raise)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_raw_json_column_is_parseable():
    """The raw JSON text stored in Postgres must be parseable by json.loads.

    Non-finite float literals (NaN, Infinity) are not valid JSON, so their
    presence would cause json.loads to raise.  This confirms the encoder has
    sanitised the value before it reached Postgres.
    """
    dt = datetime(2025, 3, 21, 8, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nan_val": float("nan"),
        "inf_val": float("inf"),
        "decimal_val": Decimal("2.718"),
        "dt_val": dt,
    }
    obj = RawEvent.objects.create(payload=payload)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload::text FROM core_rawevent WHERE id = %s", [obj.id]
        )
        raw_json = cursor.fetchone()[0]

    # This must not raise — if NaN/Inf leaked through, json.loads would error
    parsed = json.loads(raw_json)
    assert parsed["nan_val"] is None
    assert parsed["inf_val"] is None


@pytest.mark.django_db
def test_raw_json_contains_no_non_finite_literals():
    """The raw text in Postgres must not contain NaN, Infinity, or -Infinity."""
    payload = {
        "a": float("nan"),
        "b": float("inf"),
        "c": float("-inf"),
    }
    obj = RawEvent.objects.create(payload=payload)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload::text FROM core_rawevent WHERE id = %s", [obj.id]
        )
        raw_json = cursor.fetchone()[0]

    # Non-finite literals would make the JSON spec-invalid
    assert "NaN" not in raw_json
    assert "Infinity" not in raw_json
    assert "nan" not in raw_json


# ---------------------------------------------------------------------------
# Saving NaN must not raise (encoder handles it transparently)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_saving_nan_does_not_raise():
    """Creating a RawEvent with NaN in the payload must not raise any exception."""
    obj = RawEvent.objects.create(payload={"bad_float": float("nan")})
    assert obj.pk is not None


@pytest.mark.django_db
def test_saving_inf_does_not_raise():
    """Creating a RawEvent with +Inf in the payload must not raise any exception."""
    obj = RawEvent.objects.create(payload={"bad_float": float("inf")})
    assert obj.pk is not None


# ---------------------------------------------------------------------------
# Retrieved payload has no non-finite floats
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_retrieved_payload_has_no_non_finite_floats():
    """After retrieval from DB, no float value in the payload should be non-finite."""
    payload = {
        "a": float("nan"),
        "b": float("inf"),
        "c": float("-inf"),
        "d": 1.5,
        "e": 0.0,
    }
    obj = RawEvent.objects.create(payload=payload)
    obj.refresh_from_db()

    for key, value in obj.payload.items():
        if isinstance(value, float):
            assert math.isfinite(value), (
                f"Retrieved payload[{key!r}] = {value!r} is non-finite; "
                "encoder should have converted it to null."
            )


# ---------------------------------------------------------------------------
# Additional coverage: nested structures round-trip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_nested_nan_in_list_becomes_null():
    """NaN inside a list in the payload must also be stored as null."""
    obj = RawEvent.objects.create(payload={"values": [1.0, float("nan"), 3.0]})
    obj.refresh_from_db()
    assert obj.payload["values"][1] is None
    assert obj.payload["values"][0] == pytest.approx(1.0)
    assert obj.payload["values"][2] == pytest.approx(3.0)


@pytest.mark.django_db
def test_nested_decimal_in_dict_becomes_float():
    """Decimal inside a nested dict must be stored as a float."""
    obj = RawEvent.objects.create(
        payload={"market": {"price": Decimal("99.95"), "volume": Decimal("1000")}}
    )
    obj.refresh_from_db()
    assert isinstance(obj.payload["market"]["price"], float)
    assert obj.payload["market"]["price"] == pytest.approx(99.95)


@pytest.mark.django_db
def test_created_at_is_populated():
    """created_at must be auto-populated when a RawEvent is saved."""
    obj = RawEvent.objects.create(payload={"x": 1})
    assert obj.created_at is not None
