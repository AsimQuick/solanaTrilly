# ---
# module: trading.tests.test_trading_config_ac641
# sprint: sprint-13
# story: US-64 AC-64.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.schemas, trading.models, trading.execution_core, pydantic, django, ast, pathlib
# ---
"""AC-64.1 — TradingConfig schema + TradingSettings singleton + no-auto-start AST guard.

Verifies:
  1. TradingConfig Pydantic schema — pure schema validation (no DB)
  2. TradingSettings Django model — singleton write-path gate (DB required)
  3. AST guard — no production code in trading/ sets trading_enabled=True automatically

Test structure
--------------
Schema validation tests (pure Pydantic, no DB):
  test_default_trading_enabled_is_false
  test_valid_config_validates
  test_position_size_zero_rejected
  test_position_size_negative_rejected
  test_stop_loss_exceeds_disaster_cap_rejected
  test_disaster_cap_exceeds_rug_pull_rejected
  test_slippage_order_violated_rejected
  test_trailing_exceeds_tp_rejected
  test_all_fields_accessible_by_attribute
  test_slippage_panic_bps_default_is_four_tiers

Django model write-path tests (DB required, @pytest.mark.django_db):
  test_valid_config_saves_successfully
  test_singleton_second_save_overwrites_first
  test_get_creates_singleton_if_absent
  test_to_schema_returns_trading_config
  test_position_size_zero_rejected_at_save
  test_stop_loss_ordering_rejected_at_save
  test_slippage_ordering_rejected_at_save
  test_invalid_save_writes_no_row
  test_trading_enabled_defaults_false_after_get

AST guard tests (no DB):
  test_no_production_file_sets_trading_enabled_true
  test_execution_core_does_not_import_live_source
  test_execution_core_does_not_import_replay_source
  test_trading_apps_has_no_ready_auto_start
"""
import ast
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TRADING_DIR = REPO_ROOT / "trading"
EXECUTION_CORE_PY = TRADING_DIR / "execution_core.py"
APPS_PY = TRADING_DIR / "apps.py"

# ---------------------------------------------------------------------------
# Helpers — AST scanner for trading_enabled=True assignments
# (mirrors AC-11.3 / AC-42.3 pattern)
# ---------------------------------------------------------------------------


