# ---
# module: core.tests.test_cohort_grouping_ac502
# sprint: sprint-10
# story: US-50 AC-50.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.dashboard.cohort_grouping, core.schemas, ast, pathlib
# ---
"""AC-50.2 — cohort wall grouping/sorting tests.

Verifies:
  (a) Import traps (H1): apply_grouping and assign_group_key are importable.
  (b) No literals (Principle #1): cohort_grouping.py contains no numeric
      threshold literals (all bucket boundaries come from CohortGroupingConfig).
  (c) Partition correctness: each of the 5 grouping dimensions produces the
      EXACT partition over the banked 4-token corpus.
  (d) Clean offline degradation (H4): unavailable fields (outcome=None,
      score=None, exit_trigger=None when offline) return None — no crash,
      no fabricated value.
  (e) Sort correctness: sort_by within a group orders entries correctly,
      with None keys last.
  (f) Config-driven thresholds: custom CohortGroupingConfig produces a
      different partition, proving boundaries are not hard-coded literals.

Banked corpus fixture:
  Token A: outcome="win",     score=0.85, exit_trigger="take_profit", depth=500.0,  t0=7*3600
  Token B: outcome="loss",    score=0.25, exit_trigger=None,          depth=80.0,   t0=14*3600
  Token C: outcome="neutral", score=0.55, exit_trigger="timeout",     depth=1500.0, t0=20*3600
  Token D: outcome=None,      score=None, exit_trigger=None,          depth=None,   t0=None

Default config: score_band [0.3, 0.7], depth_bucket [50.0, 200.0, 1000.0],
                time_of_day [6, 12, 18].

Expected default partitions:
  outcome:      "loss"→[B], "neutral"→[C], "win"→[A], None→[D]
  score_band:   "band_0"→[B (0.25<0.3)], "band_1"→[C (0.3≤0.55<0.7)], "band_2"→[A (≥0.7)], None→[D]
  exit_trigger: "take_profit"→[A], "timeout"→[C], None→[B, D]
  depth_bucket: "bucket_1"→[B (50≤80<200)], "bucket_2"→[A (200≤500<1000)], "bucket_3"→[C (≥1000)], None→[D]
  time_of_day:  "period_1"→[A (6≤7<12)], "period_2"→[B (12≤14<18)], "period_3"→[C (18≤20)], None→[D]
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# H1 ImportError traps — fail pytest COLLECTION if functions are removed
# ---------------------------------------------------------------------------
from core.dashboard.cohort_grouping import (  # noqa: E402
    VALID_GROUP_KEYS,
    apply_grouping,
    assign_group_key,
    derive_meta_from_rows,
)
from core.schemas import CohortGroupingConfig

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
COHORT_GROUPING_PY = REPO_ROOT / "core" / "dashboard" / "cohort_grouping.py"

# ---------------------------------------------------------------------------
# Banked corpus — 4 tokens with known metadata
# ---------------------------------------------------------------------------
_MINT_A = "AC502_MINT_A"
_MINT_B = "AC502_MINT_B"
_MINT_C = "AC502_MINT_C"
_MINT_D = "AC502_MINT_D"

_CFG = CohortGroupingConfig()  # defaults: score [0.3, 0.7], depth [50, 200, 1000], tod [6, 12, 18]

_ENTRIES = [
    {
        "mint": _MINT_A,
        "candles": [],
        "meta": {
            "outcome": "win",
            "score": 0.85,
            "exit_trigger": "take_profit",
            "depth": 500.0,
            "t0": 7 * 3600,  # 7:00 AM UTC
        },
    },
    {
        "mint": _MINT_B,
        "candles": [],
        "meta": {
            "outcome": "loss",
            "score": 0.25,
            "exit_trigger": None,  # offline clean degradation
            "depth": 80.0,
            "t0": 14 * 3600,  # 2:00 PM UTC
        },
    },
    {
        "mint": _MINT_C,
        "candles": [],
        "meta": {
            "outcome": "neutral",
            "score": 0.55,
            "exit_trigger": "timeout",
            "depth": 1500.0,
            "t0": 20 * 3600,  # 8:00 PM UTC
        },
    },
    {
        "mint": _MINT_D,
        "candles": [],
        "meta": {
            "outcome": None,
            "score": None,
            "exit_trigger": None,
            "depth": None,
            "t0": None,
        },
    },
]


# ===========================================================================
# (a) H1 Import traps
# ===========================================================================

def test_import_trap_apply_grouping():
    """H1: apply_grouping is importable — fails collection if deleted."""
    assert apply_grouping is not None


def test_import_trap_assign_group_key():
    """H1: assign_group_key is importable — fails collection if deleted."""
    assert assign_group_key is not None


# ===========================================================================
# (b) No numeric literals (Principle #1)
# ===========================================================================

def test_cohort_grouping_no_literals():
    """AST: cohort_grouping.py has no numeric literals used as threshold values.

    The module must delegate ALL bucket thresholds to CohortGroupingConfig.
    Allowed numeric literals: 0 (modulo/floor-division math), 3600 (hour from
    Unix — this is a unit conversion constant, NOT a threshold). No float
    literals that look like thresholds (e.g. 0.3, 0.7, 50.0, 200.0, 1000.0,
    6, 12, 18) may appear in the source.
    """
    src = COHORT_GROUPING_PY.read_text()
    tree = ast.parse(src)

    # These are the threshold values that must NOT appear as literals
    forbidden_floats = {0.3, 0.7, 50.0, 200.0, 1000.0}
    forbidden_ints_as_thresholds = {6, 12, 18, 50, 200, 1000}

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, float) and node.value in forbidden_floats:
                pytest.fail(
                    f"cohort_grouping.py contains a numeric threshold literal: {node.value!r} "
                    f"(line {node.lineno}). All thresholds must come from CohortGroupingConfig."
                )
            if isinstance(node.value, int) and node.value in forbidden_ints_as_thresholds:
                pytest.fail(
                    f"cohort_grouping.py contains a numeric threshold literal: {node.value!r} "
                    f"(line {node.lineno}). All thresholds must come from CohortGroupingConfig."
                )


# ===========================================================================
# (c) VALID_GROUP_KEYS constant
# ===========================================================================

def test_valid_group_keys_constant():
    """VALID_GROUP_KEYS contains all 5 expected grouping dimensions."""
    expected = {"outcome", "score_band", "exit_trigger", "depth_bucket", "time_of_day"}
    assert VALID_GROUP_KEYS == expected


# ===========================================================================
# (d) assign_group_key — per-dimension unit tests
# ===========================================================================

def test_assign_group_key_outcome_available():
    """outcome group key returns the outcome string when available."""
    entry = {"mint": _MINT_A, "candles": [], "meta": {"outcome": "win"}}
    assert assign_group_key(entry, "outcome", _CFG) == "win"


def test_assign_group_key_outcome_unavailable():
    """outcome group key returns None when outcome is None (H4 — real-missing)."""
    entry = {"mint": _MINT_D, "candles": [], "meta": {"outcome": None}}
    assert assign_group_key(entry, "outcome", _CFG) is None


def test_assign_group_key_score_band_bucketing():
    """score_band key correctly assigns band_0/band_1/band_2 with default thresholds."""
    # score=0.25 < 0.3 → band_0
    entry_low = {"meta": {"score": 0.25}}
    assert assign_group_key(entry_low, "score_band", _CFG) == "band_0"

    # score=0.55 in [0.3, 0.7) → band_1
    entry_mid = {"meta": {"score": 0.55}}
    assert assign_group_key(entry_mid, "score_band", _CFG) == "band_1"

    # score=0.85 >= 0.7 → band_2
    entry_high = {"meta": {"score": 0.85}}
    assert assign_group_key(entry_high, "score_band", _CFG) == "band_2"

    # boundary: exactly 0.3 → band_1 (not < 0.3)
    entry_boundary = {"meta": {"score": 0.3}}
    assert assign_group_key(entry_boundary, "score_band", _CFG) == "band_1"

    # boundary: exactly 0.7 → band_2 (not < 0.7)
    entry_boundary2 = {"meta": {"score": 0.7}}
    assert assign_group_key(entry_boundary2, "score_band", _CFG) == "band_2"


def test_assign_group_key_score_band_unavailable():
    """score_band returns None when score is None (H4 — real-missing)."""
    entry = {"meta": {"score": None}}
    assert assign_group_key(entry, "score_band", _CFG) is None


def test_assign_group_key_exit_trigger_available():
    """exit_trigger returns the exit_trigger string when available."""
    entry = {"meta": {"exit_trigger": "take_profit"}}
    assert assign_group_key(entry, "exit_trigger", _CFG) == "take_profit"


def test_assign_group_key_exit_trigger_unavailable_clean_degradation():
    """exit_trigger returns None when unavailable (H4 — no crash, no fabrication)."""
    # None value
    entry_none = {"meta": {"exit_trigger": None}}
    result = assign_group_key(entry_none, "exit_trigger", _CFG)
    assert result is None, "exit_trigger=None must return None (real-missing, H4)"

    # Missing key altogether
    entry_missing = {"meta": {}}
    result2 = assign_group_key(entry_missing, "exit_trigger", _CFG)
    assert result2 is None, "missing exit_trigger key must return None (real-missing, H4)"

    # meta itself missing
    entry_no_meta = {}
    result3 = assign_group_key(entry_no_meta, "exit_trigger", _CFG)
    assert result3 is None, "missing meta dict must return None (real-missing, H4)"


def test_assign_group_key_depth_bucket_bucketing():
    """depth_bucket correctly assigns bucket_0/1/2/3 with default thresholds [50, 200, 1000]."""
    # depth=30 < 50 → bucket_0
    assert assign_group_key({"meta": {"depth": 30.0}}, "depth_bucket", _CFG) == "bucket_0"

    # depth=80 in [50, 200) → bucket_1
    assert assign_group_key({"meta": {"depth": 80.0}}, "depth_bucket", _CFG) == "bucket_1"

    # depth=500 in [200, 1000) → bucket_2
    assert assign_group_key({"meta": {"depth": 500.0}}, "depth_bucket", _CFG) == "bucket_2"

    # depth=1500 >= 1000 → bucket_3
    assert assign_group_key({"meta": {"depth": 1500.0}}, "depth_bucket", _CFG) == "bucket_3"


def test_assign_group_key_depth_unavailable():
    """depth_bucket returns None when depth is None (H4 — real-missing)."""
    entry = {"meta": {"depth": None}}
    assert assign_group_key(entry, "depth_bucket", _CFG) is None


def test_assign_group_key_time_of_day_bucketing():
    """time_of_day correctly assigns period_0/1/2/3 with boundaries [6, 12, 18]."""
    # t0=2*3600 → hour=2 < 6 → period_0 (night)
    assert assign_group_key({"meta": {"t0": 2 * 3600}}, "time_of_day", _CFG) == "period_0"

    # t0=7*3600 → hour=7 in [6, 12) → period_1 (morning)
    assert assign_group_key({"meta": {"t0": 7 * 3600}}, "time_of_day", _CFG) == "period_1"

    # t0=14*3600 → hour=14 in [12, 18) → period_2 (afternoon)
    assert assign_group_key({"meta": {"t0": 14 * 3600}}, "time_of_day", _CFG) == "period_2"

    # t0=20*3600 → hour=20 >= 18 → period_3 (evening)
    assert assign_group_key({"meta": {"t0": 20 * 3600}}, "time_of_day", _CFG) == "period_3"


def test_assign_group_key_time_of_day_unavailable():
    """time_of_day returns None when t0 is None (H4 — real-missing)."""
    entry = {"meta": {"t0": None}}
    assert assign_group_key(entry, "time_of_day", _CFG) is None


def test_assign_group_key_invalid_key_raises():
    """assign_group_key raises ValueError for an unknown group_by key."""
    entry = {"meta": {}}
    with pytest.raises(ValueError, match="Unknown group_by"):
        assign_group_key(entry, "nonexistent_key", _CFG)


# ===========================================================================
# (e) Partition correctness — full banked corpus
# ===========================================================================

def test_group_by_outcome_correct_partition():
    """Group by outcome produces the exact partition over the 4-token banked corpus."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "outcome", None, _CFG)
    by_key = {g["key"]: {e["mint"] for e in g["entries"]} for g in result["groups"]}
    assert by_key.get("win") == {_MINT_A}, f"win group should be {{_MINT_A}}, got {by_key.get('win')}"
    assert by_key.get("loss") == {_MINT_B}, f"loss group should be {{_MINT_B}}, got {by_key.get('loss')}"
    assert by_key.get("neutral") == {_MINT_C}, f"neutral group should be {{_MINT_C}}, got {by_key.get('neutral')}"
    assert by_key.get(None) == {_MINT_D}, f"None group should be {{_MINT_D}}, got {by_key.get(None)}"


