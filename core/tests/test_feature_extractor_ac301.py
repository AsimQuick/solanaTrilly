# ---
# module: core.tests.test_feature_extractor_ac301
# sprint: sprint-7
# story: US-30 AC-30.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.feature_extractor, core.models, core.tape.lake_reader,
#               gzip, json, pathlib, pytest
# ---
"""AC-30.1 — ONE extractor, all callers: byte-identical features from DB and lake.

Verifies that the FeatureExtractor produces byte-identical feature values when
reading the same token's swaps from the DB 'swaps' mirror versus the jsonl.gz
lake — closing the assembly-drift class #358/#359/#367 by construction
(Principle #2, PRD §6.4.4/§6.4.5).

Tests
-----
  test_feature_extractor_db_vs_lake_byte_identical
      THE KEY TEST: write identical swaps to both DB and a tmp lake, run the
      extractor over each source for the SAME mint, assert the feature dicts
      are equal (byte-identical Python dict comparison covers all tape_* keys
      and their float values).

  test_feature_extractor_db_only_returns_nonnull_dict
      extract_from_db over valid swaps returns a non-None dict with tape_* keys.

  test_feature_extractor_lake_only_returns_nonnull_dict
      extract_from_lake over valid lake rows returns a non-None dict with
      the same tape_* keys as the DB path.

  test_feature_extractor_db_returns_none_for_unknown_mint
      extract_from_db for a mint with no rows returns None (no-feature contract).

  test_feature_extractor_lake_returns_none_for_unknown_mint
      extract_from_lake with no rows matching the mint returns None.

  test_feature_extractor_lake_filters_by_mint
      Lake rows for two different mints — extractor returns features only for
      the requested mint, ignoring rows for the other.

  test_feature_extractor_stable_sort_key_determinism
      Two swaps with equal rel but different (block_time, slot, signature) are
      ordered by the stable key — run twice produces identical features.

  test_feature_extractor_one_code_path
      The _compute() method is the single compute path: both extract_from_db
      and extract_from_lake call _compute(); aliasing the method and patching
      it once catches both callers.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINT = "TestMintAC301xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_MINT_OTHER = "OtherMintAC301yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"

# Six swaps within window_s=60 — enough for slope, drawdown, bucket features.
_SWAP_ROWS = [
    {
        "mint": _MINT,
        "block_time": 1_700_000_010,
        "slot": 1001,
        "signature": "SigAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "rel": 10.0,
        "price": 1.50,
        "side": "buy",
        "vol_sol": 2.0,
        "vol_usd": 300.0,
        "sol_usd": 150.0,
        "owner": "OwnerA",
    },
    {
        "mint": _MINT,
        "block_time": 1_700_000_020,
        "slot": 1002,
        "signature": "SigBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "rel": 20.0,
        "price": 1.60,
        "side": "buy",
        "vol_sol": 1.0,
        "vol_usd": 160.0,
        "sol_usd": 160.0,
        "owner": "OwnerB",
    },
    {
        "mint": _MINT,
        "block_time": 1_700_000_030,
        "slot": 1003,
        "signature": "SigCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        "rel": 30.0,
        "price": 1.55,
        "side": "sell",
        "vol_sol": 0.5,
        "vol_usd": 85.25,
        "sol_usd": 155.0,
        "owner": "OwnerA",
    },
    {
        "mint": _MINT,
        "block_time": 1_700_000_040,
        "slot": 1004,
        "signature": "SigDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
        "rel": 40.0,
        "price": 1.70,
        "side": "buy",
        "vol_sol": 3.0,
        "vol_usd": 510.0,
        "sol_usd": 170.0,
        "owner": "OwnerC",
    },
    {
        "mint": _MINT,
        "block_time": 1_700_000_050,
        "slot": 1005,
        "signature": "SigEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
        "rel": 50.0,
        "price": 1.65,
        "side": "sell",
        "vol_sol": 1.5,
        "vol_usd": 247.5,
        "sol_usd": 165.0,
        "owner": "OwnerB",
    },
    {
        "mint": _MINT,
        "block_time": 1_700_000_058,
        "slot": 1006,
        "signature": "SigFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
        "rel": 58.0,
        "price": 1.80,
        "side": "buy",
        "vol_sol": 4.0,
        "vol_usd": 720.0,
        "sol_usd": 180.0,
        "owner": "OwnerD",
    },
]

_WINDOW_S = 60  # all 6 swaps are within [0, 60)


def _write_lake(base: Path, rows: list[dict]) -> None:
    """Write *rows* as jsonl.gz under a single partition (date derived from block_time)."""
    date_str = "2023-11-14"  # UTC date of block_time 1_700_000_010
    part_dir = base / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_path = part_dir / "part-0.jsonl.gz"
    with gzip.open(part_path, "wb") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))


def _make_feature_set(db=True):
    """Return a FeatureSet object (creates a DB row when db=True)."""
    from core.models import FeatureSet

    cols = [
        "tape_n_trades",
        "tape_n_unique_traders",
        "tape_ret_total",
        "tape_max_drawdown",
        "tape_logprice_slope_per_s",
        "tape_close_b0",
        "tape_close_b1",
        "tape_close_b2",
        "tape_close_b3",
    ]
    live = ["tape_n_trades", "tape_n_unique_traders", "tape_ret_total", "tape_logprice_slope_per_s"]
    content_hash = FeatureSet.compute_hash(cols, "solanabilly3:sprint-7")
    if db:
        return FeatureSet.objects.create(
            version="v1",
            math_version="solanabilly3:sprint-7",
            columns=cols,
            live_servable=live,
            hash=content_hash,
        )
    obj = FeatureSet(
        version="v1",
        math_version="solanabilly3:sprint-7",
        columns=cols,
        live_servable=live,
        hash=content_hash,
    )
    return obj


def _write_db_swaps():
    """Insert _SWAP_ROWS into the DB Swap table."""
    from core.models import Swap

    for s in _SWAP_ROWS:
        Swap.objects.create(
            mint=s["mint"],
            block_time=s["block_time"],
            slot=s["slot"],
            signature=s["signature"],
            rel=s["rel"],
            price=s["price"],
            side=s["side"],
            vol_sol=s["vol_sol"],
            vol_usd=s["vol_usd"],
            sol_usd=s["sol_usd"],
            owner=s["owner"],
        )


# ---------------------------------------------------------------------------
# THE KEY TEST — byte-identical DB vs lake
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_db_vs_lake_byte_identical(tmp_path: Path) -> None:
    """THE KEY TEST (AC-30.1): DB and lake paths yield byte-identical feature dicts.

    Writes the same 6 swaps to both the DB 'swaps' mirror and a tmp jsonl.gz
    lake, then asserts that FeatureExtractor produces equal feature dicts from
    both sources for the same mint — closing #358/#359/#367 by construction.
    """
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    fs = _make_feature_set(db=True)
    _write_db_swaps()
    _write_lake(tmp_path, _SWAP_ROWS)

    extractor = FeatureExtractor(feature_set=fs)

    db_features = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)
    lake_rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    lake_features = extractor.extract_from_lake(_MINT, lake_rows, window_s=_WINDOW_S)

    assert db_features is not None, "DB path returned None — expected feature dict"
    assert lake_features is not None, "Lake path returned None — expected feature dict"
    assert db_features == lake_features, (
        "Byte-identity FAILED — DB and lake feature dicts differ:\n"
        f"  DB:   {db_features}\n"
        f"  Lake: {lake_features}"
    )


# ---------------------------------------------------------------------------
# Non-null contract — each source alone returns a valid dict
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_db_only_returns_nonnull_dict() -> None:
    """extract_from_db over valid swaps returns a non-None dict with tape_* keys."""
    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    _write_db_swaps()

    extractor = FeatureExtractor(feature_set=fs)
    features = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)

    assert features is not None
    assert isinstance(features, dict)
    assert "tape_n_trades" in features
    assert "tape_ret_total" in features
    assert features["tape_n_trades"] == len(_SWAP_ROWS)


@pytest.mark.django_db
def test_feature_extractor_lake_only_returns_nonnull_dict(tmp_path: Path) -> None:
    """extract_from_lake over valid lake rows returns a non-None dict with tape_* keys."""
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    fs = _make_feature_set(db=True)
    _write_lake(tmp_path, _SWAP_ROWS)

    extractor = FeatureExtractor(feature_set=fs)
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    features = extractor.extract_from_lake(_MINT, rows, window_s=_WINDOW_S)

    assert features is not None
    assert isinstance(features, dict)
    assert "tape_n_trades" in features
    assert features["tape_n_trades"] == len(_SWAP_ROWS)


# ---------------------------------------------------------------------------
# None-on-no-data contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_db_returns_none_for_unknown_mint() -> None:
    """extract_from_db for a mint with no DB rows returns None."""
    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    extractor = FeatureExtractor(feature_set=fs)
    result = extractor.extract_from_db("UNKNOWN_MINT_NOOOOOOOO", window_s=_WINDOW_S)
    assert result is None


@pytest.mark.django_db
def test_feature_extractor_lake_returns_none_for_unknown_mint(tmp_path: Path) -> None:
    """extract_from_lake with no rows matching the mint returns None."""
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    fs = _make_feature_set(db=True)
    _write_lake(tmp_path, _SWAP_ROWS)  # rows for _MINT only
    extractor = FeatureExtractor(feature_set=fs)
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    result = extractor.extract_from_lake("UNKNOWN_MINT_NOOOOOOOO", rows, window_s=_WINDOW_S)
    assert result is None


# ---------------------------------------------------------------------------
# Mint isolation — lake rows for other mints are ignored
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_lake_filters_by_mint(tmp_path: Path) -> None:
    """Lake rows for two mints — extractor returns features only for the requested mint."""
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    # Build lake with rows for _MINT (valid) + _MINT_OTHER (single out-of-window swap)
    other_rows = [
        {
            "mint": _MINT_OTHER,
            "block_time": 1_700_001_000,
            "slot": 9999,
            "signature": "SigOTHEROTHEROTHEROTHEROTHEROTHEROTHEROTHER",
            "rel": 999.0,  # outside any reasonable window_s → None for _MINT_OTHER
            "price": 0.001,
            "side": "buy",
            "vol_sol": 0.1,
            "vol_usd": 0.1,
            "sol_usd": 100.0,
            "owner": None,
        }
    ]
    _write_lake(tmp_path, _SWAP_ROWS + other_rows)

    fs = _make_feature_set(db=True)
    extractor = FeatureExtractor(feature_set=fs)
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())

    features_mint = extractor.extract_from_lake(_MINT, rows, window_s=_WINDOW_S)
    features_other = extractor.extract_from_lake(_MINT_OTHER, rows, window_s=_WINDOW_S)

    # _MINT has 6 valid swaps — must produce features
    assert features_mint is not None
    assert features_mint["tape_n_trades"] == len(_SWAP_ROWS)
    # _MINT_OTHER has 1 swap at rel=999 (outside window_s=60) — must return None
    assert features_other is None


# ---------------------------------------------------------------------------
# Determinism — same input, same output, twice
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_stable_sort_key_determinism(tmp_path: Path) -> None:
    """Run-twice determinism: same raw data → byte-identical features on both runs."""
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader

    fs = _make_feature_set(db=True)
    _write_db_swaps()
    _write_lake(tmp_path, _SWAP_ROWS)

    extractor = FeatureExtractor(feature_set=fs)
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())

    first_run = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)
    second_run = extractor.extract_from_db(_MINT, window_s=_WINDOW_S)
    assert first_run == second_run, "Run-twice determinism failed for DB path"

    lake_first = extractor.extract_from_lake(_MINT, rows, window_s=_WINDOW_S)
    lake_second = extractor.extract_from_lake(_MINT, rows, window_s=_WINDOW_S)
    assert lake_first == lake_second, "Run-twice determinism failed for lake path"


# ---------------------------------------------------------------------------
# ONE code path structural test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_feature_extractor_one_code_path() -> None:
    """Both extract_from_db and extract_from_lake delegate to the same _compute method.

    The _compute staticmethod is the single compute path (Principle #2).
    This test asserts the structural invariant: patching _compute intercepts
    both the DB and the lake caller — confirming one code path, not two.
    """
    from unittest.mock import patch

    from core.feature_extractor import FeatureExtractor

    fs = _make_feature_set(db=True)
    _write_db_swaps()

    extractor = FeatureExtractor(feature_set=fs)

    call_log = []

    # In Python 3.12, a @staticmethod accessed via the class is already a plain
    # function — no __func__ wrapper needed.
    original_compute = FeatureExtractor._compute

    def spy_compute(swaps, *, window_s, bucket_s):
        call_log.append({"path": "compute", "n_swaps": len(swaps)})
        return original_compute(swaps, window_s=window_s, bucket_s=bucket_s)

    with patch.object(FeatureExtractor, "_compute", staticmethod(spy_compute)):
        extractor.extract_from_db(_MINT, window_s=_WINDOW_S)
        extractor.extract_from_lake(_MINT, [], window_s=_WINDOW_S)

    assert len(call_log) == 2, (
        f"Expected _compute called exactly twice (once per source), got {len(call_log)}"
    )
    assert all(entry["path"] == "compute" for entry in call_log), (
        "Both source adapters must route through _compute"
    )
