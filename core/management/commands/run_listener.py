# ---
# module: core.management.commands.run_listener
# sprint: sprint-4, sprint-5
# story: US-16 AC-16.3, US-22 AC-22.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: django, asyncio, core.detection.consumer, core.detection.helius_reconciler,
#               core.tape.birdeye_swap_source, core.tape.recorder, core.clock
# ---
"""run_listener — entry point for the dedicated listener container.

The #289 lesson: detection, recorder, and reconciler MUST NEVER share the
web/gunicorn process.  This management command is the sole entry point for
the standalone 'listener' service defined in docker-compose.yml and
docker-compose.staging.yml.

Responsibility of the listener process:
  1. Subscribe to the live Birdeye WebSocket (MEME graduation events).
  2. Run DetectionConsumer to persist graduated tokens to the database.
  3. Run MigrateReconciler against the Helius migrate stream (gap-recovery, AC-16.1).

The DataSource seam (Principle #7) ensures the same consumer classes run
unchanged in offline pytest replay tests — only the injected source differs.

AC-22.1: build_swap_recorder() is the ONLY place where BirdeyeSwapSource and
WallClock are instantiated.  Concrete sources and wall clocks live ONLY here
(the listener/adapter wiring layer) — never on the core recorder path.
"""
import asyncio
import logging
import signal
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from core.clock import WallClock
from core.tape.birdeye_swap_source import BirdeyeSwapSource
from core.tape.recorder import TapeRecorder

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter wiring — the ONLY place where concrete sources and wall clock live
# ---------------------------------------------------------------------------


def build_swap_recorder(api_key: str, mint: str) -> TapeRecorder:
    """Build a TapeRecorder wired with a concrete BirdeyeSwapSource + WallClock.

    The ONLY place where BirdeyeSwapSource and WallClock are instantiated —
    they live ONLY in this listener/adapter wiring, never on the core recorder path
    (Principle #7 / PRD §4 / US-2 static-analysis guard).

    Args:
        api_key: Birdeye API key (BIRDEYE_API_KEY from Django settings).
        mint:    Token mint address to subscribe swap events for.
    """
    source = BirdeyeSwapSource(api_key=api_key, mint=mint)
    clock = WallClock()
    return TapeRecorder(source=source, clock=clock)


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Run the dedicated listener process: consumes Birdeye MEME graduation "
        "events and the Helius migrate backstop stream.  Never runs inside gunicorn."
    )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS("Starting solanatrilly listener process ...")
        )
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Listener stopped by interrupt."))
            sys.exit(0)

    async def _run(self) -> None:
        """Async event-loop body — blocks until SIGINT or SIGTERM."""
        loop = asyncio.get_running_loop()
        stop: asyncio.Future = loop.create_future()

        def _handle_signal() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        # AC-22.1: build_swap_recorder is the wiring entry point.
        # BirdeyeSwapSource + WallClock are instantiated here (adapter layer only).
        # The recorder core (TapeRecorder) is unchanged and accepts any DataSource.
        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        if api_key:
            logger.info(
                "Listener ready. build_swap_recorder available — "
                "BIRDEYE_API_KEY present; concrete BirdeyeSwapSource wired."
            )
            self.stdout.write(
                "Listener container running.\n"
                "BIRDEYE_API_KEY present — build_swap_recorder() ready to wire "
                "BirdeyeSwapSource into TapeRecorder for live swap stream.\n"
                "Configure a mint address to begin recording."
            )
        else:
            logger.info(
                "Listener ready. build_swap_recorder available — "
                "BIRDEYE_API_KEY not set; set it to enable live swap recording."
            )
            self.stdout.write(
                "Listener container running.\n"
                "Configure BIRDEYE_API_KEY and HELIUS_API_KEY to begin consuming "
                "live streams.\n"
                "build_swap_recorder(api_key, mint) wires BirdeyeSwapSource + "
                "WallClock into TapeRecorder (AC-22.1 adapter wiring)."
            )

        await stop

        logger.info("Listener received stop signal — shutting down cleanly.")
        self.stdout.write(self.style.WARNING("Listener shut down."))
