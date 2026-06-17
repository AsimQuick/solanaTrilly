# ---
# file: core/tests/test_deploy_rca_ac461.py
# project: solanatrilly
# purpose: AC-46.1 — Verify root-cause analysis record for run 27673867804
# story: US-46 AC-46.1
# sprint: sprint-10
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, yaml
# ---
"""AC-46.1 — Root-cause diagnosis for failed US-43 per-story deploy (run 27673867804).

AC text:
  Diagnose the ROOT CAUSE of the failed US-43 per-story deploy (run 27673867804,
  conclusion: failure) — the blend serving path was CI-green on the standalone
  canonical ci.yml but its per-story boundary deploy failed (a deploy-context
  issue, not a code regression). Identify the concrete divergence and record the
  root cause (not a symptom), verified by reproducing the diagnosis against the
  deploy run logs / the unified workflow_call gate from US-40.

Deliverable: ops/rca_run_27673867804.md — the written root-cause record.

These tests verify:
  (1) The RCA document exists and is non-empty.
  (2) It references the specific failing run ID and failing step.
  (3) It identifies the CONCRETE root cause (orphaned container state /
      --remove-orphans), not just the symptom (no such container).
  (4) It explicitly rules out code regression, env/secret, migration, and
      LightGBM-image divergence.
  (5) It references the US-40 unified workflow_call gate and confirms the
      divergence is in the deploy-context path, not the test/lint gate.
  (6) The deploy.yml 'Deploy to VPS staging stack' step does NOT yet have
      --remove-orphans (structural proof this is a real open gap, not
      retroactively patched before AC-46.2).

Tests:
  test_rca_document_exists
  test_rca_cites_run_id
  test_rca_cites_failing_step
  test_rca_identifies_orphaned_container_root_cause
  test_rca_rules_out_code_regression
  test_rca_rules_out_lgbm_image_divergence
  test_rca_references_us40_workflow_call_gate
  test_rca_is_not_a_symptom_only_record
  test_deploy_yml_missing_remove_orphans_confirms_gap
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RCA_DOC = REPO_ROOT / "ops" / "rca_run_27673867804.md"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

RUN_ID = "27673867804"
FAILING_STEP = "Deploy to VPS staging stack"
ORPHAN_MARKER = "orphan"
ROOT_CAUSE_MARKER = "--remove-orphans"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rca_text() -> str:
    assert RCA_DOC.exists(), (
        f"AC-46.1: RCA document not found at {RCA_DOC}. "
        "The root-cause analysis record for run 27673867804 must be committed "
        "as ops/rca_run_27673867804.md."
    )
    text = RCA_DOC.read_text(encoding="utf-8")
    assert text.strip(), f"AC-46.1: RCA document at {RCA_DOC} is empty."
    return text


def _deploy_vps_step_script() -> str:
    """Return the raw shell script of the 'Deploy to VPS staging stack' step."""
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    jobs = data.get("jobs") or {}
    deploy_job = jobs.get("deploy")
    assert deploy_job is not None, "deploy.yml must have a 'deploy' job"
    steps = [s for s in (deploy_job.get("steps") or []) if isinstance(s, dict)]
    vps_step = next(
        (s for s in steps if FAILING_STEP in str(s.get("name", ""))),
        None,
    )
    assert vps_step is not None, (
        f"AC-46.1: No step named '{FAILING_STEP}' found in deploy.yml's deploy job. "
        f"Steps present: {[s.get('name', '') for s in steps]}"
    )
    return str(vps_step.get("run", ""))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rca_document_exists() -> None:
    """The RCA document ops/rca_run_27673867804.md must exist and be non-empty.

    AC-46.1 deliverable: a written root-cause record for the failed deploy run.
    The file must be committed to the repo so the diagnosis is durable and
    traceable alongside the sprint artifacts.
    """
    text = _rca_text()
    assert len(text) > 500, (
        f"AC-46.1: RCA document at {RCA_DOC} is too short ({len(text)} chars). "
        "A root-cause record must contain sufficient evidence to be verifiable."
    )


def test_rca_cites_run_id() -> None:
    """The RCA document must cite the specific failing run ID (27673867804).

    AC-46.1 requires the diagnosis to be 'verified by reproducing the diagnosis
    against the deploy run logs'. Without the run ID, the record is not tied to
    the specific failure event and cannot be cross-referenced against the logs.
    """
    text = _rca_text()
    assert RUN_ID in text, (
        f"AC-46.1: RCA document must cite run ID '{RUN_ID}'. "
        "The diagnosis must be anchored to the specific failing deploy run."
    )


def test_rca_cites_failing_step() -> None:
    """The RCA document must name the failing step 'Deploy to VPS staging stack'.

    AC-46.1: the root cause must be traced to the concrete failing step, not
    just to the workflow. This anchors the diagnosis to the exact execution unit
    that exited with code 1.
    """
    text = _rca_text()
    assert FAILING_STEP in text, (
        f"AC-46.1: RCA document must cite the failing step name '{FAILING_STEP}'. "
        "Without naming the step, the record identifies the failure at too coarse a level."
    )


def test_rca_identifies_orphaned_container_root_cause() -> None:
    """The RCA must identify orphaned container state as the root cause.

    AC-46.1: 'Identify the concrete divergence — not a symptom'. The symptom is
    'No such container'; the root cause is the VPS having orphaned containers
    from a prior deploy session that corrupted Docker Compose's reconciliation.
    The record must use the word 'orphan' (or 'orphaned') to demonstrate it
    has identified the mechanism, not just quoted the error message.
    """
    text = _rca_text().lower()
    assert ORPHAN_MARKER in text, (
        "AC-46.1: RCA document must identify orphaned container state as the root cause. "
        "The symptom 'No such container' is not the root cause — the root cause is that "
        "the VPS had orphaned containers from a prior deploy session that Docker Compose "
        "could not cleanly reconcile without --remove-orphans."
    )


def test_rca_rules_out_code_regression() -> None:
    """The RCA must explicitly rule out code regression as the cause.

    AC-46.1 context: 'the blend serving path was CI-green on the standalone
    canonical ci.yml but its per-story boundary deploy failed (a deploy-context
    issue, not a code regression)'. The record must document this ruling to
    confirm the investigation followed the right causal path.
    """
    text = _rca_text().lower()
    assert "code regression" in text or "not a code regression" in text, (
        "AC-46.1: RCA document must explicitly rule out code regression. "
        "The AC specifies this was a deploy-context issue, not a code regression — "
        "the record must document why the code itself is not at fault."
    )


def test_rca_rules_out_lgbm_image_divergence() -> None:
    """The RCA must explicitly rule out LightGBM image / libgomp1 divergence.

    AC-46.1 lists 'a deploy-context env/secret/migration/LightGBM-image difference'
    as candidate explanations. The record must address the LightGBM candidate
    explicitly — ruling it out demonstrates the investigation examined all the
    candidates named in the AC, not just the first plausible one.
    """
    text = _rca_text().lower()
    has_lgbm = "lightgbm" in text or "lgbm" in text or "libgomp" in text
    assert has_lgbm, (
        "AC-46.1: RCA document must address the LightGBM-image candidate cause "
        "(explicitly rule out or confirm). The AC names LightGBM-image difference "
        "as a candidate — the record must show this was considered."
    )


def test_rca_references_us40_workflow_call_gate() -> None:
    """The RCA must reference the US-40 unified workflow_call gate.

    AC-46.1: 'verified by reproducing the diagnosis against the deploy run logs /
    the unified workflow_call gate from US-40'. The record must reference US-40
    or 'workflow_call' to demonstrate the diagnosis was verified against the
    gate architecture, confirming the divergence is in the deploy-context path,
    not in the ci.yml test job.
    """
    text = _rca_text().lower()
    has_us40_ref = "us-40" in text or "us40" in text or "workflow_call" in text
    assert has_us40_ref, (
        "AC-46.1: RCA document must reference the US-40 unified workflow_call gate "
        "or 'workflow_call'. The diagnosis must be verified against the gate to "
        "confirm the ci.yml test job passed and the failure is in the deploy path."
    )


def test_rca_is_not_a_symptom_only_record() -> None:
    """The RCA must name the resolution mechanism, not just the error.

    AC-46.1: 'record the root cause (not a symptom)'. A symptom-only record
    would cite the error message ('No such container') but not explain what to
    fix. The resolution mechanism is '--remove-orphans'. The RCA must name it
    so AC-46.2 (the clean re-deploy) has a concrete remediation to implement.
    """
    text = _rca_text()
    assert ROOT_CAUSE_MARKER in text, (
        "AC-46.1: RCA document must name '--remove-orphans' as the resolution "
        "mechanism. Without naming the fix, the record identifies a symptom, not "
        "a root cause — AC-46.2's clean re-deploy depends on knowing what to change."
    )


def test_deploy_yml_missing_remove_orphans_confirms_gap() -> None:
    """deploy.yml must NOT yet have --remove-orphans — confirming this is a real open gap.

    AC-46.1 is a diagnostic AC only; AC-46.2 adds the fix. If --remove-orphans
    is already present in the current deploy.yml, either AC-46.2 was pre-implemented
    (which violates the one-AC-per-branch rule) or the gap no longer exists and
    AC-46.1's root cause is incorrect. This test proves the RCA's root cause is
    still a live gap in the deploy pipeline.

    NOTE: This test will legitimately fail (and must be removed or inverted) after
    AC-46.2 adds --remove-orphans to deploy.yml.
    """
    script = _deploy_vps_step_script()
    assert "--remove-orphans" not in script, (
        "AC-46.1: deploy.yml already contains '--remove-orphans' in the "
        f"'{FAILING_STEP}' step. This means either:\n"
        "  (a) AC-46.2 was pre-implemented before AC-46.1 was closed (one-AC-per-branch"
        " violation), or\n"
        "  (b) the fix was already in place before US-46 started (check if ALREADY-SATISFIED"
        " applies to AC-46.2).\n"
        "If AC-46.2 has been implemented, remove this test from AC-46.1's test file."
    )
