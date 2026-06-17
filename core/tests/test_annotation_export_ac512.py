# ---
# module: core.tests.test_annotation_export_ac512
# sprint: sprint-10
# story: US-51 AC-51.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tasks, core.dashboard.annotation_export, core.tape.manifest,
#               core.dashboard.annotation_api, core.models, ast, csv, hashlib, json, pytest
# ---
"""AC-51.2 — Annotation export: labeled dataset + MANIFEST, Celery task structural proof.

Tests
-----
  H1 import trap (module level)
      Importing export_annotations from core.tasks at module level fails pytest
      COLLECTION if the task is deleted/renamed (mirrors AC-31.1/H1 pattern).

  test_export_produces_csv_with_correct_columns
      Export over a banked fixture produces a CSV with exactly the required
      columns: mint, tags, notes, annotation_count.

  test_export_manifest_required_fields_all_present
      The MANIFEST.json next to the CSV contains all US-36 required fields:
      dataset_id, content_hash, mint_cohort, row_count, source, date_range.

  test_export_content_hash_matches_csv_sha256
      Independently compute SHA-256 of the CSV; assert it equals
      manifest["content_hash"] — verifying the hash covers the actual bytes.

  test_export_run_twice_byte_identical
      Run the task twice over the same annotation fixture; assert the two CSV
      outputs are byte-identical AND both manifest content_hashes are equal
      (the AC-51.2 determinism requirement).

  test_export_mint_cohort_lc_all_c_sorted
      The manifest mint_cohort is LC_ALL=C (byte-order / ASCII) sorted,
      matching the cross-machine determinism rule from core.tape.manifest.

  test_export_row_count_matches_csv_data_rows
      manifest row_count equals the number of data rows (excl. header) in CSV.

  test_export_tags_join_across_annotations
      For a mint with two annotations, the exported tags column contains the
      union of both annotation tag lists (unique, first-occurrence ordered).

  test_export_unannotated_mint_included_with_zero_count
      A mint in the corpus with no annotations gets annotation_count=0 and
      empty tags/notes — real-missing preserved, never fabricated.

  test_export_annotations_task_is_celery_task_not_view
      Structural/AST test: export_annotations is a Celery shared_task in
      core.tasks, NOT a web view in core.views.

  test_export_annotations_has_celery_task_interface
      export_annotations has .delay, .apply, .apply_async attributes (Celery
      task interface) — behavioral confirmation it is a task, not a plain fn.

  test_export_annotations_task_name
      The registered Celery task name is 'core.tasks.export_annotations'.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# H1 ImportError trap — fails pytest COLLECTION if export_annotations is
# deleted or renamed from core.tasks (mirrors AC-31.1 / H1 pattern)
# ---------------------------------------------------------------------------
from core.tasks import export_annotations  # noqa: E402

assert export_annotations  # fails collection if None

MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
MINT_C = "MintCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _seed_annotations():
    """Seed a deterministic annotation fixture: MINT_A×2, MINT_B×1, MINT_C has none."""
    from core.dashboard.annotation_api import save_annotation

    save_annotation(
        mint=MINT_A,
        author="alice",
        tags=["classic rug shape", "organic"],
        note="First take.",
    )
    save_annotation(
        mint=MINT_A,
        author="bob",
        tags=["slow bleed", "organic"],
        note="Second look.",
    )
    save_annotation(
        mint=MINT_B,
        author="charlie",
        tags=["clean ignition"],
        note="Clean entry.",
    )


def _run_export(tmp_path, corpus=None, run_id="run1"):
    """Run export_annotations synchronously and return (result, csv_path)."""
    csv_path = str(tmp_path / f"{run_id}.csv")
    if corpus is None:
        corpus = [MINT_A, MINT_B]
    result = export_annotations.apply(
        args=[corpus],
        kwargs={"output_path": csv_path, "dataset_id": "test_export"},
    ).get()
    return result, Path(csv_path)


# ---------------------------------------------------------------------------
# 1. CSV has correct columns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_produces_csv_with_correct_columns(tmp_path):
    """Exported CSV must have exactly: mint, tags, notes, annotation_count."""
    _seed_annotations()
    result, csv_path = _run_export(tmp_path)

    assert csv_path.exists(), "CSV file was not created"
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames

    assert fieldnames == ["mint", "tags", "notes", "annotation_count"], (
        f"Unexpected fieldnames: {fieldnames}"
    )


# ---------------------------------------------------------------------------
# 2. MANIFEST has all required US-36 fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_manifest_required_fields_all_present(tmp_path):
    """MANIFEST.json must contain all required US-36 fields."""
    _seed_annotations()
    result, csv_path = _run_export(tmp_path)

    manifest_path = Path(result["manifest_path"])
    assert manifest_path.exists(), "MANIFEST.json was not created"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required = {"dataset_id", "content_hash", "mint_cohort", "row_count", "source", "date_range"}
    missing = required - set(manifest.keys())
    assert not missing, f"MANIFEST missing required fields: {missing}"

    assert isinstance(manifest["mint_cohort"], list)
    assert isinstance(manifest["row_count"], int)
    assert len(manifest["content_hash"]) == 64, "content_hash must be 64-char SHA-256 hex"
    assert "start" in manifest["date_range"]
    assert "end" in manifest["date_range"]


# ---------------------------------------------------------------------------
# 3. content_hash matches independently computed SHA-256
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_content_hash_matches_csv_sha256(tmp_path):
    """Manifest content_hash must equal SHA-256 of the exported CSV bytes."""
    _seed_annotations()
    result, csv_path = _run_export(tmp_path)

    expected_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert result["manifest"]["content_hash"] == expected_hash, (
        f"content_hash mismatch:\n  manifest: {result['manifest']['content_hash']}\n"
        f"  computed: {expected_hash}"
    )


# ---------------------------------------------------------------------------
# 4. Run-twice byte-identical (the determinism requirement)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_run_twice_byte_identical(tmp_path):
    """Two export runs over the same annotation fixture must produce byte-identical CSVs."""
    _seed_annotations()
    r1, csv1 = _run_export(tmp_path, run_id="run1")
    r2, csv2 = _run_export(tmp_path, run_id="run2")

    assert csv1.read_bytes() == csv2.read_bytes(), "CSV outputs differ between runs (not deterministic)"
    assert r1["manifest"]["content_hash"] == r2["manifest"]["content_hash"], (
        f"content_hash differs:\n  run1: {r1['manifest']['content_hash']}\n"
        f"  run2: {r2['manifest']['content_hash']}"
    )


# ---------------------------------------------------------------------------
# 5. mint_cohort is LC_ALL=C sorted
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_mint_cohort_lc_all_c_sorted(tmp_path):
    """manifest mint_cohort must be LC_ALL=C (ASCII byte-order) sorted."""
    from core.tape.manifest import lc_all_c_sort

    _seed_annotations()
    corpus = [MINT_B, MINT_A]
    result, _ = _run_export(tmp_path, corpus=corpus)

    expected_order = lc_all_c_sort(corpus)
    assert result["manifest"]["mint_cohort"] == expected_order, (
        f"mint_cohort not LC_ALL=C sorted:\n  got: {result['manifest']['mint_cohort']}\n"
        f"  expected: {expected_order}"
    )


# ---------------------------------------------------------------------------
# 6. row_count matches CSV data rows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_row_count_matches_csv_data_rows(tmp_path):
    """manifest row_count must equal the number of data rows (excl. header) in the CSV."""
    _seed_annotations()
    result, csv_path = _run_export(tmp_path)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        data_rows = list(csv.DictReader(fh))

    assert result["manifest"]["row_count"] == len(data_rows), (
        f"row_count {result['manifest']['row_count']} != actual data rows {len(data_rows)}"
    )


# ---------------------------------------------------------------------------
# 7. Tags join across multiple annotations on one mint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_tags_join_across_annotations(tmp_path):
    """For a mint with two annotations, exported tags must be the union in first-occurrence order."""
    _seed_annotations()
    result, csv_path = _run_export(tmp_path, corpus=[MINT_A])

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    row = rows[0]
    assert row["mint"] == MINT_A
    tags = json.loads(row["tags"])
    # alice's tags first: "classic rug shape", "organic"
    # bob's "slow bleed" is new; bob's "organic" is already seen → skipped
    assert "classic rug shape" in tags
    assert "organic" in tags
    assert "slow bleed" in tags
    assert tags.count("organic") == 1, "Duplicate tags must be deduplicated"
    assert int(row["annotation_count"]) == 2


# ---------------------------------------------------------------------------
# 8. Unannotated mint in corpus → annotation_count=0, empty tags/notes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_unannotated_mint_included_with_zero_count(tmp_path):
    """A corpus mint with no annotations gets annotation_count=0 and empty tags/notes (real-missing)."""
    _seed_annotations()
    # MINT_C has no annotations
    result, csv_path = _run_export(tmp_path, corpus=[MINT_B, MINT_C])

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = {r["mint"]: r for r in csv.DictReader(fh)}

    assert MINT_C in rows, "Unannotated mint must still appear in export"
    row_c = rows[MINT_C]
    assert int(row_c["annotation_count"]) == 0
    assert json.loads(row_c["tags"]) == []
    assert json.loads(row_c["notes"]) == []


# ---------------------------------------------------------------------------
# 9. Structural/AST: export_annotations is in core.tasks, NOT in core.views
# ---------------------------------------------------------------------------


def test_export_annotations_task_is_celery_task_not_view():
    """AST proof: export_annotations is defined in core/tasks.py (as a @shared_task),
    and is NOT defined in core/views.py."""
    tasks_path = Path(__file__).parents[2] / "core" / "tasks.py"
    views_path = Path(__file__).parents[2] / "core" / "views.py"

    assert tasks_path.exists(), f"core/tasks.py not found at {tasks_path}"
    assert views_path.exists(), f"core/views.py not found at {views_path}"

    tasks_src = tasks_path.read_text(encoding="utf-8")
    views_src = views_path.read_text(encoding="utf-8")

    # Parse AST of tasks.py and find export_annotations function with @shared_task decorator
    tree = ast.parse(tasks_src, filename=str(tasks_path))
    found_task = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "export_annotations":
                decorator_names = []
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call):
                        fn = deco.func
                        decorator_names.append(
                            fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                        )
                    elif isinstance(deco, ast.Name):
                        decorator_names.append(deco.id)
                    elif isinstance(deco, ast.Attribute):
                        decorator_names.append(deco.attr)
                assert any(n in ("shared_task", "task") for n in decorator_names), (
                    f"export_annotations in tasks.py is NOT decorated with @shared_task: "
                    f"decorators found = {decorator_names}"
                )
                found_task = True
                break

    assert found_task, "export_annotations function not found in core/tasks.py"

    # Parse AST of views.py — must NOT contain export_annotations
    views_tree = ast.parse(views_src, filename=str(views_path))
    for node in ast.walk(views_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name != "export_annotations", (
                "export_annotations must NOT be a web view in core/views.py (#289 — never on web/gunicorn)"
            )


# ---------------------------------------------------------------------------
# 10. Celery task interface attributes present (delay, apply, apply_async)
# ---------------------------------------------------------------------------


def test_export_annotations_has_celery_task_interface():
    """export_annotations must have .delay, .apply, and .apply_async (Celery task interface)."""
    for attr in ("delay", "apply", "apply_async"):
        assert hasattr(export_annotations, attr), (
            f"export_annotations is missing Celery task attribute: {attr!r}"
        )


# ---------------------------------------------------------------------------
# 11. Registered Celery task name
# ---------------------------------------------------------------------------


def test_export_annotations_task_name():
    """export_annotations must be registered with name 'core.tasks.export_annotations'."""
    assert export_annotations.name == "core.tasks.export_annotations", (
        f"Unexpected task name: {export_annotations.name!r}"
    )
