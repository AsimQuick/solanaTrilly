# ---
# module: core.tests.test_json_safe_encoder_guard_ac53
# sprint: sprint-2
# story: US-5 AC-5.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.encoders, django.apps, django.db.models, ast, pathlib
# ---
"""AC-5.3 — Guard: every models.JSONField must declare encoder=JsonSafeEncoder.

Prevents future fields from silently bypassing the encoder and reintroducing
the #331/#332/#388 JSONB/psycopg crash class (NaN/Inf/Decimal stored as
invalid JSON literals that psycopg rejects at write time).

Two complementary layers:
  1. Runtime — Django app-registry scan: asserts every registered JSONField
     instance has field.encoder is JsonSafeEncoder.
  2. Static  — AST scan of every models.py in the repo: catches a newly
     written field before it is imported into the test session, so the guard
     fires even on a file that hasn't been committed yet.
"""
import ast
from pathlib import Path

from django.apps import apps
from django.db import models

from core.encoders import JsonSafeEncoder

# Repo root: core/tests/<this file> -> parents[0]=tests, [1]=core, [2]=root
REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_registered_json_fields():
    """Yield (model_label, field_name, encoder) for every JSONField in all apps."""
    for model in apps.get_models(include_auto_created=True):
        for field in model._meta.get_fields():
            if isinstance(field, models.JSONField):
                yield model._meta.label, field.name, field.encoder


def _find_model_files():
    """Return all models.py paths under the repo, excluding migrations and venvs."""
    excluded_parts = {".git", "__pycache__", "migrations", ".venv", "node_modules"}
    return [
        p
        for p in REPO_ROOT.rglob("models.py")
        if not excluded_parts.intersection(p.parts)
    ]


def _json_field_calls_in_file(path):
    """Parse path with AST and yield (lineno, has_encoder) for each JSONField call."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_json_field = (isinstance(func, ast.Attribute) and func.attr == "JSONField") or (
            isinstance(func, ast.Name) and func.id == "JSONField"
        )
        if is_json_field:
            has_encoder = any(kw.arg == "encoder" for kw in node.keywords)
            yield node.lineno, has_encoder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_registered_json_fields_use_json_safe_encoder():
    """Every JSONField in every registered Django model must use JsonSafeEncoder.

    Fails when a new field is added without encoder=JsonSafeEncoder — the fix
    is to add the argument and regenerate the migration.
    """
    violations = [
        f"{label}.{name}: encoder={enc!r} (expected JsonSafeEncoder)"
        for label, name, enc in _all_registered_json_fields()
        if enc is not JsonSafeEncoder
    ]
    assert not violations, (
        "JSONFields without JsonSafeEncoder detected — these silently reintroduce "
        "the #331/#332/#388 JSONB/psycopg crash class:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_json_field_guard_is_non_degenerate():
    """At least one JSONField must exist so the guard cannot trivially pass on an empty app."""
    fields = list(_all_registered_json_fields())
    assert fields, (
        "No JSONField found in any registered model — "
        "check INSTALLED_APPS and Django app registration."
    )


def test_no_model_file_json_field_lacks_encoder_kwarg():
    """Static AST guard: every JSONField(...) call in models.py files must have encoder=.

    Catches newly written fields before they are imported into the test session,
    giving a faster feedback loop than waiting for Django to load the app.
    """
    model_files = _find_model_files()
    assert model_files, f"No models.py files found under {REPO_ROOT} — cannot run static guard."

    violations = []
    for path in model_files:
        for lineno, has_encoder in _json_field_calls_in_file(path):
            if not has_encoder:
                violations.append(f"{path}:{lineno} — JSONField missing encoder= kwarg")

    assert not violations, (
        "JSONField declarations without encoder= kwarg found:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
