# ---
# module: core.management.commands.run_listener
# sprint: sprint-4, sprint-5
# story: US-16 AC-16.3, US-22 AC-22.1, US-22 AC-22.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: django, asyncio, os, types,
#               core.tape.birdeye_swap_source, core.tape.mapped_source,
#               core.tape.bounded_source, core.tape.birdeye_swap_mapper,
#               core.tape.recorder, core.tape.lake_writer, core.tape.swap_writer,
#               core.clock
# ---
"""run_listener — entry point for the dedicated listener container.

The #289 lesson: detection, recorder, and reconciler MUST NEVER share the
web/gunicorn process.  This management command is the sole entry point for
the standalone 'listener' service defined in docker-compose.yml and
docker-compose.staging.yml.

Two modes:

  IDLE (default) — no FIREHOSE_MINT set: the container stays up but records
  nothing (the firehose is deliberately operator-gated; never always-on —
  PRD §15.7 budget discipline).

  DELIBERATE ACTIVATION — FIREHOSE_MINT set (AC-22.3): build a fully-wired
  recorder (BirdeyeSwapSource -> map_birdeye_swap -> time-box) and run it for a
  bounded window; landed PumpSwap swaps flow Birdeye SUBSCRIBE_TXS -> recorder
  -> the 'swaps' DB table + a jsonl.gz lake part, then the process exits.

The DataSource seam (Principle #7) ensures the same recorder + mapper run
unchanged in offline pytest replay tests (US-21) — only the injected source
differs (BirdeyeSwapSource live vs ReplaySource over the banked golden capture).

AC-22.1/22.3: build_swap_recorder() is the ONLY place where BirdeyeSwapSource and
WallClock are instantiated.  Concrete sources and wall clocks live ONLY here
(the listener/adapter wiring layer) — never on the core recorder path.
"""
import asyncio
import logging
import signal
import sys
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand

from core.clock import WallClock
from core.tape.birdeye_swap_mapper import map_birdeye_swap
from core.tape.birdeye_swap_source import BirdeyeSwapSource
from core.tape.bounded_source import BoundedSource
from core.tape.lake_writer import LakeWriter
from core.tape.mapped_source import MappedSwapSource
from core.tape.recorder import TapeRecorder
from core.tape.swap_writer import SwapWriter

logger = logging.getLogger(__name__)

DEFAULT_DURATION_SECONDS = 300
MAX_DURATION_SECONDS = 1800  # PRD §15.7: a single activation is time-boxed <= 30 min


# ---------------------------------------------------------------------------
# Adapter wiring — the ONLY place where concrete sources and wall clock live
# ---------------------------------------------------------------------------


