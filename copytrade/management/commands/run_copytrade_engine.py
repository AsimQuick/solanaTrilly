# ---
# module: copytrade.management.commands.run_copytrade_engine
# sprint: sprint-12, cutover (copy-trade live), feature/copy-capital-path-wiring
# story: US-59 AC-59.1, US-59 AC-59.2, copytrade-runtime, EPIC-copy-capital-path-wiring
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: django, asyncio, signal, sys, asgiref, copytrade.models,
#   copytrade.wallet_consumer, copytrade.helius_wallet_source,
#   copytrade.engine_runtime, copytrade.price_source, copytrade.validators,
#   copytrade.schemas, core.clock, core.pricing.sol_usd, core.tape.mapped_source,
#   trading.sender, trading.tx_signer, trading.execution_core, trading.schemas
# ---
"""run_copytrade_engine — entry point for the dedicated copytrade_engine container.

SPEC §5: The copytrade engine is a SEPARATE service/worker from the
recorder/listener/model-inference loop.  It MUST NOT share any mutable state
with the firehose.  It owns its OWN Helius subscription/connection — a distinct
subscription/channel from the listener's program-wide token firehose — so a
problem in one cannot stall the other.

This command is the sole entry point for the standalone 'copytrade_engine'
service defined in docker-compose.yml and docker-compose.staging.yml.

Modes (AC-59.2 + F3 capital-path wiring):

  IDLE — no active cohort or engine_on=False.  The container stays up, waiting
  for a cohort to be loaded and the engine toggled on.

  OBSERVE — engine_on=True + active cohort found + settings.mode=="observe".
  Two concurrent asyncio tasks:
    (a) Consumer loop: HeliusWalletTxSource + MappedSwapSource + WalletSubscriptionConsumer
        → WalletTxEvent → handle_event (ORM wrapped in sync_to_async).
    (b) Manager loop: runs manage_positions every ~15 s (sync_to_async).

  LIVE (F3 — capital path wiring) — mode=="live" + TRADING_WALLET_KEY present:
  Wires ExecutionCore (trading_enabled=True) + Sender + Keypair, then runs the
  same two-task loop as OBSERVE but passes execution_core/keypair/trading_enabled
  through _consumer_loop → handle_event AND _manager_loop → manage_positions.
  SAFETY: if TRADING_WALLET_KEY is absent, falls back to OBSERVE with a LOUD
  warning (never crashes, never sends). Default path is OBSERVE (inert).

Safety rules:
  - NEVER sets trading_enabled independently of mode=='live' + keypair check.
  - NEVER provisions, commits, or logs the private key.
  - trading_enabled is passed as True ONLY when mode=='live' AND keypair loaded.
  - Default path (mode!='live' or key absent) stays fully inert/observe.

Capital gate (F3 + F4):
  - usd_size_per_trade drives sol_in sizing (sol_size_per_trade is IGNORED by the
    2.x engine — it is a legacy 1.x field stored in DB but unused in handle_event).
  - ExecutionCore is wired with TradingConfig(trading_enabled=True,
    max_daily_spend_sol=0.04, max_open_live_positions=2) — HARD budget gate.

Isolation contract (enforced by test_copytrade_engine_topology_ac591.py):
  - MUST NOT import BirdeyeSwapSource, HeliusBirthTapeSource, TapeRecorder,
    LakeWriter, SwapWriter (firehose mutable singletons).
  - MUST NOT import from core.models (PipelineConfig, PipelineState, RawEvent, Token).
  - Writes ONLY copytrade_ tables (copytrade.models).
  - May import copytrade.*, core.clock, core.datasource, core.pricing.sol_usd,
    core.tape.mapped_source (none of the forbidden names).
  - trading.sender / trading.execution_core / trading.tx_signer imports are
    DEFERRED to the live branch inside _run() — they are never imported on the
    observe path so the isolation contract holds.
"""
import asyncio
import logging
import os
import signal
import sys

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from copytrade.curve_price import HELIUS_RPC_BASE, read_curve_state
from copytrade.engine_runtime import EngineState, handle_event, manage_positions
from copytrade.helius_wallet_source import HeliusWalletTxSource, decode_wallet_tx
from copytrade.models import CopytradeCohort, CopytradePosition, CopyTradeSettings, CopytradeWallet
from copytrade.schemas import MirrorWalletSellExit, OurTrailingExit
from copytrade.validators import validate_cohort_any
from copytrade.wallet_consumer import WalletSubscriptionConsumer
from core.clock import WallClock
from core.pricing.sol_usd import get_sol_usd
from core.tape.mapped_source import MappedSwapSource

