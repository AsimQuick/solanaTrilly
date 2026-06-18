# ---
# module: core.tests.test_export_result_ac572
# sprint: sprint-11
# story: US-57 AC-57.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.export_result_api, celery, pytest
# ---
"""AC-57.2 — Feature Builder UI: export result view surfaces MANIFEST from banked fixture.

The AC requires:
  - A GET endpoint reads back the export RESULT from the Celery result backend
  - The UI surfaces: MANIFEST + content hash, run/status indicator
  - Verified by pytest over a banked export fixture:
      - MANIFEST + content hash matches the produced dataset
      - Deterministic (run-twice identical)
      - OFFLINE (zero firehose — Celery result is mocked)

Tests
-----
H1 ImportError trap — module-level wiring guard:
  (import) export_result_view from core.export_result_api

test_export_result_view_importable
    The view is importable and the URL is registered in core/urls.py.

test_export_result_view_requires_get
    POST /api/export/result/<task_id>/ returns HTTP 405.

test_export_result_view_pending_state
    Mock AsyncResult state=PENDING → status="queued", manifest=None, OFFLINE.

test_export_result_view_running_state
    Mock AsyncResult state=STARTED → status="running", OFFLINE.

test_export_result_view_failed_state
    Mock AsyncResult state=FAILURE → status="failed", error present, OFFLINE.

test_export_result_view_complete_reports_manifest_from_banked_fixture
    KEY TEST: loads banked fixture CSV + manifest, verifies content_hash == SHA-256,
    mocks AsyncResult state=SUCCESS with fake_task_result, calls endpoint twice,
    asserts run1==run2 (deterministic), asserts status=="complete", asserts all
    manifest fields match the banked fixture — OFFLINE (zero firehose).

test_export_result_manifest_keys_present
    Same mock as above — all required manifest keys are present in the response.

test_export_result_url_registered
    core/urls.py source contains export_result_view and api/export/result/.

test_export_result_reads_from_task_result_not_reimplemented
    AST: core/export_result_api.py does NOT import build_features_core,
    FeatureExtractor, or extract_from_lake (no re-implementation of export math).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

# ---------------------------------------------------------------------------
# H1 ImportError trap — fail pytest COLLECTION if view is removed/renamed
# ---------------------------------------------------------------------------
from core.export_result_api import export_result_view  # noqa: F401

assert export_result_view  # fails collection if None / ImportError

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_RESULT_API_PATH = REPO_ROOT / "core" / "export_result_api.py"
URLS_PATH = REPO_ROOT / "core" / "urls.py"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "export_fixture_ac572"
FIXTURE_CSV = FIXTURE_DIR / "features_1.csv"
FIXTURE_MANIFEST = FIXTURE_DIR / "features_1.manifest.json"

FAKE_TASK_ID = "test-task-id-ac572"


# ---------------------------------------------------------------------------
# 1. View is importable + URL is registered
# ---------------------------------------------------------------------------


def test_export_result_view_importable():
    """The view is importable and core/urls.py registers it."""
    # The H1 trap above already ensures importability — this test pins the URL wiring.
    assert URLS_PATH.exists(), f"core/urls.py not found at {URLS_PATH}"
    src = URLS_PATH.read_text(encoding="utf-8")
    assert "export_result_view" in src, (
        "core/urls.py must import export_result_view (H1 ImportError trap: "
        "deletion/rename fails pytest collection)."
    )
    assert "api/export/result/" in src, (
        "core/urls.py must register the path 'api/export/result/<task_id>/'."
    )


# ---------------------------------------------------------------------------
# 2. Endpoint rejects POST (DRF @api_view(["GET"]) behaviour)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_view_requires_get():
    """POST /api/export/result/<task_id>/ must return HTTP 405 Method Not Allowed."""
    client = APIClient()
    resp = client.post("/api/export/result/some-task-id/")
    assert resp.status_code == 405, (
        f"Expected 405 for POST on /api/export/result/some-task-id/, got {resp.status_code}. "
        "The endpoint must only accept GET."
    )


# ---------------------------------------------------------------------------
# 3. PENDING state → status="queued", manifest=None
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_view_pending_state():
    """Mock AsyncResult state=PENDING → response has status='queued' and manifest=None. OFFLINE."""
    with patch("celery.result.AsyncResult") as mock_ar_cls:
        mock_ar = MagicMock()
        mock_ar.state = "PENDING"
        mock_ar.result = None
        mock_ar_cls.return_value = mock_ar

        client = APIClient()
        resp = client.get(f"/api/export/result/{FAKE_TASK_ID}/")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}."
    data = resp.json()
    assert data["status"] == "queued", (
        f"Expected status='queued' for PENDING state, got {data['status']!r}."
    )
    assert data["manifest"] is None, (
        f"Expected manifest=null for queued state, got {data['manifest']!r}."
    )
    assert data["task_id"] == FAKE_TASK_ID


# ---------------------------------------------------------------------------
# 4. STARTED state → status="running"
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_view_running_state():
    """Mock AsyncResult state=STARTED → response has status='running'. OFFLINE."""
    with patch("celery.result.AsyncResult") as mock_ar_cls:
        mock_ar = MagicMock()
        mock_ar.state = "STARTED"
        mock_ar.result = None
        mock_ar_cls.return_value = mock_ar

        client = APIClient()
        resp = client.get(f"/api/export/result/{FAKE_TASK_ID}/")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}."
    data = resp.json()
    assert data["status"] == "running", (
        f"Expected status='running' for STARTED state, got {data['status']!r}."
    )
    assert data["manifest"] is None


# ---------------------------------------------------------------------------
# 5. FAILURE state → status="failed", error present
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_view_failed_state():
    """Mock AsyncResult state=FAILURE → status='failed' and error is present. OFFLINE."""
    with patch("celery.result.AsyncResult") as mock_ar_cls:
        mock_ar = MagicMock()
        mock_ar.state = "FAILURE"
        mock_ar.result = Exception("task failed")
        mock_ar_cls.return_value = mock_ar

        client = APIClient()
        resp = client.get(f"/api/export/result/{FAKE_TASK_ID}/")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}."
    data = resp.json()
    assert data["status"] == "failed", (
        f"Expected status='failed' for FAILURE state, got {data['status']!r}."
    )
    assert data["error"] is not None, "Expected error field to be populated for failed state."
    assert data["manifest"] is None


# ---------------------------------------------------------------------------
# 6. KEY TEST: complete state — MANIFEST from banked fixture, deterministic, OFFLINE
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_view_complete_reports_manifest_from_banked_fixture():
    """KEY TEST (AC-57.2): Complete task reports MANIFEST + content_hash from banked fixture.

    - Loads banked fixture CSV and manifest from core/tests/fixtures/export_fixture_ac572/
    - Computes SHA-256 of fixture CSV bytes
    - Asserts fixture manifest content_hash == computed SHA-256 (fixture integrity check)
    - Mocks AsyncResult state=SUCCESS with fake_task_result containing the fixture manifest
    - Calls GET /api/export/result/ TWICE — asserts run1 == run2 (deterministic)
    - Asserts status="complete", manifest.content_hash == computed SHA-256
    - Asserts all fixture manifest fields are returned correctly
    - OFFLINE: zero firehose (Celery result mocked)
    """
    assert FIXTURE_CSV.exists(), f"Banked fixture CSV not found: {FIXTURE_CSV}"
    assert FIXTURE_MANIFEST.exists(), f"Banked fixture manifest not found: {FIXTURE_MANIFEST}"

    # Load fixture
    csv_bytes = FIXTURE_CSV.read_bytes()
    fixture_manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))

    # Compute SHA-256 of the fixture CSV bytes
    computed_hash = hashlib.sha256(csv_bytes).hexdigest()

    # Verify fixture integrity: the banked manifest's content_hash must match
    assert fixture_manifest["content_hash"] == computed_hash, (
        f"Fixture integrity failure: manifest content_hash={fixture_manifest['content_hash']!r} "
        f"does not match SHA-256 of fixture CSV={computed_hash!r}. "
        "Regenerate the fixture manifest with the correct hash."
    )

    # Build fake task result (as build_features_core would return)
    fake_task_result = {
        "path": str(FIXTURE_CSV),
        "manifest": fixture_manifest,
        "manifest_path": str(FIXTURE_MANIFEST),
        "row_count": 2,
    }

    with patch("celery.result.AsyncResult") as mock_ar_cls:
        mock_ar = MagicMock()
        mock_ar.state = "SUCCESS"
        mock_ar.result = fake_task_result
        mock_ar_cls.return_value = mock_ar

        client = APIClient()
        resp1 = client.get(f"/api/export/result/{FAKE_TASK_ID}/")
        resp2 = client.get(f"/api/export/result/{FAKE_TASK_ID}/")

    assert resp1.status_code == 200, f"run1: Expected 200, got {resp1.status_code}."
    assert resp2.status_code == 200, f"run2: Expected 200, got {resp2.status_code}."

    run1 = resp1.json()
    run2 = resp2.json()

    # Deterministic: run-twice identical
    assert run1 == run2, (
        "Export result endpoint is NOT deterministic: run1 != run2.\n"
        f"run1={run1}\nrun2={run2}"
    )

    data = run1
    assert data["status"] == "complete", (
        f"Expected status='complete' for SUCCESS state, got {data['status']!r}."
    )

    # Content hash matches the produced dataset
    assert data["manifest"]["content_hash"] == computed_hash, (
        f"manifest.content_hash={data['manifest']['content_hash']!r} does not match "
        f"computed SHA-256={computed_hash!r}. "
        "The view must return the content_hash as stored by build_features_core."
    )

    # Row count
    assert data["manifest"]["row_count"] == 2, (
        f"Expected manifest.row_count=2, got {data['manifest']['row_count']!r}."
    )

    # Date range
    assert data["manifest"]["date_range"] == {"start": "2025-01-01", "end": "2025-01-31"}, (
        f"Unexpected date_range: {data['manifest']['date_range']!r}."
    )

    # Feature set version
    assert data["manifest"]["feature_set_version"] == "v1-fixture-ac572", (
        f"Expected feature_set_version='v1-fixture-ac572', "
        f"got {data['manifest']['feature_set_version']!r}."
    )

    # Mint cohort
    expected_cohort = ["FIXTURE_MINT_AC572_AAAA1111", "FIXTURE_MINT_AC572_BBBB2222"]
    assert data["manifest"]["mint_cohort"] == expected_cohort, (
        f"Expected mint_cohort={expected_cohort!r}, got {data['manifest']['mint_cohort']!r}."
    )

    # task_id is echoed
    assert data["task_id"] == FAKE_TASK_ID


# ---------------------------------------------------------------------------
# 7. All required manifest keys are present
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_result_manifest_keys_present():
    """Complete task response must include all required manifest keys."""
    assert FIXTURE_CSV.exists(), f"Banked fixture CSV not found: {FIXTURE_CSV}"
    assert FIXTURE_MANIFEST.exists(), f"Banked fixture manifest not found: {FIXTURE_MANIFEST}"

    fixture_manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    fake_task_result = {
        "path": str(FIXTURE_CSV),
        "manifest": fixture_manifest,
        "manifest_path": str(FIXTURE_MANIFEST),
        "row_count": 2,
    }

    with patch("celery.result.AsyncResult") as mock_ar_cls:
        mock_ar = MagicMock()
        mock_ar.state = "SUCCESS"
        mock_ar.result = fake_task_result
        mock_ar_cls.return_value = mock_ar

        client = APIClient()
        resp = client.get(f"/api/export/result/{FAKE_TASK_ID}/")

    assert resp.status_code == 200
    data = resp.json()
    manifest = data["manifest"]
    assert manifest is not None, "manifest must be populated for complete state."

    required_keys = {
        "content_hash",
        "date_range",
        "feature_set_hash",
        "feature_set_version",
        "label_def",
        "mint_cohort",
        "row_count",
        "sources",
    }
    missing = required_keys - set(manifest.keys())
    assert not missing, (
        f"Required manifest keys missing from response: {sorted(missing)}. "
        "The view must return all manifest fields from the task result."
    )


# ---------------------------------------------------------------------------
# 8. URL is registered in core/urls.py
# ---------------------------------------------------------------------------


def test_export_result_url_registered():
    """core/urls.py must import export_result_view and register api/export/result/."""
    assert URLS_PATH.exists(), f"core/urls.py not found at {URLS_PATH}"
    src = URLS_PATH.read_text(encoding="utf-8")
    assert "export_result_view" in src, (
        "core/urls.py must import export_result_view."
    )
    assert "api/export/result/" in src, (
        "core/urls.py must register the path 'api/export/result/<task_id>/'."
    )


# ---------------------------------------------------------------------------
# 9. AST: export_result_api.py does NOT re-implement export math
# ---------------------------------------------------------------------------


def test_export_result_reads_from_task_result_not_reimplemented():
    """core/export_result_api.py must NOT import build_features_core, FeatureExtractor,
    or extract_from_lake — the view is a pure read-through of the task result.
    """
    assert EXPORT_RESULT_API_PATH.exists(), (
        f"core/export_result_api.py not found at {EXPORT_RESULT_API_PATH}"
    )
    src = EXPORT_RESULT_API_PATH.read_text(encoding="utf-8")
    # Parse AST to check imports and calls
    tree = ast.parse(src, filename=str(EXPORT_RESULT_API_PATH))

    forbidden = {"build_features_core", "FeatureExtractor", "extract_from_lake"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            raise AssertionError(
                f"core/export_result_api.py references '{node.id}' — "
                "this is a re-implementation of export math in the view layer. "
                "The view must only read from AsyncResult.result (no new math, Principle #2)."
            )
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            raise AssertionError(
                f"core/export_result_api.py references attribute '{node.attr}' — "
                "this is a re-implementation of export math in the view layer."
            )
