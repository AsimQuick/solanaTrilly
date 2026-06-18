# ---
# module: copytrade.tasks
# sprint: sprint-12
# story: US-63 AC-63.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: celery, copytrade.export_builder
# ---
"""Copytrade Celery tasks — autodiscovered by the Celery worker on startup."""
from celery import shared_task


@shared_task(name="copytrade.tasks.export_copytrade_positions")
def export_copytrade_positions(
    cohort_id,
    output_path=None,
    *,
    dataset_id="copytrade_export",
):
    """Export all CopytradePosition rows for a cohort to CSV + MANIFEST (AC-63.3, SPEC §11).

    Runs on the dedicated celery-worker container — NEVER on web/gunicorn (#289).
    Reuses the EXISTING §6.5 export channel pattern (trigger via .delay(),
    result polled via /api/export/result/<task_id>/).

    Includes BOTH open and closed positions so research can compare live results
    to the offline precision baseline.

    Args:
        cohort_id:   Cohort to export.
        output_path: Destination CSV path.  Defaults to
                     /tmp/copytrade_export_<cohort_id>.csv.
        dataset_id:  Unique identifier for the MANIFEST dataset_id field.
                     Defaults to 'copytrade_export'.

    Returns:
        {"path": str, "manifest": dict, "manifest_path": str, "row_count": int}
    """
    from copytrade.export_builder import build_copytrade_export

    if output_path is None:
        safe_id = cohort_id.replace("/", "_").replace(" ", "_")
        output_path = f"/tmp/copytrade_export_{safe_id}.csv"

    return build_copytrade_export(cohort_id, output_path, dataset_id=dataset_id)
