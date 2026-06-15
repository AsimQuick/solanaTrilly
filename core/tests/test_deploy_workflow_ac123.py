# ---
# module: core.tests.test_deploy_workflow_ac123
# sprint: sprint-4
# story: US-12 AC-12.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: pathlib, re, yaml
# ---
"""AC-12.3 — Smoke-test RUNTIME retry-with-backoff structural verification.

AC-12.3 states: The CD smoke-test RETRIES with backoff AT RUNTIME, not just in
file text (retrospective C3; Sprint-3 run 27540260960 showed the curl failing
immediately with no retry).  deploy.yml's smoke-test step must loop the curl
against http://VPS:8002/health/ with a bounded backoff (sleep between attempts,
configurable max-attempts).

The structural test must assert the RUNTIME retry behavior — verifying loop
iteration, sleep/backoff calls, and a max-attempts bound in the parsed workflow
YAML — NOT merely that a loop keyword appears in the file.

Tests in this module:
  test_smoke_test_max_attempts_variable_is_defined
  test_smoke_test_max_attempts_is_positive_integer
  test_smoke_test_max_attempts_meets_minimum_for_cold_start
  test_smoke_test_retry_delay_variable_is_defined
  test_smoke_test_retry_delay_is_positive_integer
  test_smoke_test_loop_bound_references_max_attempts_variable
  test_smoke_test_sleep_call_references_delay_variable
  test_smoke_test_curl_appears_inside_loop_body
  test_smoke_test_success_exit_inside_loop_body
  test_smoke_test_failure_exit_after_loop_exhausted
  test_smoke_test_loop_counter_tracked_in_output
"""
import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths and thresholds
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# Minimum viable values — low enough not to over-constrain, high enough to
# prove the loop actually handles a slow cold start.
SMOKE_MIN_ATTEMPTS = 5
SMOKE_MIN_DELAY_S = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_deploy() -> dict:
    assert DEPLOY_YML.exists(), (
        f"deploy.yml not found at {DEPLOY_YML}. "
        "AC-12.3 requires a .github/workflows/deploy.yml file."
    )
    data = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "deploy.yml must be a valid YAML mapping."
    return data


def _get_smoke_step(data: dict) -> dict:
    jobs = data.get("jobs") or {}
    deploy_job = jobs.get("deploy")
    assert deploy_job is not None, (
        "deploy.yml must have a 'deploy' job (AC-6.3)."
    )
    for step in (deploy_job.get("steps") or []):
        if isinstance(step, dict) and "smoke" in str(step.get("name", "")).lower():
            return step
    raise AssertionError(
        "AC-12.3: No smoke-test step found in the 'deploy' job "
        "(expected a step with 'smoke' in its name)."
    )


def _smoke_script(data: dict) -> str:
    return str(_get_smoke_step(data).get("run", ""))


def _extract_loop_body(script: str) -> str:
    """Return the text between the first 'for ... do' line and its matching 'done'."""
    for_match = re.search(r'\bfor\b.*?\bdo\b', script)
    if not for_match:
        return ""
    after_do = script[for_match.end():]
    done_match = re.search(r'\bdone\b', after_do)
    if not done_match:
        return after_do
    return after_do[:done_match.start()]


def _after_loop(script: str) -> str:
    """Return the text that comes after the loop's closing 'done'."""
    for_match = re.search(r'\bfor\b.*?\bdo\b', script)
    if not for_match:
        return ""
    after_do = script[for_match.end():]
    done_match = re.search(r'\bdone\b', after_do)
    if not done_match:
        return ""
    return after_do[done_match.end():]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_smoke_test_max_attempts_variable_is_defined() -> None:
    """The smoke-test script must define SMOKE_MAX_ATTEMPTS=<number>.

    AC-12.3: configurable max-attempts means the bound must be a named variable,
    not a magic number buried inside a seq call.  A named variable makes the
    threshold visible and adjustable without hunting through shell logic.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    match = re.search(r'SMOKE_MAX_ATTEMPTS\s*=\s*(\d+)', script)
    assert match is not None, (
        "AC-12.3: smoke-test script must define 'SMOKE_MAX_ATTEMPTS=<number>' "
        "to make the retry bound configurable.\n"
        "The variable must be assigned a literal integer, e.g. SMOKE_MAX_ATTEMPTS=12.\n"
        f"Script:\n{script}"
    )


def test_smoke_test_max_attempts_is_positive_integer() -> None:
    """SMOKE_MAX_ATTEMPTS must be a positive integer (>= 1).

    AC-12.3: the loop must be bounded by a numeric value.  Zero or negative
    values would skip all retries; a non-numeric assignment would break the
    seq call at runtime.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    match = re.search(r'SMOKE_MAX_ATTEMPTS\s*=\s*(\d+)', script)
    assert match is not None, (
        "AC-12.3: SMOKE_MAX_ATTEMPTS not found in smoke-test script."
    )
    value = int(match.group(1))
    assert value >= 1, (
        f"AC-12.3: SMOKE_MAX_ATTEMPTS must be >= 1, got {value}.\n"
        "A zero or negative bound means the loop never runs."
    )


