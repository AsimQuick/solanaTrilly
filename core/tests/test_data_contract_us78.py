# ---
# module: core.tests.test_data_contract_us78
# sprint: sprint-14
# story: US-78 AC-78.1 (swaps), AC-78.2 (tokens); fix/us78-export-from-lake
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, pyarrow, pandas, core.data_contract
# ---
"""US-78 data-contract export — tests for the swaps + tokens Parquet surfaces.

Validates the §10 contract: Parquet, UTC-date partitioned, sorted by the parity
key, by-mint + by-wallet(signer) queryable, with a faithful column projection and
a cross-machine-stable manifest.  Pure-builder tests feed dicts directly (no DB);
the Celery-task test exercises the lake + Token-registry wiring.
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from core.data_contract import (
    SWAPS_COLUMNS,
    TOKENS_COLUMNS,
    build_swaps_dataset,
    build_tokens_dataset,
    project_token_row,
    swap_source_from_phase,
    utc_date_str,
)
from core.tape.manifest import MANIFEST_REQUIRED_KEYS


def _read_part(path):
    """Read a SINGLE parquet file's true columns (no Hive partition inference).

    ``pq.read_table`` on a path inside a ``dt=VALUE/`` directory auto-adds a ``dt``
    partition column; the file itself stores only the contract columns.  Reading via
    ParquetFile reflects exactly what was written.
    """
    return pq.ParquetFile(str(path)).read()


def _lake_row(**kw):
    """A minimal NormalizedSwap lake row with sensible defaults."""
    row = {
        "mint": "MINTaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "block_time": 1_750_000_000,
        "slot": 1000,
        "signature": "sigAAA",
        "price": 0.5,           # SOL per token
        "side": "buy",
        "vol_sol": 2.0,         # SOL leg -> base_amount_ui = 2.0/0.5 = 4.0
        "vol_usd": 300.0,
        "sol_usd": 150.0,
        "owner": "WALLETxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "phase": "pre",
        "source": "birdeye_live",
    }
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# Helpers under test
# ---------------------------------------------------------------------------


def test_utc_date_str_is_utc():
    # 1_750_000_000 = 2025-06-15T14:13:20Z
    assert utc_date_str(1_750_000_000) == "2025-06-15"


def test_swap_source_from_phase():
    assert swap_source_from_phase("pre") == "pump_dot_fun"
    assert swap_source_from_phase("post") == "pump_amm"
    # unknown phase falls back to the provider tag (never silently coerced)
    assert swap_source_from_phase(None, "birdeye_live") == "birdeye_live"


# ---------------------------------------------------------------------------
# Surface (1) — swaps
# ---------------------------------------------------------------------------


def test_swaps_surface_schema_and_partition(tmp_path):
    rows = [
        _lake_row(block_time=1_750_000_000, signature="sigB"),
        _lake_row(block_time=1_750_000_000, signature="sigA"),  # same day, earlier sig
        _lake_row(block_time=1_750_100_000, signature="sigC"),  # next-ish day partition
    ]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t_swaps")

    assert res["surface"] == "swaps"
    assert res["row_count"] == 3
    assert res["skipped"] == 0

    # Partition directories are dt=YYYY-MM-DD
    swaps_dir = Path(res["path"])
    parts = sorted(p.name for p in swaps_dir.glob("dt=*"))
    assert all(p.startswith("dt=") for p in parts)
    assert parts == [f"dt={utc_date_str(1_750_000_000)}", f"dt={utc_date_str(1_750_100_000)}"]

    # Each part is a readable parquet with EXACTLY the contract columns, in order.
    first = swaps_dir / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet"
    table = _read_part(first)
    assert table.schema.names == SWAPS_COLUMNS


def test_swaps_sort_and_projection(tmp_path):
    rows = [
        _lake_row(block_time=100 + 1_750_000_000, signature="sigZ"),
        _lake_row(block_time=1_750_000_000, signature="sigM"),
        _lake_row(block_time=1_750_000_000, signature="sigA"),
    ]
    build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t_swaps")
    table = _read_part(tmp_path / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet")
    d = table.to_pydict()
    # All three rows share the same UTC day -> one partition; sorted by
    # (block_time_s, tx_signature): sigA & sigM at t0, then sigZ at t0+100.
    assert d["tx_signature"] == ["sigA", "sigM", "sigZ"]
    assert d["block_time_s"] == [1_750_000_000, 1_750_000_000, 1_750_000_100]
    # Projection correctness
    assert d["sol_amount"] == [2.0, 2.0, 2.0]
    assert d["base_amount_ui"] == [4.0, 4.0, 4.0]     # vol_sol / price
    assert d["usd_value"] == [300.0, 300.0, 300.0]
    assert d["token_price"] == [0.5, 0.5, 0.5]
    assert d["source"] == ["pump_dot_fun"] * 3        # phase=pre
    assert d["ix_index"] == [None, None, None]        # not persisted


def test_swaps_signer_is_owner_and_queryable(tmp_path):
    rows = [
        _lake_row(signature="s1", owner="WALLET_A"),
        _lake_row(signature="s2", owner="WALLET_B"),
        _lake_row(signature="s3", owner="WALLET_A"),
    ]
    build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t_swaps")
    table = _read_part(tmp_path / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet")
    df = table.to_pandas()
    # by-wallet(signer) query — the copy-trade denominator
    assert sorted(df[df["signer"] == "WALLET_A"]["tx_signature"]) == ["s1", "s3"]
    assert list(df[df["signer"] == "WALLET_B"]["tx_signature"]) == ["s2"]


def test_swaps_base_amount_raw_uses_decimals(tmp_path):
    rows = [_lake_row(signature="s1")]
    mint = rows[0]["mint"]
    build_swaps_dataset(rows, out_dir=tmp_path, mint_decimals={mint: 6}, dataset_id="t")
    table = _read_part(tmp_path / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet")
    d = table.to_pydict()
    # base_amount_ui = 4.0, decimals = 6 -> raw = 4_000_000
    assert d["base_decimals"] == [6]
    assert d["base_amount_raw"] == [4_000_000]


def test_swaps_null_price_yields_null_ui(tmp_path):
    rows = [_lake_row(signature="s1", price=0.0)]   # non-positive price -> null ui
    build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t")
    table = _read_part(tmp_path / "swaps" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet")
    d = table.to_pydict()
    assert d["base_amount_ui"] == [None]
    assert d["base_amount_raw"] == [None]


def test_swaps_skips_rows_missing_keys(tmp_path):
    rows = [
        _lake_row(signature="ok"),
        _lake_row(signature=None),          # missing parity secondary key
        {"mint": "M", "signature": "x"},    # missing block_time
    ]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t")
    assert res["row_count"] == 1
    assert res["skipped"] == 2


def test_swaps_manifest(tmp_path):
    rows = [_lake_row(signature="s1"), _lake_row(signature="s2", mint="ZMINT")]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t_swaps")
    manifest = json.loads((Path(res["path"]) / "MANIFEST.json").read_text())
    assert MANIFEST_REQUIRED_KEYS.issubset(manifest.keys())
    assert manifest["format"] == "parquet"
    assert manifest["surface"] == "swaps"
    assert manifest["columns"] == SWAPS_COLUMNS
    assert manifest["row_count"] == 2
    # mint_cohort is LC_ALL=C sorted and unique
    assert manifest["mint_cohort"] == sorted(set(manifest["mint_cohort"]))
    assert manifest["notes"]  # gaps surfaced, never silently dropped


def test_swaps_content_hash_is_deterministic(tmp_path):
    rows = [_lake_row(signature="s1"), _lake_row(signature="s2")]
    a = build_swaps_dataset(rows, out_dir=tmp_path / "a", dataset_id="t")
    b = build_swaps_dataset(rows, out_dir=tmp_path / "b", dataset_id="t")
    assert a["manifest"]["content_hash"] == b["manifest"]["content_hash"]


# ---------------------------------------------------------------------------
# Surface (2) — tokens
# ---------------------------------------------------------------------------


def _token_dict(**kw):
    rg = {
        "creation_time": 1_749_990_000,
        "decimals": 6,
        "graduated": True,
        "progress_percent": 100.0,
        "source": "pump_dot_fun",
        "raw": {
            "symbol": "glippy",
            "decimals": 6,
            "meme_info": {"creator": "CREATORwallet1111111111111111111111111111"},
        },
    }
    tok = {
        "mint": "5NgDxD1en3YXvS9amAtgb15nc4oRfuGxomcAfu4wpump",
        "graduated_block_time": 1_750_000_000,
        "dex_source": "pump_dot_fun",
        "raw_graduation": rg,
    }
    tok.update(kw)
    return tok


def test_project_token_row_extracts_deployer_and_anchors():
    rec = project_token_row(_token_dict())
    assert rec["deployer"] == "CREATORwallet1111111111111111111111111111"
    assert rec["symbol"] == "glippy"
    assert rec["base_decimals"] == 6
    assert rec["creation_time_s"] == 1_749_990_000
    assert rec["graduated_time_s"] == 1_750_000_000
    assert rec["graduated"] is True
    assert rec["progress_percent"] == 100.0
    assert rec["source"] == "pump_dot_fun"


def test_project_token_row_missing_fields_null_not_crash():
    tok = {"mint": "M", "graduated_block_time": 1_750_000_000, "raw_graduation": {}}
    rec = project_token_row(tok)
    assert rec["deployer"] is None
    assert rec["symbol"] is None
    assert rec["base_decimals"] is None
    assert rec["graduated"] is True          # presence in registry => graduated
    assert rec["graduated_time_s"] == 1_750_000_000


def test_tokens_surface_schema_partition_manifest(tmp_path):
    toks = [
        _token_dict(mint="AAA", graduated_block_time=1_750_000_000),
        _token_dict(mint="BBB", graduated_block_time=1_750_100_000),
    ]
    res = build_tokens_dataset(toks, out_dir=tmp_path, dataset_id="t_tokens")
    assert res["surface"] == "tokens"
    assert res["row_count"] == 2

    tokens_dir = Path(res["path"])
    part = tokens_dir / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet"
    table = _read_part(part)
    assert table.schema.names == TOKENS_COLUMNS
    d = table.to_pydict()
    assert d["deployer"] == ["CREATORwallet1111111111111111111111111111"]

    manifest = json.loads((tokens_dir / "MANIFEST.json").read_text())
    assert manifest["surface"] == "tokens"
    assert manifest["columns"] == TOKENS_COLUMNS
    assert MANIFEST_REQUIRED_KEYS.issubset(manifest.keys())


def test_tokens_skips_rows_without_grad_time(tmp_path):
    toks = [_token_dict(), {"mint": "X", "graduated_block_time": None, "raw_graduation": {}}]
    res = build_tokens_dataset(toks, out_dir=tmp_path, dataset_id="t")
    assert res["row_count"] == 1
    assert res["skipped"] == 1


# ---------------------------------------------------------------------------
# Bad-timestamp guard in the swaps export (fix/us78-export-from-lake)
# ---------------------------------------------------------------------------


def test_swaps_export_drops_epoch_zero_block_time(tmp_path):
    """build_swaps_dataset must NOT create a dt=1970 partition for block_time=0."""
    rows = [
        _lake_row(signature="good", block_time=1_750_000_000),
        _lake_row(signature="bad_zero", block_time=0),
    ]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t")
    # Only the good row is written; the epoch-zero row is skipped.
    assert res["row_count"] == 1
    assert res["skipped"] == 1
    swaps_dir = Path(res["path"])
    partitions = [p.name for p in swaps_dir.glob("dt=*")]
    assert not any("1970" in p for p in partitions), f"1970 partition found: {partitions}"
    assert not any("1977" in p for p in partitions), f"1977 partition found: {partitions}"
    # The good row lands in the correct 2025 partition.
    assert f"dt={utc_date_str(1_750_000_000)}" in partitions


def test_swaps_export_drops_pre_2020_block_time(tmp_path):
    """Rows with a 1977-epoch block_time (raw slot number misused as timestamp) are dropped."""
    rows = [
        _lake_row(signature="good", block_time=1_750_000_000),
        _lake_row(signature="bad_1977", block_time=221_000_000),  # -> 1977-01-xx
    ]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t")
    assert res["row_count"] == 1
    assert res["skipped"] == 1
    swaps_dir = Path(res["path"])
    partitions = [p.name for p in swaps_dir.glob("dt=*")]
    assert not any("1977" in p for p in partitions), f"1977 partition created: {partitions}"


def test_swaps_export_drops_none_block_time(tmp_path):
    """Rows missing block_time entirely are still dropped (skipped count correct)."""
    rows = [
        _lake_row(signature="good", block_time=1_750_000_000),
        {"mint": "M2", "signature": "bad_none"},  # block_time absent
    ]
    res = build_swaps_dataset(rows, out_dir=tmp_path, dataset_id="t")
    assert res["row_count"] == 1
    assert res["skipped"] == 1
