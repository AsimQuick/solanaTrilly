# ---
# module: core.export_api
# sprint: sprint-11
# story: US-57 AC-57.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: djangorestframework, core.tasks, core.models, core.resolver
# ---
"""Feature Builder UI export endpoint — trigger the US-31 §6.5 labeled export via DRF.

Single endpoint: POST /api/export/trigger/

Reads export parameters from the active PipelineConfig (config-driven, Principle #1):
  - feature_set_id  → active PipelineConfig.feature_set_id
  - label_def       → active PipelineConfigSchema.outcome.label_def
  - dataset_id      → derived from feature_set_id (unique per version)

Dispatches core.tasks.build_features (the existing US-31 Celery task) via .delay() to
the celery-worker container — NEVER inline on web/gunicorn (#289 lesson, Principle #2).
No new export math; the task re-derives from raw lake via the shared US-30 extractor.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["POST"])
@permission_classes([AllowAny])
def feature_export_trigger_view(request):
    """Dispatch the §6.5 one-click labeled export to the Celery worker (US-31 task).

    Reads feature_set_id and label_def from the active PipelineConfig (Principle #1 —
    config-driven, never literals in view code).  Dispatches build_features.delay()
    to the celery-worker container — NEVER runs the export inline on web/gunicorn
    (the #289 lesson; raw = immutable, Principle #2).

    Request body (JSON, optional):
        mint_cohort: list[str]  mint address strings to include.  When absent,
                                all distinctly annotated mints from the Annotation
                                table are used (mirrors export_annotations default).

    Returns (JSON):
        task_id     — Celery async result ID for the dispatched task
        feature_set_id — config-derived ID (read from active PipelineConfig)
        dataset_id  — unique per-version identifier (derived from feature_set_id)
        status      — "queued" (task is on the Celery queue, not yet complete)

    HTTP 400 when no active PipelineConfig has a feature_set_id set.
    """
    from core.models import Annotation, PipelineConfig
    from core.resolver import get_active_config
    from core.tasks import build_features

    # Config-driven (Principle #1): read feature_set_id from the active PipelineConfig row
    active_row = PipelineConfig.objects.filter(is_active=True).first()
    feature_set_id = active_row.feature_set_id if active_row is not None else None

    if feature_set_id is None:
        return Response(
            {
                "error": (
                    "No active PipelineConfig with feature_set_id configured. "
                    "Set feature_set_id on the active config before triggering export."
                )
            },
            status=400,
        )

    # Config-driven (Principle #1): read label_def from the active config's outcome section
    config = get_active_config()
    label_def = config.outcome.label_def if config is not None else {}

    # dataset_id is config-derived: unique per feature_set version (never a literal)
    dataset_id = f"features_{feature_set_id}"

    # mint_cohort: from request body, or fall back to all distinctly annotated mints
    body = request.data or {}
    mint_cohort = body.get("mint_cohort") or list(
        Annotation.objects.values_list("mint", flat=True).distinct().order_by()
    )

    # Dispatch to celery-worker via .delay() — NEVER inline on web/gunicorn (#289)
    task = build_features.delay(
        feature_set_id=feature_set_id,
        mint_cohort=list(mint_cohort),
        label_def=label_def,
    )

    return Response(
        {
            "task_id": task.id,
            "feature_set_id": feature_set_id,
            "dataset_id": dataset_id,
            "status": "queued",
        }
    )
