# ---
# module: copytrade.position_opener
# sprint: sprint-12, sprint-13, cutover (copy-trade live), hotfix/preflight-ghostbuy
# story: US-61 AC-61.1, US-68 AC-68.1, US-68 AC-68.2, copytrade-runtime, preflight-ghostbuy
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: copytrade.models, copytrade.schemas, copytrade.trigger_pipeline,
#   trading.models, trading.execution_core
# ---
"""AC-61.1 / AC-68.1 / AC-68.2: Observe + Live position opener — shared execution chassis.

On a valid US-60 trigger in OBSERVE (paper) mode, opens a copy-trade position
by booking a fill at the injected entry price — NO real order is placed.

AC-68.1 refactor: open_observe_position NOW ALSO writes a shared
trading.Position row (source='copytrade', mode='observe', status='PAPER') so
that BOTH pipelines flow through the SHARED US-64 Position model (SPEC §0.1
'same execution path' parity).  The CopytradePosition row continues to be
written exactly as before — all existing AC-61.1 tests pass unchanged.

AC-68.2: open_live_position routes mode=live through the SHARED ExecutionCore
gated boundary.  With trading_enabled=False (the DEFAULT this sprint), the gate
returns ExecuteResult(sent=False) — NO real order is placed.  The LIVE path
writes mode='live' Position and CopytradePosition rows (capital-off / PAPER
status until Cutover, PRD §16).  The Helius wallet-subscription for LIVE capital
activation is PLANNED in ops/firehose_activation_log.md — NOT yet activated
(AC-68.2 ops-doc gate).

§5 ISOLATION PRESERVED: copytrade keeps its own engine/config/ON-OFF; only the
execution+settlement CHASSIS is shared.  The trading.Position row is written
read-only from copytrade's perspective — copytrade does NOT touch
trading_enabled or PipelineState (AST-guarded in test_observe_safety_gate_ac613).

The open_observe_position function raises ValueError if called with mode != 'observe'
so the P8 gate cannot be bypassed accidentally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from copytrade.models import CopytradePosition
from copytrade.schemas import CopyTradeConfig
from copytrade.trigger_pipeline import OpenedPositionRecord
from trading.execution_core import ExecuteResult, ExecutionCore
from trading.models import Position as SharedPosition

_logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# V2 record type — used by the cohort-2.0 engine path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenedPositionRecordV2:
    """Minimal position record for the cohort-2.0 engine path.

    Carries only the fields open_observe_position_v2 needs — the engine
    assembles these from the WalletTxEvent directly, without going through
    the legacy run_trigger_pipeline.
    """

    cohort_id: str
    mint: str
    trigger_wallet: str
    entry_ts: datetime


# ---------------------------------------------------------------------------
# Legacy 1.0 observe opener (unchanged — all AC-61.1 tests still pass)
# ---------------------------------------------------------------------------


def open_observe_position(
    record: OpenedPositionRecord,
    entry_price: float,
    config: CopyTradeConfig,
) -> CopytradePosition:
    """Open a paper position in OBSERVE mode (SPEC §10 — NO real order).

    Books a fill at *entry_price* without placing any real Solana transaction.

    Writes exactly one CopytradePosition row (as before, AC-61.1) AND one
    shared trading.Position row (AC-68.1).  The two rows are linked via
    CopytradePosition.shared_position_id.

    CopytradePosition fields:
        cohort_id      = record.cohort_id
        mint           = record.mint
        trigger_wallet = record.trigger_wallet
        status         = 'open'
        mode           = 'observe'
        entry_ts       = record.entry_ts
        entry_price    = entry_price  (the booked fill price)
        sol_in         = config.sol_size_per_trade
        shared_position_id = <pk of the shared trading.Position row>

    Shared trading.Position fields (AC-68.1):
        mint       = record.mint
        source     = 'copytrade'
        mode       = 'observe'
        status     = 'PAPER'
        entry_ts   = record.entry_ts
        entry_price = entry_price
        size_sol   = config.sol_size_per_trade

    Parameters
    ----------
    record:
        An OpenedPositionRecord produced by run_trigger_pipeline (US-60 AC-60.3).
    entry_price:
        The current curve price at which the paper fill is booked.
    config:
        Active CopyTradeConfig.  mode MUST be 'observe'; ValueError is raised
        otherwise (live execution is P8-gated).

    Returns
    -------
    The saved CopytradePosition instance (status='open', mode='observe').

    Raises
    ------
    ValueError
        If config.mode != 'observe'.
    """
    if config.mode != CopytradePosition.MODE_OBSERVE:
        raise ValueError(
            f"open_observe_position called with mode={config.mode!r}; "
            "LIVE execution is P8-gated and not implemented this sprint. "
            "The engine must remain in OBSERVE mode. "
            "See copytrade.execution and PRD §10."
        )

    # --- Write shared trading.Position row (AC-68.1 shared chassis) ---
    shared_pos = SharedPosition(
        mint=record.mint,
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_OBSERVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        size_sol=config.sol_size_per_trade,
    )
    shared_pos.save()

    # --- Write copytrade-specific position row (AC-61.1, unchanged) ---
    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        sol_in=config.sol_size_per_trade,
        shared_position_id=shared_pos.pk,
    )
    position.save()
    return position


# ---------------------------------------------------------------------------
# Cohort-2.0 observe opener — USD-sized + strategy-tagged + high_water seeded
# ---------------------------------------------------------------------------


def open_observe_position_v2(
    record: OpenedPositionRecordV2,
    entry_price: float,
    *,
    usd_size: float,
    sol_usd: float,
    strategy_id: str,
    entry_tokens: float | None = None,
) -> CopytradePosition:
    """Open a cohort-2.0 paper position in OBSERVE mode (USD-sized, strategy-tagged).

    Books a fill at *entry_price* without placing any real Solana transaction.

    Like open_observe_position but for the cohort-2.0 path:
      - USD sizing: sol_in = usd_size / sol_usd, size_usd = usd_size
      - strategy_id tag: which head owns this position's exit
      - high_water_price seeded to entry_price at open (moonshot trailing needs it)

    Writes exactly one CopytradePosition row AND one shared trading.Position row
    (AC-68.1 chassis).  The two rows are linked via CopytradePosition.shared_position_id.

    Parameters
    ----------
    record:
        An OpenedPositionRecordV2 (or any object with cohort_id, mint,
        trigger_wallet, entry_ts attributes).
    entry_price:
        The current price at which the paper fill is booked.
    usd_size:
        USD notional for this position (from GlobalConfig.usd_size_per_trade).
    sol_usd:
        SOL/USD spot at trigger time — used to compute sol_in.
    strategy_id:
        The strategy head that owns this position's exit.

    Returns
    -------
    The saved CopytradePosition instance (status='open', mode='observe').
    """
    sol_in = usd_size / sol_usd if sol_usd > 0 else 0.0

    # --- Write shared trading.Position row (AC-68.1 shared chassis) ---
    shared_pos = SharedPosition(
        mint=record.mint,
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_OBSERVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        size_sol=sol_in,
    )
    shared_pos.save()

    # --- Write copytrade-specific position row ---
    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        entry_tokens=entry_tokens,
        sol_in=sol_in,
        size_usd=usd_size,
        strategy_id=strategy_id,
        high_water_price=entry_price,
        shared_position_id=shared_pos.pk,
    )
    position.save()
    return position


# ---------------------------------------------------------------------------
# Live position opener (AC-68.2 gated boundary — NOT called by the 2.0 engine)
# ---------------------------------------------------------------------------


_LAMPORTS_PER_SOL: int = 1_000_000_000


def open_live_position(
    record: OpenedPositionRecord,
    entry_price: float,
    config: CopyTradeConfig,
    execution_core: ExecutionCore,
    serialized_tx_b64: str = "",
    *,
    sender: Any = None,
    expected_tokens: int = 0,
    get_balance_fn: Any = None,
) -> tuple[CopytradePosition | None, ExecuteResult]:
    """Open a LIVE-mode position routed through the shared ExecutionCore gate.

    This is the AC-68.2 gated boundary: mode=live routes to the shared
    (gated) execution path.  With trading_enabled=False (the DEFAULT this
    sprint) the gate returns ExecuteResult(sent=False) — NO real order is
    placed.  Capital is only committed at Cutover (PRD §16) when the operator
    provisions the trading-wallet secret and explicitly flips trading_enabled=True.

    When trading_enabled=True and exec_result.sent=True, this function performs
    ghost-buy reconciliation before booking any position:

      1. Calls sender.verify_ghost_buy(mint, expected_tokens, get_balance_fn) to
         confirm tokens actually landed.
      2. If received=False (ghost buy) or received=None (inconclusive): does NOT
         open a CopytradePosition row and does NOT write a shared Position row.
         Returns (None, exec_result) — no phantom positions.
      3. If received=True: computes the REAL landed fill:
         - real_tokens_received = GhostBuyResult.balance (raw token base units).
         - real_sol_spent = exec_result.send_result.sol_spent_lamports / 1e9 if
           available; falls back to config.sol_size_per_trade (the simulated curve
           fill) when the getTransaction decode did not surface balance data.
           Approximation: sol_spent_lamports includes the tx fee and any ATA-
           creation rent alongside the token-purchase cost; these are bundled and
           not separated (see PR body for rationale).
         - real_entry_price = real_sol_spent / real_tokens_received.
         Writes the shared Position with status=STATUS_OPEN (non-PAPER) and
         the CopytradePosition with the real landed entry_price and sol_in.

    When trading_enabled=False (observe/paper gate) or the preflight fails, the
    function falls through to the original PAPER booking path (unchanged):
    shared Position status=STATUS_PAPER, CopytradePosition written normally.
    OBSERVE/PAPER positions are byte-for-byte unchanged.

    Parameters
    ----------
    record:
        An OpenedPositionRecord produced by run_trigger_pipeline (US-60 AC-60.3).
    entry_price:
        The simulated curve fill price.  Used as entry_price when sent=False
        (observe/paper gate) or as fallback when reconciliation is unavailable.
    config:
        Active CopyTradeConfig.  mode MUST be 'live'; ValueError is raised otherwise.
    execution_core:
        The shared ExecutionCore instance.  execute_buy is called here — this IS
        the gated boundary.  With trading_enabled=False, execute_buy returns
        ExecuteResult(sent=False) without calling the Sender (capital-OFF).
    serialized_tx_b64:
        Base64-encoded signed transaction bytes.  Passed through to
        execution_core.execute_buy.  Empty string is accepted in paper/observe gate.
    sender:
        Optional Sender instance for ghost-buy verification.  Required when
        trading_enabled=True and exec_result.sent=True.  If absent on the live
        path, reconciliation is skipped and the PAPER booking path is used as
        a fail-safe (never crashes).
    expected_tokens:
        Estimated token amount from the curve buy quote (used for ghost-buy
        threshold: < 10% of expected → ghost buy).  0 accepted (no threshold).
    get_balance_fn:
        Optional callable override for ghost-buy ATA balance query (for testing).
        None in production — the Sender uses its live _get_ata_balance impl.

    Returns
    -------
    Tuple of (CopytradePosition | None, ExecuteResult).
    - (None, exec_result): ghost buy / inconclusive — no position written.
    - (position, exec_result): position booked (paper or real depending on gate).
    ExecuteResult.sent is False when trading_enabled=False (paper/observe gate).

    Raises
    ------
    ValueError
        If config.mode != 'live'.
    """
    if config.mode != CopytradePosition.MODE_LIVE:
        raise ValueError(
            f"open_live_position called with mode={config.mode!r}; "
            "this function requires mode='live'. "
            "Use open_observe_position for observe/paper mode."
        )

    # --- Gated boundary: trading_enabled=False → sent=False, no real order ---
    # Pass sol_amount so ExecutionCore's daily-spend cap (0.04 SOL) actually fires
    # on this live path — without it the cap check (guarded by sol_amount>0) is
    # silently bypassed.
    exec_result = execution_core.execute_buy(
        serialized_tx_b64, sol_amount=float(config.sol_size_per_trade)
    )

    # --- Ghost-buy reconciliation (LIVE path: exec_result.sent=True) ---
    if exec_result.sent and sender is not None:
        try:
            ghost = sender.verify_ghost_buy(
                record.mint,
                expected_tokens,
                get_balance_fn,
            )

            if not ghost.received:
                # received=False → confirmed ghost buy (0 tokens).
                # received=None  → inconclusive (all RPC errors).
                # In both cases: do NOT open a phantom position.
                _logger.warning(
                    "[copytrade] ghost-buy: mint=%.8s received=%s balance=%d "
                    "— NO position written (ghost/inconclusive)",
                    record.mint,
                    ghost.received,
                    ghost.balance,
                )
                return None, exec_result

            # Tokens confirmed landed — compute the REAL landed fill.
            real_tokens: int = ghost.balance  # raw base units

            # sol_spent_lamports: total SOL debit from payer account (pre - post balance).
            # Includes tx fee + ATA-creation rent + token-purchase SOL (bundled).
            # Approximation: fee/rent not separated from purchase cost.
            # Fallback to simulated sol_in when the getTransaction decode is unavailable.
            raw_sr = exec_result.send_result
            sol_spent_lam: int | None = getattr(raw_sr, "sol_spent_lamports", None)
            if sol_spent_lam is not None and sol_spent_lam > 0:
                real_sol_spent = sol_spent_lam / _LAMPORTS_PER_SOL
            else:
                # Fallback: use the simulated curve amount.  Log so operator can see.
                real_sol_spent = float(config.sol_size_per_trade)
                _logger.info(
                    "[copytrade] ghost-reconcile-fallback: mint=%.8s "
                    "sol_spent_lamports unavailable — using simulated sol_in=%.6f",
                    record.mint,
                    real_sol_spent,
                )

            if real_tokens > 0:
                real_entry_price = real_sol_spent / real_tokens
            else:
                real_entry_price = entry_price  # degenerate guard
                _logger.warning(
                    "[copytrade] ghost-reconcile: real_tokens=0 — using curve fill price"
                )

            _logger.info(
                "[copytrade] live-fill-reconciled: mint=%.8s tokens=%d sol=%.6f "
                "real_entry_price=%.8g (vs simulated=%.8g)",
                record.mint,
                real_tokens,
                real_sol_spent,
                real_entry_price,
                entry_price,
            )

            # Write shared Position with STATUS_OPEN (real capital at risk — non-PAPER).
            shared_pos = SharedPosition(
                mint=record.mint,
                source=SharedPosition.SOURCE_COPYTRADE,
                mode=SharedPosition.MODE_LIVE,
                status=SharedPosition.STATUS_OPEN,  # NON-PAPER: real tokens landed
                entry_ts=record.entry_ts,
                entry_price=real_entry_price,
                size_sol=real_sol_spent,
            )
            shared_pos.save()

            # Write CopytradePosition with the REAL landed fill.
            position = CopytradePosition(
                cohort_id=record.cohort_id,
                mint=record.mint,
                trigger_wallet=record.trigger_wallet,
                status=CopytradePosition.STATUS_OPEN,
                mode=CopytradePosition.MODE_LIVE,
                entry_ts=record.entry_ts,
                entry_price=real_entry_price,
                entry_tokens=float(real_tokens),
                sol_in=real_sol_spent,
                shared_position_id=shared_pos.pk,
            )
            position.save()
            return position, exec_result

        except Exception as _exc:  # noqa: BLE001 — reconciliation must never crash
            _logger.error(
                "[copytrade] ghost-reconcile-error: mint=%.8s err=%s "
                "— falling back to PAPER booking (fail-safe)",
                record.mint,
                _exc,
            )
            # Fall through to the PAPER booking path below.

    # --- PAPER booking path (sent=False OR sender absent OR reconcile error) ---
    # Unchanged from the pre-reconciliation version.
    # - sent=False (observe/paper gate, preflight failed, budget kill):
    #   writes STATUS_PAPER (no capital at risk).
    # - sent=True but no sender / reconciliation error:
    #   writes STATUS_PAPER as a fail-safe (tokens may have landed — operator
    #   must manually reconcile; the log above records the send signature).
    shared_pos = SharedPosition(
        mint=record.mint,
        source=SharedPosition.SOURCE_COPYTRADE,
        mode=SharedPosition.MODE_LIVE,
        status=SharedPosition.STATUS_PAPER,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        size_sol=config.sol_size_per_trade,
    )
    shared_pos.save()

    position = CopytradePosition(
        cohort_id=record.cohort_id,
        mint=record.mint,
        trigger_wallet=record.trigger_wallet,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_LIVE,
        entry_ts=record.entry_ts,
        entry_price=entry_price,
        sol_in=config.sol_size_per_trade,
        shared_position_id=shared_pos.pk,
    )
    position.save()
    return position, exec_result
