# ---
# module: trading.execution_core
# sprint: sprint-13
# story: US-64 AC-64.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.clock, core.datasource, trading.schemas
# ---
"""Thin clock-injected execution core for the shared trading apparatus (AC-64.1).

ExecutionCore is the single shared entry point BOTH pipelines (prediction model
and copy-trade) call into.  DataSource and Clock are INJECTED — no concrete
LiveSource or ReplaySource import on this module (Principle #7 / US-2 seam).

Design constraints (AC-64.1):
  - No direct import of LiveSource or ReplaySource (DataSource seam preserved)
  - No time.time() / datetime.now() calls (Clock-injected only)
  - trading_enabled DEFAULT False — observe/paper safety gate
  - This skeleton is extended by US-65 (ix builder) and US-66 (exit engine)

The class is intentionally thin at AC-64.1 — it holds the config + source + clock
triple and exposes is_trading_enabled() for the AST guard test to verify.
"""

from core.clock import Clock
from core.datasource import DataSource
from trading.schemas import TradingConfig


class ExecutionCore:
    """Thin clock-injected core.  Both pipelines call this.

    DataSource and Clock are injected at construction time; this class NEVER
    imports or instantiates LiveSource or ReplaySource directly (Principle #7).

    Args:
        source: An injected DataSource implementation (live or replay).
        clock:  An injected Clock implementation (wall or virtual).
        config: The validated TradingConfig holding all §10/§17 trade knobs.
    """

    def __init__(self, source: DataSource, clock: Clock, config: TradingConfig) -> None:
        self._source = source
        self._clock = clock
        self._config = config

    def is_trading_enabled(self) -> bool:
        """Return whether live trading is enabled (DEFAULT False — observe/paper gate).

        A result of False means ALL execution is in observe/paper mode and NO
        real orders will be placed (AC-64.1 safety gate / PRD §10.2).
        """
        return self._config.trading_enabled
