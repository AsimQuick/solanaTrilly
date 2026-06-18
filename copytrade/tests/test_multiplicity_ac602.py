# ---
# module: copytrade.tests.test_multiplicity_ac602
# sprint: sprint-12
# story: US-60 AC-60.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, copytrade.multiplicity, copytrade.schemas, copytrade.wallet_consumer
# ---
"""AC-60.2 multiplicity controls unit tests.

All tests are pure-function, deterministic, and offline (zero firehose,
no Django DB access — no @pytest.mark.django_db).

Scenarios required by the AC:
  A. A second buy by the SAME wallet on the SAME token is ignored.
  B. Two DIFFERENT wallets buying the SAME token open ONE position attributed
     to the FIRST (the second is recorded via record_attribution).
  C. A trigger over max_concurrent_positions is ignored.

Additional thorough coverage:
  D. All three controls work when combined in sequence.
  E. copy_first_buy_only=False: the first-buy check is a no-op (same wallet
     can trigger multiple times).
  F. dedupe_token_across_wallets=False: a second wallet on the same token opens
     a second position instead of record_attribution.
  G. open_positions_count increments correctly on "open".
  H. open_positions_count does NOT increment on skip_first_buy / skip_cap /
     record_attribution.
  I. Different wallets buying DIFFERENT tokens open separate positions (no false
     deduplication).
  J. Same wallet, different tokens: copy_first_buy_only is per (wallet, mint),
     not per wallet — T1 passes, T2 also passes.
  K. Multiple additional trigger wallets are accumulated in
     state.additional_trigger_wallets.
  L. The first triggering wallet is NOT listed in additional_trigger_wallets
     (it is the primary, not additional).
  M. wallet_token_seen is populated on a successful "open" when
     copy_first_buy_only=True.
  N. open_mints is populated on "open".
"""
from datetime import datetime, timezone

import pytest

from copytrade.multiplicity import (
    MultiplicityState,
    TriggerDecision,
    apply_multiplicity_controls,
)
from copytrade.schemas import CopyTradeConfig
from copytrade.wallet_consumer import WalletTxEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

WALLET_A = "WalletAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
WALLET_B = "WalletBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
WALLET_C = "WalletCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"

MINT_T1 = "MintT1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
MINT_T2 = "MintT2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
MINT_T3 = "MintT3AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"


def _make_event(wallet: str = WALLET_A, mint: str = MINT_T1) -> WalletTxEvent:
    """Build a minimal WalletTxEvent for multiplicity testing."""
    return WalletTxEvent(
        wallet=wallet,
        mint=mint,
        tx_signature=f"sig_{wallet[:4]}_{mint[:4]}",
        tx_type="buy",
        sol_amount=0.25,
        token_amount=1_000_000.0,
        timestamp=_TS,
        raw={},
    )


def _default_config(**overrides) -> CopyTradeConfig:
    """Return a CopyTradeConfig with sensible defaults for multiplicity tests.

    Defaults: copy_first_buy_only=True, dedupe_token_across_wallets=True,
    max_concurrent_positions=20, mirror_wallet_sells=False (required invariant).
    """
    return CopyTradeConfig(mirror_wallet_sells=False, **overrides)


# ---------------------------------------------------------------------------
# A. AC-required: second buy by SAME wallet on SAME token is ignored
# ---------------------------------------------------------------------------


def test_second_buy_same_wallet_same_token_is_skipped():
    """AC-60.2 scenario A: second buy by the same wallet is ignored (skip_first_buy)."""
    config = _default_config(copy_first_buy_only=True)
    state = MultiplicityState.new()

    event = _make_event(wallet=WALLET_A, mint=MINT_T1)

    # First buy — must open
    decision1 = apply_multiplicity_controls(event, state, config)
    assert decision1.action == "open", f"expected 'open', got {decision1.action!r}"

    # Second buy by same wallet, same token — must skip
    decision2 = apply_multiplicity_controls(event, state, config)
    assert decision2.action == "skip_first_buy", (
        f"expected 'skip_first_buy', got {decision2.action!r}"
    )
    assert "already bought" in decision2.reason


