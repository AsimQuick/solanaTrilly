# ---
# module: config.wsgi
# sprint: pre-sprint
# story: setup
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-14
# dependencies: django
# ---
"""WSGI entrypoint (used by gunicorn in production)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
