# ---
# module: core.tests.test_data_contract_predictions_us78
# sprint: sprint-14
# story: US-78 (predictions_positions surface + score-time persistence)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django, pyarrow, core.data_contract, core.models
# ---
"""US-78 — predictions_positions surface + durable Prediction persistence.

Covers the pure builder (Prediction⋈Position join, copy rows, projection, manifest),
the score-time persistence helper (run_firehose._persist_prediction_sync), and the
Celery task wiring for the third surface.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from core.data_contract import (
    PREDICTIONS_POSITIONS_COLUMNS,
    build_predictions_positions_dataset,
    utc_date_str,
)


def _read_part(path):
    return pq.ParquetFile(str(path)).read()


def _dt_utc(epoch_s: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(epoch_s, tz=_dt.timezone.utc)


def _pred(**kw):
    p = {
        "mint": "MINTpredaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "score_time": 1_750_000_120,
        "model_id": "trilly_pregrad_v3.2",
        "label_scores": {"ctrl": 1.1, "oracle": 2.2, "liq": 3.3},
        "label_ranks": {"ctrl": 0.7, "oracle": 0.8, "liq": 0.9},
        "blend": 0.7179,
        "per_day_target": 50,
        "rank_cut": 0.6976,
        "picked": True,
        "sol_usd_spot": 150.0,
    }
    p.update(kw)
    return p


def _pos(**kw):
    p = {
        "mint": "MINTpredaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "CLOSED",
        "entry_ts": _dt_utc(1_750_000_125),
        "entry_price": 0.0000399664,
        "size_sol": 0.1667,             # ~$25 at $150
        "exit_ts": _dt_utc(1_750_000_200),
        "exit_price": 0.0000390679,
        "exit_trigger": "AUTO_SELL_TIMER",
        "realized_pnl_sol": -0.00375,
    }
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# Pure builder — model rows
# ---------------------------------------------------------------------------


def test_predictions_positions_schema_and_join(tmp_path):
    res = build_predictions_positions_dataset(
        [_pred()], [_pos()], [], out_dir=tmp_path, dataset_id="t"
    )
    assert res["surface"] == "predictions_positions"
    assert res["row_count"] == 1

    part = Path(res["path"]) / f"dt={utc_date_str(1_750_000_120)}" / "part-0.parquet"
    d = _read_part(part).to_pydict()
    assert _read_part(part).schema.names == PREDICTIONS_POSITIONS_COLUMNS
    # score breakdown projected from label_scores
    assert d["ctrl_pred"] == [1.1]
    assert d["oracle_pred"] == [2.2]
    assert d["liq_pred"] == [3.3]
    assert d["blend"] == [0.7179]
    assert d["percentile"] == [0.7179]          # percentile = blend
    assert d["picked"] == [True]
    assert d["per_day_target"] == [50]
    # entry/exit joined from the position
    assert d["entry_time_s"] == [1_750_000_125]
    assert d["exit_reason"] == ["AUTO_SELL_TIMER"]
    # size_usd = size_sol * sol_usd_spot ; realized_pnl_usd = pnl_sol * spot
    assert d["size_usd"][0] == pytest.approx(0.1667 * 150.0)
    assert d["realized_pnl_usd"][0] == pytest.approx(-0.00375 * 150.0)
    assert d["entry_sol_usd"] == [150.0]


def test_predictions_positions_scored_but_no_position(tmp_path):
    # Gate-failed token: Prediction exists, no Position.
    res = build_predictions_positions_dataset(
        [_pred(picked=False)], [], [], out_dir=tmp_path, dataset_id="t"
    )
    part = Path(res["path"]) / f"dt={utc_date_str(1_750_000_120)}" / "part-0.parquet"
    d = _read_part(part).to_pydict()
    assert d["picked"] == [False]
    assert d["fill_status"] == ["NOT_PICKED"]
    assert d["entry_time_s"] == [None]
    assert d["size_usd"] == [None]
    # the score breakdown is still present (the whole point of the durable record)
    assert d["blend"] == [0.7179]


def test_predictions_positions_nearest_after_join(tmp_path):
    # Two positions for the mint; the one entered at/after score_time is chosen.
    before = _pos(entry_ts=_dt_utc(1_750_000_000), entry_price=1.0)
    after = _pos(entry_ts=_dt_utc(1_750_000_125), entry_price=2.0)
    res = build_predictions_positions_dataset(
        [_pred()], [before, after], [], out_dir=tmp_path, dataset_id="t"
    )
    part = Path(res["path"]) / f"dt={utc_date_str(1_750_000_120)}" / "part-0.parquet"
    d = _read_part(part).to_pydict()
    assert d["entry_price"] == [2.0]  # the at/after-score_time position


# ---------------------------------------------------------------------------
# Pure builder — copy rows
# ---------------------------------------------------------------------------


def test_predictions_positions_copy_rows(tmp_path):
    cp = {
        "mint": "COPYmintbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "trigger_wallet": "WATCHEDwallet1111",
        "status": "closed",
        "entry_ts": _dt_utc(1_750_000_300),
        "entry_price": 0.5,
        "size_usd": 25.0,
        "exit_ts": _dt_utc(1_750_000_400),
        "exit_price": 0.6,
        "exit_reason": "TP",
        "realized_pnl_sol": 0.01,
        "cap_pct": 0.15,
        "copy_latency_s": 1.8,
    }
    res = build_predictions_positions_dataset([], [], [cp], out_dir=tmp_path, dataset_id="t")
    part = Path(res["path"]) / f"dt={utc_date_str(1_750_000_300)}" / "part-0.parquet"
    d = _read_part(part).to_pydict()
    assert d["watched_wallet"] == ["WATCHEDwallet1111"]
    assert d["copy_latency_s"] == [1.8]
    assert d["slippage_bps"] == [1500.0]          # cap_pct * 10000
    assert d["ctrl_pred"] == [None]               # no model score on copy rows
    assert d["blend"] == [None]
    assert d["size_usd"] == [25.0]
    assert d["fill_status"] == ["CLOSED"]


def test_predictions_positions_manifest_and_partition(tmp_path):
    res = build_predictions_positions_dataset(
        [_pred(), _pred(mint="ZMINT", score_time=1_750_200_000)],
        [_pos()],
        [],
        out_dir=tmp_path,
        dataset_id="t_pp",
    )
    manifest = json.loads((Path(res["path"]) / "MANIFEST.json").read_text())
    assert manifest["surface"] == "predictions_positions"
    assert manifest["columns"] == PREDICTIONS_POSITIONS_COLUMNS
    assert manifest["row_count"] == 2
    # two distinct score_time dates -> two partitions
    assert len(manifest["partitions"]) == 2


# ---------------------------------------------------------------------------
# Persistence — run_firehose._persist_prediction_sync (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_persist_prediction_sync_upserts():
    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import Prediction

    # The persistence helper uses no instance state — bypass __init__.
    cmd = FirehoseDaemon.__new__(FirehoseDaemon)
    scoring = {
        "gate": "adaptive_topk",
        "score_at_elapsed_s": 120,
        "per_day_target": 50,
        "rank_cut": 0.6976,
        "model_id": "trilly_pregrad_v3.2",
    }
    result = {
        "label_scores": {"ctrl": 1.0, "oracle": 2.0, "liq": 3.0},
        "label_ranks": {"ctrl": 0.5, "oracle": 0.6, "liq": 0.7},
        "blend_score": 0.72,
    }
    cmd._persist_prediction_sync("MINTx", 1_750_000_120, result, 0.72, True, scoring, 150.0)

    p = Prediction.objects.get(mint="MINTx", score_time=1_750_000_120, model_id="trilly_pregrad_v3.2")
    assert p.blend == pytest.approx(0.72)
    assert p.label_scores == {"ctrl": 1.0, "oracle": 2.0, "liq": 3.0}
    assert p.picked is True
    assert p.per_day_target == 50
    assert p.rank_cut == pytest.approx(0.6976)
    assert p.sol_usd_spot == pytest.approx(150.0)

    # Idempotent: re-persist (e.g. a later tick) updates, not duplicates.
    cmd._persist_prediction_sync("MINTx", 1_750_000_120, result, 0.99, False, scoring, 151.0)
    assert Prediction.objects.filter(mint="MINTx", score_time=1_750_000_120).count() == 1
    p.refresh_from_db()
    assert p.blend == pytest.approx(0.99)
    assert p.picked is False


# ---------------------------------------------------------------------------
# Task wiring
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_export_task_predictions_positions_surface(tmp_path):
    from core.models import Prediction
    from core.tasks import export_data_contract
    from trading.models import Position

    mint = "MINTpredaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    Prediction.objects.create(
        mint=mint, score_time=1_750_000_120, model_id="trilly_pregrad_v3.2",
        label_scores={"ctrl": 1.1, "oracle": 2.2, "liq": 3.3},
        label_ranks={"ctrl": 0.7, "oracle": 0.8, "liq": 0.9},
        blend=0.7179, per_day_target=50, rank_cut=0.6976, picked=True, sol_usd_spot=150.0,
    )
    Position.objects.create(
        mint=mint, source="model", mode="observe", status="CLOSED", score=0.7179,
        entry_ts=_dt_utc(1_750_000_125), entry_price=0.0000399664, size_sol=0.1667,
        exit_ts=_dt_utc(1_750_000_200), exit_price=0.0000390679,
        exit_trigger="AUTO_SELL_TIMER", realized_pnl_sol=-0.00375,
        closed_at=_dt_utc(1_750_000_200),
    )

    out_dir = tmp_path / "export"
    result = export_data_contract(
        out_dir=str(out_dir), surfaces=["predictions_positions"], lake_base_dir=str(tmp_path / "nolake")
    )
    pp = result["surfaces"]["predictions_positions"]
    assert pp["row_count"] == 1
    part = out_dir / "predictions_positions" / f"dt={utc_date_str(1_750_000_120)}" / "part-0.parquet"
    d = _read_part(part).to_pydict()
    assert d["mint"] == [mint]
    assert d["oracle_pred"] == [2.2]
    assert d["exit_reason"] == ["AUTO_SELL_TIMER"]
    assert d["size_usd"][0] == pytest.approx(0.1667 * 150.0)
