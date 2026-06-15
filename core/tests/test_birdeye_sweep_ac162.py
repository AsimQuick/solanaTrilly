# ---
# module: core.tests.test_birdeye_sweep_ac162
# sprint: sprint-4
# story: US-16 AC-16.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.detection.birdeye_sweep, core.models, core.task_manifest,
#   celery, config.celery, django.conf, json, pathlib, pytest
# ---
"""AC-16.2 — Third-belt Birdeye REST graduation sweep as Celery-beat task.

Tests:
  1. test_sweep_reconciles_missed_graduation
       Single missed graduation event -> 1 Token row created.
  2. test_sweep_is_idempotent_no_duplicate
       Same event twice -> still exactly 1 Token row (no duplicate).
  3. test_sweep_reconciles_multiple_events
       Multiple distinct events -> one Token row each.
  4. test_sweep_skips_existing_token_row
       Token already exists (created by another path) -> 0 new rows,
       existing row fields are NOT overwritten (get_or_create semantics).
  5. test_sweep_event_without_block_time_creates_row
       Event without 'block_time' key -> row still created (fallback to now()).
  6. test_sweep_task_in_task_manifest
       'core.tasks.birdeye_graduation_sweep' is present in core/task_manifest.json.
  7. test_sweep_task_registered_in_celery_registry
       After importing core.tasks, the task appears in the Celery task registry.
  8. test_sweep_task_beat_schedule_configured
       CELERY_BEAT_SCHEDULE in Django settings includes the sweep task.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "task_manifest.json"
SWEEP_TASK_NAME = "core.tasks.birdeye_graduation_sweep"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(mint: str, pool_address: str = "POOL_ADDR", block_time: int = 1_700_000_000) -> dict:
    """Return a minimal graduation event dict for use in sweep tests."""
    return {"mint": mint, "pool_address": pool_address, "block_time": block_time}


# ---------------------------------------------------------------------------
# (a) Reconciliation behaviour tests (require DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sweep_reconciles_missed_graduation():
    """A single missed graduation event creates exactly one Token row."""
    from core.detection.birdeye_sweep import reconcile_graduation_events
    from core.models import Token

    events = [_make_event("MINT_SWEEP_A")]
    created = reconcile_graduation_events(events)

    assert created == 1, f"Expected 1 new row, got {created}"
    assert Token.objects.filter(mint="MINT_SWEEP_A").count() == 1


@pytest.mark.django_db
def test_sweep_is_idempotent_no_duplicate():
    """Calling the sweep twice with the same event creates exactly one Token row."""
    from core.detection.birdeye_sweep import reconcile_graduation_events
    from core.models import Token

    events = [_make_event("MINT_SWEEP_B")]

    first = reconcile_graduation_events(events)
    assert first == 1

    second = reconcile_graduation_events(events)
    assert second == 0, f"Second call should create 0 new rows, got {second}"

    assert Token.objects.filter(mint="MINT_SWEEP_B").count() == 1, (
        "Idempotent sweep created a duplicate Token row"
    )


@pytest.mark.django_db
def test_sweep_reconciles_multiple_events():
    """Multiple distinct graduation events each produce one Token row."""
    from core.detection.birdeye_sweep import reconcile_graduation_events
    from core.models import Token

    events = [
        _make_event("MINT_MULTI_1"),
        _make_event("MINT_MULTI_2"),
        _make_event("MINT_MULTI_3"),
    ]
    created = reconcile_graduation_events(events)

    assert created == 3, f"Expected 3 new rows, got {created}"
    assert Token.objects.filter(mint__in=["MINT_MULTI_1", "MINT_MULTI_2", "MINT_MULTI_3"]).count() == 3


@pytest.mark.django_db
def test_sweep_skips_existing_token_row():
    """Sweep does not overwrite a Token row created by another path (get_or_create)."""
    from core.detection.birdeye_sweep import reconcile_graduation_events
    from core.models import Token

    existing_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    Token.objects.create(
        mint="MINT_EXISTING",
        pool_address="ORIGINAL_POOL",
        graduated_at=existing_at,
        graduated_block_time=100,
        dex_source="helius_migrate",
        raw_graduation={"mint": "MINT_EXISTING", "source": "helius"},
    )

    events = [{"mint": "MINT_EXISTING", "pool_address": "SWEEP_POOL", "block_time": 999_999_999}]
    created = reconcile_graduation_events(events)

    assert created == 0, "Sweep must not create a duplicate row for an existing mint"
    token = Token.objects.get(mint="MINT_EXISTING")
    assert token.pool_address == "ORIGINAL_POOL", (
        "Sweep must not overwrite existing row fields (get_or_create, not update_or_create)"
    )
    assert token.dex_source == "helius_migrate", "dex_source must not be overwritten"


@pytest.mark.django_db
def test_sweep_event_without_block_time_creates_row():
    """A graduation event without 'block_time' still creates a Token row (fallback to now())."""
    from core.detection.birdeye_sweep import reconcile_graduation_events
    from core.models import Token

    events = [{"mint": "MINT_NO_BLOCKTIME", "pool_address": "POOL_X"}]
    created = reconcile_graduation_events(events)

    assert created == 1
    assert Token.objects.filter(mint="MINT_NO_BLOCKTIME").exists()


# ---------------------------------------------------------------------------
# (b) Manifest and registry tests (no DB needed)
# ---------------------------------------------------------------------------


def test_sweep_task_in_task_manifest():
    """'core.tasks.birdeye_graduation_sweep' must be present in core/task_manifest.json."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert SWEEP_TASK_NAME in manifest["tasks"], (
        f"'{SWEEP_TASK_NAME}' is missing from core/task_manifest.json. "
        f"Tasks in manifest: {manifest['tasks']}"
    )


def test_sweep_task_registered_in_celery_registry():
    """After importing core.tasks, the sweep task must be in the Celery task registry."""
    import core.tasks  # noqa: F401 — side-effect: registers @shared_task decorators
    from config import celery_app

    registered = set(celery_app.tasks.keys())
    assert SWEEP_TASK_NAME in registered, (
        f"'{SWEEP_TASK_NAME}' not found in Celery registry. "
        f"Registered tasks: {sorted(t for t in registered if not t.startswith('celery.'))}"
    )


def test_sweep_task_beat_schedule_configured():
    """CELERY_BEAT_SCHEDULE must include an entry pointing at the sweep task."""
    from django.conf import settings

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    assert beat_schedule, "CELERY_BEAT_SCHEDULE is not configured in settings"

    task_names = {entry["task"] for entry in beat_schedule.values()}
    assert SWEEP_TASK_NAME in task_names, (
        f"'{SWEEP_TASK_NAME}' is not scheduled in CELERY_BEAT_SCHEDULE. "
        f"Scheduled tasks: {task_names}"
    )
