# ---
# file: core/tests/test_deploy_regression_guard_ac712.py
# project: solanatrilly
# purpose: AC-71.2 — Extended deploy regression guard: pins the three new load-bearing
#          deploy invariants added by US-71 — the built-image import smoke (AC-71.1),
#          the AC-68.3 in-container import step with django.setup() prefix (PR #296),
#          and the AC-69.3 Live Positions open/closed smoke-test steps — so the deploy
#          path cannot silently drop them between a green deliberate run and a later
#          per-merge run (the J4 failure mode). Fails loudly; never degrades to a
#          no-op (H1 pattern). Each invariant has a positive test and a planted-removal
#          negative test.
# story: US-71 AC-71.2 US-72 AC-72.3
# sprint: sprint-14
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: .github/workflows/ci.yml, .github/workflows/deploy.yml, pyyaml
# ---
"""AC-71.2 — Extended deploy regression guard: three new load-bearing invariants.

This module extends the sprint-11 AC-54.2 nine-invariant guard (test_deploy_regression_guard_ac542.py)
with three new invariants that post-date sprint-11 and must not be silently dropped.

New invariants pinned (AC-71.2):

  INVARIANT-10  Built-image import smoke step in ci.yml (AC-71.1)
                'Pre-deploy built-image import smoke (AC-71.1)' step must be present
                in the ci.yml test job, invoking run_import_smoke.py against the
                BUILT container — not the test venv. Removing this step means the
                AppRegistryNotReady recurrence class (sprint-13 root cause) can pass
                all unit tests and reach the VPS undetected.

  INVARIANT-11  AC-68.3 in-container import step with django.setup() prefix (PR #296)
                The 'Verify shared apparatus imports in-container (AC-68.3)' step must
                be present in deploy.yml deploy job AND must contain 'django.setup()'.
                PR #296 (commit 35ce277) prepended 'import django; django.setup();'
                to prevent AppRegistryNotReady on a module that defines a Django model
                at import time. Removing it re-exposes the exact sprint-13 root cause.

  INVARIANT-12  AC-69.3 Live Positions open/closed smoke-test steps in deploy.yml
                Both 'Smoke-test US-69 Live Positions open API (AC-69.3)' (checking
                /api/trading/positions/open/) and 'Smoke-test US-69 Live Positions
                closed API (AC-69.3)' (checking /api/trading/positions/closed/) must
                be present. Either can be silently dropped without the guard firing
                (J4 failure mode) unless individually pinned.

  INVARIANT-13  AC-72.3 Calibration & PnL analytics API smoke-test step in deploy.yml
                'Smoke-test US-72 Calibration & PnL analytics API (AC-72.3)' must be
                present in the deploy.yml deploy job, checking
                /api/trading/analytics/calibration-pnl/ for HTTP 200. Removing it allows
                a broken US-72 analytics API to reach the VPS undetected (J4 failure mode).

Guard check helpers (_check_inv10/11/12) accept text arguments so that negative tests
can pass modified content — decoupling the check logic from filesystem reads and making
the planted-removal tests self-contained.

Tests:
  Positive (assert invariant present in real files):
    test_invariant_10_ci_import_smoke_step_present
    test_invariant_11_ac683_step_has_django_setup
    test_invariant_12_ac693_open_closed_smoke_steps_present
    test_invariant_13_ac723_calibration_pnl_smoke_step_present

  Negative / planted-removal (assert guard fails if invariant removed):
    test_invariant_10_planted_removal
    test_invariant_11_planted_removal_django_setup
    test_invariant_12_planted_removal_open_step
    test_invariant_12_planted_removal_closed_step
    test_invariant_13_planted_removal

  Compound (all four new invariants pass simultaneously on real files):
    test_all_new_guard_invariants_pass
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Step name constants — used by both check helpers and planted-removal tests
CI_SMOKE_STEP_NAME = "Pre-deploy built-image import smoke (AC-71.1)"
DEPLOY_AC683_STEP_MARKER = "AC-68.3"
DEPLOY_AC693_OPEN_STEP_NAME = "Smoke-test US-69 Live Positions open API (AC-69.3)"
DEPLOY_AC693_CLOSED_STEP_NAME = "Smoke-test US-69 Live Positions closed API (AC-69.3)"
DEPLOY_AC723_STEP_NAME = "Smoke-test US-72 Calibration & PnL analytics API (AC-72.3)"


# ---------------------------------------------------------------------------
# Guard check helpers (accept text so negative tests can inject modified content)
# ---------------------------------------------------------------------------


def _check_inv10(ci_text: str) -> list[str]:
    """INVARIANT-10: built-image import smoke step present in ci.yml test job."""
    failures: list[str] = []
    try:
        ci_data = yaml.safe_load(ci_text)
        steps = (ci_data or {}).get("jobs", {}).get("test", {}).get("steps") or []
    except Exception as exc:
        return [f"INVARIANT-10: ci.yml parse error: {exc}"]

    smoke_step = next(
        (
            s
            for s in steps
            if isinstance(s, dict) and CI_SMOKE_STEP_NAME in str(s.get("name", ""))
        ),
        None,
    )
    if smoke_step is None:
        failures.append(
            f"INVARIANT-10 MISSING: '{CI_SMOKE_STEP_NAME}' step not found in the "
            "ci.yml test job. This step (AC-71.1) runs the built-image import smoke "
            "against the BUILT container before any push or deploy; removing it means "
            "the AppRegistryNotReady recurrence class (sprint-13 root cause) can pass "
            "all unit tests and only surface at VPS time — the J4 failure mode. "
            "Restore the step invoking 'python3 tools/run_import_smoke.py'."
        )
    elif "run_import_smoke.py" not in str(smoke_step.get("run", "")):
        failures.append(
            f"INVARIANT-10 VIOLATED: '{CI_SMOKE_STEP_NAME}' step found but its "
            "'run:' does not invoke run_import_smoke.py. The step must execute "
            "'python3 tools/run_import_smoke.py' against the built image."
        )
    return failures


def _check_inv11(deploy_text: str) -> list[str]:
    """INVARIANT-11: AC-68.3 step with django.setup() present in deploy.yml deploy job."""
    failures: list[str] = []
    try:
        deploy_data = yaml.safe_load(deploy_text)
        steps = (
            (deploy_data or {}).get("jobs", {}).get("deploy", {}).get("steps") or []
        )
    except Exception as exc:
        return [f"INVARIANT-11: deploy.yml parse error: {exc}"]

    ac683_step = next(
        (
            s
            for s in steps
            if isinstance(s, dict) and DEPLOY_AC683_STEP_MARKER in str(s.get("name", ""))
        ),
        None,
    )
    if ac683_step is None:
        failures.append(
            f"INVARIANT-11 MISSING: No deploy.yml deploy-job step containing "
            f"'{DEPLOY_AC683_STEP_MARKER}' found. The 'Verify shared apparatus imports "
            "in-container (AC-68.3)' step must be present to catch shared-apparatus "
            "import failures on the live container before the stack serves traffic."
        )
    else:
        run_script = str(ac683_step.get("run", ""))
        if "django.setup()" not in run_script:
            failures.append(
                f"INVARIANT-11 VIOLATED: '{DEPLOY_AC683_STEP_MARKER}' step found "
                "but its 'run:' does NOT contain 'django.setup()'. "
                "PR #296 (commit 35ce277) prepended 'import django; django.setup();' "
                "to prevent AppRegistryNotReady — a module that defines a Django model "
                "at import time will raise AppRegistryNotReady without this prefix. "
                "Removing it silently re-exposes the sprint-13 root cause at VPS time."
            )
    return failures


def _check_inv12(deploy_text: str) -> list[str]:
    """INVARIANT-12: both AC-69.3 positions smoke steps present in deploy.yml."""
    failures: list[str] = []

    if DEPLOY_AC693_OPEN_STEP_NAME not in deploy_text:
        failures.append(
            f"INVARIANT-12 MISSING: '{DEPLOY_AC693_OPEN_STEP_NAME}' step absent "
            "from deploy.yml. The US-69 open API smoke (GET /api/trading/positions/open/) "
            "must be present to catch US-69 route regressions on every deploy — "
            "without it a broken route can reach the VPS undetected."
        )
    elif "/api/trading/positions/open/" not in deploy_text:
        failures.append(
            "INVARIANT-12 VIOLATED: Open API smoke step present but does not check "
            "/api/trading/positions/open/ endpoint."
        )

    if DEPLOY_AC693_CLOSED_STEP_NAME not in deploy_text:
        failures.append(
            f"INVARIANT-12 MISSING: '{DEPLOY_AC693_CLOSED_STEP_NAME}' step absent "
            "from deploy.yml. The US-69 closed API smoke (GET /api/trading/positions/closed/) "
            "must be present — each step is individually load-bearing; the open step "
            "does not cover the closed route."
        )
    elif "/api/trading/positions/closed/" not in deploy_text:
        failures.append(
            "INVARIANT-12 VIOLATED: Closed API smoke step present but does not check "
            "/api/trading/positions/closed/ endpoint."
        )

    return failures


def _check_inv13(deploy_text: str) -> list[str]:
    """INVARIANT-13: US-72 Calibration & PnL analytics API smoke step in deploy.yml."""
    failures: list[str] = []

    if DEPLOY_AC723_STEP_NAME not in deploy_text:
        failures.append(
            f"INVARIANT-13 MISSING: '{DEPLOY_AC723_STEP_NAME}' step absent "
            "from deploy.yml. The US-72 Calibration & PnL analytics API smoke "
            "(GET /api/trading/analytics/calibration-pnl/) must be present to catch "
            "US-72 analytics route regressions on every deploy — without it a broken "
            "route can reach the VPS undetected (J4 failure mode)."
        )
    elif "/api/trading/analytics/calibration-pnl/" not in deploy_text:
        failures.append(
            "INVARIANT-13 VIOLATED: Calibration & PnL analytics smoke step present "
            "but does not check /api/trading/analytics/calibration-pnl/ endpoint."
        )

    return failures


# ---------------------------------------------------------------------------
# INVARIANT-10: built-image import smoke step in ci.yml (AC-71.1)
# ---------------------------------------------------------------------------


def test_invariant_10_ci_import_smoke_step_present() -> None:
    """INVARIANT-10 (positive): ci.yml test job has the built-image import smoke step.

    AC-71.1 adds 'Pre-deploy built-image import smoke (AC-71.1)' to ci.yml immediately
    after 'Build containers', so the symbol imports are validated against the BUILT image
    (not the test venv) before any push or deploy. This is the preventive layer that
    stops the sprint-13 AppRegistryNotReady class from reaching the VPS.

    Regression: removing this step means a broken import silently passes unit tests,
    gets pushed to GHCR, and only surfaces when the VPS runs the AC-68.3 in-container
    check — an unnecessary whole-sprint round-trip.
    """
    failures = _check_inv10(CI_YML.read_text(encoding="utf-8"))
    assert not failures, (
        "AC-71.2 INVARIANT-10 FAILED:\n" + "\n".join(f"  • {f}" for f in failures)
    )


def test_invariant_10_planted_removal() -> None:
    """INVARIANT-10 (negative/planted-removal): guard fails when smoke step is removed.

    Plants a removal by replacing the step name with a sentinel string, then verifies
    _check_inv10 returns at least one failure. This proves the guard FAILS LOUDLY on
    removal and cannot degrade to a no-op (H1 pattern).
    """
    ci_text = CI_YML.read_text(encoding="utf-8")
    modified = ci_text.replace(CI_SMOKE_STEP_NAME, "PLANTED_REMOVAL_SENTINEL_AC712")
    failures = _check_inv10(modified)
    assert failures, (
        "AC-71.2 INVARIANT-10 PLANTED-REMOVAL: _check_inv10 returned NO failures "
        "after the smoke step name was replaced with a sentinel — H1 violated. "
        f"The guard degraded to a no-op and would NOT catch removal of "
        f"'{CI_SMOKE_STEP_NAME}' from ci.yml."
    )


# ---------------------------------------------------------------------------
# INVARIANT-11: AC-68.3 step with django.setup() in deploy.yml (PR #296)
# ---------------------------------------------------------------------------


def test_invariant_11_ac683_step_has_django_setup() -> None:
    """INVARIANT-11 (positive): deploy.yml AC-68.3 step exists and contains django.setup().

    PR #296 (commit 35ce277) fixed the sprint-13 root cause by prepending
    'import django; django.setup();' to the in-container apparatus check in deploy.yml.
    Without this prefix a module that defines a Django model at import time raises
    AppRegistryNotReady — which was misdiagnosed as a missing dependency for a full sprint.

    This invariant pins BOTH the step's existence AND the django.setup() prefix so
    neither can be silently removed between a green deliberate deploy run and a later
    per-merge run.
    """
    failures = _check_inv11(DEPLOY_YML.read_text(encoding="utf-8"))
    assert not failures, (
        "AC-71.2 INVARIANT-11 FAILED:\n" + "\n".join(f"  • {f}" for f in failures)
    )


def test_invariant_11_planted_removal_django_setup() -> None:
    """INVARIANT-11 (negative/planted-removal): guard fails when django.setup() is removed.

    Plants a removal by replacing 'django.setup()' with a sentinel in the deploy.yml
    text, then verifies _check_inv11 returns at least one failure. This proves the
    guard specifically catches the removal of the django.setup() prefix — the exact
    regression that cost a full sprint when it was absent from the original AC-68.3 step.
    """
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    modified = deploy_text.replace(
        "django.setup()", "PLANTED_REMOVAL_DJANGO_SETUP_AC712"
    )
    failures = _check_inv11(modified)
    assert failures, (
        "AC-71.2 INVARIANT-11 PLANTED-REMOVAL: _check_inv11 returned NO failures "
        "after django.setup() was removed from the AC-68.3 step — H1 violated. "
        "Without this check, removing django.setup() silently re-exposes "
        "AppRegistryNotReady at VPS time (the sprint-13 root cause class)."
    )


# ---------------------------------------------------------------------------
# INVARIANT-12: AC-69.3 open/closed positions smoke steps in deploy.yml
# ---------------------------------------------------------------------------


def test_invariant_12_ac693_open_closed_smoke_steps_present() -> None:
    """INVARIANT-12 (positive): both AC-69.3 smoke steps present in deploy.yml.

    AC-69.3 added two smoke steps that were previously SKIPPED by the AC-54.2
    nine-invariant guard (they post-date sprint-11):
      - 'Smoke-test US-69 Live Positions open API (AC-69.3)' → /api/trading/positions/open/
      - 'Smoke-test US-69 Live Positions closed API (AC-69.3)' → /api/trading/positions/closed/

    Both are individually load-bearing — the open step does not cover the closed route
    and vice versa. Without both pinned, either can be silently dropped between a green
    deliberate run and a later per-merge run (J4 failure mode), allowing a broken US-69
    route to reach the VPS undetected.
    """
    failures = _check_inv12(DEPLOY_YML.read_text(encoding="utf-8"))
    assert not failures, (
        "AC-71.2 INVARIANT-12 FAILED:\n" + "\n".join(f"  • {f}" for f in failures)
    )


def test_invariant_12_planted_removal_open_step() -> None:
    """INVARIANT-12 (negative/planted-removal): guard fails when open API step is removed.

    Plants a removal by replacing the open-step name with a sentinel, then verifies
    _check_inv12 returns at least one failure. Proves the open step is individually
    pinned and cannot be dropped without the guard firing (H1 pattern).
    """
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    modified = deploy_text.replace(
        DEPLOY_AC693_OPEN_STEP_NAME, "PLANTED_REMOVAL_OPEN_AC712"
    )
    failures = _check_inv12(modified)
    assert failures, (
        "AC-71.2 INVARIANT-12 PLANTED-REMOVAL (open): _check_inv12 returned NO "
        "failures after the open step name was replaced with a sentinel — H1 violated. "
        f"Guard must catch removal of '{DEPLOY_AC693_OPEN_STEP_NAME}' from deploy.yml."
    )


def test_invariant_12_planted_removal_closed_step() -> None:
    """INVARIANT-12 (negative/planted-removal): guard fails when closed API step is removed.

    Plants a removal by replacing the closed-step name with a sentinel, then verifies
    _check_inv12 returns at least one failure. Proves the closed step is individually
    pinned — not covered by the open step — and cannot be dropped without the guard firing.
    """
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    modified = deploy_text.replace(
        DEPLOY_AC693_CLOSED_STEP_NAME, "PLANTED_REMOVAL_CLOSED_AC712"
    )
    failures = _check_inv12(modified)
    assert failures, (
        "AC-71.2 INVARIANT-12 PLANTED-REMOVAL (closed): _check_inv12 returned NO "
        "failures after the closed step name was replaced with a sentinel — H1 violated. "
        f"Guard must catch removal of '{DEPLOY_AC693_CLOSED_STEP_NAME}' from deploy.yml."
    )


# ---------------------------------------------------------------------------
# INVARIANT-13: US-72 Calibration & PnL analytics API smoke step in deploy.yml (AC-72.3)
# ---------------------------------------------------------------------------


def test_invariant_13_ac723_calibration_pnl_smoke_step_present() -> None:
    """INVARIANT-13 (positive): deploy.yml has the US-72 Calibration & PnL analytics API smoke step.

    AC-72.3 adds 'Smoke-test US-72 Calibration & PnL analytics API (AC-72.3)' to deploy.yml,
    checking GET /api/trading/analytics/calibration-pnl/ for HTTP 200. Without this step a
    broken US-72 analytics route passes all unit tests and only surfaces at VPS time (J4 failure
    mode).

    Regression: removing this step means a broken /api/trading/analytics/calibration-pnl/
    silently passes deploy pipeline checks and only surfaces on the VPS.
    """
    failures = _check_inv13(DEPLOY_YML.read_text(encoding="utf-8"))
    assert not failures, (
        "AC-71.2 INVARIANT-13 FAILED:\n" + "\n".join(f"  • {f}" for f in failures)
    )


def test_invariant_13_planted_removal() -> None:
    """INVARIANT-13 (negative/planted-removal): guard fails when analytics smoke step is removed.

    Plants a removal by replacing the step name with a sentinel, then verifies
    _check_inv13 returns at least one failure. Proves the guard FAILS LOUDLY on
    removal and cannot degrade to a no-op (H1 pattern).
    """
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")
    modified = deploy_text.replace(
        DEPLOY_AC723_STEP_NAME, "PLANTED_REMOVAL_SENTINEL_AC723"
    )
    failures = _check_inv13(modified)
    assert failures, (
        "AC-71.2 INVARIANT-13 PLANTED-REMOVAL: _check_inv13 returned NO failures "
        "after the analytics smoke step name was replaced with a sentinel — H1 violated. "
        f"The guard degraded to a no-op and would NOT catch removal of "
        f"'{DEPLOY_AC723_STEP_NAME}' from deploy.yml."
    )


# ---------------------------------------------------------------------------
# COMPOUND: all four new invariants pass simultaneously on real files
# ---------------------------------------------------------------------------


def test_all_new_guard_invariants_pass() -> None:
    """COMPOUND GUARD (AC-71.2 + AC-72.3): all four new deploy invariants pass simultaneously.

    Mirrors the H1 ImportError-trap pattern from AC-54.2: fails LOUDLY if ANY single
    new invariant is missing or violated, so no invariant can be silently removed
    without breaking the CI suite. Coexistence is load-bearing — a deploy path where
    three of the four new invariants are present but one is absent is still unsound.

    Each failure message names the specific invariant and its concrete regression risk.
    """
    ci_text = CI_YML.read_text(encoding="utf-8")
    deploy_text = DEPLOY_YML.read_text(encoding="utf-8")

    all_failures: list[str] = []
    all_failures.extend(_check_inv10(ci_text))
    all_failures.extend(_check_inv11(deploy_text))
    all_failures.extend(_check_inv12(deploy_text))
    all_failures.extend(_check_inv13(deploy_text))

    assert not all_failures, (
        "AC-71.2 EXTENDED REGRESSION GUARD FAILED — the following new deploy "
        "invariants are missing or violated. The deploy path is NOT sound as a "
        "system. Each missing invariant re-exposes a specific failure mode from "
        "sprint-13 (AppRegistryNotReady / US-69 route regression / US-72 analytics "
        "route regression):\n\n"
        + "\n".join(f"  • {f}" for f in all_failures)
        + "\n\nFix: restore each named invariant to ci.yml / deploy.yml before merging."
    )
