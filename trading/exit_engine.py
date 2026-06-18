# ---
# module: trading.exit_engine
# sprint: sprint-13
# story: US-66 AC-66.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.schemas
# ---
"""Exit-rule engine implementing the PRD §10.1 priority ladder (AC-66.1).

Public API
----------
PositionSnapshot
    Injectable state snapshot — all inputs needed by evaluate_exit_rules.
    No DB session, no Redis, no network calls.

evaluate_exit_rules(position, config, now=None) -> str | None
    Returns the trigger name of the first rule that fires, or None.
    ``now`` is injectable for deterministic replay; defaults to datetime.now(UTC).

Priority ladder (§10.1 order):
    1  RUG_PULL          — deep drop, fires even inside grace
    2  DISASTER_CAP      — moderate deep drop, fires even inside grace
    3  STOP_LOSS         — ordinary loss gate, skipped inside grace window
    4  NEXT_POLL_GUARD   — first-poll price below entry (guards entry slippage)
    5  TAKE_PROFIT_PCT   — absolute percentage gain target
    6  AUTO_SELL_TIMER   — maximum hold time (hard deadline)
    7a CEILING           — extreme upside cap (before concentration/volume checks)
    7b VOLUME_COLLAPSE   — volume drops below entry * (1 - threshold)
    7c CONCENTRATION     — whale concentration grew beyond threshold
    8  TRAILING          — trailing stop (arms only if peak > entry * arm_multiple,
                           and NOT inside grace window)
    9  STALE             — catch-all timeout for positions that drift

Design constraints (AC-66.1):
    - NO Redis, NO DB session, NO network calls — fully injectable
    - Config-driven via trading.schemas.TradingConfig
    - ``now`` parameter for replay support
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from trading.schemas import TradingConfig

# ---------------------------------------------------------------------------
# PositionSnapshot
# ---------------------------------------------------------------------------


@dataclass
class PositionSnapshot:
    """Injectable state snapshot for evaluate_exit_rules (PRD §10.1 / AC-66.1).

    All time values must be UTC-aware datetimes.
    Optional fields fall open (rule disabled) when None.
    """

    entry_price: float
    current_price: float
    peak_price: float        # peak since open; equals entry_price if never moved up
    entry_ts: datetime       # position open timestamp (UTC-aware)

    # NEXT_POLL_GUARD: the price at the first Birdeye poll after entry (injectable).
    # Falls open (no fire) if None.  In replay: set to price at entry_ts + next_poll_guard_s.
    next_poll_snapshot_price: Optional[float] = None

    # VOLUME_COLLAPSE: falls open if either is None or entry_volume <= 0
    current_volume: Optional[float] = None
    entry_volume: Optional[float] = None

    # CONCENTRATION: falls open if either is None
    current_concentration: Optional[float] = None
    entry_concentration: Optional[float] = None


# ---------------------------------------------------------------------------
# evaluate_exit_rules
# ---------------------------------------------------------------------------


def evaluate_exit_rules(
    position: PositionSnapshot,
    config: TradingConfig,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Evaluate the §10.1 priority ladder and return the first trigger that fires.

    Parameters
    ----------
    position:
        Current state snapshot (entry, current, peak prices; timestamps; optional
        volume/concentration data).
    config:
        TradingConfig instance with all exit-rule thresholds.
    now:
        Current wall-clock time (UTC-aware).  Defaults to ``datetime.now(timezone.utc)``.
        Pass a fixed value for deterministic replay.

    Returns
    -------
    str
        The trigger name of the first rule that fires (e.g. ``"RUG_PULL"``).
    None
        No rule fired — position should remain open.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Derived scalars used throughout the ladder
    entry = position.entry_price
    current = position.current_price
    peak = position.peak_price

    seconds_held: float = (now - position.entry_ts).total_seconds()

    # Grace window: applies to STOP_LOSS and TRAILING.
    # - STOP_LOSS skipped inside grace (entry slippage reads as loss on thin curves).
    # - TRAILING never arms inside grace (PRD §10.1 explicit constraint).
    # - RUG_PULL and DISASTER_CAP fire inside grace — a deep drop is never entry noise.
    in_grace: bool = seconds_held < config.trailing_grace_s

    # -----------------------------------------------------------------------
    # 1. RUG_PULL (no grace)
    # -----------------------------------------------------------------------
    if entry > 0 and current <= entry * (1 - config.rug_pull_drop_pct / 100):
        return "RUG_PULL"

    # -----------------------------------------------------------------------
    # 2. DISASTER_CAP (no grace)
    # -----------------------------------------------------------------------
    if entry > 0 and current <= entry * (1 - config.disaster_cap_pct / 100):
        return "DISASTER_CAP"

    # -----------------------------------------------------------------------
    # 3. STOP_LOSS (WITH grace — skipped inside grace window)
    # -----------------------------------------------------------------------
    if not in_grace and entry > 0 and current <= entry * (1 - config.stop_loss_pct / 100):
        return "STOP_LOSS"

    # -----------------------------------------------------------------------
    # 4. NEXT_POLL_GUARD
    # Falls open when next_poll_snapshot_price is None.
    # -----------------------------------------------------------------------
    if (
        position.next_poll_snapshot_price is not None
        and entry > 0
        and position.next_poll_snapshot_price < entry
    ):
        return "NEXT_POLL_GUARD"

    # -----------------------------------------------------------------------
    # 5. TAKE_PROFIT_PCT
    # -----------------------------------------------------------------------
    if entry > 0 and current >= entry * (1 + config.take_profit_pct / 100):
        return "TAKE_PROFIT_PCT"

    # -----------------------------------------------------------------------
    # 6. AUTO_SELL_TIMER
    # -----------------------------------------------------------------------
    if seconds_held >= config.auto_sell_timer_s:
        return "AUTO_SELL_TIMER"

    # -----------------------------------------------------------------------
    # 7a. CEILING
    # ceiling_pct=300 means a 300% GAIN threshold → price >= entry * 4.0
    # -----------------------------------------------------------------------
    if entry > 0 and current >= entry * (1 + config.ceiling_pct / 100):
        return "CEILING"

    # -----------------------------------------------------------------------
    # 7b. VOLUME_COLLAPSE
    # Falls open when either volume field is None or entry_volume <= 0.
    # -----------------------------------------------------------------------
    if (
        position.current_volume is not None
        and position.entry_volume is not None
        and position.entry_volume > 0
        and position.current_volume
        < position.entry_volume * (1 - config.volume_collapse_threshold_pct / 100)
    ):
        return "VOLUME_COLLAPSE"

    # -----------------------------------------------------------------------
    # 7c. CONCENTRATION
    # Falls open when either concentration field is None.
    # -----------------------------------------------------------------------
    if (
        position.current_concentration is not None
        and position.entry_concentration is not None
        and (position.current_concentration - position.entry_concentration)
        > config.concentration_threshold_pct
    ):
        return "CONCENTRATION"

    # -----------------------------------------------------------------------
    # 8. TRAILING
    # Arms only when:
    #   - NOT inside grace window
    #   - peak >= entry * trailing_arm_multiple  (e.g. 1.05 → 5% gain required)
    #   - peak > 0 (sanity)
    #   - current <= peak * (1 - trailing_pct / 100)
    # -----------------------------------------------------------------------
    if (
        not in_grace
        and entry > 0
        and peak >= entry * config.trailing_arm_multiple
        and peak > 0
        and current <= peak * (1 - config.trailing_pct / 100)
    ):
        return "TRAILING"

    # -----------------------------------------------------------------------
    # 9. STALE (catch-all timeout)
    # -----------------------------------------------------------------------
    if seconds_held >= config.stale_timeout_s:
        return "STALE"

    return None
