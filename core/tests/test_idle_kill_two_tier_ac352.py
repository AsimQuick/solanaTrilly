# ---
# module: core.tests.test_idle_kill_two_tier_ac352
# sprint: sprint-8
# story: US-35 AC-35.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.idle_kill, datetime
# ---
"""AC-35.2 — Two-tier idle-kill TTL: pre_grad vs post-grad, reattach for both.

Tests that IdleKillMonitor applies the correct TTL tier to each mint and that
re-attachment fires for both tiers.  No DB, no Django — pure unit tests using
SimpleNamespace config mocks (Principle #1: config comes from the injected
resolver, never from os.getenv).

Constants:
  PRE_GRAD_TTL = 30 s   (fast eviction of tokens that never pop)
  POST_GRAD_TTL = 300 s (protected window — covers the outcome window)
  T0 = 2026-01-01 12:00:00 UTC

Tests:
  1. test_ungrad_mint_dropped_at_pre_grad_ttl
       Ungrad mint: NOT deactivated before pre_grad_ttl; deactivated AT pre_grad_ttl.
  2. test_grad_mint_not_dropped_at_pre_grad_ttl
       Grad mint: NOT deactivated at pre_grad_ttl (protected TTL is longer).
  3. test_grad_mint_dropped_at_idle_kill_ttl
       Grad mint: deactivated when elapsed >= post_grad (idle_kill_ttl_s).
  4. test_ungrad_mint_reattaches_on_swap
       Ungrad mint deactivated, then record_swap returns True and is_deactivated False.
  5. test_grad_mint_reattaches_on_swap
       Grad mint deactivated, then record_swap returns True and is_deactivated False.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.tape.idle_kill import IdleKillMonitor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRE_GRAD_TTL = 30    # seconds — fast eviction for ungraduated mints
POST_GRAD_TTL = 300  # seconds — protected window for graduated mints
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

_UNGRAD_MINT = "UNGRAD_MINT_111111111111111111111111111111111111111111"
_GRAD_MINT   = "GRAD_MINT_1111111111111111111111111111111111111111111"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_resolver(pre_grad_ttl: int, post_grad_ttl: int):
    """Return a config resolver lambda with two-tier TTL knobs."""
    config = SimpleNamespace(
        tape=SimpleNamespace(
            pre_grad_idle_kill_ttl_s=pre_grad_ttl,
            idle_kill_ttl_s=post_grad_ttl,
        )
    )
    return lambda: config


# ---------------------------------------------------------------------------
# Test 1: ungrad mint dropped at pre_grad_ttl
# ---------------------------------------------------------------------------


def test_ungrad_mint_dropped_at_pre_grad_ttl() -> None:
    """Ungrad mint is NOT deactivated before PRE_GRAD_TTL; deactivated AT PRE_GRAD_TTL.

    No mark_graduated call — mint stays in the pre-grad tier.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_UNGRAD_MINT, T0)

    # One second before pre_grad_ttl: still active
    before = T0 + timedelta(seconds=PRE_GRAD_TTL - 1)
    result = monitor.check_idle(before)
    assert _UNGRAD_MINT not in result, (
        "Ungrad mint must NOT be deactivated 1 s before pre_grad_idle_kill_ttl_s."
    )
    assert not monitor.is_deactivated(_UNGRAD_MINT)

    # Exactly at pre_grad_ttl: deactivated
    at_pre = T0 + timedelta(seconds=PRE_GRAD_TTL)
    result = monitor.check_idle(at_pre)
    assert _UNGRAD_MINT in result, (
        "Ungrad mint MUST be deactivated when elapsed >= pre_grad_idle_kill_ttl_s."
    )
    assert monitor.is_deactivated(_UNGRAD_MINT)
    assert len(monitor.deactivation_events) == 1
    assert monitor.deactivation_events[0] == (_UNGRAD_MINT, at_pre)


# ---------------------------------------------------------------------------
# Test 2: grad mint NOT dropped at pre_grad_ttl
# ---------------------------------------------------------------------------


