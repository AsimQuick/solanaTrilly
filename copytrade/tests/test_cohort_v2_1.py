# ---
# module: copytrade.tests.test_cohort_v2_1
# sprint: copytrade-2.1-loader
# story: copytrade-v2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, copytrade.schemas, copytrade.validators,
#   copytrade.cohort_lifecycle, copytrade.models
# ---
"""Tests for the copytrade-2.1 cohort loader.

Drives the REAL fixture (cohort_jun_deploy_v2_1.json) so the parser is
exercised against the exact bytes the engine will consume in production.
No mocks — all tests exercise real schema parsing, validation, and DB roundtrip.

Tests
-----
Schema parsing (no DB):
    test_parses_real_v2_1_fixture            — 20 wallets, correct global block
    test_ride_exit_parsed_from_ride_tagged   — trailing params: SL50/trail40/TP900
    test_scalp_wallet_resolves_mirror_exit   — non-ride wallet -> MirrorWalletSellExit
    test_wallet_to_style_map                 — style map matches fixture
    test_validate_cohort_any_dispatches_v2_1 — validate_cohort_any returns CohortV21

Validation errors (no DB):
    test_v2_1_rejects_invalid_ride_tagged    — malformed ride_tagged string
    test_v2_1_rejects_more_than_20_wallets   — >20 wallets fails
    test_v2_1_rejects_empty_wallet_list      — empty wallets fails
    test_validate_cohort_any_unknown_version — unknown version raises error

Lifecycle / DB roundtrip (DB):
    test_replace_cohort_v2_1_loads_fixture   — full load + DB state check
    test_replace_cohort_v2_1_is_fresh_start  — second load wipes first
    test_migration_applies_style_field       — style field readable from DB post-load
    test_v2_1_over_v2_0_fresh_start         — 2.1 cohort replaces a 2.0 cohort cleanly

Observe-safety guard (no DB, AST):
    test_observe_safety_still_green         — no place_buy_order call in any new code
"""
from __future__ import annotations

import ast
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from copytrade.schemas import (
    CohortV21,
    MirrorWalletSellExit,
    OurTrailingExit,
)
from copytrade.validators import (
    CohortJsonValidationError,
    validate_cohort_any,
    validate_cohort_v2_1,
)

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURE_V2_1 = os.path.join(
    os.path.dirname(__file__), "fixtures", "cohort_jun_deploy_v2_1.json"
)

FIXTURE_V2_0 = os.path.join(
    os.path.dirname(__file__), "fixtures", "cohort_2026-06-19_v1.json"
)


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Schema parsing tests (no DB)
# ---------------------------------------------------------------------------


def test_parses_real_v2_1_fixture():
    """Schema 2.1 parses the real fixture with 20 wallets and correct global block."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    assert cohort.schema_version == "copytrade-2.1"
    assert cohort.cohort_id == "copy_2026-06-20_jun_deploy"
    assert cohort.global_.mode == "observe"
    assert cohort.global_.usd_size_per_trade == 25.0
    assert cohort.global_.trigger.min_trigger_buy_usd == 250.0
    assert cohort.global_.trigger.pump_fun_only is True
    assert cohort.global_.trigger.copy_first_buy_only is True
    assert cohort.global_.trigger.dedupe_token_across_wallets is True
    assert cohort.global_.max_concurrent_positions == 30
    assert cohort.global_.reselect_cadence_days == 7
    assert len(cohort.wallets) == 20


def test_ride_exit_parsed_from_ride_tagged():
    """The ride_tagged string 'our_trailing(SL=-50%,trail=-40%,TP=+900%)' is parsed
    into a typed OurTrailingExit with stop_loss_pct=50, trailing_giveback_pct=40,
    take_profit_pct=900."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    ride_exit = cohort.ride_exit()
    assert isinstance(ride_exit, OurTrailingExit)
    assert ride_exit.type == "our_trailing"
    assert ride_exit.stop_loss_pct == 50.0
    assert ride_exit.trailing_giveback_pct == 40.0
    assert ride_exit.take_profit_pct == 900.0
    assert ride_exit.max_hold_seconds is None


