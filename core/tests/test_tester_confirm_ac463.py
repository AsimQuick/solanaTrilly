# ---
# file: core/tests/test_tester_confirm_ac463.py
# project: solanatrilly
# purpose: AC-46.3 — Structural tests verifying the Tester confirmation record for the
#          VPS live-scorer check is complete and documents all five required conditions:
#          HTTP 200 on 8002, listener container Up, scorer/celery-worker Up, solanaBilly
#          UNTOUCHED on 8001, and in-container lightgbm + promote_blend import checks.
# story: US-46 AC-46.3
# sprint: sprint-10
# status: implemented
# created-by: tester
# last-updated: 2026-06-17
# dependencies: pathlib, re
# ---
"""AC-46.3 — Tester confirmation of VPS live-scorer before operator-driven soak (I2).

AC text:
  Tester-CONFIRM from that ACTUAL GREEN deploy run that the VPS has the scorer
  LIVE before the operator-driven soak (I2): HTTP 200 on 8002 (with the AC-12.3
  retry-with-backoff), the 'listener' container Up, a scorer/celery-worker
  container Up, and solanaBilly UNTOUCHED on 8001 (every command scoped
  -p solanatrilly). Additionally confirm the scorer is deployable in-container:
  lightgbm imports and tools.promote_model.promote_blend is importable inside
  the solanatrilly container (an in-container import check), proving the soak
  prerequisite is met. A partial-evidence PR-merge deploy is NOT sufficient —
  the deliberate clean run at HEAD must be green. New/changed files carry
  metadata front matter.

Deliverable: ops/tester_confirm_ac463.md — the Tester confirmation record.
Also updates: ops/green_deploy_ac462.md — green run ID filled in (no longer PENDING).

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac463.md exists and is non-empty.
  test_tester_confirm_has_metadata_front_matter
      The record carries project-convention metadata front matter.
  test_tester_confirm_cites_green_run_id
      The record references a specific non-PENDING green run ID.
  test_tester_confirm_green_run_is_at_head
      The record confirms the run was deliberate/at HEAD, not a PR-merge deploy.
  test_tester_confirm_check1_http_200_on_8002
      The record documents HTTP 200 on port 8002.
  test_tester_confirm_check1_ac123_retry_backoff
      The record documents the AC-12.3 retry-with-backoff mechanism.
  test_tester_confirm_check2_listener_container_up
      The record documents 'listener' container Up.
  test_tester_confirm_check3_scorer_celery_worker_up
      The record documents scorer/celery-worker container Up.
  test_tester_confirm_check4_solanabilly_untouched_on_8001
      The record documents solanaBilly UNTOUCHED on 8001.
  test_tester_confirm_check4_scoped_p_solanatrilly
      The record confirms every docker command was scoped -p solanatrilly.
  test_tester_confirm_check5_lightgbm_importable
      The record documents lightgbm importable in-container.
  test_tester_confirm_check5_promote_blend_importable
      The record documents tools.promote_model.promote_blend importable.
  test_tester_confirm_verdict_confirmed
      The record contains an unambiguous CONFIRMED verdict.
  test_green_deploy_record_run_id_filled_in
      ops/green_deploy_ac462.md GREEN_RUN_ID is no longer PENDING.
  test_green_deploy_record_run_conclusion_success
      ops/green_deploy_ac462.md GREEN_RUN_CONCLUSION is success.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTER_CONFIRM = REPO_ROOT / "ops" / "tester_confirm_ac463.md"
GREEN_DEPLOY_RECORD = REPO_ROOT / "ops" / "green_deploy_ac462.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    assert path.exists(), f"AC-46.3: Required file not found: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"AC-46.3: File is empty: {path}"
    return text


# ---------------------------------------------------------------------------
# Tester confirmation record existence and structure
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac463.md must exist and have substantial content.

    The tester confirmation record is the durable evidence artifact for AC-46.3.
    It must be committed to the repo so the orchestrator can verify and record it.
    """
    text = _read(TESTER_CONFIRM)
    assert len(text) > 500, (
        f"AC-46.3: Tester confirmation record at {TESTER_CONFIRM} is too short "
        f"({len(text)} chars). It must document all five VPS checks with evidence."
    )


def test_tester_confirm_has_metadata_front_matter() -> None:
    """The tester confirmation record must carry project-convention metadata front matter.

    AC-46.3: 'New/changed files carry metadata front matter.' The file must
    begin with a YAML-style front matter block (--- delimited) containing at
    minimum: file, project, purpose, story, and status fields.
    """
    text = _read(TESTER_CONFIRM)
    assert text.startswith("# ---"), (
        f"AC-46.3: Tester confirmation record must begin with '# ---' front matter. "
        f"First 80 chars: {text[:80]!r}"
    )
    for field in ("file:", "project:", "purpose:", "story:", "status:"):
        assert field in text, (
            f"AC-46.3: Tester confirmation record front matter missing field '{field}'. "
            f"File: {TESTER_CONFIRM}"
        )


