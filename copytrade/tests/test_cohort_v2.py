# ---
# module: copytrade.tests.test_cohort_v2
# sprint: cutover (copy-trade live)
# story: cohort-2.0-schema
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django
# ---
"""Tests for the cohort-2.0 schema, validator, and fresh-start lifecycle.

Drives the REAL cohort fixture (copy_2026-06-19_v1) so the parser is exercised
against the exact bytes the engine will consume in production.
"""
import json
import os

import pytest

from copytrade.schemas import (
    CohortV2,
    MirrorWalletSellExit,
    OurTrailingExit,
)
from copytrade.validators import (
    CohortJsonValidationError,
    validate_cohort_any,
    validate_cohort_v2,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cohort_2026-06-19_v1.json")


def _load_fixture() -> dict:
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Schema parsing (no DB)
# ---------------------------------------------------------------------------


def test_parses_real_cohort_fixture():
    cohort = CohortV2.model_validate(_load_fixture())
    assert cohort.schema_version == "copytrade-2.0"
    assert cohort.cohort_id == "copy_2026-06-19_v1"
    assert cohort.global_.mode == "observe"
    assert cohort.global_.usd_size_per_trade == 25.0
    assert cohort.global_.trigger.min_trigger_buy_usd == 250.0
    assert cohort.global_.trigger.pump_fun_only is True
    assert cohort.global_.max_concurrent_positions == 30


def test_two_heads_with_distinct_exits():
    cohort = CohortV2.model_validate(_load_fixture())
    by_id = {s.id: s for s in cohort.strategies}
    assert set(by_id) == {"consistent_scalp", "moonshot"}

    scalp = by_id["consistent_scalp"]
    assert isinstance(scalp.exit, MirrorWalletSellExit)
    assert scalp.exit.type == "mirror_wallet_sell"
    assert scalp.exit.max_hold_seconds == 86400
    assert scalp.exit.hard_stop_loss_pct is None  # intentionally no stop
    assert len(scalp.wallets) == 2

    moon = by_id["moonshot"]
    assert isinstance(moon.exit, OurTrailingExit)
    assert moon.exit.type == "our_trailing"
    assert moon.exit.stop_loss_pct == 50.0
    assert moon.exit.trailing_giveback_pct == 40.0
    assert moon.exit.take_profit_pct == 900.0
    assert moon.exit.max_hold_seconds is None
    assert len(moon.wallets) == 3


def test_wallet_to_strategy_map():
    cohort = CohortV2.model_validate(_load_fixture())
    mapping = cohort.wallet_to_strategy()
    assert mapping["5C1JAeFwk9CneMCu3Qa4vNuJh8Ny6tofevQUaScp2Fxn"] == "consistent_scalp"
    assert mapping["EKFo92sGrop9YSLeh7ho5w1AodJHc4pSnF3GdjHrm9ad"] == "moonshot"
    assert len(mapping) == 5


def test_validator_dispatch_picks_v2():
    cohort = validate_cohort_any(_load_fixture())
    assert isinstance(cohort, CohortV2)


def test_validator_rejects_unknown_schema_version():
    bad = _load_fixture()
    bad["schema_version"] = "9.9"
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_any(bad)


def test_rejects_unknown_exit_type():
    bad = _load_fixture()
    bad["strategies"][0]["exit"]["type"] = "nonsense"
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2(bad)


def test_requires_at_least_one_enabled_strategy():
    bad = _load_fixture()
    for s in bad["strategies"]:
        s["enabled"] = False
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2(bad)


def test_rejects_duplicate_strategy_ids():
    bad = _load_fixture()
    bad["strategies"][1]["id"] = bad["strategies"][0]["id"]
    with pytest.raises(CohortJsonValidationError):
        validate_cohort_v2(bad)


# ---------------------------------------------------------------------------
# Fresh-start lifecycle (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_replace_cohort_v2_loads_real_cohort():
    from datetime import datetime, timezone

    from copytrade.cohort_lifecycle import replace_cohort_v2
    from copytrade.models import (
        CopytradeCohort,
        CopyTradeSettings,
        CopytradeWallet,
    )

    ts = datetime(2026, 6, 19, tzinfo=timezone.utc)
    cohort = replace_cohort_v2(_load_fixture(), settlement_price=0.0, settlement_ts=ts)

    assert cohort.cohort_id == "copy_2026-06-19_v1"
    assert cohort.active is True

    # Exactly one active cohort.
    assert CopytradeCohort.objects.filter(active=True).count() == 1

    # Settings reflect the global block.
    settings = CopyTradeSettings.get()
    assert settings.active_cohort_id == "copy_2026-06-19_v1"
    assert settings.mode == "observe"
    assert settings.usd_size_per_trade == 25.0
    assert settings.min_trigger_buy_usd == 250.0
    assert settings.engine_on is False  # stays OFF after load
    assert settings.mirror_wallet_sells is False  # global flag stays off (per-head exit)

    # All 5 wallets subscribed, each tagged with its head.
    wallets = CopytradeWallet.objects.filter(cohort_id="copy_2026-06-19_v1")
    assert wallets.count() == 5
    scalp = set(wallets.filter(strategy_id="consistent_scalp").values_list("address", flat=True))
    moon = set(wallets.filter(strategy_id="moonshot").values_list("address", flat=True))
    assert len(scalp) == 2
    assert len(moon) == 3

    # The full 2.0 dict is stored verbatim for the engine to read per-head exits.
    assert cohort.trade_config["schema_version"] == "copytrade-2.0"
    assert len(cohort.trade_config["strategies"]) == 2


@pytest.mark.django_db
def test_replace_cohort_v2_is_fresh_start():
    """A second load wipes the first cohort's rows entirely (SPEC §6)."""
    from datetime import datetime, timezone

    from copytrade.cohort_lifecycle import replace_cohort_v2
    from copytrade.models import CopytradeCohort, CopytradeWallet

    ts = datetime(2026, 6, 19, tzinfo=timezone.utc)
    replace_cohort_v2(_load_fixture(), settlement_price=0.0, settlement_ts=ts)

    # Load a second, differently-identified cohort.
    second = _load_fixture()
    second["cohort_id"] = "copy_2026-07-03_v1"
    replace_cohort_v2(second, settlement_price=0.0, settlement_ts=ts)

    assert CopytradeCohort.objects.count() == 1
    assert CopytradeCohort.objects.filter(active=True).first().cohort_id == "copy_2026-07-03_v1"
    # No wallets remain from the old cohort.
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-06-19_v1").count() == 0
    assert CopytradeWallet.objects.filter(cohort_id="copy_2026-07-03_v1").count() == 5
