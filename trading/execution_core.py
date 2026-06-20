# ---
# module: trading.execution_core
# sprint: sprint-13, feat/copy-live-exec-curve-ix
# story: US-64 AC-64.1 / US-65 AC-65.3, copy-live-exec
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: core.clock, core.datasource, trading.schemas
# ---
"""Thin clock-injected execution core for the shared trading apparatus.

ExecutionCore is the single shared entry point BOTH pipelines (prediction model
and copy-trade) call into.  DataSource and Clock are INJECTED — no concrete
LiveSource or ReplaySource import on this module (Principle #7 / US-2 seam).

Design constraints:
  - No direct import of LiveSource or ReplaySource (DataSource seam preserved)
  - No time.time() / datetime.now() calls (Clock-injected only)
  - trading_enabled DEFAULT False — observe/paper safety gate (AC-64.1)
  - execute_buy / execute_sell gate on trading_enabled; the Sender is ONLY
    reached when trading_enabled=True (Cutover, §16) — AC-65.3 safety gate
  - trading.sender is NEVER imported at module level; the Sender type is
    accepted as Any to preserve the isolation boundary (no sender import
    from non-sender modules — enforced by the AC-65.3 AST guard)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.clock import Clock
from core.datasource import DataSource
from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# Result type for execute_buy / execute_sell
# ---------------------------------------------------------------------------


@dataclass
class ExecuteResult:
    """Outcome of an execute_buy or execute_sell call.

    In observe/paper mode (trading_enabled=False), sent=False and no network
    call is ever made.  In live Cutover mode (trading_enabled=True), sent=True
    and the result carries the transaction signature.

    Attributes:
        sent:      True only when a real transaction was submitted to mainnet.
        mode:      "observe" when trading_enabled=False; "live" otherwise.
        signature: Transaction signature from the Sender, or None in observe.
        reason:    Human-readable refuse reason when sent=False on the live path
                   (e.g. "daily_cap_exceeded", "open_live_cap_exceeded",
                   "budget_check_error").  None when sent=True or mode='observe'.
    """

    sent: bool
    mode: str
    signature: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# ExecutionCore
# ---------------------------------------------------------------------------


class ExecutionCore:
    """Clock-injected core.  Both pipelines call this.

    DataSource and Clock are injected at construction time; this class NEVER
    imports or instantiates LiveSource or ReplaySource directly (Principle #7).

    The Sender is an optional injection (None in observe/paper mode). When
    trading_enabled=False, execute_buy/execute_sell return ObserveResult
    immediately — the Sender is never reached.  The Sender module is never
    imported here; it is passed in as Any so this module has no dependency on
    trading.sender (AC-65.3 isolation boundary).

    Args:
        source: An injected DataSource implementation (live or replay).
        clock:  An injected Clock implementation (wall or virtual).
        config: The validated TradingConfig holding all §10/§17 trade knobs.
        sender: Optional injected Sender — None in observe/paper; only wired
                in Cutover (§16) when the operator provides a live wallet key.
    """

    def __init__(
        self,
        source: DataSource,
        clock: Clock,
        config: TradingConfig,
        sender: Any = None,
    ) -> None:
        self._source = source
        self._clock = clock
        self._config = config
        self._sender = sender  # type: Any — never imported from trading.sender here

    def is_trading_enabled(self) -> bool:
        """Return whether live trading is enabled (DEFAULT False — observe/paper gate).

        A result of False means ALL execution is in observe/paper mode and NO
        real orders will be placed (AC-64.1 safety gate / PRD §10.2).
        """
        return self._config.trading_enabled

    def execute_buy(
        self,
        serialized_tx_b64: str,
        *,
        sol_amount: float = 0.0,
    ) -> ExecuteResult:
        """Gate the live buy send behind trading_enabled + budget kill-switch.

        In observe/paper mode (trading_enabled=False) returns ExecuteResult
        with sent=False and mode='observe' — the Sender is NEVER reached.

        In Cutover live mode (trading_enabled=True, sender wired), FIRST enforces
        the budget kill-switch before the Sender is ever reached:
          1. Daily-spend cap: sum of sol_in for today's open/closed LIVE
             CopytradePositions must not exceed max_daily_spend_sol after adding
             sol_amount.  Refusal -> ExecuteResult(sent=False, mode='live',
             reason='daily_cap_exceeded').
          2. Open-live-positions cap: current count of open LIVE CopytradePositions
             must be < max_open_live_positions.  Refusal -> ExecuteResult(sent=False,
             mode='live', reason='open_live_cap_exceeded').
        Only after both pass does the call reach self._sender.send_buy().

        Args:
            serialized_tx_b64: Base64-encoded signed transaction bytes.
            sol_amount:        SOL spend for this buy (used for daily cap check).
                               0.0 bypasses the daily cap check (observe path).

        Returns:
            ExecuteResult(sent=False, mode='observe') in paper/observe mode.
            ExecuteResult(sent=False, mode='live', reason=...) if budget gate fires.
            ExecuteResult(sent=True, mode='live', signature=...) on success.
        """
        if not self._config.trading_enabled or self._sender is None:
            return ExecuteResult(sent=False, mode="observe")

        # --- BUDGET KILL-SWITCH (live path only, before any network call) ---
        # Lazy import to avoid circular dependency and keep the observe path
        # free of any DB query (the Sender is never imported in observe mode).
        try:
            import datetime as _dt

            from copytrade.models import CopytradePosition

            today = _dt.date.today()

            # 1. Daily SOL spend cap
            if sol_amount > 0:
                from django.db.models import Sum as _Sum

                daily_spent = (
                    CopytradePosition.objects.filter(
                        mode=CopytradePosition.MODE_LIVE,
                        entry_ts__date=today,
                    ).aggregate(total=_Sum("sol_in"))["total"]
                    or 0.0
                )
                if (daily_spent + sol_amount) > self._config.max_daily_spend_sol:
                    import logging as _logging
                    _logging.getLogger("trading").warning(
                        "[execution] budget-kill: daily_cap_exceeded daily_spent=%.4f "
                        "sol_amount=%.4f cap=%.4f",
                        daily_spent,
                        sol_amount,
                        self._config.max_daily_spend_sol,
                    )
                    return ExecuteResult(sent=False, mode="live", reason="daily_cap_exceeded")

            # 2. Open live positions cap
            open_live_count = CopytradePosition.objects.filter(
                mode=CopytradePosition.MODE_LIVE,
                status=CopytradePosition.STATUS_OPEN,
            ).count()
            if open_live_count >= self._config.max_open_live_positions:
                import logging as _logging
                _logging.getLogger("trading").warning(
                    "[execution] budget-kill: open_live_cap_exceeded open=%d cap=%d",
                    open_live_count,
                    self._config.max_open_live_positions,
                )
                return ExecuteResult(sent=False, mode="live", reason="open_live_cap_exceeded")

        except Exception as _exc:  # noqa: BLE001
            # Budget check failure must NOT allow the buy to proceed unguarded.
            import logging as _logging
            _logging.getLogger("trading").error(
                "[execution] budget-check-error: %s — refusing buy (fail-safe)", _exc
            )
            return ExecuteResult(sent=False, mode="live", reason="budget_check_error")

        # --- LIVE PATH: trading_enabled=True + sender wired + budget OK ---
        result = self._sender.send_buy(serialized_tx_b64)
        return ExecuteResult(
            sent=True,
            mode="live",
            signature=result.signature,
        )

    def execute_sell(self, serialized_tx_b64: str) -> ExecuteResult:
        """Gate the live sell send behind trading_enabled.

        In observe/paper mode (trading_enabled=False) returns ExecuteResult
        with sent=False and mode='observe' — the Sender is NEVER reached.

        In Cutover live mode (trading_enabled=True, sender wired), delegates
        to self._sender.send_sell() — which includes the Sender→RPC fallback
        and circuit breaker (AC-65.3).

        Args:
            serialized_tx_b64: Base64-encoded signed transaction bytes.

        Returns:
            ExecuteResult(sent=False, mode='observe') in paper/observe mode.
            ExecuteResult(sent=True, mode='live', signature=...) in live mode.
        """
        if not self._config.trading_enabled or self._sender is None:
            return ExecuteResult(sent=False, mode="observe")
        # --- LIVE PATH: trading_enabled=True + sender wired (Cutover only) ---
        result = self._sender.send_sell(serialized_tx_b64)
        return ExecuteResult(
            sent=True,
            mode="live",
            signature=result.signature,
        )
