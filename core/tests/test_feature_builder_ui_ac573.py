# ---
# module: core.tests.test_feature_builder_ui_ac573
# sprint: sprint-11
# story: US-57 AC-57.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: core.export_api, core.export_result_api, core.tasks, ast, pathlib, pytest
# ---
"""AC-57.3 — Feature Builder UI wiring, no-silent-state-change guard, idempotency.

The AC requires:
  - The Feature Builder view renders trigger control + result/MANIFEST in the US-48 React frontend.
  - No silent state change: triggering an export NEVER flips scoring_enabled/trading_enabled
    (US-11 AST guard covers the export path).
  - The export is idempotent (triggering twice leaves PipelineState flags unchanged).
  - Feature Builder tests are wired into the canonical ci.yml 'test' job via ImportError trap
    on the named endpoint/task function (H1) so a deleted/renamed surface fails pytest collection.
  - New backend AND frontend files carry metadata front matter.
  - Deployed + smoke-tested on the VPS (structural guard: smoke step present in deploy.yml).

Tests
-----
H1 ImportError traps (module level — fail pytest COLLECTION if any surface is deleted/renamed):
  from core.export_api import feature_export_trigger_view
  from core.export_result_api import export_result_view
  from core.tasks import build_features

test_h1_feature_export_trigger_view_importable
    feature_export_trigger_view is callable (H1 wiring guard, explicit form).

test_h1_export_result_view_importable
    export_result_view is callable (H1 wiring guard, explicit form).

test_h1_build_features_importable
    build_features has the Celery task interface (H1 wiring guard, explicit form).

AST guard — export_api.py must not flip scoring_enabled/trading_enabled (US-11, AC-57.3):
  test_export_api_does_not_set_scoring_enabled_true
  test_export_api_does_not_set_trading_enabled_true
  test_export_api_does_not_reference_pipeline_state

AST guard — export_result_api.py must not flip scoring_enabled/trading_enabled (US-11):
  test_export_result_api_does_not_set_scoring_enabled_true
  test_export_result_api_does_not_set_trading_enabled_true
  test_export_result_api_does_not_reference_pipeline_state

Frontend wiring (React frontend, US-48, built fresh per §14):
  test_feature_builder_jsx_exists
  test_feature_builder_jsx_has_metadata_front_matter
  test_feature_builder_jsx_has_trigger_control
  test_feature_builder_jsx_has_manifest_surface
  test_app_jsx_imports_feature_builder
  test_app_jsx_routes_features_view_to_feature_builder

Idempotency guard — triggering export twice does not flip PipelineState flags:
  test_export_trigger_idempotent_flags_stay_false

Structural guard — deploy.yml has Feature Builder smoke test step (VPS DoD):
  test_deploy_yml_has_feature_builder_smoke_test
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# H1 ImportError traps — fail pytest COLLECTION if any surface is
# deleted or renamed.  Mirrors the standing H1 pattern (AC-11.3, AC-31.1,
# AC-42.3, AC-51.3, AC-56.3, AC-57.1, AC-57.2).
# ---------------------------------------------------------------------------
from core.export_api import feature_export_trigger_view  # noqa: F401
from core.export_result_api import export_result_view  # noqa: F401
from core.tasks import build_features  # noqa: F401

assert feature_export_trigger_view  # fails collection if None / ImportError
assert export_result_view  # fails collection if None / ImportError
assert build_features  # fails collection if None / ImportError

# ---------------------------------------------------------------------------
# Repo layout helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_API_PY = REPO_ROOT / "core" / "export_api.py"
EXPORT_RESULT_API_PY = REPO_ROOT / "core" / "export_result_api.py"
FEATURE_BUILDER_JSX = REPO_ROOT / "frontend" / "src" / "FeatureBuilder.jsx"
APP_JSX = REPO_ROOT / "frontend" / "src" / "App.jsx"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

_STATE_FLAGS = {"scoring_enabled", "trading_enabled"}


# ---------------------------------------------------------------------------
# AST helper — mirrors the AC-11.3 / AC-42.3 / AC-51.3 / AC-56.2 pattern.
# Scans a file for keyword or attribute assignments of the form
# flag=True or .flag = True where flag is in _STATE_FLAGS.
# ---------------------------------------------------------------------------


def _find_flag_true_assignments(path: Path) -> list[str]:
    """Return descriptions of scoring_enabled=True / trading_enabled=True
    assignments found anywhere in *path*."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: .update(scoring_enabled=True) / create(trading_enabled=True)
        if isinstance(node, ast.keyword):
            if (
                node.arg in _STATE_FLAGS
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                findings.append(
                    f"{rel}: keyword {node.arg}=True at line {node.value.lineno}"
                )

        # attribute assignment: state.scoring_enabled = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in _STATE_FLAGS
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    findings.append(
                        f"{rel}: attribute .{target.attr} = True at line {node.value.lineno}"
                    )

    return findings


# ---------------------------------------------------------------------------
# 1–3. H1 import traps — explicit test form
# ---------------------------------------------------------------------------


def test_h1_feature_export_trigger_view_importable():
    """feature_export_trigger_view is importable and callable (H1 wiring guard)."""
    assert callable(feature_export_trigger_view), (
        "feature_export_trigger_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_export_result_view_importable():
    """export_result_view is importable and callable (H1 wiring guard)."""
    assert callable(export_result_view), (
        "export_result_view must be callable. "
        "If it fails to import, pytest collection fails (H1 pattern)."
    )


def test_h1_build_features_importable():
    """build_features has the Celery task interface (H1 wiring guard)."""
    for attr in ("delay", "apply", "apply_async"):
        assert hasattr(build_features, attr), (
            f"build_features is missing Celery task attribute: {attr!r}. "
            "If it fails to import, pytest collection fails (H1 pattern)."
        )


# ---------------------------------------------------------------------------
# 4–6. AST guard — export_api.py must not flip scoring_enabled/trading_enabled
# (US-11 no-auto-start guard, AC-57.3 extension)
# ---------------------------------------------------------------------------


def test_export_api_does_not_set_scoring_enabled_true():
    """core/export_api.py must not assign scoring_enabled=True anywhere.

    The Feature Builder export trigger must never silently enable scoring
    (the US-11 no-auto-start guard, extended to cover this view path).
    """
    assert EXPORT_API_PY.exists(), f"core/export_api.py not found at {EXPORT_API_PY}"
    findings = _find_flag_true_assignments(EXPORT_API_PY)
    scoring_hits = [f for f in findings if "scoring_enabled" in f]
    assert not scoring_hits, (
        "core/export_api.py sets scoring_enabled=True — this silently auto-starts "
        "scoring (US-11 no-auto-start guard violation). Triggering an export must "
        "NEVER flip scoring_enabled. Findings:\n" + "\n".join(scoring_hits)
    )


def test_export_api_does_not_set_trading_enabled_true():
    """core/export_api.py must not assign trading_enabled=True anywhere.

    The Feature Builder export trigger must never silently enable trading
    (the US-11 no-auto-start guard, extended to cover this view path).
    """
    assert EXPORT_API_PY.exists(), f"core/export_api.py not found at {EXPORT_API_PY}"
    findings = _find_flag_true_assignments(EXPORT_API_PY)
    trading_hits = [f for f in findings if "trading_enabled" in f]
    assert not trading_hits, (
        "core/export_api.py sets trading_enabled=True — this silently auto-starts "
        "trading (US-11 no-auto-start guard violation). Triggering an export must "
        "NEVER flip trading_enabled. Findings:\n" + "\n".join(trading_hits)
    )


def test_export_api_does_not_reference_pipeline_state():
    """core/export_api.py must not import or reference PipelineState.

    The export trigger view writes only to the Celery queue (via .delay()).
    It must never touch pipeline control flags (§5.3/§15.6).
    """
    assert EXPORT_API_PY.exists(), f"core/export_api.py not found at {EXPORT_API_PY}"
    src = EXPORT_API_PY.read_text(encoding="utf-8")
    assert "PipelineState" not in src, (
        "core/export_api.py references PipelineState. The export trigger view "
        "must not touch pipeline control flags (§5.3/§15.6 — no silent auto-start, "
        "US-11 guard)."
    )


# ---------------------------------------------------------------------------
# 7–9. AST guard — export_result_api.py must not flip flags
# ---------------------------------------------------------------------------


def test_export_result_api_does_not_set_scoring_enabled_true():
    """core/export_result_api.py must not assign scoring_enabled=True anywhere."""
    assert EXPORT_RESULT_API_PY.exists(), (
        f"core/export_result_api.py not found at {EXPORT_RESULT_API_PY}"
    )
    findings = _find_flag_true_assignments(EXPORT_RESULT_API_PY)
    scoring_hits = [f for f in findings if "scoring_enabled" in f]
    assert not scoring_hits, (
        "core/export_result_api.py sets scoring_enabled=True — export result polling "
        "must never flip pipeline control flags (US-11 guard). Findings:\n"
        + "\n".join(scoring_hits)
    )


def test_export_result_api_does_not_set_trading_enabled_true():
    """core/export_result_api.py must not assign trading_enabled=True anywhere."""
    assert EXPORT_RESULT_API_PY.exists(), (
        f"core/export_result_api.py not found at {EXPORT_RESULT_API_PY}"
    )
    findings = _find_flag_true_assignments(EXPORT_RESULT_API_PY)
    trading_hits = [f for f in findings if "trading_enabled" in f]
    assert not trading_hits, (
        "core/export_result_api.py sets trading_enabled=True — export result polling "
        "must never flip pipeline control flags (US-11 guard). Findings:\n"
        + "\n".join(trading_hits)
    )


def test_export_result_api_does_not_reference_pipeline_state():
    """core/export_result_api.py must not import or reference PipelineState."""
    assert EXPORT_RESULT_API_PY.exists(), (
        f"core/export_result_api.py not found at {EXPORT_RESULT_API_PY}"
    )
    src = EXPORT_RESULT_API_PY.read_text(encoding="utf-8")
    assert "PipelineState" not in src, (
        "core/export_result_api.py references PipelineState. The result read-through "
        "view must not touch pipeline control flags (US-11 guard)."
    )


# ---------------------------------------------------------------------------
# 10–15. Frontend wiring guards (US-48 React frontend, built fresh per §14)
# ---------------------------------------------------------------------------


def test_feature_builder_jsx_exists():
    """FeatureBuilder.jsx must exist in frontend/src/ (US-48 React frontend, §14)."""
    assert FEATURE_BUILDER_JSX.exists(), (
        f"FeatureBuilder.jsx not found at {FEATURE_BUILDER_JSX}. "
        "The Feature Builder view must be rendered in the US-48 React frontend "
        "(built fresh per §14), not as a server-rendered table."
    )


def test_feature_builder_jsx_has_metadata_front_matter():
    """FeatureBuilder.jsx must carry metadata front matter (project convention)."""
    assert FEATURE_BUILDER_JSX.exists(), (
        f"FeatureBuilder.jsx not found at {FEATURE_BUILDER_JSX}"
    )
    src = FEATURE_BUILDER_JSX.read_text(encoding="utf-8")
    assert "// ---" in src, (
        "FeatureBuilder.jsx is missing the metadata front matter block (// ---). "
        "All new backend AND frontend files must carry metadata front matter "
        "(project convention, sprint DoD)."
    )
    assert "US-57" in src, (
        "FeatureBuilder.jsx front matter must reference US-57 (story tag)."
    )


def test_feature_builder_jsx_has_trigger_control():
    """FeatureBuilder.jsx must contain a trigger control (button for export dispatch)."""
    assert FEATURE_BUILDER_JSX.exists(), (
        f"FeatureBuilder.jsx not found at {FEATURE_BUILDER_JSX}"
    )
    src = FEATURE_BUILDER_JSX.read_text(encoding="utf-8")
    assert "Trigger Export" in src or "triggerExport" in src, (
        "FeatureBuilder.jsx must contain a trigger control (a button that dispatches "
        "the export). The view must render the trigger control per AC-57.3."
    )


def test_feature_builder_jsx_has_manifest_surface():
    """FeatureBuilder.jsx must surface the export MANIFEST (content_hash + manifest panel)."""
    assert FEATURE_BUILDER_JSX.exists(), (
        f"FeatureBuilder.jsx not found at {FEATURE_BUILDER_JSX}"
    )
    src = FEATURE_BUILDER_JSX.read_text(encoding="utf-8")
    assert "manifest" in src.lower(), (
        "FeatureBuilder.jsx must surface the export MANIFEST. The result/MANIFEST "
        "panel is required by AC-57.3."
    )
    assert "content_hash" in src, (
        "FeatureBuilder.jsx must display the content_hash from the MANIFEST "
        "(US-36 pattern — SHA-256 of decompressed bytes, per AC-57.2/57.3)."
    )


def test_app_jsx_imports_feature_builder():
    """App.jsx must import FeatureBuilder (US-48 React frontend routing)."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "FeatureBuilder" in src, (
        "App.jsx does not import FeatureBuilder. The Feature Builder view must be "
        "wired into the US-48 React frontend router (view=features)."
    )


def test_app_jsx_routes_features_view_to_feature_builder():
    """App.jsx must route view=features to the FeatureBuilder component."""
    assert APP_JSX.exists(), f"App.jsx not found at {APP_JSX}"
    src = APP_JSX.read_text(encoding="utf-8")
    assert "'features'" in src or '"features"' in src, (
        "App.jsx does not contain a 'features' view route. "
        "The Feature Builder must be reachable via ?view=features in the US-48 frontend."
    )
    assert "FeatureBuilder" in src, (
        "App.jsx does not render FeatureBuilder anywhere. "
        "The view=features route must render the FeatureBuilder component."
    )


# ---------------------------------------------------------------------------
# 16. Idempotency guard — triggering export twice does not flip PipelineState flags
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_trigger_idempotent_flags_stay_false():
    """Triggering the export twice leaves PipelineState scoring_enabled and
    trading_enabled False on both calls (idempotent, no silent state change).

    This is the core US-11 guard for the Feature Builder path (AC-57.3):
    the export dispatch is purely a Celery task queue operation and must never
    touch the pipeline control flags, regardless of how many times it is called.

    Celery is mocked so the test is OFFLINE (zero firehose, no celery worker).
    """
    from rest_framework.test import APIClient

    from core.models import PipelineConfig, PipelineState

    # Ensure pipeline state exists with flags False
    state, _ = PipelineState.objects.get_or_create(
        id=1,
        defaults={"scoring_enabled": False, "trading_enabled": False},
    )
    state.scoring_enabled = False
    state.trading_enabled = False
    state.save()

    # Create an active config with feature_set_id
    PipelineConfig.objects.filter(is_active=True).update(is_active=False)
    config = PipelineConfig.objects.create(
        version=5731,
        label="ac573-idempotency-test",
        is_active=True,
        feature_set_id=73,
        detection={},
        tape={"idle_kill_ttl_s": 300},
        scoring={"score_at_elapsed_s": 60, "window_s": 120},
        outcome={"window_s": 120, "label_def": {"label_start_s": 120}},
        trading={},
    )

    fake_async_result = MagicMock()
    fake_async_result.id = "test-task-ac573-idempotency"

    client = APIClient()

    with patch("core.tasks.build_features") as mock_task:
        mock_task.delay.return_value = fake_async_result

        # First trigger
        resp1 = client.post("/api/export/trigger/", data={}, format="json")
        # Second trigger (idempotent)
        resp2 = client.post("/api/export/trigger/", data={}, format="json")

    # Both calls must succeed
    assert resp1.status_code == 200, (
        f"First export trigger returned HTTP {resp1.status_code}, expected 200."
    )
    assert resp2.status_code == 200, (
        f"Second export trigger returned HTTP {resp2.status_code}, expected 200. "
        "The export must be idempotent — triggering twice must not cause an error."
    )

    # Both calls must have dispatched Celery tasks (idempotent dispatch)
    assert mock_task.delay.call_count == 2, (
        f"Expected build_features.delay() to be called twice (idempotent), "
        f"got {mock_task.delay.call_count} calls."
    )

    # Reload PipelineState from DB — flags must remain False
    state.refresh_from_db()
    assert state.scoring_enabled is False, (
        "Triggering the export flipped scoring_enabled to True — "
        "the Feature Builder export path MUST NOT flip pipeline state flags "
        "(US-11 no-auto-start guard, AC-57.3). "
        f"scoring_enabled={state.scoring_enabled!r} after export trigger."
    )
    assert state.trading_enabled is False, (
        "Triggering the export flipped trading_enabled to True — "
        "the Feature Builder export path MUST NOT flip pipeline state flags "
        "(US-11 no-auto-start guard, AC-57.3). "
        f"trading_enabled={state.trading_enabled!r} after export trigger."
    )

    # Cleanup
    config.delete()


# ---------------------------------------------------------------------------
# 17. Structural guard — deploy.yml has Feature Builder smoke test (VPS DoD)
# ---------------------------------------------------------------------------


def test_deploy_yml_has_feature_builder_smoke_test():
    """deploy.yml must contain a smoke test step for the US-57 Feature Builder
    export result endpoint (VPS DoD — 'works locally' is NOT done, P6 phase DoD
    requires deployed + smoke-tested on VPS per PRD §15.2/§16).
    """
    assert DEPLOY_YML.exists(), f"deploy.yml not found at {DEPLOY_YML}"
    content = DEPLOY_YML.read_text(encoding="utf-8")
    assert "api/export/result/" in content, (
        "deploy.yml does not contain a smoke test for /api/export/result/. "
        "AC-57.3 requires the Feature Builder endpoint to be deployed and "
        "smoke-tested on the VPS (P6 DoD: 'live on the VPS', PRD §15.2/§16). "
        "Add a smoke-test step: GET /api/export/result/smoke-test-ac573/ → HTTP 200."
    )
