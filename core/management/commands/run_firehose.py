# ---
# module: core.management.commands.run_firehose
# sprint: sprint-15
# story: EPIC-tape-sourcing-escalation Tier 2 + Tier 3,
#        hotfix-single-connection-fanout, hotfix-migrate-mint-rpc-resolution,
#        hotfix-grad-liveness-watchdog, hotfix-firehose-drain-and-fairness,
#        US-94 v7 gate + tr30_t600 exit + depth-gated sizing + live wiring,
#        US-92 post-grad rows carry mint
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: django, asyncio, logging, signal, asgiref, os, threading, time,
#               core.tape.birdeye_graduation_source, core.tape.birdeye_swap_source,
#               core.tape.birdeye_swap_mapper, core.tape.helius_birth_tape_source,
#               core.management.commands.run_listener, core.detection.consumer,
#               core.detection.helius_reconciler, core.firehose.spine, core.clock,
#               core.resolver, core.models, core.v4_rep_builder,
#               core.backfill.lake_backfill, core.backfill.birdeye_backfill,
#               core.pricing.sol_usd, core.v7_pregrad_features, core.v7_scorer,
#               core.v7_ride_exit
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
  a. HELIUS        — ONE physical HeliusBirthTapeSource WebSocket fans raw frames
                     into TWO asyncio.Queue objects:
                       collection_q -> LivePreGradBuffer (pre-grad tape)
                       migrate_q    -> HeliusMigrateSource.events_from_queue()
                                       -> DetectionConsumer.run() (graduation)
                     A no-data watchdog fires a forced reconnect after
                     tape.graduation_silence_watchdog_s (default 120s) of zero
                     frames — kills the silent-connection failure mode.
                     Both consumers receive the SAME frames; the collection consumer
                     filters for Buy/Sell via decode_helius_notification; the grad
                     consumer filters for Instruction: Migrate via _decode_and_dedupe.
                     BirdeyeGraduationSource (SECONDARY/gap-fill) ->
                     MigrateReconciler — runs concurrently as backstop for gaps.
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
Pipeline domain code (post-grad TTL, scoring schedule) uses the injected daemon
clock exclusively; the asyncio event-loop clock (loop.time()) is used only for
cooperative sleeping.  The GraduationLivenessWatchdog is the sole exception: it
is a threading.Thread that runs OUTSIDE the asyncio event loop (immune to loop
starvation) and deliberately uses time.time() for wall-clock staleness tracking.
Its time.time() calls are explicitly allowlisted in core/tests/test_clock.py.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

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
    to_pregrad_swaps,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_SCORE_TICK_S = 5.0
DEFAULT_POSTGRAD_TICK_S = 5.0
#: AC-3: how often the durable tape sink is flushed to the lake off the hot path.
DEFAULT_TAPE_FLUSH_INTERVAL_S = 10.0
DEFAULT_MAX_POSTGRAD_SUBSCRIPTIONS = 5

# ---------------------------------------------------------------------------
# Terminalization deadline constants (Fix 1 — infinite re-scan drain)
# ---------------------------------------------------------------------------

#: Grace buffer added to (score_at_elapsed_s + outcome_window_s) before a
#: thin/no-tape DETECTED token is terminalized.  This gives Tier-2 + Tier-3
#: backfill (lake scan + Birdeye REST) adequate time to land swaps and for the
#: result to be observed on at least one tick.
#:
#: Rationale: the outcome window is typically ~1800 s; adding 600 s (10 min)
#: ensures the backfill tasks (which are async fire-and-forget) have completed
#: and the result has been observed by at least one score tick before we give up.
#: This is deliberately conservative: a grad that is still DETECTED 40 min past
#: its outcome window close with <20 swaps will NEVER score; terminalizing it
#: immediately on the deadline is safe.
_TERMINALIZE_GRACE_S: int = 600  # 10 minutes extra grace after window close

# ---------------------------------------------------------------------------
# Graduation-liveness watchdog constant
# ---------------------------------------------------------------------------

#: If firehose_active=True and no graduation has been persisted in this many
#: seconds, the graduation-liveness watchdog fires CRITICAL + recovers by
#: exiting the process (Docker restart policy recreates it).
#:
#: Calibration rationale: live graduation rate is ~8/hour = ~1 every 7.5 min.
#: A 30-minute gap (4× the expected inter-graduation interval) is definitively
#: anomalous — the detection path is dead, not just slow.  This constant is
#: deliberately conservative to avoid false positives during genuine quiet
#: periods (weekends / low market activity) while still catching the 7-hour
#: silent-death incident that motivated this watchdog.
GRAD_STALE_SECONDS: int = 1800  # 30 minutes

#: Post-grad paper-settle window-close recovery.  A gate-passing token's paper leg
#: is DEFERRED until the full outcome window has elapsed (wall-clock), then settled
#: over a SINGLE fresh Birdeye REST fetch of the whole [entry-gap,
#: entry+outcome_window_s] window (fully indexed by then).  Settling at score time
#: (~grad+120s) walks a TRUNCATED tape — Birdeye has only indexed swaps up to ~now,
#: so the settler's AUTO_SELL_TIMER fallback books at the last available swap (a few
#: seconds past entry) and silently misses the real later TAKE_PROFIT / RUG_PULL.
#: The quote-window floor offset (entry-30) matches the settler's pre-entry quote.
_POSTGRAD_REST_QUOTE_GAP_S = 30


# ---------------------------------------------------------------------------
# Graduation-liveness watchdog — threading.Thread, NOT asyncio
# ---------------------------------------------------------------------------


