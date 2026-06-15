# ---
# module: core.tests.test_task_manifest
# sprint: sprint-2
# story: US-4 AC-4.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tasks, config.celery
# ---
"""AC-4.1 — Task manifest enumerates registered Celery tasks; autodiscovery is configured."""
import importlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"


def test_task_manifest_file_exists():
    """Assert core/task_manifest.json exists at the repo root."""
    assert MANIFEST_PATH.exists(), f"task_manifest.json not found at {MANIFEST_PATH}"


def test_task_manifest_is_valid_json_with_tasks_list():
    """Load the manifest and assert it is a dict with a non-empty 'tasks' list of strings."""
    data = json.loads(MANIFEST_PATH.read_text())
    assert isinstance(data, dict), "Manifest must be a JSON object"
    assert "tasks" in data, "Manifest must have a 'tasks' key"
    tasks = data["tasks"]
    assert isinstance(tasks, list), "'tasks' must be a list"
    assert len(tasks) > 0, "'tasks' list must not be empty"
    for entry in tasks:
        assert isinstance(entry, str), f"Each task entry must be a string, got {type(entry)!r}"


def test_autodiscover_tasks_is_called_in_celery_config():
    """Read config/celery.py as text and assert autodiscover_tasks is present."""
    celery_config_path = REPO_ROOT / "config" / "celery.py"
    source = celery_config_path.read_text()
    assert "autodiscover_tasks" in source, "autodiscover_tasks not found in config/celery.py"


def test_manifest_enumerates_known_tasks():
    """Assert core.tasks.add and core.tasks.ping are both listed in the manifest."""
    data = json.loads(MANIFEST_PATH.read_text())
    tasks = data["tasks"]
    assert "core.tasks.add" in tasks, "core.tasks.add missing from manifest"
    assert "core.tasks.ping" in tasks, "core.tasks.ping missing from manifest"


def test_manifest_tasks_all_importable():
    """For each task in the manifest, import the module and assert the callable exists."""
    data = json.loads(MANIFEST_PATH.read_text())
    for task_name in data["tasks"]:
        parts = task_name.rsplit(".", 1)
        assert len(parts) == 2, f"Task name {task_name!r} is not in 'module.function' format"
        module_path, func_name = parts
        module = importlib.import_module(module_path)
        assert hasattr(module, func_name), (
            f"Module {module_path!r} has no attribute {func_name!r} (task: {task_name!r})"
        )
