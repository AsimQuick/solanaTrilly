# ---
# module: core.consumers
# sprint: sprint-1
# story: US-1 AC-1.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-14
# dependencies: channels
# ---
"""WebSocket consumers for the core app."""
from channels.generic.websocket import AsyncWebsocketConsumer


class EchoConsumer(AsyncWebsocketConsumer):
    """Trivial echo consumer — accepts the handshake and echoes any text back."""

    async def connect(self):
        await self.accept()

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is not None:
            await self.send(text_data=text_data)
