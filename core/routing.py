# ---
# module: core.routing
# sprint: sprint-1, sprint-10
# story: US-1 AC-1.3, US-48 AC-48.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: channels, core.consumers, core.dashboard.consumer
# ---
"""WebSocket URL routing for the core app."""
from django.urls import re_path

from core.consumers import EchoConsumer
from core.dashboard.consumer import TapeFeedConsumer

websocket_urlpatterns = [
    re_path(r"ws/echo/$", EchoConsumer.as_asgi()),
    re_path(r"ws/tape/(?P<mint>[^/]+)/$", TapeFeedConsumer.as_asgi()),
]