class GraduationLivenessWatchdog:
    """Out-of-band watchdog that detects a dead graduation-detection path.

    WHY A THREAD, NOT AN ASYNCIO TASK
    ==================================
    The watchdog must survive event-loop starvation — the exact failure mode we
    are guarding against.  If the loop is jammed (backfill semaphore pressure,
    slow DB in sync_to_async, WS pump backed up) an asyncio task running on the
    same loop can be delayed for minutes, defeating the watchdog's purpose.

    A ``threading.Thread`` with a ``time.sleep`` loop is entirely outside the
    asyncio event loop.  It fires on wall-clock time regardless of loop pressure,
    making it structurally immune to the starvation scenario we observed live.

    RECOVERY: os._exit(1)
    ======================
    ``os._exit(1)`` is the correct recovery mechanism here:
      1. It bypasses Python's exception machinery and all atexit handlers —
         the process dies *immediately*, not "eventually after cleanup".
      2. Docker's ``restart: unless-stopped`` (confirmed present in
         ``docker-compose.staging.yml`` for ``inference_engine``) recreates the
         container automatically.  ``firehose_active`` is persisted in Postgres
         so the daemon self-resumes on restart without operator intervention.
      3. In-process "restart" surgery (cancelling + recreating the _helius_loop
         task, reconnecting the WS, re-draining queues) is fragile and has been
         confirmed not to work: pausing scoring did NOT revive detection in the
         live incident — the only proven recovery was a container restart.

    ``os._exit`` is chosen over ``sys.exit`` because the latter raises
    ``SystemExit`` which can be caught by any outer try/except and silently
    absorbed.  ``os._exit`` is unconditional.

    PROGRESS-BASED STALENESS — avoiding the restart-loop trap
    ==========================================================
    A naive "fire if MAX(graduated_at) > stale_seconds ago" check has a
    critical flaw: on every daemon restart after a real outage (the exact
    recovery scenario), the last graduation in the DB is hours old.  The
    watchdog would fire os._exit immediately — before detection has its
    ~7.5 min grace to produce a fresh graduation — causing Docker to restart
    the container, which immediately sees the same stale MAX and fires again,
    producing an INFINITE RESTART LOOP.

    The fix: track PROGRESS, not an absolute age.

      - On ``start()``, record ``_last_progress_at = time.time()`` (the
        startup wall-clock).
      - Track ``_last_seen_max_epoch``: the MAX(graduated_at) epoch seen on
        the PREVIOUS poll cycle (None initially).
      - Each poll: read ``MAX(graduated_at)``.
        * If it ADVANCED vs the previous value (a new graduation was
          persisted), set ``_last_progress_at = now``.
        * If it is unchanged (or None), ``_last_progress_at`` is untouched.
      - Fire only if ``now - _last_progress_at > stale_seconds``.

    Effect:
      - Startup / restart: ``_last_progress_at`` = now.  Even with a 2-hour-
        old MAX in the DB, the watchdog will NOT fire for a full stale_seconds
        (30 min) after start — giving detection plenty of time to recover.
      - Genuine dead path: once stale_seconds elapses with no new graduation,
        the watchdog fires exactly once and the container restarts.
      - New graduation mid-window: resets the progress clock immediately; the
        watchdog will not fire until stale_seconds of silence follow.
      - Restart cadence is bounded at ~stale_seconds (30 min) per cycle —
        never a tight loop.

    DESIGN
    ======
    The thread polls every ``check_interval_s`` seconds.  On each poll:
      1. Reads ``PipelineState.firehose_active`` via Django ORM.
      2. If inactive, sleeps and retries — the watchdog does nothing while the
         firehose is deliberately off.
      3. Reads ``MAX(Token.graduated_at)`` (or None if no tokens exist yet).
      4. If MAX advanced vs previous poll, updates ``_last_progress_at``.
      5. Fires if ``now - _last_progress_at > stale_seconds``.

    The ``recovery_fn`` is injectable for tests (avoids killing the test process).

    Args:
        stale_seconds:    Staleness threshold (default: GRAD_STALE_SECONDS).
        check_interval_s: How often the thread polls (default: 60s).
        recovery_fn:      Callable invoked on watchdog fire (default: os._exit(1)).
                          Signature: (no_progress_for_s: float) -> None.
                          MUST be idempotent — the thread may call it once and
                          then block; the process should be gone before a second
                          call can happen.
    """

    def __init__(
        self,
        *,
        stale_seconds: int = GRAD_STALE_SECONDS,
        check_interval_s: float = 60.0,
        recovery_fn=None,
    ) -> None:
        self._stale_seconds = stale_seconds
        self._check_interval_s = check_interval_s
        self._recovery_fn = recovery_fn or self._default_recovery
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Progress tracking — set in start() so every restart gets a fresh window.
        self._last_progress_at: float = 0.0  # epoch seconds; set by start()
        self._last_seen_max_epoch: float | None = None  # MAX(graduated_at) last poll

    @staticmethod
    def _default_recovery(no_progress_for_s: float) -> None:  # pragma: no cover
        """Default recovery: log CRITICAL and hard-exit so Docker restarts."""
        logger.critical(
            "[FIREHOSE] graduation-liveness-watchdog: FIRED — no new graduation "
            "for %.0f seconds (threshold=%d). "
            "Detection path is dead. Exiting (os._exit) so Docker restart "
            "policy recreates the container and revives detection.",
            no_progress_for_s,
            GRAD_STALE_SECONDS,
        )
        # Flush log handlers so the CRITICAL line lands before the process dies.
        logging.shutdown()
        os._exit(1)  # noqa: SLF001 — intentional hard exit for container restart

    @staticmethod
    def _read_state_sync() -> tuple[bool, "object | None"]:
        """Return (firehose_active, max_graduated_at).

        Both reads are single fast queries.  max_graduated_at is a Python
        datetime (or None if no Token rows exist yet).

        The thread closes the DB connection after each poll cycle to avoid
        holding an idle connection across long sleep intervals (Django per-thread
        connection pooling; the connection is re-opened on the next access).
        """
        from django.db.models import Max

        from core.models import PipelineState, Token

        state = PipelineState.get()
        firehose_active = bool(state.firehose_active)

        max_grad = Token.objects.aggregate(max_graduated_at=Max("graduated_at"))["max_graduated_at"]

        return firehose_active, max_grad

    def _poll_once(self) -> None:
        """One watchdog check cycle: update progress, fire if no progress for stale_seconds."""
        try:
            from django.db import connection

            firehose_active, max_graduated_at = self._read_state_sync()
            # Close the thread's DB connection immediately after use so we do
            # not hold a pooled connection open across the sleep interval.
            connection.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[FIREHOSE] graduation-liveness-watchdog: DB read failed (%s) — "
                "skipping this check cycle.",
                exc,
            )
            return

        if not firehose_active:
            # Firehose deliberately off — no graduation expected; also reset
            # progress clock so a re-activation gets a fresh grace window.
            self._last_progress_at = time.time()
            self._last_seen_max_epoch = None
            return

        now = time.time()

        if max_graduated_at is None:
            # No Token rows in DB yet (daemon just started, no graduations seen).
            # Do not fire — treat this as implicit progress (startup grace).
            logger.debug(
                "[FIREHOSE] graduation-liveness-watchdog: no Token rows yet — "
                "treating as progress (startup grace).",
            )
            self._last_progress_at = now
            return

        # Convert the timezone-aware Django datetime to epoch seconds.
        current_max_epoch = max_graduated_at.timestamp()

        # Progress check: did MAX(graduated_at) advance since the last poll?
        if (
            self._last_seen_max_epoch is None
            or current_max_epoch > self._last_seen_max_epoch
        ):
            # A new graduation was persisted — update progress timestamp.
            logger.debug(
                "[FIREHOSE] graduation-liveness-watchdog: progress — "
                "MAX(graduated_at) advanced (prev=%.0f now=%.0f).",
                self._last_seen_max_epoch or 0.0,
                current_max_epoch,
            )
            self._last_progress_at = now

        self._last_seen_max_epoch = current_max_epoch

        no_progress_for_s = now - self._last_progress_at

        if no_progress_for_s >= self._stale_seconds:
            logger.critical(
                "[FIREHOSE] graduation-liveness-watchdog: STALE — "
                "no new graduation for %.0f seconds (threshold=%d, "
                "firehose_active=True). Detection path appears dead. "
                "Triggering recovery.",
                no_progress_for_s,
                self._stale_seconds,
            )
            self._recovery_fn(no_progress_for_s)
        else:
            logger.debug(
                "[FIREHOSE] graduation-liveness-watchdog: OK — "
                "no new graduation for %.0f seconds (threshold=%d).",
                no_progress_for_s,
                self._stale_seconds,
            )

    def _run(self) -> None:
        """Thread main loop: poll every check_interval_s until stop() is called."""
        logger.info(
            "[FIREHOSE] graduation-liveness-watchdog: thread started "
            "(stale_seconds=%d, check_interval_s=%.0f).",
            self._stale_seconds,
            self._check_interval_s,
        )
        while not self._stop_event.wait(timeout=self._check_interval_s):
            self._poll_once()
        logger.info("[FIREHOSE] graduation-liveness-watchdog: thread stopped.")

    def start(self) -> None:
        """Start the watchdog thread (daemon=True so it never blocks process exit)."""
        if self._thread is not None and self._thread.is_alive():
            return  # already running
        self._stop_event.clear()
        # Reset progress tracking on every start so a restart gets a fresh
        # stale_seconds grace window (prevents the restart-loop trap).
        self._last_progress_at = time.time()
        self._last_seen_max_epoch = None
        self._thread = threading.Thread(
            target=self._run,
            name="grad-liveness-watchdog",
            daemon=True,  # dies with the process if normal shutdown completes first
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the watchdog thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


# ---------------------------------------------------------------------------
# US-76 P1.1 -- configuration guard (blocks the pool-of-1 silent-pass path)
# ---------------------------------------------------------------------------


class ConfigurationError(RuntimeError):
    """Raised when the scoring configuration is incomplete or inconsistent.

    US-76 P1.1: if scoring_enabled=True and reference_dist_path is null or the
    file is missing, the daemon refuses to start scoring rather than silently
    falling back to the pool-of-1 path (which assigns rank=1.0 to every token
    and causes every graduation to pass any threshold).
    """


# ---------------------------------------------------------------------------
# US-76 P2.5 -- threshold from model artifact (not a hardcoded constant)
# ---------------------------------------------------------------------------

# The operator-configured target trades-per-day rate.  This selects which
# depth_menu row supplies rank_cut.  Defaults to 30/day (meta.json recommended).
_DEFAULT_PER_DAY = 30


def _threshold_from_model(model_entry, *, per_day: int = _DEFAULT_PER_DAY) -> float:
    """Return the rank_cut for the active model at the given per_day target.

    Reads depth_menu from meta.json in the artifact_dir.  Falls back to the
    first available per_day entry if the requested rate is not found, and to
    0.7916 (v3.2 30/day) if no meta.json is readable.

    US-76 P2.5: replaces the hardcoded 0.8 constant.  The threshold comes from
    the model artifact so it stays in sync with the model at promotion time.

    Args:
        model_entry: Active ModelRegistry row with artifact_dir populated.
        per_day:     Trades-per-day target; selects the depth_menu row.

    Returns:
        float rank_cut from meta.json depth_menu, or 0.7916 as fallback.
    """
    _FALLBACK = 0.7916  # v3.2 30/day rank_cut; also the default in depth_menu
    try:
        artifact_dir = Path(model_entry.artifact_dir) if model_entry.artifact_dir else None
        if artifact_dir is None:
            return _FALLBACK
        meta_path = artifact_dir / "meta.json"
        if not meta_path.is_file():
            logger.warning(
                "%s _threshold_from_model: meta.json not found at %s -- using fallback %.4f",
                LOG_PREFIX,
                meta_path,
                _FALLBACK,
            )
            return _FALLBACK
        with meta_path.open(encoding="utf-8") as fh:
            meta = json.load(fh)
        depth_menu = meta.get("depth_menu", [])
        if not depth_menu:
            return _FALLBACK
        # Find exact per_day match; fall back to first entry.
        for entry in depth_menu:
            if entry.get("per_day") == per_day:
                return float(entry["rank_cut"])
        logger.warning(
            "%s _threshold_from_model: per_day=%d not in depth_menu -- using first entry %.4f",
            LOG_PREFIX,
            per_day,
            float(depth_menu[0]["rank_cut"]),
        )
        return float(depth_menu[0]["rank_cut"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s _threshold_from_model: error reading meta.json (%s) -- using fallback %.4f",
            LOG_PREFIX,
            exc,
            _FALLBACK,
        )
        return _FALLBACK


# ---------------------------------------------------------------------------
# v4 REP+recurrence feature assembly — wired at _score_tick call site
# ---------------------------------------------------------------------------

# v4 wallet bank lives under the models/ mount so it is accessible inside the
# container at /app/models/trilly_pregrad_v4/v4_wallet_bank.parquet.
# The "models" sub-path is resolved at runtime via the active model's artifact_dir
# so we never hardcode the absolute container path here.
_V4_BANK_FILENAME = "v4_wallet_bank.parquet"

# Number of enrich features produced by the v3.2 shared builder
_ENRICH_FEATURE_COUNT = 20

# v7 feature count (44 = 19 pre-grad + 1 n_pregrad_holders + 24 wallet-rep)
_V7_FEATURE_COUNT = 44

# v7 wallet bank filename (same infra as v4 — different artifact dir)
_V7_BANK_FILENAME = "v4_wallet_bank.parquet"  # v7 reuses the v4 wallet bank format

# Cache sentinel: WalletBankLookup is expensive to build (~44s, ~27 MB).
# It is loaded ONCE per FirehoseDaemon instance in __init__, stored as
# self._wallet_bank.  _try_load_wallet_bank() is the safe loader called there.
_wallet_bank_module_cache: "dict[str, object]" = {}  # {bank_path_str: WalletBankLookup}

# v7 model singleton cache: V7Model is loaded once per daemon instance.
# The key is the artifact_dir path string; None means "tried and failed".
_v7_model_cache: "dict[str, object]" = {}  # {model_dir_str: V7Model | None}


def _try_load_wallet_bank(bank_path: Path) -> "object | None":
    """Load WalletBankLookup from bank_path, using a module-level cache.

    Returns the WalletBankLookup instance, or None if the file is absent or
    the load fails.  Uses a module-level dict so multiple FirehoseDaemon
    instances in the same process (tests) share the same bank in memory.
    """
    key = str(bank_path)
    if key in _wallet_bank_module_cache:
        return _wallet_bank_module_cache[key]
    if not bank_path.is_file():
        logger.warning(
            "%s v4 wallet bank NOT found at %s — REP+recurrence features will be "
            "zero-filled until the bank is placed there.  The operator must scp "
            "v4_wallet_bank.parquet to VPS host models/trilly_pregrad_v4/ before "
            "the 33 features can be non-zero.",
            LOG_PREFIX,
            bank_path,
        )
        return None
    try:
        from core.v4_rep_builder import WalletBankLookup

        bank = WalletBankLookup(bank_path)
        _wallet_bank_module_cache[key] = bank
        logger.info(
            "%s v4 WalletBankLookup loaded from %s and cached.", LOG_PREFIX, bank_path
        )
        return bank
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "%s v4 WalletBankLookup FAILED to load from %s: %s — "
            "REP+recurrence will be zero-filled.",
            LOG_PREFIX,
            bank_path,
            exc,
        )
        return None


def _try_load_v7_model(model_dir: Path) -> "object | None":
    """Load the V7Model from model_dir, using a module-level cache.

    Returns the V7Model instance, or None if the boosters are absent (CI / host-
    local) or the load fails.  Uses a module-level dict so multiple FirehoseDaemon
    instances in the same process (tests) share the same model in memory.

    This function is a no-cover genuine I/O boundary (lightgbm + disk read).
    """
    key = str(model_dir)
    if key in _v7_model_cache:
        return _v7_model_cache[key]
    try:
        from core.v7_scorer import BOOSTERS_PRESENT, load_v7  # noqa: PLC0415

        if not BOOSTERS_PRESENT:
            logger.warning(
                "%s v7 boosters NOT found — v7 scoring path will be skipped (CI / "
                "host-local gate). Place seed0..7.txt in models/trilly_pregrad_v7/selection/ "
                "on VPS to enable v7 scoring.",
                LOG_PREFIX,
            )
            _v7_model_cache[key] = None
            return None
        v7_model = load_v7(model_dir)
        _v7_model_cache[key] = v7_model
        logger.info(
            "%s V7Model loaded from %s and cached; gate=%.5f",
            LOG_PREFIX, model_dir, v7_model.gate_threshold,
        )
        return v7_model
    except Exception as exc:  # noqa: BLE001  # pragma: no cover
        logger.error(
            "%s V7Model FAILED to load from %s: %s — v7 scoring path skipped.",
            LOG_PREFIX, model_dir, exc,
        )
        _v7_model_cache[key] = None  # pragma: no cover
        return None  # pragma: no cover


def assemble_v4_features(
    swaps: list[dict],
    graduated_block_time: int,
    *,
    wallet_bank: "object | None",
    deployer: "str | None" = None,
    min_pregrad_swaps: int = 20,
    base_decimals: int = 6,
    sol_usd_spot: "float | None" = None,
) -> "dict | None":
    """Assemble the full 53-feature vector for trilly_pregrad_v4.

    Calls assemble_pregrad_features (shared v3.2 builder, UNMODIFIED) for
    enrich20, then calls compute_rep_features + compute_recurrence_features
    for the 33 REP+recurrence features from the wallet bank, and assembles
    all 53 in meta.json feature order.

    This function ONLY lives in run_firehose.py (the _score_tick call site).
    It does NOT mutate spine.assemble_pregrad_features — the v3.2 path is
    fully unchanged.

    Parameters
    ----------
    swaps:
        Pre-grad swap dicts for this mint (same as assemble_pregrad_features).
    graduated_block_time:
        Graduation epoch (seconds); used to anchor rel and as T for bank
        leak-safe lag.
    wallet_bank:
        WalletBankLookup singleton (self._wallet_bank), or None if the bank
        is not available.  When None, the 33 REP+recurrence features are
        zero-filled — the booster still runs on the 20 enrich features, same
        as before the wire-in.
    deployer:
        Optional deployer wallet address (for enrich20 pre_deployer_* features
        and for excluding from buyer pool extraction).
    min_pregrad_swaps, base_decimals, sol_usd_spot:
        Passed through to assemble_pregrad_features (see its docstring).

    Returns
    -------
    dict of 53 features (enrich20 + REP24 + recurrence9), or None if
    assemble_pregrad_features returns None (secondary gate / no tape).
    """
    # Step 1: enrich20 via the shared builder (v3.2 spine, UNMODIFIED)
    enrich_feats = assemble_pregrad_features(
        swaps,
        graduated_block_time,
        deployer=deployer,
        min_pregrad_swaps=min_pregrad_swaps,
        base_decimals=base_decimals,
        sol_usd_spot=sol_usd_spot,
    )
    if enrich_feats is None:
        return None  # secondary gate rejected; propagate

    # Step 2: extract time/size buyers from the pre-grad tape for REP/recurrence
    from core.v4_rep_builder import (
        RECURRENCE_FEATURE_NAMES,
        REP_FEATURE_NAMES,
        compute_recurrence_features,
        compute_rep_features,
        extract_buyers_from_swaps,
    )

    try:
        # Normalize the raw collected tape to the §7.1 shape BEFORE extracting
        # buyers. extract_buyers_from_swaps filters on `side=="buy" and rel<0`;
        # raw tape dicts carry neither a canonical `side` nor a `rel` field
        # (rel = block_time - graduated_block_time is computed by the normalizer),
        # so passing raw swaps drops every swap -> 0 buyers -> the 33 REP+
        # recurrence features silently zero-fill (v4 runs degraded as enrich20).
        # This is the SAME normalization the enrich20 path uses internally, so
        # the buyer basis (rel<0, canonical side, owner, USD `vol`) matches the
        # lab's whale_edges builder exactly.
        norm = to_pregrad_swaps(
            swaps,
            graduated_block_time,
            base_decimals=base_decimals,
            sol_usd_spot=sol_usd_spot,
        )
        time_buyers, size_buyers = extract_buyers_from_swaps(
            norm, deployer=deployer
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s v4: extract_buyers_from_swaps failed (%s) — REP+recurrence zero-filled.",
            LOG_PREFIX,
            exc,
        )
        time_buyers, size_buyers = [], []

    # Step 3: REP24 + recurrence9 from wallet bank (or zero-fill if bank absent)
    grad_unix_T = float(graduated_block_time)
    if wallet_bank is not None:
        try:
            rep_feats = compute_rep_features(
                time_buyers, size_buyers, grad_unix_T, wallet_bank
            )
            rec_feats = compute_recurrence_features(
                time_buyers, size_buyers, grad_unix_T, wallet_bank
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s v4: REP/recurrence compute failed (%s) — zero-filling.",
                LOG_PREFIX,
                exc,
            )
            rep_feats = {f: 0.0 for f in REP_FEATURE_NAMES}
            rec_feats = {f: 0.0 for f in RECURRENCE_FEATURE_NAMES}
    else:
        # Bank absent: zero-fill REP+recurrence (model still runs on enrich20)
        rep_feats = {f: 0.0 for f in REP_FEATURE_NAMES}
        rec_feats = {f: 0.0 for f in RECURRENCE_FEATURE_NAMES}

    # Step 4: assemble 53 features in meta.json order
    feats = {}
    feats.update(enrich_feats)
    feats.update(rep_feats)
    feats.update(rec_feats)

    # v4 health: log how many of the 33 REP+recurrence features are non-zero on
    # each real score. If this is 0 while the bank is loaded, the builder/bank is
    # broken and v4 is running degraded (the failure we revert v4 for).
    if wallet_bank is not None:
        nz = sum(1 for k, v in {**rep_feats, **rec_feats}.items() if v)
        logger.info(
            "%s v4-features: %d/33 REP+recurrence non-zero "
            "(time_buyers=%d size_buyers=%d) — bank ACTIVE.",
            LOG_PREFIX, nz, len(time_buyers), len(size_buyers),
        )
    return feats


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

    def __init__(self, on_add=None) -> None:
        self._by_mint: dict[str, list[dict]] = {}
        # Optional durable sink hook (AC-3): called for every swap added.  Must
        # never raise on the hot path (the sink swallows its own errors).
        self._on_add = on_add

    def add(self, mint: str, swap: dict) -> None:
        self._by_mint.setdefault(mint, []).append(swap)
        if self._on_add is not None:
            self._on_add(mint, swap)

    def get(self, mint: str) -> list[dict]:
        return list(self._by_mint.get(mint, []))

    def count(self, mint: str) -> int:
        return len(self._by_mint.get(mint, []))

    def drop(self, mint: str) -> None:
        """Remove a mint's buffered swaps entirely (idle-kill eviction)."""
        self._by_mint.pop(mint, None)

    def mints(self) -> list[str]:
        """Return the mints currently buffered (snapshot)."""
        return list(self._by_mint.keys())

    def load_without_sink(self, mint: str, swaps: list[dict]) -> None:
        """Bulk-append *swaps* for *mint* WITHOUT firing the on_add sink.

        This is the Tier-2/3 backfill entry point.  ``add()`` fires ``on_add``
        which re-records swaps to the lake; lake rows fed back through ``add()``
        would DUPLICATE the corpus.  This method bypasses the hook entirely so
        the in-memory buffer is populated without any lake write.

        The caller (lake_backfill task) is responsible for ensuring only
        pre-grad phase="pre" rows are passed here (already filtered in
        LakeBackfiller.run_for_mint).

        Args:
            mint:  The Solana mint address.
            swaps: Pre-filtered swap dicts from the lake (same shape as the
                   live buffer holds — no additional normalization required).
        """
        if not swaps:
            return
        bucket = self._by_mint.setdefault(mint, [])
        bucket.extend(swaps)


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
        helius_factory=None,
        # Legacy aliases kept for backward-compat with existing tests that pass
        # collection_factory= or graduation_factory=.  When either is supplied
        # the caller also passes the other; they are composed into a helius_factory
        # shim so the test-injected sources still work without modification.
        collection_factory=None,
        graduation_factory=None,
        reconciler_factory=None,
        postgrad_factory=None,
        clock=None,
        tape_sink=None,
        wallet_bank=None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._max_runtime_s = max_runtime_s
        self._score_tick_s = score_tick_s
        self._postgrad_tick_s = postgrad_tick_s
        # helius_factory: () -> HeliusBirthTapeSource-like (for the single connection).
        # If the caller provides the legacy collection_factory / graduation_factory test
        # seams (for compatibility with pre-existing tests), use those in a shim.
        if helius_factory is not None:
            self._helius_factory = helius_factory
        elif collection_factory is not None or graduation_factory is not None:
            # Legacy test shim: preserve old behaviour so existing tests pass unchanged.
            self._collection_factory = collection_factory or self._build_collection
            self._graduation_factory = graduation_factory or self._build_graduation
            self._helius_factory = None  # signal to _helius_loop to use legacy path
        else:
            self._helius_factory = self._build_helius_raw
            self._collection_factory = None
            self._graduation_factory = None
        # Keep these available for legacy shim path even when unused
        if not hasattr(self, "_collection_factory"):
            self._collection_factory = self._build_collection
        if not hasattr(self, "_graduation_factory"):
            self._graduation_factory = self._build_graduation
        self._reconciler_factory = reconciler_factory or self._build_reconciler
        self._postgrad_factory = postgrad_factory or self._build_postgrad_source
        self._clock = clock or WallClock()
        # US-96 TAPE_SOURCE: "shared_billy" (default) reads solanaBilly's shared
        # tape read-only and does NOT open Helius or write a self-tape.
        # "self" restores the original self-firehose path as a fallback escape hatch.
        # SEAM EXCEPTION: when the legacy test seam (collection_factory / helius_factory)
        # is injected, force TAPE_SOURCE=self so the legacy _helius_loop path runs —
        # the injected factory IS the tape source in that case (Principle #7).
        if helius_factory is not None or collection_factory is not None:
            self._tape_source: str = "self"
        else:
            self._tape_source = getattr(settings, "TAPE_SOURCE", "shared_billy")

        # AC-3 durable tape sink: every collected swap is appended to the
        # daily-partitioned lake (lake/tapes) so a soak banks a replayable tape and
        # the US-78 swaps surface populates.  Injectable for tests; default writes
        # to the shared lake volume.  The hooks tag phase so the export derives the
        # venue (pre->pump_dot_fun, post->pump_amm).
        # Under TAPE_SOURCE=shared_billy: do NOT write a self-tape (we read billy's
        # tape read-only; no duplicate tape write).  _tape is created WITHOUT on_add.
        if tape_sink is None:
            from core.firehose.tape_sink import LakeTapeSink

            tape_sink = LakeTapeSink()
        self._tape_sink = tape_sink
        if self._tape_source == "shared_billy":
            # No self-tape write under shared_billy — _tape has no on_add hook.
            self._tape = TapeStore()
        else:
            self._tape = TapeStore(on_add=lambda _m, s: self._tape_sink.record(s, "pre"))
        self._postgrad_tape = TapeStore(on_add=lambda _m, s: self._tape_sink.record(s, "post"))
        self._scored_mints: set[str] = set()
        # Mints known to have graduated (Token row exists).  Refreshed by the
        # scoring task and read by the collection buffer's two-tier idle-kill so
        # a graduated mint is PROTECTED from pre-grad eviction (kept for scoring).
        self._graduated_mints: set[str] = set()
        # Tier-2 lake backfill: track mints for which a backfill task has already
        # been dispatched this daemon session.  Prevents concurrent ticks from
        # dispatching a second scan for the same mint while the first is still
        # running (concurrent-dedup).  Ephemeral per-process — restart-safety
        # comes from Token.status (DETECTED filter in _due_tokens_sync).
        self._backfill_pending: set[str] = set()
        # Tier-3 Birdeye REST backfill: concurrency cap so at most N token
        # backfills hit the REST API concurrently (credit safety on the 313+
        # DETECTED backlog).  Initialised lazily in _lake_backfill_task (the
        # event loop must be running when asyncio.Semaphore is first created).
        self._birdeye_backfill_sem: asyncio.Semaphore | None = None
        # Tier-2 lake backfill: concurrency cap on the GIL-heavy parquet scans.
        # WITHOUT this, scoring-on over the DETECTED backlog dispatches hundreds of
        # fire-and-forget lake scans (each reads ~1.7M rows) that saturate the
        # thread pool + GIL, STARVE the asyncio event loop, and kill the shared
        # Helius WS keepalive (observed: WS dies every ~20s).  Cap concurrent scans
        # so the loop keeps servicing the WS ping.  Lazily initialised (event loop).
        self._lake_backfill_sem: asyncio.Semaphore | None = None
        self._stop = asyncio.Event()
        self._active_sources: list = []
        # POST-grad subscription bookkeeping.
        self._postgrad_tasks: dict[str, asyncio.Task] = {}
        self._postgrad_seen: set[str] = set()
        self._postgrad_sources: dict[str, object] = {}
        # Deferred paper settle: gate-passers whose outcome window has not yet
        # closed wait here (mint -> (entry_ts_epoch, score, grad_bt, model_tag)).
        # model_tag is "v7" for the US-94 tr30_t600 ride path or "" for others.
        # Once wall-clock passes entry+outcome_window_s the paper leg settles:
        #   v7 path: apply_tr30_t600_exit on self._postgrad_tape (live WS stream)
        #   other:   Birdeye REST fetch over full window (see _settle_due_pending).
        # _settle_inflight dedups the in-flight fetch+settle so two ticks never
        # double-book.  Both are ephemeral per-process; restart-safety comes from
        # Token.status (a token stays DETECTED until its paper leg actually fires,
        # so a restart re-queues it).  The Semaphore caps concurrent REST fetches.
        self._pending_settle: dict[str, tuple[float, float, int, str]] = {}
        self._settle_inflight: set[str] = set()
        self._postgrad_rest_sem: asyncio.Semaphore | None = None
        # v4 REP+recurrence: WalletBankLookup singleton (~44s build, ~27 MB).
        # Loaded ONCE here, NEVER per-tick.  Injectable via wallet_bank= for tests.
        # When the active model is v3.2 (20 features), this is unused.
        # When the bank file is absent on VPS (operator must scp it), _wallet_bank
        # is None and the 33 REP+recurrence features are zero-filled — the scorer
        # still runs on enrich20 only (degraded, but safe).
        if wallet_bank is not None:
            # Caller-injected (test path or explicit override)
            self._wallet_bank = wallet_bank
        else:
            self._wallet_bank = self._load_wallet_bank_singleton()
        # v7 model singleton: loaded at startup, reused per tick.
        # None when the boosters are absent (CI / host-local gate).
        self._v7_model: "object | None" = None  # populated in _load_v7_model_singleton()

    # ------------------------------------------------------------------
    # v4 wallet bank startup load
    # ------------------------------------------------------------------

    @staticmethod
    def _load_wallet_bank_singleton() -> "object | None":
        """Load the v4 WalletBankLookup ONCE at daemon startup.

        Resolves the bank path via the active model's artifact_dir so the
        daemon is config-driven: when v4 is active the bank lives alongside
        the boosters under models/trilly_pregrad_v4/.  Falls back gracefully
        when no model is active or the bank file is absent.

        The result is cached in the module-level dict by _try_load_wallet_bank
        so repeated daemon instantiations in the same process (tests) share one
        loaded instance.
        """
        try:
            from core.resolver import get_active_model

            model_entry = get_active_model()
            if model_entry is None:
                # No active model yet — try the conventional path so a pre-promote
                # VPS with the bank already in place can still warm the cache.
                bank_path = (
                    Path(__file__).resolve().parents[3]
                    / "models"
                    / "trilly_pregrad_v4"
                    / _V4_BANK_FILENAME
                )
            else:
                artifact_dir = Path(model_entry.artifact_dir) if model_entry.artifact_dir else None
                if artifact_dir is not None:
                    bank_path = artifact_dir / _V4_BANK_FILENAME
                else:
                    return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s _load_wallet_bank_singleton: failed to resolve bank path (%s) — "
                "REP features will be zero-filled.",
                LOG_PREFIX,
                exc,
            )
            return None
        return _try_load_wallet_bank(bank_path)

    @staticmethod
    def _load_v7_model_singleton() -> "object | None":
        """Load the V7Model ONCE at daemon startup.

        Resolves the model dir via the conventional v7 path under models/.
        If boosters are absent (CI), returns None — the v7 scoring path
        is skipped for that session.  The result is cached in the module-level
        dict by _try_load_v7_model.
        """
        model_dir = (
            Path(__file__).resolve().parents[3]
            / "models"
            / "trilly_pregrad_v7"
        )
        return _try_load_v7_model(model_dir)

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
        """Launch helius/reconciler/scoring/post-grad tasks; stop on flip/stop/deadline.

        GRADUATION-LIVENESS WATCHDOG
        ============================
        A GraduationLivenessWatchdog thread runs for the duration of the active
        run.  It is a daemon threading.Thread (not asyncio) so it is immune to
        event-loop starvation.  If no graduation is persisted in GRAD_STALE_SECONDS
        while firehose_active=True, it logs CRITICAL and calls os._exit(1) so
        Docker's restart: unless-stopped recreates the container.

        DETECTION-CRASH RECOVERY
        ========================
        asyncio.gather(..., return_exceptions=True) silently absorbs a crashed
        helius_task — the process stays "up" but detection is dead.  After the
        gather we inspect helius_task: if it exited with an exception (not
        CancelledError), we log CRITICAL and trigger the same recovery path
        (os._exit(1) via the watchdog recovery_fn) so the container is restarted
        rather than limping on with a dead detector.
        """
        self._active_sources = []
        self._postgrad_tasks = {}
        self._postgrad_seen = set()
        self._postgrad_sources = {}

        # Start the graduation-liveness watchdog thread for this active run.
        # Injectable _grad_watchdog_factory for tests; defaults to the real watchdog.
        watchdog_factory = getattr(self, "_grad_watchdog_factory", None)
        if watchdog_factory is not None:
            grad_watchdog = watchdog_factory()
        else:
            grad_watchdog = GraduationLivenessWatchdog()
        grad_watchdog.start()
        logger.info(
            "%s graduation-liveness-watchdog started (stale_threshold=%ds).",
            LOG_PREFIX,
            GRAD_STALE_SECONDS,
        )

        # US-94: load the v7 model singleton on first active run (non-blocking:
        # if boosters are absent (CI / host) this is a no-op; v7 path is skipped).
        if self._v7_model is None:
            self._v7_model = self._load_v7_model_singleton()

        # US-96 TAPE_SOURCE branch:
        #   shared_billy → guard freshness first (idle + log warning if stale);
        #                   run _shared_billy_loop instead of _helius_loop;
        #                   no Helius subscription, no self-tape write.
        #   self          → original path (HeliusBirthTapeSource + self-tape).
        if self._tape_source == "shared_billy":
            from core.firehose.shared_tape import STALE_WARNING, billy_firehose_is_live
            if not billy_firehose_is_live():
                logger.warning(
                    "%s TAPE_SOURCE=shared_billy startup guard: %s — IDLE (no Helius, no self-tape).",
                    LOG_PREFIX, STALE_WARNING,
                )
                # Idle: just run the watcher + scorer so the daemon stays alive
                # and the flag is respected; no collection happens until tapes are fresh.
                collection_task = asyncio.create_task(
                    self._shared_billy_idle_loop(), name="firehose-shared-idle"
                )
            else:
                collection_task = asyncio.create_task(
                    self._shared_billy_loop(), name="firehose-shared-billy"
                )
            helius_task = collection_task  # alias for result inspection below
        else:
            helius_task = asyncio.create_task(self._helius_loop(), name="firehose-helius")
            collection_task = helius_task

        reconciler_task = asyncio.create_task(self._reconciler_loop(), name="firehose-reconciler")
        scoring_task = asyncio.create_task(self._scoring_loop(), name="firehose-scoring")
        postgrad_task = asyncio.create_task(self._postgrad_loop(), name="firehose-postgrad")
        flush_task = asyncio.create_task(self._tape_flush_loop(deadline), name="firehose-tape-flush")
        watcher_task = asyncio.create_task(self._flip_watcher(deadline), name="firehose-watcher")

        tasks = [
            helius_task, reconciler_task,
            scoring_task, postgrad_task, flush_task, watcher_task,
        ]
        try:
            # The watcher returns when firehose flips False / stop / deadline.
            await watcher_task
        finally:
            # Stop the graduation-liveness watchdog thread (clean shutdown).
            # On os._exit recovery paths this never runs (the process is already
            # dead); on normal flip-to-False shutdown it stops the thread cleanly.
            grad_watchdog.stop()

            for t in (
                helius_task, reconciler_task,
                scoring_task, postgrad_task, flush_task,
            ):
                t.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_postgrad_subscriptions()
            await self._disconnect_all_sources()
            # AC-3: final flush so the in-flight tail (< flush_every) is persisted.
            await sync_to_async(self._tape_sink.flush, thread_sensitive=True)()
            logger.info("%s active run stopped — all tasks cancelled, sources disconnected.", LOG_PREFIX)

            # DETECTION-CRASH RECOVERY: inspect helius_task/collection_task result.
            # If it exited with a real exception (not CancelledError and not None
            # from clean completion), the collection path crashed and was silently
            # absorbed by the gather.  Log CRITICAL and trigger recovery so the
            # container is restarted rather than running with dead detection.
            #
            # tasks index 0 = helius_task / collection_task (see tasks list above).
            helius_result = results[0]
            if (
                isinstance(helius_result, BaseException)
                and not isinstance(helius_result, asyncio.CancelledError)
            ):
                logger.critical(
                    "%s collection_task exited with exception: %r — "
                    "collection path is dead. Triggering recovery (os._exit) "
                    "so Docker restart policy recreates the container.",
                    LOG_PREFIX,
                    helius_result,
                )
                # Reuse the watchdog's recovery_fn so the test-injectable path
                # is also covered here (in tests this writes to a list rather
                # than calling os._exit).
                grad_watchdog._recovery_fn(0.0)

    async def _tape_flush_loop(self, deadline) -> None:
        """AC-3: periodically flush the durable tape sink off the event loop.

        Inline flushes in the sink handle bulk persistence (every flush_every rows);
        this guarantees the tail is written within a bounded interval even when the
        swap rate is low, and keeps the gzip I/O off the collection hot path.
        """
        while not self._stop.is_set():
            await self._sleep_or_stop(DEFAULT_TAPE_FLUSH_INTERVAL_S, deadline)
            try:
                await sync_to_async(self._tape_sink.flush, thread_sensitive=True)()
            except Exception as exc:  # noqa: BLE001 — never let persistence kill the run
                logger.warning("%s tape flush failed (%s) — continuing.", LOG_PREFIX, exc)

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

    def _collection_graduated(self, mint: str) -> bool:
        """Return True if *mint* has graduated (protected from pre-grad eviction).

        A mint is "graduated" for the live pre-grad buffer's two-tier idle-kill
        if a Token row exists for it (the graduation path persisted it) OR the
        daemon has already scored it.  Graduated mints are retained through
        scoring; only dead UNgraduated mints are evicted.  Reads the in-memory
        scored set directly and falls back to the cached graduated mint set the
        scoring task refreshes — never an ORM call on the streaming hot path.
        """
        if mint in self._scored_mints:
            return True
        return mint in self._graduated_mints

    @staticmethod
    def _resolve_pre_grad_ttl_sync() -> float:
        """Resolve tape.pre_grad_idle_kill_ttl_s from the active config (sync ORM).

        Falls back to the LivePreGradBuffer default (300s) when no config is
        resolvable, so the buffer always has a finite, bounded TTL.
        """
        from core.firehose.live_pregrad_buffer import (
            DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S,
        )
        from core.resolver import get_active_config

        try:
            config = get_active_config()
        except Exception:  # noqa: BLE001 - never let config break collection
            return float(DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
        if config is None:
            return float(DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
        return float(
            getattr(config.tape, "pre_grad_idle_kill_ttl_s", DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
            or DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S
        )

    #: Default silence watchdog (seconds) — if not overrideable via config.
    _DEFAULT_SILENCE_WATCHDOG_S: float = 120.0

    @staticmethod
    def _resolve_silence_watchdog_s_sync() -> float:
        """Resolve tape.graduation_silence_watchdog_s from the active config (sync ORM).

        Falls back to 120s when no config is resolvable.  The Helius collection
        stream delivers thousands of frames per minute under normal load, so 120s
        of total zero-frames is definitively a silent/dead socket.
        """
        _DEFAULT = 120.0
        from core.resolver import get_active_config

        try:
            config = get_active_config()
        except Exception:  # noqa: BLE001
            return _DEFAULT
        if config is None:
            return _DEFAULT
        return float(
            getattr(config.tape, "graduation_silence_watchdog_s", _DEFAULT) or _DEFAULT
        )

    # ------------------------------------------------------------------
    # Task a — SINGLE HELIUS CONNECTION (fan-out: collection + graduation)
    # ------------------------------------------------------------------

    async def _helius_loop(self) -> None:
        """ONE physical Helius connection feeds BOTH collection and graduation detection.

        Validated finding (2026-06-21): migrate frames arrive on the collection
        socket's raw stream (2/2 captured within ~5k frames / under a minute).
        Both subscriptions are byte-identical so there is no need for a second WS.

        Architecture:
          1. Build ONE HeliusBirthTapeSource (raw frames, connect once).
          2. Create collection_q and migrate_q (asyncio.Queue, maxsize=10000).
          3. PUMP task: async for raw frame from source.events() → put on BOTH queues.
             Records last_frame_at via self._clock for the no-data watchdog.
          4. COLLECTION consumer: LivePreGradBuffer reading _QueueDataSource(collection_q)
             through MappedSwapSource(decode_helius_notification) — SAME as before.
          5. GRADUATION consumer: DetectionConsumer reading HeliusMigrateSource via
             events_from_queue(migrate_q) — decode+dedupe+yield graduation events.
          6. WATCHDOG: if no frames for graduation_silence_watchdog_s, force-reconnect.
          7. When pump ends (WS close/error) or watchdog fires: cancel consumers, put
             None sentinels on both queues, disconnect source, reconnect with backoff.
             self._tape is RETAINED across reconnect (daemon-level store).

        Legacy test shim: if self._helius_factory is None (old-style test that
        injected collection_factory + graduation_factory), falls back to running the
        two loops concurrently so existing tests don't break.
        """
        # Legacy-shim fallback for old tests that inject collection_factory / graduation_factory.
        if self._helius_factory is None:
            await self._helius_loop_legacy()
            return

        from core.firehose.live_pregrad_buffer import LivePreGradBuffer
        from core.tape.helius_birth_tape_source import (
            HeliusMigrateSource,
            _QueueDataSource,
            decode_helius_notification,
        )
        from core.tape.mapped_source import MappedSwapSource

        # Resolve config values ONCE in a sync context.
        idle_ttl_s = await sync_to_async(
            self._resolve_pre_grad_ttl_sync, thread_sensitive=True
        )()
        watchdog_s = await sync_to_async(
            self._resolve_silence_watchdog_s_sync, thread_sensitive=True
        )()

        # Resolve graduation event_source from active config.
        def _get_event_source() -> str:
            from core.resolver import get_active_config
            try:
                config = get_active_config()
            except Exception:  # noqa: BLE001
                return "pump_dot_fun"
            if config is None:
                return "pump_dot_fun"
            try:
                filt = config.detection.filter
                if filt and filt.source:
                    return filt.source
            except AttributeError:
                pass
            return "pump_dot_fun"

        event_source = await sync_to_async(_get_event_source, thread_sensitive=True)()

        backoff = 1.0
        attempt = 0

        while not self._stop.is_set():
            # Build the single physical source.
            try:
                helius_source = await sync_to_async(
                    self._helius_factory, thread_sensitive=True
                )()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s helius: factory failed (%s) — retry in %.0fs.",
                    LOG_PREFIX, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            if helius_source is None:
                logger.warning(
                    "%s helius: no source built (HELIUS_API_KEY missing?) — disabled.",
                    LOG_PREFIX,
                )
                return

            backoff = 1.0
            attempt += 1
            self._active_sources.append(helius_source)

            # Fan-out queues — each raw frame is pushed to both consumers.
            collection_q: asyncio.Queue = asyncio.Queue(maxsize=10000)
            migrate_q: asyncio.Queue = asyncio.Queue(maxsize=10000)

            # Watchdog: track last frame time using the injected clock.
            # We use a list so the inner coroutine can mutate the reference.
            last_frame_at: list = [self._clock.now()]

            async def _pump() -> None:
                """Push every raw frame from the WS into BOTH queues."""
                await helius_source.connect()
                try:
                    async for raw_frame in helius_source.events():
                        last_frame_at[0] = self._clock.now()
                        # collection_q — put non-blocking; log WARNING if full.
                        if collection_q.full():
                            logger.warning(
                                "%s helius: collection_q full (maxsize=%d) — "
                                "frame dropped on collection path.",
                                LOG_PREFIX, collection_q.maxsize,
                            )
                        else:
                            await collection_q.put(raw_frame)
                        # migrate_q — same guard.
                        if migrate_q.full():
                            logger.warning(
                                "%s helius: migrate_q full (maxsize=%d) — "
                                "frame dropped on migrate path.",
                                LOG_PREFIX, migrate_q.maxsize,
                            )
                        else:
                            await migrate_q.put(raw_frame)
                finally:
                    await helius_source.disconnect()

            async def _watchdog() -> None:
                """Fire if no frame arrives within watchdog_s seconds."""
                while True:
                    await asyncio.sleep(watchdog_s / 4)  # check 4× per window
                    elapsed = (self._clock.now() - last_frame_at[0]).total_seconds()
                    if elapsed >= watchdog_s:
                        logger.warning(
                            "%s helius: watchdog fired — no frames for %.0fs "
                            "(watchdog_s=%.0f) — forcing reconnect.",
                            LOG_PREFIX, elapsed, watchdog_s,
                        )
                        return  # returning causes the gather to finish; teardown reconnects

            # Build collection consumer (queue-backed).
            collection_source = MappedSwapSource(
                _QueueDataSource(collection_q), decode_helius_notification
            )
            buffer = LivePreGradBuffer(
                source=collection_source,
                store=self._tape,
                clock=self._clock,
                is_graduated=self._collection_graduated,
                idle_ttl_s=idle_ttl_s,
            )

            # Build graduation consumer (queue-backed via HeliusMigrateSource.events_from_queue).
            from core.detection.consumer import DetectionConsumer
            from core.resolver import get_active_config

            # Resolve HELIUS_API_KEY for the RPC mint resolver.
            # resolve_spl_mint() calls getMultipleAccounts via the HTTPS RPC and
            # requires a real key.  "fanout" was fine before (no WS opened) but
            # now the resolver needs it for each graduation.
            _helius_api_key = getattr(settings, "HELIUS_API_KEY", None) or ""
            if not _helius_api_key:
                logger.warning(
                    "%s helius: HELIUS_API_KEY missing — SPL mint resolution will fail "
                    "(all graduations skipped). Set HELIUS_API_KEY in settings.",
                    LOG_PREFIX,
                )

            migrate_source_obj = HeliusMigrateSource(
                api_key=_helius_api_key,  # needed by resolve_spl_mint RPC calls
                event_source=event_source,
                clock=self._clock,
            )

            # Wrap events_from_queue in a DataSource-compatible shim so DetectionConsumer
            # can call source.connect()/disconnect()/events() without modification.
            class _MigrateQueueSource:
                """Minimal DataSource shim over HeliusMigrateSource.events_from_queue."""

                def __init__(self, migrate_src, q):
                    self._src = migrate_src
                    self._q = q

                async def connect(self):
                    pass  # no-op; queue is already fed by the pump

                async def disconnect(self):
                    pass  # no-op

                async def events(self):
                    async for ev in self._src.events_from_queue(self._q):
                        yield ev

            grad_source = _MigrateQueueSource(migrate_source_obj, migrate_q)
            grad_consumer = DetectionConsumer(
                source=grad_source,
                clock=self._clock,
                config_fn=get_active_config,
            )

            logger.info(
                "%s helius: single-connection fan-out started "
                "(attempt=%d, idle_ttl=%.0fs, watchdog=%.0fs, event_source=%s).",
                LOG_PREFIX, attempt, idle_ttl_s, watchdog_s, event_source,
            )

            pump_task = asyncio.create_task(_pump(), name="helius-pump")
            watchdog_task = asyncio.create_task(_watchdog(), name="helius-watchdog")
            collection_task = asyncio.create_task(buffer.run(), name="helius-collection")
            grad_task = asyncio.create_task(grad_consumer.run(), name="helius-graduation")

            inner_tasks = [pump_task, watchdog_task, collection_task, grad_task]

            try:
                # Wait for EITHER the pump to end (WS closed/error) OR the watchdog to fire.
                done, pending = await asyncio.wait(
                    {pump_task, watchdog_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # Outer task cancelled (daemon shutdown / flip-to-False).
                for t in inner_tasks:
                    t.cancel()
                await asyncio.gather(*inner_tasks, return_exceptions=True)
                # Drain queues so consumers' events() return cleanly.
                await collection_q.put(None)
                await migrate_q.put(None)
                try:
                    self._active_sources.remove(helius_source)
                except ValueError:
                    pass
                raise  # propagate CancelledError to the outer gather

            # Cancel everything; put sentinels so consumers drain cleanly.
            for t in inner_tasks:
                t.cancel()
            await collection_q.put(None)
            await migrate_q.put(None)
            await asyncio.gather(*inner_tasks, return_exceptions=True)

            try:
                self._active_sources.remove(helius_source)
            except ValueError:
                pass

            firehose_active, _, _ = await self._read_state()
            if self._stop.is_set() or not firehose_active:
                logger.info(
                    "%s helius: stream ended; firehose inactive/stopping — done "
                    "(tape retained: %d mints).",
                    LOG_PREFIX, len(self._tape.mints()),
                )
                return

            logger.info(
                "%s helius: stream ended (tape retained: %d mints) — "
                "reconnecting in %.0fs.",
                LOG_PREFIX, len(self._tape.mints()), backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _helius_loop_legacy(self) -> None:
        """Legacy shim: run collection + graduation as separate concurrent tasks.

        Only used when the daemon was constructed with the old-style
        collection_factory / graduation_factory test seams (pre-fan-out tests).
        Runs both loops concurrently and returns when either completes or the
        outer task is cancelled.
        """
        col_task = asyncio.create_task(
            self._collection_loop_legacy(), name="helius-legacy-collection"
        )
        grad_task = asyncio.create_task(
            self._graduation_loop_legacy(), name="helius-legacy-graduation"
        )
        try:
            await asyncio.gather(col_task, grad_task)
        except asyncio.CancelledError:
            col_task.cancel()
            grad_task.cancel()
            await asyncio.gather(col_task, grad_task, return_exceptions=True)
            raise

    async def _collection_loop_legacy(self) -> None:
        """Buffer ALL pump.fun bonding-curve swaps by mint — NO graduation anchor.

        Legacy version used only when collection_factory is injected (old tests).
        """
        from core.firehose.live_pregrad_buffer import LivePreGradBuffer

        idle_ttl_s = await sync_to_async(
            self._resolve_pre_grad_ttl_sync, thread_sensitive=True
        )()

        backoff = 1.0
        attempt = 0
        while not self._stop.is_set():
            try:
                source = await sync_to_async(
                    self._collection_factory, thread_sensitive=True
                )()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s collection: factory failed (%s) — retry in %.0fs.",
                    LOG_PREFIX, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            if source is None:
                logger.warning("%s collection: no source built — collection disabled.", LOG_PREFIX)
                return
            backoff = 1.0
            self._active_sources.append(source)
            buffer = LivePreGradBuffer(
                source=source,
                store=self._tape,
                clock=self._clock,
                is_graduated=self._collection_graduated,
                idle_ttl_s=idle_ttl_s,
            )
            attempt += 1
            logger.info(
                "%s collection: started (legacy, attempt=%d, idle_ttl=%.0fs).",
                LOG_PREFIX, attempt, idle_ttl_s,
            )
            try:
                await buffer.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s collection: stream error (%s) — will reconnect.", LOG_PREFIX, exc)
            finally:
                try:
                    self._active_sources.remove(source)
                except ValueError:
                    pass

            firehose_active, _, _ = await self._read_state()
            if self._stop.is_set() or not firehose_active:
                logger.info(
                    "%s collection: stream ended; firehose inactive/stopping — done "
                    "(buffer retained: %d mints).",
                    LOG_PREFIX, len(self._tape.mints()),
                )
                return
            logger.info(
                "%s collection: stream ended (buffer retained: %d swaps across %d mints) "
                "— reconnecting in %.0fs.",
                LOG_PREFIX, buffer._buffered_count, len(self._tape.mints()), backoff,
            )
            await asyncio.sleep(backoff)

    # ------------------------------------------------------------------
    # Task b — GRADUATION (legacy path, used when graduation_factory injected)
    # ------------------------------------------------------------------

    async def _graduation_loop_legacy(self) -> None:
        """Legacy graduation loop — only used when graduation_factory is injected."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                consumer, source = await sync_to_async(
                    self._graduation_factory, thread_sensitive=True
                )()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s graduation: factory failed (%s) — retry in %.0fs.",
                    LOG_PREFIX, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            if consumer is None:
                logger.warning("%s graduation: no consumer built — graduation disabled.", LOG_PREFIX)
                return
            backoff = 1.0
            if source is not None:
                self._active_sources.append(source)
            logger.info("%s graduation: started (legacy -> DetectionConsumer).", LOG_PREFIX)
            try:
                await consumer.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s graduation: stream error (%s) — will reconnect.", LOG_PREFIX, exc)
            finally:
                if source is not None:
                    try:
                        self._active_sources.remove(source)
                    except ValueError:
                        pass
            firehose_active, _, _ = await self._read_state()
            if self._stop.is_set() or not firehose_active:
                logger.info("%s graduation: stream ended; firehose inactive/stopping — done.", LOG_PREFIX)
                return
            logger.info(
                "%s graduation: stream ended (%d events) — reconnecting in %.0fs.",
                LOG_PREFIX, len(consumer.processed), backoff,
            )
            await asyncio.sleep(backoff)

    # ------------------------------------------------------------------
    # Backward-compat aliases for existing tests that reference these names.
    # Tests that call daemon._collection_loop() or daemon._graduation_loop()
    # directly still work via these aliases into the legacy implementations.
    # ------------------------------------------------------------------

    _collection_loop = _collection_loop_legacy
    _graduation_loop = _graduation_loop_legacy

    # ------------------------------------------------------------------
    # Task a2 — SHARED BILLY TAPE READER (TAPE_SOURCE=shared_billy)
    # ------------------------------------------------------------------

    async def _shared_billy_loop(self) -> None:
        """US-96: tail solanaBilly's shared tape and feed normalised swaps into
        self._tape, replacing the Helius subscription.

        No Helius subscription is opened.  No self-tape is written.  The scoring
        loop reads self._tape exactly as before.

        Freshness is re-checked inside BillyTapeTailer.async_iter; if billy's
        tape goes stale mid-run, the tailer idles (logs the warning) until fresh
        tapes reappear.
        """
        from core.firehose.shared_tape import BILLY_TAPE_DEFAULT_ROOT, BillyTapeTailer

        tape_root = BILLY_TAPE_DEFAULT_ROOT
        tailer = BillyTapeTailer(root=tape_root)
        queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)

        logger.info(
            "%s TAPE_SOURCE=shared_billy: tailing %s (no Helius sub, no self-tape write).",
            LOG_PREFIX, tape_root,
        )

        # Start the async_iter coroutine that pushes swap dicts onto the queue.
        iter_task = asyncio.create_task(
            tailer.async_iter(stop_event=self._stop, queue=queue),
            name="shared-billy-iter",
        )
        try:
            while not self._stop.is_set():
                # Drain the queue and add rows to the TapeStore.
                try:
                    swap = queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
                    continue
                mint = swap.get("mint")
                if mint:
                    self._tape.add(mint, swap)
        except asyncio.CancelledError:
            iter_task.cancel()
            await asyncio.gather(iter_task, return_exceptions=True)
            raise
        finally:
            iter_task.cancel()
            await asyncio.gather(iter_task, return_exceptions=True)

    async def _shared_billy_idle_loop(self) -> None:  # pragma: no cover — startup stale guard; I/O
        """US-96: idle loop when shared_billy tape is stale at startup.

        Periodically re-checks freshness.  When the tape becomes fresh, logs
        the fact and returns (the outer _run_active / gather loop will restart
        the active run on the next firehose_active poll cycle).
        """
        from core.firehose.shared_tape import STALE_WARNING, billy_firehose_is_live

        logger.warning(
            "%s TAPE_SOURCE=shared_billy IDLE — %s Waiting for fresh tapes.",
            LOG_PREFIX, STALE_WARNING,
        )
        while not self._stop.is_set():
            await asyncio.sleep(30.0)
            if billy_firehose_is_live():
                logger.info(
                    "%s TAPE_SOURCE=shared_billy: fresh tapes detected — restart collection.",
                    LOG_PREFIX,
                )
                return

    # ------------------------------------------------------------------
    # Task b2 — GRADUATION SECONDARY (Birdeye gap-fill → MigrateReconciler)
    # ------------------------------------------------------------------

    async def _reconciler_loop(self) -> None:
        """Secondary gap-fill: BirdeyeGraduationSource → MigrateReconciler.

        Mirrors _graduation_loop's reconnect pattern.  On each reconnect the
        reconciler uses get_or_create (not update_or_create) so it never
        overwrites Token rows the primary DetectionConsumer already wrote.

        Returns immediately if _build_reconciler returns (None, None)
        (i.e. BIRDEYE_API_KEY is absent).
        """
        backoff = 1.0
        while not self._stop.is_set():
            try:
                reconciler, source = await sync_to_async(
                    self._reconciler_factory, thread_sensitive=True
                )()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s graduation-secondary: factory failed (%s) — retry in %.0fs.",
                    LOG_PREFIX, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            if reconciler is None:
                logger.info(
                    "%s graduation-secondary: no reconciler built — Birdeye gap-fill disabled.",
                    LOG_PREFIX,
                )
                return

            backoff = 1.0
            if source is not None:
                self._active_sources.append(source)
            logger.info(
                "%s graduation-secondary: started (Birdeye → MigrateReconciler).",
                LOG_PREFIX,
            )
            try:
                await reconciler.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s graduation-secondary: stream error (%s) — will reconnect.",
                    LOG_PREFIX, exc,
                )
            finally:
                if source is not None:
                    try:
                        self._active_sources.remove(source)
                    except ValueError:
                        pass

            firehose_active, _, _ = await self._read_state()
            if self._stop.is_set() or not firehose_active:
                logger.info(
                    "%s graduation-secondary: stream ended; firehose inactive/stopping — done.",
                    LOG_PREFIX,
                )
                return
            logger.info(
                "%s graduation-secondary: stream ended — reconnecting in %.0fs.",
                LOG_PREFIX, backoff,
            )
            await asyncio.sleep(backoff)

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
                # At capacity.  grad_order is oldest-first, so every later mint is
                # also over-capacity — log the FIRST and BREAK rather than logging
                # one line PER over-capacity mint PER tick (that flooded the log
                # with ~50k lines in a 90-min window).  Remaining mints are retried
                # on a later tick once a slot frees.
                logger.info(
                    "%s postgrad: at-capacity skip mint=%s (n_active=%d max=%d) — retry later.",
                    LOG_PREFIX, mint, len(self._postgrad_tasks), max_subs,
                )
                break
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
        # Manual iteration (NOT `async for ... in source.events()`): a post-grad
        # token routinely goes SILENT seconds after graduation (pump tokens rug /
        # die fast), so the source WS yields no further frames.  With a bare
        # `async for`, the loop blocks on the next frame FOREVER and the deadline
        # check below never runs — the task never completes, its capacity slot is
        # never reaped (_postgrad_tick reaps only `task.done()`), and once every
        # slot is held by a dead-silent stream NO new mint can subscribe → no
        # post-grad tape → zero paper trades.  This is the live bug: 5 slots stuck
        # 75+ min past a 32-min TTL with 0 `ttl-expired`.  Bounding each next-frame
        # await by the REMAINING TTL enforces the wall-clock stop even on a stream
        # that never emits again, so the slot is always freed at the deadline.
        events = source.events()
        try:
            await source.connect()
            while not self._stop.is_set():
                remaining = (deadline - self._clock.now()).total_seconds()
                if remaining <= 0:
                    break  # TTL reached — free the slot.
                try:
                    event = await asyncio.wait_for(
                        events.__anext__(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    break  # TTL reached with no further frames (silent stream).
                except StopAsyncIteration:
                    break  # upstream stream ended.
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one mint's stream must not kill others
            logger.warning("%s postgrad: stream error mint=%s (%s).", LOG_PREFIX, mint, exc)
        finally:
            try:
                await events.aclose()
            except Exception:  # noqa: BLE001 - best-effort generator cleanup
                logger.debug("%s postgrad: events.aclose raised mint=%s (ignored).", LOG_PREFIX, mint)
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
        grad order      = RECENT graduations only (graduated within the last ttl_s),
                          ordered NEWEST-first.

        US-76 fix (live finding): the manager previously subscribed ALL graduated
        Token rows OLDEST-first.  In a long-lived deployment the DB accumulates
        hundreds of stale graduations, so the bounded slots (max_postgrad_subscriptions)
        were perpetually held by long-dead tokens and FRESH graduations — the only
        tokens that can actually be paper-entered — were starved of post-grad price
        data, so the paper leg could never fire.  A token whose entry+exit window
        (ttl_s) has already elapsed can never be entered, so it is excluded entirely;
        the remaining recent graduations are served newest-first.
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

        # Only graduations whose post-grad entry+exit window is still open are worth
        # a subscription; newest-first so fresh paper-trade candidates win the slots.
        now_epoch = int(self._clock.now().timestamp())
        cutoff = now_epoch - int(ttl_s)
        grad_order: list[tuple[str, int]] = []
        for tok in (
            Token.objects.filter(graduated_block_time__gte=cutoff).order_by("-graduated_block_time")
        ):
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

        # Model path detection by feature count on the active scorer.
        # len(scorer.feature_list) == 53 → v4 REP+recurrence path
        # len(scorer.feature_list) == 44 → v7 post-grad gate+ride path (US-94)
        # len(scorer.feature_list) == 20 → v3.2 enrich-only path (unchanged)
        # getattr fallback: test stubs without feature_list default to the v3.2 path.
        _scorer_feature_list = getattr(scorer, "feature_list", [])
        _is_v4_model = len(_scorer_feature_list) == 53
        # v7: 44-feature (19 pre-grad + n_pregrad_holders + 24 wallet-rep);
        # requires self._v7_model loaded (boosters present). If boosters absent
        # (CI / missing on VPS), falls back to v3.2 path (degraded, non-crashing).
        _is_v7_model = len(_scorer_feature_list) == _V7_FEATURE_COUNT and self._v7_model is not None

        # Per-tick backfill dispatch cap.  The DETECTED backlog can be hundreds of
        # tokens; dispatching a fire-and-forget lake scan for ALL of them at once
        # saturates the thread pool with GIL-heavy parquet reads and starves the
        # event loop (kills the shared Helius WS).  Cap new dispatches per tick so
        # the backlog drains gradually; remaining tokens are re-tried next tick.
        _MAX_BACKFILL_DISPATCH_PER_TICK = 4
        backfills_dispatched = 0

        # Terminalization deadline (Fix 1): grad_bt + score_at + outcome_window
        # + _TERMINALIZE_GRACE_S.  After this epoch a DETECTED token that still
        # lacks a scoreable tape (< 20 swaps) is definitively un-scoreable and
        # should be marked SKIPPED so it is never re-scanned.  We compute the
        # deadline from scoring context once per tick.
        now_epoch = self._clock.now().timestamp()
        _terminate_deadline_offset = (
            scoring["score_at_elapsed_s"]
            + scoring["outcome_window_s"]
            + _TERMINALIZE_GRACE_S
        )

        for mint, graduated_block_time in due:
            # Gate already passed this session → the paper leg is time-deferred to
            # the outcome-window close (handled in _settle_due_pending after this
            # loop).  Skip the expensive re-score: the token stays DETECTED for
            # restart-safety but must NOT re-assemble features + re-invoke the
            # scorer on every tick for the full ~30-min window (#377 starvation).
            if mint in self._pending_settle:
                continue
            swaps = self._tape.get(mint)
            # US-76 BREAK-1: serve in USD via ONE SOL/USD spot (the shared cached
            # spot resolved in the scoring context).  vol = vol_sol × spot inside
            # the normalisation layer; reproduces the offline trained feature space
            # and makes pre_insider_sell_ratio parity-true (directives §8/§9).
            if _is_v7_model:
                # v7 (US-94): assemble all 44 features (19 pre-grad + n_pregrad_holders
                # + 24 wallet-rep) from self._tape (pre-only bonding-curve tape).
                # Uses self._wallet_bank for the 24 rep features; 0-filled if absent.
                # NaN-fill uses medians from meta.json (NOT 0-fill for pre+holder feats).
                from core.v7_pregrad_features import assemble_v7_features  # noqa: PLC0415

                nan_fill = getattr(self._v7_model, "nan_fill", None)
                features = assemble_v7_features(
                    swaps,
                    float(graduated_block_time),
                    sol_usd_spot=sol_usd,
                    wallet_bank=self._wallet_bank,
                    nan_fill=nan_fill,
                )
            elif _is_v4_model:
                # v4: assemble all 53 features (enrich20 + REP24 + recurrence9).
                # Uses self._wallet_bank (singleton loaded at daemon startup).
                # If bank is absent, REP+recurrence are zero-filled — scorer still
                # runs on enrich20 only (degraded but non-crashing).
                features = assemble_v4_features(
                    swaps,
                    graduated_block_time,
                    wallet_bank=self._wallet_bank,
                    sol_usd_spot=sol_usd,
                )
            else:
                # v3.2 (or any other 20-feature model): unchanged path.
                features = assemble_pregrad_features(
                    swaps, graduated_block_time, sol_usd_spot=sol_usd
                )
            if features is None:
                # Fix 1 — terminalize past-deadline thin/no-tape tokens.
                #
                # Guard: only terminalize if backfill has had a fair chance.
                # A FRESH token (past_deadline=False) always takes the defer
                # path below so Tier-2 and Tier-3 can land their swaps.
                # Once the deadline (score_at + outcome_window + grace) has
                # elapsed, the token is definitively un-scoreable regardless
                # of how many swaps it has (< 20 fails the secondary gate and
                # that will never improve for a finished bonding curve).
                # Marking SKIPPED drains it from future ticks immediately.
                past_deadline = now_epoch > (graduated_block_time + _terminate_deadline_offset)
                if past_deadline:
                    # Definitively un-scoreable: terminate.
                    self._scored_mints.add(mint)
                    await sync_to_async(
                        self._set_token_status_sync, thread_sensitive=True
                    )(mint, "SKIPPED")
                    logger.info(
                        "%s score: mint=%s past deadline (%d swaps, need ≥20) "
                        "— marking SKIPPED (terminalized, will not re-scan).",
                        LOG_PREFIX, mint, len(swaps),
                    )
                    continue

                # Not past deadline: standard defer / backfill dispatch path.
                # Tier-2 lake backfill: if the buffer is empty AND we have not
                # already dispatched a lake scan for this mint this session,
                # fire-and-forget a task that reads the firehose lake and
                # populates the buffer.  The mint re-enters via the normal loop
                # on a later tick after the fill lands.
                if (
                    len(swaps) == 0
                    and mint not in self._backfill_pending
                    and backfills_dispatched < _MAX_BACKFILL_DISPATCH_PER_TICK
                ):
                    self._backfill_pending.add(mint)
                    backfills_dispatched += 1
                    asyncio.create_task(
                        self._lake_backfill_task(mint, graduated_block_time),
                        name=f"firehose-lake-backfill-{mint[:8]}",
                    )
                    logger.info(
                        "%s score: mint=%s no pre-grad tape — dispatching lake backfill.",
                        LOG_PREFIX, mint,
                    )
                else:
                    logger.info(
                        "%s score: mint=%s no pre-grad tape yet (%d swaps) — deferring.",
                        LOG_PREFIX, mint, len(swaps),
                    )
                continue

            if _is_v7_model:
                # US-94 v7 path: score via 8-seed mean, gate at 15.86012.
                from core.v7_ride_exit import V7_GATE_THRESHOLD, v7_gate_passes  # noqa: PLC0415
                from core.v7_scorer import score_single  # noqa: PLC0415

                v7_score = score_single(self._v7_model, features)
                passed = v7_gate_passes(v7_score, threshold=V7_GATE_THRESHOLD)
                blend = v7_score  # use v7 score as blend for logging/persistence
                result = {
                    "blend_score": v7_score,
                    "label_scores": {"v7": v7_score},
                    "label_ranks": {},
                }
            else:
                result = score_pregrad(features, scorer=scorer, ref_dist=ref_dist)
                blend = float(result["blend_score"])
                passed = gate_passes(blend, gate=scoring["gate"], threshold=threshold)
            # Mark scored ONLY once we've scored — the paper leg may still defer
            # below if the post-grad tape has not arrived yet (re-checked next tick
            # via _scored_mints exclusion being bypassed for the deferred set).
            logger.info(
                "%s score: mint=%s score=%.4f gate=%s%s",
                LOG_PREFIX, mint, blend, "pass" if passed else "fail",
                " [v7]" if _is_v7_model else "",
            )

            # US-78: persist the durable score-time breakdown (predictions_positions
            # surface source).  Recorded for EVERY scored token regardless of gate.
            # Failure-isolated: a persistence error must NEVER block scoring/trading.
            score_time = int(graduated_block_time + scoring["score_at_elapsed_s"])
            try:
                await sync_to_async(self._persist_prediction_sync, thread_sensitive=True)(
                    mint, score_time, result, blend, passed, scoring, sol_usd,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s prediction-persist failed for mint=%s (%s) — continuing.",
                    LOG_PREFIX, mint, exc,
                )

            if not passed:
                self._scored_mints.add(mint)
                # Tier-2: write status=SCORED so a restart skips this token.
                await sync_to_async(
                    self._set_token_status_sync, thread_sensitive=True
                )(mint, "SCORED")
                continue

            # --- Task d: PAPER TRADE — DEFERRED to outcome-window close ---
            # v7 path (US-94): entry is at grad+2s (ENTRY_DELAY_S), exit is the
            # tr30_t600 ride (30% trailing stop off post-grad max, hard 600s timer).
            # The paper leg uses self._postgrad_tape (live WS) for the ride exit.
            # Non-v7: the paper leg walks the FULL post-grad outcome window via
            # a Birdeye REST fetch once wall-clock passes entry+outcome_window_s.
            if _is_v7_model:
                # v7 entry: grad_ts + ENTRY_DELAY_S (2s); size = $25 flat (observe).
                from core.v7_ride_exit import ENTRY_DELAY_S as _V7_ENTRY_DELAY  # noqa: PLC0415

                entry_ts_epoch = float(graduated_block_time) + _V7_ENTRY_DELAY
            else:
                entry_ts_epoch = float(graduated_block_time + scoring["score_at_elapsed_s"])
            if mint not in self._pending_settle:
                _model_tag = "v7" if _is_v7_model else ""
                self._pending_settle[mint] = (
                    entry_ts_epoch, blend, int(graduated_block_time), _model_tag,
                )
                logger.info(
                    "%s paper: mint=%s gate=PASS — deferring settle to outcome-window "
                    "close (entry+%.0fs).%s",
                    LOG_PREFIX, mint, float(scoring["outcome_window_s"]),
                    " [v7 tr30_t600]" if _is_v7_model else "",
                )

        # Settle every gate-passer whose outcome window has now fully elapsed: one
        # fresh full-window Birdeye REST fetch (indexed by now) → honest-fill over
        # the COMPLETE post-grad tape.  Bounded fire-and-forget per mint.
        self._settle_due_pending(
            trading_cfg, size_sol, sol_usd, scoring, trading_enabled,
        )

    @staticmethod
    def _set_token_status_sync(mint: str, status: str) -> None:
        """Update Token.status for *mint* (sync ORM, run via sync_to_async).

        Tier-2 restart-safety: called after a successful score (status=SCORED)
        or a definitive no-tape outcome (status=SKIPPED) so the next daemon
        restart filters this token out of _due_tokens_sync without re-dispatching
        a lake scan or Birdeye REST call.

        Failure-isolated: a DB error here must never block scoring/trading.
        The in-memory _scored_mints set ensures correct behaviour in the current
        session even if the DB write fails.
        """
        try:
            from core.models import Token  # noqa: PLC0415

            Token.objects.filter(mint=mint).update(status=status)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s set_token_status: mint=%s status=%s failed (%s) — continuing.",
                LOG_PREFIX, mint, status, exc,
            )

    async def _lake_backfill_task(
        self, mint: str, graduated_block_time: int
    ) -> None:
        """Tier-2 + Tier-3: read the firehose lake, then Birdeye REST, for *mint*.

        Fire-and-forget coroutine (caller uses asyncio.create_task).  On
        completion the mint re-enters scoring via the normal _score_tick loop
        (no explicit re-notify needed — the tick runs on its own schedule).

        Tape-sourcing escalation chain (within this task):
            Tier 2 — local firehose lake (LakeBackfiller): free, fast, no
                      external calls.  On HIT: loads buffer via
                      ``TapeStore.load_without_sink`` (no on_add sink → no
                      lake duplication).
            Tier 3 — Birdeye REST seek_by_time (BirdeyeBackfiller): last
                      resort when the lake is empty.  On HIT: loads buffer via
                      ``TapeStore.add`` (on_add fires → newly-fetched tape is
                      BANKED to the lake for future reuse).  Concurrency-capped
                      by ``self._birdeye_backfill_sem`` (5 concurrent max).
            Terminal — status=SKIPPED (both lake and Birdeye returned empty):
                       definitively marks the token so restarts don't re-dispatch.
        """
        from core.backfill.lake_backfill import LakeBackfiller  # noqa: PLC0415

        # Concurrency cap on the GIL-heavy parquet scan.  sync_to_async(
        # thread_sensitive=False) runs each scan in the shared thread pool; without
        # a cap, the backlog dispatches saturate it and the GIL contention starves
        # the event loop → the shared Helius WS keepalive misses its ping and the
        # connection dies (~20s death cycle).  Capping concurrent scans keeps the
        # loop responsive.  Lazily created (event loop must be running).
        if self._lake_backfill_sem is None:
            self._lake_backfill_sem = asyncio.Semaphore(2)

        try:
            backfiller = LakeBackfiller()
            # sync_to_async wraps the blocking file I/O so it does not block
            # the event loop during the partition scan; the semaphore bounds how
            # many such scans run concurrently (GIL/loop-starvation guard).
            async with self._lake_backfill_sem:
                swaps = await sync_to_async(
                    backfiller.run_for_mint, thread_sensitive=False
                )(mint, graduated_block_time)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s lake-backfill: mint=%s failed (%s) — will retry on next tick.",
                LOG_PREFIX, mint, exc,
            )
            # Remove from pending so a later tick can retry.
            self._backfill_pending.discard(mint)
            return

        if swaps:
            self._tape.load_without_sink(mint, swaps)
            logger.info(
                "%s lake-backfill: mint=%s loaded %d pre-grad swaps from lake → "
                "will score on next tick.",
                LOG_PREFIX, mint, len(swaps),
            )
            return  # Tier-2 HIT — no need to fall through to Tier 3.

        # ------------------------------------------------------------------
        # Tier-3: lake miss → try Birdeye REST backfill.
        # The semaphore is lazily initialised here (event loop is guaranteed
        # running at this point, which is required for asyncio.Semaphore).
        # ------------------------------------------------------------------
        logger.info(
            "%s lake-backfill: mint=%s zero rows in lake — escalating to Tier-3 "
            "(Birdeye REST).",
            LOG_PREFIX, mint,
        )

        if self._birdeye_backfill_sem is None:
            self._birdeye_backfill_sem = asyncio.Semaphore(5)

        from core.backfill.birdeye_backfill import BirdeyeBackfiller  # noqa: PLC0415

        # Resolve the cached SOL/USD spot for price-consistent mapping.
        try:
            from core.pricing.sol_usd import get_sol_usd  # noqa: PLC0415

            sol_usd_spot: float | None = await sync_to_async(
                get_sol_usd, thread_sensitive=False
            )()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s lake-backfill: mint=%s sol_usd fetch failed (%s) — "
                "continuing without spot price.",
                LOG_PREFIX, mint, exc,
            )
            sol_usd_spot = None

        be_backfiller = BirdeyeBackfiller()
        try:
            async with self._birdeye_backfill_sem:
                be_swaps: list[dict] = await sync_to_async(
                    be_backfiller.run_for_mint, thread_sensitive=False
                )(mint, graduated_block_time, sol_usd_spot=sol_usd_spot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s birdeye-backfill: mint=%s REST fetch failed (%s) — "
                "will retry on next tick.",
                LOG_PREFIX, mint, exc,
            )
            # Remove from pending so a later tick can retry (transient error).
            self._backfill_pending.discard(mint)
            return

        if be_swaps:
            # Tier-3 HIT: load via TapeStore.add so the on_add sink fires and
            # the newly-fetched tape is banked to the lake for future reuse.
            # (Unlike the lake-hit path which uses load_without_sink to avoid
            # duplicating already-banked rows, REST-fetched rows are NEW and
            # SHOULD be banked.)
            for swap in be_swaps:
                self._tape.add(mint, swap)
            logger.info(
                "%s birdeye-backfill: mint=%s loaded %d pre-grad swaps from "
                "Birdeye REST → banked to lake + will score on next tick.",
                LOG_PREFIX, mint, len(be_swaps),
            )
        else:
            # Definitive no-tape (lake AND Birdeye both returned empty).
            # Mark SKIPPED so a restart does not re-dispatch.
            await sync_to_async(
                self._set_token_status_sync, thread_sensitive=True
            )(mint, "SKIPPED")
            logger.info(
                "%s birdeye-backfill: mint=%s zero rows from Birdeye REST — "
                "marking SKIPPED (both lake and REST exhausted).",
                LOG_PREFIX, mint,
            )

    def _settle_due_pending(
        self, trading_cfg, size_sol, sol_usd, scoring, trading_enabled,
    ) -> None:
        """Dispatch the deferred paper settle for gate-passers whose window closed.

        A gate-passer registered in ``self._pending_settle`` is settled only once
        wall-clock has passed ``entry + outcome_window_s`` — by then Birdeye has
        indexed the WHOLE post-grad window, so a single fresh ``run_for_window``
        fetch yields the complete tape and the settler walks the real
        ``[entry, entry+timer]`` outcome (not the truncated few-seconds tape a
        score-time settle would see).  Each due mint is settled by a bounded
        fire-and-forget task; ``self._settle_inflight`` dedups so two ticks never
        double-book the same mint.  Gating is on WALL-CLOCK (not the tape's own
        last-swap time) so a token that went dead post-grad still settles on
        schedule rather than deferring forever.
        """
        window_s = float(scoring["outcome_window_s"])
        now_epoch = self._clock.now().timestamp()
        for mint, pending_val in list(self._pending_settle.items()):
            # Unpack 3-tuple (legacy) or 4-tuple (US-94 model_tag)
            if len(pending_val) == 4:
                entry_ts_epoch, score, grad_bt, model_tag = pending_val
            else:
                entry_ts_epoch, score, grad_bt = pending_val  # type: ignore[misc]
                model_tag = ""
            if mint in self._settle_inflight:
                continue
            if now_epoch < entry_ts_epoch + window_s:
                continue
            self._settle_inflight.add(mint)
            asyncio.create_task(
                self._settle_pending_task(
                    mint, entry_ts_epoch, score, grad_bt,
                    window_s, trading_cfg, size_sol, sol_usd, trading_enabled,
                    model_tag=model_tag,
                ),
                name=f"firehose-settle-{mint[:8]}",
            )

    async def _settle_pending_task(
        self, mint, entry_ts_epoch, score, grad_bt,
        window_s, trading_cfg, size_sol, sol_usd, trading_enabled,
        *,
        model_tag: str = "",
    ) -> None:
        """Fetch the full post-grad window once and settle the PAPER leg.

        OBSERVE/PAPER only (settle_paper_trade asserts trading_enabled is False).

        v7 path (model_tag="v7", US-94):
          Reads self._postgrad_tape (live WS stream) for the mint.  Applies
          the tr30_t600 ride exit (30% trailing stop, 600s timer) via
          apply_tr30_t600_exit.  Honest fill = first swap >= trigger+2s.
          Uses settle_paper_trade for booking.  No Birdeye REST call (ZERO CREDITS).

        Non-v7 path:
          Fetches ``[entry-30, entry+window_s]`` via ``BirdeyeBackfiller.run_for_window``
          (fully indexed by now → the complete tape), converts to settler tuples, and
          books+settles via the SOLE paper settler.  Un-enterable rows (dead / no
          quote / slip-miss) are NOT booked (settle_paper_trade returns None), but the
          token is still marked terminal — the window has closed, so there is nothing
          more to wait for.  The blocking urllib fetch runs in the shared thread pool
          (never stalls the event loop — the #377 lesson) and is concurrency-capped by
          ``self._postgrad_rest_sem``.

        On a transient fetch/settle error the mint is LEFT in ``self._pending_settle``
        so a later tick retries; on success it is removed and marked SCORED so a
        restart skips it.
        """
        if self._postgrad_rest_sem is None:
            self._postgrad_rest_sem = asyncio.Semaphore(3)

        try:
            if model_tag == "v7":
                # US-94 v7 settle: use postgrad_tape (live WS), tr30_t600 ride exit.
                # ZERO CREDITS — no Birdeye REST.
                from core.v7_ride_exit import (  # noqa: PLC0415
                    SIZE_SHALLOW_USD,
                    apply_tr30_t600_exit,
                    find_entry_fill,
                )

                postgrad_swaps = self._postgrad_tape.get(mint)
                entry_fill = find_entry_fill(postgrad_swaps, float(grad_bt))
                if entry_fill is None:
                    # No valid entry fill in the post-grad window — not enterable.
                    logger.info(
                        "%s paper [v7]: mint=%s no valid entry fill (no swaps in "
                        "grad+2s..grad+30s window) — not booked.",
                        LOG_PREFIX, mint,
                    )
                    # Mark terminal anyway — window closed, nothing to wait for.
                    self._scored_mints.add(mint)
                    await sync_to_async(
                        self._set_token_status_sync, thread_sensitive=True
                    )(mint, "SCORED")
                    self._pending_settle.pop(mint, None)
                    return

                exit_result = apply_tr30_t600_exit(
                    postgrad_swaps,
                    entry_fill.price,
                    entry_fill.ts,
                )
                if exit_result is None:
                    # Not enough post-grad swaps to determine exit — defer until more
                    # swaps arrive (handled by the next tick).
                    logger.info(
                        "%s paper [v7]: mint=%s no exit result yet — deferring.",
                        LOG_PREFIX, mint,
                    )
                    return

                # Build trade tuple for the paper settler (entry + exit).
                # Uses the standard settle_paper_trade with 2-leg trades.
                v7_size_usd = SIZE_SHALLOW_USD  # observe-only flat $25 this sprint
                v7_size_sol = v7_size_usd / max(sol_usd, 1.0)
                # Entry + exit as trade tuples (block_time_s, price, usd_volume)
                entry_trade = (entry_fill.ts, entry_fill.price, v7_size_usd)
                exit_trade = (exit_result.exit_ts, exit_result.exit_price, v7_size_usd)
                trades = [entry_trade, exit_trade]
                logger.info(
                    "%s paper [v7]: mint=%s entry=%.8f@%.0f exit=%.8f@%.0f reason=%s "
                    "running_max=%.8f",
                    LOG_PREFIX, mint,
                    entry_fill.price, entry_fill.ts,
                    exit_result.exit_price, exit_result.exit_ts,
                    exit_result.exit_reason, exit_result.running_max,
                )
                await sync_to_async(self._paper_trade_sync, thread_sensitive=True)(
                    mint, score, trades, float(entry_fill.ts),
                    trading_cfg, v7_size_sol, sol_usd, trading_enabled,
                )
            else:
                # Non-v7: Birdeye REST full-window fetch.
                from core.backfill.birdeye_backfill import BirdeyeBackfiller  # noqa: PLC0415

                t_from = int(entry_ts_epoch - _POSTGRAD_REST_QUOTE_GAP_S)
                t_to = int(entry_ts_epoch + window_s)
                backfiller = BirdeyeBackfiller()
                async with self._postgrad_rest_sem:
                    swaps = await sync_to_async(
                        backfiller.run_for_window, thread_sensitive=False
                    )(mint, t_from, t_to, grad_bt, sol_usd_spot=sol_usd)
                trades = _swaps_to_trade_tuples(swaps)
                await sync_to_async(self._paper_trade_sync, thread_sensitive=True)(
                    mint, score, trades, float(entry_ts_epoch),
                    trading_cfg, size_sol, sol_usd, trading_enabled,
                )
            # Terminal: window closed + settled (or settler deemed un-enterable).
            self._scored_mints.add(mint)
            await sync_to_async(
                self._set_token_status_sync, thread_sensitive=True
            )(mint, "SCORED")
            self._pending_settle.pop(mint, None)
            logger.info(
                "%s paper: mint=%s settled at window close over %d post-grad swaps.%s",
                LOG_PREFIX, mint, len(trades),
                " [v7 tr30_t600]" if model_tag == "v7" else "",
            )
        except Exception as exc:  # noqa: BLE001 - one settle must not kill the loop
            logger.warning(
                "%s paper: mint=%s deferred settle failed (%s) — staying queued for "
                "retry on a later tick.",
                LOG_PREFIX, mint, exc,
            )
        finally:
            self._settle_inflight.discard(mint)

    # ------------------------------------------------------------------
    # Sync ORM/scoring helpers (run via sync_to_async)
    # ------------------------------------------------------------------

    def _due_tokens_sync(self) -> list[tuple[str, int]]:
        """Return (mint, graduated_block_time) for tokens due to score, unscored.

        Tier-2 restart-safety: only Token rows with status=DETECTED are candidates.
        Rows with status=SCORED (set on scoring success) or status=SKIPPED (set on
        definitive no-tape) are excluded so a daemon restart does not re-dispatch
        the lake scan or re-score already-processed tokens.

        The data migration (0014_backfill_scored_token_status) ensures legacy Token
        rows that already have an associated Prediction are set to status=SCORED
        before this filter is applied, so old soaked tokens are also excluded.

        BOUNDED SET (Fix 1):
        Only DETECTED tokens within a recency window are scoring candidates.
        The window is score_at_elapsed_s + outcome_window_s + _TERMINALIZE_GRACE_S:
        any token graduated more than this many seconds ago that is still DETECTED
        has missed both backfill windows and its outcome window.  These are
        zombie rows; they are excluded here so the per-tick set stays bounded
        even when the DB has a large un-terminalized backlog.  The terminalization
        itself happens in _score_tick once the deadline is confirmed per token.

        NEWEST-FIRST (Fix 2):
        Due tokens are returned newest-grad-first so the per-tick backfill dispatch
        cap (4/tick) goes to the freshest tokens, not the oldest backlog entries.
        This prevents a large backlog from starving fresh graduations of their tape.
        """
        from core.models import Token
        from core.resolver import get_active_config

        config = get_active_config()
        if config is None:
            return []
        score_at = config.scoring.score_at_elapsed_s
        outcome_window = config.outcome.window_s
        now = self._clock.now()
        now_epoch = int(now.timestamp())

        # Deadline in seconds from graduation: after this a DETECTED token that
        # still has no scoreable tape is terminalized.  The bound below filters
        # the candidate set to this window so the per-tick scan stays O(recent).
        deadline_s = score_at + outcome_window + _TERMINALIZE_GRACE_S

        due: list[tuple[str, int]] = []
        graduated: set[str] = set()

        # Refresh the graduated-mint set from ALL Token rows (needed for the
        # collection buffer's idle-kill protection — do not restrict this scan).
        for tok in Token.objects.all():
            graduated.add(tok.mint)
        self._graduated_mints = graduated

        # Scoring candidates: status=DETECTED, within the recency window,
        # ordered NEWEST-first (graduated_block_time DESC) so the dispatch cap
        # serves fresh tokens before backlog tokens.
        cutoff_bt = now_epoch - deadline_s
        for tok in (
            Token.objects
            .filter(status=Token.STATUS_DETECTED, graduated_block_time__gt=cutoff_bt)
            .order_by("-graduated_block_time")
        ):
            if tok.mint in self._scored_mints:
                continue
            if score_time_reached(tok.graduated_at, score_at, now):
                due.append((tok.mint, int(tok.graduated_block_time)))
        return due

    def _build_scoring_context_sync(self):
        """Build (scorer, ref_dist, scoring, trading_cfg, size_sol, sol_usd, threshold).

        US-76 P1.1 guard: if scoring_enabled=True and reference_dist_path is
        null or missing, raises ConfigurationError to block the pool-of-1
        silent-pass path (ref_dist=None -> rank=1.0 for every token).

        US-76 P2.5: threshold comes from rank_cut in meta.json depth_menu, not
        a hardcoded constant.
        """
        from core.models import PipelineState
        from core.resolver import get_active_config, get_active_model
        from core.scorer import BlendScorer, ReferenceDistribution
        from trading.models import TradingSettings

        config = get_active_config()
        model_entry = get_active_model()
        if model_entry is None:
            raise RuntimeError("no active model in ModelRegistry")
        scorer = BlendScorer.from_registry(model_entry)

        # P1.1 -- require ref_dist when scoring is enabled.
        state = PipelineState.get()
        if state.scoring_enabled:
            ref_path = config.scoring.reference_dist_path if config else None
            if not ref_path:
                raise ConfigurationError(
                    "scoring_enabled=True but ScoringConfig.reference_dist_path is null. "
                    "Set reference_dist_path to the frozen reference_dist.json in the "
                    "active PipelineConfig to prevent the pool-of-1 silent-pass bug "
                    "(US-76 P1.1 guard)."
                )
            if not Path(ref_path).is_file():
                raise ConfigurationError(
                    f"scoring_enabled=True but reference_dist.json is missing: {ref_path!r}. "
                    "Commit reference_dist.json alongside the model artifact and wire "
                    "ScoringConfig.reference_dist_path to it (US-76 P1.1 guard)."
                )
            ref_dist = ReferenceDistribution.from_file(ref_path)
        else:
            ref_path = config.scoring.reference_dist_path if config else None
            ref_dist = (
                ReferenceDistribution.from_file(ref_path)
                if (ref_path and Path(ref_path).is_file())
                else None
            )

        scoring = {
            "gate": config.scoring.gate,
            "score_at_elapsed_s": config.scoring.score_at_elapsed_s,
            # Exit horizon — the upper bound of the post-grad entry+exit window the
            # paper leg (and the post-grad REST entry-tape recovery) operates over.
            "outcome_window_s": config.outcome.window_s,
        }
        # Trading knobs come from the shared TradingSettings singleton (P8).
        trading_cfg = TradingSettings.get().to_schema()
        # Paper size: prefer config.trading.paper_size_usd (USD) -> SOL via sol_usd.
        # SOL/USD comes from the shared cached spot (core.pricing.sol_usd) -- one
        # USD oracle for BOTH heads; replaces the former hardcoded 140.0 (a latent
        # staleness bug).  Fail-safe: falls back to 140.0 on any price miss.
        from core.pricing.sol_usd import get_sol_usd

        sol_usd = get_sol_usd()
        paper_size_usd = None
        if config and config.trading:
            paper_size_usd = config.trading.paper_size_usd
        if paper_size_usd:
            size_sol = float(paper_size_usd) / sol_usd
        else:
            size_sol = trading_cfg.position_size_sol
        # P2.5 -- threshold from model artifact (rank_cut from meta.json depth_menu).
        # US-76 live calibration: per_day target is config-driven (scoring.per_day_target),
        # so the operator can calibrate picks/day on the live stream without a code change
        # (the lab's rank_cut is offline-population-derived — calibrate it live).
        per_day = int(getattr(config.scoring, "per_day_target", _DEFAULT_PER_DAY)) if config else _DEFAULT_PER_DAY
        threshold = _threshold_from_model(model_entry, per_day=per_day)
        # US-78: surface the calibration + model identity so _score_tick can persist
        # a durable Prediction record (the predictions_positions surface source).
        scoring["per_day_target"] = per_day
        scoring["rank_cut"] = float(threshold)
        scoring["model_id"] = str(getattr(model_entry, "model_version", "") or "")
        return scorer, ref_dist, scoring, trading_cfg, size_sol, sol_usd, threshold

    def _persist_prediction_sync(
        self, mint, score_time, result, blend, passed, scoring, sol_usd,
    ) -> None:
        """Upsert the durable score-time Prediction record (US-78).

        Idempotent on (mint, score_time, model_id) so a re-score at the same anchor
        updates rather than duplicates.  Pure persistence — never touches
        scoring_enabled / trading_enabled.
        """
        from core.models import Prediction

        Prediction.objects.update_or_create(
            mint=mint,
            score_time=int(score_time),
            model_id=str(scoring.get("model_id", "")),
            defaults={
                "label_scores": result.get("label_scores", {}),
                "label_ranks": result.get("label_ranks", {}),
                "blend": float(blend),
                "per_day_target": int(scoring.get("per_day_target", _DEFAULT_PER_DAY)),
                "rank_cut": (
                    float(scoring["rank_cut"]) if scoring.get("rank_cut") is not None else None
                ),
                "picked": bool(passed),
                "sol_usd_spot": (float(sol_usd) if sol_usd is not None else None),
            },
        )

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

    def _build_helius_raw(self):
        """Build ONE raw HeliusBirthTapeSource for the single-connection fan-out.

        Returns the unwrapped HeliusBirthTapeSource so the pump task can push raw
        frames into both the collection queue and the migrate queue.  The mapper
        (decode_helius_notification) is applied per-queue by the consumer side.

        This is the ONLY place the concrete live Helius source is built for the
        production fan-out path (Principle #7).  Returns None + warns if
        HELIUS_API_KEY is missing so the loop disables gracefully.
        """
        from core.tape.helius_birth_tape_source import HeliusBirthTapeSource

        api_key = getattr(settings, "HELIUS_API_KEY", None)
        if not api_key:
            logger.warning(
                "%s helius: HELIUS_API_KEY missing — Helius collection + graduation disabled.",
                LOG_PREFIX,
            )
            return None
        return HeliusBirthTapeSource(api_key)

    def _build_collection(self):
        """Build the COLLECTION source: program-wide Helius birth tape, mapped.

        Returns a SINGLE DataSource that yields internal §7.1 swap dicts for
        EVERY pump.fun bonding-curve swap, with NO token_store filter — exactly
        the source/mapper build_birth_tape_recorder uses, minus the recorder's
        graduation-anchor gate.  The LivePreGradBuffer buffers every mint's swaps
        from this source without needing a graduation anchor.

        This is the ONLY place the concrete live collection source is built
        (Principle #7): HeliusBirthTapeSource + decode_helius_notification, the
        SAME pair the offline birth-tape path uses (the offline recorder semantics
        in run_listener are left untouched).

        NOTE: Used only on the legacy test shim path. Production uses _build_helius_raw.
        """
        from core.tape.helius_birth_tape_source import (
            HeliusBirthTapeSource,
            decode_helius_notification,
        )
        from core.tape.mapped_source import MappedSwapSource

        api_key = getattr(settings, "HELIUS_API_KEY", None)
        if not api_key:
            logger.warning("%s collection: HELIUS_API_KEY missing — collection disabled.", LOG_PREFIX)
            return None
        return MappedSwapSource(HeliusBirthTapeSource(api_key), decode_helius_notification)

    def _build_graduation(self):
        """Build the GRADUATION consumer + its source (legacy test shim path only).

        In production the fan-out path (_helius_loop) drives graduation detection
        off the shared collection socket via HeliusMigrateSource.events_from_queue.
        This method is preserved for backward-compat with tests that inject
        graduation_factory= and is called ONLY via _graduation_loop_legacy.

        PRIMARY: HeliusMigrateSource → DetectionConsumer
          Detects pump.fun graduations directly from the 'migrate' instruction
          on the already-open Helius transactionSubscribe stream.  Zero AMM
          noise (100% pump.fun), push not poll, event-driven.

        SECONDARY: BirdeyeGraduationSource → MigrateReconciler
          Retained as a gap-fill backstop (Helius WS drop → Birdeye covers).
          Wired via _build_reconciler / _reconciler_loop — NOT returned here.
          A min_liquidity floor (~5000 USDC) filters obvious non-pump-fun pools.

        Returns (consumer, source) for the primary Helius migrate path only.
        The Birdeye secondary runs in its own separate task (_reconciler_loop).
        """
        from core.detection.consumer import DetectionConsumer
        from core.resolver import get_active_config
        from core.tape.helius_birth_tape_source import HeliusMigrateSource

        helius_api_key = getattr(settings, "HELIUS_API_KEY", None)
        if not helius_api_key:
            logger.warning(
                "%s graduation: HELIUS_API_KEY missing — migrate detection disabled.",
                LOG_PREFIX,
            )
            return None, None

        # Resolve event_source from active config so the emitted event matches
        # config.detection.filter.source (default "pump_dot_fun").
        config = get_active_config()
        event_source = "pump_dot_fun"
        if config is not None:
            try:
                filt = config.detection.filter
                if filt and filt.source:
                    event_source = filt.source
            except AttributeError:
                pass

        source = HeliusMigrateSource(api_key=helius_api_key, event_source=event_source, clock=self._clock)
        consumer = DetectionConsumer(source=source, clock=self._clock, config_fn=get_active_config)
        logger.info(
            "%s graduation: primary=HeliusMigrateSource event_source=%s",
            LOG_PREFIX,
            event_source,
        )
        return consumer, source

    def _build_reconciler(self):
        """Build the SECONDARY gap-fill: BirdeyeGraduationSource → MigrateReconciler.

        Birdeye is demoted to a gap-fill backstop that covers any Helius WS
        reconnect gaps.  A min_liquidity=5000 floor cuts obvious non-pump-fun
        pools (real pump.fun grads exit with ≥$7k liquidity).

        Returns (reconciler, source) or (None, None) if BIRDEYE_API_KEY is absent.
        """
        from core.detection.helius_reconciler import MigrateReconciler
        from core.resolver import get_active_config
        from core.tape.birdeye_graduation_source import BirdeyeGraduationSource

        birdeye_api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        if not birdeye_api_key:
            logger.warning(
                "%s graduation-secondary: BIRDEYE_API_KEY missing — Birdeye gap-fill disabled.",
                LOG_PREFIX,
            )
            return None, None

        config = get_active_config()
        detection_dict: dict = {}
        if config is not None:
            detection_dict = config.detection.model_dump()

        # Capture ALL pump.fun graduations: rely on the source=="pump_amm" filter
        # to cut non-pump pairs, NOT a liquidity floor.  The prior 5000-USDC floor
        # assumed grads "exit with ≥$7k", but a Birdeye NEW_PAIR frame carries
        # liquidity at pool-CREATION time (~$140 observed live, before the ~85-SOL
        # grad liquidity is deposited), so ANY floor above ~$140 rejected EVERY real
        # graduation — the backstop caught nothing (observed: "liquidity=140 < 5000")
        # and contributed zero gap-fill during the 2026-06-23 detection outage.
        detection_dict = dict(detection_dict)
        detection_dict["graduation_min_liquidity"] = 0

        source = BirdeyeGraduationSource(
            api_key=birdeye_api_key,
            config=detection_dict,
            clock=self._clock,
        )
        reconciler = MigrateReconciler(source=source, clock=self._clock)
        logger.info(
            "%s graduation-secondary: Birdeye gap-fill wired min_liquidity=%d",
            LOG_PREFIX,
            detection_dict.get("graduation_min_liquidity", 0),
        )
        return reconciler, source

    def _build_postgrad_source(self, mint: str):
        """Build a POST-grad BirdeyeSwapSource for one mint (adapter wiring layer).

        REUSES the existing BirdeyeSwapSource(api_key, mint) — the proven Birdeye
        SUBSCRIBE_TXS PumpSwap swap client (no handshake rewrite).  This is the
        ONLY place the concrete post-grad source is instantiated.
        """
        from core.tape.birdeye_swap_source import BirdeyeSwapSource

        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        return BirdeyeSwapSource(api_key=api_key, mint=mint)



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

    US-92 FIX: ``mint`` is stamped directly into the returned dict so that every
    post-grad row written to the lake via LakeTapeSink.record() carries its
    graduated-token mint.  Without this the lake rows have keys
    [block_time, owner, phase, price, rel, side, slot, vol, vol_usd] with NO mint,
    making post-grad fills unattributable to a token offline.  The mint is KNOWN
    at emission from the per-subscription factory call (``self._postgrad_factory(mint)``)
    — it requires ZERO additional RPC calls or credit spend.

    Returns None for an unmappable event (the caller skips None).
    """
    from core.tape.birdeye_swap_mapper import map_birdeye_swap

    mapped = map_birdeye_swap(event)
    if mapped is None:
        return None
    block_time = int(mapped.get("block_time", 0))
    return {
        "mint": mint,          # US-92: stamped from per-subscription context — zero credits
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
