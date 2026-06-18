# ---
# module: copytrade.management.commands.run_copytrade_engine
# sprint: sprint-12
# story: US-59 AC-59.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, asyncio, signal, sys
# ---
"""run_copytrade_engine — entry point for the dedicated copytrade_engine container.

SPEC §5: The copytrade engine is a SEPARATE service/worker from the
recorder/listener/model-inference loop.  It MUST NOT share any mutable state
with the firehose.  It owns its OWN Helius subscription/connection — a distinct
subscription/channel from the listener's program-wide token firehose — so a
problem in one cannot stall the other.

This command is the sole entry point for the standalone 'copytrade_engine'
service defined in docker-compose.yml and docker-compose.staging.yml.

Modes:

  IDLE (AC-59.1) — this container stays up but does no wallet-address
  subscription work yet.  The idle loop waits for SIGINT/SIGTERM.
  AC-59.2 will wire in the Helius wallet-address subscription consumer that
  drives the copytrade execution path.

Isolation contract (enforced by test_copytrade_engine_topology_ac591.py):
  - MUST NOT import BirdeyeSwapSource, HeliusBirthTapeSource, TapeRecorder,
    LakeWriter, SwapWriter (firehose mutable singletons).
  - MUST NOT import from core.models (PipelineConfig, PipelineState, RawEvent,
    Token).
  - Writes ONLY copytrade_ tables (copytrade.models).

Helius connection note:
  HELIUS_API_KEY is reserved here for a wallet-address subscription
  (accountSubscribe / logsSubscribe on tracked trader wallets), NOT the
  token-firehose transactionSubscribe used by the listener container.
  AC-59.2 will instantiate the concrete HeliusWalletSource seam here.
"""
import asyncio
import logging
import signal
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the dedicated copytrade engine process.  Separate from the listener "
        "— owns its OWN Helius connection (wallet-address subscription, not the "
        "token firehose).  Idle by default; AC-59.2 will add the wallet-subscription "
        "consumer.  Never runs in gunicorn."
    )

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run(options))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Copytrade engine stopped by interrupt."))
            sys.exit(0)

    async def _run(self, options: dict) -> None:
        await self._run_idle()

    async def _run_idle(self) -> None:
        """Idle mode: stay up, no wallet subscription yet (AC-59.2 adds the consumer).

        This container is a SEPARATE process from the listener service.  It has
        its own Helius connection reserved for wallet-address subscriptions
        (not the token firehose).  The actual subscription consumer will be
        wired in AC-59.2.
        """
        loop = asyncio.get_running_loop()
        stop: asyncio.Future = loop.create_future()

        def _handle_signal() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        self.stdout.write(
            "Copytrade engine container running (idle).\n"
            "SEPARATE process from the listener — owns its OWN Helius connection.\n"
            "Helius connection reserved for wallet-address subscription "
            "(AC-59.2 will add the wallet-subscription consumer).\n"
            "Writes ONLY copytrade_ tables; shares NO mutable state with the firehose."
        )
        logger.info(
            "copytrade_engine idle — separate from listener, own Helius connection; "
            "wallet-subscription consumer pending AC-59.2."
        )

        await stop
        self.stdout.write(self.style.WARNING("Copytrade engine shut down."))
        logger.info("copytrade_engine shut down cleanly.")
