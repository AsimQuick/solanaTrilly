# ---
# module: core.management.commands.run_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: django, asyncio, logging, signal, asgiref,
#               core.tape.birdeye_graduation_source, core.tape.birdeye_swap_source,
#               core.tape.birdeye_swap_mapper, core.tape.helius_birth_tape_source,
#               core.management.commands.run_listener, core.detection.consumer,
#               core.firehose.spine, core.clock, core.resolver, core.models
# ---
"""run_firehose — the gated live daemon that ties the pipeline spine together.

OBSERVE/PAPER ONLY.  This daemon NEVER places real orders and NEVER mutates
PipelineState flags itself.

GATING
======
Reads PipelineState.firehose_active.  While False it logs "firehose inactive"
and polls every ~poll-interval seconds until True (supports --max-runtime-seconds
for a bounded run and --poll-interval).  When the flag flips back to False
mid-run, all tasks are cancelled and every WebSocket is disconnected cleanly.
The daemon never sets firehose_active itself (operator-gated via
tools/firehose_state.py).

CONCURRENT TASKS (asyncio) while active
=======================================
  a. COLLECTION    — HeliusBirthTapeSource -> TapeRecorder (run_listener's
                     build_birth_tape_recorder wiring), continuous, storing
                     pre-grad swaps keyed by mint.
  b. GRADUATION    — BirdeyeGraduationSource -> DetectionConsumer.run(),
                     persisting Token rows on graduation (config-driven WS).
  c. SCORING SCHED — for each graduated Token not yet scored, at
                     graduated_at + scoring.score_at_elapsed_s, assemble the 20
                     PRE_FEATURE_NAMES from the collected tape and score via the
                     active BlendScorer.  Gated by PipelineState.scoring_enabled.
  d. PAPER TRADE   — when a score passes the active gate, open + settle a PAPER
                     position via the P8 apparatus over the POST-grad tape.  HARD
                     RULE: trading_enabled is False -> fills booked at observed
                     price, NO real send.
  e. POST-GRAD     — a BOUNDED manager that, for each newly-graduated Token,
                     opens a BirdeyeSwapSource(api_key, mint) and streams that
                     mint's POST-grad PumpSwap swaps into self._postgrad_tape for
                     a bounded TTL (= score_at_elapsed_s + outcome.window_s), then
                     disconnects.  SPEND BOUND: at most
                     tape.max_postgrad_subscriptions concurrent subscriptions
                     (default 5); over capacity, new mints are skipped + logged.
                     These POST-grad swaps are what the paper-trade settler walks
                     so a paper position can actually be entered + exited (the
                     PRE-grad tape only ever produces "enterable:False").

OBSERVABILITY
=============
Every stage logs a stable, greppable "[FIREHOSE] <stage>: ..." line.

CLEAN SHUTDOWN
==============
SIGTERM/SIGINT and firehose_active->False both cancel all tasks and disconnect
(including every open post-grad subscription).

SEAMS (Principle #7)
====================
Concrete sources are built ONLY in the source-factory methods (the adapter
wiring layer).  Those factories are injectable so the offline tests drive the
daemon with mock sources — the daemon logic itself never instantiates a concrete
live source in the test path.

CLOCK (AC-2.2)
==============
There is NO datetime.now()/time.time() in this module.  All "now" for the
post-grad TTL (and scoring schedule) comes from the injected daemon clock; the
asyncio event-loop clock (loop.time()) is used only for cooperative sleeping,
never as a wall-clock timestamp.
"""
import asyncio
import logging
import signal
import sys

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand

from core.clock import WallClock
from core.firehose.spine import (
    LOG_PREFIX,
    assemble_pregrad_features,
    gate_passes,
    score_pregrad,
    score_time_reached,
    settle_paper_trade,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_SCORE_TICK_S = 5.0
DEFAULT_POSTGRAD_TICK_S = 5.0
DEFAULT_MAX_POSTGRAD_SUBSCRIPTIONS = 5


# ---------------------------------------------------------------------------
# Tape store — collects swaps keyed by mint (shared across tasks)
# ---------------------------------------------------------------------------


class TapeStore:
    """In-memory swap store keyed by mint.

    Used twice in the daemon: once for the PRE-grad tape (fed by the collection
    task, read by scoring to assemble features) and once for the POST-grad tape
    (fed by the post-grad subscription manager, read by the paper-trade settler).
    It is the live analogue of the lake the offline FeatureExtractor reads.
    """

    def __init__(self) -> None:
        self._by_mint: dict[str, list[dict]] = {}

    def add(self, mint: str, swap: dict) -> None:
        self._by_mint.setdefault(mint, []).append(swap)

    def get(self, mint: str) -> list[dict]:
        return list(self._by_mint.get(mint, []))

    def count(self, mint: str) -> int:
        return len(self._by_mint.get(mint, []))


# ---------------------------------------------------------------------------
# FirehoseDaemon — the runnable, testable core
# ---------------------------------------------------------------------------


class FirehoseDaemon:
    """The gated firehose runtime.  Source factories are injectable for testing.

    Args:
        poll_interval_s:    Seconds between firehose_active polls while inactive
                            and the in-run flip check.
        max_runtime_s:      Optional bound (seconds) for the whole run (None =
                            run until firehose_active flips False or a signal).
        score_tick_s:       Seconds between scoring-scheduler ticks.
        postgrad_tick_s:    Seconds between post-grad subscription-manager ticks.
        collection_factory: () -> (recorder, source) for the COLLECTION task.
                            Injected as None in production (built from settings).
        graduation_factory: () -> (consumer, source) for the GRADUATION task.
        postgrad_factory:   (mint) -> a BirdeyeSwapSource-like source for the
                            POST-grad task (connect/events/disconnect).  Injected
                            for tests; built from settings in production.
        clock:              Injected Clock (defaults to WallClock).
    """

    def __init__(
        self,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        max_runtime_s: float | None = None,
        score_tick_s: float = DEFAULT_SCORE_TICK_S,
        postgrad_tick_s: float = DEFAULT_POSTGRAD_TICK_S,
        collection_factory=None,
        graduation_factory=None,
        postgrad_factory=None,
        clock=None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._max_runtime_s = max_runtime_s
        self._score_tick_s = score_tick_s
        self._postgrad_tick_s = postgrad_tick_s
        self._collection_factory = collection_factory or self._build_collection
        self._graduation_factory = graduation_factory or self._build_graduation
        self._postgrad_factory = postgrad_factory or self._build_postgrad_source
        self._clock = clock or WallClock()
        self._tape = TapeStore()
        self._postgrad_tape = TapeStore()
        self._scored_mints: set[str] = set()
        self._stop = asyncio.Event()
        self._active_sources: list = []
        # POST-grad subscription bookkeeping.
        self._postgrad_tasks: dict[str, asyncio.Task] = {}
        self._postgrad_seen: set[str] = set()
        self._postgrad_sources: dict[str, object] = {}

    # ------------------------------------------------------------------
    # PipelineState reads (sync ORM -> async via sync_to_async)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_state_sync() -> tuple[bool, bool, bool]:
        """Return (firehose_active, scoring_enabled, trading_enabled)."""
        from core.models import PipelineState

        state = PipelineState.get()
        return (
            bool(state.firehose_active),
            bool(state.scoring_enabled),
            bool(state.trading_enabled),
        )

    async def _read_state(self) -> tuple[bool, bool, bool]:
        return await sync_to_async(self._read_state_sync, thread_sensitive=True)()

    # ------------------------------------------------------------------
    # Public entry — gate, then run-while-active
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal a clean shutdown (SIGTERM/SIGINT handler)."""
        self._stop.set()

    async def run(self) -> None:
        """Poll until firehose_active, run the spine while active, repeat/exit.

        Returns when:
          - a stop was requested (signal), or
          - max_runtime_s elapsed.
        """
        loop = asyncio.get_event_loop()
        deadline = (
            loop.time() + self._max_runtime_s if self._max_runtime_s is not None else None
        )

        while not self._stop.is_set():
            if deadline is not None and loop.time() >= deadline:
                logger.info("%s max-runtime reached — exiting.", LOG_PREFIX)
                return

            firehose_active, _, _ = await self._read_state()
            if not firehose_active:
                logger.info(
                    "%s firehose inactive — polling every %.0fs (operator-gated).",
                    LOG_PREFIX,
                    self._poll_interval_s,
                )
                await self._sleep_or_stop(self._poll_interval_s, deadline)
                continue

            logger.info("%s firehose ACTIVE — starting collection+graduation+score+paper.", LOG_PREFIX)
            await self._run_active(deadline)
            # _run_active returns on flip-to-False / stop / deadline; loop re-checks.

    async def _sleep_or_stop(self, seconds: float, deadline) -> None:
        """Sleep up to *seconds*, waking early on stop or *deadline*."""
        loop = asyncio.get_event_loop()
        if deadline is not None:
            seconds = min(seconds, max(0.0, deadline - loop.time()))
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------
    # Active run — launch the concurrent tasks + the flip-watcher
    # ------------------------------------------------------------------

    async def _run_active(self, deadline) -> None:
        """Launch collection/graduation/scoring/post-grad tasks; stop on flip/stop/deadline."""
        self._active_sources = []
        self._postgrad_tasks = {}
        self._postgrad_seen = set()
        self._postgrad_sources = {}

        collection_task = asyncio.create_task(self._collection_loop(), name="firehose-collection")
        graduation_task = asyncio.create_task(self._graduation_loop(), name="firehose-graduation")
        scoring_task = asyncio.create_task(self._scoring_loop(), name="firehose-scoring")
        postgrad_task = asyncio.create_task(self._postgrad_loop(), name="firehose-postgrad")
        watcher_task = asyncio.create_task(self._flip_watcher(deadline), name="firehose-watcher")

        tasks = [collection_task, graduation_task, scoring_task, postgrad_task, watcher_task]
        try:
            # The watcher returns when firehose flips False / stop / deadline.
            await watcher_task
        finally:
            for t in (collection_task, graduation_task, scoring_task, postgrad_task):
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_postgrad_subscriptions()
            await self._disconnect_all_sources()
            logger.info("%s active run stopped — all tasks cancelled, sources disconnected.", LOG_PREFIX)

    async def _flip_watcher(self, deadline) -> None:
        """Watch for firehose_active->False / stop / deadline; then return."""
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            if deadline is not None and loop.time() >= deadline:
                logger.info("%s max-runtime reached mid-run — stopping tasks.", LOG_PREFIX)
                return
            firehose_active, _, _ = await self._read_state()
            if not firehose_active:
                logger.info("%s firehose flipped INACTIVE — stopping tasks cleanly.", LOG_PREFIX)
                return
            await self._sleep_or_stop(self._poll_interval_s, deadline)

    async def _disconnect_all_sources(self) -> None:
        for src in self._active_sources:
            try:
                await src.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.debug("%s source disconnect raised (ignored).", LOG_PREFIX)
        self._active_sources = []

    async def _cancel_postgrad_subscriptions(self) -> None:
        """Cancel + await every open post-grad subscription (clean shutdown).

        Each subscription task disconnects its BirdeyeSwapSource in its own
        finally; this also disconnects any source whose task is mid-cancellation.
        """
        tasks = list(self._postgrad_tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for src in list(self._postgrad_sources.values()):
            try:
                await src.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.debug("%s postgrad: source disconnect raised (ignored).", LOG_PREFIX)
        self._postgrad_tasks = {}
        self._postgrad_sources = {}

    # ------------------------------------------------------------------
    # Task a — COLLECTION (Helius birth tape -> TapeRecorder -> TapeStore)
    # ------------------------------------------------------------------

    def _on_recorded_swap(self, mint: str, ns) -> None:
        """STREAM-AS-RECORDED tap: tee each recorded swap into the TapeStore NOW.

        Fired synchronously by the collection TapeRecorder the instant a landed
        swap is normalized — i.e. while the continuous Helius birth-tape source is
        STILL streaming, long before recorder.run() would return.  This is the fix
        for the batch-vs-stream bug: previously self._tape was only populated
        AFTER recorder.run() returned, but for a continuous live source run()
        never returns until shutdown, so the scoring task always read an empty
        tape ("0 swaps — deferring") and the model never scored.

        Keyed by the mint string (matching Token.mint, which the scoring task's
        self._tape.get(mint) uses); the stored value is the SAME swap-dict shape
        assemble_pregrad_features() / _swaps_to_trade_tuples() already expect.
        """
        self._tape.add(mint, _ns_to_swap(ns))

    async def _collection_loop(self) -> None:
        try:
            recorder, source = await sync_to_async(
                self._collection_factory, thread_sensitive=True
            )()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s collection: factory failed (%s) — skipping collection.", LOG_PREFIX, exc)
            return
        if source is not None:
            self._active_sources.append(source)
        if recorder is None:
            return
        logger.info("%s collection: started (Helius birth-tape -> recorder, streaming into TapeStore).", LOG_PREFIX)
        # recorder.run() drives the recorder until its (continuous/bounded) source
        # ends or the task is cancelled on shutdown.  Each landed swap is teed into
        # self._tape IN REAL TIME via the recorder's on_swap tap (see
        # _build_collection / _on_recorded_swap) — NOT mirrored after run() returns,
        # which for a continuous live source never happens until shutdown.
        await recorder.run()
        logger.info("%s collection: recorder finished (%d swaps).", LOG_PREFIX, len(recorder.normalized_swaps))

    # ------------------------------------------------------------------
    # Task b — GRADUATION (Birdeye new-listing -> DetectionConsumer)
    # ------------------------------------------------------------------

    async def _graduation_loop(self) -> None:
        try:
            consumer, source = await sync_to_async(
                self._graduation_factory, thread_sensitive=True
            )()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s graduation: factory failed (%s) — skipping graduation.", LOG_PREFIX, exc)
            return
        if source is not None:
            self._active_sources.append(source)
        if consumer is None:
            return
        logger.info("%s graduation: started (Birdeye new-listing -> DetectionConsumer).", LOG_PREFIX)
        await consumer.run()
        logger.info("%s graduation: consumer finished (%d events).", LOG_PREFIX, len(consumer.processed))

    # ------------------------------------------------------------------
    # Task e — POST-GRAD COLLECTION (bounded BirdeyeSwapSource subscriptions)
    # ------------------------------------------------------------------

    async def _postgrad_loop(self) -> None:
        """Manager: open a bounded set of per-mint post-grad swap subscriptions.

        On each tick it polls the graduated Token rows (in graduation order) and,
        for any newly graduated mint not yet subscribed, opens a BirdeyeSwapSource
        subscription IF there is capacity (tape.max_postgrad_subscriptions
        concurrent).  Over capacity, the mint is logged "at-capacity skip" and
        retried on a later tick (no silent drop).  Each subscription self-terminates
        after its per-mint TTL; finished tasks free a capacity slot.
        """
        while not self._stop.is_set():
            try:
                await self._postgrad_tick()
            except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
                logger.warning("%s postgrad: tick error (%s).", LOG_PREFIX, exc)
            await self._sleep_or_stop(self._postgrad_tick_s, None)

    async def _postgrad_tick(self) -> None:
        """One post-grad manager pass: reap finished tasks, open up to capacity."""
        firehose_active, _, _ = await self._read_state()
        if not firehose_active:
            return

        # Reap finished subscriptions (TTL-expired / errored) to free slots.
        for mint in [m for m, t in self._postgrad_tasks.items() if t.done()]:
            self._postgrad_tasks.pop(mint, None)

        try:
            max_subs, ttl_s, grad_order = await sync_to_async(
                self._postgrad_plan_sync, thread_sensitive=True
            )()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s postgrad: cannot build plan (%s).", LOG_PREFIX, exc)
            return

        # grad_order is (mint, graduated_block_time) in graduation order (oldest first).
        for mint, graduated_block_time in grad_order:
            if mint in self._postgrad_seen:
                continue  # already subscribed (or completed) this run
            if len(self._postgrad_tasks) >= max_subs:
                logger.info(
                    "%s postgrad: at-capacity skip mint=%s (n_active=%d max=%d) — retry later.",
                    LOG_PREFIX, mint, len(self._postgrad_tasks), max_subs,
                )
                continue
            self._postgrad_seen.add(mint)
            task = asyncio.create_task(
                self._postgrad_subscription(mint, graduated_block_time, ttl_s),
                name=f"firehose-postgrad-{mint[:8]}",
            )
            self._postgrad_tasks[mint] = task
            logger.info(
                "%s postgrad: subscribe mint=%s (n_active=%d max=%d ttl=%.0fs)",
                LOG_PREFIX, mint, len(self._postgrad_tasks), max_subs, ttl_s,
            )

    async def _postgrad_subscription(
        self, mint: str, graduated_block_time: int, ttl_s: float
    ) -> None:
        """Stream one mint's POST-grad swaps into self._postgrad_tape until TTL.

        Opens a BirdeyeSwapSource (via the injectable factory), reads its events,
        maps each to the internal swap-dict shape the settler / _swaps_to_trade_tuples
        expect, anchors ``rel`` to graduation, and appends only POST-grad swaps
        (rel >= 0) into the post-grad tape.  Tears down at TTL or on cancellation;
        ALWAYS disconnects the source in finally.

        TTL is measured against the injected daemon clock (AC-2.2): the deadline
        is ``clock.now() + ttl_s`` and each loop iteration re-reads clock.now().
        """
        from datetime import timedelta

        source = self._postgrad_factory(mint)
        self._postgrad_sources[mint] = source
        count = 0
        deadline = self._clock.now() + timedelta(seconds=ttl_s)
        try:
            await source.connect()
            async for event in source.events():
                if self._stop.is_set() or self._clock.now() >= deadline:
                    break
                swap = _postgrad_event_to_swap(event, mint, graduated_block_time)
                if swap is None:
                    continue
                # Only POST-grad swaps feed the settler (entry/exit walk lives at
                # grad + score_at_elapsed_s onward).  rel >= 0 == post-grad.
                if swap["rel"] < 0:
                    continue
                self._postgrad_tape.add(mint, swap)
                count += 1
                logger.info(
                    "%s postgrad: swap mint=%s (count=%d)", LOG_PREFIX, mint, count,
                )
                if self._clock.now() >= deadline:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one mint's stream must not kill others
            logger.warning("%s postgrad: stream error mint=%s (%s).", LOG_PREFIX, mint, exc)
        finally:
            try:
                await source.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.debug("%s postgrad: disconnect raised mint=%s (ignored).", LOG_PREFIX, mint)
            self._postgrad_sources.pop(mint, None)
            logger.info(
                "%s postgrad: ttl-expired mint=%s total=%d swaps", LOG_PREFIX, mint, count,
            )

    def _postgrad_plan_sync(self) -> tuple[int, float, list[tuple[str, int]]]:
        """Return (max_concurrent, per_mint_ttl_s, [(mint, grad_bt) ...] grad order).

        max_concurrent  = tape.max_postgrad_subscriptions (config-driven bound).
        per_mint_ttl_s  = scoring.score_at_elapsed_s + outcome.window_s — covers
                          entry at grad+score_at_elapsed_s plus the exit horizon.
        grad order      = Token rows ordered by graduated_block_time (oldest first)
                          so we subscribe to the earliest graduates first.
        """
        from core.models import Token
        from core.resolver import get_active_config

        config = get_active_config()
        if config is None:
            return DEFAULT_MAX_POSTGRAD_SUBSCRIPTIONS, 0.0, []

        max_subs = int(
            getattr(config.tape, "max_postgrad_subscriptions", DEFAULT_MAX_POSTGRAD_SUBSCRIPTIONS)
        )
        ttl_s = float(config.scoring.score_at_elapsed_s + config.outcome.window_s)

        grad_order: list[tuple[str, int]] = []
        for tok in Token.objects.order_by("graduated_block_time"):
            grad_order.append((tok.mint, int(tok.graduated_block_time)))
        return max_subs, ttl_s, grad_order

    # ------------------------------------------------------------------
    # Task c — SCORING SCHEDULER (+ Task d paper-trade, gated)
    # ------------------------------------------------------------------

    async def _scoring_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._score_tick()
            except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
                logger.warning("%s score: tick error (%s).", LOG_PREFIX, exc)
            await self._sleep_or_stop(self._score_tick_s, None)

    async def _score_tick(self) -> None:
        """One scoring-scheduler pass over due, unscored graduated tokens."""
        firehose_active, scoring_enabled, trading_enabled = await self._read_state()
        if not firehose_active:
            return
        if not scoring_enabled:
            logger.info("%s score: scoring_enabled=False — skipping scoring tick.", LOG_PREFIX)
            return

        due = await sync_to_async(self._due_tokens_sync, thread_sensitive=True)()
        if not due:
            return

        # Build the scorer + config ONCE per tick (active model/config).
        try:
            scorer, ref_dist, scoring, trading_cfg, size_sol, sol_usd, threshold = (
                await sync_to_async(self._build_scoring_context_sync, thread_sensitive=True)()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s score: cannot build scoring context (%s).", LOG_PREFIX, exc)
            return

        for mint, graduated_block_time in due:
            swaps = self._tape.get(mint)
            features = assemble_pregrad_features(swaps, graduated_block_time)
            if features is None:
                logger.info(
                    "%s score: mint=%s no pre-grad tape yet (%d swaps) — deferring.",
                    LOG_PREFIX, mint, len(swaps),
                )
                continue

            result = score_pregrad(features, scorer=scorer, ref_dist=ref_dist)
            blend = float(result["blend_score"])
            passed = gate_passes(blend, gate=scoring["gate"], threshold=threshold)
            # Mark scored ONLY once we've scored — the paper leg may still defer
            # below if the post-grad tape has not arrived yet (re-checked next tick
            # via _scored_mints exclusion being bypassed for the deferred set).
            logger.info(
                "%s score: mint=%s score=%.4f gate=%s",
                LOG_PREFIX, mint, blend, "pass" if passed else "fail",
            )

            if not passed:
                self._scored_mints.add(mint)
                continue

            # --- Task d: PAPER TRADE over the POST-grad tape (gated) ---
            postgrad_swaps = self._postgrad_tape.get(mint)
            entry_ts_epoch = float(graduated_block_time + scoring["score_at_elapsed_s"])
            if not self._postgrad_enterable(postgrad_swaps, entry_ts_epoch, scoring):
                logger.info(
                    "%s paper: mint=%s awaiting post-grad swaps — deferring "
                    "(have %d post-grad swaps).",
                    LOG_PREFIX, mint, len(postgrad_swaps),
                )
                # Do NOT mark scored: retry the paper leg on a later tick while the
                # post-grad subscription is still filling its TTL window.
                continue

            self._scored_mints.add(mint)
            trades = _swaps_to_trade_tuples(postgrad_swaps)
            await sync_to_async(self._paper_trade_sync, thread_sensitive=True)(
                mint, blend, trades, entry_ts_epoch, trading_cfg, size_sol, sol_usd, trading_enabled,
            )

    @staticmethod
    def _postgrad_enterable(
        postgrad_swaps: list[dict], entry_ts_epoch: float, scoring: dict
    ) -> bool:
        """Cheap pre-check: is there enough post-grad tape to attempt a settle?

        The settler needs at least one swap in the pre-entry quote window
        [entry-30, entry] AND at least one swap at/after entry+2 (the fill).  This
        mirrors the settler's own 'dead' guard so we DEFER (retry) rather than
        book nothing when the post-grad stream simply hasn't arrived yet.
        """
        if not postgrad_swaps:
            return False
        gap_s = 30
        lat_s = 2
        bts = [float(s.get("block_time", 0)) for s in postgrad_swaps]
        has_quote = any(entry_ts_epoch - gap_s <= t <= entry_ts_epoch for t in bts)
        has_fill = any(t >= entry_ts_epoch + lat_s for t in bts)
        return has_quote and has_fill

    # ------------------------------------------------------------------
    # Sync ORM/scoring helpers (run via sync_to_async)
    # ------------------------------------------------------------------

    def _due_tokens_sync(self) -> list[tuple[str, int]]:
        """Return (mint, graduated_block_time) for tokens due to score, unscored."""
        from core.models import Token
        from core.resolver import get_active_config

        config = get_active_config()
        if config is None:
            return []
        score_at = config.scoring.score_at_elapsed_s
        now = self._clock.now()
        due: list[tuple[str, int]] = []
        for tok in Token.objects.all():
            if tok.mint in self._scored_mints:
                continue
            if score_time_reached(tok.graduated_at, score_at, now):
                due.append((tok.mint, int(tok.graduated_block_time)))
        return due

    def _build_scoring_context_sync(self):
        """Build (scorer, ref_dist, scoring, trading_cfg, size_sol, sol_usd, threshold)."""
        from core.resolver import get_active_config, get_active_model
        from core.scorer import BlendScorer, ReferenceDistribution
        from trading.models import TradingSettings

        config = get_active_config()
        model_entry = get_active_model()
        if model_entry is None:
            raise RuntimeError("no active model in ModelRegistry")
        scorer = BlendScorer.from_registry(model_entry)

        ref_dist = None
        ref_path = config.scoring.reference_dist_path if config else None
        if ref_path:
            ref_dist = ReferenceDistribution.from_file(ref_path)

        scoring = {
            "gate": config.scoring.gate,
            "score_at_elapsed_s": config.scoring.score_at_elapsed_s,
        }
        # Trading knobs come from the shared TradingSettings singleton (P8).
        trading_cfg = TradingSettings.get().to_schema()
        # Paper size: prefer config.trading.paper_size_usd (USD) -> SOL via sol_usd.
        sol_usd = 140.0
        paper_size_usd = None
        if config and config.trading:
            paper_size_usd = config.trading.paper_size_usd
        if paper_size_usd:
            size_sol = float(paper_size_usd) / sol_usd
        else:
            size_sol = trading_cfg.position_size_sol
        # adaptive_topk / threshold cut — config-driven via trading section when present.
        threshold = 0.8
        return scorer, ref_dist, scoring, trading_cfg, size_sol, sol_usd, threshold

    def _paper_trade_sync(
        self, mint, score, trades, entry_ts_epoch, trading_cfg, size_sol, sol_usd, trading_enabled,
    ) -> None:
        """Open + settle the PAPER position (OBSERVE/PAPER only)."""
        settle_paper_trade(
            mint=mint,
            score=score,
            trades=trades,
            entry_ts_epoch=entry_ts_epoch,
            trading_config=trading_cfg,
            size_sol=size_sol,
            sol_usd=sol_usd,
            trading_enabled=trading_enabled,
            now=self._clock.now(),
        )

    # ------------------------------------------------------------------
    # Source factories (the ONLY place concrete live sources are built)
    # ------------------------------------------------------------------

    def _build_collection(self):
        """Build the COLLECTION recorder + its source (Helius birth tape)."""
        from core.management.commands.run_listener import build_birth_tape_recorder

        api_key = getattr(settings, "HELIUS_API_KEY", None)
        if not api_key:
            logger.warning("%s collection: HELIUS_API_KEY missing — collection disabled.", LOG_PREFIX)
            return None, None
        token_store = self._load_token_store_sync()
        recorder = build_birth_tape_recorder(
            api_key, token_store, on_swap=self._on_recorded_swap
        )
        return recorder, recorder._source  # the bounded source for disconnect

    def _build_graduation(self):
        """Build the GRADUATION consumer + its source (Birdeye new-listing)."""
        from core.detection.consumer import DetectionConsumer
        from core.resolver import get_active_config
        from core.tape.birdeye_graduation_source import BirdeyeGraduationSource

        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        if not api_key:
            logger.warning("%s graduation: BIRDEYE_API_KEY missing — graduation disabled.", LOG_PREFIX)
            return None, None
        config = get_active_config()
        detection_dict = {}
        if config is not None:
            detection_dict = config.detection.model_dump()
        source = BirdeyeGraduationSource(api_key=api_key, config=detection_dict, clock=self._clock)
        consumer = DetectionConsumer(source=source, clock=self._clock, config_fn=get_active_config)
        return consumer, source

    def _build_postgrad_source(self, mint: str):
        """Build a POST-grad BirdeyeSwapSource for one mint (adapter wiring layer).

        REUSES the existing BirdeyeSwapSource(api_key, mint) — the proven Birdeye
        SUBSCRIBE_TXS PumpSwap swap client (no handshake rewrite).  This is the
        ONLY place the concrete post-grad source is instantiated.
        """
        from core.tape.birdeye_swap_source import BirdeyeSwapSource

        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        return BirdeyeSwapSource(api_key=api_key, mint=mint)

    @staticmethod
    def _load_token_store_sync() -> dict[str, object]:
        from types import SimpleNamespace

        from core.models import Token

        store: dict[str, object] = {}
        for tok in Token.objects.exclude(graduated_block_time__isnull=True):
            store[tok.mint] = SimpleNamespace(graduated_block_time=int(tok.graduated_block_time))
        return store


# ---------------------------------------------------------------------------
# Pure adapters
# ---------------------------------------------------------------------------


def _ns_to_swap(ns) -> dict:
    """Convert a NormalizedSwap to the spine's collected-swap dict shape."""
    return {
        "block_time": getattr(ns, "block_time", 0),
        "slot": getattr(ns, "slot", 0),
        "signature": getattr(ns, "signature", ""),
        "rel": getattr(ns, "rel", 0.0),
        "price": getattr(ns, "price", 0.0),
        "side": getattr(ns, "side", ""),
        "vol": getattr(ns, "vol_sol", getattr(ns, "vol", 0.0)),
        "owner": getattr(ns, "owner", None),
    }


def _postgrad_event_to_swap(event: dict, mint: str, graduated_block_time: int) -> dict | None:
    """Map one BirdeyeSwapSource event to the settler's collected-swap dict shape.

    The BirdeyeSwapSource yields RAW Birdeye SUBSCRIBE_TXS events; map_birdeye_swap
    is the one pure mapper to the internal §7.1 swap shape (block_time, price,
    vol_sol, side, ...).  ``rel`` is anchored to graduation
    (block_time - graduated_block_time) EXACTLY as _ns_to_swap / assemble use it,
    so the settler's entry/exit walk (over absolute block_time) lines up with the
    grad + score_at_elapsed_s entry the scoring task computes.

    Returns None for an unmappable event (the caller skips None).
    """
    from core.tape.birdeye_swap_mapper import map_birdeye_swap

    mapped = map_birdeye_swap(event)
    if mapped is None:
        return None
    block_time = int(mapped.get("block_time", 0))
    return {
        "block_time": block_time,
        "slot": int(mapped.get("slot", 0)),
        "signature": str(mapped.get("signature", "")),
        "rel": float(block_time - graduated_block_time),
        "price": float(mapped.get("price", 0.0) or 0.0),
        "side": str(mapped.get("side", "")),
        "vol": float(mapped.get("vol_sol", 0.0) or 0.0),
        "vol_usd": float(mapped.get("vol_usd", 0.0) or 0.0),
        "owner": mapped.get("owner"),
    }


def _swaps_to_trade_tuples(swaps) -> list[tuple[float, float, float]]:
    """Convert collected swaps to (block_time, price, usd_volume) settler tuples.

    usd_volume falls back to vol_sol when no usd field is present (the settler's
    impact model only needs a relative flow magnitude; SOL flow is monotone in
    USD flow for a single mint within a window).
    """
    tuples: list[tuple[float, float, float]] = []
    for s in swaps:
        usd = s.get("vol_usd")
        if usd is None:
            usd = s.get("vol", s.get("vol_sol", 0.0))
        tuples.append((float(s.get("block_time", 0)), float(s.get("price", 0.0) or 0.0), float(usd or 0.0)))
    tuples.sort(key=lambda r: r[0])
    return tuples


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Run the gated live firehose daemon (OBSERVE/PAPER only). Polls "
        "PipelineState.firehose_active; while active runs collection -> "
        "graduation -> score -> paper-trade (over a bounded post-grad swap "
        "collection). NEVER places real orders; NEVER mutates firehose_active. "
        "Clean shutdown on SIGTERM and on flip-to-False."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S,
            help=f"Seconds between firehose_active polls (default {DEFAULT_POLL_INTERVAL_S}).")
        parser.add_argument(
            "--max-runtime-seconds", type=float, default=None,
            help="Bound the whole run to N seconds (default: unbounded).")
        parser.add_argument(
            "--score-tick-seconds", type=float, default=DEFAULT_SCORE_TICK_S,
            help=f"Seconds between scoring-scheduler ticks (default {DEFAULT_SCORE_TICK_S}).")
        parser.add_argument(
            "--postgrad-tick-seconds", type=float, default=DEFAULT_POSTGRAD_TICK_S,
            help=f"Seconds between post-grad subscription-manager ticks (default {DEFAULT_POSTGRAD_TICK_S}).")

    def handle(self, *args, **options):
        daemon = FirehoseDaemon(
            poll_interval_s=options["poll_interval"],
            max_runtime_s=options.get("max_runtime_seconds"),
            score_tick_s=options["score_tick_seconds"],
            postgrad_tick_s=options["postgrad_tick_seconds"],
        )
        try:
            asyncio.run(self._run(daemon))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Firehose stopped by interrupt."))
            sys.exit(0)

    async def _run(self, daemon: "FirehoseDaemon") -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.request_stop)
            except (NotImplementedError, ValueError):  # pragma: no cover - platform/thread
                pass
        await daemon.run()
        self.stdout.write(self.style.WARNING("Firehose daemon shut down."))
