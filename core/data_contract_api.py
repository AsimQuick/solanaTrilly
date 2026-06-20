# ---
# module: core.data_contract_api
# sprint: sprint-14
# story: US-78 AC-78.1, AC-78.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: djangorestframework, core.tasks
# ---
"""US-78 data-contract export trigger — one-click Parquet export for the lab (§10).

Single endpoint: POST /api/export/data-contract/trigger/

Dispatches ``core.tasks.export_data_contract`` via ``.delay()`` to the celery-worker
container — NEVER inline on web/gunicorn (#289 lesson; the export only reads the
immutable lake + Token registry, Principle #2).  Poll the result + per-surface
MANIFESTs back via the existing GET /api/export/result/<task_id>/ endpoint.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

#: Surfaces this endpoint can build today.  predictions_positions is delivered in a
#: follow-up increment (it requires score-breakdown persistence at score-time).
_AVAILABLE_SURFACES = ("swaps", "tokens")


@api_view(["POST"])
@permission_classes([AllowAny])
def data_contract_export_trigger_view(request):
    """Dispatch the US-78 data-contract Parquet export to the Celery worker.

    Request body (JSON, optional):
        surfaces: list[str]  subset of {"swaps", "tokens"} to build.  Defaults to
                  both.  Unknown surface names are rejected (HTTP 400).
        out_dir:  export root override (defaults to the task's /tmp/data_contract).

    Returns (JSON):
        task_id   — Celery async result ID
        surfaces  — the surfaces queued
        out_dir   — export root (or null when the task default is used)
        status    — "queued"
    """
    from core.tasks import export_data_contract

    body = request.data or {}
    surfaces = body.get("surfaces") or list(_AVAILABLE_SURFACES)
    if not isinstance(surfaces, list):
        return Response({"error": "surfaces must be a list of surface names."}, status=400)
    unknown = [s for s in surfaces if s not in _AVAILABLE_SURFACES]
    if unknown:
        return Response(
            {
                "error": (
                    f"unknown surface(s): {unknown}. "
                    f"Available: {list(_AVAILABLE_SURFACES)}."
                )
            },
            status=400,
        )

    out_dir = body.get("out_dir")
    task = export_data_contract.delay(out_dir=out_dir, surfaces=list(surfaces))

    return Response(
        {
            "task_id": task.id,
            "surfaces": list(surfaces),
            "out_dir": out_dir,
            "status": "queued",
        }
    )
