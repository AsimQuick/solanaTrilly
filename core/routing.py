# ---
# module: core.routing
# sprint: sprint-1
# story: US-1 AC-1.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: channels, core.consumers
# ---
"""WebSocket URL routing for the core app."""
from django.urls import re_path

from core.consumers import EchoConsumer

websocket_urlpatterns = [
    re_path(r"ws/echo/$", EchoConsumer.as_asgi()),
]
