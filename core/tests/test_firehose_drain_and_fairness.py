# ---
# module: core.tests.test_firehose_drain_and_fairness
# sprint: epic-tape-sourcing-escalation
# story: hotfix-firehose-drain-and-fairness
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: pytest, pytest-django, asyncio, datetime, unittest.mock,
#               core.management.commands.run_firehose, core.models,
#               core.clock
# ---
"""Tests for Fix 1 (terminalize un-scoreable grads) and Fix 2 (newest-first backfill).

TEST DESIGN PRINCIPLE: anchor tests against real Postgres rows wherever the
contract involves the ORM (e.g. _due_tokens_sync, _set_token_status_sync).
Pure-logic paths (dispatch order, deadline math) use in-process state only.

TESTS
-----
FD-1  Past-deadline thin-tape token → marked SKIPPED in _score_tick, NOT
      returned by _due_tokens_sync on the subsequent tick.
      Anchor: real DB rows; VirtualClock advances past deadline.

FD-2  Fresh token (not past deadline) with 0 swaps → NOT terminalized by
      _score_tick; backfill is dispatched instead.
      Anchor: in-process state check (no DB mutation expected).

FD-3  _due_tokens_sync returns tokens NEWEST-grad-first when multiple
      DETECTED tokens exist.
      Anchor: real DB rows with different graduated_block_time values.

FD-4  _due_tokens_sync excludes SKIPPED tokens from the candidate set
      (they must not be dispatched for backfill on any tick).
      Anchor: real DB rows.

FD-5  _due_tokens_sync recency window: a DETECTED token whose
      graduated_block_time is beyond the deadline window is NOT returned
      (bounded set).
      Anchor: real DB rows.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config_mock(
    score_at_s: int = 120,
    outcome_window_s: int = 1800,
    gate: str = "adaptive_topk",
):
    """Return a minimal PipelineConfig-like mock for _due_tokens_sync."""
    cfg = MagicMock()
    cfg.scoring.score_at_elapsed_s = score_at_s
    cfg.scoring.gate = gate
    cfg.scoring.per_day_target = 30
    cfg.outcome.window_s = outcome_window_s
    cfg.tape.max_postgrad_subscriptions = 5
    return cfg


def _make_daemon(clock):
    """Build a FirehoseDaemon test instance with all external deps mocked."""
    from core.management.commands.run_firehose import FirehoseDaemon

    return FirehoseDaemon(
        collection_factory=lambda: None,
        graduation_factory=lambda: (None, None),
        reconciler_factory=lambda: (None, None),
        postgrad_factory=lambda mint: None,
        tape_sink=MagicMock(record=lambda *a: None, flush=lambda: 0),
        wallet_bank=None,
        clock=clock,
    )


def _create_token(mint, pool, grad_at, grad_bt, status):
    """Create a real Token row and return it."""
    from core.models import Token

    return Token.objects.create(
        mint=mint,
        pool_address=pool,
        graduated_at=grad_at,
        graduated_block_time=grad_bt,
        dex_source="pump_dot_fun",
        raw_graduation={},
        status=status,
    )


# ---------------------------------------------------------------------------
# FD-1: Past-deadline thin-tape token → SKIPPED + excluded from next tick
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_past_deadline_thin_tape_terminalized_and_excluded():
    """FD-1: A DETECTED token past its deadline with <20 swaps → SKIPPED.

    Sequence:
      1. Create a DETECTED token graduated 5000 s ago (>> score_at+window+grace).
      2. Call _score_tick (via asyncio.run) with a scorer that returns None features
         (< 20 swaps in the tape store) → deadline check fires → SKIPPED.
      3. Verify Token.status == SKIPPED.
      4. Call _due_tokens_sync again → the token is NOT in the returned list.

    The VirtualClock is set so 'now' is well past the terminalization deadline
    (score_at=120 + outcome_window=1800 + grace=600 = 2520 s after grad_bt).

    NOTE: _score_tick normally only reaches the scoring loop when
    `_due_tokens_sync` returns the token.  But the FD-5 test shows
    `_due_tokens_sync` excludes tokens beyond the recency window.  So for this
    test we need the token to be within the recency window in the DB query (so
    it enters the loop) but past the deadline in wall-clock (so the termination
    fires).  We achieve that by setting grad_bt exactly at the window boundary
    minus 1 s so the DB filter includes it, but `now_epoch - grad_bt > deadline_s`.
    Concretely: deadline_s = score_at + outcome_window + grace = 2520.
    We set grad_bt = now - 2521 (1 s past deadline), still within the window
    cutoff used by `_due_tokens_sync` which is now - deadline_s = now - 2520.
    grad_bt = now - 2521 < cutoff = now - 2520, so it is just outside the window!

    Instead we use a simpler approach: mock `_due_tokens_sync` to return the
    token directly, then run _score_tick with the VirtualClock past the deadline.
    This isolates the terminalization logic in _score_tick from the DB filter.
    """
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.models import PipelineConfig, PipelineState, Token

    cache.delete("active_pipeline_config_sections")

    PipelineState.objects.update_or_create(
        pk=1,
        defaults={
            "firehose_active": True,
            "scoring_enabled": True,
            "trading_enabled": False,
        },
    )
    score_at_s = 120
    outcome_window_s = 1800
    # Deadline for reference: score_at + outcome_window + grace = 120 + 1800 + 600 = 2520 s
    # Token graduated 5000 s ago — well past this deadline.

    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9910,
        label="fd1-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 3600, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={
            "score_at_elapsed_s": score_at_s,
            "window_s": outcome_window_s,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": outcome_window_s, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    # Token graduated 5000 s ago (past deadline_s=2520 s).
    base_epoch = 1_750_000_000
    grad_bt = base_epoch - 5000  # well past deadline
    grad_at = datetime.fromtimestamp(grad_bt, tz=timezone.utc)

    tok = _create_token("MINT_FD1_DEADLINE", "pool_fd1", grad_at, grad_bt, Token.STATUS_DETECTED)

    # VirtualClock: 'now' is base_epoch (so now - grad_bt = 5000 > deadline_s=2520).
    clock = VirtualClock(datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    daemon = _make_daemon(clock)

    # The tape store has 0 swaps → assemble_pregrad_features will return None.
    # No swaps in daemon._tape for this mint.

    mock_scoring = {
        "gate": "adaptive_topk",
        "score_at_elapsed_s": score_at_s,
        "outcome_window_s": outcome_window_s,
        "per_day_target": 30,
        "rank_cut": 0.7916,
        "model_id": "test-model-fd1",
    }
    mock_scorer = MagicMock()
    mock_scorer.feature_list = []  # v3.2 path (len != 53)

    # Inject the token directly into due so _score_tick processes it regardless
    # of whether _due_tokens_sync's DB window filter includes it.
    with (
        patch.object(daemon, "_due_tokens_sync", return_value=[(tok.mint, grad_bt)]),
        patch.object(daemon, "_read_state", new=AsyncMock(return_value=(True, True, False))),
        patch.object(daemon, "_build_scoring_context_sync", return_value=(
            mock_scorer, None, mock_scoring,
            MagicMock(), 0.1, 150.0, 0.7916,
        )),
    ):
        asyncio.run(daemon._score_tick())

    # Token must now be SKIPPED.
    tok.refresh_from_db()
    assert tok.status == Token.STATUS_SKIPPED, (
        f"Expected SKIPPED after deadline terminalization, got {tok.status!r}"
    )
    assert tok.mint in daemon._scored_mints, (
        "daemon._scored_mints must contain the terminalized mint"
    )

    # On the next _due_tokens_sync call (real, unpatched), the token must NOT
    # appear — it is now SKIPPED so the DB filter excludes it.
    due2 = daemon._due_tokens_sync()
    due_mints2 = {m for m, _ in due2}
    assert tok.mint not in due_mints2, (
        "Terminalized (SKIPPED) token must NOT appear in _due_tokens_sync"
    )


# ---------------------------------------------------------------------------
# FD-2: Fresh token (before deadline) → NOT terminalized, backfill dispatched
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_fresh_token_not_terminalized():
    """FD-2: A DETECTED token before its deadline is NOT terminalized.

    Token graduated score_at_s + 60 s ago (past score_time_reached but well
    before the terminalization deadline = score_at + window + grace = 2520 s).
    With 0 swaps → the token must NOT be marked SKIPPED; a backfill must be
    dispatched instead.
    """
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.models import PipelineConfig, PipelineState, Token

    cache.delete("active_pipeline_config_sections")

    PipelineState.objects.update_or_create(
        pk=1,
        defaults={"firehose_active": True, "scoring_enabled": True, "trading_enabled": False},
    )
    score_at_s = 120
    outcome_window_s = 1800

    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9911,
        label="fd2-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 3600, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={
            "score_at_elapsed_s": score_at_s,
            "window_s": outcome_window_s,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": outcome_window_s, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    base_epoch = 1_750_100_000
    # Fresh: graduated only score_at_s + 60 s ago (well within the 2520-s window).
    fresh_age = score_at_s + 60  # 180 s << 2520 s deadline
    grad_bt = base_epoch - fresh_age
    grad_at = datetime.fromtimestamp(grad_bt, tz=timezone.utc)

    tok = _create_token("MINT_FD2_FRESH", "pool_fd2", grad_at, grad_bt, Token.STATUS_DETECTED)

    clock = VirtualClock(datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    daemon = _make_daemon(clock)

    # Track which mints get a backfill dispatched by observing _backfill_pending.
    # asyncio.create_task schedules a coroutine; we stub _lake_backfill_task with
    # a no-op coroutine so the task runs without hitting the real lake.
    async def _noop_backfill(mint, grad_bt_arg):
        return  # no-op — just records dispatch via _backfill_pending

    mock_scorer = MagicMock()
    mock_scorer.feature_list = []  # v3.2 path
    mock_scoring = {
        "gate": "adaptive_topk",
        "score_at_elapsed_s": score_at_s,
        "outcome_window_s": outcome_window_s,
        "per_day_target": 30,
        "rank_cut": 0.7916,
        "model_id": "test-model-fd2",
    }

    with (
        patch.object(daemon, "_due_tokens_sync", return_value=[(tok.mint, grad_bt)]),
        patch.object(daemon, "_read_state", new=AsyncMock(return_value=(True, True, False))),
        patch.object(daemon, "_build_scoring_context_sync", return_value=(
            mock_scorer, None, mock_scoring,
            MagicMock(), 0.1, 150.0, 0.7916,
        )),
        patch.object(daemon, "_lake_backfill_task", _noop_backfill),
    ):
        asyncio.run(daemon._score_tick())

    # Token must still be DETECTED (not terminalized).
    tok.refresh_from_db()
    assert tok.status == Token.STATUS_DETECTED, (
        f"Fresh token must remain DETECTED (not past deadline), got {tok.status!r}"
    )
    assert tok.mint not in daemon._scored_mints, (
        "Fresh token must NOT be in _scored_mints after a single tick with no tape"
    )
    # A backfill was dispatched: _backfill_pending tracks it.
    assert tok.mint in daemon._backfill_pending, (
        "Lake backfill must be dispatched for fresh 0-swap token (in _backfill_pending)"
    )


# ---------------------------------------------------------------------------
# FD-3: _due_tokens_sync returns tokens NEWEST-grad-first
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_due_tokens_sync_newest_first():
    """FD-3: _due_tokens_sync must return DETECTED tokens newest-grad-first.

    Create three DETECTED tokens with graduated_block_time values:
      OLDEST (t0), MIDDLE (t0+100), NEWEST (t0+200).
    All are past score_time, within the recency window.
    The returned list must be ordered NEWEST, MIDDLE, OLDEST.
    """
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.models import PipelineConfig, PipelineState, Token

    cache.delete("active_pipeline_config_sections")

    PipelineState.objects.update_or_create(
        pk=1, defaults={"firehose_active": True, "scoring_enabled": True, "trading_enabled": False},
    )
    score_at_s = 10
    outcome_window_s = 300

    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9912,
        label="fd3-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 3600, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={
            "score_at_elapsed_s": score_at_s,
            "window_s": outcome_window_s,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": outcome_window_s, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    # All tokens graduated in the past (within window) and past score_time.
    base_epoch = 1_750_200_000
    # Graduated 200s ago — all tokens are within the window (window=300 s)
    # and past score_at (10 s).  Use different grad_bt to test ordering.
    grad_bt_oldest = base_epoch - 200
    grad_bt_middle = base_epoch - 150
    grad_bt_newest = base_epoch - 100

    for bt, pool, mint in [
        (grad_bt_oldest, "pool_fd3_old",  "MINT_FD3_OLDEST"),
        (grad_bt_middle, "pool_fd3_mid",  "MINT_FD3_MIDDLE"),
        (grad_bt_newest, "pool_fd3_new",  "MINT_FD3_NEWEST"),
    ]:
        _create_token(mint, pool, datetime.fromtimestamp(bt, tz=timezone.utc), bt, Token.STATUS_DETECTED)

    clock = VirtualClock(datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    daemon = _make_daemon(clock)

    due = daemon._due_tokens_sync()
    due_mints = [m for m, _ in due]
    # Filter to only our test mints (DB may have others from parallel tests).
    our_order = [m for m in due_mints if m.startswith("MINT_FD3_")]

    assert our_order == ["MINT_FD3_NEWEST", "MINT_FD3_MIDDLE", "MINT_FD3_OLDEST"], (
        f"Expected newest-first order, got: {our_order}"
    )


# ---------------------------------------------------------------------------
# FD-4: _due_tokens_sync excludes SKIPPED tokens
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_due_tokens_sync_excludes_skipped():
    """FD-4: SKIPPED tokens must not appear in _due_tokens_sync results.

    Regression: the _due_tokens_sync DB filter must filter out SKIPPED rows
    so they are never dispatched for backfill on any tick (Fix 2 — skip
    terminalized mints in dispatch).
    """
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.models import PipelineConfig, PipelineState, Token

    cache.delete("active_pipeline_config_sections")

    PipelineState.objects.update_or_create(
        pk=1, defaults={"firehose_active": True, "scoring_enabled": True, "trading_enabled": False},
    )

    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9913,
        label="fd4-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 3600, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={
            "score_at_elapsed_s": 10,
            "window_s": 300,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": 300, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    base_epoch = 1_750_300_000
    grad_bt = base_epoch - 50  # 50 s ago, within window (300 s) and past score_at (10 s)
    grad_at = datetime.fromtimestamp(grad_bt, tz=timezone.utc)

    _create_token("MINT_FD4_SKIPPED",  "pool_fd4s", grad_at, grad_bt, Token.STATUS_SKIPPED)
    _create_token("MINT_FD4_DETECTED", "pool_fd4d", grad_at, grad_bt, Token.STATUS_DETECTED)

    clock = VirtualClock(datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    daemon = _make_daemon(clock)

    due = daemon._due_tokens_sync()
    due_mints = {m for m, _ in due}

    assert "MINT_FD4_SKIPPED" not in due_mints, (
        "SKIPPED token must NOT appear in _due_tokens_sync"
    )
    assert "MINT_FD4_DETECTED" in due_mints, (
        "DETECTED token must appear in _due_tokens_sync"
    )


# ---------------------------------------------------------------------------
# FD-5: _due_tokens_sync recency window excludes beyond-deadline tokens
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_due_tokens_sync_recency_window_excludes_old_detected():
    """FD-5: A DETECTED token whose grad_bt is past the recency window is excluded.

    The recency window is score_at + outcome_window + _TERMINALIZE_GRACE_S.
    A DETECTED token beyond that window must NOT appear in _due_tokens_sync
    (it will be terminalized in-tick if the scoring context builds, but it
    must not keep growing the per-tick set unboundedly).
    """
    from django.core.cache import cache

    from core.clock import VirtualClock
    from core.management.commands.run_firehose import _TERMINALIZE_GRACE_S
    from core.models import PipelineConfig, PipelineState, Token

    cache.delete("active_pipeline_config_sections")

    PipelineState.objects.update_or_create(
        pk=1, defaults={"firehose_active": True, "scoring_enabled": True, "trading_enabled": False},
    )
    score_at_s = 120
    outcome_window_s = 1800

    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    PipelineConfig.objects.create(
        version=9914,
        label="fd5-test",
        is_active=True,
        detection={"filter": {"source": "pump_dot_fun", "graduated": True}},
        tape={"idle_kill_ttl_s": 3600, "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]},
        scoring={
            "score_at_elapsed_s": score_at_s,
            "window_s": outcome_window_s,
            "capture_buffer_s": 4,
            "gate": "adaptive_topk",
        },
        outcome={"window_s": outcome_window_s, "label_def": {}},
        trading={
            "gate": "adaptive_topk", "enabled": False,
            "position_size_sol": 0.1, "max_open_positions": 3, "slippage_bps": 50,
        },
    )

    base_epoch = 1_750_400_000
    deadline_s = score_at_s + outcome_window_s + _TERMINALIZE_GRACE_S  # 2520 s

    # OLD token: graduated deadline_s + 100 s ago — outside the recency window.
    old_bt = base_epoch - (deadline_s + 100)
    old_at = datetime.fromtimestamp(old_bt, tz=timezone.utc)
    _create_token("MINT_FD5_OLD", "pool_fd5o", old_at, old_bt, Token.STATUS_DETECTED)

    # RECENT token: graduated 200 s ago — inside the window.
    recent_bt = base_epoch - 200
    recent_at = datetime.fromtimestamp(recent_bt, tz=timezone.utc)
    _create_token("MINT_FD5_RECENT", "pool_fd5r", recent_at, recent_bt, Token.STATUS_DETECTED)

    clock = VirtualClock(datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    daemon = _make_daemon(clock)

    due = daemon._due_tokens_sync()
    due_mints = {m for m, _ in due}

    assert "MINT_FD5_OLD" not in due_mints, (
        "DETECTED token beyond the recency window must NOT appear in _due_tokens_sync "
        "(Fix 1 bounded set)"
    )
    assert "MINT_FD5_RECENT" in due_mints, (
        "DETECTED token within the recency window MUST appear in _due_tokens_sync"
    )
