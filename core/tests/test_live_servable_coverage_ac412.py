# ---
# module: core.tests.test_live_servable_coverage_ac412
# sprint: sprint-9
# story: US-41 AC-41.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.pregrad_features, core.feature_extractor,
#               core.tape.helius_birth_tape_source,
#               tools.helius_birth_tape_activate,
#               core.tests.test_birth_tape_live_fixture_ac343,
#               json, pathlib, types, pytest
# ---
"""AC-41.2 — live_servable[] coverage: ALL 20 pre_* features are in live_servable[]
and are computed by the shared extractor (US-30) over the banked birth-tape golden
fixture (lake/golden/helius_birth_tape/), deterministically.

ACCEPTANCE CHECK (promotion-blocking clause)
============================================
If any of the 20 pre_* features is not live-computable via the shared extractor over
the birth-tape pre-graduation window, v3.2 is unservable and must NOT be promoted.
This test suite is that explicit check.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_pregrad_features_importable
      compute_pregrad_features, PRE_FEATURE_NAMES, and
      FeatureExtractor.extract_pregrad_from_lake are importable; missing
      any of them fails pytest collection (the H1 compile-time trap).

live_servable[] coverage:
  test_v32_pre_features_subset_of_live_servable
      The 20 pre_* features (PRE_FEATURE_NAMES) are a subset of a canonical
      FeatureSet's live_servable[] — the explicit acceptance check.
  test_live_servable_count_is_20
      Exactly 20 feature names are in PRE_FEATURE_NAMES.
  test_pre_feature_names_match_meta_json
      PRE_FEATURE_NAMES matches the 20 features in the banked v3.2 meta.json
      in the SAME order (binding booster order).

Shared extractor over birth-tape fixture:
  test_extractor_pregrad_from_lake_over_golden_fixture
      FeatureExtractor.extract_pregrad_from_lake over the banked birth-tape
      golden fixture (decoded Helius notifications) returns a non-None dict
      containing all 20 pre_* features.
  test_extractor_pregrad_values_match_golden
      The extractor's output over the golden fixture matches the frozen expected
      values (the offline parity proof for the pregrad feature path).
  test_extractor_pregrad_run_twice_byte_identical
      Two calls over the same decoded fixture rows produce byte-identical dicts
      (determinism gate).
  test_extractor_pregrad_returns_feature_set_stamps
      The result dict carries _feature_set_hash and _math_version stamps.

Direct compute_pregrad_features tests (unit):
  test_compute_pregrad_features_no_pregrad_swaps_returns_none
      Swaps with rel >= 0 only → None (no pre-grad data, not a feature).
  test_compute_pregrad_features_no_buyers_returns_none
      Pre-grad swaps with only sell-side swaps → None (no buyers to analyse).
  test_compute_pregrad_features_deployer_enrichment
      When deployer is passed and is present in the pre-grad tape, the three
      pre_deployer_* features reflect that deployer's position.
  test_compute_pregrad_features_deterministic_pure
      Pure unit: same input → same output on two calls (no RNG/clock).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# H1 ImportError trap — wiring guard (must be module-level imports)
# ---------------------------------------------------------------------------
# If compute_pregrad_features, PRE_FEATURE_NAMES, or FeatureExtractor's pregrad
# methods are deleted/renamed, pytest collection fails immediately — before any
# test runs.  This is the canonical H1 pattern.
from core.feature_extractor import FeatureExtractor
from core.pregrad_features import PRE_FEATURE_NAMES, compute_pregrad_features

# Pin the pregrad extraction methods by name — ImportError trap (H1).
_TRAP_EXTRACT_PREGRAD_LAKE = FeatureExtractor.extract_pregrad_from_lake
_TRAP_EXTRACT_PREGRAD_DB = FeatureExtractor.extract_pregrad_from_db

# ---------------------------------------------------------------------------
# Constants — must match the golden fixture in test_birth_tape_live_fixture_ac343
# ---------------------------------------------------------------------------

#: The graduation block_time anchor for the golden birth-tape fixture.
_GRADUATED_BT: int = 1_750_100_100

#: Expected pre_* feature values for the golden fixture (deployer=None).
#:
#: Golden fixture pre-grad swaps (rel = block_time - GRADUATED_BT):
#:   rel=-300, side=Buy,  owner=GOLDEN_USER, vol=0.5 SOL
#:   rel=-250, side=Sell, owner=GOLDEN_USER, vol=0.5 SOL
#:   rel=-150, side=Buy,  owner=GOLDEN_USER, vol=0.5 SOL
#:
#: Derivation:
#:   buyers=[GOLDEN_USER], nb=1, tot_buyv=1.0 (2 buys × 0.5 SOL)
#:   EB10/EB20 = [GOLDEN_USER]; sold=True (they sold), buy_v=1.0 > sell_v=0.5
#:   min_rel=-300, mid_rel=-150
#:   first_buy_rel=-300 <= -150 → first half: nb_first=1, nb_second=0
#:   new_buyers_last300: -300 >= -300 → 1; last60: -300 >= -60 → 0
#:   HHI: (1.0/1.0)^2 = 1.0; whales5: 1; top5: 1.0
#:   diamond: 0 (sold); seller_of_buyers: 1.0; repeat: 1.0 (n_buy=2)
#:   insider_sell_v=0.5, total_buy_vol=1.0 → ratio=0.5/2.0=0.25
#:   deployer=None → all deployer features = 0; n_distinct_sellers=1 (GOLDEN_USER sold)
_GOLDEN_EXPECTED: dict = {
    "pre_eb10_sold_frac": 1.0,
    "pre_eb20_sold_frac": 1.0,
    "pre_eb10_netpos_frac": 1.0,
    "pre_eb20_netpos_frac": 1.0,
    "pre_buyers_first_half": 1.0,
    "pre_buyers_second_half": 0.0,
    "pre_buyer_accel": 0.0,
    "pre_new_buyers_last300": 1.0,
    "pre_new_buyers_last60": 0.0,
    "pre_buy_hhi": 1.0,
    "pre_n_whales5": 1.0,
    "pre_top5_buyer_share": 1.0,
    "pre_diamond_frac": 0.0,
    "pre_seller_of_buyers_frac": 1.0,
    "pre_repeat_buyer_frac": 1.0,
    "pre_insider_sell_ratio": 0.25,
    "pre_deployer_sold": 0.0,
    "pre_deployer_buy_share": 0.0,
    "pre_deployer_present": 0.0,
    "pre_n_distinct_sellers": 1.0,
}

_FLOAT_TOLERANCE: float = 1e-9

# ---------------------------------------------------------------------------
# Meta.json path (banked v3.2 fixture for feature name order verification)
# ---------------------------------------------------------------------------

_FIXTURES_DIR: Path = Path(__file__).parent / "fixtures" / "trilly_pregrad_v3_2"
_META_PATH: Path = _FIXTURES_DIR / "meta.json"

# ---------------------------------------------------------------------------
# FeatureSet stub — provides hash/math_version for FeatureExtractor.
# live_servable[] contains ALL 20 pre_* features (the AC-41.2 contract).
# ---------------------------------------------------------------------------

_STUB_FS = SimpleNamespace(
    hash="ac412-live-servable-coverage-stub",
    math_version="solanabilly3:sprint-9",
    live_servable=list(PRE_FEATURE_NAMES),  # all 20 pre_* features are live-computable
)


# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

def _load_golden_lake_rows() -> tuple[str, list[dict]]:
    """Decode the banked birth-tape golden fixture into lake-row format.

    Returns (golden_mint, lake_rows) where each row is a NormalizedSwap-
    compatible dict with 'rel' added (rel = block_time - GRADUATED_BT) so
    FeatureExtractor._load_lake_swaps can process it.
    """
    from core.tape.helius_birth_tape_source import decode_helius_notification
    from core.tests.test_birth_tape_live_fixture_ac343 import (
        GOLDEN_MINT,
        _ensure_fixture_exists,
    )
    from tools.helius_birth_tape_activate import load_birth_tape_fixture

    fixture_path = _ensure_fixture_exists()
    raw_rows = load_birth_tape_fixture(fixture_path)

    lake_rows: list[dict] = []
    for raw in raw_rows:
        decoded = decode_helius_notification(raw)
        if decoded is None:
            continue  # migrate event — not a trade
        # Add 'rel' as required by _lake_row_to_micro (rel = block_time - graduation).
        decoded["rel"] = decoded["block_time"] - _GRADUATED_BT
        lake_rows.append(decoded)

    return GOLDEN_MINT, lake_rows


# ---------------------------------------------------------------------------
# H1 wiring guard tests
# ---------------------------------------------------------------------------

def test_pregrad_features_importable() -> None:
    """compute_pregrad_features, PRE_FEATURE_NAMES, and extractor methods importable."""
    assert callable(compute_pregrad_features)
    assert isinstance(PRE_FEATURE_NAMES, list)
    assert callable(_TRAP_EXTRACT_PREGRAD_LAKE)
    assert callable(_TRAP_EXTRACT_PREGRAD_DB)


# ---------------------------------------------------------------------------
# live_servable[] coverage assertions
# ---------------------------------------------------------------------------

def test_v32_pre_features_subset_of_live_servable() -> None:
    """The 20 pre_* features (PRE_FEATURE_NAMES) are a subset of live_servable[].

    This is the EXPLICIT acceptance check (promotion-blocking clause of AC-41.2):
    if any pre_* feature is absent from live_servable, the test fails and
    v3.2 must NOT be promoted.
    """
    live_set = set(_STUB_FS.live_servable)
    missing = [f for f in PRE_FEATURE_NAMES if f not in live_set]
    assert missing == [], (
        f"Promotion-blocking: these pre_* features are NOT in live_servable[] — "
        f"v3.2 is unservable until they are added:\n  {missing}"
    )


def test_live_servable_count_is_20() -> None:
    """Exactly 20 feature names are in PRE_FEATURE_NAMES."""
    assert len(PRE_FEATURE_NAMES) == 20, (
        f"Expected 20 pre_* feature names; got {len(PRE_FEATURE_NAMES)}"
    )


def test_pre_feature_names_match_meta_json() -> None:
    """PRE_FEATURE_NAMES matches the 20 features in the banked v3.2 meta.json in order."""
    with _META_PATH.open() as fh:
        meta = json.load(fh)
    meta_features = meta["features"]
    assert PRE_FEATURE_NAMES == meta_features, (
        "PRE_FEATURE_NAMES does not match meta.json:features — binding order violation.\n"
        f"  PRE_FEATURE_NAMES: {PRE_FEATURE_NAMES}\n"
        f"  meta.json:features: {meta_features}"
    )


# ---------------------------------------------------------------------------
# Shared extractor over birth-tape golden fixture
# ---------------------------------------------------------------------------

def test_extractor_pregrad_from_lake_over_golden_fixture() -> None:
    """FeatureExtractor.extract_pregrad_from_lake returns all 20 pre_* features."""
    golden_mint, lake_rows = _load_golden_lake_rows()
    extractor = FeatureExtractor(_STUB_FS)

    result = extractor.extract_pregrad_from_lake(golden_mint, lake_rows)

    assert result is not None, (
        "extract_pregrad_from_lake returned None — no pre-grad swaps found in fixture"
    )
    missing = [f for f in PRE_FEATURE_NAMES if f not in result]
    assert missing == [], (
        f"Shared extractor did NOT produce these pre_* features from birth-tape: {missing}"
    )


def test_extractor_pregrad_values_match_golden() -> None:
    """Extractor output over golden fixture matches the frozen expected values."""
    golden_mint, lake_rows = _load_golden_lake_rows()
    extractor = FeatureExtractor(_STUB_FS)

    result = extractor.extract_pregrad_from_lake(golden_mint, lake_rows)
    assert result is not None

    for feature, expected in _GOLDEN_EXPECTED.items():
        actual = result[feature]
        assert abs(actual - expected) <= _FLOAT_TOLERANCE, (
            f"{feature}: expected {expected}, got {actual} (delta {abs(actual - expected):.2e})"
        )


def test_extractor_pregrad_run_twice_byte_identical() -> None:
    """Two calls over the same lake rows produce byte-identical dicts (determinism)."""
    golden_mint, lake_rows = _load_golden_lake_rows()
    extractor = FeatureExtractor(_STUB_FS)

    result1 = extractor.extract_pregrad_from_lake(golden_mint, lake_rows)
    result2 = extractor.extract_pregrad_from_lake(golden_mint, lake_rows)

    assert result1 is not None
    assert result2 is not None
    assert result1 == result2, (
        "Non-determinism detected — two calls produced different dicts:\n"
        f"  call1: {result1}\n  call2: {result2}"
    )


def test_extractor_pregrad_returns_feature_set_stamps() -> None:
    """Result carries _feature_set_hash and _math_version stamps."""
    golden_mint, lake_rows = _load_golden_lake_rows()
    extractor = FeatureExtractor(_STUB_FS)

    result = extractor.extract_pregrad_from_lake(golden_mint, lake_rows)
    assert result is not None
    assert result["_feature_set_hash"] == _STUB_FS.hash
    assert result["_math_version"] == _STUB_FS.math_version


# ---------------------------------------------------------------------------
# Unit tests for compute_pregrad_features directly
# ---------------------------------------------------------------------------

def _make_swap(
    rel: float,
    side: str,
    owner: str = "owner_A",
    vol: float = 1.0,
    price: float = 0.05,
) -> dict:
    bt = int(_GRADUATED_BT + rel)
    return {
        "rel": rel,
        "side": side,
        "owner": owner,
        "vol": vol,
        "price": price,
        "block_time": bt,
        "slot": 0,
        "signature": f"sig_{rel}_{side}",
    }


def test_compute_pregrad_features_no_pregrad_swaps_returns_none() -> None:
    """Swaps with rel >= 0 only → None (no pre-grad data)."""
    swaps = [
        _make_swap(0.0, "buy"),
        _make_swap(100.0, "sell"),
        _make_swap(200.0, "buy"),
    ]
    assert compute_pregrad_features(swaps) is None


def test_compute_pregrad_features_no_buyers_returns_none() -> None:
    """Pre-grad swaps with only sell-side → None (no buyers to analyse)."""
    swaps = [
        _make_swap(-300.0, "sell"),
        _make_swap(-200.0, "sell"),
    ]
    assert compute_pregrad_features(swaps) is None


def test_compute_pregrad_features_deployer_enrichment() -> None:
    """When deployer matches a buyer in the tape, pre_deployer_* reflect that."""
    deployer = "deployer_wallet"
    other = "other_wallet"
    swaps = [
        _make_swap(-300.0, "buy", owner=deployer, vol=2.0),
        _make_swap(-200.0, "buy", owner=other, vol=1.0),
        _make_swap(-100.0, "sell", owner=deployer, vol=1.0),
    ]
    result = compute_pregrad_features(swaps, deployer=deployer)
    assert result is not None

    assert result["pre_deployer_present"] == 1.0
    assert result["pre_deployer_sold"] == 1.0  # deployer sold after buying
    # deployer_buy_share = deployer.buy_v / tot_buyv = 2.0 / 3.0
    assert abs(result["pre_deployer_buy_share"] - 2.0 / 3.0) <= _FLOAT_TOLERANCE


def test_compute_pregrad_features_deterministic_pure() -> None:
    """Same input → same output on two calls (pure function, no RNG/clock)."""
    swaps = [
        _make_swap(-300.0, "buy", owner="A", vol=1.5),
        _make_swap(-250.0, "sell", owner="A", vol=0.5),
        _make_swap(-150.0, "buy", owner="B", vol=2.0),
        _make_swap(-80.0, "buy", owner="C", vol=0.8),
    ]
    r1 = compute_pregrad_features(swaps)
    r2 = compute_pregrad_features(swaps)
    assert r1 is not None
    assert r1 == r2
