# ---
# module: core.tests.test_no_auto_start_promote_ac423
# sprint: sprint-9
# story: US-42 AC-42.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: tools.promote_model, core.models, core.resolver, ast, pathlib, pytest
# ---
"""AC-42.3 — No silent auto-promote / no silent trading auto-start.

Promotion is an explicit, audited, idempotent action.  scoring_enabled and
trading_enabled remain defaulted False and NO promote/registry/resolver path
flips them True.  The US-11 AST no-auto-start guard (AC-11.3) is extended
to the promote path.

§5.3 / §15.6: only explicit operator actions may set scoring_enabled or
trading_enabled to True.  Promotion registers a model artifact and nothing
more — it does not start scoring, it does not start trading.

Test structure
--------------
H1 ImportError trap (wiring guard):
  test_promote_blend_importable_ac423
  test_feature_contract_error_importable_ac423

AST guard — promote path never sets scoring/trading flags (extends AC-11.3):
  test_promote_model_py_does_not_set_scoring_enabled_true
  test_promote_model_py_does_not_set_trading_enabled_true
  test_resolver_activate_model_does_not_set_scoring_enabled_true
  test_resolver_activate_model_does_not_set_trading_enabled_true
  test_promote_path_files_do_not_reference_pipeline_state

DB-backed — promotion leaves PipelineState unchanged:
  test_promote_blend_does_not_set_scoring_enabled
  test_promote_blend_does_not_set_trading_enabled
  test_promote_blend_creates_inactive_registry_entry
  test_activate_model_does_not_set_scoring_enabled
  test_activate_model_does_not_set_trading_enabled

Idempotency — re-running the promoter is safe:
  test_promote_blend_idempotent_flags_stay_false_on_second_call
  test_promote_blend_idempotent_both_rows_inactive
"""
import ast
import json
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — these imports must succeed; deletion/rename fails
# pytest collection before any test runs, enforcing the wiring contract.
# ---------------------------------------------------------------------------
from tools.promote_model import FeatureContractError, promote_blend  # noqa: F401

from core.pregrad_features import PRE_FEATURE_NAMES

# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTE_MODEL_PY = REPO_ROOT / "tools" / "promote_model.py"
RESOLVER_PY = REPO_ROOT / "core" / "resolver.py"

_STATE_FLAGS = {"scoring_enabled", "trading_enabled"}
_LABELS = ["ctrl", "oracle", "liq"]
_SEEDS = list(range(5))


# ---------------------------------------------------------------------------
# Helpers — AST flag-True-assignment scanner (mirrors AC-11.3 pattern)
# ---------------------------------------------------------------------------