def test_smoke_test_max_attempts_meets_minimum_for_cold_start() -> None:
    """SMOKE_MAX_ATTEMPTS must be large enough to survive a cold-start delay.

    AC-12.3 retrospective context: Sprint-3 run 27540260960 showed the curl
    failing immediately with no retry right after the container started.  The
    bound must be at least {SMOKE_MIN_ATTEMPTS} to give Django + daphne time to
    complete migrations and bind port 8000 before the loop exhausts.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    match = re.search(r'SMOKE_MAX_ATTEMPTS\s*=\s*(\d+)', script)
    assert match is not None, (
        "AC-12.3: SMOKE_MAX_ATTEMPTS not defined in smoke-test script."
    )
    value = int(match.group(1))
    assert value >= SMOKE_MIN_ATTEMPTS, (
        f"AC-12.3: SMOKE_MAX_ATTEMPTS={value} is too low — must be >= {SMOKE_MIN_ATTEMPTS}.\n"
        "The container needs several seconds to run migrations and start daphne; "
        "fewer than 5 attempts is not enough to survive a typical cold start."
    )


def test_smoke_test_retry_delay_variable_is_defined() -> None:
    """The smoke-test script must define SMOKE_RETRY_DELAY=<number>.

    AC-12.3: backoff means there is a sleep between attempts.  Using a named
    variable for the delay makes the backoff policy explicit and configurable.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    match = re.search(r'SMOKE_RETRY_DELAY\s*=\s*(\d+)', script)
    assert match is not None, (
        "AC-12.3: smoke-test script must define 'SMOKE_RETRY_DELAY=<number>' "
        "to make the backoff delay configurable.\n"
        f"Script:\n{script}"
    )


def test_smoke_test_retry_delay_is_positive_integer() -> None:
    """SMOKE_RETRY_DELAY must be a positive integer (>= SMOKE_MIN_DELAY_S).

    A zero-second sleep is indistinguishable from no sleep — it does not give
    the container any start-up time between retry attempts.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    match = re.search(r'SMOKE_RETRY_DELAY\s*=\s*(\d+)', script)
    assert match is not None, (
        "AC-12.3: SMOKE_RETRY_DELAY not defined in smoke-test script."
    )
    value = int(match.group(1))
    assert value >= SMOKE_MIN_DELAY_S, (
        f"AC-12.3: SMOKE_RETRY_DELAY={value}s is too short — must be >= {SMOKE_MIN_DELAY_S}s.\n"
        "A zero-second delay means the loop hammers the endpoint without giving "
        "the container time to start, which is equivalent to no retry at all."
    )


def test_smoke_test_loop_bound_references_max_attempts_variable() -> None:
    """The loop's upper bound must reference $SMOKE_MAX_ATTEMPTS, not a literal.

    AC-12.3: configurable max-attempts — the loop must use the variable as its
    bound so that changing SMOKE_MAX_ATTEMPTS once actually changes the runtime
    behavior.  A loop like 'for i in $(seq 1 12)' ignores the variable even if
    it is defined above.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    # Accept both quoted and unquoted variable reference in seq
    pattern = r'seq\s+1\s+["\']?\$\{?SMOKE_MAX_ATTEMPTS\}?["\']?'
    assert re.search(pattern, script) is not None, (
        "AC-12.3: the loop bound must reference $SMOKE_MAX_ATTEMPTS (or "
        '"${SMOKE_MAX_ATTEMPTS}"), not a hardcoded integer.\n'
        "Expected: for i in $(seq 1 \"${SMOKE_MAX_ATTEMPTS}\"); do\n"
        f"Script:\n{script}"
    )


