# ---
# module: core.firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: none
# ---
"""core.firehose — runtime spine that ties collection -> graduation -> score -> paper-trade.

OBSERVE/PAPER ONLY. Every code path in this package is gated by
PipelineState.firehose_active (the daemon) and PipelineState.trading_enabled
(the paper-trade booking).  No real RPC / Sender send path is reachable while
trading_enabled is False (the default).
"""
