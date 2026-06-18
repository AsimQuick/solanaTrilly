# ---
# file: core/tests/test_deploy_rca_ac531.py
# project: solanatrilly
# purpose: AC-53.1 — Verify root-cause analysis record for run 27683660493
# story: US-53 AC-53.1
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-53.1 — Root-cause diagnosis for failed US-47 per-story deploy (run 27683660493).

AC text:
  Diagnose the ROOT CAUSE (not a symptom) of the failed US-47 per-story deploy
  (run 27683660493, conclusion: failure) by going to the run logs / the box per
  the D5 'on-box before concluding' rule. The US-47 mechanism (the AI dev-agent
  PreToolUse ruff gate) was CI-green; the failure predates the AC-48.3 WS-key
  defect (US-47 last merged 10:49Z; PR #208 merged 11:41Z) so it is a DISTINCT
  cause. Identify the concrete divergence (e.g. a deploy-context env/secret/
  migration/orphaned-container/registry condition) and record the root cause in
  an ops/ RCA record, verified by verifying the identified root cause against
  the deploy run logs.

Deliverable: ops/rca_run_27683660493.md — the written root-cause record.

These tests verify:
  (1) The RCA document exists and is non-empty (>500 chars).
  (2) It references the specific failing run ID (27683660493).
  (3) It names the failing step ('Deploy to VPS staging stack').
  (4) It identifies the CONCRETE root cause: VPS disk space exhaustion /
      'no space left on device' during docker compose pull, NOT a symptom.
  (5) It explicitly rules out code regression.
  (6) It explicitly rules out the WS-key defect as a DISTINCT cause.
  (7) It explicitly rules out the orphaned-container class of failure as
      a DISTINCT, mutually exclusive failure mode.
  (8) It references the US-40 unified workflow_call gate and confirms the
      ci / test job was GREEN (deploy-context failure, not code failure).
  (9) It names a concrete resolution mechanism (docker image prune / disk
      cleanup), not just the error message.

Tests:
  test_rca_document_exists
  test_rca_cites_run_id
  test_rca_cites_failing_step
  test_rca_identifies_disk_exhaustion_root_cause
  test_rca_identifies_no_space_left_error
  test_rca_rules_out_code_regression
  test_rca_rules_out_ws_key_defect_as_distinct_cause
  test_rca_rules_out_orphaned_container_as_distinct_cause
  test_rca_references_us40_workflow_call_gate
  test_rca_names_resolution_mechanism
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RCA_DOC = REPO_ROOT / "ops" / "rca_run_27683660493.md"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

RUN_ID = "27683660493"
FAILING_STEP = "Deploy to VPS staging stack"
DISK_EXHAUSTION_MARKER = "no space left"
DISK_MECHANISM_MARKER = "disk"
RESOLUTION_MARKER = "prune"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rca_text() -> str:
    assert RCA_DOC.exists(), (
        f"AC-53.1: RCA document not found at {RCA_DOC}. "
        "The root-cause analysis record for run 27683660493 must be committed "
        "as ops/rca_run_27683660493.md."
    )
    text = RCA_DOC.read_text(encoding="utf-8")
    assert text.strip(), f"AC-53.1: RCA document at {RCA_DOC} is empty."
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
        f"AC-53.1: No step named '{FAILING_STEP}' found in deploy.yml's deploy job. "
        f"Steps present: {[s.get('name', '') for s in steps]}"
    )
    return str(vps_step.get("run", ""))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rca_document_exists() -> None:
    """The RCA document ops/rca_run_27683660493.md must exist and be non-empty.

    AC-53.1 deliverable: a written root-cause record for the failed deploy run.
    The file must be committed to the repo so the diagnosis is durable and
    traceable alongside the sprint artifacts.
    """
    text = _rca_text()
    assert len(text) > 500, (
        f"AC-53.1: RCA document at {RCA_DOC} is too short ({len(text)} chars). "
        "A root-cause record must contain sufficient evidence to be verifiable."
    )


def test_rca_cites_run_id() -> None:
    """The RCA document must cite the specific failing run ID (27683660493).

    AC-53.1 requires the diagnosis to be 'verified by verifying the identified
    root cause against the deploy run logs'. Without the run ID, the record is
    not tied to the specific failure event.
    """
    text = _rca_text()
    assert RUN_ID in text, (
        f"AC-53.1: RCA document must cite run ID '{RUN_ID}'. "
        "The diagnosis must be anchored to the specific failing deploy run."
    )


def test_rca_cites_failing_step() -> None:
    """The RCA document must name the failing step 'Deploy to VPS staging stack'.

    AC-53.1: the root cause must be traced to the concrete failing step, not
    just to the workflow. This anchors the diagnosis to the exact execution unit
    that exited with code 1.
    """
    text = _rca_text()
    assert FAILING_STEP in text, (
        f"AC-53.1: RCA document must cite the failing step name '{FAILING_STEP}'. "
        "Without naming the step, the record identifies the failure at too coarse a level."
    )


def test_rca_identifies_disk_exhaustion_root_cause() -> None:
    """The RCA must identify VPS disk exhaustion as the root cause mechanism.

    AC-53.1: 'Identify the concrete divergence — not a symptom'. The root cause
    is the VPS running out of disk space in /var/lib/containerd overlayfs during
    docker compose pull. The record must name this mechanism (disk exhaustion /
    space / storage) to show it has identified the infrastructure condition, not
    just quoted the error string.
    """
    text = _rca_text().lower()
    has_disk_ref = (
        "disk" in text
        or "space" in text
        or "storage" in text
        or "overlayfs" in text
        or "containerd" in text
    )
    assert has_disk_ref, (
        "AC-53.1: RCA document must identify disk space exhaustion / VPS storage "
        "as the root cause mechanism. The error 'no space left on device' is the "
        "symptom; the root cause is the VPS /var/lib/containerd partition being "
        "full from accumulated image layers."
    )


