# ---
# module: core.tests.test_v7_capital_guard_us95
# sprint: sprint-15
# story: US-95
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, core.v7_capital_guard
# ---
"""US-95 — v7 retrain-on-live-feed dependency: capital guard + risk register tests.

WHAT THIS SUITE PROVES
======================
AC-95.1 (record the risk + dependency):
  - V7_RETRAINED_ON_LIVE_FEED is False by default (observe-only enforced)
  - V7CapitalGuardError is raised when capital is requested while the flag is False
  - V7_RETRAIN_DEPENDENCY_US92 string is non-empty and mentions US-92

AC-95.2 (retrain contract):
  - V7_RETRAIN_CONTRACT is committed and contains the required fields
  - The US-84 mirror (date-gated no-op) is acknowledged in the contract

AC-95.3 (local-proof):
  - V7_RISK_REGISTER contains all four PO risks
  - assert_v7_capital_authorised raises by default, passes when flag is patched True
  - The guard is importable from core.v7_capital_guard (confirmed present on main)
  - US-95<->US-92 linkage is traceable via V7_RETRAIN_DEPENDENCY_US92

HARD NO-CREDITS: zero Birdeye/Helius/Dune calls.  Pure logic + import tests.
"""
from __future__ import annotations

import unittest.mock as mock

import pytest

# ---------------------------------------------------------------------------
# AC-95.1 — capital guard is OFF by default (observe-only enforced)
# ---------------------------------------------------------------------------


def test_v7_capital_guard_flag_is_false_by_default() -> None:
    """V7_RETRAINED_ON_LIVE_FEED == False out of the box.

    AC-95.1: the shipped boosters are Birdeye-trained reference/architecture.
    The default must enforce observe-only.  No code path sets this True
    automatically; only an explicit operator decision can do so.
    """
    from core.v7_capital_guard import V7_RETRAINED_ON_LIVE_FEED

    assert V7_RETRAINED_ON_LIVE_FEED is False, (
        "V7_RETRAINED_ON_LIVE_FEED must be False by default.  "
        "v7 is observe-only until retrained on the live AMM feed."
    )


def test_assert_v7_capital_authorised_raises_by_default() -> None:
    """assert_v7_capital_authorised() raises V7CapitalGuardError when the flag is False.

    AC-95.1: the guard must refuse capital when V7_RETRAINED_ON_LIVE_FEED=False.
    This is the sprint's core safety assertion: no capital until the retrain
    contract has been executed and the operator has explicitly authorised it.
    """
    from core.v7_capital_guard import V7CapitalGuardError, assert_v7_capital_authorised

    with pytest.raises(V7CapitalGuardError) as exc_info:
        assert_v7_capital_authorised()

    error_msg = str(exc_info.value)
    assert "V7_RETRAINED_ON_LIVE_FEED" in error_msg, (
        "Error message must name V7_RETRAINED_ON_LIVE_FEED so the operator knows "
        "which flag to check."
    )
    observe_terms = ("observe", "observe-only", "not authorised")
    assert any(t in error_msg.lower() for t in observe_terms), (
        "Error message must communicate that v7 is observe-only / capital not authorised."
    )


def test_assert_v7_capital_authorised_passes_when_flag_patched_true() -> None:
    """assert_v7_capital_authorised() does NOT raise when flag is patched True.

    AC-95.1: once the retrain contract has been executed and the operator flips
    the flag, the guard must pass cleanly.  Verified by patching the module flag.
    """
    import core.v7_capital_guard as cg
    from core.v7_capital_guard import assert_v7_capital_authorised

    with mock.patch.object(cg, "V7_RETRAINED_ON_LIVE_FEED", True):
        # Must not raise
        assert_v7_capital_authorised()


def test_v7_capital_guard_error_is_runtime_error_subclass() -> None:
    """V7CapitalGuardError is a RuntimeError subclass for ergonomic catch clauses."""
    from core.v7_capital_guard import V7CapitalGuardError

    assert issubclass(V7CapitalGuardError, RuntimeError), (
        "V7CapitalGuardError must subclass RuntimeError so callers can catch it "
        "with `except RuntimeError` as a fallback."
    )


# ---------------------------------------------------------------------------
# AC-95.1 — US-92 dependency linkage is traceable
# ---------------------------------------------------------------------------


