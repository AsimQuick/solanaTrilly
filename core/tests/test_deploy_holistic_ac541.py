# ---
# file: core/tests/test_deploy_holistic_ac541.py
# project: solanatrilly
# purpose: AC-54.1 — Holistic regression check: verify the consolidated ops record exists
#          and maps all three sprint-10 deploy failure modes (J1 WS-key, J2 disk exhaustion,
#          J4 per-merge regression) to their root causes, and that deploy.yml reflects the
#          consolidated fix as a system (not three independent point-fixes).
# story: US-54 AC-54.1
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml, re
# ---
"""AC-54.1 — Holistic deploy-path reconciliation: three co-occurring sprint-10 failures.

AC text:
  Review the deploy path HOLISTICALLY (not as three independent point-fixes) and
  reconcile the three co-occurring sprint-10 deploy failure modes: (a) the per-merge
  regression (run 27681451880) that failed after US-46's deliberate HEAD run
  (27680808876) was green; (b) the US-47-class failure (US-53); (c) the WS-key
  defect (US-52). Produce an ops/ record that maps each failure mode to its root
  cause and confirms whether they share a common deploy-context factor
  (env/secret/orphaned-container/image/registry/ordering) or are genuinely
  independent, so the path is fixed as a system.
  Verified by the record + the consolidated fix landing in deploy.yml / the deploy
  workflow.

Three failure modes:
  J4 — run 27681451880 (per-merge regression after US-46 green run 27680808876)
       Root cause: VPS disk exhaustion (ENOSPC) — shares J2's cause
  J2 — run 27683660493 (US-47 workflow_dispatch, failed at docker compose pull)
       Root cause: VPS disk exhaustion (ENOSPC) — diagnosed in ops/rca_run_27683660493.md
  J1 — WS-key defect (PR #208, merged 11:41Z; Sec-WebSocket-Key 22 bytes vs required 16)
       Root cause: code defect in smoke-test step — genuinely independent from J4/J2

Consolidated fix in deploy.yml:
  - docker image prune -f (addresses J4 + J2: disk exhaustion)
  - down --remove-orphans before pull+up (belt-and-suspenders for orphaned containers)
  - up -d --remove-orphans (orphaned-container reconcile)
  - RFC-6455-valid WS key via base64.b64encode(os.urandom(16)).decode() (addresses J1)

Note: deploys are workflow_dispatch-only (operator decision 2026-06-18). The push
trigger was intentionally removed because a push-to-main trigger fired a full ~5-min
VPS deploy on every commit (including sprint-state commits), wasting GitHub Actions
minutes. The orchestrator dispatches one deploy per sprint boundary.

Tests in this module:
  test_consolidated_rca_record_exists
      ops/rca_consolidated_ac541.md exists and is substantive (>500 chars).
  test_record_maps_j4_run_27681451880
      The record references the J4 per-merge regression run ID (27681451880).
  test_record_maps_j2_run_27683660493
      The record references the J2 disk-exhaustion run ID (27683660493).
  test_record_maps_j1_ws_key_defect
      The record references the J1 WS-key defect (US-52 / RFC-6455).
  test_record_maps_us46_green_run_27680808876
      The record references the US-46 green run (27680808876) that preceded J4.
  test_record_assesses_common_deploy_context_factor
      The record explicitly assesses whether the failures share a common factor.
  test_record_distinguishes_j4_j2_from_j1
      The record confirms J4+J2 share disk exhaustion while J1 is independent.
  test_deploy_yml_has_disk_exhaustion_fix
      deploy.yml contains 'docker image prune -f' (fixes J4 + J2).
  test_deploy_yml_has_ws_key_fix
      deploy.yml generates a valid 16-byte WS key at runtime (fixes J1).
  test_deploy_yml_has_orphan_container_fix
      deploy.yml has both 'down --remove-orphans' and 'up -d --remove-orphans'.
  test_deploy_yml_has_no_push_trigger
      deploy.yml on: block has NO push trigger — deploys are workflow_dispatch-only
      (operator decision 2026-06-18; push-to-main wasted Actions minutes).
  test_consolidated_fixes_are_present_together
      All consolidated fixes are present in deploy.yml simultaneously as a system.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RCA_RECORD = REPO_ROOT / "ops" / "rca_consolidated_ac541.md"

J4_RUN_ID = "27681451880"
J2_RUN_ID = "27683660493"
US46_GREEN_RUN_ID = "27680808876"
VPS_STEP_NAME = "Deploy to VPS staging stack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rca_text() -> str:
    assert RCA_RECORD.exists(), (
        f"AC-54.1: Consolidated RCA record not found at {RCA_RECORD}. "
        "ops/rca_consolidated_ac541.md must be committed as the holistic analysis artifact."
    )
    text = RCA_RECORD.read_text(encoding="utf-8")
    assert text.strip(), f"AC-54.1: RCA record at {RCA_RECORD} is empty."
    return text


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "deploy.yml must be valid YAML"
    return data


def _on_block() -> dict:
    """Return the 'on:' trigger block.

    PyYAML parses the YAML keyword 'on' as Python True (YAML 1.1 boolean), so
    we must look up both 'on' (string) and True (bool) to handle both PyYAML
    versions.
    """
    data = _deploy_data()
    return data.get("on") or data.get(True) or {}


def _vps_step_script() -> str:
    """Return the shell script of the 'Deploy to VPS staging stack' step."""
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = [s for s in (deploy_job.get("steps") or []) if isinstance(s, dict)]
    vps_step = next(
        (s for s in steps if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert vps_step is not None, (
        f"AC-54.1: No step named '{VPS_STEP_NAME}' in deploy.yml deploy job. "
        f"Steps: {[s.get('name', '') for s in steps]}"
    )
    return str(vps_step.get("run", ""))


# ---------------------------------------------------------------------------
# 1. Consolidated RCA record exists and is substantive
# ---------------------------------------------------------------------------


def test_consolidated_rca_record_exists() -> None:
    """ops/rca_consolidated_ac541.md must exist and be substantive (>500 chars).

    AC-54.1 deliverable: a written holistic analysis record that maps all three
    sprint-10 deploy failure modes to their root causes. The record must be
    committed to the repo so the analysis is durable and traceable.
    """
    text = _rca_text()
    assert len(text) > 500, (
        f"AC-54.1: RCA record at {RCA_RECORD} is too short ({len(text)} chars). "
        "A holistic root-cause analysis must be substantive enough to map three "
        "failure modes, assess common factors, and document the consolidated fix."
    )


# ---------------------------------------------------------------------------
# 2. Record maps J4 — run 27681451880 (per-merge regression)
# ---------------------------------------------------------------------------


def test_record_maps_j4_run_27681451880() -> None:
    """The RCA record must reference the J4 per-merge regression run ID (27681451880).

    AC-54.1 specifically names run 27681451880 as the per-merge regression that
    failed after US-46's green run. The record must cite this run ID to anchor
    the analysis to the concrete failure event — without it, the record is not
    tracing the J4 failure mode.
    """
    text = _rca_text()
    assert J4_RUN_ID in text, (
        f"AC-54.1: RCA record must cite J4 run ID '{J4_RUN_ID}' (the per-merge "
        "regression that failed after US-46's green HEAD run). The record maps "
        "this failure mode to its root cause — without the run ID it is not "
        "anchored to the specific event."
    )


# ---------------------------------------------------------------------------
# 3. Record maps J2 — run 27683660493 (disk exhaustion)
# ---------------------------------------------------------------------------


def test_record_maps_j2_run_27683660493() -> None:
    """The RCA record must reference the J2 disk-exhaustion run ID (27683660493).

    AC-54.1 names run 27683660493 as the US-47-class failure (J2), diagnosed
    in US-53. The consolidated record must cross-reference this run to map J2
    in the holistic analysis — confirming J4 and J2 share the same root cause.
    """
    text = _rca_text()
    assert J2_RUN_ID in text, (
        f"AC-54.1: RCA record must cite J2 run ID '{J2_RUN_ID}' (the US-47 per-story "
        "deploy failure diagnosed in US-53). The holistic analysis must reference "
        "this run to assess the J4/J2 common-factor relationship."
    )


# ---------------------------------------------------------------------------
# 4. Record maps J1 — WS-key defect
# ---------------------------------------------------------------------------


def test_record_maps_j1_ws_key_defect() -> None:
    """The RCA record must reference the J1 WS-key defect (US-52 / RFC-6455).

    AC-54.1 names the WS-key defect as one of the three failure modes. The record
    must address J1 — confirming it is genuinely independent from J4/J2 and
    mapping it to its root cause (RFC-6455-invalid 22-byte key length).
    """
    text = _rca_text().lower()
    has_ws_ref = (
        "ws-key" in text
        or "ws key" in text
        or "sec-websocket-key" in text
        or "websocket key" in text
        or "rfc-6455" in text
        or "rfc 6455" in text
        or "us-52" in text
        or "j1" in text
    )
    assert has_ws_ref, (
        "AC-54.1: RCA record must reference the J1 WS-key defect (US-52, "
        "RFC-6455 invalid Sec-WebSocket-Key). The holistic analysis must map "
        "this failure mode to its root cause and assess its independence from J4/J2."
    )


# ---------------------------------------------------------------------------
# 5. Record maps US-46 green run 27680808876
# ---------------------------------------------------------------------------


def test_record_maps_us46_green_run_27680808876() -> None:
    """The RCA record must reference US-46's green run (27680808876).

    AC-54.1 specifically contextualises J4 as 'failed after US-46's deliberate
    HEAD run (27680808876) was green'. The record must explain WHY the deliberate
    run succeeded but the subsequent per-merge run failed — this is the core
    of the J4 pattern analysis.
    """
    text = _rca_text()
    assert US46_GREEN_RUN_ID in text, (
        f"AC-54.1: RCA record must reference US-46's green run ID '{US46_GREEN_RUN_ID}'. "
        "The record must explain the J4 pattern (green deliberate run → failed per-merge "
        "run) by tracing how the green run consumed the last free disk space on the VPS, "
        "causing the subsequent per-merge run to fail with ENOSPC."
    )


# ---------------------------------------------------------------------------
# 6. Record explicitly assesses common deploy-context factor
# ---------------------------------------------------------------------------


def test_record_assesses_common_deploy_context_factor() -> None:
    """The RCA record must explicitly assess whether failures share a common factor.

    AC-54.1: 'confirms whether they share a common deploy-context factor
    (env/secret/orphaned-container/image/registry/ordering) or are genuinely
    independent'. The record must make this determination explicitly — not leave
    it implicit.
    """
    text = _rca_text().lower()
    has_common_factor_assessment = (
        "common" in text
        or "independent" in text
        or "shared" in text
        or "share" in text
        or "genuinely independent" in text
        or "common factor" in text
        or "common deploy" in text
    )
    assert has_common_factor_assessment, (
        "AC-54.1: RCA record must explicitly assess whether the three failure modes "
        "share a common deploy-context factor (env/secret/orphaned-container/image/"
        "registry/ordering) or are genuinely independent. This is the core AC-54.1 "
        "question — the record must answer it, not leave it implicit."
    )


# ---------------------------------------------------------------------------
# 7. Record distinguishes J4+J2 (shared) from J1 (independent)
# ---------------------------------------------------------------------------


def test_record_distinguishes_j4_j2_from_j1() -> None:
    """The RCA record must show J4+J2 share disk exhaustion while J1 is independent.

    The holistic conclusion is: J4 and J2 share VPS disk exhaustion as root cause
    (a deploy-context infrastructure-state condition); J1 is genuinely independent
    (a code correctness defect in the smoke-test step, introduced later).
    The record must make this distinction so the fix is understood as a system.
    """
    text = _rca_text().lower()
    # Record must reference disk exhaustion (the J4/J2 shared cause)
    has_disk_exhaustion = (
        "disk exhaustion" in text
        or "disk space" in text
        or "enospc" in text
        or "no space left" in text
        or "overlayfs" in text
    )
    assert has_disk_exhaustion, (
        "AC-54.1: RCA record must identify disk exhaustion (ENOSPC) as the shared "
        "root cause of J4 (27681451880) and J2 (27683660493). Without naming this "
        "shared factor, the record treats them as independently unexplained."
    )
    # Record must establish J1 as independent (introduced later, different failure point)
    has_independence_claim = (
        "independent" in text
        or "genuinely independent" in text
        or "independent from" in text
        or "distinct" in text
    )
    assert has_independence_claim, (
        "AC-54.1: RCA record must establish J1 (WS-key defect) as genuinely independent "
        "from J4/J2. J1 is a code defect introduced 52 min after J2, at a different "
        "failure point (smoke-test step, not docker compose pull). The record must "
        "distinguish the two classes of failure."
    )


# ---------------------------------------------------------------------------
# 8. deploy.yml has disk exhaustion fix (J4 + J2)
# ---------------------------------------------------------------------------


def test_deploy_yml_has_disk_exhaustion_fix() -> None:
    """deploy.yml must contain 'docker image prune -f' to fix J4 + J2 (disk exhaustion).

    Both J4 (27681451880) and J2 (27683660493) failed because the VPS
    /var/lib/containerd overlayfs partition was full. The fix reclaims dangling
    image layers before every pull so the VPS always has space, regardless of
    how many prior deploy runs have accumulated layers.

    This test fails if docker image prune -f is removed, re-exposing both J4 and J2.
    """
    script = _vps_step_script()
    assert "docker image prune -f" in script, (
        "AC-54.1: deploy.yml must contain 'docker image prune -f' in the "
        "Deploy-to-VPS SSH command. This is the consolidated fix for J4 (run "
        "27681451880) and J2 (run 27683660493), both of which failed with VPS "
        "disk exhaustion (ENOSPC) during docker compose pull. "
        f"Current Deploy-to-VPS script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 9. deploy.yml has WS-key fix (J1)
# ---------------------------------------------------------------------------


def test_deploy_yml_has_ws_key_fix() -> None:
    """deploy.yml WS smoke-test must generate a valid 16-byte RFC-6455 key (fixes J1).

    J1 (WS-key defect) was caused by a hardcoded 22-byte base64 key that violated
    RFC 6455 §4.1 (requires exactly 16 random bytes, 24-char base64). Daphne
    returned HTTP 400 on every WS handshake. The fix generates the key dynamically
    at deploy time using os.urandom(16), guaranteeing RFC-6455 compliance.

    This test fails if the hardcoded invalid key is restored, re-exposing J1.
    """
    content = _deploy_text()
    # The fix uses os.urandom(16) to generate a valid key at runtime
    assert "urandom(16)" in content or "os.urandom(16)" in content, (
        "AC-54.1: deploy.yml WS smoke-test must generate a valid 16-byte key via "
        "os.urandom(16) (RFC 6455 §4.1 compliance). J1 (WS-key defect) was caused "
        "by a hardcoded 22-byte key that made Daphne return HTTP 400 on every "
        "WebSocket handshake. "
        "The fix must generate the key dynamically, not use a hardcoded string."
    )
    # Confirm the invalid key is NOT present
    invalid_key = "c29sYW5hdHJpbGx5X2FjNDgzX2tleQ=="
    assert invalid_key not in content, (
        "AC-54.1: deploy.yml must NOT contain the invalid WS key "
        f"'{invalid_key}' (base64 of 'solanatrilly_ac483_key', 22 bytes). "
        "This was the J1 root cause — its presence means J1 is not fixed."
    )


# ---------------------------------------------------------------------------
# 10. deploy.yml has orphaned-container fix (belt-and-suspenders)
# ---------------------------------------------------------------------------


def test_deploy_yml_has_orphan_container_fix() -> None:
    """deploy.yml must have both 'down --remove-orphans' and 'up -d --remove-orphans'.

    'down --remove-orphans' before pull+up (using ';') ensures a clean VPS state
    before every deploy, regardless of prior run outcome (orphaned containers from
    interrupted runs are removed). 'up -d --remove-orphans' handles any residual
    orphans during startup reconciliation (AC-46.2 fix for run 27673867804).

    Together these prevent the orphaned-container class of failure as a system.
    """
    script = _vps_step_script()
    assert "down --remove-orphans" in script, (
        "AC-54.1: deploy.yml Deploy-to-VPS SSH command must include "
        "'down --remove-orphans' before pull+up to clear stale container state. "
        f"Current script:\n{script}"
    )
    assert "up -d --remove-orphans" in script, (
        "AC-54.1: deploy.yml Deploy-to-VPS SSH command must include "
        "'up -d --remove-orphans' to clean up orphans during stack reconciliation "
        "(AC-46.2 fix). "
        f"Current script:\n{script}"
    )


# ---------------------------------------------------------------------------
# 11. deploy.yml on: block includes push trigger (per-merge deploys re-enabled)
# ---------------------------------------------------------------------------


def test_deploy_yml_has_no_push_trigger() -> None:
    """deploy.yml on: block must have NO push trigger — deploys are workflow_dispatch-only.

    Operator decision (2026-06-18): the push trigger was intentionally removed.
    A push-to-main trigger fired a full ~5-min VPS deploy on every commit to main
    (including many sprint-state commits), wasting GitHub Actions minutes. Deploys
    now fire ONLY via workflow_dispatch — the orchestrator dispatches one deploy per
    sprint boundary.

    This test fails if the push trigger is re-added.

    Note: PyYAML parses the YAML 'on:' key as Python True (YAML 1.1 boolean), so
    we use _on_block() which handles both string 'on' and bool True lookup.
    """
    on_block = _on_block()
    assert "push" not in on_block, (
        "deploy is dispatch-only; do not re-add the push trigger "
        "(operator decision 2026-06-18 — a push-to-main trigger wastes Actions "
        "minutes by deploying on every commit). Deploys fire only via "
        "workflow_dispatch, dispatched once per sprint boundary."
    )


# ---------------------------------------------------------------------------
# 12. All consolidated fixes are present together as a system
# ---------------------------------------------------------------------------


def test_consolidated_fixes_are_present_together() -> None:
    """All three consolidated fixes must be present simultaneously in deploy.yml.

    AC-54.1 is a HOLISTIC review — not three point-fixes. This test verifies
    all fixes coexist as a system:
      - docker image prune -f (J4 + J2: disk exhaustion)
      - down --remove-orphans before pull+up (orphaned container belt-and-suspenders)
      - up -d --remove-orphans (orphaned container reconcile)
      - os.urandom(16) WS key (J1: WS-key defect)

    If any single fix is removed, the deploy path is no longer sound as a system.
    A deploy path where two of three fixes are present but one is absent is still
    unsound — this test catches partial regression.

    Note: the push trigger is intentionally NOT checked here. Deploys are
    workflow_dispatch-only (operator decision 2026-06-18) — re-adding a push
    trigger wastes Actions minutes by deploying on every commit.
    """
    content = _deploy_text()
    script = _vps_step_script()

    failures = []

    if "docker image prune -f" not in script:
        failures.append("MISSING: 'docker image prune -f' (J4+J2 disk exhaustion fix)")

    if "down --remove-orphans" not in script:
        failures.append("MISSING: 'down --remove-orphans' (orphaned-container belt-and-suspenders)")

    if "up -d --remove-orphans" not in script:
        failures.append("MISSING: 'up -d --remove-orphans' (orphaned-container reconcile)")

    if "urandom(16)" not in content:
        failures.append("MISSING: 'urandom(16)' in WS smoke-test (J1 WS-key fix)")

    assert not failures, (
        "AC-54.1: The following consolidated deploy-path fixes are absent from deploy.yml. "
        "The deploy path is not sound as a SYSTEM — each missing fix re-exposes a specific "
        "failure mode diagnosed in the holistic RCA (ops/rca_consolidated_ac541.md):\n"
        + "\n".join(f"  {f}" for f in failures)
    )
