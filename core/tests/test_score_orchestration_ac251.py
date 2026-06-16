# ---
# module: core.tests.test_score_orchestration_ac251
# sprint: sprint-6
# story: US-25 AC-25.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.score_orchestrator, core.snapshot_fetcher, core.snapshot_source,
#               core.clock, core.schemas, core.models, ast, pathlib, pytest
# ---
"""AC-25.1 — ScoreTimeOrchestrator: exactly one snapshot at score_at_elapsed_s
from get_active_config(), NOT a hardcoded constant or os.getenv (Principle #1).

Verification strategy (per the AC):

(a) Functional tests with VirtualClock + InMemorySnapshotSource:
      1. Token graduated at T0, clock set to T0+SCORE_AT_S → orchestrate fires
         exactly one snapshot and the elapsed_s in the row equals SCORE_AT_S.
      2. Clock set to T0+SCORE_AT_S-1 (one second before) → orchestrate returns
         None with no snapshot row created.
      3. Changing score_at_elapsed_s from 60 → 120 in config_fn changes the
         trigger time: with the clock at T0+60, a config of 60 fires and a
         config of 120 does NOT fire.
      4. The elapsed_s in the persisted snapshot row always equals the value
         in the active config, not a hardcoded constant.

(b) Static-analysis guards (US-2-style):
      5. score_orchestrator.py imports no concrete DataSource class
         (LiveSource, ReplaySource, core.live_source, core.replay_source).
      6. score_orchestrator.py contains no datetime.now(), datetime.utcnow(),
         or time.time() calls — all time reads come from the injected Clock.

Notes:
  - The §5.2 capture_buffer_s >= 3 invariant is already enforced by the P1
    Pydantic schema; no additional test is added here (per the AC text).
  - Tests 1–4 use @pytest.mark.django_db because orchestrate() calls
    fetcher.fetch_and_persist() which writes to the 'snapshots' table.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.clock import VirtualClock
from core.schemas import PipelineConfigSchema
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_MODULE = REPO_ROOT / "core" / "score_orchestrator.py"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SCORE_AT_S = 120  # default elapsed seconds used in most tests

MINT_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
MINT_B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.45, "count": 1000},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 5000.0,
    "tvl": 4800.0,
    "depth": {"bid": 100.0, "ask": 100.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(score_at_elapsed_s: int) -> PipelineConfigSchema:
    """Build a minimal valid PipelineConfigSchema with the given score_at_elapsed_s.

    Cross-section invariants satisfied:
      scoring.window_s > scoring.score_at_elapsed_s  (leak guard)
      tape.idle_kill_ttl_s >= outcome.window_s        (label-truncation guard D4)
      capture_buffer_s >= 3                           (§5.2)
    """
    return PipelineConfigSchema.from_model_sections(
        detection={},
        tape={"idle_kill_ttl_s": 7200},
        scoring={
            "score_at_elapsed_s": score_at_elapsed_s,
            "window_s": score_at_elapsed_s + 180,
            "capture_buffer_s": 4,
        },
        outcome={"window_s": 3600},
        trading={},
    )


class InMemorySnapshotSource(SnapshotDataSource):
    """Test double: returns pre-loaded snapshot dicts without network calls."""

    def __init__(self, payloads: dict) -> None:
        self._payloads = payloads
        self.call_count: int = 0

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self.call_count += 1
        return self._payloads[mint]


# ---------------------------------------------------------------------------
# (a) Functional tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_fires_at_t0_plus_score_at_elapsed_s():
    """Token graduated at T0, clock at T0+SCORE_AT_S → exactly one snapshot.

    Verifies:
    - orchestrate() returns the raw payload (not None)
    - Exactly one 'snapshots' row is persisted
    - The row's elapsed_s equals SCORE_AT_S (read from config_fn, Principle #1)
    - The row's taken_at equals the virtual clock's current time
    """
    from core.models import Snapshot
    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = InMemorySnapshotSource({MINT_A: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=lambda: _make_config(SCORE_AT_S),
    )

    result = orch.orchestrate(MINT_A, T0)

    assert result == SAMPLE_PAYLOAD, "orchestrate() must return the raw snapshot payload"
    rows = list(Snapshot.objects.filter(mint=MINT_A))
    assert len(rows) == 1, f"Expected exactly 1 snapshot row, got {len(rows)}"
    snap = rows[0]
    assert snap.elapsed_s == SCORE_AT_S, (
        f"elapsed_s in snapshot row ({snap.elapsed_s}) must equal "
        f"config.scoring.score_at_elapsed_s ({SCORE_AT_S})"
    )
    assert snap.taken_at == T0 + timedelta(seconds=SCORE_AT_S), (
        "taken_at must equal the virtual clock time at fetch"
    )


@pytest.mark.django_db
def test_snapshot_does_not_fire_before_score_time():
    """Clock one second before score time → orchestrate returns None, no row.

    Verifies the 'exactly at score_at_elapsed_s' boundary — the snapshot must
    NOT fire before the computed score_time.
    """
    from core.models import Snapshot
    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S - 1))
    source = InMemorySnapshotSource({MINT_A: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=lambda: _make_config(SCORE_AT_S),
    )

    result = orch.orchestrate(MINT_A, T0)

    assert result is None, "orchestrate() must return None before score time"
    assert not Snapshot.objects.filter(mint=MINT_A).exists(), (
        "No snapshot row must be created before score time"
    )


@pytest.mark.django_db
def test_changing_score_at_elapsed_s_changes_trigger_time():
    """Changing config's score_at_elapsed_s changes when the snapshot fires.

    Clock at T0+60:
      - Config with score_at_elapsed_s=60  → fires (returns payload)
      - Config with score_at_elapsed_s=120 → does NOT fire (returns None)

    This proves the trigger time is read from config_fn() at orchestration time,
    NOT from a hardcoded constant or os.getenv (Principle #1, AC-25.1).
    """
    from core.models import Snapshot
    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    clock = VirtualClock(T0 + timedelta(seconds=60))

    # Config A: score_at_elapsed_s=60 → clock is exactly at score_time → fires
    source_a = InMemorySnapshotSource({MINT_A: SAMPLE_PAYLOAD})
    fetcher_a = SnapshotFetcher(source=source_a, clock=clock)
    orch_a = ScoreTimeOrchestrator(
        fetcher=fetcher_a,
        clock=clock,
        config_fn=lambda: _make_config(60),
    )
    result_a = orch_a.orchestrate(MINT_A, T0)
    assert result_a == SAMPLE_PAYLOAD, (
        "With score_at_elapsed_s=60 and clock at T0+60, orchestrate must fire"
    )
    assert Snapshot.objects.filter(mint=MINT_A).count() == 1

    # Config B: score_at_elapsed_s=120 → clock is 60s before score_time → no fire
    source_b = InMemorySnapshotSource({MINT_B: SAMPLE_PAYLOAD})
    fetcher_b = SnapshotFetcher(source=source_b, clock=clock)
    orch_b = ScoreTimeOrchestrator(
        fetcher=fetcher_b,
        clock=clock,
        config_fn=lambda: _make_config(120),
    )
    result_b = orch_b.orchestrate(MINT_B, T0)
    assert result_b is None, (
        "With score_at_elapsed_s=120 and clock at T0+60, orchestrate must NOT fire"
    )
    assert not Snapshot.objects.filter(mint=MINT_B).exists(), (
        "No snapshot row must be created when score time has not been reached"
    )


@pytest.mark.django_db
def test_elapsed_s_in_snapshot_row_matches_config_value():
    """The elapsed_s stored in the snapshot row always equals the config value.

    Uses a non-default score_at_elapsed_s (90s) to confirm there is no
    hardcoded fallback.
    """
    from core.models import Snapshot
    from core.score_orchestrator import ScoreTimeOrchestrator
    from core.snapshot_fetcher import SnapshotFetcher

    CUSTOM_ELAPSED_S = 90
    clock = VirtualClock(T0 + timedelta(seconds=CUSTOM_ELAPSED_S))
    source = InMemorySnapshotSource({MINT_A: SAMPLE_PAYLOAD})
    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=lambda: _make_config(CUSTOM_ELAPSED_S),
    )

    orch.orchestrate(MINT_A, T0)

    snap = Snapshot.objects.get(mint=MINT_A)
    assert snap.elapsed_s == CUSTOM_ELAPSED_S, (
        f"elapsed_s in row ({snap.elapsed_s}) must equal "
        f"config.scoring.score_at_elapsed_s ({CUSTOM_ELAPSED_S}), not a hardcoded value"
    )


# ---------------------------------------------------------------------------
# (b) Static-analysis guards
# ---------------------------------------------------------------------------

_CONCRETE_MODULE_NAMES = {"core.live_source", "core.replay_source"}
_CONCRETE_CLASS_NAMES = {"LiveSource", "ReplaySource"}


def _parse_orchestrator() -> ast.Module:
    return ast.parse(ORCHESTRATOR_MODULE.read_text(encoding="utf-8"))


def _concrete_source_violations(tree: ast.Module) -> list:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _CONCRETE_MODULE_NAMES:
                found.append(f"line {node.lineno}: 'from {module} import ...'")
                continue
            for alias in node.names:
                if alias.name in _CONCRETE_CLASS_NAMES:
                    found.append(
                        f"line {node.lineno}: 'from {module} import {alias.name}'"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _CONCRETE_MODULE_NAMES:
                    found.append(f"line {node.lineno}: 'import {alias.name}'")
    return found


def _time_call_violations(tree: ast.Module) -> list:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            obj, attr = func.value.id, func.attr
            if obj == "datetime" and attr in ("now", "utcnow"):
                found.append(f"line {node.lineno}: forbidden 'datetime.{attr}()' call")
            if obj == "time" and attr == "time":
                found.append(f"line {node.lineno}: forbidden 'time.time()' call")
    return found


def test_static_analysis_no_concrete_source_import():
    """Static-analysis guard: score_orchestrator.py must not import concrete DataSource classes.

    The orchestrator depends only on SnapshotFetcher (which encapsulates the seam),
    never on LiveSource, ReplaySource, core.live_source, or core.replay_source.
    This enforces the live/replay seam (US-2 / Principle #7).
    """
    assert ORCHESTRATOR_MODULE.exists(), (
        f"score_orchestrator.py not found at {ORCHESTRATOR_MODULE}"
    )
    violations = _concrete_source_violations(_parse_orchestrator())
    assert not violations, (
        "core/score_orchestrator.py must not import concrete DataSource classes. "
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_static_analysis_no_direct_time_calls():
    """Static-analysis guard: score_orchestrator.py must not call datetime.now(),
    datetime.utcnow(), or time.time() directly.

    All time reads must come from the injected Clock (Principle #7 / AC-2.2).
    """
    assert ORCHESTRATOR_MODULE.exists(), (
        f"score_orchestrator.py not found at {ORCHESTRATOR_MODULE}"
    )
    violations = _time_call_violations(_parse_orchestrator())
    assert not violations, (
        "core/score_orchestrator.py must not call datetime.now(), datetime.utcnow(), "
        "or time.time() — time must come from the injected Clock (Principle #7).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