def test_second_buy_same_wallet_same_token_does_not_increment_count():
    """open_positions_count must NOT increment on skip_first_buy."""
    config = _default_config(copy_first_buy_only=True)
    state = MultiplicityState.new()
    event = _make_event(wallet=WALLET_A, mint=MINT_T1)

    apply_multiplicity_controls(event, state, config)  # open → count = 1
    apply_multiplicity_controls(event, state, config)  # skip  → count stays 1

    assert state.open_positions_count == 1


# ---------------------------------------------------------------------------
# B. AC-required: two different wallets buying same token → ONE position,
#    second wallet recorded for attribution
# ---------------------------------------------------------------------------


def test_two_wallets_same_token_opens_one_position():
    """AC-60.2 scenario B: two different wallets buying same token → ONE open."""
    config = _default_config(dedupe_token_across_wallets=True)
    state = MultiplicityState.new()

    event_a = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_b = _make_event(wallet=WALLET_B, mint=MINT_T1)

    decision_a = apply_multiplicity_controls(event_a, state, config)
    decision_b = apply_multiplicity_controls(event_b, state, config)

    assert decision_a.action == "open"
    assert decision_b.action == "record_attribution"
    assert state.open_positions_count == 1  # only ONE position opened


def test_second_wallet_recorded_in_additional_trigger_wallets():
    """AC-60.2 scenario B: the second wallet is recorded in additional_trigger_wallets."""
    config = _default_config(dedupe_token_across_wallets=True)
    state = MultiplicityState.new()

    event_a = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_b = _make_event(wallet=WALLET_B, mint=MINT_T1)

    apply_multiplicity_controls(event_a, state, config)
    apply_multiplicity_controls(event_b, state, config)

    assert MINT_T1 in state.additional_trigger_wallets
    assert WALLET_B in state.additional_trigger_wallets[MINT_T1]


def test_first_wallet_NOT_in_additional_trigger_wallets():
    """The first (primary) wallet must NOT appear in additional_trigger_wallets."""
    config = _default_config(dedupe_token_across_wallets=True)
    state = MultiplicityState.new()

    event_a = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_b = _make_event(wallet=WALLET_B, mint=MINT_T1)

    apply_multiplicity_controls(event_a, state, config)
    apply_multiplicity_controls(event_b, state, config)

    additional = state.additional_trigger_wallets.get(MINT_T1, [])
    assert WALLET_A not in additional, (
        "WALLET_A is the primary trigger; it must not appear in additional_trigger_wallets"
    )


# ---------------------------------------------------------------------------
# C. AC-required: trigger over max_concurrent_positions is ignored
# ---------------------------------------------------------------------------


def test_trigger_over_cap_is_skipped():
    """AC-60.2 scenario C: a trigger beyond max_concurrent_positions is skipped."""
    config = _default_config(max_concurrent_positions=1)
    state = MultiplicityState.new()

    event_t1 = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_t2 = _make_event(wallet=WALLET_B, mint=MINT_T2)

    decision1 = apply_multiplicity_controls(event_t1, state, config)
    decision2 = apply_multiplicity_controls(event_t2, state, config)

    assert decision1.action == "open"
    assert decision2.action == "skip_cap"
    assert "max_concurrent_positions" in decision2.reason


def test_skip_cap_does_not_increment_count():
    """open_positions_count must NOT increment on skip_cap."""
    config = _default_config(max_concurrent_positions=1)
    state = MultiplicityState.new()

    event_t1 = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_t2 = _make_event(wallet=WALLET_B, mint=MINT_T2)

    apply_multiplicity_controls(event_t1, state, config)  # open → count = 1
    apply_multiplicity_controls(event_t2, state, config)  # skip_cap → count stays 1

    assert state.open_positions_count == 1


