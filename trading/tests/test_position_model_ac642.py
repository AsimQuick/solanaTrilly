# ---
# module: trading.tests.test_position_model_ac642
# sprint: sprint-13
# story: US-64 AC-64.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.models, django, pytest, datetime
# ---
"""AC-64.2 — unified Position model + migration tests.

Verifies:
  1. Model field structure — all AC-64.2 fields present with correct types/nullability
  2. DB write/read round-trips — model, copytrade; observe, live; PAPER, OPEN, CLOSED
  3. Parity foundation — a copytrade observe position is expressible as source='copytrade'
  4. closed_at IS NOT NULL sentinel — closed positions carry closed_at; open do not
  5. Migration presence — 0002_position migration file exists and is valid Django migration

Test list
---------
  test_position_has_all_required_fields
  test_source_choices_are_model_and_copytrade
  test_mode_choices_are_observe_and_live
  test_status_choices_are_paper_open_closed
  test_entry_fields_are_required
  test_exit_fields_are_nullable
  test_create_model_paper_position
  test_create_copytrade_observe_position
  test_create_open_live_position
  test_create_closed_position_with_all_exit_fields
  test_copytrade_observe_position_parity_foundation
  test_closed_at_null_for_open_position
  test_closed_at_set_for_closed_position
  test_migration_0002_exists_and_creates_position
  test_str_representation
"""
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "trading" / "migrations" / "0002_position.py"

MINT_A = "So11111111111111111111111111111111111111112"
MINT_B = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 6, 18, 12, 5, 0, tzinfo=timezone.utc)


# ===========================================================================
# Section 1: Model field structure (no DB)
# ===========================================================================


def test_position_has_all_required_fields():
    """Position must have every field enumerated in AC-64.2."""
    from trading.models import Position

    required_fields = {
        "mint",
        "source",
        "mode",
        "status",
        "entry_ts",
        "entry_price",
        "size_sol",
        "exit_ts",
        "exit_price",
        "exit_trigger",
        "realized_pnl_sol",
        "realized_pnl_pct",
        "peak_price",
        "closed_at",
    }
    model_fields = {f.name for f in Position._meta.get_fields() if hasattr(f, "name")}
    missing = required_fields - model_fields
    assert not missing, f"Position is missing AC-64.2 fields: {missing}"


def test_source_choices_are_model_and_copytrade():
    """source field choices must be exactly {'model', 'copytrade'}."""
    from trading.models import Position

    field = Position._meta.get_field("source")
    choice_values = {c[0] for c in field.choices}
    assert choice_values == {"model", "copytrade"}, (
        f"source choices must be {{'model', 'copytrade'}}, got {choice_values}"
    )


def test_mode_choices_are_observe_and_live():
    """mode field choices must be exactly {'observe', 'live'}."""
    from trading.models import Position

    field = Position._meta.get_field("mode")
    choice_values = {c[0] for c in field.choices}
    assert choice_values == {"observe", "live"}, (
        f"mode choices must be {{'observe', 'live'}}, got {choice_values}"
    )


def test_status_choices_are_paper_open_closed():
    """status field choices must be exactly {'PAPER', 'OPEN', 'CLOSED'}."""
    from trading.models import Position

    field = Position._meta.get_field("status")
    choice_values = {c[0] for c in field.choices}
    assert choice_values == {"PAPER", "OPEN", "CLOSED"}, (
        f"status choices must be {{'PAPER', 'OPEN', 'CLOSED'}}, got {choice_values}"
    )


def test_entry_fields_are_required():
    """entry_ts, entry_price, size_sol must NOT be nullable."""
    from trading.models import Position

    for fname in ("entry_ts", "entry_price", "size_sol"):
        field = Position._meta.get_field(fname)
        assert not field.null, f"{fname} must be NOT NULL (required at open time)"


def test_exit_fields_are_nullable():
    """All exit/settlement fields must be nullable (populated when position closes)."""
    from trading.models import Position

    for fname in (
        "exit_ts",
        "exit_price",
        "exit_trigger",
        "realized_pnl_sol",
        "realized_pnl_pct",
        "peak_price",
        "closed_at",
    ):
        field = Position._meta.get_field(fname)
        assert field.null, f"{fname} must be nullable (null until position closes)"


# ===========================================================================
# Section 2: DB write/read round-trips
# ===========================================================================


@pytest.mark.django_db
def test_create_model_paper_position():
    """A source='model', mode='observe', status='PAPER' position must save and reload."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=NOW,
        entry_price=0.000123,
        size_sol=0.1,
    )
    reloaded = Position.objects.get(pk=pos.pk)
    assert reloaded.mint == MINT_A
    assert reloaded.source == "model"
    assert reloaded.mode == "observe"
    assert reloaded.status == "PAPER"
    assert reloaded.entry_price == pytest.approx(0.000123)
    assert reloaded.size_sol == pytest.approx(0.1)
    assert reloaded.closed_at is None


@pytest.mark.django_db
def test_create_copytrade_observe_position():
    """A source='copytrade', mode='observe' position must save and reload correctly."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_B,
        source=Position.SOURCE_COPYTRADE,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=NOW,
        entry_price=0.00045,
        size_sol=0.25,
    )
    reloaded = Position.objects.get(pk=pos.pk)
    assert reloaded.source == "copytrade"
    assert reloaded.mode == "observe"
    assert reloaded.closed_at is None


