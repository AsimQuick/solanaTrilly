# ---
# module: core.tests.test_pipeline_config_schema_ac102
# sprint: sprint-3
# story: US-10 AC-10.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.schemas, pydantic
# ---
"""AC-10.2 — §5.2 save-time invariants: invalid configs are REJECTED.

Each test constructs a config that violates exactly one §5.2 invariant and
asserts that Pydantic raises a ValidationError.  The four invariants:

  (a) Leak guard:        scoring.window_s > scoring.score_at_elapsed_s
  (b) D4 label guard:    tape.idle_kill_ttl_s >= outcome.window_s
  (c) Tape tail guard:   scoring.capture_buffer_s >= 3
  (d) id22 gate guard:   trading.gate == 'adaptive_topk' (no fixed threshold)

No DB connection is required — this is pure Pydantic validation.
"""

import pytest
from pydantic import ValidationError

from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Canonical valid section payloads — all §5.2 invariants satisfied.
# Tests mutate copies; the originals are never modified.
# ---------------------------------------------------------------------------

VALID_DETECTION = {
    "source": "birdeye_meme",
    "filter": {"source": "pump_dot_fun", "graduated": True},
    "prestage_progress_pct": 95.0,
    "dedupe_window_s": 60,
    "reconciler": "helius_migrate",
}

VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}

VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}

VALID_OUTCOME = {
    "window_s": 1800,
    "label_def": {},
}

VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
    "paper_size_usd": None,
    "exit_policy": None,
}


def _build(
    detection=None,
    tape=None,
    scoring=None,
    outcome=None,
    trading=None,
) -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection=detection or VALID_DETECTION,
        tape=tape or VALID_TAPE,
        scoring=scoring or VALID_SCORING,
        outcome=outcome or VALID_OUTCOME,
        trading=trading or VALID_TRADING,
    )


# ---------------------------------------------------------------------------
# (a) Leak guard — scoring.window_s must be > score_at_elapsed_s
# ---------------------------------------------------------------------------


def test_leak_guard_window_equal_to_score_at_is_rejected():
    """scoring.window_s == score_at_elapsed_s violates the leak guard (§5.2)."""
    scoring = {**VALID_SCORING, "score_at_elapsed_s": 120, "window_s": 120}
    with pytest.raises(ValidationError, match="leak guard"):
        _build(scoring=scoring)


def test_leak_guard_window_less_than_score_at_is_rejected():
    """scoring.window_s < score_at_elapsed_s violates the leak guard (§5.2)."""
    scoring = {**VALID_SCORING, "score_at_elapsed_s": 180, "window_s": 120}
    with pytest.raises(ValidationError, match="leak guard"):
        _build(scoring=scoring)


# ---------------------------------------------------------------------------
# (b) D4 label-truncation guard — tape.idle_kill_ttl_s must be >= outcome.window_s
# ---------------------------------------------------------------------------


def test_d4_ttl_less_than_outcome_window_is_rejected():
    """tape.idle_kill_ttl_s < outcome.window_s would truncate labels (D4, §5.2)."""
    tape = {**VALID_TAPE, "idle_kill_ttl_s": 1799}
    outcome = {**VALID_OUTCOME, "window_s": 1800}
    with pytest.raises(ValidationError, match="D4"):
        _build(tape=tape, outcome=outcome)


def test_d4_ttl_much_less_than_outcome_window_is_rejected():
    """tape.idle_kill_ttl_s well below outcome.window_s is also rejected (D4, §5.2)."""
    tape = {**VALID_TAPE, "idle_kill_ttl_s": 300}
    outcome = {**VALID_OUTCOME, "window_s": 1800}
    with pytest.raises(ValidationError, match="D4"):
        _build(tape=tape, outcome=outcome)


def test_d4_ttl_equal_to_outcome_window_is_accepted():
    """tape.idle_kill_ttl_s == outcome.window_s is the minimum valid value (§5.2)."""
    tape = {**VALID_TAPE, "idle_kill_ttl_s": 1800}
    outcome = {**VALID_OUTCOME, "window_s": 1800}
    schema = _build(tape=tape, outcome=outcome)
    assert schema.tape.idle_kill_ttl_s == schema.outcome.window_s


# ---------------------------------------------------------------------------
# (c) Tape-tail guard — scoring.capture_buffer_s must be >= 3
# ---------------------------------------------------------------------------


def test_capture_buffer_below_3_is_rejected():
    """scoring.capture_buffer_s < 3 violates the tape-tail guard (§5.2)."""
    scoring = {**VALID_SCORING, "capture_buffer_s": 2}
    with pytest.raises(ValidationError):
        _build(scoring=scoring)


def test_capture_buffer_zero_is_rejected():
    """scoring.capture_buffer_s == 0 violates the tape-tail guard (§5.2)."""
    scoring = {**VALID_SCORING, "capture_buffer_s": 0}
    with pytest.raises(ValidationError):
        _build(scoring=scoring)


def test_capture_buffer_exactly_3_is_accepted():
    """scoring.capture_buffer_s == 3 is the minimum valid value (§5.2)."""
    scoring = {**VALID_SCORING, "capture_buffer_s": 3}
    schema = _build(scoring=scoring)
    assert schema.scoring.capture_buffer_s == 3


# ---------------------------------------------------------------------------
# (d) id22 gate guard — trading.gate must be 'adaptive_topk'; fixed thresholds rejected
# ---------------------------------------------------------------------------


def test_trading_gate_fixed_threshold_string_is_rejected():
    """trading.gate set to a fixed-threshold string is rejected (id22 lesson, §9)."""
    trading = {**VALID_TRADING, "gate": "fixed_threshold"}
    with pytest.raises(ValidationError):
        _build(trading=trading)


def test_trading_gate_numeric_threshold_is_rejected():
    """trading.gate set to a numeric-string threshold is rejected (id22 lesson, §9)."""
    trading = {**VALID_TRADING, "gate": "0.65"}
    with pytest.raises(ValidationError):
        _build(trading=trading)


def test_trading_gate_arbitrary_string_is_rejected():
    """trading.gate with any non-'adaptive_topk' value is rejected (id22 lesson, §9)."""
    trading = {**VALID_TRADING, "gate": "top_percent"}
    with pytest.raises(ValidationError):
        _build(trading=trading)


def test_trading_gate_adaptive_topk_is_accepted():
    """trading.gate == 'adaptive_topk' is the only valid value (§5.2)."""
    trading = {**VALID_TRADING, "gate": "adaptive_topk"}
    schema = _build(trading=trading)
    assert schema.trading.gate == "adaptive_topk"
