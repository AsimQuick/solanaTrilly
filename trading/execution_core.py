# ---
# module: trading.execution_core
# sprint: sprint-13
# story: US-64 AC-64.1 / US-65 AC-65.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
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
    """

    sent: bool
    mode: str
    signature: str | None = None


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

    def execute_buy(self, serialized_tx_b64: str) -> ExecuteResult:
        """Gate the live buy send behind trading_enabled.

        In observe/paper mode (trading_enabled=False) returns ExecuteResult
        with sent=False and mode='observe' — the Sender is NEVER reached.

        In Cutover live mode (trading_enabled=True, sender wired), delegates
        to self._sender.send_buy() — the clearly-isolated boundary (AC-65.3).

        Args:
            serialized_tx_b64: Base64-encoded signed transaction bytes.

        Returns:
            ExecuteResult(sent=False, mode='observe') in paper/observe mode.
            ExecuteResult(sent=True, mode='live', signature=...) in live mode.
        """
        if not self._config.trading_enabled or self._sender is None:
            return ExecuteResult(sent=False, mode="observe")
        # --- LIVE PATH: trading_enabled=True + sender wired (Cutover only) ---
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
