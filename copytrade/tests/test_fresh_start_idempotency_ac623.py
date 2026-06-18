# ---
# module: copytrade.tests.test_fresh_start_idempotency_ac623
# sprint: sprint-12
# story: US-62 AC-62.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, ast, pathlib,
#   copytrade.cohort_lifecycle, copytrade.models, core.models
# ---
"""AC-62.3: fresh-start wipe is DETERMINISTIC, IDEMPOTENT, and OFFLINE.

Verifies three properties of replace_cohort():

  IDEMPOTENT — running the upload/wipe twice with the same JSON yields the
  same end state (one active cohort, correct wallets, no lingering data).

  DETERMINISTIC — the function never calls datetime.now() / time.time(); all
  timestamps are injected by the caller.  Verified by an AST guard.

  OFFLINE — no firehose or external-network access on the wipe path.
  Verified by an AST guard that cohort_lifecycle.py imports nothing from
  core.* (which would expose raw-lake models).

§6.4.1 / §5 ISOLATION — the purge NEVER touches the raw lake (RawEvent,
Token, PipelineConfig, PipelineState) or any non-copytrade_ table.
Verified by:
  (a) A functional test: RawEvent rows created before the wipe still exist
      after it.
  (b) A functional test: PipelineState is unchanged after the wipe.
  (c) An AST guard: every .delete() call in cohort_lifecycle.py traces back
      to a model name beginning with 'Copytrade' or 'CopyTrade'.
  (d) An AST guard: cohort_lifecycle.py imports no module from core.*

All tests are deterministic (injected settlement_price + settlement_ts);
zero firehose; no datetime.now().
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from copytrade.cohort_lifecycle import replace_cohort
from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LIFECYCLE_SRC = Path(__file__).resolve().parents[1] / "cohort_lifecycle.py"

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

SETTLEMENT_PRICE = 1.5
SETTLEMENT_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

_TRADE_CONFIG = {
    "mode": "observe",
    "sol_size_per_trade": 0.25,
    "take_profit_pct": 200.0,
    "stop_loss_pct": 40.0,
    "exit_before_graduation": True,
    "curve_completion_exit_pct": 90.0,
    "max_hold_seconds": 1800,
    "max_concurrent_positions": 20,
    "copy_only_pumpfun_curve_buys": True,
    "copy_first_buy_only": True,
    "dedupe_token_across_wallets": True,
    "mirror_wallet_sells": False,
}

COHORT_A_JSON = {
    "schema_version": "1.0",
    "cohort_id": "cohort-ac623-A",
    "created_at": "2026-06-17",
    "description": "Cohort A for AC-62.3",
    "trade_config": _TRADE_CONFIG,
    "wallets": [
        {"address": "WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"},
        {"address": "WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2"},
    ],
}

COHORT_B_JSON = {
    "schema_version": "1.0",
    "cohort_id": "cohort-ac623-B",
    "created_at": "2026-06-18",
    "description": "Cohort B for AC-62.3",
    "trade_config": _TRADE_CONFIG,
    "wallets": [
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"},
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2"},
        {"address": "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3"},
    ],
}

ENTRY_TS = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(engine_on=True, active_cohort_id="cohort-ac623-A"):
    CopyTradeSettings.get()
    CopyTradeSettings.objects.filter(pk=1).update(
        engine_on=engine_on,
        active_cohort_id=active_cohort_id,
    )
    return CopyTradeSettings.get()


def _make_open_position(cohort_id, mint_suffix="1"):
    return CopytradePosition.objects.create(
        cohort_id=cohort_id,
        mint=f"TokenMint{'x' * (40 - len(mint_suffix))}{mint_suffix}",
        trigger_wallet="WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
    )


def _snapshot():
    """Capture a tuple of key scalar end-state values for comparison."""
    settings = CopyTradeSettings.get()
    return {
        "cohort_count": CopytradeCohort.objects.count(),
        "active_cohort_id": settings.active_cohort_id,
        "wallet_count": CopytradeWallet.objects.count(),
        "position_count": CopytradePosition.objects.count(),
        "pnl_count": CopytradePnlByWallet.objects.count(),
        "engine_on": settings.engine_on,
        "active_count": CopytradeCohort.objects.filter(active=True).count(),
        "wallet_ids": sorted(
            CopytradeWallet.objects.values_list("address", flat=True)
        ),
    }


# ---------------------------------------------------------------------------
# IDEMPOTENCY — running the upload/wipe twice yields the same end state
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_idempotency_upload_twice_same_active_cohort_id():
    """Uploading cohort B twice → active_cohort_id is 'cohort-ac623-B' both times."""
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    state_1 = CopyTradeSettings.get().active_cohort_id

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    state_2 = CopyTradeSettings.get().active_cohort_id

    assert state_1 == "cohort-ac623-B"
    assert state_2 == "cohort-ac623-B"


@pytest.mark.django_db
def test_idempotency_upload_twice_exactly_one_active_cohort():
    """Uploading cohort B twice → exactly one active cohort after each call."""
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopytradeCohort.objects.filter(active=True).count() == 1

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopytradeCohort.objects.filter(active=True).count() == 1


@pytest.mark.django_db
def test_idempotency_upload_twice_same_wallet_count():
    """Uploading cohort B twice → same wallet count (3) after each call."""
    _make_settings()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    count_1 = CopytradeWallet.objects.filter(cohort_id="cohort-ac623-B").count()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    count_2 = CopytradeWallet.objects.filter(cohort_id="cohort-ac623-B").count()

    assert count_1 == 3
    assert count_2 == 3


@pytest.mark.django_db
def test_idempotency_upload_twice_no_positions_linger():
    """Uploading cohort B twice → zero open positions after each call."""
    _make_settings()
    # Seed an open position on cohort A before first upload
    replace_cohort(COHORT_A_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    _make_open_position(cohort_id="cohort-ac623-A", mint_suffix="1")

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopytradePosition.objects.filter(status=CopytradePosition.STATUS_OPEN).count() == 0

    # Open a position on B, then upload B again — it should also be settled+purged
    _make_open_position(cohort_id="cohort-ac623-B", mint_suffix="1")
    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopytradePosition.objects.filter(status=CopytradePosition.STATUS_OPEN).count() == 0


@pytest.mark.django_db
def test_idempotency_full_end_state_snapshot_matches():
    """Full end-state snapshot after first and second upload of cohort B is identical.

    Compares cohort count, wallet addresses, position count, pnl count,
    engine_on, and active_count — all the functional state that a caller would
    observe.  uploaded_at (auto_now_add) is intentionally excluded since it
    records wall-clock and is expected to differ.
    """
    _make_settings()
    # Seed some cohort A data so the first upload has something to wipe.
    replace_cohort(COHORT_A_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    snapshot_1 = _snapshot()

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    snapshot_2 = _snapshot()

    assert snapshot_1 == snapshot_2, (
        f"End state differs between first and second upload:\n"
        f"  first:  {snapshot_1}\n"
        f"  second: {snapshot_2}"
    )


@pytest.mark.django_db
def test_idempotency_engine_off_after_both_uploads():
    """engine_on is False after both the first and second upload."""
    _make_settings(engine_on=True)

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopyTradeSettings.get().engine_on is False

    # Manually turn engine back on, then upload again — must be OFF again.
    CopyTradeSettings.objects.filter(pk=1).update(engine_on=True)

    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)
    assert CopyTradeSettings.get().engine_on is False


# ---------------------------------------------------------------------------
# §6.4.1 / §5 ISOLATION — raw lake and non-copytrade_ tables untouched
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_raw_lake_rawevent_rows_survive_wipe():
    """RawEvent rows in the raw lake survive a full cohort wipe (§6.4.1).

    The purge must NEVER touch core.RawEvent.
    """
    from core.models import RawEvent

    # Seed two raw events before the wipe.
    RawEvent.objects.create(payload={"type": "test_ac623", "index": 1})
    RawEvent.objects.create(payload={"type": "test_ac623", "index": 2})
    before_count = RawEvent.objects.count()

    _make_settings()
    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    after_count = RawEvent.objects.count()
    assert after_count == before_count, (
        f"replace_cohort deleted {before_count - after_count} RawEvent row(s) — "
        "§6.4.1 isolation violated: the wipe must never touch the raw lake."
    )


@pytest.mark.django_db
def test_pipeline_state_untouched_after_wipe():
    """PipelineState flags are unchanged after a cohort wipe (§5 isolation).

    replace_cohort must not touch any non-copytrade_ table.
    """
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.scoring_enabled = True
    state.save()

    _make_settings()
    replace_cohort(COHORT_B_JSON, SETTLEMENT_PRICE, SETTLEMENT_TS)

    state.refresh_from_db()
    assert state.firehose_active is True, (
        "replace_cohort mutated PipelineState.firehose_active — §5 isolation violated."
    )
    assert state.scoring_enabled is True, (
        "replace_cohort mutated PipelineState.scoring_enabled — §5 isolation violated."
    )


# ---------------------------------------------------------------------------
# AST guards
# ---------------------------------------------------------------------------


def _root_name(node: ast.AST) -> str | None:
    """Walk down an AST expression chain to find the root Name identifier.

    Handles chained calls like ClassName.objects.filter().delete() by
    recursively descending through Attribute.value and Call.func.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    if isinstance(node, ast.Call):
        return _root_name(node.func)
    return None


