# ---
# module: core.tests.test_firehose_ledger_ac72
# sprint: sprint-2
# story: US-7 AC-7.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re
# ---
"""AC-7.2 — ops/firehose_activation_log.md documents the per-activation protocol
(deliberate, time-boxed <=30 min default; adjustable by explicit PO decision,
PR-reviewed) with a table schema of columns: date, role/agent, which WS
(Birdeye/Helius), purpose, duration, count-remaining, fixtures banked.

Tests:
  test_protocol_section_exists
      Ledger contains a 'Per-Activation Protocol' section.

  test_protocol_states_deliberate
      Protocol uses the word 'deliberate' to describe activations.

  test_protocol_states_time_boxed_30_min
      Protocol states a 30-minute default time-box.

  test_protocol_states_adjustable_by_po
      Protocol states the time-box is adjustable by explicit PO decision.

  test_protocol_states_pr_reviewed
      Protocol states activations are PR-reviewed.

  test_schema_has_date_column
      Activation table schema includes a 'date' column.

  test_schema_has_role_agent_column
      Activation table schema includes a 'role/agent' column.

  test_schema_has_ws_column
      Activation table schema includes a WS (Birdeye/Helius) column.

  test_schema_has_purpose_column
      Activation table schema includes a 'purpose' column.

  test_schema_has_duration_column
      Activation table schema includes a 'duration' column.

  test_schema_has_count_remaining_column
      Activation table schema includes a count-remaining column.

  test_schema_has_fixtures_banked_column
      Activation table schema includes a 'fixtures banked' column.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "ops" / "firehose_activation_log.md"


def _ledger_text() -> str:
    return LEDGER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Protocol section
# ---------------------------------------------------------------------------


def test_protocol_section_exists() -> None:
    """Ledger must contain a 'Per-Activation Protocol' section."""
    text = _ledger_text()
    assert re.search(r"per.activation protocol", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md must contain a 'Per-Activation Protocol' section "
        "(AC-7.2 requires the protocol to be documented in the ledger)."
    )


def test_protocol_states_deliberate() -> None:
    """Protocol must describe activations as 'deliberate' (AC-7.2)."""
    text = _ledger_text()
    assert "deliberate" in text.lower(), (
        "ops/firehose_activation_log.md must use the word 'deliberate' in the "
        "per-activation protocol section (AC-7.2)."
    )


def test_protocol_states_time_boxed_30_min() -> None:
    """Protocol must state a 30-minute default time-box (AC-7.2)."""
    text = _ledger_text()
    # Accept: "30 min", "30min", "30-min", "30 minutes", "30-minute"
    assert re.search(r"30[\s-]?min", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md must state a 30-minute default time-box in the "
        "per-activation protocol section (AC-7.2: time-boxed <=30 min default)."
    )


def test_protocol_states_adjustable_by_po() -> None:
    """Protocol must state that the time-box is adjustable by explicit PO decision (AC-7.2)."""
    text = _ledger_text()
    # Accept: "PO decision", "PO-approved", "explicit PO"
    assert re.search(r"(?:explicit\s+PO|PO\s+decision|PO.approved)", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md must state the time-box is adjustable by explicit PO "
        "decision (AC-7.2: '<=30 min default; adjustable by explicit PO decision')."
    )


def test_protocol_states_pr_reviewed() -> None:
    """Protocol must state activations are PR-reviewed (AC-7.2)."""
    text = _ledger_text()
    # Accept: "PR-reviewed", "PR reviewed", "pull request review", "pull-request-reviewed"
    assert re.search(r"PR[- ]reviewed|pull.request.review", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md must state activations are PR-reviewed (AC-7.2)."
    )


# ---------------------------------------------------------------------------
# Table schema columns
# ---------------------------------------------------------------------------


def test_schema_has_date_column() -> None:
    """Activation table schema must include a 'date' column (AC-7.2)."""
    text = _ledger_text()
    assert re.search(r"\bdate\b", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a 'date' column "
        "(AC-7.2 required columns: date, role/agent, ws, purpose, duration, "
        "count-remaining, fixtures banked)."
    )


def test_schema_has_role_agent_column() -> None:
    """Activation table schema must include a 'role/agent' column (AC-7.2)."""
    text = _ledger_text()
    assert re.search(r"role[/\s-]agent", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a 'role/agent' column "
        "(AC-7.2 required columns)."
    )


def test_schema_has_ws_column() -> None:
    """Activation table schema must include a WS (Birdeye/Helius) column (AC-7.2)."""
    text = _ledger_text()
    # The column can be named 'ws', 'which WS', 'WS', etc.
    assert re.search(r"\bws\b", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a WS column "
        "(AC-7.2: 'which WS (Birdeye/Helius)')."
    )


def test_schema_has_purpose_column() -> None:
    """Activation table schema must include a 'purpose' column (AC-7.2)."""
    text = _ledger_text()
    assert re.search(r"\bpurpose\b", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a 'purpose' column "
        "(AC-7.2 required columns)."
    )


def test_schema_has_duration_column() -> None:
    """Activation table schema must include a 'duration' column (AC-7.2)."""
    text = _ledger_text()
    assert re.search(r"\bduration\b", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a 'duration' column "
        "(AC-7.2 required columns)."
    )


def test_schema_has_count_remaining_column() -> None:
    """Activation table schema must include a count-remaining column (AC-7.2)."""
    text = _ledger_text()
    # Accept: "count_remaining", "count-remaining", "count remaining", "remaining"
    assert re.search(r"count[_\s-]remaining|count_remaining", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a "
        "'count-remaining' column (AC-7.2 required columns)."
    )


def test_schema_has_fixtures_banked_column() -> None:
    """Activation table schema must include a 'fixtures banked' column (AC-7.2)."""
    text = _ledger_text()
    # Accept: "fixtures_banked", "fixtures banked", "fixtures-banked"
    assert re.search(r"fixtures[_\s-]banked", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md activation table schema is missing a "
        "'fixtures banked' column (AC-7.2 required columns)."
    )
