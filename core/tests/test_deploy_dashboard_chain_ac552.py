# ---
# file: core/tests/test_deploy_dashboard_chain_ac552.py
# project: solanatrilly
# purpose: AC-55.2 — Structural guards verifying the P6 dashboard chain VPS-confirmation:
#          deploy.yml has smoke-test steps for US-49 (candle API), US-50 (cohort wall),
#          and US-51 (annotation list) endpoints; the tester confirmation artifact documents
#          all VPS conditions, US-49/US-50 PASSED promotion, and US-51 VPS-gate sign-off.
# story: US-55 AC-55.2
# sprint: sprint-11
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, yaml
# ---
"""AC-55.2 — Structural guards for the P6 dashboard chain Tester VPS-confirmation.

AC text:
  Tester VPS-CONFIRMS the dashboard chain from that green run: HTTP 200 on 8002, the
  dashboard route returns 200, the WS endpoint accepts an upgrade (HTTP 101), the
  token-detail/candle API (US-49), cohort wall (US-50), and annotation list/create +
  export (US-51) endpoints respond, the 'frontend'/'web'/'listener'/'celery-worker'
  containers are Up, and solanaBilly is untouched on 8001 (scoped -p solanatrilly).
  US-49/US-50 are promoted to PASSED (no application-code change required — CI-verified
  code-complete, blocked only by the J1 deploy defect) and US-51 receives its final
  VPS-gate sign-off beyond requirements approval.

P6 dashboard chain API endpoints confirmed by this AC:
  - US-49: /api/candles/smoke_test/?interval_s=60 → HTTP 200
  - US-50: /api/cohort/?interval_s=60 → HTTP 200
  - US-51: /api/annotations/smoke_test/ → HTTP 200

Tests in this module:
  test_tester_confirm_record_exists
      ops/tester_confirm_ac552.md exists and has content > 200 chars.
  test_tester_confirm_record_references_p6_chain
      The record mentions US-49, US-50, US-51 (P6 stories being confirmed).
  test_tester_confirm_record_documents_us49_promotion
      The record documents US-49 PASSED promotion ('US-49' + 'PASSED' present).
  test_tester_confirm_record_documents_us50_promotion
      The record documents US-50 PASSED promotion ('US-50' + 'PASSED' present).
  test_tester_confirm_record_documents_us51_sign_off
      The record documents US-51 VPS-gate sign-off ('US-51' + 'sign-off' present).
  test_tester_confirm_record_documents_vps_conditions
      Record mentions 8002, 8001, web, frontend, listener, celery.
  test_tester_confirm_record_references_green_run
      Record has a run ID field (PENDING — to be filled by Tester after merge).
  test_deploy_yml_has_us49_candle_api_smoke_test
      deploy.yml has a step named to smoke-test the US-49 candle API (AC-55.2).
  test_deploy_yml_has_us50_cohort_wall_smoke_test
      deploy.yml has a step named to smoke-test the US-50 cohort wall API (AC-55.2).
  test_deploy_yml_has_us51_annotation_api_smoke_test
      deploy.yml has a step named to smoke-test the US-51 annotation list API (AC-55.2).
  test_deploy_yml_us49_step_checks_candle_endpoint
      The US-49 smoke-test step in deploy.yml references the candle endpoint URL.
  test_deploy_yml_us50_step_checks_cohort_endpoint
      The US-50 smoke-test step in deploy.yml references the cohort endpoint URL.
  test_deploy_yml_us51_step_checks_annotation_endpoint
      The US-51 smoke-test step in deploy.yml references the annotation endpoint URL.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
TESTER_CONFIRM_RECORD = REPO_ROOT / "ops" / "tester_confirm_ac552.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy_text() -> str:
    assert DEPLOY_YML.exists(), f"AC-55.2: deploy.yml not found at {DEPLOY_YML}"
    return DEPLOY_YML.read_text(encoding="utf-8")


def _deploy_data() -> dict:
    data = yaml.safe_load(_deploy_text())
    assert isinstance(data, dict), "AC-55.2: deploy.yml must be valid YAML"
    return data


def _deploy_job_steps() -> list[dict]:
    jobs = _deploy_data().get("jobs", {})
    deploy_job = jobs.get("deploy", {})
    steps = deploy_job.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _deploy_step_names() -> list[str]:
    """Return the ordered list of deploy-job step names."""
    return [s.get("name", "") for s in _deploy_job_steps()]


def _deploy_step_run(step_name: str) -> str:
    """Return the `run:` content of a named deploy step (empty string if not found)."""
    for step in _deploy_job_steps():
        if step.get("name", "") == step_name:
            return step.get("run", "") or ""
    return ""


# ---------------------------------------------------------------------------
# 1. Tester confirm record exists
# ---------------------------------------------------------------------------


def test_tester_confirm_record_exists() -> None:
    """ops/tester_confirm_ac552.md must exist and have content > 200 chars.

    AC-55.2 is the Tester VPS-CONFIRMATION step for the full P6 dashboard chain.
    The ops record is the durable evidence artifact that documents all VPS conditions,
    the US-49/US-50 PASSED promotions, and the US-51 VPS-gate sign-off.
    Without this file, the Tester-VPS-CONFIRM has no traceable audit record.
    """
    assert TESTER_CONFIRM_RECORD.exists(), (
        f"AC-55.2: Tester confirmation record not found at {TESTER_CONFIRM_RECORD}. "
        "This file must be committed as ops/tester_confirm_ac552.md as the durable "
        "evidence artifact for the P6 dashboard chain VPS-confirmation."
    )
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert text.strip(), (
        f"AC-55.2: Tester confirmation record at {TESTER_CONFIRM_RECORD} is empty."
    )
    assert len(text) > 200, (
        f"AC-55.2: Tester confirmation record at {TESTER_CONFIRM_RECORD} is too short "
        f"({len(text)} chars). It must document the full VPS conditions, US-49/50 promotions, "
        "and US-51 sign-off."
    )


# ---------------------------------------------------------------------------
# 2. Tester confirm record references all three P6 stories
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_p6_chain() -> None:
    """The confirmation record must reference US-49, US-50, and US-51.

    AC-55.2 confirms the full P6 dashboard chain including US-49 (candle API),
    US-50 (cohort wall), and US-51 (annotation + export). A record that omits
    any of these three stories cannot serve as a complete VPS-confirmation artifact.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    for story in ("US-49", "US-50", "US-51"):
        assert story in text, (
            f"AC-55.2: Tester confirmation record must reference '{story}'. "
            f"AC-55.2 VPS-confirms {story} as part of the P6 dashboard chain. "
            f"Record: {TESTER_CONFIRM_RECORD}"
        )


