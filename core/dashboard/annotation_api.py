# ---
# module: core.dashboard.annotation_api
# sprint: sprint-10
# story: US-51 AC-51.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.models
# ---
"""Annotation storage logic (PRD §8, §13.3, US-51 AC-51.1).

Provides save_annotation() and get_annotations() — the ONLY write path for the
annotations table. NEVER touches RawEvent or any raw lake table (§6.4.1).
The annotations table is a SEPARATE store keyed on mint; immutability of the
raw lake is structurally enforced by the absence of any import or call to
RawEvent write methods here.
"""
from core.models import Annotation


def save_annotation(mint: str, author: str, tags: list, note: str = "") -> Annotation:
    """Create and persist a new annotation for the given mint.

    This is the ONLY write path to the annotations table. It never touches
    RawEvent or any other raw lake table (§6.4.1, raw=immutable principle).

    Args:
        mint:   Solana mint address (max 64 chars).
        author: Author identifier (max 128 chars).
        tags:   List of tag strings (categorical and/or free-text).
        note:   Optional free-text note.

    Returns:
        The newly created Annotation instance (id and created_at populated).
    """
    annotation = Annotation.objects.create(
        mint=mint,
        author=author,
        tags=tags,
        note=note,
    )
    return annotation


def get_annotations(mint: str):
    """Return all annotations for the given mint, ordered by created_at ascending.

    Multiple annotations per mint are retained — this function returns them all.
    Never reads from or mutates the raw lake.

    Args:
        mint: Solana mint address.

    Returns:
        QuerySet of Annotation instances ordered by created_at.
    """
    return Annotation.objects.filter(mint=mint).order_by("created_at")
