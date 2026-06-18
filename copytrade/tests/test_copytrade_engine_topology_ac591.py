# ---
# module: copytrade.tests.test_copytrade_engine_topology_ac591
# sprint: sprint-12
# story: US-59 AC-59.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, yaml, pathlib, re, importlib, ast
# ---
"""Compose topology + isolation guard tests for AC-59.1.

All tests are pure static/structural — no DB access, no Django marks required
for most.  They verify:

  1. copytrade_engine service is present in both compose files.
  2. The service has NO host ports (port-collision-free worker).
  3. The service command differs from run_listener (separate worker per SPEC §5).
  4. The service carries its own HELIUS_API_KEY env var (own connection).
  5. The listener service still exists (engine does NOT replace it).
  6. The engine module does NOT import any firehose/model mutable singletons
     (isolation guard).
"""
import pathlib
import re

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parents[2]  # solanatrilly/  (copytrade/tests/../.. = project root)
COMPOSE = ROOT / "docker-compose.yml"
STAGING = ROOT / "docker-compose.staging.yml"
ENGINE_SRC = ROOT / "copytrade" / "management" / "commands" / "run_copytrade_engine.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_compose(path: pathlib.Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _engine_import_lines() -> list[str]:
    lines = ENGINE_SRC.read_text().splitlines()
    return [line for line in lines if re.match(r'^\s*(import|from)\s+', line)]


# ---------------------------------------------------------------------------
# Compose topology tests
# ---------------------------------------------------------------------------

def test_copytrade_engine_service_in_docker_compose():
    """copytrade_engine service must be present in docker-compose.yml."""
    data = _load_compose(COMPOSE)
    assert "copytrade_engine" in data["services"], (
        "copytrade_engine service missing from docker-compose.yml"
    )


def test_copytrade_engine_service_in_docker_compose_staging():
    """copytrade_engine service must be present in docker-compose.staging.yml."""
    data = _load_compose(STAGING)
    assert "copytrade_engine" in data["services"], (
        "copytrade_engine service missing from docker-compose.staging.yml"
    )


def test_copytrade_engine_has_no_host_port_in_compose():
    """copytrade_engine must have NO ports in docker-compose.yml (port-collision-free worker)."""
    data = _load_compose(COMPOSE)
    svc = data["services"]["copytrade_engine"]
    assert "ports" not in svc, (
        "copytrade_engine must not expose host ports in docker-compose.yml"
    )


def test_copytrade_engine_has_no_host_port_in_staging():
    """copytrade_engine must have NO ports in docker-compose.staging.yml."""
    data = _load_compose(STAGING)
    svc = data["services"]["copytrade_engine"]
    assert "ports" not in svc, (
        "copytrade_engine must not expose host ports in docker-compose.staging.yml"
    )


def test_copytrade_engine_not_listener_command_in_compose():
    """copytrade_engine must NOT run run_listener (separate service per SPEC §5)."""
    data = _load_compose(COMPOSE)
    cmd = data["services"]["copytrade_engine"].get("command", "")
    assert "run_listener" not in str(cmd), (
        "copytrade_engine must not run run_listener — it is a separate service"
    )


def test_copytrade_engine_not_listener_command_in_staging():
    """Same check for staging compose."""
    data = _load_compose(STAGING)
    cmd = data["services"]["copytrade_engine"].get("command", "")
    assert "run_listener" not in str(cmd), (
        "copytrade_engine must not run run_listener in staging"
    )


def test_copytrade_engine_has_own_helius_key_in_compose():
    """copytrade_engine must declare HELIUS_API_KEY (its own Helius connection)."""
    data = _load_compose(COMPOSE)
    env = data["services"]["copytrade_engine"].get("environment", {})
    # environment can be a dict or a list of "KEY=VALUE" strings
    if isinstance(env, dict):
        keys = set(env.keys())
    else:
        keys = {entry.split("=", 1)[0] for entry in env}
    assert "HELIUS_API_KEY" in keys, (
        "copytrade_engine must declare HELIUS_API_KEY in docker-compose.yml"
    )


def test_copytrade_engine_has_own_helius_key_in_staging():
    """Same check for staging compose."""
    data = _load_compose(STAGING)
    env = data["services"]["copytrade_engine"].get("environment", {})
    if isinstance(env, dict):
        keys = set(env.keys())
    else:
        keys = {entry.split("=", 1)[0] for entry in env}
    assert "HELIUS_API_KEY" in keys, (
        "copytrade_engine must declare HELIUS_API_KEY in docker-compose.staging.yml"
    )


def test_listener_service_still_present_in_compose():
    """listener service must still exist — copytrade_engine does NOT replace it."""
    data = _load_compose(COMPOSE)
    assert "listener" in data["services"], (
        "listener service must remain in docker-compose.yml (engine does not replace it)"
    )


def test_listener_service_still_present_in_staging():
    """Same check for staging compose."""
    data = _load_compose(STAGING)
    assert "listener" in data["services"], (
        "listener service must remain in docker-compose.staging.yml"
    )


# ---------------------------------------------------------------------------
# Isolation guard tests (AST / import-line analysis)
# ---------------------------------------------------------------------------

def test_engine_module_does_not_import_birdeye_swap_source():
    """run_copytrade_engine must NOT import BirdeyeSwapSource (firehose isolation)."""
    import_lines = _engine_import_lines()
    assert not any("BirdeyeSwapSource" in line for line in import_lines), (
        "run_copytrade_engine imports BirdeyeSwapSource — violates firehose isolation"
    )


def test_engine_module_does_not_import_helius_birth_tape_source():
    """run_copytrade_engine must NOT import HeliusBirthTapeSource."""
    import_lines = _engine_import_lines()
    assert not any("HeliusBirthTapeSource" in line for line in import_lines), (
        "run_copytrade_engine imports HeliusBirthTapeSource — violates firehose isolation"
    )


def test_engine_module_does_not_import_tape_recorder():
    """run_copytrade_engine must NOT import TapeRecorder."""
    import_lines = _engine_import_lines()
    assert not any("TapeRecorder" in line for line in import_lines), (
        "run_copytrade_engine imports TapeRecorder — violates firehose isolation"
    )


def test_engine_module_does_not_import_pipeline_config():
    """run_copytrade_engine must NOT import PipelineConfig."""
    import_lines = _engine_import_lines()
    assert not any("PipelineConfig" in line for line in import_lines), (
        "run_copytrade_engine imports PipelineConfig — violates isolation from core.models"
    )


def test_engine_module_does_not_import_pipeline_state():
    """run_copytrade_engine must NOT import PipelineState."""
    import_lines = _engine_import_lines()
    assert not any("PipelineState" in line for line in import_lines), (
        "run_copytrade_engine imports PipelineState — violates isolation from core.models"
    )


def test_engine_module_does_not_import_raw_event():
    """run_copytrade_engine must NOT import RawEvent."""
    import_lines = _engine_import_lines()
    assert not any("RawEvent" in line for line in import_lines), (
        "run_copytrade_engine imports RawEvent — violates isolation from core.models"
    )


def test_engine_module_does_not_import_core_models_module():
    """run_copytrade_engine must NOT have 'from core.models' import."""
    import_lines = _engine_import_lines()
    assert not any("core.models" in line for line in import_lines), (
        "run_copytrade_engine contains 'from core.models' import — violates isolation"
    )