def test_group_by_score_band_correct_partition():
    """Group by score_band produces the exact partition over the 4-token banked corpus."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "score_band", None, _CFG)
    by_key = {g["key"]: {e["mint"] for e in g["entries"]} for g in result["groups"]}
    # B: score=0.25 < 0.3 → band_0
    assert by_key.get("band_0") == {_MINT_B}, f"band_0 should be {{_MINT_B}}, got {by_key.get('band_0')}"
    # C: score=0.55 in [0.3, 0.7) → band_1
    assert by_key.get("band_1") == {_MINT_C}, f"band_1 should be {{_MINT_C}}, got {by_key.get('band_1')}"
    # A: score=0.85 >= 0.7 → band_2
    assert by_key.get("band_2") == {_MINT_A}, f"band_2 should be {{_MINT_A}}, got {by_key.get('band_2')}"
    # D: score=None → None group
    assert by_key.get(None) == {_MINT_D}, f"None group should be {{_MINT_D}}, got {by_key.get(None)}"


def test_group_by_exit_trigger_none_groups_cleanly():
    """B and D (None exit_trigger) go to None group without crash or fabrication."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "exit_trigger", None, _CFG)
    by_key = {g["key"]: {e["mint"] for e in g["entries"]} for g in result["groups"]}
    assert by_key.get("take_profit") == {_MINT_A}
    assert by_key.get("timeout") == {_MINT_C}
    # Both B (exit_trigger=None offline) and D (all None) must be in the None group
    assert by_key.get(None) == {_MINT_B, _MINT_D}, (
        f"None exit_trigger group should be {{B, D}}, got {by_key.get(None)}. "
        "No crash, no fabricated value (H4)."
    )


