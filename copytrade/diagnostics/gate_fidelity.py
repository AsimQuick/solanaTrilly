# ---
# module: copytrade.diagnostics.gate_fidelity
# sprint: sprint-15
# story: US-80
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: copytrade.firehose_harness, copytrade.entry_features,
#               copytrade.pgrad_classifier, logging, dataclasses
# ---
"""Gate-fidelity diagnostic: recompute all three curvestage copy gates per pick
at the buy instant, using the local Jun 20-23 firehose tapes.

WHAT THIS JUDGES:
  For each historically booked curvestage position (identified by mint + buy_ts),
  recompute gates 1-3 from the local tape:
    Gate 1: on_curve — did the token graduate AFTER the buy instant?
            (i.e. the cum-vol_sol graduation label from firehose_harness fires
             at a block_time STRICTLY AFTER buy_ts)
    Gate 2: curve_frac = cum buy vol_sol at buy_ts / 85 <= 0.60
    Gate 3: P(graduate) >= frozen_threshold (using the seed LGBM on the tape prefix)

  Reports which gates would have fired at the buy instant and flags any cases
  where a booked position FAILED a gate (i.e. a bug in the live engine that let
  a position through that should have been rejected).

RECONSTRUCTION NOTE (2026-06-23):
  The files /tmp/gate_fidelity.py and /tmp/resettle.py on the VPS were LOST
  (the VPS /tmp is wiped on reboot).  This module is a RECONSTRUCTION from the
  documented diagnostic behavior in sprint15.json (AC-80.1, AC-80.3) and the
  sprint15.md description.

  The documented behavior:
    - gate_fidelity = recompute all three copy gates per pick at the buy instant
    - reads the local lake via firehose_harness.parse()
    - reports gate-firing counts per pick (not per-day aggregate)
    - zero credits — local tapes only

  The specific figures from AC-80.3 ("gate_fidelity reproduces the known
  gate-firing counts") refer to the live soak cohort.  Since the soak positions
  are in the DB (not a local artifact), this module accepts a list of pick records
  and reports gate recomputes over them, which a test can fix a fixture for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from copytrade.firehose_harness import (
    GRAD_SOL_THRESHOLD,
    TapeRow,
    label_graduations,
    parse,
)

logger = logging.getLogger("copytrade.diagnostics.gate_fidelity")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PickRecord:
    """A single historically booked curvestage position to recheck."""
    mint: str
    buy_ts: int            # Unix epoch seconds of the watched buy
    date_str: str          # "YYYY-MM-DD" of the buy (for tape lookup)
    wallet: str = ""
    sol_usd: float = 84.0  # SOL/USD at buy time (for wallet_buy_usd conversion)
    sol_amount: float = 0.0  # SOL amount of the watched buy (for wallet_buy_usd)


@dataclass
class GateFidelityResult:
    """Gate-recompute result for a single pick."""
    mint: str
    buy_ts: int
    # Gate 1: was the token still on-curve at the buy?
    gate1_on_curve: bool        # True if grad_block_time > buy_ts (or not graduated)
    grad_block_time: Optional[int]  # from the free label; None = not graduated in tape
    # Gate 2: curve room
    gate2_curve_room: bool
    curve_frac_at_buy: float    # cum_buy_sol_at_buy / GRAD_SOL_THRESHOLD
    cum_buy_sol_at_buy: float   # cumulative buy vol_sol up to and including buy_ts
    # Gate 3: P(graduate) classifier
    gate3_pgrad: bool
    pgrad_score: float          # raw score from the seed model (or -1.0 if no tape/model)
    pgrad_threshold: float      # the frozen threshold at evaluation time
    # Overall: did ALL gates pass?
    all_gates_pass: bool
    # Tape stats at the buy instant
    tape_rows_at_buy: int       # number of tape rows up to and including buy_ts
    bad_line_count: int         # bad lines in the tape for this date
    # Wall_buy_usd used for scoring
    wallet_buy_usd: float


@dataclass
class GateFidelityReport:
    """Summary report across a cohort of picks."""
    total_picks: int
    gate1_pass: int
    gate2_pass: int
    gate3_pass: int
    all_pass: int
    results: list[GateFidelityResult] = field(default_factory=list)

    @property
    def gate1_rate(self) -> float:
        return self.gate1_pass / max(1, self.total_picks)

    @property
    def gate2_rate(self) -> float:
        return self.gate2_pass / max(1, self.total_picks)

    @property
    def gate3_rate(self) -> float:
        return self.gate3_pass / max(1, self.total_picks)

    @property
    def all_pass_rate(self) -> float:
        return self.all_pass / max(1, self.total_picks)


# ---------------------------------------------------------------------------
# Core recompute function
# ---------------------------------------------------------------------------

def recompute_gates(
    picks: list[PickRecord],
    lake_base_dir: str = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    curve_frac_gate: float = 0.60,
    grad_threshold: float = GRAD_SOL_THRESHOLD,
    pgrad_classifier=None,  # Optional[PgradClassifier] — passed to avoid import
) -> GateFidelityReport:
    """Recompute all three copy gates for each pick in the cohort.

    Uses ONLY the local firehose tapes (zero credits).  The graduation label
    comes from firehose_harness.label_graduations (cum buy vol_sol >= grad_threshold).

    Parameters
    ----------
    picks:
        List of PickRecord objects identifying each historical position.
    lake_base_dir:
        Local firehose lake root.
    curve_frac_gate:
        Gate 2 threshold (default 0.60 per the cohort spec).
    grad_threshold:
        Graduation SOL threshold (default GRAD_SOL_THRESHOLD = 85 SOL).
    pgrad_classifier:
        Optional loaded PgradClassifier.  When None, Gate 3 is scored as
        pgrad_score = -1.0, gate3_pgrad = False (fail-closed; the test fixture
        can pass a mock classifier).

    Returns
    -------
    GateFidelityReport
    """
    # Group picks by date for efficient tape loading
    by_date: dict[str, list[PickRecord]] = {}
    for pick in picks:
        by_date.setdefault(pick.date_str, []).append(pick)

    results: list[GateFidelityResult] = []

    for date_str, date_picks in sorted(by_date.items()):
        rows, bad_count = parse(date_str, lake_base_dir)
        grad_labels = label_graduations(rows, threshold=grad_threshold)

        # Build per-mint sorted buy timeline for Gate 2 recompute
        mint_rows: dict[str, list[TapeRow]] = {}
        for row in rows:
            mint_rows.setdefault(row.mint, []).append(row)
        # Sort by block_time within each mint (tape is multi-mint interleaved)
        for m in mint_rows:
            mint_rows[m].sort(key=lambda r: r.block_time)

        for pick in date_picks:
            result = _recompute_one(
                pick=pick,
                rows_for_mint=mint_rows.get(pick.mint, []),
                grad_label=grad_labels.get(pick.mint),
                bad_count=bad_count,
                curve_frac_gate=curve_frac_gate,
                grad_threshold=grad_threshold,
                pgrad_classifier=pgrad_classifier,
            )
            results.append(result)
            logger.info(
                "[gate_fidelity] mint=%.8s buy_ts=%d gates=[1:%s 2:%s 3:%s] all=%s "
                "curve_frac=%.3f pgrad=%.4f",
                pick.mint, pick.buy_ts,
                "PASS" if result.gate1_on_curve else "FAIL",
                "PASS" if result.gate2_curve_room else "FAIL",
                "PASS" if result.gate3_pgrad else "FAIL",
                "PASS" if result.all_gates_pass else "FAIL",
                result.curve_frac_at_buy,
                result.pgrad_score,
            )

    report = GateFidelityReport(
        total_picks=len(results),
        gate1_pass=sum(1 for r in results if r.gate1_on_curve),
        gate2_pass=sum(1 for r in results if r.gate2_curve_room),
        gate3_pass=sum(1 for r in results if r.gate3_pgrad),
        all_pass=sum(1 for r in results if r.all_gates_pass),
        results=results,
    )
    logger.info(
        "[gate_fidelity] report: total=%d gate1=%.0f%% gate2=%.0f%% gate3=%.0f%% all=%.0f%%",
        report.total_picks,
        report.gate1_rate * 100,
        report.gate2_rate * 100,
        report.gate3_rate * 100,
        report.all_pass_rate * 100,
    )
    return report


def _recompute_one(
    pick: PickRecord,
    rows_for_mint: list[TapeRow],
    grad_label,  # GradLabel | None
    bad_count: int,
    curve_frac_gate: float,
    grad_threshold: float,
    pgrad_classifier,
) -> GateFidelityResult:
    """Recompute all three gates for a single pick against the tape."""
    # --- Gate 1: on_curve ---
    # Token is on-curve at buy_ts if graduation happened AFTER buy_ts (or not at all)
    gbt = grad_label.grad_block_time if grad_label is not None else None
    gate1_on_curve = (gbt is None) or (gbt > pick.buy_ts)

    # --- Gate 2: curve_frac = cum_buy_sol at buy_ts / 85 <= 0.60 ---
    # Only BUY rows with block_time <= buy_ts
    pre_buy_rows = [r for r in rows_for_mint if r.side == "buy" and r.block_time <= pick.buy_ts]
    cum_buy_sol_at_buy = sum(r.vol_sol for r in pre_buy_rows)
    curve_frac_at_buy = cum_buy_sol_at_buy / grad_threshold
    gate2_curve_room = curve_frac_at_buy <= curve_frac_gate

    tape_rows_at_buy = len([r for r in rows_for_mint if r.block_time <= pick.buy_ts])

    # --- Gate 3: P(graduate) from seed LGBM ---
    pgrad_score = -1.0
    gate3_pgrad = False
    pgrad_threshold_used = 0.0

    if pgrad_classifier is not None and rows_for_mint:
        try:
            from copytrade.entry_features import entry_features

            # Build the "owner tape" from firehose rows: (t, price_usd, usd, side, owner, sol)
            # price here is SOL/token from the firehose (not USD/token as the lab used).
            # For Gate 3 recompute, we use the firehose price * sol_usd as the USD price.
            sol_usd = pick.sol_usd if pick.sol_usd > 0 else 84.0
            otr = [
                (r.block_time, r.price * sol_usd, r.vol_sol * sol_usd, r.side, r.owner, r.vol_sol)
                for r in rows_for_mint
            ]
            otr.sort(key=lambda x: x[0])
            wallet_buy_usd = pick.sol_amount * sol_usd

            feats = entry_features(otr, pick.buy_ts, gts=None, wallet_buy_usd=wallet_buy_usd)
            if feats is not None:
                passed, pgrad_score = pgrad_classifier.passes(feats)
                gate3_pgrad = passed
                pgrad_threshold_used = pgrad_classifier.threshold
        except Exception as exc:
            logger.warning("[gate_fidelity] gate3 scoring failed mint=%.8s: %s", pick.mint, exc)
            pgrad_score = -1.0
            gate3_pgrad = False
    else:
        pgrad_threshold_used = getattr(pgrad_classifier, "threshold", 0.0) if pgrad_classifier else 0.0

    all_gates_pass = gate1_on_curve and gate2_curve_room and gate3_pgrad
    wallet_buy_usd = pick.sol_amount * (pick.sol_usd if pick.sol_usd > 0 else 84.0)

    return GateFidelityResult(
        mint=pick.mint,
        buy_ts=pick.buy_ts,
        gate1_on_curve=gate1_on_curve,
        grad_block_time=gbt,
        gate2_curve_room=gate2_curve_room,
        curve_frac_at_buy=curve_frac_at_buy,
        cum_buy_sol_at_buy=cum_buy_sol_at_buy,
        gate3_pgrad=gate3_pgrad,
        pgrad_score=pgrad_score,
        pgrad_threshold=pgrad_threshold_used,
        all_gates_pass=all_gates_pass,
        tape_rows_at_buy=tape_rows_at_buy,
        bad_line_count=bad_count,
        wallet_buy_usd=wallet_buy_usd,
    )


__all__ = [
    "PickRecord",
    "GateFidelityResult",
    "GateFidelityReport",
    "recompute_gates",
]
