# ---
# module: core.tests.test_label_integrity_ac353
# sprint: sprint-8
# story: US-35 AC-35.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.idle_kill, core.tests.test_idle_kill_two_tier_ac352, datetime
# ---
"""AC-35.3 — Label integrity (D4): graduated tokens are NEVER truncated before
outcome.window_s closes.

The D4 invariant (PRD §5.2 / oracle §2): idle_kill_ttl_s (the post-grad TTL)
MUST be >= outcome.window_s.  This file verifies the end-to-end runtime
consequence of that invariant at the IdleKillMonitor level:

  (a) A graduated mint that has been idle LONGER than pre_grad_idle_kill_ttl_s
      but SHORTER than outcome.window_s is NOT deactivated.  Its label window
      is protected by the post-grad TTL, not the aggressive pre-grad TTL.

  (b) The same mint, once it has been idle for idle_kill_ttl_s seconds (which
      is >= outcome.window_s), IS eligible for deactivation — confirming that
      the protected TTL (not the pre-grad TTL) governs post-graduation kill.

Scenario constants:
  PRE_GRAD_TTL   = 300 s  (aggressive kill — would fire on ungraduated mints)
  OUTCOME_WINDOW = 1800 s (v3.2 label horizon tr30_t1800)
  POST_GRAD_TTL  = 1800 s (idle_kill_ttl_s == outcome.window_s — tightest valid value)

  T0             = graduation instant (and last-swap time for the mint)
  T0 + 400 s     = past PRE_GRAD_TTL (300 s) but still within OUTCOME_WINDOW
  T0 + 1800 s    = exactly at POST_GRAD_TTL == OUTCOME_WINDOW

ImportError trap (H1)
---------------------
The module-level imports from test_idle_kill_two_tier_ac352 below are the
primary trip-wire: if any of the AC-35.2 test functions are deleted or renamed,
Python raises ImportError during pytest collection, failing the CI 'test' job
BEFORE any tests run — no second workflow file required (H1).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core.tape.idle_kill import IdleKillMonitor

# ---------------------------------------------------------------------------
# ImportError trap (H1) — wire the AC-35.2 two-tier-idle tests so they
# cannot silently vanish from CI.  Deleting or renaming these functions
# breaks pytest collection immediately.
# ---------------------------------------------------------------------------
from core.tests.test_idle_kill_two_tier_ac352 import (
    test_grad_mint_dropped_at_idle_kill_ttl as _ac352_grad_dropped,
)
from core.tests.test_idle_kill_two_tier_ac352 import (
    test_grad_mint_not_dropped_at_pre_grad_ttl as _ac352_grad_protected,
)
from core.tests.test_idle_kill_two_tier_ac352 import (
    test_grad_mint_reattaches_on_swap as _ac352_grad_reattach,
)
from core.tests.test_idle_kill_two_tier_ac352 import (
    test_ungrad_mint_dropped_at_pre_grad_ttl as _ac352_ungrad_dropped,
)
from core.tests.test_idle_kill_two_tier_ac352 import (
    test_ungrad_mint_reattaches_on_swap as _ac352_ungrad_reattach,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRE_GRAD_TTL = 300    # seconds — would fire on ungraduated mints
OUTCOME_WINDOW = 1800  # seconds — v3.2 label horizon (tr30_t1800)
POST_GRAD_TTL = 1800   # seconds — idle_kill_ttl_s; tightest valid value (== outcome.window_s)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # graduation instant

_MINT = "LABEL_INTEGRITY_MINT_111111111111111111111111111111111"

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "core" / "tests"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_resolver(pre_grad_ttl: int, post_grad_ttl: int):
    """Return a config resolver with the given two-tier TTL knobs."""
    cfg = SimpleNamespace(
        tape=SimpleNamespace(
            pre_grad_idle_kill_ttl_s=pre_grad_ttl,
            idle_kill_ttl_s=post_grad_ttl,
        )
    )
    return lambda: cfg


# ---------------------------------------------------------------------------
# Test (a): graduated mint idle PAST pre_grad_ttl but WITHIN outcome.window_s
#           is NOT killed — the label window is protected.
# ---------------------------------------------------------------------------


def test_graduated_token_not_killed_within_outcome_window() -> None:
    """Part (a): graduated mint past pre_grad_ttl but inside outcome.window_s → NOT deactivated.

    Setup:
      - Mint graduates at T0; last swap also at T0 (idle since graduation).
      - Check at T0 + 400 s: elapsed (400 s) > PRE_GRAD_TTL (300 s), confirming
        the pre-grad TTL *would* have fired — but the mint is graduated, so the
        post-grad TTL (POST_GRAD_TTL = 1800 s) governs instead.
      - Elapsed (400 s) < POST_GRAD_TTL (1800 s), so the mint must survive.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_MINT, T0)
    monitor.mark_graduated(_MINT, T0)

    # Confirm: 400 s is past the pre-grad TTL threshold
    assert 400 > PRE_GRAD_TTL, "Sanity: check time must exceed pre_grad_ttl"
    # Confirm: 400 s is still within the outcome / post-grad window
    assert 400 < POST_GRAD_TTL, "Sanity: check time must be inside post_grad_ttl"

    t_check = T0 + timedelta(seconds=400)
    deactivated = monitor.check_idle(t_check)

    assert _MINT not in deactivated, (
        f"Graduated mint must NOT be deactivated {400} s after graduation "
        f"(past pre_grad_ttl={PRE_GRAD_TTL} s but within outcome.window_s="
        f"{OUTCOME_WINDOW} s / idle_kill_ttl_s={POST_GRAD_TTL} s). "
        "D4: label window must be protected."
    )
    assert not monitor.is_deactivated(_MINT)
    assert len(monitor.deactivation_events) == 0


