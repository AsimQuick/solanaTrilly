# ---
# module: core.firehose.live_pregrad_buffer
# sprint: sprint-14
# story: live-firehose-pregrad-buffer
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: core.clock, core.datasource, datetime, logging, typing
# ---
"""LivePreGradBuffer — buffer ALL pump.fun bonding-curve swaps by mint, no anchor.

THE CHICKEN-AND-EGG BUG (fixed here)
====================================
The firehose's COLLECTION task previously wired the OFFLINE
``build_birth_tape_recorder(token_store=...)``.  That recorder hears EVERY
pump.fun swap program-wide but ONLY normalizes/records swaps whose mint is
already present in ``token_store`` (mint -> graduated_block_time), because
``NormalizedSwap.from_raw_swap`` needs the graduation anchor to compute
``rel = block_time - graduated_block_time``.

But ``token_store`` is built from already-GRADUATED Token rows.  A token's
PRE-grad swaps occur BEFORE it graduates — when it is NOT yet in token_store —
so those pre-grad swaps were dropped on the floor.  The live pre-grad tape was
therefore always empty, every scoring tick logged "no pre-grad tape yet (0
swaps)", and the model never scored.

  - OFFLINE knows the winners post-hoc and re-reads their pre-grad swaps from
    the lake (the graduation anchor is already known).
  - LIVE must buffer ALL bonding-curve swaps by mint as they stream — with NO
    graduation anchor — and apply the anchor RETROACTIVELY the moment a mint
    graduates (the anchor becomes known at score time).

THE FIX
=======
``LivePreGradBuffer`` reads the SAME source + mapper the birth-tape recorder
uses — ``MappedSwapSource(HeliusBirthTapeSource(api_key), decode_helius_notification)``
— but BYPASSES the token_store filter entirely.  Every mapped swap is buffered
into the daemon's in-memory pre-grad TapeStore keyed by its mint, carrying the
ABSOLUTE ``block_time`` and NO ``rel``.  Because the buffered swap dict has no
``rel`` key, ``firehose.spine.to_pregrad_swaps`` computes
``rel = block_time - graduated_block_time`` RETROACTIVELY at feature-assembly
time (when the anchor is finally known).  Buffering NEVER requires an anchor.

TWO-TIER IDLE-KILL (US-35 policy, bounded memory)
=================================================
A mint's buffered pre-grad swaps are EVICTED when it has had no new swap for
``tape.pre_grad_idle_kill_ttl_s`` (config, default 300 s) AND it has not
graduated.  Active mints keep buffering until graduation+anchor.  Graduated
mints are PROTECTED from pre-grad idle eviction (they are retained for scoring).
Idle is measured against the INJECTED daemon clock (AC-2.2: no datetime.now()/
time.time() in core/).

OBSERVABILITY
=============
Stable, greppable lines:
  - "[FIREHOSE] collection: idle-evict mint=… (idle>…s, N swaps dropped)"
  - "[FIREHOSE] collection: buffer mint_count=… total_swaps=… (last_swap mint=…)"
    (a periodic heartbeat so the buffer is observable in a live window)

SEAM (Principle #7)
===================
This consumer depends ONLY on the abstract DataSource + Clock interfaces — it
never instantiates a concrete live source.  The offline tests drive it with a
mock/replay source, so the buffering logic itself is exercised with zero network.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from core.clock import Clock
from core.datasource import DataSource

logger = logging.getLogger(__name__)

LOG_PREFIX = "[FIREHOSE]"

#: Two-tier idle-kill default for UNgraduated mints (seconds).  Mirrors
#: TapeConfig.pre_grad_idle_kill_ttl_s so the buffer evicts dead pre-grad mints
#: even when no active config is resolvable.
DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S: int = 300

#: How often (seconds, measured on the injected clock) to emit the buffer
#: heartbeat line so a live window can confirm the buffer is filling.
DEFAULT_HEARTBEAT_INTERVAL_S: float = 30.0


def buffered_pregrad_swap(mapped: dict[str, Any]) -> dict[str, Any]:
    """Project a mapped Helius swap dict to the buffered pre-grad swap shape.

    The buffered swap carries the ABSOLUTE ``block_time`` and deliberately OMITS
    ``rel`` — ``firehose.spine.to_pregrad_swaps`` computes ``rel`` retroactively
    from the graduation anchor at feature-assembly time.  The kept fields are
    exactly what ``compute_pregrad_features`` / ``_swaps_to_trade_tuples`` read
    (``block_time``, ``side``, ``owner``, ``vol``/``vol_sol``, ``price``,
    ``slot``, ``signature``).

    Pure function (no clock, no I/O, no anchor).  ``vol`` is carried alongside
    ``vol_sol`` so the downstream ``to_pregrad_swaps`` ``vol`` fallback resolves
    identically whether it reads ``vol`` or ``vol_sol``.

    Args:
        mapped: A swap dict produced by ``decode_helius_notification`` (the
            MappedSwapSource mapper output) — already the internal §7.1 shape.

    Returns:
        A buffered pre-grad swap dict WITHOUT ``rel`` (anchor applied later).
    """
    vol_sol = mapped.get("vol_sol", mapped.get("vol", 0.0))
    return {
        "mint": mapped.get("mint", ""),
        "block_time": int(mapped.get("block_time", 0)),
        "slot": int(mapped.get("slot", 0)),
        "signature": str(mapped.get("signature", "")),
        # NOTE: deliberately NO "rel" key — retroactive anchoring computes it.
        "price": float(mapped.get("price", 0.0) or 0.0),
        "side": str(mapped.get("side", "")),
        "vol": float(vol_sol or 0.0),
        "vol_sol": float(vol_sol or 0.0),
        "vol_usd": float(mapped.get("vol_usd", 0.0) or 0.0),
        "owner": mapped.get("owner"),
    }


class LivePreGradBuffer:
    """Consume a mapped pump.fun swap stream; buffer ALL mints by mint, no anchor.

    Reads a DataSource that already yields internal §7.1 swap dicts (i.e. a
    ``MappedSwapSource(HeliusBirthTapeSource, decode_helius_notification)`` in
    production, or a mock/replay source offline).  For EVERY swap it:

      1. projects to the buffered pre-grad shape (no ``rel`` anchor),
      2. appends it into ``store`` keyed by the swap's mint,
      3. records last-seen time (injected clock) for the two-tier idle-kill,
      4. evicts dead UNgraduated mints whose idle time exceeds the pre-grad TTL,
      5. periodically emits a buffer heartbeat line.

    Graduation status is read via an injected ``is_graduated(mint) -> bool``
    callback (the daemon supplies one backed by the Token rows / its scored set),
    so a graduated mint is PROTECTED from pre-grad idle eviction and retained
    through scoring.  The buffer never needs a graduation anchor to buffer.

    Args:
        source:        A DataSource yielding internal §7.1 swap dicts (with a
                       "mint" field).  Connected/disconnected by run().
        store:         The daemon's pre-grad TapeStore (anything with
                       ``add(mint, swap)``, ``get(mint)``, ``count(mint)`` and an
                       iterable/discardable internal map).  The same object the
                       scoring task reads via ``store.get(mint)``.
        clock:         Injected Clock — the SOLE source of "now" for idle-kill
                       and the heartbeat (AC-2.2: no datetime.now()/time.time()).
        is_graduated:  Optional callback ``(mint) -> bool``; graduated mints are
                       exempt from pre-grad idle eviction.  Defaults to "never
                       graduated" (every mint is eligible for pre-grad eviction).
        idle_ttl_s:    Optional override for the pre-grad idle TTL (seconds).
                       When None, resolved from the active config's
                       ``tape.pre_grad_idle_kill_ttl_s`` via *config_resolver*,
                       falling back to DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S.
        config_resolver: Optional callable returning the active config (or None)
                       used to resolve ``tape.pre_grad_idle_kill_ttl_s`` when
                       *idle_ttl_s* is None.  Read once at construction (a daemon
                       run is short-lived relative to config edits).
        heartbeat_interval_s: Seconds between buffer heartbeat lines.
        evict_remover: Optional callable ``(store, mint) -> None`` used to drop a
                       mint's buffer on eviction.  Defaults to a TapeStore-aware
                       remover; injectable for stores with a different internal.
    """

    def __init__(
        self,
        *,
        source: DataSource,
        store: Any,
        clock: Clock,
        is_graduated: Optional[Callable[[str], bool]] = None,
        idle_ttl_s: Optional[float] = None,
        config_resolver: Optional[Callable[[], Any]] = None,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        evict_remover: Optional[Callable[[Any, str], None]] = None,
    ) -> None:
        self._source = source
        self._store = store
        self._clock = clock
        self._is_graduated = is_graduated or (lambda _mint: False)
        self._heartbeat_interval_s = float(heartbeat_interval_s)
        self._evict_remover = evict_remover or _default_evict_remover
        self._idle_ttl_s = float(
            idle_ttl_s
            if idle_ttl_s is not None
            else _resolve_pre_grad_ttl(config_resolver)
        )
        self._last_seen: dict[str, datetime] = {}
        self._buffered_count = 0
        self._last_heartbeat_at: datetime | None = None

    # ------------------------------------------------------------------
    # Buffering — one swap at a time (the streaming tap)
    # ------------------------------------------------------------------

    def ingest(self, mapped: dict[str, Any]) -> None:
        """Buffer ONE mapped swap (no anchor) + advance the idle-kill bookkeeping.

        Skips a swap with no mint (unmappable / non-trade).  Records last-seen on
        the injected clock so the two-tier idle-kill can evict dead UNgraduated
        mints, then sweeps + heartbeats.
        """
        mint = mapped.get("mint")
        if not mint:
            return
        now = self._clock.now()
        self._store.add(mint, buffered_pregrad_swap(mapped))
        self._last_seen[mint] = now
        self._buffered_count += 1
        self._sweep_idle(now, last_swap_mint=mint)
        self._maybe_heartbeat(now)

    # ------------------------------------------------------------------
    # Two-tier idle-kill — evict dead UNgraduated mints (bounded memory)
    # ------------------------------------------------------------------

    def _sweep_idle(self, now: datetime, *, last_swap_mint: str | None = None) -> None:
        """Evict UNgraduated mints idle > pre_grad TTL; keep graduated/active mints.

        AC-2.2: idle is measured ONLY against the injected clock.  A graduated
        mint is exempt (it is retained through scoring); an active mint (idle <=
        TTL) is retained so it keeps buffering until graduation+anchor.
        """
        ttl = timedelta(seconds=self._idle_ttl_s)
        stale: list[str] = []
        for mint, last in self._last_seen.items():
            if self._is_graduated(mint):
                continue  # graduated -> protected, retained for scoring
            if now - last > ttl:
                stale.append(mint)
        for mint in stale:
            dropped = self._store.count(mint)
            self._evict_remover(self._store, mint)
            self._last_seen.pop(mint, None)
            logger.info(
                "%s collection: idle-evict mint=%s (idle>%.0fs, %d swaps dropped)",
                LOG_PREFIX,
                mint,
                self._idle_ttl_s,
                dropped,
            )

    # ------------------------------------------------------------------
    # Heartbeat — make the buffer observable in a live window
    # ------------------------------------------------------------------

    def _maybe_heartbeat(self, now: datetime, *, last_swap_mint: str | None = None) -> None:
        """Emit a periodic buffer heartbeat (mint_count / total_swaps / last mint)."""
        if (
            self._last_heartbeat_at is not None
            and (now - self._last_heartbeat_at).total_seconds() < self._heartbeat_interval_s
        ):
            return
        self._last_heartbeat_at = now
        mint_count = len(self._last_seen)
        total = sum(self._store.count(m) for m in self._last_seen)
        last_mint = last_swap_mint or _newest_mint(self._last_seen)
        logger.info(
            "%s collection: buffer mint_count=%d total_swaps=%d (last_swap mint=%s)",
            LOG_PREFIX,
            mint_count,
            total,
            last_mint,
        )

    # ------------------------------------------------------------------
    # Run loop — consume the source until it ends / the task is cancelled
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect the source, ingest every mapped swap, disconnect at the end.

        For a CONTINUOUS live source this never returns until the task is
        cancelled on shutdown; the streaming ``ingest`` fills the store in real
        time (the scoring task reads ``store.get(mint)`` concurrently).  For a
        bounded/replay source it returns when the source is exhausted.
        """
        await self._source.connect()
        try:
            async for mapped in self._source.events():
                self.ingest(mapped)
        finally:
            try:
                await self._source.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.debug("%s collection: source disconnect raised (ignored).", LOG_PREFIX)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_evict_remover(store: Any, mint: str) -> None:
    """Drop a mint's buffered swaps from a TapeStore-like object.

    Prefers an explicit ``store.drop(mint)`` if present; otherwise reaches into
    the conventional ``store._by_mint`` map (the firehose TapeStore internal).
    Best-effort: a store that exposes neither is left untouched.
    """
    drop = getattr(store, "drop", None)
    if callable(drop):
        drop(mint)
        return
    by_mint = getattr(store, "_by_mint", None)
    if isinstance(by_mint, dict):
        by_mint.pop(mint, None)


def _newest_mint(last_seen: dict[str, datetime]) -> str | None:
    """Return the mint with the most recent last-seen time, or None if empty."""
    if not last_seen:
        return None
    return max(last_seen.items(), key=lambda kv: kv[1])[0]


def _resolve_pre_grad_ttl(config_resolver: Optional[Callable[[], Any]]) -> float:
    """Resolve tape.pre_grad_idle_kill_ttl_s from the active config (or default).

    Returns DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S when no resolver is supplied, the
    resolver returns None, or the field is absent — so the buffer always has a
    finite, bounded TTL even before a config is loaded.
    """
    if config_resolver is None:
        return float(DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
    try:
        config = config_resolver()
    except Exception:  # noqa: BLE001 - resolver must never break buffering
        return float(DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
    if config is None:
        return float(DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
    tape = getattr(config, "tape", None)
    ttl = getattr(tape, "pre_grad_idle_kill_ttl_s", DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)
    return float(ttl or DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S)


__all__ = [
    "LOG_PREFIX",
    "DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "LivePreGradBuffer",
    "buffered_pregrad_swap",
]