def test_group_by_depth_bucket_correct_partition():
    """Group by depth_bucket produces the exact partition over the 4-token banked corpus."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "depth_bucket", None, _CFG)
    by_key = {g["key"]: {e["mint"] for e in g["entries"]} for g in result["groups"]}
    # B: depth=80 in [50, 200) → bucket_1
    assert by_key.get("bucket_1") == {_MINT_B}, f"bucket_1 should be {{_MINT_B}}, got {by_key.get('bucket_1')}"
    # A: depth=500 in [200, 1000) → bucket_2
    assert by_key.get("bucket_2") == {_MINT_A}, f"bucket_2 should be {{_MINT_A}}, got {by_key.get('bucket_2')}"
    # C: depth=1500 >= 1000 → bucket_3
    assert by_key.get("bucket_3") == {_MINT_C}, f"bucket_3 should be {{_MINT_C}}, got {by_key.get('bucket_3')}"
    # D: depth=None → None group
    assert by_key.get(None) == {_MINT_D}, f"None group should be {{_MINT_D}}, got {by_key.get(None)}"


def test_group_by_time_of_day_correct_partition():
    """Group by time_of_day produces the exact partition over the 4-token banked corpus."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "time_of_day", None, _CFG)
    by_key = {g["key"]: {e["mint"] for e in g["entries"]} for g in result["groups"]}
    # A: t0=7*3600 → hour=7 in [6, 12) → period_1
    assert by_key.get("period_1") == {_MINT_A}, f"period_1 should be {{_MINT_A}}, got {by_key.get('period_1')}"
    # B: t0=14*3600 → hour=14 in [12, 18) → period_2
    assert by_key.get("period_2") == {_MINT_B}, f"period_2 should be {{_MINT_B}}, got {by_key.get('period_2')}"
    # C: t0=20*3600 → hour=20 >= 18 → period_3
    assert by_key.get("period_3") == {_MINT_C}, f"period_3 should be {{_MINT_C}}, got {by_key.get('period_3')}"
    # D: t0=None → None group
    assert by_key.get(None) == {_MINT_D}, f"None group should be {{_MINT_D}}, got {by_key.get(None)}"


