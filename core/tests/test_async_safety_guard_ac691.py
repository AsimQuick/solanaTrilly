# ---
# module: core.tests.test_async_safety_guard_ac691
# sprint: sprint-13
# story: US-69 AC-69.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: ast, pathlib
# ---
"""AC-69.1 — Project-wide async-safety guard for Channels/async consumers.

Generalizes the per-consumer K4/L2 guard (sprint-11/sprint-12) to cover ALL
async consumers in the project by scanning every *consumer*.py file.  A
structural/AST check fails pytest if ANY async consumer method contains a bare
.objects. ORM call in an async context (the US-48 TapeFeedConsumer
SynchronousOnlyOperation bug class).

Tests:
  test_positive_violation_fixture_is_caught   — guard catches planted violation
  test_safe_fixture_passes_guard              — guard ignores sync-helper ORM
  test_planted_violation_not_in_sync_method  — no false positives on sync methods
  test_guard_covers_all_known_consumers       — completeness: all 4 consumer files scanned
  test_no_violations_in_real_consumers        — negative: all real consumers pass
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_consumer_files(repo_root: Path) -> list[Path]:
    """Discover all consumer source files project-wide (excluding tests and __pycache__)."""
    return [
        f
        for f in repo_root.glob("**/*consumer*.py")
        if "__pycache__" not in str(f)
        and "test_" not in f.name
    ]


def _shallow_walk(node: ast.AST):
    """Yield AST descendants without descending into FunctionDef, Lambda, or AsyncFunctionDef.

    FunctionDef and Lambda are sync contexts — ORM calls inside them are safe.
    AsyncFunctionDef descendants are handled separately by the outer walk.
    """
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop(0)
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.Lambda, ast.AsyncFunctionDef)):
            todo.extend(ast.iter_child_nodes(child))


def find_async_orm_violations(source_text: str) -> list[tuple[int, str]]:
    """Return (lineno, method_name) pairs for bare .objects access in async methods.

    A bare .objects access inside an async def body (not inside a nested sync
    function def or lambda) is a SynchronousOnlyOperation waiting to happen —
    the US-48 bug class.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    violations: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            fn_name = node.name
            for child in _shallow_walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "objects":
                    key = (child.lineno, fn_name)
                    if key not in seen:
                        seen.add(key)
                        violations.append(key)

    return violations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VIOLATION_FIXTURE = """\
# Deliberately-planted violation fixture for the AC-69.1 positive test.
from channels.generic.websocket import AsyncWebsocketConsumer

class _PlantedViolationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # VIOLATION: synchronous ORM call in async context (US-48 bug class)
        obj = SomeModel.objects.get(pk=1)
        await self.accept()
"""

_SAFE_SYNC_HELPER_FIXTURE = """\
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

class _SafeConsumer(AsyncWebsocketConsumer):
    def _get_obj_sync(self):
        # ORM in sync helper method — safe; called via sync_to_async
        return SomeModel.objects.get(pk=1)

    async def connect(self):
        obj = await sync_to_async(self._get_obj_sync)()
        await self.accept()
"""

_SYNC_ONLY_ORM_FIXTURE = """\
class SomeConsumer:
    def _sync_helper(self):
        return SomeModel.objects.get(pk=1)  # sync — OK

    async def run(self):
        fn = sync_to_async(self._sync_helper)
        obj = await fn()
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_positive_violation_fixture_is_caught():
    """Guard catches the deliberately-planted violation (positive test).

    A bare .objects. call in an async connect() must be detected, confirming
    the guard would catch the US-48 TapeFeedConsumer SynchronousOnlyOperation
    bug class.
    """
    violations = find_async_orm_violations(_VIOLATION_FIXTURE)
    assert violations, (
        "Guard FAILED to catch the planted violation — "
        "the async-safety check is broken or too permissive"
    )
    method_names = [v[1] for v in violations]
    assert "connect" in method_names, (
        f"Expected violation in connect(), got methods: {method_names}"
    )


def test_safe_fixture_passes_guard():
    """Guard passes when ORM is inside a sync helper method (negative control)."""
    violations = find_async_orm_violations(_SAFE_SYNC_HELPER_FIXTURE)
    assert not violations, (
        f"Guard wrongly flagged safe sync-helper pattern: {violations}"
    )


def test_planted_violation_not_in_sync_method():
    """Guard does NOT flag .objects. access in sync (non-async) methods.

    ORM calls in sync helper methods are safe — Django runs them in a
    thread pool when invoked via sync_to_async.  No false positives.
    """
    violations = find_async_orm_violations(_SYNC_ONLY_ORM_FIXTURE)
    assert not violations, (
        f"False positive: guard wrongly flagged sync method ORM: {violations}"
    )


def test_guard_covers_all_known_consumers():
    """Guard scans ALL known consumer file locations (completeness check).

    Ensures the file-discovery glob captures every consumer module.
    """
    consumer_files = _find_consumer_files(REPO_ROOT)
    file_strs = [str(f) for f in consumer_files]

    expected_paths = [
        "core/consumers.py",
        "core/dashboard/consumer.py",
        "core/detection/consumer.py",
        "copytrade/wallet_consumer.py",
    ]
    for expected in expected_paths:
        assert any(expected in f for f in file_strs), (
            f"Expected consumer file missing from scan: {expected}\n"
            f"Files found: {file_strs}"
        )


def test_no_violations_in_real_consumers():
    """All real consumer files in the project pass the async-safety guard (negative test).

    This is the project-wide K4 generalization (sprint-11 K4 / sprint-12 L2):
    scans ALL *consumer*.py files across the repo and asserts none contain
    bare .objects. access inside an async method body.
    """
    consumer_files = _find_consumer_files(REPO_ROOT)
    assert consumer_files, (
        "No consumer files found — check REPO_ROOT and the glob pattern"
    )

    all_violations: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(consumer_files):
        source = path.read_text(encoding="utf-8")
        violations = find_async_orm_violations(source)
        if violations:
            all_violations[str(path.relative_to(REPO_ROOT))] = violations

    assert not all_violations, (
        "Async ORM violations found in consumer files:\n"
        + "\n".join(
            f"  {fpath}: line {lineno} in async {method}()"
            for fpath, viols in sorted(all_violations.items())
            for lineno, method in viols
        )
    )