def _find_flag_true_assignments(path: Path) -> list[str]:
    """Return descriptions of scoring_enabled=True / trading_enabled=True
    assignments found anywhere in *path*."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings = []

    for node in ast.walk(tree):
        # keyword arg: .update(scoring_enabled=True) / objects.create(scoring_enabled=True)
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
# Fixture — faithful v3.2 BLEND artifact (15 tiny LightGBM boosters)
#
# session scope: trained once, reused by all DB-backed tests in this file.
# ---------------------------------------------------------------------------


def _train_tiny_booster(features: list[str], seed: int, path: str) -> None:
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    X = rng.standard_normal((30, len(features)))
    y = rng.standard_normal(30)
    train_data = lgb.Dataset(X, y, feature_name=features, free_raw_data=False)
    bst = lgb.train(
        {"num_leaves": 4, "verbose": -1, "objective": "regression", "seed": seed},
        train_data,
        num_boost_round=2,
    )
    bst.save_model(path)


@pytest.fixture(scope="session")
def v32_dir(tmp_path_factory):
    """Session-scoped faithful v3.2 BLEND fixture: 15 boosters + meta.json."""
    tmp = tmp_path_factory.mktemp("ac423_v32")
    (tmp / "boosters").mkdir()

    meta = {
        "name": "trilly_pregrad_v3_2_ac423",
        "features": PRE_FEATURE_NAMES,
        "labels": {
            "ctrl": "clip(tr30_t1800_25,-100,1000)",
            "oracle": "clip(oracle_25,-100,1000)",
            "liq": "log1p(cumbv_peak)",
        },
        "selection_recipe": "rank_average_3label_5seed",
        "feature_set": "enrich20_buyer_cohort",
    }
    (tmp / "meta.json").write_text(json.dumps(meta))

    counter = 0
    for label in _LABELS:
        for seed in _SEEDS:
            _train_tiny_booster(
                list(PRE_FEATURE_NAMES),
                seed=counter,
                path=str(tmp / "boosters" / f"{label}_s{seed}.txt"),
            )
            counter += 1

    return tmp


# ---------------------------------------------------------------------------
# H1 ImportError trap tests
# (The module-level imports already enforce this; these make it explicit.)
# ---------------------------------------------------------------------------


def test_promote_blend_importable_ac423():
    """promote_blend is importable from tools.promote_model (H1 wiring guard)."""
    from tools.promote_model import promote_blend as _pb

    assert callable(_pb)


def test_feature_contract_error_importable_ac423():
    """FeatureContractError is importable from tools.promote_model (H1 wiring guard)."""
    from tools.promote_model import FeatureContractError as _fce

    assert issubclass(_fce, Exception)


# ---------------------------------------------------------------------------
# AST guard — promote_model.py must not set scoring/trading flags (AC-11.3 ext.)
# ---------------------------------------------------------------------------


def test_promote_model_py_does_not_set_scoring_enabled_true():
    """tools/promote_model.py must not contain scoring_enabled=True anywhere.

    Extends the US-11 AC-11.3 AST guard to the promote path: promotion is an
    explicit, audited action that registers a model artifact only — it must
    never auto-start scoring (§5.3/§15.6).
    """
    violations = [
        v
        for v in _find_flag_true_assignments(PROMOTE_MODEL_PY)
        if "scoring_enabled" in v
    ]
    assert not violations, (
        "tools/promote_model.py sets scoring_enabled=True — promotion must not "
        "auto-start scoring (§5.3/§15.6):\n" + "\n".join(violations)
    )


def test_promote_model_py_does_not_set_trading_enabled_true():
    """tools/promote_model.py must not contain trading_enabled=True anywhere.

    Promotion registers a model artifact and nothing more — it must never
    auto-start trading (§5.3/§15.6).
    """
    violations = [
        v
        for v in _find_flag_true_assignments(PROMOTE_MODEL_PY)
        if "trading_enabled" in v
    ]
    assert not violations, (
        "tools/promote_model.py sets trading_enabled=True — promotion must not "
        "auto-start trading (§5.3/§15.6):\n" + "\n".join(violations)
    )


def test_resolver_activate_model_does_not_set_scoring_enabled_true():
    """core/resolver.py must not set scoring_enabled=True (extends AC-11.3).

    activate_model() flips the is_active flag on ModelRegistry only — it must
    not touch PipelineState.scoring_enabled.
    """
    violations = [
        v for v in _find_flag_true_assignments(RESOLVER_PY) if "scoring_enabled" in v
    ]
    assert not violations, (
        "core/resolver.py sets scoring_enabled=True — activate_model() must not "
        "auto-enable scoring:\n" + "\n".join(violations)
    )


def test_resolver_activate_model_does_not_set_trading_enabled_true():
    """core/resolver.py must not set trading_enabled=True (extends AC-11.3).

    activate_model() flips the is_active flag on ModelRegistry only — it must
    not touch PipelineState.trading_enabled.
    """
    violations = [
        v for v in _find_flag_true_assignments(RESOLVER_PY) if "trading_enabled" in v
    ]
    assert not violations, (
        "core/resolver.py sets trading_enabled=True — activate_model() must not "
        "auto-enable trading:\n" + "\n".join(violations)
    )


def test_promote_path_files_do_not_reference_pipeline_state():
    """tools/promote_model.py must not import or reference PipelineState.

    The promote path registers model artifacts in ModelRegistry only; pipeline
    control flags are managed exclusively by explicit operator actions, not by
    the promoter.  Mirrors the AC-11.3 guard on resolver.py.
    """
    source = PROMOTE_MODEL_PY.read_text(encoding="utf-8")
    assert "PipelineState" not in source, (
        "tools/promote_model.py references PipelineState — the promoter must "
        "not touch pipeline control flags.  Promotion records a model artifact "
        "only; scoring/trading enablement is a separate explicit operator action."
    )


# ---------------------------------------------------------------------------
# DB-backed — promotion leaves PipelineState flags unchanged
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_promote_blend_does_not_set_scoring_enabled(v32_dir):
    """promote_blend() must not set PipelineState.scoring_enabled=True."""
    from core.models import PipelineState

    state = PipelineState.get()
    assert state.scoring_enabled is False  # baseline

    promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )

    state.refresh_from_db()
    assert state.scoring_enabled is False, (
        "promote_blend() must not enable scoring — scoring_enabled was flipped True"
    )


@pytest.mark.django_db
def test_promote_blend_does_not_set_trading_enabled(v32_dir):
    """promote_blend() must not set PipelineState.trading_enabled=True."""
    from core.models import PipelineState

    state = PipelineState.get()
    assert state.trading_enabled is False  # baseline

    promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )

    state.refresh_from_db()
    assert state.trading_enabled is False, (
        "promote_blend() must not enable trading — trading_enabled was flipped True"
    )


@pytest.mark.django_db
def test_promote_blend_creates_inactive_registry_entry(v32_dir):
    """promote_blend() creates a ModelRegistry row with is_active=False.

    Promotion records the artifact without activating it — a separate explicit
    activate_model() call is required (audited, operator-driven).
    """
    entry = promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )
    assert entry.is_active is False, (
        "ModelRegistry row created by promote_blend() must have is_active=False; "
        "activation is a separate explicit operator step"
    )


@pytest.mark.django_db
def test_activate_model_does_not_set_scoring_enabled(v32_dir):
    """activate_model() must not set PipelineState.scoring_enabled=True.

    Activating a model makes it the active registry entry — it must NOT
    auto-start the scoring pipeline (§5.3/§15.6).
    """
    from core.models import PipelineState
    from core.resolver import activate_model

    entry = promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )

    state = PipelineState.get()
    activate_model(entry.pk)

    state.refresh_from_db()
    assert state.scoring_enabled is False, (
        "activate_model() must not enable scoring — scoring_enabled was flipped True"
    )


@pytest.mark.django_db
def test_activate_model_does_not_set_trading_enabled(v32_dir):
    """activate_model() must not set PipelineState.trading_enabled=True.

    Activating a model makes it the active registry entry — it must NOT
    auto-start the trading pipeline (§5.3/§15.6).
    """
    from core.models import PipelineState
    from core.resolver import activate_model

    entry = promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )

    state = PipelineState.get()
    activate_model(entry.pk)

    state.refresh_from_db()
    assert state.trading_enabled is False, (
        "activate_model() must not enable trading — trading_enabled was flipped True"
    )


# ---------------------------------------------------------------------------
# Idempotency — re-running the promoter is safe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_promote_blend_idempotent_flags_stay_false_on_second_call(v32_dir):
    """Re-running promote_blend() twice leaves PipelineState flags False both times.

    Idempotency contract: repeated promotion calls must not produce side effects
    on PipelineState (§5.3/§15.6).
    """
    from core.models import PipelineState

    state = PipelineState.get()

    for call_n in (1, 2):
        promote_blend(
            artifact_dir=v32_dir,
            feature_set_columns=list(PRE_FEATURE_NAMES),
            feature_set_live_servable=list(PRE_FEATURE_NAMES),
        )
        state.refresh_from_db()
        assert state.scoring_enabled is False, (
            f"scoring_enabled was flipped True on promote_blend() call #{call_n}"
        )
        assert state.trading_enabled is False, (
            f"trading_enabled was flipped True on promote_blend() call #{call_n}"
        )


@pytest.mark.django_db
def test_promote_blend_idempotent_both_rows_inactive(v32_dir):
    """Re-running promote_blend() twice creates two inactive rows, neither active.

    The promoter does not auto-activate any row — both calls produce an
    is_active=False ModelRegistry entry, with no active model unless the
    operator explicitly calls activate_model().
    """
    from core.models import ModelRegistry

    before_count = ModelRegistry.objects.filter(is_active=True).count()

    entry1 = promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )
    entry2 = promote_blend(
        artifact_dir=v32_dir,
        feature_set_columns=list(PRE_FEATURE_NAMES),
        feature_set_live_servable=list(PRE_FEATURE_NAMES),
    )

    assert entry1.is_active is False, "First promote_blend() row must be inactive"
    assert entry2.is_active is False, "Second promote_blend() row must be inactive"

    # No additional active rows were created by the two calls
    after_count = ModelRegistry.objects.filter(is_active=True).count()
    assert after_count == before_count, (
        f"promote_blend() created {after_count - before_count} unexpected active "
        "ModelRegistry row(s) — activation must be an explicit operator action"
    )