# ===========================================================================
# (f) Sort correctness
# ===========================================================================

def test_sort_by_outcome():
    """sort_by=outcome (no grouping) orders all entries by outcome key lexicographically."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), None, "outcome", _CFG)
    assert len(result["groups"]) == 1  # no grouping → one group
    entries = result["groups"][0]["entries"]
    mints_in_order = [e["mint"] for e in entries]
    # lexicographic order: loss < neutral < win < None(last)
    assert mints_in_order == [_MINT_B, _MINT_C, _MINT_A, _MINT_D], (
        f"Expected [B, C, A, D] (loss→neutral→win→None), got {mints_in_order}"
    )


def test_unavailable_field_none_last_in_sort():
    """Entries with None sort key appear last in each sorted group (H4 compliant)."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), None, "score_band", _CFG)
    entries = result["groups"][0]["entries"]
    # Last entry should be D (score=None)
    assert entries[-1]["mint"] == _MINT_D, (
        f"Token D (score=None) must sort last; got {[e['mint'] for e in entries]}"
    )


# ===========================================================================
# (g) Determinism
# ===========================================================================

def test_apply_grouping_run_twice_identical():
    """apply_grouping is deterministic: run-twice identical output over banked corpus."""
    run1 = apply_grouping(copy.deepcopy(_ENTRIES), "outcome", "score_band", _CFG)
    run2 = apply_grouping(copy.deepcopy(_ENTRIES), "outcome", "score_band", _CFG)
    assert run1 == run2, "apply_grouping must be run-twice identical"


