# ---
# module: core.tests.test_pipeline_config_ac104
# sprint: sprint-3
# story: US-10 AC-10.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.schemas, pydantic, django.core.exceptions
# ---
"""AC-10.4 — Pydantic validation on the model WRITE path.

PipelineConfig.save() calls full_clean() which invokes clean(), routing populated
sections through the Pydantic schema so an invalid config CANNOT be persisted.

Test structure:
  (a) Positive path   — a valid complete config saves without error
  (b) Leak guard      — scoring.window_s <= score_at_elapsed_s → ValidationError, no row
  (c) D4 guard        — tape.idle_kill_ttl_s < outcome.window_s → ValidationError, no row
  (d) Tape-tail guard — scoring.capture_buffer_s < 3 → ValidationError, no row
  (e) clean() alone   — invariant checked before any DB write (no @django_db needed)

The "(ValidationError/ValidationError-wrapped)" in the AC is satisfied by
django.core.exceptions.ValidationError (raised by clean(), re-raised by full_clean()).
"""
import pytest
from django.core.exceptions import ValidationError

from core.models import PipelineConfig

# ---------------------------------------------------------------------------
# Complete valid section payloads — all §5.2 invariants satisfied:
#   scoring.window_s (180) > score_at_elapsed_s (120)       ✓  leak guard
#   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)  ✓  D4
#   scoring.capture_buffer_s (4) >= 3                       ✓  tape tail
#   gate == "adaptive_topk"                                 ✓  id22 lesson
# ---------------------------------------------------------------------------

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
}


def _make_unsaved(**overrides) -> PipelineConfig:
    """Build a PipelineConfig instance (NOT saved) from the valid base sections."""
    sections = {
        "tape": VALID_TAPE,
        "scoring": VALID_SCORING,
        "outcome": VALID_OUTCOME,
        "trading": VALID_TRADING,
    }
    sections.update(overrides)
    return PipelineConfig(version=1, label="ac104-test", **sections)


# ---------------------------------------------------------------------------
# (a) Positive path — a valid complete config must be saveable
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_complete_config_saves_without_error():
    """A PipelineConfig with all §5.2 invariants satisfied persists cleanly."""
    config = _make_unsaved()
    config.save()
    assert PipelineConfig.objects.filter(pk=config.pk).exists()


# ---------------------------------------------------------------------------
# (b) Leak guard: scoring.window_s must be strictly > score_at_elapsed_s
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_leak_guard_equal_raises_validation_error_on_save():
    """window_s == score_at_elapsed_s violates the leak guard → save raises ValidationError."""
    bad_scoring = {**VALID_SCORING, "window_s": 120, "score_at_elapsed_s": 120}
    config = _make_unsaved(scoring=bad_scoring)
    with pytest.raises(ValidationError):
        config.save()


@pytest.mark.django_db
def test_leak_guard_violation_writes_no_row():
    """A leak-guard-violating save does not persist any row."""
    bad_scoring = {**VALID_SCORING, "window_s": 100, "score_at_elapsed_s": 120}
    config = _make_unsaved(scoring=bad_scoring)
    try:
        config.save()
    except ValidationError:
        pass
    assert PipelineConfig.objects.count() == 0


# ---------------------------------------------------------------------------
# (c) D4 label-truncation guard: tape.idle_kill_ttl_s >= outcome.window_s
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_d4_guard_violation_raises_validation_error_on_save():
    """tape.idle_kill_ttl_s < outcome.window_s violates the D4 guard → save raises."""
    bad_tape = {**VALID_TAPE, "idle_kill_ttl_s": 1799}
    config = _make_unsaved(tape=bad_tape)
    with pytest.raises(ValidationError):
        config.save()


@pytest.mark.django_db
def test_d4_guard_violation_writes_no_row():
    """A D4-violating save does not persist any row."""
    bad_tape = {**VALID_TAPE, "idle_kill_ttl_s": 900}
    config = _make_unsaved(tape=bad_tape)
    try:
        config.save()
    except ValidationError:
        pass
    assert PipelineConfig.objects.count() == 0


# ---------------------------------------------------------------------------
# (d) Tape-tail guard: scoring.capture_buffer_s >= 3
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_capture_buffer_below_minimum_raises_validation_error_on_save():
    """scoring.capture_buffer_s < 3 is rejected at save time."""
    bad_scoring = {**VALID_SCORING, "capture_buffer_s": 2}
    config = _make_unsaved(scoring=bad_scoring)
    with pytest.raises(ValidationError):
        config.save()


@pytest.mark.django_db
def test_capture_buffer_violation_writes_no_row():
    """A capture_buffer_s violation save does not persist any row."""
    bad_scoring = {**VALID_SCORING, "capture_buffer_s": 0}
    config = _make_unsaved(scoring=bad_scoring)
    try:
        config.save()
    except ValidationError:
        pass
    assert PipelineConfig.objects.count() == 0


# ---------------------------------------------------------------------------
# (e) clean() path — invariant checked independently of full DB write
# ---------------------------------------------------------------------------


def test_clean_raises_validation_error_for_leak_guard_without_db():
    """PipelineConfig.clean() raises ValidationError for leak guard (no DB access)."""
    bad_scoring = {**VALID_SCORING, "window_s": 120, "score_at_elapsed_s": 120}
    config = _make_unsaved(scoring=bad_scoring)
    with pytest.raises(ValidationError):
        config.clean()
