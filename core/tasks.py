# ---
# module: core.tasks
# sprint: sprint-7
# story: US-1 AC-1.5, US-16 AC-16.2, US-31 AC-31.1, AC-31.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: celery, core.detection.birdeye_sweep, core.feature_builder, core.models
# ---
"""Core Celery tasks — autodiscovered by the Celery worker on startup."""
from celery import shared_task


@shared_task(name="core.tasks.add")
def add(x, y):
    """Return the sum of x and y. Primary sample task for AC-1.5 verification."""
    return x + y


@shared_task(name="core.tasks.ping")
def ping():
    """Health-check sample task — returns 'pong'."""
    return "pong"


@shared_task(name="core.tasks.birdeye_graduation_sweep")
def birdeye_graduation_sweep():
    """Third-belt periodic sweep: reconcile missed graduations via Birdeye REST (AC-16.2).

    Celery-beat runs this every 5 minutes (CELERY_BEAT_SCHEDULE in config/settings.py).
    Fetches recently graduated tokens from the Birdeye REST API and reconciles any
    absent from the local tokens table.  Idempotent: duplicate events for the same
    mint produce no additional rows (get_or_create semantics).

    Returns the count of newly-created Token rows.
    """
    from core.detection.birdeye_sweep import (
        fetch_birdeye_recent_graduations,
        reconcile_graduation_events,
    )

    events = fetch_birdeye_recent_graduations()
    return reconcile_graduation_events(events)


@shared_task(name="core.tasks.build_features")
def build_features(
    feature_set_id, mint_cohort, label_def, lake_base_dir=None, output_path=None
):
    """Feature Builder: extract features for a mint cohort from the lake (AC-31.1, PRD §6.5).

    Runs the SHARED vendored extractor (US-30 FeatureExtractor) over the lake for each
    mint in the cohort, producing a CSV export.  This task MUST run on the dedicated
    celery-worker container — never on web/gunicorn (#289 lesson).

    Args:
        feature_set_id: PK of the FeatureSet to use.
        mint_cohort: List of mint address strings.
        label_def: Label definition dict (for AC-31.2/31.3; passed through here).
        lake_base_dir: Root of the lake tree (defaults to 'lake/tapes').
        output_path: Destination CSV path (defaults to /tmp/features_<id>.csv).

    Returns:
        {"path": str, "row_count": int}
    """
    from core.feature_builder import build_features_core
    from core.models import FeatureSet

    if lake_base_dir is None:
        lake_base_dir = "lake/tapes"
    if output_path is None:
        output_path = f"/tmp/features_{feature_set_id}.csv"

    fs = FeatureSet.objects.get(pk=feature_set_id)
    return build_features_core(
        feature_set=fs,
        mint_cohort=mint_cohort,
        label_def=label_def,
        lake_base_dir=lake_base_dir,
        output_path=output_path,
    )
