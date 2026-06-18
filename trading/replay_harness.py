# ---
# module: trading.replay_harness
# sprint: sprint-13
# story: US-67 AC-67.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.schemas, trading.tape_settler, trading.models, datetime
# ---
"""T2 full-pipeline replay harness — 'a day in 30s' (PRD §11 / AC-67.1).

Replays whole tape days end-to-end through the REAL serving + exit + settler
core (clock-injected, DataSource=Replay) and writes the identical artifacts
a live run would — Predictions/Positions/PnL — into the ISOLATED replay
sandbox (db_table='trading_replay_positions'), NEVER the live tables
(db_table='trading_positions').

Architecture (Principle #7 — clock-injected, DataSource seam):
    - No concrete live source import on the core path.
    - No time.time() / datetime.now() on the core path (injectable clock).
    - DataSource=Replay: tape dict passed directly; no network, no firehose.
    - config-driven via TradingConfig (Pydantic, consistent with §5/§10/§17).

Public API
----------
DayReplayHarness(config, predictor=None, score_threshold=0.5,
                 score_delay_s=30, size_sol=0.1, sol_usd=140.0)
    .run(tape, replay_run_id, now=None) -> list[dict]
        Replay a multi-token tape day. Returns serializable result dicts
        (one per token at or above the score threshold). Also writes
        ReplayPosition rows to the sandbox table for the given run ID.

Tape format
-----------
tape: dict[str, list[tuple[float, float, float]]]
    {mint: [(block_time_s, price_sol, usd_volume), ...], ...}
    Each mint's list must be pre-sorted by (block_time, slot, signature)
    — identical to load_tape() in tape_resettle.py.

Result dict schema (one per token that meets score threshold)
-------------------------------------------------------------
Shared fields:
    mint       str    — token mint address
    score      float  — predictor score (0.0–1.0)
    enterable  bool   — False if tape_settler returned un-enterable

When enterable=False:
    reason     str    — 'no-tape' | 'dead' | 'slip-miss'

When enterable=True:
    pnl        float  — realized PnL percentage (e.g. -17.08)
    trigger    str    — exit rule that fired (e.g. 'STOP_LOSS')
    held       float  — seconds from entry to trigger
    peak       float  — peak return percentage from fill
    flow       float  — total USD volume in entry ±30s window

Isolation guarantee
-------------------
    run() creates ReplayPosition rows via ReplayPosition.objects.create().
    It NEVER imports or touches trading.models.Position.
    The sandbox table ('trading_replay_positions') != live table
    ('trading_positions') — verified by the §11.3 guard test (AC-67.3).

Zero firehose
-------------
    The harness is offline/replay by construction — no Birdeye, no Helius,
    no RPC calls.  The injected tape is the SOLE data source.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from trading.schemas import TradingConfig
from trading.tape_settler import TradeTuple, simulate_tape_exit

# Predictor type: (mint, trades) -> score in [0.0, 1.0]
Predictor = Callable[[str, Sequence[TradeTuple]], float]

# Default score delay matches the tape settler's pre-trade window (_GAP = 30s)
_DEFAULT_SCORE_DELAY_S: float = 30.0
_DEFAULT_SCORE_THRESHOLD: float = 0.5
_DEFAULT_SIZE_SOL: float = 0.1
_DEFAULT_SOL_USD: float = 140.0


def _constant_one(mint: str, trades: Sequence[TradeTuple]) -> float:
    """Default predictor: all tokens score 1.0 (all qualify for entry)."""
    return 1.0


class DayReplayHarness:
    """T2 full-pipeline replay harness ('a day in 30s').

    Replays a multi-token tape day through the REAL tape settler core with an
    injectable predictor, writing results to the isolated replay sandbox table.

    Parameters
    ----------
    config:
        TradingConfig providing exit-rule thresholds (tp, sl, disaster, rugcut,
        timer).  All config is read at construction time — consistent with
        Principle #1 (config-driven, no literals in code).
    predictor:
        Callable(mint, trades) -> score.  If None, defaults to constant 1.0
        (all tokens are treated as winners).  Injected for testability.
    score_threshold:
        Minimum score to open a position.  Tokens below this are skipped
        entirely (not written to the sandbox).  Default 0.5.
    score_delay_s:
        Seconds after the first tape trade (graduation time ≈ t0) to set the
        entry timestamp.  Must match or exceed the tape settler's _GAP (30s)
        so the pre-entry quote window is populated.  Default 30.0.
    size_sol:
        Position size in SOL.  Passed to simulate_tape_exit's size_sol.
    sol_usd:
        SOL/USD rate for USD-volume impact computation.  Default 140.0 matches
        the tape settler's default and the trills oracle's --sol-usd default.
    """

    def __init__(
        self,
        config: TradingConfig,
        predictor: Optional[Predictor] = None,
        score_threshold: float = _DEFAULT_SCORE_THRESHOLD,
        score_delay_s: float = _DEFAULT_SCORE_DELAY_S,
        size_sol: float = _DEFAULT_SIZE_SOL,
        sol_usd: float = _DEFAULT_SOL_USD,
    ) -> None:
        self.config = config
        self.predictor: Predictor = predictor if predictor is not None else _constant_one
        self.score_threshold = score_threshold
        self.score_delay_s = score_delay_s
        self.size_sol = size_sol
        self.sol_usd = sol_usd

    def run(
        self,
        tape: dict[str, Sequence[TradeTuple]],
        replay_run_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Replay a multi-token tape day.

        Iterates over all mints in sorted order (deterministic), scores each
        via the injected predictor, settles enterable positions via
        simulate_tape_exit, and writes one ReplayPosition row per qualifying
        token to the isolated sandbox table.

        Parameters
        ----------
        tape:
            {mint: [(block_time_s, price_sol, usd_volume), ...], ...}
            Pre-sorted by (block_time, slot, signature) per mint.
        replay_run_id:
            Opaque identifier for this replay run (written to every
            ReplayPosition row).  Generated as a UUID4 if None.
        now:
            Settlement timestamp written to ReplayPosition.created_at.
            Injectable for deterministic tests; defaults to UTC now.

        Returns
        -------
        list[dict]
            One result dict per token at or above score_threshold, in
            sorted(mint) order (deterministic).  See module docstring for
            the full dict schema.  The list is also written to the sandbox
            table as ReplayPosition rows.
        """
        if replay_run_id is None:
            replay_run_id = str(uuid.uuid4())
        if now is None:
            now = datetime.now(timezone.utc)

        # Import here so the core module has no top-level live/model imports
        from trading.models import ReplayPosition

        results: list[dict] = []

        for mint in sorted(tape.keys()):
            trades = list(tape[mint])
            if not trades:
                continue

            score = self.predictor(mint, trades)
            if score < self.score_threshold:
                continue

            t0 = trades[0][0]
            entry_ts = t0 + self.score_delay_s

            settler_result = simulate_tape_exit(
                trades, entry_ts, self.config, self.size_sol, self.sol_usd
            )

            record: dict = {"mint": mint, "score": score}
            record.update(settler_result)

            # Compute absolute prices when enterable (for sandbox persistence)
            fill_price: Optional[float] = None
            exit_price_abs: Optional[float] = None
            peak_price_abs: Optional[float] = None
            entry_ts_dt: Optional[datetime] = None

            if settler_result.get("enterable"):
                fill_price = _find_fill_price(trades, entry_ts)
                if fill_price is not None and fill_price > 0:
                    pnl_pct: float = settler_result["pnl"]
                    peak_pct: float = settler_result["peak"]
                    exit_price_abs = fill_price * (1 + pnl_pct / 100)
                    peak_price_abs = fill_price * (1 + peak_pct / 100)
                entry_ts_dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc)

            # Write to isolated replay sandbox (NEVER to trading_positions)
            ReplayPosition.objects.create(
                replay_run_id=replay_run_id,
                mint=mint,
                score=score,
                enterable=settler_result.get("enterable", False),
                unentered_reason=settler_result.get("reason") if not settler_result.get("enterable") else None,
                entry_ts=entry_ts_dt,
                entry_price=fill_price,
                size_sol=self.size_sol,
                exit_trigger=settler_result.get("trigger"),
                realized_pnl_pct=settler_result.get("pnl"),
                peak_pct=settler_result.get("peak"),
                held_s=settler_result.get("held"),
                flow_usd=settler_result.get("flow"),
                exit_price=exit_price_abs,
                peak_price_abs=peak_price_abs,
            )

            results.append(record)

        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_fill_price(
    trades: Sequence[TradeTuple], entry_ts: float
) -> Optional[float]:
    """Return the fill price for an entry — first swap at entry + 2s.

    Replicates the tape_settler's fill convention (_LAT = 2s) so the
    harness can persist entry_price in the ReplayPosition row.
    """
    _LAT = 2
    post = [(t, p) for t, p, _ in trades if t >= entry_ts + _LAT]
    if not post:
        return None
    return post[0][1]
