# ---
# module: core.tests.test_swap_model_ac172
# sprint: sprint-5
# story: US-17 AC-17.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, core.migrations.0007_swap_swap_mint_block_slot_sig_idx, pytest
# ---
"""AC-17.2 — Composite index and side-choices invariant tests.

Verifies:
- The canonical-ordering composite index on (mint, block_time, slot, signature)
  is declared in Swap.Meta.indexes.
- The migration 0007 also declares this index via AddIndex.
- Swap.side.choices is non-empty (constrained vocabulary).
- Every side choice value fits within side.max_length (VARCHAR-width discipline).
"""
import importlib

from django.db.migrations.operations.models import AddIndex

from core.models import Swap


_TARGET_FIELDS = ["mint", "block_time", "slot", "signature"]


def test_composite_index_declared_in_meta():
    """Swap.Meta.indexes must contain exactly one index over (mint, block_time, slot, signature)."""
    matching = [
        idx for idx in Swap._meta.indexes if list(idx.fields) == _TARGET_FIELDS
    ]
    assert len(matching) == 1, (
        f"Expected one composite index on {_TARGET_FIELDS}; "
        f"found: {[list(i.fields) for i in Swap._meta.indexes]}"
    )


def test_composite_index_in_migration():
    """Migration 0007 must declare the composite index via AddIndex."""
    migration = importlib.import_module(
        "core.migrations.0007_swap_swap_mint_block_slot_sig_idx"
    )
    add_ops = [op for op in migration.Migration.operations if isinstance(op, AddIndex)]
    assert len(add_ops) == 1, f"Expected one AddIndex operation, found: {add_ops}"
    index_fields = list(add_ops[0].index.fields)
    assert index_fields == _TARGET_FIELDS, (
        f"AddIndex fields {index_fields!r} != expected {_TARGET_FIELDS!r}"
    )


def test_side_choices_nonempty():
    """side.choices must be non-empty (constrained vocabulary {buy, sell})."""
    field = Swap._meta.get_field("side")
    assert field.choices, "side field must have non-empty choices"


def test_side_choices_fit_max_length():
    """Every side choice value must fit within the declared max_length."""
    field = Swap._meta.get_field("side")
    max_len = field.max_length
    for value, _label in field.choices:
        assert len(value) <= max_len, (
            f"Choice value {value!r} (len={len(value)}) exceeds max_length={max_len}"
        )


def test_side_choices_vocabulary():
    """side choices must include exactly {buy, sell}."""
    field = Swap._meta.get_field("side")
    values = {v for v, _ in field.choices}
    assert values == {"buy", "sell"}, f"Unexpected side vocabulary: {values!r}"