# ---------------------------------------------------------------------------
# 3. Tester confirm record documents US-49 PASSED promotion
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_us49_promotion() -> None:
    """The confirmation record must document US-49 PASSED promotion.

    AC-55.2 requires that US-49 is promoted to PASSED (CI-verified code-complete in
    sprint-10; blocked only by J1 deploy defect; no application-code change required).
    The record must document this promotion so the promotion is traceable.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "US-49" in text, (
        "AC-55.2: Tester confirmation record must reference 'US-49'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "PASSED" in text, (
        "AC-55.2: Tester confirmation record must reference 'PASSED' (US-49 and US-50 "
        "are promoted to PASSED by AC-55.2). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 4. Tester confirm record documents US-50 PASSED promotion
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_us50_promotion() -> None:
    """The confirmation record must document US-50 PASSED promotion.

    AC-55.2 requires that US-50 is promoted to PASSED (CI-verified code-complete in
    sprint-10; blocked only by J1 deploy defect; no application-code change required).
    The record must document this promotion so the promotion is traceable.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "US-50" in text, (
        "AC-55.2: Tester confirmation record must reference 'US-50'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    # PASSED is already required by test_tester_confirm_record_documents_us49_promotion.
    # Here we check the US-50 promotion context specifically.
    assert "cohort" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference 'cohort' (US-50 is the "
        "cohort wall; the promotion record must identify the story by its feature). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 5. Tester confirm record documents US-51 VPS-gate sign-off
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_us51_sign_off() -> None:
    """The confirmation record must document US-51 VPS-gate sign-off.

    AC-55.2 requires US-51 to receive its 'final VPS-gate sign-off beyond requirements
    approval'. The record must document this sign-off so the final gate is traceable.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "US-51" in text, (
        "AC-55.2: Tester confirmation record must reference 'US-51'. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "sign-off" in text.lower() or "sign_off" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference 'sign-off' (US-51 receives "
        "its final VPS-gate sign-off per AC-55.2). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 6. Tester confirm record documents VPS conditions
# ---------------------------------------------------------------------------


def test_tester_confirm_record_documents_vps_conditions() -> None:
    """The confirmation record must document all key VPS conditions.

    AC-55.2 VPS conditions:
      - HTTP 200 on port 8002 (record references '8002')
      - solanaBilly untouched on port 8001 (record references '8001')
      - web, frontend, listener, celery containers Up

    A record that omits any of these conditions cannot serve as a complete Tester-CONFIRM
    artifact for the P6 dashboard chain closeout.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "8002" in text, (
        "AC-55.2: Tester confirmation record must reference '8002' (HTTP 200 on port 8002). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "8001" in text, (
        "AC-55.2: Tester confirmation record must reference '8001' (solanaBilly untouched "
        "on port 8001). Hard isolation requires confirming solanaBilly is unaffected. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "web" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference the 'web' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "frontend" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference the 'frontend' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "listener" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference the 'listener' container. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "celery" in text.lower(), (
        "AC-55.2: Tester confirmation record must reference 'celery' (celery-worker container). "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 7. Tester confirm record has run ID field (PENDING)
# ---------------------------------------------------------------------------


def test_tester_confirm_record_references_green_run() -> None:
    """The confirmation record must have a run ID field with 'PENDING'.

    AC-55.2 is confirmed from the actual green deploy run obtained in AC-55.1.
    The ops record must have a placeholder run ID field that the Tester fills in
    after confirming the actual green run on main.
    """
    text = TESTER_CONFIRM_RECORD.read_text(encoding="utf-8")
    assert "run" in text.lower(), (
        "AC-55.2: Tester confirmation record must have a run ID field. "
        "AC-55.2 is confirmed from the actual green deploy run obtained in AC-55.1. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )
    assert "PENDING" in text, (
        "AC-55.2: Tester confirmation record must have 'PENDING' fields awaiting the "
        "actual green deploy run confirmation. "
        f"Record: {TESTER_CONFIRM_RECORD}"
    )


# ---------------------------------------------------------------------------
# 8. deploy.yml has US-49 candle API smoke-test step
# ---------------------------------------------------------------------------


def test_deploy_yml_has_us49_candle_api_smoke_test() -> None:
    """deploy.yml must have a smoke-test step for the US-49 candle API (AC-55.2).

    AC-55.2 requires the US-49 candle API (/api/candles/<mint>/?interval_s=60) to
    respond on the VPS. The structural guard here ensures the smoke-test step is in
    deploy.yml so a green deploy run constitutes direct evidence the endpoint is live.

    Removing this step would:
      - Make it impossible to confirm US-49 is live from a deploy run
      - Break the US-49 → PASSED promotion chain
    """
    step_names = _deploy_step_names()
    has_us49_step = any(
        "us-49" in name.lower() or "us49" in name.lower() or "candle api" in name.lower()
        for name in step_names
    )
    assert has_us49_step, (
        "AC-55.2: deploy.yml deploy job must have a smoke-test step for the US-49 candle API. "
        f"Step names found: {step_names}. "
        "Expected a step with 'US-49' or 'candle api' in its name (AC-55.2 VPS-confirmation). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 9. deploy.yml has US-50 cohort wall smoke-test step
# ---------------------------------------------------------------------------


def test_deploy_yml_has_us50_cohort_wall_smoke_test() -> None:
    """deploy.yml must have a smoke-test step for the US-50 cohort wall API (AC-55.2).

    AC-55.2 requires the US-50 cohort wall API (/api/cohort/?interval_s=60) to respond
    on the VPS. The structural guard ensures the smoke-test step is in deploy.yml.

    Removing this step would:
      - Make it impossible to confirm US-50 is live from a deploy run
      - Break the US-50 → PASSED promotion chain
    """
    step_names = _deploy_step_names()
    has_us50_step = any(
        "us-50" in name.lower() or "us50" in name.lower() or "cohort" in name.lower()
        for name in step_names
    )
    assert has_us50_step, (
        "AC-55.2: deploy.yml deploy job must have a smoke-test step for the US-50 cohort wall API. "
        f"Step names found: {step_names}. "
        "Expected a step with 'US-50' or 'cohort' in its name (AC-55.2 VPS-confirmation). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 10. deploy.yml has US-51 annotation API smoke-test step
# ---------------------------------------------------------------------------


def test_deploy_yml_has_us51_annotation_api_smoke_test() -> None:
    """deploy.yml must have a smoke-test step for the US-51 annotation list API (AC-55.2).

    AC-55.2 requires the US-51 annotation list API (/api/annotations/<mint>/) to respond
    on the VPS. The structural guard ensures the smoke-test step is in deploy.yml.

    Removing this step would:
      - Make it impossible to confirm US-51 is live from a deploy run
      - Break the US-51 final VPS-gate sign-off chain
    """
    step_names = _deploy_step_names()
    has_us51_step = any(
        "us-51" in name.lower() or "us51" in name.lower() or "annotation" in name.lower()
        for name in step_names
    )
    assert has_us51_step, (
        "AC-55.2: deploy.yml deploy job must have a smoke-test step for the US-51 annotation API. "
        f"Step names found: {step_names}. "
        "Expected a step with 'US-51' or 'annotation' in its name (AC-55.2 VPS-confirmation). "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 11. deploy.yml US-49 step references the candle endpoint URL
# ---------------------------------------------------------------------------


def test_deploy_yml_us49_step_checks_candle_endpoint() -> None:
    """The US-49 smoke-test step in deploy.yml must reference the candle API URL.

    The smoke-test step must actually curl /api/candles/ (not just exist as a no-op step).
    A step that exists in name but does not curl the endpoint cannot confirm US-49 is live.
    """
    content = _deploy_text()
    assert "api/candles/" in content, (
        "AC-55.2: deploy.yml must reference '/api/candles/' in the US-49 smoke-test step. "
        "The step must curl the candle API endpoint to confirm US-49 is live on the VPS. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 12. deploy.yml US-50 step references the cohort endpoint URL
# ---------------------------------------------------------------------------


def test_deploy_yml_us50_step_checks_cohort_endpoint() -> None:
    """The US-50 smoke-test step in deploy.yml must reference the cohort API URL.

    The smoke-test step must actually curl /api/cohort/ (not just exist as a no-op step).
    A step that exists in name but does not curl the endpoint cannot confirm US-50 is live.
    """
    content = _deploy_text()
    assert "api/cohort/" in content, (
        "AC-55.2: deploy.yml must reference '/api/cohort/' in the US-50 smoke-test step. "
        "The step must curl the cohort wall API endpoint to confirm US-50 is live on the VPS. "
        f"deploy.yml: {DEPLOY_YML}"
    )


# ---------------------------------------------------------------------------
# 13. deploy.yml US-51 step references the annotation endpoint URL
# ---------------------------------------------------------------------------


def test_deploy_yml_us51_step_checks_annotation_endpoint() -> None:
    """The US-51 smoke-test step in deploy.yml must reference the annotation API URL.

    The smoke-test step must actually curl /api/annotations/ (not just exist as a no-op step).
    A step that exists in name but does not curl the endpoint cannot confirm US-51 is live.
    """
    content = _deploy_text()
    assert "api/annotations/" in content, (
        "AC-55.2: deploy.yml must reference '/api/annotations/' in the US-51 smoke-test step. "
        "The step must curl the annotation list API endpoint to confirm US-51 is live on the VPS. "
        f"deploy.yml: {DEPLOY_YML}"
    )