@pytest.mark.django_db
def test_create_open_live_position():
    """A source='model', mode='live', status='OPEN' position must save correctly."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_LIVE,
        status=Position.STATUS_OPEN,
        entry_ts=NOW,
        entry_price=0.00099,
        size_sol=0.5,
    )
    reloaded = Position.objects.get(pk=pos.pk)
    assert reloaded.status == "OPEN"
    assert reloaded.mode == "live"
    assert reloaded.exit_price is None
    assert reloaded.closed_at is None


@pytest.mark.django_db
def test_create_closed_position_with_all_exit_fields():
    """A CLOSED position with all exit/settlement fields must save and reload correctly."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_CLOSED,
        entry_ts=NOW,
        entry_price=0.000100,
        size_sol=0.1,
        exit_ts=LATER,
        exit_price=0.000150,
        exit_trigger="TAKE_PROFIT_PCT",
        realized_pnl_sol=0.005,
        realized_pnl_pct=50.0,
        peak_price=0.000160,
        closed_at=LATER,
    )
    reloaded = Position.objects.get(pk=pos.pk)
    assert reloaded.status == "CLOSED"
    assert reloaded.exit_trigger == "TAKE_PROFIT_PCT"
    assert reloaded.realized_pnl_pct == pytest.approx(50.0)
    assert reloaded.peak_price == pytest.approx(0.000160)
    assert reloaded.closed_at == LATER


# ===========================================================================
# Section 3: Parity foundation
# ===========================================================================


@pytest.mark.django_db
def test_copytrade_observe_position_parity_foundation():
    """A copytrade observe position is expressible as a Position row with source='copytrade'.

    This is the AC-64.2 parity foundation: both pipelines write to the SAME
    shared model; copy-trade uses source='copytrade', mode='observe', status='PAPER'.
    The row must be retrievable via the shared model (no copytrade-specific table
    access required for reading).
    """
    from trading.models import Position

    # Simulate what the copytrade engine will write (AC-68.1 wiring)
    copytrade_pos = Position.objects.create(
        mint=MINT_B,
        source=Position.SOURCE_COPYTRADE,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=NOW,
        entry_price=0.000080,
        size_sol=0.25,
    )

    # The shared model can query it with the standard Django ORM
    found = Position.objects.filter(
        source=Position.SOURCE_COPYTRADE,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
    ).first()

    assert found is not None, (
        "A copytrade observe position must be expressible as a Position row "
        "with source='copytrade' (AC-64.2 parity foundation)"
    )
    assert found.pk == copytrade_pos.pk
    assert found.source == "copytrade"
    assert found.mode == "observe"
    assert found.status == "PAPER"


# ===========================================================================
# Section 4: closed_at sentinel
# ===========================================================================


@pytest.mark.django_db
def test_closed_at_null_for_open_position():
    """An open/paper position must have closed_at=None (not yet settled)."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        entry_ts=NOW,
        entry_price=0.0001,
        size_sol=0.1,
    )
    assert pos.closed_at is None, (
        "closed_at must be NULL for unsettled positions "
        "(AC-66.3 sentinel: closed_at IS NOT NULL ↔ settled)"
    )


@pytest.mark.django_db
def test_closed_at_set_for_closed_position():
    """A settled/closed position must carry a non-null closed_at timestamp."""
    from trading.models import Position

    pos = Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_CLOSED,
        entry_ts=NOW,
        entry_price=0.0001,
        size_sol=0.1,
        exit_ts=LATER,
        exit_price=0.00015,
        exit_trigger="STOP_LOSS",
        realized_pnl_sol=-0.002,
        realized_pnl_pct=-20.0,
        peak_price=0.00012,
        closed_at=LATER,
    )
    assert pos.closed_at is not None, (
        "closed_at must be set on a CLOSED position "
        "(AC-66.3 sentinel: closed_at IS NOT NULL ↔ settled)"
    )
    # Query using the sentinel
    settled_count = Position.objects.filter(closed_at__isnull=False).count()
    assert settled_count == 1


# ===========================================================================
# Section 5: Migration presence
# ===========================================================================


def test_migration_0002_exists_and_creates_position():
    """trading/migrations/0002_position.py must exist and reference Position."""
    assert MIGRATION_PATH.exists(), (
        f"Migration file not found: {MIGRATION_PATH}. "
        "AC-64.2 requires migrations for the Position model."
    )
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "Position" in source, (
        "0002_position.py must contain a CreateModel for Position"
    )
    assert "trading_positions" in source, (
        "0002_position.py must set db_table='trading_positions'"
    )
    # Verify it is importable as a valid Django migration
    spec = importlib.util.spec_from_file_location("migration_0002", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "Migration"), "0002_position.py must define a Migration class"
    assert hasattr(mod.Migration, "dependencies"), "Migration must have dependencies"
    assert hasattr(mod.Migration, "operations"), "Migration must have operations"


def test_str_representation():
    """Position.__str__ must include mint prefix, source, and status."""
    from trading.models import Position

    pos = Position(
        mint=MINT_A,
        source="copytrade",
        mode="observe",
        status="PAPER",
        entry_ts=NOW,
        entry_price=0.0001,
        size_sol=0.1,
    )
    s = str(pos)
    assert "copytrade" in s
    assert "PAPER" in s
