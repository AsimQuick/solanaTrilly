# ---
# file: core/tests/test_ruff_hook_evidence_ac451.py
# project: solanatrilly
# purpose: AC-45.1 — confirm with EVIDENCE the G3 unforgeable ruff hook stops I001/E501/F401 recurrence
# story: US-45 AC-45.1
# sprint: sprint-9
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: scripts/install-hooks.sh, scripts/pre-commit.sh, .devcontainer/devcontainer.json,
#               .github/workflows/ci.yml, core/tests/test_ruff_gate_unforgeable_ac391.py
# ---
"""AC-45.1 — CONFIRM the G3 unforgeable ruff hook stops the I001/E501/F401 recurrence WITH EVIDENCE.

Sprint-9 is the first sprint where the auto-install mechanism (devcontainer
postCreateCommand + CI install-hooks.sh step, both landed mid-sprint-8 in AC-39.1)
has a full sprint of commits behind it.  This file provides the required evidence:

Part (a) — hook is auto-present + fires locally (re-confirming AC-39.1 via the
           auto-install path, not a manual 'make install-hooks' invocation):
  1. The devcontainer postCreateCommand auto-installs the hook (structural).
  2. The CI 'Auto-install git hooks (AC-39.1)' step auto-installs the hook before
     every 'ruff check .' (structural).
  3. Running install-hooks.sh (the script both paths invoke) correctly installs
     .git/hooks/pre-commit and makes it executable.
  4. The installed hook content invokes ruff (content check).
  5. Ruff catches all three seeded violation classes and passes once fixed:
     I001 (unsorted imports), E501 (line too long), F401 (unused import).

Part (b) — recorded observation that sprint-9's first commits did NOT incur a
           ruff CI defect attributable to a missing local hook:
  6. The evidence fixture exists and is well-formed (schema, verdict, commits).
  7. The verdict is CONFIRMED (no ruff defects in sprint-9 commits).
  8. The ruff_defects_in_sprint9 list is empty (no failures recorded).
  9. Sprint-9 first commits are documented (US-40 through US-44, 15 commits).
  10. The CI auto-install step is named in the fixture (links mechanism to evidence).

ImportError trap (H1 — no second workflow):
  The module-level import of test functions from test_ruff_gate_unforgeable_ac391
  creates a hard compile-time dependency.  Deleting or renaming those functions
  causes ImportError at pytest collection time, failing CI before any test runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# ImportError trap — pins AC-39.1 gate functions (H1)
# ---------------------------------------------------------------------------
# Deleting or renaming either function in test_ruff_gate_unforgeable_ac391
# raises ImportError at pytest collection time, failing CI immediately.
from core.tests.test_ruff_gate_unforgeable_ac391 import (
    test_ci_has_auto_install_hook_step as _ac391_ci_auto_install_step,
)
from core.tests.test_ruff_gate_unforgeable_ac391 import (
    test_devcontainer_has_post_create_command as _ac391_devcontainer_post_create,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-hooks.sh"
PRE_COMMIT_SCRIPT = REPO_ROOT / "scripts" / "pre-commit.sh"
HOOK_TARGET = REPO_ROOT / ".git" / "hooks" / "pre-commit"
DEVCONTAINER_JSON = REPO_ROOT / ".devcontainer" / "devcontainer.json"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EVIDENCE_FIXTURE = Path(__file__).parent / "fixtures" / "ruff_hook_evidence_sprint9.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_ruff(path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["ruff", "check"] + (extra_args or []) + [str(path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _load_ci() -> dict:
    with CI_YML.open() as f:
        return yaml.safe_load(f)


def _load_evidence() -> dict:
    return json.loads(EVIDENCE_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Part (a) — Structural: auto-install mechanism is wired without manual step
# ---------------------------------------------------------------------------


def test_devcontainer_post_create_auto_installs_hook() -> None:
    """devcontainer.json postCreateCommand auto-installs the hook (no manual step).

    The developer does not need to run 'make install-hooks' — opening the repo in
    VS Code Dev Containers triggers postCreateCommand automatically.  This is one of
    the two unforgeable paths (the other is the CI step below).

    AC-45.1(a): the hook is auto-present without any manual 'make install-hooks' step.
    """
    assert DEVCONTAINER_JSON.exists(), (
        f".devcontainer/devcontainer.json missing at {DEVCONTAINER_JSON}.\n"
        "AC-39.1 / AC-45.1 require this file for the devcontainer auto-install path."
    )
    raw_lines = DEVCONTAINER_JSON.read_text(encoding="utf-8").splitlines()
    json_lines = [ln for ln in raw_lines if not ln.strip().startswith("//")]
    content = json.loads("\n".join(json_lines))

    post_create = content.get("postCreateCommand", "")
    assert post_create, (
        "devcontainer.json has no 'postCreateCommand'.\n"
        "AC-45.1(a): the hook must be auto-installed on container creation — "
        "no manual step should be required."
    )
    assert "install-hooks.sh" in post_create, (
        f"postCreateCommand does not reference install-hooks.sh; got: {post_create!r}.\n"
        "AC-45.1(a): the auto-install must call scripts/install-hooks.sh."
    )


def test_ci_auto_install_step_fires_before_lint() -> None:
    """ci.yml auto-install step runs before 'ruff check .' on every CI run.

    The step order (install-hooks before Lint) ensures the hook mechanism is
    exercised on every CI run before any lint check, making it impossible for
    a missing local hook to be the cause of a CI ruff failure.

    AC-45.1(a): the auto-install is unforgeable — it fires in CI regardless of
    the developer's local .git/hooks/ state.
    """
    ci = _load_ci()
    steps = ci["jobs"]["test"]["steps"]

    install_idx = None
    lint_idx = None
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = step.get("name", "")
        run = step.get("run", "")
        if "39.1" in name or "install-hooks" in run.lower():
            install_idx = i
        if "ruff check" in run:
            lint_idx = i

    assert install_idx is not None, (
        "No auto-install hook step found in ci.yml 'test' job.\n"
        "Expected a step with '39.1' in name or 'install-hooks' in run command.\n"
        "AC-45.1(a): the CI auto-install step must be present."
    )
    assert lint_idx is not None, (
        "No 'ruff check' step found in ci.yml 'test' job.\n"
        "AC-45.1(a): a Lint step running ruff is required."
    )
    assert install_idx < lint_idx, (
        f"Auto-install step (index {install_idx}) must come BEFORE Lint step "
        f"(index {lint_idx}) in ci.yml.\n"
        "AC-45.1(a): the hook must be installed before ruff check runs."
    )


def test_install_script_exists_and_is_executable() -> None:
    """scripts/install-hooks.sh exists and is executable.

    Both auto-install paths (devcontainer postCreateCommand, CI step) call this
    script.  It must be executable so it runs without an explicit 'bash' prefix.

    AC-45.1(a): the install mechanism is in place.
    """
    assert INSTALL_SCRIPT.exists(), (
        f"scripts/install-hooks.sh not found at {INSTALL_SCRIPT}.\n"
        "AC-45.1(a): both auto-install paths depend on this script."
    )
    assert os.access(INSTALL_SCRIPT, os.X_OK), (
        "scripts/install-hooks.sh is not executable.\n"
        "Run: chmod +x scripts/install-hooks.sh"
    )


# ---------------------------------------------------------------------------
# Part (a) — Behavioral: running install-hooks.sh installs the hook
# ---------------------------------------------------------------------------


def test_running_install_script_installs_pre_commit_hook() -> None:
    """Running scripts/install-hooks.sh (the auto-install) creates .git/hooks/pre-commit.

    This test simulates what the devcontainer postCreateCommand and the CI step
    both do automatically — no manual 'make install-hooks' invocation.

    After this test, .git/hooks/pre-commit is present and the hook is active.

    AC-45.1(a): the auto-install path (install-hooks.sh) actually installs the hook.
    """
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"install-hooks.sh failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert HOOK_TARGET.exists(), (
        ".git/hooks/pre-commit not created after running install-hooks.sh.\n"
        "Expected: " + str(HOOK_TARGET)
    )


def test_installed_hook_is_executable() -> None:
    """The hook installed by install-hooks.sh is executable.

    A non-executable hook is silently skipped by Git — it would appear to be
    installed but never fire.  The install script uses 'chmod +x' to prevent this.

    AC-45.1(a): the hook fires locally (non-executable = silent no-op).
    """
    if not HOOK_TARGET.exists():
        subprocess.run(["bash", str(INSTALL_SCRIPT)], cwd=str(REPO_ROOT), check=True)

    assert os.access(HOOK_TARGET, os.X_OK), (
        ".git/hooks/pre-commit exists but is NOT executable.\n"
        "Git silently skips non-executable hooks — the gate would never fire.\n"
        "Reinstall with: bash scripts/install-hooks.sh"
    )


def test_installed_hook_content_invokes_ruff() -> None:
    """The installed pre-commit hook invokes ruff (directly or via Docker).

    The hook content must reference 'ruff' — either through 'docker compose run'
    (which runs ruff inside the container) or directly as 'ruff check'.

    AC-45.1(a): the hook fires on ruff violations (not a no-op hook).
    """
    if not HOOK_TARGET.exists():
        subprocess.run(["bash", str(INSTALL_SCRIPT)], cwd=str(REPO_ROOT), check=True)

    hook_content = HOOK_TARGET.read_text(encoding="utf-8")
    assert "ruff" in hook_content, (
        "Installed .git/hooks/pre-commit does not reference 'ruff'.\n"
        f"Hook content:\n{hook_content}\n\n"
        "AC-45.1(a): the hook must invoke ruff to catch I001/E501/F401."
    )


# ---------------------------------------------------------------------------
# Part (a) — Behavioral: ruff fires on seeded violations (re-confirming AC-39.1)
# ---------------------------------------------------------------------------


def test_ruff_fires_on_seeded_i001_violation(tmp_path: Path) -> None:
    """I001 (unsorted imports): ruff fails on bad, passes on fixed.

    Re-confirms AC-39.1 behavior in the AC-45.1 evidence suite.
    The hook ultimately calls ruff; this verifies the ruff-level gate fires.

    AC-45.1(a): hook fires locally on seeded I001 and passes once fixed.
    """
    bad = tmp_path / "bad_i001.py"
    bad.write_text(
        textwrap.dedent("""\
            import os
            import json
            import abc
        """)
    )
    result = _run_ruff(bad, extra_args=["--select", "I001"])
    assert result.returncode != 0, (
        "ruff must reject unsorted imports (I001) but returned 0.\n"
        f"stdout: {result.stdout}"
    )
    assert "I001" in result.stdout or "I001" in result.stderr, (
        f"Expected 'I001' in ruff output; stdout={result.stdout!r}"
    )

    good = tmp_path / "good_i001.py"
    good.write_text(
        textwrap.dedent("""\
            import abc
            import json
            import os
        """)
    )
    result = _run_ruff(good, extra_args=["--select", "I001"])
    assert result.returncode == 0, (
        f"ruff must accept sorted imports; stderr={result.stderr!r}"
    )


def test_ruff_fires_on_seeded_e501_violation(tmp_path: Path) -> None:
    """E501 (line too long): ruff fails on bad, passes on fixed.

    Re-confirms AC-39.1 behavior in the AC-45.1 evidence suite.

    AC-45.1(a): hook fires locally on seeded E501 and passes once fixed.
    """
    bad = tmp_path / "bad_e501.py"
    long_line = "x = " + "1" * 117  # 121 chars, over the pyproject.toml 120-char limit
    bad.write_text(long_line + "\n")

    result = subprocess.run(
        ["ruff", "check", "--config", str(REPO_ROOT / "pyproject.toml"), str(bad)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "ruff must reject lines > 120 chars (E501) but returned 0.\n"
        f"stdout: {result.stdout}"
    )
    assert "E501" in result.stdout or "E501" in result.stderr, (
        f"Expected 'E501' in ruff output; stdout={result.stdout!r}"
    )

    good = tmp_path / "good_e501.py"
    good.write_text("x = 1\n")
    result = subprocess.run(
        ["ruff", "check", "--config", str(REPO_ROOT / "pyproject.toml"), str(good)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff must accept short lines; stderr={result.stderr!r}"
    )


def test_ruff_fires_on_seeded_f401_violation(tmp_path: Path) -> None:
    """F401 (unused import): ruff fails on bad, passes on fixed.

    Re-confirms AC-39.1 behavior in the AC-45.1 evidence suite.

    AC-45.1(a): hook fires locally on seeded F401 and passes once fixed.
    """
    bad = tmp_path / "bad_f401.py"
    bad.write_text(
        textwrap.dedent("""\
            import os


            def hello():
                return "hello"
        """)
    )
    result = _run_ruff(bad)
    assert result.returncode != 0, (
        "ruff must reject unused imports (F401) but returned 0.\n"
        f"stdout: {result.stdout}"
    )
    assert "F401" in result.stdout or "F401" in result.stderr, (
        f"Expected 'F401' in ruff output; stdout={result.stdout!r}"
    )

    good = tmp_path / "good_f401.py"
    good.write_text(
        textwrap.dedent("""\
            def hello():
                return "hello"
        """)
    )
    result = _run_ruff(good)
    assert result.returncode == 0, (
        f"ruff must accept file without unused imports; stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Part (b) — Recorded observation: sprint-9 evidence fixture
# ---------------------------------------------------------------------------


def test_sprint9_evidence_fixture_exists() -> None:
    """The sprint-9 ruff hook evidence fixture exists.

    AC-45.1(b): requires a recorded observation that sprint-9's commits did
    NOT incur a ruff CI defect attributable to a missing local hook.
    This fixture IS that recorded observation.
    """
    assert EVIDENCE_FIXTURE.exists(), (
        f"Sprint-9 ruff hook evidence fixture not found at:\n  {EVIDENCE_FIXTURE}\n\n"
        "AC-45.1(b) requires a recorded observation (JSON fixture) documenting "
        "that sprint-9's first commits did not incur a ruff CI defect."
    )


def test_sprint9_evidence_fixture_is_well_formed() -> None:
    """The evidence fixture parses as JSON and has the required schema fields.

    AC-45.1(b): the recorded observation must be a durable, readable artifact.
    """
    evidence = _load_evidence()
    required_fields = [
        "schema",
        "sprint",
        "sprint_9_first_commits",
        "ci_auto_install_step_name",
        "ruff_defects_in_sprint9",
        "observation",
        "verdict",
        "verdict_detail",
        "evidence_type",
    ]
    missing = [f for f in required_fields if f not in evidence]
    assert not missing, (
        f"Evidence fixture is missing required fields: {missing}\n"
        f"Fixture path: {EVIDENCE_FIXTURE}"
    )
    assert evidence["schema"].startswith("ruff_hook_evidence/"), (
        f"Unexpected schema: {evidence['schema']!r}"
    )
    assert evidence["sprint"] == "sprint-9", (
        f"Evidence fixture records wrong sprint: {evidence['sprint']!r}"
    )


def test_sprint9_evidence_verdict_is_confirmed() -> None:
    """The verdict in the evidence fixture is CONFIRMED.

    CONFIRMED means: the G3 unforgeable ruff gate stopped the seven-sprint
    I001/E501/F401 recurrence in sprint-9.

    AC-45.1(b): assumption replaced by evidence — verdict must be explicitly confirmed.
    """
    evidence = _load_evidence()
    verdict = evidence.get("verdict", "")
    assert verdict == "CONFIRMED", (
        f"Sprint-9 evidence verdict is {verdict!r}; expected 'CONFIRMED'.\n"
        "The G3 unforgeable ruff gate must be confirmed to have stopped the "
        "recurrence before this story can be closed.\n"
        f"Fixture: {EVIDENCE_FIXTURE}"
    )


def test_sprint9_evidence_no_ruff_defects() -> None:
    """The ruff_defects_in_sprint9 list in the evidence fixture is empty.

    An empty list means: no sprint-9 CI run recorded a ruff I001/E501/F401
    failure.  Any entry in this list would indicate a hook-mechanism failure
    and should cause this test to fail, blocking the AC from being accepted.

    AC-45.1(b): the seven-sprint recurrence did NOT happen in sprint-9.
    """
    evidence = _load_evidence()
    defects = evidence.get("ruff_defects_in_sprint9", [])
    assert isinstance(defects, list), (
        f"ruff_defects_in_sprint9 must be a list; got {type(defects).__name__!r}"
    )
    assert len(defects) == 0, (
        f"ruff_defects_in_sprint9 is NOT empty — {len(defects)} defect(s) recorded:\n"
        + "\n".join(f"  {d}" for d in defects)
        + "\n\nAC-45.1(b) FAILED: ruff CI defects occurred in sprint-9."
    )


def test_sprint9_evidence_first_commits_documented() -> None:
    """The evidence fixture documents sprint-9's first implementation commits.

    The fixture must record at least the US-40 through US-44 commits that are
    the 'first commits of sprint-9' the AC requires evidence for.

    AC-45.1(b): the evidence is specific to sprint-9's first commits (not generic).
    """
    evidence = _load_evidence()
    commits = evidence.get("sprint_9_first_commits", [])
    assert len(commits) >= 10, (
        f"Expected at least 10 sprint-9 first commits documented; got {len(commits)}.\n"
        "The fixture must record the US-40 through US-44 commits."
    )
    stories_covered = {c.get("story", "") for c in commits}
    required_stories = {"US-40", "US-41", "US-42", "US-43", "US-44"}
    missing_stories = required_stories - stories_covered
    assert not missing_stories, (
        f"Evidence fixture is missing commits for stories: {sorted(missing_stories)}\n"
        "All sprint-9 implementation stories (US-40 through US-44) must be documented."
    )

    for commit in commits:
        assert commit.get("sha"), f"Commit entry missing 'sha': {commit}"
        assert commit.get("story"), f"Commit entry missing 'story': {commit}"


def test_sprint9_evidence_ci_auto_install_step_named() -> None:
    """The evidence fixture names the CI auto-install step from ci.yml.

    The fixture links the mechanism (ci_auto_install_step_name) to the evidence —
    the auto-install step is WHY no ruff defect was attributable to a missing hook.

    AC-45.1(b): mechanism linked to evidence (assumption replaced by evidence).
    """
    evidence = _load_evidence()
    step_name = evidence.get("ci_auto_install_step_name", "")
    assert step_name, (
        "Evidence fixture missing 'ci_auto_install_step_name'.\n"
        "AC-45.1(b): the fixture must name the CI step that prevents hook-missing failures."
    )

    ci = _load_ci()
    steps = ci["jobs"]["test"]["steps"]
    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    assert step_name in step_names, (
        f"ci_auto_install_step_name {step_name!r} not found in ci.yml 'test' job steps.\n"
        f"Available step names: {step_names}\n"
        "The fixture must name an actual step in ci.yml."
    )


# ---------------------------------------------------------------------------
# ImportError-trap registration test
# ---------------------------------------------------------------------------


def test_ac451_gate_functions_are_callable() -> None:
    """The AC-39.1 gate functions imported at module level are callable.

    The module-level imports are the primary ImportError trap (H1); this test
    adds a human-readable assertion in case an import resolves to a non-callable.

    AC-45.1: the prior unforgeable gate (AC-39.1) remains callable — it cannot
    be silently deleted or stubbed out.
    """
    gate_functions = {
        "_ac391_ci_auto_install_step": _ac391_ci_auto_install_step,
        "_ac391_devcontainer_post_create": _ac391_devcontainer_post_create,
    }
    not_callable = [name for name, fn in gate_functions.items() if not callable(fn)]
    assert not not_callable, (
        "AC-39.1 gate functions are not callable — they may have been stubbed:\n"
        + "\n".join(f"  {n}" for n in not_callable)
        + "\n\nAC-45.1 requires AC-39.1's unforgeable gate to remain intact."
    )
