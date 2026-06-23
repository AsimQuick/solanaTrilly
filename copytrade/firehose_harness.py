# ---
# module: copytrade.firehose_harness
# sprint: sprint-15
# story: US-81
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: gzip, json, logging, pathlib, collections, typing
# ---
"""LOCAL validation harness over the Jun 20-23 firehose tapes.

This is the Phase-A foundation that every Sprint-15 story imports:

  parse()             -- skip-and-count parser; yields validated row dicts
  label_graduations() -- FREE graduation labeler: cum buy vol_sol >= GRAD_SOL_THRESHOLD
  to_usd()            -- dollar basis: vol_sol x SOL_price (NEVER vol_usd, which is all-zero)

Schema of a yielded row (from the firehose jsonl.gz lake):
    mint         str   -- token mint address
    block_time   int   -- Unix epoch seconds (INTEGER — lake stores int(raw_bt))
    slot         int   -- Solana slot
    signature    str   -- transaction signature
    price        float -- SOL/token (vsol/vtok ratio)
    side         str   -- "buy" | "sell"
    vol          float -- raw vol
    vol_sol      float -- SOL notional (THE authoritative quantity)
    vol_usd      float -- BROKEN: always 0.0 — do NOT use for dollar values
    owner        str   -- wallet address
    phase        str   -- "pre" | "post" (NOTE: ~100% "pre" in these tapes)

TAPE CAVEATS (from MANIFEST.md, verified 2026-06-23):
  - vol_usd is ALL ZERO.  Use to_usd(vol_sol, date) for dollar values.
  - ~2-10%/day are unparseable partial writes (partial lines).  parse() skips and counts.
  - phase is unreliable (~13 post-grad swaps on Jun-22 only; Jun-20/21/23 have zero).
    Use label_graduations() for graduation ground truth, NOT the phase field.
  - Graduation = cumulative BUY vol_sol >= GRAD_SOL_THRESHOLD (= 85 SOL).
"""
from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — all graduation and dollar-basis numbers live HERE (single source)
# ---------------------------------------------------------------------------

#: Cumulative BUY vol_sol threshold for graduation (bonding-curve complete).
#: The lab's curve_frac = pre_sol_in / 85 is calibrated to this same value.
#: Changing this constant changes ALL downstream graduation labels — deliberate.
GRAD_SOL_THRESHOLD: float = 85.0

#: Per-day SOL price table (USD/SOL).  Default ~$84/SOL for this window.
#: Used by to_usd().  NEVER use vol_usd (all-zero in these tapes).
#:
#: Source: MANIFEST.md note "~$84/SOL this window".  If the operator has a
#: better per-day price series, replace this table — it is the SINGLE place.
SOL_PRICE_BY_DATE: dict[str, float] = {
    "2026-06-20": 84.0,
    "2026-06-21": 84.0,
    "2026-06-22": 84.0,
    "2026-06-23": 84.0,
}

#: Fallback SOL price for dates not in SOL_PRICE_BY_DATE.
SOL_PRICE_DEFAULT: float = 84.0


# ---------------------------------------------------------------------------
# Row schema
# ---------------------------------------------------------------------------

@dataclass
class TapeRow:
    """A validated row from the firehose tape."""
    mint: str
    block_time: int
    slot: int
    signature: str
    price: float
    side: str
    vol: float
    vol_sol: float
    vol_usd: float  # NOTE: always 0.0 — here for schema completeness; do NOT use
    owner: str
    phase: str


# ---------------------------------------------------------------------------
# Parser (AC-81.1)
# ---------------------------------------------------------------------------

def parse(
    date_str: str,
    lake_base_dir: str | Path = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    *,
    _bad_line_counter: list[int] | None = None,
) -> tuple[list[TapeRow], int]:
    """Parse a single day's firehose tape.

    Reads ``{lake_base_dir}/dt={date_str}/part-0.jsonl.gz``, yields validated
    TapeRow objects, and SKIPS-AND-COUNTS unparseable partial-write lines.

    A malformed line is NEVER fatal and NEVER silently dropped — the bad-line
    count is always surfaced in the return value AND logged at DEBUG.

    Parameters
    ----------
    date_str:
        Date partition to read (``"YYYY-MM-DD"``).
    lake_base_dir:
        Root of the firehose lake tree.  Default is the local solanatrills lake.
    _bad_line_counter:
        Optional mutable list[int] of length 1.  When provided, the caller can
        observe bad-line counts as parsing progresses (used by tests).

    Returns
    -------
    (rows, bad_count):
        rows      -- list of TapeRow (good lines only)
        bad_count -- count of lines that were skipped due to parse/validation errors
    """
    base = Path(lake_base_dir)
    part_path = base / f"dt={date_str}" / "part-0.jsonl.gz"

    rows: list[TapeRow] = []
    bad_count: int = 0

    if not part_path.exists():
        logger.warning("[firehose_harness] tape not found: %s", part_path)
        return rows, bad_count

    try:
        fh = gzip.open(str(part_path), "rt", encoding="utf-8")
    except (OSError, gzip.BadGzipFile) as exc:
        logger.warning("[firehose_harness] cannot open %s: %s", part_path, exc)
        return rows, bad_count

    try:
        while True:
            try:
                line = fh.readline()
            except (EOFError, OSError, gzip.BadGzipFile) as exc:
                logger.debug("[firehose_harness] truncated tail %s: %s", part_path, exc)
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad_count += 1
                if _bad_line_counter is not None:
                    _bad_line_counter[0] = bad_count
                logger.debug("[firehose_harness] bad json (partial write), skipped")
                continue

            row = _validate_row(obj)
            if row is None:
                bad_count += 1
                if _bad_line_counter is not None:
                    _bad_line_counter[0] = bad_count
                logger.debug("[firehose_harness] invalid row schema, skipped: %s", str(obj)[:80])
                continue

            rows.append(row)
    finally:
        fh.close()

    logger.info(
        "[firehose_harness] parse %s: good=%d bad=%d (%.1f%% bad)",
        date_str, len(rows), bad_count,
        100.0 * bad_count / max(1, len(rows) + bad_count),
    )
    return rows, bad_count


