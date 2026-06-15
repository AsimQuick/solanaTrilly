# ---
# module: core.tests.test_pipeline_config_schema_ac101
# sprint: sprint-3
# story: US-10 AC-10.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.schemas, pydantic
# ---
"""AC-10.1 — Pydantic v2 PipelineConfig schema: structure, typing, and round-trip.

Verifies:
  - A fully-populated valid config passes Pydantic validation without error
  - Each section's typed fields are accessible by attribute name
  - The schema round-trips without loss:
      from_model_sections() -> to_model_sections() -> from_model_sections()
      produces an identical schema (model_dump equality)
  - to_model_sections() returns the five canonical section keys

No DB connection is required — this is pure Pydantic validation.
"""

import pytest

from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Canonical valid section payloads — all §5.2 invariants satisfied:
#   scoring.window_s (180) > score_at_elapsed_s (120)       ✓  leak guard
#   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)  ✓  D4
#   scoring.capture_buffer_s (4) >= 3                       ✓  tape tail
#   gate == "adaptive_topk"  (enforced by Literal)          ✓  id22 lesson
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


def _build_valid_schema() -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection=VALID_DETECTION,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )


# ---------------------------------------------------------------------------
# Validation — a valid config must parse without raising
# ---------------------------------------------------------------------------


def test_valid_config_validates_without_error():
    """A fully-populated valid config should pass Pydantic validation with no exception."""
    schema = _build_valid_schema()
    assert schema is not None


# ---------------------------------------------------------------------------
# Section typing — each section's fields must be accessible by attribute name
# ---------------------------------------------------------------------------


def test_detection_section_typed_correctly():
    schema = _build_valid_schema()
    assert schema.detection.source == "birdeye_meme"
    assert schema.detection.filter.source == "pump_dot_fun"
    assert schema.detection.filter.graduated is True
    assert schema.detection.prestage_progress_pct == pytest.approx(95.0)
    assert schema.detection.dedupe_window_s == 60
    assert schema.detection.reconciler == "helius_migrate"


def test_tape_section_typed_correctly():
    schema = _build_valid_schema()
    assert "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA" in schema.tape.amm_programs
    assert schema.tape.idle_kill_ttl_s == 1800
    assert schema.tape.reattach is True
    assert schema.tape.birdeye_interval_s == 15


def test_scoring_section_typed_correctly():
    schema = _build_valid_schema()
    assert schema.scoring.score_at_elapsed_s == 120
    assert schema.scoring.window_s == 180
    assert schema.scoring.capture_buffer_s == 4
    assert schema.scoring.gate == "adaptive_topk"


def test_outcome_section_typed_correctly():
    schema = _build_valid_schema()
    assert schema.outcome.window_s == 1800
    assert schema.outcome.label_def == {}


def test_trading_section_typed_correctly():
    schema = _build_valid_schema()
    assert schema.trading.gate == "adaptive_topk"
    assert schema.trading.enabled is False
    assert schema.trading.position_size_sol == pytest.approx(0.1)
    assert schema.trading.max_open_positions == 3
    assert schema.trading.slippage_bps == 50
    assert schema.trading.paper_size_usd is None


# ---------------------------------------------------------------------------
# Round-trip — export then reconstruct must equal the original
# ---------------------------------------------------------------------------


def test_round_trip_schema_equals_original():
    """build -> to_model_sections -> from_model_sections must equal the original schema."""
    schema1 = _build_valid_schema()
    sections = schema1.to_model_sections()

    # Reconstruct from the exported sections
    schema2 = PipelineConfigSchema.from_model_sections(**sections)

    assert schema1.model_dump() == schema2.model_dump()


def test_to_model_sections_returns_five_canonical_keys():
    """to_model_sections() must return exactly the five section keys."""
    schema = _build_valid_schema()
    sections = schema.to_model_sections()

    assert set(sections.keys()) == {"detection", "tape", "scoring", "outcome", "trading"}


def test_round_trip_section_values_are_stable():
    """A second round-trip must produce the same section dicts as the first."""
    schema = _build_valid_schema()
    sections1 = schema.to_model_sections()
    sections2 = PipelineConfigSchema.from_model_sections(**sections1).to_model_sections()

    assert sections1 == sections2
