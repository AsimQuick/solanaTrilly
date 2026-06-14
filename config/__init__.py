# ---
# module: config.__init__
# sprint: sprint-1
# story: US-1 AC-1.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: config.celery
# ---
"""Expose the Celery app so `celery -A config` resolves correctly."""
from .celery import app as celery_app

__all__ = ["celery_app"]