def test_cap_of_two_allows_two_then_blocks():
    """With cap=2, first two triggers open; third is skipped."""
    config = _default_config(max_concurrent_positions=2)
    state = MultiplicityState.new()

    d1 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    d2 = apply_multiplicity_controls(_make_event(WALLET_B, MINT_T2), state, config)
    d3 = apply_multiplicity_controls(_make_event(WALLET_C, MINT_T3), state, config)

    assert d1.action == "open"
    assert d2.action == "open"
    assert d3.action == "skip_cap"
    assert state.open_positions_count == 2


# ---------------------------------------------------------------------------
# D. All three controls work when combined in sequence
# ---------------------------------------------------------------------------


def test_all_three_controls_combined():
    """All three controls interact correctly in a single session."""
    config = _default_config(
        copy_first_buy_only=True,
        dedupe_token_across_wallets=True,
        max_concurrent_positions=1,
    )
    state = MultiplicityState.new()

    # 1. WALLET_A buys T1 → open (count=1, mints={T1}, seen={(A,T1)})
    d1 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    assert d1.action == "open"

    # 2. WALLET_A buys T1 again → skip_first_buy (control #4 fires first)
    d2 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    assert d2.action == "skip_first_buy"

    # 3. WALLET_B buys T1 → record_attribution (control #5; T1 already open)
    d3 = apply_multiplicity_controls(_make_event(WALLET_B, MINT_T1), state, config)
    assert d3.action == "record_attribution"

    # 4. WALLET_C buys T2 → skip_cap (cap=1, already at 1)
    d4 = apply_multiplicity_controls(_make_event(WALLET_C, MINT_T2), state, config)
    assert d4.action == "skip_cap"

    # Only one position was ever opened
    assert state.open_positions_count == 1


# ---------------------------------------------------------------------------
# E. copy_first_buy_only=False: same wallet can trigger multiple times
# ---------------------------------------------------------------------------


def test_copy_first_buy_only_false_allows_repeat():
    """When copy_first_buy_only=False, same wallet buying same token triggers again."""
    config = _default_config(copy_first_buy_only=False, max_concurrent_positions=20)
    state = MultiplicityState.new()

    event = _make_event(wallet=WALLET_A, mint=MINT_T1)

    # First call opens the position; open_mints now contains MINT_T1
    d1 = apply_multiplicity_controls(event, state, config)
    assert d1.action == "open"

    # Manually close the position so dedupe doesn't fire (simulate position closed)
    state.open_mints.discard(MINT_T1)

    # Second call should open again (copy_first_buy_only is False)
    d2 = apply_multiplicity_controls(event, state, config)
    assert d2.action == "open", (
        f"copy_first_buy_only=False should allow re-trigger; got {d2.action!r}"
    )


def test_copy_first_buy_only_false_wallet_token_seen_not_checked():
    """When copy_first_buy_only=False, wallet_token_seen is never populated."""
    config = _default_config(copy_first_buy_only=False)
    state = MultiplicityState.new()

    event = _make_event(wallet=WALLET_A, mint=MINT_T1)
    apply_multiplicity_controls(event, state, config)

    assert len(state.wallet_token_seen) == 0


# ---------------------------------------------------------------------------
# F. dedupe_token_across_wallets=False: second wallet on same token opens new
# ---------------------------------------------------------------------------


def test_dedupe_false_second_wallet_opens_new_position():
    """When dedupe_token_across_wallets=False, a second wallet opens a second position."""
    config = _default_config(
        dedupe_token_across_wallets=False,
        max_concurrent_positions=20,
    )
    state = MultiplicityState.new()

    event_a = _make_event(wallet=WALLET_A, mint=MINT_T1)
    event_b = _make_event(wallet=WALLET_B, mint=MINT_T1)

    d1 = apply_multiplicity_controls(event_a, state, config)
    d2 = apply_multiplicity_controls(event_b, state, config)

    assert d1.action == "open"
    assert d2.action == "open"
    assert state.open_positions_count == 2


