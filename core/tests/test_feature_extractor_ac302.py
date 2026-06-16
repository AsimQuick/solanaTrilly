# ---
# module: core.tests.test_feature_extractor_ac302
# sprint: sprint-7
# story: US-30 AC-30.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, core.tape_microstructure
# ---
"""AC-30.2 — Leak-free by the causal cutoff (§6.4.4).

The FeatureExtractor hard-rejects any swap with rel >= window_s before
computing features.  Each feature result is stamped with its [0, window_s)
window.  Labels start strictly after window_s.

Tests
-----
  test_cutoff_excludes_swap_at_rel_equals_window_s
      A swap at exactly rel == window_s is EXCLUDED — n_trades reflects only
      the in-window swaps.

  test_cutoff_excludes_swap_beyond_window_s
      A swap at rel > window_s is EXCLUDED — same n_trades assertion.

  test_cutoff_includes_swap_just_before_window_s
      A swap at rel == window_s − epsilon (1e-9) IS included — n_trades
      counts it.

  test_feature_value_equals_windowed_subset
      The extractor's feature dict is byte-identical to compute_features
      run directly over only the in-window swaps — confirming the cutoff
      produces the correct windowed computation (not just a count check).

  test_feature_stamped_with_window_s
      The returned dict carries the ``_window_s`` key equal to window_s,
      stamping every result with its [0, window_s) extraction window.

  test_boundary_triple
      All three boundary cases in one fixture: at-boundary excluded,
      beyond excluded, just-before included — n_trades == 1 (the swap
      at window_s − epsilon only).
"""
from __future__ import annotations

from core.feature_extractor import FeatureExtractor
from core.tape_microstructure import compute_features

_WINDOW_S = 60  # seconds
_EPSILON = 1e-9


def _swap(rel: float, price: float = 1.5, side: str = "buy", vol: float = 1.0) -> dict:
    """Build a minimal §7.1 microstructure swap dict."""
    return {
        "block_time": 1_700_000_000 + int(rel),
        "slot": 1000 + int(rel),
        "signature": f"Sig{rel:.10f}".replace(".", "x"),
        "rel": rel,
        "price": price,
        "side": side,
        "vol": vol,
        "owner": "OwnerX",
    }


def _run(swaps: list[dict], window_s: int = _WINDOW_S) -> dict | None:
    """Call FeatureExtractor._compute directly (static — no instance needed)."""
    return FeatureExtractor._compute(swaps, window_s=window_s, bucket_s=15)


# ---------------------------------------------------------------------------
# Boundary tests — individual cases
# ---------------------------------------------------------------------------


def test_cutoff_excludes_swap_at_rel_equals_window_s() -> None:
    """A swap at rel == window_s is excluded; only the in-window swap counts."""
    in_window = _swap(rel=30.0)
    at_boundary = _swap(rel=float(_WINDOW_S))  # rel == 60 — must be excluded

    result = _run([in_window, at_boundary])

    assert result is not None
    assert result["tape_n_trades"] == 1, (
        f"Expected 1 in-window trade; got {result['tape_n_trades']}. "
        "Swap at rel == window_s must be excluded."
    )


def test_cutoff_excludes_swap_beyond_window_s() -> None:
    """A swap at rel > window_s is excluded; only the in-window swap counts."""
    in_window = _swap(rel=30.0)
    beyond = _swap(rel=float(_WINDOW_S) + 5.0)  # rel == 65 — must be excluded

    result = _run([in_window, beyond])

    assert result is not None
    assert result["tape_n_trades"] == 1, (
        f"Expected 1 in-window trade; got {result['tape_n_trades']}. "
        "Swap at rel > window_s must be excluded."
    )


def test_cutoff_includes_swap_just_before_window_s() -> None:
    """A swap at rel == window_s − epsilon IS included in the feature computation."""
    in_window = _swap(rel=30.0)
    just_before = _swap(rel=float(_WINDOW_S) - _EPSILON)  # 59.999…9 — must be included

    result = _run([in_window, just_before])

    assert result is not None
    assert result["tape_n_trades"] == 2, (
        f"Expected 2 in-window trades; got {result['tape_n_trades']}. "
        f"Swap at rel == window_s − {_EPSILON!r} must be included."
    )


# ---------------------------------------------------------------------------
# Value-equality test — feature matches compute_features over windowed subset
# ---------------------------------------------------------------------------


def test_feature_value_equals_windowed_subset() -> None:
    """Extractor features are byte-identical to compute_features over in-window swaps only.

    Creates three swaps: two inside the window, one at the boundary (excluded).
    The extractor result must equal compute_features called directly on just
    the two in-window swaps (ignoring _window_s which the extractor adds).
    """
    sw_a = _swap(rel=20.0, price=1.2, side="buy", vol=2.0)
    sw_b = _swap(rel=45.0, price=1.8, side="sell", vol=1.5)
    boundary = _swap(rel=float(_WINDOW_S), price=2.0, side="buy", vol=3.0)

    extractor_result = _run([sw_a, sw_b, boundary])
    assert extractor_result is not None

    # Reference: compute_features called directly over only the in-window swaps
    reference = compute_features([sw_a, sw_b], window_s=_WINDOW_S, bucket_s=15)
    assert reference is not None

    # Every tape_* key must match exactly (float identity — same computation path)
    for key, expected in reference.items():
        assert extractor_result[key] == expected, (
            f"Feature mismatch for {key!r}: "
            f"extractor={extractor_result[key]!r}, reference={expected!r}"
        )


# ---------------------------------------------------------------------------
# Window stamp test
# ---------------------------------------------------------------------------


def test_feature_stamped_with_window_s() -> None:
    """Every feature result carries _window_s equal to the requested window."""
    swaps = [_swap(rel=10.0), _swap(rel=20.0)]

    result = _run(swaps, window_s=_WINDOW_S)

    assert result is not None
    assert "_window_s" in result, "Feature dict must carry the _window_s stamp."
    assert result["_window_s"] == _WINDOW_S, (
        f"_window_s stamp mismatch: expected {_WINDOW_S}, got {result['_window_s']!r}"
    )


def test_feature_stamped_with_nondefault_window_s() -> None:
    """_window_s stamp reflects the actual window_s used, not a hardcoded default."""
    swaps = [_swap(rel=10.0), _swap(rel=20.0)]
    custom_window = 120

    result = _run(swaps, window_s=custom_window)

    assert result is not None
    assert result["_window_s"] == custom_window


# ---------------------------------------------------------------------------
# Boundary triple — all three cases in one fixture
# ---------------------------------------------------------------------------


def test_boundary_triple() -> None:
    """All three boundary cases together: only the swap just before window_s is included.

    - at_boundary (rel == window_s)         → excluded
    - beyond       (rel == window_s + 5)    → excluded
    - just_before  (rel == window_s − 1e-9) → included
    """
    just_before = _swap(rel=float(_WINDOW_S) - _EPSILON, price=1.5, side="buy", vol=1.0)
    at_boundary = _swap(rel=float(_WINDOW_S), price=1.6, side="buy", vol=2.0)
    beyond = _swap(rel=float(_WINDOW_S) + 5.0, price=1.7, side="sell", vol=0.5)

    result = _run([just_before, at_boundary, beyond])

    assert result is not None, (
        "Expected features from the one included swap (just_before), got None"
    )
    assert result["tape_n_trades"] == 1, (
        f"Expected exactly 1 included trade (just_before); got {result['tape_n_trades']}. "
        "Boundary swap and beyond-window swap must both be excluded."
    )
