# ---
# module: copytrade.multiplicity
# sprint: sprint-12
# story: US-60 AC-60.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.schemas, copytrade.wallet_consumer
# ---
"""AC-60.2 multiplicity controls (SPEC §3 #4/#5/#6).

Pure-function module (no I/O, no ORM) implementing the three config-driven
multiplicity controls that sit ABOVE the AC-60.1 predicate:

  #4 copy_first_buy_only        — only the wallet's FIRST buy of a token in
                                   this cohort session triggers; later adds by
                                   the same wallet on the same token are ignored.

  #5 dedupe_token_across_wallets — if we already hold/are opening this token,
                                   do NOT open a second position; RECORD the
                                   additional triggering wallet and attribute the
                                   position to the FIRST triggering wallet (PnL).

  #6 max_concurrent_positions   — ignore new triggers beyond the cap.

Controls are applied in the order #4 → #5 → #6.  The caller creates a
MultiplicityState via MultiplicityState.new() at the start of each cohort
session, passes it to every apply_multiplicity_controls() call, and inspects
the returned TriggerDecision to decide whether to open a position.

Design notes:
  - MultiplicityState is a plain mutable dataclass; it is NOT thread-safe.
    The caller (the copytrade_engine asyncio loop) is single-threaded by
    construction, so no locking is needed.
  - additional_trigger_wallets is a mint → list[wallet] map; it is populated
    whenever action == "record_attribution" and is available to the PnL
    attribution layer in US-61.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from copytrade.schemas import CopyTradeConfig
from copytrade.wallet_consumer import WalletTxEvent

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

ACTION = Literal["open", "skip_first_buy", "skip_cap", "record_attribution"]


# ---------------------------------------------------------------------------
# TriggerDecision — immutable outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerDecision:
    """Outcome of applying multiplicity controls to a buy event.

    Attributes
    ----------
    action:
        "open"               — all controls passed; caller should open a new position.
        "skip_first_buy"     — control #4 fired; same wallet already bought this token
                               this session; caller should skip.
        "record_attribution" — control #5 fired; token already has an open position;
                               caller should NOT open another position but the additional
                               wallet is recorded in state.additional_trigger_wallets.
        "skip_cap"           — control #6 fired; max_concurrent_positions reached;
                               caller should skip.
    reason:
        Human-readable description of why the decision was made; useful for logging
        and test assertions.
    """

    action: ACTION
    reason: str


# ---------------------------------------------------------------------------
# MultiplicityState — mutable in-session state
# ---------------------------------------------------------------------------


@dataclass
class MultiplicityState:
    """Mutable in-session state for multiplicity controls.

    One instance per active cohort session.  The caller creates it via
    MultiplicityState.new() and passes it to every apply_multiplicity_controls()
    call.  The state is mutated in-place to track what has been seen.

    Attributes
    ----------
    wallet_token_seen:
        Set of (wallet, mint) tuples tracking which wallet/token pairs have
        already triggered this session.  Used by control #4 (copy_first_buy_only).
    open_mints:
        Set of mints that currently have an open (or opening) position.
        Used by control #5 (dedupe_token_across_wallets).
    open_positions_count:
        Count of currently open positions.  Used by control #6
        (max_concurrent_positions).
    additional_trigger_wallets:
        mint → list[wallet] mapping recording all wallets that would have
        triggered a position on a token that was already open.  Used for PnL
        attribution (the position is attributed to the FIRST triggering wallet).
    """

    wallet_token_seen: set  # set[(wallet, mint)]
    open_mints: set  # set[mint]
    open_positions_count: int
    additional_trigger_wallets: dict  # mint -> list[wallet]

    @classmethod
    def new(cls) -> "MultiplicityState":
        """Create a fresh MultiplicityState for a new cohort session."""
        return cls(
            wallet_token_seen=set(),
            open_mints=set(),
            open_positions_count=0,
            additional_trigger_wallets={},
        )


# ---------------------------------------------------------------------------
# apply_multiplicity_controls — the core function
# ---------------------------------------------------------------------------


def apply_multiplicity_controls(
    event: WalletTxEvent,
    state: MultiplicityState,
    config: CopyTradeConfig,
) -> TriggerDecision:
    """Apply SPEC §3 #4/#5/#6 multiplicity controls in order.

    Controls are evaluated in the fixed order:
      #4 copy_first_buy_only → #5 dedupe_token_across_wallets → #6 max_concurrent_positions

    State is mutated in-place when applicable:
      - On #4 (first buy): the (wallet, mint) pair is recorded in wallet_token_seen.
      - On #5 (dedupe, attribution): the wallet is appended to
        additional_trigger_wallets[mint].
      - On "open": the mint is added to open_mints; open_positions_count is incremented.

    Parameters
    ----------
    event:
        The WalletTxEvent to evaluate.  The caller is responsible for ensuring
        this event has already passed the AC-60.1 should_copy_buy() predicate.
    state:
        The current session's MultiplicityState; mutated in-place.
    config:
        The active CopyTradeConfig.  Controls are skipped when their flag is False.

    Returns
    -------
    TriggerDecision with action "open", "skip_first_buy", "skip_cap", or
    "record_attribution".
    """
    # ------------------------------------------------------------------
    # #4: copy_first_buy_only
    # Skip if this wallet has already bought this token this session.
    # We ALWAYS record the (wallet, mint) pair in wallet_token_seen when
    # the flag is enabled — this means even the first buy is recorded so
    # subsequent buys by the same wallet are caught.
    # ------------------------------------------------------------------
    if config.copy_first_buy_only:
        key = (event.wallet, event.mint)
        if key in state.wallet_token_seen:
            return TriggerDecision(
                action="skip_first_buy",
                reason=(
                    f"wallet {event.wallet[:8]}… already bought {event.mint[:8]}…"
                    " this session"
                ),
            )
        # Record this (wallet, mint) pair so future buys by the same wallet are skipped.
        state.wallet_token_seen.add(key)

    # ------------------------------------------------------------------
    # #5: dedupe_token_across_wallets
    # If the token already has an open position, record the additional
    # triggering wallet for PnL attribution and do NOT open another position.
    # ------------------------------------------------------------------
    if config.dedupe_token_across_wallets and event.mint in state.open_mints:
        state.additional_trigger_wallets.setdefault(event.mint, []).append(event.wallet)
        return TriggerDecision(
            action="record_attribution",
            reason=(
                f"token {event.mint[:8]}… already has an open position;"
                f" recorded {event.wallet[:8]}… as additional trigger"
            ),
        )

    # ------------------------------------------------------------------
    # #6: max_concurrent_positions
    # Skip if we are at or above the cap.
    # ------------------------------------------------------------------
    if state.open_positions_count >= config.max_concurrent_positions:
        return TriggerDecision(
            action="skip_cap",
            reason=(
                f"max_concurrent_positions ({config.max_concurrent_positions}) reached"
            ),
        )

    # ------------------------------------------------------------------
    # All controls passed — caller should open a new position.
    # Update state accordingly.
    # ------------------------------------------------------------------
    state.open_mints.add(event.mint)
    state.open_positions_count += 1
    return TriggerDecision(
        action="open",
        reason="all multiplicity controls passed",
    )