# ---------------------------------------------------------------------------
# Test (b): same mint that has BOTH passed outcome.window_s AND been idle for
#           idle_kill_ttl_s IS eligible for kill.
# ---------------------------------------------------------------------------


def test_graduated_token_eligible_after_outcome_window_and_idle() -> None:
    """Part (b): graduated mint idle for idle_kill_ttl_s (>= outcome.window_s) → IS deactivated.

    Setup:
      - Same mint, same graduation at T0, last swap at T0.
      - idle_kill_ttl_s = POST_GRAD_TTL = 1800 s = OUTCOME_WINDOW.
      - Check at T0 + 1800 s: elapsed >= idle_kill_ttl_s AND outcome.window_s
        has fully closed.  The mint is NOW eligible for deactivation.

    Confirms that the PROTECTED post-grad TTL (not the pre-grad TTL) is the
    actual kill trigger for graduated mints.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_MINT, T0)
    monitor.mark_graduated(_MINT, T0)

    # One second before POST_GRAD_TTL: still protected
    t_before = T0 + timedelta(seconds=POST_GRAD_TTL - 1)
    deactivated = monitor.check_idle(t_before)
    assert _MINT not in deactivated, (
        f"Graduated mint must NOT be killed 1 s before idle_kill_ttl_s "
        f"({POST_GRAD_TTL} s). Label window is still open."
    )
    assert not monitor.is_deactivated(_MINT)

    # Exactly at POST_GRAD_TTL: outcome.window_s has closed, idle TTL fires
    t_at = T0 + timedelta(seconds=POST_GRAD_TTL)
    deactivated = monitor.check_idle(t_at)
    assert _MINT in deactivated, (
        f"Graduated mint MUST be deactivated when idle for idle_kill_ttl_s "
        f"({POST_GRAD_TTL} s >= outcome.window_s={OUTCOME_WINDOW} s). "
        "Both the label window has closed and the idle TTL has elapsed."
    )
    assert monitor.is_deactivated(_MINT)
    assert len(monitor.deactivation_events) == 1
    assert monitor.deactivation_events[0] == (_MINT, t_at)


# ---------------------------------------------------------------------------
# Wire verification (human-readable layer on top of the ImportError trap)
# ---------------------------------------------------------------------------


def test_two_tier_idle_test_functions_are_callable() -> None:
    """Verify the five AC-35.2 wired functions are importable and callable.

    The module-level imports are the primary ImportError guard; this test adds a
    human-readable assertion layer so a CI failure names the missing function.
    """
    wired = {
        "test_ungrad_mint_dropped_at_pre_grad_ttl": _ac352_ungrad_dropped,
        "test_grad_mint_not_dropped_at_pre_grad_ttl": _ac352_grad_protected,
        "test_grad_mint_dropped_at_idle_kill_ttl": _ac352_grad_dropped,
        "test_ungrad_mint_reattaches_on_swap": _ac352_ungrad_reattach,
        "test_grad_mint_reattaches_on_swap": _ac352_grad_reattach,
    }
    for name, fn in wired.items():
        assert callable(fn), f"AC-35.2 wired function not callable: {name}"


def test_two_tier_idle_test_files_exist() -> None:
    """Verify the AC-35.2 and AC-35.3 test files exist at their canonical paths."""
    required = {
        "AC-35.2 two-tier idle tests": TESTS_DIR / "test_idle_kill_two_tier_ac352.py",
        "AC-35.3 label integrity tests": TESTS_DIR / "test_label_integrity_ac353.py",
    }
    for label, path in required.items():
        assert path.exists(), f"Required test file missing: {label} → {path}"
