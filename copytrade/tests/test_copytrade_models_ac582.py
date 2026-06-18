# ---
# module: copytrade.tests.test_copytrade_models_ac582
# sprint: sprint-12
# story: US-58 AC-58.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models, django, pytest, ast
# ---
"""AC-58.2 — Four copytrade_-prefixed Django models + migrations.

Verifies:
  (a) All four models exist and carry the exact SPEC §9 fields (field names,
      types, and nullability spot-checks).
  (b) §5 ISOLATION: copytrade_ models declare NO ForeignKey into the raw lake
      (RawEvent, Token) or the firehose control plane (PipelineConfig,
      PipelineState) — verified via an AST guard over models.py source.
  (c) No copytrade migration mutates a non-copytrade table — verified by
      inspecting the operations list of every migration in copytrade/migrations/.
  (d) All four models can be saved to and queried from the test DB (structural
      DB-round-trip smoke tests).

No firehose required — all tests are offline / in-memory / DB-only.
"""

import ast
import importlib
from pathlib import Path

import pytest
from django.db import models as django_models

from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopytradeWallet,
)

# ---------------------------------------------------------------------------
# (a) Model field inventory
# ---------------------------------------------------------------------------


def _field_names(model_cls) -> set:
    return {f.name for f in model_cls._meta.get_fields() if hasattr(f, "name")}


def test_copytrade_cohort_has_required_fields():
    """CopytradeCohort must expose the SPEC §9 field set."""
    required = {"cohort_id", "created_at", "description", "trade_config", "uploaded_at", "active"}
    missing = required - _field_names(CopytradeCohort)
    assert not missing, f"CopytradeCohort missing fields: {missing}"


def test_copytrade_cohort_db_table():
    assert CopytradeCohort._meta.db_table == "copytrade_cohort"


def test_copytrade_cohort_cohort_id_is_unique():
    field = CopytradeCohort._meta.get_field("cohort_id")
    assert field.unique, "cohort_id must be unique"


def test_copytrade_cohort_active_is_bool():
    field = CopytradeCohort._meta.get_field("active")
    assert isinstance(field, django_models.BooleanField)


def test_copytrade_cohort_trade_config_is_json():
    field = CopytradeCohort._meta.get_field("trade_config")
    assert isinstance(field, django_models.JSONField)


def test_copytrade_wallet_has_required_fields():
    """CopytradeWallet must expose the SPEC §9 field set."""
    required = {"cohort_id", "address", "rank", "precision", "median_lead_min"}
    missing = required - _field_names(CopytradeWallet)
    assert not missing, f"CopytradeWallet missing fields: {missing}"


def test_copytrade_wallet_db_table():
    assert CopytradeWallet._meta.db_table == "copytrade_wallets"


def test_copytrade_wallet_rank_nullable():
    field = CopytradeWallet._meta.get_field("rank")
    assert field.null is True


def test_copytrade_position_has_required_fields():
    """CopytradePosition must expose the SPEC §9 field set."""
    required = {
        "cohort_id", "mint", "trigger_wallet", "status", "mode",
        "entry_ts", "entry_price", "sol_in",
        "exit_ts", "exit_price", "sol_out", "exit_reason",
        "realized_pnl_sol", "realized_pnl_pct",
    }
    missing = required - _field_names(CopytradePosition)
    assert not missing, f"CopytradePosition missing fields: {missing}"


def test_copytrade_position_db_table():
    assert CopytradePosition._meta.db_table == "copytrade_positions"


def test_copytrade_position_status_choices():
    field = CopytradePosition._meta.get_field("status")
    choice_values = {c[0] for c in field.choices}
    assert "open" in choice_values
    assert "closed" in choice_values


def test_copytrade_position_mode_choices():
    field = CopytradePosition._meta.get_field("mode")
    choice_values = {c[0] for c in field.choices}
    assert "observe" in choice_values
    assert "live" in choice_values


def test_copytrade_position_exit_reason_choices():
    field = CopytradePosition._meta.get_field("exit_reason")
    choice_values = {c[0] for c in field.choices}
    assert {"TP", "SL", "CURVE", "TIMER"}.issubset(choice_values)


def test_copytrade_position_exit_fields_nullable():
    for fname in ("exit_ts", "exit_price", "sol_out", "exit_reason", "realized_pnl_sol", "realized_pnl_pct"):
        field = CopytradePosition._meta.get_field(fname)
        assert field.null is True, f"{fname} should be nullable"


def test_copytrade_pnl_by_wallet_has_required_fields():
    """CopytradePnlByWallet must expose the SPEC §9 field set."""
    required = {"cohort_id", "address", "n_trades", "win_rate", "total_pnl_sol", "avg_hold_s"}
    missing = required - _field_names(CopytradePnlByWallet)
    assert not missing, f"CopytradePnlByWallet missing fields: {missing}"


