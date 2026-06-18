# ---
# file: core/tests/test_deploy_live_exercise_ac733.py
# project: solanatrilly
# purpose: AC-73.3 — Structural guards verifying the US-73 Replay viewer overlay API
#          smoke-test step is wired into deploy.yml and will run in the actual VPS
#          deploy pipeline. Verifies the tester confirm record (ops/tester_confirm_ac733.md)
#          exists with required documentation, and that deploy.yml's deploy job has the
#          AC-73.3 replay overlay API smoke step pinned by INVARIANT-14.
# story: US-73 AC-73.3
# sprint: sprint-14
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: .github/workflows/deploy.yml, ops/tester_confirm_ac733.md, pyyaml
# ---
"""AC-73.3 — Live deploy exercise: Replay viewer overlay API smoke-tested in deploy pipeline.

AC text:
  Deployed + smoke-tested on the VPS: the Replay viewer route + its API return HTTP 200
  on 8002 from an actual green deploy run; containers Up; solanaBilly UNTOUCHED on 8001;
  the deploy regression guard GREEN. Verified by the Tester from the green deploy run.
  New backend AND frontend files carry metadata front matter. Zero firehose.

This module verifies:
  A. Tester confirm record exists and documents the required conditions.
  B. deploy.yml deploy job has the US-73 Replay viewer overlay API smoke step (AC-73.3).
  C. INVARIANT-14 guard (in test_deploy_regression_guard_ac712.py) pins the smoke step.
  D. Backend file (trading/replay_api.py) carries metadata front matter.
  E. Frontend file (frontend/src/ReplayViewer.jsx) carries metadata front matter.

Tests:
  Tester confirm record:
    test_tester_confirm_record_exists
    test_tester_confirm_record_documents_replay_api
    test_tester_confirm_record_documents_8002
    test_tester_confirm_record_documents_solanabilly_isolation

  Deploy pipeline wiring:
    test_deploy_yml_has_ac733_replay_overlay_smoke_step
    test_ac733_smoke_step_checks_replay_overlay_endpoint
    test_invariant_14_guard_file_present
    test_deploy_yml_smoke_step_references_ac733

  Metadata front matter:
    test_backend_replay_api_has_metadata_front_matter
    test_frontend_replay_viewer_has_metadata_front_matter
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac733.md"
REGRESSION_GUARD_TEST = REPO_ROOT / "core" / "tests" / "test_deploy_regression_guard_ac712.py"
REPLAY_API = REPO_ROOT / "trading" / "replay_api.py"
REPLAY_VIEWER_JSX = REPO_ROOT / "frontend" / "src" / "ReplayViewer.jsx"

AC733_SMOKE_STEP_NAME = "Smoke-test US-73 Replay viewer overlay API (AC-73.3)"
REPLAY_OVERLAY_ENDPOINT = "/api/trading/replay/overlay/"


# ---------------------------------------------------------------------------
# A. Tester confirm record
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac733.md must exist and contain > 200 chars.

    AC-73.3 is Tester-confirmed from an actual green deploy run. The ops record is the
    durable evidence artifact that documents the green deploy run, the replay overlay API
    smoke step outcome, and all VPS conditions. Without this file there is no traceable
    record that the Tester-confirmed verification was performed.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-73.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac733.md as the durable "
        "evidence artifact for the AC-73.3 live deploy exercise."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), f"AC-73.3: {TESTER_CONFIRM_RECORD} is empty."
    assert len(text) > 200, (
        f"AC-73.3: {TESTER_CONFIRM_RECORD} is too short ({len(text)} chars). "
        "It must document the replay overlay API smoke step, all VPS conditions, and the "
        "deploy pipeline chain."
    )


def test_tester_confirm_record_documents_replay_api() -> None:
    """The record must reference the Replay viewer overlay API endpoint or AC-73.3.

    AC-73.3 condition 1: the Replay viewer overlay API smoke-test step must be
    PRESENT and exit-0 in the deploy run. The record must document this — it is the
    primary condition that proves the US-73 replay overlay route is live on the VPS.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_api = (
        "replay" in text.lower()
        or "AC-73.3" in text
    )
    assert has_api, (
        "AC-73.3: Tester confirmation record must reference the Replay viewer overlay "
        "API ('replay' / 'AC-73.3'). The replay overlay API smoke step "
        "returning HTTP 200 is the primary AC-73.3 condition. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_8002() -> None:
    """The record must reference '8002' — HTTP 200 on the staging stack port.

    AC-73.3 inherits the full VPS DoD: the staging stack must answer HTTP 200 on port 8002.
    The record must document this condition so the full VPS health is traceable.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-73.3: Tester confirmation record must reference '8002' — the solanatrilly "
        "staging stack port. HTTP 200 on port 8002 is a VPS DoD condition required for "
        "every deploy story. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


def test_tester_confirm_record_documents_solanabilly_isolation() -> None:
    """The record must reference solanaBilly isolation on port 8001.

    AC-73.3 inherits the hard isolation requirement: solanaBilly must be UNTOUCHED on
    port 8001 after every solanatrilly deploy. The record must document this.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8001" in text, (
        "AC-73.3: Tester confirmation record must reference '8001' (solanaBilly isolation — "
        "condition: solanaBilly UNTOUCHED on port 8001 after the deploy). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "solanaBilly" in text or "solanabilly" in text.lower(), (
        "AC-73.3: Tester confirmation record must reference 'solanaBilly' (isolation check). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# B. Deploy pipeline wiring — AC-73.3 replay overlay smoke step
# ---------------------------------------------------------------------------


def test_deploy_yml_has_ac733_replay_overlay_smoke_step() -> None:
    """deploy.yml deploy job must have the US-73 Replay viewer overlay API smoke step.

    AC-73.3 requires the replay overlay API to be smoke-tested in the actual deploy
    pipeline. The deploy.yml must include the 'Smoke-test US-73 Replay viewer overlay API
    (AC-73.3)' step so that every VPS deploy verifies the US-73 replay overlay route is live.

    Without this step, a broken /api/trading/replay/overlay/ can reach the VPS undetected
    (J4 failure mode).
    """
    assert DEPLOY_YML.exists(), f"AC-73.3: deploy.yml not found at {DEPLOY_YML}"
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    steps = (data or {}).get("jobs", {}).get("deploy", {}).get("steps") or []
    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    assert any(AC733_SMOKE_STEP_NAME in n for n in step_names), (
        f"AC-73.3: deploy.yml deploy job must have '{AC733_SMOKE_STEP_NAME}' step. "
        "This step smoke-tests the US-73 Replay viewer overlay API at "
        f"{REPLAY_OVERLAY_ENDPOINT} on every VPS deploy. Its absence means a broken replay "
        "overlay route can reach the VPS undetected. "
        f"deploy.yml: {DEPLOY_YML}"
    )


def test_ac733_smoke_step_checks_replay_overlay_endpoint() -> None:
    """The AC-73.3 smoke step must check the /api/trading/replay/overlay/ endpoint.

    The smoke step must verify the correct endpoint — not just be present with an empty run.
    It must curl /api/trading/replay/overlay/ and expect HTTP 200.
    """
    assert DEPLOY_YML.exists(), f"AC-73.3: deploy.yml not found at {DEPLOY_YML}"
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    data = yaml.safe_load(deploy_text)
    steps = (data or {}).get("jobs", {}).get("deploy", {}).get("steps") or []
    smoke_step = next(
        (s for s in steps if isinstance(s, dict) and AC733_SMOKE_STEP_NAME in str(s.get("name", ""))),
        None,
    )
    assert smoke_step is not None, (
        f"AC-73.3: '{AC733_SMOKE_STEP_NAME}' step not found in deploy.yml deploy job. "
        f"deploy.yml: {DEPLOY_YML}"
    )
    run_script = str(smoke_step.get("run", ""))
    assert REPLAY_OVERLAY_ENDPOINT in run_script, (
        f"AC-73.3: '{AC733_SMOKE_STEP_NAME}' step found but its 'run:' does not check "
        f"'{REPLAY_OVERLAY_ENDPOINT}'. The step must curl this endpoint and fail loudly if it "
        f"does not return HTTP 200. Got run: {run_script[:200]!r}"
    )


def test_invariant_14_guard_file_present() -> None:
    """core/tests/test_deploy_regression_guard_ac712.py must exist with INVARIANT-14.

    AC-73.3 pins its deploy smoke step via INVARIANT-14 in the extended regression guard.
    The guard file must be present AND contain INVARIANT-14 so that removing the AC-73.3
    smoke step from deploy.yml immediately breaks CI (H1 pattern — fail loudly).
    """
    assert REGRESSION_GUARD_TEST.exists(), (
        f"AC-73.3: Extended regression guard test not found at {REGRESSION_GUARD_TEST}. "
        "This file pins INVARIANT-14 (US-73 replay overlay API smoke step). Its absence means "
        "the smoke step can be silently removed without a CI failure."
    )
    guard_text = REGRESSION_GUARD_TEST.read_text(encoding="utf-8")
    assert "INVARIANT-14" in guard_text, (
        f"AC-73.3: {REGRESSION_GUARD_TEST} does not contain 'INVARIANT-14'. "
        "INVARIANT-14 must be added to the extended regression guard to pin the "
        f"'{AC733_SMOKE_STEP_NAME}' deploy step. Without this, the smoke step can be "
        "silently removed between a green deliberate run and a later per-merge run "
        "(J4 failure mode)."
    )


def test_deploy_yml_smoke_step_references_ac733() -> None:
    """deploy.yml must reference 'AC-73.3' in the replay overlay smoke step name.

    The step name must include 'AC-73.3' so it is traceable in the run log, in the
    regression guard (INVARIANT-14), and in the tester confirm record.
    """
    assert DEPLOY_YML.exists(), f"AC-73.3: deploy.yml not found at {DEPLOY_YML}"
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    assert "AC-73.3" in deploy_text, (
        "AC-73.3: deploy.yml must reference 'AC-73.3' (the Replay viewer overlay API "
        "smoke step). This makes the smoke step traceable in the run log and in the "
        f"INVARIANT-14 regression guard. deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# C. Metadata front matter — backend and frontend files
# ---------------------------------------------------------------------------


def test_backend_replay_api_has_metadata_front_matter() -> None:
    """trading/replay_api.py must have a structured metadata front matter header.

    Project convention (CLAUDE.md): all new backend source files must include structured
    front matter / metadata header comments. The replay overlay API (AC-73.1) is a new
    backend file and must carry the front matter.
    """
    assert REPLAY_API.exists(), (
        f"AC-73.3: trading/replay_api.py not found at {REPLAY_API}. "
        "This is the AC-73.1 replay overlay backend file."
    )
    text = REPLAY_API.read_text(encoding="utf-8")
    has_front_matter = (
        "# ---" in text
        and "module:" in text
        and "sprint:" in text
        and "story:" in text
    )
    assert has_front_matter, (
        f"AC-73.3: trading/replay_api.py must have structured metadata front matter "
        "(contains '# ---', 'module:', 'sprint:', 'story:'). "
        "Project convention (CLAUDE.md): all new backend source files must include "
        "metadata header comments. "
        f"File: {REPLAY_API}"
    )


def test_frontend_replay_viewer_has_metadata_front_matter() -> None:
    """frontend/src/ReplayViewer.jsx must have a structured metadata front matter header.

    Project convention (CLAUDE.md): all new frontend source files must include structured
    front matter / metadata header comments. ReplayViewer.jsx (AC-73.2) is a new frontend
    file and must carry the front matter.
    """
    assert REPLAY_VIEWER_JSX.exists(), (
        f"AC-73.3: frontend/src/ReplayViewer.jsx not found at {REPLAY_VIEWER_JSX}. "
        "This is the AC-73.2 frontend React component."
    )
    text = REPLAY_VIEWER_JSX.read_text(encoding="utf-8")
    has_front_matter = (
        "// ---" in text
        and "file:" in text
        and "sprint:" in text
        and "story:" in text
    )
    assert has_front_matter, (
        f"AC-73.3: frontend/src/ReplayViewer.jsx must have structured metadata front matter "
        "(contains '// ---', 'file:', 'sprint:', 'story:'). "
        "Project convention (CLAUDE.md): all new frontend source files must include "
        "metadata header comments. "
        f"File: {REPLAY_VIEWER_JSX}"
    )
