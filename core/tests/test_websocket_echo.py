# ---
# module: core.tests.test_websocket_echo
# sprint: sprint-1
# story: US-1 AC-1.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: channels, core.consumers, core.routing, config.asgi
# ---
"""WebSocket echo handshake test — verified via channels.testing.WebsocketCommunicator."""
import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator


@pytest.mark.django_db
def test_websocket_echo_handshake_and_echo():
    async def _run():
        from config.asgi import application

        communicator = WebsocketCommunicator(application, "/ws/echo/")
        connected, _ = await communicator.connect()
        assert connected
        await communicator.send_to(text_data="ping")
        response = await communicator.receive_from()
        assert response == "ping"
        await communicator.disconnect()

    async_to_sync(_run)()
