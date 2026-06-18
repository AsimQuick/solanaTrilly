# ---
# file: core/tests/test_deploy_tester_confirm_ac693.py
# project: solanatrilly
# purpose: AC-69.3 — Structural guards verifying the US-69 Live Positions dashboard board
#          is deployed to the VPS solanatrilly staging stack: Live Positions open+closed
#          API return HTTP 200, containers Up, solanaBilly UNTOUCHED on 8001,
#          nine-invariant deploy guard green.
# story: US-69 AC-69.3
# sprint: sprint-13
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-69.3 — Structural guards for the US-69 Live Positions board closeout green deploy run.

AC text:
  Deployed + smoke-tested on the VPS (the Live Positions route + its API return 200;
  containers Up; solanaBilly untouched on 8001). Zero firehose. New backend AND
  frontend files carry metadata front matter.

VPS conditions confirmed by Tester from the actual green deploy run:
  1. HTTP 200 on port 8002 (/health/) with AC-12.3 retry-with-backoff.
  2. GET /api/trading/positions/open/ returns HTTP 200.
  3. GET /api/trading/positions/closed/ returns HTTP 200.
  4. copytrade_engine container Up in the solanatrilly stack.
  5. listener container Up in the solanatrilly stack.
  6. web container Up in the solanatrilly stack.
  7. celery-worker container Up in the solanatrilly stack.
  8. frontend container Up in the solanatrilly stack.
  9. solanaBilly UNTOUCHED on port 8001 (every command scoped -p solanatrilly).
  10. Nine-invariant deploy guard (AC-54.2) remains green in CI.

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac693.md exists and has content > 200 chars.
  test_tester_confirm_record_documents_http200
      Record references '8002' (condition 1: HTTP 200 on port 8002).
  test_tester_confirm_record_documents_live_positions_open_api
      Record references '/api/trading/positions/open/' (condition 2).
  test_tester_confirm_record_documents_live_positions_closed_api
      Record references '/api/trading/positions/closed/' (condition 3).
  test_tester_confirm_record_documents_containers_up
      Record references copytrade, listener, web, celery, frontend (conditions 4-8).
  test_tester_confirm_record_documents_solanabilly_isolation
      Record references '8001' and 'solanaBilly' (condition 9).
  test_tester_confirm_record_documents_nine_invariant_guard
      Record references 'AC-54.2' or 'nine-invariant' (condition 10).
  test_live_positions_open_api_step_in_deploy_yml
      deploy.yml has the 'Smoke-test US-69 Live Positions open API (AC-69.3)' step.
  test_live_positions_closed_api_step_in_deploy_yml
      deploy.yml has the 'Smoke-test US-69 Live Positions closed API (AC-69.3)' step.
  test_live_positions_open_step_checks_correct_url
      The open API step in deploy.yml references /api/trading/positions/open/.
  test_live_positions_closed_step_checks_correct_url
      The closed API step in deploy.yml references /api/trading/positions/closed/.
  test_metadata_front_matter_backend_api
      trading/api.py carries metadata front matter (# --- header).
  test_metadata_front_matter_backend_urls
      trading/urls.py carries metadata front matter (# --- header).
  test_metadata_front_matter_frontend_live_positions
      frontend/src/LivePositions.jsx carries metadata front matter (// --- header).
  test_nine_invariant_guard_test_file_exists
      core/tests/test_deploy_regression_guard_ac542.py exists.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac693.md"
NINE_INVARIANT_GUARD = REPO_ROOT / "core" / "tests" / "test_deploy_regression_guard_ac542.py"
OPEN_STEP_NAME = "Smoke-test US-69 Live Positions open API (AC-69.3)"
CLOSED_STEP_NAME = "Smoke-test US-69 Live Positions closed API (AC-69.3)"

BACKEND_API = REPO_ROOT / "trading" / "api.py"
BACKEND_URLS = REPO_ROOT / "trading" / "urls.py"
FRONTEND_LIVE_POSITIONS = REPO_ROOT / "frontend" / "src" / "LivePositions.jsx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-69.3: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-69.3: deploy.yml must be valid YAML"
    return data


def _deploy_job_steps() -> list:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# 1. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac693.md must exist and have content > 200 chars.

    AC-69.3 is a Tester-CONFIRM step. The ops record is the durable evidence artifact
    that documents the green deploy run, the Live Positions API HTTP 200 confirmation,
    all VPS container conditions, and the nine-invariant guard.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-69.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac693.md as the durable "
        "evidence artifact for the US-69 Live Positions board deploy confirmation."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-69.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-69.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document the Live Positions API conditions, "
        "all container conditions, and the nine-invariant guard status."
    )


# ---------------------------------------------------------------------------
# 2. Tester confirm record documents HTTP 200 condition
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_http200() -> None:
    """The confirmation record must reference '8002' (condition 1: HTTP 200 on port 8002)."""
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-69.3: Tester confirmation record must reference '8002' (condition 1: "
        "HTTP 200 on port 8002 at /health/). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 3. Tester confirm record documents Live Positions open API
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_live_positions_open_api() -> None:
    """The confirmation record must reference the open positions API endpoint (condition 2).

    AC-69.3 condition 2: GET /api/trading/positions/open/ must return HTTP 200.
    This is the primary AC-69.3 deliverable — the Live Positions route returning 200.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_open_api = (
        "/api/trading/positions/open/" in text
        or "positions/open" in text
        or "positions_open" in text
    )
    assert has_open_api, (
        "AC-69.3: Tester confirmation record must reference "
        "'/api/trading/positions/open/' (condition 2: Live Positions open API HTTP 200). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Tester confirm record documents Live Positions closed API
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_live_positions_closed_api() -> None:
    """The confirmation record must reference the closed positions API endpoint (condition 3).

    AC-69.3 condition 3: GET /api/trading/positions/closed/ must return HTTP 200.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_closed_api = (
        "/api/trading/positions/closed/" in text
        or "positions/closed" in text
        or "positions_closed" in text
    )
    assert has_closed_api, (
        "AC-69.3: Tester confirmation record must reference "
        "'/api/trading/positions/closed/' (condition 3: Live Positions closed API HTTP 200). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 5. Tester confirm record documents all containers Up
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_containers_up() -> None:
    """The confirmation record must reference all five containers (conditions 4-8).

    AC-69.3 VPS conditions 4-8: copytrade_engine, listener, web, celery-worker, and
    frontend containers must all be Up in the solanatrilly stack.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "copytrade" in text.lower(), (
        "AC-69.3: Tester confirmation record must reference 'copytrade' (condition 4: "
        f"copytrade_engine container Up). Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-69.3: Tester confirmation record must reference 'listener' (condition 5: "
        f"listener container Up). Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "web" in text.lower(), (
        "AC-69.3: Tester confirmation record must reference 'web' (condition 6: "
        f"web container Up). Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-69.3: Tester confirmation record must reference 'celery' (condition 7: "
        f"celery-worker container Up). Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "frontend" in text.lower(), (
        "AC-69.3: Tester confirmation record must reference 'frontend' (condition 8: "
        f"frontend container Up). Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Tester confirm record documents solanaBilly isolation
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_solanabilly_isolation() -> None:
    """The confirmation record must reference '8001' and 'solanaBilly' (condition 9).

    AC-69.3 VPS condition 9: solanaBilly must be UNTOUCHED on port 8001 after the
    US-69 Live Positions deploy (every command scoped -p solanatrilly).
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8001" in text, (
        "AC-69.3: Tester confirmation record must reference '8001' (condition 9: "
        f"solanaBilly UNTOUCHED on port 8001). Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "solanabilly" in text.lower() or "solanaBilly" in text, (
        "AC-69.3: Tester confirmation record must reference 'solanaBilly' (condition 9: "
        f"hard isolation preserved). Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 7. Tester confirm record documents nine-invariant guard
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_nine_invariant_guard() -> None:
    """The confirmation record must reference 'AC-54.2' or 'nine-invariant' (condition 10)."""
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_guard_ref = "AC-54.2" in text or "nine-invariant" in text.lower()
    assert has_guard_ref, (
        "AC-69.3: Tester confirmation record must reference 'AC-54.2' or 'nine-invariant' "
        "(condition 10: nine-invariant deploy guard green). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 8. Live Positions open API step in deploy.yml
# ---------------------------------------------------------------------------


def test_live_positions_open_api_step_in_deploy_yml() -> None:
    """deploy.yml must have the 'Smoke-test US-69 Live Positions open API (AC-69.3)' step.

    AC-69.3 requires the deploy pipeline to actively smoke-test the open positions endpoint.
    Without this step a green deploy run does not confirm condition 2.
    """
    content = _deploy_text()
    assert OPEN_STEP_NAME in content, (
        f"AC-69.3: deploy.yml must have a step named '{OPEN_STEP_NAME}'. "
        "This step smoke-tests GET /api/trading/positions/open/ for HTTP 200. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 9. Live Positions closed API step in deploy.yml
# ---------------------------------------------------------------------------


def test_live_positions_closed_api_step_in_deploy_yml() -> None:
    """deploy.yml must have the 'Smoke-test US-69 Live Positions closed API (AC-69.3)' step.

    AC-69.3 requires the deploy pipeline to actively smoke-test the closed positions endpoint.
    Without this step a green deploy run does not confirm condition 3.
    """
    content = _deploy_text()
    assert CLOSED_STEP_NAME in content, (
        f"AC-69.3: deploy.yml must have a step named '{CLOSED_STEP_NAME}'. "
        "This step smoke-tests GET /api/trading/positions/closed/ for HTTP 200. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 10. Live Positions open step checks correct URL
# ---------------------------------------------------------------------------


def test_live_positions_open_step_checks_correct_url() -> None:
    """The open API step in deploy.yml must reference /api/trading/positions/open/."""
    content = _deploy_text()
    assert "/api/trading/positions/open/" in content, (
        "AC-69.3: deploy.yml must reference '/api/trading/positions/open/' in the "
        "Live Positions open API smoke-test step (condition 2). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 11. Live Positions closed step checks correct URL
# ---------------------------------------------------------------------------


def test_live_positions_closed_step_checks_correct_url() -> None:
    """The closed API step in deploy.yml must reference /api/trading/positions/closed/."""
    content = _deploy_text()
    assert "/api/trading/positions/closed/" in content, (
        "AC-69.3: deploy.yml must reference '/api/trading/positions/closed/' in the "
        "Live Positions closed API smoke-test step (condition 3). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 12. Backend api.py has metadata front matter
# ---------------------------------------------------------------------------


def test_metadata_front_matter_backend_api() -> None:
    """trading/api.py must carry a metadata front matter header (# --- block).

    AC-69.3: 'New backend AND frontend files carry metadata front matter.'
    trading/api.py is the primary AC-69.2 backend file.
    """
    assert BACKEND_API.exists(), (
        f"AC-69.3: trading/api.py not found at {BACKEND_API}."
    )
    text = BACKEND_API.read_text(encoding="utf-8")
    assert text.startswith("# ---"), (
        "AC-69.3: trading/api.py must start with a '# ---' metadata front matter header "
        "(project convention: all code files include structured front matter). "
        f"File: {BACKEND_API}"
    )


# ---------------------------------------------------------------------------
# 13. Backend urls.py has metadata front matter
# ---------------------------------------------------------------------------


def test_metadata_front_matter_backend_urls() -> None:
    """trading/urls.py must carry a metadata front matter header (# --- block).

    AC-69.3: 'New backend AND frontend files carry metadata front matter.'
    trading/urls.py is the AC-69.2 URL routing file.
    """
    assert BACKEND_URLS.exists(), (
        f"AC-69.3: trading/urls.py not found at {BACKEND_URLS}."
    )
    text = BACKEND_URLS.read_text(encoding="utf-8")
    assert text.startswith("# ---"), (
        "AC-69.3: trading/urls.py must start with a '# ---' metadata front matter header "
        "(project convention: all code files include structured front matter). "
        f"File: {BACKEND_URLS}"
    )


# ---------------------------------------------------------------------------
# 14. Frontend LivePositions.jsx has metadata front matter
# ---------------------------------------------------------------------------


def test_metadata_front_matter_frontend_live_positions() -> None:
    """frontend/src/LivePositions.jsx must carry a metadata front matter header (// --- block).

    AC-69.3: 'New backend AND frontend files carry metadata front matter.'
    LivePositions.jsx is the primary AC-69.2 frontend file.
    """
    assert FRONTEND_LIVE_POSITIONS.exists(), (
        f"AC-69.3: frontend/src/LivePositions.jsx not found at {FRONTEND_LIVE_POSITIONS}."
    )
    text = FRONTEND_LIVE_POSITIONS.read_text(encoding="utf-8")
    assert text.startswith("// ---"), (
        "AC-69.3: frontend/src/LivePositions.jsx must start with a '// ---' metadata "
        "front matter header (project convention: all code files include structured front "
        f"matter). File: {FRONTEND_LIVE_POSITIONS}"
    )


# ---------------------------------------------------------------------------
# 15. Nine-invariant guard test file exists
# ---------------------------------------------------------------------------


def test_nine_invariant_guard_test_file_exists() -> None:
    """core/tests/test_deploy_regression_guard_ac542.py must exist.

    AC-69.3 condition 10: the nine-invariant deploy guard (AC-54.2) must remain green.
    The guard test file must be present — its absence would mean the guard was deleted.
    """
    assert NINE_INVARIANT_GUARD.exists(), (
        f"AC-69.3: Nine-invariant guard test file not found at {NINE_INVARIANT_GUARD}. "
        "This file (AC-54.2) pins all nine load-bearing deploy invariants. Its absence "
        "means the guard has been deleted — condition 10 cannot be confirmed."
    )
