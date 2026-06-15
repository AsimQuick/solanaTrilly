# ---
# module: core.tasks
# sprint: sprint-4
# story: US-1 AC-1.5, US-16 AC-16.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: celery, core.detection.birdeye_sweep
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