# ===========================================================================
# (h) derive_meta_from_rows
# ===========================================================================

def test_derive_meta_from_rows_computes_t0_and_depth():
    """derive_meta_from_rows correctly computes t0 (min block_time) and depth (sum vol_sol)."""
    rows = [
        {"mint": "MINT_X", "block_time": 100, "vol_sol": 50.0},
        {"mint": "MINT_X", "block_time": 50, "vol_sol": 30.0},
        {"mint": "MINT_X", "block_time": 200, "vol_sol": 20.0},
        {"mint": "MINT_Y", "block_time": 10, "vol_sol": 999.0},  # different mint, excluded
    ]
    meta = derive_meta_from_rows("MINT_X", rows)
    assert meta["t0"] == 50, f"t0 should be min block_time=50, got {meta['t0']}"
    assert meta["depth"] == 100.0, f"depth should be sum=100.0, got {meta['depth']}"


def test_derive_meta_from_rows_unavailable_fields_none():
    """derive_meta_from_rows returns None for outcome/score/exit_trigger (H4 — real-missing)."""
    rows = [{"mint": "MINT_X", "block_time": 1, "vol_sol": 10.0}]
    meta = derive_meta_from_rows("MINT_X", rows)
    assert meta["outcome"] is None, "outcome must be None (real-missing, H4)"
    assert meta["score"] is None, "score must be None (real-missing, H4)"
    assert meta["exit_trigger"] is None, "exit_trigger must be None (real-missing, H4)"


def test_derive_meta_from_rows_empty_for_unknown_mint():
    """derive_meta_from_rows returns all-None for a mint not in rows."""
    rows = [{"mint": "MINT_OTHER", "block_time": 1, "vol_sol": 10.0}]
    meta = derive_meta_from_rows("MINT_UNKNOWN", rows)
    assert meta["t0"] is None
    assert meta["depth"] is None
    assert meta["outcome"] is None
    assert meta["score"] is None
    assert meta["exit_trigger"] is None


