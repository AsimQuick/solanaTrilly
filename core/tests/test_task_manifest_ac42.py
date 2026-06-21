# ---
# module: core.tests.test_task_manifest_ac42
# sprint: sprint-2
# story: US-4 AC-4.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: celery, config.celery, core.tasks
# ---
"""AC-4.2 — Live registered-task set EQUALS committed manifest (bidirectional guard).

The equality check catches two distinct failure modes:
  1. Task REMOVED or RENAMED without manifest update — manifest has the name, but
     the registry does not (the #404 failure mode). This test still catches it even
     if that task's own unit test was deleted alongside the task, because this test
     reads the manifest directly, independent of any per-task test file.
  2. Task ADDED without manifest update — registry has the name, but manifest does not.

No broker or result backend is required — registry-only inspection via in-process import.
"""
import json
from pathlib import Path

# Repo root is three levels up: core/tests/test_task_manifest_ac42.py -> core/tests -> core -> root
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"

_CELERY_BUILTIN_PREFIX = "celery."


def _load_manifest_tasks() -> set:
    """Return the set of task names from the committed manifest."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return set(manifest["tasks"])


def _registered_app_tasks() -> set:
    """Return registered non-builtin task names (excludes tasks with prefix 'celery.')."""
    import copytrade.tasks  # noqa: F401 — registers copytrade @shared_task decorators
    import core.tasks  # noqa: F401 — side-effect: registers @shared_task decorators
    from config import celery_app

    return {
        name
        for name in celery_app.tasks.keys()
        if not name.startswith(_CELERY_BUILTIN_PREFIX)
    }


def test_registered_tasks_equal_manifest():
    """Live registered-task set must be strictly equal to the committed manifest.

    Failure mode 1 — task in manifest but not registered (removed/renamed task):
        only_in_manifest is non-empty → assertion fails.
    Failure mode 2 — task registered but not in manifest (added without update):
        only_in_registry is non-empty → assertion fails.
    """
    manifest_tasks = _load_manifest_tasks()
    registered_tasks = _registered_app_tasks()

    only_in_manifest = manifest_tasks - registered_tasks
    only_in_registry = registered_tasks - manifest_tasks

    errors = []
    if only_in_manifest:
        errors.append(
            "Tasks in manifest but NOT registered "
            "(task was removed/renamed without updating the manifest — #404 failure mode): "
            f"{sorted(only_in_manifest)}"
        )
    if only_in_registry:
        errors.append(
            "Tasks registered but NOT in manifest "
            "(task was added without updating core/task_manifest.json): "
            f"{sorted(only_in_registry)}"
        )

    assert not errors, "\n".join(errors)


def test_registered_set_is_nonempty():
    """At least one non-builtin task must be registered after importing core.tasks.

    Guards against the degenerate case where all tasks were removed and the manifest
    was also emptied, which would make the equality check trivially pass.
    """
    registered_tasks = _registered_app_tasks()
    assert registered_tasks, (
        "No non-builtin tasks registered after importing core.tasks. "
        "Expected at least one @shared_task-decorated function in core/tasks.py."
    )


def test_manifest_tasks_is_nonempty():
    """The manifest 'tasks' list must enumerate at least one task.

    Guards against a degenerate empty manifest making the equality check pass trivially
    when paired with an empty registry.
    """
    manifest_tasks = _load_manifest_tasks()
    assert manifest_tasks, (
        f"Manifest 'tasks' list is empty — must enumerate at least one task. "
        f"Manifest path: {MANIFEST_PATH}"
    )
