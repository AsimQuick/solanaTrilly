# ---
# module: core.tests.test_snapshot_adapter_wiring_ac373
# sprint: sprint-8
# story: US-37 AC-37.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.management.commands.run_listener,
#               core.tape.birdeye_snapshot_source, core.snapshot_fetcher,
#               core.clock, core.tests.test_birdeye_snapshot_source_ac371,
#               core.tests.test_birdeye_snapshot_fixture_ac372,
#               ast, pathlib, yaml
# ---
"""AC-37.3 — Structural wiring: BirdeyeSnapshotSource behind the seam in run_listener.

The Birdeye REST snapshot adapter is wired behind the SnapshotDataSource seam
(Principle #7) in the listener adapter layer (run_listener.py), and is available
in BOTH compose files via BIRDEYE_API_KEY on the listener service.

This file is the AC-37.3 verification artifact.  It pins the AC-37.1 and AC-37.2
snapshot tests via module-level ImportError traps (H1 — no second workflow) so
they cannot silently vanish, and runs structural wiring assertions that confirm:
  (a) run_listener.py imports BirdeyeSnapshotSource and exposes build_snapshot_fetcher()
  (b) build_snapshot_fetcher() returns a SnapshotFetcher wired with BirdeyeSnapshotSource
  (c) BIRDEYE_API_KEY is present on the listener service in both compose files
  (d) The single canonical ci.yml 'test' job exercises these tests (H1)

Nothing about modeling runs on the VPS — this is feature/snapshot capture only.

Wiring mechanism (H1 — no second workflow)
------------------------------------------
The module-level imports below create a compile-time dependency on named functions
in the AC-37.1 and AC-37.2 test modules.  Deleting or renaming any of them raises
ImportError during pytest's collection phase, failing the CI 'test' job before
any tests run — loud, immediate, attributable.

Tests
-----
  test_snapshot_adapter_gate_functions_are_callable
      All ImportError-trapped functions resolve to callables.

  test_run_listener_imports_birdeye_snapshot_source
      run_listener.py imports BirdeyeSnapshotSource (AST scan).

  test_run_listener_exposes_build_snapshot_fetcher
      run_listener.py defines build_snapshot_fetcher() (AST scan).

  test_build_snapshot_fetcher_returns_wired_snapshot_fetcher
      build_snapshot_fetcher(api_key) returns a SnapshotFetcher whose injected
      source is a BirdeyeSnapshotSource — offline, no network I/O.

  test_build_snapshot_fetcher_source_is_birdeye_snapshot_source
      The injected source is an instance of BirdeyeSnapshotSource (type check).

  test_listener_compose_birdeye_api_key_present
      docker-compose.yml listener service has BIRDEYE_API_KEY.

  test_staging_compose_listener_birdeye_api_key_present
      docker-compose.staging.yml listener service has BIRDEYE_API_KEY.

  test_snapshot_adapter_tests_in_single_canonical_ci_job
      ci.yml defines a job named 'test' and the pytest invocation does NOT
      --ignore core/tests (H1 — no second workflow).

  test_snapshot_adapter_gate_non_trivial
      Each pinned gate function has a non-empty __name__ and __doc__.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# ImportError traps (H1): if these modules are deleted/renamed, collection fails
# ---------------------------------------------------------------------------
# AC-37.2 — Banked fixture offline replay MAIN GATE functions
from core.tests.test_birdeye_snapshot_fixture_ac372 import (
    test_at_most_one_second_call_returns_none as _ac372_at_most_once_test,
)
from core.tests.test_birdeye_snapshot_fixture_ac372 import (
    test_replay_run_twice_byte_identical as _ac372_byte_identity_test,
)

# AC-37.1 — BirdeyeSnapshotSource seam conformance MAIN GATE functions
from core.tests.test_birdeye_snapshot_source_ac371 import (
    test_birdeye_snapshot_source_is_subclass_of_snapshot_data_source as _ac371_seam_test,
)
from core.tests.test_birdeye_snapshot_source_ac371 import (
    test_future_as_of_is_clamped_before_reaching_birdeye_source as _ac371_clamp_test,
)
from core.tests.test_birdeye_snapshot_source_ac371 import (
    test_get_snapshot_assembles_canonical_seven_field_dict as _ac371_assembly_test,
)
from core.tests.test_birdeye_snapshot_source_ac371 import (
    test_snapshot_fetcher_does_not_import_birdeye_snapshot_source as _ac371_isolation_test,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_RUN_LISTENER_PY = _REPO_ROOT / "core" / "management" / "commands" / "run_listener.py"
_COMPOSE_LOCAL = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_STAGING = _REPO_ROOT / "docker-compose.staging.yml"

# ---------------------------------------------------------------------------
# Registry — pinned AC-37.1 / AC-37.2 gate functions
# ---------------------------------------------------------------------------

_SNAPSHOT_ADAPTER_GATE_FUNCTIONS: dict[str, object] = {
    # AC-37.1 — seam conformance
    "test_birdeye_snapshot_source_is_subclass_of_snapshot_data_source": _ac371_seam_test,
    "test_get_snapshot_assembles_canonical_seven_field_dict": _ac371_assembly_test,
    "test_future_as_of_is_clamped_before_reaching_birdeye_source": _ac371_clamp_test,
    "test_snapshot_fetcher_does_not_import_birdeye_snapshot_source": _ac371_isolation_test,
    # AC-37.2 — banked fixture byte-identity
    "test_replay_run_twice_byte_identical": _ac372_byte_identity_test,
    "test_at_most_one_second_call_returns_none": _ac372_at_most_once_test,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_snapshot_adapter_gate_functions_are_callable() -> None:
    """All ImportError-trapped snapshot adapter gate functions resolve to callables.

    The module-level imports are the primary ImportError trap; this test adds a
    human-readable assertion layer in case an import resolves to a non-callable
    (e.g. a constant or stub replacing a function after a partial rename).

    AC-37.3 (H1): the combined AC-37.1 seam + AC-37.2 byte-identity suite must
    remain callable — deleting either test module breaks this gate.
    """
    not_callable = [
        name
        for name, fn in _SNAPSHOT_ADAPTER_GATE_FUNCTIONS.items()
        if not callable(fn)
    ]
    assert not not_callable, (
        "Snapshot adapter GATE is broken — these named functions are not callable:\n"
        + "\n".join(f"  {n}" for n in not_callable)
        + "\n\nAC-37.3 requires these callables to exist.  "
        "Restore the deleted/renamed test to unblock the gate."
    )


def test_run_listener_imports_birdeye_snapshot_source() -> None:
    """run_listener.py imports BirdeyeSnapshotSource (static analysis / AC-37.3).

    The concrete adapter must be wired from the adapter layer (run_listener.py);
    this AST scan confirms the import exists without executing any network code.
    """
    assert _RUN_LISTENER_PY.is_file(), (
        f"run_listener.py not found at {_RUN_LISTENER_PY}"
    )
    tree = ast.parse(_RUN_LISTENER_PY.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

    assert any("BirdeyeSnapshotSource" in imp for imp in imports), (
        "run_listener.py does not import BirdeyeSnapshotSource.\n"
        "AC-37.3 requires the concrete snapshot adapter to be wired in the "
        "adapter layer (run_listener.py).  Add the import and build_snapshot_fetcher()."
    )


def test_run_listener_exposes_build_snapshot_fetcher() -> None:
    """run_listener.py defines build_snapshot_fetcher() (static analysis / AC-37.3).

    The factory function is the single place where BirdeyeSnapshotSource is
    instantiated (Principle #7 / US-2 guard).  This AST scan confirms it exists
    as a top-level function definition.
    """
    assert _RUN_LISTENER_PY.is_file(), (
        f"run_listener.py not found at {_RUN_LISTENER_PY}"
    )
    tree = ast.parse(_RUN_LISTENER_PY.read_text(encoding="utf-8"))
    top_level_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "build_snapshot_fetcher" in top_level_fns, (
        "run_listener.py does not define build_snapshot_fetcher().\n"
        "AC-37.3 requires a factory function in the adapter layer that wires "
        "BirdeyeSnapshotSource -> SnapshotFetcher (Principle #7)."
    )


def test_build_snapshot_fetcher_returns_wired_snapshot_fetcher() -> None:
    """build_snapshot_fetcher(api_key) returns a SnapshotFetcher — offline, no network.

    The offline assertion: the factory is called with a dummy key and the result
    is a SnapshotFetcher.  No REST call is made here — SnapshotFetcher only calls
    the source on fetch(), which is not invoked in this test.

    AC-37.3 (structural wiring): the adapter is wired behind the seam in
    run_listener and the returned type is SnapshotFetcher (not the concrete source).
    """
    from core.management.commands.run_listener import build_snapshot_fetcher
    from core.snapshot_fetcher import SnapshotFetcher

    fetcher = build_snapshot_fetcher(api_key="OFFLINE_DUMMY_KEY_DO_NOT_CALL")
    assert isinstance(fetcher, SnapshotFetcher), (
        f"build_snapshot_fetcher() returned {type(fetcher)!r}, expected SnapshotFetcher.\n"
        "AC-37.3: the factory must wire BirdeyeSnapshotSource -> SnapshotFetcher "
        "and return the fetcher (not the raw source)."
    )


def test_build_snapshot_fetcher_source_is_birdeye_snapshot_source() -> None:
    """The SnapshotFetcher built by the factory has BirdeyeSnapshotSource injected.

    Verifies the Principle #7 wiring: the concrete adapter is the injected source
    inside the returned fetcher.  No REST call is made.

    AC-37.3: feature/snapshot capture only — nothing about modeling wired here.
    """
    from core.management.commands.run_listener import build_snapshot_fetcher
    from core.tape.birdeye_snapshot_source import BirdeyeSnapshotSource

    fetcher = build_snapshot_fetcher(api_key="OFFLINE_DUMMY_KEY_DO_NOT_CALL")
    assert isinstance(fetcher._source, BirdeyeSnapshotSource), (
        f"SnapshotFetcher._source is {type(fetcher._source)!r}, "
        "expected BirdeyeSnapshotSource.\n"
        "AC-37.3 (Principle #7): build_snapshot_fetcher() must inject "
        "BirdeyeSnapshotSource as the DataSource seam."
    )


def _listener_env_keys(compose_path: Path) -> set[str]:
    """Return the set of environment variable keys for the listener service."""
    assert compose_path.is_file(), f"Compose file not found: {compose_path}"
    cfg = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(cfg, dict), f"Compose file did not parse to dict: {compose_path}"
    services = cfg.get("services", {})
    assert "listener" in services, (
        f"No 'listener' service in {compose_path.name}.\n"
        "AC-37.3 requires the snapshot adapter to be wired via the listener service."
    )
    env = services["listener"].get("environment") or {}
    if isinstance(env, list):
        return {e.split("=")[0] for e in env}
    return set(env.keys())


def test_listener_compose_birdeye_api_key_present() -> None:
    """docker-compose.yml listener service has BIRDEYE_API_KEY.

    AC-37.3 (both compose files): the snapshot adapter needs BIRDEYE_API_KEY
    injected into the listener container at runtime.
    """
    env_keys = _listener_env_keys(_COMPOSE_LOCAL)
    assert "BIRDEYE_API_KEY" in env_keys, (
        f"BIRDEYE_API_KEY not found in docker-compose.yml listener environment.\n"
        f"Found keys: {sorted(env_keys)}\n"
        "AC-37.3 requires BIRDEYE_API_KEY on the listener service in BOTH compose files."
    )


def test_staging_compose_listener_birdeye_api_key_present() -> None:
    """docker-compose.staging.yml listener service has BIRDEYE_API_KEY.

    AC-37.3 (both compose files): the VPS staging stack must have BIRDEYE_API_KEY
    on the listener service so the snapshot adapter can reach Birdeye REST.
    """
    env_keys = _listener_env_keys(_COMPOSE_STAGING)
    assert "BIRDEYE_API_KEY" in env_keys, (
        f"BIRDEYE_API_KEY not found in docker-compose.staging.yml listener environment.\n"
        f"Found keys: {sorted(env_keys)}\n"
        "AC-37.3 requires BIRDEYE_API_KEY on the listener service in BOTH compose files."
    )


def test_snapshot_adapter_tests_in_single_canonical_ci_job() -> None:
    """The snapshot adapter tests run in the single canonical ci.yml 'test' job.

    Asserts (H1 — no second workflow):
      (a) ci.yml exists and defines a job named 'test'.
      (b) The pytest invocation in 'test' does NOT --ignore core/tests, so all
          ImportError-trapped gate functions are always exercised by CI.

    AC-37.3 (H1): the snapshot fetcher tests + adapter offline test must pass in
    the SINGLE canonical ci.yml 'test' job.  A second workflow is a H1 violation.
    """
    assert _CI_YML.is_file(), (
        f"ci.yml not found at {_CI_YML}.\n"
        "AC-37.3 requires the snapshot adapter tests to run in the single "
        "canonical ci.yml 'test' job (H1 — no second workflow)."
    )

    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml did not parse to a dict"

    jobs = workflow.get("jobs", {})
    assert "test" in jobs, (
        "ci.yml has no job named 'test'.\n"
        "H1 requires the snapshot adapter tests to run in the single canonical "
        "'test' job — no second workflow file must be introduced."
    )

    test_job = jobs["test"]
    pytest_cmds = [
        step.get("run", "")
        for step in (test_job.get("steps") or [])
        if isinstance(step, dict) and "pytest" in step.get("run", "")
    ]
    assert pytest_cmds, (
        "The 'test' job in ci.yml contains no pytest step.\n"
        "AC-37.3 requires the snapshot adapter gate to be exercised by the "
        "pytest invocation in the canonical 'test' job."
    )

    for cmd in pytest_cmds:
        assert "--ignore=core/tests" not in cmd and "--ignore core/tests" not in cmd, (
            f"The pytest command in ci.yml ignores core/tests:\n  {cmd}\n\n"
            "The snapshot adapter wiring file lives in core/tests/ and must NOT "
            "be excluded (AC-37.3 H1).  Remove the --ignore flag."
        )


def test_snapshot_adapter_gate_non_trivial() -> None:
    """Each pinned gate function has a non-empty __name__ and __doc__.

    Guards against a stub replacement that passes the ImportError trap but
    asserts nothing meaningful.  A real gate function always has a docstring.
    """
    empty_doc: list[str] = []
    for name, fn in _SNAPSHOT_ADAPTER_GATE_FUNCTIONS.items():
        if not callable(fn):
            continue
        fn_name = getattr(fn, "__name__", "")
        doc = getattr(fn, "__doc__", None)
        if not fn_name or not doc or not doc.strip():
            empty_doc.append(name)

    assert not empty_doc, (
        "These snapshot adapter gate functions have no name or docstring — "
        "they may be stubs rather than real tests:\n"
        + "\n".join(f"  {n}" for n in empty_doc)
    )
