# ---
# module: copytrade.tasks
# sprint: sprint-12, epic/copy-paper-fill-repricing
# story: US-63 AC-63.3, EPIC-copy-paper-fill-repricing
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: celery, copytrade.export_builder, copytrade.fill_repricing
# ---
"""Copytrade Celery tasks — autodiscovered by the Celery worker on startup."""
from celery import shared_task


@shared_task(name="copytrade.tasks.reprice_copy_fill", bind=False)
def reprice_copy_fill(position_pk: int, lake_base_dir: str = "lake/firehose") -> dict:
    """Retrospectively reprice a closed copy-trade position from the firehose lake.

    Dispatched by position_manager.close_position (wrapped in bare except so
    close_position NEVER crashes due to this dispatch).  Runs on celery-worker
    which mounts the lake volume (docker-compose.staging.yml).

    EPIC-copy-paper-fill-repricing: see copytrade/fill_repricing.py for the
    WINDOW RULE, price-unit basis, and slippage cap logic.

    Args:
        position_pk:  CopytradePosition primary key.
        lake_base_dir: Lake root (default ``lake/firehose``).

    Returns:
        {"pk": int, "status": str}  where status is one of the REPRICE_STATUS_* constants.
    """
    import logging
    logger = logging.getLogger("copytrade")
    logger.info("[copytrade:reprice] task started pk=%d lake=%s", position_pk, lake_base_dir)
    try:
        from copytrade.fill_repricing import reprice_position
        status = reprice_position(position_pk, lake_base_dir=lake_base_dir)
        logger.info("[copytrade:reprice] task done pk=%d status=%s", position_pk, status)
        return {"pk": position_pk, "status": status}
    except Exception as exc:  # noqa: BLE001 — task must never crash the worker
        logger.error("[copytrade:reprice] task error pk=%d: %s", position_pk, exc)
        return {"pk": position_pk, "status": "error", "error": str(exc)}


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
