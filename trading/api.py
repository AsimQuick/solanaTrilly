# ---
# module: trading.api
# sprint: sprint-13, sprint-15
# story: US-69 AC-69.2, US-90 AC-90.2
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-26
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
                                    time-held).
                                    US-90 AC-90.2: paginated (page + page_size).
  /api/trading/positions/closed/  — closed positions (closed_at IS NOT NULL);
                                    exit_trigger + realized PnL fields.
                                    US-90 AC-90.2: paginated (page + page_size).

No new PnL math — reads Position rows only (Principle #2).
Zero firehose — no live price source contacted.

US-90 AC-90.2 PAGINATION:
  Both endpoints accept:
    page      (int, 1-indexed, default 1)
    page_size (int, default 25, max 100)
  Response includes pagination metadata:
    count     — total rows matching filters (before pagination)
    page      — current page number (1-indexed)
    page_size — rows per page
    total_pages — total pages for this query
    positions — the current page of rows
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from trading.models import Position

# ---------------------------------------------------------------------------
# Pagination defaults (US-90 AC-90.2)
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100

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


def _parse_pagination(query_params):
    """Parse page + page_size query params with safe defaults."""
    try:
        page = max(1, int(query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max(1, int(query_params.get("page_size", _DEFAULT_PAGE_SIZE))), _MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        page_size = _DEFAULT_PAGE_SIZE
    return page, page_size


@api_view(["GET"])
@permission_classes([AllowAny])
def positions_open_view(request):
    """Return open/paper positions for both source=model and source=copytrade.

    Entry price is read directly from the Position row — no live Birdeye call,
    no unrealized PnL, no time-held (zero firehose constraint, AC-69.2).
    Returns PAPER and OPEN rows with closed_at IS NULL (not yet settled).

    US-90 AC-90.2: paginated response so many tokens do not render unbounded.

    Optional query params:
      source    (str) — filter by 'model' or 'copytrade'; omit for both.
      page      (int) — 1-indexed page number (default 1).
      page_size (int) — rows per page (default 25, max 100).
    """
    qs = Position.objects.filter(closed_at__isnull=True).order_by("entry_ts")
    source = request.query_params.get("source")
    if source in (Position.SOURCE_MODEL, Position.SOURCE_COPYTRADE):
        qs = qs.filter(source=source)

    total = qs.count()
    page, page_size = _parse_pagination(request.query_params)
    offset = (page - 1) * page_size
    import math
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    rows = list(qs[offset:offset + page_size].values(*_OPEN_FIELDS))
    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "positions": rows,
    })


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
    """Return closed/settled positions for both source=model and source=copytrade.

    closed_at IS NOT NULL is the sentinel for settled/closed (AC-66.3).
    Ordered most-recently-closed first.

    US-90 AC-90.2: paginated response.

    Optional query params:
      source    (str) — filter by 'model' or 'copytrade'; omit for both.
      page      (int) — 1-indexed page number (default 1).
      page_size (int) — rows per page (default 25, max 100).
      limit     (int) — DEPRECATED: legacy single-page limit; when present,
                        overrides page_size for backward compat (max 200).
    """
    qs = Position.objects.filter(closed_at__isnull=False).order_by("-closed_at")
    source = request.query_params.get("source")
    if source in (Position.SOURCE_MODEL, Position.SOURCE_COPYTRADE):
        qs = qs.filter(source=source)

    total = qs.count()

    # Backward-compat: if legacy ?limit= param present, use it as page_size.
    if "limit" in request.query_params:
        try:
            legacy_limit = min(int(request.query_params["limit"]), 200)
        except (TypeError, ValueError):
            legacy_limit = 50
        rows = list(qs[:legacy_limit].values(*_CLOSED_FIELDS))
        return Response({
            "count": total,
            "page": 1,
            "page_size": legacy_limit,
            "total_pages": 1,
            "positions": rows,
        })

    import math
    page, page_size = _parse_pagination(request.query_params)
    offset = (page - 1) * page_size
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    rows = list(qs[offset:offset + page_size].values(*_CLOSED_FIELDS))
    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "positions": rows,
    })
