# ---
# module: core.tests.test_annotation_ac511
# sprint: sprint-10
# story: US-51 AC-51.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.annotation_api, core.models, core.schemas
# ---
"""Tests for AC-51.1: annotations table, AnnotationConfig, immutability.

H1 import trap — fails pytest COLLECTION if save_annotation is deleted/renamed.
"""
from __future__ import annotations

import json

import pytest

# H1 import trap — fails pytest COLLECTION if save_annotation is deleted/renamed
from core.dashboard.annotation_api import get_annotations, save_annotation  # noqa: E402
from core.models import Annotation, RawEvent
from core.schemas import AnnotationConfig

assert save_annotation  # fails collection if None

MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


# ---------------------------------------------------------------------------
# 1. Annotation persists with all 5 fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_annotation_persists_all_five_fields():
    """Saved annotation is retrievable with all 5 PRD §8 fields: mint, author, tags,
    note, created_at."""
    ann = save_annotation(
        mint=MINT_A,
        author="alice",
        tags=["classic rug shape", "organic"],
        note="Looked classic to me.",
    )
    # Fetch fresh from DB
    db_ann = Annotation.objects.get(pk=ann.pk)
    assert db_ann.mint == MINT_A
    assert db_ann.author == "alice"
    assert db_ann.tags == ["classic rug shape", "organic"]
    assert db_ann.note == "Looked classic to me."
    assert db_ann.created_at is not None


# ---------------------------------------------------------------------------
# 2. Multiple annotations on one mint are retained
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_multiple_annotations_on_one_mint_retained():
    """Calling save_annotation twice on the same mint retains both rows."""
    save_annotation(mint=MINT_A, author="alice", tags=["slow bleed"], note="first")
    save_annotation(mint=MINT_A, author="bob", tags=["clean ignition"], note="second")

    all_anns = list(get_annotations(MINT_A))
    assert len(all_anns) == 2
    authors = {a.author for a in all_anns}
    assert "alice" in authors
    assert "bob" in authors


# ---------------------------------------------------------------------------
# 3. Write path does NOT mutate the raw lake
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_write_path_does_not_mutate_raw_lake():
    """save_annotation() must not create or modify any RawEvent rows (§6.4.1)."""
    assert RawEvent.objects.count() == 0, "precondition: raw lake is empty"
    save_annotation(
        mint=MINT_A,
        author="charlie",
        tags=["fakeout pop"],
        note="Definitely a fakeout.",
    )
    assert RawEvent.objects.count() == 0, "raw lake must remain immutable after annotation"


# ---------------------------------------------------------------------------
# 4. AnnotationConfig categorical tags are config-driven
# ---------------------------------------------------------------------------

def test_annotation_config_categorical_tags_are_config_driven():
    """AnnotationConfig().categorical_tags contains the 5 named tags (Principle #1)."""
    cfg = AnnotationConfig()
    assert isinstance(cfg.categorical_tags, list)
    expected = {
        "classic rug shape",
        "slow bleed",
        "clean ignition",
        "fakeout pop",
        "organic",
    }
    assert expected == set(cfg.categorical_tags), (
        f"Expected exactly {expected}, got {set(cfg.categorical_tags)}"
    )
    assert len(cfg.categorical_tags) == 5


# ---------------------------------------------------------------------------
# 5. get_annotations returns annotations ordered by created_at
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_annotations_ordered_by_created_at():
    """get_annotations() returns rows ordered by created_at ascending."""
    a1 = save_annotation(mint=MINT_B, author="first", tags=[], note="one")
    a2 = save_annotation(mint=MINT_B, author="second", tags=[], note="two")
    a3 = save_annotation(mint=MINT_B, author="third", tags=[], note="three")

    result = list(get_annotations(MINT_B))
    assert len(result) == 3
    assert result[0].pk == a1.pk
    assert result[1].pk == a2.pk
    assert result[2].pk == a3.pk


# ---------------------------------------------------------------------------
# 6. annotation_list view returns 200 with correct JSON shape
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_annotation_list_view_returns_200(client):
    """GET /api/annotations/<mint>/ returns 200 and a JSON list."""
    save_annotation(mint=MINT_A, author="diana", tags=["organic"], note="nice")
    resp = client.get(f"/api/annotations/{MINT_A}/")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert isinstance(data, list)
    assert len(data) >= 1
    first = data[0]
    # All 5 fields must be present in the response
    for field in ("mint", "author", "tags", "note", "created_at"):
        assert field in first, f"Missing field {field!r} in annotation list response"
    assert first["mint"] == MINT_A
    assert first["author"] == "diana"
    assert "organic" in first["tags"]


# ---------------------------------------------------------------------------
# 7. annotation_create view creates annotation and returns 201
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_annotation_create_view_returns_201(client):
    """POST /api/annotations/<mint>/create/ creates an annotation and returns 201."""
    payload = json.dumps({"author": "eve", "tags": ["slow bleed"], "note": "bleed out"})
    resp = client.post(
        f"/api/annotations/{MINT_A}/create/",
        data=payload,
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = json.loads(resp.content)
    assert data["mint"] == MINT_A
    assert data["author"] == "eve"
    assert data["tags"] == ["slow bleed"]
    assert data["note"] == "bleed out"
    assert "created_at" in data

    # Verify it actually persisted
    assert Annotation.objects.filter(mint=MINT_A, author="eve").exists()


# ---------------------------------------------------------------------------
# 8. tags field stores a list (multiple tag values)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tags_field_stores_list():
    """tags field must round-trip as a JSON list with multiple values."""
    tags_input = ["classic rug shape", "slow bleed", "fakeout pop"]
    ann = save_annotation(mint=MINT_A, author="frank", tags=tags_input, note="")
    db_ann = Annotation.objects.get(pk=ann.pk)
    assert isinstance(db_ann.tags, list)
    assert db_ann.tags == tags_input


# ---------------------------------------------------------------------------
# 9. Annotation model db_table is "annotations" (separate from "raw_events")
# ---------------------------------------------------------------------------

def test_annotation_model_db_table_is_annotations():
    """Annotation._meta.db_table must be 'annotations', not 'raw_events' or anything else."""
    assert Annotation._meta.db_table == "annotations"
    assert RawEvent._meta.db_table != "annotations"


# ---------------------------------------------------------------------------
# 10. Annotation has NO FK to RawEvent (structural isolation)
# ---------------------------------------------------------------------------

def test_annotation_has_no_fk_to_raw_event():
    """Annotation model must not have a ForeignKey pointing to RawEvent (§6.4.1)."""
    from django.db.models import ForeignKey
    for field in Annotation._meta.get_fields():
        if isinstance(field, ForeignKey):
            assert field.related_model is not RawEvent, (
                "Annotation must not have a FK to RawEvent — structural isolation (§6.4.1)"
            )


# ---------------------------------------------------------------------------
# 11. annotation_create view rejects missing author (400)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_annotation_create_rejects_missing_author(client):
    """POST without 'author' field returns 400."""
    payload = json.dumps({"tags": ["organic"], "note": "test"})
    resp = client.post(
        f"/api/annotations/{MINT_A}/create/",
        data=payload,
        content_type="application/json",
    )
    assert resp.status_code == 400
