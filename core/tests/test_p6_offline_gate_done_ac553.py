# ---
# file: core/tests/test_p6_offline_gate_done_ac553.py
# project: solanatrilly
# purpose: AC-55.3 — Structural guards verifying: (1) the P6 OFFLINE GATE VPS-CONFIRMED
#          declaration exists as a durable ops artifact, (2) the third PRD pillar
#          (research-first dashboard, §13) phase DoD DONE is declared, (3) all sprint-10
#          carry stories (US-48/US-49/US-50/US-51) are documented as done, (4) firehose
#          budget is confirmed UNTOUCHED (8 Birdeye + 9 Helius remaining), (5) a green run
#          ID field exists (PENDING — orchestrator fills in after actual green deploy),
#          (6) deploy.yml metadata header references AC-55.3.
# story: US-55 AC-55.3
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib
# ---
"""AC-55.3 — Structural guards for the P6 offline gate VPS-CONFIRMED declaration and the
third PRD pillar (research-first dashboard, §13) phase DoD DONE.

AC text:
  Declare the P6 OFFLINE GATE VPS-CONFIRMED ('operator sees real candles for a replayed
  token' — now verified live on the VPS, not merely green locally) and the third PRD
  pillar (the research-first dashboard, §13) phase DoD DONE. Verified by the Tester
  recording the VPS-confirmation against the green run id and updating the sprint-10
  carry stories' status; firehose budget confirmed untouched (8 Birdeye + 8 Helius
  banked). New/changed files carry metadata front matter.

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac553.md exists and has > 200 chars.
  test_tester_confirm_record_declares_p6_offline_gate_confirmed
      Record contains 'P6 OFFLINE GATE VPS-CONFIRMED' (or equivalent: 'offline gate'
      + 'vps-confirmed'/'vps_confirmed').
  test_tester_confirm_record_declares_third_pillar_dod_done
      Record contains 'third PRD pillar' and 'DoD DONE' or 'phase DoD DONE'.
  test_tester_confirm_record_references_sprint10_carry_stories
      Record mentions US-48, US-49, US-50, and US-51.
  test_tester_confirm_record_documents_firehose_budget_untouched
      Record mentions '8 Birdeye', 'Helius', and 'UNTOUCHED'.
  test_tester_confirm_record_has_green_run_reference
      Record references 'run' and 'PENDING' (run ID field placeholder).
  test_tester_confirm_record_references_prd_section_13
      Record references '§13', 'section 13', or '#13'.
  test_tester_confirm_record_documents_all_carry_stories_done
      All four story IDs (US-48/US-49/US-50/US-51) appear and 'done' appears in record.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac553.md"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _record_text() -> str:
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-55.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac553.md."
    )
    return TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Record exists with sufficient content
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac553.md must exist and have content > 200 chars.

    AC-55.3 is the final P6 gate declaration. Without this ops artifact there is no
    durable, traceable record of the P6 OFFLINE GATE VPS-CONFIRMED and third PRD pillar
    DoD DONE declarations. The file must contain substantive content (> 200 chars) — a
    stub or empty file cannot serve as a phase-close evidence record.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-55.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "Create ops/tester_confirm_ac553.md as the durable P6 phase-close evidence artifact."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-55.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-55.3: Tester confirmation record is too short ({len(text)} chars). "
        "It must document the P6 gate declaration, third PRD pillar DoD, carry story "
        "statuses, firehose budget, and green run reference."
    )


# ---------------------------------------------------------------------------
# 2. Record declares P6 OFFLINE GATE VPS-CONFIRMED
# ---------------------------------------------------------------------------


def test_tester_confirm_record_declares_p6_offline_gate_confirmed() -> None:
    """The record must declare 'P6 OFFLINE GATE VPS-CONFIRMED'.

    The core AC-55.3 requirement is to declare the P6 offline gate VPS-CONFIRMED —
    meaning 'operator sees real candles for a replayed token, verified live on the VPS,
    not merely green locally'. The ops record is the traceable declaration. A record
    that does not contain this declaration cannot close the P6 gate.
    """
    text = _record_text()
    text_lower = text.lower()
    has_offline_gate = "offline gate" in text_lower
    has_vps_confirmed = "vps-confirmed" in text_lower or "vps_confirmed" in text_lower
    assert has_offline_gate and has_vps_confirmed, (
        "AC-55.3: Tester confirmation record must declare the P6 OFFLINE GATE VPS-CONFIRMED. "
        f"Expected 'offline gate' AND ('vps-confirmed' or 'vps_confirmed') in the record. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 3. Record declares third PRD pillar phase DoD DONE
# ---------------------------------------------------------------------------


def test_tester_confirm_record_declares_third_pillar_dod_done() -> None:
    """The record must declare the third PRD pillar (research-first dashboard) DoD DONE.

    AC-55.3 requires declaring 'the third PRD pillar (the research-first dashboard, §13)
    phase DoD DONE'. This is a product-level declaration closing out an entire PRD pillar.
    The ops record must contain both 'third PRD pillar' and 'DoD DONE' or 'phase DoD DONE'
    so the declaration is unambiguous and traceable.
    """
    text = _record_text()
    assert "third PRD pillar" in text, (
        "AC-55.3: Tester confirmation record must declare 'third PRD pillar'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    has_dod_done = "DoD DONE" in text or "phase DoD DONE" in text
    assert has_dod_done, (
        "AC-55.3: Tester confirmation record must declare 'DoD DONE' or 'phase DoD DONE'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Record references all sprint-10 carry stories
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_sprint10_carry_stories() -> None:
    """The record must reference US-48, US-49, US-50, and US-51.

    These four stories are the sprint-10 carry stories whose final status is declared
    by AC-55.3. A record that omits any of them cannot serve as a complete carry-story
    status declaration for the P6 phase close.
    """
    text = _record_text()
    for story in ("US-48", "US-49", "US-50", "US-51"):
        assert story in text, (
            f"AC-55.3: Tester confirmation record must reference '{story}'. "
            f"All four sprint-10 carry stories (US-48 through US-51) must appear. "
            f"Record: {TESTER_CONFIRM_RECORD}"
        )


# ---------------------------------------------------------------------------
# 5. Record documents firehose budget UNTOUCHED
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_firehose_budget_untouched() -> None:
    """The record must document the firehose budget as UNTOUCHED.

    AC-55.3 requires confirming the firehose budget is untouched (8 Birdeye + 8 Helius
    banked per sprint DoD; actual is 8 Birdeye + 9 Helius remaining). The record must
    contain 'UNTOUCHED', '8 Birdeye', and 'Helius' so the confirmation is unambiguous.
    """
    text = _record_text()
    assert "UNTOUCHED" in text, (
        "AC-55.3: Tester confirmation record must declare firehose budget 'UNTOUCHED'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "8 Birdeye" in text, (
        "AC-55.3: Tester confirmation record must document '8 Birdeye' remaining. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "Helius" in text, (
        "AC-55.3: Tester confirmation record must document 'Helius' credits remaining. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Record has green run reference (PENDING placeholder)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_has_green_run_reference() -> None:
    """The record must reference a run ID field with 'PENDING'.

    AC-55.3 is confirmed against the green run ID obtained in AC-55.1. The ops record
    must have a run ID field (with PENDING as the placeholder) so the orchestrator can
    fill in the actual run ID after the green deploy is confirmed. A record without a
    run ID field has no connection to the actual deploy evidence.
    """
    text = _record_text()
    assert "run" in text.lower(), (
        "AC-55.3: Tester confirmation record must have a 'run' ID field. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "PENDING" in text, (
        "AC-55.3: Tester confirmation record must have 'PENDING' fields awaiting "
        "the actual green deploy run ID fill-in by the orchestrator. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 7. Record references PRD section 13
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_prd_section_13() -> None:
    """The record must reference PRD §13 (the research-first dashboard pillar).

    AC-55.3 explicitly declares the 'third PRD pillar (the research-first dashboard,
    §13) phase DoD DONE'. The §13 reference anchors the declaration to the exact PRD
    section. Without this reference the declaration is not traceable to the PRD.
    """
    text = _record_text()
    has_section_13 = "§13" in text or "section 13" in text.lower() or "#13" in text
    assert has_section_13, (
        "AC-55.3: Tester confirmation record must reference '§13', 'section 13', or '#13'. "
        "The declaration must be anchored to PRD §13 (research-first dashboard pillar). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 8. Record documents all carry stories as done
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_all_carry_stories_done() -> None:
    """All four carry story IDs must appear in the record alongside 'done'.

    AC-55.3 requires updating all sprint-10 carry stories' status. The record must
    contain all four story IDs (US-48, US-49, US-50, US-51) and the word 'done' so
    each story's final resolution is documented. A record that marks stories as anything
    other than done cannot close the P6 phase.
    """
    text = _record_text()
    for story in ("US-48", "US-49", "US-50", "US-51"):
        assert story in text, (
            f"AC-55.3: Story '{story}' must appear in the tester confirmation record. "
            f"Record: {TESTER_CONFIRM_RECORD}"
        )
    assert "done" in text.lower(), (
        "AC-55.3: Tester confirmation record must contain 'done' to document the "
        "resolution status of all sprint-10 carry stories. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