def test_smoke_test_sleep_call_references_delay_variable() -> None:
    """The sleep call inside the loop must use $SMOKE_RETRY_DELAY.

    AC-12.3: the sleep must be driven by the named delay variable so the
    backoff policy is in one place.  A hardcoded 'sleep 5' ignores
    SMOKE_RETRY_DELAY even if it is defined.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    pattern = r'sleep\s+["\']?\$\{?SMOKE_RETRY_DELAY\}?["\']?'
    assert re.search(pattern, script) is not None, (
        "AC-12.3: the sleep call must reference $SMOKE_RETRY_DELAY "
        '(or "${SMOKE_RETRY_DELAY}"), not a literal number.\n'
        "Expected: sleep \"${SMOKE_RETRY_DELAY}\"\n"
        f"Script:\n{script}"
    )


def test_smoke_test_curl_appears_inside_loop_body() -> None:
    """The curl call must appear inside the retry loop body.

    AC-12.3 retrospective: Sprint-3 had the curl OUTSIDE any retry loop.
    This test extracts the text between 'for ... do' and 'done' and asserts
    that curl is called there — proving each iteration actually sends a
    request, not that curl is called once before the loop.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    loop_body = _extract_loop_body(script)
    assert loop_body, (
        "AC-12.3: Could not extract a loop body from the smoke-test script "
        "(no 'for ... do ... done' block found).\n"
        f"Script:\n{script}"
    )
    assert "curl" in loop_body, (
        "AC-12.3: The curl call must appear INSIDE the retry loop body "
        "(between 'for ... do' and 'done').\n"
        "A curl called once before or after the loop does not retry.\n"
        f"Loop body:\n{loop_body}"
    )


def test_smoke_test_success_exit_inside_loop_body() -> None:
    """'exit 0' (success) must appear inside the loop body.

    AC-12.3: the loop must exit early on success — not wait for all
    SMOKE_MAX_ATTEMPTS to elapse before returning.  'exit 0' inside the
    loop body is the runtime signal that a 200 was received.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    loop_body = _extract_loop_body(script)
    assert loop_body, (
        "AC-12.3: Could not extract loop body from smoke-test script."
    )
    assert "exit 0" in loop_body, (
        "AC-12.3: 'exit 0' must appear inside the retry loop body to signal "
        "success as soon as HTTP 200 is received.\n"
        "Without 'exit 0' inside the loop, the script cannot return success "
        "before all attempts are exhausted.\n"
        f"Loop body:\n{loop_body}"
    )


def test_smoke_test_failure_exit_after_loop_exhausted() -> None:
    """'exit 1' (failure) must appear AFTER the loop's 'done'.

    AC-12.3: the loop is bounded — after SMOKE_MAX_ATTEMPTS unsuccessful
    attempts, the script must fail.  'exit 1' must appear after 'done',
    proving the loop terminates rather than running forever.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    after = _after_loop(script)
    assert after, (
        "AC-12.3: Could not extract post-loop text from smoke-test script "
        "(no text found after the loop's 'done')."
    )
    assert "exit 1" in after, (
        "AC-12.3: 'exit 1' must appear AFTER the loop's 'done' to fail the "
        "step when all attempts are exhausted.\n"
        "Without 'exit 1' after the loop the workflow step would succeed even "
        "when the container never responds.\n"
        f"Post-loop text:\n{after}"
    )


def test_smoke_test_loop_counter_tracked_in_output() -> None:
    """The loop counter variable must appear in a progress echo inside the loop.

    AC-12.3 (deploy log shows smoke-test retrying): the loop must print each
    attempt number so the deploy log shows retry progress.  A loop that sends
    curl requests silently makes it impossible to verify retry behavior from
    the log — which is the second verification axis AC-12.3 requires.
    """
    data = _load_deploy()
    script = _smoke_script(data)
    loop_body = _extract_loop_body(script)
    assert loop_body, (
        "AC-12.3: Could not extract loop body from smoke-test script."
    )
    # The loop counter ($i or ${i}) must appear in an echo inside the body
    has_counter_in_echo = bool(
        re.search(r'echo\b.*\$\{?i\}?', loop_body)
        or re.search(r'echo\b.*\bAttempt\b', loop_body, re.IGNORECASE)
    )
    assert has_counter_in_echo, (
        "AC-12.3: The loop body must echo the attempt counter ($i or 'Attempt N') "
        "so the deploy log shows retry progress.\n"
        "This is required for the second verification axis: 'a deploy run whose log "
        "shows the smoke-test retrying when the container is slow to start'.\n"
        f"Loop body:\n{loop_body}"
    )
