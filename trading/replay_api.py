# ---
# module: trading.replay_api
# sprint: sprint-14
# story: US-73 AC-73.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: djangorestframework, trading.models, core.models, core.dashboard.candle_api
# ---
"""DRF API for the Replay viewer position-open/close overlay (PRD §13.2#5, AC-73.1).

Serves a selected replay run_id's sandbox Positions from the ISOLATED sandbox table
(trading_replay_positions, US-67) PLUS the tape/candle basis for the overlay built
from the existing recorded Swap rows (core.models.Swap, 'swaps' table).

Principle #2 — no new candle math:
  Candles are derived via the existing core.dashboard.candle_api.build_candles()
  function over existing Swap rows (the recorded tape).  No new price derivation,
  no new candle logic.

Principle #2 — no live source:
  All data is read from the DB only (trading_replay_positions + swaps).
  Zero firehose: no Birdeye, no Helius, no RPC calls.

Isolation invariant (§11.3):
  Positions are queried exclusively from trading_replay_positions (ReplayPosition
  model, db_table='trading_replay_positions').  The live trading_positions table
  (Position model, db_table='trading_positions') is NEVER touched by this view.

URL: GET /api/trading/replay/overlay/?run_id=<replay_run_id>

Response shape:
{
  "run_id": str,
  "total_positions": int,
  "positions": [
    {
      "id": int,
      "mint": str,
      "score": float,
      "enterable": bool,
      "unentered_reason": str | null,
      "entry_ts": str | null,        -- ISO-8601 (null when enterable=False)
      "entry_price": float | null,   -- null when enterable=False
      "exit_ts": str | null,         -- ISO-8601 (null when enterable=False)
      "exit_price": float | null,    -- null when enterable=False
      "exit_trigger": str | null,    -- e.g. "STOP_LOSS"
      "realized_pnl_pct": float | null,
      "peak_pct": float | null,
      "held_s": float | null,
      "flow_usd": float | null,
      "size_sol": float
    }, ...
  ],
  "candles_by_mint": {
    "<mint>": [
      {"t": int, "open": float, "high": float, "low": float,
       "close": float, "vol": float, "interval_s": int}, ...
    ]
  }
}
"""

from datetime import timedelta

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.dashboard.candle_api import build_candles
from core.models import Swap
from trading.models import ReplayPosition

# Candle window size — 15s matches the tape_microstructure bucket granularity used
# by the replay harness; small enough to show intra-minute position dynamics.
_CANDLE_INTERVAL_S: int = 15

# Fields fetched from trading_replay_positions — the full overlay marker payload.
# NOTE: exit_ts is not a stored field on ReplayPosition; it is COMPUTED as
# entry_ts + timedelta(seconds=held_s) and added by _add_exit_ts() below.
_POSITION_FIELDS: tuple = (
    "id",
    "mint",
    "score",
    "enterable",
    "unentered_reason",
    "entry_ts",
    "entry_price",
    "exit_price",
    "exit_trigger",
    "realized_pnl_pct",
    "peak_pct",
    "held_s",
    "flow_usd",
    "size_sol",
)


def _add_exit_ts(positions: list[dict]) -> list[dict]:
    """Compute exit_ts for each position from entry_ts + held_s.

    ReplayPosition stores held_s (duration in seconds) rather than an explicit
    exit_ts DateTimeField.  The overlay needs exit_ts for the close marker, so
    we derive it here — no new price math (Principle #2), just datetime arithmetic.
    Positions where entry_ts or held_s is null (un-enterable) get exit_ts=None.
    """
    result = []
    for pos in positions:
        entry_ts = pos.get("entry_ts")
        held_s = pos.get("held_s")
        if entry_ts is not None and held_s is not None:
            exit_ts = entry_ts + timedelta(seconds=held_s)
        else:
            exit_ts = None
        result.append({**pos, "exit_ts": exit_ts})
    return result

# Fields fetched from swaps — must include all fields _lake_row_to_micro expects:
# mint, block_time, slot, signature, side, price, vol_sol, rel.
_SWAP_FIELDS: tuple = (
    "mint",
    "block_time",
    "slot",
    "signature",
    "side",
    "price",
    "vol_sol",
    "rel",
)


@api_view(["GET"])
@permission_classes([AllowAny])
def replay_overlay_view(request):
    """Serve sandbox positions + tape/candle basis for a replay run_id.

    Reads ONLY from:
      - trading_replay_positions  (isolated sandbox — ReplayPosition model)
      - swaps                     (existing recorded tape — Swap model)
    Never reads from trading_positions (live table).
    Zero firehose — all data from the DB.

    Query parameters:
        run_id (str, required): The replay run identifier.

    Returns HTTP 400 when run_id is absent or blank.
    Returns an empty positions list (and empty candles_by_mint) for an unknown run_id.
    """
    run_id = request.query_params.get("run_id", "").strip()
    if not run_id:
        return Response(
            {"error": "run_id query parameter is required"},
            status=400,
        )

    # 1. Load sandbox positions for this run_id — NEVER the live table.
    #    _add_exit_ts computes exit_ts = entry_ts + held_s for the close marker.
    positions = _add_exit_ts(
        list(
            ReplayPosition.objects.filter(replay_run_id=run_id)
            .order_by("id")
            .values(*_POSITION_FIELDS)
        )
    )

    # 2. Collect mints from sandbox rows for the tape lookup.
    mints = {p["mint"] for p in positions}

    # 3. Build candles from the existing recorded tape (Swap rows).
    #    build_candles() is the existing core.dashboard.candle_api function —
    #    no new candle math (Principle #2).  Swap rows are the DB mirror of the
    #    recorded tape lake.  Empty candle list when no swaps exist for a mint.
    candles_by_mint: dict = {}
    if mints:
        swap_rows = list(
            Swap.objects.filter(mint__in=mints)
            .order_by("block_time", "slot", "signature")
            .values(*_SWAP_FIELDS)
        )
        for mint in mints:
            candles_by_mint[mint] = build_candles(swap_rows, mint, _CANDLE_INTERVAL_S)

    return Response(
        {
            "run_id": run_id,
            "total_positions": len(positions),
            "positions": list(positions),
            "candles_by_mint": candles_by_mint,
        }
    )
