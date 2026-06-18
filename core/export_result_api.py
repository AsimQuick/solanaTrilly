# ---
# module: core.export_result_api
# sprint: sprint-11
# story: US-57 AC-57.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: djangorestframework, celery
# ---
"""Feature Builder UI export result endpoint — read back the MANIFEST from a completed export task.

Single endpoint: GET /api/export/result/<task_id>/

Reads the Celery task result for the given task_id and returns the manifest dict
produced by the US-31 build_features task (build_features_core).  This view is a
pure read-through — no export math lives here; all logic is in the US-31 task.

State mapping:
  PENDING  → "queued"
  STARTED  → "running"
  RETRY    → "running"
  SUCCESS  → "complete"  (manifest and row_count are populated)
  FAILURE  → "failed"    (error string is populated)

Response shape (complete):
  {"task_id": str, "status": "complete", "manifest": {...}, "row_count": int}

Response shape (other states):
  {"task_id": str, "status": str, "manifest": null, "error": null}
"""
from __future__ import annotations

import celery.result
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

_STATE_MAP: dict[str, str] = {
    "PENDING": "queued",
    "STARTED": "running",
    "RETRY": "running",
    "SUCCESS": "complete",
    "FAILURE": "failed",
}


@api_view(["GET"])
@permission_classes([AllowAny])
def export_result_view(request, task_id: str):
    """Return the export result MANIFEST for the given Celery task_id.

    Reads the task result from the Celery result backend (no DB required for
    most states — the result is stored in Redis by the celery-worker).

    No re-implementation of export math: the manifest is read directly from
    AsyncResult.result["manifest"] as stored by build_features_core.

    Args:
        task_id: The Celery task ID returned by POST /api/export/trigger/.

    Returns:
        200 JSON response with status, manifest (or null), row_count (or null),
        and error (or null).
    """
    ar = celery.result.AsyncResult(task_id)
    celery_state = ar.state  # PENDING, STARTED, RETRY, SUCCESS, FAILURE

    status = _STATE_MAP.get(celery_state, "running")

    if status == "complete":
        task_result = ar.result or {}
        manifest = task_result.get("manifest")
        row_count = task_result.get("row_count")
        return Response(
            {
                "task_id": task_id,
                "status": "complete",
                "manifest": manifest,
                "row_count": row_count,
                "error": None,
            }
        )

    if status == "failed":
        exc = ar.result
        error_str = str(exc) if exc is not None else "task failed"
        return Response(
            {
                "task_id": task_id,
                "status": "failed",
                "manifest": None,
                "row_count": None,
                "error": error_str,
            }
        )

    # queued or running
    return Response(
        {
            "task_id": task_id,
            "status": status,
            "manifest": None,
            "row_count": None,
            "error": None,
        }
    )