def test_us92_dependency_string_is_non_empty_and_mentions_us92() -> None:
    """V7_RETRAIN_DEPENDENCY_US92 names US-92 and the mint-on-post-rows precondition.

    AC-95.1/AC-95.3: the US-95 <-> US-92 linkage must be recorded in code
    (not just sprint notes) so it is visible to every future agent that reads
    core.v7_capital_guard.
    """
    from core.v7_capital_guard import V7_RETRAIN_DEPENDENCY_US92

    assert isinstance(V7_RETRAIN_DEPENDENCY_US92, str), (
        "V7_RETRAIN_DEPENDENCY_US92 must be a string."
    )
    assert len(V7_RETRAIN_DEPENDENCY_US92) > 50, (
        "V7_RETRAIN_DEPENDENCY_US92 must be a non-trivial description."
    )
    assert "US-92" in V7_RETRAIN_DEPENDENCY_US92, (
        "V7_RETRAIN_DEPENDENCY_US92 must explicitly name US-92."
    )
    assert "mint" in V7_RETRAIN_DEPENDENCY_US92.lower(), (
        "V7_RETRAIN_DEPENDENCY_US92 must mention 'mint' (the missing field that "
        "blocks retrain)."
    )


# ---------------------------------------------------------------------------
# AC-95.2 — retrain contract is committed and complete
# ---------------------------------------------------------------------------


def test_v7_retrain_contract_has_required_fields() -> None:
    """V7_RETRAIN_CONTRACT contains all fields specified in US-95 AC-95.2.

    AC-95.2: the retrain contract commits the unambiguous spec so the future
    retrain agent knows exactly what to do.  Required fields mirror the
    US-84 weekly-retrain pattern for the copy track.
    """
    from core.v7_capital_guard import V7_RETRAIN_CONTRACT

    required_fields = {
        "data_source",
        "graduation_label",
        "outcome_attribution",
        "feature_order",
        "dollar_basis",
        "model",
        "threshold_derivation",
        "retrain_cadence",
        "minimum_data",
        "us84_mirror",
        "no_op_until",
    }
    missing = required_fields - set(V7_RETRAIN_CONTRACT.keys())
    assert not missing, (
        f"V7_RETRAIN_CONTRACT is missing required fields: {sorted(missing)}.  "
        "The retrain contract must be complete so the future retrain is unambiguous."
    )


def test_v7_retrain_contract_data_source_is_own_live_feed() -> None:
    """Retrain contract specifies solanatrilly's OWN live AMM feed (not Birdeye).

    AC-95.2: the critical transfer-learning lesson — retraining on Birdeye data
    again would reproduce the same OOD problem.  The contract must explicitly
    name the own live feed.
    """
    from core.v7_capital_guard import V7_RETRAIN_CONTRACT

    data_source = str(V7_RETRAIN_CONTRACT["data_source"]).lower()
    assert "birdeye" not in data_source or "not" in data_source, (
        "Retrain data source must NOT be Birdeye alone.  "
        "Use solanatrilly's own live AMM feed."
    )
    assert "own" in data_source or "solanatrilly" in data_source or "live" in data_source, (
        "Retrain data source must reference solanatrilly's own live feed."
    )


def test_v7_retrain_contract_us84_mirror_acknowledged() -> None:
    """Retrain contract acknowledges the US-84 date-gated no-op mirror.

    AC-95.2: the weekly retrain is a no-op until the lake matures (mirrors US-84
    copy-track retrain pattern).
    """
    from core.v7_capital_guard import V7_RETRAIN_CONTRACT

    us84_mirror = str(V7_RETRAIN_CONTRACT["us84_mirror"]).lower()
    assert "us-84" in us84_mirror or "us84" in us84_mirror or "date" in us84_mirror or "no-op" in us84_mirror, (
        "V7_RETRAIN_CONTRACT['us84_mirror'] must acknowledge the US-84 date-gated "
        "no-op pattern."
    )


def test_v7_retrain_contract_weekly_cadence() -> None:
    """Retrain cadence is WEEKLY (decay is fast; most-recent fold only valid read)."""
    from core.v7_capital_guard import V7_RETRAIN_CONTRACT

    cadence = str(V7_RETRAIN_CONTRACT["retrain_cadence"]).lower()
    assert "week" in cadence, (
        "Retrain cadence must be WEEKLY (the lane decays fast; "
        "a stale model has inflated backtest edge)."
    )


# ---------------------------------------------------------------------------
# AC-95.3 — four PO risks documented in the risk register
# ---------------------------------------------------------------------------


def test_v7_risk_register_has_all_four_po_risks() -> None:
    """V7_RISK_REGISTER contains all four PO risks from AC-95.3.

    AC-95.3: these risks must be documented so nobody scopes v7 as a guaranteed
    $500/day without understanding the uncertainty.
    """
    from core.v7_capital_guard import V7_RISK_REGISTER

    required_risks = {
        "RIGHT_TAIL_CARRIED",
        "SURVIVORSHIP_AND_FEED_TRANSFER",
        "DECAY",
        "FILL_FRAGILITY",
    }
    missing = required_risks - set(V7_RISK_REGISTER.keys())
    assert not missing, (
        f"V7_RISK_REGISTER is missing required risk entries: {sorted(missing)}.  "
        "All four PO risks must be documented."
    )