def _find_trading_enabled_true(path: Path) -> list[str]:
    """Return descriptions of trading_enabled=True assignments in *path*.

    Scans for:
      - keyword arguments: .update(trading_enabled=True) / create(trading_enabled=True)
      - attribute assignments: self.trading_enabled = True
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: func(trading_enabled=True)
        if isinstance(node, ast.keyword):
            if (
                node.arg == "trading_enabled"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                findings.append(
                    f"{rel}: keyword trading_enabled=True at line {node.value.lineno}"
                )

        # attribute assignment: obj.trading_enabled = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "trading_enabled"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    findings.append(
                        f"{rel}: attribute .trading_enabled = True at line {node.value.lineno}"
                    )

    return findings


def _is_test_file(path: Path) -> bool:
    """Return True if path is inside a tests/ directory or named test_*.py."""
    parts = path.parts
    return any(p == "tests" for p in parts) or path.name.startswith("test_")


# ===========================================================================
# Section 1: Schema validation tests (pure Pydantic, no DB)
# ===========================================================================


def test_default_trading_enabled_is_false():
    """TradingConfig() must default trading_enabled to False (AC-64.1 safety gate)."""
    cfg = TradingConfig()
    assert cfg.trading_enabled is False, (
        "trading_enabled must DEFAULT to False — the observe/paper-first safety gate "
        "(AC-64.1 DoD: 'trading_enabled must DEFAULT to False')"
    )


def test_valid_config_validates():
    """A fully specified valid TradingConfig must pass Pydantic validation."""
    cfg = TradingConfig(
        trading_enabled=False,
        position_size_sol=0.5,
        max_open_positions=5,
        slippage_tight_bps=800,
        slippage_normal_bps=1500,
        slippage_loss_bps=2500,
        slippage_panic_bps=[5000, 7000, 9000, 9900],
        take_profit_pct=150.0,
        stop_loss_pct=15.0,
        disaster_cap_pct=30.0,
        rug_pull_drop_pct=50.0,
        next_poll_guard_s=10,
        auto_sell_timer_s=300,
        trailing_pct=10.0,
        trailing_arm_multiple=1.1,
        trailing_grace_s=60,
        ceiling_pct=300.0,
        volume_collapse_threshold_pct=10.0,
        concentration_threshold_pct=40.0,
    )
    assert cfg.position_size_sol == 0.5
    assert cfg.take_profit_pct == 150.0


def test_position_size_zero_rejected():
    """position_size_sol=0 must raise PydanticValidationError (gt=0 constraint)."""
    with pytest.raises(PydanticValidationError):
        TradingConfig(position_size_sol=0)


def test_position_size_negative_rejected():
    """position_size_sol=-0.1 must raise PydanticValidationError (gt=0 constraint)."""
    with pytest.raises(PydanticValidationError):
        TradingConfig(position_size_sol=-0.1)


def test_stop_loss_exceeds_disaster_cap_rejected():
    """stop_loss_pct >= disaster_cap_pct must raise PydanticValidationError.

    The exit priority ordering invariant requires:
      stop_loss_pct < disaster_cap_pct < rug_pull_drop_pct
    """
    with pytest.raises(PydanticValidationError):
        TradingConfig(
            stop_loss_pct=40.0,
            disaster_cap_pct=40.0,  # equal — violated
            rug_pull_drop_pct=50.0,
        )


def test_disaster_cap_exceeds_rug_pull_rejected():
    """disaster_cap_pct >= rug_pull_drop_pct must raise PydanticValidationError."""
    with pytest.raises(PydanticValidationError):
        TradingConfig(
            stop_loss_pct=20.0,
            disaster_cap_pct=55.0,
            rug_pull_drop_pct=50.0,  # disaster >= rug_pull — violated
        )


def test_slippage_order_violated_rejected():
    """tight >= normal slippage must raise PydanticValidationError.

    Slippage tiers must be strictly ascending: tight < normal < loss.
    """
    with pytest.raises(PydanticValidationError):
        TradingConfig(
            slippage_tight_bps=1500,
            slippage_normal_bps=1500,  # equal — violated
            slippage_loss_bps=2500,
        )


def test_trailing_exceeds_tp_rejected():
    """trailing_pct >= take_profit_pct must raise PydanticValidationError.

    A trailing stop at or above TP would never arm correctly.
    """
    with pytest.raises(PydanticValidationError):
        TradingConfig(
            trailing_pct=100.0,
            take_profit_pct=100.0,  # equal — violated
        )


def test_all_fields_accessible_by_attribute():
    """All TradingConfig fields must be accessible as attributes on the instance."""
    cfg = TradingConfig()
    assert hasattr(cfg, "trading_enabled")
    assert hasattr(cfg, "position_size_sol")
    assert hasattr(cfg, "max_open_positions")
    assert hasattr(cfg, "slippage_tight_bps")
    assert hasattr(cfg, "slippage_normal_bps")
    assert hasattr(cfg, "slippage_loss_bps")
    assert hasattr(cfg, "slippage_panic_bps")
    assert hasattr(cfg, "take_profit_pct")
    assert hasattr(cfg, "stop_loss_pct")
    assert hasattr(cfg, "disaster_cap_pct")
    assert hasattr(cfg, "rug_pull_drop_pct")
    assert hasattr(cfg, "next_poll_guard_s")
    assert hasattr(cfg, "auto_sell_timer_s")
    assert hasattr(cfg, "trailing_pct")
    assert hasattr(cfg, "trailing_arm_multiple")
    assert hasattr(cfg, "trailing_grace_s")
    assert hasattr(cfg, "ceiling_pct")
    assert hasattr(cfg, "volume_collapse_threshold_pct")
    assert hasattr(cfg, "concentration_threshold_pct")


def test_slippage_panic_bps_default_is_four_tiers():
    """slippage_panic_bps must default to exactly [5000, 7000, 9000, 9900] (PRD §10.1)."""
    cfg = TradingConfig()
    assert cfg.slippage_panic_bps == [5000, 7000, 9000, 9900], (
        f"Expected PANIC tiers [5000, 7000, 9000, 9900], got {cfg.slippage_panic_bps}"
    )


# ===========================================================================
# Section 2: Django model write-path tests (DB required)
# ===========================================================================


@pytest.mark.django_db
def test_valid_config_saves_successfully():
    """A TradingSettings row with valid defaults must save without raising."""
    from trading.models import TradingSettings

    settings = TradingSettings()
    settings.save()  # Must not raise
    assert TradingSettings.objects.filter(pk=1).exists()


@pytest.mark.django_db
def test_singleton_second_save_overwrites_first():
    """A second TradingSettings.save() must overwrite the first row (singleton pk=1)."""
    from trading.models import TradingSettings

    s1 = TradingSettings(position_size_sol=0.1)
    s1.save()

    s2 = TradingSettings(position_size_sol=0.5)
    s2.save()

    assert TradingSettings.objects.count() == 1
    row = TradingSettings.objects.get(pk=1)
    assert row.position_size_sol == 0.5


@pytest.mark.django_db
def test_get_creates_singleton_if_absent():
    """TradingSettings.get() must create the singleton row if it does not exist."""
    from trading.models import TradingSettings

    TradingSettings.objects.all().delete()
    assert TradingSettings.objects.count() == 0

    obj = TradingSettings.get()
    assert obj.pk == 1
    assert TradingSettings.objects.count() == 1


@pytest.mark.django_db
def test_to_schema_returns_trading_config():
    """TradingSettings.to_schema() must return a TradingConfig instance."""
    from trading.models import TradingSettings

    settings = TradingSettings.get()
    schema = settings.to_schema()
    assert isinstance(schema, TradingConfig)
    assert schema.trading_enabled is False


@pytest.mark.django_db
def test_position_size_zero_rejected_at_save():
    """TradingSettings with position_size_sol=0 must raise DjangoValidationError at save()."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from trading.models import TradingSettings

    s = TradingSettings(position_size_sol=0)
    with pytest.raises(DjangoValidationError):
        s.save()


