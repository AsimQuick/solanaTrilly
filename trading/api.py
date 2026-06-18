# ---
# module: trading.api
# sprint: sprint-13
# story: US-69 AC-69.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: djangorestframework, trading.models
# ---
"""DRF API for the Live Positions dashboard board (PRD §13.2#1, AC-69.2).

Surfaces the shared US-64 Position rows for BOTH source=model and
source=copytrade (replay-sandbox + observe/paper).

GET endpoints:
  /api/trading/positions/open/    — open positions (status PAPER or OPEN,
                                    closed_at IS NULL); entry_price from the
                                    Position row — NOT a live Birdeye call
                                    (zero firehose, no unrealized PnL, no
                                    time-held)
  /api/trading/positions/closed/  — closed positions (closed_at IS NOT NULL);
                                    exit_trigger + realized PnL fields

No new PnL math — reads Position rows only (Principle #2).
Zero firehose — no live price source contacted.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from trading.models import Position

# ---------------------------------------------------------------------------
# GET — open positions
# ---------------------------------------------------------------------------

_OPEN_FIELDS = (
    "id",
    "mint",
    "source",
    "mode",
    "status",
    "entry_ts",
    "entry_price",
    "size_sol",
)


@api_view(["GET"])
@permission_classes([AllowAny])
def positions_open_view(request):
    """Return all open/paper positions for both source=model and source=copytrade.

    Entry price is read directly from the Position row — no live Birdeye call,
    no unrealized PnL, no time-held (zero firehose constraint, AC-69.2).
    Returns PAPER and OPEN rows with closed_at IS NULL (not yet settled).

    Optional query param:
      source (str) — filter by 'model' or 'copytrade'; omit for both.
    """
    qs = Position.objects.filter(closed_at__isnull=True).order_by("entry_ts")
    source = request.query_params.get("source")
    if source in (Position.SOURCE_MODEL, Position.SOURCE_COPYTRADE):
        qs = qs.filter(source=source)

    rows = list(qs.values(*_OPEN_FIELDS))
    return Response({"count": len(rows), "positions": rows})


# ---------------------------------------------------------------------------
# GET — closed positions
# ---------------------------------------------------------------------------

_CLOSED_FIELDS = (
    "id",
    "mint",
    "source",
    "mode",
    "status",
    "entry_ts",
    "entry_price",
    "size_sol",
    "exit_ts",
    "exit_price",
    "exit_trigger",
    "realized_pnl_sol",
    "realized_pnl_pct",
    "peak_price",
    "closed_at",
)


@api_view(["GET"])
@permission_classes([AllowAny])
def positions_closed_view(request):
    """Return all closed/settled positions for both source=model and source=copytrade.

    closed_at IS NOT NULL is the sentinel for settled/closed (AC-66.3).
    Ordered most-recently-closed first.

    Optional query params:
      source (str) — filter by 'model' or 'copytrade'; omit for both.
      limit  (int) — max rows, default 50, max 200.
    """
    qs = Position.objects.filter(closed_at__isnull=False).order_by("-closed_at")
    source = request.query_params.get("source")
    if source in (Position.SOURCE_MODEL, Position.SOURCE_COPYTRADE):
        qs = qs.filter(source=source)

    try:
        limit = min(int(request.query_params.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    rows = list(qs[:limit].values(*_CLOSED_FIELDS))
    return Response({"count": len(rows), "positions": rows})
