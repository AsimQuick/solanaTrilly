# ---
# module: core.tape.idle_kill
# sprint: sprint-5, sprint-8
# story: US-20 AC-20.2, US-35 AC-35.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.schemas, datetime, typing
# ---
"""IdleKillMonitor — per-mint TTL-based deactivation and re-attachment (D4).

Implements the idle-kill mechanism from PRD §6.2: a mint is deactivated after
idle_kill_ttl_s of no swaps, and RE-ATTACHES the moment a new swap arrives
(never goes blind on a quiet-then-pump token).

Two-tier TTL (AC-35.2):
  - UNgraduated mints: use pre_grad_idle_kill_ttl_s (default 300 s) so that
    tokens that never pop are evicted quickly without tying up tape slots.
  - GRADUATED mints:   use idle_kill_ttl_s (>= outcome.window_s) so the full
    outcome window is always captured after graduation.
  The tier snaps to the protected (post-grad) TTL the instant mark_graduated()
  is called; re-attachment (reattach:true) applies to BOTH tiers.

Both TTLs are read from the injected config_resolver on every check_idle()
call — never from hardcoded constants or os.getenv (Principle #1).  The §5.2
invariant (idle_kill_ttl_s >= outcome.window_s) is enforced at schema-validation
time by the Pydantic schema (US-10 / US-11); this module only honors the value.

Integration: IdleKillMonitor is injected into TapeRecorder (AC-20.2).
  - The recorder calls check_idle(timestamp) on EVERY event arrival so that
    non-swap events (heartbeats, ticks) still advance the deactivation timer.
  - The recorder calls record_swap(mint, timestamp) for each landed swap in the
    token_store, which updates last-seen time and re-attaches if deactivated.
  - The recorder (or the graduation detector) calls mark_graduated(mint, now)
    at the graduation instant, snapping the mint to the post-grad TTL.
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
        # Two-tier TTL state (AC-35.2)
        self._graduated: set[str] = set()
        self._graduation_events: list[tuple[str, datetime]] = []

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

    def mark_graduated(self, mint: str, now: datetime) -> None:
        """Mark *mint* as graduated, snapping it to the post-grad (protected) TTL.

        From this instant forward, check_idle() uses idle_kill_ttl_s for this
        mint instead of pre_grad_idle_kill_ttl_s.  Idempotent: calling again
        for an already-graduated mint appends another graduation_event but has
        no other effect.

        Args:
            mint: Mint address of the token.
            now:  Graduation timestamp from the injected Clock (never wall clock).
        """
        self._graduated.add(mint)
        self._graduation_events.append((mint, now))

    def check_idle(self, now: datetime) -> list[str]:
        """Deactivate any mints that have been idle for >= their effective TTL.

        Two-tier TTL logic (AC-35.2):
          - Graduated mints    → idle_kill_ttl_s        (post-grad / protected)
          - UNgraduated mints  → pre_grad_idle_kill_ttl_s

        Both values are read from the config_resolver on every call so that
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
        post_grad_ttl_s = config.tape.idle_kill_ttl_s
        pre_grad_ttl_s = config.tape.pre_grad_idle_kill_ttl_s
        newly_deactivated: list[str] = []
        for mint, last_time in self._last_swap_time.items():
            if mint not in self._deactivated:
                ttl_s = post_grad_ttl_s if mint in self._graduated else pre_grad_ttl_s
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
    def graduated_mints(self) -> set[str]:
        """Snapshot of all mint addresses that have been marked graduated."""
        return set(self._graduated)

    @property
    def deactivation_events(self) -> list[tuple[str, datetime]]:
        """Full audit log of (mint, deactivation_time) pairs, in order."""
        return list(self._deactivation_events)

    @property
    def reattachment_events(self) -> list[tuple[str, datetime]]:
        """Full audit log of (mint, reattachment_time) pairs, in order."""
        return list(self._reattachment_events)

    @property
    def graduation_events(self) -> list[tuple[str, datetime]]:
        """Full audit log of (mint, graduation_time) pairs, in order."""
        return list(self._graduation_events)
