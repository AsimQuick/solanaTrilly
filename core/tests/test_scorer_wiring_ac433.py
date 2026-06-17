# ---
# module: core.tests.test_scorer_wiring_ac433
# sprint: sprint-9
# story: US-43 AC-43.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tasks, core.scorer, core.replay_source, core.pregrad_features,
#   lightgbm, numpy, ast, pathlib, asyncio, pytest
# ---
"""AC-43.3 — Scorer seam wiring: container placement, flag gates, replay-testable path.

The scorer runs behind the seam in the celery-worker container (NOT the
web/gunicorn process — #289).  scoring_enabled gates scoring; trading_enabled
stays separate and False.  The serving path is replay-testable via ReplaySource.

Verifies:
  1. STRUCTURAL WIRING (AST-based): score_token is defined in core/tasks.py
     (the celery-worker entry point, not web/gunicorn).  scoring_enabled is
     referenced in the function body.  trading_enabled is NEVER set True.
     score_token is not imported/called from web-facing modules.
  2. REPLAY-TESTABLE SERVING PATH: BlendScorer + ReferenceDistribution +
     ReplaySource drive the serving path deterministically offline (run-twice
     byte-identical).  trading_enabled default is not mutated by scoring.

H1 ImportError trap — module-level import of score_token; deletion/rename
of the task fails pytest collection before any test runs.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_score_token_importable_ac433

Structural wiring — container placement and flag gates (AST):
  test_score_token_defined_in_tasks_py_ac433
  test_score_token_has_shared_task_decorator_ac433
  test_tasks_py_score_token_checks_scoring_enabled_ac433
  test_tasks_py_score_token_does_not_set_trading_enabled_true_ac433
  test_score_token_not_in_views_py_ac433
  test_score_token_not_in_wsgi_py_ac433
  test_score_token_not_in_asgi_py_ac433

Deterministic serving test via ReplaySource:
  test_serving_path_via_replay_source_scores_all_events_ac433
  test_serving_path_via_replay_source_run_twice_identical_ac433
  test_serving_path_via_replay_source_blend_in_range_ac433

scoring_enabled gate (DB):
  test_score_token_skips_when_scoring_disabled_ac433
  test_score_token_skips_when_no_active_model_ac433

trading_enabled separation:
  test_pipeline_state_trading_enabled_default_false_ac433
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.pregrad_features import PRE_FEATURE_NAMES

# ---------------------------------------------------------------------------
# H1 ImportError trap — deletion/rename of score_token fails pytest collection
# ---------------------------------------------------------------------------
from core.tasks import score_token

assert score_token  # H1: deleting score_token from core.tasks fails collection

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASKS_PY = _REPO_ROOT / "core" / "tasks.py"
_VIEWS_PY = _REPO_ROOT / "core" / "views.py"
_WSGI_PY = _REPO_ROOT / "config" / "wsgi.py"
_ASGI_PY = _REPO_ROOT / "config" / "asgi.py"

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scorer_feature_fixture_ac431.json"
_REF_DIST_FIXTURE = Path(__file__).parent / "fixtures" / "reference_dist_ac432.json"

_LABELS = ["ctrl", "oracle", "liq"]
_N_SEEDS = 5


# ---------------------------------------------------------------------------
# Helpers — train tiny boosters without touching the filesystem or DB
# ---------------------------------------------------------------------------


def _train_tiny_booster(features: list[str], seed: int):
    """Train a minimal in-memory LightGBM booster (4 leaves, 2 rounds)."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    X = rng.standard_normal((40, len(features)))
    y = rng.standard_normal(40)
    dataset = lgb.Dataset(X, y, feature_name=features, free_raw_data=False)
    return lgb.train(
        {"num_leaves": 4, "verbose": -1, "objective": "regression", "seed": seed},
        dataset,
        num_boost_round=2,
    )


def _make_model_entry(labels: list[str] = _LABELS, feature_list: list[str] | None = None):
    """Build a minimal model_entry stub (SimpleNamespace, no DB)."""
    if feature_list is None:
        feature_list = list(PRE_FEATURE_NAMES)
    return SimpleNamespace(
        labels_seeds_manifest={
            "labels": labels,
            "seeds": list(range(_N_SEEDS)),
            "boosters": {
                lbl: [f"boosters/{lbl}_s{s}.txt" for s in range(_N_SEEDS)]
                for lbl in labels
            },
        },
        feature_list=feature_list,
        artifact_dir="",
    )


# ---------------------------------------------------------------------------
# Session-scoped pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tiny_boosters():
    """Session-scoped: train 15 tiny boosters once, reuse across all tests."""
    feature_names = list(PRE_FEATURE_NAMES)
    boosters: dict[str, list] = {}
    seed_counter = 0
    for label in _LABELS:
        label_bsts = []
        for _ in range(_N_SEEDS):
            bst = _train_tiny_booster(feature_names, seed=seed_counter)
            label_bsts.append(bst)
            seed_counter += 1
        boosters[label] = label_bsts
    return boosters


