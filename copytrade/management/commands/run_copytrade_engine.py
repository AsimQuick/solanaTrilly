# ---
# module: copytrade.management.commands.run_copytrade_engine
# sprint: sprint-12
# story: US-59 AC-59.1, US-59 AC-59.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, asyncio, signal, sys, asgiref, copytrade.models, copytrade.wallet_consumer, core.clock
# ---
"""run_copytrade_engine — entry point for the dedicated copytrade_engine container.

SPEC §5: The copytrade engine is a SEPARATE service/worker from the
recorder/listener/model-inference loop.  It MUST NOT share any mutable state
with the firehose.  It owns its OWN Helius subscription/connection — a distinct
subscription/channel from the listener's program-wide token firehose — so a
problem in one cannot stall the other.

This command is the sole entry point for the standalone 'copytrade_engine'
service defined in docker-compose.yml and docker-compose.staging.yml.

Modes (AC-59.2):

  IDLE — no active cohort or engine_on=False.  The container stays up, waiting
  for a cohort to be loaded and the engine toggled on.

  CONSUMER — engine_on=True + active cohort found.  The command reads wallet
  addresses from the DB via sync_to_async (K4 async-safe), then creates a
  WalletSubscriptionConsumer with an INJECTED DataSource + Clock (Principle #7)
  and consumes events.  The concrete DataSource (HeliusWalletSource) will be
  wired in when P8 live-execution lands; until then a no-op placeholder source
  is used so the command stays offline/replay-only (zero firehose).

Isolation contract (enforced by test_copytrade_engine_topology_ac591.py):
  - MUST NOT import BirdeyeSwapSource, HeliusBirthTapeSource, TapeRecorder,
    LakeWriter, SwapWriter (firehose mutable singletons).
  - MUST NOT import from core.models (PipelineConfig, PipelineState, RawEvent,
    Token).
  - Writes ONLY copytrade_ tables (copytrade.models).
"""
import asyncio
import logging
import signal
import sys

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from copytrade.models import CopyTradeSettings, CopytradeWallet
from copytrade.wallet_consumer import WalletSubscriptionConsumer
from core.clock import WallClock
from core.replay_source import ReplaySource

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the dedicated copytrade engine process.  Separate from the listener "
        "— owns its OWN Helius connection (wallet-address subscription, not the "
        "token firehose).  Reads the active cohort's wallets via sync_to_async "
        "and runs WalletSubscriptionConsumer with injected DataSource + Clock "
        "(Principle #7).  Never runs in gunicorn."
    )

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run(options))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Copytrade engine stopped by interrupt."))
            sys.exit(0)

    async def _run(self, options: dict) -> None:
        """Start the wallet-subscription consumer (AC-59.2).

        1. Reads CopyTradeSettings singleton via sync_to_async (K4 safe).
        2. If engine_on=False or no active_cohort_id: falls back to idle.
        3. Otherwise reads wallet addresses from DB via sync_to_async and starts
           WalletSubscriptionConsumer with injected DataSource + Clock.
        """
        settings_row: CopyTradeSettings = await sync_to_async(CopyTradeSettings.get)()

        if not settings_row.engine_on or not settings_row.active_cohort_id:
            self.stdout.write(
                "Copytrade engine: engine_on=False or no active cohort — idle mode.\n"
                "Toggle engine_on and upload a cohort to start the consumer."
            )
            logger.info(
                "copytrade_engine idle (engine_on=%s, active_cohort_id=%s).",
                settings_row.engine_on,
                settings_row.active_cohort_id,
            )
            await self._run_idle()
            return

        cohort_id: str = settings_row.active_cohort_id
        wallet_addresses: list[str] = await sync_to_async(
            lambda: list(
                CopytradeWallet.objects.filter(cohort_id=cohort_id)
                .values_list("address", flat=True)
            )
        )()

        # Channel/topic name is config-driven (Principle #1): derived from cohort_id
        # so it is unique per cohort and readable from config state.
        channel_name: str = f"copytrade_wallet_updates_{cohort_id}"

        self.stdout.write(
            f"Copytrade engine starting consumer: "
            f"cohort={cohort_id} wallets={len(wallet_addresses)} "
            f"channel={channel_name}"
        )
        logger.info(
            "copytrade_engine starting consumer: cohort=%s wallets=%d channel=%s",
            cohort_id,
            len(wallet_addresses),
            channel_name,
        )

        # DataSource and Clock are INJECTED here (composition root — Principle #7).
        # ReplaySource is the offline placeholder; HeliusWalletSource replaces it
        # when P8 live-execution is built.  The WalletSubscriptionConsumer itself
        # never imports a concrete source (static-analysis guard in AC-59.2 tests).
        source = ReplaySource(event_log=[])
        clock = WallClock()

        consumer = WalletSubscriptionConsumer(
            source=source,
            clock=clock,
            wallet_addresses=wallet_addresses,
            channel_name=channel_name,
        )

        async for event in consumer.run():
            logger.info(
                "wallet_tx: wallet=%s mint=%s type=%s sig=%s",
                event.wallet,
                event.mint,
                event.tx_type,
                event.tx_signature,
            )

        # Placeholder source exhausted — return to idle until a live source is wired.
        logger.info("copytrade_engine consumer finished — returning to idle (P8 pending).")
        await self._run_idle()

    async def _run_idle(self) -> None:
        """Idle mode: stay up, waiting for SIGINT/SIGTERM.

        This container is a SEPARATE process from the listener service.  It has
        its own Helius connection reserved for wallet-address subscriptions
        (not the token firehose).
        """
        loop = asyncio.get_running_loop()
        stop: asyncio.Future = loop.create_future()

        def _handle_signal() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        self.stdout.write(
            "Copytrade engine idle — SEPARATE process from the listener.\n"
            "Owns its OWN Helius connection (wallet-address subscription).\n"
            "Writes ONLY copytrade_ tables; shares NO mutable state with the firehose."
        )
        logger.info(
            "copytrade_engine idle — separate from listener, own Helius connection."
        )

        await stop
        self.stdout.write(self.style.WARNING("Copytrade engine shut down."))
        logger.info("copytrade_engine shut down cleanly.")