@pytest.mark.django_db
def test_stop_loss_ordering_rejected_at_save():
    """TradingSettings with stop_loss_pct >= disaster_cap_pct must raise DjangoValidationError."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from trading.models import TradingSettings

    s = TradingSettings(
        stop_loss_pct=40.0,
        disaster_cap_pct=40.0,  # equal — violates ordering
        rug_pull_drop_pct=50.0,
    )
    with pytest.raises(DjangoValidationError):
        s.save()


@pytest.mark.django_db
def test_slippage_ordering_rejected_at_save():
    """TradingSettings with tight >= normal slippage must raise DjangoValidationError."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from trading.models import TradingSettings

    s = TradingSettings(
        slippage_tight_bps=2000,
        slippage_normal_bps=1500,  # tight > normal — violated
        slippage_loss_bps=2500,
    )
    with pytest.raises(DjangoValidationError):
        s.save()


@pytest.mark.django_db
def test_invalid_save_writes_no_row():
    """An invalid TradingSettings.save() must write no row to the DB."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from trading.models import TradingSettings

    TradingSettings.objects.all().delete()
    initial_count = TradingSettings.objects.count()

    s = TradingSettings(position_size_sol=0)
    with pytest.raises(DjangoValidationError):
        s.save()

    assert TradingSettings.objects.count() == initial_count, (
        "An invalid save() must not write any row to the trading_config table"
    )


@pytest.mark.django_db
def test_trading_enabled_defaults_false_after_get():
    """TradingSettings.get() must return a row with trading_enabled=False (safety gate)."""
    from trading.models import TradingSettings

    TradingSettings.objects.all().delete()
    obj = TradingSettings.get()
    assert obj.trading_enabled is False, (
        "trading_enabled must DEFAULT to False on the persisted singleton — "
        "the observe/paper-first safety gate (AC-64.1 DoD)"
    )


# ===========================================================================
# Section 3: AST guard tests (no DB)
# ===========================================================================


def test_no_production_file_sets_trading_enabled_true():
    """No production .py file in trading/ may set trading_enabled=True.

    Scans all non-test Python files in the trading/ package for any assignment
    of trading_enabled=True (keyword arg or attribute assignment).  This enforces
    the AC-64.1 safety gate: no boot/resolver/upload/ON path may auto-enable live
    trading.  Live trading is a deliberate, operator-driven Cutover action (§16).
    """
    violations = []
    for py_file in sorted(TRADING_DIR.rglob("*.py")):
        if _is_test_file(py_file):
            continue
        findings = _find_trading_enabled_true(py_file)
        violations.extend(findings)

    assert not violations, (
        "Production code in trading/ sets trading_enabled=True — this is "
        "forbidden (AC-64.1 safety gate / PRD §10.2). Live trading must be "
        "a deliberate operator-driven Cutover action, NOT auto-enabled by any "
        "boot/resolver/upload/ON path:\n"
        + textwrap.indent("\n".join(violations), "  ")
    )


def _has_import_of(path: Path, symbol: str) -> bool:
    """Return True if *path* contains an actual import statement for *symbol*.

    Uses AST to check import nodes — comments and docstrings mentioning the
    symbol name are not considered imports and are ignored.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if symbol in alias.name or (alias.asname and symbol in alias.asname):
                    return True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == symbol or (alias.asname and alias.asname == symbol):
                    return True
            # also check 'from module import *' style module path
            if node.module and symbol in node.module:
                return True
    return False


def test_execution_core_does_not_import_live_source():
    """trading/execution_core.py must NOT import LiveSource (DataSource seam, Principle #7)."""
    assert not _has_import_of(EXECUTION_CORE_PY, "LiveSource"), (
        "trading/execution_core.py imports LiveSource — this violates the DataSource "
        "seam (Principle #7). The ExecutionCore must receive an injected DataSource; "
        "it must NEVER import concrete source implementations directly."
    )


def test_execution_core_does_not_import_replay_source():
    """trading/execution_core.py must NOT import ReplaySource (DataSource seam, Principle #7)."""
    assert not _has_import_of(EXECUTION_CORE_PY, "ReplaySource"), (
        "trading/execution_core.py imports ReplaySource — this violates the DataSource "
        "seam (Principle #7). The ExecutionCore must receive an injected DataSource; "
        "it must NEVER import concrete source implementations directly."
    )


def test_trading_apps_has_no_ready_auto_start():
    """trading/apps.py must NOT contain a ready() method that could auto-start trading.

    The TradingConfig AppConfig must NOT define a ready() method — any ready()
    hook could silently trigger on Django startup, violating the no-auto-start
    safety gate (US-11 pattern / AC-64.1 DoD).
    """
    source = APPS_PY.read_text(encoding="utf-8")
    assert "def ready(" not in source, (
        "trading/apps.py defines a ready() method — this is forbidden. "
        "A ready() hook in the AppConfig could auto-start trading on Django "
        "boot, violating the AC-64.1 no-auto-start safety gate (US-11 pattern). "
        "Remove ready() from TradingConfig."
    )
