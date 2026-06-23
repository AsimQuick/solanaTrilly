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

Coverage:
  §1 STALE + firehose_active=True  → recovery fires (the core correctness case).
  §2 FRESH graduation             → recovery does NOT fire.
  §3 firehose_active=False        → recovery does NOT fire (even with stale data).
  §4 No Token rows yet            → recovery does NOT fire (daemon just started).
  §5 Crashed helius_task          → recovery fires via _run_active inspection.
  §6 Normal helius_task (cancel)  → recovery does NOT fire via _run_active.
"""
from __future__ import annotations

import asyncio
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
    def _recovery(stale_age_s: float) -> None:
        recovery_calls.append(stale_age_s)

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
# §1 — STALE + firehose_active=True → recovery fires
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_stale_graduation_fires_recovery():
    """MAX(graduated_at) > GRAD_STALE_SECONDS ago + firehose_active=True → recovery called."""
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(True)

    # Plant a token graduated WELL beyond the stale threshold.
    stale_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=7200)  # 2 hours ago
    _create_token(stale_ts)

    watchdog._poll_once()

    assert len(recovery_calls) == 1, (
        f"Expected recovery to fire once on stale graduation, got {recovery_calls}"
    )
    stale_age_reported = recovery_calls[0]
    # The reported age should be close to 7200s (within a few seconds of test wall-clock drift).
    assert stale_age_reported >= 60, (
        f"Reported stale age {stale_age_reported}s should be >= threshold 60s"
    )


# ---------------------------------------------------------------------------
# §2 — FRESH graduation → recovery does NOT fire
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_fresh_graduation_does_not_fire():
    """MAX(graduated_at) within threshold → recovery NOT called."""
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=1800)

    _set_firehose_active(True)

    # Token graduated just 5 minutes ago — well within the 30-min threshold.
    fresh_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=300)
    _create_token(fresh_ts)

    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        f"Recovery should NOT fire for a fresh graduation, got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §3 — firehose_active=False → recovery does NOT fire (even with stale data)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_inactive_firehose_does_not_fire():
    """firehose_active=False → watchdog is quiescent even when graduation data is stale."""
    recovery_calls: list = []
    watchdog = _make_watchdog(recovery_calls, stale_seconds=60)

    _set_firehose_active(False)

    # Token graduated 3 hours ago — definitely stale.
    stale_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=10800)
    _create_token(stale_ts)

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

    watchdog._poll_once()

    assert len(recovery_calls) == 0, (
        f"Recovery must NOT fire when no Token rows exist, got {recovery_calls}"
    )


# ---------------------------------------------------------------------------
# §5 — Crashed helius_task → recovery fires via _run_active inspection
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

    def _recovery(stale_age_s: float) -> None:
        recovery_calls.append(("recovery", stale_age_s))

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
# §6 — Normal helius_task (cancelled cleanly) → recovery does NOT fire
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

    def _recovery(stale_age_s: float) -> None:
        recovery_calls.append(("recovery", stale_age_s))

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