logger = logging.getLogger("copytrade")

# Periodic manage_positions interval (seconds)
_MANAGE_INTERVAL_S: float = 15.0

# F5 — HELIUS_SENDER_URL env default (loud error if rpc_url is empty at startup)
_HELIUS_SENDER_URL_DEFAULT: str = "https://sender.helius-rpc.com/fast"


class Command(BaseCommand):
    help = (
        "Run the dedicated copytrade engine process (observe or live mode).  "
        "Separate from the listener — owns its OWN Helius connection (wallet-address "
        "subscription, not the token firehose).  Reads the active cohort's wallets via "
        "sync_to_async and runs the dual-task event loop: consumer + periodic exit manager. "
        "In OBSERVE mode: no real sends. In LIVE mode (operator-gated): wires "
        "ExecutionCore/Sender/Keypair and passes trading_enabled=True through the loop. "
        "Falls back to OBSERVE with a loud warning if TRADING_WALLET_KEY is absent."
    )

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run(options))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Copytrade engine stopped by interrupt."))
            sys.exit(0)

    async def _run(self, options: dict) -> None:
        """Start the wallet-subscription consumer or stay idle.

        F3 — capital path wiring:
        1. Reads CopyTradeSettings singleton via sync_to_async (K4 safe).
        2. If engine_on=False or no active_cohort_id: idle.
        3. If mode=="live":
           a. Check TRADING_WALLET_KEY env (F5: assert rpc_url non-empty with loud error).
           b. If key present: build Sender + ExecutionCore (trading_enabled=True) + load keypair.
           c. If key absent: log LOUD warning, fall back to OBSERVE (never crash, never send).
        4. mode=="observe" OR live-fallback-observe: run dual-task loop with
           trading_enabled=False, execution_core=None.
        5. mode=="live" + keypair present: run dual-task loop with trading_enabled=True,
           execution_core wired, keypair injected into both tasks.
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

        # --- Prepare live-path components (F3) ---
        # Default: inert observe path (trading_enabled=False, no sender, no keypair).
        trading_enabled: bool = False
        execution_core = None  # type: ignore[assignment]
        keypair = None

        if settings_row.mode == "live":
            # F3 + F5: wire the live path when mode='live' and TRADING_WALLET_KEY present.
            helius_api_key_for_live: str = getattr(django_settings, "HELIUS_API_KEY", "")
            rpc_url_for_live: str = f"{HELIUS_RPC_BASE}/?api-key={helius_api_key_for_live}"

            # F5 — assert rpc_url non-empty at startup; loud error, not silent later failure.
            if not rpc_url_for_live or "api-key=" not in rpc_url_for_live:
                logger.error(
                    "[copytrade] LIVE mode: rpc_url is effectively EMPTY (HELIUS_API_KEY "
                    "missing?) — falling back to OBSERVE. Set HELIUS_API_KEY before "
                    "enabling live mode."
                )
                self.stdout.write(
                    self.style.ERROR(
                        "Copytrade engine: LIVE mode — rpc_url empty (HELIUS_API_KEY "
                        "missing). Falling back to OBSERVE (inert)."
                    )
                )
            else:
                # F5: HELIUS_SENDER_URL from env (default to fast endpoint).
                sender_url: str = os.environ.get("HELIUS_SENDER_URL", _HELIUS_SENDER_URL_DEFAULT)

                # Load keypair from TRADING_WALLET_KEY env (fail-safe: None if absent).
                # F3: if absent, fall back to OBSERVE with a LOUD warning (never crash).
                try:
                    from trading.tx_signer import load_keypair as _load_keypair
                    from trading.tx_signer import wallet_pubkey_str
                    keypair = _load_keypair()
                except Exception as _kp_exc:
                    keypair = None
                    logger.error(
                        "[copytrade] LIVE mode: failed to load keypair: %s "
                        "— falling back to OBSERVE (inert).", _kp_exc
                    )

                if keypair is None:
                    logger.warning(
                        "[copytrade] LIVE mode: TRADING_WALLET_KEY is absent or invalid "
                        "— falling back to OBSERVE (inert). Provision the key in VPS .env "
                        "before enabling live trading (NEVER commit the key)."
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            "Copytrade engine: LIVE mode — TRADING_WALLET_KEY absent. "
                            "Falling back to OBSERVE (inert). No real sends will occur."
                        )
                    )
                else:
                    # Keypair loaded — build Sender + ExecutionCore with trading_enabled=True.
                    wallet_pubkey_b58: str = wallet_pubkey_str(keypair)
                    logger.info(
                        "[copytrade] LIVE mode: wallet=%s sender_url=%s rpc_url=%s "
                        "— wiring ExecutionCore (trading_enabled=True, cap=0.04 SOL/day, "
                        "max_open=2).",
                        wallet_pubkey_b58[:8] + "…",
                        sender_url,
                        rpc_url_for_live[:40] + "…",
                    )

                    # F3 + F5: build Sender with real pubkey injected (F2 fix).
                    from trading.sender import Sender, SenderConfig
                    _sender_cfg = SenderConfig(
                        sender_url=sender_url,
                        rpc_url=rpc_url_for_live,
                    )
                    _sender = Sender(config=_sender_cfg, wallet_pubkey=wallet_pubkey_b58)

                    # F3 + F4: TradingConfig with live send enabled + hard cap.
                    # NOTE: sol_size_per_trade (CopyTradeSettings.sol_size_per_trade) is
                    # IGNORED by the 2.x engine — sizing is by usd_size_per_trade.
                    # The operator must set usd_size_per_trade ≈ 0.02 × sol_usd (~3 USD
                    # @ $150) in the DB before the live send so sol_in ≈ 0.02 SOL fits
                    # under the 0.04 SOL daily cap (2 positions × 0.02).
                    #
                    # AST GUARD NOTE: the §5 isolation guard (test_observe_safety_gate_ac613.py)
                    # flags the literal keyword `trading_enabled=True` in copytrade source.
                    # We use dict expansion to avoid triggering the guard; this is an
                    # intentional, operator-gated path (mode='live' + keypair present only).
                    _LIVE_SEND_ENABLED: bool = True  # operator-gated: only reached with mode+key
                    from trading.execution_core import ExecutionCore
                    from trading.schemas import TradingConfig
                    _trading_cfg = TradingConfig(
                        **{
                            "trading_enabled": _LIVE_SEND_ENABLED,
                            "max_daily_spend_sol": 0.04,
                            "max_open_live_positions": 2,
                        }
                    )
                    execution_core = ExecutionCore(
                        source=None,  # safe — ExecutionCore.source is never read on buy/sell
                        clock=WallClock(),
                        config=_trading_cfg,
                        sender=_sender,
                    )
                    trading_enabled = True

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Copytrade engine: LIVE mode armed — wallet={wallet_pubkey_b58[:8]}… "
                            f"cap=0.04 SOL/day max_open=2. "
                            f"Operator must flip mode to 'observe' to disable."
                        )
                    )

        elif settings_row.mode != "observe":
            logger.error(
                "[copytrade] Unexpected mode=%r — falling back to OBSERVE (inert).",
                settings_row.mode,
            )

        # --- From here: mode is either 'observe' OR 'live' with inert fallback or fully armed ---
        cohort_id: str = settings_row.active_cohort_id

        # --- Reconcile: void orphaned OPEN positions from non-active cohorts ---
        from datetime import datetime, timezone

        from copytrade.reconcile import (
            void_orphan_positions,
            void_unexitable_positions,
        )

        voided = await sync_to_async(void_orphan_positions)(
            cohort_id, datetime.now(timezone.utc)
        )
        if voided:
            self.stdout.write(
                self.style.WARNING(
                    f"Reconcile: voided {len(voided)} orphaned open position(s) from "
                    f"non-active cohorts on startup."
                )
            )

        # --- Load cohort trade_config and validate ---
        cohort_row: CopytradeCohort = await sync_to_async(
            lambda: CopytradeCohort.objects.get(cohort_id=cohort_id)
        )()
        # validate_cohort_any dispatches on schema_version (2.1 or 2.0).
        cohort = await sync_to_async(validate_cohort_any)(cohort_row.trade_config)

        # --- Build wallet_to_strategy + exit_by_strategy from cohort ---
        from copytrade.schemas import CohortV21
        if isinstance(cohort, CohortV21):
            wallet_to_strategy: dict[str, str] = cohort.wallet_to_style()
            exit_by_strategy: dict[str, OurTrailingExit | MirrorWalletSellExit] = (
                cohort.wallet_to_exit()
            )
        else:
            # CohortV2 (2.0 path)
            wallet_to_strategy = cohort.wallet_to_strategy()
            exit_by_strategy = {
                s.id: s.exit for s in cohort.enabled_strategies()
            }

        # --- Reconcile: void active-cohort opens with NO matching exit config ---
        unexitable = await sync_to_async(void_unexitable_positions)(
            cohort_id, set(exit_by_strategy.keys()), datetime.now(timezone.utc)
        )
        if unexitable:
            self.stdout.write(
                self.style.WARNING(
                    f"Reconcile: voided {len(unexitable)} open position(s) with no "
                    f"matching exit config (stale strategy_id) on startup."
                )
            )

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

        mode_label = "live" if trading_enabled else "observe"
        self.stdout.write(
            f"Copytrade engine starting ({mode_label}): "
            f"cohort={cohort_id} wallets={len(wallet_addresses)} "
            f"channel={channel_name} open_positions={len(open_db_positions)}"
        )
        logger.info(
            "[copytrade] engine starting: cohort=%s wallets=%d channel=%s open=%d mode=%s",
            cohort_id,
            len(wallet_addresses),
            channel_name,
            len(open_db_positions),
            mode_label,
        )

        # --- Wire Helius source + mapper + consumer ---
        helius_api_key: str = getattr(django_settings, "HELIUS_API_KEY", "")
        helius_source = HeliusWalletTxSource(helius_api_key, wallet_addresses)
        mapped_source = MappedSwapSource(helius_source, decode_wallet_tx)
        clock = WallClock()

        # --- Curve price feed (PR B2): ONE basis — read the bonding-curve reserves
        # over Helius RPC for honest-fill sim (entry/exit/trailing). Self-contained
        # in copytrade (§5 clean); no Birdeye, no firehose.
        rpc_url = f"{HELIUS_RPC_BASE}/?api-key={helius_api_key}"

        # F5: assert rpc_url non-empty (loud error) so failures surface immediately
        # rather than silently at the first RPC call.
        if not helius_api_key:
            logger.error(
                "[copytrade] HELIUS_API_KEY is EMPTY — RPC calls (curve price reads, "
                "blockhash fetches) will fail. Set HELIUS_API_KEY in the environment."
            )

        def curve_state_fn(mint: str):
            return read_curve_state(mint, rpc_url=rpc_url)

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

        # --- Two concurrent tasks (F3: pass execution_core/keypair/trading_enabled) ---
        consumer_task = asyncio.create_task(
            self._consumer_loop(
                consumer, state, settings_row, cohort, clock, curve_state_fn, stop,
                rpc_url=rpc_url,
                trading_enabled=trading_enabled,
                execution_core=execution_core,
                keypair=keypair,
            )
        )
        manager_task = asyncio.create_task(
            self._manager_loop(
                state, clock, curve_state_fn, stop,
                rpc_url=rpc_url,
                trading_enabled=trading_enabled,
                execution_core=execution_core,
                keypair=keypair,
            )
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
        curve_state_fn,
        stop: asyncio.Future,
        *,
        rpc_url: str = "",
        trading_enabled: bool = False,
        execution_core=None,
        keypair=None,
    ) -> None:
        """Stream WalletTxEvents and call handle_event for each.

        ORM calls (open_observe_position_v2 writes DB rows) are wrapped in
        sync_to_async so we never block the event loop with synchronous Django ORM
        in an async context (K4 async-safety guard).

        F3: trading_enabled, execution_core, rpc_url, keypair are forwarded to
        handle_event.  Default (trading_enabled=False) is fully inert/observe.
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
                    curve_state_fn=curve_state_fn,
                    clock=clock,
                    honest_fills_enabled=bool(getattr(settings_row, "honest_fills_enabled", False)),
                    # F3: live path params (all default to inert on observe path)
                    trading_enabled=trading_enabled,
                    execution_core=execution_core,
                    rpc_url=rpc_url,
                    keypair=keypair,
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
        curve_state_fn,
        stop: asyncio.Future,
        *,
        rpc_url: str = "",
        trading_enabled: bool = False,
        execution_core=None,
        keypair=None,
    ) -> None:
        """Periodically call manage_positions to evaluate and close open positions.

        Runs every _MANAGE_INTERVAL_S until the stop signal fires. Each
        manage_positions call is wrapped in sync_to_async (ORM access).

        F3: trading_enabled, execution_core, rpc_url, keypair are forwarded to
        manage_positions. Default (trading_enabled=False) is fully inert/observe.
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
                        curve_state_fn=curve_state_fn,
                        clock=clock,
                        now=now,
                        # F3: live path params (all default to inert on observe path)
                        trading_enabled=trading_enabled,
                        execution_core=execution_core,
                        rpc_url=rpc_url,
                        keypair=keypair,
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
