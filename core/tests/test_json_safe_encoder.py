# ---
# module: core.tests.test_json_safe_encoder
# sprint: sprint-2
# story: US-5 AC-5.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.encoders, json, math, decimal, datetime
# ---
"""AC-5.1 — JsonSafeEncoder unit tests.

Verifies that JsonSafeEncoder is the single, shared implementation for all
JSONB write sites and correctly produces psycopg-safe, spec-valid JSON:

  - Non-finite floats (NaN, Inf, -Inf) → JSON null
  - Decimal                             → float
  - datetime                            → ISO-8601 string (via DjangoJSONEncoder)
  - Standard Python types               → pass through unchanged
  - Nested structures                   → all elements sanitized recursively
  - Encoder is a DjangoJSONEncoder subclass (usable as models.JSONField encoder)
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.encoders import JsonSafeEncoder


def _encode(obj):
    """Encode obj with JsonSafeEncoder and round-trip through json.loads."""
    return json.loads(json.dumps(obj, cls=JsonSafeEncoder))


# ---------------------------------------------------------------------------
# Non-finite float → null
# ---------------------------------------------------------------------------


def test_nan_becomes_null():
    assert _encode(float("nan")) is None


def test_positive_inf_becomes_null():
    assert _encode(float("inf")) is None


def test_negative_inf_becomes_null():
    assert _encode(float("-inf")) is None


def test_finite_float_unchanged():
    assert _encode(1.5) == pytest.approx(1.5)


def test_negative_finite_float_unchanged():
    assert _encode(-3.14) == pytest.approx(-3.14)


def test_zero_float_unchanged():
    assert _encode(0.0) == pytest.approx(0.0)


def test_nan_in_dict_value_becomes_null():
    result = _encode({"x": float("nan"), "y": 2.0})
    assert result["x"] is None
    assert result["y"] == pytest.approx(2.0)


def test_inf_in_list_becomes_null():
    result = _encode([float("nan"), float("inf"), float("-inf"), 1.0])
    assert result[0] is None
    assert result[1] is None
    assert result[2] is None
    assert result[3] == pytest.approx(1.0)


def test_nan_in_nested_dict_becomes_null():
    result = _encode({"outer": {"inner": float("nan")}})
    assert result["outer"]["inner"] is None


def test_nan_in_list_of_dicts_becomes_null():
    result = _encode([{"v": float("nan")}, {"v": 1.0}])
    assert result[0]["v"] is None
    assert result[1]["v"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Decimal → float
# ---------------------------------------------------------------------------


def test_decimal_becomes_float():
    result = _encode(Decimal("1.5"))
    assert isinstance(result, float)
    assert result == pytest.approx(1.5)


def test_decimal_zero_becomes_float():
    result = _encode(Decimal("0"))
    assert result == pytest.approx(0.0)


def test_decimal_negative_becomes_float():
    result = _encode(Decimal("-9.99"))
    assert result == pytest.approx(-9.99)


def test_decimal_infinity_becomes_null():
    """Decimal('Infinity') converts to float('inf') which must map to null."""
    result = _encode(Decimal("Infinity"))
    assert result is None


def test_decimal_negative_infinity_becomes_null():
    result = _encode(Decimal("-Infinity"))
    assert result is None


def test_decimal_in_dict_becomes_float():
    result = _encode({"price": Decimal("9.99"), "qty": 3})
    assert isinstance(result["price"], float)
    assert result["price"] == pytest.approx(9.99)


# ---------------------------------------------------------------------------
# datetime → ISO-8601 string (inherited from DjangoJSONEncoder)
# ---------------------------------------------------------------------------


def test_datetime_utc_becomes_iso8601_string():
    dt = datetime(2025, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
    result = _encode(dt)
    assert isinstance(result, str)
    assert "2025-01-15" in result
    assert "12:30:45" in result


def test_datetime_in_dict_becomes_string():
    dt = datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    result = _encode({"ts": dt})
    assert isinstance(result["ts"], str)


def test_encoded_datetime_is_parseable():
    """The ISO-8601 string produced must be parseable back to a datetime."""
    dt = datetime(2025, 3, 21, 8, 0, 0, tzinfo=timezone.utc)
    result = _encode(dt)
    # Strip 'Z' suffix if present (ISO-8601 with UTC indicator)
    normalised = result.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    assert parsed.replace(tzinfo=None) == dt.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Standard Python types pass through unchanged
# ---------------------------------------------------------------------------


def test_none_passthrough():
    assert _encode(None) is None


def test_string_passthrough():
    assert _encode("hello") == "hello"


def test_integer_passthrough():
    assert _encode(42) == 42


def test_plain_dict_passthrough():
    assert _encode({"a": 1, "b": "text"}) == {"a": 1, "b": "text"}


def test_plain_list_passthrough():
    assert _encode([1, 2, 3]) == [1, 2, 3]


def test_bool_passthrough():
    assert _encode(True) is True
    assert _encode(False) is False


# ---------------------------------------------------------------------------
# Output is always spec-valid JSON (psycopg-safe)
# ---------------------------------------------------------------------------


def test_mixed_payload_produces_valid_json():
    """A payload mixing NaN, Decimal, datetime, and standard types must encode
    to spec-valid JSON that json.loads can parse without error."""
    payload = {
        "nan_val": float("nan"),
        "inf_val": float("inf"),
        "decimal_val": Decimal("3.14"),
        "dt_val": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "normal": [1, "two", None, True],
    }
    raw = json.dumps(payload, cls=JsonSafeEncoder)
    parsed = json.loads(raw)  # raises if not valid JSON
    assert parsed["nan_val"] is None
    assert parsed["inf_val"] is None
    assert isinstance(parsed["decimal_val"], float)
    assert isinstance(parsed["dt_val"], str)


# ---------------------------------------------------------------------------
# Encoder is a valid Django JSONField encoder class
# ---------------------------------------------------------------------------


def test_is_django_json_encoder_subclass():
    """JsonSafeEncoder must subclass DjangoJSONEncoder to be usable as JSONField encoder."""
    from django.core.serializers.json import DjangoJSONEncoder

    assert issubclass(JsonSafeEncoder, DjangoJSONEncoder), (
        "JsonSafeEncoder must subclass DjangoJSONEncoder so it can be passed as "
        "encoder= to models.JSONField (AC-5.1)."
    )


def test_encoder_instantiates_and_encodes():
    """Encoder must be directly instantiatable and its encode() method must work."""
    encoder = JsonSafeEncoder()
    result = encoder.encode({"x": float("nan"), "d": Decimal("1.5")})
    parsed = json.loads(result)
    assert parsed["x"] is None
    assert isinstance(parsed["d"], float)
