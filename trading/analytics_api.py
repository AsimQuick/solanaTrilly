# ---
# module: trading.analytics_api
# sprint: sprint-14
# story: US-72 AC-72.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: djangorestframework, trading.models
# ---
"""DRF API for the Calibration & PnL analytics dashboard view (PRD §13.2#4, AC-72.1).

Aggregates CLOSED shared Position rows (closed_at IS NOT NULL) into four analytics:
  1. win_rate_by_score_band   — positions grouped into 0.1-wide score bands; win rate per band
  2. pnl_by_exit_trigger      — realized PnL totals and averages grouped by exit_trigger
  3. scatter_points           — (score, realized_pnl_pct) pairs for scatter plot
  4. calibration_curve        — predicted score bucket mid-point vs actual win rate

Reads Position rows ONLY — both source=model and source=copytrade.
No new PnL/price math (Principle #2).  No live Birdeye call.  Zero firehose.
Every division is H4 zero-guarded (denominator checked before dividing).

Win definition: realized_pnl_pct > 0.
Score bands: 10 uniform buckets [0.0, 0.1), [0.1, 0.2), ..., [0.9, 1.0].
Score=1.0 is clamped into the [0.9, 1.0] bucket.
Positions with score IS NULL are excluded from score-dependent aggregates
(scatter_points, win_rate_by_score_band, calibration_curve) but INCLUDED in
pnl_by_exit_trigger (exit trigger does not require a score).
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from trading.models import Position

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_BAND_COUNT = 10
_BAND_WIDTH = 1.0 / _BAND_COUNT  # 0.1


def _band_label(band_index: int) -> str:
    lo = round(band_index * _BAND_WIDTH, 1)
    hi = round((band_index + 1) * _BAND_WIDTH, 1)
    return f"{lo:.1f}-{hi:.1f}"


def _score_to_band(score: float) -> int:
    """Map a score in [0.0, 1.0] to a band index in [0, _BAND_COUNT-1]."""
    idx = int(score / _BAND_WIDTH)
    return min(idx, _BAND_COUNT - 1)


def _compute_win_rate_by_score_band(rows: list[dict]) -> list[dict]:
    """Group rows with a non-null score into 10 score bands; compute win rate.

    Rows must contain 'score' and 'realized_pnl_pct' keys.
    Only rows where score is not None are included.
    H4 zero-guard: win_rate is 0.0 when count == 0.
    """
    buckets: list[dict] = [
        {"band": _band_label(i), "count": 0, "wins": 0}
        for i in range(_BAND_COUNT)
    ]
    for row in rows:
        score = row.get("score")
        pnl = row.get("realized_pnl_pct")
        if score is None:
            continue
        idx = _score_to_band(score)
        buckets[idx]["count"] += 1
        if pnl is not None and pnl > 0:
            buckets[idx]["wins"] += 1

    result = []
    for b in buckets:
        count = b["count"]
        win_rate = (b["wins"] / count) if count > 0 else 0.0  # H4 zero-guard
        result.append(
            {
                "band": b["band"],
                "count": count,
                "wins": b["wins"],
                "win_rate": win_rate,
            }
        )
    return result


def _compute_pnl_by_exit_trigger(rows: list[dict]) -> list[dict]:
    """Group ALL closed rows by exit_trigger; sum and average realized_pnl_pct.

    Includes rows regardless of source or whether score is null.
    H4 zero-guard: avg_pnl_pct is 0.0 when count == 0.
    Rows with null exit_trigger use the sentinel label 'UNKNOWN'.
    """
    totals: dict[str, dict] = {}
    for row in rows:
        trigger = row.get("exit_trigger") or "UNKNOWN"
        pnl = row.get("realized_pnl_pct") or 0.0
        if trigger not in totals:
            totals[trigger] = {"count": 0, "total_pnl_pct": 0.0}
        totals[trigger]["count"] += 1
        totals[trigger]["total_pnl_pct"] += pnl

    result = []
    for trigger, data in sorted(totals.items()):
        count = data["count"]
        total = data["total_pnl_pct"]
        avg = (total / count) if count > 0 else 0.0  # H4 zero-guard
        result.append(
            {
                "exit_trigger": trigger,
                "count": count,
                "total_pnl_pct": round(total, 6),
                "avg_pnl_pct": round(avg, 6),
            }
        )
    return result


def _compute_scatter_points(rows: list[dict]) -> list[dict]:
    """Return (score, realized_pnl_pct) pairs for each row with a non-null score.

    Only rows where both score and realized_pnl_pct are non-null are included.
    No math performed — values read directly from Position rows (Principle #2).
    """
    points = []
    for row in rows:
        score = row.get("score")
        pnl = row.get("realized_pnl_pct")
        if score is None or pnl is None:
            continue
        points.append({"score": score, "realized_pnl_pct": pnl})
    return points


def _compute_calibration_curve(rows: list[dict]) -> list[dict]:
    """Bucket rows by score band; report bucket_mid (predicted) vs actual win rate.

    Mirrors _compute_win_rate_by_score_band but adds a bucket_mid field for the
    calibration curve (predicted score vs actual win rate).
    H4 zero-guard: actual_win_rate is 0.0 when count == 0.
    """
    buckets: list[dict] = [
        {
            "bucket": _band_label(i),
            "bucket_mid": round((i + 0.5) * _BAND_WIDTH, 2),
            "count": 0,
            "wins": 0,
        }
        for i in range(_BAND_COUNT)
    ]
    for row in rows:
        score = row.get("score")
        pnl = row.get("realized_pnl_pct")
        if score is None:
            continue
        idx = _score_to_band(score)
        buckets[idx]["count"] += 1
        if pnl is not None and pnl > 0:
            buckets[idx]["wins"] += 1

    result = []
    for b in buckets:
        count = b["count"]
        actual_win_rate = (b["wins"] / count) if count > 0 else 0.0  # H4 zero-guard
        result.append(
            {
                "bucket": b["bucket"],
                "bucket_mid": b["bucket_mid"],
                "count": count,
                "wins": b["wins"],
                "actual_win_rate": actual_win_rate,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Fields fetched from the DB — no extra price/PnL fields (Principle #2)
# ---------------------------------------------------------------------------

_ANALYTICS_FIELDS = (
    "id",
    "source",
    "score",
    "exit_trigger",
    "realized_pnl_pct",
)


# ---------------------------------------------------------------------------
# GET — Calibration & PnL analytics
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def calibration_pnl_analytics_view(request):
    """Aggregate CLOSED Position rows into the four §13.2#4 analytics.

    Reads Position rows only — both source=model and source=copytrade.
    No new PnL/price math (Principle #2).  No live Birdeye call.
    Every division is H4 zero-guarded.

    Response shape:
    {
      "total_closed": int,
      "win_rate_by_score_band": [...],
      "pnl_by_exit_trigger": [...],
      "scatter_points": [...],
      "calibration_curve": [...]
    }
    """
    rows = list(
        Position.objects.filter(closed_at__isnull=False).values(*_ANALYTICS_FIELDS)
    )

    return Response(
        {
            "total_closed": len(rows),
            "win_rate_by_score_band": _compute_win_rate_by_score_band(rows),
            "pnl_by_exit_trigger": _compute_pnl_by_exit_trigger(rows),
            "scatter_points": _compute_scatter_points(rows),
            "calibration_curve": _compute_calibration_curve(rows),
        }
    )
