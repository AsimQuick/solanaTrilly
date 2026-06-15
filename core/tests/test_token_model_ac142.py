# ---
# module: core.tests.test_token_model_ac142
# sprint: sprint-4
# story: US-14 AC-14.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, django.db, pytest
# ---
"""AC-14.2 — VARCHAR-width discipline and t0 field round-trip."""
from datetime import datetime, timezone

import pytest

from core.models import Token


def test_status_choices_non_empty():
    assert len(Token.STATUS_CHOICES) > 0


def test_each_status_choice_fits_within_max_length():
    field = Token._meta.get_field("status")
    for db_value, _label in Token.STATUS_CHOICES:
        assert len(db_value) <= field.max_length, (
            f"Choice '{db_value}' (len={len(db_value)}) exceeds "
            f"status max_length={field.max_length}"
        )


def test_status_choices_defined_in_field_choices_parameter():
    field = Token._meta.get_field("status")
    assert list(field.choices) == Token.STATUS_CHOICES


@pytest.mark.django_db
def test_t0_fields_round_trip():
    t0 = datetime(2026, 6, 15, 9, 30, 0, tzinfo=timezone.utc)
    block_time = 335_123_456

    Token.objects.create(
        mint="AC142MintRoundTripXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        pool_address="AC142PoolRoundTripYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
        graduated_at=t0,
        graduated_block_time=block_time,
        dex_source="birdeye_meme",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    obj = Token.objects.get(pk="AC142MintRoundTripXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")

    assert obj.graduated_at == t0
    assert obj.graduated_block_time == block_time
    assert isinstance(obj.graduated_block_time, int)
