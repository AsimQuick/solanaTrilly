# ---
# module: core.firehose.spine
# sprint: sprint-14
# story: live-firehose-spine, US-76
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: core.pregrad_features, core.normalized_swap, core.scorer,
#               trading.tape_settler, trading.position_closer, trading.schemas,
#               trading.models (lazy)
# ---
"""Firehose spine -- deterministic scoring + paper-trade helpers (OBSERVE/PAPER only).

These are the pure / dependency-injected building blocks the run_firehose daemon
composes.  They are factored out of the management command so they can be
exercised offline (replay/mock) and RUN-TWICE IDENTICAL:

  - assemble_pregrad_features(): the 20 PRE_FEATURE_NAMES from collected pre-grad
    swaps for one mint (rel in [-3600, 0)), via the SAME core.pregrad_features math
    the offline lab uses (Principle #2).  US-76 P1.2: 3600s window cap enforced here
    (matches offline backfill_pregrad.py CAP=3600).  US-76 P2.4: secondary gate
    requires >= min_pregrad_swaps (default 20).  No live-only feature assembly.

  - score_pregrad(): score the assembled features via the active BlendScorer
    (BlendScorer.from_registry / ReferenceDistribution), gated by scoring_enabled.

  - gate_passes(): evaluate the active scoring/trading gate (adaptive_topk /
    threshold) over a blend score.

  - settle_paper_trade(): open + settle a PAPER position via the P8 shared
    apparatus (tape settler + position_closer), writing a shared trading.Position
    row.  HARD RULE: when trading_enabled is False, fills are booked at the
    observed tape price and NO real RPC/Sender path is ever reached.  This module
    NEVER imports trading.sender or trading.execution_core's live branch.

SAFETY INVARIANT
================
assert_paper_only(state) raises if trading_enabled is True -- every paper path
calls it first so a real-capital path is structurally unreachable here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

from core.normalized_swap import normalize_raw_for_features
from core.pregrad_features import PRE_FEATURE_NAMES, compute_pregrad_features

logger = logging.getLogger(__name__)

LOG_PREFIX = "[FIREHOSE]"


# ---------------------------------------------------------------------------
# Safety guard -- paper-only invariant
# ---------------------------------------------------------------------------


class RealCapitalGuardError(RuntimeError):
    """Raised if a paper path is entered while trading_enabled is True.

    The firehose spine is OBSERVE/PAPER only.  It must never be used as a
    real-capital execution path; that path lives behind trading.execution_core +
    trading.sender and the operator-gated Cutover (PRD §16), NOT here.
    """


def assert_paper_only(trading_enabled: bool) -> None:
    """Guard: refuse to proceed on any paper path if trading_enabled is True.

    Args:
        trading_enabled: The PipelineState.trading_enabled flag value.

    Raises:
        RealCapitalGuardError: when trading_enabled is True.
    """
    if trading_enabled:
        raise RealCapitalGuardError(
            "firehose spine is OBSERVE/PAPER only -- refusing to run a paper path "
            "while PipelineState.trading_enabled is True. Real capital must route "
            "through trading.execution_core + the operator-gated Cutover, not here."
        )


# ---------------------------------------------------------------------------
# Swap-shape normalization -- collected tape -> §7.1 dicts pregrad math expects
# ---------------------------------------------------------------------------


def to_pregrad_swaps(
    swaps: Sequence[dict],
    graduated_block_time: int,
    *,
    base_decimals: int = 6,
) -> list[dict]:
    """Normalise collected swap dicts to the §7.1 shape compute_pregrad_features expects.

    US-76 AC-2: this function now delegates to ``normalize_raw_for_features``
    — the single normalisation layer that guards against JSONB None (#358),
    zero/dust prices (#405), and applies per-mint decimals (#288).

    Each input swap may be either a raw collected swap (with ``block_time``,
    ``side``, ``owner``, ``vol_sol``/``vol``, ``price``, ``slot``, ``signature``)
    or an already-§7.1 dict.  ``rel`` is computed as
    ``block_time - graduated_block_time`` when absent (pre-grad swaps => rel < 0).

    Zero/dust price guard: swaps rejected by ``normalize_raw_for_features`` (zero
    or dust price) are silently dropped.  They were previously passed through as
    price=0.0 which would have caused silent division-by-zero errors in the
    settler and feature math.

    JSONB None guard: JSONB ``None`` values are coerced to ``float("nan")`` before
    any arithmetic, not silently converted to 0.0 by the old ``or 0.0`` pattern.

    Pure function (no clock, no I/O).  Stable + deterministic.

    Args:
        swaps:                Collected swaps for ONE mint.
        graduated_block_time: The mint's graduation epoch (anchors rel, §7.1).
        base_decimals:        Per-mint base-token decimals (seed from graduation
                              event MEME_DATA ``decimals`` field).  Default 6 for
                              pump.fun SPL tokens.  Pass via MintDecimalsResolver
                              at the call site; never hardcode in callers.

    Returns:
        A list of §7.1 swap dicts with rel/side/owner/vol/block_time/slot/
        signature/price keys.  Swaps with zero/dust prices are excluded.
    """
    # Build a list of recent prices to supply the dust-median guard.
    # We use a rolling window of the prices seen so far in this tape.
    peer_prices: list[float] = []
    out: list[dict] = []
    for s in swaps:
        normalised = normalize_raw_for_features(
            s,
            graduated_block_time=graduated_block_time,
            base_decimals=base_decimals,
            peer_prices=peer_prices,
        )
        if normalised is None:
            # Rejected: zero/dust price, missing block_time, or invalid side.
            # Log at DEBUG — callers get the count implicitly from len(out).
            logger.debug(
                "%s to_pregrad_swaps: swap rejected by normaliser (zero/dust price or invalid fields)",
                LOG_PREFIX,
            )
            continue
        peer_prices.append(normalised["price"])
        out.append(normalised)
    return out


def assemble_pregrad_features(
    swaps: Sequence[dict],
    graduated_block_time: int,
    *,
    deployer: str | None = None,
    min_pregrad_swaps: int = 20,
    base_decimals: int = 6,
) -> dict | None:
    """Assemble the 20 PRE_FEATURE_NAMES features from collected pre-grad swaps.

    Delegates to the SAME core.pregrad_features.compute_pregrad_features the
    offline lab and the shared FeatureExtractor use (Principle #2) -- selecting
    only pre-grad swaps within the 3600s window cap.

    US-76 AC-2: ``base_decimals`` is now threaded through to ``to_pregrad_swaps``
    so the single normalisation layer applies per-mint decimal conversion.  Seed
    from the graduation-event MEME_DATA ``decimals`` field via MintDecimalsResolver;
    the default (6) is the pump.fun SPL fallback.

    Window cap (US-76 P1.2 / binding-contract Q4):
        Only swaps with -3600 <= rel < 0 are used.  Offline backfill_pregrad.py
        truncates at grad-3600 for tokens older than 60 min; live must match.
        Applied after to_pregrad_swaps() computes rel values so the offline
        anchor point is honoured (do NOT walk back to creation_time for tokens
        whose curve life exceeds 60 min).

    Secondary gate (US-76 P2.4 / binding-contract):
        If fewer than min_pregrad_swaps swaps pass the window filter, returns
        None and logs a skip.  Feature-stability floor: p5=51, p10=100 for real
        graduates; only 0.4% of true graduates have <20 pre-grad swaps, so the
        false-reject rate is negligible while feed-bug (instant) graduations
        typically have ~0 swaps.

    Args:
        swaps:                Collected swaps for ONE mint.
        graduated_block_time: The mint's graduation epoch (anchors rel).
        deployer:             Optional deployer wallet for pre_deployer_* features.
        min_pregrad_swaps:    Minimum number of pre-grad swaps required to score
                              (secondary gate, default 20).
        base_decimals:        Per-mint base-token decimals (from MintDecimalsResolver).
                              Default 6 for pump.fun SPL tokens.

    Returns:
        Dict of the 20 pre_* features, or None if there are no usable pre-grad
        swaps after the window cap or the secondary gate rejects the token.
    """
    normalized = to_pregrad_swaps(swaps, graduated_block_time, base_decimals=base_decimals)
    # P1.2 -- 3600s window cap: match offline backfill_pregrad.py CAP=3600.
    # Only swaps with rel in [-3600, 0) qualify.
    windowed = [s for s in normalized if -3600 <= s["rel"] < 0]
    # P2.4 -- secondary gate: require >= min_pregrad_swaps for feature stability.
    if len(windowed) < min_pregrad_swaps:
        logger.info(
            "%s secondary-gate skip: %d pre-grad swaps in window < min=%d -- not scored",
            LOG_PREFIX,
            len(windowed),
            min_pregrad_swaps,
        )
        return None
    return compute_pregrad_features(windowed, deployer=deployer)


# ---------------------------------------------------------------------------
# Scoring -- via the active BlendScorer (reuses score_token's exact recipe)
# ---------------------------------------------------------------------------


def score_pregrad(
    features: dict,
    *,
    scorer,
    ref_dist=None,
) -> dict:
    """Score one token's assembled features via the active BlendScorer.

    Mirrors core.tasks.score_token's recipe: when a ReferenceDistribution is
    supplied use score_single (the AC-43.2 live single-token path); otherwise
    fall back to score_pool([features])[0].

    Pure w.r.t. the injected scorer (no DB, no clock).  RUN-TWICE IDENTICAL for
    the same scorer + features.

    Args:
        features: The assembled feature dict (20 pre_* features).
        scorer:   A BlendScorer (BlendScorer.from_registry(get_active_model())).
        ref_dist: Optional ReferenceDistribution for single-token ranking.

    Returns:
        The score result dict: {label_scores, label_ranks, blend_score}.
    """
    if ref_dist is not None:
        return scorer.score_single(features, ref_dist)
    return scorer.score_pool([features])[0]


# ---------------------------------------------------------------------------
# Gate -- adaptive_topk / threshold over a blend score
# ---------------------------------------------------------------------------


def gate_passes(blend_score: float, *, gate: str, threshold: float) -> bool:
    """Evaluate the active scoring/trading gate over a blend score.

    Both supported gates ("adaptive_topk" and "threshold") reduce, at the
    single-token live serving moment, to a percentile/threshold comparison:
    a blend percentile-rank >= threshold passes.  (adaptive_topk's "top-K"
    selection over a live pool collapses to a percentile cut because live serving
    arrives one token at a time -- the same resolution as ReferenceDistribution
    in scorer.py / AC-43.2.)

    Pure function.

    Args:
        blend_score: The token's blend percentile-rank in (0, 1].
        gate:        "adaptive_topk" | "threshold" (any other value -> threshold).
        threshold:   Pass cut in [0, 1].

    Returns:
        True if the score passes the gate.
    """
    return float(blend_score) >= float(threshold)


# ---------------------------------------------------------------------------
# Paper trade -- open + settle via the P8 shared apparatus (OBSERVE/PAPER only)
# ---------------------------------------------------------------------------


def settle_paper_trade(
    *,
    mint: str,
    score: float,
    trades: Sequence[tuple[float, float, float]],
    entry_ts_epoch: float,
    trading_config,
    size_sol: float,
    sol_usd: float,
    trading_enabled: bool,
    now: Optional[datetime] = None,
    position_factory: Optional[Callable[..., Any]] = None,
    settler: Optional[Callable[..., dict]] = None,
    closer: Optional[Callable[..., Any]] = None,
) -> Optional[Any]:
    """Open + settle a PAPER position for *mint* via the shared P8 apparatus.

    OBSERVE/PAPER ONLY.  Calls assert_paper_only(trading_enabled) FIRST so this
    function is structurally unreachable while trading_enabled is True.  The fill
    is booked at the observed tape price by the tape settler; NO RPC / Sender
    send path is ever invoked here (this module imports neither trading.sender
    nor the live branch of trading.execution_core).

    Wiring (all injectable for offline tests):
        settler          = trading.tape_settler.simulate_tape_exit
        position_factory  = trading.models.Position (open a PAPER row)
        closer           = trading.position_closer.settle_paper_position

    Args:
        mint:            Token mint.
        score:           The blend score (stored on the Position row).
        trades:          (block_time_s, price, usd_volume) tuples for the mint's
                         post-grad tape -- the settler's honest-fill input.
        entry_ts_epoch:  Entry timestamp as a Unix epoch float (paper entry).
        trading_config:  A trading.schemas.TradingConfig instance.
        size_sol:        Paper position size in SOL.
        sol_usd:         SOL/USD rate for the impact model.
        trading_enabled: PipelineState.trading_enabled -- MUST be False.
        now:             Settlement timestamp (closed_at); defaults to UTC now via
                         the caller (injected for determinism in tests).
        position_factory: Factory that builds + returns a saved Position-like row.
        settler:          simulate_tape_exit (injected for tests).
        closer:           settle_paper_position (injected for tests).

    Returns:
        The settled Position (status CLOSED), or None if the tape settler deemed
        the position un-enterable (no-tape / dead / slip-miss) -- never booked as
        a 0% or -100% row (Principle #5).

    Raises:
        RealCapitalGuardError: if trading_enabled is True.
    """
    assert_paper_only(trading_enabled)

    if settler is None:
        from trading.tape_settler import simulate_tape_exit as settler  # noqa: PLC0415
    if closer is None:
        from trading.position_closer import settle_paper_position as closer  # noqa: PLC0415

    result = settler(
        list(trades),
        entry_ts_epoch,
        trading_config,
        size_sol,
        sol_usd,
    )

    if not result.get("enterable", False):
        logger.info(
            "%s paper-skip: mint=%s reason=%s (un-enterable -- not booked)",
            LOG_PREFIX,
            mint,
            result.get("reason", "unknown"),
        )
        return None

    # Honest-fill entry price: the last pre-entry swap price (the settler's
    # "quote"), reconstructed here from the tape window so the Position row's
    # entry_price matches the settler's fill basis.
    entry_price = _entry_quote_price(trades, entry_ts_epoch)

    entry_dt = datetime.fromtimestamp(entry_ts_epoch, tz=timezone.utc)

    # --- Open the PAPER position (shared trading.Position row) ---
    position = _open_paper_position(
        mint=mint,
        score=score,
        entry_ts=entry_dt,
        entry_price=entry_price,
        size_sol=size_sol,
        position_factory=position_factory,
    )

    logger.info(
        "%s paper-buy: mint=%s size=$%.2f entry_price=%.10f score=%.4f",
        LOG_PREFIX,
        mint,
        size_sol * sol_usd,
        entry_price,
        score,
    )

    # --- Settle via the SOLE paper settler path (position_closer) ---
    settled = closer(position, result, now=now, fill_price=entry_price)

    logger.info(
        "%s paper-sell: mint=%s pnl=%.2f%% trigger=%s held=%.0fs peak=%.2f%%",
        LOG_PREFIX,
        mint,
        result["pnl"],
        result["trigger"],
        result["held"],
        result["peak"],
    )
    return settled


def _entry_quote_price(
    trades: Sequence[tuple[float, float, float]],
    entry_ts_epoch: float,
    gap_s: int = 30,
) -> float:
    """Reconstruct the settler's entry "quote" price (last swap in [entry-gap, entry]).

    Mirrors tape_settler's ``pre = [p for t,p,_ in trades if entry-_GAP <= t <= entry]``
    then ``quote = pre[-1]``.  Falls back to the first trade's price if no
    pre-entry swap exists (the settler would have rejected as 'dead', but this
    keeps the helper total).
    """
    pre = [p for (t, p, _u) in trades if entry_ts_epoch - gap_s <= t <= entry_ts_epoch]
    if pre:
        return float(pre[-1])
    return float(trades[0][1]) if trades else 0.0


def _open_paper_position(
    *,
    mint: str,
    score: float,
    entry_ts: datetime,
    entry_price: float,
    size_sol: float,
    position_factory: Optional[Callable[..., Any]],
):
    """Build + save a PAPER trading.Position row (source='model', mode='observe').

    Uses the injected *position_factory* when provided (tests), else the real
    trading.models.Position.  The row is OBSERVE/PAPER: status='PAPER',
    mode='observe'.
    """
    if position_factory is not None:
        return position_factory(
            mint=mint,
            score=score,
            entry_ts=entry_ts,
            entry_price=entry_price,
            size_sol=size_sol,
        )

    from trading.models import Position  # noqa: PLC0415 - lazy: ORM only at runtime

    position = Position(
        mint=mint,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_PAPER,
        score=score,
        entry_ts=entry_ts,
        entry_price=entry_price,
        size_sol=size_sol,
    )
    position.save()
    return position


# ---------------------------------------------------------------------------
# Score-time scheduling helper (pure)
# ---------------------------------------------------------------------------


def score_time_reached(
    graduated_at: datetime,
    score_at_elapsed_s: int,
    now: datetime,
) -> bool:
    """Return True when *now* >= graduated_at + score_at_elapsed_s.

    Pure -- mirrors ScoreTimeOrchestrator's score-time gate (§7) without any
    DataSource/snapshot dependency, for the pre-grad-feature scoring path.
    """
    return now >= graduated_at + timedelta(seconds=score_at_elapsed_s)


__all__ = [
    "PRE_FEATURE_NAMES",
    "LOG_PREFIX",
    "RealCapitalGuardError",
    "assert_paper_only",
    "to_pregrad_swaps",
    "assemble_pregrad_features",
    "score_pregrad",
    "gate_passes",
    "settle_paper_trade",
    "score_time_reached",
]
