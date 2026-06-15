# ---
# module: core.tests.test_firehose_ledger_ac73
# sprint: sprint-2
# story: US-7 AC-7.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re
# ---
"""AC-7.3 — ops/firehose_activation_log.md states the HARD RULE that every
activation MUST bank durable fixtures (a tape/detection sample or golden vectors)
into the lake / golden set so the spend compounds into the replay corpus.

Tests:
  test_hard_rule_section_exists
      Ledger contains a dedicated 'HARD RULE' section heading.

  test_hard_rule_uses_must_language
      The HARD RULE uses 'MUST' to express a hard requirement, not a suggestion.

  test_hard_rule_mentions_durable_fixtures
      The HARD RULE explicitly mentions 'durable fixtures'.

  test_hard_rule_specifies_fixture_types
      The HARD RULE names what counts as a fixture: tape/detection samples or
      golden vectors.

  test_hard_rule_mentions_lake_or_golden_set
      The HARD RULE names the destination: the lake / golden set.

  test_hard_rule_mentions_replay_corpus
      The HARD RULE explains the compounding outcome: the replay corpus.

  test_hard_rule_enforces_merge_block
      The HARD RULE states that an activation without a committed fixture is
      blocked from merge (Tester enforcement).

  test_hard_rule_not_optional_language
      The HARD RULE uses 'MUST', not weaker hedges like 'should' or 'may',
      confirming it is non-negotiable.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "ops" / "firehose_activation_log.md"


def _ledger_text() -> str:
    return LEDGER_PATH.read_text(encoding="utf-8")


def _hard_rule_section(text: str) -> str:
    """Extract the text of the HARD RULE section (from its heading to the next ##)."""
    match = re.search(r"##\s+HARD RULE.*", text, re.IGNORECASE)
    if not match:
        return ""
    start = match.start()
    # Find the next ## heading after the HARD RULE heading
    next_section = re.search(r"\n##\s+", text[start + 1:])
    end = start + 1 + next_section.start() if next_section else len(text)
    return text[start:end]


# ---------------------------------------------------------------------------
# HARD RULE section structure
# ---------------------------------------------------------------------------


def test_hard_rule_section_exists() -> None:
    """Ledger must contain a dedicated 'HARD RULE' section heading (AC-7.3)."""
    text = _ledger_text()
    assert re.search(r"##\s+HARD RULE", text, re.IGNORECASE), (
        "ops/firehose_activation_log.md must contain a '## HARD RULE' section heading. "
        "AC-7.3 requires the ledger to state the hard rule as a named section, "
        "not just a passing remark."
    )


def test_hard_rule_uses_must_language() -> None:
    """The HARD RULE must use 'MUST' (uppercase) to signal a non-negotiable requirement (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found — run test_hard_rule_section_exists first."
    assert "MUST" in section, (
        "The HARD RULE section must use 'MUST' (uppercase) to express a hard requirement. "
        "AC-7.3: 'every activation MUST bank durable fixtures' — weaker language like "
        "'should' or 'may' is insufficient."
    )


def test_hard_rule_mentions_durable_fixtures() -> None:
    """The HARD RULE must explicitly mention 'durable fixtures' (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    assert re.search(r"durable\s+fixtures", section, re.IGNORECASE), (
        "The HARD RULE section must use the phrase 'durable fixtures'. "
        "AC-7.3 requires this exact language so the rule is unambiguous: "
        "every activation must bank *durable* (committed, long-lived) fixtures."
    )


def test_hard_rule_specifies_fixture_types() -> None:
    """The HARD RULE must name what counts as a durable fixture: tape/detection sample or
    golden vectors (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    # Must mention at least one of: tape, detection sample, golden vectors
    assert re.search(r"tape|detection\s+sample|golden\s+vector", section, re.IGNORECASE), (
        "The HARD RULE section must specify what counts as a durable fixture. "
        "AC-7.3: 'a tape/detection sample or golden vectors'. Without this, agents "
        "cannot know what satisfies the banking requirement."
    )


def test_hard_rule_mentions_lake_or_golden_set() -> None:
    """The HARD RULE must name the destination: the lake / golden set (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    assert re.search(r"lake|golden\s+set", section, re.IGNORECASE), (
        "The HARD RULE section must state where fixtures are banked: "
        "'into the lake / golden set'. AC-7.3 requires this to make the destination concrete."
    )


def test_hard_rule_mentions_replay_corpus() -> None:
    """The HARD RULE must explain the compounding outcome: the replay corpus (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    assert re.search(r"replay\s+corpus", section, re.IGNORECASE), (
        "The HARD RULE section must mention 'replay corpus' as the compounding outcome. "
        "AC-7.3: 'the spend compounds into the replay corpus' — this is the WHY behind "
        "the rule; omitting it leaves agents without motivation to comply."
    )


def test_hard_rule_enforces_merge_block() -> None:
    """The HARD RULE must state that an activation without fixtures is blocked from merge (AC-7.3)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    # Accept: "blocked from merge", "blocked until", "flagged by the Tester and blocked"
    assert re.search(r"blocked\s+(?:from\s+merge|until|by)|flagged", section, re.IGNORECASE), (
        "The HARD RULE section must state that activations without committed fixtures are "
        "blocked from merge (enforcement by the Tester). AC-7.3: the rule must be "
        "enforceable — a 'nice to have' with no consequence is not a hard rule."
    )


def test_hard_rule_not_optional_language() -> None:
    """The HARD RULE must not use weaker modal verbs ('should', 'may', 'might') as the
    primary verb for the banking requirement (AC-7.3 — 'MUST', not advisory)."""
    text = _ledger_text()
    section = _hard_rule_section(text)
    assert section, "No HARD RULE section found."
    # The phrase 'MUST bank' must appear; 'should bank' or 'may bank' alone would be wrong
    assert re.search(r"MUST\s+bank", section), (
        "The HARD RULE must use 'MUST bank' (not 'should bank' or 'may bank'). "
        "AC-7.3 specifies a hard rule — the modal verb must be 'MUST' so there is no "
        "ambiguity about whether banking fixtures is optional."
    )