# ===========================================================================
# (i) Config-driven thresholds (Principle #1)
# ===========================================================================

def test_config_driven_thresholds_custom():
    """Custom CohortGroupingConfig produces a different partition — proves config-driven."""
    # With default config, score=0.55 → band_1 (0.3≤0.55<0.7)
    default_key = assign_group_key({"meta": {"score": 0.55}}, "score_band", _CFG)
    assert default_key == "band_1"

    # With tighter thresholds [0.4, 0.6], score=0.55 is still band_1 but band_0 threshold changes
    custom_cfg = CohortGroupingConfig(
        score_band_thresholds=[0.4, 0.6],
        depth_bucket_thresholds=[100.0, 500.0],
        time_of_day_hour_boundaries=[8, 16],
    )
    # score=0.35 < 0.4 → band_0 with custom; but band_0 with default too (0.35 < 0.3? No — 0.35 > 0.3)
    # Actually 0.35 ≥ 0.3 → band_1 with default; but 0.35 < 0.4 → band_0 with custom
    custom_key = assign_group_key({"meta": {"score": 0.35}}, "score_band", custom_cfg)
    default_key2 = assign_group_key({"meta": {"score": 0.35}}, "score_band", _CFG)
    assert custom_key != default_key2, (
        f"score=0.35 should produce different keys under custom vs default config "
        f"(custom={custom_key!r}, default={default_key2!r}). "
        "This proves thresholds are NOT hard-coded — they come from config (Principle #1)."
    )
    assert custom_key == "band_0"   # 0.35 < 0.4
    assert default_key2 == "band_1"  # 0.3 ≤ 0.35 < 0.7


def test_config_driven_depth_custom():
    """Custom depth thresholds produce a different depth_bucket partition (Principle #1)."""
    custom_cfg = CohortGroupingConfig(
        score_band_thresholds=[0.3, 0.7],
        depth_bucket_thresholds=[100.0, 500.0],  # different from default [50, 200, 1000]
        time_of_day_hour_boundaries=[6, 12, 18],
    )
    # depth=80 — with default [50, 200, 1000] → bucket_1; with custom [100, 500] → bucket_0
    default_key = assign_group_key({"meta": {"depth": 80.0}}, "depth_bucket", _CFG)
    custom_key = assign_group_key({"meta": {"depth": 80.0}}, "depth_bucket", custom_cfg)
    assert default_key == "bucket_1"
    assert custom_key == "bucket_0"
    assert default_key != custom_key, "Depth thresholds are config-driven (Principle #1)"


def test_apply_grouping_invalid_group_by_raises():
    """apply_grouping raises ValueError for an unknown group_by key."""
    with pytest.raises(ValueError, match="Unknown group_by"):
        apply_grouping(copy.deepcopy(_ENTRIES), "bad_key", None, _CFG)


def test_apply_grouping_invalid_sort_by_raises():
    """apply_grouping raises ValueError for an unknown sort_by key."""
    with pytest.raises(ValueError, match="Unknown sort_by"):
        apply_grouping(copy.deepcopy(_ENTRIES), None, "bad_key", _CFG)


def test_apply_grouping_none_group_by_single_group():
    """When group_by is None, all entries are in one group with key=None."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), None, None, _CFG)
    assert result["group_by"] is None
    assert result["sort_by"] is None
    assert len(result["groups"]) == 1
    assert result["groups"][0]["key"] is None
    assert result["groups"][0]["count"] == len(_ENTRIES)


def test_apply_grouping_non_none_keys_before_none():
    """Non-None group keys appear lexicographically before the None group."""
    result = apply_grouping(copy.deepcopy(_ENTRIES), "outcome", None, _CFG)
    keys = [g["key"] for g in result["groups"]]
    # None must be last
    assert keys[-1] is None, f"None group must be last; got keys={keys}"
    # All non-None keys must come before
    non_none_keys = [k for k in keys if k is not None]
    assert non_none_keys == sorted(non_none_keys), (
        f"Non-None keys must be lexicographically sorted; got {non_none_keys}"
    )
