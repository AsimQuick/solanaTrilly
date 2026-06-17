# ---
# module: core.tests.test_normalized_swap_ac173
# sprint: sprint-5
# story: US-17 AC-17.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.normalized_swap, core.encoders, pytest, json
# ---
"""AC-17.3 — NormalizedSwap schema tests.

Verifies:
1. NormalizedSwap exposes all required fields (PRD §7.1).
2. rel is anchored to token.graduated_block_time (NEVER the first swap's block_time).
3. Out-of-vocabulary source, phase, and side values raise ValueError immediately.
4. to_json() uses JsonSafeEncoder so non-finite floats are null (H3/US-5 guard).
5. VALID_SOURCES / VALID_PHASES / VALID_SIDES expose the constrained vocabularies.
"""
import json
from dataclasses import fields as dc_fields
from types import SimpleNamespace

import pytest

from core.normalized_swap import VALID_PHASES, VALID_SIDES, VALID_SOURCES, NormalizedSwap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GRADUATED_BLOCK_TIME = 334_000_000

# Minimal raw swap dict as Birdeye/Helius would produce.
_RAW_SWAP = {
    "block_time": 334_000_100,
    "slot": 334_000_050,
    "signature": "SigABC" + "x" * 82,
    "price": 0.00001234,
    "side": "buy",
    "vol_sol": 1.5,
    "vol_usd": 225.0,
    "sol_usd": 150.0,
    "owner": "OwnerWallet" + "X" * 53,
    "base_reserve": 9_000_000_000,
    "quote_reserve": 1_000_000_000,
    "quote_mint": "So11111111111111111111111111111111111111112",
}

# Minimal token-like object carrying graduated_block_time (mirrors core.models.Token).
_TOKEN = SimpleNamespace(graduated_block_time=_GRADUATED_BLOCK_TIME)


# ---------------------------------------------------------------------------
# Field completeness — all §7.1 fields must be present
# ---------------------------------------------------------------------------

def test_normalized_swap_has_all_required_fields():
    """NormalizedSwap must expose every field listed in PRD §7.1 / AC-17.3."""
    required = {
        "rel", "price", "side", "vol_sol", "vol_usd", "sol_usd",
        "owner", "block_time", "slot", "signature",
        "base_reserve", "quote_reserve", "quote_mint",
        "source", "phase",
    }
    actual = {f.name for f in dc_fields(NormalizedSwap)}
    assert required <= actual, f"Missing fields: {required - actual}"


# ---------------------------------------------------------------------------
# Vocabulary sets are the specified values
# ---------------------------------------------------------------------------

def test_valid_sources_vocabulary():
    assert VALID_SOURCES == {"birdeye_live", "birdeye_backfill", "helius_verify", "helius_live"}


def test_valid_phases_vocabulary():
    assert VALID_PHASES == {"pre", "post"}


def test_valid_sides_vocabulary():
    assert VALID_SIDES == {"buy", "sell"}


# ---------------------------------------------------------------------------
# from_raw_swap — rel is anchored to token.graduated_block_time
# ---------------------------------------------------------------------------

def test_from_raw_swap_rel_anchored_to_graduated_block_time():
    """rel must equal block_time - token.graduated_block_time (the DB anchor)."""
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase="pre")
    expected_rel = _RAW_SWAP["block_time"] - _GRADUATED_BLOCK_TIME
    assert swap.rel == pytest.approx(expected_rel)


def test_from_raw_swap_rel_not_anchored_to_first_swap_time():
    """rel must NOT be 0.0 (which would imply wrong anchoring to the swap's own block_time)."""
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase="pre")
    # If rel were anchored to the swap's own block_time it would be 0; it must not be.
    assert swap.rel != 0.0


def test_from_raw_swap_with_different_graduated_block_time():
    """rel changes when graduated_block_time changes, confirming DB-anchor semantics."""
    token_early = SimpleNamespace(graduated_block_time=334_000_000)
    token_late = SimpleNamespace(graduated_block_time=334_000_090)

    swap_early = NormalizedSwap.from_raw_swap(_RAW_SWAP, token_early, source="birdeye_live", phase="pre")
    swap_late = NormalizedSwap.from_raw_swap(_RAW_SWAP, token_late, source="birdeye_live", phase="pre")

    assert swap_early.rel == pytest.approx(100.0)
    assert swap_late.rel == pytest.approx(10.0)


def test_from_raw_swap_all_fields_mapped():
    """All fields of the raw swap dict map to the correct NormalizedSwap attributes."""
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_backfill", phase="post")

    assert swap.block_time == _RAW_SWAP["block_time"]
    assert swap.slot == _RAW_SWAP["slot"]
    assert swap.signature == _RAW_SWAP["signature"]
    assert swap.price == pytest.approx(_RAW_SWAP["price"])
    assert swap.side == _RAW_SWAP["side"]
    assert swap.vol_sol == pytest.approx(_RAW_SWAP["vol_sol"])
    assert swap.vol_usd == pytest.approx(_RAW_SWAP["vol_usd"])
    assert swap.sol_usd == pytest.approx(_RAW_SWAP["sol_usd"])
    assert swap.owner == _RAW_SWAP["owner"]
    assert swap.base_reserve == _RAW_SWAP["base_reserve"]
    assert swap.quote_reserve == _RAW_SWAP["quote_reserve"]
    assert swap.quote_mint == _RAW_SWAP["quote_mint"]
    assert swap.source == "birdeye_backfill"
    assert swap.phase == "post"


