# ---
# file: core/tests/test_retrospective_sprint9_ac453.py
# project: solanatrilly
# purpose: AC-45.3 — verify retrospective.md is updated for sprint-9 with required content
#          (H2 evidence verdict, P7 outcome, what went well/didn't/action items)
# story: US-45 AC-45.3
# sprint: sprint-9
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: scrum-master/retrospective.md, core/tests/test_ruff_hook_evidence_ac451.py,
#               core/tests/test_sprint_boundary_ac452.py
# ---
"""AC-45.3 — retrospective.md updated for sprint-9.

AC text:
  retrospective.md updated for sprint-9 (named owner: Tester / scrum facilitator —
  retrospective A3): what went well / what didn't / action items, including the H2
  evidence verdict (did the unforgeable hook stop the recurrence?) and the P7 outcome
  (is the serving path complete and the operator unblocked to soak + promote?). After
  updating any /scrum-master/ doc, re-index via mcp__devrag__reindex_document (project
  convention). New files carry metadata front matter.

Tests in this module:
  (1)  ImportError trap — pin named functions from AC-45.1 and AC-45.2 test files
       (H1: deletion fails pytest collection before any test runs)
  (2)  retrospective.md exists and is readable
  (3)  Sprint-9 section is present
  (4)  H2 evidence verdict "CONFIRMED" is mentioned
  (5)  P7 serving path complete stated
  (6)  Operator unblocked to soak + promote stated
  (7)  "What went well" subsection present in sprint-9 section
  (8)  "What didn't go well" subsection present in sprint-9 section
  (9)  "Action items" subsection present in sprint-9 section
  (10) Named owner (Tester / scrum facilitator) referenced (retrospective A3 contract)
  (11) Firehose budget statement present (0 activations, banked)
  (12) US-43 deploy failure noted (I1 carry action)
"""

import pathlib
import re

# Wiring guard — H1 ImportError trap
# Importing named functions from prior AC-45 test files: if either module is deleted
# or the function renamed, pytest collection fails before any test in this file runs.
from core.tests.test_ruff_hook_evidence_ac451 import (  # noqa: F401
    test_sprint9_evidence_no_ruff_defects,
    test_sprint9_evidence_verdict_is_confirmed,
)
from core.tests.test_sprint_boundary_ac452 import (  # noqa: F401
    test_promoter_wired_in_deploy_job_post_us40,
    test_sprint9_integrity_guard_passes_check_sprint,
)

RETRO_PATH = pathlib.Path(__file__).parents[2] / "scrum-master" / "retrospective.md"


def _sprint9_section(text: str) -> str:
    """Extract the sprint-9 section from the retrospective text."""
    # Find the Sprint-9 heading and return from there to the next same-level heading or end
    match = re.search(r"## Sprint-9.*", text, re.DOTALL)
    if not match:
        return ""
    section = text[match.start():]
    # Cut at the next h2 heading (if any)
    next_h2 = re.search(r"\n## Sprint-(?!9)", section)
    if next_h2:
        section = section[: next_h2.start()]
    return section


def test_retrospective_file_exists():
    """retrospective.md must exist and be readable."""
    assert RETRO_PATH.exists(), f"retrospective.md not found at {RETRO_PATH}"
    content = RETRO_PATH.read_text(encoding="utf-8")
    assert len(content) > 1000, "retrospective.md appears empty or truncated"


def test_sprint9_section_present():
    """Sprint-9 section heading must be present in retrospective.md."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    assert "## Sprint-9" in content, "Sprint-9 retrospective section not found in retrospective.md"


def test_h2_verdict_confirmed_present():
    """H2 evidence verdict 'CONFIRMED' must appear in the sprint-9 section."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert section, "Sprint-9 section could not be extracted"
    assert "CONFIRMED" in section, (
        "H2 evidence verdict 'CONFIRMED' not found in sprint-9 retrospective section"
    )


def test_p7_serving_path_complete_stated():
    """The sprint-9 section must state that the P7 serving path is complete."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    # Look for P7 serving path completion language
    assert re.search(r"serving path.*complet|P7.*complet|complete.*serving path", section, re.IGNORECASE), (
        "P7 serving path completion not stated in sprint-9 retrospective section"
    )


def test_operator_unblocked_stated():
    """The sprint-9 section must state the operator is unblocked to soak + promote."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert re.search(r"unblock.*soak|soak.*promot|operator.*unblock", section, re.IGNORECASE), (
        "Operator unblocked to soak + promote not stated in sprint-9 retrospective section"
    )


def test_what_went_well_subsection_present():
    """Sprint-9 section must contain a 'What went well' subsection."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert "went well" in section.lower(), (
        "'What went well' subsection missing from sprint-9 retrospective section"
    )


def test_what_didnt_go_well_subsection_present():
    """Sprint-9 section must contain a "What didn't go well" subsection."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert "didn't go well" in section.lower() or "did not go well" in section.lower(), (
        "'What didn't go well' subsection missing from sprint-9 retrospective section"
    )


def test_action_items_subsection_present():
    """Sprint-9 section must contain an 'Action items' subsection."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert "action items" in section.lower(), (
        "'Action items' subsection missing from sprint-9 retrospective section"
    )


def test_named_owner_tester_referenced():
    """The retrospective A3 contract (named owner: Tester / scrum facilitator) must be referenced."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    # The A3 action is established in sprint-1 and referenced via 'tester_sprint_notes' or
    # similar; the sprint-9 section should credit the Tester role.
    section = _sprint9_section(content)
    assert re.search(r"tester|scrum facilitator", section, re.IGNORECASE), (
        "Named owner (Tester / scrum facilitator) not referenced in sprint-9 retrospective section"
    )


def test_firehose_budget_preserved_stated():
    """Sprint-9 section must state the firehose budget is preserved (0 activations)."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert re.search(r"0 activation|zero.*activation|no activation|firehose.*banked", section, re.IGNORECASE), (
        "Firehose budget preservation (0 activations) not stated in sprint-9 retrospective section"
    )


def test_us43_deploy_failure_noted():
    """Sprint-9 section must note the US-43 per-story deploy failure (carry as I1)."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    section = _sprint9_section(content)
    assert re.search(r"US-43.*deploy.*fail|deploy.*fail.*US-43|27673867804", section, re.IGNORECASE), (
        "US-43 deploy failure not noted in sprint-9 retrospective section"
    )


def test_retrospective_has_metadata_front_matter():
    """retrospective.md must have a metadata front matter comment block (project convention)."""
    content = RETRO_PATH.read_text(encoding="utf-8")
    # The file uses HTML comment front matter: <!-- ... -->
    assert content.startswith("<!--"), (
        "retrospective.md does not begin with a metadata front matter comment block"
    )
    assert "last-updated" in content[:500], (
        "retrospective.md front matter does not contain a last-updated field"
    )
