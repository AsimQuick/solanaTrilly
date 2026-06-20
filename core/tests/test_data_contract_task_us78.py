# ---
# module: core.tests.test_data_contract_task_us78
# sprint: sprint-14
# story: US-78 AC-78.1, AC-78.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, pyarrow, core.tasks, core.data_contract_api
# ---
"""US-78 — Celery task + trigger-API wiring tests (DB-backed).

Exercises the full ``export_data_contract`` path: read the raw lake, build the
{mint: decimals} map from the Token registry, and emit both Parquet surfaces.
Also checks the trigger view's validation and that it dispatches (never runs the
export inline on web).
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from core.data_contract import utc_date_str

pytestmark = pytest.mark.django_db


def _read_part(path):
    """Read a single parquet file's true columns (no Hive partition inference)."""
    return pq.ParquetFile(str(path)).read()


def _write_lake(base: Path, rows: list[dict]) -> None:
    """Write rows as a single dt=DATE/part-0.jsonl.gz lake partition."""
    date_str = utc_date_str(rows[0]["block_time"])
    part_dir = base / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(part_dir / "part-0.jsonl.gz", "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_export_data_contract_task_end_to_end(tmp_path):
    from core.models import Token
    from core.tasks import export_data_contract

    mint = "5NgDxD1en3YXvS9amAtgb15nc4oRfuGxomcAfu4wpump"
    Token.objects.create(
        mint=mint,
        pool_address="POOLxxxx",
        graduated_at=_dt.datetime(2025, 6, 15, 14, 13, 20, tzinfo=_dt.timezone.utc),
        graduated_block_time=1_750_000_000,
        dex_source="pump_dot_fun",
        raw_graduation={
            "creation_time": 1_749_990_000,
            "decimals": 6,
            "graduated": True,
            "progress_percent": 100.0,
            "source": "pump_dot_fun",
            "raw": {
                "symbol": "glippy",
                "decimals": 6,
                "meme_info": {"creator": "CREATORwallet1111"},
            },
        },
        status="DETECTED",
    )

    lake_base = tmp_path / "lake"
    _write_lake(
        lake_base,
        [
            {
                "mint": mint, "block_time": 1_750_000_050, "slot": 10,
                "signature": "sigA", "price": 0.5, "side": "buy", "vol_sol": 2.0,
                "vol_usd": 300.0, "sol_usd": 150.0, "owner": "WALLET_A", "phase": "pre",
                "source": "birdeye_live",
            },
            {
                "mint": mint, "block_time": 1_750_000_060, "slot": 11,
                "signature": "sigB", "price": 0.5, "side": "sell", "vol_sol": 1.0,
                "vol_usd": 150.0, "sol_usd": 150.0, "owner": "WALLET_B", "phase": "post",
                "source": "birdeye_live",
            },
        ],
    )

    out_dir = tmp_path / "export"
    result = export_data_contract(
        out_dir=str(out_dir), lake_base_dir=str(lake_base)
    )

    assert set(result["surfaces"]) == {"swaps", "tokens"}
    assert result["surfaces"]["swaps"]["row_count"] == 2
    assert result["surfaces"]["tokens"]["row_count"] == 1

    # swaps: decimals came from the Token registry -> base_amount_raw derivable
    swaps_part = out_dir / "swaps" / f"dt={utc_date_str(1_750_000_050)}" / "part-0.parquet"
    d = _read_part(swaps_part).to_pydict()
    assert d["base_decimals"] == [6, 6]
    assert d["base_amount_raw"] == [4_000_000, 2_000_000]
    # post-grad row's source mapped to pump_amm
    assert set(d["source"]) == {"pump_dot_fun", "pump_amm"}

    # tokens: deployer wired from raw_graduation
    tok_part = out_dir / "tokens" / f"dt={utc_date_str(1_750_000_000)}" / "part-0.parquet"
    td = _read_part(tok_part).to_pydict()
    assert td["deployer"] == ["CREATORwallet1111"]


def test_trigger_view_rejects_unknown_surface(client):
    resp = client.post(
        "/api/export/data-contract/trigger/",
        data=json.dumps({"surfaces": ["swaps", "bogus"]}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "bogus" in resp.json()["error"]


def test_trigger_view_dispatches(client, monkeypatch):
    """The view dispatches via .delay() (worker), never runs the export inline."""
    captured = {}

    class _FakeTask:
        id = "fake-task-123"

    def _fake_delay(*args, **kwargs):
        captured["called"] = True
        captured["kwargs"] = kwargs
        return _FakeTask()

    monkeypatch.setattr(
        "core.tasks.export_data_contract.delay", _fake_delay, raising=True
    )

    resp = client.post(
        "/api/export/data-contract/trigger/",
        data=json.dumps({"surfaces": ["swaps"]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "fake-task-123"
    assert body["status"] == "queued"
    assert body["surfaces"] == ["swaps"]
    assert captured.get("called") is True