def test_ast_lifecycle_no_core_imports():
    """AST guard: cohort_lifecycle.py must NOT import anything from core.*.

    No core.* import → cohort_lifecycle cannot access RawEvent, Token,
    PipelineConfig, PipelineState, or any other raw-lake model (§6.4.1/§5).
    """
    source = LIFECYCLE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("core"):
                violations.append(
                    f"line {node.lineno}: from {node.module} import ..."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("core"):
                    violations.append(f"line {node.lineno}: import {alias.name}")

    assert not violations, (
        "cohort_lifecycle.py imports from core.* — §6.4.1/§5 isolation violated; "
        "the purge path must have zero access to raw-lake models:\n"
        + "\n".join(violations)
    )


def test_ast_delete_calls_only_on_copytrade_models():
    """AST guard: every .delete() call in cohort_lifecycle.py traces back to a
    Copytrade* or CopyTrade* model name (§6.4.1 — purge touches only copytrade_ tables).
    """
    source = LIFECYCLE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match: <expr>.delete()
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "delete"):
            continue
        root = _root_name(node.func.value)
        if root is None:
            continue
        if not (root.startswith("Copytrade") or root.startswith("CopyTrade")):
            violations.append(
                f"line {node.lineno}: .delete() called on {root!r} — "
                "must only delete from copytrade_* tables"
            )

    assert not violations, (
        "cohort_lifecycle.py calls .delete() on non-copytrade_ models — "
        "§6.4.1 isolation violated:\n" + "\n".join(violations)
    )


