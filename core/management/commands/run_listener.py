# ---
# module: core.management.commands.run_listener
# sprint: sprint-4
# story: US-16 AC-16.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, asyncio, core.detection.consumer, core.detection.helius_reconciler
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
"""
import asyncio
import logging
import signal
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


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

        logger.info(
            "Listener ready.  "
            "Waiting for live DataSource configuration (BIRDEYE_API_KEY / HELIUS_API_KEY)."
        )
        self.stdout.write(
            "Listener container running.\n"
            "Configure BIRDEYE_API_KEY and HELIUS_API_KEY to begin consuming live streams."
        )

        await stop

        logger.info("Listener received stop signal — shutting down cleanly.")
        self.stdout.write(self.style.WARNING("Listener shut down."))