def test_rca_identifies_no_space_left_error() -> None:
    """The RCA must quote the 'no space left' ENOSPC error from the run log.

    AC-53.1: 'verified by verifying the identified root cause against the deploy
    run logs'. The literal error string from job 81877608439 timestamp
    2026-06-17T10:53:49.7154177Z must appear in the record to demonstrate the
    diagnosis was made from the actual logs, not inferred.
    """
    text = _rca_text().lower()
    assert DISK_EXHAUSTION_MARKER in text, (
        f"AC-53.1: RCA document must quote the '{DISK_EXHAUSTION_MARKER}' error "
        "from the deploy run log (job 81877608439). The diagnosis must be traceable "
        "to the literal log output, not a paraphrase."
    )


def test_rca_rules_out_code_regression() -> None:
    """The RCA must explicitly rule out code regression as the cause.

    AC-53.1: 'The US-47 mechanism (the AI dev-agent PreToolUse ruff gate) was
    CI-green'. The ci / test job passed — the failure is in the deploy-context
    path, not in the code. The record must document this ruling.
    """
    text = _rca_text().lower()
    assert "code regression" in text or "not a code regression" in text, (
        "AC-53.1: RCA document must explicitly rule out code regression. "
        "The ci / test job was GREEN — the record must confirm the US-47 "
        "ruff gate itself was not at fault."
    )


def test_rca_rules_out_ws_key_defect_as_distinct_cause() -> None:
    """The RCA must establish the WS-key defect as a DISTINCT, unrelated cause.

    AC-53.1: 'the failure predates the AC-48.3 WS-key defect (US-47 last merged
    10:49Z; PR #208 merged 11:41Z) so it is a DISTINCT cause'. The record must
    explicitly address and rule out the WS-key defect, demonstrating the two
    failures are independent.
    """
    text = _rca_text().lower()
    has_ws_key_ref = (
        "ws-key" in text
        or "ws key" in text
        or "sec-websocket-key" in text
        or "websocket key" in text
        or "ws_key" in text
        or "j1" in text
        or "us-52" in text
        or "ac-48.3" in text
        or "pr #208" in text
        or "pr#208" in text
    )
    assert has_ws_key_ref, (
        "AC-53.1: RCA document must reference and rule out the WS-key defect "
        "(AC-48.3 / US-52) as a distinct cause. The AC explicitly states the "
        "US-47 failure predates PR #208 and is a DISTINCT cause — the RCA must "
        "address this to show the two failures were independently diagnosed."
    )


def test_rca_rules_out_orphaned_container_as_distinct_cause() -> None:
    """The RCA must distinguish this failure from the orphaned-container class.

    AC-53.1 context: the co-occurring sprint-10 failures include the AC-46.1
    orphaned-container failure (run 27673867804). This run's failure is during
    'docker compose pull', not 'docker compose up -d'. The record must address
    the orphaned-container candidate to show the investigation considered and
    ruled it out as a different failure mode.
    """
    text = _rca_text().lower()
    has_orphan_ref = (
        "orphan" in text
        or "no such container" in text
        or "ac-46" in text
        or "27673867804" in text
    )
    assert has_orphan_ref, (
        "AC-53.1: RCA document must reference and distinguish from the orphaned-"
        "container failure mode (AC-46.1, run 27673867804). The US-47 failure is "
        "during 'docker compose pull', not 'docker compose up -d' — the RCA must "
        "show these are mutually exclusive failure points."
    )


def test_rca_references_us40_workflow_call_gate() -> None:
    """The RCA must reference the US-40 unified workflow_call gate.

    AC-53.1: the diagnosis must confirm the ci / test job was GREEN on the same
    commit, establishing this as a deploy-context failure. The US-40 gate or
    'workflow_call' must be referenced to anchor the verification.
    """
    text = _rca_text().lower()
    has_us40_ref = "us-40" in text or "us40" in text or "workflow_call" in text
    assert has_us40_ref, (
        "AC-53.1: RCA document must reference the US-40 unified workflow_call gate "
        "or 'workflow_call'. The diagnosis must confirm the ci.yml test job passed "
        "to establish this as a deploy-context failure, not a code failure."
    )


def test_rca_names_resolution_mechanism() -> None:
    """The RCA must name the resolution mechanism, not just the error.

    AC-53.1: 'record the root cause (not a symptom)'. A symptom-only record
    would cite 'no space left on device' but not explain what to fix. The
    resolution involves disk cleanup — specifically 'docker image prune' to
    reclaim space from accumulated unused image layers. The RCA must name it
    so AC-53.2 has a concrete remediation to implement.
    """
    text = _rca_text().lower()
    assert RESOLUTION_MARKER in text, (
        f"AC-53.1: RCA document must name '{RESOLUTION_MARKER}' (docker image prune "
        "or similar disk cleanup) as the resolution mechanism. Without naming the fix, "
        "the record identifies a symptom, not a root cause — AC-53.2's clean re-deploy "
        "depends on knowing what to change in deploy.yml."
    )
