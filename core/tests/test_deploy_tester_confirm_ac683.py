# ---
# file: core/tests/test_deploy_tester_confirm_ac683.py
# project: solanatrilly
# purpose: AC-68.3 — Structural guards verifying the shared P8 execution apparatus is deployed
#          to the VPS solanatrilly staging stack: shared apparatus imports in-container,
#          copytrade_engine + listener/web/celery-worker/frontend Up, solanaBilly UNTOUCHED
#          on 8001, nine-invariant deploy guard green.
# story: US-68 AC-68.3
# sprint: sprint-13
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-68.3 — Structural guards for the P8 shared apparatus closeout green deploy run.

AC text:
  Deployed to the VPS solanatrilly staging stack and Tester-confirmed from an actual
  green deploy run (HTTP 200 on 8002, the shared apparatus imports in-container,
  copytrade_engine + listener/web/celery-worker/frontend Up, solanaBilly UNTOUCHED
  on 8001; nine-invariant deploy guard green). Zero firehose. New files carry metadata
  front matter.

VPS conditions confirmed by Tester from the actual green deploy run:
  1. HTTP 200 on port 8002 (/health/) with AC-12.3 retry-with-backoff.
  2. Shared apparatus imports in-container: ExecutionCore, simulate_tape_exit,
     evaluate_exit_rules, settle_paper_position, open_live_position all importable in
     the live web container with no ImportError.
  3. copytrade_engine container Up in the solanatrilly stack.
  4. listener container Up in the solanatrilly stack.
  5. web container Up in the solanatrilly stack.
  6. celery-worker container Up in the solanatrilly stack.
  7. frontend container Up in the solanatrilly stack.
  8. solanaBilly UNTOUCHED on port 8001 (every command scoped -p solanatrilly).
  9. Nine-invariant deploy guard (AC-54.2) remains green in CI.

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac683.md exists and has content > 200 chars.
  test_tester_confirm_record_documents_http200
      Record references '8002' (condition 1: HTTP 200 on port 8002).
  test_tester_confirm_record_documents_shared_apparatus
      Record references 'shared apparatus' or 'trading' and the key module names
      (condition 2: shared apparatus imports in-container).
  test_tester_confirm_record_documents_containers_up
      Record references copytrade_engine, listener, web, celery, frontend
      (conditions 3-7: all five containers Up).
  test_tester_confirm_record_documents_solanabilly_isolation
      Record references '8001' and 'solanaBilly' (condition 8: solanaBilly UNTOUCHED).
  test_tester_confirm_record_documents_nine_invariant_guard
      Record references 'AC-54.2' or 'nine-invariant' (condition 9).
  test_shared_apparatus_import_step_in_deploy_yml
      deploy.yml has the 'Verify shared apparatus imports in-container (AC-68.3)' step
      that SSHes to VPS and runs the in-container Python import check.
  test_shared_apparatus_step_checks_all_required_imports
      The shared apparatus step in deploy.yml references all five required symbols:
      ExecutionCore, simulate_tape_exit, evaluate_exit_rules, settle_paper_position,
      open_live_position.
  test_copytrade_engine_container_verified_in_deploy_yml
      deploy.yml has the step that verifies the copytrade_engine container is Up.
  test_nine_invariant_guard_test_file_exists
      core/tests/test_deploy_regression_guard_ac542.py exists (AC-54.2 guard is present
      and will pass in CI — a missing file would indicate the guard was deleted).
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac683.md"
NINE_INVARIANT_GUARD = REPO_ROOT / "core" / "tests" / "test_deploy_regression_guard_ac542.py"
SHARED_APPARATUS_STEP_NAME = "Verify shared apparatus imports in-container (AC-68.3)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-68.3: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-68.3: deploy.yml must be valid YAML"
    return data


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# 1. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac683.md must exist and have content > 200 chars.

    AC-68.3 is a Tester-CONFIRM step. The ops record is the durable evidence artifact
    that documents the green deploy run, the shared apparatus import confirmation, all
    VPS conditions, and the nine-invariant guard. Without this file there is no traceable
    record that the Tester-CONFIRM was performed.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-68.3: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac683.md as the durable "
        "evidence artifact for the P8 shared apparatus deploy confirmation."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-68.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-68.3: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document the shared apparatus imports, all "
        "container conditions, and the nine-invariant guard status."
    )


