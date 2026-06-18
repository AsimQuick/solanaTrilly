# ---
# module: trading.tests.test_sandbox_isolation_ac673
# sprint: sprint-13
# story: US-67 AC-67.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.replay_harness, trading.models, django.db.models.signals, django.db, pytest, ast
# ---
"""AC-67.3 — Replay sandbox isolation guard (§11.3).

Verifies at the ORM/transaction level that no replay write touches live-schema
tables, as required by PRD §11.3 and the sprint-13 Definition of Done.

The guard operates at two complementary levels:

  ORM level   — Django pre_save/post_save signals on the live Position model:
                fired before any SQL is generated, catches misdirected writes
                regardless of whether they are later rolled back.

  SQL level   — Django connection.execute_wrapper intercepts every statement
                executed against the database and asserts no INSERT/UPDATE/DELETE
                appears for 'trading_positions'.

Together these provide a stronger guarantee than count-based checks (which cannot
distinguish a write-then-rollback from no write at all).

Test sections
-------------
  §1  ORM-level signal trap
        test_replay_does_not_trigger_position_pre_save
        test_replay_does_not_trigger_position_post_save

  §2  SQL-level execute_wrapper interception
        test_replay_sql_no_writes_to_live_table

  §3  Static table-name divergence (schema separation)
        test_sandbox_and_live_table_names_differ
        test_replay_position_db_table_constant
        test_live_position_db_table_constant

  §4  Positive sandbox assertion
        test_replay_writes_to_sandbox_table

  §5  Module-level import isolation (offline / zero-firehose)
        test_replay_harness_has_no_toplevel_position_import
        test_replay_harness_has_no_toplevel_live_source_import

Zero firehose — all tests are offline/deterministic; no network, no Birdeye,
no Helius.  DB tests write only to the isolated sandbox table.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TAPE_FIXTURE = _FIXTURES_DIR / "replay_tape_day_ac671.json"
_HARNESS_SRC = Path(__file__).parent.parent / "replay_harness.py"

_LIVE_TABLE = "trading_positions"
_SANDBOX_TABLE = "trading_replay_positions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_tape() -> dict:
    with open(_TAPE_FIXTURE) as f:
        return json.load(f)


def _make_harness():
    from trading.replay_harness import DayReplayHarness
    from trading.schemas import TradingConfig

    return DayReplayHarness(
        config=TradingConfig(),
        predictor=None,  # constant 1.0 — all tokens qualify
        score_threshold=0.5,
        score_delay_s=30.0,
        size_sol=0.1,
        sol_usd=140.0,
    )


# ---------------------------------------------------------------------------
# §1  ORM-level signal trap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_replay_does_not_trigger_position_pre_save():
    """No replay run may trigger pre_save on the live Position model.

    pre_save fires at the Django ORM layer — before SQL is generated — so this
    catches misdirected writes regardless of whether a transaction is later
    rolled back.  It is a stronger guard than count-based before/after checks.
    """
    from django.db.models.signals import pre_save

    from trading.models import Position

    violations: list[str] = []

    def _trap(sender, instance, **kwargs):
        violations.append(
            f"pre_save fired on '{sender._meta.db_table}' (pk={instance.pk!r})"
        )

    pre_save.connect(_trap, sender=Position)
    try:
        tape = _load_tape()
        harness = _make_harness()
        harness.run(tape["tape"], replay_run_id="signal-guard-pre-save")
    finally:
        pre_save.disconnect(_trap, sender=Position)

    assert not violations, (
        f"§11.3 VIOLATION — replay triggered pre_save on live Position model "
        f"({len(violations)} time(s)):\n" + "\n  ".join(violations)
    )


@pytest.mark.django_db
def test_replay_does_not_trigger_position_post_save():
    """No replay run may trigger post_save on the live Position model.

    post_save fires after the row is committed — confirming that no live-table
    write completed, not merely that it was attempted.
    """
    from django.db.models.signals import post_save

    from trading.models import Position

    violations: list[str] = []

    def _trap(sender, instance, created, **kwargs):
        action = "created" if created else "updated"
        violations.append(
            f"post_save fired on '{sender._meta.db_table}' ({action}, pk={instance.pk!r})"
        )

    post_save.connect(_trap, sender=Position)
    try:
        tape = _load_tape()
        harness = _make_harness()
        harness.run(tape["tape"], replay_run_id="signal-guard-post-save")
    finally:
        post_save.disconnect(_trap, sender=Position)

    assert not violations, (
        f"§11.3 VIOLATION — replay triggered post_save on live Position model "
        f"({len(violations)} time(s)):\n" + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# §2  SQL-level execute_wrapper interception
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_replay_sql_no_writes_to_live_table():
    """No SQL emitted by a replay run may INSERT/UPDATE/DELETE 'trading_positions'.

    Uses Django's connection.execute_wrapper to intercept every SQL statement
    at the database driver layer — a transaction-level guarantee that the live
    table is never touched, independent of ORM state.

    This is complementary to the signal guard in §1: both must pass.
    """
    from django.db import connection

    _WRITE_KEYWORDS = ("insert into", "update ", "delete from")
    sql_violations: list[str] = []

    def _intercept(execute, sql, params, many, context):
        sql_lower = sql.lower()
        if any(kw in sql_lower for kw in _WRITE_KEYWORDS):
            if _LIVE_TABLE in sql_lower:
                sql_violations.append(sql.strip()[:200])  # cap length for readability
        return execute(sql, params, many, context)

    tape = _load_tape()
    harness = _make_harness()

    with connection.execute_wrapper(_intercept):
        harness.run(tape["tape"], replay_run_id="sql-guard-check")

    assert not sql_violations, (
        f"§11.3 VIOLATION — replay emitted {len(sql_violations)} SQL write(s) "
        f"to '{_LIVE_TABLE}':\n" + "\n  ".join(sql_violations[:5])
    )


# ---------------------------------------------------------------------------
# §3  Static table-name divergence (schema separation)
# ---------------------------------------------------------------------------


def test_sandbox_and_live_table_names_differ():
    """ReplayPosition and Position must use strictly different db_table names.

    §11.3 requires the replay sandbox to be a SEPARATE schema from the live
    tables.  If the two models share a table name, every replay write would
    corrupt the live data.
    """
    from trading.models import Position, ReplayPosition

    live = Position._meta.db_table
    sandbox = ReplayPosition._meta.db_table

    assert live != sandbox, (
        f"§11.3 VIOLATION — live and sandbox models share the same db_table "
        f"name: {live!r}.  They must be distinct."
    )


def test_replay_position_db_table_constant():
    """ReplayPosition.db_table is the expected sandbox constant."""
    from trading.models import ReplayPosition

    assert ReplayPosition._meta.db_table == _SANDBOX_TABLE, (
        f"ReplayPosition.db_table changed from expected '{_SANDBOX_TABLE}' "
        f"to '{ReplayPosition._meta.db_table}'. "
        "Renaming the sandbox table is a §11.3 schema-isolation change — "
        "update the constant and all dependent fixtures/tests together."
    )


def test_live_position_db_table_constant():
    """Position.db_table is the expected live-table constant."""
    from trading.models import Position

    assert Position._meta.db_table == _LIVE_TABLE, (
        f"Position.db_table changed from expected '{_LIVE_TABLE}' "
        f"to '{Position._meta.db_table}'. "
        "Renaming the live table is a breaking change — update the constant "
        "and all dependent tests together."
    )


# ---------------------------------------------------------------------------
# §4  Positive sandbox assertion
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_replay_writes_to_sandbox_table():
    """Harness DOES write rows to the sandbox table, confirming the path is live.

    This positive assertion rules out the degenerate case where the harness
    silently skips all writes (which would also satisfy the isolation guards
    vacuously).  At least one ReplayPosition row must be created.
    """
    from trading.models import ReplayPosition

    tape = _load_tape()
    harness = _make_harness()
    run_id = "sandbox-positive-check"

    harness.run(tape["tape"], replay_run_id=run_id)

    count = ReplayPosition.objects.filter(replay_run_id=run_id).count()
    assert count >= 1, (
        f"Harness wrote 0 rows to '{_SANDBOX_TABLE}' for run_id={run_id!r}. "
        "The isolation guard would pass vacuously — the harness must write "
        "at least one ReplayPosition row per successful run."
    )


# ---------------------------------------------------------------------------
# §5  Module-level import isolation (offline / zero-firehose)
# ---------------------------------------------------------------------------


def test_replay_harness_has_no_toplevel_position_import():
    """trading.replay_harness must NOT import Position at module level.

    Principle #7 (live/replay seam): the core path must not have a concrete
    live-table import at module scope.  The harness defers Position-related
    imports to within run() via a local import — any top-level
    'from trading.models import Position' would couple the harness to the live
    table at import time and break the DataSource seam.

    Uses AST inspection (ast.parse) of the source file — no import side-effects.
    """
    assert _HARNESS_SRC.exists(), (
        f"replay_harness.py not found at expected path: {_HARNESS_SRC}"
    )
    src = _HARNESS_SRC.read_text()
    tree = ast.parse(src)

    # Only check module-level (top-level) statements — not inside functions/classes.
    # tree.body contains only the direct children of the module node.
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "trading.models":
                imported_names = [alias.name for alias in node.names]
                assert "Position" not in imported_names, (
                    "trading.replay_harness imports 'Position' from 'trading.models' "
                    "at module level.  This couples the harness to the live table "
                    "(§11.3 / Principle #7).  Move the import inside run()."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "trading.models", (
                    "trading.replay_harness imports 'trading.models' at module level. "
                    "Move any trading.models usage inside run() as a local import."
                )


def test_replay_harness_has_no_toplevel_live_source_import():
    """trading.replay_harness must NOT import live data sources at module level.

    The harness is offline/replay by construction (zero firehose, §11.3 / DoD).
    Any top-level import of live data sources (core.live_source, requests,
    aiohttp, birdeye, helius, websockets) would make the module unusable in an
    offline context and risk accidental network activation.

    Uses AST inspection — no import side-effects.
    """
    assert _HARNESS_SRC.exists(), (
        f"replay_harness.py not found at expected path: {_HARNESS_SRC}"
    )
    src = _HARNESS_SRC.read_text()
    tree = ast.parse(src)

    _LIVE_PREFIXES = (
        "core.live_source",
        "requests",
        "aiohttp",
        "httpx",
        "birdeye",
        "helius",
        "websockets",
        "solana",          # Solana RPC client
        "anchorpy",        # Anchor client (mainnet)
    )

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in _LIVE_PREFIXES:
                assert not node.module.startswith(prefix), (
                    f"trading.replay_harness imports live dependency "
                    f"'{node.module}' at module level — zero-firehose violation "
                    f"(§11.3 / DoD).  Remove or defer the import."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in _LIVE_PREFIXES:
                    assert not alias.name.startswith(prefix), (
                        f"trading.replay_harness imports live dependency "
                        f"'{alias.name}' at module level — zero-firehose violation."
                    )
