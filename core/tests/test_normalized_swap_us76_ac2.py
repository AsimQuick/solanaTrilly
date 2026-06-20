# ---
# module: core.tests.test_normalized_swap_us76_ac2
# sprint: sprint-14
# story: US-76 AC-2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, math, json, base64, struct, pathlib,
#               core.normalized_swap, core.tape.mint_decimals,
#               core.tape.helius_birth_tape_source, core.firehose.spine
# ---
"""US-76 AC-2 — single NormalizedSwap layer: guards, per-mint decimals, dict shape parity.

Tests (binding per AC-2 + Tester revisions #405 / #358 / #288 / #287):

  #405 guards:
    test_zero_price_rejected          — swap with price=0.0 → rejected (not crash)
    test_none_price_coerced_rejected  — JSONB None price → coerced → rejected
    test_negative_price_rejected      — price < 0 → rejected
    test_dust_price_below_median_rejected — price 1e-10x median → rejected
    test_nonzero_price_accepted       — price > 0 and above dust → accepted

  #358 guards:
    test_jsonb_none_vol_sol_coerced   — real JSONB dict (None vol_sol) → not crash
    test_jsonb_none_price_coerced     — real JSONB dict (None price) → rejected
    test_jsonb_none_owner_preserved   — None owner passes through as None
    test_all_none_numeric_fields      — JSONB dict with all None numerics → rejected

  #288 — per-mint decimals:
    test_spl_6_decimal_token_amount   — 6-decimal (SPL) raw → correct ui
    test_token2022_8_decimal_token    — 8-decimal (Token-2022) raw → different ui
    test_default_decimals_is_6        — default decimals fallback is 6
    test_mint_decimals_resolver_seed  — resolver.seed + resolve round-trip
    test_mint_decimals_resolver_fallback — unknown mint → default 6
    test_mint_decimals_resolver_seed_invalid — bad decimals value → ignored

  Shape parity:
    test_helius_swap_and_offline_swap_same_shape — Helius-sourced swap and Birdeye
        offline-style swap → same §7.1 dict keys + same compute_pregrad_features input
    test_vol_is_sol_space_no_usd      — vol field is SOL-space (directives §8 Q2)

  #287 — Borsh offset regression (committed fixture, no network):
    test_borsh_offset_fixture_decode  — decode fixture with decode_helius_trade_event,
        assert sol_amount / token_amount / side / owner vs known-good values
    test_borsh_offset_price_computation — price = vsol/vtok at fixture values

  Integration:
    test_to_pregrad_swaps_none_coercion    — JSONB None dict → not crash
    test_to_pregrad_swaps_zero_price_drop  — zero-price swaps dropped silently
    test_to_pregrad_swaps_sol_volume_preserved — vol field = vol_sol (SOL-space)
    test_coerce_jsonb_none_function        — unit test the helper directly
    test_is_dust_price_function            — unit test the helper directly
"""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import pytest

