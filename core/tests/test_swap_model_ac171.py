# ---
# module: core.tests.test_swap_model_ac171
# sprint: sprint-5
# story: US-17 AC-17.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, django.db, pytest
# ---
"""AC-17.1 — Swap model round-trip test.

Verifies that the Swap model (table 'swaps') can create a row with all
required columns and read every column back from real Postgres.
"""
from datetime import datetime, timezone

import pytest

from core.models import Swap, Token


# ---------------------------------------------------------------------------
# Field introspection — schema invariants
# ---------------------------------------------------------------------------

def test_swap_table_name():
    assert Swap._meta.db_table == "swaps"


def test_mint_field_is_charfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("mint")
    assert isinstance(field, dj_models.CharField)


def test_block_time_is_integerfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("block_time")
    assert isinstance(field, dj_models.IntegerField)


def test_slot_is_integerfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("slot")
    assert isinstance(field, dj_models.IntegerField)


def test_signature_is_charfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("signature")
    assert isinstance(field, dj_models.CharField)


def test_price_is_floatfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("price")
    assert isinstance(field, dj_models.FloatField)


def test_vol_sol_is_floatfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("vol_sol")
    assert isinstance(field, dj_models.FloatField)


def test_vol_usd_is_floatfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("vol_usd")
    assert isinstance(field, dj_models.FloatField)


def test_sol_usd_is_floatfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("sol_usd")
    assert isinstance(field, dj_models.FloatField)


def test_owner_is_nullable_charfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("owner")
    assert isinstance(field, dj_models.CharField)
    assert field.null is True


def test_base_reserve_is_nullable_bigintegerfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("base_reserve")
    assert isinstance(field, dj_models.BigIntegerField)
    assert field.null is True


def test_quote_reserve_is_nullable_bigintegerfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("quote_reserve")
    assert isinstance(field, dj_models.BigIntegerField)
    assert field.null is True


def test_rel_is_floatfield():
    from django.db import models as dj_models
    field = Swap._meta.get_field("rel")
    assert isinstance(field, dj_models.FloatField)


# ---------------------------------------------------------------------------
# Round-trip: create a row and read every column back from Postgres
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_swap_row_roundtrip_all_columns():
    """Create a Swap row and verify every column survives a Postgres round-trip."""
    mint_addr = "Abc123MintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    # Create the referenced Token row first so FK-index integrity is satisfied.
    Token.objects.create(
        mint=mint_addr,
        pool_address="PoolAddrYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
        graduated_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        graduated_block_time=334_000_000,
        dex_source="birdeye_meme",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    swap = Swap.objects.create(
        mint=mint_addr,
        block_time=334_000_100,
        slot=334_000_050,
        signature="5xABC" + "z" * 83,
        side=Swap.SIDE_BUY,
        price=0.00001234,
        vol_sol=1.5,
        vol_usd=225.0,
        sol_usd=150.0,
        owner="OwnerWalletAddr" + "X" * 49,
        base_reserve=9_000_000_000,
        quote_reserve=1_000_000_000,
        rel=100,
    )

    obj = Swap.objects.get(pk=swap.pk)

    assert obj.mint == mint_addr
    assert obj.block_time == 334_000_100
    assert obj.slot == 334_000_050
    assert obj.signature == "5xABC" + "z" * 83
    assert obj.side == Swap.SIDE_BUY
    assert obj.price == pytest.approx(0.00001234)
    assert obj.vol_sol == pytest.approx(1.5)
    assert obj.vol_usd == pytest.approx(225.0)
    assert obj.sol_usd == pytest.approx(150.0)
    assert obj.owner == "OwnerWalletAddr" + "X" * 49
    assert obj.base_reserve == 9_000_000_000
    assert obj.quote_reserve == 1_000_000_000
    assert obj.rel == pytest.approx(100)


@pytest.mark.django_db
def test_swap_nullable_fields_accept_none():
    """owner, base_reserve, quote_reserve are nullable — None round-trips correctly."""
    mint_addr = "NullMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    Token.objects.create(
        mint=mint_addr,
        pool_address="PoolAddrYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
        graduated_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        graduated_block_time=334_000_000,
        dex_source="birdeye_meme",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    swap = Swap.objects.create(
        mint=mint_addr,
        block_time=334_000_200,
        slot=334_000_100,
        signature="NullSig" + "z" * 81,
        side=Swap.SIDE_SELL,
        price=0.00002,
        vol_sol=0.5,
        vol_usd=75.0,
        sol_usd=150.0,
        owner=None,
        base_reserve=None,
        quote_reserve=None,
        rel=200,
    )

    obj = Swap.objects.get(pk=swap.pk)
    assert obj.owner is None
    assert obj.base_reserve is None
    assert obj.quote_reserve is None
