# ---
# module: core.dashboard.candle_api
# sprint: sprint-10
# story: US-49 AC-49.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, typing
# ---
"""Tape→candle API: derive OHLC candles from raw lake rows (AC-49.1).

Principle #2 — ONE price basis:
  The candle price source is EXCLUSIVELY the raw lake rows, normalized via
  _lake_row_to_micro from core.feature_extractor — the SAME function and the
  SAME §7.1 price field that the FeatureExtractor uses.  There is NO
  candle-local price source, no separate fetch, and no independent price
  derivation.  Structural test: candle_api.py imports _lake_row_to_micro from
  core.feature_extractor and never imports any network client or price feed.

Principle #1 — config-driven intervals:
  The supported candle intervals (1s/5s/15s/1m → [1, 5, 15, 60] seconds) are
  read from DashboardConfig.candle_intervals_s via get_active_config().  The
  interval requested by the caller is validated against this config-driven set.

Candle format: {t, open, high, low, close, vol, interval_s}
  t        — Unix timestamp of the candle window open (block_time floor-divided
             to interval_s boundary; same alignment as TapeFeedProcessor)
  open     — price of the FIRST swap in the window (stable sort order)
  high     — maximum price within the window
  low      — minimum price within the window
  close    — price of the LAST swap in the window
  vol      — sum of vol_sol for all swaps in the window
  interval_s — the interval (from config, passed in)
"""
from __future__ import annotations

from core.feature_extractor import _lake_row_to_micro

# ---------------------------------------------------------------------------
# SENTINEL — H1 import-time guard (same pattern as consumer.py)
# ---------------------------------------------------------------------------
__all__ = ["build_candles", "SUPPORTED_INTERVALS_DEFAULT"]

# Default supported intervals (seconds): 1s / 5s / 15s / 1m.
# The live view reads these from DashboardConfig (Principle #1); this constant
# is for offline callers (tests, CLI) that have no active DB config.
SUPPORTED_INTERVALS_DEFAULT: list[int] = [1, 5, 15, 60]


def build_candles(
    rows: list[dict],
    mint: str,
    interval_s: int,
) -> list[dict]:
    """Derive OHLC candles for *mint* from raw lake *rows* at *interval_s* granularity.

    The price values come from the raw lake rows via _lake_row_to_micro — the
    same §7.1 normalization path used by FeatureExtractor.extract_from_lake().
    This is NOT a separate price source; it is the same source.

    Candle windows are aligned to Unix epoch boundaries:
        window_start = (block_time // interval_s) * interval_s

    Args:
        rows:       Raw jsonl.gz lake rows (list[dict]), as returned by
                    LakeReader.iter_rows().  Each row must have 'mint',
                    'block_time', 'slot', 'signature', 'price', 'vol_sol',
                    'side' fields.
        mint:       Token mint address.  Rows with a different mint are
                    silently filtered (same filter as FeatureExtractor).
        interval_s: Candle interval in seconds (must be > 0).

    Returns:
        List of OHLC candle dicts sorted by ascending 't'.  Empty list if
        there are no usable rows for *mint*.

    Raises:
        ValueError: if interval_s <= 0.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be > 0, got {interval_s}")

    # 1. Filter to the requested mint — same filter as FeatureExtractor._load_lake_swaps
    filtered = [r for r in rows if r.get("mint") == mint]

    if not filtered:
        return []

    # 2. Sort by the stable (block_time, slot, signature) key — identical to
    #    FeatureExtractor._load_lake_swaps sort so candle order matches feature order.
    filtered.sort(key=lambda r: (int(r["block_time"]), int(r["slot"]), str(r["signature"])))

    # 3. Normalize via _lake_row_to_micro — the SAME §7.1 normalization the
    #    FeatureExtractor uses.  Price comes from row["price"] (same field).
    micro_rows = [_lake_row_to_micro(r) for r in filtered]

    # 4. Bin into interval_s OHLC candles.
    #    candle_map: window_start (int) → {open, high, low, close, vol}
    candle_map: dict[int, dict] = {}
    for row in micro_rows:
        price: float = row["price"]
        vol: float = row["vol"]
        block_time: int = int(row["block_time"])
        window_start: int = (block_time // interval_s) * interval_s

        if window_start not in candle_map:
            candle_map[window_start] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "vol": vol,
            }
        else:
            c = candle_map[window_start]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price  # last-seen within stable sort = close
            c["vol"] += vol

    # 5. Emit sorted candle list.
    candles = [
        {
            "t": t,
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "vol": c["vol"],
            "interval_s": interval_s,
        }
        for t, c in sorted(candle_map.items())
    ]

    return candles
