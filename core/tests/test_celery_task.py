# ---
# module: core.tests.test_celery_task
# sprint: sprint-1
# story: US-1 AC-1.5
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: celery, core.tasks, config.celery
# ---
"""AC-1.5 — Celery worker autodiscovers tasks; sample task dispatches and resolves within timeout."""
import pytest


@pytest.fixture()
def eager_celery():
    """Configure Celery for synchronous eager execution — no live worker needed in test.

    task_always_eager makes .delay() run the task inline in the calling process,
    so .get(timeout=N) resolves immediately. This exercises the full dispatch/result
    code path without requiring an external worker process.
    """
    from config import celery_app

    prev_eager = celery_app.conf.task_always_eager
    prev_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    celery_app.conf.task_always_eager = prev_eager
    celery_app.conf.task_eager_propagates = prev_propagates


def test_add_task_dispatches_and_resolves_within_timeout(eager_celery):
    """Dispatch add(4, 6) and assert result 10 is returned within 5 seconds."""
    from core.tasks import add

    result = add.delay(4, 6)
    assert result.get(timeout=5) == 10


def test_ping_task_dispatches_and_resolves_within_timeout(eager_celery):
    """Dispatch ping() and assert 'pong' is returned within 5 seconds."""
    from core.tasks import ping

    result = ping.delay()
    assert result.get(timeout=5) == "pong"


def test_celery_autodiscovers_core_tasks():
    """Verify core.tasks.add and core.tasks.ping are in the Celery task registry."""
    import core.tasks  # noqa: F401 — import triggers registration via autodiscovery

    from config import celery_app

    registered = set(celery_app.tasks.keys())
    assert "core.tasks.add" in registered, f"core.tasks.add not found in registry: {registered}"
    assert "core.tasks.ping" in registered, f"core.tasks.ping not found in registry: {registered}"


def test_celery_broker_url_is_redis():
    """Verify the Celery broker is configured to use Redis (not in-memory/default)."""
    from config import celery_app

    broker = celery_app.conf.broker_url
    assert broker is not None, "CELERY_BROKER_URL is not set"
    assert broker.startswith("redis://"), f"Expected redis:// broker, got: {broker!r}"


def test_celery_result_backend_is_redis():
    """Verify the result backend is configured to use Redis."""
    from config import celery_app

    backend = celery_app.conf.result_backend
    assert backend is not None, "CELERY_RESULT_BACKEND is not set"
    assert backend.startswith("redis://"), f"Expected redis:// result backend, got: {backend!r}"
