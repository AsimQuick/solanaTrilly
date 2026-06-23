# ---
# module: copytrade.soak_killswitch
# sprint: sprint-15
# story: US-85
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: logging, dataclasses, typing
# ---
"""Kill-switch health monitor for the curvestage soak (US-85).

KILL-SWITCH RULE:
  Halt the soak when the selected grad-rate drops below KILLSWITCH_GRAD_RATE_FLOOR
  over >= KILLSWITCH_MIN_TRADES real on-curve entries.

  Denominator counts ONLY real on-curve entries (RESULT_GRAD or RESULT_NO_GRAD).
  VOID and ENTRY_REJECTED rows are EXCLUDED from the denominator.
  This fixes the curvestage_hc.sh denominator bug (it counted all rows including
  VOID/ENTRY_REJECTED, diluting the grad-rate and producing false negatives).

RATIONALE:
  Offline soak (Jun 20-23) showed 60-69% selected grad-rate.  A 40% floor is a
  conservative kill threshold that trips only on genuine regime degradation.
  Grad-rate converges faster than PnL (N=15 is achievable in hours of soak),
  making it the FAST leading health metric.

SOAK JUDGMENT TRINITY (documented per AC-85.3):
  (a) Judge on firehose-reconstructed real fills (resoak_harness), NEVER dashboard.
  (b) Selected grad-rate vs ~39% breakeven is the leading kill metric (this module).
  (c) Net $/tr after honest fill (resoak_harness report).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("copytrade")

# ---------------------------------------------------------------------------
# Kill-switch configuration constants
# ---------------------------------------------------------------------------

#: Selected grad-rate floor — if grad-rate drops below this, kill the soak.
KILLSWITCH_GRAD_RATE_FLOOR: float = 0.40

#: Minimum real on-curve trades before the kill-switch can trigger.
#: Below this, the sample is too small for a reliable kill decision.
KILLSWITCH_MIN_TRADES: int = 15

# ---------------------------------------------------------------------------
# Row-type constants (the denominator logic hinges on these)
# ---------------------------------------------------------------------------

RESULT_GRAD = "GRAD"               # Selected, and token graduated
RESULT_NO_GRAD = "NO_GRAD"         # Selected, but token did NOT graduate
RESULT_VOID = "VOID"               # Gate rejected before entry (NOT in denominator)
RESULT_ENTRY_REJECTED = "ENTRY_REJECTED"  # Entry rejected by honest fill (NOT in denominator)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SoakTrade:
    """A single soak trade record for kill-switch evaluation.

    Parameters
    ----------
    result:
        One of RESULT_GRAD, RESULT_NO_GRAD, RESULT_VOID, RESULT_ENTRY_REJECTED.
        GRAD/NO_GRAD contribute to the denominator; VOID/ENTRY_REJECTED do not.
    mint:
        Token mint (optional, for logging).
    """
    result: str
    mint: Optional[str] = None


@dataclass
class KillSwitchState:
    """Running state of the kill-switch monitor.

    Tracks:
        n_real_entries: denominator (GRAD + NO_GRAD rows only)
        n_grad: numerator (GRAD rows only)
        halted: True if the kill condition has been met
        halt_reason: human-readable string describing why the soak was halted
    """
    n_real_entries: int = 0
    n_grad: int = 0
    halted: bool = False
    halt_reason: Optional[str] = None
    # Full history of trades received (for audit / replay)
    trades: list[SoakTrade] = field(default_factory=list)

    @property
    def grad_rate(self) -> Optional[float]:
        """Selected grad-rate; None when no real entries yet."""
        if self.n_real_entries == 0:
            return None
        return self.n_grad / self.n_real_entries

    @property
    def can_kill(self) -> bool:
        """True when the sample is large enough to trigger the kill-switch."""
        return self.n_real_entries >= KILLSWITCH_MIN_TRADES

    def to_dict(self) -> dict:
        """Serialisable summary for logging / API."""
        return {
            "n_real_entries": self.n_real_entries,
            "n_grad": self.n_grad,
            "grad_rate": self.grad_rate,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "can_kill": self.can_kill,
            "killswitch_grad_rate_floor": KILLSWITCH_GRAD_RATE_FLOOR,
            "killswitch_min_trades": KILLSWITCH_MIN_TRADES,
        }


# ---------------------------------------------------------------------------
# Kill-switch evaluation
# ---------------------------------------------------------------------------

def evaluate_killswitch(
    trades: list[SoakTrade],
    *,
    grad_rate_floor: float = KILLSWITCH_GRAD_RATE_FLOOR,
    min_trades: int = KILLSWITCH_MIN_TRADES,
) -> KillSwitchState:
    """Evaluate the kill-switch against a list of soak trade records.

    DENOMINATOR: counts only RESULT_GRAD and RESULT_NO_GRAD rows.
    VOID and ENTRY_REJECTED rows are EXCLUDED from both numerator and denominator.

    Parameters
    ----------
    trades:
        Full list of soak trades (any order).
    grad_rate_floor:
        Kill threshold (default KILLSWITCH_GRAD_RATE_FLOOR = 0.40).
    min_trades:
        Minimum real-entry trades before kill is allowed (default KILLSWITCH_MIN_TRADES = 15).

    Returns
    -------
    KillSwitchState with .halted=True if the kill condition is met.
    """
    state = KillSwitchState(trades=list(trades))

    for trade in trades:
        if trade.result in (RESULT_GRAD, RESULT_NO_GRAD):
            state.n_real_entries += 1
            if trade.result == RESULT_GRAD:
                state.n_grad += 1

    # The kill-switch can only fire when the sample is large enough
    if state.n_real_entries >= min_trades:
        rate = state.grad_rate  # not None here since n_real_entries > 0
        if rate < grad_rate_floor:
            state.halted = True
            state.halt_reason = (
                f"selected grad-rate {rate:.1%} < floor {grad_rate_floor:.0%} "
                f"over {state.n_real_entries} real entries "
                f"(min={min_trades})"
            )
            logger.warning(
                "[killswitch] HALT: %s", state.halt_reason,
            )
        else:
            logger.info(
                "[killswitch] healthy: grad-rate=%.1f%% (%d/%d) — above %.0f%% floor",
                rate * 100, state.n_grad, state.n_real_entries, grad_rate_floor * 100,
            )
    else:
        logger.debug(
            "[killswitch] sample too small to kill (%d/%d real entries so far)",
            state.n_real_entries, min_trades,
        )

    return state


def check_killswitch_from_resoak_report(report: dict) -> KillSwitchState:
    """Convenience wrapper: evaluate kill-switch from a resoak_harness report dict.

    Constructs SoakTrade records from the report's 'trades' list (each entry
    must have a 'graduated' bool and an 'entry_valid' bool to classify GRAD /
    NO_GRAD / ENTRY_REJECTED).

    Parameters
    ----------
    report:
        Dict with key 'trades': list of dicts, each with keys:
            - 'graduated': bool
            - 'entry_valid': bool  (False = ENTRY_REJECTED)
            - 'mint': str (optional)
    """
    trades = []
    for row in report.get("trades", []):
        if not row.get("entry_valid", True):
            result = RESULT_ENTRY_REJECTED
        elif row.get("graduated", False):
            result = RESULT_GRAD
        else:
            result = RESULT_NO_GRAD
        trades.append(SoakTrade(result=result, mint=row.get("mint")))

    return evaluate_killswitch(trades)
