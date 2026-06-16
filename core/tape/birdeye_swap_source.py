# ---
# module: core.tape.birdeye_swap_source
# sprint: sprint-5
# story: US-22 AC-22.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.datasource, json, typing, websockets
# ---
"""BirdeyeSwapSource — concrete DataSource that subscribes to Birdeye SUBSCRIBE_TXS.

This class is the live-data adapter for the PumpSwap swap stream.  It lives
ONLY in the listener/adapter wiring layer (core/tape/ is the boundary; it is
NOT imported from core/tape/recorder.py or any other core module).

The US-2 static-analysis guard (test_tape_recorder_ac181.py) scans core/tape/
for imports of LiveSource / ReplaySource — this file contains neither.
The AC-2.2 guard (test_clock.py) scans core/ for datetime.now() / time.time()
calls — this file contains neither.

Lifecycle:
    source = BirdeyeSwapSource(api_key=..., mint=...)
    await source.connect()        # opens WS, sends SUBSCRIBE_TXS
    async for event in source.events():
        ...                       # yields parsed swap dicts
    await source.disconnect()     # closes WS
"""
import json
from typing import Any, AsyncGenerator

from core.datasource import DataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BIRDEYE_WS_URL: str = "wss://public-api.birdeye.so/socket/solana"
BIRDEYE_WS_ORIGIN: str = "ws://public-api.birdeye.so"
BIRDEYE_WS_SUBPROTOCOL: str = "echo-protocol"
PUMPSWAP_PROGRAM: str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


# ---------------------------------------------------------------------------
# Concrete DataSource — Birdeye SUBSCRIBE_TXS swap stream
# ---------------------------------------------------------------------------


class BirdeyeSwapSource(DataSource):
    """Live swap-event source backed by the Birdeye WebSocket SUBSCRIBE_TXS feed.

    Subscribes to all swap transactions for a given token mint on the PumpSwap
    AMM.  Each yielded event is a plain dict extracted from the Birdeye message
    envelope (``data.data`` or ``data`` if no nested key).

    Args:
        api_key: Birdeye API key (X-API-KEY header).
        mint:    Token mint address to subscribe swap events for.

    Note:
        ``websockets`` is imported lazily inside ``connect()`` to avoid import-
        time side effects during structural tests that only scan the AST.
    """

    def __init__(self, api_key: str, mint: str) -> None:
        self._api_key: str = api_key
        self._mint: str = mint
        self._ws: Any = None

    async def connect(self) -> None:
        """Open the Birdeye WebSocket and send the SUBSCRIBE_TXS message.

        Uses the proven Birdeye handshake (matching the AC-22.2 capture tool):
        the API key is a query param, the Origin header + 'echo-protocol'
        subprotocol are required, and TLS uses certifi.  Subscribe payload uses
        ``queryType: simple`` (the form Birdeye answers with TXS_DATA frames).
        """
        import ssl  # lazy — keeps import-time free of side effects

        import websockets  # lazy import — keeps import-time free of network side effects

        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # pragma: no cover - certifi always present in the image
            ssl_ctx = ssl.create_default_context()

        url = f"{BIRDEYE_WS_URL}?x-api-key={self._api_key}"
        self._ws = await websockets.connect(
            url,
            extra_headers={"Origin": BIRDEYE_WS_ORIGIN},
            subprotocols=[BIRDEYE_WS_SUBPROTOCOL],
            ssl=ssl_ctx,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=10,
        )
        subscribe_msg = json.dumps(
            {
                "type": "SUBSCRIBE_TXS",
                "data": {
                    "queryType": "simple",
                    "address": self._mint,
                },
            }
        )
        await self._ws.send(subscribe_msg)

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield parsed swap event dicts from the Birdeye SUBSCRIBE_TXS stream.

        Each raw WebSocket message is JSON-parsed; the inner ``data`` key is
        unwrapped if present (Birdeye envelope: ``{"type":…,"data":{…}}``).
        Non-dict results and JSON parse errors are silently skipped.

        Yields nothing if the connection was never opened (``self._ws is None``).
        """
        if self._ws is None:
            return

        async for raw_message in self._ws:
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue

            # Only TXS_DATA frames carry a swap; WELCOME/ack frames are skipped.
            if not isinstance(parsed, dict) or parsed.get("type") != "TXS_DATA":
                continue

            event = parsed.get("data")
            if isinstance(event, dict):
                yield event