from core.normalized_swap import (
    DUST_MEDIAN_MIN_SAMPLES,
    DUST_PRICE_RATIO,
    coerce_jsonb_none,
    is_dust_price,
    normalize_raw_for_features,
)
from core.tape.mint_decimals import (
    DEFAULT_BASE_DECIMALS,
    MintDecimalsResolver,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_BORSH_FIXTURE = _FIXTURES_DIR / "helius_borsh_offset_ac287.json"

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 1_750_000_000

# A realistic JSONB-shaped dict as Postgres would deserialise it after a
# JSONB column round-trip.  Key difference from a mock: None values are Python
# None (not np.nan).  Tests MUST use this dict, not a hand-built np.nan version.
_JSONB_HELIUS_SWAP: dict = {
    "mint": "BibK2MNtSSgjKqsbdCxWJZuUQkAvkuwiVU4VS3rVgvmE",
    "block_time": _GRADUATED_BLOCK_TIME - 300,
    "slot": 325_000_000,
    "signature": "5abc" + "x" * 84,
    "side": "buy",
    "price": 9.875e-05,          # from virtual reserves (already valid)
    "vol_sol": 0.75,             # SOL leg, already in SOL-space
    "vol_usd": None,             # JSONB NULL — Helius live has no USD oracle
    "sol_usd": None,             # JSONB NULL
    "owner": "EETFuvMzZW4R1DfqCovARkXT3T4vKTBhVmYU54ZGfJDw",
    "base_reserve": None,        # JSONB NULL (optional in Helius path)
    "quote_reserve": 79_000_000_000,
    "quote_mint": "So11111111111111111111111111111111111111112",
    "token_amount": 50_000_000_000,  # raw, 6-decimal → 50 000 tokens
    "source": "helius_live",
    "phase": "pre",
    "failed": False,
}

# Birdeye offline-style raw swap (all fields numeric, no None, uiAmount already in SOL).
_JSONB_BIRDEYE_SWAP: dict = {
    "mint": "BibK2MNtSSgjKqsbdCxWJZuUQkAvkuwiVU4VS3rVgvmE",
    "block_time": _GRADUATED_BLOCK_TIME - 300,
    "slot": 325_000_000,
    "signature": "5abc" + "x" * 84,
    "side": "buy",
    "price": 9.875e-05,
    "vol_sol": 0.75,
    "vol_usd": 112.5,
    "sol_usd": 150.0,
    "owner": "EETFuvMzZW4R1DfqCovARkXT3T4vKTBhVmYU54ZGfJDw",
    "base_reserve": None,
    "quote_reserve": 79_000_000_000,
    "quote_mint": "So11111111111111111111111111111111111111112",
    "source": "birdeye_backfill",
    "phase": "pre",
}

# §7.1 required keys that compute_pregrad_features reads
_FEATURE_MATH_REQUIRED_KEYS: frozenset[str] = frozenset({
    "rel", "block_time", "slot", "signature",
    "price", "side", "vol", "owner",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(raw: dict, *, price_override: float | None = None, peer_prices: list | None = None) -> dict | None:
    """Wrapper around normalize_raw_for_features with optional price override."""
    if price_override is not None:
        raw = dict(raw, price=price_override)
    return normalize_raw_for_features(
        raw,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
        peer_prices=peer_prices or [],
    )


# ===========================================================================
# coerce_jsonb_none — unit tests
# ===========================================================================


def test_coerce_jsonb_none_maps_none_to_nan() -> None:
    """None (JSONB NULL) is coerced to float('nan') (#358)."""
    result = coerce_jsonb_none(None)
    assert math.isnan(result), f"Expected nan, got {result!r}"


def test_coerce_jsonb_none_passes_float() -> None:
    """A plain float passes through unchanged."""
    assert coerce_jsonb_none(1.5) == pytest.approx(1.5)


def test_coerce_jsonb_none_passes_int() -> None:
    """An int is coerced to float."""
    assert coerce_jsonb_none(42) == pytest.approx(42.0)


def test_coerce_jsonb_none_passes_numeric_string() -> None:
    """A numeric string is coerced to float."""
    assert coerce_jsonb_none("3.14") == pytest.approx(3.14)


def test_coerce_jsonb_none_bad_string_is_nan() -> None:
    """A non-numeric string returns nan."""
    result = coerce_jsonb_none("bad")
    assert math.isnan(result)


def test_coerce_jsonb_none_nan_passes_through() -> None:
    """An existing nan passes through (not converted to something else)."""
    result = coerce_jsonb_none(float("nan"))
    assert math.isnan(result)


def test_coerce_jsonb_none_zero_passes() -> None:
    """Zero passes through as 0.0 (not nan — zero is a valid numeric value)."""
    result = coerce_jsonb_none(0)
    assert result == 0.0
    assert not math.isnan(result)


# ===========================================================================
# is_dust_price — unit tests
# ===========================================================================


def test_is_dust_price_zero_is_dust() -> None:
    """price=0.0 is always dust (#405 — zero is not safe to divide by)."""
    assert is_dust_price(0.0, []) is True


def test_is_dust_price_negative_is_dust() -> None:
    """price < 0 is always dust."""
    assert is_dust_price(-1e-5, []) is True


def test_is_dust_price_nan_is_dust() -> None:
    """nan is always dust."""
    assert is_dust_price(float("nan"), []) is True


def test_is_dust_price_no_peers_nonzero_accepted() -> None:
    """Without peer context, a nonzero positive price is accepted."""
    assert is_dust_price(1e-5, []) is False


def test_is_dust_price_few_peers_no_median_guard() -> None:
    """With fewer than DUST_MEDIAN_MIN_SAMPLES peers, only the zero guard applies."""
    peers = [1e-3, 1e-3, 1e-3]  # 3 peers < 5
    assert is_dust_price(1e-15, peers) is False  # no median guard at 3 peers


def test_is_dust_price_dust_below_median() -> None:
    """Price far below the median is dust (#405 dust-fill guard)."""
    peers = [1e-3] * DUST_MEDIAN_MIN_SAMPLES
    median = 1e-3
    dust = median * DUST_PRICE_RATIO * 0.5  # well below threshold
    assert is_dust_price(dust, peers) is True


def test_is_dust_price_above_dust_threshold_accepted() -> None:
    """Price well above the dust threshold is accepted."""
    peers = [1e-3] * DUST_MEDIAN_MIN_SAMPLES
    median = 1e-3
    good = median * DUST_PRICE_RATIO * 10  # 10x above threshold
    assert is_dust_price(good, peers) is False


def test_is_dust_price_at_exact_threshold_rejected() -> None:
    """Price exactly at the threshold is accepted (threshold is strict <)."""
    peers = [1e-3] * DUST_MEDIAN_MIN_SAMPLES
    median = 1e-3
    at_threshold = median * DUST_PRICE_RATIO  # exactly at threshold
    # is_dust_price uses strict <: price < median * ratio → at-threshold is NOT dust
    assert is_dust_price(at_threshold, peers) is False


# ===========================================================================
# #405 — zero/dust price guards via normalize_raw_for_features
# ===========================================================================


def test_zero_price_rejected() -> None:
    """swap with price=0.0 → rejected, no crash (#405)."""
    raw = dict(_JSONB_HELIUS_SWAP, price=0.0)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "Expected None for zero-price swap (#405)"


def test_none_price_coerced_rejected() -> None:
    """JSONB None price → coerced to nan → rejected (not crash) (#358 + #405)."""
    raw = dict(_JSONB_HELIUS_SWAP, price=None)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "Expected None: None price coerced to nan then rejected"


def test_negative_price_rejected() -> None:
    """Negative price is rejected."""
    raw = dict(_JSONB_HELIUS_SWAP, price=-1e-6)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "Expected None for negative-price swap"


def test_dust_price_below_median_rejected() -> None:
    """Price far below the local median is rejected (dust-fill guard, #405)."""
    raw = dict(_JSONB_HELIUS_SWAP)
    median_price = _JSONB_HELIUS_SWAP["price"]
    peers = [median_price] * DUST_MEDIAN_MIN_SAMPLES
    dust = median_price * DUST_PRICE_RATIO * 0.01  # 100x below threshold
    raw["price"] = dust
    result = normalize_raw_for_features(
        raw,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
        peer_prices=peers,
    )
    assert result is None, f"Expected None for dust price {dust!r} vs median {median_price!r}"


def test_nonzero_price_accepted() -> None:
    """A valid nonzero price produces a §7.1 dict."""
    result = _normalise(_JSONB_HELIUS_SWAP)
    assert result is not None, "Expected a §7.1 dict for valid Helius swap"
    assert result["price"] == pytest.approx(_JSONB_HELIUS_SWAP["price"])


# ===========================================================================
# #358 — JSONB None → nan coercion
# ===========================================================================


def test_jsonb_none_vol_sol_coerced_not_crash() -> None:
    """Real JSONB dict with None vol_sol → does not crash (#358).

    The REAL JSONB-shaped dict is used here (None values, not np.nan).
    When vol_sol is None it becomes 0.0 (degenerate volume, not rejected).
    """
    raw = dict(_JSONB_HELIUS_SWAP, vol_sol=None)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    # price is still valid → swap is accepted; vol_sol=None → 0.0
    assert result is not None, "Swap with None vol_sol must not crash and must be accepted"
    assert result["vol"] == 0.0, f"Expected vol=0.0 for None vol_sol, got {result['vol']!r}"


def test_jsonb_none_price_coerced_rejected() -> None:
    """Real JSONB dict with None price → coerced to nan → rejected (#358).

    Uses the real JSONB shape (None, not np.nan).  This is the production-realistic
    case: price stored as JSONB NULL comes back as Python None.
    """
    raw = dict(_JSONB_HELIUS_SWAP, price=None)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "None price must be coerced to nan and then rejected"


def test_jsonb_none_owner_preserved() -> None:
    """None owner (JSONB NULL) passes through as None (#358 — None is valid for owner)."""
    raw = dict(_JSONB_HELIUS_SWAP, owner=None)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is not None
    assert result["owner"] is None, "None owner must pass through as None"


def test_jsonb_all_none_numeric_fields_rejected() -> None:
    """A JSONB dict with ALL numeric fields None → rejected gracefully (no crash)."""
    raw = {
        "mint": "BibK2MNtSSgjKqsbdCxWJZuUQkAvkuwiVU4VS3rVgvmE",
        "block_time": _GRADUATED_BLOCK_TIME - 100,
        "slot": None,
        "signature": "abc",
        "side": "buy",
        "price": None,      # JSONB NULL → nan → rejected
        "vol_sol": None,    # JSONB NULL → 0.0
        "vol_usd": None,    # JSONB NULL
        "sol_usd": None,    # JSONB NULL
        "owner": None,
        "base_reserve": None,
        "quote_reserve": None,
        "quote_mint": "So11111111111111111111111111111111111111112",
    }
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "All-None numeric dict must be rejected (None price → nan → rejected)"


def test_jsonb_vol_usd_and_sol_usd_none_accepted() -> None:
    """vol_usd=None and sol_usd=None pass through as nan — not a rejection criterion."""
    raw = dict(_JSONB_HELIUS_SWAP)  # already has None vol_usd and sol_usd
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is not None, "None vol_usd/sol_usd must not reject a valid swap"
    assert math.isnan(result["vol_usd"]), "None vol_usd must become nan"
    assert math.isnan(result["sol_usd"]), "None sol_usd must become nan"


# ===========================================================================
# #288 — per-mint decimals
# ===========================================================================


def test_spl_6_decimal_token_amount() -> None:
    """6-decimal (SPL) raw token amount converts correctly.

    pump.fun bonding-curve tokens use 6 decimals.
    50_000_000_000 raw / 10^6 = 50 000 token units (UI amount).
    """
    resolver = MintDecimalsResolver()
    resolver.seed("BibK2MNtSSgjKqsbdCxWJZuUQkAvkuwiVU4VS3rVgvmE", decimals=6)
    dec = resolver.resolve("BibK2MNtSSgjKqsbdCxWJZuUQkAvkuwiVU4VS3rVgvmE")
    assert dec == 6
    raw_amount = 50_000_000_000
    ui_amount = raw_amount / (10 ** dec)
    assert ui_amount == pytest.approx(50_000.0)


def test_token2022_8_decimal_token_amount_differs() -> None:
    """8-decimal (Token-2022) raw → different UI amount than 6-decimal (#288).

    A Token-2022 mint with 8 decimals produces a different UI amount than an
    SPL mint with 6 decimals for the same raw integer.  Hardcoding 6 would
    produce a 100x error for 8-decimal mints.
    """
    raw_amount = 50_000_000_000

    resolver6 = MintDecimalsResolver()
    resolver6.seed("MINT_SPL_6", decimals=6)

    resolver8 = MintDecimalsResolver()
    resolver8.seed("MINT_T22_8", decimals=8)

    ui_6 = raw_amount / (10 ** resolver6.resolve("MINT_SPL_6"))
    ui_8 = raw_amount / (10 ** resolver8.resolve("MINT_T22_8"))

    assert ui_6 == pytest.approx(50_000.0)
    assert ui_8 == pytest.approx(500.0)
    assert ui_6 != ui_8, "SPL-6 and Token-2022-8 must produce different UI amounts"


def test_default_decimals_fallback_is_6() -> None:
    """DEFAULT_BASE_DECIMALS is 6 (the pump.fun SPL fallback, not hardcoded truth)."""
    assert DEFAULT_BASE_DECIMALS == 6


def test_mint_decimals_resolver_seed_and_resolve_round_trip() -> None:
    """resolver.seed() + resolve() returns the seeded value (#288)."""
    resolver = MintDecimalsResolver()
    mint = "SomeMintAddress111111111111111111111111111111"
    resolver.seed(mint, decimals=8)
    assert resolver.resolve(mint) == 8


def test_mint_decimals_resolver_unknown_mint_returns_default() -> None:
    """resolver.resolve() for an unknown mint returns DEFAULT_BASE_DECIMALS."""
    resolver = MintDecimalsResolver()
    dec = resolver.resolve("UnknownMint1111111111111111111111111111111111")
    assert dec == DEFAULT_BASE_DECIMALS


def test_mint_decimals_resolver_has_returns_true_after_seed() -> None:
    """resolver.has() returns True after seeding a mint."""
    resolver = MintDecimalsResolver()
    mint = "SomeMint111111111111111111111111111111111111"
    resolver.seed(mint, decimals=9)
    assert resolver.has(mint) is True


def test_mint_decimals_resolver_has_returns_false_for_unknown() -> None:
    """resolver.has() returns False for an unseeded mint."""
    resolver = MintDecimalsResolver()
    assert resolver.has("Unknown1111111111111111111111111111111111111") is False


def test_mint_decimals_resolver_seed_invalid_value_ignored() -> None:
    """resolver.seed() with an invalid decimals value is ignored (not stored)."""
    resolver = MintDecimalsResolver()
    mint = "SomeMint111111111111111111111111111111111111"
    resolver.seed(mint, decimals=-1)   # invalid
    resolver.seed(mint, decimals=39)   # invalid (too large for a Solana mint)
    # Neither invalid value should be stored
    assert not resolver.has(mint), "Invalid decimals must not be stored in the resolver"
    assert resolver.resolve(mint) == DEFAULT_BASE_DECIMALS


def test_normalize_raw_with_8_decimal_mint() -> None:
    """normalize_raw_for_features with base_decimals=8 stores token_ui correctly.

    Although v3.2 features don't use token_ui directly, the normaliser must
    accept base_decimals=8 without crashing and must produce the same §7.1 shape
    as for base_decimals=6 (vol is always SOL-space, not token-space).
    """
    raw = dict(_JSONB_HELIUS_SWAP)
    result_6 = normalize_raw_for_features(
        raw,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
        base_decimals=6,
    )
    result_8 = normalize_raw_for_features(
        raw,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
        base_decimals=8,
    )
    assert result_6 is not None
    assert result_8 is not None
    # vol (SOL-space) is the SAME regardless of base_decimals — it comes from vol_sol
    assert result_6["vol"] == pytest.approx(result_8["vol"]), (
        "vol (SOL-space) must be the same regardless of base_decimals"
    )
    # Both must have the same required keys
    assert _FEATURE_MATH_REQUIRED_KEYS <= result_6.keys()
    assert _FEATURE_MATH_REQUIRED_KEYS <= result_8.keys()


# ===========================================================================
# Shape parity — Helius-sourced and Birdeye offline-style → same dict keys
# ===========================================================================


def test_helius_swap_and_offline_swap_same_shape() -> None:
    """Helius-sourced and Birdeye offline-style swaps normalise to the same dict shape.

    Both sources must produce exactly the §7.1 required keys so they are
    interchangeable inputs to compute_pregrad_features.
    """
    helius_result = normalize_raw_for_features(
        _JSONB_HELIUS_SWAP,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
    )
    birdeye_result = normalize_raw_for_features(
        _JSONB_BIRDEYE_SWAP,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
    )

    assert helius_result is not None, "Helius swap must produce a §7.1 dict"
    assert birdeye_result is not None, "Birdeye swap must produce a §7.1 dict"

    # Both must have ALL required §7.1 keys
    assert _FEATURE_MATH_REQUIRED_KEYS <= helius_result.keys(), (
        f"Helius result missing keys: {_FEATURE_MATH_REQUIRED_KEYS - helius_result.keys()}"
    )
    assert _FEATURE_MATH_REQUIRED_KEYS <= birdeye_result.keys(), (
        f"Birdeye result missing keys: {_FEATURE_MATH_REQUIRED_KEYS - birdeye_result.keys()}"
    )

    # Key-set must be identical
    assert set(helius_result.keys()) == set(birdeye_result.keys()), (
        f"Shape mismatch:\n"
        f"  Helius-only: {set(helius_result.keys()) - set(birdeye_result.keys())}\n"
        f"  Birdeye-only: {set(birdeye_result.keys()) - set(helius_result.keys())}"
    )


def test_vol_is_sol_space_no_usd_introduced() -> None:
    """vol is the SOL leg (SOL-space), not USD (directives §8 Q2 — v3.2 is scale-invariant).

    No per-trade USD conversion is done.  vol == vol_sol exactly.
    """
    result = normalize_raw_for_features(
        _JSONB_HELIUS_SWAP,
        graduated_block_time=_GRADUATED_BLOCK_TIME,
    )
    assert result is not None
    assert result["vol"] == pytest.approx(_JSONB_HELIUS_SWAP["vol_sol"]), (
        f"vol must equal vol_sol (SOL-space); got vol={result['vol']!r}"
    )
    # vol_usd is None (nan) in the Helius source — must NOT default to vol*some_price
    assert math.isnan(result["vol_usd"]), (
        "vol_usd must be nan for Helius-live (no USD oracle); got {result['vol_usd']!r}"
    )


def test_rel_computation_correct() -> None:
    """rel = block_time - graduated_block_time."""
    raw = dict(_JSONB_HELIUS_SWAP)
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is not None
    expected_rel = raw["block_time"] - _GRADUATED_BLOCK_TIME
    assert result["rel"] == pytest.approx(float(expected_rel))


def test_invalid_side_rejected() -> None:
    """An invalid side value rejects the swap."""
    raw = dict(_JSONB_HELIUS_SWAP, side="long")
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "Invalid side must be rejected"


def test_missing_block_time_rejected() -> None:
    """A swap with no block_time is rejected (cannot compute rel)."""
    raw = {k: v for k, v in _JSONB_HELIUS_SWAP.items() if k != "block_time"}
    result = normalize_raw_for_features(raw, graduated_block_time=_GRADUATED_BLOCK_TIME)
    assert result is None, "Missing block_time must be rejected"


# ===========================================================================
# #287 — Borsh offset regression (committed fixture, no network)
# ===========================================================================


def test_borsh_offset_fixture_exists() -> None:
    """The committed Borsh offset regression fixture must be present in core/tests/fixtures/."""
    assert _BORSH_FIXTURE.is_file(), (
        f"Borsh offset fixture not found at {_BORSH_FIXTURE}.\n"
        "US-76 AC-2 #287: commit helius_borsh_offset_ac287.json to core/tests/fixtures/."
    )


def test_borsh_offset_fixture_decode() -> None:
    """Decode the committed pump.fun tx fixture with decode_helius_trade_event and
    assert sol_amount / token_amount / side / owner match known-good values (#287).

    No network.  The fixture encodes a real Borsh TradeEvent binary blob.
    Layout: disc(8)+mint(32)+sol_amount(u64 LE @ 40)+token_amount(u64 LE @ 48)+
            is_buy(u8 @ 56)+user(32 @ 57)+timestamp(i64 @ 89)+vsol(u64)+vtok(u64).
    """
    from core.tape.helius_birth_tape_source import decode_helius_trade_event

    assert _BORSH_FIXTURE.is_file(), f"Fixture missing: {_BORSH_FIXTURE}"

    with _BORSH_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    expected = fixture["expected"]
    raw_bytes = base64.b64decode(fixture["program_data_b64"])

    result = decode_helius_trade_event(raw_bytes)
    assert result is not None, (
        "decode_helius_trade_event returned None for the committed fixture — "
        "discriminator or buffer length check failed (#287)"
    )

    assert result["sol_amount"] == expected["sol_amount"], (
        f"sol_amount mismatch: got {result['sol_amount']}, expected {expected['sol_amount']}\n"
        "Offset regression: sol_amount is at bytes [40:48] (struct.unpack_from('<QQ', decoded, 40))"
    )
    assert result["token_amount"] == expected["token_amount"], (
        f"token_amount mismatch: got {result['token_amount']}, expected {expected['token_amount']}\n"
        "Offset regression: token_amount is at bytes [48:56]"
    )
    assert result["side"] == expected["side"], (
        f"side mismatch: got {result['side']!r}, expected {expected['side']!r}\n"
        "Offset regression: is_buy byte is at offset 56"
    )
    assert result["owner"] == expected["owner"], (
        f"owner mismatch: got {result['owner']!r}, expected {expected['owner']!r}\n"
        "Offset regression: user pubkey is at bytes [57:89]"
    )
    assert result["mint"] == expected["mint"], (
        f"mint mismatch: got {result['mint']!r}, expected {expected['mint']!r}"
    )
    assert result["block_time"] == expected["block_time"], (
        f"block_time mismatch: got {result['block_time']}, expected {expected['block_time']}\n"
        "Offset regression: timestamp is at bytes [89:97] (struct.unpack_from('<qQQ', decoded, 89))"
    )


def test_borsh_offset_price_computation() -> None:
    """Price = vsol / vtok matches known-good value at fixture offsets (#287)."""
    from core.tape.helius_birth_tape_source import decode_helius_trade_event

    assert _BORSH_FIXTURE.is_file(), f"Fixture missing: {_BORSH_FIXTURE}"

    with _BORSH_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    expected = fixture["expected"]
    raw_bytes = base64.b64decode(fixture["program_data_b64"])
    result = decode_helius_trade_event(raw_bytes)
    assert result is not None

    expected_price = expected["virtual_sol_reserves"] / expected["virtual_token_reserves"]
    computed_price = result["virtual_sol_reserves"] / result["virtual_token_reserves"]
    assert computed_price == pytest.approx(expected_price, rel=1e-9), (
        f"Price from vsol/vtok: computed={computed_price}, expected={expected_price}\n"
        "#287: if reserves are swapped the price is inverted"
    )
    assert computed_price == pytest.approx(expected["price"], rel=1e-9)


def test_borsh_offset_vol_sol_computation() -> None:
    """vol_sol = sol_amount / 1e9 (lamports → SOL) at fixture values (#287)."""
    from core.tape.helius_birth_tape_source import decode_helius_trade_event

    assert _BORSH_FIXTURE.is_file(), f"Fixture missing: {_BORSH_FIXTURE}"

    with _BORSH_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    raw_bytes = base64.b64decode(fixture["program_data_b64"])
    result = decode_helius_trade_event(raw_bytes)
    assert result is not None

    expected_vol_sol = fixture["expected"]["sol_amount"] / 1e9
    actual_vol_sol = result["sol_amount"] / 1e9
    assert actual_vol_sol == pytest.approx(expected_vol_sol), (
        f"vol_sol: got {actual_vol_sol}, expected {expected_vol_sol}"
    )
    assert actual_vol_sol == pytest.approx(fixture["expected"]["vol_sol"])


# ===========================================================================
# Integration — to_pregrad_swaps uses the normalisation layer
# ===========================================================================


def test_to_pregrad_swaps_none_coercion_not_crash() -> None:
    """to_pregrad_swaps accepts JSONB-shaped dicts with None values without crashing (#358)."""
    from core.firehose.spine import to_pregrad_swaps

    grad_bt = _GRADUATED_BLOCK_TIME
    # A real JSONB-shaped list of swaps where some numeric fields are None
    swaps = [
        {
            "block_time": grad_bt - 200 - i,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"W{i}",
            "price": 1e-4 + i * 1e-6,
            "vol_sol": None if i == 3 else 0.5,  # JSONB NULL on one swap
            "vol_usd": None,                       # all NULL (Helius-live)
            "sol_usd": None,
        }
        for i in range(10)
    ]
    # Must not raise
    result = to_pregrad_swaps(swaps, grad_bt)
    # All 10 swaps have valid price and block_time → all accepted (vol=0.0 for the None one)
    assert len(result) == 10, f"Expected 10 normalised swaps, got {len(result)}"
    # The swap with None vol_sol must have vol=0.0
    none_vol_swap = next(s for s in result if s.get("signature") == "s3")
    assert none_vol_swap["vol"] == 0.0


def test_to_pregrad_swaps_zero_price_dropped() -> None:
    """Zero-price swaps are silently dropped by to_pregrad_swaps (#405)."""
    from core.firehose.spine import to_pregrad_swaps

    grad_bt = 10_000
    swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"W{i}",
            "price": 0.0 if i == 0 else 1e-4,  # first swap has zero price
            "vol_sol": 0.5,
            "vol_usd": None,
            "sol_usd": None,
        }
        for i in range(5)
    ]
    result = to_pregrad_swaps(swaps, grad_bt)
    # Zero-price swap (i=0) must be dropped; remaining 4 must be present
    assert len(result) == 4, f"Expected 4 swaps (zero-price dropped), got {len(result)}"
    sigs = [s["signature"] for s in result]
    assert "s0" not in sigs, "Zero-price swap (s0) must be dropped"


def test_to_pregrad_swaps_sol_volume_preserved() -> None:
    """vol field in the §7.1 output equals vol_sol (SOL-space, directives §8 Q2)."""
    from core.firehose.spine import to_pregrad_swaps

    grad_bt = 10_000
    swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy" if i % 2 == 0 else "sell",
            "owner": f"W{i}",
            "price": 1e-4,
            "vol_sol": 0.5 + i * 0.1,
            "vol_usd": None,
            "sol_usd": None,
        }
        for i in range(5)
    ]
    result = to_pregrad_swaps(swaps, grad_bt)
    for orig, norm in zip(swaps, result):
        assert norm["vol"] == pytest.approx(orig["vol_sol"]), (
            f"vol must equal vol_sol (SOL-space); got vol={norm['vol']!r}"
        )


