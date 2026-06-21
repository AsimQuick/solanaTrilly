# ---
# module: copytrade.api
# sprint: sprint-12, epic/copy-paper-fill-repricing
# story: US-63 AC-63.1, AC-63.3, EPIC-copy-paper-fill-repricing
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: djangorestframework, copytrade.models, copytrade.cohort_lifecycle,
#   copytrade.engine_control, copytrade.validators, copytrade.tasks
# ---
"""DRF API for the Copy Trade dashboard tab (SPEC §8, AC-63.1) + export (SPEC §11, AC-63.3).

Surfaces the copytrade_ backends (read) and operator actions:

GET endpoints:
  /api/copytrade/pnl/             — per-wallet PnL rollup (CopytradePnlByWallet)
  /api/copytrade/positions/       — open positions (CopytradePosition status=open)
  /api/copytrade/trades/          — recent closed trades (exit_reason + mode)
  /api/copytrade/summary/         — cohort summary (total trades, win-rate, PnL, since)

POST/action endpoints:
  /api/copytrade/upload/          — JSON upload → US-58 validate → US-62 wipe+load
  /api/copytrade/engine/          — ON/OFF toggle (body: {"engine_on": bool})
  /api/copytrade/mode/            — mode (observe/live; live is P8-gated INERT this sprint)
  /api/copytrade/overrides/       — persist sol_size / take_profit_pct / stop_loss_pct
  /api/copytrade/export/trigger/  — dispatch export task to celery-worker (AC-63.3 / SPEC §11)

No new PnL/price math — reads the copytrade_ rows (Principle #2).
Export runs OFF the celery container (#289 — NEVER web/gunicorn); result polled via
the EXISTING §6.5 result endpoint GET /api/export/result/<task_id>/.
"""

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from copytrade.cohort_lifecycle import replace_cohort
from copytrade.engine_control import set_engine_on
from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)
from copytrade.tasks import export_copytrade_positions
from copytrade.validators import CohortJsonValidationError

# ---------------------------------------------------------------------------
# GET — per-wallet PnL rollup
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def copytrade_pnl_view(request):
    """Return per-wallet PnL rollup for the active cohort.

    Reads CopytradePnlByWallet rows — no new math.
    Ordered by total_pnl_sol descending (best wallets first).
    """
    settings = CopyTradeSettings.get()
    cohort_id = settings.active_cohort_id
    if cohort_id is None:
        return Response({"cohort_id": None, "wallets": []})

    rows = list(
        CopytradePnlByWallet.objects.filter(cohort_id=cohort_id)
        .order_by("-total_pnl_sol")
        .values("address", "n_trades", "win_rate", "total_pnl_sol", "avg_hold_s")
    )
    return Response({"cohort_id": cohort_id, "wallets": rows})


# ---------------------------------------------------------------------------
# GET — open positions
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def copytrade_positions_view(request):
    """Return currently open positions for the active cohort."""
    settings = CopyTradeSettings.get()
    cohort_id = settings.active_cohort_id
    if cohort_id is None:
        return Response({"cohort_id": None, "positions": []})

    rows = list(
        CopytradePosition.objects.filter(
            cohort_id=cohort_id,
            status=CopytradePosition.STATUS_OPEN,
        )
        .order_by("entry_ts")
        .values(
            "id",
            "mint",
            "trigger_wallet",
            "mode",
            "entry_ts",
            "entry_price",
            "sol_in",
        )
    )
    return Response({"cohort_id": cohort_id, "positions": rows})


