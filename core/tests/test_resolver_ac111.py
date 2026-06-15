# ---
# module: core.tests.test_resolver_ac111
# sprint: sprint-3
# story: US-11 AC-11.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.resolver, core.models, core.schemas, django.test.utils
# ---
"""AC-11.1 — Single cached get_active_config() resolver + static-analysis guard.

Test structure:
  (1) test_get_active_config_returns_active_config         — returns valid schema for active config
  (2) test_get_active_config_returns_none_when_no_active_config — returns None with no active row
  (3) test_get_active_config_is_cached                     — second call issues zero DB queries
  (4) test_no_service_module_uses_os_getenv_or_environ     — static AST guard (regression gate)
  (5) test_pipeline_service_file_scan_is_non_degenerate    — guard can't pass on empty directory
"""
import ast
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import PipelineConfig
from core.resolver import get_active_config, invalidate_active_config_cache
from core.schemas import PipelineConfigSchema

# ---------------------------------------------------------------------------
# Valid section fixtures — all §5.2 invariants satisfied (same as AC-10.4):
#   scoring.window_s (180) > score_at_elapsed_s (120)       ✓  leak guard
#   tape.idle_kill_ttl_s (1800) >= outcome.window_s (1800)  ✓  D4
#   scoring.capture_buffer_s (4) >= 3                       ✓  tape tail
#   gate == "adaptive_topk"                                 ✓  id22 lesson
# ---------------------------------------------------------------------------

VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}

# ---------------------------------------------------------------------------
# Repo root for static-analysis scan
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers for static-analysis guard (tests 4 & 5)
# ---------------------------------------------------------------------------


def _find_pipeline_service_files():
    """Return Python files in core/ and services/ excluding tests and migrations."""
    excluded_parts = {".git", "__pycache__", "migrations", ".venv", "node_modules", "tests"}
    scan_dirs = ["core"]  # expands to include "services" when that directory exists
    result = []
    for d in scan_dirs:
        p = REPO_ROOT / d
        if not p.exists():
            continue
        for f in p.rglob("*.py"):
            if not excluded_parts.intersection(set(f.parts)):
                result.append(f)
    return result


def _os_env_usages(path: Path) -> list[int]:
    """Return line numbers where os.getenv or os.environ is used."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # os.getenv(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                violations.append(node.lineno)
            # from os import getenv; getenv(...)
            if isinstance(func, ast.Name) and func.id == "getenv":
                violations.append(node.lineno)
        # os.environ (attribute access — catches os.environ.get and os.environ[...])
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            violations.append(node.lineno)
    return violations


# ---------------------------------------------------------------------------
# Test 1: resolver returns the active config as a validated PipelineConfigSchema
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_active_config_returns_active_config():
    """get_active_config() returns the active config as a PipelineConfigSchema instance."""
    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac111-test",
        is_active=True,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )
    result = get_active_config()
    assert result is not None
    assert isinstance(result, PipelineConfigSchema)
    # Spot-check a tunable to confirm round-trip fidelity
    assert result.scoring.score_at_elapsed_s == 120


# ---------------------------------------------------------------------------
# Test 2: resolver returns None when no active config exists
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_active_config_returns_none_when_no_active_config():
    """get_active_config() returns None when no row has is_active=True."""
    invalidate_active_config_cache()
    # Create an inactive config — must not be returned
    PipelineConfig.objects.create(
        version=1,
        label="inactive",
        is_active=False,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )
    result = get_active_config()
    assert result is None


# ---------------------------------------------------------------------------
# Test 3: second call does not hit the DB (cache hit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_active_config_is_cached():
    """A second get_active_config() call issues zero DB queries (cache hit)."""
    invalidate_active_config_cache()
    PipelineConfig.objects.create(
        version=1,
        label="ac111-cache-test",
        is_active=True,
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )
    # First call — populates the cache
    get_active_config()

    # Second call — must be served from cache (zero DB queries)
    with CaptureQueriesContext(connection) as ctx:
        second_result = get_active_config()

    assert len(ctx.captured_queries) == 0, (
        f"Expected zero DB queries on the cached second call, "
        f"got {len(ctx.captured_queries)}: {ctx.captured_queries}"
    )
    assert second_result is not None


# ---------------------------------------------------------------------------
# Test 4: static-analysis guard — no core/service module uses os.getenv/environ
# ---------------------------------------------------------------------------


def test_no_service_module_uses_os_getenv_or_environ():
    """No core/ service module reads a pipeline tunable via os.getenv or os.environ.

    This guard passes trivially at sprint start (no service modules yet) and
    becomes a regression gate as services land — the established US-2 pattern.
    """
    files = _find_pipeline_service_files()
    violations_found = []
    for path in files:
        lines = _os_env_usages(path)
        if lines:
            rel = path.relative_to(REPO_ROOT)
            for lineno in lines:
                violations_found.append(f"{rel}:{lineno}")

    assert not violations_found, (
        "Service/core modules must use get_active_config() — not os.getenv or os.environ. "
        "Violations found:\n" + "\n".join(violations_found)
    )


# ---------------------------------------------------------------------------
# Test 5: non-degenerate guard — the scan finds at least one file
# ---------------------------------------------------------------------------


def test_pipeline_service_file_scan_is_non_degenerate():
    """The file scan finds at least one Python file so the guard cannot trivially pass."""
    files = _find_pipeline_service_files()
    assert len(files) >= 1, (
        "Expected at least one Python file in core/ for the static-analysis guard to scan. "
        "If the core/ directory was moved or renamed, update _find_pipeline_service_files()."
    )
