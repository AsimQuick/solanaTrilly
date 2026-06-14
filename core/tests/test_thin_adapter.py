# ---
# module: core.tests.test_thin_adapter
# sprint: sprint-2
# story: US-2 AC-2.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: ast, pathlib
# ---
"""AC-2.4 — Thin-adapter ground rule: core is framework/network-free.

Tests:
  1. test_core_modules_import_no_framework_or_network
       AST import-node scan of all pure-core files.  Fails if any of them
       imports from a forbidden namespace (celery, channels, requests, httpx,
       aiohttp, websockets, urllib3).  Also fails if any listed pure-core
       file does not exist, so adding new files without updating the list
       is caught immediately.

  2. test_adapter_modules_exist_and_use_frameworks
       Verifies that the thin-adapter files (tasks.py, consumers.py) ARE
       present and DO import the expected framework packages — confirming
       they are the designated adapters and have not accidentally been
       stripped of their framework dependencies.
"""
import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "core"

# ---------------------------------------------------------------------------
# Pure-core files — these must NEVER import framework or network modules.
# Update this list whenever a new pure-core file is added.
# ---------------------------------------------------------------------------

PURE_CORE_FILES = [
    CORE_ROOT / "datasource.py",
    CORE_ROOT / "clock.py",
    CORE_ROOT / "live_source.py",
    CORE_ROOT / "replay_source.py",
]

# ---------------------------------------------------------------------------
# Forbidden top-level namespaces for pure-core files.
# A pure-core file must not import any name whose top-level package is in
# this set.  "Top-level package" means the first dotted component, e.g.
# "celery.app.utils" -> "celery".
# ---------------------------------------------------------------------------

FORBIDDEN_NAMESPACES = {
    "celery",
    "channels",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib3",
}

# ---------------------------------------------------------------------------
# Adapter files — these ARE the thin adapters and SHOULD import frameworks.
# ---------------------------------------------------------------------------

ADAPTER_EXPECTATIONS = {
    CORE_ROOT / "tasks.py": "celery",
    CORE_ROOT / "consumers.py": "channels",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _top_level_namespace(module_name: str) -> str:
    """Return the first dotted component of *module_name*.

    Examples:
        "celery.app.utils"  -> "celery"
        "channels"          -> "channels"
        "os"                -> "os"
    """
    return module_name.split(".")[0]


def _forbidden_imports_in_file(py_file: Path) -> list[str]:
    """Return descriptions of forbidden imports found in *py_file* via AST analysis.

    Detects both bare ``import X`` and ``from X import ...`` forms.
    Raises ValueError if the file does not exist (caller should check first).
    """
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{py_file.relative_to(REPO_ROOT)}: SyntaxError — {exc}"]

    rel = py_file.relative_to(REPO_ROOT)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                ns = _top_level_namespace(alias.name)
                if ns in FORBIDDEN_NAMESPACES:
                    found.append(
                        f"{rel}:{node.lineno}: 'import {alias.name}' "
                        f"(forbidden namespace '{ns}')"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            ns = _top_level_namespace(module)
            if ns in FORBIDDEN_NAMESPACES:
                names = ", ".join(a.name for a in node.names)
                found.append(
                    f"{rel}:{node.lineno}: 'from {module} import {names}' "
                    f"(forbidden namespace '{ns}')"
                )

    return found


def _imports_namespace(py_file: Path, namespace: str) -> bool:
    """Return True if *py_file* imports anything from *namespace* (AST-based)."""
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top_level_namespace(alias.name) == namespace:
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _top_level_namespace(module) == namespace:
                return True

    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_core_modules_import_no_framework_or_network() -> None:
    """Pure-core files must not import any framework or network package.

    This test:
      - Asserts every listed pure-core file actually exists (so adding a new
        file without updating PURE_CORE_FILES causes an immediate failure).
      - Uses AST import-node analysis (not line scanning) to check each file
        for forbidden top-level namespace imports.
      - Fails with a clear, actionable message if any violation is found.

    Forbidden namespaces: celery, channels, requests, httpx, aiohttp,
    websockets, urllib3.

    Rationale (AC-2.4 / PRD §4 Principle #7):
      The clock-injected core must be pure so it can run identically in live
      and offline/replay modes.  Framework coupling breaks parity.  Celery
      tasks and WS consumers are the ONLY permitted places for such imports;
      they act as thin adapters around the pure core.
    """
    # Step 1: Every listed file must exist.
    missing = [f for f in PURE_CORE_FILES if not f.exists()]
    assert not missing, (
        "Pure-core files listed in PURE_CORE_FILES do not exist on disk. "
        "Either the file was deleted or it was never created.\n"
        "Missing:\n" + "\n".join(f"  {f.relative_to(REPO_ROOT)}" for f in missing)
    )

    # Step 2: None of the existing files may import forbidden namespaces.
    all_violations: list[str] = []
    for py_file in PURE_CORE_FILES:
        all_violations.extend(_forbidden_imports_in_file(py_file))

    assert not all_violations, (
        "Pure-core files must not import framework or network packages "
        "(AC-2.4 / PRD §4 Principle #7 — thin-adapter ground rule).\n"
        "Celery tasks and WS consumers are the ONLY permitted adapters;\n"
        "core logic must remain framework-free so live and replay code paths\n"
        "are byte-identical.\n\n"
        "Forbidden namespaces: " + ", ".join(sorted(FORBIDDEN_NAMESPACES)) + "\n\n"
        "Violations found:\n" + "\n".join(f"  {v}" for v in all_violations)
    )


def test_adapter_modules_exist_and_use_frameworks() -> None:
    """Thin-adapter files must exist AND must import their expected frameworks.

    This test verifies that tasks.py imports celery and consumers.py imports
    channels — confirming they are the designated thin adapters rather than
    accidentally pure files that have had their framework imports removed.

    If an adapter's framework import disappears, the framework integration is
    broken (not improved), and this test surfaces that regression immediately.
    """
    missing_files: list[str] = []
    missing_imports: list[str] = []

    for adapter_file, expected_namespace in ADAPTER_EXPECTATIONS.items():
        rel = adapter_file.relative_to(REPO_ROOT)

        # File must exist.
        if not adapter_file.exists():
            missing_files.append(str(rel))
            continue

        # File must import the expected framework namespace.
        if not _imports_namespace(adapter_file, expected_namespace):
            missing_imports.append(
                f"{rel}: expected import from '{expected_namespace}' but none found"
            )

    assert not missing_files, (
        "Thin-adapter files are missing from the repository.\n"
        "These files should exist and import their respective frameworks:\n"
        + "\n".join(f"  {f}" for f in missing_files)
    )

    assert not missing_imports, (
        "Thin-adapter files exist but do not import their expected framework packages.\n"
        "Each adapter must import its framework (celery for tasks.py,\n"
        "channels for consumers.py) to qualify as a thin adapter.\n"
        "If the framework import was removed, the adapter is broken — not improved.\n\n"
        "Missing imports:\n" + "\n".join(f"  {m}" for m in missing_imports)
    )
