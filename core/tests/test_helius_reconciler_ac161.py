# ---
# module: core.tests.test_helius_reconciler_ac161
# sprint: sprint-4
# story: US-16 AC-16.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.helius_reconciler, core.detection.consumer,
#   core.models, core.resolver, core.clock, core.replay_source,
#   asyncio, json, pathlib, pytest, ast
# ---
"""AC-16.1 — Helius 'migrate' gap-recovery reconciler backstop behind DataSource seam.

Tests:
  1. test_helius_recovers_dropped_meme_event
       MEME stream omits MINT_A; Helius stream carries both MINT_A + MINT_B.
       After MEME consumer runs, MINT_A is absent from DB.
       After reconciler runs, MINT_A is present — recovered by the backstop.
       MINT_B has exactly 1 row (no duplicate from reconciler).

  2. test_both_mints_exist_after_both_consumers
       Run MEME consumer then Helius reconciler.
       Both MINT_A and MINT_B have exactly 1 Token row each (total count == 2).

  3. test_no_duplicate_when_both_streams_carry_mint
       MEME consumer creates MINT_B; reconciler also sees MINT_B.
       get_or_create (not update_or_create) ensures still exactly 1 row for MINT_B.

  4. test_helius_only_creates_token_when_meme_missed
       Run Helius reconciler alone (no MEME consumer).
       Both MINT_A and MINT_B are created by the reconciler alone (count == 2).

  5. test_reconciler_static_analysis_no_concrete_source_import
       AST-scan core/detection/helius_reconciler.py and assert no import of
       LiveSource, ReplaySource, core.live_source, or core.replay_source.

  6. test_reconciler_subscription_constants
       Import PUMP_PROGRAM, MIGRATE_INSTRUCTION, MAX_TX_VERSION from the reconciler
       and assert their expected values match the PRD §3.3 specification.
"""
import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.models import PipelineConfig, Token
from core.resolver import get_active_config, invalidate_active_config_cache

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
MEME_FIXTURE = FIXTURE_DIR / "meme_stream_ac161.json"
HELIUS_FIXTURE = FIXTURE_DIR / "helius_migrate_ac161.json"

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mint addresses from the fixture
MINT_A = "AC161MINTalpha11111111111111111111111111111"  # Helius only
MINT_B = "AC161MINTbeta222222222222222222222222222222"  # both streams

# ---------------------------------------------------------------------------
# Valid config sections (mirrors test_detection_consumer_ac154.py convention)
# ---------------------------------------------------------------------------

_VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
_VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
_VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
_VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}
_DETECTION = {
    "filter": {"source": "pump_dot_fun", "graduated": True},
    "prestage_progress_pct": 95.0,
    "dedupe_window_s": 60,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_config() -> PipelineConfig:
    """Create an is_active PipelineConfig for AC-16.1 tests."""
    return PipelineConfig.objects.create(
        version=1,
        label="test-ac161",
        is_active=True,
        detection=_DETECTION,
        tape=_VALID_TAPE,
        scoring=_VALID_SCORING,
        outcome=_VALID_OUTCOME,
        trading=_VALID_TRADING,
    )


def _load_meme_events() -> list[dict]:
    """Load the events array from the MEME stream fixture."""
    with MEME_FIXTURE.open() as fh:
        data = json.load(fh)
    return data["events"]


def _load_helius_events() -> list[dict]:
    """Load the events array from the Helius migrate fixture."""
    with HELIUS_FIXTURE.open() as fh:
        data = json.load(fh)
    return data["events"]


async def _run_meme_consumer(events: list[dict]) -> None:
    """Drive DetectionConsumer with the given MEME events via ReplaySource + VirtualClock."""
    from core.clock import VirtualClock
    from core.detection.consumer import DetectionConsumer
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    consumer = DetectionConsumer(ReplaySource(events), VirtualClock(t0), config_fn=get_active_config)
    await consumer.run()


async def _run_helius_reconciler(events: list[dict]) -> None:
    """Drive MigrateReconciler with the given Helius events via ReplaySource + VirtualClock."""
    from core.clock import VirtualClock
    from core.detection.helius_reconciler import MigrateReconciler
    from core.replay_source import ReplaySource

    t0 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    reconciler = MigrateReconciler(ReplaySource(events), VirtualClock(t0))
    await reconciler.run()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_helius_recovers_dropped_meme_event() -> None:
    """MINT_A is absent from the MEME stream but present in Helius — reconciler recovers it.

    Scenario:
      - MEME stream carries only MINT_B (MINT_A was dropped).
      - After MEME consumer runs: MINT_A is NOT in DB; MINT_B IS in DB.
      - Helius stream carries both MINT_A and MINT_B.
      - After reconciler runs: MINT_A IS now in DB (recovered); MINT_B count stays 1.
    """
    invalidate_active_config_cache()
    _make_active_config()

    # Run MEME consumer — only sees MINT_B
    asyncio.run(_run_meme_consumer(_load_meme_events()))

    assert not Token.objects.filter(mint=MINT_A).exists(), (
        "MINT_A should NOT exist in DB after MEME consumer (MEME stream omits it)"
    )
    assert Token.objects.filter(mint=MINT_B).exists(), (
        "MINT_B should exist in DB after MEME consumer"
    )

    # Run Helius reconciler — sees both MINT_A and MINT_B
    asyncio.run(_run_helius_reconciler(_load_helius_events()))

    assert Token.objects.filter(mint=MINT_A).exists(), (
        "MINT_A should exist in DB after Helius reconciler (gap-recovery backstop)"
    )
    assert Token.objects.filter(mint=MINT_B).count() == 1, (
        "MINT_B should still have exactly 1 Token row after reconciler (no duplicate)"
    )


@pytest.mark.django_db(transaction=True)
def test_both_mints_exist_after_both_consumers() -> None:
    """Running MEME consumer then Helius reconciler yields exactly 1 row each for MINT_A and MINT_B.

    Total Token count must be exactly 2.
    """
    invalidate_active_config_cache()
    _make_active_config()

    asyncio.run(_run_meme_consumer(_load_meme_events()))
    asyncio.run(_run_helius_reconciler(_load_helius_events()))

    assert Token.objects.filter(mint=MINT_A).count() == 1, (
        f"Expected exactly 1 Token row for MINT_A, got "
        f"{Token.objects.filter(mint=MINT_A).count()}"
    )
    assert Token.objects.filter(mint=MINT_B).count() == 1, (
        f"Expected exactly 1 Token row for MINT_B, got "
        f"{Token.objects.filter(mint=MINT_B).count()}"
    )
    total = Token.objects.count()
    assert total == 2, f"Expected total Token count == 2, got {total}"


@pytest.mark.django_db(transaction=True)
def test_no_duplicate_when_both_streams_carry_mint() -> None:
    """Both MEME and Helius streams carry MINT_B — reconciler must NOT create a duplicate.

    MigrateReconciler uses get_or_create (not update_or_create), so if the MEME
    consumer already created the MINT_B row, the reconciler leaves it untouched.
    The result is exactly 1 row for MINT_B.
    """
    invalidate_active_config_cache()
    _make_active_config()

    # MEME consumer creates MINT_B
    asyncio.run(_run_meme_consumer(_load_meme_events()))
    # Helius reconciler also sees MINT_B — must NOT create a second row
    asyncio.run(_run_helius_reconciler(_load_helius_events()))

    count = Token.objects.filter(mint=MINT_B).count()
    assert count == 1, (
        f"Expected exactly 1 Token row for MINT_B when both streams carry it, got {count}"
    )


@pytest.mark.django_db(transaction=True)
def test_helius_only_creates_token_when_meme_missed() -> None:
    """Running the Helius reconciler alone (no MEME consumer) creates both MINT_A and MINT_B.

    The reconciler fills ALL gaps when no MEME consumer has run at all.
    Both mints should be created; total count == 2.
    """
    asyncio.run(_run_helius_reconciler(_load_helius_events()))

    assert Token.objects.filter(mint=MINT_A).exists(), (
        "MINT_A should be created by Helius reconciler when running alone"
    )
    assert Token.objects.filter(mint=MINT_B).exists(), (
        "MINT_B should be created by Helius reconciler when running alone"
    )
    total = Token.objects.count()
    assert total == 2, f"Expected total Token count == 2 when reconciler runs alone, got {total}"


def test_reconciler_static_analysis_no_concrete_source_import() -> None:
    """Static-analysis guard: helius_reconciler.py must not import any concrete source class.

    AST-scans core/detection/helius_reconciler.py and fails if it imports
    LiveSource, ReplaySource, core.live_source, or core.replay_source.
    The reconciler must depend only on the DataSource interface (Principle #7).
    """
    reconciler_path = REPO_ROOT / "core" / "detection" / "helius_reconciler.py"
    assert reconciler_path.exists(), f"Expected {reconciler_path} to exist"

    source_text = reconciler_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    _forbidden_classes = {"LiveSource", "ReplaySource"}
    _forbidden_modules = {"core.live_source", "core.replay_source"}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _forbidden_modules:
                violations.append(f"'from {module} import ...' found in helius_reconciler.py")
                continue
            for alias in node.names:
                if alias.name in _forbidden_classes:
                    violations.append(
                        f"'from {module} import {alias.name}' found in helius_reconciler.py"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _forbidden_modules:
                    violations.append(f"'import {alias.name}' found in helius_reconciler.py")

    assert not violations, (
        "helius_reconciler.py must NOT import any concrete DataSource class (Principle #7).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_reconciler_subscription_constants() -> None:
    """Module-level subscription constants match the PRD §3.3 specification.

    These constants are exported for the live HeliusSource wiring layer.
    Verifies the exact values required by the Helius transactionSubscribe API.
    """
    from core.detection.helius_reconciler import (
        MAX_TX_VERSION,
        MIGRATE_INSTRUCTION,
        PUMP_PROGRAM,
    )

    assert PUMP_PROGRAM == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", (
        f"Expected PUMP_PROGRAM == '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', "
        f"got {PUMP_PROGRAM!r}"
    )
    assert MIGRATE_INSTRUCTION == "migrate", (
        f"Expected MIGRATE_INSTRUCTION == 'migrate', got {MIGRATE_INSTRUCTION!r}"
    )
    assert MAX_TX_VERSION == 0, (
        f"Expected MAX_TX_VERSION == 0, got {MAX_TX_VERSION!r}"
    )
