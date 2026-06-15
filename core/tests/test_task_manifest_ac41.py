# ---
# module: core.tests.test_task_manifest_ac41
# sprint: sprint-2
# story: US-4 AC-4.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: celery, config.celery, core.tasks
# ---
"""AC-4.1 — Committed task manifest enumerates every expected Celery task;
autodiscovery registers all listed tasks; config confirms autodiscover_tasks call.

These tests do not require a live broker or result backend — they only inspect
the in-process Celery task registry and the source text of config/celery.py.
No .delay() calls are made.
"""
import json
from pathlib import Path

# Repo root is three levels up from this file:
# core/tests/test_task_manifest_ac41.py -> core/tests -> core -> repo-root
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"


def test_manifest_file_exists_at_committed_path():
    """Assert that core/task_manifest.json exists at the expected repo-relative path."""
    assert MANIFEST_PATH.exists(), (
        f"task_manifest.json not found at expected path: {MANIFEST_PATH}"
    )


def test_manifest_is_valid_json_with_required_structure():
    """Load core/task_manifest.json and assert it is valid JSON with required keys.

    Checks:
    - File parses as JSON without error.
    - Top-level key 'tasks' exists and its value is a non-empty list.
    - Top-level key 'version' exists.
    """
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)  # raises json.JSONDecodeError if invalid

    assert "version" in manifest, (
        f"Manifest is missing required key 'version'. Keys present: {list(manifest.keys())}"
    )
    assert "tasks" in manifest, (
        f"Manifest is missing required key 'tasks'. Keys present: {list(manifest.keys())}"
    )
    tasks = manifest["tasks"]
    assert isinstance(tasks, list), (
        f"'tasks' must be a list, got {type(tasks).__name__}"
    )
    assert len(tasks) > 0, (
        "'tasks' list must be non-empty — the manifest must enumerate at least one task."
    )


def test_all_manifest_tasks_are_registered_via_autodiscovery():
    """Assert every task listed in the manifest is present in the Celery task registry.

    Importing core.tasks triggers the @shared_task decorator registration, which
    mirrors what app.autodiscover_tasks() does when the worker starts. This confirms
    that every manifest entry is a genuinely discoverable task, not a stale name.
    """
    import core.tasks  # noqa: F401 — side-effect: registers @shared_task decorators
    from config import celery_app

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registered = set(celery_app.tasks.keys())

    for task_name in manifest["tasks"]:
        assert task_name in registered, (
            f"Manifest task '{task_name}' is not present in the Celery registry. "
            f"Registered tasks: {sorted(registered)}"
        )


def test_celery_app_calls_autodiscover_tasks():
    """Static analysis: confirm config/celery.py contains the autodiscover_tasks call.

    This ensures the autodiscovery mechanism is in place so a real Celery worker
    will register all manifest tasks on startup, not just in tests that manually
    import core.tasks.
    """
    celery_config_path = REPO_ROOT / "config" / "celery.py"
    assert celery_config_path.exists(), (
        f"config/celery.py not found at: {celery_config_path}"
    )
    source = celery_config_path.read_text(encoding="utf-8")
    assert "autodiscover_tasks" in source, (
        "config/celery.py does not contain 'autodiscover_tasks'. "
        "The autodiscovery call must be present for a live worker to discover tasks."
    )