# ---------------------------------------------------------------------------
# G. open_positions_count increments correctly on "open"
# ---------------------------------------------------------------------------


def test_open_positions_count_increments_on_open():
    """open_positions_count increments by 1 for each successful 'open'."""
    config = _default_config(max_concurrent_positions=20)
    state = MultiplicityState.new()

    assert state.open_positions_count == 0

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    assert state.open_positions_count == 1

    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T2), state, config)
    assert state.open_positions_count == 2

    apply_multiplicity_controls(_make_event(WALLET_C, MINT_T3), state, config)
    assert state.open_positions_count == 3


# ---------------------------------------------------------------------------
# H. open_positions_count NOT incremented on skipped/recorded events
# ---------------------------------------------------------------------------


def test_record_attribution_does_not_increment_count():
    """open_positions_count must NOT increment when action is record_attribution."""
    config = _default_config(dedupe_token_across_wallets=True)
    state = MultiplicityState.new()

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    assert state.open_positions_count == 1

    # Different wallet, same token → record_attribution
    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T1), state, config)
    assert state.open_positions_count == 1  # must stay at 1


# ---------------------------------------------------------------------------
# I. Different wallets buying DIFFERENT tokens → separate positions (no false
#    deduplication)
# ---------------------------------------------------------------------------


def test_different_tokens_open_separate_positions():
    """Different tokens must not be conflated — each gets its own position."""
    config = _default_config(max_concurrent_positions=20)
    state = MultiplicityState.new()

    d1 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    d2 = apply_multiplicity_controls(_make_event(WALLET_B, MINT_T2), state, config)
    d3 = apply_multiplicity_controls(_make_event(WALLET_C, MINT_T3), state, config)

    assert d1.action == "open"
    assert d2.action == "open"
    assert d3.action == "open"
    assert state.open_positions_count == 3
    assert state.open_mints == {MINT_T1, MINT_T2, MINT_T3}


# ---------------------------------------------------------------------------
# J. Same wallet, different tokens: copy_first_buy_only is per (wallet, mint)
# ---------------------------------------------------------------------------


def test_same_wallet_different_tokens_both_trigger():
    """copy_first_buy_only is per (wallet, mint) — same wallet, T2 still triggers."""
    config = _default_config(copy_first_buy_only=True, max_concurrent_positions=20)
    state = MultiplicityState.new()

    d1 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    d2 = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T2), state, config)

    assert d1.action == "open", "first buy on T1 must open"
    assert d2.action == "open", (
        "first buy on T2 by same wallet must also open (different (wallet, mint) key)"
    )
    assert state.open_positions_count == 2


def test_same_wallet_same_token_then_different_token_blocked_then_opened():
    """Second buy of T1 is blocked; buy of T2 by same wallet still opens."""
    config = _default_config(copy_first_buy_only=True, max_concurrent_positions=20)
    state = MultiplicityState.new()

    d_t1_first = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    d_t1_second = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    d_t2_first = apply_multiplicity_controls(_make_event(WALLET_A, MINT_T2), state, config)

    assert d_t1_first.action == "open"
    assert d_t1_second.action == "skip_first_buy"
    assert d_t2_first.action == "open"


# ---------------------------------------------------------------------------
# K. Multiple additional trigger wallets are accumulated
# ---------------------------------------------------------------------------


def test_multiple_additional_trigger_wallets_accumulated():
    """All additional triggering wallets must be recorded in order."""
    config = _default_config(
        dedupe_token_across_wallets=True,
        copy_first_buy_only=False,  # disable to let wallets B and C through
        max_concurrent_positions=20,
    )
    state = MultiplicityState.new()

    # WALLET_A opens the position
    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)

    # WALLET_B and WALLET_C arrive while T1 is already open
    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T1), state, config)
    apply_multiplicity_controls(_make_event(WALLET_C, MINT_T1), state, config)

    additional = state.additional_trigger_wallets.get(MINT_T1, [])
    assert WALLET_B in additional
    assert WALLET_C in additional
    assert len(additional) == 2


