# ---
# module: core.tests.test_feature_set_ac292
# sprint: sprint-7
# story: US-29 AC-29.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.models, hashlib, json
# ---
"""AC-29.2 — FeatureSet.compute_hash determinism tests.

The hash is a pure function of ordered columns[] + math_version (§6.4.5).
Verified properties:
  1. Identical inputs → identical hash (deterministic)
  2. Reordering columns → different hash (column order is encoded)
  3. Bumping math_version → different hash
"""
from core.models import FeatureSet

_COLS = [
    "tape_n_trades",
    "tape_n_unique_traders",
    "tape_ret_total",
    "tape_max_drawdown",
    "tape_logprice_slope_per_s",
    "tape_close_b0",
    "tape_close_b1",
]
_MATH_VERSION = "solanabilly3:sprint-7"


def test_same_inputs_produce_same_hash():
    """Identical columns + math_version → byte-identical hash every call."""
    h1 = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    h2 = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    assert h1 == h2


def test_hash_is_64_hex_chars():
    """SHA-256 hex digest is exactly 64 characters."""
    h = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_reorder_columns_changes_hash():
    """Swapping two columns produces a different hash — order is encoded."""
    cols_reordered = list(_COLS)
    cols_reordered[0], cols_reordered[1] = cols_reordered[1], cols_reordered[0]
    h_original = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    h_reordered = FeatureSet.compute_hash(cols_reordered, _MATH_VERSION)
    assert h_original != h_reordered


def test_reverse_columns_changes_hash():
    """Reversing the column list produces a different hash."""
    cols_reversed = list(reversed(_COLS))
    h_original = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    h_reversed = FeatureSet.compute_hash(cols_reversed, _MATH_VERSION)
    assert h_original != h_reversed


def test_math_version_bump_changes_hash():
    """Bumping math_version changes the hash even with identical columns."""
    h1 = FeatureSet.compute_hash(_COLS, "solanabilly3:sprint-7")
    h2 = FeatureSet.compute_hash(_COLS, "solanabilly3:sprint-8")
    assert h1 != h2


def test_different_math_versions_all_differ():
    """Every distinct math_version string produces a unique hash."""
    versions = [
        "solanabilly3:sprint-7",
        "solanabilly3:sprint-8",
        "solanabilly3:v2.0",
        "custom:experiment-1",
    ]
    hashes = [FeatureSet.compute_hash(_COLS, v) for v in versions]
    assert len(hashes) == len(set(hashes)), "All math_version variants must hash differently"


def test_single_column_reorder_changes_hash():
    """Moving a single column to a different position changes the hash."""
    cols_a = ["tape_n_trades", "tape_ret_total", "tape_max_drawdown"]
    cols_b = ["tape_ret_total", "tape_n_trades", "tape_max_drawdown"]
    assert FeatureSet.compute_hash(cols_a, _MATH_VERSION) != FeatureSet.compute_hash(
        cols_b, _MATH_VERSION
    )


def test_empty_columns_is_deterministic():
    """Empty column list hashes deterministically."""
    h1 = FeatureSet.compute_hash([], _MATH_VERSION)
    h2 = FeatureSet.compute_hash([], _MATH_VERSION)
    assert h1 == h2


def test_empty_vs_nonempty_columns_differ():
    """Empty and non-empty column lists produce different hashes."""
    h_empty = FeatureSet.compute_hash([], _MATH_VERSION)
    h_nonempty = FeatureSet.compute_hash(_COLS, _MATH_VERSION)
    assert h_empty != h_nonempty


def test_compute_hash_is_pure_function():
    """compute_hash is a static method — callable without a model instance."""
    h = FeatureSet.compute_hash(["tape_n_trades"], "solanabilly3:sprint-7")
    assert isinstance(h, str)
    assert len(h) == 64
