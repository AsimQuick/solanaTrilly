# ---
# module: core.tape.idle_kill
# sprint: sprint-5
# story: US-20 AC-20.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.schemas, datetime, typing
# ---
"""IdleKillMonitor — per-mint TTL-based deactivation and re-attachment (D4).

Implements the idle-kill mechanism from PRD §6.2: a mint is deactivated after
idle_kill_ttl_s of no swaps, and RE-ATTACHES the moment a new swap arrives
(never goes blind on a quiet-then-pump token).

idle_kill_ttl_s is read from the injected config_resolver on every check_idle()
call — never from a hardcoded constant or os.getenv (Principle #1).  The §5.2
invariant (idle_kill_ttl_s >= outcome.window_s) is enforced at schema-validation
time by the Pydantic schema (US-10 / US-11); this module only honors the value.

Integration: IdleKillMonitor is injected into TapeRecorder (AC-20.2).
  - The recorder calls check_idle(timestamp) on EVERY event arrival so that
    non-swap events (heartbeats, ticks) still advance the deactivation timer.
  - The recorder calls record_swap(mint, timestamp) for each landed swap in the
    token_store, which updates last-seen time and re-attaches if deactivated.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.schemas import PipelineConfigSchema


class IdleKillMonitor:
    """Per-mint idle-kill tracker: deactivate on TTL, re-attach on next swap.

    Args:
        config_resolver: Callable returning PipelineConfigSchema | None.
                         Should be get_active_config (US-11 resolver).
                         Called on every check_idle() invocation so that
                         config changes take effect immediately (Principle #1).
    """

    def __init__(
        self,
        config_resolver: "Callable[[], PipelineConfigSchema | None]",
    ) -> None:
        self._config_resolver = config_resolver
        self._last_swap_time: dict[str, datetime] = {}
        self._deactivated: set[str] = set()
        self._deactivation_events: list[tuple[str, datetime]] = []
        self._reattachment_events: list[tuple[str, datetime]] = []

    def record_swap(self, mint: str, now: datetime) -> bool:
        """Record a landed swap for *mint* at time *now*.

        If mint is currently deactivated, re-attaches: clears the deactivated
        flag and logs a re-attachment event.  Always updates last_swap_time.

        Args:
            mint: Mint address of the token.
            now:  Timestamp from the injected Clock (never wall clock).

        Returns:
            True if a re-attachment occurred (mint was previously deactivated).
        """
        reattached = False
        if mint in self._deactivated:
            self._deactivated.discard(mint)
            self._reattachment_events.append((mint, now))
            reattached = True
        self._last_swap_time[mint] = now
        return reattached

    def check_idle(self, now: datetime) -> list[str]:
        """Deactivate any mints that have been idle for >= idle_kill_ttl_s.

        Reads idle_kill_ttl_s from the config_resolver on every call so that
        TTL changes in the active config take effect immediately (Principle #1).
        Returns immediately if no config is active.

        Args:
            now: Current time from the injected Clock (never wall clock).

        Returns:
            List of mint addresses newly deactivated in this call.
        """
        config = self._config_resolver()
        if config is None:
            return []
        ttl_s = config.tape.idle_kill_ttl_s
        newly_deactivated: list[str] = []
        for mint, last_time in self._last_swap_time.items():
            if mint not in self._deactivated:
                elapsed = (now - last_time).total_seconds()
                if elapsed >= ttl_s:
                    self._deactivated.add(mint)
                    self._deactivation_events.append((mint, now))
                    newly_deactivated.append(mint)
        return newly_deactivated

    def is_deactivated(self, mint: str) -> bool:
        """Return True if *mint* is currently in deactivated state."""
        return mint in self._deactivated

    @property
    def deactivated_mints(self) -> set[str]:
        """Snapshot of the currently deactivated mint addresses."""
        return set(self._deactivated)

    @property
    def deactivation_events(self) -> list[tuple[str, datetime]]:
        """Full audit log of (mint, deactivation_time) pairs, in order."""
        return list(self._deactivation_events)

    @property
    def reattachment_events(self) -> list[tuple[str, datetime]]:
        """Full audit log of (mint, reattachment_time) pairs, in order."""
        return list(self._reattachment_events)