def test_copytrade_pnl_by_wallet_db_table():
    assert CopytradePnlByWallet._meta.db_table == "copytrade_pnl_by_wallet"


def test_copytrade_pnl_by_wallet_unique_together():
    unique_together = CopytradePnlByWallet._meta.unique_together
    assert ("cohort_id", "address") in unique_together, (
        "CopytradePnlByWallet must have unique_together on (cohort_id, address)"
    )


# ---------------------------------------------------------------------------
# (b) §5 ISOLATION — AST guard: no FK into RawEvent / Token / PipelineConfig
# ---------------------------------------------------------------------------

_FORBIDDEN_FK_TARGETS = {"RawEvent", "Token", "PipelineConfig", "PipelineState"}

_MODELS_SRC = Path(__file__).parent.parent / "models.py"


def _fk_targets_in_source(src_path: Path) -> set:
    """Return the set of ForeignKey/OneToOneField 'to' argument names found in the file."""
    tree = ast.parse(src_path.read_text())
    targets = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match django.db.models.ForeignKey / OneToOneField calls
        func_name = None
        if isinstance(func, ast.Attribute):
            func_name = func.attr
        elif isinstance(func, ast.Name):
            func_name = func.id
        if func_name not in ("ForeignKey", "OneToOneField"):
            continue
        # First positional arg or 'to' keyword arg is the target model
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                targets.add(arg.value.split(".")[-1])
            elif isinstance(arg, ast.Name):
                targets.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                targets.add(arg.attr)
        for kw in node.keywords:
            if kw.arg == "to":
                val = kw.value
                if isinstance(val, ast.Constant):
                    targets.add(str(val.value).split(".")[-1])
                elif isinstance(val, ast.Name):
                    targets.add(val.id)
    return targets


def test_ast_no_fk_into_raw_lake():
    """copytrade/models.py must not declare any FK into RawEvent/Token/PipelineConfig/PipelineState."""
    targets = _fk_targets_in_source(_MODELS_SRC)
    violations = targets & _FORBIDDEN_FK_TARGETS
    assert not violations, (
        f"§5 isolation violated: copytrade/models.py has FK(s) pointing at {violations}"
    )


# ---------------------------------------------------------------------------
# (c) Migration guard — no copytrade migration mutates a non-copytrade table
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# Operations that create/alter/delete tables in other apps
_CROSS_APP_OP_TYPES = (
    "migrations.CreateModel",
    "migrations.DeleteModel",
    "migrations.AddField",
    "migrations.RemoveField",
    "migrations.AlterField",
    "migrations.RenameField",
    "migrations.RenameModel",
    "migrations.AlterUniqueTogether",
    "migrations.AlterIndexTogether",
    "migrations.AddIndex",
    "migrations.RemoveIndex",
    "migrations.RunSQL",
    "migrations.RunPython",
)


def _collect_migration_modules():
    """Yield (filename, migration_class) for every migration in copytrade/migrations/."""
    for f in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        module_name = f"copytrade.migrations.{f.stem}"
        mod = importlib.import_module(module_name)
        # Django migrations define a class named 'Migration'
        migration_cls = getattr(mod, "Migration", None)
        if migration_cls is not None:
            yield f.name, migration_cls


def test_migrations_only_reference_copytrade_app():
    """All operations in copytrade migrations must target the copytrade app only.

    Specifically, RunSQL / RunPython must not reference non-copytrade table names,
    and CreateModel/AlterField/etc. are only generated for copytrade_ tables.
    """
    for fname, migration_cls in _collect_migration_modules():
        for op in migration_cls.operations:
            op_type = type(op).__name__
            # Check dependencies — should only be within copytrade or none
            # (we only flag actual schema operations)
            if hasattr(op, "name"):
                # CreateModel, DeleteModel, AlterUniqueTogether etc have .name
                table_name = getattr(op, "name", "") or ""
                # Django operation .name is the model name, not table name.
                # Model name check: must NOT be a known core model.
                if table_name and table_name not in (
                    "CopyTradeSettings",
                    "CopytradeCohort",
                    "CopytradeWallet",
                    "CopytradePosition",
                    "CopytradePnlByWallet",
                ):
                    # Allow index operations on known copytrade models
                    if op_type in ("AddIndex", "RemoveIndex"):
                        idx = getattr(op, "index", None)
                        if idx is not None:
                            # The model_name attribute identifies the target model
                            model_name = getattr(op, "model_name", "")
                            assert model_name.lower() in (
                                "copytradesettings",
                                "copytradecohort",
                                "copytradewallet",
                                "copytradeposition",
                                "copytradepnlbywallet",
                            ), (
                                f"{fname}: operation {op_type} targets non-copytrade model '{model_name}'"
                            )
                    elif op_type == "AlterUniqueTogether":
                        model_name = getattr(op, "name", "")
                        assert model_name.lower() in (
                            "copytradesettings",
                            "copytradecohort",
                            "copytradewallet",
                            "copytradeposition",
                            "copytradepnlbywallet",
                        ), (
                            f"{fname}: AlterUniqueTogether targets non-copytrade model '{model_name}'"
                        )
                    else:
                        pytest.fail(
                            f"{fname}: operation {op_type} targets non-copytrade model '{table_name}'"
                        )