def test_tester_confirm_cites_green_run_id() -> None:
    """The record must reference a specific, non-PENDING green deploy run ID.

    AC-46.3: 'from that ACTUAL GREEN deploy run' — the record must cite the
    numeric GitHub Actions run ID of the green deploy run, not a placeholder.
    """
    text = _read(TESTER_CONFIRM)
    assert "PENDING" not in text, (
        "AC-46.3: Tester confirmation record must NOT contain 'PENDING'. "
        "The green run ID must be filled in with the actual run ID."
    )
    run_id_match = re.search(r"\b\d{10,}\b", text)
    assert run_id_match is not None, (
        "AC-46.3: Tester confirmation record must contain a numeric GitHub Actions "
        "run ID (10+ digits). Pattern not found in the record."
    )


def test_tester_confirm_green_run_is_at_head() -> None:
    """The record must confirm the run was deliberate (at HEAD), not a PR-merge deploy.

    AC-46.3: 'A partial-evidence PR-merge deploy is NOT sufficient — the deliberate
    clean run at HEAD must be green.' The record must explicitly document this.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(head|at HEAD|clean run|deliberate)", text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must confirm the run was at HEAD / deliberate. "
        "Partial-evidence PR-merge deploy evidence is not sufficient per AC-46.3."
    )
    assert re.search(r"(main|branch|conclusion.*success|success)", text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must confirm the run concluded as success "
        "on the main branch."
    )


# ---------------------------------------------------------------------------
# Check 1 — HTTP 200 on 8002 + AC-12.3 retry-with-backoff
# ---------------------------------------------------------------------------


def test_tester_confirm_check1_http_200_on_8002() -> None:
    """The record must document HTTP 200 on port 8002.

    AC-46.3: 'HTTP 200 on 8002' — confirmed from the VPS by the Tester.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(HTTP 200|200 on.{0,30}8002|8002.{0,30}200)", text) is not None, (
        "AC-46.3: Tester confirmation record must document HTTP 200 on port 8002. "
        f"File: {TESTER_CONFIRM}"
    )
    assert "8002" in text, (
        "AC-46.3: Tester confirmation record must reference port 8002."
    )


def test_tester_confirm_check1_ac123_retry_backoff() -> None:
    """The record must document the AC-12.3 retry-with-backoff mechanism.

    AC-46.3: 'HTTP 200 on 8002 (with the AC-12.3 retry-with-backoff)' — the
    record must confirm the retry mechanism was used in the smoke-test check.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"AC-12\.3|retry.{0,20}backoff|SMOKE_MAX_ATTEMPTS", text) is not None, (
        "AC-46.3: Tester confirmation record must reference AC-12.3 retry-with-backoff. "
        f"File: {TESTER_CONFIRM}"
    )


# ---------------------------------------------------------------------------
# Check 2 — 'listener' container Up
# ---------------------------------------------------------------------------


def test_tester_confirm_check2_listener_container_up() -> None:
    """The record must document the 'listener' container being Up.

    AC-46.3: 'the "listener" container Up' — confirmed from VPS ps output.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"listener.{0,50}(up|running|Up)", text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must document 'listener' container Up. "
        f"File: {TESTER_CONFIRM}"
    )


# ---------------------------------------------------------------------------
# Check 3 — scorer/celery-worker container Up
# ---------------------------------------------------------------------------


def test_tester_confirm_check3_scorer_celery_worker_up() -> None:
    """The record must document a scorer/celery-worker container being Up.

    AC-46.3: 'a scorer/celery-worker container Up' — confirmed from VPS ps output.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"celery.worker.{0,50}(up|running|Up)", text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must document celery-worker container Up. "
        f"File: {TESTER_CONFIRM}"
    )


# ---------------------------------------------------------------------------
# Check 4 — solanaBilly UNTOUCHED on 8001, scoped -p solanatrilly
# ---------------------------------------------------------------------------


def test_tester_confirm_check4_solanabilly_untouched_on_8001() -> None:
    """The record must document solanaBilly untouched on port 8001.

    AC-46.3: 'solanaBilly UNTOUCHED on 8001' — confirmed from VPS isolation check.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(solanaBilly|solanabilly|8001).{0,100}(untouched|up|running|responding|PASS)",
                     text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must document solanaBilly untouched on 8001. "
        f"File: {TESTER_CONFIRM}"
    )
    assert "8001" in text, (
        "AC-46.3: Tester confirmation record must reference port 8001."
    )


