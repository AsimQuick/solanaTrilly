# ---
# module: core.tests.test_token_model_ac141
# sprint: sprint-4
# story: US-14 AC-14.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.encoders, django.db, pytest
# ---
"""AC-14.1 — Token model round-trip test.

Verifies that the Token model (table 'tokens') can create a row with all
required columns and read every column back from real Postgres.  Also verifies
field-level invariants: JSONField uses JsonSafeEncoder so the H3/US-5 guard
stays green, mint is the primary key, and graduated_block_time is an integer.
"""
from datetime import datetime, timezone

import pytest

from core.encoders import JsonSafeEncoder
from core.models import Token

# ---------------------------------------------------------------------------
# Field introspection — schema invariants
# ---------------------------------------------------------------------------


def test_token_table_name():
    assert Token._meta.db_table == "tokens"


def test_mint_is_primary_key():
    field = Token._meta.get_field("mint")
    assert field.primary_key is True


def test_raw_graduation_uses_json_safe_encoder():
    """raw_graduation must declare encoder=JsonSafeEncoder (H3/US-5 guard)."""
    field = Token._meta.get_field("raw_graduation")
    assert field.encoder is JsonSafeEncoder


def test_graduated_block_time_is_integer_field():
    from django.db import models as dj_models

    field = Token._meta.get_field("graduated_block_time")
    assert isinstance(field, dj_models.IntegerField)


def test_graduated_at_is_datetimefield():
    from django.db import models as dj_models

    field = Token._meta.get_field("graduated_at")
    assert isinstance(field, dj_models.DateTimeField)


def test_status_has_choices():
    field = Token._meta.get_field("status")
    assert field.choices, "status field must declare choices"


def test_status_default_is_detected():
    field = Token._meta.get_field("status")
    assert field.default == Token.STATUS_DETECTED


# ---------------------------------------------------------------------------
# Round-trip: create a row and read every column back from Postgres
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_token_row_roundtrip_all_columns():
    """Create a Token row and verify every column survives a Postgres round-trip."""
    t0 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    raw = {"address": "Abc123", "graduated": True, "progress_percent": 100.0}

    Token.objects.create(
        mint="Abc123MintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        pool_address="PoolAddrYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
        graduated_at=t0,
        graduated_block_time=334_000_000,
        dex_source="birdeye_meme",
        raw_graduation=raw,
        status=Token.STATUS_DETECTED,
    )

    obj = Token.objects.get(pk="Abc123MintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")

    assert obj.mint == "Abc123MintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    assert obj.pool_address == "PoolAddrYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY"
    assert obj.graduated_at == t0
    assert obj.graduated_block_time == 334_000_000
    assert obj.dex_source == "birdeye_meme"
    assert obj.raw_graduation == raw
    assert obj.status == Token.STATUS_DETECTED


@pytest.mark.django_db
def test_token_nan_in_raw_graduation_stored_as_null():
    """NaN in raw_graduation must be stored as JSON null (H3/US-5 guard)."""
    t0 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    Token.objects.create(
        mint="NanMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        pool_address="PoolNanYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
        graduated_at=t0,
        graduated_block_time=1,
        dex_source="birdeye_meme",
        raw_graduation={"bad_float": float("nan")},
        status=Token.STATUS_DETECTED,
    )
    obj = Token.objects.get(pk="NanMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
    assert obj.raw_graduation["bad_float"] is None


@pytest.mark.django_db
def test_token_status_choices_are_all_valid_values():
    """Every choice value must be storeable and readable back via Postgres."""
    t0 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    for i, (value, _label) in enumerate(Token.STATUS_CHOICES):
        mint = f"StTst{i:04d}" + "X" * 55  # exactly 64 chars
        Token.objects.create(
            mint=mint,
            pool_address="Pool" + "Y" * 60,
            graduated_at=t0,
            graduated_block_time=i,
            dex_source="birdeye_meme",
            raw_graduation={},
            status=value,
        )
        obj = Token.objects.get(pk=mint)
        assert obj.status == value