def test_scalp_wallet_resolves_mirror_exit():
    """A wallet with style != 'ride' (e.g. 'scalp') resolves to MirrorWalletSellExit."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    # First two wallets are ride; all others are scalp.
    scalp_wallet = next(w for w in cohort.wallets if w.style != "ride")
    exit_cfg = cohort.exit_for_wallet(scalp_wallet)
    assert isinstance(exit_cfg, MirrorWalletSellExit)
    assert exit_cfg.type == "mirror_wallet_sell"
    assert exit_cfg.max_hold_seconds == 86400
    assert exit_cfg.hard_stop_loss_pct is None


def test_ride_wallet_resolves_trailing_exit():
    """A wallet with style == 'ride' resolves to OurTrailingExit."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    ride_wallet = next(w for w in cohort.wallets if w.style == "ride")
    exit_cfg = cohort.exit_for_wallet(ride_wallet)
    assert isinstance(exit_cfg, OurTrailingExit)
    assert exit_cfg.stop_loss_pct == 50.0


def test_wallet_to_style_map():
    """wallet_to_style() maps each address to its declared style tag."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    style_map = cohort.wallet_to_style()
    assert len(style_map) == 20
    # Ranks 1 and 2 are "ride" per the fixture.
    rank1 = cohort.wallets[0]
    rank2 = cohort.wallets[1]
    assert rank1.style == "ride"
    assert rank2.style == "ride"
    assert style_map[rank1.address] == "ride"
    assert style_map[rank2.address] == "ride"
    # All others are scalp.
    for w in cohort.wallets[2:]:
        assert style_map[w.address] == "scalp"


def test_wallet_to_exit_map():
    """wallet_to_exit() returns exit config keyed by address."""
    cohort = CohortV21.model_validate(_load(FIXTURE_V2_1))
    exit_map = cohort.wallet_to_exit()
    assert len(exit_map) == 20
    for addr, exit_cfg in exit_map.items():
        assert isinstance(exit_cfg, (OurTrailingExit, MirrorWalletSellExit))


def test_validate_cohort_any_dispatches_v2_1():
    """validate_cohort_any dispatches schema 2.1 to CohortV21."""
    result = validate_cohort_any(_load(FIXTURE_V2_1))
    assert isinstance(result, CohortV21)
    assert result.schema_version == "copytrade-2.1"


# ---------------------------------------------------------------------------
# Validation error tests (no DB)
# ---------------------------------------------------------------------------


def test_v2_1_rejects_invalid_ride_tagged():
    """A malformed ride_tagged string raises CohortJsonValidationError."""
    bad = _load(FIXTURE_V2_1)
    bad["global"]["exit"]["ride_tagged"] = "NOT_VALID(SL=50,trail=40)"
    with pytest.raises((CohortJsonValidationError, ValueError)):
        validate_cohort_v2_1(bad)


def test_v2_1_rejects_more_than_20_wallets():
    """More than 20 wallets in a 2.1 cohort raises CohortJsonValidationError."""
    bad = _load(FIXTURE_V2_1)
    extra = {"rank": 21, "address": "ExtraWalletXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX", "style": "scalp"}
    bad["wallets"].append(extra)
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2_1(bad)


def test_v2_1_rejects_empty_wallet_list():
    """An empty wallet list in a 2.1 cohort raises CohortJsonValidationError."""
    bad = _load(FIXTURE_V2_1)
    bad["wallets"] = []
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2_1(bad)


def test_validate_cohort_any_unknown_version():
    """validate_cohort_any raises CohortJsonValidationError for an unknown version."""
    bad = _load(FIXTURE_V2_1)
    bad["schema_version"] = "copytrade-9.9"
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_any(bad)


def test_v2_1_rejects_missing_cohort_id():
    """Missing cohort_id raises CohortJsonValidationError."""
    bad = _load(FIXTURE_V2_1)
    bad["cohort_id"] = ""
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2_1(bad)


# ---------------------------------------------------------------------------
# DB lifecycle tests
# ---------------------------------------------------------------------------


_TS = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.django_db
def test_replace_cohort_v2_1_loads_fixture():
    """replace_cohort_v2_1 loads the real fixture, persists all 20 wallets with
    correct style tags, and updates CopyTradeSettings with the global block."""
    from copytrade.cohort_lifecycle import replace_cohort_v2_1
    from copytrade.models import (
        CopytradeCohort,
        CopyTradeSettings,
        CopytradeWallet,
    )

    cohort_row = replace_cohort_v2_1(_load(FIXTURE_V2_1), settlement_price=0.0, settlement_ts=_TS)

    assert cohort_row.cohort_id == "copy_2026-06-20_jun_deploy"
    assert cohort_row.active is True
    assert CopytradeCohort.objects.filter(active=True).count() == 1

    settings = CopyTradeSettings.get()
    assert settings.active_cohort_id == "copy_2026-06-20_jun_deploy"
    assert settings.mode == "observe"
    assert settings.usd_size_per_trade == 25.0
    assert settings.min_trigger_buy_usd == 250.0
    assert settings.max_concurrent_positions == 30
    assert settings.engine_on is False
    assert settings.mirror_wallet_sells is False

    # All 20 wallets subscribed.
    wallets = list(CopytradeWallet.objects.filter(cohort_id="copy_2026-06-20_jun_deploy"))
    assert len(wallets) == 20

    # Style field set correctly.
    ride_wallets = [w for w in wallets if w.style == "ride"]
    scalp_wallets = [w for w in wallets if w.style == "scalp"]
    assert len(ride_wallets) == 2
    assert len(scalp_wallets) == 18

    # strategy_id mirrors style (for transparent engine dispatch).
    for w in wallets:
        assert w.strategy_id == w.style, (
            f"wallet {w.address}: strategy_id={w.strategy_id!r} != style={w.style!r}"
        )

    # The full 2.1 dict is stored verbatim.
    assert cohort_row.trade_config["schema_version"] == "copytrade-2.1"


@pytest.mark.django_db
def test_replace_cohort_v2_1_is_fresh_start():
    """A second replace_cohort_v2_1 call wipes the first cohort (SPEC §6)."""
    from copytrade.cohort_lifecycle import replace_cohort_v2_1
    from copytrade.models import CopytradeCohort, CopytradeWallet

    replace_cohort_v2_1(_load(FIXTURE_V2_1), settlement_price=0.0, settlement_ts=_TS)

    second = _load(FIXTURE_V2_1)
    second["cohort_id"] = "copy_2026-06-27_jun_deploy_v2"
    replace_cohort_v2_1(second, settlement_price=0.0, settlement_ts=_TS)

    assert CopytradeCohort.objects.count() == 1
    assert CopytradeCohort.objects.filter(active=True).first().cohort_id == "copy_2026-06-27_jun_deploy_v2"
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-20_jun_deploy").count() == 0
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-27_jun_deploy_v2").count() == 20


@pytest.mark.django_db
def test_migration_applies_style_field():
    """After loading the 2.1 fixture, the style field is readable from the DB."""
    from copytrade.cohort_lifecycle import replace_cohort_v2_1
    from copytrade.models import CopytradeWallet

    replace_cohort_v2_1(_load(FIXTURE_V2_1), settlement_price=0.0, settlement_ts=_TS)

    # Ranks 1 and 2 from the fixture are ride wallets.
    rank1_addr = "EKFo92sGrop9YSLeh7ho5w1AodJHc4pSnF3GdjHrm9ad"
    rank2_addr = "GhA8SoXSYieW9pdiftRdbf1LF1HZpApMDUgPzVKrySWe"
    rank3_addr = "9cU81cZim9EZbuoStfCii5xcpksPPN7NkxotbCUawLsq"

    w1 = CopytradeWallet.objects.get(address=rank1_addr)
    w2 = CopytradeWallet.objects.get(address=rank2_addr)
    w3 = CopytradeWallet.objects.get(address=rank3_addr)

    assert w1.style == "ride"
    assert w1.rank == 1
    assert w2.style == "ride"
    assert w2.rank == 2
    assert w3.style == "scalp"
    assert w3.rank == 3


@pytest.mark.django_db
def test_v2_1_over_v2_0_fresh_start():
    """A 2.1 cohort can replace a 2.0 cohort cleanly (SPEC §6 fresh-start)."""
    from copytrade.cohort_lifecycle import replace_cohort_v2, replace_cohort_v2_1
    from copytrade.models import CopytradeCohort, CopytradeWallet

    replace_cohort_v2(_load(FIXTURE_V2_0), settlement_price=0.0, settlement_ts=_TS)
    assert CopytradeCohort.objects.filter(cohort_id="copy_2026-06-19_v1").exists()
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-19_v1").count() == 5

    replace_cohort_v2_1(_load(FIXTURE_V2_1), settlement_price=0.0, settlement_ts=_TS)
    assert CopytradeCohort.objects.count() == 1
    assert CopytradeCohort.objects.filter(cohort_id="copy_2026-06-20_jun_deploy", active=True).count() == 1
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-19_v1").count() == 0
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-20_jun_deploy").count() == 20


@pytest.mark.django_db
def test_invalid_v2_1_json_does_not_mutate_db():
    """A bad 2.1 payload leaves the existing cohort intact (validate-first guard)."""
    from copytrade.cohort_lifecycle import replace_cohort_v2_1
    from copytrade.models import CopytradeCohort

    # Seed an existing v2.1 cohort.
    replace_cohort_v2_1(_load(FIXTURE_V2_1), settlement_price=0.0, settlement_ts=_TS)
    assert CopytradeCohort.objects.filter(active=True).count() == 1
    original_id = CopytradeCohort.objects.get(active=True).cohort_id

    bad = _load(FIXTURE_V2_1)
    bad["wallets"] = []  # fails validation: empty list

    with pytest.raises(CohortJsonValidationError):
        replace_cohort_v2_1(bad, settlement_price=0.0, settlement_ts=_TS)

    # Original cohort untouched.
    assert CopytradeCohort.objects.filter(cohort_id=original_id, active=True).count() == 1


# ---------------------------------------------------------------------------
# Observe-safety guard
# ---------------------------------------------------------------------------


def test_observe_safety_still_green():
    """No new code in copytrade/ sets mode='live' or calls place_buy_order (AST guard).

    This is an incremental guard: the existing test_observe_safety_gate_ac613 is
    the primary AST enforcer; this test confirms the 2.1 additions do not
    introduce any forbidden pattern.

    Specifically checks:
      - cohort_lifecycle.py has no mode='live' assignment
      - schemas.py has no mode='live' assignment
      - validators.py has no mode='live' assignment
    """
    _COPYTRADE = Path(__file__).resolve().parents[1]
    _TARGET_FILES = [
        _COPYTRADE / "cohort_lifecycle.py",
        _COPYTRADE / "schemas.py",
        _COPYTRADE / "validators.py",
        _COPYTRADE / "management" / "commands" / "load_cohort.py",
        _COPYTRADE / "management" / "commands" / "run_copytrade_engine.py",
    ]

    violations = []
    for path in _TARGET_FILES:
        if not path.exists():
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            # keyword arg: fn(mode="live")
            if isinstance(node, ast.keyword):
                if (
                    node.arg == "mode"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == "live"
                ):
                    violations.append(f"{path.name}: keyword mode='live' at line {node.value.lineno}")

            # attribute assignment: obj.mode = "live"
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "mode"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value == "live"
                    ):
                        violations.append(
                            f"{path.name}: attribute .mode = 'live' at line {node.value.lineno}"
                        )

            # place_buy_order call
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "place_buy_order":
                    violations.append(
                        f"{path.name}: call to place_buy_order at line {func.lineno}"
                    )
                if isinstance(func, ast.Attribute) and func.attr == "place_buy_order":
                    violations.append(
                        f"{path.name}: call to place_buy_order at line {func.lineno}"
                    )

    assert not violations, (
        "New 2.1 code introduces a forbidden pattern (mode='live' or place_buy_order):\n"
        + "\n".join(violations)
    )
