# ---
# module: core.tests.test_annotation_no_auto_start_ac513
# sprint: sprint-10
# story: US-51 AC-51.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tasks, core.dashboard.annotation_export, core.dashboard.annotation_api,
#               core.models, ast, pathlib, pytest
# ---
"""AC-51.3 — No silent state change: annotation/export paths never enable scoring or trading.

Extends the US-11 AST no-auto-start guard (AC-11.3) and the AC-42.3 extension to cover the
annotation/export path: annotation_api.py, annotation_export.py, and the export_annotations
Celery task must never set scoring_enabled=True or trading_enabled=True, and must not
reference PipelineState at all (§5.3/§15.6).

The export is also verified to be idempotent: running it twice with the same DB state
leaves pipeline_state flags unchanged both times.

H1 ImportError trap (module level):
    Importing export_annotations from core.tasks at module level fails pytest COLLECTION
    if the task is deleted or renamed — mirroring the standing H1 pattern.

Test structure
--------------
H1 ImportError trap (module level):
    test_export_annotations_importable_ac513

AST guards — annotation path never sets scoring/trading flags (extends AC-11.3 / AC-42.3):
    test_annotation_api_py_does_not_reference_pipeline_state
    test_annotation_export_py_does_not_reference_pipeline_state
    test_annotation_api_py_does_not_set_scoring_enabled_true
    test_annotation_api_py_does_not_set_trading_enabled_true
    test_annotation_export_py_does_not_set_scoring_enabled_true
    test_annotation_export_py_does_not_set_trading_enabled_true
    test_export_annotations_task_body_does_not_set_scoring_enabled_true
    test_export_annotations_task_body_does_not_set_trading_enabled_true

DB-backed — annotation/export operations leave PipelineState unchanged:
    test_save_annotation_does_not_flip_pipeline_state_flags
    test_export_annotations_does_not_flip_pipeline_state_flags

Idempotency — running the export twice leaves flags False both times:
    test_export_annotations_idempotent_flags_stay_false
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — fails pytest COLLECTION if export_annotations is
# deleted or renamed from core.tasks (mirrors AC-31.1 / H1 pattern)
# ---------------------------------------------------------------------------
from core.tasks import export_annotations  # noqa: E402

assert export_annotations  # H1 guard — deletion/rename fails collection

# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_API_PY = REPO_ROOT / "core" / "dashboard" / "annotation_api.py"
ANNOTATION_EXPORT_PY = REPO_ROOT / "core" / "dashboard" / "annotation_export.py"
TASKS_PY = REPO_ROOT / "core" / "tasks.py"

_STATE_FLAGS = {"scoring_enabled", "trading_enabled"}

MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


# ---------------------------------------------------------------------------
# Helpers — AST flag-True-assignment scanner (mirrors AC-11.3 / AC-42.3 pattern)
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
# H1 import trap — explicit test (in addition to the module-level guard)
# ---------------------------------------------------------------------------


def test_export_annotations_importable_ac513():
    """export_annotations is importable from core.tasks (H1 wiring guard).

    The module-level import already enforces this at collection time; this test
    makes the requirement explicit and self-documenting.
    """
    from core.tasks import export_annotations as _ea

    assert callable(_ea), "export_annotations must be callable"


# ---------------------------------------------------------------------------
# AST guard — annotation_api.py must not reference PipelineState
# ---------------------------------------------------------------------------


def test_annotation_api_py_does_not_reference_pipeline_state():
    """core/dashboard/annotation_api.py must not import or reference PipelineState.

    The annotation API writes to the annotations table only — it must never
    touch pipeline control flags (§5.3/§15.6).  Mirrors the AC-11.3 guard on
    resolver.py and the AC-42.3 guard on promote_model.py.
    """
    source = ANNOTATION_API_PY.read_text(encoding="utf-8")
    assert "PipelineState" not in source, (
        "core/dashboard/annotation_api.py references PipelineState — the annotation "
        "write path must not touch pipeline control flags (§5.3/§15.6).  Annotations "
        "are stored in a separate table and must never auto-enable scoring or trading."
    )


# ---------------------------------------------------------------------------
# AST guard — annotation_export.py must not reference PipelineState
# ---------------------------------------------------------------------------


def test_annotation_export_py_does_not_reference_pipeline_state():
    """core/dashboard/annotation_export.py must not import or reference PipelineState.

    The export path writes a CSV + MANIFEST only — it must never touch pipeline
    control flags (§5.3/§15.6).  Mirrors AC-11.3 / AC-42.3 guard pattern.
    """
    source = ANNOTATION_EXPORT_PY.read_text(encoding="utf-8")
    assert "PipelineState" not in source, (
        "core/dashboard/annotation_export.py references PipelineState — the export "
        "path must not touch pipeline control flags (§5.3/§15.6).  Export produces "
        "a labeled dataset and must never auto-enable scoring or trading."
    )


# ---------------------------------------------------------------------------
# AST guard — annotation_api.py must not set scoring_enabled=True
# ---------------------------------------------------------------------------


def test_annotation_api_py_does_not_set_scoring_enabled_true():
    """core/dashboard/annotation_api.py must not contain scoring_enabled=True anywhere.

    Extends the US-11 AC-11.3 AST guard to the annotation write path: saving an
    annotation is a labeling action only — it must never auto-start scoring
    (§5.3/§15.6).
    """
    violations = [
        v for v in _find_flag_true_assignments(ANNOTATION_API_PY) if "scoring_enabled" in v
    ]
    assert not violations, (
        "core/dashboard/annotation_api.py sets scoring_enabled=True — the annotation "
        "write path must not auto-start scoring (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AST guard — annotation_api.py must not set trading_enabled=True
# ---------------------------------------------------------------------------


def test_annotation_api_py_does_not_set_trading_enabled_true():
    """core/dashboard/annotation_api.py must not contain trading_enabled=True anywhere.

    Saving an annotation must never auto-start trading (§5.3/§15.6).
    """
    violations = [
        v for v in _find_flag_true_assignments(ANNOTATION_API_PY) if "trading_enabled" in v
    ]
    assert not violations, (
        "core/dashboard/annotation_api.py sets trading_enabled=True — the annotation "
        "write path must not auto-start trading (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AST guard — annotation_export.py must not set scoring_enabled=True
# ---------------------------------------------------------------------------


def test_annotation_export_py_does_not_set_scoring_enabled_true():
    """core/dashboard/annotation_export.py must not contain scoring_enabled=True anywhere.

    Exporting annotations produces a labeled dataset — it must never auto-start
    scoring (§5.3/§15.6).
    """
    violations = [
        v for v in _find_flag_true_assignments(ANNOTATION_EXPORT_PY) if "scoring_enabled" in v
    ]
    assert not violations, (
        "core/dashboard/annotation_export.py sets scoring_enabled=True — the export "
        "path must not auto-start scoring (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AST guard — annotation_export.py must not set trading_enabled=True
# ---------------------------------------------------------------------------


def test_annotation_export_py_does_not_set_trading_enabled_true():
    """core/dashboard/annotation_export.py must not contain trading_enabled=True anywhere.

    Exporting annotations must never auto-start trading (§5.3/§15.6).
    """
    violations = [
        v for v in _find_flag_true_assignments(ANNOTATION_EXPORT_PY) if "trading_enabled" in v
    ]
    assert not violations, (
        "core/dashboard/annotation_export.py sets trading_enabled=True — the export "
        "path must not auto-start trading (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AST guard — export_annotations task body must not set scoring_enabled=True
# ---------------------------------------------------------------------------


def test_export_annotations_task_body_does_not_set_scoring_enabled_true():
    """The export_annotations function body in core/tasks.py must not set scoring_enabled=True.

    The Celery task that runs the export must not auto-start scoring regardless
    of its result (§5.3/§15.6).  Scoped to the export_annotations function body
    only (not the whole file) via AST function-node inspection.
    """
    source = TASKS_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TASKS_PY))
    rel = TASKS_PY.relative_to(REPO_ROOT)

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "export_annotations":
                for child in ast.walk(node):
                    if isinstance(child, ast.keyword):
                        if (
                            child.arg == "scoring_enabled"
                            and isinstance(child.value, ast.Constant)
                            and child.value.value is True
                        ):
                            violations.append(
                                f"{rel}: export_annotations body: keyword scoring_enabled=True"
                            )
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and target.attr == "scoring_enabled"
                                and isinstance(child.value, ast.Constant)
                                and child.value.value is True
                            ):
                                violations.append(
                                    f"{rel}: export_annotations body: .scoring_enabled = True"
                                )

    assert not violations, (
        "export_annotations task body sets scoring_enabled=True — the export task "
        "must not auto-start scoring (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# AST guard — export_annotations task body must not set trading_enabled=True
# ---------------------------------------------------------------------------


def test_export_annotations_task_body_does_not_set_trading_enabled_true():
    """The export_annotations function body in core/tasks.py must not set trading_enabled=True.

    The Celery task that runs the export must not auto-start trading (§5.3/§15.6).
    """
    source = TASKS_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TASKS_PY))
    rel = TASKS_PY.relative_to(REPO_ROOT)

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "export_annotations":
                for child in ast.walk(node):
                    if isinstance(child, ast.keyword):
                        if (
                            child.arg == "trading_enabled"
                            and isinstance(child.value, ast.Constant)
                            and child.value.value is True
                        ):
                            violations.append(
                                f"{rel}: export_annotations body: keyword trading_enabled=True"
                            )
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and target.attr == "trading_enabled"
                                and isinstance(child.value, ast.Constant)
                                and child.value.value is True
                            ):
                                violations.append(
                                    f"{rel}: export_annotations body: .trading_enabled = True"
                                )

    assert not violations, (
        "export_annotations task body sets trading_enabled=True — the export task "
        "must not auto-start trading (§5.3/§15.6):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# DB-backed — save_annotation() leaves PipelineState flags unchanged
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_annotation_does_not_flip_pipeline_state_flags():
    """save_annotation() must not set PipelineState.scoring_enabled or trading_enabled to True.

    Labeling a token is a research/annotation action — it must never automatically
    start scoring or trading as a side effect (§5.3/§15.6).
    """
    from core.dashboard.annotation_api import save_annotation
    from core.models import PipelineState

    state = PipelineState.get()
    assert state.scoring_enabled is False  # baseline
    assert state.trading_enabled is False  # baseline

    save_annotation(
        mint=MINT_A,
        author="tester",
        tags=["classic rug shape", "organic"],
        note="AC-51.3 guard test",
    )

    state.refresh_from_db()
    assert state.scoring_enabled is False, (
        "save_annotation() must not enable scoring — scoring_enabled was flipped True"
    )
    assert state.trading_enabled is False, (
        "save_annotation() must not enable trading — trading_enabled was flipped True"
    )


# ---------------------------------------------------------------------------
# DB-backed — export_annotations task leaves PipelineState flags unchanged
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_annotations_does_not_flip_pipeline_state_flags(tmp_path):
    """export_annotations Celery task must not set PipelineState flags to True.

    Producing a labeled export dataset must never automatically start scoring
    or trading (§5.3/§15.6).
    """
    from core.dashboard.annotation_api import save_annotation
    from core.models import PipelineState

    save_annotation(mint=MINT_A, author="tester", tags=["slow bleed"], note="")
    save_annotation(mint=MINT_B, author="tester", tags=["clean ignition"], note="entry ok")

    state = PipelineState.get()
    assert state.scoring_enabled is False  # baseline
    assert state.trading_enabled is False  # baseline

    csv_path = str(tmp_path / "export.csv")
    export_annotations.apply(
        args=[[MINT_A, MINT_B]],
        kwargs={"output_path": csv_path, "dataset_id": "ac513_guard"},
    ).get()

    state.refresh_from_db()
    assert state.scoring_enabled is False, (
        "export_annotations must not enable scoring — scoring_enabled was flipped True"
    )
    assert state.trading_enabled is False, (
        "export_annotations must not enable trading — trading_enabled was flipped True"
    )


# ---------------------------------------------------------------------------
# Idempotency + no-state-change — two export runs leave flags False both times
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_annotations_idempotent_flags_stay_false(tmp_path):
    """Running export_annotations twice produces byte-identical output AND flags stay False.

    Combines the idempotency requirement (AC-51.3) with the no-auto-start guard:
    re-running the export must not accumulate side effects on PipelineState.
    """
    from core.dashboard.annotation_api import save_annotation
    from core.models import PipelineState

    save_annotation(mint=MINT_A, author="alice", tags=["fakeout pop"], note="first")
    save_annotation(mint=MINT_B, author="bob", tags=["organic"], note="second")

    state = PipelineState.get()

    csv1 = str(tmp_path / "run1.csv")
    csv2 = str(tmp_path / "run2.csv")

    export_annotations.apply(
        args=[[MINT_A, MINT_B]],
        kwargs={"output_path": csv1, "dataset_id": "ac513_idempotent"},
    ).get()

    state.refresh_from_db()
    assert state.scoring_enabled is False, "scoring_enabled was flipped True after first export run"
    assert state.trading_enabled is False, "trading_enabled was flipped True after first export run"

    export_annotations.apply(
        args=[[MINT_A, MINT_B]],
        kwargs={"output_path": csv2, "dataset_id": "ac513_idempotent"},
    ).get()

    state.refresh_from_db()
    assert state.scoring_enabled is False, "scoring_enabled was flipped True after second export run"
    assert state.trading_enabled is False, "trading_enabled was flipped True after second export run"

    csv1_bytes = (tmp_path / "run1.csv").read_bytes()
    csv2_bytes = (tmp_path / "run2.csv").read_bytes()
    assert csv1_bytes == csv2_bytes, (
        "export_annotations is not idempotent: two runs over the same DB state produced "
        "different CSV bytes (violates AC-51.3 idempotency requirement)"
    )