def iter_parse(
    date_str: str,
    lake_base_dir: str | Path = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
) -> tuple[Iterator[TapeRow], "BadLineCounter"]:
    """Streaming variant: yield TapeRow objects one at a time (low-memory path).

    Returns
    -------
    (row_iterator, counter):
        counter.bad is updated as parsing proceeds.  Call list(row_iterator)
        to drain, then inspect counter.bad.
    """
    counter = BadLineCounter()

    def _gen() -> Iterator[TapeRow]:
        base = Path(lake_base_dir)
        part_path = base / f"dt={date_str}" / "part-0.jsonl.gz"

        if not part_path.exists():
            logger.warning("[firehose_harness] tape not found: %s", part_path)
            return

        try:
            fh = gzip.open(str(part_path), "rt", encoding="utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            logger.warning("[firehose_harness] cannot open %s: %s", part_path, exc)
            return

        try:
            while True:
                try:
                    line = fh.readline()
                except (EOFError, OSError, gzip.BadGzipFile) as exc:
                    logger.debug("[firehose_harness] truncated tail: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    counter.bad += 1
                    continue

                row = _validate_row(obj)
                if row is None:
                    counter.bad += 1
                    continue

                yield row
        finally:
            fh.close()

    return _gen(), counter


class BadLineCounter:
    """Mutable bad-line counter for the streaming parser."""
    def __init__(self) -> None:
        self.bad: int = 0


def _validate_row(obj: dict) -> TapeRow | None:
    """Validate a parsed JSON dict against the TapeRow schema.

    Returns None (bad line) if any required field is missing or has
    an incompatible type.  Does NOT raise — caller counts and skips.
    """
    try:
        mint = str(obj["mint"])
        block_time = int(obj["block_time"])
        slot = int(obj["slot"])
        signature = str(obj["signature"])
        price = float(obj["price"])
        side = str(obj["side"])
        vol = float(obj["vol"])
        vol_sol = float(obj["vol_sol"])
        vol_usd = float(obj.get("vol_usd", 0.0))  # always 0 — present for schema completeness
        owner = str(obj["owner"])
        phase = str(obj.get("phase", "pre"))
    except (KeyError, TypeError, ValueError):
        return None

    # Sanity guards
    if not mint or not owner or not signature:
        return None
    if price < 0 or vol_sol < 0:
        return None
    if side not in ("buy", "sell"):
        return None  # malformed side field

    return TapeRow(
        mint=mint,
        block_time=block_time,
        slot=slot,
        signature=signature,
        price=price,
        side=side,
        vol=vol,
        vol_sol=vol_sol,
        vol_usd=vol_usd,
        owner=owner,
        phase=phase,
    )


# ---------------------------------------------------------------------------
# Graduation labeler (AC-81.2)
# ---------------------------------------------------------------------------

@dataclass
class GradLabel:
    """Result of the graduation labeler for a single mint."""
    mint: str
    graduated: bool
    grad_block_time: int | None  # block_time of the crossing row (None if not graduated)
    cum_buy_vol_sol: float        # total cumulative buy vol_sol seen (may exceed threshold)


def label_graduations(
    rows: list[TapeRow] | None = None,
    *,
    date_str: str | None = None,
    lake_base_dir: str | Path = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
    threshold: float = GRAD_SOL_THRESHOLD,
) -> dict[str, GradLabel]:
    """Label each mint GRADUATED when cumulative BUY vol_sol first crosses threshold.

    This is the ZERO-CREDIT, FULL-POPULATION graduation ground truth for the
    Jun 20-23 firehose window.  It does NOT rely on the ``phase`` field (unreliable)
    or any external API.

    The firehose is bonding-curve-only (phase ~100% "pre").  A token "graduates"
    (completes the bonding curve and migrates to PumpSwap) when approximately 85 SOL
    of cumulative BUY vol_sol is deposited.  Post-grad rows are NOT captured here
    (only 13 on Jun-22), so graduation OUTCOME = this label, not a post-grad tape.

    Parameters
    ----------
    rows:
        Pre-loaded list of TapeRow (e.g. from parse()).  If None, date_str must be
        provided and the tape is read here.
    date_str:
        Date to parse when rows is None.  Ignored when rows is provided.
    lake_base_dir:
        Lake root (used only when rows is None).
    threshold:
        Cumulative buy vol_sol threshold.  Default = GRAD_SOL_THRESHOLD = 85 SOL.
        This is a named constant — the single source of truth for the graduation label.

    Returns
    -------
    dict mapping mint -> GradLabel (for every mint seen in the input rows).
    """
    if rows is None:
        if date_str is None:
            raise ValueError("Either rows or date_str must be provided")
        rows, _ = parse(date_str, lake_base_dir)

    # Per-mint cumulative buy vol_sol and first-crossing block_time.
    cum_buy: dict[str, float] = {}
    grad_bt: dict[str, int] = {}        # mint -> block_time of crossing row

    for row in rows:
        if row.side != "buy":
            continue
        prev = cum_buy.get(row.mint, 0.0)
        new_cum = prev + row.vol_sol
        cum_buy[row.mint] = new_cum

        # Record the first crossing block_time (the exact row that crossed)
        if prev < threshold <= new_cum and row.mint not in grad_bt:
            grad_bt[row.mint] = row.block_time

    # Build label for every mint seen (including non-graduates)
    all_mints: set[str] = {row.mint for row in rows}
    result: dict[str, GradLabel] = {}
    for mint in all_mints:
        cum = cum_buy.get(mint, 0.0)
        bt = grad_bt.get(mint)
        result[mint] = GradLabel(
            mint=mint,
            graduated=bt is not None,
            grad_block_time=bt,
            cum_buy_vol_sol=cum,
        )

    graduated_count = sum(1 for lbl in result.values() if lbl.graduated)
    logger.info(
        "[firehose_harness] label_graduations: %d mints, %d graduated (threshold=%.0f SOL)",
        len(result), graduated_count, threshold,
    )
    return result


# ---------------------------------------------------------------------------
# Dollar basis (AC-81.3)
# ---------------------------------------------------------------------------

def sol_price_for_date(date_str: str) -> float:
    """Return the pinned SOL/USD price for a given date.

    All dollar values in the harness and consumers MUST derive from:
        dollars = vol_sol * sol_price_for_date(date_str)

    vol_usd is ALL ZERO in these tapes and is NEVER used for dollar values.
    The price is config-driven — update SOL_PRICE_BY_DATE to change it.
    """
    return SOL_PRICE_BY_DATE.get(date_str, SOL_PRICE_DEFAULT)


def to_usd(vol_sol: float, date_str: str) -> float:
    """Convert a SOL quantity to USD using the pinned per-day SOL price.

    This is THE dollar-basis function for all Sprint-15 consumers.
    Do NOT read row.vol_usd — it is always 0.0.

    Parameters
    ----------
    vol_sol:
        SOL quantity to convert.
    date_str:
        Date of the transaction (``"YYYY-MM-DD"``).  Used to look up the
        per-day SOL price from SOL_PRICE_BY_DATE.

    Returns
    -------
    float: USD value = vol_sol * SOL_price.
    """
    return vol_sol * sol_price_for_date(date_str)


def to_usd_with_price(vol_sol: float, sol_price: float) -> float:
    """Convert SOL to USD with an explicit SOL price (for callers that already have it).

    This variant is used when the caller has already resolved the SOL price
    (e.g. from a position's stored sol_usd field) and doesn't have the date.
    """
    return vol_sol * sol_price


# ---------------------------------------------------------------------------
# Multi-day parser (convenience for harness consumers)
# ---------------------------------------------------------------------------

def parse_window(
    date_strs: list[str],
    lake_base_dir: str | Path = "/Users/asim/NoIcloud/solanatrills/lake/firehose",
) -> tuple[list[TapeRow], dict[str, int]]:
    """Parse multiple days and return all rows + per-day bad-line counts.

    Returns
    -------
    (all_rows, bad_per_day):
        all_rows    -- flat list of TapeRow across all days (ordered day-then-file)
        bad_per_day -- dict mapping date_str -> bad_count for each day
    """
    all_rows: list[TapeRow] = []
    bad_per_day: dict[str, int] = {}
    for ds in date_strs:
        rows, bad = parse(ds, lake_base_dir)
        all_rows.extend(rows)
        bad_per_day[ds] = bad
    return all_rows, bad_per_day


__all__ = [
    # Constants
    "GRAD_SOL_THRESHOLD",
    "SOL_PRICE_BY_DATE",
    "SOL_PRICE_DEFAULT",
    # Types
    "TapeRow",
    "GradLabel",
    "BadLineCounter",
    # API
    "parse",
    "iter_parse",
    "label_graduations",
    "sol_price_for_date",
    "to_usd",
    "to_usd_with_price",
    "parse_window",
]
