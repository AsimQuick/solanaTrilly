# ---
# module: config.asgi
# sprint: sprint-1
# story: US-1 AC-1.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: django, channels
# ---
"""ASGI entrypoint — Django Channels ProtocolTypeRouter."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Get the Django ASGI application (must be called before importing channels)
_django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": _django_asgi_app,
        # WebSocket routes wired in AC-1.3
    }
)
