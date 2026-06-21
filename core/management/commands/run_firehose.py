# ---
# module: core.management.commands.run_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: django, asyncio, logging, signal, asgiref,
#               core.tape.birdeye_graduation_source, core.tape.birdeye_swap_source,
#               core.tape.birdeye_swap_mapper, core.tape.helius_birth_tape_source,
#               core.management.commands.run_listener, core.detection.consumer,
#               core.firehose.spine, core.clock, core.resolver, core.models,
#               core.v4_rep_builder
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
  a. COLLECTION    — HeliusBirthTapeSource -> MappedSwapSource(decode) ->
                     LivePreGradBuffer, continuous, buffering EVERY pump.fun
                     bonding-curve swap by mint with NO graduation anchor (the
                     anchor is applied RETROACTIVELY at score time).  A two-tier
                     idle-kill evicts dead UNgraduated mints; graduated mints are
                     retained for scoring.  (The OLD token_store-filtered birth-
                     tape recorder dropped all pre-grad swaps — chicken-and-egg.)
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
import json
import logging
import signal
import sys
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
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_SCORE_TICK_S = 5.0
DEFAULT_POSTGRAD_TICK_S = 5.0
#: AC-3: how often the durable tape sink is flushed to the lake off the hot path.
DEFAULT_TAPE_FLUSH_INTERVAL_S = 10.0
DEFAULT_MAX_POSTGRAD_SUBSCRIPTIONS = 5


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

