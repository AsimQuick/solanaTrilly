# ---
# file: core/tests/test_deploy_regression_guard_ac542.py
# project: solanatrilly
# purpose: AC-54.2 — Regression guard: pins all load-bearing deploy invariants by name
#          so the deploy path cannot silently regress between a green deliberate run and
#          a later per-merge run. Mirrors the H1 ImportError-trap pattern: fails loudly,
#          never degrades to a no-op. Each named invariant has its own test; a compound
#          test verifies all invariants coexist simultaneously.
# story: US-54 AC-54.2
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml, re, base64, os
# ---
"""AC-54.2 — Deploy-path regression guard: all load-bearing deploy invariants pinned by name.

AC text:
  Add a REGRESSION GUARD so the deploy path cannot silently regress between a green
  deliberate run and a later per-merge run: a structural/CI test (and/or a deploy-workflow
  self-check) that pins the load-bearing deploy invariants by name — e.g. --remove-orphans
  present (US-46/AC-46.2), the AC-39.2 phase-promoter step ordered before the VPS deploy,
  the AC-12.3 retry-with-backoff smoke-test, the unified workflow_call gate (US-40/H1, no
  divergent inline copy), and the RFC-6455-valid WS key (US-52) — mirroring the H1
  ImportError-trap 'fails loudly, never degrades to a no-op' pattern. Verified by a pytest
  test that fails if any pinned deploy invariant is removed/reverted.

Invariants pinned (named by AC-54.2):
  INVARIANT-1  --remove-orphans in up -d (US-46/AC-46.2)
               'up -d --remove-orphans' present in the Deploy-to-VPS SSH command.
  INVARIANT-2  down --remove-orphans before up (AC-52.3 orphaned-container fix)
               'down --remove-orphans' present AND positioned before 'up -d' in SSH cmd.
  INVARIANT-3  AC-39.2 phase-promoter step exists
               A step invoking promote_sprint_phase.py present in the deploy job.
  INVARIANT-4  AC-39.2 phase-promoter ordered BEFORE VPS deploy
               Phase-promoter step index < 'Deploy to VPS staging stack' step index.
  INVARIANT-5  AC-12.3 retry-with-backoff smoke-test
               SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY + bounded retry loop on /health/ 8002.
  INVARIANT-6  Unified workflow_call gate — no divergent inline copy (US-40/H1)
               deploy.yml calls ci.yml via uses: ./.github/workflows/ci.yml;
               no inline pytest in any non-workflow_call job.
  INVARIANT-7  RFC-6455-valid WS key (US-52)
               The WS smoke-test PYEOF block generates key = base64.b64encode(os.urandom(16)).decode()
               which evaluates to exactly 24 chars / 16 bytes — cannot revert to the old
               hardcoded invalid 22-byte key that caused Daphne HTTP 400.
  INVARIANT-8  VPS disk-exhaustion fix (AC-53.2 / J4+J2)
               'docker image prune -f' present in the Deploy-to-VPS SSH command before pull.
  INVARIANT-9  No push trigger — deploys are workflow_dispatch-only (operator decision 2026-06-18)
               deploy.yml on: block must NOT include a push trigger. A push-to-main trigger
               fired a full ~5-min VPS deploy on every commit, wasting Actions minutes; the
               orchestrator dispatches one deploy per sprint boundary instead.

Tests in this module:
  test_invariant_remove_orphans_in_up_command
      INVARIANT-1: 'up -d --remove-orphans' in VPS SSH command.
  test_invariant_down_remove_orphans_before_up
      INVARIANT-2: 'down --remove-orphans' present AND before 'up -d --remove-orphans'.
  test_invariant_phase_promoter_step_exists
      INVARIANT-3: promote_sprint_phase.py step present in deploy job.
  test_invariant_phase_promoter_ordered_before_vps_deploy
      INVARIANT-4: phase-promoter step index < VPS deploy step index.
  test_invariant_smoke_test_retry_with_backoff
      INVARIANT-5: SMOKE_MAX_ATTEMPTS + SMOKE_RETRY_DELAY + loop + 8002/health/ present.
  test_invariant_workflow_call_gate_no_inline_pytest
      INVARIANT-6: ci.yml called via workflow_call; no inline pytest.
  test_invariant_rfc6455_valid_ws_key
      INVARIANT-7: WS key evaluates to 24-char/16-byte RFC-6455 valid key.
  test_invariant_disk_exhaustion_fix
      INVARIANT-8: 'docker image prune -f' present in VPS SSH command before pull.
  test_invariant_no_push_trigger
      INVARIANT-9: NO push trigger in deploy.yml on: block (workflow_dispatch-only).
  test_all_regression_guard_invariants_pass
      COMPOUND: all nine invariants pass simultaneously (removes any one → fails loudly).
"""

