# ---
# module: core.tests.test_pipeline_config_schema_ac351
# sprint: sprint-8
# story: US-35 AC-35.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.schemas, pydantic
# ---
"""AC-35.1 — Two config knobs in TapeConfig: pre_grad_idle_kill_ttl_s (new, pre-grad-only)
and idle_kill_ttl_s (existing, protected post-grad TTL).

Three invariants under test:
  (A) pre_grad_idle_kill_ttl_s=300 is ACCEPTED even when 300 < outcome.window_s (1800 s);
      the new knob is exempt from the D4 floor.
  (B) idle_kill_ttl_s < outcome.window_s is REJECTED at save time (D4 guard still bites).
  (C) pre_grad_idle_kill_ttl_s < outcome.window_s is ACCEPTED (pre-grad-only, no label
      obligation, no ≥ window_s floor applied).

No DB connection required — pure Pydantic validation.
"""

import pytest
from pydantic import ValidationError

from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Canonical valid section payloads.  outcome.window_s=1800 (v3.2's label horizon).
# ---------------------------------------------------------------------------

_DETECTION = {
    "source": "birdeye_meme",
    "filter": {"source": "pump_dot_fun", "graduated": True},
    "prestage_progress_pct": 95.0,
    "dedupe_window_s": 60,
    "reconciler": "helius_migrate",
}

_TAPE_VALID = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "pre_grad_idle_kill_ttl_s": 300,
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}

_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}

_OUTCOME = {"window_s": 1800, "label_def": {}}

_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
    "paper_size_usd": None,
    "exit_policy": None,
}


def _build(tape=None) -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection=_DETECTION,
        tape=tape if tape is not None else _TAPE_VALID,
        scoring=_SCORING,
        outcome=_OUTCOME,
        trading=_TRADING,
    )


# ---------------------------------------------------------------------------
# (A) pre_grad_idle_kill_ttl_s=300 is accepted (below outcome.window_s=1800)
# ---------------------------------------------------------------------------


def test_pre_grad_ttl_300_is_accepted():
    """pre_grad_idle_kill_ttl_s=300 must be accepted even though 300 < outcome.window_s=1800.

    The new knob is exempt from the D4 ≥ window_s floor (it governs UNgraduated
    tokens, which have no label obligation).
    """
    schema = _build(tape={**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": 300})
    assert schema.tape.pre_grad_idle_kill_ttl_s == 300


def test_pre_grad_ttl_default_is_300():
    """TapeConfig.pre_grad_idle_kill_ttl_s must default to 300 (oracle §2)."""
    schema = _build()
    assert schema.tape.pre_grad_idle_kill_ttl_s == 300


def test_pre_grad_ttl_small_value_is_accepted():
    """pre_grad_idle_kill_ttl_s=60 (1 min) is accepted — no floor from outcome.window_s."""
    schema = _build(tape={**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": 60})
    assert schema.tape.pre_grad_idle_kill_ttl_s == 60


# ---------------------------------------------------------------------------
# (B) idle_kill_ttl_s < outcome.window_s is REJECTED (D4 guard still bites)
# ---------------------------------------------------------------------------


def test_post_grad_ttl_less_than_outcome_window_is_rejected():
    """idle_kill_ttl_s < outcome.window_s must be rejected (D4, PRD §5.2).

    The D4 guard is unchanged: post-grad TTL must cover the full label horizon
    so a graduated token's label is never truncated.
    """
    tape = {**_TAPE_VALID, "idle_kill_ttl_s": 1799}
    with pytest.raises(ValidationError, match="D4"):
        _build(tape=tape)


def test_post_grad_ttl_well_below_outcome_window_is_rejected():
    """idle_kill_ttl_s well below outcome.window_s is also rejected (D4, §5.2)."""
    tape = {**_TAPE_VALID, "idle_kill_ttl_s": 300}
    with pytest.raises(ValidationError, match="D4"):
        _build(tape=tape)


def test_post_grad_ttl_equal_to_outcome_window_is_accepted():
    """idle_kill_ttl_s == outcome.window_s is the minimum valid post-grad TTL (§5.2)."""
    tape = {**_TAPE_VALID, "idle_kill_ttl_s": 1800}
    schema = _build(tape=tape)
    assert schema.tape.idle_kill_ttl_s == schema.outcome.window_s


# ---------------------------------------------------------------------------
# (C) pre_grad_idle_kill_ttl_s < outcome.window_s is ACCEPTED (pre-grad-only, no floor)
# ---------------------------------------------------------------------------


def test_pre_grad_ttl_below_window_s_accepted():
    """pre_grad_idle_kill_ttl_s=300 (< outcome.window_s=1800) must be accepted.

    The new knob applies ONLY to UNgraduated tokens; the D4 floor is not
    applied to it.  The recorder snaps to the protected idle_kill_ttl_s at
    the graduation instant (AC-35.2).
    """
    tape = {**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": 300, "idle_kill_ttl_s": 1800}
    schema = _build(tape=tape)
    assert schema.tape.pre_grad_idle_kill_ttl_s == 300
    assert schema.tape.idle_kill_ttl_s == 1800
    assert schema.tape.pre_grad_idle_kill_ttl_s < schema.outcome.window_s


def test_pre_grad_ttl_far_below_window_s_accepted():
    """pre_grad_idle_kill_ttl_s=1 is accepted — only lower bound is gt=0."""
    tape = {**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": 1, "idle_kill_ttl_s": 1800}
    schema = _build(tape=tape)
    assert schema.tape.pre_grad_idle_kill_ttl_s == 1


# ---------------------------------------------------------------------------
# (D) Field-level guards
# ---------------------------------------------------------------------------


def test_pre_grad_ttl_zero_is_rejected():
    """pre_grad_idle_kill_ttl_s=0 is rejected by the gt=0 field constraint."""
    tape = {**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": 0}
    with pytest.raises(ValidationError):
        _build(tape=tape)


def test_pre_grad_ttl_negative_is_rejected():
    """pre_grad_idle_kill_ttl_s=-1 is rejected by the gt=0 field constraint."""
    tape = {**_TAPE_VALID, "pre_grad_idle_kill_ttl_s": -1}
    with pytest.raises(ValidationError):
        _build(tape=tape)


def test_two_tier_knobs_both_present_in_schema():
    """Both knobs are present on the schema and serialised via to_model_sections."""
    schema = _build()
    sections = schema.to_model_sections()
    tape = sections["tape"]
    assert "pre_grad_idle_kill_ttl_s" in tape, (
        "pre_grad_idle_kill_ttl_s must be present in the serialised tape section"
    )
    assert "idle_kill_ttl_s" in tape, (
        "idle_kill_ttl_s must be present in the serialised tape section"
    )
