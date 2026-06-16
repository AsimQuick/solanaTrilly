# ---
# module: core.tests.test_helius_g2b_activate_ac322
# sprint: sprint-7
# story: US-32 AC-32.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: gzip, json, pathlib, tools.helius_g2b_activate
# ---
"""AC-32.2 — unit coverage for the pure (no-network) helpers of the G2(b) Helius
activation tool (tools/helius_g2b_activate.py).

The live fetch/CLI paths (`fetch_helius_transactions`, `main`) carry `# pragma: no
cover` because they hit the network and are exercised only during the ONE budgeted
activation. The deterministic helpers below — signature extraction, the slim-decode
reducer, pool-account discovery, pool-flow side detection, and the fixture
round-trip — are the logic that BANKS the durable raw-truth fixture, so they are
unit-tested here over small in-memory inputs.
"""
import gzip
import json
from pathlib import Path

from tools.helius_g2b_activate import (
    BANKED_MINT,
    WSOL_MINT,
    bank_helius_fixture,
    determine_pool_account,
    determine_side,
    extract_signatures,
    load_birdeye_fixture,
    slim_helius_tx,
)

POOL = "DZxWcyPpTyr2NTfmEN2xAUSCb77t1ZLpkg63PbpbKmbC"
USER = "User1111111111111111111111111111111111111111"


def _buy_tx(sig: str) -> dict:
    """A full-shape Helius Enhanced TX where the pool SENDS the mint (→ buy)."""
    return {
        "signature": sig,
        "slot": 1234,
        "timestamp": 1750000000,
        "type": "SWAP",
        "source": "PUMP_AMM",
        "feePayer": USER,
        "tokenTransfers": [
            {"mint": BANKED_MINT, "fromUserAccount": POOL, "toUserAccount": USER, "tokenAmount": 1000.0},
            {"mint": WSOL_MINT, "fromUserAccount": USER, "toUserAccount": POOL, "tokenAmount": 2.5},
            {"mint": "OtherMint1111111111111111111111111111111111",
             "fromUserAccount": "x", "toUserAccount": "y", "tokenAmount": 9.0},
        ],
        "nativeTransfers": [
            {"fromUserAccount": USER, "toUserAccount": POOL, "amount": 2500000000},
            {"fromUserAccount": "z", "toUserAccount": "w", "amount": 0},
        ],
        "accountData": [
            {"account": POOL, "tokenBalanceChanges": [
                {"mint": BANKED_MINT, "rawTokenAmount": {"tokenAmount": "-1000"}},
            ]},
        ],
    }


def _sell_tx(sig: str) -> dict:
    """A full-shape Helius Enhanced TX where the pool RECEIVES the mint (→ sell)."""
    return {
        "signature": sig,
        "slot": 1235,
        "timestamp": 1750000005,
        "type": "SWAP",
        "source": "OKX_DEX_ROUTER",
        "feePayer": USER,
        "tokenTransfers": [
            {"mint": BANKED_MINT, "fromUserAccount": USER, "toUserAccount": POOL, "tokenAmount": 500.0},
            {"mint": WSOL_MINT, "fromUserAccount": POOL, "toUserAccount": USER, "tokenAmount": 1.25},
        ],
        "nativeTransfers": [],
        "accountData": [],
    }


def test_extract_signatures_dedups_and_preserves_order() -> None:
    rows = [
        {"txHash": "sigA"},
        {"txHash": "sigB"},
        {"txHash": "sigA"},  # duplicate
        {"no_hash": True},   # skipped
        {"txHash": "sigC"},
    ]
    assert extract_signatures(rows) == ["sigA", "sigB", "sigC"]