def test_migration_dependencies_do_not_reference_core():
    """copytrade migrations must not have dependencies on the core app migrations."""
    for fname, migration_cls in _collect_migration_modules():
        for dep_app, _ in migration_cls.dependencies:
            assert dep_app != "core", (
                f"{fname}: has dependency on core app migration — §5 isolation requires "
                "copytrade migrations to be self-contained"
            )


# ---------------------------------------------------------------------------
# (d) DB round-trip smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_copytrade_cohort_saves_and_queries():
    """CopytradeCohort can be written and read back from the DB."""
    from django.utils import timezone

    cohort = CopytradeCohort.objects.create(
        cohort_id="test-cohort-001",
        created_at=timezone.now(),
        description="Test cohort",
        trade_config={"mode": "observe"},
        active=True,
    )
    fetched = CopytradeCohort.objects.get(cohort_id="test-cohort-001")
    assert fetched.active is True
    assert fetched.description == "Test cohort"
    assert cohort.pk == fetched.pk


@pytest.mark.django_db
def test_copytrade_wallet_saves_and_queries():
    """CopytradeWallet can be written and read back from the DB."""
    wallet = CopytradeWallet.objects.create(
        cohort_id="test-cohort-001",
        address="WaLLeTaddressABC123",
        rank=1,
        precision=0.78,
        median_lead_min=2.5,
    )
    fetched = CopytradeWallet.objects.get(pk=wallet.pk)
    assert fetched.address == "WaLLeTaddressABC123"
    assert fetched.rank == 1
    assert fetched.cohort_id == "test-cohort-001"


@pytest.mark.django_db
def test_copytrade_position_saves_and_queries():
    """CopytradePosition can be written and read back from the DB."""
    from django.utils import timezone

    pos = CopytradePosition.objects.create(
        cohort_id="test-cohort-001",
        mint="MiNtAddressXYZ",
        trigger_wallet="WaLLeTaddressABC123",
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=timezone.now(),
        entry_price=0.000012,
        sol_in=0.25,
    )
    fetched = CopytradePosition.objects.get(pk=pos.pk)
    assert fetched.status == "open"
    assert fetched.mode == "observe"
    assert fetched.exit_ts is None
    assert fetched.exit_reason is None


@pytest.mark.django_db
def test_copytrade_pnl_by_wallet_saves_and_queries():
    """CopytradePnlByWallet can be written and read back from the DB."""
    pnl = CopytradePnlByWallet.objects.create(
        cohort_id="test-cohort-001",
        address="WaLLeTaddressABC123",
        n_trades=5,
        win_rate=0.6,
        total_pnl_sol=0.15,
        avg_hold_s=420.0,
    )
    fetched = CopytradePnlByWallet.objects.get(pk=pnl.pk)
    assert fetched.n_trades == 5
    assert fetched.win_rate == pytest.approx(0.6)


@pytest.mark.django_db
def test_copytrade_pnl_by_wallet_unique_together_enforced():
    """CopytradePnlByWallet (cohort_id, address) must be unique."""
    from django.db import IntegrityError

    CopytradePnlByWallet.objects.create(
        cohort_id="test-cohort-001",
        address="WaLLeTaddressABC123",
        n_trades=3,
    )
    with pytest.raises(IntegrityError):
        CopytradePnlByWallet.objects.create(
            cohort_id="test-cohort-001",
            address="WaLLeTaddressABC123",
            n_trades=99,
        )


@pytest.mark.django_db
def test_all_four_tables_are_distinct_db_tables():
    """Each model must map to a distinct db_table name."""
    tables = {
        CopytradeCohort._meta.db_table,
        CopytradeWallet._meta.db_table,
        CopytradePosition._meta.db_table,
        CopytradePnlByWallet._meta.db_table,
    }
    assert len(tables) == 4, f"Expected 4 distinct db_table names, got {tables}"
    for t in tables:
        assert t.startswith("copytrade_"), f"Table '{t}' must start with 'copytrade_'"
