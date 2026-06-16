# ---
# module: core.tests.test_snapshot_idempotency_ac232
# sprint: sprint-6
# story: US-23 AC-23.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, core.encoders, django.db, pytest, decimal, datetime, math
# ---
"""AC-23.2 — Snapshot at-most-one-row-per-token + JsonSafeEncoder guard.

Two invariants verified:

1. JsonSafeEncoder guard (H3/US-5): a Snapshot whose raw payload contains
   NaN, Inf, Decimal, and datetime values persists without error and reads
   back as spec-valid JSON (NaN/Inf → null, Decimal → float, datetime → ISO
   string).

2. At-most-one-row-per-token (§6.3): re-writing a Snapshot for the same
   mint via update_or_create leaves exactly one 'snapshots' row — no
   duplicate is created on retry or re-snapshot.
"""
import json
import math
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Snapshot

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_TAKEN_AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _mint(tag: str) -> str:
    """Return a 44-char test mint (safe for VARCHAR(64))."""
    prefix = f"AC232{tag}"
    return (prefix + "1" * 44)[:44]


_MINT = _mint("Base")


def _make_raw(**overrides):
    base = {
        "holders": 10,
        "mint_authority": None,
        "freeze_authority": None,
        "lp_burned": False,
        "liquidity": 1000.0,
        "tvl": 950.0,
        "depth": {"bid": 50.0, "ask": 50.0},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. JsonSafeEncoder — non-finite / Decimal / datetime in raw
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_raw_with_nan_persists_without_error():
    """A raw payload containing NaN does not raise on save."""
    Snapshot.objects.create(
        mint=_mint("nan"),
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=_make_raw(score=float("nan")),
    )
    obj = Snapshot.objects.get(mint=_mint("nan"))
    assert obj.raw["score"] is None, "NaN must read back as null (json-safe)"


@pytest.mark.django_db
def test_raw_with_inf_persists_without_error():
    """A raw payload containing +Inf and -Inf does not raise on save."""
    Snapshot.objects.create(
        mint=_mint("inf"),
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=_make_raw(pos_inf=float("inf"), neg_inf=float("-inf")),
    )
    obj = Snapshot.objects.get(mint=_mint("inf"))
    assert obj.raw["pos_inf"] is None, "+Inf must read back as null"
    assert obj.raw["neg_inf"] is None, "-Inf must read back as null"


@pytest.mark.django_db
def test_raw_with_decimal_persists_and_reads_as_float():
    """A raw payload containing Decimal values persists and reads back as float."""
    Snapshot.objects.create(
        mint=_mint("dec"),
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=_make_raw(price=Decimal("3.14159")),
    )
    obj = Snapshot.objects.get(mint=_mint("dec"))
    price = obj.raw["price"]
    assert isinstance(price, float), "Decimal must read back as float"
    assert abs(price - 3.14159) < 1e-4


@pytest.mark.django_db
def test_raw_with_datetime_persists_and_reads_as_iso_string():
    """A raw payload containing a datetime persists and reads back as ISO string."""
    dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    Snapshot.objects.create(
        mint=_mint("dt"),
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=_make_raw(captured_at=dt),
    )
    obj = Snapshot.objects.get(mint=_mint("dt"))
    captured = obj.raw["captured_at"]
    assert isinstance(captured, str), "datetime must read back as ISO string"
    # Must be parseable
    normalised = captured.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    assert parsed.replace(tzinfo=None) == dt.replace(tzinfo=None)


@pytest.mark.django_db
def test_mixed_non_finite_payload_is_json_safe():
    """A payload mixing NaN, Inf, Decimal, and datetime all round-trip correctly."""
    dt = datetime(2026, 3, 15, 8, 0, 0, tzinfo=timezone.utc)
    raw_payload = _make_raw(
        nan_val=float("nan"),
        inf_val=float("inf"),
        neg_inf_val=float("-inf"),
        decimal_val=Decimal("99.99"),
        dt_val=dt,
    )
    Snapshot.objects.create(
        mint=_mint("mix"),
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=raw_payload,
    )
    obj = Snapshot.objects.get(mint=_mint("mix"))
    raw = obj.raw

    assert raw["nan_val"] is None
    assert raw["inf_val"] is None
    assert raw["neg_inf_val"] is None
    assert isinstance(raw["decimal_val"], float)
    assert isinstance(raw["dt_val"], str)

    # Confirm the stored raw is itself spec-valid JSON (no NaN/Inf literals)
    encoded = json.dumps(raw)
    assert math.isnan(json.loads(encoded).get("nan_val") or 0) is False


# ---------------------------------------------------------------------------
# 2. At-most-one-row-per-token (§6.3 idempotency via update_or_create)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_first_snapshot_creates_one_row():
    """Creating a Snapshot produces exactly one 'snapshots' row for the mint."""
    Snapshot.objects.update_or_create(
        mint=_MINT,
        defaults={"taken_at": _TAKEN_AT, "elapsed_s": 60, "raw": _make_raw()},
    )
    assert Snapshot.objects.filter(mint=_MINT).count() == 1


@pytest.mark.django_db
def test_re_snapshot_same_mint_yields_one_row():
    """update_or_create for an already-snapshotted mint leaves exactly one row."""
    Snapshot.objects.update_or_create(
        mint=_MINT,
        defaults={"taken_at": _TAKEN_AT, "elapsed_s": 60, "raw": _make_raw()},
    )
    # Simulate a retry / re-snapshot at a later elapsed_s
    updated_raw = _make_raw(holders=99)
    Snapshot.objects.update_or_create(
        mint=_MINT,
        defaults={
            "taken_at": datetime(2026, 6, 16, 12, 5, 0, tzinfo=timezone.utc),
            "elapsed_s": 300,
            "raw": updated_raw,
        },
    )
    assert Snapshot.objects.filter(mint=_MINT).count() == 1, (
        "re-snapshot must not create a duplicate row (AC-23.2 at-most-one-row-per-token)"
    )


@pytest.mark.django_db
def test_re_snapshot_updates_existing_row():
    """After update_or_create, the single row reflects the latest data."""
    Snapshot.objects.update_or_create(
        mint=_MINT,
        defaults={"taken_at": _TAKEN_AT, "elapsed_s": 60, "raw": _make_raw(holders=10)},
    )
    new_taken = datetime(2026, 6, 16, 12, 5, 0, tzinfo=timezone.utc)
    Snapshot.objects.update_or_create(
        mint=_MINT,
        defaults={"taken_at": new_taken, "elapsed_s": 300, "raw": _make_raw(holders=99)},
    )
    obj = Snapshot.objects.get(mint=_MINT)
    assert obj.elapsed_s == 300
    assert obj.raw["holders"] == 99


@pytest.mark.django_db
def test_unique_constraint_prevents_duplicate_create():
    """Direct objects.create for the same mint raises IntegrityError — the DB
    constraint enforces uniqueness even without update_or_create."""
    from django.db import IntegrityError

    Snapshot.objects.create(
        mint=_MINT,
        taken_at=_TAKEN_AT,
        elapsed_s=60,
        raw=_make_raw(),
    )
    with pytest.raises(IntegrityError):
        Snapshot.objects.create(
            mint=_MINT,
            taken_at=_TAKEN_AT,
            elapsed_s=120,
            raw=_make_raw(holders=5),
        )