def test_slim_helius_tx_keeps_relevant_legs_and_drops_others() -> None:
    slim = slim_helius_tx(_buy_tx("sigA"), BANKED_MINT, WSOL_MINT)
    assert slim is not None
    assert slim["signature"] == "sigA"
    assert slim["block_time"] == 1750000000
    assert slim["helius_source"] == "PUMP_AMM"
    # Only MINT + wSOL legs survive; the unrelated OtherMint leg is dropped.
    mints = {t["mint"] for t in slim["token_transfers"]}
    assert mints == {BANKED_MINT, WSOL_MINT}
    # Zero-amount native transfer is filtered out; the real one is kept.
    assert slim["native_transfers"] == [
        {"from_account": USER, "to_account": POOL, "amount": 2500000000}
    ]
    assert slim["account_token_changes"] == [
        {"account": POOL, "mint": BANKED_MINT, "raw_change": -1000}
    ]


def test_slim_helius_tx_returns_none_without_mint_leg() -> None:
    tx = {
        "signature": "sigX",
        "tokenTransfers": [
            {"mint": WSOL_MINT, "fromUserAccount": "a", "toUserAccount": "b", "tokenAmount": 1.0},
        ],
    }
    assert slim_helius_tx(tx, BANKED_MINT, WSOL_MINT) is None


def test_slim_helius_tx_returns_none_without_signature() -> None:
    assert slim_helius_tx({"tokenTransfers": []}, BANKED_MINT, WSOL_MINT) is None


def test_determine_pool_account_picks_most_frequent_mint_account() -> None:
    slim = [
        slim_helius_tx(_buy_tx("s1"), BANKED_MINT, WSOL_MINT),
        slim_helius_tx(_sell_tx("s2"), BANKED_MINT, WSOL_MINT),
        slim_helius_tx(_buy_tx("s3"), BANKED_MINT, WSOL_MINT),
    ]
    assert determine_pool_account(slim, BANKED_MINT) == POOL


def test_determine_pool_account_empty_returns_blank() -> None:
    assert determine_pool_account([], BANKED_MINT) == ""


def test_determine_side_buy_when_pool_sends_mint() -> None:
    slim = slim_helius_tx(_buy_tx("s1"), BANKED_MINT, WSOL_MINT)
    assert determine_side(slim, BANKED_MINT, POOL) == "buy"


def test_determine_side_sell_when_pool_receives_mint() -> None:
    slim = slim_helius_tx(_sell_tx("s2"), BANKED_MINT, WSOL_MINT)
    assert determine_side(slim, BANKED_MINT, POOL) == "sell"


def test_determine_side_none_when_pool_absent() -> None:
    slim = slim_helius_tx(_buy_tx("s1"), BANKED_MINT, WSOL_MINT)
    assert determine_side(slim, BANKED_MINT, "NotThePoolAccount") is None


def test_bank_and_load_round_trip_is_byte_identical(tmp_path: Path, monkeypatch) -> None:
    """bank_helius_fixture writes gzip jsonl that loads back to the same rows."""
    import tools.helius_g2b_activate as mod

    monkeypatch.setattr(mod, "HELIUS_LAKE_BASE", tmp_path / "helius_decoded_txs")
    rows = [
        slim_helius_tx(_buy_tx("s1"), BANKED_MINT, WSOL_MINT),
        slim_helius_tx(_sell_tx("s2"), BANKED_MINT, WSOL_MINT),
    ]
    path = bank_helius_fixture(BANKED_MINT, rows, date="2026-06-17")
    assert path.exists()
    assert path.name == f"{BANKED_MINT}_helius_g2b_raw.jsonl.gz"

    with gzip.open(path, "rt", encoding="utf-8") as gz:
        loaded = [json.loads(line) for line in gz if line.strip()]
    assert loaded == rows


def test_load_birdeye_fixture_reads_gzip_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "be.jsonl.gz"
    src = [{"txHash": "sigA", "side": "buy"}, {"txHash": "sigB", "side": "sell"}]
    with gzip.open(path, "wb") as gz:
        for row in src:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
    assert load_birdeye_fixture(path) == src