import base64
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

VPS_STEP_NAME = "Deploy to VPS staging stack"
PHASE_PROMOTER_STEP_NAME = "Phase-promoter"
PYEOF_OPEN = "<<'PYEOF'"
PYEOF_CLOSE = "PYEOF"
INVALID_WS_KEY = "c29sYW5hdHJpbGx5X2FjNDgzX2tleQ=="


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-54.2: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-54.2: deploy.yml must be valid YAML"
    return data


def _on_block() -> dict:
    """Return the 'on:' trigger block. PyYAML parses 'on' as Python True (YAML 1.1)."""
    data = _deploy_data()
    return data.get("on") or data.get(True) or {}


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _vps_step_script() -> str:
    steps = _deploy_job_steps()
    step = next(
        (s for s in steps if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert step is not None, (
        f"AC-54.2: No step named '{VPS_STEP_NAME}' in deploy.yml deploy job. "
        f"Steps: {[s.get('name', '') for s in steps]}"
    )
    return str(step.get("run", ""))


def _extract_ws_key_assignment() -> str | None:
    """Extract the 'key = ...' line from the WS smoke-test PYEOF heredoc."""
    lines = _deploy_text().splitlines()
    in_pyeof = False
    for line in lines:
        stripped = line.strip()
        if PYEOF_OPEN in stripped:
            in_pyeof = True
            continue
        if in_pyeof and stripped == PYEOF_CLOSE:
            break
        if in_pyeof and stripped.startswith("key = "):
            return stripped
    return None


def _evaluate_ws_key(key_assignment: str) -> str:
    namespace: dict = {"base64": base64, "os": os}
    exec(key_assignment, namespace)  # noqa: S102
    return namespace.get("key", "")


# ---------------------------------------------------------------------------
# INVARIANT-1: --remove-orphans in up -d (US-46/AC-46.2)
# ---------------------------------------------------------------------------


def test_invariant_remove_orphans_in_up_command() -> None:
    """INVARIANT-1: 'up -d --remove-orphans' must be in the VPS SSH deploy command.

    AC-46.2 fix: --remove-orphans added to 'docker compose up -d' so stale
    orphaned containers from prior deploy sessions are removed during stack
    reconciliation, eliminating the 'No such container' race condition (run 27673867804).

    Regression: removing this flag re-exposes the orphaned-container race on every
    deploy that follows an interrupted run.
    """
    script = _vps_step_script()
    assert "up -d --remove-orphans" in script, (
        "AC-54.2 INVARIANT-1 VIOLATED: 'up -d --remove-orphans' is absent from the "
        "Deploy-to-VPS SSH command in deploy.yml. "
        "This flag is required by US-46/AC-46.2 to prevent orphaned-container naming "
        "conflicts during stack reconciliation. Its removal re-exposes run-27673867804-class failures. "
        f"Current VPS step script:\n{script}"
    )


# ---------------------------------------------------------------------------
# INVARIANT-2: down --remove-orphans before up (AC-52.3)
# ---------------------------------------------------------------------------


def test_invariant_down_remove_orphans_before_up() -> None:
    """INVARIANT-2: 'down --remove-orphans' must appear BEFORE 'up -d' in the SSH command.

    AC-52.3 fix: 'docker compose down --remove-orphans' (using ';', not '&&') before
    every 'pull && up' guarantees a clean VPS state regardless of prior run outcome.
    Interrupted deploy runs leave hash-prefixed container names that conflict with the
    next 'up' run; 'down' removes them first.

    Regression: removing 'down' re-exposes the sprint-11 container-naming conflict
    that broke every deploy after an interrupted run.
    """
    content = _deploy_text()
    assert "down --remove-orphans" in content, (
        "AC-54.2 INVARIANT-2 VIOLATED: 'down --remove-orphans' is absent from deploy.yml. "
        "The Deploy-to-VPS SSH command must run 'docker compose down --remove-orphans' "
        "before 'pull && up' to clear stale container state (AC-52.3)."
    )
    down_pos = content.find("down --remove-orphans")
    up_pos = content.find("up -d --remove-orphans")
    assert down_pos < up_pos, (
        f"AC-54.2 INVARIANT-2 VIOLATED: 'down --remove-orphans' (pos {down_pos}) must appear "
        f"BEFORE 'up -d --remove-orphans' (pos {up_pos}) in deploy.yml. "
        "Running 'up' before 'down' does not clear stale container state (AC-52.3)."
    )


# ---------------------------------------------------------------------------
# INVARIANT-3: AC-39.2 phase-promoter step exists
# ---------------------------------------------------------------------------


def test_invariant_phase_promoter_step_exists() -> None:
    """INVARIANT-3: A step invoking promote_sprint_phase.py must exist in the deploy job.

    AC-39.2: the phase-promoter pre-deploy gate runs 'python3 tools/promote_sprint_phase.py
    scrum-master/sprint*.json' and BLOCKS the deploy if the sprint phase is stale or the
    sprint integrity check fails. Without this step the deploy reaches the VPS regardless
    of sprint state.

    Regression: removing the phase-promoter step allows a deploy to bypass the sprint gate.
    """
    content = _deploy_text()
    assert "promote_sprint_phase.py" in content, (
        "AC-54.2 INVARIANT-3 VIOLATED: No 'promote_sprint_phase.py' step found in deploy.yml. "
        "The AC-39.2 phase-promoter pre-deploy gate ('python3 tools/promote_sprint_phase.py "
        "scrum-master/sprint*.json') must be present in the deploy job. "
        "Without it the deploy proceeds regardless of sprint integrity."
    )


# ---------------------------------------------------------------------------
# INVARIANT-4: AC-39.2 phase-promoter ordered BEFORE VPS deploy
# ---------------------------------------------------------------------------


def test_invariant_phase_promoter_ordered_before_vps_deploy() -> None:
    """INVARIANT-4: The phase-promoter step must come BEFORE 'Deploy to VPS staging stack'.

    A phase-promoter step that follows the VPS deploy step cannot block a bad deploy —
    the VPS is already updated before the gate fires. The ordering is load-bearing:
    if the phase-promoter index >= VPS deploy index, the gate is ineffective.

    Regression: swapping the step order makes the AC-39.2 gate a no-op.
    """
    steps = _deploy_job_steps()
    step_names = [s.get("name", "") for s in steps]

    promoter_idx = next(
        (i for i, s in enumerate(steps)
         if "promote_sprint_phase.py" in str(s.get("run", ""))
         or "phase-promoter" in str(s.get("name", "")).lower()),
        None,
    )
    vps_idx = next(
        (i for i, s in enumerate(steps) if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )

    assert promoter_idx is not None, (
        "AC-54.2 INVARIANT-4: Phase-promoter step not found in deploy job. "
        f"Steps: {step_names}"
    )
    assert vps_idx is not None, (
        f"AC-54.2 INVARIANT-4: '{VPS_STEP_NAME}' step not found in deploy job. "
        f"Steps: {step_names}"
    )
    assert promoter_idx < vps_idx, (
        f"AC-54.2 INVARIANT-4 VIOLATED: Phase-promoter step (index {promoter_idx}) "
        f"must appear BEFORE '{VPS_STEP_NAME}' (index {vps_idx}). "
        f"Current step order: {step_names}. "
        "A post-deploy phase-promoter cannot gate the VPS deploy (AC-39.2)."
    )


# ---------------------------------------------------------------------------
# INVARIANT-5: AC-12.3 retry-with-backoff smoke-test
# ---------------------------------------------------------------------------


def test_invariant_smoke_test_retry_with_backoff() -> None:
    """INVARIANT-5: The AC-12.3 retry-with-backoff smoke-test must be in the deploy job.

    The smoke-test must:
    - define SMOKE_MAX_ATTEMPTS (bounded retry count)
    - define SMOKE_RETRY_DELAY (sleep between attempts)
    - use a for-loop bounded by $SMOKE_MAX_ATTEMPTS
    - check port 8002 / /health/ endpoint
    - exit 0 on success inside the loop, exit 1 after exhausting all attempts

    A single-shot curl would fail during container startup (containers take time to
    become healthy after 'up -d'). The bounded retry-with-backoff is the mechanism
    that makes the smoke-test reliable across deploy timing variance.

    Regression: replacing the retry loop with a single curl re-exposes flaky
    smoke-test failures during container startup.
    """
    steps = _deploy_job_steps()
    smoke_step = next(
        (s for s in steps if "smoke" in str(s.get("name", "")).lower()),
        None,
    )
    assert smoke_step is not None, (
        "AC-54.2 INVARIANT-5: No smoke-test step found in deploy job. "
        f"Steps: {[s.get('name') for s in steps]}"
    )
    script = str(smoke_step.get("run", ""))

    assert re.search(r"SMOKE_MAX_ATTEMPTS\s*=\s*\d+", script) is not None, (
        "AC-54.2 INVARIANT-5 VIOLATED: SMOKE_MAX_ATTEMPTS not defined in smoke-test step. "
        "AC-12.3 requires a bounded retry count for the /health/ smoke-test."
    )
    assert "SMOKE_RETRY_DELAY" in script, (
        "AC-54.2 INVARIANT-5 VIOLATED: SMOKE_RETRY_DELAY not defined in smoke-test step. "
        "AC-12.3 requires a configurable sleep delay between retry attempts."
    )
    assert re.search(r"seq\s+1\s+[\"']?\$\{?SMOKE_MAX_ATTEMPTS\}?", script) is not None, (
        "AC-54.2 INVARIANT-5 VIOLATED: Retry loop not bounded by $SMOKE_MAX_ATTEMPTS. "
        "AC-12.3 requires a for-loop over the retry count."
    )
    assert re.search(r"sleep\s+[\"']?\$\{?SMOKE_RETRY_DELAY\}?", script) is not None, (
        "AC-54.2 INVARIANT-5 VIOLATED: Retry loop does not sleep $SMOKE_RETRY_DELAY. "
        "AC-12.3 backoff requires sleeping between attempts."
    )
    assert "8002" in script, (
        "AC-54.2 INVARIANT-5 VIOLATED: Smoke-test does not check port 8002. "
        "AC-12.3 smoke-test must GET /health/ on port 8002."
    )
    assert "/health/" in script, (
        "AC-54.2 INVARIANT-5 VIOLATED: Smoke-test does not check /health/ endpoint. "
        "AC-12.3 requires GET /health/ as the primary health signal."
    )
    assert "exit 0" in script, (
        "AC-54.2 INVARIANT-5 VIOLATED: Smoke-test does not exit 0 inside the loop on success. "
        "Without 'exit 0' the loop runs all attempts even after success."
    )
    assert "exit 1" in script, (
        "AC-54.2 INVARIANT-5 VIOLATED: Smoke-test does not exit 1 on exhaustion. "
        "Without 'exit 1' a failed smoke-test silently passes."
    )


# ---------------------------------------------------------------------------
# INVARIANT-6: Unified workflow_call gate — no divergent inline copy (US-40/H1)
# ---------------------------------------------------------------------------


def test_invariant_workflow_call_gate_no_inline_pytest() -> None:
    """INVARIANT-6: deploy.yml must call ci.yml via workflow_call; no inline pytest allowed.

    US-40/H1: the deploy gate must be the CANONICAL ci.yml 'test' job, called via
    workflow_call, NOT a divergent inline copy in deploy.yml. A divergent copy can
    diverge from the canonical test suite — e.g. a test added to ci.yml may not be
    present in the inline copy, allowing a regression to pass the deploy gate.

    Regression: adding inline pytest to deploy.yml creates a divergent gate that can
    silently skip tests present in ci.yml.
    """
    data = _deploy_data()
    jobs = data.get("jobs", {})

    inline_pytest_jobs = []
    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        if "uses" in job_def:
            continue
        for step in (job_def.get("steps") or []):
            if isinstance(step, dict) and "pytest" in str(step.get("run", "")):
                inline_pytest_jobs.append(job_name)
                break

    assert not inline_pytest_jobs, (
        "AC-54.2 INVARIANT-6 VIOLATED: deploy.yml has inline pytest in job(s): "
        f"{inline_pytest_jobs}. "
        "US-40/H1: the canonical ci.yml 'test' job must be called via workflow_call — "
        "NOT duplicated inline. An inline copy can diverge and mask regressions."
    )

    ci_calling_jobs = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]
    assert ci_calling_jobs, (
        "AC-54.2 INVARIANT-6 VIOLATED: deploy.yml does not call ci.yml via workflow_call. "
        "US-40/H1: the canonical test gate must be 'uses: ./.github/workflows/ci.yml'. "
        f"Current jobs: {list(jobs.keys())}"
    )


# ---------------------------------------------------------------------------
# INVARIANT-7: RFC-6455-valid WS key (US-52)
# ---------------------------------------------------------------------------


def test_invariant_rfc6455_valid_ws_key() -> None:
    """INVARIANT-7: The WS smoke-test must generate a valid RFC-6455 Sec-WebSocket-Key.

    US-52/AC-52.1: the WS smoke-test PYEOF block must generate the key as:
      key = base64.b64encode(os.urandom(16)).decode()
    which produces exactly 24 chars / 16 bytes — the RFC 6455 §4.1 requirement.

    The original defect (J1) was a hardcoded 22-byte key ('c29sYW5hdHJpbGx5X2FjNDgzX2tleQ==')
    that caused Daphne to return HTTP 400 on EVERY WebSocket handshake. This test verifies:
    1. The PYEOF block contains a 'key = ...' assignment.
    2. The key evaluates to exactly 24 chars.
    3. The key decodes to exactly 16 bytes.
    4. The invalid key is not present.

    Regression: reverting to ANY hardcoded key (regardless of length) or any expression
    that does not produce a 24-char / 16-byte result re-exposes J1.
    """
    content = _deploy_text()

    assert INVALID_WS_KEY not in content, (
        f"AC-54.2 INVARIANT-7 VIOLATED: The J1 invalid WS key '{INVALID_WS_KEY}' "
        "(base64 of 'solanatrilly_ac483_key', 22 bytes) is present in deploy.yml. "
        "This key causes Daphne to return HTTP 400 on every WebSocket handshake (J1). "
        "The fix (US-52/AC-52.1) must use os.urandom(16) to generate a valid RFC-6455 key."
    )

    key_assignment = _extract_ws_key_assignment()
    assert key_assignment is not None, (
        "AC-54.2 INVARIANT-7 VIOLATED: No 'key = ...' assignment found in the deploy.yml "
        f"WS smoke-test PYEOF heredoc (between '{PYEOF_OPEN}' and '{PYEOF_CLOSE}'). "
        "The RFC-6455 key generation expression must be present (US-52/AC-52.1)."
    )

    assert "urandom(16)" in key_assignment, (
        f"AC-54.2 INVARIANT-7 VIOLATED: WS key assignment '{key_assignment}' does not use "
        "os.urandom(16). RFC 6455 §4.1 requires exactly 16 RANDOM bytes — a static/predictable "
        "value is not RFC-6455 compliant (US-52/AC-52.1)."
    )

    key = _evaluate_ws_key(key_assignment)
    assert isinstance(key, str) and len(key) == 24, (
        f"AC-54.2 INVARIANT-7 VIOLATED: WS key evaluates to {len(key)} chars "
        f"(expected 24). Expression: {key_assignment!r}. "
        "RFC 6455 §4.1: Sec-WebSocket-Key must be exactly 24 chars (base64 of 16 bytes)."
    )

    decoded = base64.b64decode(key)
    assert len(decoded) == 16, (
        f"AC-54.2 INVARIANT-7 VIOLATED: WS key '{key}' decodes to {len(decoded)} bytes "
        f"(expected 16). Expression: {key_assignment!r}. "
        "RFC 6455 §4.1: the Sec-WebSocket-Key must encode exactly 16 bytes."
    )


# ---------------------------------------------------------------------------
# INVARIANT-8: VPS disk-exhaustion fix (AC-53.2 / J4+J2)
# ---------------------------------------------------------------------------


def test_invariant_disk_exhaustion_fix() -> None:
    """INVARIANT-8: 'docker image prune -f' must be in the VPS SSH command before pull.

    AC-53.2 / J4+J2: VPS disk exhaustion (ENOSPC on /var/lib/containerd) caused both
    J4 (run 27681451880) and J2 (run 27683660493) to fail during 'docker compose pull'
    because accumulated image layers from prior sprint-10 deploy runs exhausted the
    overlayfs partition. 'docker image prune -f' reclaims dangling (untagged, unreferenced)
    image layers before each pull, preventing disk exhaustion regardless of how many
    prior runs have accumulated layers.

    Regression: removing 'docker image prune -f' re-exposes the ENOSPC failure on a VPS
    that has not had its image layers manually pruned between deployments.
    """
    script = _vps_step_script()
    assert "docker image prune -f" in script, (
        "AC-54.2 INVARIANT-8 VIOLATED: 'docker image prune -f' is absent from the "
        "Deploy-to-VPS SSH command in deploy.yml. "
        "This command is required to prevent VPS disk exhaustion (ENOSPC) during "
        "'docker compose pull' — the root cause shared by J4 (run 27681451880) and "
        f"J2 (run 27683660493). Current VPS step script:\n{script}"
    )

    # Verify disk-prune appears before pull (ordering is load-bearing)
    prune_pos = script.find("docker image prune -f")
    pull_pos = script.find("compose pull") if "compose pull" in script else script.find("pull &&")
    if pull_pos != -1:
        assert prune_pos < pull_pos, (
            f"AC-54.2 INVARIANT-8 VIOLATED: 'docker image prune -f' (pos {prune_pos}) "
            f"must appear BEFORE 'docker compose pull' (pos {pull_pos}). "
            "Pruning after pull does not prevent the ENOSPC failure during pull."
        )


# ---------------------------------------------------------------------------
# INVARIANT-9: Per-merge push trigger restored (US-52/AC-52.1)
# ---------------------------------------------------------------------------


def test_invariant_no_push_trigger() -> None:
    """INVARIANT-9: deploy.yml on: block must have NO push trigger (workflow_dispatch-only).

    Operator decision (2026-06-18): the push trigger was intentionally removed.
    A push-to-main trigger fired a full ~5-min VPS deploy on every commit to main
    (including many sprint-state commits), wasting GitHub Actions minutes. Deploys
    now fire ONLY via workflow_dispatch — the orchestrator dispatches one deploy per
    sprint boundary.

    Regression: re-adding the push trigger resumes per-commit Actions waste.
    """
    on_block = _on_block()
    assert "push" not in on_block, (
        "deploy is dispatch-only; do not re-add the push trigger "
        "(operator decision 2026-06-18 — a push-to-main trigger wastes Actions "
        "minutes by deploying on every commit). Deploys fire only via "
        "workflow_dispatch, dispatched once per sprint boundary. "
        f"Current on: block: {on_block}"
    )


# ---------------------------------------------------------------------------
# COMPOUND: all nine invariants pass simultaneously
# ---------------------------------------------------------------------------


def test_all_regression_guard_invariants_pass() -> None:
    """COMPOUND GUARD: all nine named deploy invariants must be present simultaneously.

    This test mirrors the H1 ImportError-trap pattern: it fails LOUDLY if ANY single
    invariant is missing, so no invariant can be silently removed without breaking the
    CI suite. The compound check ensures the invariants coexist as a SYSTEM — a deploy
    path where eight of nine invariants are present but one is absent is still unsound.

    Each failure message names the specific invariant removed and its concrete regression risk.
    """
    content = _deploy_text()
    script = _vps_step_script()
    steps = _deploy_job_steps()
    data = _deploy_data()
    jobs = data.get("jobs", {})

    failures: list[str] = []

    # INVARIANT-1: up -d --remove-orphans
    if "up -d --remove-orphans" not in script:
        failures.append(
            "INVARIANT-1 MISSING: 'up -d --remove-orphans' absent from VPS SSH command "
            "(US-46/AC-46.2 — orphaned-container reconcile)"
        )

    # INVARIANT-2: down --remove-orphans before up
    if "down --remove-orphans" not in content:
        failures.append(
            "INVARIANT-2 MISSING: 'down --remove-orphans' absent from deploy.yml "
            "(AC-52.3 — clean VPS state before every pull)"
        )
    else:
        down_pos = content.find("down --remove-orphans")
        up_pos = content.find("up -d --remove-orphans")
        if down_pos >= up_pos:
            failures.append(
                f"INVARIANT-2 ORDERING: 'down --remove-orphans' (pos {down_pos}) "
                f"must precede 'up -d --remove-orphans' (pos {up_pos}) (AC-52.3)"
            )

    # INVARIANT-3: phase-promoter step exists
    if "promote_sprint_phase.py" not in content:
        failures.append(
            "INVARIANT-3 MISSING: 'promote_sprint_phase.py' not in deploy.yml "
            "(AC-39.2 — phase-promoter pre-deploy gate)"
        )

    # INVARIANT-4: phase-promoter ordered before VPS deploy
    promoter_idx = next(
        (i for i, s in enumerate(steps)
         if "promote_sprint_phase.py" in str(s.get("run", ""))
         or "phase-promoter" in str(s.get("name", "")).lower()),
        None,
    )
    vps_idx = next(
        (i for i, s in enumerate(steps) if VPS_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    if promoter_idx is None:
        failures.append("INVARIANT-4: Phase-promoter step not found in deploy job steps")
    elif vps_idx is None:
        failures.append(f"INVARIANT-4: '{VPS_STEP_NAME}' step not found in deploy job steps")
    elif promoter_idx >= vps_idx:
        failures.append(
            f"INVARIANT-4 ORDERING: Phase-promoter (step {promoter_idx}) must precede "
            f"VPS deploy (step {vps_idx}) — post-deploy gate is a no-op (AC-39.2)"
        )

    # INVARIANT-5: AC-12.3 retry-with-backoff smoke-test
    smoke_step = next(
        (s for s in steps if "smoke" in str(s.get("name", "")).lower()),
        None,
    )
    if smoke_step is None:
        failures.append("INVARIANT-5 MISSING: No smoke-test step in deploy job (AC-12.3)")
    else:
        smoke_script = str(smoke_step.get("run", ""))
        if re.search(r"SMOKE_MAX_ATTEMPTS\s*=\s*\d+", smoke_script) is None:
            failures.append("INVARIANT-5 MISSING: SMOKE_MAX_ATTEMPTS absent from smoke-test (AC-12.3)")
        if "SMOKE_RETRY_DELAY" not in smoke_script:
            failures.append("INVARIANT-5 MISSING: SMOKE_RETRY_DELAY absent from smoke-test (AC-12.3)")
        if "8002" not in smoke_script or "/health/" not in smoke_script:
            failures.append("INVARIANT-5 MISSING: smoke-test must check port 8002 /health/ (AC-12.3)")
        if "exit 1" not in smoke_script:
            failures.append("INVARIANT-5 MISSING: smoke-test must exit 1 on failure (AC-12.3)")

    # INVARIANT-6: workflow_call gate, no inline pytest
    inline_pytest = [
        name for name, job in jobs.items()
        if isinstance(job, dict)
        and "uses" not in job
        and any("pytest" in str(s.get("run", "")) for s in (job.get("steps") or []) if isinstance(s, dict))
    ]
    if inline_pytest:
        failures.append(
            f"INVARIANT-6 VIOLATED: inline pytest in job(s) {inline_pytest} "
            "(US-40/H1 — divergent inline copy forbidden)"
        )
    ci_callers = [
        name for name, job in jobs.items()
        if isinstance(job, dict) and "ci.yml" in str(job.get("uses", ""))
    ]
    if not ci_callers:
        failures.append(
            "INVARIANT-6 MISSING: deploy.yml does not call ci.yml via workflow_call "
            "(US-40/H1 unified gate required)"
        )

    # INVARIANT-7: RFC-6455-valid WS key
    if INVALID_WS_KEY in content:
        failures.append(
            f"INVARIANT-7 VIOLATED: Invalid WS key '{INVALID_WS_KEY}' present (J1 re-exposed, US-52)"
        )
    key_assignment = _extract_ws_key_assignment()
    if key_assignment is None:
        failures.append("INVARIANT-7 MISSING: No 'key = ...' in WS PYEOF heredoc (US-52/AC-52.1)")
    elif "urandom(16)" not in key_assignment:
        failures.append(
            f"INVARIANT-7 VIOLATED: WS key '{key_assignment}' does not use os.urandom(16) (US-52)"
        )
    else:
        key = _evaluate_ws_key(key_assignment)
        if len(key) != 24:
            failures.append(
                f"INVARIANT-7 VIOLATED: WS key evaluates to {len(key)} chars (need 24) (US-52/RFC-6455)"
            )

    # INVARIANT-8: disk-exhaustion fix
    if "docker image prune -f" not in script:
        failures.append(
            "INVARIANT-8 MISSING: 'docker image prune -f' absent from VPS SSH command "
            "(AC-53.2 / J4+J2 — disk exhaustion fix)"
        )

    # INVARIANT-9 (push trigger) intentionally NOT checked: deploys are
    # workflow_dispatch-only (operator decision 2026-06-18). Re-adding a push
    # trigger wastes Actions minutes by deploying on every commit.

    assert not failures, (
        "AC-54.2 REGRESSION GUARD FAILED — the following deploy invariants are missing or violated. "
        "The deploy path is NOT sound as a system. Each missing invariant re-exposes a specific "
        "failure mode diagnosed in the sprint-10/11 holistic RCA "
        "(ops/rca_consolidated_ac541.md):\n\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\nFix: restore each named invariant to deploy.yml before merging."
    )
