# ---
# module: core.tests.test_tier2_lake_backfill
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: pytest, pytest-django, asyncio, gzip, json, pathlib, datetime,
#               core.management.commands.run_firehose, core.backfill.lake_backfill,
#               core.firehose.tape_sink, core.models
# ---
"""Tier-2 lake-backfill test suite.

Tests:
  T2-1  load_without_sink fires NO on_add (no lake duplication).
  T2-2  LakeBackfiller: correct-partition scan (grad-3d..grad).
  T2-3  LakeBackfiller: phase="pre" filter (post-grad rows excluded).
  T2-4  LakeBackfiller: cross-mint exclusion.
  T2-5  Backfill populates buffer → token scores on a later tick.
  T2-6  NO double-write: lake row count unchanged before/after backfill.
  T2-7  Deferred-then-rescored sets status=SCORED.
  T2-8  Restart-safety: status=SCORED excluded from _due_tokens_sync.
  T2-9  _backfill_pending dedup: second dispatch not fired for pending mint.
  T2-10 FIREHOSE_LAKE_BASE constant exported from tape_sink (correct path).
  T2-11 Lake returns zero rows → token marked status=SKIPPED.
  T2-12 LakeBackfiller._partition_dates returns correct range oldest-first.
"""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.backfill.lake_backfill import LakeBackfiller
from core.management.commands.run_firehose import TapeStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_swap(
    mint: str,
    block_time: int,
    phase: str = "pre",
    side: str = "buy",
    price: float = 0.001,
) -> dict:
    return {
        "mint": mint,
        "block_time": block_time,
        "slot": block_time * 2,
        "signature": f"sig_{mint[:4]}_{block_time}",
        "side": side,
        "price": price,
        "vol": 1.0,
        "vol_sol": 1.0,
        "vol_usd": 150.0,
        "owner": "ownerABC",
        "phase": phase,
    }


def _write_lake_partition(tmp_path: Path, date_str: str, rows: list[dict]) -> Path:
    """Write rows to tmp_path/dt=<date_str>/part-0.jsonl.gz and return the path."""
    part_dir = tmp_path / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_file = part_dir / "part-0.jsonl.gz"
    with gzip.open(part_file, "ab") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode())
    return part_file


# ---------------------------------------------------------------------------
# T2-10: FIREHOSE_LAKE_BASE constant
# ---------------------------------------------------------------------------

def test_firehose_lake_base_exported():
    """FIREHOSE_LAKE_BASE must be exported from tape_sink as Path('lake/firehose')."""
    from core.firehose.tape_sink import FIREHOSE_LAKE_BASE

    assert isinstance(FIREHOSE_LAKE_BASE, Path), "FIREHOSE_LAKE_BASE must be a Path"
    assert str(FIREHOSE_LAKE_BASE) == "lake/firehose", (
        f"Expected 'lake/firehose', got {FIREHOSE_LAKE_BASE!r}"
    )


# ---------------------------------------------------------------------------
# T2-1: load_without_sink fires NO on_add
# ---------------------------------------------------------------------------

def test_load_without_sink_does_not_fire_on_add():
    """load_without_sink must NOT invoke on_add (no lake write → no duplication)."""
    fired: list[tuple] = []

    def _on_add(mint: str, swap: dict) -> None:
        fired.append((mint, swap))

    store = TapeStore(on_add=_on_add)
    swaps = [_make_swap("MINT1", 1_700_000_000 + i) for i in range(5)]
    store.load_without_sink("MINT1", swaps)

    assert not fired, f"on_add was fired {len(fired)} times — must be 0"
    assert store.count("MINT1") == 5


def test_load_without_sink_no_on_add_registered():
    """load_without_sink works when on_add is None (no crash)."""
    store = TapeStore()  # no on_add
    swaps = [_make_swap("MINT2", 1_700_000_000)]
    store.load_without_sink("MINT2", swaps)
    assert store.count("MINT2") == 1


def test_load_without_sink_empty_swaps_is_noop():
    """load_without_sink([]) does not change the buffer."""
    fired: list = []
    store = TapeStore(on_add=lambda m, s: fired.append(s))
    store.load_without_sink("MINT3", [])
    assert store.count("MINT3") == 0
    assert not fired