# ---------------------------------------------------------------------------
# L. Primary wallet not in additional_trigger_wallets (already verified in B)
#    — explicit cross-check for clarity
# ---------------------------------------------------------------------------


def test_primary_wallet_absent_from_additional():
    """The first wallet (primary) must never appear in additional_trigger_wallets."""
    config = _default_config(
        dedupe_token_across_wallets=True,
        copy_first_buy_only=False,
        max_concurrent_positions=20,
    )
    state = MultiplicityState.new()

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T1), state, config)
    apply_multiplicity_controls(_make_event(WALLET_C, MINT_T1), state, config)

    additional = state.additional_trigger_wallets.get(MINT_T1, [])
    assert WALLET_A not in additional


# ---------------------------------------------------------------------------
# M. wallet_token_seen populated on "open" when copy_first_buy_only=True
# ---------------------------------------------------------------------------


def test_wallet_token_seen_populated_on_open():
    """(wallet, mint) pair must be added to wallet_token_seen on first open."""
    config = _default_config(copy_first_buy_only=True)
    state = MultiplicityState.new()

    assert len(state.wallet_token_seen) == 0

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)

    assert (WALLET_A, MINT_T1) in state.wallet_token_seen


# ---------------------------------------------------------------------------
# N. open_mints populated on "open"
# ---------------------------------------------------------------------------


def test_open_mints_populated_on_open():
    """Mint must be added to open_mints when a position is opened."""
    config = _default_config()
    state = MultiplicityState.new()

    assert len(state.open_mints) == 0

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)

    assert MINT_T1 in state.open_mints


def test_open_mints_not_populated_on_skip():
    """Mint must NOT be added to open_mints when trigger is skipped."""
    config = _default_config(max_concurrent_positions=1)
    state = MultiplicityState.new()

    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)  # opens T1
    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T2), state, config)  # skip_cap

    assert MINT_T2 not in state.open_mints


# ---------------------------------------------------------------------------
# O. MultiplicityState.new() returns correct initial values
# ---------------------------------------------------------------------------


def test_multiplicity_state_new_initial_values():
    """MultiplicityState.new() must return an empty-but-ready state."""
    state = MultiplicityState.new()
    assert state.wallet_token_seen == set()
    assert state.open_mints == set()
    assert state.open_positions_count == 0
    assert state.additional_trigger_wallets == {}


# ---------------------------------------------------------------------------
# P. TriggerDecision is frozen (immutable)
# ---------------------------------------------------------------------------


def test_trigger_decision_is_frozen():
    """TriggerDecision is frozen=True — mutations must raise."""
    decision = TriggerDecision(action="open", reason="test")
    with pytest.raises((AttributeError, TypeError)):
        decision.action = "skip_cap"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Q. reason string mentions relevant details
# ---------------------------------------------------------------------------


def test_skip_cap_reason_mentions_limit():
    """skip_cap reason must mention the configured max_concurrent_positions value."""
    config = _default_config(max_concurrent_positions=3)
    state = MultiplicityState.new()

    # Fill up to the cap
    apply_multiplicity_controls(_make_event(WALLET_A, MINT_T1), state, config)
    apply_multiplicity_controls(_make_event(WALLET_B, MINT_T2), state, config)
    apply_multiplicity_controls(_make_event(WALLET_C, MINT_T3), state, config)

    # This should be skipped
    extra_wallet = "WalletDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
    extra_mint = "MintT4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
    d = apply_multiplicity_controls(_make_event(extra_wallet, extra_mint), state, config)

    assert d.action == "skip_cap"
    assert "3" in d.reason