# Cache sentinel: WalletBankLookup is expensive to build (~44s, ~27 MB).
# It is loaded ONCE per FirehoseDaemon instance in __init__, stored as
# self._wallet_bank.  _try_load_wallet_bank() is the safe loader called there.
_wallet_bank_module_cache: "dict[str, object]" = {}  # {bank_path_str: WalletBankLookup}


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
        time_buyers, size_buyers = extract_buyers_from_swaps(
            swaps, deployer=deployer
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
        tape_sink=None,
        wallet_bank=None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._max_runtime_s = max_runtime_s
        self._score_tick_s = score_tick_s
        self._postgrad_tick_s = postgrad_tick_s
        self._collection_factory = collection_factory or self._build_collection
        self._graduation_factory = graduation_factory or self._build_graduation
        self._postgrad_factory = postgrad_factory or self._build_postgrad_source
        self._clock = clock or WallClock()
        # AC-3 durable tape sink: every collected swap is appended to the
        # daily-partitioned lake (lake/tapes) so a soak banks a replayable tape and
        # the US-78 swaps surface populates.  Injectable for tests; default writes
        # to the shared lake volume.  The hooks tag phase so the export derives the
        # venue (pre->pump_dot_fun, post->pump_amm).
        if tape_sink is None:
            from core.firehose.tape_sink import LakeTapeSink

            tape_sink = LakeTapeSink()
        self._tape_sink = tape_sink
        self._tape = TapeStore(on_add=lambda _m, s: self._tape_sink.record(s, "pre"))
        self._postgrad_tape = TapeStore(on_add=lambda _m, s: self._tape_sink.record(s, "post"))
        self._scored_mints: set[str] = set()
        # Mints known to have graduated (Token row exists).  Refreshed by the
        # scoring task and read by the collection buffer's two-tier idle-kill so
        # a graduated mint is PROTECTED from pre-grad eviction (kept for scoring).
        self._graduated_mints: set[str] = set()
        self._stop = asyncio.Event()
        self._active_sources: list = []
        # POST-grad subscription bookkeeping.
        self._postgrad_tasks: dict[str, asyncio.Task] = {}
        self._postgrad_seen: set[str] = set()
        self._postgrad_sources: dict[str, object] = {}
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
                    Path(__file__).resolve().parents[4]
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
        flush_task = asyncio.create_task(self._tape_flush_loop(deadline), name="firehose-tape-flush")
        watcher_task = asyncio.create_task(self._flip_watcher(deadline), name="firehose-watcher")

        tasks = [collection_task, graduation_task, scoring_task, postgrad_task, flush_task, watcher_task]
        try:
            # The watcher returns when firehose flips False / stop / deadline.
            await watcher_task
        finally:
            for t in (collection_task, graduation_task, scoring_task, postgrad_task, flush_task):
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_postgrad_subscriptions()
            await self._disconnect_all_sources()
            # AC-3: final flush so the in-flight tail (< flush_every) is persisted.
            await sync_to_async(self._tape_sink.flush, thread_sensitive=True)()
            logger.info("%s active run stopped — all tasks cancelled, sources disconnected.", LOG_PREFIX)

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

    async def _collection_loop(self) -> None:
        """Buffer ALL pump.fun bonding-curve swaps by mint — NO graduation anchor.

        THE FIX (chicken-and-egg): the OLD wiring used the token_store-filtered
        birth-tape recorder, which dropped every swap whose mint had not yet
        graduated — i.e. every PRE-grad swap, which is exactly what scoring needs.
        Here a LivePreGradBuffer reads the SAME Helius source + mapper but BYPASSES
        the token_store filter, buffering every mint's swaps (absolute block_time,
        no rel) into self._tape.  The graduation anchor is applied RETROACTIVELY by
        assemble_pregrad_features at score time.
        """
        from core.firehose.live_pregrad_buffer import LivePreGradBuffer

        # Resolve the pre-grad idle TTL ONCE in a SYNC context (ORM read) so the
        # buffer never touches the ORM on the async hot path.
        idle_ttl_s = await sync_to_async(
            self._resolve_pre_grad_ttl_sync, thread_sensitive=True
        )()

        # RECONNECT LOOP (fix): the live Helius WS closes after a while (idle /
        # plan limit) — ``buffer.run()`` then returns NORMALLY ("buffer
        # finished").  The OLD code ran it ONCE, so over a long window collection
        # silently DIED mid-run (observed ~49 min in: "buffer finished" then no
        # further swaps), the buffer went stale, and nothing graduating afterward
        # could ever score.  Here we rebuild the source and RESTART, RETAINING the
        # accumulated buffer (self._tape), until the firehose flips inactive or the
        # daemon stops.  Shutdown/deadline/flip cancels this task -> CancelledError
        # propagates out of the loop.
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
            backoff = 1.0  # a successful build resets the backoff
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
                "%s collection: started (Helius birth-tape -> live pre-grad buffer, "
                "attempt=%d, ALL mints, no anchor gate; idle_ttl=%.0fs).",
                LOG_PREFIX, attempt, idle_ttl_s,
            )
            try:
                await buffer.run()
            except asyncio.CancelledError:
                raise  # daemon shutdown / deadline / flip — propagate to stop cleanly
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s collection: stream error (%s) — will reconnect.", LOG_PREFIX, exc)
            finally:
                try:
                    self._active_sources.remove(source)
                except ValueError:
                    pass

            # The WS closed (normal return or error).  Stop only if the firehose
            # flipped inactive or we are shutting down; otherwise reconnect —
            # RETAINING the buffer so in-flight pre-grad tape survives the drop.
            firehose_active, _, _ = await self._read_state()
            if self._stop.is_set() or not firehose_active:
                logger.info(
                    "%s collection: stream ended; firehose inactive/stopping — collection done "
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
    # Task b — GRADUATION (Birdeye new-listing -> DetectionConsumer)
    # ------------------------------------------------------------------

    async def _graduation_loop(self) -> None:
        # RECONNECT LOOP (same fix as collection): the Birdeye new-listing WS can
        # also close over a long window; running the consumer ONCE would silently
        # stop graduation detection (no new graduations -> no scoring triggers).
        # Rebuild + restart until the firehose flips inactive / the daemon stops.
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
            logger.info("%s graduation: started (Birdeye new-listing -> DetectionConsumer).", LOG_PREFIX)
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

        # v4 path: detect by feature count on the active scorer.
        # len(scorer.feature_list) == 53 → v4 REP+recurrence path
        # len(scorer.feature_list) == 20 → v3.2 enrich-only path (unchanged)
        # getattr fallback: test stubs without feature_list default to the v3.2 path.
        _scorer_feature_list = getattr(scorer, "feature_list", [])
        _is_v4_model = len(_scorer_feature_list) == 53

        for mint, graduated_block_time in due:
            swaps = self._tape.get(mint)
            # US-76 BREAK-1: serve in USD via ONE SOL/USD spot (the shared cached
            # spot resolved in the scoring context).  vol = vol_sol × spot inside
            # the normalisation layer; reproduces the offline trained feature space
            # and makes pre_insider_sell_ratio parity-true (directives §8/§9).
            if _is_v4_model:
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
        graduated: set[str] = set()
        for tok in Token.objects.all():
            # Refresh the graduated-mint set so the collection buffer's two-tier
            # idle-kill protects every graduated mint (retained for scoring) — not
            # just the ones already scored.  A Token row == graduated.
            graduated.add(tok.mint)
            if tok.mint in self._scored_mints:
                continue
            if score_time_reached(tok.graduated_at, score_at, now):
                due.append((tok.mint, int(tok.graduated_block_time)))
        self._graduated_mints = graduated
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
