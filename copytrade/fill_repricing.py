# ---
# module: copytrade.fill_repricing
# sprint: epic/copy-paper-fill-repricing
# story: EPIC-copy-paper-fill-repricing
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: copytrade.models, core.tape.lake_reader, datetime, logging
# ---
"""Retrospective fill repricing for copy-trade observe/paper positions.

Reprices BOTH legs (entry + exit) of an observe/paper position from the firehose
lake (``lake/firehose/dt=YYYY-MM-DD/part-0.jsonl.gz``) at settlement time —
NOT during the live consume path (that would block the single-threaded consumer,
and lake write-lag > 1.5s anyway).

PRICE-UNIT BASIS (audited 2026-06-21):
  - ``event.raw["price"]`` = ``vsol/vtok`` (Helius TradeEvent decode, lamports-ratio).
  - Pre-grad lake rows (phase="pre") = same ``vsol/vtok`` ratio.
  - Post-grad lake rows (phase="post") = Birdeye tokenPrice (USD/token) — DIFFERENT.
  Copy-trade wallet buys are bonding-curve (pre-grad), so the relevant lake rows
  share the same price units as the wallet quote.  No unit conversion is needed
  when filtering by the mint and block_time window.

WINDOW RULE (the load-bearing delta):
  Lake ``block_time`` is INTEGER-second (tape_sink.py:121 ``int(raw_bt)``).  The
  lab reconstructed at 0.5/1/2s latency and got the SAME break-even PnL at every
  speed → row selection collapses to blocks.  Deterministic rule:

      candidate = first row for mint where
          int(row["block_time"]) >= int(wallet_block_time) + REPRICE_BLOCK_OFFSET
      within [wallet_block_time + REPRICE_BLOCK_OFFSET,
              wallet_block_time + REPRICE_BLOCK_OFFSET + REPRICE_WINDOW_S]
      EXCLUDE same-block rows.
      No side/size filter.  None → NO_TAPE (keep curve-sim price, do NOT reject).

Module-level constants (NOT DB config — methodology parameters):
  REPRICE_BLOCK_OFFSET = 1   (first block AFTER the wallet buy)
  REPRICE_WINDOW_S     = 10  (search window)

Scan the wallet's UTC date AND the next date (10s window can cross midnight).

Showstoppers verified:
  1. copytrade_engine docker-compose.staging.yml lake mount — added in this PR.
  2. Price-unit basis audited — pre-grad lake rows use same vsol/vtok units.
     No conversion needed; direct 15% cap comparison is valid.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.tape.lake_reader import LakeReader

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# WINDOW RULE constants (module-level, NOT DB config)
# ---------------------------------------------------------------------------

#: Minimum block offset from wallet buy: select first row with
#: int(block_time) >= int(wallet_block_time) + REPRICE_BLOCK_OFFSET.
#: Default 1 = the first NEXT block after the wallet buy.
REPRICE_BLOCK_OFFSET: int = 1

#: Forward search window in seconds from wallet_block_time + REPRICE_BLOCK_OFFSET.
#: None returned if no row found within this window (→ NO_TAPE).
REPRICE_WINDOW_S: int = 10

#: Slippage cap for repriced fills — mirrors the live honest_fill cap (15%).
REPRICE_SLIP_CAP: float = 0.15

# ---------------------------------------------------------------------------
# Status constants (written to CopytradePosition.entry_reprice_status)
# ---------------------------------------------------------------------------

REPRICE_STATUS_REPRICED = "REPRICED"
REPRICE_STATUS_ENTRY_REJECTED_TAPE = "ENTRY_REJECTED_TAPE"
REPRICE_STATUS_NO_TAPE = "NO_TAPE"
REPRICE_STATUS_PENDING = None  # null in DB = not yet settled


def find_next_trade_price(
    mint: str,
    wallet_block_time: float,
    *,
    lake_base_dir: str = "lake/firehose",
    block_offset: int = REPRICE_BLOCK_OFFSET,
    window_s: int = REPRICE_WINDOW_S,
) -> Optional[float]:
    """Find the price of the first real trade for *mint* after *wallet_block_time*.

    Implements the WINDOW RULE from the epic spec:

        candidate = first lake row for mint where
            int(row["block_time"]) >= int(wallet_block_time) + block_offset
        within window [int(wallet_block_time) + block_offset,
                       int(wallet_block_time) + block_offset + window_s]
        EXCLUDE same-block rows (block_time == int(wallet_block_time)).
        No side/size/signer filter.

    Scans the wallet's UTC date AND the following UTC date so the window
    correctly handles midnight crossings.

    Parameters
    ----------
    mint:
        Token mint address to filter by.
    wallet_block_time:
        On-chain block_time of the wallet's buy (Unix epoch float or int).
        Converted to int at the boundary.
    lake_base_dir:
        Root of the firehose lake tree (default ``lake/firehose``).
    block_offset:
        Minimum block offset from wallet_block_time (default REPRICE_BLOCK_OFFSET=1).
    window_s:
        Search window in seconds (default REPRICE_WINDOW_S=10).

    Returns
    -------
    float | None
        Price from the first matching lake row, or None if no row found in the
        window (→ NO_TAPE; caller keeps the curve-sim price and does NOT reject).
    """
    wallet_bt_int: int = int(wallet_block_time)
    low: int = wallet_bt_int + block_offset
    high: int = wallet_bt_int + block_offset + window_s

    # Determine UTC dates to scan (cross midnight if window spans two days).
    low_dt = datetime.fromtimestamp(low, tz=timezone.utc)
    high_dt = datetime.fromtimestamp(high, tz=timezone.utc)
    date_strs: list[str] = [low_dt.strftime("%Y-%m-%d")]
    next_date_str = (low_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    if high_dt.strftime("%Y-%m-%d") != date_strs[0]:
        date_strs.append(next_date_str)

    reader = LakeReader(base_dir=lake_base_dir)

    for date_str in date_strs:
        for row in reader.iter_rows(date_str=date_str):
            # Only rows for this mint
            if row.get("mint") != mint:
                continue
            row_bt_raw = row.get("block_time")
            if row_bt_raw is None:
                continue
            try:
                row_bt = int(row_bt_raw)
            except (TypeError, ValueError):
                continue

            # Apply WINDOW RULE: must be >= low AND <= high AND != wallet block
            if row_bt < low:
                continue
            if row_bt > high:
                # Lake rows are NOT guaranteed to be sorted by block_time across
                # the whole file (rows from different mints interleave), so we
                # cannot break here.  Continue scanning.
                continue
            # Exclude same-block rows (though the low >= wallet_bt+1 already ensures
            # row_bt != wallet_bt_int for any row in window, this is an explicit guard).
            if row_bt == wallet_bt_int:
                continue

            price_raw = row.get("price")
            if price_raw is None:
                continue
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            logger.debug(
                "[copytrade:reprice] mint=%.8s wallet_bt=%d found row_bt=%d price=%.8g date=%s",
                mint,
                wallet_bt_int,
                row_bt,
                price,
                date_str,
            )
            return price

    # No row found in window
    logger.debug(
        "[copytrade:reprice] mint=%.8s wallet_bt=%d — NO_TAPE in window [%d, %d]",
        mint,
        wallet_bt_int,
        low,
        high,
    )
    return None


def reprice_position(
    position_pk: int,
    *,
    lake_base_dir: str = "lake/firehose",
) -> str:
    """Retrospectively reprice a closed copy-trade position from the lake.

    Looks up the CopytradePosition by pk, reads the lake for the entry and exit
    legs, applies the 15% slippage cap, and updates entry_reprice_status.

    Called from the Celery task ``reprice_copy_fill`` at settlement.

    Entry leg:
      - Find the first lake trade for ``position.mint`` after
        ``int(position.entry_ts.timestamp())`` (using WINDOW RULE).
      - If no row found: status = NO_TAPE (keep curve-sim price, do NOT reject).
      - If repriced price > quote_price * (1 + REPRICE_SLIP_CAP):
        status = ENTRY_REJECTED_TAPE; set realized_pnl_sol = exit_reason_note only.
        (We do NOT blank the realized_pnl here — the position is already closed.
        NO_TAPE and ENTRY_REJECTED_TAPE are informational status flags only.)
      - If within cap: update entry_price to repriced value, recompute PnL,
        status = REPRICED.

    Exit leg:
      - When entry REPRICED: also find the first lake trade after
        ``int(position.exit_ts.timestamp())`` and reprice exit_price.
      - If no exit row found: keep original exit_price (NO_TAPE for exit is OK
        — the entry is already corrected which is the load-bearing change).

    Parameters
    ----------
    position_pk:
        Primary key of the CopytradePosition to reprice.
    lake_base_dir:
        Root of the firehose lake tree.

    Returns
    -------
    str: One of the REPRICE_STATUS_* constants.
    """
    # Import inside function — Celery task body; avoid circular at module level.
    from copytrade.models import CopytradePosition

    try:
        position = CopytradePosition.objects.get(pk=position_pk)
    except CopytradePosition.DoesNotExist:
        logger.warning("[copytrade:reprice] position pk=%d not found", position_pk)
        return REPRICE_STATUS_NO_TAPE

    # Only closed non-rejected positions need repricing
    if position.status != CopytradePosition.STATUS_CLOSED:
        logger.debug("[copytrade:reprice] pk=%d status=%s — skipping (not closed)", position_pk, position.status)
        return REPRICE_STATUS_NO_TAPE

    if position.exit_reason == CopytradePosition.EXIT_ENTRY_REJECTED:
        # Already rejected at live path — nothing to reprice
        logger.debug("[copytrade:reprice] pk=%d EXIT_ENTRY_REJECTED — nothing to reprice", position_pk)
        return REPRICE_STATUS_NO_TAPE

    if position.entry_ts is None:
        logger.warning("[copytrade:reprice] pk=%d entry_ts is None — cannot reprice", position_pk)
        _set_reprice_status(position, REPRICE_STATUS_NO_TAPE)
        return REPRICE_STATUS_NO_TAPE

    # --- ENTRY LEG ---
    wallet_entry_bt = position.entry_ts.timestamp()
    entry_repriced_price = find_next_trade_price(
        position.mint,
        wallet_entry_bt,
        lake_base_dir=lake_base_dir,
    )

    if entry_repriced_price is None:
        # NO_TAPE: keep curve-sim entry price (do NOT reject)
        logger.info(
            "[copytrade:reprice] pk=%d mint=%.8s entry_bt=%.0f — NO_TAPE (keeping curve-sim)",
            position_pk,
            position.mint,
            wallet_entry_bt,
        )
        _set_reprice_status(position, REPRICE_STATUS_NO_TAPE)
        return REPRICE_STATUS_NO_TAPE

    # Check 15% cap vs quote_price (the wallet's price = event.raw["price"])
    # quote_price field was populated by honest_fill telemetry at entry time.
    quote_price: Optional[float] = None
    if position.quote_price is not None:
        try:
            quote_price = float(position.quote_price)
        except (TypeError, ValueError):
            quote_price = None

    if quote_price is not None and quote_price > 0:
        slip = entry_repriced_price / quote_price - 1.0
        if slip > REPRICE_SLIP_CAP:
            logger.info(
                "[copytrade:reprice] pk=%d mint=%.8s entry_repriced=%.8g quote=%.8g "
                "slip=%.4f > cap=%.2f — ENTRY_REJECTED_TAPE",
                position_pk,
                position.mint,
                entry_repriced_price,
                quote_price,
                slip,
                REPRICE_SLIP_CAP,
            )
            _set_reprice_status(position, REPRICE_STATUS_ENTRY_REJECTED_TAPE)
            return REPRICE_STATUS_ENTRY_REJECTED_TAPE
    # When quote_price unavailable, skip the cap and proceed with repricing
    # (the 15% cap requires the wallet reference; without it, we trust the lake price).

    # --- EXIT LEG (only when entry repriced) ---
    exit_repriced_price: Optional[float] = None
    if position.exit_ts is not None:
        wallet_exit_bt = position.exit_ts.timestamp()
        exit_repriced_price = find_next_trade_price(
            position.mint,
            wallet_exit_bt,
            lake_base_dir=lake_base_dir,
        )
        if exit_repriced_price is None:
            # NO_TAPE on exit: keep original exit_price
            exit_repriced_price = float(position.exit_price or 0.0)
            logger.debug(
                "[copytrade:reprice] pk=%d mint=%.8s exit_bt=%.0f — NO_TAPE for exit (keeping original %.8g)",
                position_pk,
                position.mint,
                wallet_exit_bt,
                exit_repriced_price,
            )

    # --- Recompute PnL with repriced prices ---
    sol_in = float(position.sol_in or 0.0)
    final_exit_price = exit_repriced_price if exit_repriced_price is not None else float(position.exit_price or 0.0)

    if entry_repriced_price > 0 and final_exit_price > 0 and sol_in > 0:
        sol_out = sol_in * (final_exit_price / entry_repriced_price)
        realized_pnl_sol = sol_out - sol_in
        realized_pnl_pct = (realized_pnl_sol / sol_in) * 100.0

        CopytradePosition.objects.filter(pk=position_pk).update(
            entry_price=entry_repriced_price,
            exit_price=final_exit_price,
            sol_out=sol_out,
            realized_pnl_sol=realized_pnl_sol,
            realized_pnl_pct=realized_pnl_pct,
            entry_reprice_status=REPRICE_STATUS_REPRICED,
        )
        position.entry_price = entry_repriced_price
        position.exit_price = final_exit_price
        position.sol_out = sol_out
        position.realized_pnl_sol = realized_pnl_sol
        position.realized_pnl_pct = realized_pnl_pct
        position.entry_reprice_status = REPRICE_STATUS_REPRICED

        logger.info(
            "[copytrade:reprice] pk=%d mint=%.8s REPRICED entry=%.8g exit=%.8g "
            "pnl_sol=%.6f pnl_pct=%.2f%%",
            position_pk,
            position.mint,
            entry_repriced_price,
            final_exit_price,
            realized_pnl_sol,
            realized_pnl_pct,
        )
        return REPRICE_STATUS_REPRICED
    else:
        _set_reprice_status(position, REPRICE_STATUS_NO_TAPE)
        return REPRICE_STATUS_NO_TAPE


def _set_reprice_status(position, status: Optional[str]) -> None:
    """Persist entry_reprice_status to DB without touching PnL fields."""
    from copytrade.models import CopytradePosition
    CopytradePosition.objects.filter(pk=position.pk).update(entry_reprice_status=status)
    position.entry_reprice_status = status


__all__ = [
    "REPRICE_BLOCK_OFFSET",
    "REPRICE_WINDOW_S",
    "REPRICE_SLIP_CAP",
    "REPRICE_STATUS_REPRICED",
    "REPRICE_STATUS_ENTRY_REJECTED_TAPE",
    "REPRICE_STATUS_NO_TAPE",
    "REPRICE_STATUS_PENDING",
    "find_next_trade_price",
    "reprice_position",
]
