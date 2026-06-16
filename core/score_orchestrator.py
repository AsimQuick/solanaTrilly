# ---
# module: core.score_orchestrator
# sprint: sprint-6
# story: US-25 AC-25.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.clock, core.snapshot_fetcher, core.resolver, datetime, typing
# ---
"""ScoreTimeOrchestrator — fires EXACTLY ONE score-time snapshot per graduated token.

On graduation, orchestrate() reads score_at_elapsed_s from config_fn() — which
defaults to get_active_config() (the US-11 resolver, Principle #1).  It NEVER
reads from a hardcoded constant or os.getenv.

The score time is computed as:
    score_time = graduated_at + timedelta(seconds=config.scoring.score_at_elapsed_s)

If the injected clock has reached score_time, the SnapshotFetcher is called once
and the raw payload is returned.  If score time has not been reached, None is
returned immediately (the caller is responsible for retrying at the right time,
e.g. via a Celery countdown task).

The config_fn and clock are both injectable for replay-testability (Principle #7).
No concrete DataSource class is imported here — the seam is encapsulated inside
the SnapshotFetcher dependency.
"""
from datetime import datetime, timedelta
from typing import Callable, Optional

from core.clock import Clock
from core.resolver import get_active_config
from core.snapshot_fetcher import SnapshotFetcher


class ScoreTimeOrchestrator:
    """Fires a score-time snapshot for a graduated token when score time arrives.

    Reads score_at_elapsed_s from config_fn() (default: get_active_config — the
    US-11 resolver, Principle #1).  The clock is injected — all time reads use
    self._clock.now(), never datetime.now() or time.time() (Principle #7).

    Call orchestrate(mint, graduated_at):
      - Returns the raw snapshot dict when clock.now() >= score_time.
      - Returns None when score time has not yet been reached, or when the mint
        was already fetched (SnapshotFetcher at-most-one guard, §6.3).
    """

    def __init__(
        self,
        fetcher: SnapshotFetcher,
        clock: Clock,
        config_fn: Optional[Callable] = None,
    ) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self._config_fn = config_fn if config_fn is not None else get_active_config

    def orchestrate(self, mint: str, graduated_at: datetime) -> Optional[dict]:
        """Fire a score-time snapshot if the clock has reached score_at_elapsed_s.

        Reads score_at_elapsed_s exclusively from config_fn() (Principle #1 —
        never a hardcoded constant or os.getenv).  Computes:
            score_time = graduated_at + timedelta(seconds=score_at_elapsed_s)

        If clock.now() >= score_time, calls fetcher.fetch_and_persist() with
        elapsed_s=score_at_elapsed_s and returns the raw snapshot dict.
        Returns None if score time has not been reached yet.

        The SnapshotFetcher's at-most-one guard (§6.3) ensures a second call for
        the same mint is a no-op — no second DataSource read is attempted.
        """
        config = self._config_fn()
        score_at_elapsed_s = config.scoring.score_at_elapsed_s
        score_time = graduated_at + timedelta(seconds=score_at_elapsed_s)
        if self._clock.now() < score_time:
            return None
        return self._fetcher.fetch_and_persist(mint, elapsed_s=score_at_elapsed_s)
