# ---
# module: copytrade.engine_runtime
# sprint: cutover (copy-trade live), US-75
# story: copytrade-runtime, US-75 AC-1
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: copytrade.buy_trigger, copytrade.exits, copytrade.honest_fill,
#   copytrade.models, copytrade.multiplicity, copytrade.position_manager,
#   copytrade.position_opener, copytrade.schemas, copytrade.wallet_consumer,
#   core.clock
# ---
"""Pure, injected copy-trade engine runtime (observe/paper only).

This module is the testable orchestration core — it has:
  - NO live I/O (DataSource is injected by the command)
  - NO datetime.now() / time.time() (Clock is injected)
  - NO open_live_position call (OBSERVE/PAPER ONLY — the command asserts mode)
  - NO import of BirdeyeSwapSource, HeliusBirthTapeSource, TapeRecorder,
    LakeWriter, SwapWriter, PipelineConfig, or PipelineState (§5 isolation)

The two public functions are:

  ``handle_event(event, ...)``
      Processes one WalletTxEvent: routes sells as mirror signals, tests buys
      against the trigger predicate and multiplicity controls, and opens an
      observe position via open_observe_position_v2.

  ``manage_positions(...)``
      Evaluates exit conditions for all open positions, closes fired ones via
      close_position, and returns the list of positions closed this tick.

EngineState holds all in-memory runtime state for one cohort session.  It is
NOT thread-safe (the asyncio engine loop is single-threaded by construction).

Logging: greppable ``[copytrade]`` prefix on every actionable log line so
operators can ``grep '[copytrade]'`` in the container log stream.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from copytrade.buy_trigger import should_copy_buy_v2
from copytrade.curve_price import (
    LAMPORTS_PER_SOL,
    CurveState,
    simulate_buy,
    simulate_sell,
)
from copytrade.exits import (
    EXIT_TIMER,
    evaluate_mirror_exit,
    evaluate_trailing_exit,
)
from copytrade.honest_fill import check_copy_entry
from copytrade.models import CopytradePosition
from copytrade.multiplicity import MultiplicityState, apply_multiplicity_controls
from copytrade.position_manager import close_position
from copytrade.position_opener import OpenedPositionRecordV2, open_observe_position_v2
from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit
from copytrade.wallet_consumer import WalletTxEvent
from core.clock import Clock

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# EngineState — in-memory cohort-session state
# ---------------------------------------------------------------------------


@dataclass
class EngineState:
    """All in-memory state for one active cohort session.

    One instance is created at engine startup and passed to every
    ``handle_event`` / ``manage_positions`` call.  NOT thread-safe
    (single-threaded asyncio loop by construction).

    Attributes
    ----------
    multiplicity:
        MultiplicityState tracking copy_first_buy_only / dedupe / cap across
        the full session.

    open_positions:
        mint -> CopytradePosition map for positions currently open.  Seeded
        from the DB at startup (existing open rows) and maintained in-memory
        thereafter.

    wallet_to_strategy:
        wallet_address -> strategy_id mapping (from CohortV2.wallet_to_strategy()).
        Determines which head's exit config applies when a wallet triggers.

    exit_by_strategy:
        strategy_id -> exit config (OurTrailingExit | MirrorWalletSellExit).
        Populated from the enabled strategies at startup.

    sold_signals:
        Map of mint -> the source wallet's SELL price (the curve price from its
        sell TradeEvent).  A mint present here means the owning source wallet has
        sold; MirrorWalletSellExit fires the mirror close AND books it at this
        price (so the scalp exit does NOT depend on an external price feed having
        a price for a fresh pre-grad token — the Birdeye REST source returns None
        for not-yet-indexed bonding-curve mints).
    """

    multiplicity: MultiplicityState
    open_positions: dict  # mint -> CopytradePosition
    wallet_to_strategy: dict  # address -> strategy_id
    exit_by_strategy: dict  # strategy_id -> exit cfg (OurTrailingExit | MirrorWalletSellExit)
    sold_signals: dict  # mint -> source-wallet sell price (curve price)

    @classmethod
    def new(
        cls,
        wallet_to_strategy: dict,
        exit_by_strategy: dict,
        open_positions: Optional[dict] = None,
    ) -> "EngineState":
        """Create a fresh EngineState for a new cohort session.

        Parameters
        ----------
        wallet_to_strategy:
            Mapping from wallet address to strategy id (from CohortV2).
        exit_by_strategy:
            Mapping from strategy_id to the head's exit config.
        open_positions:
            Optionally pre-seed from open DB rows (for crash recovery).
            Defaults to an empty dict (fresh start).
        """
        state = cls(
            multiplicity=MultiplicityState.new(),
            open_positions=open_positions if open_positions is not None else {},
            wallet_to_strategy=dict(wallet_to_strategy),
            exit_by_strategy=dict(exit_by_strategy),
            sold_signals={},
        )
        # Seed multiplicity from any pre-existing open positions
        for mint, position in state.open_positions.items():
            state.multiplicity.open_mints.add(mint)
            state.multiplicity.open_positions_count += 1
        return state


# ---------------------------------------------------------------------------
# Multiplicity-compatible shim
# ---------------------------------------------------------------------------

class _MultiplicityConfigShim:
    """Thin shim that exposes multiplicity config fields from CopyTradeSettings.

    apply_multiplicity_controls reads these three flags off the config object;
    we pull them from the CopyTradeSettings model row (or any object that has
    these attributes) and expose them with the same names.
    """

    def __init__(
        self,
        copy_first_buy_only: bool,
        dedupe_token_across_wallets: bool,
        max_concurrent_positions: int,
    ) -> None:
        self.copy_first_buy_only = copy_first_buy_only
        self.dedupe_token_across_wallets = dedupe_token_across_wallets
        self.max_concurrent_positions = max_concurrent_positions


# ---------------------------------------------------------------------------
# handle_event — per-event orchestration
# ---------------------------------------------------------------------------


def handle_event(
    event: WalletTxEvent,
    *,
    settings: Any,
    state: EngineState,
    cohort: Any,  # CohortV2 instance
    sol_usd: float,
    curve_state_fn: Callable[[str], Optional[CurveState]],
    clock: Clock,
    honest_fills_enabled: bool = False,
) -> Optional[CopytradePosition]:
    """Process one WalletTxEvent and return an opened position or None.

    SELL path
    ---------
    If the event is a sell from a watched wallet and we hold the mint, record
    the sell signal so the mirror-exit evaluator fires on the next manage_positions
    tick.  Returns None (we do not close synchronously here — close on the
    periodic manager tick so high_water is persisted consistently).

    BUY path
    --------
    1. Trigger predicate: should_copy_buy_v2 — pump.fun token, >= min_trigger_buy_usd.
    2. Multiplicity controls: copy_first_buy_only / dedupe / max_concurrent cap.
    3. If action == "open":
       a. Resolve strategy_id from state.wallet_to_strategy.
       b. Read the bonding-curve reserves via curve_state_fn and simulate OUR
          buy (curve fill); skip if the curve is unreadable/graduated.
       c. Open a paper position via open_observe_position_v2.
       d. Register in state.open_positions; update multiplicity open count.
    4. Return the opened CopytradePosition, or None for any skip/dedupe.

    Safety
    ------
    This function NEVER calls open_live_position.  Mode safety is asserted by
    the command (composition root) before the event loop starts — this module
    simply never references the live path.

    Parameters
    ----------
    event:
        The WalletTxEvent from the WalletSubscriptionConsumer.
    settings:
        CopyTradeSettings row (or compatible object) exposing
        min_trigger_buy_usd, usd_size_per_trade, copy_first_buy_only,
        dedupe_token_across_wallets, max_concurrent_positions, active_cohort_id.
    state:
        The current EngineState (mutated in-place on open).
    cohort:
        Validated CohortV2 instance for the active cohort.
    sol_usd:
        Current SOL/USD spot (injected — no internal fetch).
    curve_state_fn:
        Callable ``(mint: str) -> CurveState | None`` reading the bonding-curve
        reserves over RPC.  None on a read miss / graduated curve -> skip the open.
        Our entry fill is simulated against these reserves (curve basis, one basis
        end-to-end), NOT the wallet's quoted price.
    clock:
        Injected Clock — used to stamp entry_ts on the new position.
    honest_fills_enabled:
        US-75 feature flag.  Slippage telemetry (quote vs our curve fill) is ALWAYS
        recorded; when this is True the 15% Anchor-6002 cap also REJECTS the entry
        (ENTRY_REJECTED, PnL NULL, excluded from win-rate).  quote = event.raw["price"]
        (the wallet's curve buy price), our fill = simulate_buy result.
        Default False — the running soak is unaffected until deliberately flipped.

    Returns
    -------
    CopytradePosition if a position was opened, else None.
    Note: an ENTRY_REJECTED position (honest_fills_enabled=True, fill > cap)
    also returns None — no open position is tracked.
    """
    cohort_id: str = getattr(settings, "active_cohort_id", "") or ""

    # ------------------------------------------------------------------
    # SELL path — track mirror signals
    # ------------------------------------------------------------------
    if event.tx_type == "sell":
        if event.wallet in state.wallet_to_strategy and event.mint in state.open_positions:
            # Record the source wallet's sell PRICE (curve price from its sell
            # TradeEvent) so the mirror exit can book at it without depending on an
            # external feed having a price for this (often pre-grad) mint.
            sell_price = 0.0
            raw_price = event.raw.get("price")
            if raw_price is not None:
                try:
                    sell_price = float(raw_price)
                except (TypeError, ValueError):
                    sell_price = 0.0
            state.sold_signals[event.mint] = sell_price
            logger.info(
                "[copytrade] sell-signal: wallet=%.8s mint=%.8s price=%.8g (mirror trigger queued)",
                event.wallet,
                event.mint,
                sell_price,
            )
        return None

    # ------------------------------------------------------------------
    # BUY path — only process "buy" events
    # ------------------------------------------------------------------
    if event.tx_type != "buy":
        return None

    # 1. Trigger predicate (pump.fun token + >= min_trigger_buy_usd)
    min_usd = float(getattr(settings, "min_trigger_buy_usd", 250.0))
    if not should_copy_buy_v2(event, min_trigger_buy_usd=min_usd, sol_usd=sol_usd):
        return None

    # 2. Multiplicity controls
    mult_cfg = _MultiplicityConfigShim(
        copy_first_buy_only=bool(getattr(settings, "copy_first_buy_only", True)),
        dedupe_token_across_wallets=bool(getattr(settings, "dedupe_token_across_wallets", True)),
        max_concurrent_positions=int(getattr(settings, "max_concurrent_positions", 30)),
    )
    decision = apply_multiplicity_controls(event, state.multiplicity, mult_cfg)
    if decision.action != "open":
        logger.debug(
            "[copytrade] skip: wallet=%.8s mint=%.8s reason=%s",
            event.wallet,
            event.mint,
            decision.reason,
        )
        return None

    # 3a. Resolve strategy_id
    strategy_id: str = state.wallet_to_strategy.get(event.wallet, "")

    usd_size = float(getattr(settings, "usd_size_per_trade", 25.0))
    entry_ts = clock.now()

    # 3b. CURVE honest fill (PR B2) — price ONE basis: the bonding curve.
    #   quote = the watched wallet's curve buy price (vsol/vtok from its TradeEvent).
    #   fill  = OUR realized buy, simulated against the CURRENT curve reserves (read
    #           over RPC = the "next tick" after our detection) — constant-product +
    #           1% fee + our size impact INHERENT. This is the honest fill the live
    #           bot would receive; we NEVER book the wallet's price (a phantom fill).
    curve_state: Optional[CurveState] = None
    try:
        curve_state = curve_state_fn(event.mint)
    except Exception:  # noqa: BLE001 — a read miss must never break the engine
        curve_state = None

    if curve_state is None or curve_state.complete:
        # Pre-grad bonding curve unavailable (not found / already graduated — the
        # post-grad AMM path is Phase 2). Can't price our fill on one basis -> skip.
        logger.warning(
            "[copytrade] curve-unavailable: wallet=%.8s mint=%.8s — skipping open "
            "(graduated or unreadable curve)",
            event.wallet,
            event.mint,
        )
        state.multiplicity.open_positions_count -= 1
        state.multiplicity.open_mints.discard(event.mint)
        return None

    sol_in = usd_size / sol_usd if sol_usd > 0 else 0.0
    size_lamports = int(sol_in * LAMPORTS_PER_SOL)
    fill = simulate_buy(curve_state, size_lamports)
    if fill is None or fill.tokens <= 0:
        logger.warning(
            "[copytrade] curve-fill-failed: wallet=%.8s mint=%.8s size_lamports=%d "
            "— skipping open",
            event.wallet,
            event.mint,
            size_lamports,
        )
        state.multiplicity.open_positions_count -= 1
        state.multiplicity.open_mints.discard(event.mint)
        return None

    entry_price = fill.price
    entry_tokens = fill.tokens

    # 3c. Honest-fill slippage telemetry (US-75). quote = wallet's curve buy price;
    #   fill = our simulated curve fill. Telemetry is ALWAYS recorded so we can
    #   measure copy-latency + execution slippage live; the 15% Anchor-6002
    #   REJECTION is gated by honest_fills_enabled (the running soak is unaffected
    #   until deliberately flipped).
    quote_price: Optional[float] = None
    raw_quote = event.raw.get("price")
    if raw_quote is not None:
        try:
            quote_price = float(raw_quote)
        except (TypeError, ValueError):
            quote_price = None

    fill_telemetry: dict = {}
    if quote_price is not None and quote_price > 0:
        fill_check = check_copy_entry(
            quote_price=quote_price,
            fill_price=entry_price,
            clock_arrival_ts=entry_ts,
            block_time=event.block_time,
        )
        fill_telemetry = {
            "quote_price": fill_check.quote_price,
            "fill_price": fill_check.fill_price,
            "cap_pct": fill_check.cap_pct,
            "copy_latency_s": fill_check.copy_latency_s,
        }

        if honest_fills_enabled and not fill_check.enterable:
            # Write an ENTRY_REJECTED row (PnL NULL — excluded from win-rate;
            # no live position tracked; multiplicity undone).
            record = OpenedPositionRecordV2(
                cohort_id=cohort_id,
                mint=event.mint,
                trigger_wallet=event.wallet,
                entry_ts=entry_ts,
            )
            _write_rejected_entry(
                record,
                fill_telemetry=fill_telemetry,
                reason=fill_check.reason,
                usd_size=usd_size,
                sol_usd=sol_usd,
                strategy_id=strategy_id,
            )
            logger.info(
                "[copytrade] ENTRY_REJECTED: head=%s wallet=%.8s mint=%.8s "
                "quote=%.8g fill=%.8g slip=%.4f reason=%s",
                strategy_id,
                event.wallet,
                event.mint,
                quote_price,
                entry_price,
                fill_check.realized_slip_pct if fill_check.realized_slip_pct is not None else float("nan"),
                fill_check.reason,
            )
            state.multiplicity.open_positions_count -= 1
            state.multiplicity.open_mints.discard(event.mint)
            return None  # no live position

    # 3d. Open the paper position at OUR curve fill (curve basis end-to-end).
    record = OpenedPositionRecordV2(
        cohort_id=cohort_id,
        mint=event.mint,
        trigger_wallet=event.wallet,
        entry_ts=entry_ts,
    )

    position = open_observe_position_v2(
        record,
        entry_price,
        usd_size=usd_size,
        sol_usd=sol_usd,
        strategy_id=strategy_id,
        entry_tokens=entry_tokens,
    )

    # Persist honest-fill telemetry onto the position row (whenever we had a quote).
    if fill_telemetry:
        CopytradePosition.objects.filter(pk=position.pk).update(**fill_telemetry)
        for k, v in fill_telemetry.items():
            setattr(position, k, v)

    # 3e. Register in state
    state.open_positions[event.mint] = position

    slip_str = (
        f"{(entry_price / quote_price - 1.0):.4f}"
        if (quote_price is not None and quote_price > 0)
        else "n/a"
    )
    logger.info(
        "[copytrade] copy-buy: head=%s wallet=%.8s mint=%.8s usd=%.2f entry=%.8g "
        "tokens=%d slip=%s",
        strategy_id,
        event.wallet,
        event.mint,
        usd_size,
        entry_price,
        entry_tokens,
        slip_str,
    )
    return position


def _write_rejected_entry(
    record: OpenedPositionRecordV2,
    *,
    fill_telemetry: dict,
    reason: str,
    usd_size: float,
    sol_usd: float,
    strategy_id: str,
) -> CopytradePosition:
    """Write a closed ENTRY_REJECTED position row (PnL NULL, excluded from win-rate).

    US-75 AC-1: mirrors solanaBilly _honest_entry_fill's rejection path
    (paper_monitor_tasks.py:287-296).  The position is immediately closed with
    exit_reason=ENTRY_REJECTED and NULL PnL; it does NOT enter state.open_positions.

    Called only from handle_event when honest_fills_enabled=True and the slippage
    cap is exceeded.  Never calls place_buy_order (OBSERVE isolation preserved).
    """
    from trading.models import Position as SharedPosition  # lazy — copytrade §5 isolation

    sol_in = usd_size / sol_usd if sol_usd > 0 else 0.0

    # Shared trading.Position row (AC-68.1 chassis) — status=PAPER, closed immediately
    shared_pos = SharedPosition(
        mint=record.mint,
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_OBSERVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=record.entry_ts,
        entry_price=fill_telemetry.get("quote_price"),  # book at quote (signal price)
        size_sol=sol_in,
    )
    shared_pos.save()

    # CopytradePosition row — immediately closed, PnL fields left NULL
    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=record.entry_ts,
        entry_price=fill_telemetry.get("quote_price"),  # signal-time quote
        sol_in=sol_in,
        size_usd=usd_size,
        strategy_id=strategy_id,
        exit_ts=record.entry_ts,  # immediate — no hold
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
        # PnL left NULL — excluded from win-rate (the honest-fill discipline)
        realized_pnl_sol=None,
        realized_pnl_pct=None,
        shared_position_id=shared_pos.pk,
        # Honest-fill telemetry
        quote_price=fill_telemetry.get("quote_price"),
        fill_price=fill_telemetry.get("fill_price"),
        cap_pct=fill_telemetry.get("cap_pct"),
        copy_latency_s=fill_telemetry.get("copy_latency_s"),
    )
    position.save()
    return position


# ---------------------------------------------------------------------------
# manage_positions — periodic exit tick
# ---------------------------------------------------------------------------


def manage_positions(
    *,
    state: EngineState,
    curve_state_fn: Callable[[str], Optional[CurveState]],
    clock: Clock,
    now: datetime,
) -> list:
    """Evaluate exit conditions for all open positions and close fired ones.

    Called periodically (every ~15 s) by the command's manager task.  Prices on
    ONE basis — the bonding curve (``curve_state_fn`` reads the reserves over RPC):

      1. Read the curve state.  If unreadable/graduated (None or ``complete``):
         - a pending MIRROR fires at the source wallet's curve SELL price
           (``state.sold_signals`` — same curve basis); else
         - past ``max_hold_seconds``, a stale-timer force-closes at entry (flat) so
           a dead/graduated token is not held forever.
      2. ``current_price = state.spot_price()`` (vsol/vtok). Ratchet high_water.
      3. Evaluate the head's exit (OurTrailingExit / MirrorWalletSellExit).
      4. On close, simulate OUR size-impacted curve SELL of ``entry_tokens``
         (``simulate_sell``) for the realized exit price — the honest round-trip.
         Falls back to the spot/mirror price when the curve is gone or the
         position predates ``entry_tokens``.

    Parameters
    ----------
    state:
        Current EngineState (mutated in-place for closes).
    curve_state_fn:
        Callable ``(mint: str) -> CurveState | None`` reading the bonding-curve
        reserves (None on miss / graduated curve).
    clock:
        Injected Clock — NOT used for ``now`` (caller passes ``now`` explicitly
        so both the consumer loop and the test harness control the timestamp).
    now:
        Current timestamp used as ``current_ts`` for all exit evaluations.

    Returns
    -------
    List of CopytradePosition rows that were closed this tick.
    """
    closed: list[CopytradePosition] = []

    for mint in list(state.open_positions.keys()):
        position = state.open_positions[mint]

        # 1. Read the current bonding-curve state (one-basis pricing).
        curve_state: Optional[CurveState] = None
        try:
            curve_state = curve_state_fn(mint)
        except Exception:  # noqa: BLE001 — a read miss must never break the engine
            curve_state = None

        current_price: Optional[float] = (
            curve_state.spot_price()
            if (curve_state is not None and not curve_state.complete)
            else None
        )

        if current_price is None or current_price <= 0:
            # Curve unreadable/graduated. A pending MIRROR can still book at the
            # source wallet's curve SELL price (same basis); otherwise enforce the
            # max-hold so a dead/graduated token is not held forever (the 15h-stuck
            # bug — and how the pre-fix stale opens get cleared).
            sell_price = state.sold_signals.get(mint, 0.0)
            if sell_price and sell_price > 0:
                current_price = sell_price
            else:
                cfg = state.exit_by_strategy.get(position.strategy_id or "")
                max_hold = getattr(cfg, "max_hold_seconds", None) if cfg is not None else None
                if (
                    max_hold
                    and position.entry_ts is not None
                    and (now - position.entry_ts).total_seconds() >= max_hold
                ):
                    fill = float(position.entry_price or 0.0)
                    closed_position = close_position(position, EXIT_TIMER, fill, now)
                    closed.append(closed_position)
                    logger.info(
                        "[copytrade] stale-timer close: mint=%.8s held>=%ds, curve "
                        "unreadable/graduated -> TIMER @ entry (flat).",
                        mint,
                        int(max_hold),
                    )
                    del state.open_positions[mint]
                    state.multiplicity.open_mints.discard(mint)
                    state.multiplicity.open_positions_count = max(
                        0, state.multiplicity.open_positions_count - 1
                    )
                    state.sold_signals.pop(mint, None)
                continue  # priced-out: just force-closed, or still within hold

        # 2. Ratchet high_water_price
        prior_hw = float(position.high_water_price or position.entry_price or current_price)
        new_hw = max(prior_hw, current_price)
        if new_hw != prior_hw:
            position.high_water_price = new_hw
            CopytradePosition.objects.filter(pk=position.pk).update(high_water_price=new_hw)

        # 3. Evaluate exit
        strategy_id = position.strategy_id or ""
        exit_cfg = state.exit_by_strategy.get(strategy_id)

        if exit_cfg is None:
            logger.warning(
                "[copytrade] no-exit-cfg: strategy=%s mint=%.8s — holding",
                strategy_id,
                mint,
            )
            continue

        entry_price = float(position.entry_price or current_price)
        entry_ts = position.entry_ts

        if isinstance(exit_cfg, OurTrailingExit):
            decision = evaluate_trailing_exit(
                exit_cfg,
                entry_price=entry_price,
                current_price=current_price,
                high_water_price=new_hw,
                entry_ts=entry_ts,
                current_ts=now,
            )
            # Persist updated high_water from trailing evaluation (may differ from our ratchet)
            if decision.high_water_price != new_hw:
                position.high_water_price = decision.high_water_price
                CopytradePosition.objects.filter(pk=position.pk).update(
                    high_water_price=decision.high_water_price
                )

        elif isinstance(exit_cfg, MirrorWalletSellExit):
            source_sold = mint in state.sold_signals
            decision = evaluate_mirror_exit(
                exit_cfg,
                source_wallet_sold=source_sold,
                entry_price=entry_price,
                current_price=current_price,
                entry_ts=entry_ts,
                current_ts=now,
            )
        else:
            logger.warning(
                "[copytrade] unknown-exit-type: strategy=%s mint=%.8s type=%s",
                strategy_id,
                mint,
                type(exit_cfg).__name__,
            )
            continue

        # 4. Close if fired — book OUR size-impacted curve SELL when we can.
        if decision.should_close:
            exit_reason = decision.exit_reason
            exit_price = decision.exit_price if decision.exit_price is not None else current_price

            # Simulate our realized curve SELL (size impact + 1% fee) when the curve
            # is live and we know the tokens held; else fall back to the spot/mirror
            # price (no size impact — pre-B2 positions without entry_tokens, or a
            # graduated curve booked at the wallet's sell).
            if (
                curve_state is not None
                and not curve_state.complete
                and position.entry_tokens
                and position.entry_tokens > 0
            ):
                sell_fill = simulate_sell(curve_state, int(position.entry_tokens))
                if sell_fill is not None:
                    exit_price = sell_fill.price

            closed_position = close_position(position, exit_reason, exit_price, now)
            closed.append(closed_position)

            # Compute PnL% for logging
            try:
                pnl_pct = float(closed_position.realized_pnl_pct or 0.0)
            except (TypeError, ValueError):
                pnl_pct = 0.0

            logger.info(
                "[copytrade] copy-sell: head=%s mint=%.8s reason=%s pnl=%.1f%%",
                strategy_id,
                mint,
                exit_reason,
                pnl_pct,
            )

            # Clean up state
            del state.open_positions[mint]
            state.multiplicity.open_mints.discard(mint)
            state.multiplicity.open_positions_count = max(
                0, state.multiplicity.open_positions_count - 1
            )
            state.sold_signals.pop(mint, None)

    return closed


__all__ = ["EngineState", "handle_event", "manage_positions"]
