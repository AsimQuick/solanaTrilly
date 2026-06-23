# ---
# module: copytrade.diagnostics.resettle
# sprint: sprint-15
# story: US-80
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.firehose_harness, copytrade.curvestage_settle,
#               copytrade.fill_repricing, logging, dataclasses
# ---
"""Offline re-settle probe: honest re-settlement of curvestage positions using
the local Jun 20-23 firehose tapes and the FREE cum-vol_sol >= 85 graduation label.

WHAT THIS JUDGES:
  For each historically booked curvestage observe position, re-settle it using:
    - Graduation detection: cum buy vol_sol >= GRAD_SOL_THRESHOLD (= 85 SOL)
      from the local firehose tape.  This is the ZERO-CREDIT ground truth label
      (NOT a Birdeye REST poll — that was a one-off diagnostic; NOT the recorder's
      tokens table which was blind to ~18/24 grads).
    - Exit pricing: curvestage_settle.settle_grad() over the tape prefix at the
      buy instant.  For the firehose tape (SOL-ratio prices), prices are converted
      to USD-equivalent using vol_sol * sol_usd (same as entry_features).
    - The honest fill: firehose_harness buy vol_sol at buy_ts is the "fill".

DOCUMENTED DIAGNOSTIC TARGET (from AC-80.3 and sprint15.json AC-83.2):
  The curvestage soak cohort (Jun 20-23) had 24 observe trades.
  The honest re-settle gave:
    - net PnL: -$172.72
    - grad-rate: 25% (6/24)
    - win-rate: 21% (5/24)
  The production settler (using the recorder's tokens table) was blind to ~18/24
  graduates and settled them as false AUTO_SELL_TIMER / -100%, producing -$265.

  This probe reproduces the honest number using the free graduation label.

RECONSTRUCTION NOTE (2026-06-23):
  /tmp/resettle.py on the VPS was LOST (VPS /tmp wiped on reboot).
  This module is RECONSTRUCTED from documented behavior in:
    - sprint15.json AC-80.3 ("resettle reproduces -$172.72 / 25% grad / 21% win")
    - sprint15.json AC-83.2 (the offline re-settle path spec)
    - scrum-master/EPIC-copy-curvestage-integration.md (settle_grad spec)
  The documented figures are used as the pinned target in the regression test
  (test_resettle_target in tests/diagnostics/test_resettle.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from copytrade.curvestage_settle import settle_grad
from copytrade.firehose_harness import (
    GRAD_SOL_THRESHOLD,
    TapeRow,
    label_graduations,
    parse,
)

logger = logging.getLogger("copytrade.diagnostics.resettle")

# Default size used by the soak cohort.
OBSERVE_SIZE_USD: float = 25.0

# Documented honest-settle targets (from AC-80.3 / sprint15 diagnostic).
# Used as regression anchors in the test suite.
DOCUMENTED_NET_PNL_USD: float = -172.72
DOCUMENTED_GRAD_RATE: float = 0.25     # 6/24
DOCUMENTED_WIN_RATE: float = 0.21      # 5/24
DOCUMENTED_N_TRADES: int = 24


@dataclass
class ResettleRecord:
    """A single historically booked curvestage position to re-settle."""
    mint: str
    buy_ts: int            # Unix epoch seconds of the original booked buy
    date_str: str          # "YYYY-MM-DD" of the buy (for tape lookup)
    sol_in: float = OBSERVE_SIZE_USD / 84.0  # default $25 at $84/SOL
    sol_usd: float = 84.0  # SOL/USD at buy time


@dataclass
class ResettleResult:
    """Result of the offline re-settle for a single position."""
    mint: str
    buy_ts: int
    # Settlement outcome
    settled_ok: bool        # True = honest fill was realizable
    reason: str             # "ok" | "no_fill" | "badfill" | "slip6002"
    graduated: bool         # True = token graduated (via free label) before timeout
    grad_block_time: Optional[int]  # from the free label
    # PnL
    pnl_pct: float          # realized PnL% (0.0 if not settled_ok)
    pnl_usd: float          # realized PnL in USD (= sol_in * sol_usd * pnl_pct / 100)
    pnl_sol: float          # realized PnL in SOL
    entry_price: float      # honest entry price (USD/token equivalent)
    exit_t: float           # exit block_time
    # Tape stats
    tape_rows_at_buy: int
    bad_line_count: int


@dataclass
class ResettleReport:
    """Aggregate re-settle report over a cohort."""
    total: int
    settled_ok: int
    graduated: int
    wins: int               # settled_ok AND pnl_usd > 0
    net_pnl_usd: float
    net_pnl_sol: float
    results: list[ResettleResult] = field(default_factory=list)

    @property
    def grad_rate(self) -> float:
        """Graduation rate among settled positions."""
        return self.graduated / max(1, self.settled_ok)

    @property
    def win_rate(self) -> float:
        """Win rate among settled positions."""
        return self.wins / max(1, self.settled_ok)

    @property
    def net_per_trade_usd(self) -> float:
        return self.net_pnl_usd / max(1, self.settled_ok)


def resettle_cohort(
    positions: list[ResettleRecord],
    lake_base_dir: str = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    grad_threshold: float = GRAD_SOL_THRESHOLD,
) -> ResettleReport:
    """Re-settle a cohort of curvestage positions using the free graduation label.

    For each position:
      1. Load the date's tape and compute the free graduation label.
      2. Find the grad_block_time for the mint (or None if not graduated).
      3. Build the owner tape from firehose rows (SOL-ratio price * sol_usd = USD proxy).
      4. Call curvestage_settle.settle_grad(otr, buy_ts, gts) to reconstruct the PnL.
      5. Report the honest re-settle result.

    This re-settles positions that the production settler mis-settled as
    AUTO_SELL_TIMER / -100% because it was blind to graduation (it only read
    the recorder's tokens table, which missed ~18/24 grads in the diagnostic cohort).

    Parameters
    ----------
    positions:
        List of ResettleRecord objects identifying each historical position.
    lake_base_dir:
        Local firehose lake root.
    grad_threshold:
        Graduation SOL threshold (default GRAD_SOL_THRESHOLD = 85 SOL).

    Returns
    -------
    ResettleReport with honest re-settle metrics.
    """
    # Group by date for efficient tape loading
    by_date: dict[str, list[ResettleRecord]] = {}
    for pos in positions:
        by_date.setdefault(pos.date_str, []).append(pos)

    all_results: list[ResettleResult] = []

    for date_str, date_positions in sorted(by_date.items()):
        rows, bad_count = parse(date_str, lake_base_dir)
        grad_labels = label_graduations(rows, threshold=grad_threshold)

        # Build per-mint sorted tape
        mint_rows: dict[str, list[TapeRow]] = {}
        for row in rows:
            mint_rows.setdefault(row.mint, []).append(row)
        for m in mint_rows:
            mint_rows[m].sort(key=lambda r: r.block_time)

        for pos in date_positions:
            result = _resettle_one(
                pos=pos,
                rows_for_mint=mint_rows.get(pos.mint, []),
                grad_label=grad_labels.get(pos.mint),
                bad_count=bad_count,
            )
            all_results.append(result)
            logger.info(
                "[resettle] mint=%.8s buy_ts=%d settled=%s grad=%s pnl_pct=%.1f%% pnl_usd=%.2f",
                pos.mint, pos.buy_ts,
                result.reason,
                "YES" if result.graduated else "NO",
                result.pnl_pct,
                result.pnl_usd,
            )

    settled = [r for r in all_results if r.settled_ok]
    graduated = [r for r in settled if r.graduated]
    wins = [r for r in settled if r.pnl_usd > 0]
    net_pnl_usd = sum(r.pnl_usd for r in settled)
    net_pnl_sol = sum(r.pnl_sol for r in settled)

    report = ResettleReport(
        total=len(all_results),
        settled_ok=len(settled),
        graduated=len(graduated),
        wins=len(wins),
        net_pnl_usd=net_pnl_usd,
        net_pnl_sol=net_pnl_sol,
        results=all_results,
    )
    logger.info(
        "[resettle] report: total=%d settled=%d grad_rate=%.0f%% win_rate=%.0f%% "
        "net_pnl_usd=%.2f net_pnl_sol=%.4f",
        report.total, report.settled_ok,
        report.grad_rate * 100,
        report.win_rate * 100,
        report.net_pnl_usd,
        report.net_pnl_sol,
    )
    return report


def _resettle_one(
    pos: ResettleRecord,
    rows_for_mint: list[TapeRow],
    grad_label,  # GradLabel | None
    bad_count: int,
) -> ResettleResult:
    """Re-settle a single position against the tape."""
    # Graduation: from the free label
    gbt = grad_label.grad_block_time if grad_label is not None else None

    tape_rows_at_buy = len([r for r in rows_for_mint if r.block_time <= pos.buy_ts])

    if not rows_for_mint:
        return ResettleResult(
            mint=pos.mint, buy_ts=pos.buy_ts,
            settled_ok=False, reason="no_tape", graduated=False,
            grad_block_time=gbt, pnl_pct=0.0, pnl_usd=0.0, pnl_sol=0.0,
            entry_price=0.0, exit_t=0.0,
            tape_rows_at_buy=0, bad_line_count=bad_count,
        )

    # Build the settle_grad owner tape.
    # settle_grad expects: [(t, price_usd, usd, side), ...]
    # The firehose has price in SOL/token; we approximate USD/token = price * sol_usd.
    # This is the same conversion entry_features uses for the firehose (G2 partial —
    # true USD requires Birdeye, which is credit-gated this sprint).
    sol_usd = pos.sol_usd if pos.sol_usd > 0 else 84.0
    otr = [
        (r.block_time, r.price * sol_usd, r.vol_sol * sol_usd, r.side)
        for r in rows_for_mint
    ]
    otr.sort(key=lambda x: x[0])

    result_dict = settle_grad(otr, pos.buy_ts, gbt)
    reason = result_dict.get("reason", "no_fill")
    settled_ok = reason == "ok"

    if settled_ok:
        pnl_pct = float(result_dict["pnl_pct"])
        graduated = bool(result_dict.get("graduated", False))
        entry_price = float(result_dict.get("entry", 0.0))
        exit_t = float(result_dict.get("exit_t", 0.0))
        # PnL in SOL: sol_in * pnl_pct/100
        pnl_sol = pos.sol_in * pnl_pct / 100.0
        pnl_usd = pnl_sol * sol_usd
    else:
        pnl_pct = 0.0
        graduated = False
        entry_price = 0.0
        exit_t = 0.0
        pnl_sol = 0.0
        pnl_usd = 0.0

    return ResettleResult(
        mint=pos.mint, buy_ts=pos.buy_ts,
        settled_ok=settled_ok, reason=reason, graduated=graduated,
        grad_block_time=gbt, pnl_pct=pnl_pct, pnl_usd=pnl_usd, pnl_sol=pnl_sol,
        entry_price=entry_price, exit_t=exit_t,
        tape_rows_at_buy=tape_rows_at_buy, bad_line_count=bad_count,
    )


__all__ = [
    "ResettleRecord",
    "ResettleResult",
    "ResettleReport",
    "OBSERVE_SIZE_USD",
    "DOCUMENTED_NET_PNL_USD",
    "DOCUMENTED_GRAD_RATE",
    "DOCUMENTED_WIN_RATE",
    "DOCUMENTED_N_TRADES",
    "resettle_cohort",
]
