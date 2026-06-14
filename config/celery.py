# ---
# module: config.celery
# sprint: sprint-1
# story: US-1 AC-1.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: celery, django
# ---
"""Celery application factory."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("solanatrilly")

# Read config from Django settings, namespace CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in all INSTALLED_APPS
app.autodiscover_tasks()
