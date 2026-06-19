# ---
# module: copytrade.rejected_entry_settler
# sprint: US-75
# story: US-75 AC-2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: math, logging, copytrade.models
# ---
"""Rejected-entry counterfactual settler (US-75 AC-2).

For each ENTRY_REJECTED position row, reads the recorder tape FORWARD from the
rejection timestamp and writes:

  peak_return_pct  — best (price/quote - 1) in the forward window (fraction)
  rug_outcome      — True if price dropped >=50% from peak in the window
  forward_window_s — actual seconds of tape consumed (bounded)

Design goals
------------
- **Bounded forward window** (§8 P3 / #304): an empty tape writes outcome=None
  immediately — never a perpetual skip or infinite loop.  The window is capped at
  COUNTERFACTUAL_WINDOW_S (default 3600 s = 1 hour).
- **Zero/dust guard** (#405): prices <= 0 or dust-level (<1e-15) are skipped.
- **NaN/None guard** (#358): prices that are None or NaN after coercion are skipped.
- **No external I/O** in the core settler function — the tape is injected as a
  list of (block_time, price) tuples.  The DB-writing wrapper is separate.

Public API
----------
settle_counterfactual(tape, rejection_ts, quote_price, *, window_s) -> dict
    Pure function; no DB writes.  Takes the recorder tape (unsorted OK, will
    be sorted internally), the rejection Unix epoch float, and the quote price
    at rejection.  Returns a dict with peak_return_pct / rug_outcome / forward_window_s.

settle_rejected_entries(cohort_id) -> int
    DB-aware wrapper: queries unsettled ENTRY_REJECTED rows for the given cohort,
    fetches the tape, and bulk-updates the counterfactual fields.  Returns count
    of rows updated.

Tape source
-----------
The tape is the recorder firehose (the copytrade firehose stores every swap by
signer; the settled column can be queried/exported by mint).  The settler is
called with pre-fetched tape rows; in production the caller fetches from the
DB raw lake (RawEvent / TapeStore) or from Parquet fixtures in tests.  The
settler itself is pure — fully testable against a real Parquet fixture (§8 P3).
"""
from __future__ import annotations

import logging
import math
from typing import Sequence, Tuple

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COUNTERFACTUAL_WINDOW_S: int = 3600          # 1 hour forward window (bounded)
RUG_DROP_THRESHOLD: float = 0.50             # 50% drop from peak = rug signal
DUST_PRICE_THRESHOLD: float = 1e-15          # skip dust prices (#405)

# Type alias: (block_time_seconds_float, price_float)
TapeTuple = Tuple[float, float]


# ---------------------------------------------------------------------------
# Pure settler (no DB writes)
# ---------------------------------------------------------------------------


def settle_counterfactual(
    tape: Sequence[TapeTuple],
    rejection_ts: float,
    quote_price: float,
    *,
    window_s: int = COUNTERFACTUAL_WINDOW_S,
) -> dict:
    """Settle the forward counterfactual for one ENTRY_REJECTED row.

    Parameters
    ----------
    tape:
        List of (block_time, price) tuples for the rejected mint.  Will be
        sorted by block_time internally.  May be empty.
    rejection_ts:
        Unix epoch float — the entry_ts of the ENTRY_REJECTED position (when we
        passed on this token).
    quote_price:
        The watched wallet's confirmed fill price (CopytradePosition.quote_price).
        Used as the denominator for peak_return_pct.  Must be > 0.
    window_s:
        Forward window in seconds.  Tape entries with block_time > rejection_ts +
        window_s are ignored (bounded window).

    Returns
    -------
    dict with keys:
        peak_return_pct  : float | None — best (price/quote - 1) in window
        rug_outcome      : bool | None  — price dropped >=50% from peak in window
        forward_window_s : int | None   — actual seconds of tape consumed

    None on all three fields means the tape was empty in the forward window
    (the #304 guard — never a perpetual skip).

    Guards
    ------
    - quote_price <= 0 → returns all None with a warning (#405).
    - Empty tape or no tape entries in [rejection_ts, rejection_ts+window_s]
      → all None immediately (bounded window, #304).
    - Individual price <= 0 or NaN → skipped (#405/#358).
    """
    _NONE = dict(peak_return_pct=None, rug_outcome=None, forward_window_s=None)

    # Guard: invalid quote (#405)
    if not (quote_price > 0) or math.isnan(quote_price):
        logger.warning(
            "[copytrade] settle_counterfactual: invalid quote_price=%r — returning None",
            quote_price,
        )
        return _NONE

    # Sort tape by block_time (deterministic)
    sorted_tape = sorted(tape, key=lambda t: t[0])

    # Filter to forward window: [rejection_ts, rejection_ts + window_s]
    window_end = rejection_ts + window_s
    forward = [
        (bt, px)
        for bt, px in sorted_tape
        if bt >= rejection_ts and bt <= window_end
    ]

    # Bounded window guard (#304): empty tape → outcome=None immediately
    if not forward:
        return _NONE

    # Walk the forward tape
    peak_price: float = quote_price   # start peak at quote (the reference level)
    last_bt: float = rejection_ts

    for bt, px in forward:
        # Guard: dust/zero/NaN price (#405/#358)
        if px is None:
            continue
        px_f = float(px)
        if math.isnan(px_f) or px_f <= DUST_PRICE_THRESHOLD:
            continue
        peak_price = max(peak_price, px_f)
        last_bt = bt

    actual_window_s = int(last_bt - rejection_ts)

    # peak_return_pct = (peak / quote) - 1  (#405: quote_price > 0 already asserted)
    peak_return = peak_price / quote_price - 1.0

    # rug_outcome = price dropped >=50% from peak by the end of the window
    # Use the last valid price in the window as the "end" price
    end_prices = [
        float(px)
        for _, px in forward
        if px is not None and not math.isnan(float(px)) and float(px) > DUST_PRICE_THRESHOLD
    ]
    if not end_prices:
        return _NONE

    end_price = end_prices[-1]
    rug = bool(end_price <= peak_price * (1.0 - RUG_DROP_THRESHOLD))

    return dict(
        peak_return_pct=peak_return,
        rug_outcome=rug,
        forward_window_s=actual_window_s,
    )


