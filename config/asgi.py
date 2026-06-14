# ---
# module: config.asgi
# sprint: pre-sprint
# story: setup
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-14
# dependencies: django
# ---
"""ASGI entrypoint (for async servers / websockets if needed later)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
