# ---
# module: core.tests.test_firehose_postgrad_collection_postgrad
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, asyncio, unittest.mock,
#               core.management.commands.run_firehose, core.firehose.spine,
#               core.tape.birdeye_swap_mapper, core.clock, core.models, trading.*
# ---
"""POST-GRAD swap-collection tests for the firehose daemon (offline, deterministic).

THE BUG (pre-fix): the paper-trade leg fed the settler the PRE-grad swaps with a
POST-grad entry_ts, so the settler always returned enterable=False ('dead') and
no paper position was ever booked.

THE FIX: a bounded post-grad collector inside the daemon opens a per-mint
BirdeyeSwapSource subscription, streams that mint's POST-grad PumpSwap swaps into
self._postgrad_tape, and the paper-trade leg settles over THAT tape.

These tests cover, all offline (a replay/mock BirdeyeSwapSource, no network):
  §1 canned post-grad swaps land in self._postgrad_tape incrementally + the
     subscription tears down at TTL (clock-driven, AC-2.2).
  §2 capacity bound: max=2, 3 graduated mints -> only 2 concurrent subscriptions
     open; the 3rd is logged "at-capacity skip".
  §3 end-to-end: gate passes + sufficient post-grad tape -> settle_paper_trade
     books a PAPER Position with ZERO real-order (trading.sender.Sender) calls.
  §4 deferral: gate passes but EMPTY post-grad tape -> no position booked,
     "awaiting post-grad swaps" logged, no crash.

Style note: this project drives coroutines with asyncio.run() inside sync test
functions (see test_tape_recorder_ac181.py) — it does NOT use pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import contextlib as _contextlib
import logging as _logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator

import pytest

from core.clock import Clock, VirtualClock
from core.datasource import DataSource
from core.management.commands.run_firehose import (
    FirehoseDaemon,
    _postgrad_event_to_swap,
    _swaps_to_trade_tuples,
)

_WSOL = "So11111111111111111111111111111111111111112"


# Loggers under core/ and trading/ are configured with propagate=False (see
# settings.LOGGING), so pytest's caplog (a root handler) never sees their records.
# This context manager temporarily turns propagation back on for ONE logger so the
# greppable [FIREHOSE] log lines can be asserted on, then restores it.
@_contextlib.contextmanager
def _propagate(logger_name: str, caplog=None):
    """Make a propagate=False logger observable by caplog for the duration.

    settings.LOGGING sets propagate=False on the ``core``/``trading`` loggers, so
    caplog's root handler never sees their records.  We both flip propagation on
    AND (belt-and-braces) attach caplog's own handler directly to the target
    logger, so the greppable [FIREHOSE] lines are always captured.
    """
    lg = _logging.getLogger(logger_name)
    prev_prop = lg.propagate
    prev_level = lg.level
    lg.propagate = True
    lg.setLevel(_logging.INFO)
    handler = getattr(caplog, "handler", None)
    attached = False
    if handler is not None and handler not in lg.handlers:
        lg.addHandler(handler)
        attached = True
    try:
        yield
    finally:
        if attached:
            lg.removeHandler(handler)
        lg.propagate = prev_prop
        lg.setLevel(prev_level)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _raw_birdeye_swap(mint: str, block_time: int, sig: str, side: str, price: float, vol_sol: float) -> dict:
    """A raw Birdeye SUBSCRIBE_TXS event map_birdeye_swap() can map cleanly.

    The token leg carries the mint; the quote leg is WSOL with uiAmount==vol_sol.
    """
    return {
        "tokenAddress": mint,
        "blockUnixTime": block_time,
        "blockNumber": block_time,  # slot stand-in (monotone)
        "txHash": sig,
        "side": side,
        "tokenPrice": price,
        "volumeUSD": vol_sol * 140.0,
        "owner": "owner_" + sig,
        "from": {"address": mint, "uiAmount": 1000.0, "price": price},
        "to": {"address": _WSOL, "uiAmount": vol_sol, "price": 1.0},
    }


class _ReplaySwapSource(DataSource):
    """Replay stand-in for BirdeyeSwapSource: yields canned raw Birdeye events.

    After yielding every seeded event it sets *streamed_all* and then awaits a
    hold event that is never set — emulating a live WS that stays open until the
    subscription's TTL expires (or the task is cancelled on shutdown).  Tracks
    connect()/disconnect() so the teardown assertion is exact.
    """

    def __init__(self, events: list[dict]) -> None:
        self._events = events
        self.streamed_all = asyncio.Event()
        self._hold = asyncio.Event()  # never set -> stays "live"
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        for e in self._events:
            yield e
            await asyncio.sleep(0)
        self.streamed_all.set()
        await self._hold.wait()


class _AdvancingClock(Clock):
    """A VirtualClock that advances a fixed step on each now() read.

    Lets a TTL deadline be crossed deterministically inside an otherwise live
    (never-ending) post-grad stream — no wall clock, no real sleep (AC-2.2).
    """

    def __init__(self, start: datetime, step_s: float) -> None:
        self._t = start
        self._step = timedelta(seconds=step_s)

    def now(self) -> datetime:
        cur = self._t
        self._t = self._t + self._step
        return cur


# ---------------------------------------------------------------------------
# §0 — pure adapter: raw Birdeye event -> settler swap dict shape
# ---------------------------------------------------------------------------


def test_postgrad_event_to_swap_anchors_rel_to_graduation():
    grad_bt = 1000
    raw = _raw_birdeye_swap("MintX", block_time=1060, sig="p1", side="buy", price=0.002, vol_sol=3.0)
    swap = _postgrad_event_to_swap(raw, "MintX", grad_bt)
    assert swap is not None
    assert swap["block_time"] == 1060
    assert swap["rel"] == float(1060 - grad_bt)  # post-grad => rel > 0
    assert swap["price"] == 0.002
    assert swap["side"] == "buy"
    # Maps to the (block_time, price, usd) tuple shape the settler walks.
    tuples = _swaps_to_trade_tuples([swap])
    assert tuples == [(1060.0, 0.002, swap["vol_usd"])]


def test_postgrad_event_to_swap_returns_none_for_unmappable():
    assert _postgrad_event_to_swap({"garbage": True}, "MintX", 1000) is None


# ---------------------------------------------------------------------------
# §1 — canned post-grad swaps land in the tape + the subscription tears down at TTL
# ---------------------------------------------------------------------------


def test_postgrad_swaps_land_in_tape_and_subscription_tears_down_at_ttl():
    grad_bt = 1000
    mint = "MintTTL"
    # Three post-grad swaps (block_time > grad_bt => rel > 0).
    events = [
        _raw_birdeye_swap(mint, 1030, "p1", "buy", 0.001, 2.0),
        _raw_birdeye_swap(mint, 1040, "p2", "buy", 0.0011, 1.0),
        _raw_birdeye_swap(mint, 1050, "p3", "sell", 0.0012, 1.5),
    ]
    source = _ReplaySwapSource(events)

    daemon = FirehoseDaemon(
        postgrad_factory=lambda m: source,
        # Clock advances 1s per read; after a few reads inside the stream the
        # TTL (3s) is crossed and the subscription tears down deterministically.
        clock=_AdvancingClock(datetime(2026, 6, 19, tzinfo=timezone.utc), step_s=1.0),
    )

    async def _drive():
        task = asyncio.create_task(daemon._postgrad_subscription(mint, grad_bt, ttl_s=3.0))
        await asyncio.wait_for(task, timeout=2.0)  # returns when TTL crossed
        return daemon._postgrad_tape.get(mint), source.connected, source.disconnected

    stored, connected, disconnected = asyncio.run(_drive())

    assert connected is True
    assert disconnected is True, "subscription must disconnect the source in finally"
    # At least the swaps streamed before the TTL deadline are stored; all are post-grad.
    assert len(stored) >= 1
    assert all(s["rel"] >= 0 for s in stored)
    assert all(s["block_time"] > grad_bt for s in stored)


def test_postgrad_subscription_cancellation_disconnects_source():
    """Shutdown (task cancel) tears the subscription down + disconnects cleanly."""
    mint = "MintCancel"
    source = _ReplaySwapSource([_raw_birdeye_swap(mint, 1030, "p1", "buy", 0.001, 2.0)])
    daemon = FirehoseDaemon(
        postgrad_factory=lambda m: source,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),  # never crosses TTL
    )

    async def _drive():
        task = asyncio.create_task(daemon._postgrad_subscription(mint, 1000, ttl_s=10_000.0))
        await asyncio.wait_for(source.streamed_all.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return source.disconnected

    assert asyncio.run(_drive()) is True


# ---------------------------------------------------------------------------
# §2 — capacity bound (max concurrent subscriptions)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_capacity_bound_only_max_concurrent_subscriptions(caplog):
    """max=2 + 3 graduated mints -> only 2 concurrent subs; the 3rd is skipped+logged."""
    import logging

    from core.models import PipelineState, Token

    state = PipelineState.get()
    state.firehose_active = True
    state.save()

    grad_bt = 1000
    mints = ["MintA", "MintB", "MintC"]
    for i, m in enumerate(mints):
        Token.objects.create(
            mint=m,
            pool_address="pool_" + m,
            graduated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            graduated_block_time=grad_bt + i,  # graduation order A<B<C
            dex_source="pumpswap",
            raw_graduation={"mint": m},
        )

    # Each subscription source stays "live" forever (never crosses TTL), so all
    # opened subscriptions stay OPEN — exposing the concurrency cap.
    sources: dict[str, _ReplaySwapSource] = {}

    def _factory(mint: str) -> _ReplaySwapSource:
        src = _ReplaySwapSource([_raw_birdeye_swap(mint, grad_bt + 30, "p1", "buy", 0.001, 2.0)])
        sources[mint] = src
        return src

    daemon = FirehoseDaemon(
        postgrad_factory=_factory,
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),  # never crosses huge TTL
    )

    async def _drive():
        # Patch the plan builder used inside _postgrad_tick.
        import unittest.mock as mock


        with mock.patch.object(
            FirehoseDaemon, "_postgrad_plan_sync",
            lambda self: (2, 10_000.0, [(m, grad_bt + i) for i, m in enumerate(mints)]),
        ):
            with _propagate("core.management.commands.run_firehose", caplog), \
                    caplog.at_level(logging.INFO):
                await daemon._postgrad_tick()  # first pass: opens A and B, skips C
                # Capacity is decided synchronously in the tick (tasks tracked in
                # _postgrad_tasks); record it before the tasks even start running.
                n_active = len(daemon._postgrad_tasks)
                # Let the two opened subscription tasks actually start + stream so
                # the factory runs and each source reaches its (open) hold state.
                for m in ("MintA", "MintB"):
                    for _ in range(50):
                        if m in sources:
                            break
                        await asyncio.sleep(0)
                    await asyncio.wait_for(sources[m].streamed_all.wait(), timeout=2.0)
                return n_active

    try:
        n_active = asyncio.run(_drive())
    finally:
        # Clean up the still-open subscription tasks.
        async def _cleanup():
            await daemon._cancel_postgrad_subscriptions()
        asyncio.run(_cleanup())

    assert n_active == 2, "exactly two concurrent post-grad subscriptions allowed at max=2"
    assert "MintC" not in daemon._postgrad_tasks, "the 3rd mint must NOT be subscribed at capacity"
    assert any("at-capacity skip mint=MintC" in r.getMessage() for r in caplog.records), (
        "the skipped mint must be logged, never silently dropped"
    )
    # MintC was NOT marked seen -> it is retried on a later tick (no permanent drop).
    assert "MintC" not in daemon._postgrad_seen


# ---------------------------------------------------------------------------
# §3 — end-to-end: gate passes + sufficient post-grad tape -> PAPER position, no real send
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_end_to_end_books_paper_position_with_zero_real_order_calls(caplog):
    """Sufficient post-grad tape -> settle_paper_trade books a PAPER Position row.

    Asserts the real-order path (trading.sender.Sender) is NEVER instantiated.
    """
    import logging
    import unittest.mock as mock

    from trading.models import Position, TradingSettings

    grad_bt = 1000
    score_at = 60
    entry_ts = float(grad_bt + score_at)  # = 1060

    # A post-grad tape that the settler can ENTER: a quote swap in [entry-30, entry],
    # a fill swap at entry+2, then a rising walk so a TP/timer exit is reached.
    # (offset_s, sig, side, price, vol_sol)
    plan = [
        (-5, "q", "buy", 0.0010, 2.0),
        (2, "f", "buy", 0.0010, 2.0),
        (30, "x1", "buy", 0.0020, 3.0),
        (90, "x2", "sell", 0.0030, 4.0),
    ]
    post_swaps = [
        _postgrad_event_to_swap(
            _raw_birdeye_swap("MintE", int(entry_ts + off), sig, side, price, vol),
            "MintE", grad_bt,
        )
        for (off, sig, side, price, vol) in plan
    ]
    trades = _swaps_to_trade_tuples([s for s in post_swaps if s is not None])

    trading_cfg = TradingSettings.get().to_schema()
    clock = VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc))

    daemon = FirehoseDaemon(clock=clock)


    before = Position.objects.count()

    # Sentinel: if a real send path is ever touched, the test fails loudly.
    sentinel = AssertionError("real-order Sender must NOT be used on the paper path")
    with mock.patch("trading.sender.Sender", side_effect=sentinel):
        with _propagate("core.firehose.spine", caplog), caplog.at_level(logging.INFO):
            daemon._paper_trade_sync(
                "MintE", 0.95, trades, entry_ts, trading_cfg,
                size_sol=0.1, sol_usd=140.0, trading_enabled=False,
            )

    after = Position.objects.count()
    assert after == before + 1, "a PAPER position must be booked from the post-grad tape"

    pos = Position.objects.filter(mint="MintE").latest("id")
    # Booked as a PAPER/observe row then settled -> status flips to CLOSED.
    assert pos.status == Position.STATUS_CLOSED
    assert pos.mode == Position.MODE_OBSERVE
    assert pos.source == Position.SOURCE_MODEL
    assert pos.closed_at is not None, "the paper position must be settled (closed) over the post-grad walk"
    # Greppable buy + sell lines fired.
    msgs = [r.getMessage() for r in caplog.records]
    assert any("paper-buy: mint=MintE" in m for m in msgs)
    assert any("paper-sell: mint=MintE" in m for m in msgs)


@pytest.mark.django_db(transaction=True)
def test_paper_trade_refuses_when_trading_enabled_true():
    """HARD SAFETY: the paper path raises if trading_enabled is True (assert_paper_only)."""
    from core.firehose.spine import RealCapitalGuardError
    from trading.models import TradingSettings

    daemon = FirehoseDaemon(clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)))
    trading_cfg = TradingSettings.get().to_schema()
    with pytest.raises(RealCapitalGuardError):
        daemon._paper_trade_sync(
            "MintGuard", 0.95, [(1060.0, 0.001, 100.0)], 1060.0, trading_cfg,
            size_sol=0.1, sol_usd=140.0, trading_enabled=True,
        )


# ---------------------------------------------------------------------------
# §4 — deferral: gate passes but EMPTY post-grad tape -> no position, logged, no crash
# ---------------------------------------------------------------------------


def test_postgrad_enterable_defers_on_empty_tape():
    """The pre-check returns False for an empty / insufficient post-grad tape."""
    entry_ts = 1060.0
    scoring = {"gate": "adaptive_topk", "score_at_elapsed_s": 60}
    # Empty -> defer.
    assert FirehoseDaemon._postgrad_enterable([], entry_ts, scoring) is False
    # Only a quote, no fill -> defer.
    quote_only = [_postgrad_event_to_swap(_raw_birdeye_swap("M", 1055, "q", "buy", 0.001, 2.0), "M", 1000)]
    assert FirehoseDaemon._postgrad_enterable(quote_only, entry_ts, scoring) is False
    # Quote + fill -> enterable.
    full = quote_only + [
        _postgrad_event_to_swap(_raw_birdeye_swap("M", 1062, "f", "buy", 0.001, 2.0), "M", 1000)
    ]
    assert FirehoseDaemon._postgrad_enterable(full, entry_ts, scoring) is True


@pytest.mark.django_db(transaction=True)
def test_score_tick_defers_paper_when_postgrad_tape_empty(caplog):
    """gate passes but the post-grad tape is empty -> no Position, 'awaiting' logged, no crash."""
    import logging
    import unittest.mock as mock

    from trading.models import Position, TradingSettings

    grad_bt = 1000
    score_at = 60
    mint = "MintDefer"

    # PRE-grad tape good enough to assemble features + score (rel < 0 => pre-grad).
    # (block_time, sig, side, owner, vol, price)
    pre_rows = [
        (900, "s1", "buy", "A", 2.0, 0.0010),
        (910, "s2", "buy", "B", 1.0, 0.0011),
        (920, "s3", "buy", "C", 3.0, 0.0012),
        (930, "s4", "sell", "A", 1.5, 0.0013),
        (940, "s5", "buy", "A", 0.5, 0.0014),
        (950, "s6", "buy", "D", 4.0, 0.0015),
    ]
    pre_tape = [
        {
            "block_time": bt, "slot": i + 1, "signature": sig, "side": side,
            "owner": owner, "vol": vol, "price": price, "rel": float(bt - grad_bt),
        }
        for i, (bt, sig, side, owner, vol, price) in enumerate(pre_rows)
    ]

    class _StubScorer:
        labels = ["ctrl", "oracle", "liq"]

        def score_pool(self, fl):
            return [{"label_scores": {x: 0.99 for x in self.labels},
                     "label_ranks": {x: 0.99 for x in self.labels},
                     "blend_score": 0.99} for _ in fl]

        def score_single(self, f, ref):
            return self.score_pool([f])[0]

    trading_cfg = TradingSettings.get().to_schema()
    daemon = FirehoseDaemon(clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)))
    daemon._tape.add(mint, pre_tape[0])
    for s in pre_tape[1:]:
        daemon._tape.add(mint, s)
    # POST-grad tape intentionally EMPTY.

    # Drive a single _score_tick with patched DB/scoring seams (no real model/config).
    async def _drive():
        with mock.patch.object(FirehoseDaemon, "_read_state", new=_async_const((True, True, False))), \
             mock.patch.object(FirehoseDaemon, "_due_tokens_sync", lambda self: [(mint, grad_bt)]), \
             mock.patch.object(
                 FirehoseDaemon, "_build_scoring_context_sync",
                 lambda self: (_StubScorer(), None,
                               {"gate": "adaptive_topk", "score_at_elapsed_s": score_at},
                               trading_cfg, 0.1, 140.0, 0.8),
             ):
            with _propagate("core.management.commands.run_firehose", caplog), \
                    caplog.at_level(logging.INFO):
                await daemon._score_tick()

    before = Position.objects.count()
    asyncio.run(_drive())
    after = Position.objects.count()

    assert after == before, "no position may be booked while the post-grad tape is empty"
    assert any("awaiting post-grad swaps" in r.getMessage() for r in caplog.records), (
        "the deferral must be logged"
    )
    # Deferred -> NOT marked scored -> retried on a later tick.
    assert mint not in daemon._scored_mints


def _async_const(value):
    """Return an async method that ignores args and returns *value* (for patching)."""

    async def _method(self, *args, **kwargs):
        return value

    return _method