def test_from_raw_swap_nullable_fields_accept_none():
    """owner, base_reserve, quote_reserve are optional in the raw dict."""
    raw = dict(_RAW_SWAP)
    raw.pop("owner", None)
    raw.pop("base_reserve", None)
    raw.pop("quote_reserve", None)
    swap = NormalizedSwap.from_raw_swap(raw, _TOKEN, source="helius_verify", phase="pre")
    assert swap.owner is None
    assert swap.base_reserve is None
    assert swap.quote_reserve is None


# ---------------------------------------------------------------------------
# Constrained vocabulary — out-of-vocabulary values are rejected
# ---------------------------------------------------------------------------

def test_invalid_source_raises_value_error():
    """An out-of-vocabulary source value must raise ValueError immediately."""
    with pytest.raises(ValueError, match="source"):
        NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="unknown_source", phase="pre")


def test_invalid_phase_raises_value_error():
    """An out-of-vocabulary phase value must raise ValueError immediately."""
    with pytest.raises(ValueError, match="phase"):
        NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase="during")


def test_invalid_side_raises_value_error():
    """An out-of-vocabulary side value must raise ValueError immediately."""
    raw = dict(_RAW_SWAP, side="long")
    with pytest.raises(ValueError, match="side"):
        NormalizedSwap.from_raw_swap(raw, _TOKEN, source="birdeye_live", phase="pre")


def test_direct_construction_invalid_source_raises():
    """Direct dataclass construction also enforces source vocabulary."""
    with pytest.raises(ValueError, match="source"):
        NormalizedSwap(
            rel=100.0, block_time=334_000_100, slot=334_000_050,
            signature="sig", price=0.0001, side="buy",
            vol_sol=1.0, vol_usd=150.0, sol_usd=150.0, owner=None,
            base_reserve=None, quote_reserve=None,
            quote_mint="So11111111111111111111111111111111111111112",
            source="bad_source", phase="pre",
        )


def test_direct_construction_invalid_phase_raises():
    """Direct dataclass construction also enforces phase vocabulary."""
    with pytest.raises(ValueError, match="phase"):
        NormalizedSwap(
            rel=100.0, block_time=334_000_100, slot=334_000_050,
            signature="sig", price=0.0001, side="buy",
            vol_sol=1.0, vol_usd=150.0, sol_usd=150.0, owner=None,
            base_reserve=None, quote_reserve=None,
            quote_mint="So11111111111111111111111111111111111111112",
            source="birdeye_live", phase="middle",
        )


def test_direct_construction_invalid_side_raises():
    """Direct dataclass construction also enforces side vocabulary."""
    with pytest.raises(ValueError, match="side"):
        NormalizedSwap(
            rel=100.0, block_time=334_000_100, slot=334_000_050,
            signature="sig", price=0.0001, side="short",
            vol_sol=1.0, vol_usd=150.0, sol_usd=150.0, owner=None,
            base_reserve=None, quote_reserve=None,
            quote_mint="So11111111111111111111111111111111111111112",
            source="birdeye_live", phase="pre",
        )


# ---------------------------------------------------------------------------
# All valid vocabularies round-trip without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", sorted(VALID_SOURCES))
def test_all_valid_sources_accepted(source):
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source=source, phase="pre")
    assert swap.source == source


@pytest.mark.parametrize("phase", sorted(VALID_PHASES))
def test_all_valid_phases_accepted(phase):
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase=phase)
    assert swap.phase == phase


@pytest.mark.parametrize("side", sorted(VALID_SIDES))
def test_all_valid_sides_accepted(side):
    raw = dict(_RAW_SWAP, side=side)
    swap = NormalizedSwap.from_raw_swap(raw, _TOKEN, source="birdeye_live", phase="pre")
    assert swap.side == side


# ---------------------------------------------------------------------------
# to_json — uses JsonSafeEncoder (H3/US-5 guard)
# ---------------------------------------------------------------------------

def test_to_json_returns_valid_json():
    """to_json() must return a parseable JSON string."""
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase="pre")
    result = json.loads(swap.to_json())
    assert result["rel"] == pytest.approx(swap.rel)
    assert result["source"] == "birdeye_live"
    assert result["phase"] == "pre"


def test_to_json_nan_becomes_null():
    """Non-finite float values in to_json() must serialize as null (H3/US-5 guard)."""
    raw = dict(_RAW_SWAP, price=float("nan"))
    swap = NormalizedSwap.from_raw_swap(raw, _TOKEN, source="birdeye_live", phase="pre")
    result = json.loads(swap.to_json())
    assert result["price"] is None


def test_to_json_inf_becomes_null():
    """Infinity in to_json() must serialize as null (H3/US-5 guard)."""
    raw = dict(_RAW_SWAP, vol_sol=float("inf"))
    swap = NormalizedSwap.from_raw_swap(raw, _TOKEN, source="birdeye_live", phase="pre")
    result = json.loads(swap.to_json())
    assert result["vol_sol"] is None


def test_to_dict_contains_all_fields():
    """to_dict() must contain all 15 PRD §7.1 fields."""
    swap = NormalizedSwap.from_raw_swap(_RAW_SWAP, _TOKEN, source="birdeye_live", phase="pre")
    d = swap.to_dict()
    required = {
        "rel", "price", "side", "vol_sol", "vol_usd", "sol_usd",
        "owner", "block_time", "slot", "signature",
        "base_reserve", "quote_reserve", "quote_mint",
        "source", "phase",
    }
    assert required <= d.keys(), f"Missing keys: {required - d.keys()}"
