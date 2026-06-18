# ---
# module: copytrade.trigger_pipeline
# sprint: sprint-12
# story: US-60 AC-60.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.buy_trigger, copytrade.multiplicity, copytrade.wallet_consumer, core.datasource, core.clock
# ---
"""AC-60.3: End-to-end copy-BUY trigger pipeline behind the DataSource seam.

Wires the full trigger path in order:

  1. WalletSubscriptionConsumer (AC-59.2) — normalises raw DataSource events
     to WalletTxEvents using an injected Clock (no datetime.now() / time.time()).
  2. should_copy_buy (AC-60.1) — rejects sells, transfers, non-pump.fun tokens,
     and post-graduation buys.  mirror_wallet_sells stays False by construction:
     the predicate only passes tx_type == "buy" events.
  3. apply_multiplicity_controls (AC-60.2) — enforces copy_first_buy_only,
     dedupe_token_across_wallets, and max_concurrent_positions.
  4. Records an OpenedPositionRecord for every event that reaches action == "open".

The pipeline is DETERMINISTIC: given the same DataSource event sequence and the
same injected Clock state, two runs produce byte-identical TriggerResult lists
and byte-identical OpenedPositionRecord sets (AC-60.3 run-twice-identical gate).

Zero I/O, zero ORM, zero firehose by construction.  The caller injects a
concrete DataSource (e.g. ReplaySource for offline testing, or a Helius source
in production); this module never imports a concrete source implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from copytrade.buy_trigger import should_copy_buy
from copytrade.multiplicity import (
    MultiplicityState,
    TriggerDecision,
    apply_multiplicity_controls,
)
from copytrade.schemas import CopyTradeConfig
from copytrade.wallet_consumer import WalletSubscriptionConsumer, WalletTxEvent
from core.clock import Clock
from core.datasource import DataSource


@dataclass(frozen=True)
class OpenedPositionRecord:
    """In-memory record of a position that would be written to copytrade_positions.

    Produced by the trigger pipeline when decision.action == "open".  The price
    field (entry_price) is intentionally absent — price booking is US-61's
    responsibility (observe-mode fill at the live price).

    All fields are derived deterministically from the event stream and the
    injected Clock: same stream + same clock state → same records (AC-60.3
    run-twice-identical gate).

    Attributes
    ----------
    cohort_id:       Active cohort identifier (injected by caller).
    mint:            Token mint address that triggered the position.
    trigger_wallet:  The watched wallet whose buy signal triggered this position.
    entry_ts:        Timestamp from the injected Clock at the moment of the trigger.
    """

    cohort_id: str
    mint: str
    trigger_wallet: str
    entry_ts: datetime


@dataclass(frozen=True)
class TriggerResult:
    """Result of running the full trigger pipeline on one WalletTxEvent.

    Attributes
    ----------
    event:
        The normalised WalletTxEvent yielded by the WalletSubscriptionConsumer.
    predicate_passed:
        True iff should_copy_buy(event) returned True.  False means the event
        was rejected before multiplicity controls were applied (e.g. it was a
        sell, a non-pump.fun token buy, or a post-graduation buy).
    decision:
        The TriggerDecision from apply_multiplicity_controls, or None when
        predicate_passed is False (multiplicity is never evaluated on rejected
        events).
    opened_position:
        Populated with an OpenedPositionRecord iff decision.action == "open".
        None in all other cases (sell/non-pumpfun/graduated/multiplicity-skipped).
    """

    event: WalletTxEvent
    predicate_passed: bool
    decision: TriggerDecision | None
    opened_position: OpenedPositionRecord | None


async def run_trigger_pipeline(
    cohort_id: str,
    source: DataSource,
    clock: Clock,
    wallet_addresses: list[str],
    channel_name: str,
    config: CopyTradeConfig,
) -> list[TriggerResult]:
    """Run the full AC-60.1/60.2 trigger path over one DataSource event stream.

    This is the AC-60.3 offline gate: a caller wires a ReplaySource (or any
    DataSource) + VirtualClock and calls this function twice with the same
    arguments to prove determinism.

    Steps per emitted WalletTxEvent:
      1. should_copy_buy(event)          — rejects non-buy / non-pumpfun / graduated
      2. apply_multiplicity_controls()   — first-buy-only, dedupe, max-cap
      3. Record OpenedPositionRecord     — only when action == "open"

    Returns a list of TriggerResult, one per WalletTxEvent emitted by the
    consumer (filtered events from un-watched wallets or empty-mint events are
    never yielded and therefore never appear in the results).

    Parameters
    ----------
    cohort_id:       Active cohort ID — stamped into every OpenedPositionRecord.
    source:          Injected DataSource (ReplaySource for offline; Helius for live).
    clock:           Injected Clock (VirtualClock for offline; WallClock for live).
    wallet_addresses: Watched wallet addresses from the active cohort.
    channel_name:    Config-driven channel/subscription name (Principle #1).
    config:          Active CopyTradeConfig.  mirror_wallet_sells MUST be False
                     (enforced at schema construction time by CopyTradeConfig).
    """
    consumer = WalletSubscriptionConsumer(
        source=source,
        clock=clock,
        wallet_addresses=wallet_addresses,
        channel_name=channel_name,
    )
    state = MultiplicityState.new()
    results: list[TriggerResult] = []

    async for event in consumer.run():
        passed = should_copy_buy(event)
        if not passed:
            results.append(TriggerResult(
                event=event,
                predicate_passed=False,
                decision=None,
                opened_position=None,
            ))
            continue

        decision = apply_multiplicity_controls(event, state, config)
        opened: OpenedPositionRecord | None = None
        if decision.action == "open":
            opened = OpenedPositionRecord(
                cohort_id=cohort_id,
                mint=event.mint,
                trigger_wallet=event.wallet,
                entry_ts=event.timestamp,
            )
        results.append(TriggerResult(
            event=event,
            predicate_passed=True,
            decision=decision,
            opened_position=opened,
        ))

    return results