def test_ast_lifecycle_no_datetime_now_calls():
    """AST guard: cohort_lifecycle.py never calls datetime.now() / datetime.utcnow()
    / time.time() — all timestamps are injected (DETERMINISTIC, Principle #7).
    """
    source = LIFECYCLE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in ("now", "utcnow") and isinstance(func.value, ast.Name):
                if func.value.id == "datetime":
                    violations.append(f"line {node.lineno}: datetime.{func.attr}()")
            elif (
                func.attr == "time"
                and isinstance(func.value, ast.Name)
                and func.value.id == "time"
            ):
                violations.append(f"line {node.lineno}: time.time()")

    assert not violations, (
        "cohort_lifecycle.py has forbidden direct time calls — "
        "timestamps must be injected for determinism:\n" + "\n".join(violations)
    )


def test_ast_lifecycle_no_network_imports():
    """AST guard: cohort_lifecycle.py imports nothing from network/firehose libs
    (aiohttp, requests, websockets, helius, birdeye) — wipe is OFFLINE.
    """
    _FORBIDDEN = {"aiohttp", "requests", "websockets", "helius", "birdeye", "httpx"}
    source = LIFECYCLE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            root_mod = node.module.split(".")[0]
            if root_mod in _FORBIDDEN:
                violations.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _FORBIDDEN:
                    violations.append(f"line {node.lineno}: import {alias.name}")

    assert not violations, (
        "cohort_lifecycle.py imports from network/firehose libraries — "
        "the wipe must be OFFLINE:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# H1 import trap
# ---------------------------------------------------------------------------


def test_h1_import_trap():
    """Import trap: replace_cohort must be importable from copytrade.cohort_lifecycle.

    Deleting or renaming replace_cohort fails pytest collection here.
    """
    from copytrade.cohort_lifecycle import replace_cohort as _fn  # noqa: F401

    assert _fn is not None
