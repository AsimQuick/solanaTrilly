# ---
# module: core.tests.test_grad_liveness_watchdog
# sprint: hotfix-grad-liveness-watchdog
# story: hotfix-grad-liveness-watchdog
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: pytest, pytest-django, asyncio, threading, unittest.mock,
#               core.management.commands.run_firehose, core.models, core.clock
# ---
"""Graduation-liveness watchdog tests (deterministic, anchored to real DB).

Testing philosophy: every test here anchors to the real Django ORM / Postgres
(pytest-django, transaction=True) so the watchdog's DB reads exercise the same
code path as production.  The recovery function is injected so os._exit is
never called inside the test process.

The watchdog uses a PROGRESS-based staleness check, not an absolute-age check:
  - _last_progress_at is set to time.time() on start() — fresh grace window.
  - It advances to now() whenever MAX(graduated_at) increases (new graduation).
  - Recovery fires when now() - _last_progress_at > stale_seconds.

This avoids the restart-loop trap: on startup with a hours-old MAX in the DB,
the watchdog does NOT fire immediately — it only fires after stale_seconds of
no new graduation, giving detection time to recover.

Coverage:
  §1 PROGRESS exhausted + firehose_active=True  → recovery fires.
  §2 Fresh graduation (new MAX)                 → recovery does NOT fire.
  §3 firehose_active=False                      → recovery does NOT fire.
  §4 No Token rows yet                          → recovery does NOT fire.
  §5 STARTUP with stale MAX (restart scenario)  → recovery does NOT fire during
       grace window (the restart-loop bug test).
  §6 New graduation mid-window resets clock     → recovery does NOT fire even
       after the original stale window would have expired.
  §7 Crashed helius_task                        → recovery fires via _run_active.
  §8 Normal helius_task (cancel)                → recovery does NOT fire.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from core.management.commands.run_firehose import (
    GRAD_STALE_SECONDS,
    FirehoseDaemon,
    GraduationLivenessWatchdog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_watchdog(recovery_calls: list, *, stale_seconds: int = GRAD_STALE_SECONDS) -> GraduationLivenessWatchdog:
    """Build a watchdog whose recovery_fn appends to *recovery_calls* (no os._exit)."""
    def _recovery(no_progress_for_s: float) -> None:
        recovery_calls.append(no_progress_for_s)

    return GraduationLivenessWatchdog(
        stale_seconds=stale_seconds,
        check_interval_s=9999,  # we call _poll_once() directly — no real sleep
        recovery_fn=_recovery,
    )


def _set_firehose_active(active: bool) -> None:
    from core.models import PipelineState
    state = PipelineState.get()
    state.firehose_active = active
    state.save()


def _create_token(graduated_at: datetime) -> None:
    """Insert a minimal Token row with the given graduated_at timestamp.

    Supplies all NOT NULL columns so the DB constraint is satisfied.
    raw_graduation is the verbatim graduation event JSON — a minimal stub is
    sufficient for the watchdog (which only reads graduated_at, never raw_graduation).
    """
    from core.models import Token

    # Use a deterministic mint derived from the timestamp so multiple tokens
    # in the same test don't collide (update_or_create handles the duplicate case).
    mint = f"WATCHDOG_TEST_{int(graduated_at.timestamp())}"
    Token.objects.update_or_create(
        mint=mint,
        defaults={
            "pool_address": "POOL_TEST",
            "graduated_at": graduated_at,
            "graduated_block_time": int(graduated_at.timestamp()),
            "dex_source": "pump_amm",
            "raw_graduation": {"stub": True},
        },
    )


# ---------------------------------------------------------------------------
# §1 — Progress exhausted + firehose_active=True → recovery fires
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_progress_exhausted_fires_recovery():
    """No progress for > stale_seconds → recovery fires.

    Simulate the case where the watchdog has been running for stale_seconds
    with no new graduation: manually backdate _last_progress_at to stale_seconds
    ago and call _poll_once with a MAX(graduated_at) that has NOT advanced.
    This is the genuine dead-detection scenario.
    """
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Plant a token that graduated 2 hours ago (the "last seen" state in a dead scenario).
    old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=7200)
    _create_token(old_ts)

    # Simulate the watchdog having already observed this token on a previous poll.
    # The watchdog saw this MAX epoch and did NOT advance — so _last_progress_at
    # was not updated.  Backdate it to more than stale_seconds ago to trigger fire.
    watchdog._last_seen_max_epoch = old_ts.timestamp()  # already seen this token
    watchdog._last_progress_at = time.time() - 120  # 120s ago (> threshold of 60s)

    watchdog._poll_once()

    assert len(recovery_calls) == 1, (
        f"Expected recovery to fire when progress exhausted, got {recovery_calls}"
    )
    no_progress_reported = recovery_calls[0]
    assert no_progress_reported >= 60, (
        f"Reported no-progress duration {no_progress_reported}s should be >= threshold 60s"
    )


# ---------------------------------------------------------------------------
# §2 — Fresh graduation (new MAX) → recovery does NOT fire
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_fresh_graduation_does_not_fire():
    """A new graduation (MAX advances) → progress clock resets → recovery NOT called."""
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Token graduated just now — MAX is fresh and has never been seen before.
    fresh_ts = datetime.now(tz=timezone.utc)
    _create_token(fresh_ts)

    # Simulate the watchdog having seen an older epoch previously (progress advances).
    watchdog._last_seen_max_epoch = (fresh_ts - timedelta(seconds=300)).timestamp()
    watchdog._last_progress_at = time.time() - 90  # would fire WITHOUT the new grad

    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        f"Recovery should NOT fire when MAX graduated_at advances (new graduation), got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §3 — firehose_active=False → recovery does NOT fire (even with stale data)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_inactive_firehose_does_not_fire():
    """firehose_active=False → watchdog is quiescent even when progress is exhausted."""
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(False)

    # Token graduated 3 hours ago — definitely stale.
    stale_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=10800)
    _create_token(stale_ts)

    # Progress exhausted (no update for 120s > 60s threshold).
    watchdog._last_seen_max_epoch = stale_ts.timestamp()
    watchdog._last_progress_at = time.time() - 120

    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        f"Recovery must NOT fire when firehose_active=False, got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §4 — No Token rows → recovery does NOT fire (fresh daemon startup)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_no_token_rows_does_not_fire():
    """No Token rows in DB (daemon just started) → watchdog does not fire."""
    from core.models import Token

    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Ensure no tokens exist for a clean test.
    Token.objects.all().delete()

    # Even with a stale _last_progress_at, no tokens = startup grace.
    watchdog._last_progress_at = time.time() - 120

    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        f"Recovery must NOT fire when no Token rows exist, got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §5 — STARTUP with stale MAX (restart scenario) → no fire during grace window
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_startup_with_stale_max_does_not_fire_immediately():
    """Restart after outage: MAX(graduated_at) is hours old but _last_progress_at = now.

    This is the restart-loop bug scenario:
      - System just restarted after a real outage.
      - MAX(graduated_at) is hours old (last graduation was before the outage).
      - _last_progress_at is set to NOW in start() — full grace window.
      - The watchdog must NOT fire immediately.
      - It should only fire after stale_seconds of CONTINUED no-progress.

    We call _poll_once() multiple times to confirm:
      1. First poll: sees hours-old MAX for the first time (first observation →
         _last_seen_max_epoch updates, but since it was None, this counts as
         "first observation progress" → _last_progress_at advances to now).
      2. Second poll: MAX unchanged → no progress update; but since
         _last_progress_at is still ~now, no fire.
    """
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Hours-old graduation in the DB (the outage scenario).
    stale_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=7200)
    _create_token(stale_ts)

    # Simulate fresh start: _last_progress_at = NOW (as set in start()).
    # _last_seen_max_epoch = None (never polled before, as on a real restart).
    watchdog._last_progress_at = time.time()
    watchdog._last_seen_max_epoch = None

    # First poll: sees the hours-old MAX for the first time.
    # Since _last_seen_max_epoch was None, this is a "first observation"
    # and _last_progress_at advances. No fire.
    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        "Recovery must NOT fire on first poll after restart (first-observation grace), "
        f"got {recovery_calls}"
    )

    # Second poll: MAX unchanged (no new graduation). _last_progress_at was just
    # updated to ~now in the first poll (first-observation advances progress).
    # Still no fire because < 60s have elapsed since last progress.
    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        "Recovery must NOT fire on second poll shortly after restart, "
        f"got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §6 — New graduation mid-window resets clock → no fire after original window
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_new_graduation_mid_window_resets_progress_clock():
    """A new graduation arriving resets _last_progress_at; no fire follows.

    Scenario:
      1. Watchdog is stale (50s without progress, threshold=60s).
      2. A new graduation arrives (MAX advances).
      3. _last_progress_at resets to now.
      4. Even after the ORIGINAL 60s window has passed, no fire because the
         clock was reset.

    This proves the reset semantics are correct end-to-end.
    """
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Initial state: an old graduation that was already observed.
    old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=3600)
    _create_token(old_ts)
    watchdog._last_seen_max_epoch = old_ts.timestamp()
    # Nearly at the stale threshold — 50s without progress.
    watchdog._last_progress_at = time.time() - 50

    # Poll 1: MAX unchanged (no new graduation). No fire yet (50s < 60s).
    watchdog._poll_once()
    assert len(recovery_calls) == 0, f"Should not fire at 50s, got {recovery_calls}"

    # New graduation arrives — insert a NEWER token.
    new_ts = datetime.now(tz=timezone.utc)
    _create_token(new_ts)

    # Poll 2: MAX advances. _last_progress_at resets to ~now.
    watchdog._poll_once()
    assert len(recovery_calls) == 0, (
        f"Recovery should NOT fire when new graduation resets clock, got {recovery_calls}"
    )

    # Verify _last_progress_at was actually reset (within a few seconds of now).
    assert time.time() - watchdog._last_progress_at < 5.0, (
        "Expected _last_progress_at to be ~now after new graduation, but it was not updated"
    )

    # Poll 3: MAX unchanged again. But _last_progress_at is fresh, so no fire.
    watchdog._poll_once()
    assert len(recovery_calls) == 0, (
        f"Recovery must NOT fire immediately after a clock reset, got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §7 — Crashed helius_task → recovery fires via _run_active inspection
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_crashed_helius_task_triggers_recovery():
    """A helius_task that raises an exception → _run_active detects it and calls recovery.

    This proves the asyncio.gather return_exceptions=True swallowed-crash path
    is caught: the detection coroutine crashes, gather absorbs it silently, but
    _run_active's post-gather inspection fires the recovery function.

    Approach:
      - Build a minimal FirehoseDaemon whose _helius_loop raises RuntimeError.
      - Provide a fake _grad_watchdog_factory that injects a non-os._exit watchdog.
      - Run the daemon with max_runtime_s=0.5 and firehose_active=True.
      - Assert the injected recovery was called.
    """
    from core.clock import VirtualClock
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    recovery_calls: list = []

    def _recovery(no_progress_for_s: float) -> None:
        recovery_calls.append(("recovery", no_progress_for_s))

    class _CrashingDaemon(FirehoseDaemon):
        """FirehoseDaemon subclass where _helius_loop raises immediately."""

        async def _helius_loop(self) -> None:
            raise RuntimeError("simulated helius_loop crash")

        # Stub out the other loops so the test runs fast.
        async def _reconciler_loop(self) -> None:
            await asyncio.sleep(999)

        async def _scoring_loop(self) -> None:
            await asyncio.sleep(999)

        async def _postgrad_loop(self) -> None:
            await asyncio.sleep(999)

    # Inject a non-os._exit watchdog via _grad_watchdog_factory attribute.
    daemon = _CrashingDaemon(
        poll_interval_s=0.01,
        max_runtime_s=0.3,
        score_tick_s=999,
        postgrad_tick_s=999,
        clock=VirtualClock(datetime(2026, 6, 23, tzinfo=timezone.utc)),
        wallet_bank=None,
    )

    # Override the watchdog factory so we get a non-os._exit recovery.
    def _watchdog_factory():
        return GraduationLivenessWatchdog(
            stale_seconds=GRAD_STALE_SECONDS,
            check_interval_s=9999,  # no polling — crash path covers recovery
            recovery_fn=_recovery,
        )

    daemon._grad_watchdog_factory = _watchdog_factory

    asyncio.run(daemon.run())

    assert len(recovery_calls) >= 1, (
        f"Expected recovery to be called after helius_task crash, got {recovery_calls}"
    )
    assert recovery_calls[0][0] == "recovery", f"Unexpected recovery_calls: {recovery_calls}"


# ---------------------------------------------------------------------------
# §8 — Normal helius_task (cancelled cleanly) → recovery does NOT fire
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_normal_helius_cancel_does_not_trigger_recovery():
    """helius_task cancelled cleanly (CancelledError) → _run_active does NOT call recovery.

    The detection path ending via cancellation (normal shutdown: flip-to-False,
    SIGTERM, max_runtime) must NOT trigger the crash-recovery path.
    """
    from core.clock import VirtualClock
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    recovery_calls: list = []

    def _recovery(no_progress_for_s: float) -> None:
        recovery_calls.append(("recovery", no_progress_for_s))

    class _NormalDaemon(FirehoseDaemon):
        """FirehoseDaemon whose _helius_loop blocks until cancelled (normal behaviour)."""

        async def _helius_loop(self) -> None:
            # Block forever; will be cancelled by _run_active's shutdown.
            await asyncio.sleep(9999)

        async def _reconciler_loop(self) -> None:
            await asyncio.sleep(9999)

        async def _scoring_loop(self) -> None:
            await asyncio.sleep(9999)

        async def _postgrad_loop(self) -> None:
            await asyncio.sleep(9999)

    daemon = _NormalDaemon(
        poll_interval_s=0.01,
        max_runtime_s=0.2,
        score_tick_s=999,
        postgrad_tick_s=999,
        clock=VirtualClock(datetime(2026, 6, 23, tzinfo=timezone.utc)),
        wallet_bank=None,
    )

    def _watchdog_factory():
        return GraduationLivenessWatchdog(
            stale_seconds=GRAD_STALE_SECONDS,
            check_interval_s=9999,
            recovery_fn=_recovery,
        )

    daemon._grad_watchdog_factory = _watchdog_factory

    asyncio.run(daemon.run())

    assert len(recovery_calls) == 0, (
        f"Recovery must NOT fire when helius_task is cancelled cleanly, got {recovery_calls}"
    )