def test_tester_confirm_check4_scoped_p_solanatrilly() -> None:
    """The record must confirm every docker command was scoped -p solanatrilly.

    AC-46.3: '(every command scoped -p solanatrilly)' — the hard isolation rule
    from CLAUDE.md must be documented as honored.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"-p\s+solanatrilly|p solanatrilly|scoped.*solanatrilly", text) is not None, (
        "AC-46.3: Tester confirmation record must confirm docker commands were scoped "
        "'-p solanatrilly'. Hard isolation rule from CLAUDE.md."
    )


# ---------------------------------------------------------------------------
# Check 5 — In-container import check
# ---------------------------------------------------------------------------


def test_tester_confirm_check5_lightgbm_importable() -> None:
    """The record must document lightgbm importable inside the container.

    AC-46.3: 'lightgbm imports ... inside the solanatrilly container
    (an in-container import check)'.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(lightgbm|lgb).{0,50}(import|PASS|4\.\d)", text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must document lightgbm importable in-container. "
        f"File: {TESTER_CONFIRM}"
    )


def test_tester_confirm_check5_promote_blend_importable() -> None:
    """The record must document tools.promote_model.promote_blend importable in-container.

    AC-46.3: 'tools.promote_model.promote_blend is importable inside the
    solanatrilly container (an in-container import check), proving the soak
    prerequisite is met.'
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(promote_blend|tools\.promote_model).{0,50}(import|PASS)", text,
                     re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must document tools.promote_model.promote_blend "
        "importable in-container. This proves the soak prerequisite is met."
    )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def test_tester_confirm_verdict_confirmed() -> None:
    """The record must contain an unambiguous CONFIRMED verdict.

    AC-46.3 is a Tester-CONFIRM AC — the deliverable must not be ambiguous.
    The record must state CONFIRMED (or equivalent) to satisfy the Tester gate.
    """
    text = _read(TESTER_CONFIRM)
    assert re.search(r"(CONFIRMED|confirmed|all.{0,30}pass|soak prerequisite.*met)",
                     text, re.IGNORECASE) is not None, (
        "AC-46.3: Tester confirmation record must contain an unambiguous CONFIRMED verdict "
        "or 'soak prerequisite met' statement. "
        f"File: {TESTER_CONFIRM}"
    )


# ---------------------------------------------------------------------------
# Green deploy record — run ID must be filled in (no longer PENDING)
# ---------------------------------------------------------------------------


def test_green_deploy_record_run_id_filled_in() -> None:
    """ops/green_deploy_ac462.md GREEN_RUN_ID must no longer be PENDING.

    AC-46.2 required the green run ID to be recorded. AC-46.3 confirms from
    that run. The record must have been updated with the actual run ID.
    """
    text = _read(GREEN_DEPLOY_RECORD)
    assert "GREEN_RUN_ID: PENDING" not in text, (
        "AC-46.3: ops/green_deploy_ac462.md GREEN_RUN_ID is still PENDING. "
        "The actual green run ID must be filled in."
    )
    run_id_match = re.search(r"GREEN_RUN_ID:\s*(\d+)", text)
    assert run_id_match is not None, (
        "AC-46.3: ops/green_deploy_ac462.md must contain 'GREEN_RUN_ID: <numeric_id>'. "
        f"File: {GREEN_DEPLOY_RECORD}"
    )
    run_id = run_id_match.group(1)
    assert len(run_id) >= 10, (
        f"AC-46.3: GREEN_RUN_ID '{run_id}' looks too short for a GitHub Actions run ID."
    )


def test_green_deploy_record_run_conclusion_success() -> None:
    """ops/green_deploy_ac462.md GREEN_RUN_CONCLUSION must be success.

    AC-46.3: 'the deliberate clean run at HEAD must be green'. The green deploy
    record must confirm a successful conclusion.
    """
    text = _read(GREEN_DEPLOY_RECORD)
    assert "GREEN_RUN_CONCLUSION: PENDING" not in text, (
        "AC-46.3: ops/green_deploy_ac462.md GREEN_RUN_CONCLUSION is still PENDING. "
        "The run conclusion must be recorded."
    )
    assert re.search(r"GREEN_RUN_CONCLUSION:\s*success", text) is not None, (
        "AC-46.3: ops/green_deploy_ac462.md GREEN_RUN_CONCLUSION must be 'success'. "
        f"Current record: {GREEN_DEPLOY_RECORD}"
    )
