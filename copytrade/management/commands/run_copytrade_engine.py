# ---
# module: copytrade.management.commands.run_copytrade_engine
# sprint: sprint-12, cutover (copy-trade live)
# story: US-59 AC-59.1, US-59 AC-59.2, copytrade-runtime
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: django, asyncio, signal, sys, asgiref, copytrade.models,
#   copytrade.wallet_consumer, copytrade.helius_wallet_source,
#   copytrade.engine_runtime, copytrade.price_source, copytrade.validators,
#   copytrade.schemas, core.clock, core.pricing.sol_usd, core.tape.mapped_source
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

  OBSERVE — engine_on=True + active cohort found + settings.mode=="observe".
  Two concurrent asyncio tasks:
    (a) Consumer loop: HeliusWalletTxSource + MappedSwapSource + WalletSubscriptionConsumer
        → WalletTxEvent → handle_event (ORM wrapped in sync_to_async).
    (b) Manager loop: runs manage_positions every ~15 s (sync_to_async).

  LIVE (BLOCKED) — mode=="live": logs a clear warning and stays idle.  Live
  trading is operator-gated (trading_enabled flag) and not active here.

Safety rules:
  - NEVER calls open_live_position.
  - NEVER sets trading_enabled or touches PipelineState.
  - Asserts mode == "observe" before starting the consumer loop.

Isolation contract (enforced by test_copytrade_engine_topology_ac591.py):
  - MUST NOT import BirdeyeSwapSource, HeliusBirthTapeSource, TapeRecorder,
    LakeWriter, SwapWriter (firehose mutable singletons).
  - MUST NOT import from core.models (PipelineConfig, PipelineState, RawEvent, Token).
  - Writes ONLY copytrade_ tables (copytrade.models).
  - May import copytrade.*, core.clock, core.datasource, core.pricing.sol_usd,
    core.tape.mapped_source (none of the forbidden names).