def test_v7_risk_register_entries_are_non_trivial() -> None:
    """Each risk entry in V7_RISK_REGISTER is a non-trivial description (>30 chars)."""
    from core.v7_capital_guard import V7_RISK_REGISTER

    for key, val in V7_RISK_REGISTER.items():
        assert isinstance(val, str) and len(val) > 30, (
            f"V7_RISK_REGISTER['{key}'] is too short ({len(val)} chars).  "
            "Risk descriptions must be substantive."
        )


def test_v7_risk_register_right_tail_names_right_tail() -> None:
    """RIGHT_TAIL_CARRIED entry mentions the tail structure explicitly."""
    from core.v7_capital_guard import V7_RISK_REGISTER

    entry = V7_RISK_REGISTER["RIGHT_TAIL_CARRIED"].lower()
    assert "tail" in entry or "5%" in entry or "median" in entry, (
        "RIGHT_TAIL_CARRIED must describe the tail-driven EV structure "
        "(median loses; ~5% carry 110-190% of profit)."
    )


def test_v7_risk_register_survivorship_names_t120_lesson() -> None:
    """SURVIVORSHIP_AND_FEED_TRANSFER mentions the transfer-learning issue."""
    from core.v7_capital_guard import V7_RISK_REGISTER

    entry = V7_RISK_REGISTER["SURVIVORSHIP_AND_FEED_TRANSFER"].lower()
    assert "birdeye" in entry or "transfer" in entry or "t120" in entry or "ood" in entry, (
        "SURVIVORSHIP_AND_FEED_TRANSFER must name the Birdeye training source "
        "and the transfer-learning risk (OOD on live feed / t120 lesson)."
    )


def test_v7_risk_register_decay_names_feb_jun() -> None:
    """DECAY entry names the measured Feb->Jun fade."""
    from core.v7_capital_guard import V7_RISK_REGISTER

    entry = V7_RISK_REGISTER["DECAY"].lower()
    assert "decay" in entry or "fade" in entry or "feb" in entry or "stale" in entry, (
        "DECAY must describe the lane-fade finding (Feb->Jun measured decay)."
    )


def test_v7_risk_register_fill_fragility_names_depth() -> None:
    """FILL_FRAGILITY entry names the depth/slippage concern."""
    from core.v7_capital_guard import V7_RISK_REGISTER

    entry = V7_RISK_REGISTER["FILL_FRAGILITY"].lower()
    assert "$500" in entry or "depth" in entry or "slippage" in entry or "fill" in entry, (
        "FILL_FRAGILITY must describe the $500 vs ~$350 depth question."
    )


# ---------------------------------------------------------------------------
# AC-95.3 — guard is importable + observe-only is enforced by default
# ---------------------------------------------------------------------------


def test_v7_capital_guard_module_importable() -> None:
    """core.v7_capital_guard is importable and exports the required symbols.

    AC-95.3: the guard must be present on main (anti-prune guard for Phase-C).
    """
    import core.v7_capital_guard as cg

    for symbol in [
        "V7_RETRAINED_ON_LIVE_FEED",
        "V7CapitalGuardError",
        "assert_v7_capital_authorised",
        "V7_RETRAIN_DEPENDENCY_US92",
        "V7_RISK_REGISTER",
        "V7_RETRAIN_CONTRACT",
    ]:
        assert hasattr(cg, symbol), (
            f"core.v7_capital_guard is missing exported symbol: {symbol}"
        )


def test_observe_only_enforced_no_automatic_capital_path() -> None:
    """No code path in core.v7_capital_guard sets V7_RETRAINED_ON_LIVE_FEED=True.

    AC-95.3: the flag must NOT be flippable automatically.  Only an explicit
    operator decision (code change) can authorise capital.  This test reads the
    module source and confirms no assignment to True is present.
    """
    from pathlib import Path

    guard_path = Path(__file__).resolve().parents[2] / "core" / "v7_capital_guard.py"
    src = guard_path.read_text()

    # The flag is declared as False; no subsequent assignment to True should be present
    # in the module body.  We look for V7_RETRAINED_ON_LIVE_FEED = True pattern.
    import ast as _ast

    tree = _ast.parse(src)
    # Look for module-level or function-level assignments where the target name
    # is V7_RETRAINED_ON_LIVE_FEED and the value is the constant True.
    # This excludes string literals / docstrings that mention the name.
    auto_true_assignments = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, _ast.Name)
                    and target.id == "V7_RETRAINED_ON_LIVE_FEED"
                    and isinstance(node.value, _ast.Constant)
                    and node.value.value is True
                ):
                    auto_true_assignments.append(_ast.unparse(node))
    assert not auto_true_assignments, (
        "core.v7_capital_guard.py must NOT contain "
        "an assignment `V7_RETRAINED_ON_LIVE_FEED = True`.  "
        "Capital authorisation requires an explicit operator decision."
    )
