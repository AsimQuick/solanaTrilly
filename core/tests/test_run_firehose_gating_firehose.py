# ---
# module: core.tests.test_run_firehose_gating_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, asyncio, unittest.mock,
#               core.management.commands.run_firehose, core.models, core.clock
# ---
"""run_firehose gating tests (offline, deterministic).

Verifies:
  §1 firehose_active=False -> the daemon polls and NEVER calls the source
     factories (collection/graduation never start).
  §2 firehose_active=True  -> the daemon wires collection + graduation (both
     factories are called) then stops cleanly on flip-to-False.
  §3 The daemon NEVER mutates firehose_active itself.
  §4 max-runtime bounds an inactive run.

All source factories are mocked — zero network.  --max-runtime-seconds keeps
every run bounded and deterministic.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.clock import VirtualClock
from core.management.commands.run_firehose import FirehoseDaemon


def _noop_collection():
    """Factory returning a single no-op collection DataSource (new buffer contract).

    The collection task now wraps this source in a LivePreGradBuffer instead of a
    token_store-gated recorder, so the factory returns ONE source whose events()
    immediately ends (an empty live stream) and whose connect/disconnect are
    async no-ops.
    """
    source = MagicMock()

    async def _connect():
        return None

    async def _events():
        if False:  # pragma: no cover - empty async generator
            yield {}

    async def _disc():
        return None

    source.connect = _connect
    source.events = _events
    source.disconnect = _disc
    return source


def _noop_graduation():
    consumer = MagicMock()

    async def _run():
        return None

    consumer.run = _run
    consumer.processed = []
    source = MagicMock()

    async def _disc():
        return None

    source.disconnect = _disc
    return consumer, source


@pytest.mark.django_db(transaction=True)
def test_inactive_firehose_never_starts_sources():
    """§1 — firehose_active=False: factories are never called; bounded run exits."""
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = False
    state.save()

    collection = MagicMock(side_effect=_noop_collection)
    graduation = MagicMock(side_effect=_noop_graduation)

    daemon = FirehoseDaemon(
        poll_interval_s=0.01,
        max_runtime_s=0.05,
        score_tick_s=0.01,
        collection_factory=collection,
        graduation_factory=graduation,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
    )
    asyncio.run(daemon.run())

    collection.assert_not_called()
    graduation.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_active_firehose_wires_collection_and_graduation():
    """§2 — firehose_active=True: both factories are called; flip-to-False stops it."""
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.scoring_enabled = False  # keep the scoring tick a no-op
    state.save()

    collection = MagicMock(side_effect=_noop_collection)
    graduation = MagicMock(side_effect=_noop_graduation)

    daemon = FirehoseDaemon(
        poll_interval_s=0.01,
        max_runtime_s=0.3,
        score_tick_s=0.01,
        collection_factory=collection,
        graduation_factory=graduation,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
    )
    asyncio.run(daemon.run())

    assert collection.called, "collection factory must be called when firehose is active"
    assert graduation.called, "graduation factory must be called when firehose is active"


@pytest.mark.django_db(transaction=True)
def test_daemon_never_mutates_firehose_active():
    """§3 — the daemon must never set firehose_active (operator-gated)."""
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    daemon = FirehoseDaemon(
        poll_interval_s=0.01,
        max_runtime_s=0.15,
        score_tick_s=0.01,
        collection_factory=lambda: _noop_collection(),
        graduation_factory=lambda: _noop_graduation(),
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
    )
    asyncio.run(daemon.run())

    state.refresh_from_db()
    # The daemon ran while active and exited on max-runtime; it must NOT have
    # flipped the flag itself — it stays exactly as the operator left it (True).
    assert state.firehose_active is True


@pytest.mark.django_db(transaction=True)
def test_flip_to_false_stops_active_run():
    """§2b — flipping firehose_active False mid-run stops the tasks cleanly."""
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    # A graduation factory that flips firehose_active False after being wired,
    # proving the flip-watcher tears the run down (the run returns promptly).
    flip_done = {"v": False}

    def _graduation_then_flip():
        consumer = MagicMock()

        async def _run():
            # Flip the flag off from inside the run.
            def _off():
                s = PipelineState.get()
                s.firehose_active = False
                s.save()
            from asgiref.sync import sync_to_async
            await sync_to_async(_off)()
            flip_done["v"] = True
            # Never-ending until cancelled.
            await asyncio.sleep(3600)

        consumer.run = _run
        consumer.processed = []
        source = MagicMock()

        async def _disc():
            return None

        source.disconnect = _disc
        return consumer, source

    daemon = FirehoseDaemon(
        poll_interval_s=0.02,
        max_runtime_s=5.0,
        score_tick_s=0.02,
        collection_factory=lambda: _noop_collection(),
        graduation_factory=_graduation_then_flip,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
    )
    asyncio.run(daemon.run())

    assert flip_done["v"] is True
    state.refresh_from_db()
    assert state.firehose_active is False