# ---------------------------------------------------------------------------
# 2. Tester confirm record documents HTTP 200 condition
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_http200() -> None:
    """The confirmation record must reference '8002' (condition 1: HTTP 200 on port 8002).

    AC-68.3 VPS condition 1: the staging stack answers HTTP 200 on port 8002 at /health/
    with the AC-12.3 retry-with-backoff. A record that omits this is not a complete
    AC-68.3 evidence artifact.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-68.3: Tester confirmation record must reference '8002' (condition 1: "
        "HTTP 200 on port 8002 at /health/). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 3. Tester confirm record documents shared apparatus imports
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_shared_apparatus() -> None:
    """The confirmation record must reference the shared apparatus import check.

    AC-68.3 condition 2: the shared trading apparatus (ExecutionCore, simulate_tape_exit,
    evaluate_exit_rules, settle_paper_position, open_live_position) imports correctly in the
    live web container with no ImportError. This is the P8 parity proof: the same code the
    prediction pipeline + copy-trade now delegate to is importable end-to-end in-container.

    The record must reference 'shared apparatus' or 'trading' + at least one of the five
    key symbols to confirm the in-container import check was performed.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_shared = "shared apparatus" in text.lower() or "trading" in text.lower()
    assert has_shared, (
        "AC-68.3: Tester confirmation record must reference 'shared apparatus' or 'trading' "
        "(condition 2: shared P8 apparatus imports in-container). The record must document "
        "that the trading module's key symbols import without error in the live web container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    has_symbol = any(
        sym in text
        for sym in (
            "ExecutionCore",
            "simulate_tape_exit",
            "evaluate_exit_rules",
            "settle_paper_position",
            "open_live_position",
        )
    )
    assert has_symbol, (
        "AC-68.3: Tester confirmation record must reference at least one shared apparatus "
        "symbol (ExecutionCore, simulate_tape_exit, evaluate_exit_rules, settle_paper_position, "
        "open_live_position) confirming the in-container import was verified. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Tester confirm record documents all five containers Up
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_containers_up() -> None:
    """The confirmation record must reference all five containers (conditions 3-7).

    AC-68.3 VPS conditions 3-7: copytrade_engine, listener, web, celery-worker, and frontend
    containers must all be Up in the solanatrilly stack. Partial confirmation leaves gaps in
    the AC-68.3 evidence.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "copytrade" in text.lower(), (
        "AC-68.3: Tester confirmation record must reference 'copytrade' (condition 3: "
        "copytrade_engine container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-68.3: Tester confirmation record must reference 'listener' (condition 4: "
        "listener container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "web" in text.lower(), (
        "AC-68.3: Tester confirmation record must reference 'web' (condition 5: "
        "web container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-68.3: Tester confirmation record must reference 'celery' (condition 6: "
        "celery-worker container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "frontend" in text.lower(), (
        "AC-68.3: Tester confirmation record must reference 'frontend' (condition 7: "
        "frontend container Up). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 5. Tester confirm record documents solanaBilly isolation
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_solanabilly_isolation() -> None:
    """The confirmation record must reference '8001' and 'solanaBilly' (condition 8).

    AC-68.3 VPS condition 8: solanaBilly must be UNTOUCHED on port 8001 after the
    P8 shared apparatus deploy (every command scoped -p solanatrilly).
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8001" in text, (
        "AC-68.3: Tester confirmation record must reference '8001' (condition 8: "
        "solanaBilly UNTOUCHED on port 8001). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "solanabilly" in text.lower() or "solanaBilly" in text, (
        "AC-68.3: Tester confirmation record must reference 'solanaBilly' (condition 8: "
        "hard isolation preserved). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Tester confirm record documents nine-invariant guard
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_nine_invariant_guard() -> None:
    """The confirmation record must reference 'AC-54.2' or 'nine-invariant' (condition 9).

    AC-68.3 condition 9: the nine-invariant deploy guard (AC-54.2) must remain green.
    The guard pins all load-bearing deploy invariants; its presence in the record confirms
    the operator knows the guard is in force and its CI status was checked.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    has_guard_ref = "AC-54.2" in text or "nine-invariant" in text.lower()
    assert has_guard_ref, (
        "AC-68.3: Tester confirmation record must reference 'AC-54.2' or 'nine-invariant' "
        "(condition 9: nine-invariant deploy guard green). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 7. Shared apparatus import step present in deploy.yml
# ---------------------------------------------------------------------------


def test_shared_apparatus_import_step_in_deploy_yml() -> None:
    """deploy.yml must have the 'Verify shared apparatus imports in-container (AC-68.3)' step.

    AC-68.3 requires the deploy pipeline to actively verify the shared trading apparatus
    imports in-container. Without this step a green deploy run does not confirm condition 2
    — it merely confirms the containers started, not that the trading module is importable.

    The step must SSH to the VPS and run a docker compose exec command against the web
    container to perform the Python import check.
    """
    content = _deploy_text()
    assert SHARED_APPARATUS_STEP_NAME in content, (
        f"AC-68.3: deploy.yml must have a step named '{SHARED_APPARATUS_STEP_NAME}'. "
        "This step verifies the shared trading apparatus (ExecutionCore, simulate_tape_exit, "
        "evaluate_exit_rules, settle_paper_position, open_live_position) imports in the live "
        "web container. Without this step a green deploy run does not confirm condition 2. "
        f"deploy.yml: {DEPLOY_YML}"
    )
    assert "docker compose" in content and "exec" in content, (
        "AC-68.3: The shared apparatus import step in deploy.yml must use 'docker compose exec' "
        "to run the import check in the live web container (not a standalone python -c on the "
        "runner — that checks the host environment, not the container). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 8. Shared apparatus step checks all five required symbols
# ---------------------------------------------------------------------------


def test_shared_apparatus_step_checks_all_required_imports() -> None:
    """The shared apparatus step in deploy.yml must check all five required symbols.

    AC-68.3 condition 2 requires all five symbols to be importable:
      - ExecutionCore (trading.execution_core — the gated buy/sell boundary)
      - simulate_tape_exit (trading.tape_settler — the sole paper/observe settler)
      - evaluate_exit_rules (trading.exit_engine — the §10.1 priority ladder)
      - settle_paper_position (trading.position_closer — bridges settler to Position model)
      - open_live_position (copytrade.position_opener — the Live toggle wired path)

    Checking fewer than five symbols leaves the shared apparatus partially unverified.
    A failing import for any of these five indicates a broken shared execution path.
    """
    content = _deploy_text()
    required_symbols = [
        "ExecutionCore",
        "simulate_tape_exit",
        "evaluate_exit_rules",
        "settle_paper_position",
        "open_live_position",
    ]
    for sym in required_symbols:
        assert sym in content, (
            f"AC-68.3: deploy.yml shared apparatus step must check '{sym}' (one of the five "
            "required shared apparatus symbols). A missing symbol means the in-container "
            "import check does not fully verify the P8 execution path. "
            f"deploy.yml: {DEPLOY_YML}"
        )


# ---------------------------------------------------------------------------
# 9. copytrade_engine container verified in deploy.yml
# ---------------------------------------------------------------------------


def test_copytrade_engine_container_verified_in_deploy_yml() -> None:
    """deploy.yml must have a step verifying the copytrade_engine container is Up.

    AC-68.3 condition 3: the copytrade_engine container must be Up. This was established
    in AC-59.3. This test guards that the AC-59.3 step is still present after the P8
    shared apparatus changes — it cannot be silently removed without breaking AC-68.3.
    """
    content = _deploy_text()
    assert (
        "solanatrilly.copytrade.engine" in content
        or "copytrade_engine" in content
        or "copytrade-engine" in content
    ), (
        "AC-68.3: deploy.yml must verify the 'copytrade_engine' container is Up (condition 3). "
        "The AC-59.3 container Up check must remain present after P8 shared apparatus changes. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 10. Nine-invariant guard test file exists
# ---------------------------------------------------------------------------


def test_nine_invariant_guard_test_file_exists() -> None:
    """core/tests/test_deploy_regression_guard_ac542.py must exist.

    AC-68.3 condition 9: the nine-invariant deploy guard (AC-54.2) must remain green.
    The guard test file must be present in the repo — its absence would mean the guard
    was deleted, which is equivalent to a silent guard failure. A present file that
    passes CI is the only way to confirm condition 9.
    """
    assert NINE_INVARIANT_GUARD.exists(), (
        f"AC-68.3: Nine-invariant guard test file not found at {NINE_INVARIANT_GUARD}. "
        "This file (AC-54.2) pins all nine load-bearing deploy invariants. Its absence "
        "means the guard has been deleted — condition 9 (nine-invariant guard green) "
        "cannot be confirmed without this file present and passing in CI."
    )