# ---------------------------------------------------------------------------
# GET — recent trades (closed positions)
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def copytrade_trades_view(request):
    """Return recent closed trades for the active cohort.

    Includes exit_reason (TP/SL/CURVE/TIMER/SETTLE) and mode (observe/live).
    Query param: limit (int, default 50, max 200).
    """
    settings = CopyTradeSettings.get()
    cohort_id = settings.active_cohort_id
    if cohort_id is None:
        return Response({"cohort_id": None, "trades": []})

    try:
        limit = min(int(request.query_params.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    # EPIC-copy-paper-fill-repricing: EXCLUDE ENTRY_REJECTED from trades log.
    # Those rows were never entered (slippage cap), lost nothing, and should not
    # appear in the trade history (they inflate the loss-count).
    rows = list(
        CopytradePosition.objects.filter(
            cohort_id=cohort_id,
            status=CopytradePosition.STATUS_CLOSED,
        )
        .exclude(exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED)
        .order_by("-exit_ts")[:limit]
        .values(
            "id",
            "mint",
            "trigger_wallet",
            "mode",
            "entry_ts",
            "entry_price",
            "sol_in",
            "exit_ts",
            "exit_price",
            "sol_out",
            "exit_reason",
            "realized_pnl_sol",
            "realized_pnl_pct",
            "entry_reprice_status",
        )
    )
    return Response({"cohort_id": cohort_id, "trades": rows})


# ---------------------------------------------------------------------------
# GET — cohort summary
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def copytrade_summary_view(request):
    """Return cohort summary: total trades, win-rate, net PnL SOL, cohort start.

    Also includes engine_on, mode, n_open_positions, n_wallets for the
    dashboard status bar.
    """
    settings = CopyTradeSettings.get()
    cohort_id = settings.active_cohort_id

    if cohort_id is None:
        return Response(
            {
                "cohort_id": None,
                "total_trades": 0,
                "win_rate": 0.0,
                "net_pnl_sol": 0.0,
                "since": None,
                "engine_on": settings.engine_on,
                "mode": settings.mode,
                "n_open_positions": 0,
                "n_wallets": 0,
                # EPIC-copy-paper-fill-repricing fields
                "n_rejected": 0,
                "n_repriced": 0,
                "n_no_tape": 0,
                "n_pending": 0,
                "pnl_is_repriced": False,
            }
        )

    cohort = CopytradeCohort.objects.filter(cohort_id=cohort_id, active=True).first()
    since = cohort.created_at.isoformat() if cohort else None

    # EPIC-copy-paper-fill-repricing: EXCLUDE ENTRY_REJECTED from trade count + win-rate.
    # Those were never entered (slippage cap), lost nothing.  n_rejected is surfaced
    # separately so the dashboard can show the "N signals skipped (slippage)" chip.
    all_closed_qs = CopytradePosition.objects.filter(
        cohort_id=cohort_id,
        status=CopytradePosition.STATUS_CLOSED,
    )

    # Count rejected entries (excluded from trades, returned as n_rejected)
    n_rejected = all_closed_qs.filter(
        exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED,
    ).count()

    # Trades queryset excludes ENTRY_REJECTED
    closed_qs = all_closed_qs.exclude(exit_reason=CopytradePosition.EXIT_ENTRY_REJECTED)
    total_trades = closed_qs.count()
    agg = closed_qs.aggregate(
        net_pnl=Sum("realized_pnl_sol"),
        wins=Count("id", filter=Q(realized_pnl_sol__gt=0)),
    )
    net_pnl_sol = float(agg["net_pnl"] or 0.0)
    wins = agg["wins"] or 0
    win_rate = round(wins / total_trades, 4) if total_trades > 0 else 0.0

    # Reprice trustworthiness counts (from non-rejected closed positions)
    reprice_agg = closed_qs.aggregate(
        n_repriced=Count("id", filter=Q(entry_reprice_status=CopytradePosition.REPRICE_STATUS_REPRICED)),
        n_no_tape=Count("id", filter=Q(entry_reprice_status=CopytradePosition.REPRICE_STATUS_NO_TAPE)),
        n_pending=Count("id", filter=Q(entry_reprice_status__isnull=True)),
    )
    n_repriced = reprice_agg["n_repriced"] or 0
    n_no_tape = reprice_agg["n_no_tape"] or 0
    n_pending = reprice_agg["n_pending"] or 0
    # pnl_is_repriced = True only when ALL non-rejected closed positions are REPRICED
    # (NO_TAPE counts as NOT trusted — curve-sim price kept)
    pnl_is_repriced = (total_trades > 0) and (n_pending == 0) and (n_no_tape == 0)

    n_open = CopytradePosition.objects.filter(
        cohort_id=cohort_id,
        status=CopytradePosition.STATUS_OPEN,
    ).count()
    n_wallets = CopytradeWallet.objects.filter(cohort_id=cohort_id).count()

    return Response(
        {
            "cohort_id": cohort_id,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "net_pnl_sol": net_pnl_sol,
            "since": since,
            "engine_on": settings.engine_on,
            "mode": settings.mode,
            "n_open_positions": n_open,
            "n_wallets": n_wallets,
            # EPIC-copy-paper-fill-repricing fields
            "n_rejected": n_rejected,
            "n_repriced": n_repriced,
            "n_no_tape": n_no_tape,
            "n_pending": n_pending,
            "pnl_is_repriced": pnl_is_repriced,
        }
    )


# ---------------------------------------------------------------------------
# POST — upload cohort JSON → US-58 validate → US-62 wipe+load
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([AllowAny])
def copytrade_upload_view(request):
    """Upload a leaderboard.json cohort → validate (US-58) → wipe+load (US-62).

    Body: the parsed leaderboard.json dict (Content-Type: application/json).
    Triggers the SPEC §6 full REPLACEMENT via replace_cohort().

    Uses timezone.now() as settlement_ts (API boundary is the time source here)
    and 1.0 as settlement_price (paper fill for any open positions that are
    closed-and-purged during the wipe step).
    """
    body = request.data
    if not isinstance(body, dict):
        return Response(
            {"error": "Request body must be a JSON object (leaderboard.json dict)"},
            status=400,
        )

    try:
        cohort = replace_cohort(
            new_cohort_json=body,
            settlement_price=1.0,
            settlement_ts=timezone.now(),
        )
    except CohortJsonValidationError as exc:
        return Response({"error": str(exc)}, status=400)

    return Response(
        {
            "cohort_id": cohort.cohort_id,
            "description": cohort.description,
            "active": cohort.active,
            "status": "loaded",
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# POST — engine ON/OFF
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([AllowAny])
def copytrade_engine_view(request):
    """Toggle the copytrade engine ON or OFF.

    Body: {"engine_on": true|false}

    Gates ONLY the copytrade engine (SPEC §5 isolation). Does NOT touch the
    firehose, graduation feed, or model pipeline.
    """
    body = request.data
    if not isinstance(body, dict) or "engine_on" not in body:
        return Response(
            {"error": "Body must be JSON with 'engine_on' (bool) field"},
            status=400,
        )

    value = body["engine_on"]
    if not isinstance(value, bool):
        return Response({"error": "'engine_on' must be a boolean"}, status=400)

    settings = set_engine_on(value)
    return Response({"engine_on": settings.engine_on})


# ---------------------------------------------------------------------------
# POST — mode override (observe/live; live is P8-gated INERT this sprint)
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([AllowAny])
def copytrade_mode_view(request):
    """Set the copy-trade mode (observe or live).

    Body: {"mode": "observe" | "live"}

    Live mode is P8-gated and INERT this sprint (no PumpSwap execution path
    has been built yet — PRD §10). Attempting mode='live' returns HTTP 400
    with a clear guard message. observe persists to CopyTradeSettings.
    """
    body = request.data
    if not isinstance(body, dict) or "mode" not in body:
        return Response(
            {"error": "Body must be JSON with 'mode' field ('observe' or 'live')"},
            status=400,
        )

    mode = body["mode"]
    if mode not in ("observe", "live"):
        return Response({"error": "mode must be 'observe' or 'live'"}, status=400)

    if mode == "live":
        return Response(
            {
                "error": (
                    "live mode is P8-gated and INERT this sprint — "
                    "the PumpSwap execution path (PRD §10) has not yet been built. "
                    "Flip to live only after P8 lands."
                ),
                "mode_unchanged": True,
            },
            status=400,
        )

    settings = CopyTradeSettings.get()
    settings.mode = mode
    settings.save()
    return Response({"mode": settings.mode})


# ---------------------------------------------------------------------------
# POST — persist SOL size / TP% / SL% overrides to the config store
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([AllowAny])
def copytrade_overrides_view(request):
    """Persist SOL size / TP% / SL% operator overrides to CopyTradeSettings.

    Body (all optional; only provided fields are updated):
        sol_size_per_trade  — float > 0
        take_profit_pct     — float > 0
        stop_loss_pct       — float > 0 and <= 100

    Reads existing settings, applies provided overrides, then saves through
    the Pydantic validation gate (CopyTradeSettings.save() rejects invalid
    bounds — Principle #1 config-driven, write-path-rejection discipline).
    """
    body = request.data
    if not isinstance(body, dict):
        return Response({"error": "Request body must be a JSON object"}, status=400)

    allowed = {"sol_size_per_trade", "take_profit_pct", "stop_loss_pct"}
    unknown = set(body.keys()) - allowed
    if unknown:
        return Response(
            {"error": f"Unknown override fields: {sorted(unknown)}"},
            status=400,
        )

    settings = CopyTradeSettings.get()

    for field in allowed:
        if field in body:
            value = body[field]
            if not isinstance(value, (int, float)):
                return Response(
                    {"error": f"'{field}' must be a number"},
                    status=400,
                )
            setattr(settings, field, float(value))

    try:
        settings.save()
    except Exception as exc:
        return Response({"error": f"Override rejected: {exc}"}, status=400)

    return Response(
        {
            "sol_size_per_trade": settings.sol_size_per_trade,
            "take_profit_pct": settings.take_profit_pct,
            "stop_loss_pct": settings.stop_loss_pct,
        }
    )


# ---------------------------------------------------------------------------
# POST — dispatch copytrade positions export to celery-worker (SPEC §11 / AC-63.3)
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([AllowAny])
def copytrade_export_trigger_view(request):
    """Dispatch the §11 click-to-download copytrade export to the Celery worker (AC-63.3).

    Reuses the EXISTING §6.5 export channel: dispatches
    copytrade.tasks.export_copytrade_positions.delay() to the celery-worker
    container — NEVER runs inline on web/gunicorn (#289 lesson).

    Includes ALL CopytradePosition rows for the active cohort (open + closed)
    so research can compare live results to the offline precision baseline.

    Request body (JSON, optional):
        dataset_id: str  — identifier for the MANIFEST.  Defaults to
                          "copytrade_export_<cohort_id>".

    Returns (JSON):
        task_id    — Celery async result ID; poll via GET /api/export/result/<task_id>/
        cohort_id  — cohort exported
        status     — "queued"

    HTTP 400 when no active cohort is configured.
    """
    settings = CopyTradeSettings.get()
    cohort_id = settings.active_cohort_id

    if cohort_id is None:
        return Response(
            {
                "error": (
                    "No active cohort configured. "
                    "Upload a cohort JSON before triggering an export."
                )
            },
            status=400,
        )

    body = request.data or {}
    safe_id = cohort_id.replace("/", "_").replace(" ", "_")
    dataset_id = body.get("dataset_id") or f"copytrade_export_{safe_id}"

    task = export_copytrade_positions.delay(
        cohort_id=cohort_id,
        dataset_id=dataset_id,
    )

    return Response(
        {
            "task_id": task.id,
            "cohort_id": cohort_id,
            "status": "queued",
        }
    )