# ---------------------------------------------------------------------------
# DB-aware batch settler
# ---------------------------------------------------------------------------


def settle_rejected_entries(
    cohort_id: str,
    *,
    tape_fn: "Callable[[str], list[TapeTuple]]",
    window_s: int = COUNTERFACTUAL_WINDOW_S,
) -> int:
    """Settle the forward counterfactual for all unsettled ENTRY_REJECTED rows.

    Queries ENTRY_REJECTED positions in the given cohort that have NOT yet been
    settled (peak_return_pct IS NULL), fetches each mint's forward tape via
    tape_fn, calls settle_counterfactual, and bulk-updates the DB rows.

    Parameters
    ----------
    cohort_id:
        The active cohort to settle.
    tape_fn:
        Callable ``(mint: str) -> list[TapeTuple]``.  Injected by the caller
        (no I/O in this module).  Returns a list of (block_time, price) tuples
        for the mint from the recorder tape.
    window_s:
        Forward window for the counterfactual (default COUNTERFACTUAL_WINDOW_S).

    Returns
    -------
    Number of rows updated.
    """
    from copytrade.models import CopytradePosition  # lazy — avoid circular at module level

    unsettled = list(
        CopytradePosition.objects.filter(
            cohort_id=cohort_id,
            exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
            peak_return_pct__isnull=True,
        ).values("pk", "mint", "entry_ts", "quote_price")
    )

    updated = 0
    for row in unsettled:
        pk = row["pk"]
        mint = row["mint"]
        entry_ts_dt = row["entry_ts"]
        quote_price = row["quote_price"]

        if entry_ts_dt is None or quote_price is None:
            logger.debug(
                "[copytrade] settle_rejected: skip pk=%s mint=%.8s — missing ts/quote",
                pk,
                mint,
            )
            continue

        # Convert datetime to Unix epoch float
        try:
            rejection_ts = float(entry_ts_dt.timestamp())
        except (AttributeError, OSError, ValueError):
            logger.warning(
                "[copytrade] settle_rejected: pk=%s — could not convert entry_ts to float",
                pk,
            )
            continue

        # Fetch forward tape (injected — no I/O in this module)
        try:
            tape = tape_fn(mint)
        except Exception:  # noqa: BLE001
            logger.exception("[copytrade] settle_rejected: tape_fn failed for mint=%.8s", mint)
            tape = []

        result = settle_counterfactual(
            tape,
            rejection_ts,
            float(quote_price),
            window_s=window_s,
        )

        CopytradePosition.objects.filter(pk=pk).update(
            peak_return_pct=result["peak_return_pct"],
            rug_outcome=result["rug_outcome"],
            forward_window_s=result["forward_window_s"],
        )
        updated += 1
        logger.info(
            "[copytrade] settle_rejected: pk=%s mint=%.8s "
            "peak=%.4f rug=%s window=%ss",
            pk,
            mint,
            result["peak_return_pct"] if result["peak_return_pct"] is not None else float("nan"),
            result["rug_outcome"],
            result["forward_window_s"],
        )

    return updated


# For type hints in the signature above
from typing import Callable  # noqa: E402  (after the function that uses the type)

__all__ = [
    "COUNTERFACTUAL_WINDOW_S",
    "RUG_DROP_THRESHOLD",
    "TapeTuple",
    "settle_counterfactual",
    "settle_rejected_entries",
]