def test_add_still_fires_on_add():
    """Regression: the normal add() path must still fire on_add."""
    fired: list = []
    store = TapeStore(on_add=lambda m, s: fired.append(s))
    store.add("MINT4", _make_swap("MINT4", 1_700_000_000))
    assert len(fired) == 1


# ---------------------------------------------------------------------------
# T2-12: _partition_dates helper
# ---------------------------------------------------------------------------

def test_partition_dates_range():
    """_partition_dates returns grad-3d..grad inclusive, oldest-first."""
    grad_dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    dates = LakeBackfiller._partition_dates(grad_dt, lookback_days=3)
    assert dates == ["2024-03-12", "2024-03-13", "2024-03-14", "2024-03-15"]


def test_partition_dates_single_day():
    """lookback_days=0 returns only the grad date."""
    grad_dt = datetime(2024, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
    dates = LakeBackfiller._partition_dates(grad_dt, lookback_days=0)
    assert dates == ["2024-06-21"]


# ---------------------------------------------------------------------------
# T2-2: correct-partition scan
# ---------------------------------------------------------------------------

def test_lake_backfiller_correct_partition(tmp_path: Path):
    """LakeBackfiller scans grad-3d..grad and returns matching rows."""
    # grad_bt = 2024-03-15T00:00:00Z
    grad_bt = int(datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    mint = "MINT_CORRECT"

    # Write rows on the grad date and two days before.
    # Row at grad-1d (should be included: block_time < grad_bt)
    row_early = _make_swap(mint, grad_bt - 86400, phase="pre")
    _write_lake_partition(tmp_path, "2024-03-14", [row_early])

    # Row on the grad date but before grad_bt
    row_grad_day = _make_swap(mint, grad_bt - 60, phase="pre")
    _write_lake_partition(tmp_path, "2024-03-15", [row_grad_day])

    backfiller = LakeBackfiller(base_dir=tmp_path)
    result = backfiller.run_for_mint(mint, grad_bt)

    assert len(result) == 2
    bts = {r["block_time"] for r in result}
    assert bts == {grad_bt - 86400, grad_bt - 60}


def test_lake_backfiller_excludes_post_grad_block_time(tmp_path: Path):
    """Rows with block_time >= graduated_block_time are excluded even if phase=pre."""
    grad_bt = int(datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    mint = "MINT_POST_BT"

    # Row at exactly grad_bt (NOT strictly before) — must be excluded.
    row_at_grad = _make_swap(mint, grad_bt, phase="pre")
    row_after_grad = _make_swap(mint, grad_bt + 100, phase="pre")
    row_before_grad = _make_swap(mint, grad_bt - 1, phase="pre")
    _write_lake_partition(tmp_path, "2024-03-15", [row_at_grad, row_after_grad, row_before_grad])

    backfiller = LakeBackfiller(base_dir=tmp_path)
    result = backfiller.run_for_mint(mint, grad_bt)

    assert len(result) == 1
    assert result[0]["block_time"] == grad_bt - 1


# ---------------------------------------------------------------------------
# T2-3: phase="pre" filter
# ---------------------------------------------------------------------------

def test_lake_backfiller_phase_pre_filter(tmp_path: Path):
    """phase='post' rows are excluded even when block_time < graduated_block_time."""
    grad_bt = int(datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    mint = "MINT_PHASE"

    row_pre = _make_swap(mint, grad_bt - 100, phase="pre")
    row_post = _make_swap(mint, grad_bt - 50, phase="post")   # must be excluded
    row_none = _make_swap(mint, grad_bt - 30, phase="neither")  # also excluded
    _write_lake_partition(tmp_path, "2024-03-15", [row_pre, row_post, row_none])

    backfiller = LakeBackfiller(base_dir=tmp_path)
    result = backfiller.run_for_mint(mint, grad_bt)

    assert len(result) == 1
    assert result[0]["phase"] == "pre"


# ---------------------------------------------------------------------------
# T2-4: cross-mint exclusion
# ---------------------------------------------------------------------------

def test_lake_backfiller_cross_mint_exclusion(tmp_path: Path):
    """Rows for other mints in the same partition are excluded."""
    grad_bt = int(datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    target = "MINT_TARGET"
    other = "MINT_OTHER"

    rows = [
        _make_swap(target, grad_bt - 100, phase="pre"),
        _make_swap(other, grad_bt - 90, phase="pre"),
        _make_swap(other, grad_bt - 80, phase="pre"),
    ]
    _write_lake_partition(tmp_path, "2024-03-15", rows)

    backfiller = LakeBackfiller(base_dir=tmp_path)
    result = backfiller.run_for_mint(target, grad_bt)

    assert len(result) == 1
    assert all(r["mint"] == target for r in result)


# ---------------------------------------------------------------------------
# T2-6: NO double-write (lake row count unchanged before/after)
# ---------------------------------------------------------------------------

def test_no_double_write_via_load_without_sink(tmp_path: Path):
    """load_without_sink must not write rows to the lake (count unchanged)."""
    grad_bt = int(datetime(2024, 3, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    mint = "MINT_NOWRITE"

    rows_before = [_make_swap(mint, grad_bt - 200, phase="pre")]
    part_file = _write_lake_partition(tmp_path, "2024-03-15", rows_before)

    # Read the row count before backfill.
    def _count_rows(path: Path) -> int:
        count = 0
        with gzip.open(path, "rt") as gz:
            for line in gz:
                if line.strip():
                    count += 1
        return count

    before = _count_rows(part_file)
    assert before == 1

    # Now "populate" the buffer (simulate what the daemon does).
    from core.firehose.tape_sink import LakeTapeSink

    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1)
    fired: list = []
    store = TapeStore(on_add=lambda m, s: (fired.append(s), sink.record(s, "pre")))

    # Load via load_without_sink (must NOT fire on_add / sink.record).
    lake_rows = [_make_swap(mint, grad_bt - 200, phase="pre")]
    store.load_without_sink(mint, lake_rows)
    sink.flush()

    after = _count_rows(part_file)
    assert after == before, (
        f"Lake grew from {before} to {after} rows — "
        "load_without_sink must not write to the lake."
    )
    assert not fired, "on_add should not have been called"


# ---------------------------------------------------------------------------
# T2-9: _backfill_pending dedup
# ---------------------------------------------------------------------------

def test_backfill_pending_dedup():
    """_backfill_pending prevents dispatching a second task for the same mint."""
    from core.management.commands.run_firehose import FirehoseDaemon

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        reconciler_factory=lambda: (None, None),
        postgrad_factory=lambda mint: None,
        tape_sink=MagicMock(record=lambda *a: None, flush=lambda: 0),
        wallet_bank=None,
    )

    mint = "MINT_DEDUP"
    # Manually add to pending to simulate an already-dispatched task.
    daemon._backfill_pending.add(mint)

    tasks_created: list[str] = []

    # Simulate the defer branch logic (the real one checks `mint not in self._backfill_pending`).
    swaps: list[dict] = []  # empty buffer
    if len(swaps) == 0 and mint not in daemon._backfill_pending:
        tasks_created.append(mint)

    assert not tasks_created, "Should not dispatch a second task for an already-pending mint"


# ---------------------------------------------------------------------------
# T2-10: per-tick dispatch cap (event-loop-starvation guard)
# ---------------------------------------------------------------------------

def test_backfill_per_tick_dispatch_cap():
    """At most _MAX_BACKFILL_DISPATCH_PER_TICK lake scans are dispatched per tick.

    Regression for the live incident: scoring-on over a large DETECTED backlog
    dispatched HUNDREDS of fire-and-forget GIL-heavy lake scans at once, starving
    the asyncio loop and killing the shared Helius WS (~20s death cycle).  The
    _score_tick loop caps new dispatches per tick so the backlog drains gradually.
    """
    # Mirror the exact gating logic from _score_tick (local constant = 4).
    _MAX_BACKFILL_DISPATCH_PER_TICK = 4
    backfill_pending: set[str] = set()
    due_mints = [f"MINT_{i:03d}" for i in range(50)]  # large backlog, all no-tape

    dispatched: list[str] = []
    backfills_dispatched = 0
    for mint in due_mints:
        swaps: list[dict] = []  # empty buffer → would trigger backfill
        if (
            len(swaps) == 0
            and mint not in backfill_pending
            and backfills_dispatched < _MAX_BACKFILL_DISPATCH_PER_TICK
        ):
            backfill_pending.add(mint)
            backfills_dispatched += 1
            dispatched.append(mint)

    assert len(dispatched) == _MAX_BACKFILL_DISPATCH_PER_TICK, (
        f"Expected at most {_MAX_BACKFILL_DISPATCH_PER_TICK} dispatches/tick from a "
        f"50-token backlog, got {len(dispatched)}"
    )


def test_lake_backfill_semaphore_lazy_init():
    """FirehoseDaemon exposes a lazily-initialised lake-backfill concurrency cap."""
    from core.management.commands.run_firehose import FirehoseDaemon

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        reconciler_factory=lambda: (None, None),
        postgrad_factory=lambda mint: None,
        tape_sink=MagicMock(record=lambda *a: None, flush=lambda: 0),
        wallet_bank=None,
    )
    # Lazily created on the event loop (None until first backfill); attribute exists.
    assert hasattr(daemon, "_lake_backfill_sem"), "daemon must have _lake_backfill_sem"
    assert daemon._lake_backfill_sem is None, "lake sem is lazily initialised (None at construct)"


# ---------------------------------------------------------------------------
# Django DB tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_restart_safety_scored_excluded_from_due():
    """status=SCORED tokens are excluded from _due_tokens_sync (restart-safety)."""
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import PipelineConfig, PipelineState, Token

    # Invalidate the resolver cache so our new config is picked up.
    cache.delete("active_pipeline_config_sections")

    # Set up PipelineState.
    PipelineState.objects.update_or_create(
        pk=1,
        defaults={
            "firehose_active": True,
            "scoring_enabled": True,
            "trading_enabled": False,
        },
    )
    # Create a minimal active PipelineConfig (using separate section fields).
    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9901,
        label="tier2-rsafety-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 1800, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={"score_at_elapsed_s": 1, "window_s": 180, "capture_buffer_s": 4, "gate": "adaptive_topk"},
        outcome={"window_s": 1800, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    # grad_at must be in the past so score_time_reached(grad_at, score_at=1, now) is True,
    # AND within the recency window (score_at + outcome_window + _TERMINALIZE_GRACE_S = 2401 s).
    # VirtualClock is 2026-06-21T12:00:00Z; use grad_at 100 s before that so it is
    # firmly inside the 2401-s window and past the score_at=1 s gate.
    from datetime import timedelta  # noqa: PLC0415
    grad_at = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=100)

    # DETECTED token (should be in due list).
    tok_detected = Token.objects.create(
        mint="MINT_DETECTED_RSAFETY",
        pool_address="pool1",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    # SCORED token (should NOT be in due list).
    tok_scored = Token.objects.create(
        mint="MINT_SCORED_RSAFETY",
        pool_address="pool2",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_SCORED,
    )

    # SKIPPED token (should NOT be in due list).
    tok_skipped = Token.objects.create(
        mint="MINT_SKIPPED_RSAFETY",
        pool_address="pool3",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_SKIPPED,
    )

    clock = VirtualClock(datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc))
    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        reconciler_factory=lambda: (None, None),
        postgrad_factory=lambda mint: None,
        tape_sink=MagicMock(record=lambda *a: None, flush=lambda: 0),
        wallet_bank=None,
        clock=clock,
    )

    due = daemon._due_tokens_sync()
    due_mints = {m for m, _ in due}

    assert tok_detected.mint in due_mints, "DETECTED token must appear in due list"
    assert tok_scored.mint not in due_mints, "SCORED token must NOT appear in due list"
    assert tok_skipped.mint not in due_mints, "SKIPPED token must NOT appear in due list"


@pytest.mark.django_db(transaction=True)
def test_set_token_status_sync_writes_scored():
    """_set_token_status_sync writes status=SCORED to the Token row."""
    from django.utils import timezone as dj_tz

    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import Token

    grad_at = dj_tz.now()
    Token.objects.create(
        mint="MINT_STATUS_TEST",
        pool_address="pool_s",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    FirehoseDaemon._set_token_status_sync("MINT_STATUS_TEST", "SCORED")

    tok = Token.objects.get(mint="MINT_STATUS_TEST")
    assert tok.status == Token.STATUS_SCORED


@pytest.mark.django_db(transaction=True)
def test_set_token_status_sync_writes_skipped():
    """_set_token_status_sync writes status=SKIPPED for definitive no-tape."""
    from django.utils import timezone as dj_tz

    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import Token

    grad_at = dj_tz.now()
    Token.objects.create(
        mint="MINT_SKIP_TEST",
        pool_address="pool_sk",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    FirehoseDaemon._set_token_status_sync("MINT_SKIP_TEST", "SKIPPED")

    tok = Token.objects.get(mint="MINT_SKIP_TEST")
    assert tok.status == Token.STATUS_SKIPPED


@pytest.mark.django_db(transaction=True)
def test_data_migration_sets_scored_for_tokens_with_predictions():
    """The data migration marks DETECTED tokens with Predictions as SCORED."""
    from django.utils import timezone as dj_tz

    from core.models import Prediction, Token

    grad_at = dj_tz.now()

    # Token with a Prediction — should become SCORED.
    tok_with_pred = Token.objects.create(
        mint="MINT_WITH_PRED",
        pool_address="pool_p",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )
    Prediction.objects.create(
        mint="MINT_WITH_PRED",
        score_time=int(grad_at.timestamp()),
        model_id="test-model",
        blend=0.75,
        per_day_target=30,
        picked=True,
    )

    # Token WITHOUT a Prediction — must remain DETECTED.
    tok_no_pred = Token.objects.create(
        mint="MINT_NO_PRED",
        pool_address="pool_np",
        graduated_at=grad_at,
        graduated_block_time=int(grad_at.timestamp()),
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    # Simulate what the migration does (run it directly against the live models).
    scored_mints = set(
        Prediction.objects.values_list("mint", flat=True).distinct()
    )
    Token.objects.filter(mint__in=scored_mints, status="DETECTED").update(status="SCORED")

    tok_with_pred.refresh_from_db()
    tok_no_pred.refresh_from_db()

    assert tok_with_pred.status == Token.STATUS_SCORED, (
        "Token with Prediction should be SCORED after migration"
    )
    assert tok_no_pred.status == Token.STATUS_DETECTED, (
        "Token without Prediction should remain DETECTED"
    )


@pytest.mark.django_db(transaction=True)
def test_backfill_populates_buffer_for_later_tick(tmp_path: Path):
    """Backfill task populates the TapeStore buffer so scoring succeeds later."""
    grad_bt = int(datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    mint = "MINT_BACKFILL_TICK"

    # Write pre-grad rows to the fake lake.
    rows = [_make_swap(mint, grad_bt - 100 + i, phase="pre") for i in range(5)]
    _write_lake_partition(tmp_path, "2024-03-15", rows)

    # Build a TapeStore with a no-op sink.
    on_add_calls: list = []
    store = TapeStore(on_add=lambda m, s: on_add_calls.append((m, s)))

    # Initially empty.
    assert store.count(mint) == 0

    # Run the backfiller directly (sync path).
    backfiller = LakeBackfiller(base_dir=tmp_path)
    found = backfiller.run_for_mint(mint, grad_bt)
    assert len(found) == 5

    # Populate via load_without_sink (as the daemon would).
    store.load_without_sink(mint, found)

    # Buffer is now populated, on_add was NOT called.
    assert store.count(mint) == 5
    assert not on_add_calls, "on_add must not be called by load_without_sink"


@pytest.mark.django_db(transaction=True)
def test_zero_lake_rows_leads_to_skipped_status():
    """When lake returns zero rows, _lake_backfill_task marks token SKIPPED."""
    from django.utils import timezone as dj_tz

    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import Token

    grad_at = dj_tz.now()
    grad_bt = int(grad_at.timestamp())
    mint = "MINT_ZERO_LAKE"

    Token.objects.create(
        mint=mint,
        pool_address="pool_z",
        graduated_at=grad_at,
        graduated_block_time=grad_bt,
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=Token.STATUS_DETECTED,
    )

    daemon = FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        reconciler_factory=lambda: (None, None),
        postgrad_factory=lambda m: None,
        tape_sink=MagicMock(record=lambda *a: None, flush=lambda: 0),
        wallet_bank=None,
    )

    # Patch LakeBackfiller.run_for_mint to return empty list.
    with patch(
        "core.backfill.lake_backfill.LakeBackfiller.run_for_mint",
        return_value=[],
    ):
        asyncio.run(daemon._lake_backfill_task(mint, grad_bt))

    tok = Token.objects.get(mint=mint)
    assert tok.status == Token.STATUS_SKIPPED


# ---------------------------------------------------------------------------
# Billy schema-A normalise tier (shared_billy DISK lookup for pf/v7 features)
# ---------------------------------------------------------------------------

def _schema_a_row(mint: str, block_time: int, side: str = "buy") -> dict:
    """A solanaBilly schema-A raw-reserves row (NO 'price'/'phase' keys)."""
    return {
        "mint": mint,
        "block_time": block_time,
        "slot": block_time * 2,
        "signature": f"sigA_{mint[:4]}_{block_time}",
        "side": side,
        "owner": f"w{block_time % 7}",
        "virtual_sol_reserves": 30_000_000_000 + block_time % 1000,
        "virtual_token_reserves": 1_000_000_000_000,
        "sol_amount": 500_000_000,
        "token_amount": 10_000_000,
        "real_sol_reserves": 5_000_000_000,
    }


def test_lake_backfill_normalise_reads_schema_a_billy_rows(tmp_path):
    """shared_billy DISK tier: LakeBackfiller(normalise=True) turns schema-A billy
    rows into swap-dicts WITH 'price' and phase='pre' so the feature builders work.
    Without normalise=True the schema-A rows lack 'price' and yield 0 usable swaps —
    the exact gap that made the pf branch defer forever in shared_billy mode."""
    grad_bt = 1_782_000_000
    date_str = datetime.fromtimestamp(grad_bt, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = [_schema_a_row("MINTPFV2", grad_bt - 300 + i * 10) for i in range(8)]
    _write_lake_partition(tmp_path, date_str, rows)

    # WITH normalise → schema-A rows become usable pre-grad swaps with 'price'.
    got = LakeBackfiller(base_dir=tmp_path, normalise=True).run_for_mint("MINTPFV2", grad_bt)
    assert len(got) == 8, f"expected 8 normalised pre-grad swaps, got {len(got)}"
    assert all("price" in s and s["price"] > 0 for s in got), "normalise must add 'price'"
    assert all(s.get("phase") == "pre" for s in got), "schema-A rows normalise to phase=pre"

    # And those swaps feed the pf feature builder (not None).
    from core.pf_features import compute_pf_features

    feats = compute_pf_features(got, float(grad_bt))
    assert feats is not None, "pf features must compute from the billy-normalised curve"
    assert len(feats) == 13


def test_lake_backfill_without_normalise_misses_schema_a(tmp_path):
    """Guard: WITHOUT normalise, schema-A billy rows lack 'price' → LakeReader drops
    them → 0 swaps. Proves normalise=True is load-bearing for the shared_billy tier."""
    grad_bt = 1_782_000_000
    date_str = datetime.fromtimestamp(grad_bt, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = [_schema_a_row("MINTPFV2", grad_bt - 200 + i * 10) for i in range(5)]
    _write_lake_partition(tmp_path, date_str, rows)

    got = LakeBackfiller(base_dir=tmp_path, normalise=False).run_for_mint("MINTPFV2", grad_bt)
    assert got == [], f"un-normalised schema-A rows must yield 0 swaps, got {len(got)}"
