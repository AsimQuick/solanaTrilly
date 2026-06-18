# ---
# module: copytrade.tests.test_copytrade_export_ac633
# sprint: sprint-12
# story: US-63 AC-63.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pytest, pytest-django, celery, copytrade.api, copytrade.tasks,
#   copytrade.export_builder, copytrade.models
# ---
"""AC-63.3: Click-to-download copytrade export wiring tests.

The AC requires:
  - The export reuses the EXISTING §6.5 export channel (trigger via POST → celery →
    poll result via GET /api/export/result/<task_id>/).
  - Runs OFF the celery container (#289, NEVER web/gunicorn) — dispatched via .delay().
  - INCLUDES the copytrade_positions table (both open and closed).
  - Copy-trade view tests wired into the canonical ci.yml 'test' job via H1 ImportError
    trap on named API/task functions — a deleted/renamed surface fails pytest collection.
  - New backend files carry metadata front matter.

H1 ImportError traps (module level — fail pytest COLLECTION if any surface is
deleted or renamed):
  from copytrade.api import copytrade_export_trigger_view
  from copytrade.tasks import export_copytrade_positions
  from copytrade.export_builder import build_copytrade_export

Tests:
  test_h1_copytrade_export_trigger_view_callable
  test_h1_export_copytrade_positions_callable
  test_h1_build_copytrade_export_callable
  test_export_task_is_shared_task
  test_export_task_name_is_copytrade_prefixed
  test_export_trigger_no_active_cohort_returns_400
  test_export_trigger_dispatches_to_celery
  test_export_trigger_returns_task_id_and_queued_status
  test_export_trigger_returns_cohort_id
  test_build_copytrade_export_writes_csv_with_positions
  test_build_copytrade_export_includes_open_and_closed_positions
  test_build_copytrade_export_csv_has_required_columns
  test_build_copytrade_export_returns_manifest
  test_build_copytrade_export_empty_cohort
  test_export_trigger_url_is_wired
  test_export_builder_has_metadata_front_matter
  test_export_tasks_module_has_metadata_front_matter
  test_export_celery_task_never_inline_on_view
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

# ---------------------------------------------------------------------------
# H1 ImportError traps — fail pytest COLLECTION if any named surface is
# deleted or renamed.  Mirrors the standing H1 pattern (AC-11.3, AC-31.1,
# AC-42.3, AC-51.3, AC-56.3, AC-57.1, AC-63.1, AC-63.2).
# ---------------------------------------------------------------------------
from copytrade.api import copytrade_export_trigger_view  # noqa: F401
from copytrade.export_builder import build_copytrade_export  # noqa: F401
from copytrade.tasks import export_copytrade_positions  # noqa: F401

# Fail collection immediately if any surface is missing
assert copytrade_export_trigger_view, "copytrade_export_trigger_view must be importable (H1 guard)"
assert export_copytrade_positions, "export_copytrade_positions must be importable (H1 guard)"
assert build_copytrade_export, "build_copytrade_export must be importable (H1 guard)"

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_BUILDER_PY = REPO_ROOT / "copytrade" / "export_builder.py"
TASKS_PY = REPO_ROOT / "copytrade" / "tasks.py"

# ---------------------------------------------------------------------------
# Fixture constants (mirror AC-63.1 fixture for determinism)
# ---------------------------------------------------------------------------

COHORT_ID = "copytrade-ac633-fixture"
COHORT_CREATED_AT = datetime(2026, 6, 18, 0, 0, 0, tzinfo=timezone.utc)
WALLET_A = "WalletAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_B = "WalletBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MINT_1 = "Mint1111111111111111111111111111111111111111"
MINT_2 = "Mint2222222222222222222222222222222222222222"
MINT_3 = "Mint3333333333333333333333333333333333333333"
ENTRY_TS = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)


def _build_export_fixture():
    """Populate the DB with a minimal copytrade fixture for export tests."""
    from copytrade.models import (
        CopytradeCohort,
        CopytradePosition,
        CopyTradeSettings,
    )

    CopytradeCohort.objects.create(
        cohort_id=COHORT_ID,
        created_at=COHORT_CREATED_AT,
        description="AC-63.3 export test fixture",
        trade_config={},
        active=True,
    )

    # closed TP position
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_1,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=ENTRY_TS,
        exit_price=2.0,
        sol_out=0.50,
        exit_reason=CopytradePosition.EXIT_TP,
        realized_pnl_sol=0.25,
        realized_pnl_pct=100.0,
    )
    # closed SL position
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_2,
        trigger_wallet=WALLET_B,
        status=CopytradePosition.STATUS_CLOSED,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
        exit_ts=ENTRY_TS,
        exit_price=0.5,
        sol_out=0.125,
        exit_reason=CopytradePosition.EXIT_SL,
        realized_pnl_sol=-0.125,
        realized_pnl_pct=-50.0,
    )
    # open position (must be included — SPEC §11)
    CopytradePosition.objects.create(
        cohort_id=COHORT_ID,
        mint=MINT_3,
        trigger_wallet=WALLET_A,
        status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=ENTRY_TS,
        entry_price=1.0,
        sol_in=0.25,
    )

    settings = CopyTradeSettings.get()
    CopyTradeSettings.objects.filter(pk=settings.pk).update(
        active_cohort_id=COHORT_ID,
    )


# ---------------------------------------------------------------------------
# H1 explicit test forms
# ---------------------------------------------------------------------------


def test_h1_copytrade_export_trigger_view_callable():
    """copytrade_export_trigger_view is callable (H1 wiring guard, explicit form)."""
    assert callable(copytrade_export_trigger_view), (
        "copytrade_export_trigger_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_export_copytrade_positions_callable():
    """export_copytrade_positions Celery task is callable (H1 wiring guard)."""
    assert callable(export_copytrade_positions), (
        "export_copytrade_positions must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_build_copytrade_export_callable():
    """build_copytrade_export is callable (H1 wiring guard)."""
    assert callable(build_copytrade_export), (
        "build_copytrade_export must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


# ---------------------------------------------------------------------------
# Celery task surface checks
# ---------------------------------------------------------------------------


def test_export_task_is_shared_task():
    """export_copytrade_positions must be a Celery shared_task (has .delay())."""
    assert hasattr(export_copytrade_positions, "delay"), (
        "export_copytrade_positions must be a Celery @shared_task with a .delay() method. "
        "AC-63.3: the export runs OFF the celery container (#289, NEVER web/gunicorn)."
    )


def test_export_task_name_is_copytrade_prefixed():
    """Celery task name must be 'copytrade.tasks.export_copytrade_positions'."""
    assert export_copytrade_positions.name == "copytrade.tasks.export_copytrade_positions", (
        f"Task name must be 'copytrade.tasks.export_copytrade_positions', "
        f"got '{export_copytrade_positions.name}'. "
        "Named surface required for the task manifest bidirectional equality guard (AC-4.2)."
    )


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_trigger_no_active_cohort_returns_400():
    """POST export/trigger/ with no active cohort returns HTTP 400."""
    from copytrade.models import CopyTradeSettings

    CopyTradeSettings.get()
    CopyTradeSettings.objects.filter(pk=1).update(active_cohort_id=None)

    client = APIClient()
    resp = client.post("/api/copytrade/export/trigger/", data={}, format="json")
    assert resp.status_code == 400
    assert "error" in resp.json()


@pytest.mark.django_db
def test_export_trigger_dispatches_to_celery():
    """POST export/trigger/ dispatches export_copytrade_positions.delay() (NEVER inline)."""
    _build_export_fixture()
    client = APIClient()

    mock_task = MagicMock()
    mock_task.id = "mock-task-id-ac633"

    with patch("copytrade.api.export_copytrade_positions") as mock_fn:
        mock_fn.delay.return_value = mock_task
        resp = client.post("/api/copytrade/export/trigger/", data={}, format="json")

    assert resp.status_code == 200
    mock_fn.delay.assert_called_once_with(
        cohort_id=COHORT_ID,
        dataset_id=f"copytrade_export_{COHORT_ID}",
    )


@pytest.mark.django_db
def test_export_trigger_returns_task_id_and_queued_status():
    """POST export/trigger/ returns task_id and status='queued'."""
    _build_export_fixture()
    client = APIClient()

    mock_task = MagicMock()
    mock_task.id = "test-task-id-xyz"

    with patch("copytrade.api.export_copytrade_positions") as mock_fn:
        mock_fn.delay.return_value = mock_task
        resp = client.post("/api/copytrade/export/trigger/", data={}, format="json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "test-task-id-xyz"
    assert data["status"] == "queued"


@pytest.mark.django_db
def test_export_trigger_returns_cohort_id():
    """POST export/trigger/ response includes the active cohort_id."""
    _build_export_fixture()
    client = APIClient()

    mock_task = MagicMock()
    mock_task.id = "task-abc"

    with patch("copytrade.api.export_copytrade_positions") as mock_fn:
        mock_fn.delay.return_value = mock_task
        resp = client.post("/api/copytrade/export/trigger/", data={}, format="json")

    assert resp.status_code == 200
    assert resp.json()["cohort_id"] == COHORT_ID


# ---------------------------------------------------------------------------
# Celery task body — direct synchronous invocation (covers the task path
# that .delay() dispatches to the celery-worker)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_task_body_writes_export_with_explicit_path(tmp_path):
    """Calling the task body synchronously writes the export at the given path."""
    _build_export_fixture()
    out = str(tmp_path / "task_export.csv")
    result = export_copytrade_positions(COHORT_ID, output_path=out)
    assert result["row_count"] == 3
    assert Path(out).exists()


@pytest.mark.django_db
def test_export_task_body_defaults_output_path_to_tmp():
    """With no output_path the task defaults to /tmp/copytrade_export_<cohort_id>.csv."""
    _build_export_fixture()
    result = export_copytrade_positions(COHORT_ID)
    expected = f"/tmp/copytrade_export_{COHORT_ID}.csv"
    assert result["path"] == expected
    assert Path(expected).exists()


# ---------------------------------------------------------------------------
# build_copytrade_export — functional tests (writes real CSV)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_copytrade_export_writes_csv_with_positions(tmp_path):
    """build_copytrade_export writes a CSV file with the correct row count."""
    _build_export_fixture()
    out = str(tmp_path / "export.csv")
    result = build_copytrade_export(COHORT_ID, out)
    assert result["row_count"] == 3
    assert Path(out).exists()


@pytest.mark.django_db
def test_build_copytrade_export_includes_open_and_closed_positions(tmp_path):
    """Export includes BOTH open and closed positions (SPEC §11 requirement)."""
    import csv

    _build_export_fixture()
    out = str(tmp_path / "export.csv")
    result = build_copytrade_export(COHORT_ID, out)

    with open(out, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    statuses = {r["status"] for r in rows}
    assert "open" in statuses, "Export must include open positions (SPEC §11)"
    assert "closed" in statuses, "Export must include closed positions (SPEC §11)"
    assert result["row_count"] == 3


@pytest.mark.django_db
def test_build_copytrade_export_csv_has_required_columns(tmp_path):
    """CSV must include all copytrade_positions columns for research parity."""
    import csv

    _build_export_fixture()
    out = str(tmp_path / "export.csv")
    build_copytrade_export(COHORT_ID, out)

    with open(out, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames

    required = {
        "id", "cohort_id", "mint", "trigger_wallet", "mode", "status",
        "entry_ts", "entry_price", "sol_in",
        "exit_ts", "exit_price", "sol_out", "exit_reason",
        "realized_pnl_sol", "realized_pnl_pct",
    }
    missing = required - set(fieldnames or [])
    assert not missing, f"CSV is missing required columns: {missing}"


@pytest.mark.django_db
def test_build_copytrade_export_returns_manifest(tmp_path):
    """build_copytrade_export returns a MANIFEST dict following the §6.5 pattern."""
    _build_export_fixture()
    out = str(tmp_path / "export.csv")
    result = build_copytrade_export(COHORT_ID, out, dataset_id="test_ac633")

    assert "manifest" in result
    assert "manifest_path" in result
    manifest = result["manifest"]
    assert manifest["dataset_id"] == "test_ac633"
    assert manifest["source"] == "copytrade_positions"
    assert "content_hash" in manifest
    assert "mint_cohort" in manifest
    assert manifest["row_count"] == 3


@pytest.mark.django_db
def test_build_copytrade_export_empty_cohort(tmp_path):
    """build_copytrade_export with no positions returns row_count=0."""
    from copytrade.models import CopyTradeSettings

    CopyTradeSettings.get()
    out = str(tmp_path / "empty_export.csv")
    result = build_copytrade_export("nonexistent-cohort-id", out)
    assert result["row_count"] == 0
    assert Path(out).exists()


# ---------------------------------------------------------------------------
# Structural checks — metadata front matter + URL wiring
# ---------------------------------------------------------------------------


def test_export_trigger_url_is_wired():
    """The export trigger URL is present in copytrade/urls.py."""
    import copytrade.urls

    url_patterns = [str(p.pattern) for p in copytrade.urls.urlpatterns]
    assert any("export/trigger" in p for p in url_patterns), (
        "copytrade/urls.py does not contain the export/trigger/ URL pattern. "
        "AC-63.3 requires POST /api/copytrade/export/trigger/ to be wired."
    )


def test_export_builder_has_metadata_front_matter():
    """copytrade/export_builder.py must carry the project metadata front matter."""
    assert EXPORT_BUILDER_PY.exists(), f"export_builder.py not found at {EXPORT_BUILDER_PY}"
    src = EXPORT_BUILDER_PY.read_text(encoding="utf-8")
    assert "# ---" in src, (
        "copytrade/export_builder.py is missing the metadata front matter block (# ---). "
        "All new backend files must carry metadata front matter (project convention, sprint DoD)."
    )
    assert "US-63" in src, (
        "copytrade/export_builder.py front matter must reference US-63 (story tag)."
    )


def test_export_tasks_module_has_metadata_front_matter():
    """copytrade/tasks.py must carry the project metadata front matter."""
    assert TASKS_PY.exists(), f"tasks.py not found at {TASKS_PY}"
    src = TASKS_PY.read_text(encoding="utf-8")
    assert "# ---" in src, (
        "copytrade/tasks.py is missing the metadata front matter block (# ---). "
        "All new backend files must carry metadata front matter (project convention, sprint DoD)."
    )
    assert "US-63" in src, (
        "copytrade/tasks.py front matter must reference US-63 (story tag)."
    )


def test_export_celery_task_never_inline_on_view():
    """The export trigger view must NOT call build_copytrade_export directly (#289 guard).

    The view is only allowed to call .delay() — never the task function or the
    export builder inline.  This enforces the §6.5 channel contract: export logic
    runs on the celery-worker container, not on web/gunicorn.

    Uses raw file source rather than inspect.getsource (which returns the DRF
    wrapper's source after @api_view decoration).
    """
    api_path = REPO_ROOT / "copytrade" / "api.py"
    assert api_path.exists(), f"copytrade/api.py not found at {api_path}"
    src = api_path.read_text(encoding="utf-8")

    trigger_fn_start = src.find("def copytrade_export_trigger_view")
    assert trigger_fn_start != -1, "copytrade_export_trigger_view not found in copytrade/api.py"

    trigger_fn_src = src[trigger_fn_start:]
    assert "build_copytrade_export" not in trigger_fn_src, (
        "copytrade_export_trigger_view calls build_copytrade_export directly (#289 violation). "
        "The view must dispatch via .delay() — NEVER run export logic inline on web/gunicorn."
    )
    assert ".delay(" in trigger_fn_src, (
        "copytrade_export_trigger_view must dispatch the task via .delay(). "
        "AC-63.3: export runs OFF the celery container (#289, NEVER web/gunicorn)."
    )
