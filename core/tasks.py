# ---
# module: core.tasks
# sprint: sprint-1
# story: US-1 AC-1.5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: celery
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