def build_swap_recorder(
    api_key: str,
    mint: str,
    *,
    graduated_block_time: int | None = None,
    max_events: int | None = None,
    max_seconds: float | None = None,
    lake_base_dir: str = "lake/tapes",
) -> TapeRecorder:
    """Build a fully-wired TapeRecorder for a deliberate Birdeye swap activation.

    The ONLY place where BirdeyeSwapSource and WallClock are instantiated
    (Principle #7 / PRD §4 / US-2 static-analysis guard).  Wiring chain:

        BirdeyeSwapSource(raw Birdeye SUBSCRIBE_TXS)
          -> MappedSwapSource(map_birdeye_swap)     # raw -> internal §7.1 shape
          -> BoundedSource(max_events, max_seconds) # time-box so writes land
          -> TapeRecorder(+ token_store, LakeWriter, SwapWriter)

    Args:
        api_key:              Birdeye API key (X-API-KEY).
        mint:                 Token mint to subscribe swap events for.
        graduated_block_time: The token's graduation unix-time; anchors rel
                              (§7.1 — NEVER the first swap's time).  When None,
                              token_store is empty and no swaps normalize
                              (structural / no-DB use).
        max_events:           Stop after N swaps (None = rely on the time-box).
        max_seconds:          Time-box in seconds (None = until source ends).
        lake_base_dir:        Base dir for the jsonl.gz lake parts.
    """
    source: BirdeyeSwapSource = BirdeyeSwapSource(api_key=api_key, mint=mint)
    bounded = BoundedSource(
        MappedSwapSource(source, map_birdeye_swap),
        max_events=max_events,
        max_seconds=max_seconds,
    )
    clock = WallClock()
    token_store: dict[str, object] = {}
    if graduated_block_time is not None:
        token_store = {mint: SimpleNamespace(graduated_block_time=int(graduated_block_time))}
    return TapeRecorder(
        source=bounded,
        clock=clock,
        token_store=token_store,
        swap_source="birdeye_live",
        swap_phase="pre",
        lake_writer=LakeWriter(lake_base_dir),
        swap_writer=SwapWriter(),
    )


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Run the dedicated listener process.  Idle by default; pass --mint "
        "(+ --graduated-bt) to run a deliberate, time-boxed Birdeye swap "
        "activation that records to the 'swaps' table + lake.  Never runs in gunicorn."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mint", default=None,
            help="Token mint to subscribe; omit for idle mode (record nothing).")
        parser.add_argument(
            "--graduated-bt", type=int, default=None,
            help="Token graduation unix-time; anchors rel (§7.1). Required with --mint.")
        parser.add_argument(
            "--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS,
            help=f"Activation time-box (default {DEFAULT_DURATION_SECONDS}, max {MAX_DURATION_SECONDS}).")
        parser.add_argument(
            "--max-events", type=int, default=0,
            help="Stop after N swaps (0 = rely on the time-box).")
        parser.add_argument(
            "--lake-dir", default="lake/tapes",
            help="Base dir for the jsonl.gz lake parts.")

    def handle(self, *args, **options):
        try:
            asyncio.run(self._run(options))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Listener stopped by interrupt."))
            sys.exit(0)

    async def _run(self, options: dict) -> None:
        mint = (options.get("mint") or "").strip()
        if mint:
            await self._run_activation(mint, options)
            return
        await self._run_idle()

    async def _run_activation(self, mint: str, options: dict) -> None:
        """Deliberate, time-boxed firehose activation (AC-22.3)."""
        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        grad_bt = options.get("graduated_bt")
        if not api_key:
            self.stdout.write(self.style.ERROR(
                "--mint given but BIRDEYE_API_KEY missing — cannot activate."))
            sys.exit(2)
        if grad_bt is None:
            self.stdout.write(self.style.ERROR(
                "--mint given but --graduated-bt missing — rel must be anchored to "
                "the token's graduation time (§7.1); refusing to guess."))
            sys.exit(2)

        duration = min(int(options["duration_seconds"]), MAX_DURATION_SECONDS)
        max_events = int(options.get("max_events") or 0) or None
        lake_dir = options.get("lake_dir") or "lake/tapes"

        self.stdout.write(self.style.SUCCESS(
            f"Deliberate firehose activation: mint={mint} duration<={duration}s "
            f"max_events={max_events or 'unbounded'} (PRD §15.7)."))
        logger.info("Firehose activation start: mint=%s duration=%ss", mint, duration)

        recorder = build_swap_recorder(
            api_key,
            mint,
            graduated_block_time=int(grad_bt),
            max_events=max_events,
            max_seconds=duration,
            lake_base_dir=lake_dir,
        )
        # recorder.run() drives stamp_events, which connect()s the source, records
        # every landed swap, then (after the time-box) writes the lake + 'swaps'
        # rows and disconnect()s.
        await recorder.run()

        recorded = len(recorder.normalized_swaps)
        skipped = len(recorder.skipped_degenerate)
        self.stdout.write(self.style.SUCCESS(
            f"Activation complete: {recorded} swap(s) recorded to 'swaps' + lake; "
            f"{skipped} degenerate skipped."))
        logger.info("Firehose activation done: recorded=%d skipped=%d", recorded, skipped)

    async def _run_idle(self) -> None:
        """Idle mode: stay up, record nothing (firehose is deliberately gated)."""
        loop = asyncio.get_running_loop()
        stop: asyncio.Future = loop.create_future()

        def _handle_signal() -> None:
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        api_key = getattr(settings, "BIRDEYE_API_KEY", None)
        self.stdout.write(
            "Listener container running (idle).\n"
            f"BIRDEYE_API_KEY {'present' if api_key else 'NOT set'}.\n"
            "Pass --mint + --graduated-bt to run a deliberate, "
            "time-boxed swap activation (AC-22.3)."
        )
        logger.info("Listener idle — no FIREHOSE_MINT set; recording nothing.")
        await stop
        self.stdout.write(self.style.WARNING("Listener shut down."))