@pytest.fixture(scope="session")
def blend_scorer(tiny_boosters):
    """Session-scoped BlendScorer (no DB required)."""
    from core.scorer import BlendScorer

    model_entry = _make_model_entry()
    return BlendScorer(model_entry, tiny_boosters)


@pytest.fixture(scope="session")
def feature_fixture():
    """Load the AC-43.1 banked feature fixture (5 token feature dicts)."""
    with _FIXTURE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def ref_dist_banked():
    """Load the AC-43.2 banked reference distribution fixture."""
    from core.scorer import ReferenceDistribution

    return ReferenceDistribution.from_file(_REF_DIST_FIXTURE)


# ---------------------------------------------------------------------------
# H1 ImportError trap
# ---------------------------------------------------------------------------


def test_score_token_importable_ac433():
    """score_token is importable from core.tasks (H1 wiring guard)."""
    from core.tasks import score_token as _st

    assert callable(_st)


# ---------------------------------------------------------------------------
# Structural wiring — AST-based container placement and flag gate checks
# ---------------------------------------------------------------------------


def _parse_tasks():
    """Return the AST tree for core/tasks.py."""
    return ast.parse(_TASKS_PY.read_text(encoding="utf-8"), filename=str(_TASKS_PY))


def _find_score_token_node(tree):
    """Return the FunctionDef AST node for score_token, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "score_token":
            return node
    return None


def test_score_token_defined_in_tasks_py_ac433():
    """score_token is defined as a function in core/tasks.py.

    The celery-worker container auto-discovers tasks from core/tasks.py via
    CELERY_AUTODISCOVER_TASKS.  Defining score_token there ensures it runs
    in the worker container, NOT in the web/gunicorn process (#289).
    """
    tree = _parse_tasks()
    node = _find_score_token_node(tree)
    assert node is not None, (
        "score_token is not defined in core/tasks.py — it must live there so "
        "the celery-worker container runs it, NOT web/gunicorn (#289)"
    )


def test_score_token_has_shared_task_decorator_ac433():
    """score_token is decorated with @shared_task (Celery task registration)."""
    tree = _parse_tasks()
    node = _find_score_token_node(tree)
    assert node is not None
    decorators = node.decorator_list
    has_shared_task = any(
        (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "shared_task")
        or (isinstance(d, ast.Name) and d.id == "shared_task")
        for d in decorators
    )
    assert has_shared_task, (
        "score_token must have @shared_task so the celery-worker can register "
        "and execute it — not on web/gunicorn (#289)"
    )


def test_tasks_py_score_token_checks_scoring_enabled_ac433():
    """score_token's body references scoring_enabled (the operator-controlled gate).

    §5.3/§15.6: only an explicit operator action may start scoring.  The task
    must gate on PipelineState.scoring_enabled before calling the scorer.
    """
    source = _TASKS_PY.read_text(encoding="utf-8")
    tree = _parse_tasks()
    node = _find_score_token_node(tree)
    assert node is not None
    func_source = ast.get_source_segment(source, node)
    assert func_source is not None
    assert "scoring_enabled" in func_source, (
        "score_token must check scoring_enabled before scoring (§5.3/§15.6 gate) — "
        "the flag is not referenced in the function body"
    )


def test_tasks_py_score_token_does_not_set_trading_enabled_true_ac433():
    """score_token never sets trading_enabled=True (scoring and trading are separate).

    §5.3/§15.6: trading_enabled must remain False until the operator explicitly
    enables it.  The scoring task must not auto-enable trading.
    """
    source = _TASKS_PY.read_text(encoding="utf-8")
    tree = _parse_tasks()
    node = _find_score_token_node(tree)
    assert node is not None
    func_source = ast.get_source_segment(source, node)
    assert func_source is not None
    assert "trading_enabled=True" not in func_source, (
        "score_token sets trading_enabled=True — scoring must not auto-enable trading"
    )
    assert "trading_enabled = True" not in func_source, (
        "score_token sets trading_enabled = True — scoring must not auto-enable trading"
    )


def test_score_token_not_in_views_py_ac433():
    """score_token is not imported or called in core/views.py (not in web/gunicorn)."""
    if not _VIEWS_PY.exists():
        pytest.skip("core/views.py does not exist")
    source = _VIEWS_PY.read_text(encoding="utf-8")
    assert "score_token" not in source, (
        "score_token must NOT appear in core/views.py — "
        "scoring runs in the worker container, not the web process (#289)"
    )


def test_score_token_not_in_wsgi_py_ac433():
    """score_token is not imported or called in config/wsgi.py (not in web process)."""
    if not _WSGI_PY.exists():
        pytest.skip("config/wsgi.py does not exist")
    source = _WSGI_PY.read_text(encoding="utf-8")
    assert "score_token" not in source, (
        "score_token must NOT appear in config/wsgi.py — "
        "scoring runs in the worker container, not the gunicorn web server (#289)"
    )


def test_score_token_not_in_asgi_py_ac433():
    """score_token is not imported or called in config/asgi.py (not in web process)."""
    if not _ASGI_PY.exists():
        pytest.skip("config/asgi.py does not exist")
    source = _ASGI_PY.read_text(encoding="utf-8")
    assert "score_token" not in source, (
        "score_token must NOT appear in config/asgi.py — "
        "scoring runs in the worker container, not the ASGI web server (#289)"
    )


# ---------------------------------------------------------------------------
# Replay-testable serving path
# ---------------------------------------------------------------------------


def _run_serving_path_via_replay(feature_fixture, scorer_obj, ref_dist_obj):
    """Drive the serving path through ReplaySource (the replay-testable seam).

    Constructs score events from the feature fixture, loads them into a
    ReplaySource, then asynchronously consumes events and scores each token
    via BlendScorer.score_single(features, ref_dist).

    This mirrors the live serving path:
      live DataSource → graduation event → FeatureExtractor (US-30) → score_single

    In the deterministic replay path, ReplaySource replaces the live DataSource;
    pre-extracted features replace the live FeatureExtractor call.  The scorer
    itself is identical in both paths (same BlendScorer, same ReferenceDistribution).

    Returns list of {"mint": str, "score": dict} in event order.
    """
    from core.replay_source import ReplaySource

    events = [
        {"mint": f"REPLAY_{i}", "features": feat}
        for i, feat in enumerate(feature_fixture)
    ]
    source = ReplaySource(event_log=events)
    results = []

    async def _consume():
        async for event in source.events():
            score = scorer_obj.score_single(event["features"], ref_dist_obj)
            results.append({"mint": event["mint"], "score": score})

    asyncio.run(_consume())
    return results


def test_serving_path_via_replay_source_scores_all_events_ac433(
    blend_scorer, feature_fixture, ref_dist_banked
):
    """ReplaySource serving path scores every event in the banked fixture."""
    results = _run_serving_path_via_replay(feature_fixture, blend_scorer, ref_dist_banked)
    assert len(results) == len(feature_fixture), (
        f"Expected {len(feature_fixture)} results from ReplaySource, got {len(results)} "
        "(AC-43.3 replay path must score all events)"
    )


def test_serving_path_via_replay_source_run_twice_identical_ac433(
    blend_scorer, feature_fixture, ref_dist_banked
):
    """Running the ReplaySource serving path twice yields byte-identical results.

    This is the deterministic serving test required by AC-43.3: the replay path
    produces identical results on every run (no randomness, no side-effects).
    """
    results1 = _run_serving_path_via_replay(feature_fixture, blend_scorer, ref_dist_banked)
    results2 = _run_serving_path_via_replay(feature_fixture, blend_scorer, ref_dist_banked)
    assert results1 == results2, (
        "ReplaySource serving path is not deterministic: two runs over the same "
        "banked fixture produced different results (AC-43.3 determinism gate)"
    )


def test_serving_path_via_replay_source_blend_in_range_ac433(
    blend_scorer, feature_fixture, ref_dist_banked
):
    """All blend_scores from the ReplaySource path are in (0, 1]."""
    results = _run_serving_path_via_replay(feature_fixture, blend_scorer, ref_dist_banked)
    for r in results:
        blend = r["score"]["blend_score"]
        assert 0.0 < blend <= 1.0, (
            f"blend_score {blend} out of (0, 1] for mint {r['mint']!r}"
        )


# ---------------------------------------------------------------------------
# scoring_enabled gate — DB tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_score_token_skips_when_scoring_disabled_ac433():
    """score_token returns skipped when PipelineState.scoring_enabled is False.

    §5.3/§15.6: scoring must not run until the operator explicitly enables it.
    The default state (scoring_enabled=False) must cause the task to skip.
    """
    from core.models import PipelineState

    state = PipelineState.get()
    state.scoring_enabled = False
    state.save()

    result = score_token.apply(args=["MINT_A", {"pre_net_vol_t0": 1.0}]).get()
    assert result.get("skipped") is True
    assert "scoring_enabled" in result.get("reason", "")


@pytest.mark.django_db
def test_score_token_skips_when_no_active_model_ac433():
    """score_token returns skipped when scoring_enabled=True but no active model.

    Before a model is promoted (promote_blend()), the registry is empty.
    The task must skip gracefully rather than raise.
    """
    from core.models import ModelRegistry, PipelineState

    state = PipelineState.get()
    state.scoring_enabled = True
    state.save()

    # Ensure no active model
    ModelRegistry.objects.filter(is_active=True).update(is_active=False)

    result = score_token.apply(args=["MINT_B", {"pre_net_vol_t0": 1.0}]).get()
    assert result.get("skipped") is True
    assert "model" in result.get("reason", "").lower()

    # Restore
    state.scoring_enabled = False
    state.save()


# ---------------------------------------------------------------------------
# trading_enabled separation
# ---------------------------------------------------------------------------


def test_pipeline_state_trading_enabled_default_false_ac433():
    """PipelineState.trading_enabled defaults to False (never auto-enabled by scoring).

    The scoring path (score_token task + BlendScorer.score_single) must never
    set trading_enabled True.  This test locks the default at the model level.
    """
    from core.models import PipelineState

    field = PipelineState._meta.get_field("trading_enabled")
    assert field.default is False, (
        "PipelineState.trading_enabled must default to False — "
        "trading is a separate explicit operator action (§5.3/§15.6)"
    )
