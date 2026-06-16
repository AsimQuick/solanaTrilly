# ---
# module: core.tests.test_snapshot_model_ac231
# sprint: sprint-6
# story: US-23 AC-23.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, core.encoders, django.db, pytest
# ---
"""AC-23.1 — Snapshot model round-trip test.

Verifies that the Snapshot model (table 'snapshots') can create a row with all
required columns and read every column back from real Postgres.  Also verifies
field-level invariants: table name, JSONField uses JsonSafeEncoder, taken_at is
DateTimeField (TIMESTAMPTZ), elapsed_s is IntegerField, and mint carries a
db_index + unique constraint.
"""
from datetime import datetime, timezone

import pytest
from django.db import models as dj_models

from core.encoders import JsonSafeEncoder
from core.models import Snapshot


# ---------------------------------------------------------------------------
# Field introspection — schema invariants
# ---------------------------------------------------------------------------


def test_snapshot_table_name():
    assert Snapshot._meta.db_table == "snapshots"


def test_mint_field_is_charfield():
    field = Snapshot._meta.get_field("mint")
    assert isinstance(field, dj_models.CharField)


def test_mint_has_db_index():
    field = Snapshot._meta.get_field("mint")
    assert field.db_index is True


def test_mint_is_unique():
    field = Snapshot._meta.get_field("mint")
    assert field.unique is True


def test_taken_at_is_datetimefield():
    field = Snapshot._meta.get_field("taken_at")
    assert isinstance(field, dj_models.DateTimeField)


def test_elapsed_s_is_integerfield():
    field = Snapshot._meta.get_field("elapsed_s")
    assert isinstance(field, dj_models.IntegerField)


def test_raw_uses_json_safe_encoder():
    """raw must declare encoder=JsonSafeEncoder (H3/US-5 guard)."""
    field = Snapshot._meta.get_field("raw")
    assert field.encoder is JsonSafeEncoder


# ---------------------------------------------------------------------------
# Round-trip: create a row and read every column back from Postgres
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_row_roundtrip_all_columns():
    """Create a Snapshot row and verify every column survives a Postgres round-trip."""
    taken = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
    raw_payload = {
        "holders": 42,
        "mint_authority": None,
        "freeze_authority": None,
        "lp_burned": True,
        "liquidity": 5000.0,
        "tvl": 4800.0,
        "depth": {"bid": 100.0, "ask": 100.0},
    }
    mint = "SnapshotMint111111111111111111111111111111111111111111111111111"

    Snapshot.objects.create(
        mint=mint,
        taken_at=taken,
        elapsed_s=120,
        raw=raw_payload,
    )

    obj = Snapshot.objects.get(mint=mint)

    assert obj.mint == mint
    assert obj.taken_at == taken
    assert obj.elapsed_s == 120
    assert obj.raw == raw_payload
