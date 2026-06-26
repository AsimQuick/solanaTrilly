# ---
# module: core.pipeline_control_api
# sprint: sprint-15
# story: US-79 (operator dashboard control), US-96 (shared firehose freshness gate)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: djangorestframework, core.models, core.firehose.shared_tape
# ---
"""Operator control API for the live inference pipeline (Start/Stop from the UI).

The firehose daemon (run_firehose) runs as a persistent, gated service: while
``PipelineState.firehose_active`` is False it idles (no Birdeye/Helius subscriptions,
no spend); flip it True and it begins detect -> collect -> score -> paper.  These
endpoints are the dashboard's Start/Stop for that engine — so the operator never
touches a shell.

Endpoints:
  GET  /api/control/pipeline/   — current flags + live counts (status board)
  POST /api/control/inference/  — {"on": bool} start/stop inference (firehose+scoring)

SAFETY: inference start sets ``firehose_active`` AND ``scoring_enabled`` only.  It
NEVER touches ``trading_enabled`` (the real-capital gate stays where the operator
left it — observe/paper).  The live-capital toggle is a separate, explicitly-gated
endpoint (Phase 2).
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _pipeline_state_dict():
    """Return the current PipelineState flags + live counts as a plain dict."""
    from core.models import PipelineState, Prediction, Token

    state = PipelineState.get()
    counts = {
        "tokens": Token.objects.count(),
        "predictions": Prediction.objects.count(),
    }
    # Position counts are best-effort (trading app); never let a miss break status.
    try:
        from trading.models import Position

        counts["model_positions_open"] = Position.objects.filter(
            source="model", status__in=["PAPER", "OPEN"]
        ).count()
        counts["model_positions_closed"] = Position.objects.filter(
            source="model", status="CLOSED"
        ).count()
    except Exception:  # noqa: BLE001
        pass

    return {
        "firehose_active": bool(state.firehose_active),
        "scoring_enabled": bool(state.scoring_enabled),
        "trading_enabled": bool(state.trading_enabled),
        "inference_on": bool(state.firehose_active and state.scoring_enabled),
        "counts": counts,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def pipeline_status_view(request):
    """GET /api/control/pipeline/ — live control-flag status + counts for the dashboard."""
    return Response(_pipeline_state_dict())


@api_view(["POST"])
@permission_classes([AllowAny])
def inference_control_view(request):
    """POST /api/control/inference/ — start/stop the inference engine.

    Body: {"on": true|false}

    on=true  -> firehose_active=True  + scoring_enabled=True (detect+score+paper)
    on=false -> firehose_active=False (the daemon idles; subscriptions dropped)

    NEVER touches trading_enabled (capital safety; paper/observe is preserved).
    """
    body = request.data
    if not isinstance(body, dict) or "on" not in body:
        return Response({"error": "Body must be JSON with an 'on' (bool) field."}, status=400)
    on = body["on"]
    if not isinstance(on, bool):
        return Response({"error": "'on' must be a boolean."}, status=400)

    # US-96 freshness precondition: when enabling inference under TAPE_SOURCE=shared_billy,
    # check that solanaBilly's tape is live BEFORE flipping the flag.
    # If stale → refuse with the verbatim warning; do NOT flip firehose_active.
    if on:
        from django.conf import settings as django_settings
        tape_source = getattr(django_settings, "TAPE_SOURCE", "shared_billy")
        if tape_source == "shared_billy":
            from core.firehose.shared_tape import STALE_WARNING, billy_firehose_is_live
            if not billy_firehose_is_live():
                return Response(
                    {"error": STALE_WARNING},
                    status=409,
                )

    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = on
    # Scoring rides with the inference switch: starting inference enables scoring;
    # stopping it leaves scoring as-is is pointless, so we mirror the flag.
    state.scoring_enabled = on
    state.save()

    return Response(_pipeline_state_dict())