def test_grad_mint_not_dropped_at_pre_grad_ttl() -> None:
    """Graduated mint is NOT deactivated at PRE_GRAD_TTL; the post-grad TTL applies.

    mark_graduated is called before check_idle so the mint is in the post-grad tier.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_GRAD_MINT, T0)
    monitor.mark_graduated(_GRAD_MINT, T0)

    # At pre_grad_ttl: graduated mint is still protected
    at_pre = T0 + timedelta(seconds=PRE_GRAD_TTL)
    result = monitor.check_idle(at_pre)
    assert _GRAD_MINT not in result, (
        "Grad mint must NOT be deactivated at pre_grad_idle_kill_ttl_s; "
        "it uses the post-grad (idle_kill_ttl_s) TTL."
    )
    assert not monitor.is_deactivated(_GRAD_MINT)


# ---------------------------------------------------------------------------
# Test 3: grad mint dropped at post-grad idle_kill_ttl_s
# ---------------------------------------------------------------------------


def test_grad_mint_dropped_at_idle_kill_ttl() -> None:
    """Graduated mint is deactivated when elapsed >= POST_GRAD_TTL (idle_kill_ttl_s)."""
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_GRAD_MINT, T0)
    monitor.mark_graduated(_GRAD_MINT, T0)

    # One second before post-grad TTL: still active
    before = T0 + timedelta(seconds=POST_GRAD_TTL - 1)
    result = monitor.check_idle(before)
    assert _GRAD_MINT not in result, (
        "Grad mint must NOT be deactivated 1 s before idle_kill_ttl_s."
    )
    assert not monitor.is_deactivated(_GRAD_MINT)

    # Exactly at post-grad TTL: deactivated
    at_post = T0 + timedelta(seconds=POST_GRAD_TTL)
    result = monitor.check_idle(at_post)
    assert _GRAD_MINT in result, (
        "Grad mint MUST be deactivated when elapsed >= idle_kill_ttl_s."
    )
    assert monitor.is_deactivated(_GRAD_MINT)
    assert len(monitor.deactivation_events) == 1
    assert monitor.deactivation_events[0] == (_GRAD_MINT, at_post)


# ---------------------------------------------------------------------------
# Test 4: ungrad mint reattaches on swap
# ---------------------------------------------------------------------------


def test_ungrad_mint_reattaches_on_swap() -> None:
    """Ungrad mint: after deactivation at pre_grad_ttl, record_swap re-attaches it.

    record_swap must return True (reattached) and is_deactivated must become False.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_UNGRAD_MINT, T0)

    # Deactivate at pre_grad_ttl
    monitor.check_idle(T0 + timedelta(seconds=PRE_GRAD_TTL))
    assert monitor.is_deactivated(_UNGRAD_MINT), "Precondition: mint must be deactivated."

    # New swap arrives → re-attach
    t_reattach = T0 + timedelta(seconds=PRE_GRAD_TTL + 5)
    reattached = monitor.record_swap(_UNGRAD_MINT, t_reattach)

    assert reattached is True, "record_swap must return True on re-attachment."
    assert not monitor.is_deactivated(_UNGRAD_MINT), (
        "Mint must be active (not deactivated) after re-attachment."
    )
    assert len(monitor.reattachment_events) == 1
    assert monitor.reattachment_events[0] == (_UNGRAD_MINT, t_reattach)


# ---------------------------------------------------------------------------
# Test 5: grad mint reattaches on swap
# ---------------------------------------------------------------------------


def test_grad_mint_reattaches_on_swap() -> None:
    """Grad mint: after deactivation at post_grad_ttl, record_swap re-attaches it.

    record_swap must return True (reattached) and is_deactivated must become False.
    """
    monitor = IdleKillMonitor(_make_resolver(PRE_GRAD_TTL, POST_GRAD_TTL))
    monitor.record_swap(_GRAD_MINT, T0)
    monitor.mark_graduated(_GRAD_MINT, T0)

    # Deactivate at post-grad TTL
    monitor.check_idle(T0 + timedelta(seconds=POST_GRAD_TTL))
    assert monitor.is_deactivated(_GRAD_MINT), "Precondition: grad mint must be deactivated."

    # New swap arrives → re-attach
    t_reattach = T0 + timedelta(seconds=POST_GRAD_TTL + 5)
    reattached = monitor.record_swap(_GRAD_MINT, t_reattach)

    assert reattached is True, "record_swap must return True on re-attachment."
    assert not monitor.is_deactivated(_GRAD_MINT), (
        "Grad mint must be active (not deactivated) after re-attachment."
    )
    assert len(monitor.reattachment_events) == 1
    assert monitor.reattachment_events[0] == (_GRAD_MINT, t_reattach)