"""
import asyncio
import logging
import signal
import sys

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from copytrade.engine_runtime import EngineState, handle_event, manage_positions
from copytrade.helius_wallet_source import HeliusWalletTxSource, decode_wallet_tx
from copytrade.models import CopytradeCohort, CopytradePosition, CopyTradeSettings, CopytradeWallet
from copytrade.price_source import fetch_mint_price_usd
from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit
from copytrade.validators import validate_cohort_v2
from copytrade.wallet_consumer import WalletSubscriptionConsumer
from core.clock import WallClock
from core.pricing.sol_usd import get_sol_usd
from core.tape.mapped_source import MappedSwapSource

logger = logging.getLogger("copytrade")

# Periodic manage_positions interval (seconds)
_MANAGE_INTERVAL_S: float = 15.0


class Command(BaseCommand):
    help = (
        "Run the dedicated copytrade engine process (observe/paper mode).  "
        "Separate from the listener — owns its OWN Helius connection (wallet-address "
        "subscription, not the token firehose).  Reads the active cohort's wallets via "
        "sync_to_async and runs the dual-task event loop: consumer + periodic exit manager. "
        "Never calls open_live_position.  LIVE mode is operator-gated (stay idle)."
    )

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run(options))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Copytrade engine stopped by interrupt."))
            sys.exit(0)

    async def _run(self, options: dict) -> None:
        """Start the wallet-subscription consumer (AC-59.2) or stay idle.

        1. Reads CopyTradeSettings singleton via sync_to_async (K4 safe).
        2. If engine_on=False or no active_cohort_id: falls back to idle.
        3. If mode=="live": logs a warning and stays idle (DO NOT trade — live is
           operator-gated).
        4. Otherwise (mode=="observe"): loads cohort + wallets via sync_to_async,
           builds EngineState, then runs TWO concurrent asyncio tasks:
             (a) consumer_task: streams WalletTxEvents and calls handle_event.
             (b) manager_task: runs manage_positions every _MANAGE_INTERVAL_S.
        """
        settings_row: CopyTradeSettings = await sync_to_async(CopyTradeSettings.get)()

        # --- Check engine_on and active cohort ---
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

        # --- LIVE mode guard: stay idle, never trade ---
        if settings_row.mode == "live":
            logger.warning(
                "[copytrade] mode='live' detected — live trading is operator-gated "
                "and NOT active.  Staying idle.  Flip mode to 'observe' to run the engine."
            )
            self.stdout.write(
                self.style.WARNING(
                    "Copytrade engine: mode='live' — live trading is NOT active. "
                    "Engine is idling.  Set mode='observe' to run the paper engine."
                )
            )
            await self._run_idle()
            return

        # --- OBSERVE mode: assert safety before proceeding ---
        assert settings_row.mode == "observe", (
            f"Unexpected mode {settings_row.mode!r}; engine may only run in 'observe' mode."
        )

        cohort_id: str = settings_row.active_cohort_id

        # --- Load cohort trade_config and validate ---
        cohort_row: CopytradeCohort = await sync_to_async(
            lambda: CopytradeCohort.objects.get(cohort_id=cohort_id)
        )()
        cohort = await sync_to_async(validate_cohort_v2)(cohort_row.trade_config)

        # --- Build wallet_to_strategy + exit_by_strategy from cohort ---
        wallet_to_strategy: dict[str, str] = cohort.wallet_to_strategy()
        exit_by_strategy: dict[str, OurTrailingExit | MirrorWalletSellExit] = {
            s.id: s.exit for s in cohort.enabled_strategies()
        }

        # --- Load wallet addresses from DB (strategy-aware) ---
        wallet_addresses: list[str] = await sync_to_async(
            lambda: list(
                CopytradeWallet.objects.filter(cohort_id=cohort_id)
                .values_list("address", flat=True)
            )
        )()

        # --- Seed open positions from DB (crash recovery) ---
        open_db_positions: dict[str, CopytradePosition] = await sync_to_async(
            lambda: {
                p.mint: p
                for p in CopytradePosition.objects.filter(
                    cohort_id=cohort_id,
                    status=CopytradePosition.STATUS_OPEN,
                )
            }
        )()

        # --- Build EngineState ---
        state = EngineState.new(
            wallet_to_strategy=wallet_to_strategy,
            exit_by_strategy=exit_by_strategy,
            open_positions=open_db_positions,
        )

        channel_name: str = f"copytrade_wallet_updates_{cohort_id}"

        self.stdout.write(
            f"Copytrade engine starting (observe/paper): "
            f"cohort={cohort_id} wallets={len(wallet_addresses)} "
            f"channel={channel_name} open_positions={len(open_db_positions)}"
        )
        logger.info(
            "[copytrade] engine starting: cohort=%s wallets=%d channel=%s open=%d mode=observe",
            cohort_id,
            len(wallet_addresses),
            channel_name,
            len(open_db_positions),
        )

        # --- Wire Helius source + mapper + consumer ---
        helius_api_key: str = getattr(django_settings, "HELIUS_API_KEY", "")
        helius_source = HeliusWalletTxSource(helius_api_key, wallet_addresses)
        mapped_source = MappedSwapSource(helius_source, decode_wallet_tx)
        clock = WallClock()
        consumer = WalletSubscriptionConsumer(
            source=mapped_source,
            clock=clock,
            wallet_addresses=wallet_addresses,
            channel_name=channel_name,
        )

        # --- Shutdown future ---
        loop = asyncio.get_running_loop()
        stop: asyncio.Future = loop.create_future()

        def _handle_signal() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        # --- Two concurrent tasks ---
        consumer_task = asyncio.create_task(
            self._consumer_loop(consumer, state, settings_row, cohort, clock, stop)
        )
        manager_task = asyncio.create_task(
            self._manager_loop(state, clock, stop)
        )

        try:
            await asyncio.gather(consumer_task, manager_task)
        except asyncio.CancelledError:
            pass

        self.stdout.write(self.style.WARNING("Copytrade engine shut down."))
        logger.info("[copytrade] engine shut down cleanly.")

    async def _consumer_loop(
        self,
        consumer: WalletSubscriptionConsumer,
        state: EngineState,
        settings_row: CopyTradeSettings,
        cohort,
        clock: WallClock,
        stop: asyncio.Future,
    ) -> None:
        """Stream WalletTxEvents and call handle_event for each.

        ORM calls (open_observe_position_v2 writes DB rows) are wrapped in
        sync_to_async so we never block the event loop with synchronous Django ORM
        in an async context (K4 async-safety guard).
        """
        try:
            async for event in consumer.run():
                if stop.done():
                    break
                sol_usd: float = await sync_to_async(get_sol_usd)()
                await sync_to_async(handle_event)(
                    event,
                    settings=settings_row,
                    state=state,
                    cohort=cohort,
                    sol_usd=sol_usd,
                    price_fn=fetch_mint_price_usd,
                    clock=clock,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[copytrade] consumer_loop error: %s", exc)
        finally:
            if not stop.done():
                stop.set_result(None)

    async def _manager_loop(
        self,
        state: EngineState,
        clock: WallClock,
        stop: asyncio.Future,
    ) -> None:
        """Periodically call manage_positions to evaluate and close open positions.

        Runs every _MANAGE_INTERVAL_S until the stop signal fires.  Each
        manage_positions call is wrapped in sync_to_async (ORM access).
        """
        try:
            while not stop.done():
                try:
                    await asyncio.sleep(_MANAGE_INTERVAL_S)
                except asyncio.CancelledError:
                    break
                if stop.done():
                    break
                now = clock.now()
                try:
                    closed = await sync_to_async(manage_positions)(
                        state=state,
                        price_fn=fetch_mint_price_usd,
                        clock=clock,
                        now=now,
                    )
                    if closed:
                        logger.info(
                            "[copytrade] manage_positions: closed %d position(s) this tick",
                            len(closed),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("[copytrade] manager_loop tick error: %s", exc)
        finally:
            if not stop.done():
                stop.set_result(None)

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