def test_to_pregrad_swaps_with_decimals_parameter() -> None:
    """to_pregrad_swaps accepts base_decimals parameter without error."""
    from core.firehose.spine import to_pregrad_swaps

    grad_bt = 10_000
    swaps = [
        {
            "block_time": grad_bt - 100 - i,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy",
            "owner": f"W{i}",
            "price": 1e-4,
            "vol_sol": 0.5,
            "token_amount": 50_000_000,  # raw amount
        }
        for i in range(5)
    ]
    # base_decimals=6 (default) and =8 (Token-2022) must both work without crash
    result_6 = to_pregrad_swaps(swaps, grad_bt, base_decimals=6)
    result_8 = to_pregrad_swaps(swaps, grad_bt, base_decimals=8)
    assert len(result_6) == 5
    assert len(result_8) == 5
    # vol (SOL-space) must be identical regardless of base_decimals
    for r6, r8 in zip(result_6, result_8):
        assert r6["vol"] == pytest.approx(r8["vol"]), (
            "vol (SOL-space) must not change with different base_decimals"
        )


def test_assemble_pregrad_features_with_decimals() -> None:
    """assemble_pregrad_features accepts base_decimals without error (#288)."""
    from core.firehose.spine import assemble_pregrad_features

    grad_bt = 10_000
    # 20 swaps to pass the secondary gate
    swaps = [
        {
            "block_time": grad_bt - 3000 + i * 100,
            "slot": i,
            "signature": f"s{i}",
            "side": "buy" if i % 3 != 0 else "sell",
            "owner": f"W{i % 8}",
            "price": 1e-4 + i * 1e-7,
            "vol_sol": 0.5,
            "token_amount": 50_000_000,
        }
        for i in range(25)
    ]
    feats_6 = assemble_pregrad_features(swaps, grad_bt, base_decimals=6)
    feats_8 = assemble_pregrad_features(swaps, grad_bt, base_decimals=8)
    assert feats_6 is not None, "Expected features with base_decimals=6"
    assert feats_8 is not None, "Expected features with base_decimals=8"
    # Feature math is SOL-space (vol-based) so features must be identical
    from core.pregrad_features import PRE_FEATURE_NAMES  # noqa: PLC0415
    for feat in PRE_FEATURE_NAMES:
        v6 = feats_6.get(feat)
        v8 = feats_8.get(feat)
        if v6 is None and v8 is None:
            continue
        if v6 is None or v8 is None:
            pytest.fail(f"Feature {feat!r}: one is None (v6={v6}, v8={v8})")
        if isinstance(v6, float) and isinstance(v8, float):
            if math.isnan(v6) and math.isnan(v8):
                continue
        assert v6 == pytest.approx(v8, rel=1e-9), (
            f"Feature {feat!r} differs between base_decimals=6 ({v6}) and =8 ({v8}). "
            "v3.2 features are SOL-space ratios — they must not depend on base_decimals."
        )
