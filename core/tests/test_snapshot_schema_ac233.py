# ---
# module: core.tests.test_snapshot_schema_ac233
# sprint: sprint-6
# story: US-23 AC-23.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_schema, core.encoders, json, math, decimal, pytest
# ---
"""AC-23.3 — Score-time snapshot schema tests.

Verifies two invariants:

1. Field mapping — SnapshotSchema.from_raw() correctly maps all seven fields
   from a raw Birdeye snapshot payload:
     {holder_distribution, mint_authority, freeze_authority, lp_burned (bool),
      liquidity, tvl, depth}
   liquidity / tvl / depth are first-class (§1.1): KeyError is raised when any
   is absent so the schema never silently defaults them.

2. JsonSafeEncoder guard (H3/US-5) — to_json_safe() serialises the schema
   through JsonSafeEncoder: NaN/Inf → null, Decimal → float.  The output is
   spec-valid JSON (no NaN/Inf literals), keeping the guard green.
"""
import json
from decimal import Decimal

import pytest

from core.snapshot_schema import SnapshotSchema

# ---------------------------------------------------------------------------
# Representative raw Birdeye snapshot payload (schema-faithful fixture)
# ---------------------------------------------------------------------------

RAW_BIRDEYE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.45, "top20": 0.60, "count": 1234},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 5000.0,
    "tvl": 4800.0,
    "depth": {"bid": 100.0, "ask": 100.0},
}


# ---------------------------------------------------------------------------
# 1a. from_raw() — field mapping
# ---------------------------------------------------------------------------


def test_from_raw_returns_snapshot_schema_instance():
    """from_raw() constructs a SnapshotSchema from a valid raw payload."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert isinstance(schema, SnapshotSchema)


def test_from_raw_maps_holder_distribution():
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.holder_distribution == RAW_BIRDEYE_PAYLOAD["holder_distribution"]


def test_from_raw_maps_mint_authority_as_none():
    """mint_authority is None when the authority has been renounced."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.mint_authority is None


def test_from_raw_maps_mint_authority_when_present():
    """mint_authority is the address string when set."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "mint_authority": "MintAuth111111111111111111111111111111111111"}
    schema = SnapshotSchema.from_raw(raw)
    assert schema.mint_authority == "MintAuth111111111111111111111111111111111111"


def test_from_raw_maps_freeze_authority_as_none():
    """freeze_authority is None when the authority has been renounced."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.freeze_authority is None


def test_from_raw_maps_freeze_authority_when_present():
    """freeze_authority is the address string when set."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "freeze_authority": "FreezeAuth1111111111111111111111111111111111"}
    schema = SnapshotSchema.from_raw(raw)
    assert schema.freeze_authority == "FreezeAuth1111111111111111111111111111111111"


def test_from_raw_maps_lp_burned_true():
    """lp_burned is True when the raw value is truthy."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.lp_burned is True


def test_from_raw_lp_burned_is_bool_type():
    """lp_burned is always a Python bool — raw int 1 is cast to True."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "lp_burned": 1}
    schema = SnapshotSchema.from_raw(raw)
    assert schema.lp_burned is True
    assert isinstance(schema.lp_burned, bool)


def test_from_raw_lp_burned_false():
    """lp_burned is False when the raw value is falsy."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "lp_burned": False}
    schema = SnapshotSchema.from_raw(raw)
    assert schema.lp_burned is False


def test_from_raw_maps_liquidity():
    """liquidity is a first-class field (§1.1) — extracted verbatim."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.liquidity == 5000.0


def test_from_raw_maps_tvl():
    """tvl is a first-class field (§1.1) — extracted verbatim."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.tvl == 4800.0


def test_from_raw_maps_depth():
    """depth is a first-class field (§1.1) — extracted verbatim."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    assert schema.depth == {"bid": 100.0, "ask": 100.0}


# ---------------------------------------------------------------------------
# 1b. liquidity / tvl / depth are first-class — never assumed (§1.1)
# ---------------------------------------------------------------------------


def test_missing_liquidity_raises_key_error():
    """from_raw() raises KeyError when liquidity is absent (§1.1 — never assumed)."""
    raw = {k: v for k, v in RAW_BIRDEYE_PAYLOAD.items() if k != "liquidity"}
    with pytest.raises(KeyError):
        SnapshotSchema.from_raw(raw)


def test_missing_tvl_raises_key_error():
    """from_raw() raises KeyError when tvl is absent (§1.1 — never assumed)."""
    raw = {k: v for k, v in RAW_BIRDEYE_PAYLOAD.items() if k != "tvl"}
    with pytest.raises(KeyError):
        SnapshotSchema.from_raw(raw)


def test_missing_depth_raises_key_error():
    """from_raw() raises KeyError when depth is absent (§1.1 — never assumed)."""
    raw = {k: v for k, v in RAW_BIRDEYE_PAYLOAD.items() if k != "depth"}
    with pytest.raises(KeyError):
        SnapshotSchema.from_raw(raw)


# ---------------------------------------------------------------------------
# 2. to_json_safe() — JsonSafeEncoder guard (H3/US-5)
# ---------------------------------------------------------------------------


def test_to_json_safe_returns_valid_json_string():
    """to_json_safe() returns a parseable JSON string."""
    schema = SnapshotSchema.from_raw(RAW_BIRDEYE_PAYLOAD)
    result = schema.to_json_safe()
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_to_json_safe_nan_in_holder_distribution_becomes_null():
    """NaN inside holder_distribution is encoded as null via JsonSafeEncoder."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "holder_distribution": {"ratio": float("nan")}}
    schema = SnapshotSchema.from_raw(raw)
    parsed = json.loads(schema.to_json_safe())
    assert parsed["holder_distribution"]["ratio"] is None


def test_to_json_safe_inf_liquidity_becomes_null():
    """Infinite liquidity is encoded as null via JsonSafeEncoder (H3/US-5)."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "liquidity": float("inf")}
    schema = SnapshotSchema.from_raw(raw)
    parsed = json.loads(schema.to_json_safe())
    assert parsed["liquidity"] is None


def test_to_json_safe_neg_inf_tvl_becomes_null():
    """-Inf tvl is encoded as null via JsonSafeEncoder (H3/US-5)."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "tvl": float("-inf")}
    schema = SnapshotSchema.from_raw(raw)
    parsed = json.loads(schema.to_json_safe())
    assert parsed["tvl"] is None


def test_to_json_safe_decimal_in_depth_becomes_float():
    """Decimal value in depth is encoded as float via JsonSafeEncoder."""
    raw = {**RAW_BIRDEYE_PAYLOAD, "depth": {"bid": Decimal("99.99"), "ask": Decimal("100.01")}}
    schema = SnapshotSchema.from_raw(raw)
    parsed = json.loads(schema.to_json_safe())
    assert isinstance(parsed["depth"]["bid"], float)
    assert isinstance(parsed["depth"]["ask"], float)


def test_to_json_safe_output_contains_no_nan_inf_literals():
    """JSON output is spec-valid — no NaN or Inf literals that break json.loads."""
    raw = {
        **RAW_BIRDEYE_PAYLOAD,
        "liquidity": float("nan"),
        "tvl": float("-inf"),
        "holder_distribution": {"top10": float("inf")},
    }
    schema = SnapshotSchema.from_raw(raw)
    result = schema.to_json_safe()
    # spec-valid: json.loads must not raise
    parsed = json.loads(result)
    assert parsed["liquidity"] is None
    assert parsed["tvl"] is None
    assert parsed["holder_distribution"]["top10"] is None
    # no NaN in raw string
    assert "NaN" not in result
    assert "Infinity" not in result
